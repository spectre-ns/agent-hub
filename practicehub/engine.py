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

import json
import re
import secrets
from datetime import datetime, timedelta

from . import blocks as blockmod
from . import clock
from . import expr
from . import schema
from . import visibility
from . import workflows as wfmod
from .db import utcnow
from .backends import backend_for
from .fields import FieldError, coerce, json_schema

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


# The run that holds the notice about an event no workflow took: not a workflow anyone wrote, so it is not under change
# control and starts nothing; ``Hub.workflow`` gives it this one step, already skipped, for the pages that show a run.
UNHANDLED_WORKFLOW = "event-not-taken"
UNHANDLED_SOURCE = (f'id = "{UNHANDLED_WORKFLOW}"\ntitle = "An event no workflow took"\n'
                    'summary = "An event arrived and no workflow listening for it started. The track has what it '
                    'carried."\n[[steps]]\nid = "look"\ntitle = "Look at the event"\nkind = "wait"\nuntil = "false"\n')

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
                              "status": "queued", "classes": ",".join(sorted(classes)), "claimed_by": None,
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
        return set(re.findall(r"\{\{\s*([A-Za-z_][\w-]*)\.", json.dumps(st.params or {})))

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


def act_record_create(engine, run, step, params, actor):
    rtype = params.get("type")
    data = params.get("data") or {}
    if not rtype:
        raise ValueError("record.create: no type")
    rec = engine.hub.create_record(actor, rtype, data, run_id=run["id"])
    ref = f"{rtype}/{rec['id']}"
    if params.get("attest"):  # filed under a signature given earlier in this run (a note signed as a draft)
        engine.hub.attest(actor, ref, run, str(params["attest"]))
    return {"id": rec["id"], "reference": ref}


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
                         inputs=params.get("input") or {}, via_automation=True, parent=run["id"])
    return {"run_id": child["id"]}


def act_context_set(engine, run, step, params, actor):
    return dict(params)


