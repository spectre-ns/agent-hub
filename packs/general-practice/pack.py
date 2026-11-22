"""General practice: the deterministic parts of the pack.

* ``ACTIONS``: what automations can do here beyond the core (the time, a patient's details).
* ``GUARDS``: checks on an answer, keyed by skill, run on every answer (person or agent) before it is accepted.
* ``STANDINS``: plain-code answers for each skill, so the whole practice runs with no model at all (tests, demos,
  and a baseline to compare a model against). They are deliberately simple; when they cannot answer, they escalate.
* ``summarize``: a one-line label for a record.
* ``demo``: a few fictional patients, the week's bookings and some work in progress.
* ``UPGRADES``: what version 2 does to a practice on version 1 (its appointments become visits).
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta

# how a visit's status reads in its one-line label (planned is FHIR's word for booked)
STATUS_WORDS = {"planned": "booked", "in-progress": "in progress", "onleave": "on leave", "noshow": "no-show",
                "entered-in-error": "entered in error"}
SETTING_WORDS = {"AMB": "in clinic", "VR": "virtual", "HH": "home visit"}   # the visit's class (v3-ActCode)
FEES = {"minor-assessment": 35, "complete-assessment": 65, "counselling": 55, "renewal-no-visit": 15}
SEVERITY = {"normal": 0, "abnormal": 1, "critical": 2}


# --------------------------------------------------------------------------------------------------- summaries
def _name(rec: dict) -> str:
    for n in rec.get("name", []) or []:
        given = " ".join(n.get("given", []) or [])
        return f"{given} {n.get('family', '')}".strip()
    return rec.get("id", "")


def _text(cc) -> str:
    """The words of a CodeableConcept (or a list of them): its text, else its codings' display or code."""
    if isinstance(cc, list):
        return ", ".join(t for t in (_text(c) for c in cc) if t)
    if isinstance(cc, str):
        return cc
    if not isinstance(cc, dict):
        return ""
    if cc.get("text"):
        return str(cc["text"])
    if cc.get("display") or cc.get("code"):  # a bare Coding
        return str(cc.get("display") or cc.get("code"))
    return ", ".join(str(c.get("display") or c.get("code") or "") for c in cc.get("coding", []) or [] if c)


def _day(v) -> str:
    return str(v or "")[:10]


def _date_words(v, time: bool = True) -> str:
    """A date or date-time in words: Mon Nov 23, 09:00 (a booking), Nov 23, 2026 (a day); else as written."""
    from datetime import datetime as _dt
    v = str(v or "")
    try:
        d = _dt.fromisoformat(v[:16] if "T" in v else v[:10])
    except ValueError:
        return v[:16].replace("T", " ")
    if time and "T" in v:
        return f"{d:%a %b} {d.day}, {d:%H:%M}"
    return f"{d:%b} {d.day}, {d.year}"


def _quantity(q) -> str:
    q = q or {}
    return f"{q.get('value', '')} {q.get('unit', '')}".strip()


def _range(ob: dict) -> str:
    rr = (ob.get("referenceRange") or [{}])[0] or {}
    if rr.get("text"):
        return str(rr["text"])
    low, high = (rr.get("low") or {}).get("value"), (rr.get("high") or {}).get("value")
    if low is not None and high is not None:
        return f"{low}-{high}"
    if low is not None:
        return f">= {low}"
    if high is not None:
        return f"<= {high}"
    return ""


def _flag(ob: dict) -> str:
    return _text(ob.get("interpretation"))


def _join(*parts) -> str:
    return "; ".join(p for p in parts if p)


def summarize(rec: dict) -> str:
    t = rec.get("resourceType")
    # a patient is named by its type's title (and the core's Subject)
    if t == "RelatedPerson":
        phone = next((x.get("value") for x in rec.get("telecom", []) or [] if x.get("value")), "")
        rel = _text(rec.get("relationship"))
        return f"{_name(rec) or 'Related person'}{f' ({rel})' if rel else ''}{f', {phone}' if phone else ''}"
    if t == "Coverage":
        payor = ", ".join(p.get("display") or p.get("reference", "") for p in rec.get("payor", []) or [])
        what = _text(rec.get("type")) or payor or "Coverage"
        return _join(what, rec.get("subscriberId") and f"ID {rec['subscriberId']}", rec.get("status"))
    if t == "Observation":
        what = _text(rec.get("code")) or "Result"
        # Every value the result carries is shown: a lab that sends "7.1 mmol/L" and the text "see comment: haemolysed"
        # must not have the text hidden behind the number.
        values = []
        for v in (_quantity(rec.get("valueQuantity")) if rec.get("valueQuantity") else "",
                  rec.get("valueString") or "", _text(rec.get("valueCodeableConcept"))):
            if v and str(v).strip() and str(v).strip() not in values:
                values.append(str(v).strip())
        value = values[0] if len(values) == 1 else " / ".join(values) + " (values differ: check the report)" if values else ""
        rng = _range(rec)
        out = f"{what} {value}".strip() + (f" (ref {rng})" if rng else "")
        flag = _flag(rec)
        if flag:
            out += f" {flag.upper() if flag.lower() in ('abnormal', 'critical', 'high', 'low') else flag}"
        if rec.get("status") and rec["status"] not in ("final",):
            out += f" [{rec['status']}]"
        return out
    if t == "AllergyIntolerance":
        substance = _text(rec.get("code")) or "Unknown substance"
        kind = rec.get("type") or "allergy"
        reactions = []
        for r in rec.get("reaction", []) or []:
            m = _text(r.get("manifestation")) or "reaction"
            sev = r.get("severity")
            reactions.append(f"{m} ({sev})" if sev else m)
        status = _text(rec.get("clinicalStatus"))
        return _join(f"{substance} {kind}", reactions and "reaction: " + ", ".join(reactions),
                     rec.get("criticality") and f"criticality {rec['criticality']}",
                     status and status != "active" and status)
    if t == "Condition":
        status = _text(rec.get("clinicalStatus")) or "status not recorded"
        since = _day(rec.get("onsetDateTime")) or _day(rec.get("recordedDate"))
        return f"{_text(rec.get('code')) or 'Problem'} ({status}{f', since {since}' if since else ''})"
    if t == "MedicationRequest":
        med = _text(rec.get("medicationCodeableConcept")) or "medication"
        authored = _day(rec.get("authoredOn"))
        return f"{med} ({rec.get('status', '')}{f', prescribed {authored}' if authored else ''})"
    if t == "MedicationStatement":
        med = _text(rec.get("medicationCodeableConcept")) or "medication"
        dose = _text((rec.get("dosage") or [{}])[0])
        return f"{med}{f', {dose}' if dose else ''} (taking: {rec.get('status', 'unknown')})"
    if t == "Encounter":
        booked = str(rec.get("plannedStartDate") or "")
        when = _date_words((rec.get("period") or {}).get("start") or booked, time=False)
        if rec.get("status") in ("planned", "arrived", "triaged", "in-progress", "onleave") and booked:
            when = _date_words(booked)  # a booking is known by its time: Mon Nov 23, 09:00
        reason = _text(rec.get("reasonCode"))  # clinical: a title (read by every reader) is made without it
        if not reason:  # what it is booked for (not clinical), when the reason is not there to read
            reason = str(((((rec.get("serviceType") or [{}])[0] or {}).get("concept") or {}).get("text")) or "")
            reason = reason.replace("-", " ").capitalize()
        code = (rec.get("class") or {}).get("code") if isinstance(rec.get("class"), dict) else None
        cls = SETTING_WORDS.get(code or "") or _text(rec.get("class"))
        kind = _text(rec.get("type"))
        status = rec.get("status") if rec.get("status") not in ("finished", None) else ""
        return _join(f"Visit {when}".strip(), reason, ", ".join(x for x in (kind, cls) if x),
                     status and STATUS_WORDS.get(status, status))
    if t == "Composition":
        when = _day(rec.get("date"))
        return f"{rec.get('title', 'Note')}{f' {when}' if when else ''} ({rec.get('status', '')})"
    if t == "ServiceRequest":
        return _text(rec.get("code")) or "Referral"
    if t == "DiagnosticReport":
        return _join(f"{_text(rec.get('code')) or 'Report'} {_day(rec.get('effectiveDateTime') or rec.get('issued'))}".strip(),
                     rec.get("conclusion"), rec.get("status"))
    if t == "Immunization":
        when = _day(rec.get("occurrenceDateTime"))
        return f"{_text(rec.get('vaccineCode')) or 'Immunization'}{f' on {when}' if when else ''} ({rec.get('status', '')})"
    if t == "Communication":
        text = " ".join(str(p.get("contentString", "")) for p in rec.get("payload", []) or []).strip()
        if len(text) > 80:
            text = text[:77] + "..."
        return _join(f"Message {_day(rec.get('sent'))}".strip(), _text(rec.get("topic")), text, rec.get("status"))
    if t == "ChargeItem":
        return f"{_text(rec.get('code')) or 'charge'} {(rec.get('priceOverride') or {}).get('value', '')}".strip()
    if t == "Claim":
        total = rec.get("total") or {}
        return _join(f"Claim {_day(rec.get('created'))}".strip(),
                     total and f"total {total.get('value', '')} {total.get('currency', '')}".strip(), rec.get("status"))
    if t == "QuestionnaireResponse":
        return f"Intake form {_day(rec.get('authored'))} ({rec.get('status', '')})".replace(" ()", "")
    return t or "record"


