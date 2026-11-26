"""The online booking portal: what the people a practice serves see of it, from the internet (``docs/portal.md``).

This is the hub's side of it. The pages are a process of their own (``practicehub/web/portal_app.py``,
``practicehub portal serve``) that holds no database and no secret of the hub's: it renders, and forwards what a
person sends to the hub's ``/api/portal/`` (``web/portal_api.py``) with a token of the portal's service member
(scope ``portal``). Codes, sessions, counts, bookings and the track all stay here. The portal acts as that member
(``[portal] member``): it does only what that member's roles may do, so it writes contact details and starts the
workflows the practice lets it start, and never reads what the practice keeps from it. Everything here is the pack's
to say, in ``[portal]``, which is under the pack's change control (two policy owners) like the rest of its settings:

* ``kinds``: what a person may book. Each kind has a length (``minutes``), the roles whose calendars it books, how far
  ahead (``horizon_days``), whether it books straight away (``mode = "book"``) or goes in as a request (``"request"``),
  the workflow it starts, the questions it asks (that workflow's own inputs) and what it says back. A kind can be
  about one of the person's own records (``about``): they choose which, from those of its type about them where its
  condition holds, and the workflow is given that record.
* ``identity``: how a person proves who they are: the date of birth field of the type whose records sign in (``type``:
  the practice's subject type, or a type under it, as individuals are under clients; its records and those of every
  type under it sign in, and a new registration is made as it), its identifier (the core's ``Subject.identifier``) and
  a one-time code sent to its contact (``Subject.contact``).
* ``register``: the fields a new person gives, the values that keep their record from signing in or booking until
  the practice has taken them in (``pending``), and ``intake_workflow``, the workflow that takes them in.

A question a person answers in their own words is screened: its kind names the step of its workflow that reads it
(``screen``) and says when the answer must be told loudly (``alarm``); a kind booked straight away books only after
that step (``booked`` runs after ``screen`` and its ``when`` reads it).

Every booking starts a workflow (``Portal._book``): the portal never writes a booking itself. A cancellation is a
move of the booking to the kind's ``cancel_to`` state (``Portal._cancel``), made only where the booking's own workflow
is waiting on that move (``Hub.state_move_refusal``). The core names no pack, type or workflow here.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import date, datetime, timedelta, timezone

from . import expr, schema

KINDS_KEYS = {"label", "description", "mode", "workflow", "roles", "minutes", "horizon_days", "slot", "slot_input",
              "inputs", "questions", "advice", "alarm", "booked", "booking_kind", "cancel_to",
              "cancel_before_hours", "max_slots", "done", "screen", "max_upcoming", "about"}
ABOUT_KEYS = {"type", "when", "input"}
ABOUT_LIMIT = 50           # the person's records of the kind's type a choice is made from, newest first
TOP_KEYS = {"member", "title", "welcome", "privacy", "contact_line", "wording", "identity", "code", "register",
            "intake_workflow", "session_minutes", "kinds", "public_origin", "per_minute", "over_limit"}
OVER_LIMIT_KEYS = {"workflow", "input"}
QUESTION_KEYS = {"input", "label", "help", "choices"}
FREE_TEXT = ("string", "text")    # an input a person fills in their own words: screened
MODES = ("book", "request")
SLOTS = ("required", "optional", "none")
ID = re.compile(r"[a-z][a-z0-9-]*")

CODE_MINUTES = 10          # how long a one-time code holds, unless [portal.code] minutes says
CODE_LENGTH = 6
CODE_ATTEMPTS = 5          # wrong codes against one sign-in before it is dead (the address and the person lock too)
SESSION_MINUTES = 30
CLIENT_LIMIT = 5           # failed attempts in the lockout window from one address
PERSON_LIMIT = 10          # ... on one identifier, from anywhere
REGISTER_LIMIT = 5         # registration requests from one address in the window
SENDS = ((15 * 60, 3), (24 * 3600, 10))  # codes sent to one record: 3 a quarter hour, 10 a day (more: none, same words)
PER_MINUTE = 60            # sign-ins and registrations begun, from everyone, in one minute ([portal] per_minute)
MAX_UPCOMING = 2           # a person's open bookings (or requests) of one kind, unless the kind's max_upcoming says
SUBMIT_HOURS = 24          # how long a form's answer is kept, so a second tap or a refresh lands on the same page

IDENTITY_KEYS = {"type", "born", "born_label", "identifier_label", "inputmode", "digits", "identifier_help", "born_help"}
REGISTER_FIELD_KEYS = {"field", "label", "help", "required"}

WORDING = {  # what the pages say, unless [portal.wording] says otherwise
    "booking": "Booking",
    "sent": "If what you gave matches our records, we have sent a code to the contact details we have for you. "
            "It expires in {minutes} minutes.",
    "no_match": "That code is not right, or it has expired. Ask for a new one.",
    "duplicate": "You already have an open request with us. We will be in touch about it; you do not need to ask "
                 "again.",
    "clash": "Someone has just taken that time. Here are the times free now.",
    "requested": "We have your request. We will contact you about it.",
    "booked": "You are booked on {when} with {with}.",
    "registered": "Thank you. We have your details and will contact you.",
    "cancelled": "Your booking on {when} is cancelled.",
    "emergency": "If this is an emergency, call 911 or go to the nearest emergency department now.",
    "working": "We are still taking your request. This page updates in a moment.",
    "too_many": "You already have as many of these booked or asked for as can be done online. Call the practice to "
                "book more.",
    "safety": "If this is an emergency, call 911 or go to the nearest emergency department now.",
    "at_limit": "You already have {open} of these open, the most that can be made online. You can still write to us "
                "here: a person at the practice reads it.",
    "over_limit": "You already have as many of these open as can be made online, so nothing new was booked. A person "
                  "at the practice has what you wrote and will contact you.",
    "busy": "Many people are using this just now. Try again in a minute.",
    "failed": "We could not book that time. The practice has been told and will contact you.",
    "closed": "Online booking is closed for now. Call the practice.",
    "visit_next": "Come to the practice a few minutes before your time.",
    "request_next": "Someone at the practice contacts you to arrange a time.",
    "no_cancel_soon": "Less than {hours} hours away: call the practice to cancel or change it.",
    "no_cancel": "This cannot be cancelled online: call the practice to cancel or change it.",
    "about": "Which one this is about",
    "choose_about": "Choose which one this is about.",
    "nothing_about": "Nothing of yours is waiting for this just now. If you expected something, call the practice.",
}


class Clash(Exception):
    """The time chosen is no longer free; ``slots`` are the ones free now."""

    def __init__(self, slots: list[dict]):
        super().__init__("that time is no longer free")
        self.slots = slots


# ------------------------------------------------------------------------------------------ the pack's [portal]
def config(pack) -> dict:
    """The practice's ``[portal]``, with the kinds its modules add (a module adds kinds, never changes the rest)."""
    if pack is None:
        return {}
    cfg = dict(pack.portal or {})
    kinds = dict(cfg.get("kinds") or {})
    for m in pack.modules:
        for kid, k in ((m.portal or {}).get("kinds") or {}).items():
            kinds.setdefault(kid, k)
    if kinds:
        cfg["kinds"] = kinds
    return cfg


def _type_doc(pack, tid: str) -> dict:
    import tomllib
    for p in pack.all_parts():
        src = p.type_sources.get(tid)
        if src:
            try:
                return tomllib.loads(src)
            except tomllib.TOMLDecodeError:
                return {}
    return {}


def _chain_docs(pack, tid: str) -> list[tuple[str, dict]]:
    """``tid``'s source and those of the pack's types above it (``extends``), its own first: a type has its parents'
    fields and their ``[base]``."""
    out: list[tuple[str, dict]] = []
    while tid and tid not in (t for t, _ in out):
        doc = _type_doc(pack, tid)
        if not doc:
            break
        out.append((tid, doc))
        tid = doc.get("extends")
    return out


def _workflow(pack, wid: str):
    """The pack's workflow ``wid``, parsed; None when there is none; a list of problems when it does not parse."""
    from .workflows import DefinitionError, parse
    for p in pack.all_parts():
        src = p.workflow_sources.get(wid)
        if src:
            try:
                return parse(src)
            except DefinitionError as exc:
                return [str(x) for x in exc.args[0]] if exc.args and isinstance(exc.args[0], list) else [str(exc)]
    return None


def _fields_of(doc: dict) -> set[str]:
    return {f.get("name") for f in doc.get("field", []) or [] if isinstance(f, dict)}


