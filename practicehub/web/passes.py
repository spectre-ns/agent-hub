"""The passes of a record whose lifecycle loops, and what a person signs over.

A record's own workflow walks it one pass per run (a revision returned and revised, a CAPA found not effective, a
change order rejected): each run's ``ctx.run.previous`` is the pass before. Here, generically, for any record type:

- which pass a run is, the pass before and after it, and how the pass before ended (who decided what, when, and
  their comment), for the item and run pages;
- for a decision about the run's own record, that record open and formatted above the Decide box, with what changed
  since the version last in use (the approved revision);
- for a wait over several records, each of them with where it is and who holds it;
- when separation of duties keeps every holder of a step's role out of it, a warning that names them and why.

Nothing here knows any one record type, workflow or module; what is shown is what the member may see.
"""

from __future__ import annotations

import difflib
import html

from .. import schema
from ..visibility import Viewer

e = html.escape


class PassPages:
    # ------------------------------------------------------------------------------------------ passes
    def _pass_runs(self, run: dict) -> list[dict]:
        """The run and every pass before it, earliest first (a broken or looping chain stops where it breaks)."""
        chain, seen, cur = [run], {run["id"]}, run
        while True:
            prev = ((cur.get("ctx") or {}).get("run") or {}).get("previous")
            if not prev or prev in seen:
                break
            row = self.hub.db.runs.get(prev)
            if not row or row.get("record") != run.get("record"):
                break
            cur = self.hub.engine.run(prev)
            chain.append(cur)
            seen.add(prev)
        return list(reversed(chain))

    def _next_pass(self, run: dict) -> dict | None:
        row = self.hub.db.runs.find_one({"ctx.run.previous": run["id"], "record": run.get("record")}, fields=["_id"])
        return self.hub.engine.run(row["_id"]) if row else None

    def _pass_ending(self, me, run: dict) -> dict | None:
        """How a pass ended: its last decision (step, outcome, who, when, comment), as this member may see it."""
        try:
            wf, _ = self.hub.workflow(run["workflow_id"], run["version"])
        except Exception:  # noqa: BLE001 - a workflow since removed
            return None
        viewer = Viewer(self.hub, me)
        best = None
        for s in wf.steps:
            got = (run.get("ctx") or {}).get(s.id)
            if s.kind != "gate" or not isinstance(got, dict) or not got.get("outcome"):
                continue
            if not viewer.step_visible(wf, s.id)[0]:
                continue
            if best is None or (got.get("event") or 0) > (best["answer"].get("event") or 0):
                best = {"step": s, "answer": got}
        if not best:
            return None
        ev = self.hub.db.events.find_one({"seq": best["answer"].get("event")}, fields=["at"]) \
            if best["answer"].get("event") else None
        return {"title": best["step"].title, "outcome": best["answer"]["outcome"], "by": best["answer"].get("by"),
                "at": (ev or {}).get("at"), "comment": best["answer"].get("comment") or ""}

    def _pass_outcome_words(self, me, run: dict) -> str:
        """For a finished run: how it ended, in words (its last decision, who took it and when), not only its status."""
        if run.get("status") == "running":
            return ""
        ended = self._pass_ending(me, run)
        if not ended:
            return ""
        when = f" on {self._at(ended['at'])}" if ended.get("at") else ""
        return (f" · ended at {e(ended['title'])}: <b>{e(self.human(ended['outcome']))}</b> by "
                f"{e(self.member_name(ended['by']) or 'the workflow')}{when}")

    def _pass_number(self, run: dict) -> int:
        return len(self._pass_runs(run)) if run.get("record") else 1

    def _pass_strip(self, me, run: dict) -> str:
        """Which pass this is, how the pass before ended (in words, with the decider's comment), and links to the
        passes before and after. Nothing for a run walked once."""
        if not run.get("record"):
            return ""
        chain = self._pass_runs(run)
        nxt = self._next_pass(run)
        if len(chain) == 1 and not nxt:
            return ""
        bits = [f"<b>Pass {len(chain)}</b>"]
        if len(chain) > 1:
            prev = chain[-2]
            ended = self._pass_ending(me, prev)
            if ended:
                when = f" on {self._at(ended['at'])}" if ended.get("at") else ""
                said = f": “{e(ended['comment'])}”" if ended["comment"] else ""
                bits.append(f"the pass before ended at {e(ended['title'])}: <b>{e(self.human(ended['outcome']))}</b> "
                            f"by {e(self.member_name(ended['by']) or 'the workflow')}{when}{said}")
        if nxt:
            bits.append(f"pass {self._pass_number(nxt)} has begun")
        return f'<div class="card passes"><p>{" · ".join(bits)}</p></div>'

    # ------------------------------------------------------------------------------------------ what is signed
    def _signed_record_view(self, me, run: dict, step) -> str:
        """For a decision about the run's own record: that record as it is now, open and formatted (its text as
        text, not data), and what changed since the version of it last in use. The signature covers it already
        (``shown_digest`` reads the record); this is so the signer reads it in full, here, before Decide."""
        ref = run.get("record") or ""
        if not step or step.kind != "gate" or "/" not in ref:
            return ""
        hub = self.hub
        rtype, rid = ref.split("/", 1)
        rt = hub.record_type(rtype)
        if rt is None:
            return ""
        try:
            rec = hub.read_record(me, ref)
        except Exception:  # noqa: BLE001 - not theirs to read: the context says what it needs
            return ""
        lc = rt.lifecycle or {}
        skip = {lc.get("field")}
        rows = []
        for f in rt.fields:
            if f["name"] in skip or f.get("advanced") or "*" in f["path"]:
                continue
            vals = [v for v in schema.get(rec, f["path"]) if v not in (None, "", [], {})]
            if not vals:
                continue
            rows.append(f"<dt>{e(f['label'])}</dt><dd>{self._value_html(f, rec)}</dd>")
        version = hub.record_version(ref)
        diff = self._since_in_use(me, rt, ref, rec)
        return (f'<div class="section signed-record"><h2>What you sign: {e(hub.short_title(rec) or rt.label)}, version '
                f'{version}</h2><div class="card"><dl>{"".join(rows)}</dl></div>{diff}</div>')

    def _since_in_use(self, me, rt, ref: str, rec: dict) -> str:
        """What changed, line by line, since the last version in the lifecycle's ``in_use`` state (the revision staff
        work from), or since it was made, when it was never in use."""
        lc = rt.lifecycle or {}
        try:
            versions = self.hub.record_history(me, ref)  # newest first
        except Exception:  # noqa: BLE001
            return ""
        base, label = None, ""
        for v in versions[1:]:
            if lc.get("in_use") and schema.first(v["data"], lc["path"]) == lc["in_use"]:
                base, label = v, f"the version in use (version {v.get('version')})"
                break
        if base is None:
            return '<p class="muted">Nothing of it was in use before: all of it is new.</p>' if len(versions) > 1 \
                else ""
        changes = []
        for f in rt.fields:
            if f["name"] == lc.get("field") or "*" in f["path"]:
                continue
            old = self._field_text(base["data"], f)
            new = self._field_text(rec, f)
            if old == new:
                continue
            lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=1))[2:]
            body = "".join(
                f'<div class="{"ins" if ln.startswith("+") else "del" if ln.startswith("-") else "muted"}">'
                f'{e({"+": "+ ", "-": "− "}.get(ln[:1], "  ") + ln[1:])}</div>'
                for ln in lines if not ln.startswith("@@"))
            changes.append(f"<dt>{e(f['label'])}</dt><dd class='diff'>{body}</dd>")
        if not changes:
            return f'<p class="muted">Unchanged since {e(label)}.</p>'
        return (f'<details class="card" open><summary><b>What changed since {e(label)}</b></summary>'
                f'<dl>{"".join(changes)}</dl></details>')

    @staticmethod
    def _field_text(rec: dict, f) -> str:
        vals = [v for v in schema.get(rec, f["path"]) if v not in (None, "", [], {})]
        out = []
        for v in vals:
            for x in (v if isinstance(v, list) else [v]):
                out.append(x if isinstance(x, str) else str(schema.ref_of(x) or x))
        return "\n".join(out)

    # ------------------------------------------------------------------------------------------ waits and blocks
    def _wait_records_html(self, me, run: dict, step) -> str:
        """A wait over several records (``for_each``): each with its title, where it is now and who holds its work."""
        if not step or step.kind != "wait" or not step.for_each:
            return ""
        hub, eng = self.hub, self.hub.engine
        from .. import workflows as wfmod
        try:
            got = wfmod.render(step.for_each, eng._live_ctx(run)) or []
        except Exception:  # noqa: BLE001
            return ""
        refs = [eng._ref_of(x) for x in (got if isinstance(got, list) else [got])]
        rows = []
        for ref in refs:
            if not isinstance(ref, str) or "/" not in ref:
                continue
            rtype = ref.split("/", 1)[0]
            rt = hub.record_type(rtype)
            if rt is None or not self._may_read(me, rtype):
                rows.append('<li class="muted">A record you may not read</li>')
                continue
            rec = hub.records.read(*ref.split("/", 1))
            if not rec:
                continue
            state = rt.state_label(schema.first(rec, rt.lifecycle["path"])) if rt.lifecycle else ""
            bits = [self.record_link(ref)]
            if state:
                bits.append(f'<span class="badge">{e(state)}</span>')
            for other in eng.runs(record=ref, status="running", limit=5):
                item = hub.db.items.find_one({"_id": other.get("item")}) if other.get("item") else None
                if not item or item.get("status") not in ("open", "claimed", "held"):
                    continue
                who = self.member_name(item.get("claimed_by") or item.get("assignee")) \
                    or self._role_words(item.get("role")) or ""
                due = f", due {self._at(item['due_at'])}" if item.get("due_at") else ""
                bits.append(f'<a href="/items/{e(item["_id"])}">{e(item.get("step_title") or "")}</a>'
                            f'{" with " + e(who) if who else ""}{due}')
            rows.append(f"<li>{' '.join(bits)}</li>")
        if not rows:
            return ""
        return f'<ul class="plain wait-records">{"".join(rows)}</ul>'

    def _blocked_html(self, item: dict | None) -> str:
        """When every holder of the item's role is kept out of it by separation of duties: who, and why."""
        if not item:
            return ""
        blocked = self.hub.engine.blocked_holders(item)
        if not blocked:
            return ""
        who = "".join(f"<li>{e(m['name'])}: {e(why)}</li>" for m, why in blocked)
        return (f'<div class="error blocked" role="alert"><p><b>Nobody may take this.</b> Everyone who holds the role '
                f'{e(self._role_words(item.get("role")) or item.get("role") or "")} is kept out of it:</p>'
                f'<ul>{who}</ul><p>A policy owner has been told. Give the role to someone else to go on.</p></div>')

    @staticmethod
    def _open_wait(run: dict, wf):
        """The wait the run's item is waiting on now, if any."""
        for s in wf.steps:
            if s.kind == "wait" and (run.get("steps", {}).get(s.id) or {}).get("state") == "waiting":
                return s
        return None

    # ------------------------------------------------------------------------------------------ history in words
    def _history_in_words(self, me, rt, ref: str, versions: list[dict]) -> str:
        """A record's history as sentences, newest first: one row per change, each changed field with what it was
        and what it became; the decisions its workflows took about it (who, how, the comment, what they signed)
        between them; each row with the pass it belongs to; and who did it by name, or the workflow that did it
        (never the bookkeeping actor). The stored versions stay behind a Raw toggle."""
        import json
        runs, wfs = {}, {}

        def run_of(rid):
            if rid and rid not in runs:
                try:
                    runs[rid] = self.hub.engine.run(rid)
                except Exception:  # noqa: BLE001 - a run since removed
                    runs[rid] = None
            return runs.get(rid) if rid else None

        def wf_of(run):
            key = (run["workflow_id"], run["version"])
            if key not in wfs:
                try:
                    wfs[key] = self.hub.workflow(*key)[0]
                except Exception:  # noqa: BLE001
                    wfs[key] = None
            return wfs[key]

        def pass_label(run):
            if not run:
                return ""
            wf = wf_of(run)
            name = wf.title if wf else run["workflow_id"]
            return f"{e(name)}, pass {self._pass_number(run)}"

        def by_whom(actor, run):
            try:
                m = self.hub.member(actor) if actor else None
            except Exception:  # noqa: BLE001
                m = None
            if m and m.get("kind") == "person":
                return e(m["name"])
            if run:
                wf = wf_of(run)
                return f"the {e(wf.title if wf else run['workflow_id'])} workflow"
            return e(m["name"]) if m else "the hub"

        def short(t):
            return t if len(t) <= 160 else t[:157] + "…"

        lc = rt.lifecycle or {}
        rows = []  # (at, html)
        for i, v in enumerate(versions):
            prev = versions[i + 1]["data"] if i + 1 < len(versions) else None
            run = run_of(v.get("run_id"))
            said = []
            for f in rt.fields:
                if "*" in f["path"]:
                    continue
                new = self._field_text(v["data"], f)
                old = self._field_text(prev, f) if prev is not None else ""
                if new == old:
                    continue
                if f["name"] == lc.get("field"):
                    old, new = (rt.state_label(old) if old else ""), (rt.state_label(new) if new else "")
                if prev is None:
                    said.append(f"<li>{e(f['label'])}: <b>{e(short(new))}</b></li>")
                elif not new:
                    said.append(f"<li>{e(f['label'])} cleared (was {e(short(old))})</li>")
                elif not old:
                    said.append(f"<li>{e(f['label'])} set to <b>{e(short(new))}</b></li>")
                else:
                    said.append(f"<li>{e(f['label'])}: {e(short(old))} → <b>{e(short(new))}</b></li>")
            what = "Made" if prev is None else "Changed"
            note = f'<p class="muted">“{e(v["comment"])}”</p>' if v.get("comment") else ""
            body = (f'<ul class="plain">{"".join(said)}</ul>' if said
                    else '<p class="muted">Nothing you can see changed.</p>')
            cmp_ = (f' <a href="/records/{e(rt.id)}/{e(ref.split("/", 1)[1])}/compare?a={versions[i + 1]["version"]}'
                    f'&amp;b={v["version"]}">Compare</a>') if prev is not None else ""
            rows.append((str(v.get("at") or ""), f'<tr><td>{what} by {by_whom(v.get("actor"), run)}{note}{body}</td>'
                                                 f'<td>{pass_label(run)}</td><td class="muted">{self._at(v["at"])}'
                                                 f'<br>v{v["version"]}{cmp_}</td>'
                                                 f'</tr>'))
        viewer = Viewer(self.hub, me)
        for run in self.hub.engine.runs(record=ref, limit=200):
            runs.setdefault(run["id"], run)
            wf = wf_of(run)
            if not wf:
                continue
            for s in wf.steps:
                got = (run.get("ctx") or {}).get(s.id)
                if s.kind != "gate" or not isinstance(got, dict) or not got.get("outcome"):
                    continue
                if not viewer.step_visible(wf, s.id)[0]:
                    continue
                ev = self.hub.db.events.find_one({"seq": got.get("event")}, fields=["at", "data"]) \
                    if got.get("event") else None
                at = (ev or {}).get("at") or ""
                meaning = (s.meanings or {}).get(got["outcome"]) or s.meaning or ""
                signed = f' and signed “{e(meaning)}”' if meaning else ""
                said = f'<p class="muted">“{e(got["comment"])}”</p>' if got.get("comment") else ""
                rows.append((str(at), f'<tr class="decision"><td><b>{e(s.title)}</b>: '
                                      f'{e(self.human(got["outcome"]))}, decided by {by_whom(got.get("by"), run)}'
                                      f'{signed}{said}</td><td>{pass_label(run)}</td>'
                                      f'<td class="muted">{self._at(at) if at else ""}</td></tr>'))
        if not rows:
            return "<p class='muted'>No history.</p>"
        rows.sort(key=lambda r: r[0], reverse=True)
        raw = "".join(f'<h3>Version {v["version"]}</h3><pre>{e(json.dumps(v["data"], indent=1, default=str))}</pre>'
                      for v in versions)
        return ("<div class='tablewrap'><table class='history-words'><tr><th>What happened</th><th>Pass</th>"
                "<th>When</th></tr>" + "".join(r for _, r in rows) + "</table></div>"
                f"<details class='raw'><summary>Raw</summary>{raw}</details>")