CORE_ACTIONS = {
    "message.send": act_message_send,
    "record.create": act_record_create,
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
    (``type`` names one when it has several)."""
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
    for b in bookings(hub, since=when, until=when + "~"):
        if b["start"][:16] == when and member in b["with"]:
            raise ValueError(f"the slot {when} with {member} was taken in the meantime; propose another")
    who = hub.member(member)
    rec = schema.compose(hub.record_type(rtype), {
        "start": start.isoformat(timespec="minutes"),
        "end": (start + timedelta(minutes=minutes)).isoformat(timespec="minutes"),
        "reason": str(params.get("reason") or ""), "subject": params.get("subject") or run["record"],
        "with": [member],
        **{k: str(params[k]) for k in ("kind", "priority") if params.get(k) and k in hub.record_type(rtype).base_map}},
        hub.member_ref_type(), dict(params.get("defaults") or {}))
    made = hub.create_record(actor, rtype, rec, run_id=run["id"])
    return {"reference": f"{rtype}/{made['id']}", "when": start.strftime("%A %B %d at %H:%M"),
            "member": member, "member_name": who["name"]}


CORE_ACTIONS["booking.book"] = act_booking_book


def prov_free_slots(engine, run, step, member):
    """Free appointment slots in the practice calendar, for the next ``lookahead_days`` working days."""
    hub = engine.hub
    cal = hub.pack.calendar if hub.pack else {}
    minutes = int(cal.get("slot_minutes", 15))
    hours = cal.get("hours", {})
    role = cal.get("provider_role")
    providers = [m for m in (hub.role(role)["holders"] if role else []) if m["kind"] == "person" and m["status"] == "active"]
    busy = set()
    now = engine.now()
    for b in bookings(hub, since=now.date().isoformat()):
        for who in b["with"]:
            busy.add((who, b["start"][:16]))
    now = engine.now()
    out = []
    day = now.date()
    days_seen = 0
    while days_seen < int(cal.get("lookahead_days", 5)) and len(out) < int(cal.get("max_slots", 24)):
        day += timedelta(days=1)
        key = day.strftime("%a").lower()[:3]
        if key not in hours:
            continue
        days_seen += 1
        start_s, end_s = hours[key]
        t = datetime.combine(day, datetime.strptime(start_s, "%H:%M").time())
        end = datetime.combine(day, datetime.strptime(end_s, "%H:%M").time())
        while t + timedelta(minutes=minutes) <= end:
            for prov in providers:
                stamp = t.strftime("%Y-%m-%dT%H:%M")
                if (prov["id"], stamp) not in busy:
                    out.append({"value": f"{stamp}|{prov['id']}",
                                "label": f"{t.strftime('%a %b %d %H:%M')} with {prov['name']}"})
            t += timedelta(minutes=minutes * int(cal.get("slot_stride", 4)))
    return out[: int(cal.get("max_slots", 24))]


def prov_members(engine, run, step, member):
    return [{"value": m["id"], "label": f"{m['name']} ({m['kind']})"} for m in engine.hub.members() if m["status"] == "active"]


CORE_PROVIDERS = {"free-slots": prov_free_slots, "members": prov_members}


# urgent work first, then what is due soonest, then the oldest
def _queue_key(item: dict):
    """The order work is shown in: urgent first, then by due time (none last), then oldest first."""
    return (0 if item.get("priority") == "urgent" else 1, item.get("due_at") is None, item.get("due_at") or "",
            item.get("created_at") or "", item.get("n") or 0)


def _plain(doc: dict | None) -> dict | None:
    """A document as the hub hands it out: its fields, without the store's ``_id``."""
    return {k: v for k, v in doc.items() if k != "_id"} if doc is not None else None


def _mentions(value, ref: str) -> bool:
    """Whether ``ref`` is anywhere in this value, as a string or a key (what searching its JSON for it found)."""
    if isinstance(value, str):
        return value == ref
    if isinstance(value, dict):
        return any(k == ref or _mentions(v, ref) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_mentions(v, ref) for v in value)
    return False


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


class Engine:
    def __init__(self, hub):
        self.hub = hub
        self._now = None
        self._zone = None  # (until, the practice's ZoneInfo): ``zone`` reads the setting at most every 2 seconds
        self._advancing: set = set()  # runs advancing now (a record change inside one does not re-enter it)

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
        if not mentions:
            return [_plain(r) for r in self.hub.db.runs.find(flt, sort=order, limit=limit)]
        # about it, or carrying it anywhere in the run's data (runs keep the records they carry in ``refs``; one
        # saved before it did is searched)
        out = []
        flt = {**flt, "$or": [{"record": mentions}, {"refs": mentions}, {"refs": {"$exists": False}}]}
        for r in self.hub.db.runs.find(flt, sort=order):
            if r.get("record") == mentions or mentions in (r.get("refs") or ()) or _mentions(r.get("ctx"), mentions):
                out.append(_plain(r))
                if len(out) >= limit:
                    break
        return out

    def _save(self, run: dict) -> None:
        self.hub.db.runs.set(run["id"], {"ctx": run["ctx"], "steps": run["steps"], "status": run["status"],
                                         "refs": run_refs(run), "updated_at": utcnow()})

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
              cause: int | None = None, via_automation: bool = False, parent: str | None = None) -> dict:
        from .hub import Denied, Invalid
        hub = self.hub
        wf, row = hub.workflow(wid)
        if not via_automation:
            if actor.get("status") != "active":
                raise Denied("an inactive member cannot start work")
            hub.require(actor, "starter", f"workflow:{wid}", f"start {wf.title}")
        if wf.record:
            if not record or record.split("/")[0] != wf.record:
                raise Invalid(f"{wf.title} is about a {wf.record}; say which one")
            if not hub.records.read(*record.split("/", 1)):
                raise Invalid(f"there is no {record}")
        try:
            values = coerce(wf.inputs, inputs or {})
        except FieldError as exc:
            raise Invalid("the start form has problems", exc.problems) from None
        bad = self._check_references(wf, record, values)
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
        ctx = {"run": {"id": run_id, "record": record, "started_by": actor["id"], "workflow": wid, "parent": parent,
                       "blocks": pins, "skills": skills, "now": utcnow(),
                       "today": self.now().date().isoformat()},
               "input": values}
        with hub.db:
            hub.db.runs.insert({"_id": run_id, "id": run_id, "workflow_id": wid, "version": row["version"],
                                "title": title, "record": record, "status": "running", "ctx": ctx,
                                "steps": {s.id: {"state": "pending"} for s in wf.steps}, "started_by": actor["id"],
                                "refs": run_refs({"ctx": ctx, "record": record}),
                                "root_event": None, "created_at": utcnow(), "updated_at": utcnow()})
            seq = hub.track.append(actor, "run.started", record or "", run_id=run_id, cause=cause,
                                   data={"workflow": wid, "version": row["version"], "title": title, "input": values,
                                         "parent": parent})
            hub.db.runs.set(run_id, {"root_event": seq, "item": self._run_item(run_id, wf, title, seq)})
            if getattr(wf, "deadline", None):  # [deadline] within: when the run is to be done by
                hub.db.runs.set(run_id, {"deadline_at": self._deadline_at(wf), "deadline_missed_at": None})
            self.advance(run_id, seq)
        return self.run(run_id)

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

    def _advance(self, run_id: str, cause: int | None) -> None:
        hub = self.hub
        progressed = True
        while progressed:
            progressed = False
            run = self.run(run_id)
            if run["status"] != "running":
                return
            wf, _ = hub.workflow(run["workflow_id"], run["version"])
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
                if step.after and all(run["steps"][a]["state"] == "skipped" for a in step.after):
                    holds = False  # a step after only skipped steps is skipped too
                else:
                    holds = expr.holds(step.when, self._live_ctx(run))
                if not holds:
                    st.update(state="skipped")
                    self._save(run)
                    hub.track.append(self.automation_actor(run), "step.skipped", f"step:{step.id}", run_id=run_id,
                                     cause=cause, data={"step": step.id, "when": step.when})
                    break
                if step.kind == "wait":
                    st.update(state="waiting")
                    self._save(run)
                    self._hist_note(run, step, done=False)
                    hub.track.append(self.automation_actor(run), "step.waiting", f"step:{step.id}", run_id=run_id,
                                     cause=cause, data={"step": step.id, "until": step.until,
                                                        **({"for_each": step.for_each} if step.for_each else {}),
                                                        **_timing(step)})
                    if not self._wait_over(run, step, cause) and step.role:
                        self._notice(run, step, f"Waiting: {step.title}", role=step.role, cause=cause)
                    break
                if step.kind == "automation":
                    actor = self.automation_actor(run)
                    live = self._live_ctx(run)
                    params = wfmod.render(step.params, live) if not step.for_each else {}
                    extra: dict = {}
                    try:
                        with hub.db:  # a savepoint: an automation that fails halfway leaves nothing behind
                            if step.block:
                                output, extra = self._run_block(run, step, params)
                            else:
                                fn = CORE_ACTIONS.get(step.action) or (hub.pack.actions.get(step.action)
                                                                       if hub.pack else None)
                                if fn is None:
                                    raise ValueError(f"there is no action {step.action!r}")
                                if step.for_each:
                                    output, params = self._for_each(run, step, fn, actor, live)
                                else:
                                    output = fn(self, run, step, params, actor) or {}
                    except Exception as exc:  # an automation that fails stops the run and asks a person
                        problems = getattr(exc, "problems", None)
                        if problems and problems != [str(exc)]:
                            exc = ValueError(f"{exc}: {'; '.join(problems)}")
                        st.update(state="failed", error=str(exc))
                        self._save(run)
                        self._hist_note(run, step, outcome="failed", by=actor["id"])
                        seq = hub.track.append(actor, "step.failed", f"step:{step.id}", run_id=run_id, cause=cause,
                                               data={"step": step.id, "action": step.action, "error": str(exc),
                                                     **({"block": step.block} if step.block else {})})
                        self._failed(run, step, exc, seq)
                        progressed = True  # steps that do not wait on this one go on: an urgent call is not held
                        break
                    run = self.run(run_id)  # the action may have changed the run (e.g. started a child)
                    run["ctx"][step.id] = output
                    run["steps"][step.id] = {"state": "done", "by": actor["id"]}
                    self._save(run)
                    self._hist_note(run, step, outcome="done", by=actor["id"])
                    cause = hub.track.append(actor, "action.ran", f"step:{step.id}", run_id=run_id, cause=cause,
                                             data={"step": step.id, "action": step.action, "params": params,
                                                   "output": output, **extra})
                else:
                    seq = hub.track.append(self.automation_actor(run), "step.activated", f"step:{step.id}",
                                           run_id=run_id, cause=cause,
                                           data={"step": step.id, "kind": step.kind, "role": step.role})
                    if not run.get("item"):  # a run with no item of its own (none before it): an item per step
                        st.update(state="active", item=self._new_item(run, step, seq))
                    elif step.for_each:  # once per person: a child item each
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
            hub.track.append(self.automation_actor(run), "run.completed", run["record"] or "", run_id=run_id, cause=cause)
            self._late_finish(run, cause)
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
                if not expr.holds(step.until, {**live, "each": each, "each_record": each_record or {}}):
                    return self._timed_out(run, step, cause)
                waited.append(each)
        elif not expr.holds(step.until, live):
            return self._timed_out(run, step, cause)
        out = {"waited_for": waited or []}
        if getattr(step, "timeout", None):
            out["timed_out"] = False
        return self._end_wait(run, step, cause, out, "step.done")

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

    def recheck_waits(self, cause: int | None) -> None:
        """Something changed: let every run that is waiting see whether what it waits for now holds."""
        rows = self.hub.db.runs.find({"status": "running", **_WAITING}, fields=["id"])
        for r in rows:
            if r["id"] in self._advancing:
                continue  # that run is advancing now and checks its waits itself
            try:
                with self.hub.db:  # a savepoint: one run that cannot go on never fails the write that woke it
                    self.advance(r["id"], cause)
            except Exception as exc:  # noqa: BLE001
                self.hub.track.append({"id": "engine", "kind": "automation"}, "run.stuck", r["id"], cause=cause,
                                      data={"error": str(exc)})

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
        if run["started_by"] != actor["id"] and not self.hub.is_policy_owner(actor):
            raise Denied("only whoever started it or a policy owner may cancel a run")
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
            self._abandon(run, seq)

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
            run["steps"][step_id] = {"state": "pending"}
            self._save(run)
            self.hub.db.items.update({"run_id": run_id, "step_id": step_id, "kind": "notice", "status": "open"},
                                     {"$set": {"status": "done", "updated_at": utcnow()}}, many=True)
            seq = self.hub.track.append(actor, "step.retried", f"step:{step_id}", run_id=run_id,
                                        data={"step": step_id, "error": st.get("error"), "reason": reason})
        self.advance(run_id, seq)

    def _abandon(self, run: dict, cause: int) -> None:
        """A run about a record was cancelled: if the record sits in a state only workflows leave, move it where its
        lifecycle says abandoned work goes, unless other work on it is still open."""
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
        st = schema.first(rec, lc["path"])
        to = lc["abandoned"].get(st)
        if to:
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
        block = self.block_of(run, step)
        inputs = self._block_inputs(block, step, params)
        engine = backend_for(block)
        res = engine.run(block, inputs, {"run": run["id"], "step": step.id,
                                         "version": (run["ctx"]["run"].get("blocks") or {}).get(block.id)})
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

    def _new_item(self, run: dict, step, cause: int, *, parent: dict | None = None, person: str | None = None) -> str:
        """A child item of the run's item (``parent``), or, for a run without one, an item for this step alone."""
        item_id = _new_id("item")
        route = self._route(run, step, person)
        self.hub.db.items.insert(_item_doc(
            self.hub, id=item_id, run_id=run["id"], step_id=step.id, kind=step.kind,
            title=run["title"] if parent else step.title, step_title=step.title, status="open", cause=cause,
            parent_id=parent["id"] if parent else None, step_started_at=utcnow(),
            history=[self._entry(step, route, item_id)], **route))
        if parent:
            self.hub.db.items.update({"_id": parent["id"]}, {"$push": {"children": item_id, "wave": item_id},
                                                             "$set": {"updated_at": utcnow()}})
        if not route["assignee"] and route["role"] and not self.hub.active_holders(
                route["role"], persons_only=bool(route["persons_only"])):
            self._nobody_holds(run, step, item_id, cause)
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
        step = waits[0] if waits else None
        hub.db.items.set(item_id, {"waiting": "; ".join(words), "kind": "wait", "step_id": step.id if step else "",
                                   "step_title": step.title if step else "", "role": None, "assignee": None,
                                   "claimed_by": None, "due_at": None, "escalated": 0, "escalate_next": None,
                                   "persons_only": 0, "updated_at": utcnow()})

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
        return False

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

    def _notice(self, run: dict, step, text: str, *, role: str | None = None, assignee: str | None = None,
                cause: int | None = None, priority: str | None = None, about: str | None = None) -> str:
        item_id = _new_id("item")
        prio = priority or (getattr(step, "priority", None) if step else None) or "normal"
        self.hub.db.items.insert(_item_doc(
            self.hub, id=item_id, run_id=run["id"], step_id=step.id if step else "", kind="notice", title=text,
            role=role, assignee=assignee, persons_only=1, status="open", cause=cause,
            priority=prio if prio in ("normal", "urgent") else "normal", output={"about": about} if about else None))
        return item_id

    def _staffed(self, role: str | None, besides: str | None = None) -> list[str]:
        """The people on duty who hold ``role``, leaving out ``besides``."""
        return [m["id"] for m in (self.hub.active_holders(role, persons_only=True) if role else [])
                if m["id"] != besides]

    def alert(self, run: dict, step, text: str, *, role: str | None = None, assignee: str | None = None,
              cause: int | None = None, priority: str | None = None, about: str | None = None,
              besides: str | None = None, owners_too: bool = False) -> list[str]:
        """A notice someone on duty will see: the person named if they are active, else the role named if a person
        on duty holds it, else the practice's escalation role, else each policy owner on duty. Never a notice to
        nobody. ``besides`` (whoever is late with the work) is never the only one told: when they are the role's
        only holder on duty, each other person holding it is told by name, else the next in line is."""
        hub = self.hub
        out: list[str] = []
        if assignee and assignee != besides:
            m = hub.db.members.find_one({"_id": assignee}, fields=["status", "kind"])
            if m and m["status"] == "active" and m["kind"] == "person":
                return [self._notice(run, step, text, assignee=assignee, cause=cause, priority=priority, about=about)]
        for r in (role, hub.practice.get("escalation_role")):
            others = self._staffed(r, besides)
            if not others:
                continue
            if besides and besides in self._holders(r):  # the late one holds it too: tell the others by name
                out = [self._notice(run, step, text, assignee=o, cause=cause, priority=priority, about=about)
                       for o in others]
            else:
                out = [self._notice(run, step, text, role=r, cause=cause, priority=priority, about=about)]
            break
        told = set(out)
        if out and not owners_too:
            return out
        owners = [m for m in hub.members("person") if m["status"] == "active" and hub.is_policy_owner(m)
                  and m["id"] != besides]
        named = {r.get("assignee") for r in hub.db.items.find({"_id": {"$in": sorted(told)}}, fields=["assignee"])
                 } if told else set()
        return out + [self._notice(run, step, text, assignee=m["id"], cause=cause, priority=priority, about=about)
                      for m in owners if m["id"] not in named]

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
            return False, f"{item['claimed_by']} is working on it"
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
        if not item["assignee"] and item["role"] not in member["roles"]:
            return False, f"it needs the {item['role']} role"
        run = self.run(item["run_id"])
        wf, _ = hub.workflow(run["workflow_id"], run["version"])
        step = wf.step(item["step_id"])
        for prior in step.not_by:
            if prior == "run":
                if run["started_by"] == member["id"]:
                    return False, "whoever started this does not also do this step"
            elif prior == "record.editors":
                if run["record"] and member["id"] in hub.revision_authors(run["record"]):
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
            return False, ("what it is about is withheld from you at the subject's request: open the file in an "
                           "emergency first, or leave it to someone the directive allows")
        if is_agent:
            if member["kind"] == "service":
                return False, "services do not take work items"
            if run["record"] and hub.restrictions.withholds_from(member, run["record"], reaching=True):
                # the subject's directive gives an agent nothing of what it withholds: the work's inputs copy it
                return False, "what it is about is withheld from agents at the subject's request: a person does it"
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
        starter = self.hub.member(run["started_by"]) if run.get("started_by") else None
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
                         f"at the subject's request")
        data: dict = {}
        for c in step.context:
            if c.startswith("record:"):
                dc = c[7:]
                recs = []
                if run["record"]:
                    rtype, rid = run["record"].split("/", 1)
                    main = hub.records.read(rtype, rid)
                    if main and hub.class_or_none(rtype) == dc:
                        recs.append(main)
                    recs += [r for r in hub.records.search(subject=run["record"], limit=None)
                             if hub.class_or_none(r["resourceType"]) == dc and r is not main
                             and f"{r['resourceType']}/{r['id']}" != run["record"]]
                data[c] = hub.redact_all(member, recs)  # without the fields of a class the member may not read
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
                       "choices": step.outcomes, "help": ""},
                      {"name": "comment", "label": "Comment", "type": "text", "required": False, "help": ""}]
        return {
            "item": item, "run": {"id": run["id"], "title": self.hub.run_title(member, run), "record": run["record"],
                                  "workflow": wf.id, "version": run["version"],
                                  "today": self.now().date().isoformat(), "time_zone": self.zone().key},
            "step": {"id": step.id, "title": step.title, "kind": step.kind, "risk": step.risk, "role": step.role,
                     "instructions": instructions, "outcomes": step.outcomes,
                     "meaning": self.meaning_for(member, run, step),
                     "meanings": {o: self.meaning_for(member, run, step, o) for o in step.outcomes},
                     "signer": step.signer, "skill": skill.id if skill else step.skill, "block": step.block},
            "skill": {"id": skill.id, "title": skill.title, "instructions": skill.instructions} if skill else None,
            "context": data, "draft": draft, "fields": fields,
            "schema": json_schema(fields, self._choice_ctx(fields, data, run)),
            "choices": {f["name"]: self._choices(f, data, run) for f in fields if f["type"] == "choice" or f.get("choices_from")},
        }

    def meaning_for(self, member: dict, run: dict, step, outcome: str | None = None) -> str:
        """What signing this gate says for this member: a ``meanings_if_did`` meaning when they did that earlier
        step (by the run's step history, so a reassignment or take-over does not change it), else the outcome's
        own meaning, else the step's."""
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

    def complete(self, member: dict, item_id: str, output: dict) -> dict:
        """Finish a task with its fields."""
        from .hub import Invalid
        hub = self.hub
        with hub.db:
            item = self.item(item_id)
            if item["kind"] != "task":
                raise Invalid("this item is a decision or a notice, not a task")
            self._require_eligible(member, item)
            run, wf, step = self.step_of(item)
            ctxview = self.context(member, item_id)
            fields = self.fields_for(step, run)
            try:
                values = coerce(fields, output or {}, self._choice_ctx(fields, ctxview["context"], run))
            except FieldError as exc:
                raise Invalid("the answer has problems", exc.problems) from None
            guard = None
            if hub.pack:
                block = self.block_of(run, step)
                if block:  # a block's own guard; the extended skill's guard only while its answer has that shape
                    guard = hub.pack.guards.get(f"block:{block.id}")
                    if not guard and not block.outputs and block.extends:
                        guard = hub.pack.guards.get(block.extends)
                elif step.skill:
                    guard = hub.pack.guards.get(step.skill)
            if guard:
                problems = guard(values, ctxview) or []
                if problems:
                    raise Invalid("the answer does not pass the step's checks", problems)
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
            if outcome not in step.outcomes:
                raise Invalid(f"the decision is one of: {', '.join(step.outcomes)}")
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
        # the run's own item waits for its next step; a child (or an item of a run without one) is done
        walks = bool(run.get("item")) and item["id"] == run.get("item")
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
                  **({"signature": seq} if signed else {"signature": signature} if signature else {})}
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

    # ------------------------------------------------------------------ upgrade: one item per run
    def fold_run_items(self) -> list[str]:
        """Runs still open from before one item walked its workflow (an item per step) get their one item: the
        earliest item keeps its key and gets every item's step history; the one step open on its own moves onto
        it, and steps open side by side become its child items (keeping their keys). Items folded in are marked
        ``folded`` (``folded_into`` the kept one), so a link to one still finds the work. Safe to run again: a run
        that has been looked at carries ``item`` (None when its workflow has no step for a person or a wait)."""
        hub = self.hub
        done = []
        for r in hub.db.runs.find({"status": "running", "item": {"$exists": False}}, sort=[("created_at", 1)]):
            run = _plain(r)
            try:
                with hub.db:
                    self._fold(run)
                done.append(run["id"])
            except Exception as exc:  # noqa: BLE001 - one run that cannot be folded never stops the hub opening
                hub.db.runs.set(run["id"], {"item": None})
                hub.track.append({"id": "hub", "kind": "automation", "name": "Practice Hub"}, "run.fold_failed",
                                 run["id"], run_id=run["id"], data={"error": str(exc)})
        return done

    def _fold(self, run: dict) -> None:
        hub = self.hub
        rows = hub.db.items.find({"run_id": run["id"], "kind": {"$nin": ["notice", "hold"]},
                                  "status": {"$ne": "folded"}}, sort="n")
        try:
            wf, _ = hub.workflow(run["workflow_id"], run["version"])
        except Exception:  # noqa: BLE001
            wf = None
        if not rows:
            pid = self._run_item(run["id"], wf, run["title"], run.get("root_event")) if wf else None
            hub.db.runs.set(run["id"], {"item": pid})
            if pid:
                self._place(run["id"], run.get("root_event"))
            return
        keep = rows[0]
        live = [x for x in rows if x["status"] in OPEN_WORK]

        def entry(x) -> dict:
            closed = x["status"] in ("done", "cancelled")
            title = x.get("step_title") or x["title"]
            kind = x["kind"]
            try:
                st = wf.step(x["step_id"]) if wf else None
                title, kind = st.title, st.kind
            except KeyError:
                pass
            return {"step": x["step_id"], "title": title, "kind": kind, "role": x.get("role"),
                    "assignee": x.get("assignee"), "started_at": x["created_at"], "taken_at": None,
                    "done_at": x["updated_at"] if closed else None, "by": x.get("claimed_by") if closed else None,
                    "outcome": ((x.get("output") or {}).get("outcome") or "done") if x["status"] == "done"
                    else ("cancelled" if closed else None), "due_at": x.get("due_at"),
                    "escalated": int(x.get("escalated") or 0), "item": x["_id"]}

        for x in rows:  # when each was first taken, from the track
            ev = hub.db.events.find_one({"subject": f"item:{x['_id']}", "action": "item.claimed"}, sort="seq",
                                        fields=["at"])
            x["_taken"] = ev["at"] if ev else None
        history = []
        for x in rows:
            if x["status"] in OPEN_WORK and x is not keep and len(live) > 1:
                continue  # stays a child item, with its own history
            history.append({**entry(x), "taken_at": x["_taken"]})
        carried = keep if keep["status"] in OPEN_WORK else (live[0] if len(live) == 1 else None)
        children = [x for x in live if x is not carried]
        fields = {"title": run["title"], "parent_id": None, "history": history,
                  "children": [x["_id"] for x in children], "wave": [x["_id"] for x in children],
                  "updated_at": utcnow()}
        if carried is not None:
            st = None
            try:
                st = wf.step(carried["step_id"]) if wf else None
            except KeyError:
                pass
            for h in history:
                if h["item"] == carried["_id"]:
                    h["item"] = keep["_id"]
            fields.update({k: carried.get(k) for k in ("step_id", "kind", "role", "assignee", "persons_only", "status",
                                                       "claimed_by", "output", "note", "cause", "due_at", "escalated",
                                                       "escalate_next", "priority")},
                          step_title=st.title if st else carried["title"], step_started_at=carried["created_at"],
                          waiting="")
        else:
            fields.update(status="waiting", kind="wait", step_id="", step_title="", claimed_by=None, role=None,
                          assignee=None, due_at=None)
        hub.db.items.set(keep["_id"], fields)
        steps = run["steps"]
        for x in rows:
            if x is keep:
                continue
            if x in children:
                hub.db.items.set(x["_id"], {"parent_id": keep["_id"], "step_title": x.get("step_title") or x["title"],
                                            "title": run["title"], "step_started_at": x["created_at"],
                                            "history": [{**entry(x), "taken_at": x["_taken"]}]})
                continue
            hub.db.items.set(x["_id"], {"status": "folded", "folded_into": keep["_id"], "updated_at": utcnow()})
            hub.db.items.update({"kind": "hold", "output.holds": x["_id"]}, {"$set": {"output.holds": keep["_id"]}},
                                many=True)
            for sid, st in steps.items():
                if st.get("item") == x["_id"]:
                    st["item"] = keep["_id"]
        hub.db.runs.set(run["id"], {"item": keep["_id"], "steps": steps})
        hub.track.append({"id": "hub", "kind": "automation", "name": "Practice Hub"}, "run.items_folded",
                         f"item:{keep['_id']}", run_id=run["id"],
                         data={"kept": keep["_id"], "folded": [x["_id"] for x in rows if x is not keep
                                                               and x not in children],
                               "children": [x["_id"] for x in children]})
        if carried is None and wf is not None:
            self._show_waiting(self.run(run["id"]), wf, keep["_id"])

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
        seq = self.hub.track.append(actor, "event.received", record or "", data={"type": event_type, **(data or {})})
        started = []
        rec = self.hub.records.read(*record.split("/", 1)) if record and "/" in record else None
        for w in self.hub.workflows():
            wf = w["workflow"]
            if self._starts_on(wf, event_type, {"event": data or {}, "record": rec or {}}):
                started.append(self.start(actor, wf.id, record=record, inputs=data or {}, cause=seq))
        if not started:
            self._unhandled(actor, event_type, record, seq)
        return started

    def _unhandled(self, actor: dict, event_type: str, record: str | None, cause: int) -> str:
        """An event that no workflow took (none listens for it, or none's ``when`` held) is not dropped: the track
        records it and the escalation role gets an urgent notice, on a run of its own so the notice has a home. Only
        the event's type is kept on the run; what it carried stays in the track."""
        hub = self.hub
        with hub.db:
            seq = hub.track.append(actor, "event.unhandled", record or "", cause=cause, data={"type": event_type})
            run_id = _new_id("run")
            run = {"id": run_id}
            hub.db.runs.insert({"_id": run_id, "id": run_id, "workflow_id": UNHANDLED_WORKFLOW, "version": 0,
                                "title": f"An event no workflow took: {event_type}", "record": record,
                                "status": "completed", "ctx": {"event": {"type": event_type}},
                                "steps": {"look": {"state": "skipped"}}, "started_by": actor["id"],
                                "root_event": cause, "created_at": utcnow(), "updated_at": utcnow()})
            self.alert(run, None, f"Urgent: {actor.get('name') or actor['id']} sent the event \u201c{event_type}\u201d "
                                  f"and no workflow took it. Check the sender, or the workflow meant to listen for it.",
                       role=hub.practice.get("escalation_role"), cause=seq, priority="urgent")
        return run_id

    def on_record(self, change: str, rt, rec: dict, before: dict | None, actor: dict, cause: int) -> list[dict]:
        """A record was created, updated or moved through its lifecycle: start the workflows listening for
        ``record.<change>:<Type>`` whose ``trigger.when`` holds. The record change is the cause; the workflow was
        approved to start this way, so no starter role is needed. A run never restarts its own workflow."""
        ref = f"{rt.id}/{rec['id']}"
        events = {f"record.{change}:{rt.id}"}
        if change == "transitioned":
            events.add(f"record.updated:{rt.id}")
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
                started.append(self.start(starter, wf.id, record=ref if wf.record == rt.id else None, inputs=inputs,
                                          cause=cause, via_automation=True))
            except Exception as exc:  # noqa: BLE001 - a trigger that cannot start is reported, not fatal to the write
                self.hub.track.append(self.automation_actor_for(wf), "trigger.failed", ref, cause=cause,
                                      data={"workflow": wf.id, "event": wf.event, "error": str(exc)})
        self.recheck_waits(cause)
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
        return out

    def _tick(self, now: datetime, now_utc: datetime, stamp: str) -> dict:
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
                for rec in hub.records.search(wf.for_each, limit=None):
                    if wf.where and not expr.holds(wf.where, {"record": rec, "run": {"today": now.date().isoformat()}}):
                        continue
                    ref = f"{wf.for_each}/{rec['id']}"
                    try:
                        started.append(self.start(actor, wf.id, record=ref if wf.record == wf.for_each else None,
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
            wf, _ = hub.workflow(run["workflow_id"], run["version"])
            step = wf.step(item["step_id"])
            late = item["claimed_by"] or item["assignee"]
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
                # never only to the one who is late: to whoever else holds the escalation role, else the practice's
                self.alert(run, step, text, role=step.escalate_to, cause=seq, priority=item.get("priority"),
                           besides=late)
                if level >= 1:  # the second time and after: the practice's escalation role and owners hear too
                    self.alert(run, step, text, role=hub.practice.get("escalation_role"), cause=seq,
                               priority="urgent", besides=late, owners_too=True)
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