def check_config(pack) -> list[str]:
    """Every problem with the pack's ``[portal]``, in words, each saying where: empty when it is right (or absent)."""
    cfg = config(pack)
    if not cfg:
        return []
    problems: list[str] = []
    for k in sorted(set(cfg) - TOP_KEYS):
        problems.append(f"{k}: not a portal setting (settings: {', '.join(sorted(TOP_KEYS))})")
    roles = pack.role_ids()
    members = {m.get("id"): m for p in pack.all_parts() for m in p.members}
    mid = cfg.get("member")
    portal_roles: set[str] = set()
    if not mid or mid not in members:
        problems.append(f"member: name the service member the portal acts as (one of the pack's members), not {mid!r}")
    else:
        if members[mid].get("kind") != "service":
            problems.append(f"member: {mid} is a {members[mid].get('kind')}: the portal acts as a service")
        portal_roles = set(members[mid].get("roles") or [])
    for key in ("title", "welcome", "privacy", "contact_line"):
        if key in cfg and not isinstance(cfg[key], str):
            problems.append(f"{key}: words, in quotes")
    if not str(cfg.get("privacy") or "").strip():
        problems.append("privacy: say, in a line, what is done with what people give here (every page shows it)")
    wording = cfg.get("wording") or {}
    if not isinstance(wording, dict):
        problems.append("wording: a table of what the pages say")
    else:
        problems += [f"wording.{k}: not something the portal says (it says: {', '.join(sorted(WORDING))})"
                     for k in sorted(set(wording) - set(WORDING))]
    po = cfg.get("public_origin")
    if po is not None and not re.fullmatch(r"https://[a-z0-9.-]+(:\d+)?", str(po)):
        problems.append("public_origin: the address people reach the portal at, as https://host (no path)")
    try:
        if not 1 <= int(cfg.get("per_minute", PER_MINUTE)) <= 10000:
            problems.append("per_minute: between 1 and 10000")
    except (TypeError, ValueError):
        problems.append("per_minute: a whole number")
    try:
        session = int(cfg.get("session_minutes", SESSION_MINUTES))
        if not 5 <= session <= 120:
            problems.append("session_minutes: between 5 and 120")
    except (TypeError, ValueError):
        problems.append("session_minutes: a number of minutes")

    subject = (pack.subject or {}).get("type")
    ident = cfg.get("identity") or {}
    if not isinstance(ident, dict):
        problems.append("identity: a table ([portal.identity])")
        ident = {}
    who = ident.get("type") or subject  # the type whose records sign in
    chain = _chain_docs(pack, who) if isinstance(who, str) and who else []
    if "type" in ident and (not chain or subject not in (t for t, _ in chain)):
        problems.append(f"identity.type: the practice's type whose records sign in, {subject or 'the subject type'} "
                        f"or a type under it, not {ident['type']!r}")
        who, chain = subject, _chain_docs(pack, subject) if subject else []
    elif chain and chain[0][1].get("abstract") and cfg.get("register"):
        problems.append(f"identity.type: {who} is abstract, and a person who registers online is made as the type "
                        f"that signs in: name one of the types under it ([portal.identity] type)")
    sdoc = chain[0][1] if chain else {}
    sfields = set().union(*(_fields_of(d) for _, d in chain)) if chain else set()
    subject = who
    up = [t for t, _ in chain] or ([who] if who else [])  # the type that signs in and the pack's types above it
    born = ident.get("born")
    if not born or born not in sfields:
        problems.append(f"identity.born: the field of {subject or 'the subject type'} that holds the date of birth, "
                        f"not {born!r}")
    for key in set(ident) - IDENTITY_KEYS:
        problems.append(f"identity.{key}: not a setting ({', '.join(sorted(IDENTITY_KEYS))})")
    if "digits" in ident and (not isinstance(ident["digits"], int) or isinstance(ident["digits"], bool)
                              or not 1 <= ident["digits"] <= 30):
        problems.append("identity.digits: how many digits the identifier has (1 to 30), checked on the sign-in form")
    if ident.get("inputmode") not in (None, "text", "numeric"):
        problems.append("identity.inputmode: text or numeric (the keyboard a phone shows for the identifier)")
    base = next((d["base"] for _, d in chain if isinstance(d.get("base"), dict)), {})  # set at the top of its chain
    for need in ("identifier", "contact"):
        if sdoc and not base.get(need):
            problems.append(f"identity: {subject} keeps no {need} for the core's Subject ([base] {need}): a person "
                            f"proves who they are by it")
    code = cfg.get("code") or {}
    if not isinstance(code, dict):
        problems.append("code: a table ([portal.code])")
        code = {}
    if not re.fullmatch(r"[a-z0-9_-]+", str(code.get("channel") or "")):
        problems.append("code.channel: the outbox channel a one-time code goes out on (sms, email)")
    for key, lo, hi in (("minutes", 2, 30), ("length", 6, 10)):
        if key in code:
            try:
                if not lo <= int(code[key]) <= hi:
                    problems.append(f"code.{key}: between {lo} and {hi}")
            except (TypeError, ValueError):
                problems.append(f"code.{key}: a number")

    def wf_problem(where: str, wid, record=None):
        wf = _workflow(pack, wid) if isinstance(wid, str) else None
        if wf is None:
            problems.append(f"{where}: there is no workflow {wid!r}")
            return None
        if isinstance(wf, list):
            problems.append(f"{where}: the workflow {wid} does not read: {'; '.join(wf)}")
            return None
        if portal_roles and not set(wf.starters) & portal_roles:
            problems.append(f"{where}: {mid} may not start {wid} (its trigger.starters name none of its roles)")
        if record is not None and wf.record not in record and not wf.new_record:
            # a workflow about another type is started on the person only when it makes its own record ([new_record]);
            # one about a type above theirs runs on them as on any of its kinds
            problems.append(f"{where}: {wid} is about a {wf.record}, not a {record[0]}, and makes no record of its "
                            f"own ([new_record]) about one")
        return wf

    reg = cfg.get("register")
    if reg is not None:
        if not isinstance(reg, dict) or not isinstance(reg.get("fields"), list) or not reg.get("fields"):
            problems.append("register.fields: the fields of the subject type a new person gives")
        else:
            for f in reg["fields"]:
                if isinstance(f, dict):
                    problems += [f"register.fields: {x} is not a setting of a field "
                                 f"({', '.join(sorted(REGISTER_FIELD_KEYS))})" for x in sorted(set(f) - REGISTER_FIELD_KEYS)]
                    if "required" in f and not isinstance(f["required"], bool):
                        problems.append(f"register.fields: required is true or false, not {f['required']!r}")
                    f = f.get("field")
                if f not in sfields:
                    problems.append(f"register.fields: {subject} has no field {f!r}")
        pend = reg.get("pending") if isinstance(reg, dict) else None
        if not isinstance(pend, dict) or not pend:
            problems.append("register.pending: the field values a new registration gets so that it cannot sign in or "
                            "book until the practice has taken the person in (for example active = false)")
        else:
            problems += [f"register.pending: {subject} has no field {f!r}" for f in pend if f not in sfields]
    if cfg.get("intake_workflow"):
        wf_problem("intake_workflow", cfg["intake_workflow"], up)
        if reg is None:
            problems.append("register: say the fields a new person gives ([portal.register] fields), or leave out "
                            "intake_workflow")

    ol = cfg.get("over_limit")
    if ol is not None:
        if not isinstance(ol, dict):
            problems.append("over_limit: a table ([portal.over_limit]): the workflow that takes what a person wrote "
                            "when they may book no more online, and its input for it")
        else:
            problems += [f"over_limit.{x}: not a setting (settings: {', '.join(sorted(OVER_LIMIT_KEYS))})"
                         for x in sorted(set(ol) - OVER_LIMIT_KEYS)]
            wf = wf_problem("over_limit.workflow", ol.get("workflow"), up)
            if wf is not None:
                problems += over_limit_problems(wf, ol.get("input"))

    kinds = cfg.get("kinds")
    if not isinstance(kinds, dict) or not kinds:
        problems.append("kinds: at least one kind of booking ([portal.kinds.<id>])")
        kinds = {}
    slot_minutes = int((pack.calendar or {}).get("slot_minutes", 15) or 15)
    for kid, k in kinds.items():
        w = f"kinds.{kid}"
        if not ID.fullmatch(str(kid)):
            problems.append(f"{w}: an id is lower-case letters, digits and dashes")
        if not isinstance(k, dict):
            problems.append(f"{w}: a table")
            continue
        problems += [f"{w}.{x}: not a setting of a kind (settings: {', '.join(sorted(KINDS_KEYS))})"
                     for x in sorted(set(k) - KINDS_KEYS)]
        if not str(k.get("label") or "").strip():
            problems.append(f"{w}.label: what a person sees it as")
        mode = k.get("mode")
        if mode not in MODES:
            problems.append(f"{w}.mode: book (straight away) or request (it goes in as a request), not {mode!r}")
        slot = k.get("slot", "required" if mode == "book" else "optional")
        if slot not in SLOTS:
            problems.append(f"{w}.slot: one of {', '.join(SLOTS)}")
        if mode == "book" and slot != "required":
            problems.append(f"{w}.slot: a kind booked straight away needs a time (slot = \"required\")")
        rl = k.get("roles")
        if slot != "none":
            if not isinstance(rl, list) or not rl:
                problems.append(f"{w}.roles: whose calendars it books (roles)")
            else:
                problems += [f"{w}.roles: there is no role {r!r}" for r in rl if r not in roles]
        for key, lo, hi in (("minutes", 5, 480), ("horizon_days", 1, 365), ("max_slots", 1, 200),
                            ("max_upcoming", 1, 20)):
            if key in k or (key in ("minutes", "horizon_days") and slot != "none"):
                try:
                    v = int(k.get(key))
                    if not lo <= v <= hi:
                        problems.append(f"{w}.{key}: between {lo} and {hi}")
                    elif key == "minutes" and v % slot_minutes:
                        problems.append(f"{w}.minutes: a multiple of the calendar's slot ({slot_minutes} minutes)")
                except (TypeError, ValueError):
                    problems.append(f"{w}.{key}: a whole number")
        for key in ("alarm",):
            if k.get(key):
                try:
                    expr.check(str(k[key]))
                except expr.ExprError as exc:
                    problems.append(f"{w}.{key}: {exc}")
        wf = wf_problem(f"{w}.workflow", k.get("workflow"), up)
        if wf is not None:
            names = {i["name"]: i for i in wf.inputs}
            fixed = k.get("inputs") or {}
            if not isinstance(fixed, dict):
                problems.append(f"{w}.inputs: a table of input = value, given to the workflow as they are")
                fixed = {}
            qs = []
            for q in k.get("questions") or []:
                name = q if isinstance(q, str) else q.get("input") if isinstance(q, dict) else None
                if not name:
                    problems.append(f"{w}.questions: each is an input's name, or {{ input = ..., label = ... }}")
                    continue
                qs.append(name)
                if isinstance(q, dict):
                    problems += [f"{w}.questions.{name}.{x}: not a setting of a question (settings: "
                                 f"{', '.join(sorted(QUESTION_KEYS))})" for x in sorted(set(q) - QUESTION_KEYS)]
                    words = q.get("choices")
                    if words is not None:
                        have = [str(c) for c in (names.get(name) or {}).get("choices") or []]
                        if not isinstance(words, dict) or not have:
                            problems.append(f"{w}.questions.{name}.choices: the words a person sees for each of "
                                            f"{wf.id}'s choices ({{ choice = \"words\" }}), for a choice only")
                        else:
                            problems += [f"{w}.questions.{name}.choices: {wf.id} has no choice {c!r}"
                                         for c in sorted(set(words) - set(have))]
            problems += kind_problems(w, k, wf, qs)
            given = {*fixed, *qs}
            ab = about_problems(w, k.get("about"), pack, up, names, given) if "about" in k else None
            problems += ab or []
            if ab == []:
                given.add(k["about"]["input"])
            if slot != "none":
                si = k.get("slot_input", "slot")
                given.add(si)
                if si not in names:
                    problems.append(f"{w}.slot_input: {wf.id} has no input {si!r} to give the time to")
            problems += [f"{w}: {wf.id} has no input {n!r}" for n in sorted({*fixed, *qs} - set(names))]
            missing = [n for n, i in names.items() if i.get("required") and i.get("default") is None and n not in given]
            if missing:
                problems.append(f"{w}: {wf.id} needs {', '.join(missing)}: ask it (questions) or give it (inputs)")
            for key in ("advice", "booked"):
                if k.get(key) and str(k[key]).split(".")[0] not in {s.id for s in wf.steps}:
                    problems.append(f"{w}.{key}: {wf.id} has no step {str(k[key]).split('.')[0]!r}")
        if mode == "book" and not k.get("booked"):
            problems.append(f"{w}.booked: the step of {k.get('workflow')} that books it (its output says when)")
        if k.get("cancel_to"):
            # a move of the booking its workflow walks, to a state a person may move it to (the hub refuses it unless
            # that workflow is waiting on it then)
            wf = _workflow(pack, k.get("workflow") or "")
            about = getattr(wf, "record", None) if wf is not None and not isinstance(wf, list) else None
            life = (_type_doc(pack, about).get("lifecycle") or {}) if about else {}
            tos = {t.get("to") for t in life.get("transition") or [] if t.get("by") != "workflow"}
            if not about or not life:
                problems.append(f"{w}.cancel_to: {k.get('workflow')} walks no record with a lifecycle to cancel")
            elif k["cancel_to"] not in tos:
                problems.append(f"{w}.cancel_to: a {about} is not moved to {k['cancel_to']!r} by a person")
            if not k.get("booking_kind"):
                problems.append(f"{w}.booking_kind: what its bookings are for (the Booking's kind), so the portal "
                                f"knows which may be cancelled")
            try:
                float(k.get("cancel_before_hours", 0))
            except (TypeError, ValueError):
                problems.append(f"{w}.cancel_before_hours: a number of hours")
    return problems


