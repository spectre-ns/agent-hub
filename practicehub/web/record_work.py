"""A record's work, on the record's own page: where it is, who holds it, and what can be done now.

Like an issue in a tracker, the record is the page people work on. Next to its title: its state, who holds the step it
is at, when that is due and, for a record walked more than once, which pass this is. Under the title, its work: a stuck
step (one that did not go through, nobody may take, or a wait), the moves the member may make now as buttons
(``Hub.transition_screen``; a move that asks for fields, needs something filled in, or is signed opens its transition
screen first), the form of the step it is at when that step is not itself one of the moves (a task with its own
answer, a decision), the step's claim, give back and take over, and cancelling the work. The moves the member may not
make, each with why, are in a quiet disclosure. The steps themselves are told in the History tab
(``Hub.record_timeline``); a run's own page is only for work about no record.

What a member may not see stays out (``visibility.Viewer``): a step whose data they may not read is not named, only
that the work is at a step kept from them and who holds it.
"""

from __future__ import annotations

import html
import urllib.parse

from ..visibility import Viewer

e = html.escape


class RecordWork:
    # ------------------------------------------------------------------------------------------ where work lives
    def run_href(self, run) -> str:
        """Where a piece of work is worked on: its record's page (at its work), else, for work about no record, the
        run's own page. ``run``: a run, or its id."""
        if isinstance(run, str):
            row = self.hub.db.runs.get(run) if run else None
            if not row:
                return f"/runs/{run}"
            run = {**row, "id": row.get("id") or row.get("_id") or run}
        ref = run.get("record")
        if ref and "/" in str(ref):
            return self.record_href(ref) + "#work"
        return f"/runs/{run['id']}"

    def own_runs(self, me, ref: str) -> list[tuple[dict, object]]:
        """The running work walking this record (its ``record``), each with the workflow the member sees it by."""
        return [(run, wf) for run, wf in self.open_runs(me, ref) if run.get("record") == ref]

    # ------------------------------------------------------------------------------------------ the header
    def record_work(self, req, ref: str, rt, rec: dict | None, st, url: str, *, editing: bool = False
                    ) -> tuple[str, str]:
        """(the status line beside the title, the work section under it), as HTML. ``req["_work"]``, after a refused
        step or move sent from this page: ``{"iid", "kept", "error"}``."""
        me, hub, eng = req["me"], self.hub, self.hub.engine
        viewer = Viewer(hub, me)
        back = f"{url}#work"
        again = req.get("_work") or {}
        status: list[str] = [self._state_loz(st, rt)] if rt is not None and rt.lifecycle and st is not None else []
        stuck: list[str] = []
        steps: list[str] = []
        quiet: list[str] = []
        ends: list[str] = []
        screen = (hub.transition_screen(me, ref) if rt is not None and rt.lifecycle and not editing
                  else {"may": [], "may_not": []})
        by_move = {m["to"] for m in screen["may"] if m.get("does")}
        for run, wf in self.own_runs(me, ref):
            n = self._pass_number(run)
            if n > 1:
                status.append(f'<span class="rs-item">pass {n}</span>')
            for sid, sst in (run.get("steps") or {}).items():
                if (sst or {}).get("state") != "failed":
                    continue
                shown = viewer.step_state(wf, run, sid) is not None
                title = wf.step(sid).title if shown else ""
                said = (viewer.run_withheld(run) or str(sst.get("error") or "")) if shown else ""
                retry = ""
                if shown and hub.is_policy_owner(me):
                    retry = (f'<form method="post" action="/runs/{e(run["id"])}/steps/{e(sid)}/retry" class="inline">'
                             f'<input type="hidden" name="_back" value="{e(back)}">'
                             f'<label>What was fixed <input name="reason" required></label> '
                             f'<button>Try it again</button></form>')
                elif shown:
                    retry = '<span class="muted">A policy owner has been told and can try it again.</span>'
                stuck.append(f'<div class="flash error stuck" role="alert"><p><b>Stuck: '
                             + (f"“{e(title)}” did not go through" if shown else "a step you may not see did not go "
                                                                               "through")
                             + "</b>" + (f' <span class="muted">{e(said[:200])}</span>' if said else "") + "</p>"
                             + retry + "</div>")
            item = hub.db.items.get(run["item"]) if run.get("item") else None
            if item:
                item = {**item, "id": item.get("id") or item.get("_id")}
            live = item and item.get("status") in ("open", "claimed", "held", "waiting")
            step = None
            if live and item.get("step_id"):
                try:
                    step = wf.step(item["step_id"])
                except KeyError:
                    step = None
            visible = bool(live and step and self.may_see_item(me, item, run))
            if live and item["status"] == "waiting":
                wait = self._open_wait(run, wf)
                what = item.get("waiting") or "the workflow to go on"
                status.append(f'<span class="rs-item"><span class="muted">waiting for</span> {e(what)}</span>'
                              if visible or not step else
                              '<span class="rs-item muted">waiting at a step you may not see</span>')
                if visible:
                    stuck.append(self._wait_records_html(me, run, wait))
            elif live:
                where = (f"<b>{e(step.title)}</b>" if visible else
                         '<span class="muted">a step you may not see</span>')
                status.append(f'<span class="rs-item"><span class="muted">at</span> {where}</span>'
                              f'<span class="rs-item">{self.holder_html(item)}{self._urgency(item)}</span>')
                stuck.append(self._blocked_html(item))
                if visible and item["kind"] in ("task", "gate"):
                    done_by_move = step.state and step.state in by_move
                    mine = again if again.get("iid") == item["id"] else {}
                    form = ""
                    if not done_by_move and eng.eligibility(me, item)[0]:
                        err = mine.get("error")
                        form = ((f'<div class="flash error" role="alert"><p>That did not go through: {e(str(err))}'
                                 f'</p><p>Nothing was saved. What you typed is kept below.</p></div>' if err else "")
                                + self._work_form(me, item, item["id"], step, mine.get("kept"), back=back,
                                                  on_record=True))
                    verbs = self._item_actions(me, item, back=back)
                    if form or verbs:
                        steps.append(f'<div class="rw-step"><h3>{e(step.title)}</h3>'
                                     + (f'<div class="rw-verbs">{verbs}</div>' if verbs else "") + form + "</div>")
                elif visible and item["kind"] == "hold":
                    steps.append(f'<p><a class="btn secondary" href="/items/{e(item["id"])}">Open the approval</a></p>')
            # the work as a whole: cancelling it, or why not
            if run["status"] == "running":
                why = eng.cancel_refusal(me, run)
                if why:
                    quiet.append(f"<li><b>Cancel {e(wf.title.lower())}</b>: {e(why)}</li>")
                else:
                    ends.append(f'<details class="cancel-work"><summary class="btn subtle">Cancel '
                                f'{e(wf.title.lower())}…</summary><form method="post" '
                                f'action="/runs/{e(run["id"])}/cancel" class="inline">'
                                f'<input type="hidden" name="_back" value="{e(back)}"><label>Why it is cancelled '
                                f'<input name="reason" required></label> <button class="danger">Cancel this work'
                                f'</button></form></details>')
        moves = self._move_buttons(rt, rec, st, url, screen["may"]) if screen["may"] else ""
        for m in screen["may_not"]:
            quiet.append(f"<li><b>{e(m['label'])}</b>: {e(m['why'])}</li>")
        moved_err = again.get("error") if not again.get("iid") else None
        head = (f'<div class="rec-status" aria-label="Where it is">{"".join(status)}</div>' if status else "")
        parts = [p for p in stuck if p]
        if moved_err is not None:
            parts.insert(0, f'<div class="flash error" role="alert"><p>{e(str(moved_err))}</p></div>')
        if moves:
            parts.append(f'<div class="rw-moves" role="group" aria-label="Move it on">{moves}</div>')
        parts += steps
        if quiet:
            parts.append(f'<details class="rw-not"><summary>What you may not do here, and why</summary>'
                         f'<ul class="plain">{"".join(quiet)}</ul></details>')
        parts += ends
        body = (f'<section id="work" class="rec-work" aria-label="Its work">{"".join(parts)}</section>'
                if parts else '<span id="work"></span>')
        return head, body

    def _move_buttons(self, rt, rec, st, url: str, may: list[dict]) -> str:
        """The moves a member may make now, as buttons: the forward one loud, a correction or an ending in red, the
        rest quiet. One that asks for something (fields, a filled-in field, a comment, a signature) opens its screen;
        one that asks nothing is made at once, after a confirmation that says what follows."""
        trans = {m["to"]: rt.transition(st, m["to"]) for m in may}
        open_now, later = self.moves_open_now(rt, [t for t in trans.values() if t], rec or {})
        ok = {t["to"] for t in open_now}
        # the main button: the move the open step makes, else the one forward by the lifecycle's shape (never a side
        # move that leads back, like the patient stepping out, nor an ending)
        ahead = next((m["to"] for m in may if m.get("does") and m["to"] in ok), None)
        if ahead is None:
            fwd = self.forward_move(rt.lifecycle, st, open_now) if open_now else None
            ahead = fwd["to"] if fwd else None
        out = []
        for m in may:
            t = trans.get(m["to"])
            if t is None or m["to"] not in ok:
                continue
            kind = "danger-subtle" if self._undoing(t) else "primary" if m["to"] == ahead else "secondary"
            asks = m["asks"] or m["requires"] or m["sign"] or m["comment"]
            if asks:
                out.append(f'<a class="btn {kind}" href="{url}/move?to={e(urllib.parse.quote(m["to"]))}" '
                           f'data-modal>{e(m["label"])}…</a>')
                continue
            said = self.move_confirm(rt, t, rec or {})
            confirm = (f' data-confirm="{e(said)}"' if said else
                       f' data-confirm="Move it to {e(m["state_label"].lower())}?"' if kind != "primary" else "")
            out.append(f'<form method="post" action="{url}/move" class="inline">'
                       f'<input type="hidden" name="to" value="{e(m["to"])}">'
                       f'<input type="hidden" name="_version" value="{e(str(((rec or {}).get("meta") or {}).get("versionId", "")))}">'
                       f'<button class="{kind}"{confirm}>{e(m["label"])}{"…" if said else ""}</button></form>')
        return "".join(out) + later
