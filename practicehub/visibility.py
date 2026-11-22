"""What a member may see of a run and of the track.

Records are guarded per data class, but a workflow copies record data into its run: a draft note in a step's output,
a transcript in a run's input, lab values in an event. So step data carries the data classes its step read:

* a task or gate step carries the classes in its ``context``, plus those of the step it reviews (transitively);
* an automation, a run's input, and an event that starts workflows carry every class the workflow reads.

A member sees step data only if they may read every class it carries. Everything else about the run (who did what,
when, because of what) stays visible: that is the audit, and it holds no record content. Signatures stay visible.

A field may have a data class of its own (``data_class`` on the field: a visit's reason is clinical, its time is
not). The type's class says who sees the record exists, lists it and creates it; the field's class says who sees
and sets that value. Every path that hands a record to a member goes through ``redact`` (the hub's reads, searches,
history, chart; the pages, lists, calendar, exports, MCP, the API, an agent's context): a field the member may not
read is left out, never blanked, so nothing says it holds a value. ``guard_write`` refuses a member who sets or
changes a field they may not write, and keeps its stored value when their write leaves it out. A record's title and
facts never use such a field, for anyone (titles travel: notices, pickers, the track).
"""

from __future__ import annotations

import copy

from .workflows import Step, Workflow


# ------------------------------------------------------------------------------------------------ fields
def _sees_all(member: dict | None) -> bool:
    return bool(member) and member.get("kind") == "automation"  # the hub itself, a workflow step, an import


def hidden_fields(hub, member: dict | None, rt, *, write: bool = False) -> list[dict]:
    """The fields of ``rt`` whose own data class the member may not read (or, with ``write``, write)."""
    if rt is None or not rt.classed_fields or _sees_all(member):
        return []
    rel = "writer" if write else "reader"
    ok: dict[str, bool] = {}
    out = []
    for f in rt.classed_fields:
        dc = f["data_class"]
        if dc not in ok:
            ok[dc] = bool(member) and hub.can(member, rel, f"data_class:{dc}")
        if not ok[dc]:
            out.append(f)
    return out


def may_field(hub, member: dict | None, f: dict, *, write: bool = False) -> bool:
    """Whether the member may read (``write``: write) this field, by its own data class (a field with none is the
    record's: that is asked of the record as a whole)."""
    dc = f.get("data_class")
    return not dc or _sees_all(member) or (bool(member) and hub.can(member, "writer" if write else "reader",
                                                                    f"data_class:{dc}"))


def _drop(value, parts: list[str]):
    """``value`` without what is at ``parts``; a list or object emptied by it goes too (None: nothing is left)."""
    if not parts:
        return None
    head, rest = parts[0], parts[1:]
    if isinstance(value, dict):
        if head not in value:
            return value
        out = dict(value)
        sub = _drop(out[head], rest)
        if sub is None:
            out.pop(head)
        else:
            out[head] = sub
        return out or None
    if isinstance(value, list):
        if head == "*":
            idx = list(range(len(value)))
        elif head.isdigit() and int(head) < len(value):
            idx = [int(head)]
        else:
            return value
        out = list(value)
        for i in idx:
            out[i] = _drop(out[i], rest) if rest else None
            if out[i] is None and rest:
                out[i] = {}  # an emptied entry keeps its place: the next one stays where it is
        if not rest:
            out = [x for i, x in enumerate(out) if i not in idx]
        return None if all(x in ({}, [], None) for x in out) else out
    return value


def strip(rec: dict, fields: list[dict]) -> dict:
    """A copy of ``rec`` without these fields' values."""
    out = rec
    for f in fields:
        top = f["path"].split(".")[0]
        if top in out:
            out = dict(out)
            sub = _drop(out[top], f["path"].split(".")[1:])
            if sub is None:
                out.pop(top)
            else:
                out[top] = sub
    return out


def strip_classed(hub, rec: dict | None) -> dict | None:
    """``rec`` without every field that has a data class of its own: what is safe to name it by, for anyone."""
    if not rec:
        return rec
    rt = hub.record_type_for_reading(rec.get("resourceType") or "")
    return strip(rec, rt.classed_fields) if rt is not None and rt.classed_fields else rec


def redact(hub, member: dict | None, rec: dict | None) -> dict | None:
    """What the member may see of a record: without the fields whose data class they may not read."""
    if not rec or not isinstance(rec, dict):
        return rec
    rt = hub.record_type_for_reading(rec.get("resourceType") or "")
    hidden = hidden_fields(hub, member, rt)
    return strip(rec, hidden) if hidden else rec