def about_problems(w: str, ab, pack, up: list[str], names: dict, given: set) -> list[str]:
    """A kind's ``about = { type, when, input }``: a record type about the type that signs in (a ``rel = "subject"``
    reference to it or to a type above it; ``up`` is that type, then the pack's types above it), a condition over ``record.`` (and ``today``) that says which of a person's records of it the
    kind is for, and the workflow's reference input the chosen one goes to (not given otherwise)."""
    if not isinstance(ab, dict):
        return [f"{w}.about: a table {{ type = ..., when = ..., input = ... }}: which of the person's own records the "
                f"kind is about"]
    out = [f"{w}.about.{x}: not a setting (settings: {', '.join(sorted(ABOUT_KEYS))})"
           for x in sorted(set(ab) - ABOUT_KEYS)]
    subject = up[0] if up else None
    tid = ab.get("type")
    chain = _chain_docs(pack, tid) if isinstance(tid, str) else []
    if not chain:
        out.append(f"{w}.about.type: there is no record type {tid!r}")
    elif not any(f.get("rel") == "subject" and set(up) & set([f["target"]] if isinstance(f.get("target"), str)
                                                              else f.get("target") or [])
                 for _, doc in chain for f in doc.get("field") or [] if isinstance(f, dict)):
        out.append(f"{w}.about.type: a {tid} is not about a {subject} (no rel = \"subject\" reference to one): a "
                   f"person chooses from their own")
    when = ab.get("when")
    if when is not None:
        try:
            expr.check(str(when))
            out += [f"{w}.about.when: reads {x}; it reads the record (record.<field>) and today"
                    for x in expr.paths(str(when)) if x.split(".")[0] not in ("record", "today")]
        except expr.ExprError as exc:
            out.append(f"{w}.about.when: {exc}")
    name = ab.get("input")
    i = names.get(name) if isinstance(name, str) else None
    if not i or i.get("type") != "reference":
        out.append(f"{w}.about.input: the reference input of the kind's workflow the chosen record goes to, not "
                   f"{name!r}")
    elif name in given:
        out.append(f"{w}.about.input: {name} is given by the kind already (inputs or questions); the person's choice "
                   f"gives it")
    elif isinstance(tid, str) and i.get("target") and not {t for t, _ in chain} & set(i["target"]):
        out.append(f"{w}.about.input: {name} takes a {' or '.join(i['target'])}, not a {tid}")
    return out


def over_limit_problems(wf, name) -> list[str]:
    """``[portal.over_limit]`` against its workflow: it takes what the person wrote in an input in their own words,
    which one of its steps screens, and it needs nothing else."""
    import json
    names = {i["name"]: i for i in wf.inputs}
    if name not in names or (names[name].get("type") or "string") not in FREE_TEXT:
        return [f"over_limit.input: the input of {wf.id} that takes what the person wrote, in their own words, not "
                f"{name!r}"]
    out = []
    if not any(s.block and f"input.{name}" in json.dumps(s.params or {}, default=str) for s in wf.steps):
        out.append(f"over_limit.workflow: no step of {wf.id} screens input.{name} for red flags")
    missing = [n for n, i in names.items() if i.get("required") and i.get("default") is None and n != name]
    if missing:
        out.append(f"over_limit.workflow: {wf.id} needs {', '.join(missing)} as well: the portal gives it only "
                   f"what the person wrote")
    return out


def kind_problems(w: str, k: dict, wf, qs: list[str]) -> list[str]:
    """A kind against the workflow it starts (the pack's, or the hub's active definition): every question a person
    answers in their own words is read by the kind's ``screen`` step, the kind says when the answer is loud
    (``alarm``), and a kind booked straight away books only after that screen and on what it found."""
    out: list[str] = []
    names = {i["name"]: i for i in wf.inputs}
    steps = {s.id: s for s in wf.steps}
    free = [n for n in qs if n in names and (names[n].get("type") or "string") in FREE_TEXT]
    if not free:
        return out
    sid = k.get("screen")
    if not sid or sid not in steps:
        out.append(f"{w}.screen: {', '.join(free)} is in the person's own words, so name the step of {wf.id} that "
                   f"screens it for red flags (screen = \"<step>\"), or ask it as a choice")
        return out
    import json
    said = json.dumps(steps[sid].params or {}, default=str)
    out += [f"{w}.screen: {wf.id}'s step {sid} does not read {n} (its params name no input.{n}): a free-text answer "
            f"must be screened" for n in free if f"input.{n}" not in said]
    if not k.get("alarm"):
        out.append(f"{w}.alarm: say when what the screen found is told loudly (for example {sid}.red_flag == true)")
    if not k.get("advice"):
        out.append(f"{w}.advice: the screen's words to the person, said on the page at once (for example "
                   f"{sid}.advice)")
    if k.get("mode") == "book" and k.get("booked") in steps:
        b = steps[k["booked"]]
        if sid not in (b.after or []) or f"{sid}." not in str(b.when or ""):
            out.append(f"{w}.booked: {wf.id}'s step {b.id} books whatever the screen found: it runs after {sid} and "
                       f"its when reads it (a flag becomes a request to a person, never a booking)")
    return out


