"""The run engine: workflows in motion, the inbox, and the guards on who may do what.

Guards are deterministic code, no model. The ones that treat agents differently from people are all in
``Engine.eligibility``, so a reviewer can read in one place exactly what an agent may and may not do:

1. The agent is active and so is its sponsor (an agent whose sponsor left stops).
2. It holds the step's role (or is the step's named assignee).
3. The step admits agents: never a gate whose signer is a person, never an item escalated to people.
4. It may decide a gate only within the gate's ``agent_criteria``, checked against the run's data.
5. The step's risk is within the agent's ``max_risk``. Low risk work goes ahead; medium risk work goes ahead and is
   reported to the agent's sponsor; high risk work is *held*: nothing happens until the sponsor approves it.
6. The step's skill is in the agent's profile: its job description. For a step that uses a prompt block, that is
   the block (or the skill it extends).

Rules that apply to everyone: separation of duties (``not_by``) and data-class read permission for the step's context.
"""

from __future__ import annotations

import contextlib
import functools
import json
import os
import re
import secrets
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

from . import blocks as blockmod
from . import clock
from . import expr
from . import schema
from . import visibility
from . import workflows as wfmod
from .states import FAILED
from .db import utcnow
from .backends import backend_for, _timeout as engine_timeout
from .fields import FieldError, coerce, json_schema
from . import decimals

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


# The run that holds the notice about an event no workflow took: not a workflow anyone wrote, so it is not under change
# control and starts nothing; ``Hub.workflow`` gives it this one step, already skipped, for the pages that show a run.
UNHANDLED_WORKFLOW = "event-not-taken"
UNHANDLED_SOURCE = wfmod.acts_only_source(
    UNHANDLED_WORKFLOW, "An event no workflow took",
    "An event arrived and no workflow listening for it started. The track has what it carried.",
    "look", "Look at the event")

def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(4)}"


# ---------------------------------------------------------------------------------------------- core actions
def act_message_send(engine, run, step, params, actor):
    to = params.get("to") or ""
    if not to:
        raise ValueError("message.send: no recipient")
    hub = engine.hub
    purpose = params.get("purpose") or None
    why = hub.consent_problem(str(to), purpose)  # the CASL rule: a commercial message needs consent that holds
    if why:
        raise ValueError(f"message.send refused: {why}")
    classes = message_classes(hub, run, step)
    body = str(params.get("body", ""))
    gap = hub.restrictions.incomplete(run.get("record"), classes) if str(to) != run.get("record") else None
    if gap and gap not in body:  # a letter written from a file the patient limited says it is incomplete
        body = f"{body.rstrip()}\n\n{gap}"
    subject = str(params.get("subject", ""))

    def put(recipient, subject, body, classes):
        oid = hub.db.next_seq("outbox")
        hub.db.outbox.insert({"_id": oid, "id": oid, "at": utcnow(), "channel": params.get("channel", "message"),
                              "recipient": str(recipient), "subject": subject,
                              "body": body, "run_id": run["id"], "sent_by": actor["id"],
                              "status": "queued", "classes": sorted(classes), "claimed_by": None,
                              "claimed_at": None, "done_at": None, "done_by": None, "note": "", "escalated": 0,
                              "purpose": purpose})
        return oid
    split = _split_for_directive(hub, run, str(to))
    if split is None:
        return {"message_id": put(to, subject, body, classes), "to": to}
    # a message to a role about a file whose directive keeps it from some who hold the role: each holder gets their
    # own, and those it is kept from are told only that there is one, and to open it on the hub
    raw_subject = str((step.params or {}).get("subject", "")) if step else ""
    neutral_subject = subject if "{{" not in raw_subject else "A message about a restricted file"
    base = {(hub.pack.practice if hub.pack else {}).get("message_class") or "messages"}
    ids = []
    for m, kept in split:
        if kept:
            ids.append(put(f"member:{m['id']}", neutral_subject, NEUTRAL_BODY, base))
        else:
            ids.append(put(f"member:{m['id']}", subject, body, classes))
    return {"message_id": ids[0] if ids else None, "message_ids": ids, "to": to}


NEUTRAL_BODY = ("A message for you about someone whose file is restricted at their request. It is not sent here: "
                "open it on the hub.")


def _split_for_directive(hub, run, to: str):
    """For a message to ``role:<id>`` about a file with a subject's directive that keeps it from some of the role's
    holders: [(holder, kept_from_them)], one per person on duty who holds it. None when nothing needs splitting."""
    if not to.startswith("role:") or not run.get("record"):
        return None
    subject = hub.restrictions.subject_of_ref(run["record"])
    if not subject or not hub.restrictions.of_subject(subject):
        return None
    holders = hub.active_holders(to.split(":", 1)[1])
    marks = [(m, hub.restrictions.withholds_from(m, subject, reaching=True)) for m in holders]
    if not any(k for _, k in marks):
        return None
    return marks


def message_classes(hub, run, step) -> set[str]:
    """The data classes a message's words come from, so the outbox shows it only to those who may read them. What a
    step was shown (``record:clinical`` in its context) and what it quotes (another step's output, or the run's
    input) both count, followed back through the steps quoted. Free text (the subject's own words, or what a person
    or agent wrote with nothing on record about what they were shown) is the pack's ``free_text_class``; fixed
    wording is its ``message_class``."""
    import re
    practice = (hub.pack.practice if hub.pack else {}) or {}
    base = practice.get("message_class") or "messages"          # the class of a message as such
    sensitive = practice.get("free_text_class") or base           # what free text may hold
    wf, _ = hub.workflow(run["workflow_id"], run["version"])
    steps = {s.id: s for s in wf.steps}
    free_input = any(i.get("type", "string") in ("text", "string") for i in wf.inputs)

    def quoted(st) -> set[str]:
        return set(re.findall(r"\{\{\s*([A-Za-z_][\w-]*)\.", json.dumps(st.params or {}, default=decimals.jsonable)))

    out, seen, todo = {base}, set(), list(quoted(step))
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        if name == "input":
            if free_input:
                out.add(sensitive)
            continue
        src = steps.get(name)
        if src is None:
            continue
        shown = {c.split(":", 1)[1] for c in src.context if c.startswith("record:")}
        out |= shown
        if src.kind in ("task", "gate") and not shown:
            out.add(sensitive)
        todo += [c.split(".", 1)[0] for c in src.context if not c.startswith("record:")]
        todo += list(quoted(src))
    return out


def _besides(besides) -> set:
    """``besides`` of an alert as a set: nobody, one member, or several."""
    if not besides:
        return set()
    return {besides} if isinstance(besides, str) else set(besides)


def act_record_create(engine, run, step, params, actor):
    rtype = params.get("type")
    data = params.get("data") or {}
    if not rtype:
        raise ValueError("record.create: no type")
    rec = engine.hub.create_record(actor, rtype, data, run_id=run["id"], origin=_origin(step))
    ref = f"{rtype}/{rec['id']}"
    if params.get("attest"):  # filed under a signature given earlier in this run (a note signed as a draft)
        engine.hub.attest(actor, ref, run, str(params["attest"]))
    return {"id": rec["id"], "reference": ref}


def _origin(step) -> dict | None:
    """Where a spawn came from: the step that made it and the edge that step runs on (None on no edge)."""
    return {"step": step.id, "edge": getattr(step, "on_edge", None)} if step is not None else None


def _record_update_append(rec: dict, params: dict) -> dict:
    """record.update's ``append``: a table of list field = entry, each entry added to the end of that list on the
    record (what every pass of a record did, kept one entry per pass rather than written over). An entry whose text
    is all empty (nothing to keep) is left out."""
    add = params.get("append") or {}
    if not isinstance(add, dict):
        raise ValueError("record.update: append is a table of list field = entry")
    out = dict(params.get("set") or {})
    for path, entry in add.items():
        cur = out[path] if path in out else expr.lookup(rec, path)
        if cur is not None and not isinstance(cur, list):
            raise ValueError(f"record.update: {path} is not a list")
        empty = entry in (None, "", [], {}) or (isinstance(entry, dict) and not any(
            v not in (None, "", [], {}) for v in entry.values()))
        out[path] = list(cur or []) + ([] if empty else [entry])
    return {**params, "set": out} if add else params


def act_record_update(engine, run, step, params, actor):
    # a step that names its record must name one: an empty ref never falls back to the run's record
    ref = params.get("ref") if "ref" in step.params else run["record"]
    if not ref or "/" not in str(ref):
        raise ValueError(f"record.update: no record to update (ref is {ref!r})")
    rtype, rid = ref.split("/", 1)
    rec = engine.hub.records.read(rtype, rid)
    if not rec:
        raise ValueError(f"record.update: there is no {ref}")
    # increment: a list of fields, each up by one (a document's revision), or a table of field = amount (stock on hand
    # up by what was received); decrement: a table, down by the amount. The amount is checked to be a number here,
    # after its placeholder is filled: a move of "two boxes" fails the step rather than writing nonsense.
    deltas: list[tuple[str, object]] = []
    inc = params.get("increment") or []
    if isinstance(inc, list) and all(isinstance(x, str) for x in inc):
        deltas += [(p, 1) for p in inc]
    elif isinstance(inc, dict):
        deltas += list(inc.items())
    else:
        raise ValueError("record.update: increment is a list of field names or a table of field = amount")
    dec = params.get("decrement") or {}
    if not isinstance(dec, dict):
        raise ValueError("record.update: decrement is a table of field = amount")
    deltas += [(p, -v if isinstance(v, (int, float)) and not isinstance(v, bool) else v) for p, v in dec.items()]
    for path, by in deltas:
        if isinstance(by, bool) or not isinstance(by, (int, float)):
            raise ValueError(f"record.update: the amount for {path} is not a number ({by!r})")
        cur = expr.lookup(rec, path)
        if isinstance(cur, bool) or (cur is not None and not isinstance(cur, (int, float))):
            raise ValueError(f"record.update: {path} is not a number")
        params = {**params, "set": {**(params.get("set") or {}), path: (cur or 0) + by}}
    params = _record_update_append(rec, params)
    for path, value in (params.get("set") or {}).items():
        cur = rec
        parts = path.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value
    engine.hub.update_record(actor, ref, rec, run_id=run["id"])
    return {"reference": ref}


def act_workflow_start(engine, run, step, params, actor):
    child = engine.start(actor, params["workflow"], record=params.get("record") or run["record"],
                         inputs=params.get("input") or {}, via_automation=True, parent=run["id"],
                         origin=_origin(step))
    return {"run_id": child["id"]}


def act_context_set(engine, run, step, params, actor):
    return dict(params)


def act_record_child(engine, run, step, params, actor):
    """record.child: a sub-record of this run's record (see ``Engine.make_child_record``)."""
    return engine.make_child_record(run, params, actor, step)


CORE_ACTIONS = {
    "message.send": act_message_send,
    "record.create": act_record_create,
    "record.child": act_record_child,
    "record.update": act_record_update,
    "workflow.start": act_workflow_start,
    "context.set": act_context_set,
}


# ---------------------------------------------------------------------------------------------- core providers
def bookings(hub, *, active_only: bool = True, since: str | None = None, until: str | None = None,
             member: dict | None = None) -> list[dict]:
    """The practice calendar: every record of a type that extends the core's ``Booking``, read through it (``start``,
    ``end``, ``reason``, ``subject``, ``with`` members, ``cancelled``), as ``{record, type, ...those}``. Nothing here
    knows how a pack keeps them. ``active_only`` leaves out cancelled ones. ``member``: each as that member may read it
    (a reason of a class they may not read is not there)."""
    out = []
    for rtype in hub.of_base("Booking"):
        rt = hub.record_type(rtype)
        held = (rt.base_map.get("start") or {}).get("fields") or [] if rt else []
        start_path = rt.field(held[0])["path"] if len(held) == 1 else None
        ranged = bool((since or until) and start_path and "*" not in start_path and schema.FIELD_PATH.fullmatch(start_path))
        for rec in hub.records.search(rtype, limit=None, field=start_path if ranged else None,
                                      since=since if ranged else None, until=until if ranged else None):
            raw = schema.project(rt, rec, hub.member_types())
            if member is not None:
                rec = hub.redact(member, rec)
            seen = schema.project(rt, rec, hub.member_types())
            hidden = bool((visibility.withheld(rec) or {}).get("whole"))
            # the time is held whoever may read what it is for: when and with whom come from the booking itself, so a
            # visit a directive withholds still holds its slot (shown as Busy), never shows as free time; whose visit
            # it is, and why, stay withheld (never on the patient's own list of what is coming)
            seen.update({k: raw.get(k) for k in ("start", "end", "with", "cancelled")})
            if hidden:
                seen.update(subject=None, reason="", kind="", priority="", overbooked="")
            start = str(seen.get("start") or "")
            if not start:  # asked for, not yet booked: it holds no time
                continue
            if not ranged and ((since and start < since) or (until and start >= until)):
                continue
            if active_only and seen.get("cancelled"):
                continue
            out.append({"record": rec, "type": rtype, "start": start, "end": str(seen.get("end") or ""),
                        "reason": seen.get("reason") or "", "subject": seen.get("subject"),
                        "with": list(seen.get("with") or []), "cancelled": bool(seen.get("cancelled")),
                        "kind": seen.get("kind") or "", "priority": seen.get("priority") or "",
                        "overbooked": seen.get("overbooked") or "", "withheld": hidden})
    return out


def act_booking_book(engine, run, step, params, actor):
    """Book a slot ``YYYY-MM-DDTHH:MM|<member id>`` (as ``free-slots`` offers them) for the run's subject, unless
    someone took it in the meantime. Written through the core's ``Booking`` fields into the practice's booking type
    (``type`` names one when it has several). ``ref``: the booking record already made (asked for, with no time yet),
    which is given the time; ``"{{run.record}}"`` when the run is about it."""
    hub = engine.hub
    slot = str(params.get("slot") or "")
    if "|" not in slot:
        raise ValueError(f"booking.book: {slot!r} is not a slot")
    when, member = slot.split("|", 1)
    start = datetime.strptime(when, "%Y-%m-%dT%H:%M")
    kinds = hub.of_base("Booking")
    rtype = str(params.get("type") or (kinds[0] if len(kinds) == 1 else ""))
    if rtype not in kinds:
        raise ValueError(f"booking.book: say which booking type ({', '.join(kinds) or 'the practice keeps none'})")
    minutes = int((hub.pack.calendar or {}).get("slot_minutes", 15)) if hub.pack else 15
    ref = str(params.get("ref") or "")
    own = str(run.get("record") or "")
    if not ref and run.get("subject") not in (None, "", own) and own.split("/", 1)[0] in kinds:
        ref = own  # a run about its own booking (asked for, not yet booked) books that one, not another
    existing = None
    if ref:
        if "/" not in ref or ref.split("/", 1)[0] not in kinds:
            raise ValueError(f"booking.book: {ref!r} is not a booking")
        rtype = ref.split("/", 1)[0]
        existing = hub.records.read(*ref.split("/", 1))
        if existing is None:
            raise ValueError(f"booking.book: there is no {ref}")
    for b in bookings(hub, since=when, until=when + "~"):
        if b["start"][:16] == when and member in b["with"] and f"{b['type']}/{b['record']['id']}" != ref:
            raise ValueError(f"the slot {when} with {member} was taken in the meantime; propose another")
    who = hub.member(member)
    if existing is not None:
        rt = hub.record_type(rtype)
        had = schema.project(rt, existing, hub.member_types())
        subject = params.get("subject")
        if subject and str(had.get("subject") or "") != str(subject):
            raise ValueError(f"booking.book: {ref} is about someone else")
        given = {k: str(params[k]) for k in ("reason", "kind", "priority") if params.get(k) and k in rt.base_map}
        rec = schema.compose(rt, {"start": start.isoformat(timespec="minutes"),
                                  "end": (start + timedelta(minutes=minutes)).isoformat(timespec="minutes"),
                                  "with": [member], **given},
                             hub.member_ref_type(), {**existing, **dict(params.get("defaults") or {})})
        hub.update_record(actor, ref, rec, run_id=run["id"])
        return {"reference": ref, "when": start.strftime("%A %B %d at %H:%M"), "member": member,
                "member_name": who["name"]}
    rec = schema.compose(hub.record_type(rtype), {
        "start": start.isoformat(timespec="minutes"),
        "end": (start + timedelta(minutes=minutes)).isoformat(timespec="minutes"),
        "reason": str(params.get("reason") or ""), "subject": params.get("subject") or run["record"],
        "with": [member],
        **{k: str(params[k]) for k in ("kind", "priority") if params.get(k) and k in hub.record_type(rtype).base_map}},
        hub.member_ref_type(), dict(params.get("defaults") or {}))
    made = hub.create_record(actor, rtype, rec, run_id=run["id"], origin=_origin(step))
    return {"reference": f"{rtype}/{made['id']}", "when": start.strftime("%A %B %d at %H:%M"),
            "member": member, "member_name": who["name"]}


CORE_ACTIONS["booking.book"] = act_booking_book


def prov_free_slots(engine, run, step, member):
    """Free appointment slots in the practice calendar, for the next ``lookahead_days`` working days.

    ``max_slots`` caps each provider's free slots on each day, never the whole list: every working day in the window
    and every provider is offered, so a patient who asked for a later day or a named physician can have it (a cap on
    the whole list offered only the first morning). Each option also says its day and whom it is with (``day``,
    ``day_label``, ``time``, ``with``, ``with_name``), so a page can let the scheduler pick a day and a provider
    first. Earliest first; at one time, providers in the role's order."""
    hub = engine.hub
    cal = hub.pack.calendar if hub.pack else {}
    minutes = int(cal.get("slot_minutes", 15))
    stride = minutes * int(cal.get("slot_stride", 4))
    cap = int(cal.get("max_slots", 24))
    hours = cal.get("hours", {})
    role = cal.get("provider_role")
    providers = [m for m in (hub.role(role)["holders"] if role else []) if m["kind"] == "person" and m["status"] == "active"]
    busy = set()
    now = engine.now()
    for b in bookings(hub, since=now.date().isoformat()):
        for who in b["with"]:
            busy.add((who, b["start"][:16]))
    out = []
    day = now.date()
    days_seen = 0
    while days_seen < int(cal.get("lookahead_days", 5)):
        day += timedelta(days=1)
        key = day.strftime("%a").lower()[:3]
        if key not in hours:
            continue
        days_seen += 1
        start_s, end_s = hours[key]
        first = datetime.combine(day, datetime.strptime(start_s, "%H:%M").time())
        end = datetime.combine(day, datetime.strptime(end_s, "%H:%M").time())
        day_label = day.strftime("%a %b %d")
        for order, prov in enumerate(providers):
            t, n = first, 0
            while t + timedelta(minutes=minutes) <= end and n < cap:
                stamp = t.strftime("%Y-%m-%dT%H:%M")
                if (prov["id"], stamp) not in busy:
                    out.append({"value": f"{stamp}|{prov['id']}",
                                "label": f"{day_label} {t.strftime('%H:%M')} with {prov['name']}",
                                "day": day.isoformat(), "day_label": day_label, "time": t.strftime("%H:%M"),
                                "with": prov["id"], "with_name": prov["name"], "_order": order})
                    n += 1
                t += timedelta(minutes=stride)
    out.sort(key=lambda o: (o["value"][:16], o.pop("_order")))
    return out


def prov_members(engine, run, step, member):
    return [{"value": m["id"], "label": f"{m['name']} ({m['kind']})"} for m in engine.hub.members() if m["status"] == "active"]


CORE_PROVIDERS = {"free-slots": prov_free_slots, "members": prov_members}


# urgent work first, then what is due soonest, then the oldest
def _queue_key(item: dict):
    """The order work is shown in: urgent first, then by due time (none last), then oldest first."""
    return (0 if item.get("priority") == "urgent" else 1, item.get("due_at") is None, item.get("due_at") or "",
            item.get("created_at") or "", item.get("n") or 0)


def _same_value(a, b) -> bool:
    """Two values a start form gave for the same thing, the same as sent: equal numbers ("6.6" and 6.6), or the same
    words but for spacing and a trailing unit ("4.0-6.0" and "4.0-6.0 %")."""
    try:
        return float(str(a).strip()) == float(str(b).strip())
    except ValueError:
        pass
    x, y = sorted(("".join(str(v).split()).lower() for v in (a, b)), key=len)  # x the shorter
    return x == y or (bool(x) and y.startswith(x) and not y[len(x)].isalnum() and not y[len(x)] in ".-")


def _plain(doc: dict | None) -> dict | None:
    """A document as the hub hands it out: its fields, without the store's ``_id``."""
    return {k: v for k, v in doc.items() if k != "_id"} if doc is not None else None


_REF = re.compile(r"[A-Z][A-Za-z0-9_]*/[A-Za-z0-9_.-]+")