# ------------------------------------------------------------------------------------------------------ subject
def age(born, today: date | None = None) -> int | None:
    """Age in whole years from a FHIR birthDate (YYYY-MM-DD); None when it is missing or partial."""
    try:
        b = date.fromisoformat(str(born)[:10])
    except (TypeError, ValueError):
        return None
    t = today or date.today()
    return t.year - b.year - ((t.month, t.day) < (b.month, b.day))


def _address(a: dict) -> str:
    if a.get("text"):
        return str(a["text"])
    region = " ".join(x for x in (a.get("state"), a.get("postalCode")) if x)
    return ", ".join(x for x in [*(a.get("line") or []), a.get("city"), region, a.get("country")] if x)


def _contact(c: dict) -> str:
    n = c.get("name") or {}
    who = n.get("text") or f"{' '.join(n.get('given', []) or [])} {n.get('family', '')}".strip() or "contact"
    rel = _text(c.get("relationship"))
    phone = ", ".join(t.get("value", "") for t in c.get("telecom", []) or [] if t.get("value"))
    return f"{who}{f' ({rel})' if rel else ''}{f', {phone}' if phone else ''}"


def facts(rec: dict, today: date | None = None) -> dict:
    """``today`` is the practice's date (the hub passes it), so an age turns over at the practice's midnight."""
    gp = ((rec.get("generalPractitioner") or [{}])[0].get("reference") or "").split("/")[-1]
    years = age(rec.get("birthDate"), today)
    return {"born": rec.get("birthDate"), "age": years, "gender": rec.get("gender"),
            "contact": ", ".join(t.get("value", "") for t in rec.get("telecom", []) or []),
            "address": "; ".join(_address(a) for a in rec.get("address", []) or []),
            "emergency contact": "; ".join(_contact(c) for c in rec.get("contact", []) or []) or "none recorded",
            "health card": ", ".join(i.get("value", "") for i in rec.get("identifier", []) or []),
            "physician": gp and f"member:{gp}"}


# ------------------------------------------------------------------------------------------------------ actions
def _when(engine, params) -> dict:
    now = engine.now().replace(microsecond=0)
    out = {"now": now.isoformat(), "today": now.date().isoformat()}
    if params.get("valid_days") not in (None, ""):
        out["valid_until"] = (now.date() + timedelta(days=int(params["valid_days"]))).isoformat()
    if any(params.get(k) not in (None, "") for k in ("start", "end", "minutes")):
        minutes = int(params.get("minutes") or 15)
        end = datetime.fromisoformat(str(params["end"])) if params.get("end") else None
        start = datetime.fromisoformat(str(params["start"])) if params.get("start") else None
        if start is None:
            start = (end or now) - timedelta(minutes=minutes)
        if end is None:
            end = start + timedelta(minutes=minutes)
        out["start"], out["end"] = start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")
    return out


def act_clock_now(engine, run, step, params, actor):
    """The time, for dating records: ``now``, ``today``; with ``valid_days`` a ``valid_until`` date; with ``start``,
    ``end`` or ``minutes`` a visit period (``start``/``end``). Other params pass through (like context.set)."""
    return {**{k: v for k, v in params.items() if k not in ("start", "end", "minutes", "valid_days")},
            **_when(engine, params)}


def act_patient_details(engine, run, step, params, actor):
    """What a pharmacy or specialist needs to identify the patient: name, date of birth, health card; and the
    authorizing physician's name. Also the time, as ``clock.now`` gives it."""
    hub = engine.hub
    ref = str(params.get("patient") or run["record"] or "")
    if not ref.startswith("Patient/"):
        raise ValueError(f"patient.details: {ref!r} is not a patient")
    p = hub.records.read(*ref.split("/", 1))
    if not p:
        raise ValueError(f"patient.details: there is no {ref}")
    card = next((i.get("value") for i in p.get("identifier", []) or []
                 if str(i.get("system", "")).endswith("health-card")), None)
    out = {"name": _name(p), "birth_date": p.get("birthDate") or "not recorded",
           "health_card": card or "not recorded", **_when(engine, params)}
    if params.get("physician"):
        try:
            out["physician_name"] = hub.member(str(params["physician"]))["name"]
        except Exception:  # noqa: BLE001 - the id is still on the prescription
            out["physician_name"] = str(params["physician"])
    return out


