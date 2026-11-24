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
            if sst.get("state") not in ("active", "waiting", "failed"):
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
        now_steps = [s for s, x in seen if x in ("active", "waiting", "failed")]
        hidden_now = any((run["steps"].get(s.id) or {}).get("state") in ("active", "waiting", "failed")
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
            cls = ("done" if x == "done" else "now" if x in ("active", "waiting", "failed") else
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
        """The record's lifecycle as a diagram (its states and moves, where it is now), for the dialog."""
        me, hub = req["me"], self.hub
        from ..hub import Invalid
        rt = hub.record_type(rtype)
        if not rt or not rt.lifecycle:
            raise Invalid(f"{rtype} records have no lifecycle")
        rec = hub.read_record(me, f"{rtype}/{rid}")
        st = schema.first(rec, rt.lifecycle["path"])
        url = f"/records/{e(rtype)}/{e(rid)}"
        body = (f'<div data-modal-body class="lc-modal"><h2>How {e(self._lower(rt.plural))} move</h2>'
                f'<p class="muted">{e(hub.title(rec))} is <b>{e(rt.state_label(st))}</b>. Its moves from here are '
                f'drawn in blue.</p>{self.lifecycle_svg(rt, st)}{self._lifecycle_table(rt, st)}'
                f'<div class="filters"><a class="btn secondary" href="{url}" data-modal-close>Close</a></div></div>')
        return self.layout(req, f"Workflow: {hub.title(rec)}", body,
                           crumbs=[("/records", "Records"), (f"/records/{rtype}", rt.plural), (url, hub.title(rec)),
                                   (None, "Workflow")])

    def _lifecycle_table(self, rt, st) -> str:
        lc = rt.lifecycle
        rows = []
        for t in lc["transition"]:
            frm = "any state" if "*" in t["from"] else ", ".join(rt.state_label(f) for f in t["from"])
            here = (st in t["from"] or "*" in t["from"]) and t["by"] != "workflow"
            who = "only when its work is cancelled" if t.get("abandon") else \
                "only a workflow" if t["by"] == "workflow" else "anyone who may change it"
            rows.append(f'<tr{" class=here" if here and t["to"] != st else ""}><td>{e(frm)}</td>'
                        f'<td>{e(rt.state_label(t["to"]))}</td><td>{e(t["label"])}</td><td>{e(who)}</td></tr>')
        return ('<details class="lc-moves"><summary>Every move, in words</summary><div class="tablewrap"><table>'
                '<tr><th>From</th><th>To</th><th>Button</th><th>Who</th></tr>' + "".join(rows) + "</table></div></details>")

    def lifecycle_svg(self, rt, st) -> str:
        """The lifecycle drawn as boxes (states) and arrows (moves), top to bottom from the initial state; the
        current state filled, its moves from here in the accent colour, moves only a workflow makes dashed, moves
        from any state as a small "any state" tab above their target."""
        lc = rt.lifecycle
        states = list(lc["states"])
        trans = lc["transition"]
        # layers: how many moves from the initial state, by the moves that name where they come from; a state no
        # move leaves (an end: finished, cancelled, entered in error) goes on the last row, so the arrows run down
        leaves = {f for t in trans for f in t["from"] if f != "*" and f != t["to"]}
        layer = {lc["initial"]: 0} if lc["initial"] in states else {}
        frontier = list(layer)
        while frontier:
            nxt = []
            for s in frontier:
                for t in trans:
                    if s in t["from"] and t["to"] not in layer and t["to"] in leaves:
                        layer[t["to"]] = layer[s] + 1
                        nxt.append(t["to"])
            frontier = nxt
        last = max(layer.values(), default=-1) + 1
        for s in states:
            layer.setdefault(s, last)
        rows: dict[int, list[str]] = {}
        for s in states:
            rows.setdefault(layer[s], []).append(s)
        W, H, GX, GY, PAD, LANE = 136, 34, 20, 64, 16, 9
        inner = max(len(r) for r in rows.values()) * (W + GX) - GX + 2 * PAD + 60
        pos = {}
        for li, (k, row) in enumerate(sorted(rows.items())):
            # each row in the order of where its moves come from, so the arrows cross as little as they can
            src = lambda s: [pos[f][0] for t in trans if t["to"] == s for f in t["from"] if f in pos]
            row.sort(key=lambda s: (sum(src(s)) / len(src(s)) if src(s) else 1e9, states.index(s)))
            rw = len(row) * (W + GX) - GX
            x0 = (inner - 60 - rw) / 2
            for i, s in enumerate(row):
                pos[s] = [x0 + i * (W + GX), PAD + 22 + li * (H + GY)]
        # a move that skips a row runs down a lane at the side nearer its target, not through the boxes between
        lanes: dict = {}
        for t in trans:
            for f in t["from"]:
                if "*" not in t["from"] and f in layer and layer[t["to"]] - layer[f] > 1:
                    side = "L" if pos[t["to"]][0] + W / 2 <= (inner - 60) / 2 else "R"
                    lanes[(id(t), f)] = (side, sum(1 for v in lanes.values() if v[0] == side))
        gl = sum(1 for v in lanes.values() if v[0] == "L") * LANE + 12
        gr = sum(1 for v in lanes.values() if v[0] == "R") * LANE + 12
        for p_ in pos.values():
            p_[0] += gl
        width = gl + inner + gr
        height = PAD + 22 + len(rows) * (H + GY) - GY + PAD
        tone = lambda s: self._state_tone(s, rt)
        edges, any_tabs = [], []
        for t in trans:
            # "a move from here" is per source: only the arrow that leaves the current state, and never one only a
            # workflow makes (the viewer cannot make it)
            mine = t["by"] != "workflow" and t["to"] != st
            look = lambda cur: ("lc-edge" + (" auto" if t["by"] == "workflow" else "") + (" here" if cur else ""),
                                "url(#lc-arrow-here)" if cur else "url(#lc-arrow)")
            tx, ty = pos[t["to"]]
            if "*" in t["from"]:
                cur = mine
                cls, mk = look(cur)
                ax, ay = tx + W / 2, ty - 24
                any_tabs.append(f'<g class="lc-any{" here" if cur else ""}"><rect x="{ax - 38}" y="{ay - 10}" width="76" '
                                f'height="18" rx="9"/><text x="{ax}" y="{ay + 3}">any state</text></g>'
                                f'<path class="{cls}" d="M{ax},{ay + 8} L{ax},{ty - 2}" marker-end="{mk}"/>')
                continue
            for f in t["from"]:
                if f == t["to"] or f not in pos:
                    continue
                fx, fy = pos[f]
                cls, mk = look(mine and f == st)
                if (id(t), f) in lanes:  # down into the gap, along to its lane, down, along, into the target
                    side, k = lanes[(id(t), f)]
                    lx = gl - 8 - k * LANE if side == "L" else gl + inner - 4 + k * LANE
                    x1, y1 = fx + W / 2 + (k % 3 - 1) * 14, fy + H
                    ya = y1 + 10 + (k % 5) * 8
                    yd = ty - 10 - (k % 5) * 8
                    x2 = tx + W / 2 + (k % 5 - 2) * 10
                    d = f"M{x1},{y1} L{x1},{ya} L{lx},{ya} L{lx},{yd} L{x2},{yd} L{x2},{ty - 2}"
                elif layer[t["to"]] > layer[f]:  # down: bottom of one to the top of the other
                    x1, y1, x2, y2 = fx + W / 2, fy + H, tx + W / 2, ty
                    d = f"M{x1},{y1} C{x1},{y1 + GY / 2} {x2},{y2 - GY / 2} {x2},{y2 - 2}"
                elif layer[t["to"]] == layer[f]:  # beside: an arc over the gap
                    left = tx > fx
                    x1, x2 = (fx + W, tx) if left else (fx, tx + W)
                    y = fy + H / 2
                    d = f"M{x1},{y} C{x1 + (12 if left else -12)},{y - 22} {x2 + (-12 if left else 12)},{y - 22} {x2 + (-2 if left else 2)},{y}"
                else:  # back up: out of the right side and round
                    x1, y1, x2, y2 = fx + W, fy + H / 2, tx + W, ty + H / 2
                    bend = max(x1, x2) + 30
                    d = f"M{x1},{y1} C{bend},{y1} {bend},{y2} {x2 + 2},{y2}"
                edges.append(f'<path class="{cls}" d="{d}" marker-end="{mk}"/>')
        nodes = []
        for s in states:
            x, y = pos[s]
            words = rt.state_label(s)
            words = words if len(words) <= 20 else words[:19] + "…"
            cur = " current" if s == st else ""
            start = " start" if s == lc["initial"] else ""
            nodes.append(f'<g class="lc-node {tone(s)}{cur}{start}"><title>{e(rt.state_label(s))}'
                         f'{" (where it is now)" if cur else ""}</title><rect x="{x}" y="{y}" width="{W}" height="{H}" '
                         f'rx="4"/><text x="{x + W / 2}" y="{y + H / 2 + 4}">{e(words.upper())}</text></g>')
        return (f'<div class="lc-wrap"><svg class="lc-svg" viewBox="0 0 {width:.0f} {height:.0f}" width="{width:.0f}" style="max-width:{width:.0f}px" '
                f'height="{height:.0f}" role="img" aria-label="Lifecycle of {e(self._lower(rt.label))}: '
                f'{e(", ".join(rt.state_label(s) for s in states))}; now {e(rt.state_label(st))}">'
                '<defs><marker id="lc-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" '
                'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="lc-head"/></marker>'
                '<marker id="lc-arrow-here" viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" '
                'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="lc-head here"/></marker></defs>'
                + "".join(edges) + "".join(any_tabs) + "".join(nodes) + "</svg></div>"
                '<p class="lc-legend muted small"><span class="lc-key cur"></span> where it is now '
                '<span class="lc-key here"></span> a move from here <span class="lc-key auto"></span> only a workflow '
                'makes it</p>')
