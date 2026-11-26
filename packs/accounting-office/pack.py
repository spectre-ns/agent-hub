"""Tidewater Chartered Professional Accountants: everything the hub does that is about accounting.

Sectioned like pack.toml, one section per service, each with its own registries, merged at the bottom:

    FOUNDATION   clients, engagements, messages, invoices, the portal's approvals
    T1           personal tax: organizer, slips, the comparison with last year (the risk line), drafting, filing
    BOOKS        monthly bookkeeping                       (placeholder)
    T2           corporate year-end                        (placeholder)
    GST-PAYROLL  GST/HST returns and payroll               (placeholder)

A section adds to its own ``<SECTION>_ACTIONS``, ``_PROVIDERS``, ``_GUARDS``, ``_STANDINS``, ``_SUMMARIES`` (record
type -> summary) and ``ENGAGEMENT_STARTERS`` (service -> what a signed engagement opens); nothing else needs to change
in another section. Everything here is deterministic: the stand-ins answer for the agents in tests and demos. The hub
never calls the CRA and has no EFILE connector: a partner files from the firm's certified tax software and records
the CRA's confirmation number here (``t1.filing_recorded`` checks what was recorded, and sends nothing).

This file is run as one module (the hub checks its digest): it imports nothing from beside it.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# =====================================================================================================================
# FOUNDATION
# =====================================================================================================================
FIRM = "Tidewater CPA"
PROVINCE = "NS"  # where the firm and its clients are: the province whose HST it charges and whose income tax it works out
DEFAULT_FEES = {"t1": 350, "bookkeeping": 400, "t2": 2400, "gst-hst": 300, "payroll": 150}
CENT = Decimal("0.01")
SIN = re.compile(r"\b\d{3}[ -]?\d{3}[ -]?\d{3}\b")
BN = re.compile(r"\b\d{9}\s?[A-Z]{2}\s?\d{4}\b")


def _dec(v, default=None):
    """Money as an exact Decimal (the hub keeps amounts so: 12.30, never 12.299999); ``default`` for none."""
    if isinstance(v, Decimal):
        return v
    if v is None or isinstance(v, bool) or v == "":
        return default
    try:
        d = Decimal(str(v).replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        return default
    return d if d.is_finite() else default


def _cents(v) -> Decimal:
    """To the cent, half up: 12.345 is 12.35 (as the hub rounds a two-place amount)."""
    return (_dec(v, Decimal(0)) + 0).quantize(CENT, ROUND_HALF_UP)


def _plain(v) -> str:
    """A number without trailing zeros: 25.00 is 25, 12.50 is 12.5."""
    s = f"{_dec(v, Decimal(0)):f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _money(v) -> str:
    n = _dec(v)
    if n is None:
        return "—"
    n = _cents(n)
    return f"-${abs(n):,.2f}" if n < 0 else f"${n:,.2f}"


def _ref(v) -> str:
    if isinstance(v, dict):
        v = v.get("reference")
    return str(v or "")


def _read(hub, ref):
    ref = _ref(ref)
    if "/" not in ref:
        return None
    return hub.records.read(*ref.split("/", 1))


def _today(engine) -> date:
    return engine.now().date()


def _kind(rec, is_a=None) -> str | None:
    """Which kind of client a record is, Individual or Business, by its type's family (``is_a``, the hub's): an
    overlay's own kind of individual is an individual. Without ``is_a``, by the record's own type alone."""
    t = str((rec or {}).get("resourceType") or "")
    return next((k for k in ("Individual", "Business") if (is_a(t, k) if is_a else t == k)), None)


def _summary_client(rec, is_a=None):
    if _kind(rec, is_a) == "Business":
        what = {"ccpc": "CCPC", "corporation": "corporation", "partnership": "partnership",
                "sole-proprietor": "sole proprietor", "non-profit": "non-profit"}.get(rec.get("business_type"), "business")
        bits = [what, f"year end {rec['fiscal_year_end']}" if rec.get("fiscal_year_end") else ""]
    else:
        bits = ["self-employed" if rec.get("self_employed") else "", rec.get("marital_status") or ""]
    services = ", ".join(str(s).upper() if s in ("t1", "t2") else str(s) for s in rec.get("services") or [])
    return " · ".join(b for b in (*bits, services) if b) or None


def _summary_engagement(rec):
    fee = f"{_money(rec['fee'])} + HST" if rec.get("fee") is not None else ""
    signed = f"signed {rec['client_signed_on']}" if rec.get("client_signed_on") else ""
    return " · ".join(b for b in (fee, rec.get("fee_basis") or "", signed) if b) or None


def _summary_invoice(rec):
    return " · ".join(b for b in (_money(rec.get("total") or rec.get("amount")), rec.get("description") or "",
                                  f"due {rec['due_on']}" if rec.get("due_on") and rec.get("status") == "sent" else "",
                                  f"paid {rec['paid_on']}" if rec.get("paid_on") else "") if b)


def _summary_communication(rec):
    who = {"out": "to the client", "in": "from the client"}.get(rec.get("direction"), "")
    return " · ".join(b for b in (rec.get("channel") or "", who, str(rec.get("at") or "")[:10]) if b) or None


def _summary_relationship(rec):
    return f"{_plain(rec['percent'])}%" if _dec(rec.get("percent")) is not None else None


FOUNDATION_SUMMARIES = {"Client": _summary_client, "Engagement": _summary_engagement, "Invoice": _summary_invoice,
                        "Communication": _summary_communication, "Relationship": _summary_relationship}


def _communication(engine, run, actor, client_ref, *, channel, subject, body, about=None, direction="out",
                   drafted_by=None, step=None) -> dict:
    """A message on the client's file (a Communication) and, for one the firm sends, the outbox message that delivers
    it: email to the client's address, or the portal (the client's file is the recipient)."""
    from practicehub.engine import act_message_send
    hub = engine.hub
    client = _read(hub, client_ref) or {}
    out = {}
    if direction == "out":
        to = client.get("email") if channel == "email" and client.get("email") else client_ref
        sent = act_message_send(engine, run, step, {"channel": channel, "to": to, "subject": subject, "body": body},
                                actor)
        out["message_id"] = sent.get("message_id")
    data = {"client": client_ref, "direction": direction, "channel": channel, "subject": subject[:200] or "(no subject)",
            "body": body, "at": engine.now().replace(microsecond=0).isoformat()}
    if about:
        data["about"] = about
    if drafted_by and hub.db.members.get(str(drafted_by)):
        data["drafted_by"] = str(drafted_by)
    if out.get("message_id") is not None:
        data["outbox"] = str(out["message_id"])
    rec = hub.create_record(actor, "Communication", data, run_id=run["id"])
    return {**out, "communication": f"Communication/{rec['id']}"}


def _is_client_ref(hub, ref) -> bool:
    return "/" in str(ref or "") and hub.is_a(str(ref).split("/", 1)[0], "Client")


def _subject_of_run(engine, run) -> str:
    """The client a run is about: its subject, else its record when that is a client (of either kind)."""
    if _is_client_ref(engine.hub, run.get("subject")):
        return run["subject"]
    rec = _read(engine.hub, run.get("record"))
    if rec and _is_client_ref(engine.hub, run.get("record")):
        return run["record"]
    return _ref((rec or {}).get("client"))


def act_client_message(engine, run, step, params, actor):
    """client.message: a message to the run's client, kept on their file. ``channel`` email or portal, ``subject``,
    ``body``; ``drafted_by`` the member who wrote it (an agent's draft says so)."""
    client = _subject_of_run(engine, run)
    if not client:
        raise ValueError("client.message: the work is about no client")
    body = str(params.get("body") or "")
    if SIN.search(body) or BN.search(body):
        raise ValueError("client.message: the message quotes a SIN or business number; it is not sent")
    return _communication(engine, run, actor, client, step=step, channel=str(params.get("channel") or "email"),
                          subject=str(params.get("subject") or ""), body=body, about=run.get("record"),
                          drafted_by=params.get("drafted_by"))


# What a client signs on the portal, by the type of record they chose: the states it waits for them in, the field their
# signature fills (empty until they sign), and what they approved, in words.
CLIENT_SIGNS = {
    "T1Return": (("with-client", "t183-overdue"), "t183_signed_on", "the return and authorised filing (T183)", "return"),
    "Engagement": (("proposed", "overdue"), "client_signed_on", "the engagement letter", "engagement"),
}


def act_client_sign(engine, run, step, params, actor):
    """client.sign: the client answered on the portal about one record of theirs, the one they read and chose
    (``about``): the return waiting for their T183, or the engagement letter waiting for their signature. An approval
    is recorded on that record alone (its date and how), which its own workflow, waiting for it, then moves on
    (client-signed, signed): this run never moves another's record. Nothing is written, and the answer is for the
    office, unless the record is the client's, waits for them now and is not signed yet. ``signed`` says whether it
    was recorded."""
    hub = engine.hub
    client = _subject_of_run(engine, run)
    ref, answer = _ref(params.get("about")), str(params.get("answer") or "")
    rtype = ref.split("/", 1)[0]
    if rtype not in CLIENT_SIGNS:
        raise ValueError(f"client.sign: nothing a client signs is a {rtype or 'record'} ({ref!r})")
    states, field, approved, noun = CLIENT_SIGNS[rtype]
    today = _today(engine).isoformat()
    name = (_read(hub, client) or {}).get("name") or "The client"
    rec = _read(hub, ref) or {}
    theirs = bool(rec) and _ref(rec.get("client")) == client
    if answer != "approve":
        _communication(engine, run, actor, client, channel="portal", direction="in", about=ref if theirs else None,
                       subject=f"Question about my {noun}", body=f"{name} has a question before approving.")
        return {"signed": False, "summary": f"{name} has a question before approving: call them back."}
    if not theirs:
        return {"signed": False, "summary": f"{name} approved {ref} on the portal, but it is not theirs: nothing was "
                                            f"recorded."}
    if rec.get("status") not in states or rec.get(field):
        return {"signed": False, "summary": f"{name} approved {hub.key_of(ref) or ref} on the portal, but it is not "
                                            f"waiting for them ({rec.get('status')}"
                                            f"{', signed already' if rec.get(field) else ''}): nothing was recorded."}
    if rtype == "T1Return":
        values = {"t183_signed_on": today, "t183_how": "portal"}
    else:
        values = {"client_signed_on": today, "client_signed_how": "portal", "signed_by_name": name}
    _set(hub, actor, ref, run, **values)
    _communication(engine, run, actor, client, channel="portal", direction="in", about=ref,
                   subject="Approved on the portal", body=f"{name} approved {approved} on {today}.")
    return {"signed": True, "record": ref, "summary": f"{name} approved {ref} on the portal."}


# ---- engagements: one service, one year
# What a signed engagement opens, by service: fn(engine, run, engagement, actor) -> the reference of the work it
# opened (or None). Each section adds its own.
ENGAGEMENT_STARTERS: dict = {}


def act_engagement_send(engine, run, step, params, actor):
    """engagement.send: the letter on the engagement, and the email asking the client to sign it on the portal."""
    hub, ref = engine.hub, run["record"]
    eng = _read(hub, ref)
    eng["letter"] = str(params.get("letter") or "")
    eng["sent_on"] = _today(engine).isoformat()
    hub.update_record(actor, ref, eng, run_id=run["id"])
    return _communication(engine, run, actor, _ref(eng.get("client")), step=step, channel="email",
                          subject=str(params.get("subject") or "Your engagement letter"),
                          body=str(params.get("body") or ""), about=ref, drafted_by=params.get("drafted_by"))


def act_engagement_activate(engine, run, step, params, actor):
    """engagement.activate: open the work the engagement is for (ENGAGEMENT_STARTERS, by service)."""
    hub, ref = engine.hub, run["record"]
    eng = _read(hub, ref)
    start = ENGAGEMENT_STARTERS.get(eng.get("service"))
    if start is None:
        return {"work": None, "summary": f"Nothing opens itself for {eng.get('service')}: the team starts it."}
    work = start(engine, run, eng, actor)
    if work:
        eng = _read(hub, ref)
        eng["work"] = work
        hub.update_record(actor, ref, eng, run_id=run["id"])
    return {"work": work, "summary": f"Opened {work}." if work else "Nothing to open."}


def act_engagement_close(engine, run, step, params, actor):
    """engagement.close: the work is done; that is recorded on its engagement (``record.engagement``), whose own
    workflow, waiting for it, closes it."""
    hub = engine.hub
    work = _read(hub, run["record"]) or {}
    ref = _ref(work.get("engagement"))
    eng = _read(hub, ref) if ref else None
    if not eng or eng.get("status") != "active":
        return {"closed": False}
    _set(hub, actor, ref, run, completed_on=_today(engine).isoformat())
    return {"closed": True, "engagement": ref}


def act_engagement_renewal_date(engine, run, step, params, actor):
    """engagement.renewal_date: when next year's engagement is proposed: January 15 after the work is done."""
    today = _today(engine)
    return {"on": date(today.year + 1, 1, 15).isoformat()}


def act_engagement_renew(engine, run, step, params, actor):
    """engagement.renew: next year's engagement, as this one was: waiting for a partner to accept it (then its
    workflow sends it), or planned (``status``: its workflow waits for its season, then a partner accepts it)."""
    hub, ref = engine.hub, run["record"]
    eng = _read(hub, ref)
    year = int(eng["year"]) + 1
    have = [r for r in hub.records.search("Engagement", subject=_ref(eng["client"]), limit=None)
            if r.get("service") == eng["service"] and r.get("year") == year]
    if have:
        return {"engagement": f"Engagement/{have[0]['id']}", "new": False}
    new = hub.create_record(actor, "Engagement", {
        "client": eng["client"], "client_name": eng.get("client_name") or "", "service": eng["service"], "year": year,
        "fee": eng.get("fee", DEFAULT_FEES.get(eng["service"], 0)), "fee_basis": eng.get("fee_basis") or "fixed",
        "scope": eng.get("scope") or "", "previous": ref,
        "status": "planned" if params.get("status") == "planned" else "acceptance"}, run_id=run["id"])
    return {"engagement": f"Engagement/{new['id']}", "new": True}


def sales_tax_rate(hub, on: date, province: str = PROVINCE) -> Decimal | None:
    """The sales tax (HST) in force in ``province`` on the day ``on``: the TaxRate of the latest day it applies from,
    on or before it. None when none is on file: an invoice is never charged a guessed rate."""
    day = on.isoformat()
    rows = [r for r in hub.records.search("TaxRate", limit=None)
            if r.get("kind") == "sales-tax" and r.get("jurisdiction") == province
            and str(r.get("effective_from") or "") <= day]
    if not rows:
        return None
    return _dec(max(rows, key=lambda r: str(r["effective_from"]))["rate"])


def act_invoice(engine, run, step, params, actor, *, description: str, fee=None) -> dict:
    """An invoice for the run's work, sent: the engagement's fee, one line, plus HST at the rate in force today (a
    TaxRate record), due in 30 days, and the email with it. With no rate on file the step fails, and a person enters
    the rate and runs it again: nothing is invoiced at a guessed rate."""
    hub = engine.hub
    work = _read(hub, run["record"]) or {}
    eng = _read(hub, work.get("engagement")) if work.get("engagement") else None
    client = _ref(work.get("client"))
    amount = _cents(fee if fee is not None else (eng or {}).get("fee") or DEFAULT_FEES.get((eng or {}).get("service"), 0))
    today = _today(engine)
    rate = sales_tax_rate(hub, today)
    if rate is None:
        raise ValueError(f"no HST rate ({PROVINCE}) is on file for {today.isoformat()}: a partner or manager enters it "
                         f"(Tax rates), then this step is run again")
    hst = _cents(amount * rate)
    inv = hub.create_record(actor, "Invoice", {  # numbered by the hub as it is made sent (Invoice.toml [number])
        "client": client, "client_name": work.get("client_name") or "", "description": description,
        "lines": [{"description": description, "amount": amount}], "amount": amount, "hst_rate": rate, "hst": hst,
        "total": amount + hst, "issued_on": today.isoformat(),
        "due_on": (today + timedelta(days=30)).isoformat(), "status": "sent", "about": run["record"],
        **({"engagement": f"Engagement/{eng['id']}"} if eng else {})}, run_id=run["id"])
    ref, number = f"Invoice/{inv['id']}", inv["number"]
    _communication(engine, run, actor, client, step=step, channel="email", about=ref, subject=f"Invoice {number}",
                   body=f"Thank you for choosing {FIRM}. Invoice {number} for {description}: "
                        f"{_money(amount)} plus HST {_money(hst)}, total {_money(amount + hst)}, due "
                        f"{(today + timedelta(days=30)).isoformat()}.")
    return {"invoice": ref, "number": number, "total": amount + hst}


FOUNDATION_ACTIONS = {"client.message": act_client_message, "client.sign": act_client_sign,
                      "engagement.send": act_engagement_send, "engagement.activate": act_engagement_activate,
                      "engagement.close": act_engagement_close, "engagement.renewal_date": act_engagement_renewal_date,
                      "engagement.renew": act_engagement_renew}
FOUNDATION_PROVIDERS: dict = {}


def guard_no_identifiers(values, ctxview):
    """Nothing an agent writes for a client quotes a SIN or a business number."""
    return [f"{k} quotes a SIN or business number; never write one in a message"
            for k, v in values.items() if isinstance(v, str) and (SIN.search(v) or BN.search(v))]


FOUNDATION_GUARDS = {"engagement-letter": guard_no_identifiers}


def _recs(ctx, data_class, rtype=None, is_a=None):
    """The records of ``data_class`` in a step's context; with ``rtype``, those of that type or a type under it."""
    return [r for r in (ctx["context"].get(f"record:{data_class}") or [])
            if not rtype or (is_a(str(r.get("resourceType") or ""), rtype) if is_a else r.get("resourceType") == rtype)]


def _client_in(ctx, is_a=None) -> dict | None:
    """The client a step's context is about: an individual or a business, or a kind of either."""
    return next(iter(_recs(ctx, "profile", "Client", is_a) if is_a else
                     [r for k in ("Individual", "Business") for r in _recs(ctx, "profile", k)]), None)


def _first_name(client: dict, is_a=None) -> str:
    """What a letter calls the client: a business by its name, a person by their first name."""
    name = str(client.get("name") or "")
    return name if _kind(client, is_a) == "Business" else (name.split() or ["there"])[0]


SERVICE_WORDS = {"t1": "prepare and file your personal income tax return (T1)",
                 "bookkeeping": "keep your books each month", "t2": "prepare and file your corporation's year-end "
                 "financial statements and T2 return", "gst-hst": "prepare and file your GST/HST returns",
                 "payroll": "run your payroll and file the remittances, T4s and the T4 summary"}


def standin_engagement_letter(ctx, is_a=None):
    eng = next(iter(_recs(ctx, "engagement", "Engagement")), None)
    client = _client_in(ctx, is_a)
    if not eng or not client:
        return {"answer": {}, "escalate": True, "reason": "the engagement or the client's file is not readable"}
    service, year = eng.get("service"), eng.get("year")
    fee = eng.get("fee") if eng.get("fee") is not None else DEFAULT_FEES.get(service, 0)
    letter = "\n\n".join([
        f"# Engagement letter: {client.get('name')}, {year}",
        f"{FIRM} will {SERVICE_WORDS.get(service, service)} for {year}.",
        "You will give us every slip, receipt and statement we ask for, complete and on time; you are responsible for "
        "the accuracy of what you give us. We file only once you have approved what we prepared.",
        f"Fee: {_money(fee)} ({eng.get('fee_basis') or 'fixed'}), plus HST.",
        (eng.get("scope") or "").strip()])
    body = (f"Hello {_first_name(client, is_a)},\n\nThis year's engagement letter for {SERVICE_WORDS.get(service, service)} "
            f"({year}) is ready. Please read it and sign it on our client portal so we can start.\n\n{FIRM}")
    return {"letter": letter.strip(), "subject": f"Your {year} engagement letter", "body": body,
            "summary": f"Engagement letter for {service} {year}, fee {_money(fee)}."}


FOUNDATION_STANDINS = {"engagement-letter": standin_engagement_letter}


# =====================================================================================================================
# T1: personal tax
# =====================================================================================================================
RISK_LINE_REFUND = 1000          # a refund that changes by more than this from last year is over the risk line
RISK_LINE_INCOME = Decimal("0.25")  # ...and total income that changes by more than this share (and by $10,000)
INCOME_SLIPS = {"T4", "T4A", "T4A-P", "T4A-OAS", "T4E", "T5", "T3", "T5008", "business-income"}
SLIP_WORDS = {"T4": "employment income", "T4A": "pension or other income", "T4A-P": "CPP benefits",
              "T4A-OAS": "Old Age Security", "T4E": "EI benefits", "T5": "investment income", "T3": "trust income",
              "T5008": "securities sold", "business-income": "self-employment income", "RRSP": "RRSP contribution",
              "donation": "donations", "medical": "medical expenses", "childcare": "childcare", "T2202": "tuition"}
# The year's rates are records (TaxRate, in the rates data class); estimate() reads the four a T1 needs. The donation
# credit over $200 is the federal rule's own rate, not a year's.
DONATION_HIGH_RATE = Decimal("0.29")
T1_RATES = {"federal": ("income-tax", "federal"), "province": ("income-tax", PROVINCE), "cpp": ("cpp", "federal"),
            "ei": ("ei", "federal")}
FILE_MEANING = "I authorise filing this return with the CRA"
REVIEW_MEANING = "I reviewed this return and it is ready for the client"


def tax_rates(hub, year) -> dict:
    """The rates a T1 for ``year`` is worked out with, by what estimate() calls them (T1_RATES): each the TaxRate of
    that kind, place and year, or None when none is on file."""
    rows = [r for r in hub.records.search("TaxRate", limit=None) if r.get("year") == year]
    return {name: next((r for r in rows if (r.get("kind"), r.get("jurisdiction")) == kw), None)
            for name, kw in T1_RATES.items()}


# The rates a new firm starts with (seed_rates): federal and Nova Scotia income tax, CPP and EI for 2024 and 2025
# (brackets and basic amounts only: the estimate is one a reviewer checks), and Nova Scotia's HST since 2010. A partner
# or manager adds the next year's as a TaxRate when it is published.
def _brackets(*rows):
    return [{"over": Decimal(str(over)), "rate": Decimal(rate)} for over, rate in rows]


_FED_RATES = ("0.15", "0.205", "0.26", "0.29", "0.33")
_NS_RATES = ("0.0879", "0.1495", "0.1667", "0.175", "0.21")
SEED_RATES = [
    {"kind": "income-tax", "jurisdiction": "federal", "year": 2024, "rate": Decimal("0.15"),
     "brackets": _brackets(*zip((0, 55867, 111733, 173205, 246752), _FED_RATES)),
     "basic_amount": Decimal("15705"), "employment_amount": Decimal("1433"), "source": "CRA, 2024 indexation"},
    {"kind": "income-tax", "jurisdiction": "federal", "year": 2025, "rate": Decimal("0.15"),
     "brackets": _brackets(*zip((0, 57375, 114750, 177882, 253414), _FED_RATES)),
     "basic_amount": Decimal("16129"), "employment_amount": Decimal("1471"),
     "source": "CRA, 2025 indexation (the lowest rate at 15%, as the estimate has it)"},
    {"kind": "income-tax", "jurisdiction": "NS", "year": 2024, "rate": Decimal("0.0879"),
     "brackets": _brackets(*zip((0, 29590, 59180, 93000, 150000), _NS_RATES)),
     "basic_amount": Decimal("8481"), "source": "Nova Scotia, 2024 (no supplement)"},
    {"kind": "income-tax", "jurisdiction": "NS", "year": 2025, "rate": Decimal("0.0879"),
     "brackets": _brackets(*zip((0, 30507, 61015, 95883, 154650), _NS_RATES)),
     "basic_amount": Decimal("11744"), "source": "Nova Scotia budget 2025, indexed"},
    {"kind": "cpp", "jurisdiction": "federal", "year": 2024, "rate": Decimal("0.0595"), "basic_amount": Decimal("3500"),
     "max_earnings": Decimal("68500"), "source": "CRA, CPP 2024"},
    {"kind": "cpp", "jurisdiction": "federal", "year": 2025, "rate": Decimal("0.0595"), "basic_amount": Decimal("3500"),
     "max_earnings": Decimal("71300"), "source": "CRA, CPP 2025"},
    {"kind": "ei", "jurisdiction": "federal", "year": 2024, "rate": Decimal("0.0166"), "max_earnings": Decimal("63200"),
     "source": "CRA, EI 2024"},
    {"kind": "ei", "jurisdiction": "federal", "year": 2025, "rate": Decimal("0.0164"), "max_earnings": Decimal("65700"),
     "source": "CRA, EI 2025"},
    {"kind": "sales-tax", "jurisdiction": "NS", "effective_from": "2010-07-01", "rate": Decimal("0.15"),
     "source": "Nova Scotia HST, 15% from July 1, 2010"},
    {"kind": "sales-tax", "jurisdiction": "NS", "effective_from": "2025-04-01", "rate": Decimal("0.14"),
     "source": "Nova Scotia HST, 14% from April 1, 2025"},
]


def _rate_key(r: dict) -> tuple:
    return r.get("kind"), r.get("jurisdiction"), r.get("year"), str(r.get("effective_from") or "") or None


def seed_rates(hub, actor) -> list[str]:
    """The SEED_RATES not on file yet, made as TaxRate records by ``actor``: the references made. A rate the firm
    already has (of that kind, place and year or day) is left as the firm has it."""
    have = {_rate_key(r) for r in hub.records.search("TaxRate", limit=None)}
    made = []
    for row in SEED_RATES:
        if _rate_key(row) not in have:
            made.append(f"TaxRate/{hub.create_record(actor, 'TaxRate', dict(row))['id']}")
    return made


def missing_rates(rates: dict) -> list[str]:
    """The rates not on file, as a person would look for them ('income-tax NS')."""
    return [" ".join(T1_RATES[n]) for n, r in rates.items() if not r]


def _tax(income: Decimal, rate: dict) -> Decimal:
    """Income tax on ``income`` by a TaxRate's brackets: each bracket's rate on what is over its amount, up to the
    next one's."""
    rows = sorted(((_dec(b["over"]), _dec(b["rate"])) for b in rate.get("brackets") or []), key=lambda b: b[0])
    out = Decimal(0)
    for i, (over, r) in enumerate(rows):
        if income <= over:
            break
        top = rows[i + 1][0] if i + 1 < len(rows) else None
        out += ((income if top is None else min(income, top)) - over) * r
    return out


def slip_key(slip: dict) -> str:
    """How a slip is named in the organizer and the missing list: 'T4 · Harbour Logistics Ltd.'."""
    return " · ".join(b for b in (str(slip.get("slip_type") or "other"), str(slip.get("issuer") or "").strip()) if b)


def _key_matches(key: str, slip: dict) -> bool:
    kind, _, issuer = key.partition(" · ")
    return slip.get("slip_type") == kind and (not issuer or issuer.lower() == str(slip.get("issuer") or "").lower())


def estimate(slips: list[dict], year: int, rates: dict) -> dict:
    """The return's figures from the year's slips, worked out with that year's rates (tax_rates): a deterministic
    estimate (lines 15000 and 26000, tax, refund), in exact amounts, each to the cent. Rates of another year, or
    missing, are refused: an estimate never borrows a year's rates."""
    missing = missing_rates(rates)
    if missing or any(r.get("year") != year for r in rates.values()):
        raise ValueError(f"no {year} rates on file for {', '.join(missing) or 'every kind'}: nothing is estimated")
    fed, prov, cpp, ei = (rates[n] for n in ("federal", "province", "cpp", "ei"))
    fed_rate, prov_rate = _dec(fed["rate"]), _dec(prov["rate"])
    zero = Decimal(0)
    income = deductions = credits = deducted = employment = zero
    for s in slips:
        amt = _dec(s.get("amount"), zero)
        kind = s.get("slip_type")
        if kind == "T5008":
            amt = amt / 2  # the taxable half of a capital gain
        if kind in INCOME_SLIPS:
            income += amt
        if kind == "T4":
            employment += amt
        elif kind in ("RRSP", "childcare"):
            deductions += amt
        elif kind == "donation":
            credits += fed_rate * min(amt, 200) + DONATION_HIGH_RATE * max(amt - 200, zero)
        elif kind in ("medical", "T2202"):
            credits += fed_rate * amt
        deducted += _dec(s.get("tax_deducted"), zero)
    # CPP and EI on employment income: credits (both governments), and the Canada employment amount (federal)
    cpp_ei = max(min(employment, _dec(cpp["max_earnings"])) - _dec(cpp.get("basic_amount"), zero), zero) \
        * _dec(cpp["rate"]) + min(employment, _dec(ei["max_earnings"])) * _dec(ei["rate"])
    taxable = max(income - deductions, zero)
    fed_credits = (_dec(fed["basic_amount"]) + cpp_ei + min(employment, _dec(fed.get("employment_amount"), zero))) \
        * fed_rate + credits
    tax = max(_tax(taxable, fed) - fed_credits, zero) + \
        max(_tax(taxable, prov) - (_dec(prov["basic_amount"]) + cpp_ei) * prov_rate, zero)
    tax = _cents(tax)
    return {"total_income": _cents(income), "taxable_income": _cents(taxable), "tax_deducted": _cents(deducted),
            "tax": tax, "net_refund": _cents(deducted) - tax}


# The T1's states while its slips are gathered (organizer to the call about what is missing), and before it is drafted.
T1_GATHERING = ("organizer", "gathering", "chasing", "reminded", "slips-missing")
T1_BEFORE_DRAFT = (*T1_GATHERING, "comparing", "preparing")
# ...drafted and not filed yet (in review, or reviewed and with the client or the partner): a slip that comes now
# sends it back to be compared and drafted again.
T1_DRAFTED = ("in-review", "client-approval", "with-client", "t183-overdue", "client-signed")
T1_ASSESSED = ("assessment-differs", "assessed")


def _year_slips(slips, year):
    return [s for s in slips if s.get("resourceType") == "TaxSlip" and s.get("year") == year
            and s.get("status") == "extracted" and s.get("slip_type") != "NOA"]


def _client_records(hub, client_ref, rtype):
    return hub.records.search(rtype, subject=client_ref, limit=None)


def _last_return(returns, year):
    return next((r for r in returns if r.get("resourceType") == "T1Return" and r.get("year") == year - 1
                 and r.get("status") in ("filed", *T1_ASSESSED)), None)


def compare(ret: dict, slips: list[dict], returns: list[dict], rates: dict) -> dict:
    """This year against last year: the risk line. Over it when a kind of income is new, a slip that came last year
    did not come, the refund changes by more than RISK_LINE_REFUND, or income by more than RISK_LINE_INCOME (and
    $10,000); for a client with no return of last year to compare with; and for a year whose rates (``rates``,
    tax_rates) are not all on file, when nothing is estimated (``estimate`` None) and a person works it out."""
    year = ret.get("year")
    now, before = _year_slips(slips, year), _year_slips(slips, (year or 0) - 1)
    missing = missing_rates(rates)
    est = None if missing else estimate(now, year, rates)
    last = _last_return(returns, year or 0)
    reasons = []
    if missing:
        reasons.append(f"No {year} rates on file ({', '.join(missing)}): nothing was estimated; a partner or manager "
                       f"enters them (Tax rates)")
    if last is None:
        reasons.append("No return of last year to compare with")
    kinds_before = {s.get("slip_type") for s in before}
    for kind in sorted({s.get("slip_type") for s in now if s.get("slip_type") in INCOME_SLIPS} - kinds_before):
        if last is not None:
            reasons.append(f"New income: {SLIP_WORDS.get(kind, kind)} ({kind})")
    for s in before:
        if not any(_key_matches(slip_key(s), n) for n in now):
            reasons.append(f"Last year's {slip_key(s)} did not come")
    last_refund = (last or {}).get("assessed_refund", (last or {}).get("net_refund"))
    if est and _dec(last_refund) is not None and abs(est["net_refund"] - _dec(last_refund)) > RISK_LINE_REFUND:
        reasons.append(f"Refund changes by {_money(abs(est['net_refund'] - _dec(last_refund)))} from last year "
                       f"({_money(last_refund)} to {_money(est['net_refund'])})")
    last_income = (last or {}).get("total_income")
    if est and _dec(last_income):
        change = est["total_income"] - _dec(last_income)
        if abs(change) > 10000 and abs(change) / _dec(last_income) > RISK_LINE_INCOME:
            reasons.append(f"Total income changes by {_money(change)} ({change / _dec(last_income):+.0%})")
    return {"over_line": bool(reasons), "reasons": reasons, "estimate": est, "slips": len(now),
            "counted": [f"TaxSlip/{s['id']}" for s in now if s.get("id")],
            "last_year": f"T1Return/{last['id']}" if last else None, "last_year_refund": last_refund,
            "last_year_income": last_income}


def _due_dates(pack, year: int, self_employed: bool) -> dict:
    """The return's deadlines from [[calendar.deadlines]] (T1 ones): filing, and the balance owing."""
    out = {}
    for d in (pack.calendar or {}).get("deadlines", []) if pack else []:
        if d.get("service") != "t1":
            continue
        when = date(year + 1, int(d["month"]), int(d["day"])).isoformat()
        if d["id"] == "t1-balance":
            out["balance_due_date"] = when
        elif d["id"] == "t1-filing-self-employed" and self_employed:
            out["due_date"] = when
        elif d["id"] == "t1-filing" and not (self_employed and "due_date" in out):
            out.setdefault("due_date", when)
    return out


def start_t1(engine, run, eng, actor) -> str:
    """A signed T1 engagement opens the year's T1 return (gathering), which its workflow then walks. A T1 is an
    individual's: a business's engagement for one opens nothing, and says why."""
    hub = engine.hub
    client_ref = _ref(eng["client"])
    if not hub.is_a(str(client_ref).split("/", 1)[0], "Individual"):
        raise ValueError(f"a T1 return is an individual's personal tax: {client_ref} is not an individual")
    have = [r for r in _client_records(hub, client_ref, "T1Return")
            if r.get("year") == eng["year"] and r.get("status") != "withdrawn"]
    if have:
        return f"T1Return/{have[0]['id']}"
    client = _read(hub, client_ref) or {}
    se = bool(client.get("self_employed"))
    rec = hub.create_record(actor, "T1Return", {
        "client": client_ref, "client_name": client.get("name") or "", "year": int(eng["year"]),
        "engagement": f"Engagement/{eng['id']}", "status": "gathering", "self_employed": se,
        **_due_dates(hub.pack, int(eng["year"]), se)}, run_id=run["id"])
    return f"T1Return/{rec['id']}"


ENGAGEMENT_STARTERS["t1"] = start_t1


def _set(hub, actor, ref, run, **values) -> dict:
    rec = _read(hub, ref)
    for k, v in values.items():
        rec[k] = v  # None clears the field
    return hub.update_record(actor, ref, rec, run_id=run["id"])


def act_t1_organizer_sent(engine, run, step, params, actor):
    """t1.organizer_sent: the organizer goes to the client; the return keeps what it expects and what is missing."""
    hub, ref = engine.hub, run["record"]
    expected = [str(x) for x in params.get("expected") or []]
    missing = [str(x) for x in params.get("missing") or []]
    _set(hub, actor, ref, run, expected_slips=expected, missing_slips=missing,
         slips_complete=bool(expected) and not missing)
    out = act_client_message(engine, run, step, {"channel": "email", "subject": params.get("subject"),
                                                 "body": params.get("body"), "drafted_by": params.get("drafted_by")},
                             actor)
    return {**out, "expected": len(expected), "missing": len(missing)}


def act_t1_slip_filed(engine, run, step, params, actor):
    """t1.slip_filed (a slip's run): the slip counts on the client's return for its year. A slip it was waiting for
    is no longer missing (all in: slips_complete); a notice of assessment is recorded on the return it assesses.

    A slip read after the return was drafted (one that came late, or one read again), and before it is filed,
    reopens it: the return goes back to be
    compared and drafted again with the slip in it (the slip is its review point, which the next draft answers
    first), the client's authorisation (T183) is cleared, and it is reviewed
    and authorised again before it can be filed. It is never left as a flag on a return someone could still file.
    One that comes after the filing was recorded is flagged for a person (the return may need an adjustment)."""
    hub = engine.hub
    slip = _read(hub, run["record"])
    client, year = _ref(slip.get("client")), slip.get("year")
    returns = [r for r in _client_records(hub, client, "T1Return") if r.get("year") == year
               and r.get("status") != "withdrawn"]
    if not returns:
        return {"return": None, "summary": f"No {year} return is open for this client: kept on file."}
    ret = returns[0]
    ref = f"T1Return/{ret['id']}"
    if slip.get("slip_type") == "NOA":
        boxes = parse_boxes(slip.get("boxes"))
        _set(hub, actor, ref, run, noa_received=True, noa_date=str(boxes.get("date") or slip.get("received_on")
                                                                     or _today(engine).isoformat()),
             assessed_refund=_dec(slip.get("amount"), Decimal(0)),
             assessed_taxable_income=_dec(boxes.get("26000")))
        return {"return": ref, "notice": True, "summary": f"Notice of assessment recorded on {ref}."}
    slips = _year_slips(_client_records(hub, client, "TaxSlip"), year)
    if ret.get("status") in T1_BEFORE_DRAFT:
        expected = ret.get("expected_slips") or []
        missing = [k for k in expected if not any(_key_matches(k, s) for s in slips)]
        income_in = any(s.get("slip_type") in INCOME_SLIPS for s in slips)
        complete = (not missing) if expected else income_in
        # staff were to call the client about what was missing, and it came: the return goes on to be compared,
        # as it would have from waiting (the call's ``moves`` lets this through, and the call is withdrawn)
        on = {"status": "comparing"} if complete and ret.get("status") == "slips-missing" else {}
        _set(hub, actor, ref, run, missing_slips=missing, slips_complete=complete, **on)
        return {"return": ref, "missing": missing, "summary": f"{slip_key(slip)} counted on {ref}; "
                + (f"still missing: {', '.join(missing)}." if missing else "nothing expected is missing.")
                + (" It goes on to be compared and drafted." if on else "")}
    if ret.get("status") in T1_DRAFTED:
        why = f"a slip was read after the return was drafted: {slip_key(slip)}"  # a late slip, or one read again
        note = "\n".join(b for b in (str(ret.get("note") or "").strip(),
                                     f"Reopened {_today(engine).isoformat()}: {why}.") if b)
        # the return's own workflow lets this move through (its open work's ``moves``, its wait's ``until``): this
        # pass ends, and the next one compares and drafts it again (t1.reopen)
        _set(hub, actor, ref, run, status="comparing", t183_signed_on=None, t183_how=None, note=note,
             attention=f"Reopened: {why}", review_points=f"Count the slip read after the draft: {slip_key(slip)}.")
        return {"return": ref, "late": True, "reopened": True,
                "summary": f"{slip_key(slip)} came after {ref} was drafted: reopened, to be drafted, reviewed and "
                           f"authorised again."}
    _set(hub, actor, ref, run, attention=f"A slip came after the return was filed: {slip_key(slip)}. It may need "
                                         f"an adjustment (T1-ADJ).")
    return {"return": ref, "late": True, "reopened": False,
            "summary": f"{slip_key(slip)} came after {ref} was filed: flagged for a person."}


def act_t1_reopen(engine, run, step, params, actor):
    """t1.reopen: the return is back to be compared and drafted (a person reopened it for changes, or a slip came
    after it was drafted). What the client authorised was the return as it was: the T183 is cleared, so the return
    is reviewed, and authorised by the client, again before it can be filed. A return never drafted is here because
    its last slip came while staff were to call about it: it is drafted for the first time, and nothing is cleared."""
    hub, ref = engine.hub, run["record"]
    ret = _read(hub, ref)
    if not ret.get("draft_summary") and not ret.get("t183_signed_on"):
        return {"reopened": False, "cleared_t183": False,
                "summary": "The slips are in: it is compared with last year and drafted."}
    had = ret.get("t183_signed_on")
    _set(hub, actor, ref, run, t183_signed_on=None, t183_how=None)
    return {"reopened": True, "cleared_t183": bool(had),
            "summary": "Reopened for changes: it is drafted, reviewed and authorised by the client again."
                       + (f" The authorisation of {had} no longer stands." if had else "")}


def act_t1_compare(engine, run, step, params, actor):
    """t1.compare: this year against last year, and whether the draft is over the risk line (``over_line``). The
    return keeps the slips it counted (``slips``), each pinned as it is now: a signature on the return covers them,
    so a slip read again since leaves it not current."""
    hub, ref = engine.hub, run["record"]
    ret = _read(hub, ref)
    client = _ref(ret.get("client"))
    out = compare(ret, _client_records(hub, client, "TaxSlip"), _client_records(hub, client, "T1Return"),
                  tax_rates(hub, ret.get("year")))
    _set(hub, actor, ref, run, last_year_refund=out["last_year_refund"], slips=out["counted"],
         attention=("Over the risk line: " + "; ".join(out["reasons"]))[:2000] if out["over_line"] else None)
    if hub.pins_behind(hub.record_type("T1Return"), _read(hub, ref)):  # a slip kept from the last pass, read again
        hub.repin(actor, ref, "slips", run_id=run["id"])
    out["summary"] = ("Over the risk line: " + "; ".join(out["reasons"]) + ".") if out["over_line"] else \
        "Within the risk line: like last year."
    return out


def act_t1_filing_recorded(engine, run, step, params, actor):
    """t1.filing_recorded: check the filing a partner recorded. Nothing is sent to the CRA: the hub has no EFILE
    connector, and the return was filed from the firm's certified tax software. The step runs as the workflow; it
    refuses unless the last signature on the return is a person's, a partner's, saying it authorises filing, the
    client's authorisation (T183) is on the return, and the CRA's confirmation number and the date filed were
    recorded (the lifecycle already asks for all of these; this checks they were given). It writes neither: a
    confirmation number is never made up here. Dates that do not agree are flagged for a person, since the filing
    itself is done."""
    hub, ref = engine.hub, run["record"]
    ret = _read(hub, ref)
    signed = hub.db.events.find_one({"action": "record.signed", "subject": ref}, sort=[("seq", -1)])
    meaning = (((signed or {}).get("data") or {}).get("signed") or {}).get("meaning")
    signer = hub.member(signed["actor"]) if signed and hub.db.members.get(str(signed["actor"])) else {}
    if meaning != FILE_MEANING or signer.get("kind") != "person" or "partner" not in (signer.get("roles") or []):
        raise ValueError("t1.filing_recorded: the return is not signed for filing by a partner")
    if not ret.get("t183_signed_on"):
        raise ValueError("t1.filing_recorded: no T183 authorisation on the return")
    confirmation, filed_on = str(ret.get("efile_confirmation") or "").strip(), str(ret.get("filed_on") or "")
    if not confirmation or not filed_on:
        raise ValueError("t1.filing_recorded: the EFILE confirmation number and the date filed are not recorded")
    odd = []
    if filed_on > _today(engine).isoformat():
        odd.append(f"it is recorded as filed on {filed_on}, a day that has not come")
    if filed_on < str(ret["t183_signed_on"]):
        odd.append(f"it is recorded as filed on {filed_on}, before the client authorised it "
                   f"({ret['t183_signed_on']})")
    if odd:
        _set(hub, actor, ref, run, attention=("Check the filing dates: " + "; ".join(odd))[:2000])
    return {"confirmation": confirmation, "filed_on": filed_on, "signed_by": signer.get("id"),
            "summary": f"Filed on {filed_on} from the firm's tax software, as {signer.get('name') or 'a partner'} "
                       f"recorded: EFILE confirmation {confirmation}."
                       + (" Check the dates: " + "; ".join(odd) + "." if odd else "")}


def act_t1_invoice(engine, run, step, params, actor):
    ret = _read(engine.hub, run["record"]) or {}
    return act_invoice(engine, run, step, params, actor, description=f"Personal tax return (T1) {ret.get('year')}")


def act_t1_compare_noa(engine, run, step, params, actor):
    """t1.compare_noa: the assessment against the return as filed. A difference of a dollar or more goes to a
    person (``differs``)."""
    hub, ref = engine.hub, run["record"]
    ret = _read(hub, ref)
    diffs = []
    for mine, theirs, what in (("net_refund", "assessed_refund", "refund"),
                               ("taxable_income", "assessed_taxable_income", "taxable income")):
        if _dec(ret.get(mine)) is not None and _dec(ret.get(theirs)) is not None \
                and abs(_dec(ret[mine]) - _dec(ret[theirs])) >= 1:
            diffs.append(f"{what}: filed {_money(ret[mine])}, assessed {_money(ret[theirs])}")
    if diffs:
        _set(hub, actor, ref, run, attention=("The assessment differs: " + "; ".join(diffs))[:2000])
    else:
        _set(hub, actor, ref, run, attention=None, assessment_notes="Assessed as filed.")
    return {"differs": bool(diffs), "differences": diffs,
            "summary": ("Assessed differently: " + "; ".join(diffs) + ".") if diffs else "Assessed as filed."}


def prov_t1_last_year(engine, run, step, member):
    """provider t1-last-year: last year's return figures, for the draft and the review."""
    hub = engine.hub
    ret = _read(hub, run["record"]) or {}
    last = _last_return(_client_records(hub, _ref(ret.get("client")), "T1Return"), ret.get("year") or 0)
    if not last:
        return {}
    return {k: last.get(k) for k in ("year", "total_income", "taxable_income", "net_refund", "assessed_refund",
                                     "status", "filed_on")}


T1_ACTIONS = {"t1.organizer_sent": act_t1_organizer_sent, "t1.slip_filed": act_t1_slip_filed,
              "t1.compare": act_t1_compare, "t1.reopen": act_t1_reopen, "t1.filing_recorded": act_t1_filing_recorded,
              "t1.invoice": act_t1_invoice, "t1.compare_noa": act_t1_compare_noa}
T1_PROVIDERS = {"t1-last-year": prov_t1_last_year}

FINAL = re.compile(r"\b(filed|final|signed|approved by)\b", re.I)


DRAFT_FIGURES = ("total_income", "taxable_income", "tax_deducted", "net_refund")


def guard_t1_draft(values, ctxview):
    """Every reason the comparison gave is in the flags (the reviewer sees each), and a draft never says it is filed,
    final or signed. An agent's figures are the comparison's estimate, worked out from the slips read so far: it copies
    them, as it copies a slip's amount, and never adds up a document's text itself, so a document not read yet is not
    counted. With nothing estimated (no rates for the year), an agent does not draft. A person drafts from the firm's
    tax software, and may differ."""
    cmp = (ctxview.get("context") or {}).get("compare") or {}
    flags = str(values.get("flags") or "")
    problems = [f"flags must name: {r}" for r in cmp.get("reasons") or [] if r not in flags]
    problems += [f"{k} calls the draft filed, final or signed; it is a draft for review"
                 for k in ("draft_summary", "summary") if FINAL.search(str(values.get(k) or ""))]
    if (ctxview.get("by") or {}).get("kind") == "agent":
        est = cmp.get("estimate")
        if not est:
            problems.append("nothing was estimated (no rates on file for the year): escalate; a person drafts it")
        else:
            problems += [f"{k} is {_money(values.get(k))}; the slips read so far give {_money(est.get(k))} "
                         f"(compare.estimate): copy that figure; a document not read yet is not counted"
                         for k in DRAFT_FIGURES if _dec(values.get(k)) != _dec(est.get(k))]
    return problems + guard_no_identifiers(values, ctxview)


# words an agent writing a picture's words uses about what it saw, never printed on a slip: a transcription that says
# the picture was hard to read is not a reading anyone can check, and goes to a person
UNSURE = re.compile(r"\[[^\]\n]*\]|\b(?:blurr(?:y|ed)|unclear|illegible|unreadable|faded|smudged|legible enough|"
                    r"hard to (?:read|make out|see)|difficult to (?:read|make out|see)|can(?:not|'t) (?:read|make out|see)|"
                    r"partly|partially|not (?:sure|certain)|uncertain|possibly|probably|appears? to (?:be|read|say)|"
                    r"seems? to (?:be|read|say)|i think)\b", re.I)


# a line of a document that says who issued it (its name after it, or on the next line): a reading of it names them
ISSUED_BY = re.compile(r"^[ \t]*(payer|employer|issuer|issued by)(?:'s name)?\b[ \t]*:?[ \t]*(?:\n[ \t]*)?\w",
                       re.I | re.M)
# a reading's own field names, never printed on a document: in what an agent wrote of a picture, they are its own words
OWN_FIELD = re.compile(r"^[ \t]*(?:slip_type|tax_deducted|summary)[ \t]*:", re.I | re.M)


def guard_slip_extract(values, ctxview):
    """The amount read is in the document's words, as printed: an agent copies numbers, never computes them. The words
    are the slip's text, read from its file when it was attached (a PDF's text); a photo has none, and an agent shown
    it writes them (``text``), as printed, before reading the slip from them: what it read is checked against what it
    wrote, and what it wrote holds nothing of its own (a note that the picture was blurred, a guess): a picture it
    could not read clearly is escalated, and the fields it read are in their own fields, never in what it wrote. An
    agent reads only the words, so its slip and year are in them too: a document that does not say which slip it is,
    or for which year, goes to a person, who sees the document itself. A document that says who issued it is read
    with its issuer, as a return's expected slips are named by it ('T5 · Scotiabank')."""
    ctx = ctxview.get("context") or {}
    slip = next(iter(ctx.get("record:client-documents") or []), {})
    own, typed = str(slip.get("text") or "").strip(), str(values.get("text") or "").strip()
    agent = (ctxview.get("by") or {}).get("kind") == "agent"
    problems = []
    if agent and own and typed:
        problems.append("the slip's text was read from its file; leave text empty")
    if agent and not own and not typed and (ctx.get("file:file") or {}).get("picture"):
        problems.append("the slip is a picture: write its words in text, as printed, line by line, or escalate")
    unsure = UNSURE.search(typed) if agent and typed and not own else None
    if unsure:
        problems.append(f"text is the document's words as printed, nothing of your own, and you wrote "
                        f"{unsure.group(0)!r}: a picture you cannot read clearly is escalated, and a person reads it")
    dump = OWN_FIELD.search(typed) if agent and typed and not own else None
    if dump:
        problems.append(f"text is the document's words as printed, and you wrote {dump.group(0).strip()!r}, the name "
                        f"of a field: put what you read in its own field (slip_type, year, issuer, amount, "
                        f"tax_deducted, boxes), never in text")
    named = ISSUED_BY.search(own if agent and own else typed) if agent else None
    if named and not str(values.get("issuer") or "").strip():
        problems.append(f"the document says who issued it (its {named.group(1)!r} line), and issuer is empty: copy "
                        f"that name into issuer")
    text = (own if agent and own else typed or own).replace(",", "")
    amt = values.get("amount")
    if text and _dec(amt) is not None:  # as printed: 1234.50 or 1234.5, never a sum of other boxes
        forms = {f"{abs(_dec(amt)):.2f}", _plain(abs(_dec(amt)))}
        if not any(f in text for f in forms):
            problems.append(f"the amount {amt} is not in the document's text; copy it as printed")
    if text and agent:
        kind, year = values.get("slip_type"), values.get("year")
        said = dict(SLIP_PATTERNS).get(kind)
        if said and not said.search(text):
            problems.append(f"the document's text does not say it is a {kind}; a document you cannot place is "
                            f"escalated to a person")
        if year is not None and not re.search(rf"\b{int(year)}\b", text):
            problems.append(f"the year {year} is not in the document's text; copy it as printed, or escalate")
    kind = values.get("slip_type")  # the main and tax boxes are the amount and the tax deducted, not other boxes
    boxes = {re.sub(r"(?i)^box\s*", "", k): v for k, v in parse_boxes(values.get("boxes")).items()}
    for box, field, what in ((MAIN_BOX.get(kind), "amount", "its amount"),
                             (TAX_BOX.get(kind), "tax_deducted", "the income tax deducted")):
        if box in boxes and _dec(values.get(field)) != _dec(boxes[box]):
            have = _money(values.get(field)) if _dec(values.get(field)) is not None else "empty"
            problems.append(f"box {box} of a {kind} is {what}: {field} is {have} but box {box} reads "
                            f"{_money(boxes[box])}; put it in {field}")
    return problems


def guard_t1_client_letter(values, ctxview):
    """The letter quotes the reviewed refund (or balance owing) exactly, and never a SIN."""
    ret = next(iter((ctxview.get("context") or {}).get("record:tax") or []), {})
    body = str(values.get("body") or "")
    problems = guard_no_identifiers(values, ctxview)
    if _dec(ret.get("net_refund")) is not None and _money(abs(_dec(ret["net_refund"]))) not in body:
        problems.append(f"the letter must give the reviewed figure, {_money(abs(_dec(ret['net_refund'])))}")
    if re.search(r"\b(has been|was|is) filed\b", body, re.I):
        problems.append("the letter says the return is filed; it is filed only after the client approves")
    return problems


def guard_t1_organizer(values, ctxview):
    """The organizer expects only what came last year: each slip it lists is one of the client's slips on file for
    last year, by its type and issuer (an example in the instructions is no client's slip), and each one still missing
    is one it expects. Never a SIN or a business number."""
    problems = guard_no_identifiers(values, ctxview)
    view = {"context": ctxview.get("context") or {}}
    year = (_the_return(view) or {}).get("year")
    last = _year_slips(_recs(view, "client-documents", "TaxSlip"), int(year) - 1) if year else []
    expected = [str(k) for k in values.get("expected_slips") or []]
    for k in expected:
        if not any(_key_matches(k, s) for s in last):
            problems.append(f"{k} is not one of the client's slips on file for last year; list only those, as "
                            f"'<slip type> · <issuer>'")
    for k in values.get("missing_slips") or []:
        if str(k) not in expected:
            problems.append(f"{k} is listed as missing but not as expected; a missing slip is one expected")
    return problems


T1_GUARDS = {"t1-draft": guard_t1_draft, "slip-extract": guard_slip_extract, "t1-client-letter": guard_t1_client_letter,
             "t1-organizer": guard_t1_organizer, "slip-chase": guard_no_identifiers}


def _the_return(ctx):
    return next(iter(_recs(ctx, "tax", "T1Return")), None)


def standin_t1_organizer(ctx, is_a=None):
    ret, client = _the_return(ctx), _client_in(ctx, is_a)
    if not ret or not client:
        return {"answer": {}, "escalate": True, "reason": "the return or the client's file is not readable"}
    year = ret.get("year")
    slips = _recs(ctx, "client-documents", "TaxSlip")
    expected = list(dict.fromkeys(slip_key(s) for s in _year_slips(slips, year - 1)))
    have = _year_slips(slips, year)
    missing = [k for k in expected if not any(_key_matches(k, s) for s in have)]
    lines = "\n".join(f"- {k}" for k in expected) or "- every slip and receipt you received for the year"
    body = (f"Hello {_first_name(client, is_a)},\n\nIt is time to prepare your {year} return. Last year we had:\n{lines}\n\n"
            "Please send this year's, and anything new: a new job, investments, a property sold, RRSP contributions, "
            "childcare, medical expenses or donations. Upload them on our portal or reply to this email.\n\n"
            f"Your filing deadline is {ret.get('due_date') or 'April 30'}.\n\n{FIRM}")
    return {"expected_slips": expected, "missing_slips": missing, "subject": f"Your {year} tax organizer",
            "body": body, "summary": f"Organizer for {year}: {len(expected)} slip(s) expected from last year, "
                                     f"{len(missing)} not in yet."}


def standin_slip_chase(ctx, is_a=None):
    ret, client = _the_return(ctx), _client_in(ctx, is_a)
    missing = list((ret or {}).get("missing_slips") or [])
    if not ret or not client:
        return {"answer": {}, "escalate": True, "reason": "the return or the client's file is not readable"}
    if not missing:
        return {"answer": {}, "escalate": True, "reason": "nothing is listed as missing, yet the slips are not complete"}
    lines = "\n".join(f"- {k}" for k in missing)
    body = (f"Hello {_first_name(client, is_a)},\n\nWe are still waiting for these for your {ret.get('year')} return:\n"
            f"{lines}\n\nIf one of them does not exist this year (a job that ended, an account closed), just tell us. "
            f"Your filing deadline is {ret.get('due_date') or 'April 30'}.\n\n{FIRM}")
    return {"missing_slips": missing, "subject": f"Still needed for your {ret.get('year')} return", "body": body,
            "summary": f"Reminded the client of {len(missing)} missing slip(s)."}


SLIP_PATTERNS = [
    ("NOA", re.compile(r"notice of assessment", re.I)), ("T4A-OAS", re.compile(r"T4A\s*\(OAS\)", re.I)),
    ("T4A-P", re.compile(r"T4A\s*\(P\)", re.I)), ("T4E", re.compile(r"\bT4E\b")), ("T4A", re.compile(r"\bT4A\b")),
    ("T4", re.compile(r"\bT4\b")), ("T5008", re.compile(r"\bT5008\b")), ("T5", re.compile(r"\bT5\b")),
    ("T3", re.compile(r"\bT3\b")), ("T2202", re.compile(r"\bT2202\b")),
    ("RRSP", re.compile(r"RRSP", re.I)), ("donation", re.compile(r"donation", re.I)),
    ("medical", re.compile(r"medical", re.I)), ("childcare", re.compile(r"child ?care", re.I)),
    ("business-income", re.compile(r"business income|statement of business", re.I))]
MAIN_BOX = {"T4": "14", "T5": "13", "T4A": "16", "T4A-P": "20", "T4A-OAS": "18", "T4E": "14", "T3": "26",
            "T5008": "21"}
TAX_BOX = {k: "22" for k in ("T4", "T4A", "T4E", "T4A-P", "T4A-OAS")}  # income tax deducted


def parse_boxes(text) -> dict:
    """A slip's other boxes, written one a line as ``box: amount`` (a ``date: YYYY-MM-DD`` line too): by box."""
    out = {}
    for line in str(text or "").splitlines():
        k, sep, v = line.partition(":")
        k, v = k.strip(), v.strip().replace(",", "").lstrip("$")
        if not sep or not k:
            continue
        d = _dec(v) if re.fullmatch(r"-?\d+(\.\d+)?", v) else None
        out[k] = d if d is not None else v
    return out


def boxes_text(boxes: dict) -> str:
    return "\n".join(f"{k}: {_cents(v):.2f}" if isinstance(v, (float, Decimal)) else f"{k}: {v}"
                     for k, v in boxes.items())


def read_slip(text: str) -> dict | None:
    """What a slip's text says, read the way a clerk reads it: its type, year, issuer, boxes. None if unreadable."""
    kind = next((k for k, rx in SLIP_PATTERNS if rx.search(text)), None)
    year = re.search(r"\b(?:tax year|year|for)\s*:?\s*(20\d\d)\b", text, re.I) or re.search(r"\b(20\d\d)\b", text)
    if not kind or not year:
        return None
    issuer = re.search(r"(?:issuer|employer|payer|from|issued by)\s*:\s*(.+)", text, re.I)
    boxes = {m.group(1): _dec(m.group(2))
             for m in re.finditer(r"box\s*(\d{2,5})\s*[:=]?\s*\$?(-?[\d,]+(?:\.\d+)?)", text, re.I)}
    amount = re.search(r"(?:amount|refund|total)\s*:?\s*\$?(-?[\d,]+(?:\.\d+)?)", text, re.I)
    owing = re.search(r"balance owing\s*:?\s*\$?([\d,]+(?:\.\d+)?)", text, re.I)
    out = {"slip_type": kind, "year": int(year.group(1)), "issuer": issuer.group(1).strip() if issuer else ""}
    main = MAIN_BOX.get(kind)
    if main and main in boxes:
        out["amount"] = boxes.pop(main)
    elif owing:
        out["amount"] = -_dec(owing.group(1))
    elif amount:
        out["amount"] = _dec(amount.group(1))
    else:
        return None
    if TAX_BOX.get(kind) in boxes:
        out["tax_deducted"] = boxes.pop(TAX_BOX[kind])
    elif "28" in boxes and kind == "T4A":
        out["tax_deducted"] = boxes.pop("28")
    dated = re.search(r"date\s*:?\s*(20\d\d-\d\d-\d\d)", text, re.I)
    if dated:
        boxes = {**boxes, "date": dated.group(1)}
    if boxes:
        out["boxes"] = boxes_text(boxes)
    return out


def standin_slip_extract(ctx):
    slip = next(iter(_recs(ctx, "client-documents", "TaxSlip")), None)
    got = read_slip(str((slip or {}).get("text") or ""))
    if not got and (ctx["context"].get("file:file") or {}).get("picture"):  # the stand-in sees no picture
        return {"answer": {}, "escalate": True, "reason": "the slip is a picture, with no words to read: a person "
                                                          "(or a model that can see it) reads it"}
    if not got:
        return {"answer": {}, "escalate": True, "reason": "the document could not be read: a person must look at it"}
    return {**got, "summary": f"{slip_key(got)} {got['year']}: {_money(got['amount'])}."}


def standin_t1_draft(ctx):
    ret = _the_return(ctx)
    if not ret:
        return {"answer": {}, "escalate": True, "reason": "the return is not readable"}
    cmp = ctx["context"].get("compare") or {}
    slips = _year_slips(_recs(ctx, "client-documents", "TaxSlip"), ret.get("year"))
    if any(s.get("slip_type") == "business-income" for s in slips):
        return {"answer": {}, "escalate": True,
                "reason": "Self-employment income: the statement of business activities (T2125), its expenses and "
                          "CCA, is prepared from the client's books by a person"}
    est = cmp.get("estimate")  # worked out by the comparison with the year's rates, from the slips on file now
    if not est:
        return {"answer": {}, "escalate": True,
                "reason": f"No {ret.get('year')} rates are on file, so nothing was estimated: a person drafts it"}
    est = {k: _dec(v) for k, v in est.items()}
    flags = "\n".join(cmp.get("reasons") or [])
    refund = est["net_refund"]
    # sent back with review points: the draft answers them first (the standin redrafts from the slips on file now)
    answered = (f"Review points answered first: redrafted from the {len(slips)} slip(s) on file now. "
                if str(ret.get("review_points") or "").strip() else "")
    summary = (answered + f"Total income {_money(est['total_income'])}, taxable {_money(est['taxable_income'])}; "
               f"{'refund' if refund >= 0 else 'balance owing'} {_money(abs(refund))} "
               f"(last year {_money(cmp.get('last_year_refund'))}). "
               + ("Over the risk line: see the flags." if cmp.get("over_line") else "Like last year."))
    return {**{k: est[k] for k in ("total_income", "taxable_income", "tax_deducted", "net_refund")},
            "flags": flags, "draft_summary": summary, "summary": summary}


def standin_t1_client_letter(ctx, is_a=None):
    ret, client = _the_return(ctx), _client_in(ctx, is_a)
    if not ret or not client or ret.get("net_refund") is None:
        return {"answer": {}, "escalate": True, "reason": "the reviewed return is not readable"}
    refund = _dec(ret["net_refund"])
    what = (f"a refund of {_money(refund)}" if refund >= 0 else
            f"a balance owing of {_money(-refund)}, due {ret.get('balance_due_date') or 'April 30'}")
    body = (f"Hello {_first_name(client, is_a)},\n\nYour {ret.get('year')} return is ready for your approval. Your total "
            f"income is {_money(ret.get('total_income'))}, and the return shows {what}.\n\n"
            + (f"What changed from last year: {ret.get('flags')}\n\n" if ret.get("flags") else "")
            + "Please review it and approve it on our client portal: approving authorises us to file it "
              f"electronically for you (form T183).\n\n{FIRM}")
    return {"subject": f"Your {ret.get('year')} return is ready for your approval", "body": body,
            "summary": f"Summary sent for approval: {what}."}


T1_STANDINS = {"t1-organizer": standin_t1_organizer, "slip-chase": standin_slip_chase,
               "slip-extract": standin_slip_extract, "t1-draft": standin_t1_draft,
               "t1-client-letter": standin_t1_client_letter}


def _summary_t1(rec):
    bits = []
    if rec.get("net_refund") is not None:
        r = _dec(rec["net_refund"], Decimal(0))
        bits.append(f"refund {_money(r)}" if r >= 0 else f"owing {_money(-r)}")
    if rec.get("missing_slips") and rec.get("status") in T1_GATHERING:
        bits.append(f"{len(rec['missing_slips'])} slip(s) missing")
    if rec.get("due_date") and rec.get("status") not in ("filed", *T1_ASSESSED, "withdrawn"):
        bits.append(f"due {rec['due_date']}")
    if rec.get("efile_confirmation"):
        bits.append(f"EFILE {rec['efile_confirmation']}")
    return " · ".join(bits) or None


def _summary_slip(rec):
    if rec.get("amount") is None:
        return "not read yet" if rec.get("status") == "received" else None
    bits = [_money(rec["amount"])]
    if rec.get("tax_deducted"):
        bits.append(f"tax deducted {_money(rec['tax_deducted'])}")
    return " · ".join(bits)


T1_SUMMARIES = {"T1Return": _summary_t1, "TaxSlip": _summary_slip}


# =====================================================================================================================
# BOOKS: monthly bookkeeping (placeholder: the bookkeeping section adds its own registries here)
# =====================================================================================================================
BOOKS_ACTIONS: dict = {}
BOOKS_PROVIDERS: dict = {}
BOOKS_GUARDS: dict = {}
BOOKS_STANDINS: dict = {}
BOOKS_SUMMARIES: dict = {}


# =====================================================================================================================
# T2: corporate year-end (placeholder)
# =====================================================================================================================
T2_ACTIONS: dict = {}
T2_PROVIDERS: dict = {}
T2_GUARDS: dict = {}
T2_STANDINS: dict = {}
T2_SUMMARIES: dict = {}


# =====================================================================================================================
# GST-PAYROLL: GST/HST returns and payroll (placeholder)
# =====================================================================================================================
GST_PAYROLL_ACTIONS: dict = {}
GST_PAYROLL_PROVIDERS: dict = {}
GST_PAYROLL_GUARDS: dict = {}
GST_PAYROLL_STANDINS: dict = {}
GST_PAYROLL_SUMMARIES: dict = {}


# =====================================================================================================================
# What the hub reads: every section's registries, merged
# =====================================================================================================================
ACTIONS = {**FOUNDATION_ACTIONS, **T1_ACTIONS, **BOOKS_ACTIONS, **T2_ACTIONS, **GST_PAYROLL_ACTIONS}
PROVIDERS = {**FOUNDATION_PROVIDERS, **T1_PROVIDERS, **BOOKS_PROVIDERS, **T2_PROVIDERS, **GST_PAYROLL_PROVIDERS}
GUARDS = {**FOUNDATION_GUARDS, **T1_GUARDS, **BOOKS_GUARDS, **T2_GUARDS, **GST_PAYROLL_GUARDS}
STANDINS = {**FOUNDATION_STANDINS, **T1_STANDINS, **BOOKS_STANDINS, **T2_STANDINS, **GST_PAYROLL_STANDINS}
SUMMARIES = {**FOUNDATION_SUMMARIES, **T1_SUMMARIES, **BOOKS_SUMMARIES, **T2_SUMMARIES, **GST_PAYROLL_SUMMARIES}
def upgrade_rates_are_records(hub, actor):
    """1.7: the rates the pack had in its code (the 2025 brackets, CPP, EI and HST) are TaxRate records, so a hub the
    pack upgrades has the rates its returns and invoices are worked out with. None until the TaxRate type is in
    force."""
    if hub.record_type("TaxRate") is None:
        return None
    return {"rates_made": seed_rates(hub, actor)}


def upgrade_clients_by_kind(hub, actor):
    """1.8: each client an older version left (a Client, a person or a business by its ``kind``) becomes an Individual
    or a Business, keeping its id, its CLI key and its TW client number, and everything that named it (returns,
    engagements, invoices, messages, open work) names it. It keeps the fields its kind has; ``kind``, and anything it
    held of the other kind's, go (``fields_dropped``); a client that cannot move stays a Client, read as it was, until
    a person moves it (``clients_not_moved``). None until Client's 1.8 version (abstract) is in force;
    then ``hub.UPGRADE_BATCH`` clients a call, in id order from where the call before stopped."""
    rt = hub.record_type("Client")
    kinds = {k: hub.record_type(k) for k in ("Individual", "Business")}
    if rt is None or not rt.abstract or None in kinds.values():
        return None
    rows = hub.db.records.find({"type": "Client", "_id": {"$gt": hub.upgrade_state.get("after") or ""}},
                               sort="_id", limit=hub.UPGRADE_BATCH, fields=["_id"])
    moved, dropped, kept, refs = [], [], [], {}
    for row in rows:
        hub.upgrade_state["after"] = row["_id"]
        rec = hub.records.read(*row["_id"].split("/", 1)) or {}
        into = "Business" if rec.get("kind") == "business" else "Individual"
        names = {f["name"] for f in kinds[into].fields}
        data = {k: v for k, v in rec.items() if k in names}
        gone = sorted(k for k, v in rec.items() if k not in names and k not in ("resourceType", "id", "meta", "kind")
                      and v not in (None, "", []))
        try:
            hub.convert_record(actor, row["_id"], into, data, repoint=False)
        except Exception as exc:  # it stays a Client, read as it was, and is named: a person moves it
            kept.append(f"{row['_id']}: {exc}")
            continue
        new = f"{into}/{row['_id'].split('/', 1)[1]}"
        moved.append(new)
        refs[row["_id"]] = new
        if gone:
            dropped.append(f"{new}: {', '.join(gone)}")
    hub.repoint_work(refs)
    return {"clients_moved": moved, **({"fields_dropped": dropped} if dropped else {}),
            **({"clients_not_moved": kept} if kept else {}), "more": len(rows) == hub.UPGRADE_BATCH}


def upgrade_documents_agent_sees(hub, actor):
    """1.9: the documents agent reads a slip that is a photo with a model that can see it (its profile's
    ``vision_model``, as the pack names it). A hub on an older version has the agent without one: the upgrade proposes
    it, and a policy owner approves it, as any change to what an agent may do; a model shown clients' documents is
    never put in force by the pack alone. A proposal already waiting keeps what it changes. Nothing to propose when
    the agent is not here or has a model for pictures."""
    want = next(((m.get("profile") or {}).get("vision_model") for m in hub.pack.members
                 if m.get("id") == "documents-agent"), None)
    row = hub.db.members.get("documents-agent")
    if not want or row is None or (row.get("profile") or {}).get("vision_model"):
        return {"profiles_proposed": 0}
    waiting = (hub.agent_profile_proposal("documents-agent") or {}).get("profile") or {}
    if waiting.get("vision_model"):
        return {"profiles_proposed": 0}
    hub.propose_agent_profile(actor, "documents-agent", {**waiting, "vision_model": want},
                              "Accounting office 1.9: a slip that is a photo is read by a model that can see it")
    return {"profiles_proposed": 1}


UPGRADES = {"1.7-rates-are-records": upgrade_rates_are_records, "1.8-clients-by-kind": upgrade_clients_by_kind,
            "1.9-documents-agent-sees": upgrade_documents_agent_sees}


def summarize(rec: dict, is_a=None) -> str | None:
    """A record's summary, by its type or the nearest type above it the pack summarizes (an overlay's own kind of
    individual is summarized as a client)."""
    t = str(rec.get("resourceType") or "")
    fn = SUMMARIES.get(t) or next((f for k, f in SUMMARIES.items() if is_a and is_a(t, k)), None)
    if fn is _summary_client:
        return fn(rec, is_a)
    return fn(rec) if fn else None


def facts(rec: dict, today: date | None = None, is_a=None) -> dict | None:
    """What an individual's file says at a glance: their age. A business's, or an individual's with no date of birth,
    is the core's (its type's ``fact = true`` fields)."""
    if _kind(rec, is_a) != "Individual" or not rec.get("birth_date") or len(str(rec["birth_date"])) != 10:
        return None
    b, t = date.fromisoformat(rec["birth_date"]), today or date.today()
    return {"age": t.year - b.year - ((t.month, t.day) < (b.month, b.day))}

# =====================================================================================================================
# Demo (init --demo): each section's step, run in order (DEMO_STEPS). The foundation makes the clients and keeps
# them in DEMO_CLIENTS (name -> reference), for the sections after it to give work to.
# =====================================================================================================================
IMPORTER = {"id": "import:accounting-office", "kind": "automation", "name": "Import from the old practice system",
            "status": "active"}
DEMO_CLIENTS: dict = {}


def _quiet(*_):
    pass


def _agents_work(hub):
    """Every agent works its inbox, as `practicehub agents run` would, but answered by the pack's stand-ins whatever
    model it names: the demo's past is built without a model, and the same every time."""
    from practicehub.agents.runner import run_all_local
    from practicehub.models import ModelGateway
    run_all_local(hub, ModelGateway(hub, scripted=True), log=_quiet)


# name, birth date, email, extra fields: each an Individual
DEMO_PEOPLE = [
    ("Margaret Doucette", "1951-07-19", "margaret.doucette@example.ca", {"marital_status": "widowed"}),
    ("Sarah MacNeil", "1980-02-03", "sarah.macneil@example.ca", {"marital_status": "single"}),
    ("Liam Chen", "1987-10-11", "liam@chenphoto.example.ca", {"marital_status": "married", "self_employed": True}),
    ("Mei Chen", "1989-04-27", "mei@chendesign.example.ca", {"marital_status": "married", "self_employed": True}),
    ("James Okafor", "1975-12-02", "james.okafor@example.ca", {"marital_status": "married"}),
    ("Aisha Okafor", "1978-06-14", "aisha.okafor@example.ca", {"marital_status": "married"}),
    ("Kevin Thibodeau", "1992-03-08", "kevin.thibodeau@example.ca", {"marital_status": "single"}),
    ("Fatima Haddad", "1984-09-30", "fatima.haddad@example.ca", {"marital_status": "single"}),
    ("Daniel Boudreau", "1996-01-22", "daniel.boudreau@example.ca", {"marital_status": "single"}),
    ("Robert Langille", "1960-05-05", "bob.langille@example.ca", {"marital_status": "divorced"}),
]
# name, year end, email, services: each a Business
DEMO_BUSINESSES = [
    ("Harbourview Bakery Ltd.", "12-31", "accounts@harbourviewbakery.example.ca",
     ["bookkeeping", "t2", "gst-hst", "payroll"]),
    ("Chen Design Inc.", "06-30", "studio@chendesign.example.ca", ["bookkeeping", "t2", "gst-hst"]),
]
DEMO_OWNERS = [("Sarah MacNeil", "Harbourview Bakery Ltd.", "owner", 100), ("Mei Chen", "Chen Design Inc.", "owner", 100),
               ("Liam Chen", "Chen Design Inc.", "director", None), ("Liam Chen", "Mei Chen", "spouse", None),
               ("James Okafor", "Aisha Okafor", "spouse", None)]


def demo_foundation(hub):
    """The firm's rates (seed_rates), and its clients: people (Individual) and their companies (Business), imported
    from the practice's old system. Each is given the next client number as it is made (Client.toml [number]: TW-0001
    on), one series for both kinds."""
    DEMO_CLIENTS.clear()
    seed_rates(hub, IMPORTER)
    n = 0
    for name, born, email, extra in DEMO_PEOPLE:
        n += 1
        rec = hub.create_record(IMPORTER, "Individual", {
            "name": name, "email": email, "birth_date": born,
            "phone": f"902-555-{140 + n:04d}", "address": f"{10 + 7 * n} Spring Garden Road, Halifax NS",
            "services": ["t1"], "partner_in_charge": "anne", "active": True, **extra})
        DEMO_CLIENTS[name] = f"Individual/{rec['id']}"
    for name, year_end, email, services in DEMO_BUSINESSES:
        n += 1
        rec = hub.create_record(IMPORTER, "Business", {
            "name": name, "email": email,
            "business_type": "ccpc", "fiscal_year_end": year_end, "services": services,
            "phone": f"902-555-{140 + n:04d}", "address": f"{100 + n} Hollis Street, Halifax NS",
            "partner_in_charge": "anne", "active": True})
        DEMO_CLIENTS[name] = f"Business/{rec['id']}"
    for who, of, kind, pct in DEMO_OWNERS:
        hub.create_record(IMPORTER, "Relationship", {"client": DEMO_CLIENTS[who], "other": DEMO_CLIENTS[of],
                                                     "kind": kind, **({"percent": pct} if pct else {})})


# ---- T1: last year filed and assessed; this year in every state the T1 goes through
def slip_pdf(text: str) -> bytes:
    """A slip as the bank or employer sends it: a PDF of its words, its first line the title, one line a paragraph.
    The hub reads its text when the slip is attached (the slip's ``text`` is ``from_file``)."""
    from practicehub import pdf
    head, *rest = text.split("\n")
    return pdf.render(head, "\n\n".join(rest))


def blurred_photo(w: int = 96, h: int = 64) -> bytes:
    """A photo of a slip too blurred to read (demo data): a PNG of soft grey, as a phone out of focus takes one."""
    import struct
    import zlib
    rows = b"".join(b"\x00" + bytes(200 - (x * 7 + y * 3) % 40 for x in range(w)) for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def slip_text(kind: str, year: int, issuer: str, amount: float, tax: float = 0.0) -> str:
    """The text of a slip as a scanner reads it (what the documents agent reads)."""
    head = {"T4": ("T4 Statement of Remuneration Paid", "Employer", "14"),
            "T5": ("T5 Statement of Investment Income", "Payer", "13"),
            "T4A-P": ("T4A(P) Statement of Canada Pension Plan Benefits", "Issued by", "20"),
            "T4A-OAS": ("T4A(OAS) Statement of Old Age Security", "Issued by", "18"),
            "T4E": ("T4E Statement of Employment Insurance Benefits", "Issued by", "14"),
            "T5008": ("T5008 Statement of Securities Transactions", "Payer", "21")}
    if kind in head:
        title, who, box = head[kind]
        lines = [title, f"Year: {year}", f"{who}: {issuer}", f"Box {box}: {amount:,.2f}"]
        if tax:
            lines.append(f"Box 22: {tax:,.2f}")
        return "\n".join(lines)
    title = {"RRSP": "RRSP contribution receipt", "donation": "Official receipt for income tax purposes (donation)",
             "medical": "Pharmacy receipt for medical expenses", "childcare": "Child care receipt",
             "business-income": "Statement of business activities (T2125)"}[kind]
    return "\n".join([title, f"Year: {year}", f"Issued by: {issuer}", f"Amount: {amount:,.2f}"])


# client -> [(slip type, issuer, amount last year, tax last year, amount this year (None: did not come), tax)]
DEMO_SLIPS = {
    "Margaret Doucette": [("T4A-P", "Service Canada", 9480.0, 0.0, 9760.0, 0.0),
                          ("T4A-OAS", "Service Canada", 8560.0, 860.0, 8720.0, 870.0),
                          ("T5", "Bluenose Credit Union", 1210.0, 0.0, 1340.0, 0.0),
                          ("donation", "IWK Foundation", 400.0, 0.0, 450.0, 0.0)],
    "Sarah MacNeil": [("T4", "Harbourview Bakery Ltd.", 58000.0, 9100.0, 61000.0, 9600.0),
                      ("T5", "Scotiabank", 640.0, 0.0, 700.0, 0.0),
                      ("T5008", "Wealthsimple", None, None, 24000.0, 0.0)],
    "Liam Chen": [("business-income", "Chen Photography", 41000.0, 0.0, 46500.0, 0.0),
                  ("RRSP", "Tangerine", 3000.0, 0.0, 3500.0, 0.0)],
    "Mei Chen": [("T4", "Chen Design Inc.", 72000.0, 14100.0, 74000.0, 14500.0)],
    "James Okafor": [("T4", "Nova Scotia Health", 88000.0, 19800.0, 90500.0, 20300.0),
                     ("RRSP", "RBC Royal Bank", 6000.0, 0.0, 6000.0, 0.0)],
    "Aisha Okafor": [("T4", "Halifax Regional Centre for Education", 67000.0, 13000.0, 69000.0, 13400.0),
                     ("childcare", "Little Lighthouse Daycare", 7200.0, 0.0, 7400.0, 0.0)],
    "Kevin Thibodeau": [("T4", "Irving Shipbuilding", 76000.0, 15600.0, 79000.0, 16300.0),
                        ("T5", "Scotiabank", 310.0, 0.0, None, None)],
    "Fatima Haddad": [("T4", "Dalhousie University", 54000.0, 9400.0, 41000.0, 7100.0),
                      ("T4E", "Service Canada", None, None, 6200.0, 620.0)],
}


def _prior_year(hub, name, year, slips):
    """Last year, as the old system had it: the slips read, the return filed and assessed, the engagement done."""
    cref = DEMO_CLIENTS[name]
    made = []
    for kind, issuer, amount, tax, *_ in slips:
        if amount is None:
            continue
        s = {"client": cref, "year": year, "status": "extracted", "slip_type": kind, "issuer": issuer,
             "amount": amount, "tax_deducted": tax or 0.0, "source": "portal", "text": slip_text(kind, year, issuer,
                                                                                                  amount, tax or 0.0),
             "received_on": f"{year + 1}-03-0{1 + len(made) % 8}", "resourceType": "TaxSlip"}
        hub.create_record(IMPORTER, "TaxSlip", {k: v for k, v in s.items() if k != "resourceType"})
        made.append(s)
    est = estimate(made, year, tax_rates(hub, year))
    se = bool((_read(hub, cref) or {}).get("self_employed"))
    eng = hub.create_record(IMPORTER, "Engagement", {
        "client": cref, "client_name": name, "service": "t1", "year": year, "fee": 350 if not se else 650,
        "fee_basis": "fixed", "status": "closed", "client_signed_on": f"{year + 1}-01-20", "client_signed_how": "portal",
        "completed_on": f"{year + 1}-05-28"})
    ret = hub.create_record(IMPORTER, "T1Return", {
        "client": cref, "client_name": name, "year": year, "engagement": f"Engagement/{eng['id']}",
        "status": "assessed", "self_employed": se, **_due_dates(hub.pack, year, se),
        **{k: est[k] for k in ("total_income", "taxable_income", "tax_deducted", "net_refund")},
        "t183_signed_on": f"{year + 1}-04-0{3 + len(name) % 5}", "t183_how": "portal",
        "efile_confirmation": _demo_confirmation(name, year),
        "filed_on": f"{year + 1}-04-1{len(name) % 9}", "noa_received": True, "noa_date": f"{year + 1}-05-2{len(name) % 9}",
        "assessed_refund": est["net_refund"], "assessed_taxable_income": est["taxable_income"],
        "assessment_notes": "Assessed as filed."})
    hub.update_record(IMPORTER, f"Engagement/{eng['id']}",
                      {**_read(hub, f"Engagement/{eng['id']}"), "work": f"T1Return/{ret['id']}"})
    return f"Engagement/{eng['id']}"


ACCEPT_MEANING = "I accept this engagement for the firm"


def _engage(hub, name, year, previous=None, *, sign=True, answer="approve"):
    """This year's T1 engagement: Anne (partner) accepts it for the firm, signed; the agent writes and sends the
    letter; the client signs on the portal, and the hub opens the year's return. The return's reference (None if not
    signed)."""
    sophie, portal = hub.member("sophie"), hub.member("client-portal")
    cref = DEMO_CLIENTS[name]
    se = bool((_read(hub, cref) or {}).get("self_employed"))
    eng = hub.create_record(sophie, "Engagement", {"client": cref, "client_name": name, "service": "t1", "year": year,
                                                   "fee": 350 if not se else 650, "fee_basis": "fixed",
                                                   **({"previous": previous} if previous else {}),
                                                   "status": "acceptance"})
    eref = f"Engagement/{eng['id']}"
    hub.move_record(hub.member("anne"), eref, "drafting",
                    {"acceptance_note": "Returning client, no conflicts; the same work as last year."
                     if previous else "New client: identity checked, no conflicts; a T1 we do every season."},
                    signature=ACCEPT_MEANING)
    _agents_work(hub)
    if not sign:
        if answer != "approve":
            hub.engine.start(portal, "client-sign", record=cref, inputs={"about": eref, "answer": answer})
            _agents_work(hub)
        return None
    hub.engine.start(portal, "client-sign", record=cref, inputs={"about": eref, "answer": "approve"})
    _agents_work(hub)
    got = [r for r in hub.records.search("T1Return", subject=cref, limit=None) if r.get("year") == year]
    return f"T1Return/{got[0]['id']}" if got else None


def _upload(hub, name, year, slips, *, only=None):
    """The client uploads this year's slips on the portal; the documents agent reads each."""
    portal = hub.member("client-portal")
    # what the organizer did not expect first: the return is drafted as soon as the expected ones are all in
    for kind, issuer, _, _, amount, tax in sorted(slips, key=lambda s: s[2] is not None):
        if amount is None or (only is not None and kind not in only):
            continue
        f = hub.add_file(portal, "TaxSlip", slip_pdf(slip_text(kind, year, issuer, amount, tax or 0.0)),
                         f"{kind.lower()}-{year}.pdf", "application/pdf")
        hub.create_record(portal, "TaxSlip", {"client": DEMO_CLIENTS[name], "status": "received", "source": "portal",
                                              "received_on": hub.engine.now().date().isoformat(), "file": f})
    _agents_work(hub)


def _approve_holds(hub, ref):
    """The sponsor approves what the agent drafted over the risk line (they read the flags)."""
    rob = hub.member("rob")
    for row in hub.db.items.find({"kind": "hold", "status": "open", "assignee": "rob"}):
        held = hub.db.items.get(row["output"]["holds"]) or {}
        run = hub.engine.run(held.get("run_id")) if held.get("run_id") else None
        if run and run.get("record") == ref:
            hub.engine.decide(rob, row["id"], "approve", "Read the flags; the draft explains each.")
    _agents_work(hub)


def _review(hub, ref, notes):
    hub.move_record(hub.member("rob"), ref, "client-approval", {"review_notes": notes}, signature=REVIEW_MEANING)
    _agents_work(hub)


def _client_approves(hub, ref):
    """The client authorises the return they read (T183), on the portal."""
    hub.engine.start(hub.member("client-portal"), "client-sign", record=_ref((_read(hub, ref) or {}).get("client")),
                     inputs={"about": ref, "answer": "approve"})
    _agents_work(hub)


def _demo_confirmation(name: str, year) -> str:
    """Demo data, marked as such: the number a partner would copy from the firm's tax software. The hub files
    nothing and never makes a confirmation number of its own."""
    return "DEMO-" + hashlib.sha256(f"{name}{year}".encode()).hexdigest()[:6].upper()


def _file(hub, ref):
    """Anne (partner) records the filing she made from the tax software: its confirmation number (demo data) and the
    date, signed."""
    ret = _read(hub, ref) or {}
    hub.move_record(hub.member("anne"), ref, "filing",
                    {"efile_confirmation": _demo_confirmation(ret.get("client_name") or "", ret.get("year")),
                     "filed_on": hub.engine.now().date().isoformat()}, signature=FILE_MEANING)
    _agents_work(hub)


def _notice(hub, name, year, refund, taxable, date_on):
    portal = hub.member("client-portal")
    text = "\n".join(["Notice of Assessment", f"Tax year: {year}", "Issued by: Canada Revenue Agency",
                      f"Date: {date_on}",
                      f"Refund: {refund:.2f}" if refund >= 0 else f"Balance owing: {-refund:.2f}",
                      f"Box 26000: {taxable:.2f}"])
    f = hub.add_file(portal, "TaxSlip", slip_pdf(text), f"notice-of-assessment-{year}.pdf", "application/pdf")
    hub.create_record(portal, "TaxSlip", {"client": DEMO_CLIENTS[name], "status": "received", "source": "portal",
                                          "received_on": date_on, "file": f})
    _agents_work(hub)


def demo_t1(hub):
    """Last year's returns, filed and assessed; this year's in every state the T1 goes through, the agents having
    done what they do: letters, organizers, reading slips, chasing, drafting."""
    from datetime import timezone
    year = hub.engine.now().year - 1
    prev = {name: _prior_year(hub, name, year - 1, slips) for name, slips in DEMO_SLIPS.items()}

    # Kevin: one slip came, last year's T5 did not; the agent chased it, and three weeks on it still had not come:
    # Ethan (staff) is to call him. Time is moved on for his return alone, before anyone else's work starts.
    kevin = _engage(hub, "Kevin Thibodeau", year, prev["Kevin Thibodeau"])
    _upload(hub, "Kevin Thibodeau", year, DEMO_SLIPS["Kevin Thibodeau"])
    now = datetime.now(timezone.utc)
    hub.engine.tick(now + timedelta(days=22))
    _agents_work(hub)
    hub.engine.tick(now + timedelta(days=37))
    _agents_work(hub)

    # Margaret: every slip in, like last year: the agent drafted it, within the risk line; Rob reviews it.
    margaret = _engage(hub, "Margaret Doucette", year, prev["Margaret Doucette"])
    _upload(hub, "Margaret Doucette", year, DEMO_SLIPS["Margaret Doucette"])

    # Sarah: sold shares this year (a T5008): over the risk line, the draft is held for Rob, the agent's sponsor.
    sarah = _engage(hub, "Sarah MacNeil", year, prev["Sarah MacNeil"])
    _upload(hub, "Sarah MacNeil", year, DEMO_SLIPS["Sarah MacNeil"])

    # Liam: self-employed (due June 15): the agent will not draft business income; Priya (senior) prepares it.
    liam = _engage(hub, "Liam Chen", year, prev["Liam Chen"])
    _upload(hub, "Liam Chen", year, DEMO_SLIPS["Liam Chen"])

    # Mei: reviewed; the summary is on the portal, waiting for her to approve (T183).
    mei = _engage(hub, "Mei Chen", year, prev["Mei Chen"])
    _upload(hub, "Mei Chen", year, DEMO_SLIPS["Mei Chen"])
    _approve_holds(hub, mei)
    _review(hub, mei, "T4 agrees with Chen Design's payroll. No changes.")

    # James: reviewed and approved by him on the portal: Anne (partner) is to file it and record that here.
    james = _engage(hub, "James Okafor", year, prev["James Okafor"])
    _upload(hub, "James Okafor", year, DEMO_SLIPS["James Okafor"])
    _approve_holds(hub, james)
    _review(hub, james, "RRSP receipt matches the contribution room on last year's NOA.")
    _client_approves(hub, james)

    # Aisha: filed from the tax software and recorded here (a demo confirmation number), invoiced, waiting for the
    # notice of assessment.
    aisha = _engage(hub, "Aisha Okafor", year, prev["Aisha Okafor"])
    _upload(hub, "Aisha Okafor", year, DEMO_SLIPS["Aisha Okafor"])
    _approve_holds(hub, aisha)
    _review(hub, aisha, "Childcare claimed by the lower-income spouse, as last year.")
    _client_approves(hub, aisha)
    _file(hub, aisha)

    # Fatima: lost her job in the year (EI): filed; the CRA assessed a different refund: Rob looks into it.
    fatima = _engage(hub, "Fatima Haddad", year, prev["Fatima Haddad"])
    _upload(hub, "Fatima Haddad", year, DEMO_SLIPS["Fatima Haddad"])
    _approve_holds(hub, fatima)
    _review(hub, fatima, "EI repayment threshold checked: none owing.")
    _client_approves(hub, fatima)
    _file(hub, fatima)
    filed = _read(hub, fatima)
    _notice(hub, "Fatima Haddad", year, _dec(filed["net_refund"]) - 412,
            _dec(filed["taxable_income"]) + 1800, hub.engine.now().date().isoformat())

    # Daniel: a new client: the letter is out, not signed yet; he uploaded a photo the agent could not read
    # (Ethan types it in). Robert asked a question on the portal instead of signing (Sophie calls him back).
    _engage(hub, "Daniel Boudreau", year, sign=False)
    portal = hub.member("client-portal")
    hub.create_record(portal, "TaxSlip", {
        "client": DEMO_CLIENTS["Daniel Boudreau"], "status": "received", "source": "portal",
        "received_on": hub.engine.now().date().isoformat(),
        "file": hub.add_file(portal, "TaxSlip", blurred_photo(), "IMG_2291.png", "image/png")})
    _agents_work(hub)
    _engage(hub, "Robert Langille", year, sign=False, answer="question")
    return {"kevin": kevin, "margaret": margaret, "sarah": sarah, "liam": liam, "mei": mei, "james": james,
            "aisha": aisha, "fatima": fatima}


DEMO_STEPS = [demo_foundation, demo_t1]  # BOOKS, T2 and GST-PAYROLL add theirs after these


def demo(hub):
    for step in DEMO_STEPS:
        step(hub)