def act_notice_send(engine, run, step, params, actor):
    """A notice on the board, at once: to each person on duty holding ``role`` (each by name, so one acknowledging
    does not clear it for the others), or to the member ``to``. ``text`` is the notice's title, which a notice shows
    even where the record is withheld: it never carries clinical detail. ``outbox`` (a message id) ties the notice to
    a message, whose status the notice then shows; ``link`` (a record reference) is linked from it. The output says,
    in words, who was told and what became of the message."""
    hub = engine.hub
    text = str(params.get("text") or "").strip()[:200]
    if not text:
        raise ValueError("notice.send: no text")
    prio = "urgent" if params.get("priority") == "urgent" else "normal"
    page, link = str(params.get("outbox") or ""), str(params.get("link") or "")
    about = f"outbox:{page}" if page else None
    told: list[str] = []
    if params.get("role"):
        rid = str(params["role"])
        people = hub.active_holders(rid, persons_only=True)
        for m in people:
            engine.alert(run, step, text, assignee=m["id"], priority=prio, about=about)
            told.append(m["name"])
        if not people:  # nobody on duty holds it: the practice's escalation role, as for any late work
            engine.alert(run, step, text, role=rid, priority=prio, about=about)
    elif params.get("to"):
        m = hub.db.members.find_one({"_id": str(params["to"])}, fields=["status", "kind", "name"])
        if m and m["status"] == "active" and m["kind"] == "person":  # an agent or a feed reads no board
            iid = engine._notice(run, step, text, assignee=str(params["to"]), priority=prio, about=about)
            if link and not about:
                hub.db.items.set(iid, {"output": {"summary": text, "visit": link}})
            told.append(m["name"])
    out: dict = {"told": told}
    who = ", ".join(told) if told else "the people next in line (nobody on duty holds the role)"
    if page:
        try:
            r = hub._message(page)
        except Exception:  # noqa: BLE001 - a message that is gone: say it was queued, as the page step did
            r = None
        state = {"queued": "is waiting to be sent", "sending": "is being sent", "sent": "was sent",
                 "failed": "failed", "delivered": "was delivered by hand"}.get((r or {}).get("status"), "was queued")
        out["summary"] = f"Told {who} on the board at once; the page (message #{page}) {state}."
        out["page"] = f"message #{page}"
    else:
        out["summary"] = f"Told {who} on the board." if params.get("role") or told else "Nobody to tell on the board."
    return out


ACTIONS = {"clock.now": act_clock_now,
           "notice.send": act_notice_send,
           "patient.details": act_patient_details}
PROVIDERS: dict = {}


# ------------------------------------------------------------------------------------------------------- guards
def guard_visit_coding(values, ctxview):
    problems = []
    fee = FEES.get(values["fee_code"])
    if fee is not None and abs(values["amount"] - fee * values["units"]) > 0.01:
        problems.append(f"{values['fee_code']} x {values['units']} is {fee * values['units']} on the fee schedule, "
                        f"not {values['amount']}")
    if values["fee_code"] != "counselling" and values["units"] != 1:
        problems.append("only counselling is billed in more than one unit")
    return problems


def guard_lab_triage(values, ctxview):
    lab = ((ctxview.get("context") or {}).get("input") or {}).get("flag")
    if lab in SEVERITY and SEVERITY[values["category"]] < SEVERITY[lab]:
        return [f"the lab flagged this result {lab}; it may be raised, never lowered to {values['category']}"]
    return []


SIGNING = re.compile(r"\b(electronically signed|signed by|e-signed)\b", re.I)


def guard_no_signature(values, ctxview):
    """Drafts are drafts: nothing in them may claim to be signed."""
    return [f"{k} claims a signature; drafts are signed by a clinician, in the sign step"
            for k, v in values.items() if isinstance(v, str) and SIGNING.search(v)]


def guard_renewal_prep(values, ctxview):
    """The order a renewal replaces must be this patient's active prescription, copied as written: the workflow marks
    it completed, so an answer may never point it at another record."""
    recs = (ctxview.get("context") or {}).get("record:clinical") or []
    meds = {f"MedicationRequest/{r.get('id')}": r for r in recs if r.get("resourceType") == "MedicationRequest"}
    ref = str(values.get("current_order") or "")
    if not values.get("found_in_chart"):
        return [f"current_order is {ref}, but found_in_chart is not true"] if ref else []
    if ref not in meds:
        return ["current_order must be one of this patient's prescriptions, as MedicationRequest/<id>"]
    order = meds[ref]
    problems = []
    if order.get("status") != "active":
        problems.append(f"{ref} is {order.get('status')}; only an active prescription is renewed")
    disp = order.get("dispenseRequest") or {}
    for key, have in (("quantity", (disp.get("quantity") or {}).get("value")),
                      ("repeats", disp.get("numberOfRepeatsAllowed"))):
        if have is not None and values.get(key) is not None and values[key] != have:
            problems.append(f"{key} is {values[key]}, but {ref} says {have}; copy the order as written")
    return problems


GUARDS = {"visit-coding": guard_visit_coding, "lab-triage": guard_lab_triage, "renewal-prep": guard_renewal_prep,
          "visit-note-draft": guard_no_signature, "referral-letter": guard_no_signature}


# ----------------------------------------------------------------------------------------------------- stand-ins
def _recs(ctx, data_class, rtype=None):
    return [r for r in (ctx["context"].get(f"record:{data_class}") or []) if not rtype or r.get("resourceType") == rtype]


def _patient(ctx):
    return next(iter(_recs(ctx, "demographics", "Patient")), None)


def _answers(qr: dict) -> dict:
    out = {}
    for it in qr.get("item", []) or []:
        ans = (it.get("answer") or [{}])[0]
        out[it.get("linkId")] = next(iter(ans.values()), None) if ans else None
    return out


def standin_intake_review(ctx):
    p = _patient(ctx)
    if not p:
        return {"answer": {}, "escalate": True, "reason": "there is no registration to review"}
    form = next(iter(_recs(ctx, "intake", "QuestionnaireResponse")), None)
    a = _answers(form) if form else {}
    missing = []
    if not any(i.get("system", "").endswith("health-card") for i in p.get("identifier", []) or []):
        missing.append("Nova Scotia health card number")
    if not p.get("birthDate"):
        missing.append("date of birth")
    if not p.get("telecom"):
        missing.append("a phone number or email")
    if not p.get("contact"):
        missing.append("an emergency contact")
    if not form:
        missing.append("the intake form")
    else:
        for key, label in (("medications", "current medications (or 'none')"), ("allergies", "allergies (or 'none')")):
            if not a.get(key):
                missing.append(label)
        if a.get("privacy-consent") is not True:
            missing.append("consent to the privacy notice")
    flags, urgent = [], False
    concern = str(a.get("urgent-concern") or "").strip()
    if concern and concern.lower() not in ("no", "none"):
        urgent = True
        flags.append(f"Says they have an urgent problem: \"{concern}\"")
    if str(a.get("current-physician") or "").strip().lower() not in ("", "none", "no"):
        flags.append(f"Currently has a physician: \"{a['current-physician']}\"")
    who = _name(p)
    summary = (f"{who}'s paperwork is complete." if not missing else
               f"{who}'s paperwork is missing {len(missing)} item(s): {', '.join(missing)}.")
    if flags:
        summary += " " + " ".join(flags)
    return {"complete": not missing, "missing": missing, "flags": flags,
            "urgency": "urgent" if urgent else "routine", "summary": summary}


