"""What a member may see of a run and of the track.

Records are guarded per data class, but a workflow copies record data into its run: a draft note in a step's output,
a transcript in a run's input, lab values in an event. So step data carries the data classes its step read:

* a task or gate step carries the classes in its ``context``, plus those of the step it reviews (transitively);
* an automation, a run's input, and an event that starts workflows carry every class the workflow reads.

A member sees step data only if they may read every class it carries. Everything else about the run (who did what,
when, because of what) stays visible: that is the audit, and it holds no record content. Signatures stay visible.
"""

from __future__ import annotations

from .workflows import Step, Workflow


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
    """One member's view; caches workflow lookups for a page's worth of events."""

    def __init__(self, hub, member: dict):
        self.hub, self.me = hub, member
        self._wf: dict = {}
        self._runs: dict = {}
        self._owner: bool | None = None

    def may_read_classes(self, classes: set[str]) -> bool:
        return all(self.hub.can(self.me, "reader", f"data_class:{c}") for c in classes)

    def missing(self, classes: set[str]) -> list[str]:
        return sorted(c for c in classes if not self.hub.can(self.me, "reader", f"data_class:{c}"))

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
            classes = set()
            for w in self.hub.workflows():
                if w["workflow"].event == data.get("type"):
                    classes |= workflow_classes(w["workflow"])
            if not self.may_read_classes(classes):
                hidden = self.missing(classes)
        elif action in ("record.created", "record.updated"):
            rtype = (ev.get("subject") or "").split("/")[0]
            dc = self.hub.class_or_none(rtype) if rtype else None
            if dc and not self.may_read_classes({dc}):
                hidden = [dc]
        elif action.startswith("outbox.") and data.get("classes"):  # a connector's reason, a by-hand note
            classes = set(data["classes"])
            if not self.may_read_classes(classes) and any(k in data for k in ("note", "reason", "reference")):
                hidden = self.missing(classes)
        if hidden is None:
            return ev
        keep = {k: data[k] for k in ("step", "action", "workflow", "version", "title", "type", "signed", "message",
                                     "channel", "how", "status") if k in data}
        return {**ev, "data": {**keep, "hidden": f"content needs access to: {', '.join(hidden)}"}}

    ACCESS_EVENTS = frozenset({"record.viewed", "records.exported", "access.reported"})

    def events(self, evs: list[dict]) -> list[dict]:
        """What the member may see of these events. Who opened which file is itself personal health information:
        only a policy owner (the privacy officer) sees views and access reports, wherever events are listed."""
        if self._owner is None:
            self._owner = bool(self.me) and self.hub.is_policy_owner(self.me)
        return [self.event(e) for e in evs if self._owner or e["action"] not in self.ACCESS_EVENTS]
