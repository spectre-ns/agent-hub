"""Pages for the practice's definitions under change control: record types (fields, lifecycles, signature meanings),
views (boards, lists, dashboards) and skills. A manager changes them here, without touching files on the server.

Saving is a proposal; a policy owner who did not write it approves and signs it, after reading what it changes.
Agents may propose (definitions are data, never code); only people approve.
"""

from __future__ import annotations

import difflib
import tomllib
from html import escape as e

KINDS = {"types": ("type", "Record types", "record type"), "views": ("view", "Views", "view"),
         "skills": ("skill", "Skills", "skill")}
HELP = {
    "type": ('<li><code>[[field]]</code>: <code>name</code>, <code>label</code>, <code>type</code> (text, number, date, '
             'datetime, choice, member, reference...), <code>required</code>, <code>choices</code>.</li>'
             '<li><code>[lifecycle]</code>: <code>field</code>, <code>initial</code>, <code>locked</code> (states in which '
             'people change nothing but the state), <code>abandoned</code> (where a record goes if the work moving it '
             'is cancelled) and <code>[[lifecycle.transition]]</code> with <code>from</code>, <code>to</code>, '
             '<code>label</code> and <code>by = "workflow"</code> for moves only a workflow makes.</li>'
             '<li><code>[[signature]]</code>: a <code>meaning</code> people sign with, the <code>roles</code> and '
             '<code>states</code> it is allowed in, and <code>moves_to</code> to move the record when signed.</li>'),
    "view": ('<li><code>kind</code>: board, list or dashboard; <code>record</code>: the type it shows; '
             '<code>group_by</code>, <code>[[column]]</code> or <code>[[widget]]</code>.</li>'),
    "skill": ('<li><code>instructions</code>: what an agent with this skill does; <code>[[output]]</code>: the shape of '
              'its answer, checked like any answer.</li>'),
}