def standin_slot_proposal(ctx):
    slots = ctx["choices"].get("slot") or []
    if not slots:
        return {"answer": {}, "escalate": True, "reason": "the calendar has no free slot in the next few days"}
    prefs = str(ctx["context"].get("input.preferences") or "").lower()
    p = _patient(ctx) or {}
    gp = ((p.get("generalPractitioner") or [{}])[0].get("reference") or "").split("/")[-1]
    pick = None
    if ctx["context"].get("input.urgency") != "urgent":
        for s in slots:  # a physician named in the preferences, else the patient's own physician
            prov = s["value"].split("|")[1]
            surname = s["label"].rsplit(" ", 1)[-1].lower()
            if (prefs and surname in prefs) or (not prefs and gp and prov == gp):
                pick = s
                break
        for day in ("monday", "tuesday", "wednesday", "thursday", "friday"):
            if day in prefs:
                pick = next((s for s in slots if s["label"].lower().startswith(day[:3])
                             and (pick is None or s["value"].split("|")[1] == pick["value"].split("|")[1])), pick)
        if "afternoon" in prefs:
            pick = next((s for s in slots if int(s["value"][11:13]) >= 12
                         and (pick is None or s["value"][:10] == pick["value"][:10]
                              or s["value"].split("|")[1] == pick["value"].split("|")[1])), pick)
    pick = pick or slots[0]
    return {"slot": pick["value"], "why": f"{pick['label']}: " + ("earliest slot (urgent)"
                                                                if ctx["context"].get("input.urgency") == "urgent"
                                                                else "fits the request")}


SOAP = {"patient": "subjective", "exam": "objective", "assessment": "assessment", "plan": "plan",
        "follow-up": "follow_up"}


def standin_visit_note_draft(ctx):
    """Sorts a labelled transcript ("Patient: ...", "Exam: ...", "Plan: ...") into a SOAP note."""
    transcript = str(ctx["context"].get("input.transcript") or "")
    out = {"subjective": [], "objective": [], "assessment": [], "plan": [], "follow_up": []}
    for line in transcript.splitlines():
        label, _, text = line.partition(":")
        key = SOAP.get(label.strip().lower())
        if key and text.strip():
            out[key].append(text.strip())
    if not out["subjective"] or not out["plan"]:
        return {"answer": {}, "escalate": True,
                "reason": "the transcript has no patient account or no plan I can attribute to the physician"}
    return {k: ("\n".join(v) if k != "follow_up" else "; ".join(v)) for k, v in out.items()}


def standin_visit_coding(ctx):
    vt = ctx["context"].get("input.visit_type") or "minor"
    minutes = int(ctx["context"].get("input.minutes") or 15)
    if vt == "counselling":
        units = max(1, min(4, math.ceil(minutes / 20)))
        code = "counselling"
    else:
        units, code = 1, "complete-assessment" if vt == "complete" else "minor-assessment"
    return {"fee_code": code, "units": units, "amount": FEES[code] * units,
            "note": f"{vt} visit, {minutes} min, dx {', '.join(ctx['context'].get('edit.diagnosis_codes') or [])}"}


def standin_claim_submit(ctx):
    code = (ctx["context"].get("code") or {})
    if not code.get("fee_code"):
        return {"answer": {}, "escalate": True, "reason": "the claim has no coding"}
    return {"outcome": "submit", "comment": f"{code['fee_code']} x {code.get('units')} = {code.get('amount')}, "
                                            "matches the visit record"}


def _med_text(r):
    return ((r.get("medicationCodeableConcept") or {}).get("text") or "")


def standin_renewal_prep(ctx):
    asked = str(ctx["context"].get("input.medication") or "").strip()
    word = asked.split()[0].lower() if asked else ""
    meds = [r for r in _recs(ctx, "clinical", "MedicationRequest")
            if word and word in _med_text(r).lower() and r.get("status") == "active"]
    meds.sort(key=lambda r: r.get("authoredOn", ""), reverse=True)
    visits = sorted((r.get("period", {}).get("start") or r.get("meta", {}).get("lastUpdated", ""))[:10]
                    for r in _recs(ctx, "visits", "Encounter"))
    last_visit = visits[-1] if visits else None
    concerns = []
    for al in _recs(ctx, "clinical", "AllergyIntolerance"):
        if word and word in _text(al.get("code")).lower():
            concerns.append(f"Allergy recorded: {summarize(al)}")
    for ob in _recs(ctx, "clinical", "Observation"):
        if _flag(ob).lower() in ("abnormal", "critical", "high", "low"):
            concerns.append(f"Recent {_flag(ob)} result: {summarize(ob)}")
    if not meds:
        return {"medication": asked, "found_in_chart": False, "last_visit": last_visit, "due_for_visit": True,
                "concerns": concerns + ["No active prescription for this medication in the chart: it cannot be "
                                        "renewed; a new prescription is needed"],
                "summary": f"{asked} is not on the chart as an active prescription; the physician should decide."}
    m = meds[0]
    if len(meds) > 1:
        concerns.append(f"{len(meds)} active prescriptions match \"{asked}\"; renewing the newest")
    last_rx = (m.get("authoredOn") or "")[:10] or None
    today = date.fromisoformat(ctx["run"]["today"]) if (ctx.get("run") or {}).get("today") else date.today()
    due = not last_visit or (today - date.fromisoformat(last_visit)).days > 365
    if asked.lower() != _med_text(m).lower():
        concerns.append(f"Requested \"{asked}\", chart has \"{_med_text(m)}\"")
    disp = m.get("dispenseRequest") or {}
    qty = disp.get("quantity") or {}
    out = {"medication": _med_text(m), "found_in_chart": True, "current_order": f"MedicationRequest/{m['id']}",
           "dosage": _text((m.get("dosageInstruction") or [{}])[0]) or None,
           "quantity": qty.get("value"), "unit": qty.get("unit"),
           "repeats": disp.get("numberOfRepeatsAllowed"),
           "days_supply": (disp.get("expectedSupplyDuration") or {}).get("value"),
           "last_prescribed": last_rx, "last_visit": last_visit, "due_for_visit": due, "concerns": concerns,
           "summary": f"{_med_text(m)}, last prescribed {last_rx or 'on an unknown date'}; last visit "
                      f"{last_visit or 'not on record'}" + ("; due for a visit." if due else ".")}
    if out["quantity"] is None or out["repeats"] is None:
        concerns.append("The current order has no quantity or repeats; the physician must set them")
    return {k: v for k, v in out.items() if v is not None}


