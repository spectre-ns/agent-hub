"""The panel beside a record: where the record is in its lifecycle, and the work under way about it.

Like the side panel of an issue tracker: the record's status as a lozenge, the moves the member may make from it (the
hub's own rules: a member who may write the type, a transition not kept for workflows), a link to the lifecycle
drawn as a diagram, then each open run about the record (its own record, or one its input or steps carry): where it
is now, who holds it, when it is due, how far along it is, and the member's own piece of it, if they have one.

What a member may not see stays out (``visibility.Viewer``): a step whose data they may not read is not named, not
placed and not dated, and a conditional step they may not see the reason for is shown as "if needed" until it runs
(that it was skipped would say which way the run went). Finished runs are on the record's Work tab.
"""

from __future__ import annotations

import html
import re

from .. import schema
from ..visibility import Viewer

e = html.escape

STEP_WORDS = {"done": "Done", "active": "Now", "waiting": "Waiting", "failed": "Stopped", "pending": "To come",
              "queued": "In the queue", "running": "Under way",
              "if-needed": "If needed", "skipped": "Not needed", "hidden": "Not shown"}


class WorkPanel:
    def work_routes(self, R):
        R("GET", r"/records/(?P<rtype>[A-Za-z][\w]*)/(?P<rid>[\w.-]+)/workflow", self.page_record_workflow)

    # ------------------------------------------------------------------------------------------ the panel
    def open_runs(self, me, ref: str) -> list[tuple[dict, object]]:
        """The running work about this record that has a workflow (a mention or an unhandled event has none)."""
        viewer = Viewer(self.hub, me)
        out = []
        for run in self.hub.engine.runs(mentions=ref, status="running", limit=50):
            wf = viewer.workflow_of_run(run["id"])
            if wf is not None:
                out.append((run, wf))
        return out

    def forward_move(self, lc: dict, st, moves: list[dict]):
        """The one move from ``st`` drawn as the main button; the rest are secondary. Read from the lifecycle's shape,
        not from state names: never a move that undoes or ends the record (cancel, entered in error), one into a
        state no move leaves (a no-show), or a side move that leads back here (the patient stepping out and back).
        Ties go to the lifecycle's own order. None when no move goes forward: then every button is secondary."""
        # a workflow starting on the record again (its entry, from any state) leads back to nothing
        trans = [t for t in lc.get("transition", []) if "*" not in t["from"] and not t.get("entry")]

        def reach(s):
            seen, todo = set(), [s]
            while todo:
                x = todo.pop()
                for t in trans:
                    if x in t["from"] and t["to"] not in seen:
                        seen.add(t["to"])
                        todo.append(t["to"])
            return seen

        ok = [t for t in moves if not self._undoing(t) and st not in reach(t["to"])
              and any(t["to"] in x["from"] for x in trans)]
        return min(ok, key=moves.index) if ok else None

    # ------------------------------------------------------------------------------------ a move's time and words
    def moves_open_now(self, rt, moves: list[dict], rec: dict) -> tuple[list[dict], str]:
        """The moves a person may make now, and a line saying when the others open (a move with ``opens``: a visit's
        check-in from two hours before it is booked for, a no-show from its time)."""
        now = self.hub.engine.now()
        ok, later = [], []
        for t in moves:
            at = schema.opens_at(rt, t, rec)
            if at is not None and now < at:
                same = at.date() == now.date()
                later.append(f"{t['label']} from {at.strftime('%H:%M') if same else at.strftime('%a %d %b %H:%M')}")
            else:
                ok.append(t)
        note = f'<p class="muted small moves-later">{e("; ".join(later))}</p>' if later else ""
        return ok, note

    _CONFIRM = re.compile(r"\{([A-Za-z_][\w.]*)(?::(time|notice))?\}")

    def move_confirm(self, rt, t: dict, rec: dict) -> str:
        """What follows a move, as its type says it (``confirm``), with the record's own values in it: ``{field}``
        as the page shows it, ``{field:time}`` a time's hour and minute, ``{field:notice}`` how long from now until
        that time, in hours ("20 hours' notice"). Empty when the type says nothing."""
        text = t.get("confirm") or ""
        if not text:
            return ""
        from datetime import datetime

        def fill(m):
            f = rt.field(m.group(1))
            v = schema.first(rec, f["path"]) if f else None
            if v in (None, ""):
                return "its time" if m.group(2) else ""
            try:
                at = datetime.fromisoformat(str(v)[:19])
            except ValueError:
                at = None
            if m.group(2) == "time" and at:
                return at.strftime("%H:%M")
            if m.group(2) == "notice" and at:
                hours = (at - self.hub.engine.now()).total_seconds() / 3600
                if hours <= 0:
                    return "no notice (its time has passed)"
                n = int(hours)
                return "less than an hour's notice" if n < 1 else f"{n} hour's notice" if n == 1 else \
                    f"{n} hours' notice"
            return str(v)[:16].replace("T", " ")
        return self._CONFIRM.sub(fill, text)

    def work_panel(self, me, ref: str, *, rt=None, st=None, moves_html: str = "", auto=(), url: str = "",
                   own: bool = True) -> str:
        """The side panel's lifecycle and open-work sections, as HTML (empty when there is neither). ``own``: False on
        the record's own page, where the work walking it is at the top (``record_work``), not a card beside it."""
        out = []
        if rt is not None and rt.lifecycle:
            lc_link = (f'<a class="wf-link" href="{e(url)}/workflow" data-modal>View workflow</a>' if url else "")
            out.append(f'<section class="wp-status" aria-labelledby="wp-status-h"><h2 id="wp-status-h">Status</h2>'
                       f'<div class="status">{self._state_loz(st, rt)}{lc_link}</div>')
            if moves_html:
                out.append(f'<div class="side-actions" role="group" aria-label="Move it to">{moves_html}</div>')
            if auto:
                out.append("<p class='muted small'>A workflow moves it to "
                           + e(", ".join(rt.state_label(t["to"]) for t in auto)) + ".</p>")
            out.append("</section>")
        runs = [(r, w) for r, w in self.open_runs(me, ref) if own or r.get("record") != ref]
        if runs:
            viewer = Viewer(self.hub, me)
            cards = "".join(self.run_card(me, run, wf, viewer, ref) for run, wf in runs)
            out.append(f'<section class="wp-work" aria-labelledby="wp-work-h"><h2 id="wp-work-h">Open work '
                       f'<span class="count">{len(runs)}</span></h2>{cards}</section>')
        out.append(self.family_panel(me, ref))
        return "".join(out)

    # ---------------------------------------------------------------------------- sub-records and their parent
    def family_panel(self, me, ref: str) -> str:
        """The record a sub-record was made for (a link back), in the side panel. The sub-records made from this one
        are in the page's main column (``started_from``)."""
        hub = self.hub
        out = []
        parent = hub.engine.parent_record(ref)
        if parent and hub.may_read(me, parent.split("/", 1)[0]):
            out.append(f'<p class="wp-parent"><span class="muted">Made for</span> {self._family_link(parent)}</p>')
        # the move of another record whose automation started the work walking this one (``workflow.start``,
        # ``record.create``): "Started by “Needs a call” on <the lab result>"; one from before moves were noted on
        # their spawns names the record only
        runs = sorted(hub.db.runs.find({"record": ref}), key=lambda r: str(r.get("created_at") or ""))
        by = hub.engine.started_by(runs[-1]) if runs else None
        src = str((by or {}).get("ref") or "")
        if "/" in src and src != parent and src != ref and hub.may_read(me, src.split("/", 1)[0]):
            label = (by or {}).get("label")
            out.append(f'<p class="wp-parent wp-started-by"><span class="muted">Started by</span> '
                       + (f"“{e(label)}” <span class=\"muted\">on</span> " if label else "")
                       + f"{self._family_link(src)}</p>")
        return "".join(out)

    def started_from(self, me, ref: str, rt=None) -> str:
        """The records made from this record, for the main column: its sub-records (``record.child``: a visit's
        referrals, prescriptions and lab orders) and every record a move of it started (``Hub.record_activity``: the
        lab result's follow-up call), once each. Each with its state and, in words, where its own work is (Done,
        Stopped, Stuck: <step> failed, or who holds it and when it is due), stuck, urgent and overdue first. One of a
        type the member may not read keeps its kind, its state and who holds it, but neither its title nor a link."""
        hub, rows = self.hub, []
        viewer = Viewer(hub, me)
        kids, seen = [], set()
        for kid in hub.engine.children_of(ref):
            if kid["ref"] not in seen:
                seen.add(kid["ref"])
                kids.append(kid)
        for move in self.activity_of(me, ref):
            for r in move.get("ran") or []:
                for s in r.get("started") or []:
                    run = s.get("run")
                    if isinstance(run, str):
                        row = hub.db.runs.get(run) if run else None
                        run = {**row, "id": row.get("id") or row.get("_id") or run} if row else None
                    kref = str(s.get("ref") or (run or {}).get("record") or "")
                    if "/" in kref and kref != ref and kref not in seen:
                        seen.add(kref)
                        kids.append({"ref": kref, "run": run})
        for n, kid in enumerate(kids):
            rtype = kid["ref"].split("/", 1)[0]
            krt = hub.record_type(rtype)
            rec = hub.records.read(*kid["ref"].split("/", 1))
            st = schema.first(rec, krt.lifecycle["path"]) if rec and krt and krt.lifecycle else None
            name = self._family_link(kid["ref"], rec) if hub.may_read(me, rtype) else \
                f'<span class="muted">{e(krt.label if krt else rtype)}</span>'
            work, rank = self._child_work(kid["run"] or {}, viewer)
            rows.append((rank, n, f'<li>{name} {self._state_loz(st, krt) if st else ""} {work}</li>'))
        if not rows:
            return ""
        what = rt.label.lower() if rt is not None else "record"
        rows.sort(key=lambda r: r[:2])
        return (f'<section class="children started-from" aria-labelledby="started-from-h"><h2 id="started-from-h">'
                f'Started from this {e(what)} <span class="count">{len(rows)}</span></h2><ul class="plain">'
                + "".join(r[2] for r in rows) + "</ul></section>")

    def _child_work(self, run: dict, viewer) -> tuple[str, tuple]:
        """Where a sub-record's own work is, in words, and its rank in the list: stuck first, then urgent, then due
        soonest (overdue before due); one with no work of its own after those, finished last."""
        if not run:
            return "", (3, "")
        if run.get("status") == "completed":
            return '<span class="muted">Done</span>', (4, "")
        if run.get("status") != "running":
            return '<span class="loz removed">Stopped</span>', (4, "")
        wf = viewer.workflow_of_run(run["id"])
        for sid, sst in (run.get("steps") or {}).items():
            if (sst or {}).get("state") == "failed":
                shown = wf is not None and viewer.step_state(wf, run, sid) is not None
                title = wf.step(sid).title if shown else "a step you may not see"
                return f'<span class="loz removed">Stuck: {e(title)} failed</span>', (0, "")
        live = [it for sst in (run.get("steps") or {}).values() if (sst or {}).get("item")
                for it in [self.hub.db.items.get(sst["item"])]
                if it and it.get("status") in ("open", "claimed", "held") and it.get("kind") != "notice"]
        if not live:
            job = next((sid for sid, sst in (run.get("steps") or {}).items()
                        if (sst or {}).get("state") in ("queued", "running")), None)
            if job is not None:  # a job a worker runs: it will go on by itself
                shown = wf is not None and viewer.step_state(wf, run, job) is not None
                return (f'<span class="loz inprogress">Under way: '
                        f'{e(wf.step(job).title if shown else "a step you may not see")}</span>'), (2, "")
            return '<span class="muted">waiting</span>', (3, "")
        it = min(live, key=lambda i: (i.get("priority") != "urgent", i.get("due_at") or "~"))
        return self._holder(it) + self._urgency(it), (1 if it.get("priority") == "urgent" else 2, it.get("due_at") or "~")

    def _family_link(self, ref: str, rec: dict | None = None) -> str:
        """A link to a parent or sub-record this member was checked for already, named by its title."""
        rec = rec if rec is not None else self.hub.records.read(*ref.split("/", 1))
        return f'<a href="{e(self.record_href(ref))}">{e(self.hub.short_title(rec) if rec else ref)}</a>'

    def _holder(self, item: dict) -> str:
        """Who holds a piece of work, in words: the member who took it or it was given to, else its role."""
        who = item.get("claimed_by") or item.get("assignee")
        if who:
            return f"with {self.who(who)}"
        if item.get("role"):
            try:
                name = self.hub.role(item["role"]).get("name") or item["role"]
            except Exception:  # noqa: BLE001 - a role since removed
                name = item["role"]
            return f'<span class="muted">waiting for someone in</span> {e(name)}'
        return '<span class="muted">unassigned</span>'

    def _role_name(self, role: str | None) -> str:
        if not role:
            return ""
        try:
            return self.hub.role(role).get("name") or role
        except Exception:  # noqa: BLE001 - a role since removed
            return role

    def _step_facts(self, run: dict, s, item: dict | None, rt) -> list[str]:
        """Who holds a step now, when it is due and who is told when it is late, whatever its state: a stopped step
        keeps its role, its due time and its escalation, and says who is fixing it."""
        from .server import duration_text
        sst = run["steps"].get(s.id) or {}
        out = []
        live = item and item.get("status") in ("open", "claimed", "held") and item.get("step_id") == s.id
        if live:
            out.append(self._holder(item) + self._urgency(item))
        elif sst.get("state") == "failed":
            notes = list(self.hub.db.items.find({"run_id": run["id"], "step_id": s.id, "kind": "notice",
                                                 "status": {"$in": ["open", "claimed"]}}))
            fixers = sorted({self.who(n["assignee"]) if n.get("assignee") else self._role_name(n.get("role"))
                             for n in notes} - {""})
            role = self._role_name(s.role) if s.role else ""
            out.append(("for " + e(role) + "; " if role else "")
                       + ("being fixed by " + e(", ".join(fixers)) if fixers else
                          '<span class="muted">a policy owner can try it again from its page</span>'))
            if sst.get("error"):
                out.append(f'<span class="muted small">{e(str(sst["error"])[:160])}</span>')
        elif s.role and sst.get("state") != "waiting":
            out.append(f'<span class="muted">for</span> {e(self._role_name(s.role))}')
        if not live and s.due:
            due = (item or {}).get("due_at") if item and item.get("step_id") == s.id else None
            out.append(f'<span class="badge due" title="due {e(due)}">Due {e(due[:16].replace("T", " "))}</span>'
                       if due else f'<span class="muted">due within {e(duration_text(s.due))}</span>')
        if s.escalate_to:
            out.append(f'<span class="muted">late: {e(self._role_name(s.escalate_to))} is told</span>')
        if s.state and rt is not None and rt.lifecycle:
            out.append(f'<span class="muted">puts it in</span> {e(rt.state_label(s.state))}')
        return out

    def _next_steps(self, wf, run: dict, now_ids: set, rt, visible: set) -> list[str]:
        """What comes next: the steps waiting on the ones open now, with the state each puts the record in."""
        out = []
        for s in wf.steps:
            if s.id not in visible or (run["steps"].get(s.id) or {}).get("state") not in (None, "pending") \
                    or not set(s.after) & now_ids:
                continue
            label = f" ({rt.state_label(s.state)})" if s.state and rt is not None and rt.lifecycle else ""
            who = f" · {self._role_name(s.role)}" if s.role else ""
            out.append(f"{s.title}{who}{label}")
        return out

    def _record_state(self, me, run: dict, rt, ref: str) -> str:
        """The state of the record a run walks, in words, when the card is not on that record's own page (a visit's
        work on the patient's chart): the state is not clinical, so whoever may read the type sees it."""
        rec_ref = str(run.get("record") or "")
        if rt is None or not rt.lifecycle or rec_ref == ref or "/" not in rec_ref \
                or not self.hub.may_read(me, rt.id):
            return ""
        rec = self.hub.records.read(*rec_ref.split("/", 1))
        st = schema.first(rec, rt.lifecycle["path"]) if rec else None
        return f" {self._state_loz(st, rt)}" if st else ""

    def _hidden_now(self, run: dict, hidden: list) -> list[str]:
        """Where a run is when it is at a step the member may not see: not the step (its title, its branch), but who
        holds it and when it is due, so the front desk can say "with the nurse, due by 3" without the clinical part."""
        out = []
        for s in hidden:
            sst = run["steps"].get(s.id) or {}
            if sst.get("state") not in ("active", "waiting", "failed", "queued", "running"):
                continue
            item = self.hub.db.items.get(sst["item"]) if sst.get("item") else None
            bits = ['<span class="muted">At a step you may not see</span>']
            if sst.get("state") == "failed":
                bits.append('<span class="loz removed">Stopped</span>')
            if item and item.get("status") in ("open", "claimed", "held"):
                bits.append(self._holder(item) + self._urgency(item))
            elif s.role and sst.get("state") != "waiting":
                bits.append(f'<span class="muted">for</span> {e(self._role_name(s.role))}')
            out.append(f'<li>{" ".join(bits)}</li>')
        return list(dict.fromkeys(out))  # two hidden branches at once read as one

    def run_card(self, me, run: dict, wf, viewer: Viewer, ref: str) -> str:
        """One open run: where it is now, who holds it, its steps in a strip, the viewer's own piece of it."""
        eng = self.hub.engine
        title = wf.title if run.get("record") == ref else self.hub.run_title(me, run)
        try:
            rt = self.hub.record_type(str(run.get("record") or "").split("/", 1)[0]) if run.get("record") else None
        except Exception:  # noqa: BLE001 - a type since removed
            rt = None
        cond = viewer.conditional(wf)  # a step under a condition that has not run: if needed, not surely to come
        states = [(s, "if-needed" if x == "pending" and s.id in cond else x)
                  for s in wf.steps for x in [viewer.step_state(wf, run, s.id)]]
        seen = [(s, x) for s, x in states if x is not None]
        hidden = [s for s, x in states if x is None]
        now_steps = [s for s, x in seen if x in ("active", "waiting", "failed", "queued", "running")]
        hidden_now = any((run["steps"].get(s.id) or {}).get("state") in ("active", "waiting", "failed", "queued",
                                                                         "running")
                         for s in hidden)
        # where it is now
        now_html, mine = [], []
        for s in now_steps:
            sst = run["steps"].get(s.id) or {}
            item = None
            if sst.get("item"):
                try:
                    item = eng.item(sst["item"])
                except Exception:  # noqa: BLE001
                    item = None
            bits = [f"<b>{e(s.title)}</b>"]
            if sst.get("state") == "failed":
                bits.append('<span class="loz removed">Stopped</span>')
            elif sst.get("state") == "waiting":
                bits.append('<span class="muted">waiting</span>')
            elif sst.get("state") in ("queued", "running"):
                bits.append(f'<span class="loz inprogress">{e(STEP_WORDS[sst["state"]])}</span>')
            bits += self._step_facts(run, s, item, rt)
            if sst.get("state") == "waiting":  # a wait over several records: each, where it is, who has it
                bits.append(self._wait_records_html(me, run, s))
            elif item and item.get("step_id") == s.id:  # nobody may take it: who is kept out, and why
                bits.append(self._blocked_html(item))
            if item and item.get("status") in ("open", "claimed", "held") and item.get("step_id") == s.id:
                if item.get("claimed_by") == me["id"] or item.get("assignee") == me["id"] or (
                        item["status"] == "open" and not item.get("claimed_by") and eng.eligibility(me, item)[0]):
                    mine.append((s, item))
            now_html.append(f'<li>{" ".join(bits)}</li>')
        if hidden_now:
            now_html += self._hidden_now(run, hidden)
        if not now_html:
            now_html.append('<li class="muted">Between steps</li>')
        ahead = self._next_steps(wf, run, {s.id for s in now_steps}, rt, {s.id for s, _ in seen}) \
            if not hidden_now else []
        next_html = (f'<div class="wnow"><span class="k">Next</span><ul class="plain">'
                     + "".join(f"<li>{e(t)}</li>" for t in ahead) + "</ul></div>") if ahead else ""
        # the strip: each step it shows, in the workflow's order; the ones the member may not see as one
        segs, rows = [], []
        n = {"done": 0, "now": 0, "todo": 0, "maybe": 0}
        hidden_put = False
        for s, x in states:
            if x is None:
                if not hidden_put:
                    segs.append(f'<li class="seg hidden" title="{len(hidden)} step(s) you may not see"></li>')
                    rows.append(f'<li><span class="sw hidden">Not shown</span> {len(hidden)} step'
                                f'{"s" if len(hidden) != 1 else ""} you may not see</li>')
                    hidden_put = True
                continue
            if x == "skipped":
                continue  # not on this run's path
            cls = ("done" if x == "done" else "now" if x in ("active", "waiting", "failed", "queued", "running") else
                   "maybe" if x == "if-needed" else "todo")
            n[cls] += 1
            segs.append(f'<li class="seg {cls}" title="{e(STEP_WORDS.get(x, x))}: {e(s.title)}"></li>')
            rows.append(f'<li><span class="sw {cls}">{e(STEP_WORDS.get(x, x))}</span> {e(s.title)}</li>')
        summary = " · ".join(p for p in [f"{n['done']} done" if n["done"] else "",
                                         f"{n['todo']} still to come" if n["todo"] else "",
                                         f"{n['maybe']} if needed" if n["maybe"] else ""] if p)
        # the run's one item: its key, and how many of its parts (child items) are still open
        key = ""
        if run.get("item"):
            kids = list((self.hub.db.items.get(run["item"]) or {}).get("children") or [])
            left = self.hub.db.items.count({"_id": {"$in": kids}, "status": {"$in": ["open", "claimed", "held"]}}) \
                if kids else 0
            key = (f' <a class="key" href="/items/{e(run["item"])}">{e(self._key(run["item"]))}</a>'
                   + (f' <span class="muted">{left} of {len(kids)} parts open</span>' if left else ""))
        act = "".join(f'<a class="btn primary wp-mine" href="/items/{e(item["id"])}">Your turn: {e(s.title)}</a>'
                      for s, item in mine)
        state = self._record_state(me, run, rt, ref)
        return (f'<article class="wcard">'
                f'<div class="wcard-head"><a href="{e(self.run_href(run))}">{e(title)}</a>{key}{state}</div>'
                f'<div class="wnow"><span class="k">Now</span><ul class="plain">{"".join(now_html)}</ul></div>'
                f'{next_html}'
                f'<ol class="wstrip" aria-hidden="true">{"".join(segs)}</ol>'
                f'<details class="wsteps"><summary>{e(summary or "Steps")}</summary><ol class="plain">{"".join(rows)}'
                f'</ol></details>{act}</article>')

    # ------------------------------------------------------------------------------------------ the lifecycle
    def page_record_workflow(self, req, rtype, rid):
        """The record's workflow as a map, for the dialog: its states and the moves between them, the way this record
        came drawn over it (numbered, in order), where it is now, and the moves a person can make from here. Below
        the map, folded, every move in words and its workflow stage by stage."""
        me, hub = req["me"], self.hub
        from ..hub import Invalid
        rt = hub.record_type(rtype)
        if not rt or not rt.lifecycle:
            raise Invalid(f"{rtype} records have no lifecycle")
        ref = f"{rtype}/{rid}"
        rec = hub.read_record(me, ref)
        st = schema.first(rec, rt.lifecycle["path"])
        url = f"/records/{e(rtype)}/{e(rid)}"
        journey = hub.record_journey(me, ref)
        been = [s for s in journey["stages"] if s["kind"] == "lifecycle"]
        came = (f' It has been through {len(been)} stages to get here; the way it came is numbered.'
                if len(been) > 1 else "")
        wf = hub._own_workflow(rt)
        outline = ""
        if wf is not None and getattr(wf, "shape", None) and Viewer(hub, me).full(wf):
            taken = {m["edge"] for s in journey["stages"] for m in self._moves_of(s) if m.get("edge")}
            marks = {"visited": {s["state"] for s in journey["stages"]}, "current": st, "taken": taken}
            outline = (f'<details class="lc-moves"><summary>Its workflow, stage by stage</summary>'
                       f'{self._states_view(wf, marks)}</details>')
        body = (f'<div data-modal-body class="lc-modal"><h2>How {e(self._lower(rt.plural))} move</h2>'
                f'<p class="muted">{e(hub.title(rec))} is <b>{e(rt.state_label(st))}</b>.{e(came)}</p>'
                f'{self.lifecycle_svg(rt, st, journey)}{self._lifecycle_table(rt, st)}{outline}'
                f'<div class="filters"><a class="btn secondary" href="{url}" data-modal-close>Close</a></div></div>')
        return self.layout(req, f"Workflow: {hub.title(rec)}", body,
                           crumbs=[("/records", "Records"), (f"/records/{rtype}", rt.plural), (url, hub.title(rec)),
                                   (None, "Workflow")])

    @staticmethod
    def _moves_of(stage: dict) -> list[dict]:
        """A stage's moves and the ones that followed on from them, flat."""
        out, todo = [], list(stage.get("moves") or [])
        while todo:
            m = todo.pop(0)
            out.append(m)
            todo += m.get("then") or []
        return out

    def _lifecycle_table(self, rt, st) -> str:
        lc = rt.lifecycle
        rows = []
        for t in lc["transition"]:
            if t.get("entry"):
                continue  # where its workflow starts it: the map's Start, not a move out of every state
            frm = "any state" if "*" in t["from"] else ", ".join(rt.state_label(f) for f in t["from"])
            here = schema.leaves(rt.lifecycle, t, st) and t["by"] != "workflow"
            who = "only when its work is cancelled" if t.get("abandon") else \
                "only a workflow" if t["by"] == "workflow" else "anyone who may change it"
            rows.append(f'<tr{" class=here" if here and t["to"] != st else ""}><td>{e(frm)}</td>'
                        f'<td>{e(rt.state_label(t["to"]))}</td><td>{e(t["label"])}</td><td>{e(who)}</td></tr>')
        return ('<details class="lc-moves"><summary>Every move, in words</summary><div class="tablewrap"><table>'
                '<tr><th>From</th><th>To</th><th>Button</th><th>Who</th></tr>' + "".join(rows) + "</table></div></details>")

    MAP_W, MAP_H, MAP_GX, MAP_GY = 150, 40, 26, 50
    MAP_START = "\x00start"  # the map's own node for where a record begins: no state has this name

    def lifecycle_graph(self, rt) -> dict:
        """A type's states and moves as the map draws them: ``{"nodes", "edges": {(a, b): [move]}, "start": [state],
        "endings": [state], "any": {state: [move]}}``. The moves are the type's own and its workflow's edges
        (``catalog._with_workflow_moves``). Where its workflow starts a record is one Start node with a move to each
        such state, never a move from every state into them; a move from any state is kept apart (``any``): it is
        said on its target, not drawn from everywhere."""
        lc = rt.lifecycle
        states = list(lc["states"])
        edges: dict = {}
        begins: list = []
        anywhere: dict = {}
        for t in lc["transition"]:
            if t["to"] not in states:
                continue
            if t.get("entry"):
                if t["to"] not in begins:
                    begins.append(t["to"])
                continue
            if "*" in t["from"]:
                anywhere.setdefault(t["to"], []).append(t)
                continue
            for f in t["from"]:
                if f in states and f != t["to"]:
                    edges.setdefault((f, t["to"]), []).append(t)
        first = [s for s in [lc.get("initial"), *(lc.get("starts") or [])] if s in states]
        start = list(dict.fromkeys(begins or first))
        leaves = {a for a, _ in edges}
        endings = [s for s in states if s not in leaves and s not in start]
        return {"nodes": states, "edges": edges, "start": start, "endings": endings, "any": anywhere}

    def lifecycle_svg(self, rt, st, journey: dict | None = None) -> str:
        """The map: states as boxes in rows from where a record starts down to where it ends (``flowmap.layout``:
        no arrow crosses a box), moves as arrows. Drawn over it, the way this record came (``journey``, from
        ``Hub.record_journey``): its moves numbered in order and named, the states it has been in solid and the rest
        faint, where it is now ringed. The moves a person can make from here are in the accent colour and named;
        moves only its workflow makes are dashed. Every other arrow says what it is when pointed at. A way to an
        ending (cancelled, entered in error) that many states have is not an arrow from each: the state says it in
        small words, so the map keeps to the ways a record usually goes."""
        from . import flowmap
        g = self.lifecycle_graph(rt)
        states, edges, START = g["nodes"], dict(g["edges"]), self.MAP_START
        W, H = self.MAP_W, self.MAP_H
        # the way it came: each change of state in order (a stop only its workflow keeps is no state of the map)
        walked = [s["state"] for s in (journey or {}).get("stages") or [] if s["kind"] == "lifecycle"
                  and s["state"] in states]
        if len(walked) > 1 and (walked[0], walked[1]) not in edges and walked[1] in g["start"]:
            walked = walked[1:]  # made, then put where its workflow starts it: that is where it began
        route: dict = {}
        for n, (a, b) in enumerate(zip(walked, walked[1:]), 1):
            if a != b:
                route.setdefault((a, b), []).append(n)
        visited = set(walked) or {st}
        entered = walked[0] if walked else None
        # a way to an ending that three or more states have is said on each state, not drawn (unless it was taken)
        into: dict = {}
        for (a, b) in edges:
            if b in g["endings"]:
                into.setdefault(b, []).append(a)
        crowded = {b for b, froms in into.items() if len(froms) >= 3}
        said: dict = {}  # state -> the endings it can also go to, in words
        drawn: dict = {}
        ways_on = {s: sum(1 for (a, _) in edges if a == s) for s in states}
        for (a, b), moves in edges.items():
            # ... but a state whose only way on is that ending keeps its arrow: the map still ends somewhere
            if b in crowded and (a, b) not in route and ways_on[a] > 1:
                said.setdefault(a, []).append(b)
            else:
                drawn[(a, b)] = moves
        for pair in route:  # a move it made that no arrow says (one from any state, a correction): drawn as made
            if pair not in drawn:
                drawn[pair] = list(g["any"].get(pair[1], [])) or edges.get(pair) or []
        # one arrow from Start: to where this record began (else where the type says a record begins); the other
        # states a record can start in say so when pointed at, and keep their heavier edge
        begin = entered or next(iter(g["start"]), None)
        pairs = ([(START, begin)] if begin else []) + list(drawn)
        joined = {x for pair in pairs for x in pair} | set(said) | set(g["any"]) | crowded | visited | {st}
        states = [s for s in states if s in joined]  # a value no move reaches or leaves is no part of the map
        fed = {b for _, b in pairs}
        lay = flowmap.layout([START, *states], pairs, [START], last=[s for s in g["endings"] if s not in fed],
                             w=W, h=H, gx=self.MAP_GX, gy=self.MAP_GY)
        own = self.hub._own_workflow(rt)
        begin_label = ((getattr(own, "shape", None) or {}).get("start") or {}).get("label") if own else None
        runs_of = {x["id"]: len(x.get("steps") or []) for x in ((getattr(own, "shape", None) or {}).get("edges") or [])}
        short = lambda words, n=24: words if len(words) <= n else words[:n - 1] + "…"
        paths, tags = [], []
        for (a, b) in pairs:
            pts = lay["routes"][(a, b)]
            if a == START:
                on = b == entered
                cls = "lc-edge begin" + (" route" if on else " dim" if journey else "")
                paths.append(f'<path class="{cls}" d="{flowmap.path_d(pts)}" marker-end="url(#lc-arrow'
                             f'{"-route" if on else ""})"><title>A {e(self._lower(rt.label))} can start as '
                             f'{e(rt.state_label(b))}</title></path>')
                if on and begin_label:
                    x, y = lay["labels"][(a, b)]
                    tags.append((0, f'<g class="lc-tag route"><text class="lc-name" x="{x + 8:.1f}" '
                                    f'y="{y + 3.5:.1f}">{e(short(begin_label))}</text></g>'))
                continue
            moves = drawn[(a, b)]
            auto = bool(moves) and all(t["by"] == "workflow" for t in moves)
            mine = a == st and any(t["by"] != "workflow" for t in moves) and b != st
            came = route.get((a, b))
            names = list(dict.fromkeys(t["label"] for t in moves if t.get("label"))) or ["Moved"]
            ran = sum(runs_of.get(t.get("edge"), 0) for t in moves)
            cls = "lc-edge" + (" auto" if auto else "") + (" here" if mine else "") + (" route" if came else "") \
                + (" dim" if journey and not came and not mine else "")
            mark = "-route" if came else "-here" if mine else ""
            how = ("its workflow makes it" + (f", running {ran} automation{'s' if ran != 1 else ''}" if ran else "")) \
                if auto else "made by hand"
            paths.append(f'<path class="{cls}" d="{flowmap.path_d(pts)}" marker-end="url(#lc-arrow{mark})">'
                         f'<title>{e(rt.state_label(a))} to {e(rt.state_label(b))}: {e("; ".join(names))} '
                         f'({e(how)})</title></path>')
            if came or mine:
                x, y = lay["labels"][(a, b)]
                num = ""
                if came:
                    nums = ", ".join(str(n) for n in came)
                    wide = 12 + 6 * len(nums)
                    num = (f'<rect class="lc-num" x="{x - wide / 2:.1f}" y="{y - 8:.1f}" width="{wide}" height="16" '
                           f'rx="8"/><text class="lc-num" x="{x:.1f}" y="{y + 3.5:.1f}">{e(nums)}</text>')
                    x += wide / 2 + 4
                words = short(names[0]) + (f" ⚙{ran}" if ran else "")
                anchor = "" if came else ' text-anchor="middle"'
                tags.append((min(came) if came else 10 ** 6,  # read in the order it went, then what is open
                             f'<g class="lc-tag{" route" if came else " here"}">{num}'
                             f'<text class="lc-name" x="{x:.1f}" y="{y + 3.5:.1f}"{anchor}>{e(words)}</text></g>'))
        nodes = []
        tone = lambda s: self._state_tone(s, rt)
        sx, sy = lay["pos"][START]
        nodes.append(f'<g class="lc-begin"><rect x="{sx + W / 2 - 30:.1f}" y="{sy + H - 22:.1f}" width="60" height="22" '
                     f'rx="11"/><text x="{sx + W / 2:.1f}" y="{sy + H - 7:.1f}">Start</text></g>')
        for s in states:
            x, y = lay["pos"][s]
            also = [rt.state_label(b) for b in said.get(s, [])]
            if s in g["any"] and (s not in visited or s == st):
                note = "from any state"
            elif also:
                note = "or: " + ", ".join(also)
            else:
                note = ""
            cur = " current" if s == st else ""
            cls = f"lc-node {tone(s)}" + (" start" if s in g["start"] else "") \
                + (" dim" if journey and s not in visited else "") + cur
            name_y = y + (H / 2 + 4 if not note else 17)
            title = rt.state_label(s) + (" (where it is now)" if cur else "") + (f"; {note}" if note else "") \
                + ("; a record can start here" if s in g["start"] else "")
            nodes.append(f'<g class="{cls}"><title>{e(title)}</title><rect x="{x:.1f}" y="{y:.1f}" width="{W}" '
                         f'height="{H}" rx="4"/><text x="{x + W / 2:.1f}" y="{name_y:.1f}">'
                         f'{e(short(rt.state_label(s), 22).upper())}</text>'
                         + (f'<text class="lc-note" x="{x + W / 2:.1f}" y="{y + 31:.1f}">{e(short(note, 30))}</text>'
                            if note else "") + "</g>")
        width, height = lay["width"], lay["height"]
        marker = lambda mid, cls: (
            f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="9" '
            f'markerHeight="9" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="lc-head{cls}"/></marker>')
        went = (f'; it went {e(", then ".join(rt.state_label(s) for s in walked))}' if len(walked) > 1 else "")
        legend = ['<span class="lc-key cur"></span> where it is now']
        if route:
            legend.append('<span class="lc-key came"></span> the way it came, in order')
        legend.append('<span class="lc-key here"></span> a move you can make from here')
        legend.append('<span class="lc-key auto"></span> only its workflow makes it')
        if journey and any(s not in visited for s in states):
            legend.append('<span class="lc-key faint"></span> not been there')
        wide = " data-wide" if width > 640 else ""  # a broad map asks its dialog for room
        return (f'<div class="lc-wrap"{wide}><svg class="lc-svg" viewBox="0 0 {width:.0f} {height:.0f}" '
                f'width="{width:.0f}" height="{height:.0f}" style="max-width:{width:.0f}px" role="img" '
                f'aria-label="How {e(self._lower(rt.label))} records move: '
                f'{e(", ".join(rt.state_label(s) for s in states))}{went}; now {e(rt.state_label(st))}">'
                f'<defs>{marker("lc-arrow", "")}{marker("lc-arrow-here", " here")}{marker("lc-arrow-route", " route")}'
                f'</defs>' + "".join(paths) + "".join(nodes) + "".join(t for _, t in sorted(tags, key=lambda t: t[0]))
                + "</svg></div>"
                f'<p class="lc-legend muted small">{" ".join(legend)}</p>')
