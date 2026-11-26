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
            here = next((x for x in wf.shape["states"] if x["id"] == run.get("at")), None)
            if here and status and run["at"] not in rt.states():  # a stop only the run keeps
                tone = self.TONE_CLASS.get(here.get("tone") or "", "default")
                status[0] = f'<span class="loz {tone}">{e(here.get("label") or run["at"])}</span>'
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
                # the engine's words: "waiting: <a wait step>", "waiting until <when>", "waiting on 2 of 3"
                what = item.get("waiting") or "waiting for the workflow to go on"
                said = (f'<span class="muted">at</span> {e(what.split(": ", 1)[1])}' if what.startswith("waiting: ")
                        else e(what[:1].upper() + what[1:]))
                status.append(f'<span class="rs-item">{said}</span>'
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
                if visible:
                    stuck.append(self._pass_strip(me, run))  # which pass this is, and how the one before ended
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
                    quiet.append(f"<li><b>Cancel “{e(wf.title)}”</b>: {e(why)}</li>")
                else:
                    ends.append(f'<details class="cancel-work"><summary class="btn subtle">Cancel '
                                f'“{e(wf.title)}”…</summary><form method="post" '
                                f'action="/runs/{e(run["id"])}/cancel" class="inline">'
                                f'<input type="hidden" name="_back" value="{e(back)}"><label>Why it is cancelled '
                                f'<input name="reason" required></label> <button class="danger">Cancel this work'
                                f'</button></form></details>')
        moves = self._move_buttons(rt, rec, st, url, screen["may"]) if screen["may"] else ""
        for m in screen["may_not"]:
            quiet.append(f"<li><b>{e(m['label'])}</b>: {e(m['why'])}</li>")
        moved_err = again.get("error") if not again.get("iid") else None
        key = hub.key_of(ref)  # its key first, as an issue's (T1-12): what people quote for it
        if key:
            status.insert(0, f'<a class="rs-item key" href="/{e(key)}" title="Its key: this address opens it">{e(key)}</a>')
        head = (f'<div class="rec-status" aria-label="Where it is">{"".join(status)}</div>' if status else "")
        parts = [p for p in stuck if p]
        if moved_err is not None:
            parts.insert(0, f'<div class="flash error" role="alert"><p>{e(str(moved_err))}</p></div>')
        if rt is not None:  # what the latest move set going, from work of any status (a closed one's open follow-up)
            parts.append(self._activity_html(me, ref, url))
            parts = [p for p in parts if p]
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

    # ------------------------------------------------------------------------------- what a move set going
    MESSAGE_WORDS = {"queued": "waiting to be sent", "sending": "being sent", "sent": "sent",
                     "delivered": "delivered", "failed": "not delivered"}
    RECENT = 600  # seconds: a settled move this recent still shows its list open

    def activity_of(self, me, ref: str) -> list[dict]:
        """``Hub.record_activity``, read once a request: the block, History and "Started from" all ask for it."""
        cache = getattr(self._local, "activity", None)
        if cache is None:
            return self.hub.record_activity(me, ref)
        key = (me.get("id"), ref)
        if key not in cache:
            cache[key] = self.hub.record_activity(me, ref)
        return cache[key]

    def activity_url(self, ref: str) -> str:
        """Where the block alone is fetched from: the record's flat address (a part's too), never a run's."""
        return f"/records/{ref}/activity"

    def _activity_html(self, me, ref: str, url: str) -> str:
        """The block in the work section: what the latest move (a person's, with the moves that followed on from it
        by themselves) ran and started, each in words with its outcome, from ``Hub.record_activity``. Empty when the
        record has no such move or the move ran nothing. ``url``: the record page's address, already escaped. While
        anything is still going on, the section carries ``data-live`` (the script fetches it again) and a Refresh
        link; ``data-at`` is the record's version and its work's last change, so the script can tell a page that
        has moved on from one whose follow-ups only changed."""
        from .records_pages import ran_summary  # History's words for the same list
        hub = self.hub
        moves = self.activity_of(me, ref)
        if not moves:
            return ""
        first = max((i for i, m in enumerate(moves) if m.get("after_person")), default=len(moves) - 1)
        cascade = moves[first:]
        if not any(m.get("ran") for m in cascade):
            return ""
        settled = all(m.get("settled") for m in cascade)
        ran = [r for m in cascade for r in m.get("ran") or []]
        lead = cascade[0]
        when = (f'{self.who(lead["who"])} ' if lead.get("who") else "") + (self._at(lead["at"]) if lead.get("at")
                                                                           else "")
        recent = False
        if lead.get("at") or cascade[-1].get("at"):
            from datetime import datetime, timezone
            try:
                at = datetime.fromisoformat(str(cascade[-1].get("at") or lead["at"]).replace("Z", "+00:00"))
                if at.tzinfo is None:
                    at = at.replace(tzinfo=timezone.utc)
                recent = (datetime.now(timezone.utc) - at).total_seconds() < self.RECENT
            except ValueError:
                recent = False
        body = [self._ran_list_html(me, lead.get("ran") or [])]
        for m in cascade[1:]:
            body.append(f'<h4 class="ra-then">Then, “{e(m.get("label") or "")}”</h4>'
                        + self._ran_list_html(me, m.get("ran") or []))
        live = ""
        if not settled:
            live = (f'<p class="ra-live"><span class="muted small">Some of this is still going on.</span> '
                    f'<a href="{url}#activity">Refresh</a></p>')
        rec = hub.records.read(*ref.split("/", 1)) or {}
        runs = list(hub.db.runs.find({"record": ref}))
        changed = max((str(r.get("updated_at") or r.get("created_at") or "") for r in runs), default="")
        at_attr = f'{((rec.get("meta") or {}).get("versionId", ""))}|{changed}'
        data_live = f' data-live="{e(self.activity_url(ref))}"' if not settled else ""
        if any(r.get("state") in ("queued", "running") for r in ran):
            data_live += " data-busy"  # a job in the queue or under way: the script asks again sooner
        opened = " open" if not settled or recent else ""
        return (f'<section id="activity" class="rw-activity" aria-labelledby="rw-activity-h" data-at="{e(at_attr)}"'
                f'{data_live}><details class="ra-fold"{opened}><summary><h3 id="rw-activity-h" class="ra-h">'
                f'After “{e(lead.get("label") or "")}”</h3>'
                + (f' <span class="muted small ra-when">{when}</span>' if when else "")
                + f' <span class="muted small ra-sum">{e(ran_summary(ran))}</span></summary>'
                + "".join(body) + live + "</details></section>")

    def _ran_list_html(self, me, ran: list[dict]) -> str:
        """What one move ran, one line per automation: its title and, in words, done, did not go through (with what
        went wrong) or waiting on the one that did not; a message's own status (waiting to be sent, sent, not
        delivered); and under it each record it started, as it is now: its state and where its own work is (Done,
        Stopped, Stuck, or who holds it and when it is due). One the member may not read keeps its kind, state and
        holder but neither its title nor a link. Automations kept from the member fold to one line. Shared by the
        work section's block and the History tab."""
        hub, viewer = self.hub, Viewer(self.hub, me)
        failed = next((r for r in ran if r.get("state") == "failed"), None) \
            or next((r for r in ran if r.get("state") in ("running", "queued")), None)
        blocker = ("a step you may not see" if failed is None or failed.get("hidden")
                   else f"“{failed.get('title') or ''}”")
        lines, hidden = [], 0
        for r in ran:
            if r.get("hidden"):
                hidden += 1
                continue
            state = r.get("state")
            title = e(r.get("title") or "")
            if state == "failed":
                said = '<span class="ra-mark" aria-hidden="true">✕</span> <span class="ra-word bad">did not go through</span>'
                if r.get("error"):
                    said += f' <span class="muted small">{e(str(r["error"])[:200])}</span>'
            elif state == "held":
                said = (f'<span class="ra-mark" aria-hidden="true">…</span> '
                        f'<span class="ra-word">waiting on {e(blocker)}</span>')
            elif state == "queued":
                said = ('<span class="ra-mark" aria-hidden="true">…</span> '
                        '<span class="ra-word going">in the queue</span>')
            elif state == "running":
                said = ('<span class="ra-mark"><span class="jr-spin" aria-hidden="true"></span></span> '
                        '<span class="ra-word going">under way</span>'
                        + (f' <span class="muted small">since {self._at(r["since"])}</span>' if r.get("since") else ""))
            elif r.get("message"):
                word = self.MESSAGE_WORDS.get(r["message"], r["message"])
                mark = "✕" if r["message"] == "failed" else "✓" if r["message"] in ("sent", "delivered") else "…"
                said = (f'<span class="ra-mark" aria-hidden="true">{mark}</span> '
                        f'<span class="ra-word{" bad" if r["message"] == "failed" else ""}">{e(word)}</span>')
            else:
                said = '<span class="ra-mark" aria-hidden="true">✓</span> <span class="ra-word">done</span>'
                if r.get("ms") is not None:  # a job that ran by itself: how long it took
                    took = f'{r["ms"] / 1000:.1f} s' if r["ms"] >= 1000 else "under a second"
                    said += f' <span class="muted small">in {e(took)}</span>'
            for t in r.get("retried") or []:
                said += (f' <span class="muted small ra-retried">tried again by '
                         f'{e((t.get("by") or {}).get("name") or "someone")}'
                         + (f': {e(str(t["reason"])[:120])}' if t.get("reason") else "") + "</span>")
            kids = "".join(self._started_html(me, s, viewer) for s in r.get("started") or [])
            lines.append(f'<li class="ra-{e(str(state or ""))}"><b>{title}</b>: {said}'
                         + (f'<ul class="plain ra-started">{kids}</ul>' if kids else "") + "</li>")
        if hidden:
            lines.append(f'<li class="muted ra-hidden">{"a step" if hidden == 1 else f"{hidden} steps"} '
                         f'you may not see</li>')
        return f'<ul class="plain ra-list">{"".join(lines)}</ul>' if lines else ""

    def _started_html(self, me, s: dict, viewer) -> str:
        """One record a move started, as it is now (``started_from``'s words for it)."""
        from .. import schema
        hub = self.hub
        ref = str(s.get("ref") or "")
        run = s.get("run")
        if isinstance(run, str):
            row = hub.db.runs.get(run) if run else None
            run = {**row, "id": row.get("id") or row.get("_id") or run} if row else None
        if "/" not in ref:
            ref = str((run or {}).get("record") or "")
        if "/" not in ref:
            return ""
        rtype = ref.split("/", 1)[0]
        krt = hub.record_type(rtype)
        rec = hub.records.read(*ref.split("/", 1))
        st = schema.first(rec, krt.lifecycle["path"]) if rec and krt and krt.lifecycle else None
        readable = s.get("readable", True) and hub.may_read(me, rtype)
        name = (self._family_link(ref, rec) if readable and rec else
                f'<span class="muted">{e(krt.label if krt else "a record")}</span>')
        work = self._child_work(run or {}, viewer)[0]
        return f'<li>{name} {self._state_loz(st, krt) if st else ""} {work}</li>'

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
            asks = m["asks"] or m["requires"] or m["sign"] or m["comment"] or m.get("assign")
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