def standin_lab_triage(ctx):
    inp = ctx["context"].get("input") or {}
    test = str(inp.get("test", "")).lower()
    prev = [ob for ob in _recs(ctx, "clinical", "Observation")
            if ((ob.get("code") or {}).get("text") or "").lower() == test
            and f"Observation/{ob['id']}" != inp.get("observation")]
    prev.sort(key=lambda o: o.get("effectiveDateTime", ""))
    previous = "; ".join(f"{(o.get('valueQuantity') or {}).get('value')} on {o.get('effectiveDateTime', '')[:10]}"
                         for o in prev) or "no earlier results"
    return {"category": inp.get("flag", "abnormal"), "previous": previous,
            "rationale": f"Lab flag {inp.get('flag')} for {inp.get('test')} {inp.get('value')} {inp.get('unit') or ''}"
                         f" (range {inp.get('range') or 'not given'}). Earlier: {previous}."}


def standin_referral_letter(ctx):
    inp = ctx["context"].get("input") or {}
    p = _patient(ctx)
    if not p or not inp.get("reason"):
        return {"answer": {}, "escalate": True, "reason": "no patient or no reason for referral"}
    meds = [summarize(r) for r in _recs(ctx, "clinical", "MedicationRequest") if r.get("status") == "active"]
    allergies = [summarize(r) for r in _recs(ctx, "clinical", "AllergyIntolerance")]
    results = [r for r in _recs(ctx, "clinical", "Observation")]
    results.sort(key=lambda o: o.get("effectiveDateTime", ""), reverse=True)
    letter = "\n".join([
        f"Dear {inp.get('specialist') or 'colleague'},",
        "",
        f"I am referring {_name(p)} (born {p.get('birthDate', '?')}) to {inp['specialty']}"
        f"{' (urgent)' if inp.get('urgency') == 'urgent' else ''}.",
        "",
        f"Reason for referral: {inp['reason']}",
        f"Current medications: {', '.join(meds) or 'none recorded'}",
        f"Allergies: {', '.join(allergies) or 'none recorded'}",
        f"Recent results: {'; '.join(summarize(r) + ' (' + r.get('effectiveDateTime', '')[:10] + ')' for r in results[:3]) or 'none'}",
        "",
        "Thank you for seeing this patient.",
    ])
    return {"letter": letter, "attachments": [f"Observation/{r['id']}" for r in results[:3]]}


CLINICAL_WORDS = ("pain", "symptom", "result", "medication", "dose", "rash", "fever", "bleeding", "test", "prescription",
                  "side effect", "worse", "sick")


def standin_patient_reply(ctx):
    """The patient-reply-draft prompt block: anything that sounds clinical goes to a nurse."""
    q = str(ctx["context"].get("block-input.question") or "").lower()
    if not q:
        return {"answer": {}, "escalate": True, "reason": "there is no question to answer"}
    if any(w in q for w in CLINICAL_WORDS):
        return {"reply": "Thank you for your message. A member of our care team will be in touch with you shortly.",
                "needs_clinician": True}
    return {"reply": "Thank you for your message. Our front desk is open Monday to Friday; you can book, change or "
                     "cancel an appointment through the portal or by calling us, and forms are at the front desk.",
            "needs_clinician": False}


STANDINS = {
    "intake-review": standin_intake_review,
    "slot-proposal": standin_slot_proposal,
    "visit-note-draft": standin_visit_note_draft,
    "visit-coding": standin_visit_coding,
    "claim-submit": standin_claim_submit,
    "renewal-prep": standin_renewal_prep,
    "lab-triage": standin_lab_triage,
    "referral-letter": standin_referral_letter,
    "block:patient-reply-draft": standin_patient_reply,
}


# ---------------------------------------------------------------------------------------------------------- demo
ICD9 = "http://hl7.org/fhir/sid/icd-9-cm"
CLINICAL_STATUS = "http://terminology.hl7.org/CodeSystem/condition-clinical"
ALLERGY_STATUS = "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical"
INTERPRETATION = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"


def _cc(code: str, system: str, text: str | None = None) -> dict:
    return {"coding": [{"system": system, "code": code}], "text": text or code}


def _patient_rec(given, family, born, gender, *, card=None, phone=None, street="", postal="", contact=None, gp=None):
    rec = {"name": [{"given": [given], "family": family}], "birthDate": born, "gender": gender,
           "address": [{"use": "home", "line": [street] if street else [], "city": "Halifax", "state": "NS",
                        "postalCode": postal, "country": "CA"}]}
    if card:
        rec["identifier"] = [{"system": "https://novascotia.ca/health-card", "value": card}]
    if phone:
        rec["telecom"] = [{"system": "phone", "value": phone}]
    if contact:
        name, rel, tel = contact
        rec["contact"] = [{"relationship": [{"text": rel}], "name": {"text": name},
                           "telecom": [{"system": "phone", "value": tel}]}]
    if gp:
        rec["generalPractitioner"] = [{"reference": f"Practitioner/{gp}"}]
    return rec


def _order(subj, text, dosage, qty, unit, repeats, days, authored, gp="dr-amara"):
    return {"status": "active", "intent": "order", "subject": subj, "authoredOn": authored,
            "medicationCodeableConcept": {"text": text},
            "dosageInstruction": [{"text": dosage}],
            "dispenseRequest": {"quantity": {"value": qty, "unit": unit}, "numberOfRepeatsAllowed": repeats,
                                "expectedSupplyDuration": {"value": days, "unit": "days"}},
            "requester": {"reference": f"Practitioner/{gp}"}}