# ------------------------------------------------------------------------------------------ helpers
def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _stamp(t: datetime) -> str:
    return t.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))


def norm_identifier(v) -> str:
    """An identifier as a person types it, as it is compared: letters and digits, upper case (spaces, dashes go)."""
    return "".join(ch for ch in str(v or "").upper() if ch.isalnum())


def norm_date(v) -> str | None:
    try:
        return date.fromisoformat(str(v or "").strip()[:10]).isoformat()
    except ValueError:
        return None


def client_key(client) -> str | None:
    """Where a request came from, as it is counted: an IPv6 address by its /64 (one household or one host's whole
    range: rotating within it gets past nothing), anything else as it is."""
    if not client:
        return None
    import ipaddress
    try:
        ip = ipaddress.ip_address(str(client).strip())
    except ValueError:
        return str(client)[:64]
    if ip.version == 6:
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def clock(t: datetime) -> str:
    """A time as people say it: 9:00 a.m., 12:30 p.m."""
    h = t.hour % 12 or 12
    return f"{h}:{t:%M} {'a.m.' if t.hour < 12 else 'p.m.'}"


def day_words(t) -> str:
    """A day in words: Monday, November 23."""
    return f"{t:%A}, {t:%B} {t.day}"


def when_words(t: datetime) -> str:
    return f"{day_words(t)} at {clock(t)}"