def _refs_in(value, out: set) -> set:
    """Every record reference in this value (a string, or a key), as ``Type/id``."""
    if isinstance(value, str):
        if _REF.fullmatch(value):
            out.add(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            _refs_in(k, out)
            _refs_in(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _refs_in(v, out)
    return out


def run_refs(run: dict) -> list[str]:
    """The records a run is about: its own, and every one its input and steps carry (the visit a note documents,
    the result a review is of). A run shows on each of their pages."""
    out = _refs_in(run.get("ctx"), set())
    if run.get("record"):
        out.add(run["record"])
    return sorted(out)


# runs with a step waiting: a step's state is ``steps.<step id>.state``
_WAITING = {"$expr": {"$in": ["waiting", {"$map": {"input": {"$objectToArray": {"$ifNull": ["$steps", {}]}},
                                                    "as": "s", "in": "$$s.v.state"}}]}}


OPEN_WORK = ("open", "claimed", "held")


def _waits(run: dict) -> dict:
    """What a run's waits depend on, kept on the run so a write rechecks only the runs it can wake: ``waits_on`` is
    its record (a wait's ``until`` is pure, and sees only the run's own record, its context and the time), or "*"
    for a wait over a list (``for_each`` reads other records) or a run with no record; None when nothing waits.
    ``resting``: every step still open is a wait no one is asked to watch (no role, no time it ends): a document in
    force, an item in stock. Nothing is to be done about it, so it is not open work on the record's page, on the
    board or in the metrics."""
    st = [v for v in (run.get("steps") or {}).values() if isinstance(v, dict)]
    waiting = [v for v in st if v.get("state") == "waiting"]
    if not waiting or run.get("status") != "running":
        return {"waits_on": None, "resting": False}
    on = "*" if any(v.get("each") for v in waiting) or not run.get("record") else run["record"]
    rests = all(v.get("rests") for v in waiting) \
        and not any(v.get("state") in ("active", "failed", *JOB_STATES) for v in st)
    return {"waits_on": on, "resting": rests}


JOB_STATES = ("queued", "running")  # a block automation waiting for a worker, and one a worker is running
JOB_MARGIN = 60    # seconds past twice an engine's own time limit before a job still "running" is taken as cut short
JOB_ATTEMPTS = 2   # how often a python job cut short is put back in the queue by itself (an engine's: never)


def _jobs(run: dict) -> list | None:
    """The steps of a run that are jobs in the queue or under way (``runs.jobs``, so a worker finds them by index);
    None when there are none, or the run no longer runs."""
    if run.get("status") != "running":
        return None
    return [sid for sid, v in (run.get("steps") or {}).items()
            if isinstance(v, dict) and v.get("state") in JOB_STATES] or None


def _item_doc(hub, **fields) -> dict:
    """A new work item with every field it has: what is not given takes its default."""
    doc = {"id": None, "n": hub.db.next_seq("items"), "run_id": None, "step_id": "", "kind": None, "title": "",
           "role": None, "assignee": None, "persons_only": 0, "status": "open", "claimed_by": None, "output": None,
           "note": "", "cause": None, "created_at": utcnow(), "updated_at": utcnow(), "due_at": None, "escalated": 0,
           "priority": "normal", "escalate_next": None,
           # one item per run walks its workflow: the step it is at now, what it did so far, and the child items of
           # people working in parallel (or once per person of a step's for_each)
           "step_title": "", "history": [], "parent_id": None, "children": [], "wave": [], "step_started_at": None,
           "waiting": ""}
    doc.update(fields)
    doc["_id"] = doc["id"]
    return doc


def _run_doc(**fields) -> dict:
    """A new run with every field it has: what is not given takes its default."""
    doc = {"id": None, "workflow_id": None, "version": None, "title": "", "record": None, "subject": None,
           "status": "running", "ctx": {}, "steps": {}, "started_by": None, "root_event": None, "item": None,
           "waits_on": None, "resting": False, "jobs": None, "picks": None, "at": None, "deadline_at": None,
           "deadline_missed_at": None, "created_at": utcnow(), "updated_at": utcnow()}
    doc.update(fields)
    doc["_id"] = doc["id"]
    doc["refs"] = run_refs(doc)
    return doc


def home_run(hub, wid: str, *, title: str, started_by: str, cause, record: str | None = None,
             ctx: dict | None = None) -> str:
    """The run one of the hub's own notices hangs on (an event no workflow took, an emergency access, something
    about a member's account, a change waiting for approval, a mention): of the hub's own workflow ``wid``
    (version 0), ended as it is made, its steps passed over. Returns its id."""
    wf, _ = hub.workflow(wid, 0)
    run_id = _new_id("run")
    hub.db.runs.insert(_run_doc(id=run_id, workflow_id=wid, version=0, title=title, record=record,
                                status="completed", ctx=ctx or {}, steps={s.id: {"state": "skipped"} for s in wf.steps},
                                started_by=started_by, root_event=cause))
    return run_id


class Engine:
    def __init__(self, hub):
        self.hub = hub
        self._now = None
        self._zone = None  # (until, the practice's ZoneInfo): ``zone`` reads the setting at most every 2 seconds
        self._advancing: set = set()  # runs advancing now (a record change inside one does not re-enter it)
        # the people chosen, in the transition that is being made, for the work it sets going: "<workflow>/<step>" ->
        # member id (``assignments_needed``); read by ``_route`` for work that names no one itself
        self._picks: dict = {}
        self._local = threading.local()  # per thread: whether blocks run here and now (``jobs_inline``)
        self.job_delay = 0.0  # seconds a worker waits before each job (``serve --dev --job-delay``: to watch one run)

    def zone(self):
        """The practice's time zone (``[practice] timezone``, as its policy owners last set it). Raises
        ``clock.ZoneError`` for a name the tz database does not know, or when this machine has no tz data."""
        import time
        got = self._zone
        if got and got[0] > time.monotonic():
            return got[1]
        hub = self.hub
        name = hub.practice.get("timezone") or ((hub.pack.practice or {}).get("timezone") if hub.pack else None)
        tz = clock.zone(name)
        self._zone = (time.monotonic() + 2, tz)  # a page shows many times: read the setting once, not per time
        return tz

    def now(self) -> datetime:
        """The practice's wall time now, without an offset: what its calendar shows, the date its notes carry and
        the ``today`` its rules compare with (``clock``). Never the server's clock, which is often UTC."""
        tz = self.zone()
        return clock.local(self._now, tz) if self._now else clock.now(tz)

    def instant(self) -> datetime:
        """Now as an aware UTC instant: the tick's own time while a tick runs (``tick(now)``), else the clock's.
        Timers and deadlines count from and to instants, so a clock change never moves them."""
        return getattr(self, "_tick_at", None) or clock.utc(self.now(), self.zone())

    def utc(self, t: datetime) -> str:
        """A time as the track writes it (UTC, ISO, Z). A naive time is the practice's wall time."""
        return clock.stamp(t, self.zone())

    # ------------------------------------------------------------------ runs
    def run(self, run_id: str) -> dict:
        row = self.hub.db.runs.get(run_id)
        if not row:
            from .hub import NotFound
            raise NotFound(f"there is no run {run_id}")
        return _plain(row)

    def runs(self, *, record: str | None = None, mentions: str | None = None, status: str | None = None,
             limit: int = 100) -> list[dict]:
        """``record``: runs about that record. ``mentions``: runs about it or that carry it in their inputs or data
        (a lab-result review is about the patient, and its input is the result)."""
        flt: dict = {}
        if record:
            flt["record"] = record
        if status:
            flt["status"] = status
        order = [("created_at", -1)]
        if mentions:  # about it, or carrying it anywhere in its data: a run keeps the records it carries in ``refs``
            flt["refs"] = mentions
        return [_plain(r) for r in self.hub.db.runs.find(flt, sort=order, limit=limit)]

    def _save(self, run: dict) -> None:
        # the people chosen, in the transition being made, for this run's work: kept on the run until that work
        # starts (after an agent's step, a wait), each used once (``_route``)
        mine = {k: v for k, v in self._picks.items() if k.startswith(f"{run['workflow_id']}/")}
        if mine:
            run["picks"] = {**(run.get("picks") or {}), **mine}
        self.hub.db.runs.set(run["id"], {"ctx": run["ctx"], "steps": run["steps"], "status": run["status"],
                                         "refs": run_refs(run), **_waits(run), "jobs": _jobs(run),
                                         "updated_at": utcnow(),
                                         **({"picks": run["picks"]} if mine else {})})

    def automation_actor(self, run: dict) -> dict:
        return {"id": f"workflow:{run['workflow_id']}@v{run['version']}", "kind": "automation", "name": run["title"],
                "status": "active"}

    def _check_references(self, wf, record: str | None, values: dict) -> list[str]:
        """A record named on the start form exists, is of a type the input takes, and (for a workflow about a
        record, unless ``of_record = false``) is about that same record: a lab review never files another
        patient's result, a note never lands on another patient's visit. Checked before anything runs, so a
        wrong reference never stops a signed decision halfway."""
        hub, bad = self.hub, []
        about = None
        if record:
            rec = hub.records.read(*record.split("/", 1))
            about = hub.subject_of(rec) if rec else None
        for f in wf.inputs:
            val = values.get(f["name"])
            if f["type"] != "reference" or not val:
                continue
            rtype, rid = val.split("/", 1)
            targets = f.get("target")
            if targets:
                allowed = hub.concrete([targets] if isinstance(targets, str) else targets)
                if rtype not in allowed:
                    bad.append(f"{f['label']}: {val} is not a {' or '.join(allowed)}")
                    continue
            rec = hub.records.read(rtype, rid) if hub.record_type(rtype) else None
            if not rec:
                bad.append(f"{f['label']}: there is no {val}")
                continue
            if record and f.get("of_record", True) and hub.subject_of(rec) != about:
                bad.append(f"{f['label']}: {val} is about someone else, not this {record.split('/')[0].lower()}")
            elif f.get("active") and not schema.is_active(hub.record_type(rtype), rec):
                bad.append(f"{f['label']}: {val} is no longer active (it is over or cancelled)")
        return bad

    def start(self, actor: dict, wid: str, *, record: str | None = None, inputs: dict | None = None,
              cause: int | None = None, via_automation: bool = False, parent: str | None = None,
              allow_duplicate: bool = False, on_create: bool = False, previous: str | None = None,
              origin: dict | None = None) -> dict:
        """``allow_duplicate``: the starter confirmed starting another run although one with the same
        ``trigger.one_open_per`` value is open (only where the trigger has ``allow_duplicate = true``).

        A workflow about a record type with ``[new_record]`` may be started on what such a record is about (its
        subject): the run makes the record, as its first act, and is about it from then on. ``on_create``: started
        because its record was just made (the type names this workflow); the start form is what the record says
        (each ``[new_record]`` field that is one input gives it back), and nothing on it is required. ``previous``: the
        run of the same workflow that walked the record's last pass (its ``not_by`` still counts: see ``_did``).
        ``origin``: for a run a step of ``parent`` started, ``{step, edge}`` (the move that set it going)."""
        from .hub import Denied, Invalid
        hub = self.hub
        wf, row = hub.workflow(wid)
        if not via_automation:
            if actor.get("status") != "active":
                raise Denied("an inactive member cannot start work")
            hub.require(actor, "starter", f"workflow:{wid}", f"start {wf.title}")
        subject, make = None, None
        if wf.record:
            if record and "/" in record and not hub.is_a(record.split("/")[0], wf.record) and wf.new_record:
                # started on what the record will be about (a person): the run makes its record, then works it
                make = self._new_record_field(wf, record.split("/")[0])
                if make is None:
                    raise Invalid(f"a {wf.record} is not about a {record.split('/')[0]}; say which one")
                subject, record = record, None
                own = self._own_record_input(wf, inputs or {}, subject)
                if own:  # the start form names one already made (the result the lab filed): the run walks that one
                    record, make = own, None
                    walking = [r for r in self.runs(record=own, status="running") if r["workflow_id"] == wid]
                    if walking:  # the same submission again (the lab sending its result twice): the run walking it
                        self._same_submission(wf, walking[0], inputs or {})
                        return walking[0]
            elif not record or not hub.is_a(record.split("/")[0], wf.record):
                raise Invalid(f"{wf.title} is about a {wf.record}; say which one")
            got = hub.records.read(*(record or subject).split("/", 1))
            if not got:
                raise Invalid(f"there is no {record or subject}")
            if record:
                subject = hub.subject_of(got)
        given = dict(inputs or {})
        if record and wf.new_record:
            rt_rec = hub.record_type(wf.record)
            for name, tmpl in wf.new_record.items():
                m = re.fullmatch(r"\{\{\s*input\.([A-Za-z0-9_-]+)\s*\}\}", str(tmpl))
                f = rt_rec.field(name) if rt_rec else None
                if m and f and given.get(m.group(1)) in (None, ""):
                    val = schema.first(got, f["path"])
                    if val not in (None, ""):
                        given[m.group(1)] = schema.ref_of(val) if f["type"] == "reference" else val
        try:
            values = coerce([{**f, "required": False} for f in wf.inputs] if on_create else wf.inputs, given)
        except FieldError as exc:
            raise Invalid("the start form has problems", exc.problems) from None
        bad = self._check_references(wf, record or subject, values)
        if bad:
            raise Invalid("the start form has problems", bad)
        run_id = _new_id("run")
        # who the run is about is shown per viewer (Hub.run_title): a title stored with a name in it would reach
        # everyone who can list runs, whether or not they may read the record
        title = wf.title
        pins = {}
        for s in wf.steps:  # a run keeps the block versions that were approved when it started
            if s.block and s.block not in pins:
                try:
                    pins[s.block] = hub.block(s.block)[1]["version"]
                except Exception:  # noqa: BLE001 - reported when the step runs
                    pass
        skills = {}
        for s in wf.steps:  # and the skill versions, so a skill changed mid-run does not change what agents were told
            b = None
            if s.block:
                try:
                    b = hub.block(s.block, pins.get(s.block))[0]
                except Exception:  # noqa: BLE001
                    b = None
            name = (b.extends if b else None) or s.skill
            if name and name not in skills:
                v = hub.skill_version(name)
                if v is not None:
                    skills[name] = v
        # the record's own type: one under the workflow's has its lifecycle, at the same path
        lc_type = hub.record_type((record or "").split("/")[0] or wf.record) if wf.record else None
        start_state = schema.first(got, lc_type.lifecycle["path"]) if record and lc_type and lc_type.lifecycle else \
            None
        ctx = {"run": {"id": run_id, "record": record, "subject": subject, "started_by": actor["id"],
                       "start_state": start_state, **({"previous": previous} if previous else {}),
                       "workflow": wid, "parent": parent, **({"origin": origin} if origin else {}), "blocks": pins, "skills": skills, "now": utcnow(),
                       "today": self.now().date().isoformat()},
               "input": values}
        with hub.db:  # checked in the transaction that starts it: two people pressing at once start one
            self._refuse_duplicate(wf, record, values, by_hand=not via_automation and cause is None,
                                   confirmed=allow_duplicate, subject=subject)
            self._refuse_second_owner_run(wf, record)
            if not via_automation and make is None:
                self._refuse_start_over(wf, record)
            if make is not None:  # the record is made in the same step: refused, nothing is left behind
                record = self._make_record(wf, row, run_id, make, subject, ctx, cause)
                ctx["run"]["record"] = record
                lc = lc_type.lifecycle if lc_type else None
                ctx["run"]["start_state"] = schema.first(hub.records.read(*record.split("/", 1)), lc["path"]) \
                    if lc else None
            hub.db.runs.insert(_run_doc(id=run_id, workflow_id=wid, version=row["version"], title=title,
                                        record=record, subject=subject, ctx=ctx, started_by=actor["id"],
                                        steps={s.id: {"state": "pending"} for s in wf.steps}))
            seq = hub.track.append(actor, "run.started", record or "", run_id=run_id, cause=cause,
                                   data={"workflow": wid, "version": row["version"], "title": title, "input": values,
                                         "parent": parent, **({"origin": origin} if origin else {})})
            hub.db.runs.set(run_id, {"root_event": seq, "item": self._run_item(run_id, wf, title, seq)})
            if getattr(wf, "deadline", None):  # [deadline] within: when the run is to be done by
                hub.db.runs.set(run_id, {"deadline_at": self._deadline_at(wf), "deadline_missed_at": None})
            self.advance(run_id, seq)
        return self.run(run_id)

    def _own_record_input(self, wf, inputs: dict, subject: str) -> str | None:
        """A record of the workflow's own type that the start form names, about ``subject``: started on the subject
        with it, the run walks that record rather than making another. One about someone else is refused."""
        from .hub import Invalid
        for f in wf.inputs:
            ref = self._ref_of(inputs.get(f["name"]))
            if f["type"] != "reference" or not isinstance(ref, str) or not self.hub.is_a(ref.split("/", 1)[0],
                                                                                          wf.record):
                continue
            rec = self.hub.records.read(*ref.split("/", 1))
            if rec is not None and self.hub.subject_of(rec) == subject:
                return ref
        return None  # none, or one that is missing or about someone else: the start form's checks refuse it

    def _same_submission(self, wf, run: dict, inputs: dict) -> None:
        """Started again on the record a run already walks: the same submission again is that run; one that says
        something else (another value for the same result) is refused out loud, never folded into the open run."""
        from .hub import Invalid
        had = (run.get("ctx") or {}).get("input") or {}
        differ = [f.get("label") or f["name"] for f in wf.inputs
                  if f["type"] != "reference" and inputs.get(f["name"]) not in (None, "")
                  and had.get(f["name"]) not in (None, "") and not _same_value(inputs[f["name"]], had[f["name"]])]
        if differ:
            raise Invalid(f"{run['record']} is already being worked ({run['id']}) with another "
                          f"{', '.join(differ).lower()}: change that record, or start this on a new one")

    def start_on(self, wf) -> list[str]:
        """The record types a person starts ``wf`` on: its record's type, or, for a workflow that makes its record
        (``[new_record]``), what such a record is about (its subject reference's targets); each with the types under
        it that records are made as. None: about nothing."""
        if not wf.record:
            return []
        if wf.new_record:
            rt = self.hub.record_type(wf.record)
            f = next((f for f in (rt.fields if rt else []) if f["type"] == "reference" and f.get("rel") == "subject"
                      and "*" not in f["path"]), None)
            if f:
                return list(self.hub.concrete(f["target"]))
        return self.hub.concrete([wf.record])

    def _new_record_field(self, wf, subject_type: str) -> dict | None:
        """The field of the workflow's record type that says what a new one is about, for a ``subject_type``."""
        rt = self.hub.record_type(wf.record)
        if rt is None:
            return None
        return next((f for f in rt.fields if f["type"] == "reference" and f.get("rel") == "subject"
                     and "*" not in f["path"] and (subject_type in self.hub.concrete(f["target"]))), None)

    def _refuse_second_owner_run(self, wf, record: str | None) -> None:
        """A record whose type names this workflow is walked by one run of it at a time: its state is that run's
        position, which two runs would each claim."""
        from .hub import Invalid
        if not record or "/" not in record:
            return
        rt = self.hub.record_type(record.split("/", 1)[0])
        if rt is None or (rt.workflow or {}).get("id") != wf.id:
            return
        if self.hub.db.runs.find_one({"workflow_id": wf.id, "record": record, "status": "running"}, fields=["id"]):
            raise Invalid(f"{wf.title} is already under way about this {rt.label.lower()}: its own workflow walks it "
                          f"once at a time")

    def start_over_refusal(self, wf, record: str | None) -> str | None:
        """Why a member may not start ``wf`` about ``record``, when the record's type names it as its own workflow
        and the record is in a locked state, or None. Starting it puts the record in the workflow's first state,
        whatever state it was in: for a locked record (a return that was filed, a document made obsolete) that
        would open to changes what its lock keeps as it was reviewed, by no move its lifecycle has. So it is refused
        unless the type says its workflow walks the record where it is (its ``workflow.when``: a document in force
        that is revised). What the hub starts itself (a record made, sent back, picked up) asks the type the same
        thing before it starts, and is not asked again here."""
        if not record or "/" not in record:
            return None
        rt = self.hub.record_type(record.split("/", 1)[0])
        if rt is None or (rt.workflow or {}).get("id") != wf.id or not rt.lifecycle:
            return None
        rec = self.hub.records.read(*record.split("/", 1))
        state = schema.first(rec, rt.lifecycle["path"]) if rec else None
        if schema.sealed(rt, state):
            return (f"a {rt.label.lower()} that is {rt.state_label(state).lower()} is final: no work is started on "
                    f"it, and {wf.title} would change it")
        if state not in rt.lifecycle.get("locked", []) or schema.starts_workflow(rt, rec) == wf.id:
            return None
        return (f"a {rt.label.lower()} that is {rt.state_label(state).lower()} is not started again: it is kept as "
                f"it is, and {wf.title} would take it back to its first step")

    def _refuse_start_over(self, wf, record: str | None) -> None:
        from .hub import Invalid
        why = self.start_over_refusal(wf, record)
        if why:
            raise Invalid(why)

    def _re_enter(self, run: dict, cause) -> None:
        """A lifecycle that loops (a document revised after it was approved, a change order sent back, a CAPA found
        not effective) is walked one pass per run, since a workflow has no loops: a run that ends with its record
        moved since it started and back where its type's workflow starts hands the record on to a new run of it,
        of the version in force now, which walks the next pass. A run that never moved its record ends for good,
        whatever its version (it cannot start itself over and over): its record is picked up when it is next
        written (``Hub._pick_up``). The new run's ``run.previous`` is this one, so who did what before still
        counts."""
        hub = self.hub
        ref = str(run.get("record") or "")
        if "/" not in ref:
            return
        try:
            rt = hub.record_type(ref.split("/", 1)[0])
            rec = hub.records.read(*ref.split("/", 1))
            if rt is None or rec is None or not self._walked_again(run, rt, rec) \
                    or hub.db.runs.find_one({"workflow_id": run["workflow_id"], "record": ref, "status": "running"}):
                return  # work still open walks it
            with hub.db:
                self.start({"id": f"type:{rt.id}", "kind": "automation", "name": rt.label, "status": "active"},
                           run["workflow_id"], record=ref, inputs=dict((run.get("ctx") or {}).get("input") or {}),
                           cause=cause, via_automation=True, on_create=True, previous=run["id"])
        except Exception as exc:  # noqa: BLE001 - the run that ended stays ended; the hand-on is noted
            hub.track.append(self.automation_actor(run), "trigger.failed", ref, run_id=run["id"], cause=cause,
                             data={"workflow": run["workflow_id"], "error": f"walking the next pass: {exc}"})

    def _walked_again(self, run: dict, rt, rec: dict) -> bool:
        """Whether the record a run of its type's own workflow ended on is walked on by another run of it
        (``_re_enter``): it moved during the run and is back where its workflow starts."""
        hub = self.hub
        wid = run["workflow_id"]
        if schema.starts_workflow(rt, rec) != wid or hub.record_workflow(rt) != wid:
            return False
        return bool(hub.db.events.find_one({"action": "record.transitioned", "subject": run.get("record"),
                                            "seq": {"$gt": run.get("root_event") or 0}}, fields=["seq"]))

    def stop(self, actor: dict, run_id: str, why: str, cause: int | None) -> None:
        """Stop a record's own run because its record was moved out of its hands (entered in error, cancelled): its
        open items are cancelled and ``run.stopped`` goes on the track, caused by the move. Nothing is abandoned: the
        move already says where the record is."""
        run = self.run(run_id)
        if run["status"] != "running":
            return
        run["status"] = "cancelled"
        self._save(run)
        self.hub.db.items.update({"run_id": run_id, "status": {"$in": ["open", "claimed", "held", "waiting"]}},
                                 {"$set": {"status": "cancelled", "updated_at": utcnow()}}, many=True)
        self.hub.track.append(actor, "run.stopped", run["record"] or "", run_id=run_id, cause=cause,
                              data={"reason": why})

    def follow_let_through(self, actor: dict, run_id: str, new_state: str, cause: int | None) -> None:
        """The run's record was moved to ``new_state`` by a person or another run, through a state its open step
        lists in ``moves``. A task (with nothing it must ask) whose own ``state`` is where the record went is done
        by that move, by whoever made it: an author who sends a revision for review on its page has done the step
        that asked them to. A task or decision whose ``when`` no longer holds of the record as it is now is
        withdrawn (``step.withdrawn``): a purchaser's order for an item a person cleared or discontinued is nobody's
        work, and the steps after it go on as if it were not needed."""
        hub = self.hub
        run = self.run(run_id)
        if run["status"] != "running":
            return
        wf, _ = hub.workflow(run["workflow_id"], run["version"])
        live = self._live_ctx(run)
        changed = []
        for s in wf.steps:
            st = run["steps"].get(s.id) or {}
            if st.get("state") != "active" or s.kind not in ("task", "gate") or s.for_each:
                continue
            if s.kind == "task" and s.state == new_state and not any(f.get("required") for f in s.fields):
                how, done = "moved", {"state": "done", "item": st.get("item"), "by": actor["id"], "moved": True}
                run["ctx"][s.id] = {"by": actor["id"], "by_kind": actor.get("kind"), "moved_to": new_state}
            elif s.when and not expr.holds(s.when, live):
                how, done = "withdrawn", {"state": "skipped", "item": st.get("item"), "withdrawn": True}
                run["ctx"][s.id] = {"withdrawn": True, "moved_to": new_state, "moved_by": actor["id"]}
            else:
                continue
            run["steps"][s.id] = done
            changed.append((s, how, st.get("item")))
        if not changed:
            return
        self._save(run)
        for s, how, item_id in changed:
            if item_id:
                walks = item_id == run.get("item")
                hub.db.items.set(item_id, {"status": "waiting" if walks else ("done" if how == "moved" else "cancelled"),
                                           "claimed_by": actor["id"] if how == "moved" else None,
                                           "updated_at": utcnow()})
                self._hist_update(item_id, s.id, done_at=utcnow(), by=actor["id"] if how == "moved" else None,
                                  outcome="done" if how == "moved" else "withdrawn")
            hub.track.append(actor, "step.done" if how == "moved" else "step.withdrawn", f"step:{s.id}",
                             run_id=run_id, cause=cause, data={"step": s.id, "moved_to": new_state,
                                                               **({"when": s.when} if how == "withdrawn" else {})})

    def _make_record(self, wf, row, run_id: str, subject_field: dict, subject: str, ctx: dict, cause) -> str:
        """The record a run started on its subject makes: the workflow's ``[new_record]`` fields (placeholders over
        ``run`` and ``input``), about the subject. Written by the workflow (its own run), never started again by it."""
        hub = self.hub
        rt = hub.record_type(wf.record)
        values = wfmod.render(dict(wf.new_record), {"run": ctx["run"], "input": ctx["input"]})
        rec = self._by_field_names(rt, [(subject_field["name"], subject), *values.items()], "new_record")
        actor = self.automation_actor({"workflow_id": wf.id, "version": row["version"], "title": wf.title})
        made = hub.create_record(actor, wf.record, rec, cause=cause, run_id=run_id, start_workflow=False)
        return f"{wf.record}/{made['id']}"

    def _by_field_names(self, rt, pairs, what: str) -> dict:
        """A record of ``rt`` from (field name, value) pairs, each written where its field keeps it. An empty value is
        left out; so is a member that is not one (a run, not a person) and a number given words ("<0.1")."""
        rec: dict = {}
        for name, val in pairs:
            if val in (None, "", [], {}):
                continue
            f = rt.field(name)
            if f is None:
                raise ValueError(f"{what}: {rt.label} has no field {name!r}")
            if f["type"] == "member" and isinstance(val, str) and not self.hub.db.members.get(val):
                continue  # started by another workflow, not a person: no one to name (its cause says which run)
            if f["type"] == "reference" and isinstance(val, str):
                val = {"reference": val}
            if f["type"] == "number" and isinstance(val, str):  # a placeholder fills in words: "6.6" is 6.6
                try:
                    val = float(val)
                    val = int(val) if val.is_integer() else val
                except ValueError:
                    continue  # not a number: left out, never a step refused (other fields keep the words)
            if f["type"] == "decimal" and not isinstance(val, bool):  # "1,234.5" or 12.345: exact, at its places
                try:
                    val = decimals.to_decimal(val, f["scale"])
                except ValueError:
                    continue
            rec = schema.place(rec, f, val)
        return rec

    # ------------------------------------------------------------------ sub-records: a record's step makes another
    def make_child_record(self, run: dict, params: dict, actor: dict, step=None) -> dict:
        """``record.child``: a step makes a record of another type (``type``; ``data`` by its field names) about the
        same subject as the run's record and linked to it (its parent: the new record's one reference to the
        parent's type, or the field ``link`` names). The new type's own workflow starts on it in the same
        transaction, as a run whose parent is this run and whose cause is the record made, with ``input`` as its
        start form; it walks its own lifecycle. A step that should wait for it waits on its reference (a wait with
        ``for_each`` and ``until = "each_closed"``); one that should not, does not."""
        hub = self.hub
        rtype = params.get("type")
        rt = hub.record_type(rtype) if rtype else None
        if rt is None:
            raise ValueError(f"record.child: there is no record type {rtype!r}")
        parent = str(run.get("record") or "")
        subject = str(run.get("subject") or "") or parent
        if "/" not in parent:
            raise ValueError("record.child: the run has no record to be the parent")
        sub_f = self._subject_field(rt, subject.split("/", 1)[0])
        link_f = self._parent_field(rt, parent.split("/", 1)[0], params.get("link"))
        if sub_f is None or link_f is None:
            raise ValueError(f"record.child: a {rt.label.lower()} has no field for "
                             f"{'its subject' if sub_f is None else 'the record it comes from'}")
        data = params.get("data") or {}
        if not isinstance(data, dict):
            raise ValueError("record.child: data is a table of field = value")
        pairs = [(sub_f["name"], subject), (link_f["name"], parent)]
        rec = self._by_field_names(rt, pairs + [(k, v) for k, v in data.items()
                                                if k not in (sub_f["name"], link_f["name"])], "record.child")
        made = hub.create_record(actor, rt.id, rec, run_id=run["id"], start_workflow=False)
        ref = f"{rt.id}/{made['id']}"
        ev = hub.db.events.find_one({"action": "record.created", "subject": ref}, sort="seq")
        # the parent's own history says what was made from it (with or without a workflow of its own)
        seq = hub.track.append(actor, "record.child", parent, run_id=run["id"], cause=ev["seq"] if ev else None,
                               data={"child": ref, "type": rt.id})
        child = self._start_child_run(rt, ref, run, params.get("input") or {}, seq, origin=_origin(step))
        return {"id": made["id"], "reference": ref, **({"run_id": child["id"]} if child else {})}

    def _start_child_run(self, rt, ref: str, parent_run: dict, inputs: dict, cause,
                         origin: dict | None = None) -> dict | None:
        """The sub-record's own workflow (its type's), started on it as a child run of ``parent_run``."""
        rec = self.hub.records.read(*ref.split("/", 1))
        wid = schema.starts_workflow(rt, rec) if rec else None
        if not wid:
            return None
        starter = {"id": f"type:{rt.id}", "kind": "automation", "name": rt.label, "status": "active"}
        return self.start(starter, wid, record=ref, inputs=inputs, cause=cause, via_automation=True,
                          on_create=True, parent=parent_run["id"], origin=origin)

    def _subject_field(self, rt, subject_type: str) -> dict | None:
        return next((f for f in rt.fields if f["type"] == "reference" and f.get("rel") == "subject"
                     and "*" not in f["path"] and subject_type in self.hub.concrete(f["target"])), None)

    def _parent_field(self, rt, parent_type: str, name: str | None) -> dict | None:
        """The field of ``rt`` that links a sub-record to its parent: the one named, else its one reference to the
        parent's type (not its subject)."""
        if name:
            f = rt.field(name)
            return f if f and f["type"] == "reference" else None
        found = [f for f in rt.fields if f["type"] == "reference" and f.get("rel") != "subject"
                 and parent_type in self.hub.concrete(f["target"])]
        return found[0] if len(found) == 1 else None

    def children_of(self, ref: str) -> list[dict]:
        """The sub-records made for a record (``record.child``), oldest first, as ``{ref, run}``: each with the run
        of its own workflow walking it (None for a type with no workflow), read from the parent's history."""
        out = []
        for ev in self.hub.db.events.find({"action": "record.child", "subject": ref}, sort="seq"):
            child = str((ev.get("data") or {}).get("child") or "")
            if "/" not in child:
                continue
            run = self.hub.db.runs.find_one({"record": child, "ctx.run.parent": ev.get("run_id")}) \
                if ev.get("run_id") else None
            out.append({"ref": child, "run": run})
        return out

    def parent_record(self, ref: str) -> str | None:
        """The record a sub-record was made for (``record.child``), or None."""
        ev = self.hub.db.events.find_one({"action": "record.child", "data.child": ref}, sort="seq")
        return ev["subject"] if ev else None

    def parent_of(self, run: dict) -> str | None:
        """The record a sub-record's run was made for: the record of its parent run, when that is not its subject."""
        pid = ((run.get("ctx") or {}).get("run") or {}).get("parent")
        prow = self.hub.db.runs.get(pid) if pid else None
        ref = str((prow or {}).get("record") or "")
        return ref if "/" in ref and ref not in (run.get("record"), run.get("subject")) else None

    def started_by(self, run: dict) -> dict | None:
        """The record whose move started this run, and that move's edge label: ``{ref, label}`` from the run's
        ``parent`` and ``origin``. A run started before origins were kept (or by a step on no edge) has the record
        only (``label`` None). None for a run no other record's work started."""
        rc = (run.get("ctx") or {}).get("run") or {}
        pid = rc.get("parent")
        prow = self.hub.db.runs.get(pid) if pid else None
        ref = str((prow or {}).get("record") or "")
        if "/" not in ref or ref == run.get("record"):
            return None
        label = None
        edge = (rc.get("origin") or {}).get("edge")
        if edge:
            try:
                shape = self.hub.workflow(prow["workflow_id"], prow["version"])[0].shape
            except Exception:  # noqa: BLE001 - a workflow that cannot be read names no edge
                shape = {}
            label = next((e.get("label") for e in [shape.get("start"), *(shape.get("edges") or [])]
                          if e and e.get("id") == edge), None)
        return {"ref": ref, "label": label}

    def _move_record(self, run: dict, step, actor: dict, cause) -> None:
        """``state`` on a step: the run's record moves to that lifecycle state as the step is done, through the
        record's own transitions (their rules, events and audit), by the workflow."""
        hub = self.hub
        ref = run.get("record") or ""
        rt = hub.record_type(ref.split("/", 1)[0]) if "/" in ref else None
        if rt is not None and step.on_edge and (not rt.lifecycle or step.state not in rt.states()):
            # written as states and edges, a stop the record's state field has no value for (or a record with no
            # state field): the run keeps where it is, and the record stays as it is
            run["at"] = step.state
            hub.db.runs.set(run["id"], {"at": step.state})
            return
        if rt is None or not rt.lifecycle:
            raise ValueError(f"state: the run's record ({ref or 'none'}) has no lifecycle to move through")
        rec = hub.records.read(*ref.split("/", 1))
        if rec is None:
            raise ValueError(f"state: there is no {ref}")
        now = schema.first(rec, rt.lifecycle["path"])
        if now != step.state:
            moved = schema.put(rec, rt.lifecycle["path"], step.state)
            t = rt.transition(now, step.state)
            # a person's step with a state is that move: what its form answered goes in as the fields the move asks
            got = run["ctx"].get(step.id) if step.kind == "task" and isinstance(run["ctx"].get(step.id), dict) else {}
            names = {f["name"] for f in self.fields_for(step, run)} if got else set()
            for n in (t or {}).get("asks", []):
                f = rt.field(n)
                if f and n in names and got.get(n) not in (None, ""):
                    moved = schema.put(moved, f["path"], got[n])
            by = (run["steps"].get(step.id) or {}).get("by")  # whoever did the step made the move: its stamp names them
            if step.kind == "automation" and step.on_edge:  # an edge's own move: whoever did the work it follows
                by = next((run["steps"][s]["by"] for s in reversed(step.needs or [])
                           if (run["steps"].get(s) or {}).get("by")), by)
            mover = hub.db.members.get(by) if isinstance(by, str) and t and t.get("stamp") else None
            hub.update_record(actor, ref, moved, cause=cause, run_id=run["id"], _mover=mover)

    def _signed_move(self, run: dict, step) -> tuple[str, str, str] | None:
        """(record label, state label, meaning) when the move a step with ``state`` makes from where its record is
        now is a signed transition: only a person moving the record signs it, never the workflow finishing the step."""
        ref = run.get("record") or ""
        if not step.state or "/" not in ref:
            return None
        rt = self.hub.record_type(ref.split("/", 1)[0])
        rec = self.hub.records.read(*ref.split("/", 1)) if rt is not None and rt.lifecycle else None
        if rec is None:
            return None
        t = rt.transition(schema.first(rec, rt.lifecycle["path"]), step.state)
        return (rt.label, rt.state_label(step.state).lower(), t["sign"]) if t and t.get("sign") else None

    def _refused_move(self, run: dict, step, values: dict, member: dict | None = None) -> str | None:
        """Why the move a person's step with ``state`` makes as it is done would be refused by the move's checks
        (``[[lifecycle.transition.check]]``), for the record as it would be with what the answer gives the fields the
        move asks for; None when it would not. The task is then not finished: the move would fail after it."""
        ref = run.get("record") or ""
        if not step.state or "/" not in ref:
            return None
        rt = self.hub.record_type(ref.split("/", 1)[0])
        rec = self.hub.records.read(*ref.split("/", 1)) if rt is not None and rt.lifecycle else None
        if rec is None or step.state not in rt.states():
            return None
        now = schema.first(rec, rt.lifecycle["path"])
        t = rt.transition(now, step.state) if now != step.state else None
        if not t or not t.get("check"):
            return None
        moved = schema.put(dict(rec), rt.lifecycle["path"], step.state)
        for n in t.get("asks", []):
            f = rt.field(n)
            if f and values.get(n) not in (None, ""):
                moved = schema.put(moved, f["path"], values[n])
        moved = self.hub.stamped(rt, t, moved, member or {})  # as the move would stamp it, made by whoever does it
        return self.hub.move_check_refusal(rt, ref, t, schema.exact(rt, moved))

    def _moves_due(self, run: dict, wf, cause) -> bool:
        """Move the record for each step with a ``state`` that people or a wait finished (an automation moves it as it
        runs). A move the record's lifecycle refuses is told to the practice's escalation role."""
        due = [s for s in wf.steps if s.state and run["steps"][s.id]["state"] == "done"
               and not run["steps"][s.id].get("moved")]
        for step in due:
            actor = self.automation_actor(run)
            try:
                with self.hub.db:
                    self._move_record(run, step, actor, cause)
                run["steps"][step.id]["moved"] = True
            except Exception as exc:  # noqa: BLE001
                run["steps"][step.id]["moved"] = "failed"
                seq = self.hub.track.append(actor, "step.failed", f"step:{step.id}", run_id=run["id"], cause=cause,
                                            data={"step": step.id, "state": step.state, "error": str(exc)})
                self.alert(run, step, f"{step.title}: the record did not move to {step.state!r}: {exc}",
                           role=self.hub.practice.get("escalation_role"), cause=seq, priority="urgent")
            self._save(run)
        return bool(due)

    def advance(self, run_id: str, cause: int | None) -> None:
        """Activate every step whose predecessors have finished; run automations; complete the run when done."""
        if run_id in self._advancing:
            return
        self._advancing.add(run_id)
        try:
            self._advance(run_id, cause)
        finally:
            self._advancing.discard(run_id)

    def _failed(self, run: dict, step, exc: Exception, seq: int) -> None:
        """Tell the practice's escalation role that a step failed, and each person who decided something in this
        run (the physician who signed "call the patient", "file this note") that what they decided has not all
        happened: which step waits for a fix, and which steps go on without it."""
        hub = self.hub
        wf, _ = hub.workflow(run["workflow_id"], run["version"])
        held = sorted(self._waiting_on(wf, step.id))
        titles = [wf.step(s).title for s in held]
        text = f"{step.title} failed: {exc}"
        if titles:
            text += ". Waiting on it: " + "; ".join(titles)
        text += ". Other steps go on. Fix the cause, then retry the step from the run's page."
        self.alert(run, step, text, role=hub.practice.get("escalation_role"), cause=seq, priority="urgent")
        told = set()
        for sid, out in (run.get("ctx") or {}).items():
            if isinstance(out, dict) and out.get("by_kind") == "person" and out.get("by") not in told:
                prior = wf.step(sid) if sid in {s.id for s in wf.steps} else None
                if prior is not None and prior.kind in ("gate", "task"):
                    told.add(out["by"])
                    self.alert(run, step, f"After your \"{prior.title}\": {text}", assignee=out["by"], cause=seq,
                               priority="urgent")

    def _fail_over(self, run: dict, wf, step, actor: dict, cause) -> None:
        """An edge's automation written with ``on_fail`` (states.py) failed: its record goes where that says and the
        run goes on from there. What waited on it to move the record along its edge is skipped; the failure stays
        recorded and told, and the step is not retried (the record went on without it)."""
        fid = FAILED.format(step=step.id)
        fb = next((s for s in wf.steps if s.id == fid and s.on_edge), None)
        if fb is None or run["steps"].get(fid, {}).get("state") != "pending":
            return
        hub = self.hub
        for sid in sorted(self._waiting_on(wf, step.id) - {fid}):
            s = wf.step(sid)
            if s.state and s.on_edge == fb.on_edge and run["steps"][sid]["state"] == "pending":
                run["steps"][sid] = {"state": "skipped"}
                hub.track.append(actor, "step.skipped", f"step:{sid}", run_id=run["id"], cause=cause,
                                 data={"step": sid, "failed": step.id})
        run["steps"][step.id]["failed_over"] = True
        self._save(run)
        try:
            with hub.db:
                self._move_record(run, fb, actor, cause)
        except Exception as exc:  # noqa: BLE001 - the move refused: the failure already asks a person
            run = self.run(run["id"])
            run["steps"][fid] = {"state": "failed", "error": str(exc)}
            self._save(run)
            return
        run = self.run(run["id"])
        run["steps"][fid] = {"state": "done", "by": actor["id"], "moved": True}
        self._save(run)
        self._hist_note(run, fb, outcome="done", by=actor["id"])
        hub.track.append(actor, "action.ran", f"step:{fid}", run_id=run["id"], cause=cause,
                         data={"step": fid, "action": None, "params": {}, "output": {}, "failed": step.id})

    @staticmethod
    def _waiting_on(wf, failed: str) -> set[str]:
        """Every step that waits, directly or through others, on the failed one."""
        out, grew = set(), True
        while grew:
            grew = False
            for s in wf.steps:
                if s.id not in out and any(a == failed or a in out for a in s.after):
                    out.add(s.id)
                    grew = True
        return out

    def _step_done(self, run_id: str, step, actor: dict, output: dict, params: dict, extra: dict, cause,
                   more: dict | None = None) -> int:
        """An automation did what it does: its output is the run's, the step is done (``more``: a job's times), and
        the track says so. Returns the event's number: what it caused follows from it."""
        run = self.run(run_id)  # the action may have changed the run (e.g. started a child)
        run["ctx"][step.id] = output
        run["steps"][step.id] = {"state": "done", "by": actor["id"], **({"moved": True} if step.state else {}),
                                 **(more or {})}
        self._save(run)
        self._hist_note(run, step, outcome="done", by=actor["id"])
        return self.hub.track.append(actor, "action.ran", f"step:{step.id}", run_id=run_id, cause=cause,
                                     data={"step": step.id, "action": step.action, "params": params,
                                           "output": output, **extra})

    def _step_failed(self, run: dict, wf, step, actor: dict, exc: Exception, cause, more: dict | None = None) -> int:
        """An automation failed: the step stops with what went wrong (``more``: a job's times, that it was cut
        short), the practice is told, and its record goes where ``on_fail`` says if it says. Returns the event's
        number."""
        problems = getattr(exc, "problems", None)
        if problems and problems != [str(exc)]:
            exc = ValueError(f"{exc}: {'; '.join(problems)}")
        run["steps"][step.id] = {"state": "failed", "error": str(exc), **(more or {})}
        self._save(run)
        self._hist_note(run, step, outcome="failed", by=actor["id"])
        seq = self.hub.track.append(actor, "step.failed", f"step:{step.id}", run_id=run["id"], cause=cause,
                                    data={"step": step.id, "action": step.action, "error": str(exc),
                                          **({"block": step.block} if step.block else {}),
                                          **{k: v for k, v in (more or {}).items() if k in ("attempt", "interrupted")}})
        self._failed(run, step, exc, seq)
        self._fail_over(run, wf, step, actor, seq)
        return seq

    # ------------------------------------------------------------------ jobs: blocks run outside the request
    # A block (python in the sandbox, or an engine: a webhook, Kestra) can take seconds, a minute. Run where it
    # became ready, it would hold the request that made it ready, its transaction and every other request with it.
    # So where a worker is running (``Hub.jobs_async``: ``practicehub serve``), a block automation is queued
    # (``queued``), a worker takes it (``running``), calls its engine with no transaction open, and writes what came
    # back (``done`` or ``failed``) before the run goes on. Each of the three is its own transaction, so the page
    # can show a job in the queue, under way and done. Without a worker (a command, a test) a block runs where it
    # became ready, as every other automation always does.
    @contextlib.contextmanager
    def jobs_inline(self):
        """Blocks run here and now in this thread, whatever the hub says: for a caller that answers from what a
        block found in the same request (the portal tells a person what the screen of their message advised)."""
        self._local.inline = getattr(self._local, "inline", 0) + 1
        try:
            yield
        finally:
            self._local.inline -= 1

    def _in_background(self, step) -> bool:
        return bool(step.block) and bool(getattr(self.hub, "jobs_async", False)) \
            and not getattr(self._local, "inline", 0)

    def _queue_job(self, run: dict, wf, step, actor: dict, cause) -> None:
        hub = self.hub
        st = run["steps"][step.id]
        try:
            block = self.block_of(run, step)
            engine = backend_for(block).name
        except Exception as exc:  # no such block, no engine for it: it fails where it stands
            self._step_failed(run, wf, step, actor, exc, cause)
            return
        attempt = int(st.get("attempt") or 0) + 1
        seq = hub.track.append(actor, "step.queued", f"step:{step.id}", run_id=run["id"], cause=cause,
                               data={"step": step.id, "block": block.id, "engine": engine, "attempt": attempt,
                                     "block_version": (run["ctx"]["run"].get("blocks") or {}).get(block.id)})
        run["steps"][step.id] = {"state": "queued", "queued_at": utcnow(), "engine": engine, "attempt": attempt,
                                 "cause": seq}
        self._save(run)
        hub.wake_jobs()

    def jobs(self, lane: str | None = None) -> list[tuple[str, str]]:
        """The jobs in the queue, oldest first, as ``(run id, step id)``. ``lane``: only those of that engine
        ("local": python in the sandbox, quick and never waiting behind a slow engine)."""
        found = []
        for run in self.hub.db.runs.find({"status": "running", "jobs": {"$ne": None}}, fields=["id", "steps"]):
            for sid, st in (run.get("steps") or {}).items():
                if isinstance(st, dict) and st.get("state") == "queued" and lane in (None, st.get("engine")):
                    found.append((str(st.get("queued_at") or ""), run["id"], sid))
        return [(rid, sid) for _, rid, sid in sorted(found)]

    def job_start(self, run_id: str, step_id: str) -> dict | None:
        """Take a job from the queue: check what it is given, mark it running, and say so on the track, in one
        transaction. Returns what ``job_call`` needs, or None when it is no longer in the queue (another worker took
        it, its work was stopped) or could not start (its inputs have problems: the step failed)."""
        hub = self.hub

        def take():
            run = self.run(run_id)
            st = (run.get("steps") or {}).get(step_id) or {}
            if run["status"] != "running" or st.get("state") != "queued":
                return None
            wf, _ = hub.workflow(run["workflow_id"], run["version"])
            step = wf.step(step_id)
            actor = self.automation_actor(run)
            try:
                params = wfmod.render(step.params, self._live_ctx(run))
                block, engine, inputs, meta = self._block_call(run, step, params)
            except Exception as exc:
                seq = self._step_failed(run, wf, step, actor, exc, st.get("cause"),
                                        {k: st[k] for k in ("queued_at", "engine", "attempt") if k in st})
                self.advance(run_id, seq)
                return None
            token = secrets.token_hex(8)
            run["steps"][step_id] = {**st, "state": "running", "started_at": utcnow(), "job": token,
                                     "worker": f"{socket.gethostname()}:{os.getpid()}"}
            self._save(run)
            seq = hub.track.append(actor, "step.started", f"step:{step_id}", run_id=run_id, cause=st.get("cause"),
                                   data={"step": step_id, "block": block.id, "engine": engine.name,
                                         "attempt": st.get("attempt"), "params": inputs,
                                         "block_version": meta.get("version")})
            if run.get("item"):
                self._place(run_id, seq)  # its item says what it waits on
            return {"run": run_id, "step": step_id, "token": token, "block": block, "engine": engine,
                    "inputs": inputs, "meta": meta, "params": params, "cause": seq}
        return hub.db.atomic(take)

    def job_call(self, job: dict) -> tuple[dict | None, Exception | None, int]:
        """Run the job on its engine. No transaction is open and no lock is held: this is the part that takes time.
        Returns what came back, or what went wrong, and how long it took in milliseconds."""
        if self.job_delay:
            time.sleep(self.job_delay)
        began = time.monotonic()
        try:
            res, exc = job["engine"].run(job["block"], job["inputs"], job["meta"]), None
        except Exception as e:  # noqa: BLE001 - whatever went wrong on the engine is the step's failure
            res, exc = None, e
        return res, exc, int((time.monotonic() - began) * 1000)

    def job_finish(self, job: dict, res: dict | None, exc: Exception | None, ms: int) -> None:
        """Write what the engine answered, in one transaction: its outputs are checked, the record moves if the step
        moves it, the step is done or failed, and the run goes on. An answer that comes after its work was stopped,
        or after the step was failed as cut short and run again, changes nothing: the track says it came."""
        hub = self.hub
        run_id, sid = job["run"], job["step"]

        def write():
            run = self.run(run_id)
            st = (run.get("steps") or {}).get(sid) or {}
            wf, _ = hub.workflow(run["workflow_id"], run["version"])
            step = wf.step(sid)
            actor = self.automation_actor(run)
            block = job["block"]
            late = {"step": sid, "block": block.id, "engine": job["engine"].name,
                    "outcome": "failed" if exc else "done", **({"error": str(exc)} if exc else {}),
                    **({"engine_ref": res["ref"]} if isinstance(res, dict) and res.get("ref") else {})}
            if st.get("state") != "running" or st.get("job") != job["token"]:
                hub.track.append(actor, "step.late", f"step:{sid}", run_id=run_id, cause=job["cause"],
                                 data={**late, "why": "superseded"})
                return
            times = {**{k: st[k] for k in ("queued_at", "started_at", "engine", "attempt") if k in st},
                     "done_at": utcnow(), "ms": ms}
            if run["status"] != "running":  # its work was stopped while it ran: nothing follows from its answer
                run["steps"][sid] = {"state": "failed" if exc else "done", "late": True, **times,
                                     **({"error": str(exc)} if exc else {"by": actor["id"]})}
                self._save(run)
                hub.track.append(actor, "step.late", f"step:{sid}", run_id=run_id, cause=job["cause"],
                                 data={**late, "why": "cancelled"})
                return
            failed, output, extra = exc, {}, {}
            if failed is None:
                try:
                    with hub.db:  # a savepoint: outputs that do not fit, or a move refused, leave nothing behind
                        output, extra = self._block_result(run, block, job["engine"], res or {})
                        if step.state:
                            self._move_record(run, step, actor, job["cause"])
                except Exception as e:  # noqa: BLE001
                    failed = e
            if failed is not None:
                seq = self._step_failed(run, wf, step, actor, failed, job["cause"], times)
            else:
                seq = self._step_done(run_id, step, actor, output, job["params"], {**extra, "ms": ms,
                                                                                   "attempt": st.get("attempt")},
                                      job["cause"], times)
            self.advance(run_id, seq)
        hub.db.atomic(write)

    def run_jobs(self, lane: str | None = None, limit: int | None = None) -> int:
        """Run what is in the queue (of ``lane``), one job after another, until it is empty: a worker's round, and
        what a test or ``practicehub tick`` calls. Returns how many jobs it ran."""
        n = 0
        while limit is None or n < limit:
            job = None
            for run_id, sid in self.jobs(lane):
                job = self.job_start(run_id, sid)
                if job:
                    break
            if not job:
                return n
            self.job_finish(job, *self.job_call(job))
            n += 1
        return n

    def recover_jobs(self, startup: bool = False) -> int:
        """Jobs left "running" by a worker that is gone: one whose worker was a process on this machine that no
        longer lives (``startup``), or that has run past twice its engine's time limit. A job on an engine fails,
        saying it was cut short: whether it finished there is not known, and it is never run again by itself (a
        person checks the engine, then retries the step). Python in the sandbox does nothing outside itself, so it
        goes back in the queue, ``JOB_ATTEMPTS`` times at most. Returns how many it found."""
        hub = self.hub
        host, limit, found = socket.gethostname(), 2 * engine_timeout() + JOB_MARGIN, 0
        now = datetime.now(timezone.utc)
        for row in hub.db.runs.find({"status": "running", "jobs": {"$ne": None}}, fields=["id", "steps"]):
            for sid, st in (row.get("steps") or {}).items():
                if not isinstance(st, dict) or st.get("state") != "running":
                    continue
                w_host, _, w_pid = str(st.get("worker") or "").rpartition(":")
                gone = startup and w_host == host and w_pid.isdigit() and int(w_pid) != os.getpid() \
                    and not _alive(int(w_pid))
                try:
                    late = (now - _parse(st["started_at"])).total_seconds() > limit
                except (KeyError, TypeError, ValueError):
                    late = True
                if gone or late:
                    found += 1
                    hub.db.atomic(self._cut_short, row["id"], sid, st.get("job"))
        return found

    def _cut_short(self, run_id: str, sid: str, token) -> None:
        hub = self.hub
        run = self.run(run_id)
        st = (run.get("steps") or {}).get(sid) or {}
        if run["status"] != "running" or st.get("state") != "running" or st.get("job") != token:
            return
        wf, _ = hub.workflow(run["workflow_id"], run["version"])
        step = wf.step(sid)
        actor = self.automation_actor(run)
        attempt = int(st.get("attempt") or 1)
        if st.get("engine") == "local" and attempt < JOB_ATTEMPTS:
            seq = hub.track.append(actor, "step.requeued", f"step:{sid}", run_id=run_id, cause=st.get("cause"),
                                   data={"step": sid, "attempt": attempt + 1,
                                         "why": "the worker running it stopped before it finished"})
            run["steps"][sid] = {"state": "queued", "queued_at": utcnow(), "engine": "local",
                                 "attempt": attempt + 1, "cause": seq}
            self._save(run)
            hub.wake_jobs()
            return
        why = ("it was cut short and did not finish" if st.get("engine") == "local" else
               "it was cut short: whether it finished on its engine is not known. Check there, then try it again")
        seq = self._step_failed(run, wf, step, actor, RuntimeError(why), st.get("cause"),
                                {**{k: st[k] for k in ("queued_at", "started_at", "engine", "attempt") if k in st},
                                 "interrupted": True})
        self.advance(run_id, seq)

    def _advance(self, run_id: str, cause: int | None) -> None:
        hub = self.hub
        progressed = True
        while progressed:
            progressed = False
            run = self.run(run_id)
            if run["status"] != "running":
                return
            wf, _ = hub.workflow(run["workflow_id"], run["version"])
            if self._moves_due(run, wf, cause):
                run = self.run(run_id)
                if run["status"] != "running":
                    return
            for step in wf.steps:
                st = run["steps"][step.id]
                if st["state"] == "waiting":
                    if self._wait_over(run, step, cause):
                        progressed = True
                        break
                    continue
                if st["state"] != "pending":
                    continue
                if not all(run["steps"][a]["state"] in ("done", "skipped") for a in step.after):
                    continue
                progressed = True
                anchors = step.after if step.needs is None else step.needs
                if anchors and all(run["steps"][a]["state"] == "skipped" for a in anchors):
                    holds = False  # a step after only skipped steps is skipped too (on an edge: after its work)
                else:
                    holds = expr.holds(step.when, self._live_ctx(run))
                if not holds:
                    st.update(state="skipped")
                    self._save(run)
                    hub.track.append(self.automation_actor(run), "step.skipped", f"step:{step.id}", run_id=run_id,
                                     cause=cause, data={"step": step.id, "when": step.when})
                    break
                if step.kind == "wait":
                    st.update(state="waiting", **({"each": True} if step.for_each else {}))
                    self._save(run)
                    self._hist_note(run, step, done=False)
                    hub.track.append(self.automation_actor(run), "step.waiting", f"step:{step.id}", run_id=run_id,
                                     cause=cause, data={"step": step.id, "until": step.until,
                                                        **({"for_each": step.for_each} if step.for_each else {}),
                                                        **_timing(step)})
                    if not self._wait_over(run, step, cause):
                        if step.role:
                            self._notice(run, step, f"Waiting: {step.title}", role=step.role, cause=cause)
                        elif not step.for_each and not getattr(step, "timeout", None) \
                                and not run["steps"][step.id].get("wake_at") \
                                and run["steps"][step.id].get("state") == "waiting":
                            run["steps"][step.id]["rests"] = True  # no one watches it, and no time ends it
                            self._save(run)
                    break
                if step.kind == "automation":
                    actor = self.automation_actor(run)
                    if self._in_background(step):  # a block: a worker runs it, outside this request
                        self._queue_job(run, wf, step, actor, cause)
                        break
                    live = self._live_ctx(run)
                    params = wfmod.render(step.params, live) if not step.for_each else {}
                    extra: dict = {}
                    try:
                        with hub.db:  # a savepoint: an automation that fails halfway leaves nothing behind
                            if step.block:
                                output, extra = self._run_block(run, step, params)
                            elif not step.action:  # a step that only moves the record, or chooses the way on
                                output = {"edge": next((e for e, c in step.route if expr.holds(c, live)), None)} \
                                    if step.route else {}
                            else:
                                fn = CORE_ACTIONS.get(step.action) or (hub.pack.actions.get(step.action)
                                                                       if hub.pack else None)
                                if fn is None:
                                    raise ValueError(f"there is no action {step.action!r}")
                                if step.for_each:
                                    output, params = self._for_each(run, step, fn, actor, live)
                                else:
                                    output = fn(self, run, step, params, actor) or {}
                            if step.state:
                                self._move_record(run, step, actor, cause)
                    except Exception as exc:  # an automation that fails stops the run and asks a person
                        self._step_failed(run, wf, step, actor, exc, cause)
                        progressed = True  # steps that do not wait on this one go on: an urgent call is not held
                        break
                    cause = self._step_done(run_id, step, actor, output, params, extra, cause)
                else:
                    seq = hub.track.append(self.automation_actor(run), "step.activated", f"step:{step.id}",
                                           run_id=run_id, cause=cause,
                                           data={"step": step.id, "kind": step.kind, "role": step.role})
                    if step.for_each:  # once per person: a child item each
                        people = self._people_of(run, step)
                        if not people:
                            run["ctx"][step.id] = {"each": [], "by": None}
                            st.update(state="done", by=self.automation_actor(run)["id"])
                            self._save(run)
                            hub.track.append(self.automation_actor(run), "step.done", f"step:{step.id}",
                                             run_id=run_id, cause=seq, data={"step": step.id, "each": []})
                            break
                        st.update(state="active", people=people, cause=seq)
                    else:  # placed on the run's item, or a child of it, once every step ready now is known
                        st.update(state="active", cause=seq)
                    self._save(run)
                break
        run = self.run(run_id)
        if run["status"] == "running" and all(s["state"] in ("done", "skipped") for s in run["steps"].values()):
            run["status"] = "completed"
            self._save(run)
            seq = hub.track.append(self.automation_actor(run), "run.completed", run["record"] or "", run_id=run_id,
                                   cause=cause)
            self._abandon(run, seq, stranded_only=True)
            self._late_finish(run, cause)
            self._re_enter(run, seq)
        if run["status"] == "running":
            self._place(run_id, cause)
        elif run["status"] == "completed" and run.get("item"):
            # done: the item shows the last step a person did (or the run's last wait), and who did it
            row = self.hub.db.items.get(run["item"]) or {}
            hist = [h for h in row.get("history") or [] if h.get("item") == run["item"]]
            last = next((h for h in reversed(hist) if h.get("kind") in ("task", "gate")), None) or \
                next((h for h in reversed(hist) if h.get("kind") == "wait"), None)
            fields = {"status": "done", "waiting": "", "updated_at": utcnow()}
            if last:
                fields.update(step_id=last["step"], step_title=last["title"], kind=last["kind"],
                              claimed_by=last.get("by") if last["kind"] != "wait" else None)
            self.hub.db.items.update({"_id": run["item"], "status": {"$in": ["waiting", "open"]}}, {"$set": fields})

    def _wait_over(self, run, step, cause) -> bool:
        """Finish a waiting step if what it waits for now holds (for every item of its for_each). A wait that cannot
        be checked fails the step and asks a person, like a failed automation; it never fails the change that
        prompted the check."""
        try:
            with self.hub.db:
                return self._check_wait(run, step, cause)
        except Exception as exc:  # noqa: BLE001
            run = self.run(run["id"])
            run["steps"][step.id] = {"state": "failed", "error": str(exc)}
            self._save(run)
            self._hist_close(run, step.id, outcome="failed")
            seq = self.hub.track.append(self.automation_actor(run), "step.failed", f"step:{step.id}",
                                        run_id=run["id"], cause=cause, data={"step": step.id, "until": step.until,
                                                                             "error": str(exc)})
            self.alert(run, step, f"{step.title} failed: {exc}", role=self.hub.practice.get("escalation_role"),
                       cause=seq, priority="urgent")
            return False

    def _check_wait(self, run, step, cause) -> bool:
        live = self._live_ctx(run)
        timer = self._wait_timer(run, step, live)
        if timer is not None:
            if self.instant() < _parse(timer):
                return False
            return self._end_wait(run, step, cause, {"waited_for": [], "woke_at": timer}, "step.done")
        waited = None
        if step.for_each:
            items = wfmod.render(step.for_each, live) or []
            if not isinstance(items, list):
                items = [items]
            waited = []
            for item in items:
                each = self._ref_of(item)
                each_record = None
                if isinstance(each, str) and "/" in each:
                    each_record = self.hub.records.read(*each.split("/", 1))
                if not expr.holds(step.until, {**live, "each": each, "each_record": each_record or {},
                                               "each_closed": self._closed(each, each_record)}):
                    return self._timed_out(run, step, cause)
                waited.append(each)
        elif not expr.holds(step.until, live):
            return self._timed_out(run, step, cause)
        # when it ended, on the practice's clock (as record times are written): a later step can say how long
        # before a booked time something happened (a cancellation's notice)
        out = {"waited_for": waited or [], "ended_at": self.now().isoformat(timespec="seconds")}
        if getattr(step, "timeout", None):
            out["timed_out"] = False
        return self._end_wait(run, step, cause, out, "step.done")

    def _closed(self, ref, rec) -> bool:
        """A record in a state its lifecycle has no move out of (a sub-record done with), or gone."""
        if not isinstance(ref, str) or "/" not in ref:
            return False
        if not rec:
            return True
        rt = self.hub.record_type(ref.split("/", 1)[0])
        lc = rt.lifecycle if rt else None
        if not lc:
            return False
        at = schema.first(rec, lc["path"])
        return not any(schema.leaves(lc, t, at) for t in lc.get("transition", []))

    def _end_wait(self, run, step, cause, output: dict, action: str) -> bool:
        run["ctx"][step.id] = output
        run["steps"][step.id] = {"state": "done", "by": self.automation_actor(run)["id"]}
        self._save(run)
        self._hist_close(run, step.id, outcome="timed out" if output.get("timed_out") else "done",
                         by=self.automation_actor(run)["id"],
                         **({"timed_out": bool(output["timed_out"])} if "timed_out" in output else {}))
        self.hub.db.items.update({"run_id": run["id"], "step_id": step.id, "kind": "notice", "status": "open"},
                                 {"$set": {"status": "done", "updated_at": utcnow()}}, many=True)  # no longer waiting
        self.hub.track.append(self.automation_actor(run), action, f"step:{step.id}", run_id=run["id"],
                              cause=cause, data={"step": step.id, "until": step.until, **_timing(step), **output})
        return True

    def _timed_out(self, run, step, cause) -> bool:
        """A wait on ``until`` whose ``timeout`` has passed ends anyway, as timed out: later steps branch on
        ``<step>.timed_out``."""
        if not getattr(step, "timeout", None):
            return False
        st = run["steps"][step.id]
        if not st.get("timeout_at"):
            st["timeout_at"] = clock.stamp(self.instant() + timedelta(seconds=wfmod.duration_seconds(step.timeout)),
                                           self.zone())
            self._save(run)
        if self.instant() < _parse(st["timeout_at"]):
            return False
        return self._end_wait(run, step, cause, {"waited_for": [], "timed_out": True}, "step.timed_out")

    def _wait_timer(self, run, step, live) -> str | None:
        """When a wait on time (``for`` or ``at``) ends, as a track time; worked out once, when the wait starts, and
        kept on the step (``wake_at``). None for a wait on ``until``."""
        if not (getattr(step, "wait_for", None) or getattr(step, "at", None)):
            return None
        st = run["steps"][step.id]
        if st.get("wake_at"):
            return st["wake_at"]
        tz = self.zone()
        if step.wait_for:
            when = self.instant() + timedelta(seconds=wfmod.duration_seconds(step.wait_for))
        else:
            m = wfmod.AT.fullmatch(step.at)
            value = wfmod.render(m.group(1), live)
            when = _moment(value, tz)
            if when is None:
                raise ValueError(f"it waits until {m.group(1).strip()}, which is {value!r}: not a date or a time")
            if m.group(2):
                secs = wfmod.duration_seconds(m.group(3))
                when += timedelta(seconds=secs if m.group(2) == "+" else -secs)
        st["wake_at"] = clock.stamp(when, tz)
        self._save(run)
        return st["wake_at"]

    # ------------------------------------------------------------------ run deadlines (service levels)
    def _deadline_at(self, wf) -> str:
        return clock.stamp(self.instant() + timedelta(seconds=wfmod.duration_seconds(wf.deadline)), self.zone())

    def _late_finish(self, run: dict, cause) -> None:
        """A run that finished after its deadline before a tick noticed still counts as late."""
        row = self.hub.db.runs.get(run["id"]) or {}
        if row.get("deadline_at") and not row.get("deadline_missed_at") \
                and self.instant() >= _parse(row["deadline_at"]):
            self.hub.db.runs.set(run["id"], {"deadline_missed_at": clock.stamp(self.instant(), self.zone())})
            self.hub.track.append(self.automation_actor(run), "run.deadline_missed", run.get("record") or "",
                                  run_id=run["id"], cause=cause,
                                  data={"deadline_at": row["deadline_at"], "finished": True})

    def _missed_deadlines(self, now_utc: datetime) -> list[str]:
        """Runs still open past their deadline: flagged once, on the track and with a notice to the deadline's
        role (else the practice's escalation role), and marked ``deadline_missed_at``."""
        hub, tz = self.hub, self.zone()
        out = []
        for row in hub.db.runs.find({"status": "running", "deadline_at": {"$ne": None, "$lte": clock.stamp(now_utc, tz)},
                                     "deadline_missed_at": None}, sort=[("deadline_at", 1)]):
            run = _plain(row)
            try:
                wf, _ = hub.workflow(run["workflow_id"], run["version"])
            except Exception:  # noqa: BLE001
                wf = None
            with hub.db:
                hub.db.runs.set(run["id"], {"deadline_missed_at": clock.stamp(now_utc, tz)})
                seq = hub.track.append(self.automation_actor(run), "run.deadline_missed", run.get("record") or "",
                                       run_id=run["id"], cause=run.get("root_event"),
                                       data={"deadline_at": run["deadline_at"]})
                text = (f"Not done in time: {run['title']}, started {clock.shown(run['created_at'], tz)}, was to be "
                        f"done by {clock.shown(run['deadline_at'], tz)}")
                self.alert(run, None, text, role=getattr(wf, "deadline_escalate_to", None), cause=seq)
            out.append(run["id"])
        return out

    def recheck_waits(self, cause: int | None, ref: str | None = None) -> None:
        """Something changed: let every run that is waiting see whether what it waits for now holds. ``ref``: the
        record that changed; only the runs whose waits can see it are rechecked (``waits_on``: that record, or "*");
        the tick rechecks every waiting run (time, and changes made outside)."""
        q = {"status": "running", **_WAITING}
        if ref:
            q["waits_on"] = {"$in": [ref, "*"]}
        rows = self.hub.db.runs.find(q, fields=["id"])
        for r in rows:
            if r["id"] in self._advancing:
                continue  # that run is advancing now and checks its waits itself
            try:
                with self.hub.db:  # a savepoint: one run that cannot go on never fails the write that woke it
                    self.advance(r["id"], cause)
            except Exception as exc:  # noqa: BLE001
                self.hub.track.append({"id": "engine", "kind": "automation"}, "run.stuck", r["id"], cause=cause,
                                      data={"error": str(exc)})

    def outcome_follows(self, run: dict, step, outcome: str) -> list:
        """What deciding ``step`` with ``outcome`` sets going: the steps whose condition reads a decision and would
        hold were it made (each step that copies the decision, ``{{<step>.outcome}}``, given it too). What a decision
        dialog says follows it; the run itself is not touched."""
        wf = self.hub.workflow(run["workflow_id"], run["version"])[0]
        tag = "{{" + step.id + ".outcome"
        given = {step.id: {"outcome": outcome}}
        for s in wf.steps:  # a step that gathers decisions ("reviewed": {{review.outcome}}{{review-flagged.outcome}})
            if s.kind == "automation" and tag in json.dumps(s.params, default=str):
                given[s.id] = {"outcome": outcome}
        live = {**self._live_ctx(run), **given}
        out = []
        for s in wf.steps:
            if s.id in given or not s.when or ".outcome" not in s.when:
                continue
            if not any(g in s.when for g in given):
                continue
            try:
                if expr.holds(s.when, live):
                    out.append(s)
            except Exception:  # noqa: BLE001 - a condition over what is not there yet: it does not follow (yet)
                continue
        # written as states and edges: where the record lands, then the work waiting for it there
        for s in [s for s in out if s.on_edge and s.state]:
            out += [w for w in wf.steps if w.at_state == s.state and w.kind != "automation" and w not in out
                    and (not w.when or self._holds_quietly(w.when, live))]
        return out

    def assignments_needed(self, run: dict, step, outcome: str) -> list[dict]:
        """The people's work that ``step`` ended with ``outcome`` sets going, each to be given to one person by whoever
        ends it: in this run (the steps ``outcome_follows`` names) and in the runs its automations start
        (``workflow.start``: the work waiting in the new run's first state). Each is ``{key, title, workflow, role,
        people}``: ``key`` ("<workflow>/<step>") is what the choice is sent under, ``people`` the active people who
        hold the role. Work that names its own person (``assign_to``), is done once per person (``for_each``), that
        agents of its role may take, or that nobody holds the role for is not asked about: it goes where it goes."""
        hub = self.hub
        wf = hub.workflow(run["workflow_id"], run["version"])[0]
        follows = self.outcome_follows(run, step, outcome)
        live = {**self._live_ctx(run), step.id: {"outcome": outcome}}
        out, seen = [], set()

        def need(wid, wtitle, s):
            n = self.assignable(wid, wtitle, s)
            if n and n["key"] not in seen:
                seen.add(n["key"])
                out.append(n)

        for s in follows:
            if s.kind in ("task", "gate"):
                need(wf.id, wf.title, s)
            elif s.kind == "automation" and s.action == "workflow.start":
                try:
                    params = wfmod.render(s.params, live) or {}
                    child = hub.workflow(str(params.get("workflow") or ""))[0]
                except Exception:  # noqa: BLE001 - a workflow not there (yet): nothing to ask about
                    continue
                first = next((x for x in child.steps if x.on_edge == "start" and x.state), None)
                if first is None:
                    continue
                ctx = {"input": params.get("input") or {}, "run": {"record": run.get("record")}}
                for w in child.steps:
                    if w.at_state == first.state and (not w.when or self._holds_quietly(w.when, ctx)):
                        need(child.id, child.title, w)
        return out

    def assignable(self, wid: str, wtitle: str, s) -> dict | None:
        """People's work at step ``s`` of workflow ``wid`` as one to be given to a person where it is set going (see
        ``assignments_needed``), else None."""
        if s.kind not in ("task", "gate") or not s.role or s.assign_to or s.for_each:
            return None
        holders = self.hub.active_holders(s.role)
        if not holders or (s.agents_may and any(m["kind"] != "person" for m in holders)):
            return None
        people = [m for m in holders if m["kind"] == "person"]
        if not people:
            return None
        return {"key": f"{wid}/{s.id}", "title": s.title, "workflow": wtitle, "role": s.role, "people": people}

    def holding(self, picks: dict):
        """While a transition is made: ``picks`` (checked) are who its new work goes to (``_route``)."""
        import contextlib

        @contextlib.contextmanager
        def held():
            saved, self._picks = self._picks, dict(picks or {})
            try:
                yield
            finally:
                self._picks = saved
        return held()

    @staticmethod
    def pick_problems(needed: list[dict], assign: dict | None, *, required: bool = False) -> dict:
        """``assign`` checked against ``needed``: the picks by key, or Invalid saying what is missing or wrong."""
        from .hub import Invalid
        if assign is not None and not isinstance(assign, dict):
            raise Invalid("assign is who does each piece of work it sets going: {\"<workflow>/<step>\": \"<member>\"}")
        picks, problems = {}, []
        for n in needed:
            who = str((assign or {}).get(n["key"]) or "").strip()
            if not who:
                if required:
                    problems.append(f"choose who does “{n['title']}”")
                continue
            if who not in {m["id"] for m in n["people"]}:
                problems.append(f"“{n['title']}” is done by someone who holds {n['role']}: {who} does not")
                continue
            picks[n["key"]] = who
        if problems:
            raise Invalid("; ".join(problems), problems)
        return picks

    def check_picks(self, run: dict, step, outcome: str, assign: dict, *, required: bool = False) -> dict:
        """The people chosen for the work ending ``step`` with ``outcome`` sets going, checked: each one of the people
        ``assignments_needed`` offers for it (and, ``required``, every one chosen). Returns them by key."""
        return self.pick_problems(self.assignments_needed(run, step, outcome), assign, required=required)

    @staticmethod
    def _holds_quietly(cond, ctx) -> bool:
        try:
            return bool(expr.holds(cond, ctx))
        except Exception:  # noqa: BLE001 - over what is not there yet
            return False

    def _live_ctx(self, run) -> dict:
        """The run's context plus ``record``: its record as it is now. Read fresh each time and never stored, so a
        step sees what earlier steps (and people) wrote."""
        rec = None
        if run.get("record") and "/" in str(run["record"]):
            rec = self.hub.records.read(*str(run["record"]).split("/", 1))
        return {**run["ctx"], "record": rec or {}}

    @staticmethod
    def _ref_of(item):
        return item.get("reference") if isinstance(item, dict) and "reference" in item else item

    def _for_each(self, run, step, fn, actor, live):
        items = wfmod.render(step.for_each, live)
        if items is None or items == "":
            items = []
        if isinstance(items, (str, dict)) and self._ref_of(items):  # one reference: a list of one
            items = [items]
        if not isinstance(items, list):
            raise ValueError(f"for_each: {step.for_each} is not a list")
        outputs, passed_over, rendered = [], [], []
        for item in items:
            each = self._ref_of(item)
            each_record = None
            if isinstance(each, str) and "/" in each:
                each_record = self.hub.records.read(*each.split("/", 1))
                if each_record is None:
                    raise ValueError(f"for_each: {each} does not exist")
            ctx = {**live, "each": each, "each_record": each_record or {}}
            if step.where and not expr.holds(step.where, ctx):
                passed_over.append(each)
                continue
            params = wfmod.render(step.params, ctx)
            rendered.append(params)
            outputs.append(fn(self, run, step, params, actor) or {})
        return {"each": outputs, "passed_over": passed_over}, rendered

    def cancel(self, actor: dict, run_id: str, reason: str) -> None:
        from .hub import Denied
        from .hub import Invalid
        run = self.run(run_id)
        why = self.cancel_refusal(actor, run)
        if why:
            raise Denied(why)
        with self.hub.db:
            run = self.run(run_id)
            if run["status"] != "running":
                raise Invalid(f"only running work can be cancelled; this is {run['status']}")
            run["status"] = "cancelled"
            self._save(run)
            self.hub.db.items.update({"run_id": run_id, "status": {"$in": ["open", "claimed", "held", "waiting"]}},
                                     {"$set": {"status": "cancelled", "updated_at": utcnow()}}, many=True)
            seq = self.hub.track.append(actor, "run.cancelled", run["record"] or "", run_id=run_id,
                                        data={"reason": reason})
            self._on_cancel(run, actor, reason, seq)
            self._abandon(run, seq)

    def cancel_refusal(self, actor: dict, run: dict) -> str | None:
        """Why ``actor`` may not cancel ``run``, in words, or None. A policy owner may; else whoever started it,
        until a step for one of the workflow's ``cancel_by`` roles is claimed: from then on only someone holding
        one of the roles whose step was claimed (a renewal a physician took up is the physician's to cancel)."""
        if self.hub.is_policy_owner(actor):
            return None
        try:
            wf = self.hub.workflow(run["workflow_id"], run["version"])[0]
        except Exception:  # noqa: BLE001 - a run whose workflow cannot be read: the plain rule
            wf = None
        guarded = set(getattr(wf, "cancel_by", None) or [])
        taken: list[str] = []
        if guarded:
            for ev in self.hub.db.events.find({"run_id": run["id"], "action": "item.claimed"}, sort=[("seq", 1)]):
                try:
                    role = wf.step((ev.get("data") or {}).get("step")).role
                except KeyError:
                    continue
                if role in guarded and role not in taken:
                    taken.append(role)
        if taken:
            if set(taken) & set(actor.get("roles") or []):
                return None
            names = " or ".join(taken)
            return f"its work was taken up by the {names} role: only that role or a policy owner may cancel it now"
        if run["started_by"] != actor["id"]:
            return "only whoever started it or a policy owner may cancel a run"
        return None

    def _on_cancel(self, run: dict, actor: dict, reason: str, cause: int) -> None:
        """The workflow's ``[[on_cancel]]`` actions, in the cancel's own transaction: one that fails refuses the
        cancel (``Invalid``), so a cancel never leaves the people it tells untold."""
        from .hub import Invalid
        try:
            wf = self.hub.workflow(run["workflow_id"], run["version"])[0]
        except Exception:  # noqa: BLE001
            return
        if not getattr(wf, "on_cancel", None):
            return
        hub = self.hub
        me = self.automation_actor(run)
        live = {**self._live_ctx(run), "cancel": {"reason": reason, "by": actor.get("name") or actor.get("id")}}
        for step in wf.on_cancel:
            if step.when and not expr.holds(step.when, live):
                continue
            fn = CORE_ACTIONS.get(step.action) or (hub.pack.actions.get(step.action) if hub.pack else None)
            params = wfmod.render(step.params, live)
            try:
                if fn is None:
                    raise ValueError(f"there is no action {step.action!r}")
                output = fn(self, run, step, params, me) or {}
            except Exception as exc:  # noqa: BLE001 - said in words, and nothing of the cancel is kept
                raise Invalid(f"it cannot be cancelled now: '{step.title}' did not go through ({exc})") from None
            hub.track.append(me, "action.ran", f"step:{step.id}", run_id=run["id"], cause=cause,
                             data={"step": step.id, "action": step.action, "params": params, "output": output,
                                   "on_cancel": True})

    def retry(self, actor: dict, run_id: str, step_id: str, reason: str = "") -> None:
        """Run a failed step again (after its module, block or the record it needed was fixed). Policy owners only:
        a retry acts with the workflow's authority, not the retrier's."""
        from .hub import Denied, Invalid
        if not self.hub.is_policy_owner(actor):
            raise Denied("only a policy owner may retry a failed step")
        with self.hub.db:
            run = self.run(run_id)
            if run["status"] != "running":
                raise Invalid(f"only running work can be retried; this is {run['status']}")
            st = run["steps"].get(step_id)
            if not st or st["state"] != "failed":
                raise Invalid(f"step {step_id!r} has not failed")
            if st.get("failed_over"):
                raise Invalid(f"step {step_id!r} failed and the record went on without it: it is not run again")
            run["steps"][step_id] = {"state": "pending", **({"attempt": st["attempt"]} if st.get("attempt") else {})}
            self._save(run)
            self.hub.db.items.update({"run_id": run_id, "step_id": step_id, "kind": "notice", "status": "open"},
                                     {"$set": {"status": "done", "updated_at": utcnow()}}, many=True)
            seq = self.hub.track.append(actor, "step.retried", f"step:{step_id}", run_id=run_id,
                                        data={"step": step_id, "error": st.get("error"), "reason": reason})
            self.advance(run_id, seq)  # in the same transaction: a worker's answer never lands halfway through it

    def _abandon(self, run: dict, cause: int, *, stranded_only: bool = False) -> None:
        """A run about a record was cancelled: move the record where its lifecycle says abandoned work goes
        (``abandoned``, a move of its own that needs no transition written for it; a state named as its own target
        stays where it is), unless other work on it is still open. ``stranded_only``: the run finished, on a way that
        never moved its record on (a request a nurse dealt with by phone): only a state that workflows alone leave is
        closed so."""
        ref = run.get("record") or ""
        if "/" not in ref:
            return
        hub = self.hub
        rt = hub.record_type(ref.split("/", 1)[0])
        lc = rt.lifecycle if rt else None
        if not lc or not lc.get("abandoned"):
            return
        if any(r["id"] != run["id"] for r in self.runs(record=ref, status="running")):
            return
        actor = self.automation_actor(run)
        rec = hub.records.read(*ref.split("/", 1))
        if rec is None:
            return
        if stranded_only and self._walked_again(run, rt, rec):
            return  # not stranded: its own workflow walks the next pass (``_re_enter``)
        st = schema.first(rec, lc["path"])
        to = lc["abandoned"].get(st)
        if to and stranded_only:
            exits = [t for t in lc["transition"] if st in t["from"]]
            to = to if exits and all(t["by"] == "workflow" for t in exits) else None
        if to and to != st:
            hub.update_record(actor, ref, schema.put(rec, lc["path"], to), cause=cause, run_id=run["id"])

    # ------------------------------------------------------------------ blocks
    def block_of(self, run: dict, step):
        """The block a step uses, at the version pinned when the run started."""
        if not step.block:
            return None
        version = ((run["ctx"].get("run") or {}).get("blocks") or {}).get(step.block)
        return self.hub.block(step.block, version)[0]

    def _block_inputs(self, block, step, params: dict) -> dict:
        try:
            return coerce(block.inputs, params)
        except FieldError as exc:
            raise ValueError(f"the inputs for block {block.id!r} have problems: {'; '.join(exc.problems)}") from None

    def _run_block(self, run: dict, step, params: dict) -> tuple[dict, dict]:
        """Run a python or external block: check its inputs, run it on its engine, check its outputs."""
        block, engine, inputs, meta = self._block_call(run, step, params)
        return self._block_result(run, block, engine, engine.run(block, inputs, meta))

    def _block_call(self, run: dict, step, params: dict) -> tuple:
        """What a block is run with: the block, its engine, its inputs (checked) and what the engine is told of the
        call. Nothing is run."""
        block = self.block_of(run, step)
        inputs = self._block_inputs(block, step, params)
        return block, backend_for(block), inputs, {"run": run["id"], "step": step.id,
                                                   "version": (run["ctx"]["run"].get("blocks") or {}).get(block.id)}

    def _block_result(self, run: dict, block, engine, res: dict) -> tuple[dict, dict]:
        """What a block's engine answered, as the step's output (checked) and what the track keeps of the call."""
        try:
            output = coerce(block.outputs, res.get("output") or {})
        except FieldError as exc:
            raise ValueError(f"block {block.id!r} answered with problems: {'; '.join(exc.problems)}") from None
        extra = {"block": block.id, "block_version": (run["ctx"]["run"].get("blocks") or {}).get(block.id),
                 "runtime": block.runtime, "engine": engine.name}
        if block.code:
            extra["code_sha256"] = block.code_sha256
        if res.get("log"):
            extra["log"] = res["log"]
        if res.get("ref"):
            extra["engine_ref"] = res["ref"]
        return output, extra

    def capability_of(self, run: dict, step) -> str | None:
        """What an agent's profile must list to do this step: its skill, or its prompt block's capability."""
        if step.block:
            return self.block_of(run, step).capability
        return step.skill

    def _skill(self, run: dict, step):
        """The pack skill behind a step (directly, or extended by its prompt block)."""
        name = step.skill
        if step.block:
            name = self.block_of(run, step).extends
        if not name:
            return None
        version = ((run["ctx"].get("run") or {}).get("skills") or {}).get(name) if run else None
        return self.hub.skill(name, version)

    # ------------------------------------------------------------------ items
    # One item per run walks its workflow. It is made when the run starts (when the workflow has any step a person
    # does or waits on) and moves from step to step: offered to the step's role or person, then, while automations
    # run or a wait waits, shown as waiting and in nobody's to-do list. Steps people work at the same time, and a
    # step done once per person (``for_each``), get child items under it, each with its own key, which close back
    # into it. The item's ``history`` says who did which step, and when.
    STEP_KINDS = ("task", "gate", "wait")

    def open_duplicate(self, wf, record: str | None, values: dict, subject: str | None = None) -> dict | None:
        """The open run of ``wf`` that already has the value its ``trigger.one_open_per`` names, if any."""
        key = getattr(wf, "one_open_per", None)
        if not key:
            return None
        if key == "record":
            path, value = "record", record
        elif key == "subject":  # who the record is about
            path, value = "subject", subject
        else:
            path, value = f"ctx.input.{key[6:]}", values.get(key[6:])
        if isinstance(value, dict):
            value = value.get("reference") or None
        if value in (None, "", []):
            return None
        flt = {"workflow_id": wf.id, "status": "running", path: value}
        within = getattr(wf, "one_open_within", None)
        if within:  # only the same submission twice: a run started a moment ago
            since = datetime.now(timezone.utc) - timedelta(seconds=wfmod.duration_seconds(within))  # as created_at
            flt["created_at"] = {"$gte": since.strftime("%Y-%m-%dT%H:%M:%S")}
        for name in getattr(wf, "one_open_same", None) or []:
            v = values.get(name[6:])
            flt[f"ctx.input.{name[6:]}"] = v.get("reference") if isinstance(v, dict) and "reference" in v else v
        for row in self.hub.db.runs.find(flt, sort="created_at"):
            ref = row.get("record") or ""
            if wf.one_open_while and ref != value and "/" in ref:
                # only while its record is still where the condition says (a request not yet booked)
                rec = self.hub.records.read(*ref.split("/", 1))
                if not rec or not expr.holds(wf.one_open_while, {"record": rec}):
                    continue
            return _plain(row)
        return None

    def _refuse_duplicate(self, wf, record, values, *, by_hand: bool, confirmed: bool,
                          subject: str | None = None) -> None:
        """``trigger.one_open_per``: a run started by hand while another open one has the same value is refused,
        unless the trigger allows another and the starter confirmed it. An event or a schedule always starts its run
        (a request that came in is never dropped)."""
        from .hub import Duplicate
        if not by_hand:
            return
        other = self.open_duplicate(wf, record, values, subject)
        if other is None or (confirmed and wf.allow_duplicate):
            return
        raise Duplicate(f"{wf.title} is already open for this", other, wf.allow_duplicate)

    def _run_item(self, run_id: str, wf, title: str, cause: int) -> str | None:
        if not any(s.kind in self.STEP_KINDS for s in wf.steps):
            return None  # automations only: nothing for anyone to see waiting
        item_id = _new_id("item")
        self.hub.db.items.insert(_item_doc(self.hub, id=item_id, run_id=run_id, kind="wait", title=title,
                                           status="waiting", cause=cause, persons_only=0))
        return item_id

    def _route(self, run: dict, step, person: str | None = None) -> dict:
        """Who a step's work goes to, and by when: role, assignee, persons_only, due_at, priority."""
        due_at = None
        if step.due:
            secs = wfmod.duration_seconds(step.due)
            tz = self.zone()  # counted in real time, from the instant: a due time across a clock change is not off
            due_at = clock.stamp(clock.utc(self.now(), tz) + timedelta(seconds=secs), tz)
        assignee = None
        who = person
        if who is None and not step.assign_to:  # chosen by whoever made the move that set it going, used once
            key = f"{run['workflow_id']}/{step.id}"
            kept = run.get("picks") or {}
            who = self._picks.pop(key, None) or kept.get(key)
            if key in kept:
                kept.pop(key)
                self.hub.db.runs.set(run["id"], {"picks": kept})
        if who is None and step.assign_to:  # offered to that member only if they hold the step's role and are active
            who = wfmod.render(step.assign_to, self._live_ctx(run))
            who = (who.get("reference") if isinstance(who, dict) else who) or ""
            who = str(who).split("/")[-1]
        if who:
            try:
                m = self.hub.member(who)
            except Exception:  # noqa: BLE001
                m = None
            if m and m["status"] == "active" and step.role in m["roles"]:
                assignee = m["id"]
        role, persons_only = step.role, bool(step.persons_only)
        if run.get("record") and step.role and self.hub.restrictions.of_subject(
                self.hub.restrictions.subject_of_ref(run["record"]) or ""):
            role, persons_only, assignee = self._directive_route(run, step, assignee)
        return {"role": role, "assignee": assignee, "persons_only": int(persons_only), "due_at": due_at,
                "priority": step.priority}

    @staticmethod
    def _entry(step, route: dict, item_id: str, **more) -> dict:
        """One step in an item's history."""
        return {"step": step.id, "title": step.title, "kind": step.kind, "role": route.get("role"),
                "assignee": route.get("assignee"), "started_at": utcnow(), "taken_at": None, "done_at": None,
                "by": None, "outcome": None, "due_at": route.get("due_at"), "escalated": 0, "item": item_id, **more}

    def _new_item(self, run: dict, step, cause: int, *, parent: dict, person: str | None = None) -> str:
        """A child item of the run's item (``parent``): a step people work beside another, or once per person."""
        item_id = _new_id("item")
        route = self._route(run, step, person)
        self.hub.db.items.insert(_item_doc(
            self.hub, id=item_id, run_id=run["id"], step_id=step.id, kind=step.kind,
            title=run["title"], step_title=step.title, status="open", cause=cause,
            parent_id=parent["id"], step_started_at=utcnow(),
            history=[self._entry(step, route, item_id)], **route))
        self.hub.db.items.update({"_id": parent["id"]}, {"$push": {"children": item_id, "wave": item_id},
                                                         "$set": {"updated_at": utcnow()}})
        if not route["assignee"] and route["role"] and not self.hub.active_holders(
                route["role"], persons_only=bool(route["persons_only"])):
            self._nobody_holds(run, step, item_id, cause)
        else:
            self._nobody_may_take(run, step, item_id, cause)
        self.hub.wake_agents()
        return item_id

    def _move(self, run: dict, parent: dict, step, cause) -> None:
        """The run's item moves on to ``step``."""
        hub = self.hub
        route = self._route(run, step)
        parent = self.item(parent["id"])
        kids = [c["_id"] for c in hub.db.items.find({"_id": {"$in": parent.get("wave") or []},
                                                     "status": {"$in": list(OPEN_WORK)}}, fields=["_id"])]
        hub.db.items.set(parent["id"], {
            "step_id": step.id, "kind": step.kind, "step_title": step.title, "status": "open", "claimed_by": None,
            "note": "", "cause": cause, "escalated": 0, "escalate_next": None, "step_started_at": utcnow(),
            "waiting": "", "wave": kids, "history": (parent.get("history") or []) + [self._entry(step, route, parent["id"])],
            "updated_at": utcnow(), **route})
        if not route["assignee"] and route["role"] and not hub.active_holders(
                route["role"], persons_only=bool(route["persons_only"])):
            self._nobody_holds(run, step, parent["id"], cause)
        else:
            self._nobody_may_take(run, step, parent["id"], cause)

    def _people_of(self, run: dict, step) -> list[str]:
        """The members a step done once per person is for: its ``for_each``, as member ids."""
        got = wfmod.render(step.for_each, self._live_ctx(run))
        if got is None or got == "":
            got = []
        if not isinstance(got, list):
            got = [got]
        out = []
        for g in got:
            g = self._ref_of(g)
            g = str(g or "").split("/")[-1]
            if g and g not in out:
                out.append(g)
        return out

    def _place(self, run_id: str, cause) -> None:
        """Put the steps that became active on the run's item: one alone moves the item on; steps at the same time
        (or once per person) become child items; with nothing for a person, the item shows what it waits on."""
        hub = self.hub
        run = self.run(run_id)
        if not run.get("item"):
            return
        parent = self.item(run["item"])
        wf, _ = hub.workflow(run["workflow_id"], run["version"])
        free = parent["status"] == "waiting"
        ready = [s for s in wf.steps if run["steps"][s.id]["state"] == "active" and not run["steps"][s.id].get("item")]
        single = [s for s in ready if not s.for_each]
        for s in ready:
            st = run["steps"][s.id]
            at = st.pop("cause", None) or cause
            if s.for_each:
                st.update(item=parent["id"], each=[self._new_item(run, s, at, parent=parent, person=p)
                                                   for p in st.pop("people", [])])
            elif free and len(single) == 1:
                self._move(run, parent, s, at)
                st["item"] = parent["id"]
                free = False
            else:
                st["item"] = self._new_item(run, s, at, parent=parent)
        if ready:
            self._save(run)
            hub.wake_agents()
        if free:
            self._show_waiting(run, wf, parent["id"])

    def _show_waiting(self, run: dict, wf, item_id: str) -> None:
        """The run's item between people's steps: what it waits on, in words (its child items, a wait's time, a
        step that stopped)."""
        hub = self.hub
        parent = self.item(item_id)
        if parent["status"] != "waiting":
            return
        wave = parent.get("wave") or []
        still = hub.db.items.count({"_id": {"$in": wave}, "status": {"$in": list(OPEN_WORK)}}) if wave else 0
        words = [f"waiting on {still} of {len(wave)}"] if still else []
        waits = [s for s in wf.steps if run["steps"][s.id]["state"] == "waiting"]
        tz = self.zone()
        for s in waits:
            wake = run["steps"][s.id].get("wake_at")
            words.append(f"waiting until {clock.shown(wake, tz)}" if wake else f"waiting: {s.title}")
        words += [f"stopped: {s.title} failed" for s in wf.steps if run["steps"][s.id]["state"] == "failed"]
        words += [f"under way: {s.title}" for s in wf.steps if run["steps"][s.id]["state"] == "running"]
        words += [f"in the queue: {s.title}" for s in wf.steps if run["steps"][s.id]["state"] == "queued"]
        step = waits[0] if waits else None
        hub.db.items.set(item_id, {"waiting": "; ".join(words), "kind": "wait", "step_id": step.id if step else "",
                                   "step_title": step.title if step else "", "role": None, "assignee": None,
                                   "claimed_by": None, "due_at": None, "escalated": 0, "escalate_next": None,
                                   "persons_only": 0, "resting": bool(_waits(run)["resting"]),
                                   "updated_at": utcnow()})

    def _hist_note(self, run: dict, step, *, done: bool = True, outcome: str | None = None,
                   by: str | None = None) -> None:
        """An automation or a wait in the run's item's history."""
        if not run.get("item"):
            return
        now = utcnow()
        entry = self._entry(step, {"role": step.role}, run["item"], done_at=now if done else None, by=by,
                            outcome=outcome)
        self.hub.db.items.update({"_id": run["item"]}, {"$push": {"history": entry}})

    def _hist_update(self, item_id: str, step_id: str, **fields) -> dict | None:
        """Update the last open entry of a step in an item's history; the entry as it is now."""
        row = self.hub.db.items.get(item_id)
        if not row:
            return None
        hist = list(row.get("history") or [])
        for e in reversed(hist):
            if e.get("step") == step_id and not e.get("done_at"):
                e.update(fields)
                self.hub.db.items.set(item_id, {"history": hist})
                return e
        return None

    def _hist_close(self, run: dict, step_id: str, **fields) -> None:
        if run.get("item"):
            self._hist_update(run["item"], step_id, done_at=utcnow(), **fields)

    def _did(self, run: dict, step_id: str, mid: str) -> bool:
        """Whether this member did that step of the run: by its items' step history (any child too), or the run's
        own record of the step's answer."""
        if (run["ctx"].get(step_id) or {}).get("by") == mid:
            return True
        for row in self.hub.db.items.find({"run_id": run["id"], "kind": {"$nin": ["notice", "hold"]}},
                                          fields=["history"]):
            if any(e.get("step") == step_id and e.get("by") == mid for e in row.get("history") or []):
                return True
        return self._did_before(run, step_id, mid)

    def _did_before(self, run: dict, step_id: str, mid: str) -> bool:
        """A record's earlier passes (``run.previous``: a CAPA reopened, a change order sent back) are one piece of
        work: whoever did the step in any pass before this one is kept off a ``not_by`` naming it, all the way back
        along the chain of passes, not only in the last pass that did it."""
        seen = {run["id"]}
        while True:
            prev = ((run.get("ctx") or {}).get("run") or {}).get("previous")
            if not prev or prev in seen or len(seen) > 200:
                return False
            seen.add(prev)
            run = self.hub.db.runs.get(prev)
            if run is None:
                return False
            if (run["ctx"].get(step_id) or {}).get("by") == mid:
                return True
            for row in self.hub.db.items.find({"run_id": run["id"], "kind": {"$nin": ["notice", "hold"]}},
                                              fields=["history"]):
                if any(e.get("step") == step_id and e.get("by") == mid for e in row.get("history") or []):
                    return True

    @staticmethod
    def what(item: dict) -> str:
        """What an item is about, in words: the step it is at, else its title."""
        return item.get("step_title") or item.get("title") or ""

    def _nobody_holds(self, run: dict, step, item_id: str, cause: int) -> None:
        """Work for a role nobody switched on holds is never left to wait unseen: it is raised with the step's
        escalation role, or, when nobody holds that either, with each policy owner on duty."""
        hub = self.hub
        what = "Urgent: " if step.priority == "urgent" else ""
        text = f"{what}nobody on duty holds the role {step.role}: \u201c{step.title}\u201d is waiting ({item_id})"
        seq = hub.track.append({"id": "hub", "kind": "automation", "name": "Practice Hub"}, "item.unstaffed",
                               f"item:{item_id}", cause=cause,
                               data={"role": step.role, "escalated_to": step.escalate_to, "priority": step.priority})
        self.alert(run, step, text, role=step.escalate_to, cause=seq)

    def blocked_holders(self, item: dict) -> list[tuple[dict, str]] | None:
        """When people on duty hold an open task's or decision's role but none of them may take it (separation of
        duties: each wrote the record, or did an earlier step), each of them with why; None when someone may (or
        the role is held by nobody: ``_nobody_holds`` raises that)."""
        if item.get("kind") not in ("task", "gate") or not item.get("role") or item.get("status") not in OPEN_WORK:
            return None
        holders = self.hub.active_holders(item["role"], persons_only=bool(item.get("persons_only")))
        free = {**item, "status": "open", "claimed_by": None, "assignee": None}
        out = []
        for m in holders:
            ok, why = self.eligibility(m, free)
            if ok:
                return None
            out.append((m, why))
        return out or None

    def _nobody_may_take(self, run: dict, step, item_id: str, cause: int) -> None:
        """Work whose role is held, but by nobody who may take it (each holder is kept out by separation of duties),
        never waits unseen: each policy owner on duty is told by name, with who is kept out and why, and
        ``item.blocked`` goes on the track. Someone must give the role to another person."""
        if step.kind not in ("task", "gate"):
            return
        item = self.hub.db.items.get(item_id)
        blocked = self.blocked_holders(_plain(item)) if item else None
        if not blocked:
            return
        hub = self.hub
        who = "; ".join(f"{m['name']} ({why})" for m, why in blocked)
        seq = hub.track.append({"id": "hub", "kind": "automation", "name": "Practice Hub"}, "item.blocked",
                               f"item:{item_id}", run_id=run["id"], cause=cause,
                               data={"role": step.role, "kept_out": [{"member": m["id"], "why": why}
                                                                     for m, why in blocked]})
        text = (f"Nobody may take \u201c{step.title}\u201d ({item_id}): everyone holding the role {step.role} is "
                f"kept out: {who}. Give the role to someone else.")
        owners = [m["id"] for m in hub.members("person") if m.get("status") == "active" and hub.is_policy_owner(m)]
        for mid in owners:
            self._notice(run, step, text, assignee=mid, cause=seq, priority="urgent", about=f"item:{item_id}")
        if not owners:
            self.alert(run, step, text, role=step.escalate_to, cause=seq, priority="urgent")

    def _notice(self, run: dict, step, text: str, *, role: str | None = None, assignee: str | None = None,
                cause: int | None = None, priority: str | None = None, about: str | None = None,
                escalates: str | None = None) -> str:
        """A notice. One that ``escalates`` an overdue item is the one row for that item and that recipient: told
        again, the open notice takes the new words and counts the repeat (``repeats``) instead of a new row."""
        prio = priority or (getattr(step, "priority", None) if step else None) or "normal"
        prio = prio if prio in ("normal", "urgent") else "normal"
        if escalates:
            prior = self.hub.db.items.find_one({"kind": "notice", "escalates": escalates, "role": role,
                                                "assignee": assignee, "status": {"$in": ["open", "claimed"]}})
            if prior:  # told twice for one cause (one tick's two passes) counts once
                again = 0 if cause is not None and prior.get("cause") == cause else 1
                self.hub.db.items.set(prior["_id"], {
                    "title": text, "cause": cause, "repeats": int(prior.get("repeats") or 1) + again,
                    "priority": "urgent" if "urgent" in (prio, prior.get("priority")) else "normal",
                    "updated_at": utcnow()})
                return prior["_id"]
        item_id = _new_id("item")
        more = {"escalates": escalates, "repeats": 1} if escalates else {}
        self.hub.db.items.insert(_item_doc(
            self.hub, id=item_id, run_id=run["id"], step_id=step.id if step else "", kind="notice", title=text,
            role=role, assignee=assignee, persons_only=1, status="open", cause=cause,
            priority=prio, output={"about": about} if about else None, **more))
        return item_id

    def _staffed(self, role: str | None, besides=None) -> list[str]:
        """The people on duty who hold ``role``, leaving out ``besides`` (one member, or several)."""
        out = _besides(besides)
        return [m["id"] for m in (self.hub.active_holders(role, persons_only=True) if role else [])
                if m["id"] not in out]

    def alert(self, run: dict, step, text: str, *, role: str | None = None, assignee: str | None = None,
              cause: int | None = None, priority: str | None = None, about: str | None = None,
              besides: str | None = None, owners_too: bool = False, escalates: str | None = None) -> list[str]:
        """A notice someone on duty will see: the person named if they are active, else the role named if a person
        on duty holds it, else the practice's escalation role, else each policy owner on duty. Never a notice to
        nobody. ``besides`` (whoever is late with the work) is never the only one told: when they are the role's
        only holder on duty, each other person holding it is told by name, else the next in line is; when nobody
        else is on duty at all, they are told themselves (by name), and with nobody on duty the notice waits on the
        role. ``escalates`` (an overdue item) keeps one notice row per item and recipient."""
        hub = self.hub
        out: list[str] = []
        late = _besides(besides)
        notice = functools.partial(self._notice, escalates=escalates)
        if assignee and assignee not in late:
            m = hub.db.members.find_one({"_id": assignee}, fields=["status", "kind"])
            if m and m["status"] == "active" and m["kind"] == "person":
                return [notice(run, step, text, assignee=assignee, cause=cause, priority=priority, about=about)]
        for r in (role, hub.practice.get("escalation_role")):
            others = self._staffed(r, besides)
            if not others:
                continue
            if late & set(self._holders(r)):  # the late one holds it too: tell the others by name
                out = [notice(run, step, text, assignee=o, cause=cause, priority=priority, about=about)
                       for o in others]
            else:
                out = [notice(run, step, text, role=r, cause=cause, priority=priority, about=about)]
            break
        told = set(out)
        if out and not owners_too:
            return out
        owners = [m for m in hub.members("person") if m["status"] == "active" and hub.is_policy_owner(m)
                  and m["id"] not in late]
        named = {r.get("assignee") for r in hub.db.items.find({"_id": {"$in": sorted(told)}}, fields=["assignee"])
                 } if told else set()
        out = out + [notice(run, step, text, assignee=m["id"], cause=cause, priority=priority, about=about)
                     for m in owners if m["id"] not in named]
        if out:
            return out
        # nobody else on duty: the late themselves, by name, rather than nobody; with no one on duty at all, the
        # notice waits on the role for whoever comes on duty first
        here = [m for m in sorted(late) if self._active_person(m)]
        if here:
            return [notice(run, step, text, assignee=m, cause=cause, priority=priority, about=about) for m in here]
        waits_on = role or hub.practice.get("escalation_role")
        return [notice(run, step, text, role=waits_on, cause=cause, priority=priority, about=about)] if waits_on else []

    def _active_person(self, mid: str) -> bool:
        m = self.hub.db.members.find_one({"_id": mid}, fields=["status", "kind"])
        return bool(m and m["status"] == "active" and m["kind"] == "person")

    def item(self, item_id: str) -> dict:
        row = self.hub.db.items.get(item_id)
        if not row:
            from .hub import NotFound
            raise NotFound(f"there is no work item {item_id}")
        row = _plain(row)
        row.setdefault("output", None)
        return row

    def _open_items(self) -> list[dict]:
        """Every open or claimed item, in queue order."""
        rows = self.hub.db.items.find({"status": {"$in": ["open", "claimed"]}})
        rows.sort(key=_queue_key)
        return rows

    def eligibility(self, member: dict, item: dict) -> tuple[bool, str]:
        """Whether this member may take this item, and if not, why (in words a person can act on)."""
        hub = self.hub
        if member.get("status") != "active":
            return False, f"{member['name']} is not active"
        if item["status"] == "waiting":
            return False, item.get("waiting") or "it is waiting for the workflow to go on"
        if item["status"] not in ("open", "claimed"):
            return False, "this item is finished"
        if item["status"] == "claimed" and item["claimed_by"] != member["id"]:
            who = hub.db.members.get(item["claimed_by"]) or {}
            return False, f"{who.get('name') or 'someone else'} is working on it"
        if item["kind"] == "notice":
            ok = item["assignee"] == member["id"] or (item["role"] and member["id"] in self._holders(item["role"]))
            return (True, "") if ok else (False, "this notice is for someone else")
        if item["kind"] == "hold":
            if member["kind"] != "person":
                return False, "only a person approves an agent's high-risk work"
            if item["assignee"] == member["id"] or (not item["assignee"] and item["role"]
                                                     and member["id"] in self._holders(item["role"])):
                return True, ""
            return False, "this approval is for the agent's sponsor"
        is_agent = member["kind"] != "person"
        if item["assignee"] and item["assignee"] != member["id"]:
            return False, "it is assigned to someone else"
        run = self.run(item["run_id"])
        wf, _ = hub.workflow(run["workflow_id"], run["version"])
        step = wf.step(item["step_id"])
        # overdue work is escalated to the step's escalate_to role: a person holding it may take the work itself
        # (every other rule below still holds: the data it reads, who did what, the directive, signing)
        escalated_to_them = (bool(item.get("escalated")) and not is_agent
                             and getattr(step, "escalate_to", None) in member["roles"])
        if not item["assignee"] and item["role"] not in member["roles"] and not escalated_to_them:
            return False, f"it needs the {item['role']} role"
        for prior in step.not_by:
            if prior == "run":
                if run["started_by"] == member["id"]:
                    return False, "whoever started this does not also do this step"
            elif prior == "record.editors":
                moved_in = (run["steps"].get(step.id) or {}).get("moved_in")
                if run["record"] and member["id"] in hub.revision_authors(run["record"], before=moved_in):
                    return False, "whoever wrote or changed this record does not also approve it"
            elif self._did(run, prior, member["id"]):  # who did which step: the run's step history
                return False, f"whoever did '{wf.step(prior).title}' does not also do this"
        for c in step.context:
            if c.startswith("record:") and not hub.can(member, "reader", f"data_class:{c[7:]}"):
                return False, f"it needs to read {c[7:]} records"
        # an earlier step's whole output carries the data classes that step read (a draft note is clinical); naming
        # one field of it (``edit.diagnosis_codes``) is a deliberate disclosure, written in the approved workflow
        from .visibility import step_classes
        ids = {s.id for s in wf.steps}
        for ref in [c for c in step.context if c in ids] + ([step.review_of] if step.review_of else []):
            if ref in ids and ref != step.id:
                for dc in sorted(step_classes(wf, wf.step(ref))):
                    if not hub.can(member, "reader", f"data_class:{dc}"):
                        return False, f"it reads '{wf.step(ref).title}', which holds {dc} data"
        if (not is_agent and run["record"]
                and hub.restrictions.withholds_from(member, run["record"], reaching=True)):
            # the work shows the file's content: a member a directive keeps from it takes it only in an emergency
            return False, (f"what it is about is withheld from you at the {hub.subject_word()}'s request: use "
                           f"emergency access on the file first, or leave it to someone the directive allows")
        if is_agent:
            if member["kind"] == "service":
                return False, "services do not take work items"
            if run["record"] and hub.restrictions.withholds_from(member, run["record"], reaching=True):
                # the subject's directive gives an agent nothing of what it withholds: the work's inputs copy it
                return False, (f"what it is about is withheld from agents at the {hub.subject_word()}'s request: "
                               f"a person must do this")
            sponsor = hub.member(member["sponsor"]) if member["sponsor"] else None
            if not sponsor or sponsor["status"] != "active":
                return False, "its sponsor is not active"
            if item["persons_only"] or step.persons_only:
                return False, "this item is for people"
            if step.kind == "gate" and step.signer == "person":
                return False, "only a person may sign this"
            if step.kind == "gate":
                if not step.agent_criteria:
                    return False, "the workflow gives agents no criteria for this decision"
                if not expr.holds(step.agent_criteria, run["ctx"]):
                    return False, f"outside the criteria agents may decide within ({step.agent_criteria})"
            if RISK_ORDER[step.risk] > RISK_ORDER[member["profile"].get("max_risk", "low")]:
                return False, f"its risk ({step.risk}) is above what {member['name']} may take on"
            capability = self.capability_of(run, step)
            if not capability:
                return False, "the step has no skill for agents"
            if capability not in member["profile"].get("skills", []):
                return False, f"{capability} is not one of {member['name']}'s skills"
        return True, ""

    def _holders(self, role: str) -> list[str]:
        return [s.split(":", 1)[1] for (_, _, s) in self.hub.authz.read(obj=f"role:{role}", relation="assignee")]

    def inbox(self, member: dict, *, include_ineligible: bool = False) -> list[dict]:
        """Open items this member may take or is working on (and, optionally, role items it may not take, with why)."""
        rows = self._open_items()
        out = []
        for r in rows:
            item = self.item(r["id"])
            if item["kind"] != "notice" and not item["assignee"] and item["role"] not in member["roles"]:
                continue
            ok, why = self.eligibility(member, item)
            if ok or include_ineligible:
                if item["kind"] == "notice" and not ok:
                    continue
                run = self.run(item["run_id"])
                item.update(eligible=ok, reason=why, run_title=self.hub.run_title(member, run), record=run["record"])
                out.append(item)
        return out

    def queue(self, member: dict | None = None) -> list[dict]:
        """Every open item, for the practice board (run titles name the record only for a member who may read it)."""
        out = []
        for r in self._open_items():
            item = self.item(r["id"])
            run = self.run(item["run_id"])
            item.update(run_title=self.hub.run_title(member, run), record=run["record"])
            out.append(item)
        return out

    def _require_eligible(self, member: dict, item: dict) -> None:
        from .hub import Denied
        ok, why = self.eligibility(member, item)
        if not ok:
            self.hub.track.append(member, "item.refused", f"item:{item['id']}", run_id=item["run_id"],
                                  cause=item["cause"], data={"reason": why})
            raise Denied(f"{member['name']} may not take '{self.what(item)}': {why}")

    def claim(self, member: dict, item_id: str) -> dict:
        from .hub import Invalid
        with self.hub.db:
            item = self.item(item_id)
            self._require_eligible(member, item)
            if item["status"] == "claimed":
                return item
            if item["kind"] == "notice":
                raise Invalid("a notice is acknowledged, not claimed")
            self.hub.db.items.set(item_id, {"status": "claimed", "claimed_by": member["id"], "updated_at": utcnow()})
            entry = next((e for e in reversed(item.get("history") or []) if e.get("step") == item["step_id"]
                          and not e.get("done_at")), None)
            if entry is not None and not entry.get("taken_at"):
                self._hist_update(item_id, item["step_id"], taken_at=utcnow())
            self.hub.track.append(member, "item.claimed", f"item:{item_id}", run_id=item["run_id"], cause=item["cause"],
                                  data={"step": item["step_id"]})
        return self.item(item_id)

    def release(self, member: dict, item_id: str, note: str = "", *, escalate: bool = False) -> dict:
        """Give an item back. ``escalate`` (what an agent does when it cannot do the work well) hands it to people."""
        from .hub import Denied
        from .hub import Invalid
        item = self.item(item_id)
        if item["status"] != "claimed":
            raise Invalid("only an item someone is working on can be given back")
        if item["claimed_by"] != member["id"]:
            raise Denied("only whoever is working on an item gives it back")
        persons_only = 1 if (escalate or item["persons_only"]) else 0
        role, assignee = item["role"], item["assignee"]
        if persons_only and member["kind"] == "agent":
            role, assignee = self._to_people(item)
        self.hub.db.items.set(item_id, {"status": "open", "claimed_by": None, "persons_only": persons_only, "note": note,
                                        "role": role, "assignee": assignee, "updated_at": utcnow()})
        self.hub.track.append(member, "item.escalated" if escalate else "item.released", f"item:{item_id}",
                              run_id=item["run_id"], cause=item["cause"], data={"note": note, "step": item["step_id"]})
        return self.item(item_id)

    def holder(self, item: dict) -> str | None:
        """Who has this work in hand: whoever claimed it, else the one it is assigned to."""
        if item["status"] == "claimed":
            return item["claimed_by"]
        return item["assignee"] if item["status"] == "open" else None

    def may_reassign(self, member: dict, item: dict) -> bool:
        """A person holding the step's escalation role or the practice's, or a policy owner, may move work that is in
        someone else's hands (claimed, or assigned to them) to another person or back to the queue, at any time: a
        holiday is known before the work is late."""
        if item["kind"] in ("notice", "hold") or item["status"] not in ("open", "claimed"):
            return False
        held = self.holder(item)
        if not held or held == member["id"]:
            return False
        if member.get("kind") != "person" or member.get("status") != "active":
            return False
        _, _, step = self.step_of(item)
        roles = {r for r in (getattr(step, "escalate_to", None), self.hub.practice.get("escalation_role")) if r}
        return bool(roles & set(member["roles"])) or self.hub.is_policy_owner(member)

    def may_take_over(self, member: dict, item: dict) -> bool:
        """Overdue work in someone else's hands (claimed by them, or assigned to them and waiting) can be taken back
        by whoever the overdue notice went to: a person holding the step's escalation role or the practice's, or a
        policy owner."""
        return bool(item["escalated"]) and self.may_reassign(member, item)

    def reassign_options(self, item: dict) -> list[dict]:
        """The active people who could do this step, but for the one who has it now."""
        held = self.holder(item)
        out = []
        for m in self.hub.members("person"):
            if m["id"] == held or m.get("status") != "active":
                continue
            if self.eligibility(m, {**item, "status": "open", "claimed_by": None, "assignee": None})[0]:
                out.append(m)
        return out

    def take_over(self, member: dict, item_id: str, note: str = "", to: str | None = None) -> dict:
        """Take work off whoever has it. ``to`` names the person it goes to (who must be able to do the step), or
        "queue" for anyone who can; without it, overdue work is claimed by this member when they may do it, else it
        goes back to the queue (a manager reassigns clinical work, never does it)."""
        from .hub import Denied, Invalid
        if self.item(item_id)["kind"] == "notice":
            return self.take_over_from_notice(member, item_id, note)
        with self.hub.db:
            item = self.item(item_id)
            if to:
                if not self.may_reassign(member, item):
                    raise Denied(f"{member['name']} may not reassign this: the escalation role or a policy owner does")
            elif not self.may_take_over(member, item):
                raise Denied(f"{member['name']} may not take this over: only overdue work, by the escalation role or a"
                             " policy owner")
            if not (note or "").strip():
                raise Invalid("say why the work is moved: the one who had it will see it")
            before = self.holder(item)
            free = {**item, "status": "open", "claimed_by": None, "assignee": None}
            if to and to != "queue":
                who = self.hub.member(to)
                ok, why = self.eligibility(who, free) if who and who["kind"] == "person" else (False, "not a person")
                if not ok:
                    raise Invalid(f"{(who or {}).get('name', to)} cannot do this step: {why}")
                status, claimed, assignee = "open", None, to
            elif to == "queue":
                status, claimed, assignee = "open", None, None
            else:
                ok, _ = self.eligibility(member, free)
                status, claimed, assignee = ("claimed", member["id"], member["id"]) if ok else ("open", None, None)
            self.hub.db.items.set(item_id, {"status": status, "claimed_by": claimed, "assignee": assignee, "note": note,
                                            "updated_at": utcnow()})
            self.hub.track.append(member, "item.taken_over", f"item:{item_id}", run_id=item["run_id"],
                                  cause=item["cause"], data={"from": before, "to": claimed or assignee,
                                                             "claimed": bool(claimed), "note": note})
            if before and before != member["id"]:
                who = self.hub.member(before)
                if who and who["kind"] == "person":
                    run, _, step = self.step_of(item)
                    self.alert(run, step, f"{member['name']} moved \"{self.what(item)}\" off you: {note}",
                               assignee=before, cause=None)
        return self.item(item_id)

    def escalated_item(self, member: dict, notice: dict) -> dict | None:
        """The overdue item an escalation notice is about, when this member may take it over from the notice: it is
        still open, and they may claim it (it waits on a queue they can take from) or take it off whoever has it."""
        iid = notice.get("escalates") if notice.get("kind") == "notice" else None
        if not iid or notice["status"] not in ("open", "claimed") or not self.eligibility(member, notice)[0]:
            return None
        item = self.hub.db.items.get(iid)
        if not item or item["status"] not in ("open", "claimed"):
            return None
        item = self.item(iid)
        if self.holder(item) == member["id"]:
            return None
        free = {**item, "status": "open", "claimed_by": None, "assignee": None}
        if self.holder(item) is None:
            return item if self.eligibility(member, item)[0] else None
        return item if self.may_take_over(member, item) and self.eligibility(member, free)[0] else None

    def take_over_from_notice(self, member: dict, notice_id: str, note: str = "") -> dict:
        """Take it over, from the overdue notice: the item it is about is claimed by this member (taken off whoever
        had it, who is told why), and the notice is acknowledged. Returns the item taken."""
        from .hub import Denied
        notice = self.item(notice_id)
        item = self.escalated_item(member, notice)
        if item is None:
            raise Denied(f"{member['name']} may not take this over: the work is done, someone else has it, or it "
                         "needs what you do not hold")
        if self.holder(item) is None:
            taken = self.claim(member, item["id"])
        else:
            taken = self.take_over(member, item["id"], (note or "").strip() or f"Overdue: {member['name']} took it over")
        self.acknowledge(member, notice_id)
        return taken

    def _directive_route(self, run: dict, step, assignee: str | None) -> tuple[str, bool, str | None]:
        """Where work on a file with a subject's directive goes: (role, persons_only, assignee). An agent the
        directive withholds the file from never gets it (its inputs copy the file), so the item goes to people:
        the step's people_role when a person holds it, else its own role. And when some people who hold the role
        are excluded by the directive and exactly one who holds it is allowed, it is offered to that one first."""
        hub = self.hub
        rec = run["record"]
        role, persons_only = step.role, bool(step.persons_only)
        if not persons_only and any(m["kind"] != "person" and hub.restrictions.withholds_from(m, rec, reaching=True)
                                    for m in hub.active_holders(step.role)):
            persons_only = True
            people_role = getattr(step, "people_role", None)
            if people_role and hub.active_holders(people_role, persons_only=True):
                role = people_role
        if not assignee:
            people = hub.active_holders(role, persons_only=True)
            allowed = [m for m in people if not hub.restrictions.withholds_from(m, rec, reaching=True)]
            if len(allowed) == 1 and len(allowed) < len(people):
                assignee = allowed[0]["id"]
        return role, persons_only, assignee

    def _to_people(self, item: dict) -> tuple[str | None, str | None]:
        """Where an item goes when it is handed from agents to people: (role, assignee). Never to a role no person
        holds, where it would wait for good."""
        run, wf, step = self.step_of(item)
        # a run a record's type started (``type:<id>``) or a schedule started has no member as its starter
        starter = self.hub.member(run["started_by"]) if run.get("started_by") and \
            self.hub.db.members.get(run["started_by"]) else None
        starter = starter if starter and starter["kind"] == "person" and starter["status"] == "active" else None
        first = getattr(step, "people_role", None) or item["role"]
        if item["assignee"]:
            who = self.hub.member(item["assignee"])
            if who and who["kind"] == "person" and who["status"] == "active":
                return first, item["assignee"]
        # people_role, then the step's own role, then the practice's escalation role: the first a person holds
        for role in dict.fromkeys(r for r in (first, item["role"], self.hub.practice.get("escalation_role")) if r):
            if starter and role in starter["roles"]:
                return role, starter["id"]
            if any(m and m["kind"] == "person" and m["status"] == "active"
                   for m in (self.hub.member(x) for x in self._holders(role))):
                return role, None
        if starter:
            return first, starter["id"]
        return self.hub.practice.get("escalation_role") or first, None  # no person anywhere: the practice's problem

    def release_claims_of(self, mid: str, actor: dict, reason: str) -> list[str]:
        """Work a member was doing, or that was theirs alone, goes back to its role, and the role (or whoever covers
        for it) is told: work does not wait unseen on someone who is gone. Their notices are passed on the same way,
        and approvals of an agent's held work move to someone who may approve it."""
        hub = self.hub
        who = hub.db.members.find_one({"_id": mid}, fields=["name"])
        name = who["name"] if who else mid
        released = []
        rows = hub.db.items.find({"status": {"$in": ["open", "claimed"]}, "kind": {"$in": ["task", "gate"]},
                                  "$or": [{"claimed_by": mid}, {"assignee": mid}]}, sort="n")
        for row in rows:
            hub.db.items.set(row["id"], {"status": "open", "claimed_by": None, "assignee": None, "note": reason,
                                         "updated_at": utcnow()})
            seq = hub.track.append(actor, "item.released", f"item:{row['id']}", run_id=row["run_id"],
                                   cause=row["cause"], data={"note": reason, "from": mid})
            run = self.run(row["run_id"])
            wf, _ = hub.workflow(run["workflow_id"], run["version"])
            step = wf.step(row["step_id"])
            self.alert(run, step, f"Back in the queue: \u201c{self.what(row)}\u201d ({name}: {reason})",
                       role=row["role"] if self._staffed(row["role"]) else (step.escalate_to if step else None),
                       cause=seq, priority=row["priority"])
            released.append(row["id"])
        for row in hub.db.items.find({"status": "open", "kind": {"$in": ["notice", "hold"]}, "assignee": mid},
                                     sort="n"):
            self._pass_on(row, actor, f"{name}: {reason}")
        return released

    def _pass_on(self, row: dict, actor: dict, why: str, to: str | None = None) -> None:
        """A notice or approval for someone who cannot act on it now goes to ``to``, or else to whoever covers."""
        hub = self.hub
        if to is None:
            staffed = next((r for r in (hub.practice.get("escalation_role"),) if self._staffed(r)), None)
            owners = [m["id"] for m in hub.members("person") if m["status"] == "active" and hub.is_policy_owner(m)]
            role, to = (staffed, None) if staffed else (None, owners[0] if owners else None)
        else:
            role = None
        hub.db.items.set(row["id"], {"assignee": to, "role": role, "note": why, "updated_at": utcnow()})
        hub.track.append(actor, "item.passed_on", f"item:{row['id']}", run_id=row["run_id"], cause=row["cause"],
                         data={"from": row["assignee"], "to": to, "role": role, "why": why})

    def move_sponsor_work(self, agent_id: str, sponsor: str, actor: dict) -> list[str]:
        """An agent's held work and the notices about it go to its new sponsor."""
        moved = []
        for row in self.hub.db.items.find({"status": "open", "kind": {"$in": ["hold", "notice"]}}, sort="n"):
            out = row.get("output") or {}
            held = (self.hub.db.items.find_one({"_id": out.get("holds")}, fields=["claimed_by"])
                    if row["kind"] == "hold" and out.get("holds") else None)
            if (held and held["claimed_by"] == agent_id) or out.get("about") == agent_id:
                self._pass_on(row, actor, "the agent has a new sponsor", to=sponsor)
                moved.append(row["id"])
        return moved

    def step_of(self, item: dict):
        run = self.run(item["run_id"])
        wf, _ = self.hub.workflow(run["workflow_id"], run["version"])
        return run, wf, wf.step(item["step_id"])

    def fields_for(self, step, run: dict | None = None) -> list[dict]:
        """The answer a task asks for: its own fields, else its block's outputs, else its skill's. A task with
        ``outcomes`` asks first which way it ended (``outcome``)."""
        fields = self._answer_fields(step, run)
        if step.kind == "task" and step.outcomes:
            return [{"name": "outcome", "label": "How it ended", "type": "choice", "required": True, "help": "",
                     "choices": self.open_outcomes(run, step), **self._outcome_words(step)}, *fields]
        return fields

    def open_outcomes(self, run: dict | None, step) -> list[str]:
        """The outcomes a person may choose now: on a workflow written as states and edges, the edges out of the
        record's state whose condition holds (an edge's ``when``), as an issue tracker offers only the transitions
        whose conditions pass."""
        if not step.outcome_when or run is None:
            return list(step.outcomes)
        live = self._live_ctx(run)
        out = []
        for o in step.outcomes:
            cond = step.outcome_when.get(o)
            try:
                if not cond or expr.holds(cond, live):
                    out.append(o)
            except Exception:  # noqa: BLE001 - a condition over what is not there: the edge is not offered
                continue
        return out

    @staticmethod
    def _outcome_words(step) -> dict:
        return {"labels": dict(step.outcome_labels)} if step.outcome_labels else {}

    def _answer_fields(self, step, run: dict | None = None) -> list[dict]:
        if step.fields:
            return step.fields
        if step.block and run is not None:
            block = self.block_of(run, step)
            if block.outputs:
                return block.outputs
        skill = self._skill(run, step) if run is not None else (
            self.hub.skill(step.skill) if step.skill else None)
        return skill.output if skill else []

    def context(self, member: dict, item_id: str) -> dict:
        """What the member sees to do this item: the step, its instructions, the data it reads (only what the
        member's data classes allow), and the shape of the answer."""
        from .hub import Denied
        item = self.item(item_id)
        self._require_eligible(member, item)
        run, wf, step = self.step_of(item)
        hub = self.hub
        if run["record"] and hub.restrictions.withholds_from(member, run["record"], reaching=True):
            # a step's inputs are copies of the file (a transcript, a result's value): never handed to an agent, or
            # to a person, the subject's directive keeps it from (MCP and the API read context through here too)
            hub.track.append(member, "access.denied", f"item:{item_id}", run_id=run["id"],
                             data={"relation": "reader", "what": "read the work's inputs, withheld at the subject's "
                                                                "request"})
            raise Denied(f"what '{self.what(item)}' is about is withheld from {member.get('name') or member['id']} "
                         f"at the {hub.subject_word()}'s request")
        data: dict = {}
        for c in step.context:
            if c.startswith("record:"):
                dc = c[7:]
                recs, seen = [], set()
                # the run's record, and the file of whom it is about (its subject, or the record itself)
                about = run.get("subject") or run["record"]
                for ref in dict.fromkeys(r for r in (run["record"], about) if r):
                    rtype, rid = ref.split("/", 1)
                    main = hub.records.read(rtype, rid)
                    if main and hub.class_or_none(rtype) == dc and ref not in seen:
                        recs.append(main)
                        seen.add(ref)
                if about:
                    for r in hub.records.search(subject=about, limit=None):
                        ref = f"{r['resourceType']}/{r['id']}"
                        if hub.class_or_none(r["resourceType"]) == dc and ref not in seen:
                            recs.append(r)
                            seen.add(ref)
                data[c] = hub.redact_all(member, recs)  # without the fields of a class the member may not read
            elif c.startswith("file:"):
                data[c] = self._file_context(member, run, step, c[5:])
            elif c.startswith("provider:"):
                name = c[9:]
                fn = CORE_PROVIDERS.get(name) or hub.pack.providers.get(name)
                data[c] = fn(self, run, step, member)
            else:
                data[c] = expr.lookup(run["ctx"], c)
        for c, recs in list(data.items()):  # what the member is shown is a view of each record in it
            if c.startswith("record:"):
                for r in recs:
                    if (visibility.withheld(r) or {}).get("whole"):
                        continue  # a stub: nothing of it was seen
                    hub.note_view(member, f"{r['resourceType']}/{r['id']}", how=f"work item {item_id}")
                if any(visibility.withheld(r) for r in recs):  # never silently incomplete, for a person or an agent
                    data["withheld"] = hub.restrictions.note(run["record"]) + (
                        ": what you were given of this file is incomplete; say so in anything you write from it")
        ctx_for_choices = {**run["ctx"], **{k.replace(":", "_"): v for k, v in data.items()}}
        fields = self.fields_for(step, run)
        draft = run["ctx"].get(step.review_of) if step.review_of else None
        skill = self._skill(run, step)
        instructions = step.instructions
        block = self.block_of(run, step)
        if block and block.runtime == "prompt":
            # the injected prompt: author-written values are filled in; values read from the run stay data
            params = wfmod.render(step.params, run["ctx"])
            inputs = self._block_inputs(block, step, params)
            literal = {k for k, v in step.params.items() if not wfmod.TEMPLATE.search(str(v))}
            literal |= {f["name"] for f in block.inputs if f["name"] not in step.params}
            for name, value in inputs.items():
                if name not in literal:
                    data[f"block-input.{name}"] = value
            instructions = "\n\n".join(p for p in (blockmod.render_prompt(block, inputs, literal), step.instructions) if p)
        if run["workflow_id"] == "mention" and run["version"] == 0 and (item.get("output") or {}).get("mention"):
            from .mentions import mentions_of
            data["mention"] = mentions_of(hub).context(member, item["output"]["mention"])
        if step.kind == "gate":
            fields = [{"name": "outcome", "label": "Decision", "type": "choice", "required": True,
                       "choices": self.open_outcomes(run, step), "help": "", **self._outcome_words(step)},
                      {"name": "comment", "label": "Comment", "type": "text", "required": False, "help": ""}]
        return {
            "item": item, "run": {"id": run["id"], "title": self.hub.run_title(member, run), "record": run["record"],
                                  "workflow": wf.id, "version": run["version"],
                                  "today": self.now().date().isoformat(), "time_zone": self.zone().key},
            "step": {"id": step.id, "title": step.title, "kind": step.kind, "risk": step.risk, "role": step.role,
                     "instructions": instructions, "outcomes": self.open_outcomes(run, step),
                     "outcome_labels": dict(step.outcome_labels),
                     "meaning": self.meaning_for(member, run, step),
                     "meanings": {o: self.meaning_for(member, run, step, o) for o in step.outcomes},
                     "signer": step.signer, "skill": skill.id if skill else step.skill, "block": step.block,
                     # the links its record must have before it is done (``requires_links``) that it does not yet
                     "needs": self.hub.missing_links(step, run["record"]) if step.requires_links and run.get("record")
                     else []},
            "skill": {"id": skill.id, "title": skill.title, "instructions": skill.instructions} if skill else None,
            "context": data, "draft": draft, "fields": fields,
            "schema": json_schema(fields, self._choice_ctx(fields, data, run)),
            "choices": {f["name"]: self._choices(f, data, run) for f in fields if f["type"] == "choice" or f.get("choices_from")},
        }

    def _file_context(self, member: dict, run: dict, step, name: str) -> dict | None:
        """``file:<field>``: the file in that attachment field of the run's record, as the member may read it: what
        it is, its words (``filetext.text_of``) and whether it is a picture a model that can see is shown. No bytes:
        an agent fetches those through the record (``Hub.read_file``), on the track. Only a record of a data class
        the step reads (``record:<class>`` beside it): what the step's answer carries stays what the workflow says.
        None: no such file."""
        from . import filetext
        hub, ref = self.hub, run.get("record")
        rtype = (ref or "").split("/", 1)[0]
        rt = hub.record_type(rtype) if ref else None
        f = rt.field(name) if rt else None
        if not f or f["type"] != "attachment" or hub.class_or_none(rtype) not in visibility.step_classes_own(step):
            return None
        rec = hub.redact(member, hub.records.read(*ref.split("/", 1)) or {})
        att = schema.first(rec, f["path"])
        got = hub.db.get_file(att["blob"]) if isinstance(att, dict) and att.get("blob") else None
        if not got:
            return None
        data, meta = got
        ctype = att.get("contentType") or meta.get("contentType") or ""
        hub.note_view(member, ref, how="file")
        return {"ref": ref, "field": name, "blob": att["blob"], "name": att.get("name") or "", "contentType": ctype,
                "size": len(data), "text": filetext.text_of(data, att.get("name") or "", ctype),
                "picture": filetext.picture_type(data, ctype)}

    def meaning_for(self, member: dict, run: dict, step, outcome: str | None = None) -> str:
        """What signing this gate says for this member: a ``meanings_if_did`` meaning when they did that earlier
        step (by the run's step history, so a reassignment or take-over does not change it), else the outcome's
        own meaning, else the step's. An outcome with its own meaning (e.g. discard) says that, whoever signs."""
        if outcome and outcome in step.meanings:
            return step.meanings[outcome]
        for sid, text in (getattr(step, "meanings_if_did", None) or {}).items():
            if self._did(run, sid, member["id"]):
                return text
        return step.meanings.get(outcome, step.meaning) if outcome else step.meaning

    def _choice_ctx(self, fields, data, run):
        ctx = dict(run["ctx"])
        for k, v in data.items():
            ctx[k.replace(":", "_")] = v
        return ctx

    def _choices(self, f, data, run):
        if f.get("choices"):
            return [{"value": c, "label": c} for c in f["choices"]]
        src = expr.lookup(self._choice_ctx([f], data, run), f["choices_from"]) or []
        return [c if isinstance(c, dict) else {"value": c, "label": c} for c in src]

    def _require_links(self, run: dict, step, outcome: str | None = None) -> None:
        """A step's ``requires_links``: the record must have them before the step is done (or, on a gate, before
        the outcomes they are for are decided)."""
        from .hub import Invalid
        if not step.requires_links or not run.get("record"):
            return
        missing = self.hub.missing_links(step, run["record"], outcome)
        if missing:
            rt = self.hub.record_type(run["record"].split("/", 1)[0])
            raise Invalid(f"this step needs the {rt.label.lower() if rt else 'record'} to have: {'; '.join(missing)}", missing)

    def complete(self, member: dict, item_id: str, output: dict, *, assign: dict | None = None,
                 assign_required: bool = False) -> dict:
        """Finish a task with its fields. ``assign``: who does each piece of work it sets going (``check_picks``)."""
        out = dict(output or {})
        with self._picking(item_id, str(out.get("outcome") or ""), assign, assign_required):
            return self._complete(member, item_id, out)

    def _picking(self, item_id: str, outcome: str, assign: dict | None, required: bool):
        """While a transition is made: the people chosen for the work it sets going, checked, for ``_route``."""
        picks = {}
        if assign is not None or required:
            item = self.item(item_id)
            if item["kind"] in ("task", "gate"):
                run, _, step = self.step_of(item)
                picks = self.check_picks(run, step, outcome, assign or {}, required=required)
            else:
                self.pick_problems([], assign)
        return self.holding(picks)

    def _complete(self, member: dict, item_id: str, output: dict) -> dict:
        from .hub import Invalid
        hub = self.hub
        with hub.db:
            item = self.item(item_id)
            if item["kind"] != "task":
                raise Invalid("this item is a decision or a notice, not a task")
            self._require_eligible(member, item)
            run, wf, step = self.step_of(item)
            signed = self._signed_move(run, step)
            if signed:  # the workflow cannot sign for a person: Done here would leave the record where it is
                raise Invalid(f"{step.title} moves the {signed[0]} to {signed[1]}, which is signed {signed[2]!r}: "
                              f"make that move on the record's page, with your signature")
            ctxview = self.context(member, item_id)
            fields = self.fields_for(step, run)
            try:
                values = coerce(fields, output or {}, self._choice_ctx(fields, ctxview["context"], run))
            except FieldError as exc:
                raise Invalid("the answer has problems", exc.problems) from None
            guard = None
            if hub.pack:
                block = self.block_of(run, step)
                owned = []  # the definitions whose part's pack.py would hold this answer's guard
                if block:  # a block's own guard; the extended skill's guard only while its answer has that shape
                    guard = hub.pack.guards.get(f"block:{block.id}")
                    owned.append(("block", block.id))
                    if not guard and not block.outputs and block.extends:
                        guard = hub.pack.guards.get(block.extends)
                        owned.append(("skill", block.extends))
                elif step.skill:
                    guard = hub.pack.guards.get(step.skill)
                    owned.append(("skill", step.skill))
                if not guard:
                    # no guard is loaded: that means "this answer has no hard rule" only while the code that would
                    # hold one is running. Code waiting for review is not run, so its guards fail closed, as its
                    # actions do: the answer is not taken unchecked.
                    why = next((w for w in (hub.code_not_running(k, d) for k, d in owned) if w), None)
                    if why:
                        raise Invalid("the answer cannot be checked now, so it is not taken: the code that checks "
                                      f"this step is not running ({why})")
            if guard:  # it sees what the answerer was shown, and who answers (a person or an agent)
                problems = guard(values, {**ctxview, "by": {"id": member["id"], "kind": member["kind"]}}) or []
                if problems:
                    raise Invalid("the answer does not pass the step's checks", problems)
            refused = self._refused_move(run, step, values, member)
            if refused:  # finished, its move would fail after it: the record would stay where it is
                raise Invalid(f"{step.title} is not done yet: {refused}")
            self._require_links(run, step)
            if item["status"] == "open":  # Done on an open item takes it first, on the record: nobody presses
                # Assign to me only to be allowed to finish
                hub.track.append(member, "item.claimed", f"item:{item_id}", run_id=item["run_id"], cause=item["cause"],
                                 data={"step": item["step_id"], "how": "taken by finishing it"})
            if member["kind"] == "agent" and step.risk == "high":
                return self._hold(member, item, run, step, values, action="item.completed")
            return self._finish(member, item, run, step, values, action="item.completed")

    def shown_digest(self, member: dict, item_id: str) -> str:
        """The SHA-256 of exactly what this member is shown to decide an item: the step's words, the data it reads
        (as this member may see it), the draft under review, or an agent's held answer. A decision signs this hash,
        and is refused if what the member read has changed since (a dose edited, a note rewritten)."""
        import hashlib
        item = self.item(item_id)
        if item["kind"] == "hold":
            shown = {"held": self.item(item["output"]["holds"])["output"], "hold": item["output"].get("action")}
        else:
            ctx = self.context(member, item_id)
            shown = {"step": {k: ctx["step"].get(k) for k in ("id", "title", "instructions", "outcomes", "meaning", "meanings")},
                     "context": ctx["context"], "draft": ctx.get("draft")}
        blob = json.dumps(shown, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def _check_shown(self, member: dict, item_id: str, seen: str | None) -> str:
        from .hub import Invalid
        now = self.shown_digest(member, item_id)
        if seen is not None and seen != now:
            raise Invalid("what you were shown has changed since you opened it; read it again before you decide")
        return now

    def decide(self, member: dict, item_id: str, outcome: str, comment: str = "", *, seen: str | None = None,
               propose: bool = False, assign: dict | None = None, assign_required: bool = False) -> dict:
        """Decide a gate (``_decide``). ``assign``: who does each piece of work the decision sets going."""
        with self._picking(item_id, outcome, assign, assign_required):
            return self._decide(member, item_id, outcome, comment, seen=seen, propose=propose)

    def _decide(self, member: dict, item_id: str, outcome: str, comment: str = "", *, seen: str | None = None,
                propose: bool = False) -> dict:
        """Decide a gate. A person's decision is an electronic signature with the step's meaning, by this signer, over
        the hash of what they were shown (``seen``: the hash the page showed them; refused if it no longer matches).

        Agents never sign, whichever way they call (the API, the agent runner, MCP): the engine itself makes an
        agent's decision one of two things, and neither carries a signature.
        - A *proposal*, when the step is high risk or ``propose`` is set (MCP always sets it): it waits, unapplied,
          for its sponsor, whose approval is the person's signature.
        - An *attestation*, for the routine decisions a workflow lets agents make (``signer = "any"`` within the
          gate's ``agent_criteria``, at low or medium risk): the agent's own recorded action, with the same fields a
          signature would cover, shown as "decided by <agent> (an agent)", never in a record's signatures."""
        from .hub import Invalid
        hub = self.hub
        with hub.db:
            item = self.item(item_id)
            if item["kind"] == "hold":
                self._require_eligible(member, item)
                return self._decide_hold(member, item, outcome, comment,
                                         shown=self._check_shown(member, item_id, seen))
            if item["kind"] != "gate":
                raise Invalid("this item is not a decision")
            self._require_eligible(member, item)
            run, wf, step = self.step_of(item)
            if outcome not in self.open_outcomes(run, step):
                raise Invalid(f"the decision is one of: {', '.join(self.open_outcomes(run, step))}")
            self._require_links(run, step, outcome)
            values = {"outcome": outcome, "comment": comment or ""}
            what = {"run": run["id"], "step": step.id, "outcome": outcome,
                    "meaning": self.meaning_for(member, run, step, outcome),
                    "record": run["record"], "workflow": f"{wf.id}@v{run['version']}",
                    "shown_sha256": self._check_shown(member, item_id, seen), "comment": comment or ""}
            if run["record"] and "/" in run["record"]:  # the decision is also a signature on the record it is about
                rec = hub.records.read(*run["record"].split("/", 1))
                if rec is not None:
                    what.update(version=hub.record_version(run["record"]), record_sha256=hub._record_digest(rec))
            if member["kind"] != "person":
                attested = {**what, "agent": member["id"], "kind": "agent attestation"}
                if step.risk == "high" or propose:
                    return self._hold(member, item, run, step, values, action="gate.decided", attested=attested)
                return self._finish(member, item, run, step, values, action="gate.decided", attested=attested)
            return self._finish(member, item, run, step, values, action="gate.decided",
                                signed={**what, "signer": member["id"]})

    # ------------------------------------------------------------------ high risk: held for the sponsor
    def _hold(self, agent, item, run, step, values, *, action, attested=None) -> dict:
        """An agent's high-risk answer (or its proposed decision) waits, unapplied, until its sponsor approves it."""
        hub = self.hub
        hub.db.items.set(item["id"], {"status": "held", "claimed_by": agent["id"], "output": values,
                                      "updated_at": utcnow()})
        seq = hub.track.append(agent, "item.held", f"item:{item['id']}", run_id=run["id"], cause=item["cause"],
                               data={"step": step.id, "output": values})
        hold_id = _new_id("item")
        hub.db.items.insert(_item_doc(
            hub, id=hold_id, run_id=run["id"], step_id=step.id, kind="hold",
            title=f"Approve {agent['name']}'s '{step.title}'" + (" (high risk)" if step.risk == "high" else ""), role=None, assignee=agent["sponsor"],
            persons_only=1, status="open", output={"holds": item["id"], "action": action, "attested": attested}, cause=seq))
        sp = hub.db.members.find_one({"_id": agent["sponsor"]}, fields=["status"])
        if not sp or sp["status"] != "active":
            self._pass_on(self.item(hold_id), agent, "the agent's sponsor is not active")
        return self.item(item["id"])

    def _decide_hold(self, member, hold, outcome, comment, *, shown: str = "") -> dict:
        from .hub import Invalid
        hub = self.hub
        if outcome not in ("approve", "reject"):
            raise Invalid("approve or reject the agent's work")
        self._require_eligible(member, hold)
        held = self.item(hold["output"]["holds"])
        run, wf, step = self.step_of(held)
        agent = hub.member(held["claimed_by"])
        if outcome == "approve" and agent["status"] != "active":
            raise Invalid(f"{agent['name']} has been switched off since; reject its work instead")
        attested = hold["output"].get("attested")
        meaning = f"I approve {agent['name']}'s work on '{step.title}'" if outcome == "approve" else ""
        signed = None
        if outcome == "approve":
            signed = {"run": run["id"], "step": step.id, "outcome": outcome, "meaning": meaning,
                      "item": held["id"], "signer": member["id"], "shown_sha256": shown}
            if attested:  # approving an agent's proposed decision: the person signs that decision, on the record
                signed["meaning"] = (f"I approve {agent['name']}'s decision on '{step.title}': "
                                     f"{attested.get('meaning') or attested.get('outcome')}")
                signed["decision"] = attested.get("outcome")
                ref = attested.get("record")
                rec = hub.records.read(*ref.split("/", 1)) if ref and "/" in ref else None
                if rec is not None:
                    signed.update(record=ref, version=hub.record_version(ref), record_sha256=hub._record_digest(rec))
        seq = hub.track.append(member, "hold.decided", f"item:{hold['id']}", run_id=run["id"], cause=hold["cause"],
                               data={"step": step.id, "outcome": outcome, "comment": comment or "", "agent": agent["id"]},
                               signed=signed)
        hub.db.items.set(hold["id"], {"status": "done", "claimed_by": member["id"], "updated_at": utcnow()})
        if outcome == "approve":
            self._finish(agent, held, run, step, held["output"], action=hold["output"]["action"],
                         attested=attested, cause=seq, approved_by=member["id"], signature=seq)
        else:  # back to people, with the sponsor's reason
            role, assignee = self._to_people(held)
            hub.db.items.set(held["id"], {
                "status": "open", "claimed_by": None, "output": None, "persons_only": 1,
                "note": f"{member['name']} rejected {agent['name']}'s answer: {comment or 'no reason given'}",
                "role": role, "assignee": assignee, "updated_at": utcnow()})
        return self.item(hold["id"])

    def _finish(self, member, item, run, step, values, *, action, signed=None, attested=None, cause=None,
                approved_by=None, signature=None) -> dict:
        """Apply a step's answer. ``signed``: a person's signature on it. ``attested``: an agent's decision, recorded
        as the agent's own attested action and never as a signature (``signature``: the event of the person who
        signed their approval of it, if any). The engine refuses a signature by anyone but a person, whatever the
        caller passes."""
        from .hub import Denied
        hub = self.hub
        if signed is not None and member.get("kind") != "person":
            raise Denied("only a person signs: agents never sign")
        # the run's own item waits for its next step; a child is done
        walks = item["id"] == run.get("item")
        hub.db.items.set(item["id"], {"status": "waiting" if walks else "done", "claimed_by": member["id"],
                                      "output": values, "updated_at": utcnow()})
        entry = self._hist_update(item["id"], step.id, done_at=utcnow(), by=member["id"],
                                  outcome=values.get("outcome") or "done")
        if entry and item.get("parent_id"):  # a child closes back into the run's item: its history has the step
            hub.db.items.update({"_id": item["parent_id"]}, {"$push": {"history": entry}})
        data = {"step": step.id, "output": values, **({"approved_by": approved_by} if approved_by else {}),
                **({"attested": attested} if attested else {})}
        seq = hub.track.append(member, action, f"item:{item['id']}", run_id=run["id"], cause=cause or item["cause"],
                               data=data, signed=signed)
        run = self.run(run["id"])
        answer = {**values, "by": member["id"], "by_kind": member["kind"], "event": seq,
                  # a signature keeps what it meant: a note filed from it names its signer's meaning
                  **({"signature": seq, "meaning": signed.get("meaning") or ""} if signed
                     else {"signature": signature} if signature else {})}
        st = run["steps"][step.id]
        if step.for_each and st.get("each"):  # once per person: done when each of them is
            got = run["ctx"].get(step.id) if isinstance(run["ctx"].get(step.id), dict) else {}
            got = {"each": [*(got.get("each") or []), {**answer, "person": item.get("assignee") or member["id"]}],
                   "by": member["id"]}
            run["ctx"][step.id] = got
            left = hub.db.items.count({"_id": {"$in": st["each"]}, "status": {"$ne": "done"}})
            if not left:
                run["steps"][step.id] = {**st, "state": "done", "by": member["id"]}
        else:
            run["ctx"][step.id] = answer
            run["steps"][step.id] = {"state": "done", "item": item["id"], "by": member["id"]}
        self._save(run)
        if member["kind"] == "agent" and RISK_ORDER[step.risk] == 1:
            self.alert(run, step, f"{member['name']} did '{step.title}' ({step.risk} risk): take a look",
                       assignee=member["sponsor"], cause=seq, about=member["id"])
        self.advance(run["id"], seq)
        return self.item(item["id"])

    def acknowledge(self, member: dict, item_id: str) -> dict:
        from .hub import Invalid
        item = self.item(item_id)
        if item["kind"] != "notice":
            raise Invalid("only notices are acknowledged")
        if item["status"] not in ("open", "claimed"):
            raise Invalid("this notice has been acknowledged already")
        self._require_eligible(member, item)
        self.hub.db.items.set(item_id, {"status": "done", "claimed_by": member["id"], "updated_at": utcnow()})
        self.hub.track.append(member, "notice.acknowledged", f"item:{item_id}", run_id=item["run_id"], cause=item["cause"])
        return self.item(item_id)

    # ------------------------------------------------------------------ inbound events
    def _starts_on(self, wf, event_type: str, ctx: dict) -> bool:
        if wf.event != event_type:
            return False
        try:
            return expr.holds(wf.when, ctx) if wf.when else True
        except expr.ExprError:
            return False

    def emit(self, actor: dict, event_type: str, *, record: str | None = None, data: dict | None = None) -> list[dict]:
        """An event from outside (a lab interface, a patient portal, a schedule) starts the workflows listening for it,
        when their ``trigger.when`` holds. The emitter must be a member allowed to start them."""
        from .hub import Conflict, Invalid
        seq = self.hub.track.append(actor, "event.received", record or "", data={"type": event_type, **(data or {})})
        started, refused = [], []
        rec = self.hub.records.read(*record.split("/", 1)) if record and "/" in record else None
        for w in self.hub.workflows():
            wf = w["workflow"]
            if self._starts_on(wf, event_type, {"event": data or {}, "record": rec or {}}):
                try:
                    started.append(self.start(actor, wf.id, record=record, inputs=data or {}, cause=seq))
                except Conflict:
                    raise  # the same work is already open for this (the event sent again): it is being handled
                except Invalid as exc:
                    # what the event carried does not pass the workflow's start form (a lab flag the form does not
                    # list), or the workflow cannot be read: nothing started, so it is not dropped either
                    refused.append((wf, exc))
        if refused:
            self._unhandled(actor, event_type, record, seq, refused=refused)
            raise refused[0][1]  # the sender is told as well: the event was not taken
        if not started:
            self._unhandled(actor, event_type, record, seq)
        return started

    def _unhandled(self, actor: dict, event_type: str, record: str | None, cause: int, refused=()) -> str:
        """An event that no workflow took (none listens for it, none's ``when`` held, or the one that listens refused
        to start on what it carried: ``refused``, ``[(workflow, error)]``) is not dropped: the track records it and
        the escalation role gets an urgent notice, on a run of its own so the notice has a home. Only the event's
        type is kept on the run and in the notice; what it carried, and what was wrong with it, stay in the track
        (shown, like the event, to those who read its data classes)."""
        hub = self.hub
        who = actor.get("name") or actor["id"]
        if refused:
            names = ", ".join(f"\u201c{wf.title}\u201d" for wf, _ in refused)
            n = sum(len(getattr(exc, "problems", None) or [1]) for _, exc in refused)
            title = f"An event a workflow did not start on: {event_type}"
            words = (f"Urgent: {who} sent the event \u201c{event_type}\u201d and {names} did not start on it: what it "
                     f"carried did not pass the start form ({n} problem{'s' if n != 1 else ''}; the track says "
                     f"which). Nothing was started for it. Deal with what it was about by hand, and check the "
                     f"sender or the workflow's start form.")
            data = {"type": event_type, "refused": [{"workflow": wf.id, "problems": list(
                getattr(exc, "problems", None) or [str(exc)])[:20]} for wf, exc in refused]}
        else:
            title = f"An event no workflow took: {event_type}"
            words = (f"Urgent: {who} sent the event \u201c{event_type}\u201d and no workflow took it. Check the "
                     f"sender, or the workflow meant to listen for it.")
            data = {"type": event_type}
        with hub.db:
            seq = hub.track.append(actor, "event.unhandled", record or "", cause=cause, data=data)
            run_id = home_run(hub, UNHANDLED_WORKFLOW, title=title, started_by=actor["id"], cause=cause, record=record,
                              ctx={"event": {"type": event_type}})
            run = {"id": run_id}
            self.alert(run, None, words, role=hub.practice.get("escalation_role"), cause=seq, priority="urgent")
        return run_id

    def on_record(self, change: str, rt, rec: dict, before: dict | None, actor: dict, cause: int) -> list[dict]:
        """A record was created, updated or moved through its lifecycle: start the workflows listening for
        ``record.<change>:<Type>`` whose ``trigger.when`` holds: of its own type or any type above it (a change to an
        individual is a change to a client). The record change is the cause; the workflow was approved to start this
        way, so no starter role is needed. A run never restarts its own workflow."""
        ref = f"{rt.id}/{rec['id']}"
        kin = (rt.id, *rt.ancestors)
        events = {f"record.{change}:{t}" for t in kin}
        if change == "transitioned":
            events |= {f"record.updated:{t}" for t in kin}
        started = []
        for w in self.hub.workflows():
            wf = w["workflow"]
            if not wf.event or wf.event not in events:
                continue
            if str(actor.get("id", "")).startswith(f"workflow:{wf.id}@"):
                continue
            ctx = {"event": {"type": wf.event, "change": change, "ref": ref}, "record": rec, "before": before or {}}
            if not self._starts_on(wf, wf.event, ctx):
                continue
            starter = actor if actor.get("kind") in ("person", "agent", "service") else self.automation_actor_for(wf)
            inputs = {f["name"]: rec.get(f["name"]) for f in wf.inputs if f["name"] in rec}
            try:
                started.append(self.start(starter, wf.id, record=ref if wf.record in kin else None, inputs=inputs,
                                          cause=cause, via_automation=True))
            except Exception as exc:  # noqa: BLE001 - a trigger that cannot start is reported, not fatal to the write
                self.hub.track.append(self.automation_actor_for(wf), "trigger.failed", ref, cause=cause,
                                      data={"workflow": wf.id, "event": wf.event, "error": str(exc)})
        self.recheck_waits(cause, ref)
        return started

    def automation_actor_for(self, wf) -> dict:
        return {"id": f"trigger:{wf.id}", "kind": "automation", "name": wf.title, "status": "active"}

    # ------------------------------------------------------------------ time: schedules and deadlines
    def tick(self, now: datetime | None = None) -> dict:
        """Run what time makes due: scheduled workflows (once per slot, remembered in settings) and overdue items
        (flagged, and escalated to the step's ``escalate_to`` role), waits whose time or timeout has come, and runs
        past their deadline (flagged once). Safe to call as often as you like."""
        tz = self.zone()
        # schedules fire by the practice's wall clock (a daily 08:00 is 08:00 in the practice, whatever the server's
        # zone); an aware ``now`` (an instant) is turned into it, a naive one already is it
        now = clock.local(now, tz) if now else self.now()
        now_utc = clock.utc(now, tz)
        stamp = now.strftime("%Y-%m-%dT%H:%M")
        self._tick_at = now_utc
        try:
            out = self._tick(now, now_utc, stamp)
        finally:
            self._tick_at = None
        # jobs: one cut short is failed or queued again, and one no worker has taken for two minutes is run here
        # (the workers stopped, or this hub is not served and something else queued it)
        self.recover_jobs()
        old = [st for row in self.hub.db.runs.find({"status": "running", "jobs": {"$ne": None}}, fields=["steps"])
               for st in (row.get("steps") or {}).values() if isinstance(st, dict) and st.get("state") == "queued"
               and (datetime.now(timezone.utc) - _parse(st["queued_at"])).total_seconds() > 120]
        if old:
            self.run_jobs()
        return out

    def _tick(self, now: datetime, now_utc: datetime, stamp: str) -> dict:
        from .hub import Invalid
        hub = self.hub
        tz = self.zone()
        started, overdue = [], []
        last = hub.db.get_setting("schedule_last", {}) or {}
        for w in hub.workflows():
            wf = w["workflow"]
            slot = _slot(wf.schedule, now) if wf.schedule else None
            if not slot or last.get(wf.id) == slot:
                continue
            last[wf.id] = slot
            hub.db.set_setting("schedule_last", last)
            actor = self.automation_actor_for(wf)
            seq = hub.track.append(actor, "schedule.fired", f"workflow:{wf.id}", data={"slot": slot})
            if wf.for_each:
                for rec in hub.records.search(hub.family(wf.for_each), limit=None):
                    if wf.where and not expr.holds(wf.where, {"record": rec, "run": {"today": now.date().isoformat()}}):
                        continue
                    ref = f"{rec['resourceType']}/{rec['id']}"
                    on = wf.record and hub.is_a(rec["resourceType"], wf.record)
                    try:
                        started.append(self.start(actor, wf.id, record=ref if on else None,
                                                  cause=seq, via_automation=True)["id"])
                    except Exception as exc:  # noqa: BLE001
                        hub.track.append(actor, "trigger.failed", ref, cause=seq, data={"error": str(exc)})
            else:
                try:
                    started.append(self.start(actor, wf.id, cause=seq, via_automation=True)["id"])
                except Exception as exc:  # noqa: BLE001
                    hub.track.append(actor, "trigger.failed", f"workflow:{wf.id}", cause=seq,
                                     data={"error": str(exc)})
        for item in hub.db.items.find({"status": {"$in": ["open", "claimed"]},
                                       "due_at": {"$ne": None, "$lt": clock.stamp(now_utc, tz)},
                                       "kind": {"$ne": "notice"}}, sort="n"):
            # escalation repeats until the work is done: once per the step's own time allowed (at least 15 minutes),
            # first to the step's escalation role, then also to the practice's escalation role and its owners
            level = int(item["escalated"] or 0)
            if item.get("escalate_next") and now_utc < _parse(item["escalate_next"]):
                continue
            due = _parse(item["due_at"])
            began = _parse(item.get("step_started_at") or item["created_at"])
            allowed = min(max(timedelta(minutes=15), due - began), timedelta(days=1))
            run = self.run(item["run_id"])
            try:
                wf, _ = hub.workflow(run["workflow_id"], run["version"])
            except Invalid:
                continue  # its version no longer reads (``Hub.workflow`` says why): the clock goes on for the rest
            step = wf.step(item["step_id"])
            # whom it is stuck with: who took it, else everyone on duty it waits for
            held = item["claimed_by"] or item["assignee"]
            pool = set() if held else set(self._staffed(item.get("role")))
            with hub.db:
                hub.db.items.set(item["id"], {"escalated": level + 1,
                                              "escalate_next": clock.stamp(now_utc + allowed, tz),
                                              "updated_at": utcnow()})
                self._hist_update(item["id"], item["step_id"], escalated=level + 1)
                seq = hub.track.append(self.automation_actor(run), "item.overdue", f"item:{item['id']}",
                                       run_id=run["id"], cause=item["cause"],
                                       data={"due_at": item["due_at"], "level": level + 1})
                what = self.what(item)
                text = (f"Overdue: {what}" if level == 0 else f"Still not done ({level + 1}): {what}")
                text += ". You may take it over."
                once = dict(cause=seq, escalates=item["id"])
                if held:  # never only to the one who is late: whoever else holds the escalation role, else the next
                    self.alert(run, step, text, role=step.escalate_to, priority=item.get("priority"), besides=held,
                               **once)
                else:
                    # a pool nobody took: each holder of the escalation role is named, though they are in the pool
                    # (the medical director is a physician); when that tells no one outside the pool, the practice's
                    # escalation role hears too, so it is never only those it already waits on
                    named = self._staffed(step.escalate_to)
                    for m in named:
                        self._notice(run, step, text, assignee=m, priority=item.get("priority"), **once)
                    chasers = self._staffed(hub.practice.get("escalation_role"), pool)
                    if not named:
                        self.alert(run, step, text, role=step.escalate_to, priority=item.get("priority"),
                                   besides=pool, **once)
                    elif not set(named) - pool and chasers:
                        self.alert(run, step, text, role=hub.practice.get("escalation_role"),
                                   priority=item.get("priority"), besides=pool, **once)
                if level >= 1:  # the second time and after: the practice's escalation role and owners hear too
                    self.alert(run, step, text, role=hub.practice.get("escalation_role"), priority="urgent",
                               besides=held or pool, owners_too=True, **once)
            overdue.append(item["id"])
        missed = self._missed_deadlines(now_utc)
        checked = hub.db.get_setting("waits_checked", "") or ""
        if (clock.stamp(now_utc - timedelta(minutes=15), tz) >= checked
                and hub.db.runs.exists({"status": "running", **_WAITING})) \
                or hub.db.runs.exists({"status": "running", **_timers_due(clock.stamp(now_utc, tz))}):
            hub.db.set_setting("waits_checked", clock.stamp(now_utc, tz))
            tick_seq = hub.track.append({"id": "clock", "kind": "automation"}, "clock.ticked", "", data={"at": stamp})
            self.recheck_waits(tick_seq)  # a wait on something time changes (or that changed outside the hub)
        return {"started": started, "overdue": overdue, "deadlines_missed": missed, "at": stamp}


def _parse(stamp: str) -> datetime:
    """A track time (UTC, ISO, Z) as an aware datetime."""
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _alive(pid: int) -> bool:
    """Whether a process of this machine still runs."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # it is there, and not ours to signal
        return True
    return True


def _timing(step) -> dict:
    """A wait's time settings, for the track."""
    return {k: v for k, v in (("for", getattr(step, "wait_for", None)), ("at", getattr(step, "at", None)),
                              ("timeout", getattr(step, "timeout", None))) if v}


def _timers_due(now_stamp: str) -> dict:
    """Runs with a waiting step whose time (``wake_at``) or timeout (``timeout_at``) has come."""
    due = lambda k: {"$lte": [{"$ifNull": [f"$$s.v.{k}", "~"]}, now_stamp]}  # "~" sorts after every time
    return {"$expr": {"$gt": [{"$size": {"$filter": {
        "input": {"$objectToArray": {"$ifNull": ["$steps", {}]}}, "as": "s",
        "cond": {"$and": [{"$eq": ["$$s.v.state", "waiting"]}, {"$or": [due("wake_at"), due("timeout_at")]}]}}}}, 0]}}


def _moment(value, tz) -> datetime | None:
    """A value a wait's ``at`` gives, as an aware instant: a date is its midnight in the practice, a time without
    an offset the practice's wall time, one with an offset (or Z) that instant. None for anything else."""
    from datetime import date
    if isinstance(value, dict) and "start" in value:
        value = value["start"]
    if isinstance(value, datetime):
        return clock.utc(value, tz)
    if isinstance(value, date):
        return clock.utc(datetime(value.year, value.month, value.day), tz)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return clock.utc(t, tz)


DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _slot(schedule: str, now: datetime) -> str | None:
    """The slot a schedule is in at ``now`` (once it has come round), or None if it has not fired yet in this
    period. A slot is the label that makes a schedule fire once: the hour, the stretch of minutes, the day, the week
    or the month."""
    import calendar
    if schedule == "hourly":
        return now.strftime("%Y-%m-%dT%H")
    parts = schedule.split()
    if parts[0] == "every":  # every 15m, every 2h: stretches counted from midnight, so each day starts afresh
        n, unit = int(parts[1][:-1]), parts[1][-1]
        size = n * (60 if unit == "h" else 1)
        start = (now.hour * 60 + now.minute) // size * size
        return f"{now:%Y-%m-%d}T{start // 60:02d}:{start % 60:02d}"
    hh, mm = map(int, parts[-1].split(":"))
    if (now.hour, now.minute) < (hh, mm):
        return None
    if parts[0] == "daily":
        return now.strftime("%Y-%m-%d")
    if parts[0] == "weekdays":
        return now.strftime("%Y-%m-%d") if now.weekday() < 5 else None
    if parts[0] == "weekly":
        days = [DAYS.index(d) for d in parts[1].split(",")]
        if now.weekday() not in days:
            return None
        # one day keeps its old label (the week), so a schedule already fired this week does not fire again
        return now.strftime("%G-W%V") if len(days) == 1 else now.strftime("%Y-%m-%d")
    if parts[0] == "monthly":
        day = calendar.monthrange(now.year, now.month)[1] if parts[1] == "last" else int(parts[1])
        return now.strftime("%Y-%m") if now.day == day else None
    return None