def demo(hub):
    """Fictional patients and some work in progress, so every inbox has something in it."""
    jo, amara, portal, lab = (hub.member(m) for m in ("jo", "dr-amara", "portal", "lab-feed"))
    now = hub.engine.now().replace(second=0, microsecond=0)  # the practice's wall time, as its calendar keeps it
    today = now.date()

    def ago(days):
        return (today - timedelta(days=days)).isoformat()

    # A patient who asked that her mental-health care be kept from everyone but her own physician (a consent
    # directive, PHIA): the rest of the practice sees that something is withheld at her request, never what.
    rana, liam = hub.member("rana"), hub.member("dr-liam")
    nadia = hub.create_record(jo, "Patient", _patient_rec(
        "Nadia", "Rahimi", "1979-05-08", "female", card="4444 222 111", phone="902-555-0166",
        street="88 Quinpool Road", postal="B3L 1A2", gp="dr-liam"))
    nref = {"reference": f"Patient/{nadia['id']}"}
    hub.create_record(liam, "Condition", {
        "subject": nref, "clinicalStatus": _cc("active", CLINICAL_STATUS),
        "verificationStatus": _cc("confirmed", "http://terminology.hl7.org/CodeSystem/condition-ver-status"),
        "category": [{"text": "problem-list-item"}], "code": _cc("311", ICD9, "Depressive disorder"),
        "onsetDateTime": ago(400), "recordedDate": ago(400), "recorder": {"reference": "Practitioner/dr-liam"}})
    rx = hub.create_record(liam, "MedicationRequest", {**_order(nref, "Sertraline 50 mg daily", "1 tablet by mouth daily",
                                                                30, "tablet", 2, 30, ago(60)), "status": "draft"})
    hub.update_record(liam, f"MedicationRequest/{rx['id']}", {"status": "active"})
    hub.create_record(rana, "Consent", {
        "patient": nref, "status": "active", "received": ago(30),
        "request": "Only Dr. Fraser should see my mental-health care. Not the front desk, not other staff.",
        "withhold_classes": ["clinical"], "allow_members": [{"reference": "Practitioner/dr-liam"}],
        "told": "Other staff, and the practice's agents, will see that information is withheld but not what it is. "
                "In an emergency a physician or nurse may open it; each time is reviewed by the privacy officer."})

    # An established patient with a history: an allergy, an active problem, active medications, results.
    ellen = hub.create_record(jo, "Patient", _patient_rec(
        "Ellen", "MacLeod", "1958-03-14", "female", card="1234 567 890", phone="902-555-0142",
        street="12 Inglis Street", postal="B3H 1K1", contact=("Fiona MacLeod", "Daughter", "902-555-0100"),
        gp="dr-amara"))
    ref = f"Patient/{ellen['id']}"
    subj = {"reference": ref}
    # the chart's history came from the practice's old system: imported as it was, in the states it had reached
    imported = {"id": "import:general-practice", "kind": "automation", "name": "Chart import", "status": "active"}
    for order in (_order(subj, "Ramipril 5 mg daily", "1 capsule by mouth once daily", 90, "capsule", 3, 90, ago(170)),
                  _order(subj, "Atorvastatin 20 mg daily", "1 tablet by mouth once daily at bedtime", 90, "tablet", 3,
                         90, ago(170))):
        rx = hub.create_record(amara, "MedicationRequest", {**order, "status": "draft"})  # written, then issued
        hub.update_record(amara, f"MedicationRequest/{rx['id']}", {"status": "active"})
    hub.create_record(amara, "AllergyIntolerance", {
        "patient": subj, "clinicalStatus": _cc("active", ALLERGY_STATUS),
        "verificationStatus": _cc("confirmed", "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification"),
        "type": "allergy", "category": ["medication"], "criticality": "high", "code": {"text": "Penicillin"},
        "reaction": [{"manifestation": [{"text": "Hives"}], "severity": "moderate"}],
        "recordedDate": ago(3000), "recorder": {"reference": "Practitioner/dr-amara"}})
    hub.create_record(amara, "Condition", {
        "subject": subj, "clinicalStatus": _cc("active", CLINICAL_STATUS),
        "verificationStatus": _cc("confirmed", "http://terminology.hl7.org/CodeSystem/condition-ver-status"),
        "category": [{"text": "problem-list-item"}], "code": _cc("401", ICD9, "Essential hypertension"),
        "onsetDateTime": "2016-05-10", "recordedDate": "2016-05-10", "recorder": {"reference": "Practitioner/dr-amara"}})
    hub.create_record(amara, "Immunization", {
        "status": "completed", "patient": subj, "vaccineCode": {"text": "Influenza vaccine (seasonal)"},
        "occurrenceDateTime": ago(40), "primarySource": True, "performer": [{"actor": {"reference": "Practitioner/dr-amara"}}]})
    hub.create_record(imported, "Observation", {
        "status": "final", "category": [{"text": "laboratory"}], "subject": subj, "code": {"text": "HbA1c"},
        "effectiveDateTime": ago(200), "valueQuantity": {"value": 5.9, "unit": "%"},
        "referenceRange": [{"low": {"value": 4.0, "unit": "%"}, "high": {"value": 6.0, "unit": "%"}, "text": "4.0-6.0 %"}],
        "interpretation": [{**_cc("N", INTERPRETATION), "text": "normal"}]})
    hub.create_record(imported, "Encounter", {
        "status": "finished", "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB",
                                        "display": "ambulatory"},
        "subject": subj, "plannedStartDate": ago(170) + "T10:00", "plannedEndDate": ago(170) + "T10:30",
        "period": {"start": ago(170) + "T10:00", "end": ago(170) + "T10:30"},
        "type": [{"text": "complete"}], "participant": [{"individual": {"reference": "Practitioner/dr-amara"}}],
        "reasonCode": [_cc("401", ICD9, "401")], "length": {"value": 30, "unit": "min"}})

    # Ellen's pharmacy asks for a renewal; a lab result arrives (abnormal, awaiting the physician's review).
    hub.engine.start(portal, "prescription-renewal", record=ref,
                     inputs={"medication": "Ramipril 5 mg", "pharmacy": "Spring Garden Pharmacy, fax 902-555-0177"})
    obs = hub.create_record(lab, "Observation", {
        "status": "registered", "category": [{"text": "laboratory"}], "subject": subj, "code": {"text": "HbA1c"},
        "effectiveDateTime": today.isoformat(), "valueQuantity": {"value": 6.6, "unit": "%"},
        "referenceRange": [{"low": {"value": 4.0, "unit": "%"}, "high": {"value": 6.0, "unit": "%"}, "text": "4.0-6.0 %"}],
        "interpretation": [{**_cc("H", INTERPRETATION), "text": "abnormal"}]})
    obs = hub.update_record(lab, f"Observation/{obs['id']}", {"status": "preliminary"})  # the lab's first report
    hub.engine.emit(lab, "lab.result", record=ref,
                    data={"test": "HbA1c", "value": "6.6", "unit": "%", "range": "4.0-6.0", "flag": "abnormal",
                          "observation": f"Observation/{obs['id']}"})

    # A new patient registers on the portal, with paperwork that is not quite complete (no emergency contact yet).
    omar = hub.create_record(portal, "Patient", _patient_rec("Omar", "Haddad", "1990-07-02", "male",
                                                             card="9876 543 210", phone="902-555-0199",
                                                             street="301 Robie Street", postal="B3L 3B9"))
    hub.create_record(portal, "QuestionnaireResponse", {
        "status": "completed", "subject": {"reference": f"Patient/{omar['id']}"}, "authored": today.isoformat(),
        "item": [{"linkId": "medications", "answer": [{"valueString": "none"}]},
                 {"linkId": "allergies", "answer": [{"valueString": "none"}]},
                 {"linkId": "current-physician", "answer": [{"valueString": "none"}]},
                 {"linkId": "urgent-concern", "answer": [{"valueString": "no"}]},
                 {"linkId": "privacy-consent", "answer": [{"valueBoolean": True}]}]})
    hub.engine.emit(portal, "patient.registered", record=f"Patient/{omar['id']}")

    # A visit just ended with Dr. Okafor.
    priya = hub.create_record(jo, "Patient", _patient_rec(
        "Priya", "Nair", "1984-11-20", "female", card="5555 123 456", phone="902-555-0123",
        street="45 Spring Garden Road", postal="B3J 1E7", contact=("Arjun Nair", "Spouse", "902-555-0124"),
        gp="dr-amara"))
    # she was booked, arrived and was seen; the note's workflow finishes her visit
    visit = _book(hub, jo, priya, "dr-amara", now - timedelta(minutes=15), "Sore throat")
    for st in ("arrived", "in-progress"):
        hub.update_record(amara, f"Encounter/{visit['id']}", {"status": st})
    hub.engine.emit(amara, "visit.ended", record=f"Patient/{priya['id']}", data={
        "appointment": f"Encounter/{visit['id']}", "visit_type": "minor", "minutes": 15,
        "start": (now - timedelta(minutes=15)).isoformat(timespec="minutes"),
        "end": now.isoformat(timespec="minutes"),
        "transcript": "\n".join([
            "Patient: Sore throat for four days, no fever, eating and drinking fine.",
            "Patient: No cough. Partner had a cold last week.",
            "Exam: Temp 37.1. Throat mildly red, no exudate. No neck nodes.",
            "Assessment: Viral pharyngitis.",
            "Plan: Fluids, rest, lozenges, acetaminophen as needed. No antibiotics.",
            "Follow-up: if not better in a week or if fever develops."])})

    # The calendar for the next working days: booked visits with both physicians, and one cancelled.
    hours = (hub.pack.calendar or {}).get("hours", {}) if hub.pack else {}
    days, day = [], today
    while len(days) < 3 and hours:
        day += timedelta(days=1)
        if day.strftime("%a").lower()[:3] in hours:
            days.append(day)
    if days:
        def at(d, hm):
            return datetime.combine(d, datetime.strptime(hm, "%H:%M").time())
        for d, hm, who, patient, reason, kind in (
                (0, "09:00", "dr-amara", ellen, "Blood pressure review", "follow-up"),
                (0, "09:30", "dr-liam", omar, "New patient visit", "check-up"),
                (0, "10:30", "dr-amara", priya, "Follow-up: sore throat", "follow-up"),
                (0, "11:00", "dr-liam", ellen, "Flu shot", "immunization"),
                (1, "09:15", "dr-amara", omar, "Back pain", "new-concern"),
                (1, "14:00", "dr-liam", priya, "Travel vaccines", "immunization"),
                (2, "10:00", "dr-amara", ellen, "Medication review", "prescription-renewal")):
            _book(hub, jo, patient, who, at(days[min(d, len(days) - 1)], hm), reason, kind=kind)
        # Dr. Okafor squeezes Priya in over Ellen's visit: she called with a worsening cough and fever
        hub.create_record(amara, "Encounter", {
            "status": "planned", "class": AMBULATORY, "subject": {"reference": f"Patient/{priya['id']}"},
            "plannedStartDate": at(days[0], "09:00").isoformat(timespec="minutes"),
            "plannedEndDate": at(days[0], "09:15").isoformat(timespec="minutes"),
            "participant": [{"individual": {"reference": "Practitioner/dr-amara"}}],
            "serviceType": [{"concept": {"text": "new-concern"}}], "reasonCode": [{"text": "Cough and fever"}]},
            overbook="worse since the throat visit, fever 39 overnight: seen before the weekend")
        off = _book(hub, jo, omar, "dr-amara", at(days[0], "11:30"), "Annual physical", kind="check-up")
        hub.update_record(jo, f"Encounter/{off['id']}", {"status": "cancelled", "extension": [
            {"url": CANCEL_REASON, "valueString": "the patient called to cancel"}]})