def shown(hub, member: dict | None, rt, fields: list[dict]) -> list[dict]:
    """Of these fields, those the member may read: a column or a row of a field they may not read is left out, never
    shown empty (an empty cell would say nothing is there, or that something is)."""
    hidden = hidden_fields(hub, member, rt)
    return [f for f in fields if f not in hidden] if hidden else list(fields)


def hidden_keys(hub, member: dict | None, rtype: str, *, write: bool = False) -> set[str]:
    """The top-level keys of ``rtype`` the member sees nothing of (each field there is hidden from them); ``write``:
    those they may not write."""
    rt = hub.record_type_for_reading(rtype)
    hidden = hidden_fields(hub, member, rt, write=write)
    if not hidden:
        return set()
    seen = {f["path"].split(".")[0] for f in rt.fields if f not in hidden}
    return {f["path"].split(".")[0] for f in hidden} - seen | {
        f["path"] for f in hidden if "." not in f["path"]}


def hides_path(hub, member: dict | None, rtype: str, path: str) -> dict | None:
    """The hidden field a filter or sort on ``path`` would look into, if any."""
    rt = hub.record_type_for_reading(rtype)
    for f in hidden_fields(hub, member, rt):
        if path == f["path"] or _inside(path, f["path"]) or _inside(f["path"], path):
            return f
    return None


def _inside(path: str, outer: str) -> bool:
    a, b = path.split("."), outer.split(".")
    return len(a) > len(b) and all(x == y or "*" in (x, y) for x, y in zip(a, b))


def _at(rec, path: str):
    from .schema import get
    return get(rec, path)


def guard_write(hub, member: dict, rt, before: dict | None, after: dict) -> dict:
    """``after`` as it may be written by the member: a field they may not write keeps its stored value when they
    left it out, and is refused (Denied, naming it) when they set or changed it."""
    from .hub import Denied
    hidden = hidden_fields(hub, member, rt, write=True)
    if not hidden:
        return after
    before = before or {}
    for f in hidden:
        old, new = _at(before, f["path"]), _at(after, f["path"])
        if new == old:
            continue
        if not [v for v in new if v not in (None, "", [], {})]:  # left out (or emptied by a view without it)
            after = _graft(after, before, f["path"].split("."))
            continue
        raise Denied(f"{member.get('name') or member.get('id')} may not set {rt.label.lower()} {f['label']} "
                     f"(it is {f['data_class']})")
    return after


def _graft(after, before, parts: list[str]):
    """``after`` with ``before``'s value at ``parts`` put back (and what holds it, where ``after`` has none)."""
    if not parts or not isinstance(before, (dict, list)):
        return copy.deepcopy(before)
    head, rest = parts[0], parts[1:]
    if isinstance(before, dict):
        if head not in before:
            return after
        out = dict(after) if isinstance(after, dict) else {}
        out[head] = _graft(out.get(head), before[head], rest) if head in out else copy.deepcopy(before[head])
        return out
    if not isinstance(after, list):
        return copy.deepcopy(before)
    out = list(after)
    idx = range(len(before)) if head == "*" else [int(head)] if head.isdigit() else []
    for i in idx:
        if i >= len(before):
            continue
        while len(out) <= i:
            out.append({})
        out[i] = _graft(out[i], before[i], rest)
    return out


def step_classes(wf: Workflow, step: Step, _seen: set | None = None) -> set[str]:
    seen = _seen if _seen is not None else set()
    if step.id in seen:
        return set()
    seen.add(step.id)
    if step.kind == "automation":
        return workflow_classes(wf)
    out = {c[7:] for c in step.context if c.startswith("record:")}
    if step.review_of:
        try:
            out |= step_classes(wf, wf.step(step.review_of), seen)
        except KeyError:
            pass
    return out


def workflow_classes(wf: Workflow) -> set[str]:
    return {c[7:] for s in wf.steps for c in s.context if c.startswith("record:")}


