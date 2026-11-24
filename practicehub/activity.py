"""What each move of a record set going: the automations on the edge it took, their outcomes, and the records they
started.

Nothing stores a move as such. A workflow written as states and edges compiles each edge's automations to steps
with ``on_edge`` and ends the edge with an arrive step (``states.ARRIVE``); within one run each step runs once, so
``(run, edge)`` names a move. ``Hub.record_activity`` reads the moves of every run about a record, oldest first, from
the runs' pinned workflow versions, their step states and outputs, the track and the outbox. What the viewer may not
see is marked, never given: a hidden automation carries no title, error or started records; a started record of a
type they may not read is ``readable`` False (the page then names and links nothing).
"""
from __future__ import annotations

from . import visibility
from .states import ARRIVE, FAILED
from .store import ASC

# the worst of several messages first: one not delivered, then one still waiting, ...
_MESSAGE_ORDER = {"failed": 0, "queued": 1, "sending": 2, "sent": 3, "delivered": 4}
_UNSENT = ("failed", "queued", "sending")


class ActivityMixin:
    def record_activity(self, viewer: dict, ref: str) -> list[dict]:
        """Every move of ``ref`` (over runs about it, of any status), oldest first, each with what it ran::

            {"run", "edge", "label", "to", "seq", "at", "who", "after_person",
             "ran": [{"step", "title", "state", "at", "error", "hidden", "started", "message"}],
             "settled"}

        ``state`` is done, failed, or held (pending behind a failed one, while the run still runs). ``who`` is the
        actor id of the event that set the move going (a decision, a completion, ``run.started`` for the start edge,
        an automation's for an automated state). ``after_person`` False: the move followed on from the one before (an
        automated state's edge); a cascade is a move with it True and the moves after it with it False. A run whose
        workflow version cannot be read gives no moves."""
        rtype = ref.split("/", 1)[0]
        self.require(viewer, "reader", f"data_class:{self.class_of(rtype)}", f"read {rtype} records")
        view = visibility.Viewer(self, viewer)
        runs = self.db.runs.find({"record": ref})
        if not runs:
            return []
        evs: dict = {}  # (run id, step id) -> its action.ran / step.failed events, oldest first
        for e in self.db.events.find({"run_id": {"$in": [r["id"] for r in runs]},
                                      "action": {"$in": ["action.ran", "step.failed"]}}, sort=[("seq", ASC)]):
            sid = (e.get("data") or {}).get("step")
            if isinstance(sid, str):
                evs.setdefault((e["run_id"], sid), []).append(e)
        moves: list[dict] = []
        for run in runs:
            moves += self._run_moves(viewer, view, run, evs)
        moves.sort(key=lambda m: m["seq"])
        return moves

    def _run_moves(self, viewer, view, run: dict, evs: dict) -> list[dict]:
        wf = view.workflow_of_run(run["id"])
        if wf is None:
            return []
        shape = wf.shape
        automated = {s["id"]: bool(s.get("automated")) for s in shape.get("states") or []}
        steps = run.get("steps") or {}
        running = run.get("status") == "running"
        withheld = bool(view.run_withheld(run))
        out = []
        for edge in [shape.get("start"), *(shape.get("edges") or [])]:
            if not edge:
                continue
            own = [s for s in edge.get("steps") or [] if _has(wf, s)]
            fails = [FAILED.format(step=s) for s in own if _has(wf, FAILED.format(step=s))]
            arrive = ARRIVE.format(edge=edge["id"])
            every = own + fails + ([arrive] if _has(wf, arrive) else [])
            if not any((steps.get(s) or {}).get("state") in ("done", "failed") for s in every):
                continue  # not taken
            first = min((evs[(run["id"], s)][0] for s in every if (run["id"], s) in evs),
                        key=lambda e: e["seq"], default=None)
            start = self._set_going(run, first)
            ran, settled = [], True
            for sid in every:
                st = (steps.get(sid) or {}).get("state")
                mine = evs.get((run["id"], sid)) or []
                if sid not in own and st != "failed":
                    continue  # the move itself (arriving, or the on_fail move), not something it ran
                if st == "done":
                    state = "done"
                elif st == "failed":
                    state = "failed"
                elif st == "pending" and running:
                    state = "held"
                else:
                    continue  # skipped (its own when was false), or never to run (the work was cancelled)
                if state != "done":
                    settled = False
                output = (run.get("ctx") or {}).get(sid)
                started = self._started_by_step(viewer, run, output, _action(wf, sid))
                message = self._message_status(output)
                if message in _UNSENT or any(s["status"] == "running" for s in started):
                    settled = False
                ok = view.step_visible(wf, sid)[0]
                try:
                    title = wf.step(sid).title or sid
                except KeyError:
                    title = sid
                error = (steps.get(sid) or {}).get("error") if state == "failed" else None
                ran.append({"step": sid, "title": title if ok else "", "state": state,
                            "at": mine[-1]["at"] if mine else None,
                            "error": error if ok and not withheld else None, "hidden": not ok,
                            "started": [{k: s[k] for k in ("ref", "run", "readable")} for s in started] if ok else [],
                            "message": message if ok else None})
            out.append({"run": run["id"], "edge": edge["id"], "label": edge.get("label") or edge["id"],
                        "to": self._landed(wf, run, every, evs) or edge.get("to"),
                        "seq": start["seq"] if start else 0, "at": start["at"] if start else None,
                        "who": start["actor"] if start else None,
                        "after_person": not edge.get("from") or not automated.get(edge["from"], False),
                        "ran": ran, "settled": settled})
        return out

    def _set_going(self, run: dict, first: dict | None) -> dict | None:
        """The event that set a move going: what caused its first event (the decision or completion that chose the
        edge, ``run.started`` for the start edge), else that first event itself."""
        if first is None:
            return None
        cause = self.db.events.find_one({"seq": first["cause"]}) if first.get("cause") is not None else None
        if cause is not None and cause.get("run_id") == run["id"]:
            return cause
        return first

    @staticmethod
    def _landed(wf, run: dict, every: list[str], evs: dict) -> str | None:
        """Where the move put the record: the last of its steps that moves it (a diversion, the on_fail move, the
        arrive step) to have been done."""
        best, at = None, -1
        for sid in every:
            try:
                step = wf.step(sid)
            except KeyError:
                continue
            if step.state and (run.get("steps", {}).get(sid) or {}).get("state") == "done":
                seq = max((e["seq"] for e in evs.get((run["id"], sid)) or []), default=0)
                if seq >= at:
                    best, at = step.state, seq
        return best

    def _started_by_step(self, viewer, run: dict, output, action: str | None = None) -> list[dict]:
        """The records an automation started, from its output: a run it started (``run_id``) and the record it
        walks, or a record it made (``reference``, made by this run: not one it only updated) and the run walking
        it. Inside ``each`` for an automation done once per item. Each ``{ref, run, readable, status}``."""
        if not isinstance(output, dict):
            return []
        outs = output.get("each") if isinstance(output.get("each"), list) else [output]
        found: list[dict] = []
        seen: set = set()
        for o in outs:
            if not isinstance(o, dict):
                continue
            row = None
            ref = None
            if o.get("run_id"):
                row = self.db.runs.get(o["run_id"])
                if row is None:
                    continue
                ref = row.get("record")
            elif action != "record.update" and isinstance(o.get("reference"), str) and "/" in o["reference"] \
                    and o["reference"] != run.get("record") and self.db.events.exists(
                    {"action": "record.created", "subject": o["reference"], "run_id": run["id"]}):
                ref = o["reference"]
                row = self.db.runs.find_one({"record": ref, "ctx.run.parent": run["id"]}, sort="created_at") \
                    or self.db.runs.find_one({"record": ref}, sort="created_at")
            else:
                continue
            key = ref or (row or {}).get("id")
            if key in seen:
                continue
            seen.add(key)
            readable = bool(ref) and "/" in ref and self._readable_type(viewer, ref.split("/", 1)[0])
            found.append({"ref": ref, "run": (row or {}).get("id"), "readable": readable,
                          "status": (row or {}).get("status")})
        return found

    def _readable_type(self, viewer, rtype: str) -> bool:
        try:
            return bool(self.may_read(viewer, rtype))
        except Exception:  # noqa: BLE001 - a type no longer installed is not one they may read
            return False

    def _message_status(self, output) -> str | None:
        """The status of the messages an automation queued (``message_id``, ``message_ids``), the worst of them."""
        if not isinstance(output, dict):
            return None
        outs = output.get("each") if isinstance(output.get("each"), list) else [output]
        ids: list = []
        for o in outs:
            if isinstance(o, dict):
                ids += [o.get("message_id"), *(o.get("message_ids") or [])]
        worst = None
        for oid in dict.fromkeys(i for i in ids if i is not None):
            row = self.db.outbox.get(oid)
            if row is None:
                continue
            st = "failed" if row.get("status") == "refused" else row.get("status")
            if st in _MESSAGE_ORDER and (worst is None or _MESSAGE_ORDER[st] < _MESSAGE_ORDER[worst]):
                worst = st
        return worst


def _action(wf, sid: str) -> str | None:
    try:
        return getattr(wf.step(sid), "action", None)
    except KeyError:
        return None


def _has(wf, sid: str) -> bool:
    try:
        wf.step(sid)
        return True
    except KeyError:
        return False
