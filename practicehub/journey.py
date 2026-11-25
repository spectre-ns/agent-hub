"""A record's journey: the stages it was in, the work done in each, and the moves between them with what they ran.

``Hub.record_timeline`` (moves.py) tells a record's history as one list; this tells the same history nested, as the
record's page shows it. A **stage** is a stretch the record spent in one state (a value of its state field, or a stop
only its workflow keeps). In a stage are its **lines**: the work done there (a person's task, a decision, a wait, an
automated state's own work), each with who did it and whether that was a person, an agent or the workflow; the
record's edits; and what its work began or ended. Out of a stage lead its **moves**: the one that ended it, and any
that were tried and did not go through. A move made along a workflow's edge carries the edge's **steps** (its
automations, each done, failed, held behind a failed one, in the queue or under way), how many of its automations
were not needed, and the other ways out that were not taken. A move made by hand that the workflow then followed
carries what followed (``then``).

Nothing stores a journey. The record's own writes come from ``MovesMixin._record_changes``; the rest is read here
from the runs about the record, their pinned workflow versions (``shape``, practicehub/states.py), their step states
and the track. A step belongs to a move by the edge it was written on, never by when it ran: an edge's automations
may run after the move they belong to, and a wait ends after the move that ended it.

What the viewer may not see is counted, never told: a hidden piece of work is a fold with its data classes; a move
along an edge whose choice they may not know (``Viewer.edge_visible``) keeps where the record went, which is the
record's own history, without the edge's name or the ways out it was chosen over.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from . import schema, visibility
from .activity import _action, _has
from .states import ARRIVE, FAILED
from .store import ASC

_STEP_ACTIONS = ["run.started", "run.completed", "run.cancelled", "run.stopped", "step.activated", "step.done",
                 "step.skipped", "step.waiting", "step.failed", "step.withdrawn", "step.timed_out", "step.retried",
                 "step.queued", "step.started", "item.claimed", "item.completed", "item.held", "item.released",
                 "item.escalated", "gate.decided", "action.ran"]
_OWN_ACTIONS = ["record.created", "record.updated", "record.transitioned", "record.signed"]
_GOING = ("queued", "running")
LIVE_FOR = 600  # seconds an agent's open work, or a message waiting to be sent, keeps the page asking for news


def _kind(actor_kind) -> str:
    return actor_kind if actor_kind in ("person", "agent", "service") else "automatic"


def _seconds(a, b) -> int | None:
    try:
        x, y = (datetime.fromisoformat(str(t).replace("Z", "+00:00")) for t in (a, b))
    except (TypeError, ValueError):
        return None
    x, y = (t if t.tzinfo else t.replace(tzinfo=timezone.utc) for t in (x, y))
    return max(0, int((y - x).total_seconds()))


class JourneyMixin:
    def record_journey_token(self, viewer: dict, ref: str) -> str:
        """What changes whenever the journey could: the last event of the runs about the record (their steps, their
        messages), the last write of the record itself (a view is not one) and who is asking. Cheap: nothing is
        built, so a page that asks again and again is answered from two index reads."""
        ids = [r["id"] for r in self.db.runs.find({"record": ref}, fields=["id"])]
        a = self.db.events.max("seq", {"run_id": {"$in": ids}}, default=0) if ids else 0
        b = self.db.events.max("seq", {"subject": ref, "action": {"$in": _OWN_ACTIONS}}, default=0)
        return hashlib.sha256(f"{viewer.get('id')}|{ref}|{a}|{b}".encode()).hexdigest()[:24]

    def record_journey(self, viewer: dict, ref: str) -> dict:
        """The record's journey as ``{"stages", "live", "busy", "token"}``, oldest stage first::

            stage = {"key", "kind": "lifecycle" | "stop" | "none", "state", "label", "tone", "at", "until",
                     "seconds", "now", "entered": {"how", "by", "label", "from"} | None, "by", "lines", "moves"}
            line  = {"kind": "work", "what": "task" | "decision" | "wait" | "automatic", "title", "status", "by",
                     "for", "at", "done_at", "seconds", "since", "chose", "signed", "approved_by", "via_move",
                     "error"}
                  | {"kind": "edit", "at", "by", "changed", "unread", "comment"}
                  | {"kind": "signature", "at", "by", "meaning"}
                  | {"kind": "note", "what": "began" | "finished" | "cancelled" | "stopped", "title", "by", "why",
                     "at", "again"}
                  | {"kind": "fold", "what": "step" | "change" | "not-needed", "count", "classes", "titles"}
            move  = {"how": "edge" | "hand" | "auto", "label", "edge", "to", "to_label", "status": "done" | "going" |
                     "failed" | "fell-back", "at", "by", "values", "unread", "signed", "comment", "steps",
                     "not_needed", "hidden", "not_taken", "then"}
            step  = {"title", "status": "queued" | "running" | "done" | "failed" | "held", "lands", "at", "ms",
                     "error", "retried", "message", "started"}

        ``by`` is ``{"id", "name", "kind"}`` (a member; ``kind``: person, agent, service or automatic). ``status`` of a piece of
        work: open (no one has it), taken (someone does: an agent's is being worked on now), held (waiting for a
        sponsor), waiting, queued, running, done, timed-out, failed, withdrawn or cancelled. Open work says where it
        is now, which is the item's to say and not the step's: ``for`` (the role it waits on), ``assigned`` (the one
        person, when it has one), ``people_only``, and ``passed`` (``{"by", "at", "up", "note"}``) when whoever last
        had it gave it back or, an agent that could not do it, passed it up to a person. ``live``: something is
        going on that will change this without anyone here doing anything (a job in the queue or under way, an agent
        with open work, a message on its way); ``busy``: a job is in the queue or under way."""
        rtype, _, rid = ref.partition("/")
        rt = self._type(rtype)
        self.require(viewer, "reader", f"data_class:{self.class_of(rtype)}", f"read {rtype} records")
        return _Journey(self, viewer, rt, ref).build()


class _Journey:
    def __init__(self, hub, viewer, rt, ref):
        self.hub, self.viewer, self.rt, self.ref = hub, viewer, rt, ref
        self.view = visibility.Viewer(hub, viewer)
        self.names: dict = {}
        self.lc = rt.lifecycle or None
        self.states = set(rt.states()) if self.lc else set()
        self.live = self.busy = False
        self.now = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------ small things
    def who(self, mid, kind=None) -> dict | None:
        if not mid:
            return None
        if kind is None:
            row = self.hub.db.members.get(mid)
            kind = (row or {}).get("kind") if row else "automation"
        return {"id": mid, "name": self.hub._who(self.names, mid), "kind": _kind(kind)}

    def label(self, state, wf=None) -> str:
        if state in self.states or wf is None:
            return self.rt.state_label(state)
        st = next((s for s in (wf.shape or {}).get("states") or [] if s["id"] == state), None)
        return (st or {}).get("label") or schema.state_words(state)

    def tone(self, state, wf=None) -> str | None:
        declared = ((self.lc or {}).get("tones") or {}).get(str(state))
        if declared or wf is None:
            return declared
        st = next((s for s in (getattr(wf, "shape", None) or {}).get("states") or [] if s["id"] == state), None)
        return (st or {}).get("tone")

    @staticmethod
    def step(wf, sid):
        try:
            return wf.step(sid)
        except KeyError:
            return None

    # ------------------------------------------------------------------ reading
    def build(self) -> dict:
        hub, ref = self.hub, self.ref
        self.changes = hub._record_changes(self.viewer, self.rt, ref)
        self.runs = sorted(hub.db.runs.find({"record": ref}), key=lambda r: (str(r.get("created_at") or ""), r["id"]))
        self.wfs = {r["id"]: self.view.workflow_of_run(r["id"]) for r in self.runs}
        self.events: list[dict] = []
        if self.runs:
            self.events = hub.db.events.find(
                {"run_id": {"$in": [r["id"] for r in self.runs]}, "action": {"$in": _STEP_ACTIONS}},
                sort=[("seq", ASC)],
                fields=["seq", "at", "actor", "actor_kind", "action", "cause", "run_id", "signature", "data.step",
                        "data.output.outcome", "data.error", "data.state", "data.reason", "data.why",
                        "data.approved_by", "data.attested", "data.failed", "data.ms", "data.how", "data.note"])
        self.by_seq = {e["seq"]: e for e in self.events}
        self.by_step: dict = {}
        for e in self.events:
            sid = (e.get("data") or {}).get("step")
            if isinstance(sid, str):
                self.by_step.setdefault((e["run_id"], sid), []).append(e)
        self.items = {i["id"]: i for i in hub.db.items.find(
            {"run_id": {"$in": [r["id"] for r in self.runs if r.get("status") == "running"]},
             "kind": {"$ne": "notice"}}, fields=["id", "run_id", "step_id", "status", "claimed_by", "updated_at", "role", "assignee",
                                              "persons_only"])} \
            if self.runs else {}
        insts = [i for run in self.runs for i in self.edges_taken(run)]
        bounds = self.boundaries(insts)
        stages = self.stages(bounds)
        self.place_moves(stages, bounds, insts)
        self.place_lines(stages, bounds)
        prev = None
        for n, st in enumerate(stages):
            st["key"] = f"s{n}"
            st["lines"] = self.fold([line for _, line in sorted(st.pop("_lines"), key=lambda x: x[0])])
            st.pop("_from", None)
            for m in st["moves"]:
                self.finish(m)
            st["by"] = self.brought(prev, st)
            prev = st
        return {"stages": stages, "live": self.live or self.busy, "busy": self.busy,
                "token": hub.record_journey_token(self.viewer, ref)}

    @staticmethod
    def brought(prev: dict | None, st: dict) -> dict | None:
        """Who or what put the record in this stage: whoever made it, for its first; else whoever made the move
        that took it there (``kind`` automatic when no one did: the workflow moved it). None when no move the
        viewer is told of led there."""
        if prev is None:
            return (st.get("entered") or {}).get("by")

        def flat(moves):
            for m in moves:
                yield m
                yield from flat(m.get("then") or [])
        went = [m for m in flat(prev["moves"]) if m["status"] != "failed"]
        took = [m for m in went if m.get("to") == st["state"]] or went
        if not took:
            return None
        return took[-1].get("by") or {"id": None, "name": "", "kind": "automatic"}

    def finish(self, m: dict) -> None:
        for k in [k for k in m if k.startswith("_")]:
            m.pop(k)
        for t in m.get("then") or []:
            self.finish(t)

    # ------------------------------------------------------------------ the edges a run took
    def edges_taken(self, run: dict) -> list[dict]:
        """Each edge the run took (or is taking), with its steps as they are: a move in the making."""
        wf = self.wfs.get(run["id"])
        shape = getattr(wf, "shape", None) or {}
        if not shape:
            return []
        hub, view = self.hub, self.view
        steps = run.get("steps") or {}
        running = run.get("status") == "running"
        withheld = bool(view.run_withheld(run))
        full = view.full(wf)
        out_of = {s["id"]: s.get("out") or [] for s in shape.get("states") or []}
        labels = {x["id"]: x.get("label") or x["id"] for x in shape.get("edges") or []}
        out = []
        for edge in [shape.get("start"), *(shape.get("edges") or [])]:
            if not edge:
                continue
            own = [s for s in edge.get("steps") or [] if _has(wf, s)]
            fails = [FAILED.format(step=s) for s in own if _has(wf, FAILED.format(step=s))]
            arrive = ARRIVE.format(edge=edge["id"])
            every = own + fails + ([arrive] if _has(wf, arrive) else [])
            state_of = lambda s: (steps.get(s) or {}).get("state")
            if not any(state_of(s) in ("done", "failed", *_GOING) for s in every):
                continue
            evs = sorted((e for s in every for e in self.by_step.get((run["id"], s)) or []), key=lambda e: e["seq"])
            first = evs[0] if evs else None
            going = first
            if first is not None and first.get("cause") in self.by_seq:
                going = self.by_seq[first["cause"]]  # the decision or completion that chose the edge
            seen = view.edge_visible(wf, edge["id"])
            psteps, hidden, not_needed, landed = [], 0, [], None
            failed = held = busy = fell = False
            for sid in every:
                st = state_of(sid)
                mine = self.by_step.get((run["id"], sid)) or []
                step = self.step(wf, sid)
                mover = sid not in own
                if st == "skipped":
                    if not mover:
                        not_needed.append(step.title or sid)
                    continue
                if st == "done":
                    status = "done"
                elif st == "failed":
                    status = "failed"
                elif st in _GOING:
                    status = st
                elif st == "pending" and running and not mover:
                    status = "held"
                elif st == "pending" and running and sid == arrive and any(state_of(s) == "failed" for s in own):
                    continue  # the move itself waits on what failed: the failed step says so
                else:
                    continue
                failed = failed or status == "failed"
                held = held or status == "held"
                busy = busy or status in _GOING
                fell = fell or (status == "done" and sid in fails)
                if step.state and status == "done":
                    seq = max((e["seq"] for e in mine), default=0)
                    if landed is None or seq >= landed[1]:
                        landed = (step.state, seq)
                if not view.step_visible(wf, sid)[0]:
                    hidden += 1
                    continue
                output = (run.get("ctx") or {}).get(sid)
                started = hub._started_by_step(self.viewer, run, output, _action(wf, sid))
                message = hub._message_status(output)
                last = mine[-1] if mine else None
                if message in ("queued", "sending") and last and (_seconds(last["at"], self.now) or 0) < LIVE_FOR:
                    self.live = True
                psteps.append({
                    "title": step.title or sid, "status": status, "lands": bool(step.state),
                    "at": last["at"] if last else None,
                    "ms": (steps.get(sid) or {}).get("ms"),
                    "since": (steps.get(sid) or {}).get("started_at") or (steps.get(sid) or {}).get("queued_at"),
                    "error": (steps.get(sid) or {}).get("error") if status == "failed" and not withheld else None,
                    "retried": [{"at": e["at"], "by": self.who(e["actor"], e.get("actor_kind")),
                                 "reason": (e.get("data") or {}).get("reason") or None}
                                for e in mine if e["action"] == "step.retried"],
                    "message": None if withheld else message,
                    "started": [] if withheld else [{k: s[k] for k in ("ref", "run", "readable")} for s in started]})
            if busy and running:
                self.busy = True
            # behind a failure nothing more will happen by itself; behind a job it will
            status = "failed" if failed else "going" if busy or held else "fell-back" if fell else "done"
            frm = edge.get("from")
            others = [labels[x] for x in out_of.get(frm, []) if x != edge["id"]] if frm else []
            out.append({
                "how": "edge", "label": edge.get("label") if seen else None, "edge": edge["id"] if seen else None,
                "to": (landed[0] if landed else None) or edge.get("to"), "status": status,
                "at": going["at"] if going else None,
                "by": self.who(going["actor"], going.get("actor_kind")) if going and seen
                and going.get("actor_kind") in ("person", "agent", "service") else None,
                "values": [], "unread": False, "signed": None, "comment": None,
                "steps": psteps,
                "not_needed": {"count": len(not_needed), "titles": not_needed} if not_needed and full and seen
                else None,
                "hidden": {"count": hidden} if hidden else None,
                "not_taken": others if seen else [], "then": [],
                "_run": run["id"], "_edge": edge["id"], "_wf": wf, "_steps": set(every),
                "_seq": going["seq"] if going else 0, "_first": first, "_moved": False,
                # nothing of its own ran: only the step that moves the record along it
                "_plain": not any(state_of(s) in ("done", "failed", "pending", *_GOING) for s in own + fails)})
        return out

    # ------------------------------------------------------------------ where the record went, in order
    def boundaries(self, insts: list[dict]) -> list[dict]:
        """Each change of where the record is, oldest first: a move of its state field (by hand, by an edge of a run
        about it, or by the hub), and a stop only its workflow keeps. All the changes one edge made are one."""
        by_edge = {(i["_run"], i["_edge"]): i for i in insts}
        bounds: list[dict] = []
        for c in self.changes:
            if c["kind"] != "move":
                continue
            inst = self.made_by(c, by_edge)
            b = {"seq": c["moved"], "at": c["at"], "from": c["from"], "to": c["to"], "change": c, "inst": inst,
                 "how": "edge" if inst else "hand" if c.get("by_kind") in ("person", "agent", "service")
                 and not c.get("run") else "auto"}
            if inst and inst["_moved"]:
                prior = next(x for x in bounds if x["inst"] is inst)
                prior["to"] = c["to"]  # one edge, one move: it took the record where its last landing says
                prior["change"] = {**prior["change"], "said": prior["change"]["said"] + c["said"]}
                continue
            if inst:
                inst["_moved"] = True
            bounds.append(b)
        for inst in insts:  # a stop the record's state field has no value for: the run keeps it, no event says it
            if inst["_moved"] or inst["status"] not in ("done", "fell-back"):
                continue
            wf, run_id = inst["_wf"], inst["_run"]
            landed = [(e["seq"], e, self.step(wf, sid)) for sid in inst["_steps"]
                      for e in self.by_step.get((run_id, sid)) or [] if e["action"] == "action.ran"]
            landed = [(seq, e, s) for seq, e, s in landed if s is not None and s.state and s.state not in self.states
                      and self.view.step_visible(wf, s.id)[0]]  # a stop is the workflow's to tell, not the record's
            if landed:
                seq, e, s = max(landed, key=lambda x: x[0])
                inst["_moved"] = True
                bounds.append({"seq": seq, "at": e["at"], "from": None, "to": s.state, "change": None, "inst": inst,
                               "how": "edge", "stop": True})
        bounds.sort(key=lambda b: b["seq"])
        return bounds

    def made_by(self, c: dict, by_edge: dict) -> dict | None:
        """The edge of a run that made this move of the record: the step whose completion caused the write (a
        person's task that is the move), else the run's next automation after it (the edge's arrive step, or the
        automation whose action moved the record)."""
        run_id = c.get("run")
        if not run_id or run_id not in self.wfs or self.wfs[run_id] is None:
            return None
        wf = self.wfs[run_id]
        cause = self.by_seq.get(c.get("cause"))
        if cause and cause["run_id"] == run_id and cause["action"] in ("item.completed", "gate.decided", "step.done"):
            step = self.step(wf, (cause.get("data") or {}).get("step"))
            if step is not None and step.state == c["to"] and step.on_edge:
                return by_edge.get((run_id, step.on_edge))
        for e in self.events:
            if e["seq"] > c["moved"] and e["run_id"] == run_id and e["action"] in ("action.ran", "step.failed"):
                step = self.step(wf, (e.get("data") or {}).get("step"))
                if step is not None and step.on_edge:
                    return by_edge.get((run_id, step.on_edge))
                return None
        return None

    def stages(self, bounds: list[dict]) -> list[dict]:
        first = next((c for c in self.changes if c["kind"] in ("created", "filed")), None)
        run0 = self.runs[0] if self.runs else None
        wf0 = self.wfs.get(run0["id"]) if run0 else None
        entered = None
        if first is not None:
            by = self.who(first["actor"])
            if by and by["kind"] == "automatic" and run0 and run0.get("started_by"):
                by = self.who(run0["started_by"]) or by  # a workflow made it: who asked for the work says more
            origin = self.hub.engine.started_by(run0) if run0 else None
            entered = {"how": "started" if origin else first["kind"], "by": by,
                       "label": (origin or {}).get("label"), "from": (origin or {}).get("ref")}
        state0 = first["state"] if first is not None and self.lc else None
        at0 = first["at"] if first is not None else (run0 or {}).get("created_at")
        stages = [self.stage("lifecycle" if state0 is not None else "none", state0, at0, None, entered, -2)]
        for b in bounds:
            wf = b["inst"]["_wf"] if b["inst"] else wf0
            stages[-1]["until"] = b["at"]
            stages[-1]["seconds"] = _seconds(stages[-1]["at"], b["at"])
            stages.append(self.stage("stop" if b.get("stop") else "lifecycle", b["to"], b["at"], wf, None, b["seq"]))
        stages[-1]["now"] = True
        return stages

    def stage(self, kind, state, at, wf, entered, seq) -> dict:
        return {"kind": kind, "state": state, "label": self.label(state, wf) if state is not None else "",
                "tone": self.tone(state, wf) if state is not None else None, "at": at, "until": None,
                "seconds": None, "now": False, "entered": entered, "moves": [], "_lines": [], "_from": seq}

    def stage_at(self, stages: list[dict], seq: int) -> dict:
        """The stage the record was in at ``seq``."""
        found = stages[0]
        for st in stages:
            if st["_from"] <= seq:
                found = st
        return found

    # ------------------------------------------------------------------ moves
    def place_moves(self, stages: list[dict], bounds: list[dict], insts: list[dict]) -> None:
        made: dict = {}
        for n, b in enumerate(bounds):
            c, inst = b["change"], b["inst"]
            wf = inst["_wf"] if inst else None
            if inst:
                m = inst
            else:
                t = self.rt.transition(b["from"], b["to"]) or {}
                m = {"how": b["how"], "label": t.get("label") if b["how"] == "hand" else None, "edge": None,
                     "status": "done",
                     "by": self.who(c["actor"], c.get("by_kind")) if b["how"] == "hand" else None,
                     "steps": [], "not_needed": None, "hidden": None, "not_taken": [], "then": [],
                     "_seq": b["seq"]}
            m.update(to=b["to"], to_label=self.label(b["to"], wf), at=b["at"])
            if c is not None:
                kept = inst is not None and bool(self.view.run_withheld(self.run(inst["_run"])))
                m.update(values=[] if kept else c["said"], unread=c["unread"], comment=c.get("comment"),
                         signed={"meaning": c["signed"]["meaning"], "by": self.who(c["signed"]["actor"])}
                         if c.get("signed") else None)
            m.setdefault("values", [])
            m.setdefault("unread", False)
            m.setdefault("signed", None)
            m.setdefault("comment", None)
            stages[n]["moves"].append(m)
            made[b["seq"]] = m
        for inst in sorted(insts, key=lambda i: i["_seq"]):
            if inst["_moved"]:
                continue
            if inst["status"] == "done" and inst["_plain"]:
                continue  # the workflow followed a move already made, and ran nothing: the move itself tells it
            inst["to_label"] = self.label(inst["to"], inst["_wf"]) if inst.get("to") else ""
            after = self.follows(inst, made, bounds)
            if after is not None and after is not inst:
                after["then"].append(inst)
            else:  # tried from where the record was (it did not go through), or it moved nothing
                self.stage_at(stages, inst["_seq"])["moves"].append(inst)
        for st in stages:
            st["moves"].sort(key=lambda m: m["_seq"])

    def follows(self, inst: dict, made: dict, bounds: list[dict]) -> dict | None:
        """The move this edge followed on from by itself: climb what caused it through the run's own automations and
        waits to a move of the record made by hand (one the workflow then followed), or to a task that such a move
        did (moving the record by hand along its one way on does the task that asked for it)."""
        e, hops = inst["_first"], 0
        steps = self.run(inst["_run"]).get("steps") or {}
        while e is not None and hops < 50:
            hops += 1
            cause = e.get("cause")
            if cause in made:
                return made[cause]
            nxt = self.by_seq.get(cause)
            if nxt is None:
                return None
            if nxt["action"] in ("item.completed", "gate.decided"):
                step = self.step(inst["_wf"], (nxt.get("data") or {}).get("step"))
                b = next((b for b in reversed(bounds) if b["seq"] < nxt["seq"]), None)
                if step is not None and step.state and (steps.get(step.id) or {}).get("moved") is True \
                        and b is not None and b["how"] == "hand" and b["to"] == step.state:
                    return made.get(b["seq"])
                return None
            if nxt["action"] not in ("action.ran", "step.done", "step.skipped", "step.waiting"):
                return None
            e = nxt
        return None

    def run(self, run_id: str) -> dict:
        return next(r for r in self.runs if r["id"] == run_id)

    # ------------------------------------------------------------------ lines
    def place_lines(self, stages: list[dict], bounds: list[dict]) -> None:
        last = stages[-1]
        for c in self.changes:
            if c["kind"] in ("created", "filed", "move"):
                continue
            st = self.stage_at(stages, c["seq"])
            if c.get("hide"):
                st["_lines"].append((c["seq"], {"kind": "fold", "what": "change", "count": 1, "classes": c["hide"],
                                                "titles": []}))
            elif c["kind"] == "signature":
                st["_lines"].append((c["seq"], {"kind": "signature", "at": c["at"], "by": self.who(c["actor"]),
                                                "meaning": c["meaning"]}))
            else:
                st["_lines"].append((c["seq"], {"kind": "edit", "at": c["at"], "by": self.who(c["actor"]),
                                                "changed": c.get("changed") or [], "unread": c["unread"],
                                                "comment": c.get("comment")}))
        seen_wf: dict = {}
        for run in self.runs:
            wf = self.wfs.get(run["id"])
            title = str(run.get("title") or "").split(": ", 1)[0] or "the work"
            again = seen_wf.get(run["workflow_id"], 0)
            seen_wf[run["workflow_id"]] = again + 1
            for e in self.events:
                if e["run_id"] != run["id"] or not e["action"].startswith("run."):
                    continue
                what = {"run.started": "began", "run.completed": "finished", "run.cancelled": "cancelled",
                        "run.stopped": "stopped"}[e["action"]]
                why = (e.get("data") or {}).get("reason") or (e.get("data") or {}).get("why")
                by = self.who(e["actor"], e.get("actor_kind")) if what != "finished" else None
                self.stage_at(stages, e["seq"])["_lines"].append((e["seq"], {
                    "kind": "note", "what": what, "title": title, "at": e["at"],
                    "by": by if by and by["kind"] != "automatic" else None,
                    "why": why if isinstance(why, str) else None, "again": again + 1 if again else None}))
            if wf is None:
                continue
            for step in wf.steps:
                if not getattr(step, "at_state", None):
                    continue  # an edge's automation (its move tells it), or the choice of a way out
                self.work_line(stages, bounds, last, run, wf, step)

    def work_line(self, stages, bounds, last, run, wf, step) -> None:
        sid = step.id
        evs = self.by_step.get((run["id"], sid)) or []
        st = (run.get("steps") or {}).get(sid) or {}
        state = st.get("state")
        running = run.get("status") == "running"
        if state in (None, "pending") or not evs and state == "skipped":
            return
        skipped = next((e for e in evs if e["action"] == "step.skipped"), None)
        withdrawn = next((e for e in evs if e["action"] == "step.withdrawn"), None)
        if state == "skipped" and not withdrawn:
            # not needed where the record was (its own condition did not hold): told only of a state it reached
            if skipped and any((run["steps"].get(a) or {}).get("state") == "done" for a in step.after) \
                    and self.view.full(wf) and self.view.step_visible(wf, sid)[0]:
                self.stage_at(stages, skipped["seq"])["_lines"].append((skipped["seq"], {
                    "kind": "fold", "what": "not-needed", "count": 1, "classes": None, "titles": [step.title or sid]}))
            return
        first = evs[0] if evs else None
        ok, missing = self.view.step_visible(wf, sid)
        closing = next((e for e in reversed(evs) if e["action"] in (
            "item.completed", "gate.decided", "step.done", "step.timed_out", "step.withdrawn", "action.ran")), None)
        if step.kind in ("task", "gate"):
            where = self.stage_at(stages, closing["seq"]) if closing else last
            seq = closing["seq"] if closing else (first["seq"] if first else 0)
        else:
            where = self.stage_at(stages, first["seq"]) if first else last
            seq = first["seq"] if first else 0
        via_move = False
        if closing is not None and step.kind == "task" and step.state and st.get("moved") is True:
            # done by moving the record by hand: it belongs to the stage that move ended, just above the move
            b = next((b for b in reversed(bounds) if b["seq"] < closing["seq"]), None)
            if b is not None and b["how"] == "hand" and b["to"] == step.state:
                via_move = True
                where = self.stage_at(stages, b["seq"] - 1)
                seq = b["seq"] - 0.5
        if not ok:
            where["_lines"].append((seq, {"kind": "fold", "what": "step", "count": 1,
                                          "classes": " and ".join(missing), "titles": []}))
            return
        withheld = bool(self.view.run_withheld(run))
        opened = next((e for e in evs if e["action"] in ("step.activated", "step.waiting", "step.queued")), first)
        claimed = next((e for e in reversed(evs) if e["action"] == "item.claimed"), None)
        item = self.items.get(st.get("item")) if st.get("item") else None
        line = {"kind": "work", "title": step.title or sid, "at": opened["at"] if opened else None,
                "done_at": None, "seconds": None, "by": None, "for": None, "assigned": None, "people_only": False,
                "passed": None, "since": None, "chose": None,
                "signed": False, "approved_by": None, "via_move": via_move, "error": None,
                "what": {"task": "task", "gate": "decision", "wait": "wait"}.get(step.kind, "automatic")}
        if step.kind in ("task", "gate"):
            done = next((e for e in reversed(evs) if e["action"] in ("item.completed", "gate.decided")), None)
            if state == "done" and done is not None:
                data = done.get("data") or {}
                outcome = (data.get("output") or {}).get("outcome")
                labels = getattr(step, "outcome_labels", None) or {}
                line.update(status="done", done_at=done["at"], by=self.who(done["actor"], done.get("actor_kind")),
                            seconds=_seconds(opened["at"], done["at"]) if opened else None,
                            signed=bool(done.get("signature")),
                            approved_by=self.hub._who(self.names, data["approved_by"]) if data.get("approved_by")
                            else None)
                if outcome and not withheld and (outcome in labels or step.kind == "gate"):
                    line["chose"] = labels.get(outcome) or schema.state_words(outcome)  # the way out it chose
                # a task whose own move of the record was refused: its edge says so, once
                failed = next((e for e in evs if e["action"] == "step.failed"), None)
                if failed is not None and st.get("moved") == "failed" and not step.on_edge:
                    line.update(status="failed", error=None if withheld else (failed.get("data") or {}).get("error"))
            elif withdrawn is not None:
                line.update(status="withdrawn", done_at=withdrawn["at"])
            elif not running:
                line.update(status="cancelled")
            else:
                # where the work is now is the item's to say: handed on, it is no longer for the role the step names
                mine = item if (item or {}).get("step_id") == sid else {}
                holder = mine.get("claimed_by")
                held = next((e for e in reversed(evs) if e["action"] == "item.held"), None)
                role = mine.get("role") or step.role
                people_only = bool(mine.get("persons_only") or step.persons_only)
                line["for"] = self.hub.role_name(role) if role else None
                if held is not None and (claimed is None or held["seq"] > claimed["seq"]):
                    line.update(status="held", by=self.who(held["actor"], held.get("actor_kind")))
                elif holder:
                    line.update(status="taken", by=self.who(holder))
                else:
                    line.update(status="open", people_only=people_only,
                                assigned=self.who(mine["assignee"]) if mine.get("assignee") else None)
                    # given back or passed up since it was last taken: who did, and why, in their own words
                    gave = next((e for e in reversed(evs) if e["action"] in ("item.released", "item.escalated")), None)
                    if gave is not None and (claimed is None or gave["seq"] > claimed["seq"]):
                        note = (gave.get("data") or {}).get("note")
                        line["passed"] = {"by": self.who(gave["actor"], gave.get("actor_kind")), "at": gave["at"],
                                          "up": gave["action"] == "item.escalated",
                                          "note": note if isinstance(note, str) and note and not withheld else None}
                fresh = (_seconds(mine.get("updated_at") or (opened or {}).get("at"), self.now) or 0) < LIVE_FOR
                if fresh and ((line["by"] or {}).get("kind") == "agent"
                              or line["status"] == "open" and step.agents_may and not people_only):
                    self.live = True
                if where is last and opened is not None:
                    began = self.stage_at(stages, opened["seq"])
                    line["since"] = began["label"] if began is not last else None
        elif step.kind == "wait":
            over = next((e for e in reversed(evs) if e["action"] in ("step.done", "step.timed_out")), None)
            if over is not None:
                line.update(status="timed-out" if over["action"] == "step.timed_out" else "done", done_at=over["at"],
                            seconds=_seconds(opened["at"], over["at"]) if opened else None)
            elif state == "failed":
                line.update(status="failed", error=None if withheld else st.get("error"))
            else:
                line.update(status="waiting" if running else "cancelled")
        else:  # an automated state's own work
            line["by"] = {"name": "", "kind": "automatic"}
            if state == "done":
                line.update(status="done", done_at=closing["at"] if closing else None)
            elif state == "failed":
                line.update(status="failed", error=None if withheld else st.get("error"))
            elif state in _GOING:
                line.update(status=state if running else "cancelled")
                self.busy = self.busy or running
            else:
                return
        where["_lines"].append((seq, line))

    @staticmethod
    def fold(lines: list[dict]) -> list[dict]:
        """One line per stretch of what the viewer may not read, and per stretch of work that was not needed."""
        out: list[dict] = []
        for line in lines:
            prev = out[-1] if out else None
            if line["kind"] == "fold" and prev is not None and prev["kind"] == "fold" \
                    and (prev["what"], prev["classes"]) == (line["what"], line["classes"]):
                prev["count"] += line["count"]
                prev["titles"] += line["titles"]
            else:
                out.append(line)
        return out