class DefinitionPages:
    """Mixed into the web App."""

    def definition_routes(self, R):
        k = "(?P<kinds>types|views|skills)"
        R("GET", rf"/define/{k}", self.page_definitions)
        R("GET", rf"/define/{k}/new", self.page_definition_edit)
        R("POST", rf"/define/{k}/propose", self.post_definition_propose)
        R("POST", rf"/api/define/{k}/check", self.api_definition_check)
        R("GET", rf"/define/{k}/(?P<did>[\w-]+)", self.page_definition)
        R("GET", rf"/define/{k}/(?P<did>[\w-]+)/edit", self.page_definition_edit)
        R("POST", rf"/define/{k}/(?P<did>[\w-]+)/(?P<ver>\d{{1,9}})/(?P<verb>approve|withdraw|rollback)",
          self.post_definition_verb)
        R("POST", rf"/define/{k}/(?P<did>[\w-]+)/(?P<verb>retire|approve-retire|withdraw-retire)",
          self.post_definition_retire)

    # ------------------------------------------------------------------------------------------ list
    def page_definitions(self, req, kinds):
        kind, title, noun = KINDS[kinds]
        hub, me = self.hub, req["me"]
        rows = []
        for d in hub.definitions(kind):
            df, row = d["definition"], d["row"]
            label = getattr(df, "label", None) or getattr(df, "title", None) or d["id"]
            what = ""
            if kind == "type":
                what = f"{len(df.fields)} fields" + (f", {len(df.lifecycle['states'])} states" if df.lifecycle else "") \
                    + (f", {len(df.signatures)} signatures" if df.signatures else "")
            elif kind == "view":
                what = f"{getattr(df, 'kind', '')} of {getattr(df, 'record', '') or 'work'}"
            pending = f' <span class="loz moved">{d["proposed"]} proposed</span>' if d["proposed"] else ""
            module = f'<span class="muted">{e(row.get("module") or "the practice")}</span>'
            rows.append(f'<tr><td><a href="/define/{kinds}/{e(d["id"])}">{e(label)}</a>{pending}<br>'
                        f'<code class="muted">{e(d["id"])}</code></td><td>{e(what)}</td><td>'
                        f'{"v" + str(row["version"]) if row["version"] else "not yet active"}</td><td>{module}</td></tr>')
        body = (f'<p class="muted">Every {noun} the practice runs, under change control: a change is proposed, and a '
                f'policy owner who did not write it approves and signs it.</p>'
                f'<div class="tablewrap"><table><thead><tr><th scope="col">{e(noun.capitalize())}</th>'
                f'<th scope="col">What it has</th><th scope="col">Active</th><th scope="col">From</th></tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table></div>')
        return self.layout(req, title, body, actions=f'<a class="btn primary" href="/define/{kinds}/new">'
                                                     f'New {e(noun)}</a>')

    # ------------------------------------------------------------------------------------------ one definition
    def page_definition(self, req, kinds, did):
        from ..hub import NotFound
        kind, title, noun = KINDS[kinds]
        hub, me = self.hub, req["me"]
        versions = hub.definition_versions(kind, did)
        if not versions:
            raise NotFound(f"there is no {noun} {did!r}")
        active = next((v for v in versions if v["status"] == "active"), None)
        owner = me["kind"] == "person" and hub.is_policy_owner(me)
        main = []
        if active:
            main.append(f'<div class="section"><h2>In force: v{active["version"]}</h2>'
                        f'<pre class="code">{e(active["source"])}</pre></div>')
        rows = []
        for v in versions:
            extra = ""
            if v["status"] == "proposed":
                try:
                    impact = hub.impact(kind, did, v["version"])
                except Exception as exc:  # noqa: BLE001 - a proposal that no longer checks is shown as such
                    impact = list(getattr(exc, "problems", [str(exc)]))
                warn = "".join(f'<div class="flash warning"><p>{e(n)}</p></div>' for n in impact) or \
                    "<p class='muted'>Nothing changes for records, workflows or agents.</p>"
                diff = "\n".join(difflib.unified_diff((active["source"] if active else "").splitlines(),
                                                      v["source"].splitlines(), "in force", f"v{v['version']}",
                                                      lineterm=""))
                buttons = ""
                if owner and v["author"] != me["id"]:
                    buttons += (f'<form method="post" action="/define/{kinds}/{e(did)}/{v["version"]}/approve" '
                                f'class="inline"><button class="primary">Approve and sign</button></form>')
                if v["author"] == me["id"] or owner:
                    buttons += (f'<form method="post" action="/define/{kinds}/{e(did)}/{v["version"]}/withdraw" '
                                f'class="inline"><label class="visually-hidden" for="r{v["version"]}">Reason</label>'
                                f'<input id="r{v["version"]}" name="reason" placeholder="Reason"><button class="secondary">'
                                f'{"Withdraw" if v["author"] == me["id"] else "Decline"}</button></form>')
                extra = (f'<div class="card"><h3>Before you approve</h3>{warn}<details open><summary>Changes to the '
                         f'text</summary><pre class="diff">{self._diff(diff)}</pre></details>'
                         f'<div class="filters">{buttons}</div></div>')
            back = ""
            if owner and v["status"] == "retired":
                back = (f'<form method="post" action="/define/{kinds}/{e(did)}/{v["version"]}/rollback" class="inline">'
                        f'<button class="secondary">Propose going back to this</button></form>')
            rows.append(f'<tr id="v{v["version"]}"><td>v{v["version"]}</td><td><span class="badge {e(v["status"])}">'
                        f'{e(v["status"])}</span></td><td>{self.who(v["author"])}</td><td>{e(v["note"] or "")}</td>'
                        f'<td>{self.who(v["approved_by"]) if v.get("approved_by") else ""}</td><td>{back}</td></tr>'
                        + (f'<tr><td colspan="6">{extra}</td></tr>' if extra else ""))
        main.append('<div class="section"><h2>Versions</h2><div class="tablewrap"><table><thead><tr>'
                    '<th scope="col">Version</th><th scope="col">Status</th><th scope="col">Author</th>'
                    '<th scope="col">Note</th><th scope="col">Approved by</th><th scope="col">'
                    '<span class="visually-hidden">Actions</span></th></tr></thead><tbody>' + "".join(rows)
                    + "</tbody></table></div></div>")
        asked = hub.retire_request(kind, did) if active else None
        if asked:
            btns = ""
            if owner and asked["by"] != me["id"]:
                btns += (f'<form method="post" action="/define/{kinds}/{e(did)}/approve-retire" class="inline">'
                         f'<button class="danger" data-confirm="Retire {e(did)}?">Approve and retire</button></form>')
            if owner or asked["by"] == me["id"]:
                btns += (f'<form method="post" action="/define/{kinds}/{e(did)}/withdraw-retire" class="inline">'
                         f'<button class="secondary">Withdraw the proposal</button></form>')
            main.append(f'<div class="section"><h2>Retire</h2><div class="card"><p><b>Waiting for a second policy '
                        f'owner:</b> {self.who(asked["by"])} proposed retiring it: {e(asked["reason"])}</p>'
                        f'<div class="filters">{btns}</div></div></div>')
        elif owner and active:
            main.append(f'<div class="section"><h2>Retire</h2><p class="muted">Stop using it. Records of a retired '
                        f'type stay readable, with their history and signatures, but are not written. Another policy '
                        f'owner approves it before it stops.</p>'
                        f'<form method="post" action="/define/{kinds}/{e(did)}/retire"><label>Why *'
                        f'<input name="reason" required></label><button class="danger">Propose retiring it</button>'
                        f'</form></div>')
        return self.layout(req, did, "".join(main), crumbs=[(f"/define/{kinds}", title)],
                           actions=f'<a class="btn" href="/define/{kinds}/{e(did)}/edit">Propose a change</a>')

    def page_definition_edit(self, req, kinds, did=None, *, source=None, note="", problems=None):
        from ..hub import NotFound
        kind, title, noun = KINDS[kinds]
        if source is None:
            if did:
                versions = self.hub.definition_versions(kind, did)
                if not versions:
                    raise NotFound(f"there is no {noun} {did!r}")
                active = next((v for v in versions if v["status"] == "active"), versions[0])
                source = active["source"]
            else:
                source = ""
        probs = ('<div class="flash error" role="alert"><p>It was not saved. Fix these first:</p><ul>'
                 + "".join(f"<li>{e(p)}</li>" for p in problems) + "</ul></div>") if problems else ""
        body = (f'<p class="muted">Saving is a proposal: a policy owner who did not write it approves and signs it '
                f'before the practice uses it.</p>{probs}'
                f'<details class="card"><summary>How to write a {e(noun)}</summary><ul>{HELP[kind]}</ul></details>'
                f'<form method="post" action="/define/{kinds}/propose" class="card"><label>{e(noun.capitalize())} '
                f'(TOML) *<textarea name="source" rows="28" class="code" spellcheck="false" required '
                f'data-check="/api/define/{kinds}/check" data-status="def-status" aria-describedby="def-status">'
                f'{e(source)}</textarea></label><div class="status" id="def-status" aria-live="polite"></div><label>What changes and why *<input name="note" value="{e(note)}" required>'
                f'</label><button class="primary">Propose</button></form>')
        crumbs = [(f"/define/{kinds}", title)] + ([(f"/define/{kinds}/{did}", did)] if did else [])
        return self.layout(req, f"Propose a change: {did}" if did else f"New {noun}", body, crumbs=crumbs)

    def post_definition_propose(self, req, kinds):
        from ..hub import Invalid
        kind, _, _ = KINDS[kinds]
        f = self.form(req)
        try:
            out = self.hub.propose(kind, req["me"], f.get("source", ""), f.get("note", ""))
        except Invalid as exc:
            try:
                did = tomllib.loads(f.get("source", "")).get("id")
            except tomllib.TOMLDecodeError:
                did = None
            did = did if isinstance(did, str) and self.hub.definition_versions(kind, did) else None
            return self.page_definition_edit(req, kinds, did, source=f.get("source", ""), note=f.get("note", ""),
                                             problems=exc.problems or [str(exc)])
        raise self._redirect(f"/define/{kinds}/{out[f'{kind}_id']}#v{out['version']}")

    def api_definition_check(self, req, kinds):
        kind, _, _ = KINDS[kinds]
        try:
            self.hub.check_definition(kind, str(self.body_json(req).get("source", "")))
        except Exception as exc:  # noqa: BLE001 - every problem comes back in plain words
            return {"ok": False, "problems": list(getattr(exc, "problems", [str(exc)]))}
        return {"ok": True, "problems": []}

    def post_definition_verb(self, req, kinds, did, ver, verb):
        kind, _, _ = KINDS[kinds]
        me = req["me"]
        if verb == "approve":
            self.hub.approve(me, kind, did, int(ver))
        elif verb == "withdraw":
            self.hub.withdraw(me, kind, did, int(ver), self.form(req).get("reason", ""))
        else:
            out = self.hub.roll_back(me, kind, did, int(ver))
            raise self._redirect(f"/define/{kinds}/{did}#v{out['version']}")
        raise self._redirect(f"/define/{kinds}/{did}")

    def post_definition_retire(self, req, kinds, did, verb="retire"):
        """Propose retiring a definition; a second policy owner approves (or anyone of them withdraws) it."""
        kind, _, _ = KINDS[kinds]
        if verb == "retire":
            self.hub.retire(req["me"], kind, did, self.form(req).get("reason", ""))
            raise self._redirect(f"/define/{kinds}/{did}")
        if verb == "approve-retire":
            self.hub.approve_retirement(req["me"], kind, did)
            raise self._redirect(f"/define/{kinds}")
        self.hub.withdraw_retirement(req["me"], kind, did)
        raise self._redirect(f"/define/{kinds}/{did}")