# ------------------------------------------------------------------------------------------ the portal
class Portal:
    """The portal's work, as its service member, on the hub: identity, slots, bookings through workflows, the
    person's own view. ``web/portal_api.py`` puts it on the hub's ``/api/portal/``."""

    def __init__(self, hub, *, dev: bool = False):
        self.hub, self.dev = hub, dev
        problems = check_config(hub.pack)
        if problems:
            raise ValueError("[portal]: " + "; ".join(problems))
        self.cfg = config(hub.pack)
        if not self.cfg:
            raise ValueError("this practice has no [portal]: its pack says what people may book online")
        pend = (self.cfg.get("register") or {}).get("pending")
        if pend:
            rec = self._fill({}, pend)
            if schema.is_active(self._rt(), rec):
                raise ValueError(f"[portal] register.pending: a record with {pend} is still active as "
                                 f"{self.subject_type} says it: a new registration must not sign in before the "
                                 f"practice takes it in")

    # --- configuration
    @property
    def member(self) -> dict:
        return self.hub.member(self.cfg["member"])

    def say(self, key: str, **values) -> str:
        text = str((self.cfg.get("wording") or {}).get(key) or WORDING[key])
        for k, v in values.items():
            text = text.replace("{" + k + "}", str(v))
        return text

    def kinds(self) -> dict[str, dict]:
        return {kid: {"id": kid, **k} for kid, k in (self.cfg.get("kinds") or {}).items()}

    def kind(self, kid: str) -> dict:
        from .hub import NotFound
        k = self.kinds().get(kid)
        if not k:
            raise NotFound(f"there is no kind {kid!r}")
        return k

    def slot_mode(self, k: dict) -> str:
        return k.get("slot", "required" if k.get("mode") == "book" else "optional")

    @property
    def subject_type(self) -> str:
        """The type whose records sign in (``[portal.identity] type``, else the practice's subject type); those of
        every type under it sign in too."""
        return (self.cfg.get("identity") or {}).get("type") or self.hub.pack.subject_type

    def _rt(self):
        return self.hub.record_type(self.subject_type)

    def code_minutes(self) -> int:
        return int((self.cfg.get("code") or {}).get("minutes", CODE_MINUTES))

    def closed(self) -> str | None:
        """Why the portal is not open now, in words for the staff (the person is told ``closed``), or None. It is
        closed while the practice pack has a problem (a change on disk not yet reviewed, say), and while a workflow
        a kind starts is not an active definition of the hub's that still asks and screens what the kind says (a
        workflow changed on the hub, not on disk: the pack's own copy was checked when it was loaded)."""
        from .hub import HubError
        if getattr(self.hub, "pack_problem", None):
            return f"the practice pack has a problem: {self.hub.pack_problem}"
        wids = [("intake_workflow", self.cfg.get("intake_workflow"))] if self.cfg.get("intake_workflow") else []
        for kid, k in self.kinds().items():
            wids.append((f"kinds.{kid}.workflow", k.get("workflow")))
        if self.cfg.get("over_limit"):
            wids.append(("over_limit.workflow", self.cfg["over_limit"].get("workflow")))
        for where, wid in wids:
            try:
                wf, _ = self.hub.workflow(wid)
            except HubError as exc:
                return f"{where}: {wid} is not an active workflow of this hub ({exc})"
            if where == "over_limit.workflow":
                problems = over_limit_problems(wf, self.cfg["over_limit"].get("input"))
                if problems:
                    return "; ".join(problems)
            elif where.startswith("kinds."):
                k = self.kinds()[where.split(".")[1]]
                qs = [q if isinstance(q, str) else q["input"] for q in k.get("questions") or []]
                problems = kind_problems(where.rsplit(".", 1)[0], k, wf, qs)
                missing = [n for n in qs if n not in {i["name"] for i in wf.inputs}]
                if missing:
                    problems.append(f"{where}: {wid} in force has no input {', '.join(missing)}")
                if problems:
                    return "; ".join(problems)
        return None

    def questions(self, k: dict) -> list[dict]:
        """The kind's questions, as the pack's ``[portal]`` words them: the workflow's input (its name, type, choices,
        whether it is required) with the portal's label and help only. The workflow's own help is written for staff
        and is never shown here. A choice is ``[(value, words)]``."""
        wf, _ = self.hub.workflow(k["workflow"])
        by = {i["name"]: i for i in wf.inputs}
        out = []
        for q in k.get("questions") or []:
            q = {"input": q} if isinstance(q, str) else q
            name = q["input"]
            if name not in by:
                continue
            i = by[name]
            words = q.get("choices") or {}
            out.append({"name": name, "label": q.get("label") or i.get("label") or name, "help": q.get("help") or "",
                        "type": i.get("type") or "string", "required": bool(i.get("required")),
                        "choices": [[str(c), str(words.get(c) or str(c).replace("-", " ").capitalize())]
                                    for c in i.get("choices") or []]})
        return out

    def register_fields(self) -> list[dict]:
        """The fields a new person gives: the subject type's, in the portal's words where ``[portal.register]``
        words them (``{field, label, help, required}``; ``required`` may only add to what the type requires)."""
        rt = self._rt()
        reg = self.cfg.get("register") or {}
        out = []
        for entry in reg.get("fields") or []:
            words = entry if isinstance(entry, dict) else {"field": entry}
            f = rt.field(words.get("field"))
            if not f:
                continue
            f = dict(f)
            for key in ("label", "help"):
                if words.get(key):
                    f[key] = str(words[key])
            if words.get("required"):
                f["required"] = True
            out.append(f)
        return out

    def public(self) -> dict:
        """What the portal's pages show, and nothing more: words, kinds and the questions they ask, the fields a new
        person gives. The pages ask the hub for this; it holds no record and no secret."""
        rt = self._rt()
        ident = self.cfg.get("identity") or {}
        born = rt.field(ident.get("born") or "")
        id_name = ((rt.base_map.get("identifier") or {}).get("fields") or [""])[0]
        contact_name = ((rt.base_map.get("contact") or {}).get("fields") or [""])[0]
        id_f = rt.field(id_name)
        practice = (self.hub.pack.practice or {}) if self.hub.pack else {}
        kinds = []
        for k in self.kinds().values():
            kinds.append({"id": k["id"], "label": k["label"], "description": k.get("description") or "",
                          "mode": k["mode"], "slot": self.slot_mode(k), "questions": self.questions(k),
                          "about": bool(k.get("about")),
                          "cancel": bool(k.get("cancel_to")),
                          "cancel_before_hours": float(k.get("cancel_before_hours", 0))})
        fields = []
        for f in self.register_fields():
            fields.append({"name": f["name"], "label": f.get("label") or f["name"], "type": f["type"],
                           "required": bool(f.get("required")), "help": f.get("help") or "",
                           "role": "born" if born and f["name"] == born["name"] else
                                   "identifier" if f["name"] == id_name else
                                   "contact" if f["name"] == contact_name else ""})
        return {"title": self.cfg.get("title") or practice.get("name") or "", "welcome": self.cfg.get("welcome") or "",
                "privacy": self.cfg.get("privacy") or "", "contact_line": self.cfg.get("contact_line") or "",
                "public_origin": self.cfg.get("public_origin") or "",
                "wording": {k: self.say(k) for k in WORDING}, "code_minutes": self.code_minutes(),
                "session_minutes": int(self.cfg.get("session_minutes", SESSION_MINUTES)),
                "identity": {"born_label": ident.get("born_label") or (born or {}).get("label") or "Date of birth",
                             "identifier_label": ident.get("identifier_label") or (id_f or {}).get("label")
                             or "Identifier",
                             "identifier_inputmode": ident.get("inputmode") or "text",
                             "digits": ident.get("digits"), "identifier_help": ident.get("identifier_help") or "",
                             "born_help": ident.get("born_help") or ""},
                "register": bool(self.cfg.get("intake_workflow") and self.cfg.get("register")),
                "register_fields": fields, "kinds": kinds, "dev": self.dev}

    # --- counting: lockouts as the staff sign-in counts them (``credentials``), caps in fixed windows of our own
    def _keys(self, client: str | None, identifier: str | None) -> list[tuple[str, int]]:
        out = []
        ck = client_key(client)
        if ck:
            out.append((f"portal client {ck}", CLIENT_LIMIT))
        if identifier:  # the identifier typed, hashed: it locks whether or not anyone has it, so it says nothing
            out.append((f"portal id {_sha(norm_identifier(identifier))[:32]}", PERSON_LIMIT))
        return out

    def _check_locked(self, client, identifier, also=()) -> None:
        from .hub import Denied
        now = datetime.now(timezone.utc)
        for key, _ in [*self._keys(client, identifier), *also]:
            if self.hub._locked_until(key, now):
                raise Denied("Too many attempts. Wait a quarter of an hour and try again, or call the practice.")

    def _bump(self, keys, what: str | None) -> None:
        now = datetime.now(timezone.utc)

        def write():
            for key, limit in keys:
                self.hub._bump_failures(key, now, limit)
            if what:
                self.hub.track.append(self.member, "portal.signin_failed", "portal", data={"what": what})
        self.hub.db.surviving(write)

    def _hit(self, key: str, seconds: int) -> int:
        """One more in this window for ``key``, atomically: how many there have been in it, this one included. Never
        inside a transaction (a count stays counted)."""
        from .store import DuplicateKey
        now = datetime.now(timezone.utc)
        bucket = int(now.timestamp() // seconds)
        _id = f"{key}|{seconds}|{bucket}"
        ends = _stamp(datetime.fromtimestamp((bucket + 1) * seconds, timezone.utc))
        for _ in range(3):
            try:
                row = self.hub.db.portal_counts.find_one_and_update(
                    {"_id": _id}, {"$inc": {"n": 1}, "$setOnInsert": {"key": key, "expires_at": ends}}, upsert=True)
                return int(row["n"])
            except DuplicateKey:  # two first hits at once: the other made the row, count on it
                continue
        return 10 ** 6

    def _ceiling(self) -> None:
        """Everyone's sign-ins and registrations begun in this minute: past ``per_minute``, nobody's (a flood spends
        no codes, no track and no records)."""
        from .hub import Denied
        if self._hit("portal minute", 60) > int(self.cfg.get("per_minute", PER_MINUTE)):
            raise Denied(self.say("busy"))

    # --- identity
    def _find(self, born: str | None, identifier: str) -> str | None:
        """The one active record of the type that signs in (or a type under it) with this date of birth and
        identifier, as ``Type/id`` of its own type. A registration still pending (``[portal.register] pending``) is
        not active, so it is not found."""
        rt = self._rt()
        bf = rt.field(self.cfg["identity"]["born"])
        want = norm_identifier(identifier)
        if not born or not want or bf is None:
            return None
        path = bf["path"]
        kin = self.hub.family(rt.id)
        if schema.FIELD_PATH.fullmatch(path) and "." not in path:
            found = self.hub.records.search(kin, limit=None, field=path, since=born, until=born + "~")
        else:
            found = self.hub.records.search(kin, limit=None, text=born)
        hits = []
        for rec in found:
            seen = schema.project(rt, rec, self.hub.member_types())  # its [base] is the one at the top of its chain
            if (norm_date(schema.first(rec, path)) == born and norm_identifier(seen.get("identifier")) == want
                    and schema.is_active(rt, rec)):
                hits.append(f"{rec.get('resourceType') or rt.id}/{rec['id']}")
        return hits[0] if len(hits) == 1 else None  # two records with one identifier: neither is signed into

    def begin_sign_in(self, born: str, identifier: str, client: str | None = None) -> dict:
        """Step one: a date of birth and an identifier. The answer is the same whether or not they match anyone,
        and whether or not a code went out: a sign-in to finish (``challenge``, for the browser to keep) and, only on
        a dev hub, the code itself. Every attempt counts against the identifier typed (matched or not, so the lock's
        timing tells nothing), and a record is sent at most ``SENDS`` codes."""
        self._check_locked(client, identifier)
        self._ceiling()
        self._bump(self._keys(None, identifier), None)  # every attempt on an identifier, right or wrong
        born_iso = norm_date(born)
        ref = self._find(born_iso, identifier)
        raw = "ppc_" + secrets.token_urlsafe(24)
        cid = _sha(raw)
        length = int((self.cfg.get("code") or {}).get("length", CODE_LENGTH))
        code = "".join(secrets.choice("0123456789") for _ in range(length))
        now = datetime.now(timezone.utc)
        contact = None
        if ref:
            contact = schema.project(self._rt(), self.hub.records.read(*ref.split("/", 1)),
                                     self.hub.member_types()).get("contact")
        withheld = None
        if ref and contact:
            for seconds, cap in SENDS:
                n = self._hit(f"portal sends {ref}", seconds)
                if n > cap:
                    withheld = (seconds, cap, n)
                    break
        outbox_id = None
        with self.hub.db:
            if ref and contact and not withheld:
                outbox_id = self._send_code(ref, code)
                self.hub.track.append(self.member, "portal.code_sent", ref, data={"message": outbox_id})
            elif withheld:
                seconds, cap, n = withheld
                if n == cap + 1:  # once a window: the sends asked for past the cap are one event, with its count
                    self.hub.track.append(self.member, "portal.code_withheld", ref,
                                          data={"window_minutes": seconds // 60, "cap": cap, "count": n})
            else:  # no one, or no one we can reach: counted as a failure from the address, and said no differently
                self._bump(self._keys(client, None), "no match" if not ref else "no contact on file")
            self.hub.db.portal_codes.insert({
                "_id": cid, "record": ref if outbox_id is not None else None, "code_hash": _sha(f"{cid}:{code}"),
                "identifier": _sha(norm_identifier(identifier))[:32], "outbox": outbox_id, "attempts": 0,
                "created_at": _stamp(now), "expires_at": _stamp(now + timedelta(minutes=self.code_minutes())),
                "used_at": None})
        return {"challenge": raw, "dev_code": code if self.dev and outbox_id is not None else None}

    def _send_code(self, ref: str, code: str) -> int:
        """The code, on the outbox to the person's contact: a connector of the practice's delivers it."""
        hub = self.hub
        channel = self.cfg["code"]["channel"]
        practice = (hub.pack.practice or {}) if hub.pack else {}
        cls = practice.get("message_class") or "messages"
        name = self.cfg.get("title") or practice.get("name") or "the practice"
        oid = hub.db.next_seq("outbox")
        hub.db.outbox.insert({"_id": oid, "id": oid, "at": _stamp(datetime.now(timezone.utc)), "channel": channel,
                              "recipient": ref, "subject": "Your sign-in code",
                              "body": f"Your code to sign in to {name} is {code}. It expires in "
                                      f"{self.code_minutes()} minutes. If you did not ask for it, ignore this.",
                              "run_id": None, "sent_by": self.member["id"], "status": "queued", "classes": [cls],
                              "claimed_by": None, "claimed_at": None, "done_at": None, "done_by": None, "note": "",
                              "escalated": 0, "purpose": "sign-in"})
        return oid

    def _scrub(self, row: dict) -> None:
        """A code used or expired is taken out of the outbox: its words go, and a message not yet sent is withdrawn
        (a terminal status: no connector attached later sends it)."""
        if row.get("outbox") is None:
            return
        out = self.hub.db.outbox
        out.set(row["outbox"], {"body": "A sign-in code (removed: it was used or has expired)."})
        out.update({"_id": row["outbox"], "status": {"$in": ["queued", "failed"]}},
                   {"$set": {"status": "withdrawn", "done_at": _stamp(datetime.now(timezone.utc)),
                             "done_by": self.cfg["member"], "note": "a sign-in code, used or expired"}})

    def sweep(self, limit: int = 500) -> dict:
        """Take out what has run its time: expired codes (and their outbox messages), sessions, counts and form
        answers. ``practicehub tick`` runs it, and every sign-in runs a little of it."""
        now = _stamp(datetime.now(timezone.utc))
        db = self.hub.db
        n = 0
        for row in db.portal_codes.find({"expires_at": {"$lte": now}}, limit=limit):
            self._scrub(row)
            db.portal_codes.delete({"_id": row["_id"]})
            n += 1
        return {"codes": n,
                "sessions": db.portal_sessions.delete({"$or": [{"expires_at": {"$lte": now}}, {"revoked": 1}]}),
                "counts": db.portal_counts.delete({"expires_at": {"$lte": now}}),
                "answers": db.portal_submits.delete({"expires_at": {"$lte": now}})}

    def finish_sign_in(self, challenge: str, code: str, client: str | None = None) -> str:
        """Step two: the code. A session (for the browser's cookie), or Invalid, the same for every wrong answer.
        Each try spends one of the code's attempts first, in one atomic update guarded on what is left (so tries at
        once cannot all get through), and only then is the code compared; a right code is used by a conditional
        update, so it opens one session however many tries carry it."""
        from .hub import Invalid
        self.sweep(limit=50)
        cid = _sha(str(challenge or ""))
        row = self.hub.db.portal_codes.get(cid) if challenge else None
        person_key = [(f"portal id {row['identifier']}", PERSON_LIMIT)] if row else []
        self._check_locked(client, None, also=person_key)
        now = datetime.now(timezone.utc)
        typed = "".join(ch for ch in str(code or "") if ch.isdigit())
        live = None
        if row is not None:
            live = self.hub.db.portal_codes.find_one_and_update(
                {"_id": cid, "used_at": None, "attempts": {"$lt": CODE_ATTEMPTS}, "expires_at": {"$gt": _stamp(now)}},
                {"$inc": {"attempts": 1}})
        good = (live is not None and live.get("record")
                and secrets.compare_digest(live["code_hash"], _sha(f"{cid}:{typed}")))
        if good:
            won = self.hub.db.portal_codes.update({"_id": cid, "used_at": None}, {"$set": {"used_at": _stamp(now)}})
            good = won == 1
        if not good:
            self._bump(self._keys(client, None) + person_key, "code")
            raise Invalid(self.say("no_match"))
        with self.hub.db:
            self._scrub(live)
            raw = "pps_" + secrets.token_urlsafe(32)
            minutes = int(self.cfg.get("session_minutes", SESSION_MINUTES))
            self.hub.db.portal_sessions.insert({"_id": _sha(raw), "record": live["record"], "created_at": _stamp(now),
                                                "expires_at": _stamp(now + timedelta(minutes=minutes)), "revoked": 0,
                                                "form": secrets.token_urlsafe(16)})
            self.hub.track.append(self.member, "portal.signed_in", live["record"])
        return raw

    def session(self, raw: str | None) -> dict | None:
        """The signed-in person's session ``{record, form}``, or None (as a record still pending is never signed in,
        nor is one that stopped being active since)."""
        if not raw or not str(raw).startswith("pps_"):
            return None
        row = self.hub.db.portal_sessions.get(_sha(raw))
        if not row or row.get("revoked") or _parse(row["expires_at"]) <= datetime.now(timezone.utc):
            return None
        rtype, rid = row["record"].split("/", 1)
        rec = self.hub.records.read(rtype, rid)
        if not rec or not schema.is_active(self._rt(), rec):
            return None
        return row

    def end_session(self, raw: str | None) -> None:
        if raw and str(raw).startswith("pps_"):
            self.hub.db.portal_sessions.set(_sha(raw), {"revoked": 1})

    # --- a form's answer, once: a second tap, a refresh, a resend all land on the same page
    def once(self, person: str | None, token: str, what: str, fn) -> str:
        """Run ``fn`` for this form (``token``, the form's own) at most once, and keep what it returned: the answer's
        id, for the page that shows it. A second submit of the same form, at once or later, gets the same id; if ``fn``
        fails the form may be sent again."""
        from .hub import Invalid
        from .store import DuplicateKey
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", str(token or "")):
            raise Invalid("This form has expired. Open the page again.")
        key = _sha(f"{person or '-'}|{token}")
        db = self.hub.db
        row = db.portal_submits.get(key)
        if row:
            return row["done"]
        now = datetime.now(timezone.utc)
        done = secrets.token_urlsafe(12)
        try:
            db.portal_submits.insert({"_id": key, "done": done, "record": person, "what": what, "state": "working",
                                      "result": None, "created_at": _stamp(now),
                                      "expires_at": _stamp(now + timedelta(hours=SUBMIT_HOURS))})
        except DuplicateKey:  # the other tap got here first: its page
            return db.portal_submits.get(key)["done"]
        try:
            result = fn()
        except BaseException:
            db.portal_submits.delete({"_id": key})
            raise
        db.portal_submits.set(key, {"state": "done", "result": result})
        return done

    def answer(self, person: str | None, done: str) -> dict:
        """The answer a form got (``once``), for the person who sent it: ``{what, state, result}``."""
        from .hub import NotFound
        row = self.hub.db.portal_submits.find_one({"done": str(done or ""), "record": person}) if done else None
        if not row:
            raise NotFound("There is nothing to show here. Start again.")
        return {"what": row["what"], "state": row["state"], "result": row.get("result")}

    # --- free time
    def _holders(self, k: dict) -> list[dict]:
        seen, out = set(), []
        for r in k.get("roles") or []:
            for m in self.hub.role(r)["holders"]:
                if m["id"] not in seen and m["kind"] == "person" and m["status"] == "active":
                    seen.add(m["id"])
                    out.append(m)
        return out

    def free_slots(self, kid: str, *, day: str | None = None, limit: int | None = None) -> list[dict]:
        """The times free for this kind: its length, with the members of its roles, between the practice's hours,
        from now to its horizon, or on ``day`` (``YYYY-MM-DD``) alone. Each is ``{value, label, day, date, time,
        with}``; ``value`` is ``YYYY-MM-DDTHH:MM|member``, as the core's ``booking.book`` takes it. ``max_slots`` caps
        what one day offers (a page lists one day at a time: ``free_days`` lists the days)."""
        k = self.kind(kid)
        if self.slot_mode(k) == "none":
            return []
        now = self.hub.engine.now().replace(second=0, microsecond=0)
        last = now.date() + timedelta(days=int(k.get("horizon_days", 14)))
        if day is not None:
            try:
                first = datetime.strptime(str(day)[:10], "%Y-%m-%d").date()
            except ValueError:
                return []
            if not now.date() <= first <= last:
                return []
            last = first
        else:
            first = now.date()
        per_day = limit or int(k.get("max_slots", 60))
        out = []
        for d, slots in self._free(k, first, last, now):
            out += slots[:per_day]
        return out

    def free_days(self, kid: str) -> list[dict]:
        """The working days in the kind's horizon that have a free time, first to last: ``{date, label, count}``."""
        k = self.kind(kid)
        if self.slot_mode(k) == "none":
            return []
        now = self.hub.engine.now().replace(second=0, microsecond=0)
        last = now.date() + timedelta(days=int(k.get("horizon_days", 14)))
        return [{"date": d.isoformat(), "label": slots[0]["day"], "count": len(slots)}
                for d, slots in self._free(k, now.date(), last, now) if slots]

    def _free(self, k: dict, first, last, now: datetime):
        """Each working day from ``first`` to ``last``, with its free times (every member of the kind's roles)."""
        from .engine import bookings
        cal = (self.hub.pack.calendar or {}) if self.hub.pack else {}
        hours = cal.get("hours") or {}
        step = int(k.get("minutes") or cal.get("slot_minutes", 15))
        size = int(cal.get("slot_minutes", 15))
        busy: dict[str, list[tuple[datetime, datetime]]] = {}
        for b in bookings(self.hub, since=first.isoformat(), until=(last + timedelta(days=1)).isoformat()):
            span = self.hub.booking_span(b, size)
            if span:
                for who in b["with"]:
                    busy.setdefault(who, []).append(span)
        people = self._holders(k)
        day = first
        while day <= last:
            key = day.strftime("%a").lower()[:3]
            if key in hours:
                out = []
                opens, closes = (datetime.combine(day, datetime.strptime(x, "%H:%M").time()) for x in hours[key])
                t = opens
                while t + timedelta(minutes=step) <= closes:
                    end = t + timedelta(minutes=step)
                    if t > now:
                        for m in people:
                            if not any(s < end and t < e for s, e in busy.get(m["id"], [])):
                                out.append({"value": f"{t:%Y-%m-%dT%H:%M}|{m['id']}", "day": day_words(t),
                                            "date": day.isoformat(), "time": clock(t), "with": m["name"],
                                            "label": f"{when_words(t)} with {m['name']}"})
                    t = end
                yield day, out
            day += timedelta(days=1)

    def slot_free(self, kid: str, value: str) -> bool:
        return any(s["value"] == value for s in self.free_slots(kid, day=str(value)[:10], limit=10000))

    # --- booking: always through the kind's workflow
    def _runs(self, person: str, k: dict) -> list[dict]:
        """The kind's workflow's runs still open about the person that the portal started, for this kind (its fixed
        inputs): each one booking or request the person made online."""
        fixed = {n: str(v) for n, v in (k.get("inputs") or {}).items()}
        out = []
        for r in self.hub.db.runs.find({"workflow_id": k["workflow"], "status": "running",
                                        "started_by": self.cfg["member"],
                                        "$or": [{"subject": person}, {"record": person}]},
                                       sort=[("created_at", -1)], limit=200):
            given = (r.get("ctx") or {}).get("input") or {}
            if all(str(given.get(n)) == v for n, v in fixed.items()):
                out.append(r)
        return out

    def _requests(self, person: str, k: dict) -> list[dict]:
        """Those of the kind's open runs not booked yet: a time not held (its record holds none, or it has none)."""
        from .engine import bookings
        held = {f"{b['type']}/{b['record']['id']}" for b in bookings(self.hub, active_only=False)
                if b["subject"] == person}
        out = []
        for r in self._runs(person, k):
            ref = r.get("record") or ""
            if ref in held or "/" not in ref or ref == person:
                continue
            rec = self.hub.records.read(*ref.split("/", 1))
            if rec and schema.is_active(self.hub.record_type(ref.split("/", 1)[0]), rec):
                out.append(r)
        return out

    def open_count(self, person: str, k: dict) -> int:
        """The person's open portal bookings of this kind: upcoming bookings of its booking kind, and requests of it
        still waiting for a time."""
        return (sum(1 for b in self.upcoming(person) if b.get("kind_id") == k["id"])
                + len(self._requests(person, k)))

    def book(self, person: str, kid: str, slot: str | None, answers: dict, about: str | None = None) -> dict:
        """Book (or ask for) this kind for the signed-in person. The time must be free now (else Clash, with the
        times that are); the person may have at most the kind's ``max_upcoming`` open; a kind about one of their
        records takes one of those it offers them now (``about``, checked again here, whatever the page sent); the
        kind's workflow is started (``_book``). What the person is told comes back: ``{run, kind, mode, advice,
        alarm, when, with, failed, requested}``. The same request sent again a moment later (the workflow's
        ``one_open_per``) is not a second one: its answer is the first one's."""
        from .hub import Duplicate, Invalid
        k = self.kind(kid)
        mode = self.slot_mode(k)
        slot = (slot or "").strip() or None
        full = self.open_count(person, k) >= self.limit(k)
        if full:
            slot = None  # nothing more is booked online: what they wrote is still read (over_limit)
        if mode == "required" and not slot and not full:
            raise Invalid("choose a time", ["Choose one of the times shown."])
        qs = self.questions(k)
        problems = [f"{q['label']}: choose one" for q in qs
                    if q["required"] and q["choices"] and str(answers.get(q["name"]) or "") == ""]
        problems += [f"{q['label']}: choose one of the answers shown" for q in qs
                     if q["choices"] and answers.get(q["name"]) not in (None, "")
                     and str(answers[q["name"]]) not in {c for c, _ in q["choices"]}]
        problems += [f"{q['label']}: say a little about it" for q in qs
                     if q["required"] and not q["choices"] and not str(answers.get(q["name"]) or "").strip()]
        about = (about or "").strip() or None
        if k.get("about") and not full:
            mine = [c["ref"] for c in self.about_choices(person, k)]
            if not mine:
                problems.append(self.say("nothing_about"))
            elif about not in mine:
                problems.append(self.say("choose_about"))
        else:
            about = None
        if problems:
            raise Invalid("the form has problems", problems)
        if slot and (mode == "none" or not self.slot_free(kid, slot)):
            raise Clash(self.free_slots(kid, day=slot[:10]) if slot else [])
        if full:
            return self.over_limit(k, person, answers)
        try:
            run = self._book(k, person, slot, answers, about)
        except Duplicate as dup:  # the same thing sent again a moment ago: the page the first one got
            if (dup.run or {}).get("subject") not in (person, None) and (dup.run or {}).get("record") != person:
                raise
            run = dup.run
            slot = ((dup.run.get("ctx") or {}).get("input") or {}).get(k.get("slot_input", "slot")) or slot
        return self.outcome(k, run["id"], slot)

    def limit(self, k: dict) -> int:
        return int(k.get("max_upcoming", MAX_UPCOMING))

    def about_choices(self, person: str, k: dict) -> list[dict]:
        """The person's own records a kind can be about (its ``about``): of its type, about them, where its ``when``
        holds now, newest first. Each is named as the hub names it anywhere (its title, never from a field the
        practice keeps from the portal). Read for the portal, which shows a person only what is theirs."""
        ab = k.get("about") or {}
        rt = self.hub.record_type(ab.get("type") or "")
        if rt is None:
            return []
        today = self.hub.engine.now().date().isoformat()
        out = []
        for rec in self.hub.records.search(self.hub.family(rt.id), subject=person, limit=ABOUT_LIMIT):
            own = self.hub.record_type(rec.get("resourceType") or rt.id) or rt  # its when reads its own fields
            if ab.get("when") and not expr.holds(ab["when"], schema.check_context(own, rec, today)):
                continue
            out.append({"ref": f"{own.id}/{rec['id']}", "title": self.hub.title(rec)})
        return out

    def limits(self, person: str) -> dict[str, dict]:
        """Per kind, how many the person has open and the most they may (``max_upcoming``): told up front, on their
        page and on the form, before they write anything."""
        return {kid: {"open": self.open_count(person, k), "max": self.limit(k)} for kid, k in self.kinds().items()}

    def screen(self, k: dict, inputs: dict) -> dict | None:
        """The kind's screen (the block its workflow's ``screen`` step runs), run here on what the person wrote, with
        no workflow started: what it found, or None when it cannot run (read as loud, never as quiet)."""
        from .workflows import render
        sid = k.get("screen")
        try:
            wf, _ = self.hub.workflow(k["workflow"])
            step = next((s for s in wf.steps if s.id == sid), None)
            if step is None or not step.block:
                return None
            params = render(step.params or {}, {"input": inputs})
            out, _ = self.hub.engine._run_block({"id": "portal", "ctx": {"run": {}}}, step, params)
            return out
        except Exception:  # noqa: BLE001 - a screen that cannot run is a loud answer, never an error page
            return None

    def _words(self, k: dict, ctx: dict) -> tuple[str | None, bool, list[str]]:
        """The kind's ``advice`` and ``alarm`` read on a run's context (or the screen's alone), and the names of what
        the screen found (its ``flags`` and the signs it was unsure of), for the page to name in plain words."""
        advice, alarm = None, False
        if k.get("advice"):
            got = expr.lookup(ctx, str(k["advice"]))
            advice = str(got).strip() if got not in (None, "") else None
        if k.get("alarm"):
            try:
                alarm = bool(expr.holds(str(k["alarm"]), ctx))
            except expr.ExprError:
                alarm = True  # a check that cannot be read says the advice loudly, never quietly
        found: list[str] = []
        got = ctx.get(str(k.get("screen") or "")) if k.get("screen") else None
        if isinstance(got, dict):
            for key in ("flags", "mentioned"):
                found += [str(x) for x in got.get(key) or [] if str(x) not in found]
        return advice, alarm, found

    def over_limit(self, k: dict, person: str, answers: dict) -> dict:
        """A person at the kind's limit who sends the form anyway: nothing is booked, but what they wrote is screened
        first, here. Anything not plainly routine goes to the practice through ``[portal] over_limit`` (its workflow
        screens it again and puts it in front of a person), and the page says the screen's advice, loudly when it
        found something. Plainly routine, or no such workflow: refused, with what to do in an emergency."""
        from .hub import Denied
        inputs = {**(k.get("inputs") or {}), **{n: v for n, v in answers.items() if v not in (None, "")}}
        found = self.screen(k, inputs)
        ctx = {"input": inputs, str(k.get("screen") or "screen"): found}
        advice, alarm, flags = self._words(k, ctx)
        text = " ".join(str(answers.get(q["name"]) or "") for q in self.questions(k)
                        if q["type"] in FREE_TEXT and not q["choices"]).strip()
        ol = self.cfg.get("over_limit") or {}
        routine = bool(found and found.get("routine")) and not alarm
        if routine or not text or not ol.get("workflow"):
            raise Denied(self.say("too_many"))  # the refusal page says what to do in an emergency (safety)
        with self.hub.engine.jobs_inline():  # the answer given here is read from what the run found
            run = self.hub.engine.start(self.member, ol["workflow"], record=person, inputs={ol["input"]: text})
        return {"run": run["id"], "kind": k["label"], "mode": k["mode"], "advice": advice, "alarm": alarm,
                "found": flags, "when": None, "with": None, "failed": False, "requested": True, "over_limit": True,
                "closed": self.closed_words()}

    def opens(self, now: datetime | None = None) -> datetime | None:
        """When the practice next opens (its calendar's ``hours``), its wall time; None while it is open now (or when
        it keeps no hours)."""
        hours = ((self.hub.pack.calendar or {}) if self.hub.pack else {}).get("hours") or {}
        if not hours:
            return None
        now = now or self.hub.engine.now()
        for d in range(0, 15):
            day = now.date() + timedelta(days=d)
            span = hours.get(day.strftime("%a").lower()[:3])
            if not span:
                continue
            opens, closes = (datetime.combine(day, datetime.strptime(x, "%H:%M").time()) for x in span)
            if d == 0 and opens <= now < closes:
                return None
            if opens > now:
                return opens
        return None

    def closed_words(self) -> str | None:
        """When the practice next opens, in words ('Monday, November 23 at 9:00 a.m.'), while it is closed now."""
        t = self.opens()
        return when_words(t) if t else None

    def outcome(self, k: dict, run_id: str, slot: str | None) -> dict:
        """What the person is told of the run their form started: the screen's words, whether they are loud, and
        whether it is booked (when, with whom), asked for (a person reads it first) or could not be done."""
        run = self.hub.engine.run(run_id) or {}
        ctx = run.get("ctx") or {}
        advice, alarm, found = self._words(k, ctx)
        out = {"run": run_id, "kind": k["label"], "mode": k["mode"], "advice": advice, "alarm": alarm,
               "found": found, "when": None, "with": None, "failed": False, "requested": False,
               "closed": self.closed_words()}
        booked = str(k.get("booked") or "")
        done = (ctx.get(booked) or {}) if booked else {}
        state = ((run.get("steps") or {}).get(booked) or {}).get("state") if booked else None
        if done.get("when") and slot:
            t = datetime.strptime(slot.split("|", 1)[0], "%Y-%m-%dT%H:%M")
            out["when"], out["with"] = when_words(t), done.get("member_name")
        elif state == "failed":
            out["failed"] = True  # the workflow could not do it: its step failed, and the practice is told
        else:
            out["requested"] = True  # in as a request: a person reads it first (a flag, or not plainly routine)
        return out

    def _book(self, k: dict, person: str, slot: str | None, answers: dict, about: str | None = None) -> dict:
        """The one place the portal books: it starts the kind's workflow, as the portal's member, on the person, with
        the kind's own inputs, the answers to its questions, the record it is about and the time chosen. The
        workflow makes the visit (or whatever its record is) and books it, or hands it to a person, as it says."""
        inputs = dict(k.get("inputs") or {})
        if about:
            inputs[k["about"]["input"]] = about
        for q in self.questions(k):
            if answers.get(q["name"]) not in (None, ""):
                inputs[q["name"]] = answers[q["name"]]
        if slot:
            inputs[k.get("slot_input", "slot")] = slot
        # the person is answered from what the screen of their message found, in this same request: it runs
        # here, never in the queue (an emergency is told what to do now, not once a worker gets to it)
        with self.hub.engine.jobs_inline():
            return self.hub.engine.start(self.member, k["workflow"], record=person, inputs=inputs)

    # --- the person's own view
    def upcoming(self, person: str) -> list[dict]:
        """The person's bookings still to come: when, with whom, what kind (in the portal's words), and its state in
        its type's words. Never why: the reason and anything clinical stay with the practice."""
        from .engine import bookings
        now = self.hub.engine.now()
        by_kind = {k.get("booking_kind"): k for k in self.kinds().values() if k.get("booking_kind")}
        out = []
        for b in bookings(self.hub, since=now.isoformat(timespec="minutes")):
            if b["subject"] != person or b["cancelled"]:
                continue
            rt = self.hub.record_type(b["type"])
            if not schema.is_active(rt, b["record"]):
                continue
            k = by_kind.get(b["kind"])
            ref = f"{b['type']}/{b['record']['id']}"
            state = None
            lc = rt.lifecycle if rt else None
            if lc:
                st = schema.first(b["record"], lc["path"])
                state = rt.state_label(st) if st is not None else None
            try:
                start = datetime.fromisoformat(b["start"][:16])
            except ValueError:
                continue
            names = []
            for who in b["with"]:
                try:
                    names.append(self.hub.member(who)["name"])
                except Exception:  # noqa: BLE001 - someone since removed: no name
                    pass
            hours = float((k or {}).get("cancel_before_hours", 0))
            soon = start - now < timedelta(hours=hours)
            ok = bool(k and k.get("cancel_to") and not soon and self._may_move(rt, ref, b["record"], k["cancel_to"]))
            why = None if ok else self.say("no_cancel_soon", hours=f"{hours:g}") if k and k.get("cancel_to") and soon \
                else self.say("no_cancel")
            out.append({"ref": ref, "start": start.isoformat(timespec="minutes"),
                        "when": when_words(start), "with": names,
                        "kind": (k or {}).get("label") or self.say("booking"), "state": state, "can_cancel": ok,
                        "why_not": why, "next": self.say("visit_next"), "kind_id": (k or {}).get("id")})
        return sorted(out, key=lambda x: x["start"])

    def _local_day(self, stamp) -> str:
        """A stored instant (UTC, ``...Z``) as the practice's day in words: a request sent at 9 p.m. is that day's,
        not tomorrow's."""
        from . import clock
        raw = str(stamp or "")
        try:
            t = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return raw[:10]
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return day_words(clock.local(t, self.hub.engine.zone()).date())

    def open_requests(self, person: str) -> list[dict]:
        """The person's requests still open that the portal sent: what (the kind's words) and since when."""
        out = []
        for k in self.kinds().values():
            for r in self._requests(person, k):
                out.append({"what": k["label"], "since": self._local_day(r.get("created_at")),
                            "next": self.say("request_next")})
        return out

    def _may_move(self, rt, ref: str, rec: dict, to: str) -> bool:
        """Whether a person's move of ``ref`` to ``to`` is one its lifecycle has and its workflow waits on now."""
        if not rt or not rt.lifecycle:
            return False
        t = rt.transition(schema.first(rec, rt.lifecycle["path"]), to)
        if t is None or t.get("by") == "workflow":
            return False
        moved = schema.put(dict(rec), rt.lifecycle["path"], to)
        return self.hub.state_move_refusal(rt, ref, t, moved, None) is None

    def cancel(self, person: str, booking: str) -> dict:
        """Cancel one of the person's own upcoming bookings, where its kind lets them (``_cancel``)."""
        from .hub import Denied, HubError
        mine = {b["ref"]: b for b in self.upcoming(person)}
        b = mine.get(booking)
        if not b or not b["can_cancel"]:
            raise Denied("That visit cannot be cancelled online. Call the practice.")
        try:
            self._cancel(self.kind(b["kind_id"]), booking)
        except HubError:
            raise Denied("That visit could not be cancelled online. Call the practice.") from None
        return b

    def _cancel(self, k: dict, booking: str) -> dict:
        """The one place the portal cancels: a move of the booking, as the portal's member, to the kind's
        ``cancel_to`` through its lifecycle. The hub makes it only as a move the booking's own workflow is waiting on
        (that workflow then goes on from it, in the same transaction); the portal never writes the booking else."""
        rtype, rid = booking.split("/", 1)
        rt = self.hub.record_type(rtype)
        rec = self.hub.records.read(rtype, rid)
        top = rt.lifecycle["path"].split(".", 1)[0]
        moved = schema.put(dict(rec), rt.lifecycle["path"], k["cancel_to"])
        return self.hub.update_record(self.member, booking, {top: moved[top]},
                                      comment="cancelled online by the person it is for")

    # --- someone new
    def _fill(self, rec: dict, values: dict) -> dict:
        rt = self._rt()
        for name, v in values.items():
            f = rt.field(name)
            if f is not None:
                rec = schema.set_in(rec, schema.locate(rec, f["path"], f.get("with")).split("."), v, f.get("with"))
        return rec

    def register(self, values: dict, client: str | None = None) -> dict | None:
        """A new person's details: a request to be taken in, never a person who can sign in. The record is made with
        ``[portal.register] pending`` (inactive as its type says), so it is not found at sign-in and books nothing
        until the intake workflow, about it, takes the person in. Counted per address and against everyone's
        ceiling, so the form is not a way to fill the practice's files. Returns the intake's run."""
        from .hub import Denied, Invalid
        wid = self.cfg.get("intake_workflow")
        if not wid or not self.cfg.get("register"):
            raise Denied("New registrations are not taken online here. Call the practice.")
        key = f"portal new {client_key(client) or 'unknown'}"
        now = datetime.now(timezone.utc)
        if self.hub._locked_until(key, now):
            raise Denied("Too many requests from here. Try again later, or call the practice.")
        self._ceiling()
        self.hub.db.surviving(lambda: self.hub._bump_failures(key, now, REGISTER_LIMIT))
        rt = self._rt()
        given: dict = {}
        problems = []
        for f in self.register_fields():
            v = str(values.get(f["name"]) or "").strip()[:200]
            if not v:
                if f.get("required"):
                    problems.append(f"{f['label']}: required")
                continue
            if f["type"] == "date":
                v = norm_date(v)
                if v is None:
                    problems.append(f"{f['label']}: a date")
                    continue
            given[f["name"]] = v
        if problems:
            raise Invalid("the form has problems", problems)
        rec = self._fill(self._fill({}, given), dict((self.cfg.get("register") or {}).get("pending") or {}))
        with self.hub.db:
            made = self.hub.create_record(self.member, rt.id, rec)
            ref = f"{rt.id}/{made['id']}"
            with self.hub.engine.jobs_inline():
                return self.hub.engine.start(self.member, wid, record=ref)