def _book(hub, member, patient, who, start, reason, minutes=15, kind=None):
    """The front desk books the time and what it is for; the reason is clinical, so the physician writes it on the
    visit."""
    visit = hub.create_record(member, "Encounter", {
        "status": "planned", "class": AMBULATORY, "subject": {"reference": f"Patient/{patient['id']}"},
        **({"serviceType": [{"concept": {"text": kind}}]} if kind else {}),
        "plannedStartDate": start.isoformat(timespec="minutes"),
        "plannedEndDate": (start + timedelta(minutes=minutes)).isoformat(timespec="minutes"),
        "participant": [{"individual": {"reference": f"Practitioner/{who}"}}]})
    return hub.update_record(hub.member(who), f"Encounter/{visit['id']}", {"reasonCode": [{"text": reason}]})


# ------------------------------------------------------------------------------------------------------ upgrades
AMBULATORY = {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB", "display": "ambulatory"}
CANCEL_REASON = "https://harbourview.example/fhir/cancellation-reason"
NOTE = "https://harbourview.example/fhir/booking-note"
# a version-1 Appointment's status, as the Visit (FHIR R5 Encounter) it becomes
APPOINTMENT_STATUS = {"proposed": "planned", "pending": "planned", "booked": "planned", "waitlist": "planned",
                      "arrived": "arrived", "checked-in": "arrived", "fulfilled": "finished",
                      "cancelled": "cancelled", "noshow": "noshow", "entered-in-error": "entered-in-error"}


def visit_from_appointment(appt: dict, visit: dict | None = None) -> dict:
    """A version-1 Appointment as a Visit: its booked time, patient, physicians, reason and status; on top of the
    visit that was filed for it, when there was one (that visit keeps what it recorded of the visit itself)."""
    people = [p.get("actor", {}).get("reference", "") for p in appt.get("participant", []) or []]
    patient = next((r for r in people if r.startswith("Patient/")), "")
    staff = [r for r in people if r and not r.startswith("Patient/")]
    out = {k: v for k, v in (visit or {}).items() if k not in ("appointment", "meta", "id", "resourceType")}
    out.setdefault("class", AMBULATORY)
    start = appt.get("start") or ""
    end = appt.get("end") or ""
    if start and not end and appt.get("minutesDuration"):
        end = (datetime.fromisoformat(start[:16]) + timedelta(minutes=int(appt["minutesDuration"]))).isoformat(
            timespec="minutes")
    out["plannedStartDate"] = start
    if end:
        out["plannedEndDate"] = end
    reasons = list(appt.get("reasonCode") or [])
    if appt.get("description"):
        reasons = [{**(reasons[0] if reasons else {}), "text": appt["description"]}] + reasons[1:]
    if visit is not None and reasons and not any(
            isinstance(r, dict) and (r.get("text") or r.get("coding")) for r in out.get("reasonCode") or []):
        out["reasonCode"] = reasons  # the visit says nothing of why: the booking's reason is kept, never lost
    if visit is None:
        out["status"] = APPOINTMENT_STATUS.get(appt.get("status") or "", "planned")
        out["subject"] = {"reference": patient}
        out["participant"] = [{"individual": {"reference": r}} for r in staff]
        if reasons:
            out["reasonCode"] = reasons
        if appt.get("serviceType"):
            out["type"] = appt["serviceType"]
        if out["status"] == "finished" and appt.get("minutesDuration"):
            out["length"] = {"value": appt["minutesDuration"], "unit": "min"}
    ext = list(out.get("extension") or [])
    why = appt.get("cancelationReason") or appt.get("cancellation_reason")
    if isinstance(why, dict):
        why = why.get("text") or ", ".join(c.get("display") or c.get("code", "") for c in why.get("coding", []) or [])
    if why:
        ext.append({"url": CANCEL_REASON, "valueString": str(why)})
    if appt.get("comment"):
        ext.append({"url": NOTE, "valueString": str(appt["comment"])})
    if ext:
        out["extension"] = ext
    return out


def _first_appointment(hub, v: dict) -> str | None:
    """The appointment a visit was filed for: the first one it names that is still there."""
    return next((a.get("reference") for a in v.get("appointment", []) or [] if isinstance(a, dict)
                 and str(a.get("reference") or "").startswith("Appointment/")
                 and hub.records.read("Appointment", a["reference"].split("/", 1)[1])), None)


def upgrade_appointments_to_visits(hub, actor):
    """Version 2 merges Appointment into Visit. Once the Visit that is a Booking is in force: an appointment a visit
    was filed for becomes that visit (whatever pointed at the appointment points at it, open work included); any other
    appointment becomes a visit with the same id; then every visit gets its booked time; the Appointment type is
    retired. None until the new Visit is approved.

    It works in batches of ``hub.UPGRADE_BATCH`` (each a transaction the hub writes with the upgrade's place): the
    appointments still there first (each batch converts some, so the next finds the rest), then the visits to date, in
    id order from where the last batch stopped (``hub.upgrade_state``). A batch converts or dates each record whole, so
    stopping between batches loses nothing: an appointment not converted yet is still an appointment."""
    rt = hub.record_type("Encounter")
    if rt is None or not rt.is_a("Booking") or rt.field("plannedStartDate") is None:
        return None
    size = max(1, int(getattr(hub, "UPGRADE_BATCH", 200) or 200))
    state = hub.upgrade_state
    appts = hub.records.search("Appointment", limit=size)
    if appts:
        refs = {f"Appointment/{a['id']}" for a in appts}
        filed: dict = {}  # Appointment/<id> -> the visit filed for it (the newest that names it first)
        for row in hub.db.records.find({"type": "Encounter", "data.appointment.reference": {"$in": sorted(refs)}},
                                       sort=[("updated_at", -1)], fields=["data"]):
            ref = _first_appointment(hub, row["data"])
            if ref in refs and ref not in filed:
                filed[ref] = row["data"]
        merged, made, moved = [], [], {}
        for a in appts:
            ref = f"Appointment/{a['id']}"
            visit = filed.get(ref)
            if visit is not None:
                hub.convert_record(actor, ref, "Encounter", visit_from_appointment(a, visit),
                                   into=f"Encounter/{visit['id']}", repoint=False)
                merged.append(ref)
                moved[ref] = f"Encounter/{visit['id']}"
            else:
                hub.convert_record(actor, ref, "Encounter", visit_from_appointment(a), repoint=False)
                made.append(ref)
                moved[ref] = f"Encounter/{a['id']}"
        hub.repoint_work(moved)  # runs, items and messages under way, once for the batch
        return {"more": True, "visits_dated": 0, "appointments_merged_into_their_visit": merged,
                "appointments_now_visits": made, "appointment_type_retired": False}
    after = state.get("dated_after") or ""
    rows = hub.db.records.find({"type": "Encounter", "_id": {"$gt": after}, "$or": [
        {"data.plannedStartDate": {"$in": [None, ""]}}, {"data.appointment": {"$exists": True}}]},
        sort="_id", limit=size, fields=["data"])
    for row in rows:
        v = row["data"]
        period = v.get("period") or {}
        hub.update_record(actor, f"Encounter/{v['id']}", {
            "plannedStartDate": v.get("plannedStartDate") or period.get("start")
            or (v.get("meta") or {}).get("lastUpdated", "")[:16],
            "plannedEndDate": v.get("plannedEndDate") or period.get("end"), "appointment": None})
        state["dated_after"] = row["_id"]
    if len(rows) == size:
        return {"more": True, "visits_dated": len(rows), "appointments_merged_into_their_visit": [],
                "appointments_now_visits": [], "appointment_type_retired": False}
    retired = False
    if hub._active_row("type", "Appointment") is not None:
        hub._retire_now(actor, "type", "Appointment", "merged into Visit (pack version 2)")
        retired = True
    return {"visits_dated": len(rows), "appointments_merged_into_their_visit": [], "appointments_now_visits": [],
            "appointment_type_retired": retired}


def upgrade_diagnosis_apart_from_reason(hub, actor):
    """Version 3 keeps what was billed (diagnosis) apart from why the visit was booked (reasonCode): before, finishing a
    visit wrote the diagnosis codes over the booked reason. Once the Visit with a diagnosis is in force, each visit
    whose reason is the codes ("ICD-9 ...") gets them as its diagnosis, and its reason back from the version before
    the codes were written (none, if it never had another). None until the new Visit is approved.

    It works in batches of ``hub.UPGRADE_BATCH``: a visit done has a diagnosis, so the next batch finds the rest."""
    rt = hub.record_type("Encounter")
    if rt is None or rt.field("diagnosis_text") is None:
        return None
    moved, restored, more = [], [], False
    for v in hub.records.search("Encounter", limit=None):
        reasons = [r for r in v.get("reasonCode") or [] if isinstance(r, dict)]
        codes = [r for r in reasons if str(r.get("text") or "").startswith("ICD-9 ")]
        if not codes or v.get("diagnosis"):
            continue
        if len(moved) >= hub.UPGRADE_BATCH:
            more = True
            break
        earlier = None
        for row in hub.db.record_versions.find({"type": "Encounter", "id": v["id"]}, sort=[("version", -1)],
                                               fields=["data"]):
            had = [r for r in (row["data"] or {}).get("reasonCode") or [] if isinstance(r, dict)]
            if had and not any(str(r.get("text") or "").startswith("ICD-9 ") for r in had):
                earlier = had
                break
        hub.update_record(actor, f"Encounter/{v['id']}", {
            "diagnosis": [{"condition": [{"concept": {"text": r["text"]}}]} for r in codes],
            "reasonCode": earlier or [r for r in reasons if r not in codes] or None})
        moved.append(f"Encounter/{v['id']}")
        if earlier:
            restored.append(f"Encounter/{v['id']}")
    return {"visits_with_diagnosis_apart": moved, "booked_reason_restored": restored, "more": more}


UPGRADES = {"2-appointments-are-visits": upgrade_appointments_to_visits,
            "3-diagnosis-apart-from-reason": upgrade_diagnosis_apart_from_reason}