class Viewer:
    """One member's view; caches workflow lookups, and what the member may read, for a page's worth of events (a
    Viewer lives for one page or call, so a grant changed meanwhile applies from the next)."""

    def __init__(self, hub, member: dict):
        self.hub, self.me = hub, member
        self._wf: dict = {}
        self._runs: dict = {}
        self._owner: bool | None = None
        self._reads: dict[str, bool] = {}
        self._gone: dict[str, set] = {}
        self._by_event: dict[str, set] = {}

    def reads(self, dc: str) -> bool:
        if dc not in self._reads:
            self._reads[dc] = bool(self.hub.can(self.me, "reader", f"data_class:{dc}"))
        return self._reads[dc]

    def may_read_classes(self, classes: set[str]) -> bool:
        return all(self.reads(c) for c in classes)

    def missing(self, classes: set[str]) -> list[str]:
        return sorted(c for c in classes if not self.reads(c))

    def _hidden_keys(self, rtype: str) -> set:
        if rtype not in self._gone:
            self._gone[rtype] = hidden_keys(self.hub, self.me, rtype)
        return self._gone[rtype]

    def _event_classes(self, etype) -> set:
        if etype not in self._by_event:
            classes: set = set()
            for w in self.hub.workflows():
                if w["workflow"].event == etype:
                    classes |= workflow_classes(w["workflow"])
            self._by_event[etype] = classes
        return self._by_event[etype]

    def workflow_of_run(self, run_id: str) -> Workflow | None:
        if run_id not in self._runs:
            try:
                run = self.hub.engine.run(run_id)
                key = (run["workflow_id"], run["version"])
                if key not in self._wf:
                    self._wf[key] = self.hub.workflow(*key)[0]
                self._runs[run_id] = self._wf[key]
            except Exception:
                self._runs[run_id] = None
        return self._runs[run_id]

    def step_visible(self, wf: Workflow, step_id: str) -> tuple[bool, list[str]]:
        try:
            step = wf.step(step_id)
        except KeyError:
            return False, ["unknown step"]
        classes = step_classes(wf, step)
        return self.may_read_classes(classes), self.missing(classes)

    def event(self, ev: dict) -> dict:
        """The event with record content replaced by a note when the member may not read it."""
        data, action = ev.get("data") or {}, ev["action"]
        hidden: list[str] | None = None
        wf = self.workflow_of_run(ev["run_id"]) if ev.get("run_id") else None
        if action in ("item.completed", "gate.decided", "action.ran", "step.failed", "item.held") and wf and data.get("step"):
            ok, missing = self.step_visible(wf, data["step"])
            if not ok:
                hidden = missing
        elif action == "run.started" and wf:
            classes = workflow_classes(wf)
            if not self.may_read_classes(classes) and data.get("input"):
                hidden = self.missing(classes)
        elif action == "event.received":
            classes = self._event_classes(data.get("type"))
            if not self.may_read_classes(classes):
                hidden = self.missing(classes)
        elif action in ("record.created", "record.updated"):
            rtype = (ev.get("subject") or "").split("/")[0]
            dc = self.hub.class_or_none(rtype) if rtype else None
            if dc and not self.may_read_classes({dc}):
                hidden = [dc]
            elif rtype and data.get("changed"):  # a change to a field they may not read is not theirs to know of
                gone = self._hidden_keys(rtype)
                if gone and set(data["changed"]) & gone:
                    data = {**data, "changed": [k for k in data["changed"] if k not in gone]}
                    ev = {**ev, "data": data}
        elif action == "booking.overbooked" and data.get("classes"):  # why it was booked over another: its field's class
            classes = set(data["classes"])
            if not self.may_read_classes(classes) and "reason" in data:
                hidden = self.missing(classes)
        elif action.startswith("outbox.") and data.get("classes"):  # a connector's reason, a by-hand note
            classes = set(data["classes"])
            if not self.may_read_classes(classes) and any(k in data for k in ("note", "reason", "reference")):
                hidden = self.missing(classes)
        if hidden is None:
            return ev
        keep = {k: data[k] for k in ("step", "action", "workflow", "version", "title", "type", "signed", "message",
                                     "channel", "how", "status", "with", "over") if k in data}
        return {**ev, "data": {**keep, "hidden": f"content needs access to: {', '.join(hidden)}"}}

    ACCESS_EVENTS = frozenset({"record.viewed", "records.exported", "records.listed", "access.reported"})

    def events(self, evs: list[dict]) -> list[dict]:
        """What the member may see of these events. Who opened which file is itself personal health information:
        only a policy owner (the privacy officer) sees views and access reports, wherever events are listed."""
        if self._owner is None:
            self._owner = bool(self.me) and self.hub.is_policy_owner(self.me)
        return [self.event(e) for e in evs if self._owner or e["action"] not in self.ACCESS_EVENTS]
