"""Pages for the practice's definitions under change control: record types (fields, lifecycles, signature meanings),
views (boards, lists, dashboards) and skills. A manager changes them here, without touching files on the server.

Saving is a proposal; a policy owner who did not write it approves and signs it, after reading what it changes.
Agents may propose (definitions are data, never code); only people approve.
"""

from __future__ import annotations

import difflib
import json
import re
import tomllib
import urllib.parse
from html import escape as e

from .. import schema
from .. import views as viewmod

KINDS = {"types": ("type", "Record types", "record type"), "views": ("view", "Views", "view"),
         "skills": ("skill", "Skills", "skill")}
HELP = {
    "type": ('<li><code>[[field]]</code>: <code>name</code>, <code>label</code>, <code>type</code> (text, number, date, '
             'datetime, choice, member, reference...), <code>required</code>, <code>choices</code>.</li>'
             '<li><code>[lifecycle]</code>: <code>field</code>, <code>initial</code>, <code>locked</code> (states in which '
             'people change nothing but the state), <code>abandoned</code> (where a record goes if the work moving it '
             'is cancelled: a move of its own, with no transition written for it) and '
             '<code>[[lifecycle.transition]]</code> with <code>from</code>, <code>to</code>, <code>label</code>, '
             '<code>asks</code> and <code>requires</code> for the moves people make by hand. The moves a type\'s own '
             'workflow makes are that workflow\'s edges, not listed here; <code>by = "workflow"</code> is for a move no '
             'edge of it makes (another workflow\'s, a signature\'s).</li>'
             '<li><code>[[signature]]</code>: a <code>meaning</code> people sign with, the <code>roles</code> and '
             '<code>states</code> it is allowed in, and <code>moves_to</code> to move the record when signed.</li>'),
    "view": ('<li><code>id</code>, <code>title</code>, <code>summary</code>, <code>nav</code> (in the sidebar) and '
             '<code>roles</code> (who sees it there).</li><li><code>[[widget]]</code>: <code>kind</code> (count, '
             'table, list, board, chart, tree, links), <code>source</code> (records, items, runs), <code>type</code>, '
             '<code>where</code> (a condition over <code>record</code> or <code>row</code>), <code>columns</code>, '
             '<code>group_by</code>, <code>sort</code> and <code>descending</code>, <code>limit</code>, '
             '<code>links</code> and <code>grid</code> (<code>{ x, y, w, h }</code> on 12 columns).</li>'),
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
        R("POST", r"/define/(?P<kinds>types)/build", self.post_type_build)
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
            if df is None:
                what = "no longer passes the checks: " + (d.get("problem") or "")
            elif kind == "type":
                what = f"{len(df.fields)} fields" + (f", {len(df.lifecycle['states'])} states" if df.lifecycle else "") \
                    + (f", {len(df.signatures)} signatures" if df.signatures else "")
            elif kind == "view":
                n = len(getattr(df, "widgets", []))
                what = f"{n} widget{'' if n == 1 else 's'}: " + ", ".join(
                    sorted({viewmod.KINDS.get(w["kind"], {}).get("label", w["kind"]).lower() for w in df.widgets}))
            pending = f' <span class="loz moved">{d["proposed"]} proposed</span>' if d["proposed"] else ""
            module = f'<span class="muted">{e(row.get("module") or "the practice")}</span>'
            rows.append(f'<tr><td><a href="/define/{kinds}/{e(d["id"])}">{e(label)}</a>{pending}<br>'
                        f'<code class="muted">{e(d["id"])}</code></td><td>{e(what)}</td><td>'
                        f'{"v" + str(row["version"]) if row["version"] else "not yet active"}</td><td>{module}</td></tr>')
        broken = ""
        if kind == "type":
            hub.record_types()
            bad = getattr(hub, "broken_types", {})
            if bad:  # stored before a check that now refuses them: hidden from the work until a fixed version is approved
                broken = ('<div class="error" role="alert"><b>These record types no longer pass the checks, so the '
                          'hub hides them until a fixed version is proposed and approved:</b><ul>' + "".join(
                              f'<li><a href="/define/types/{e(t)}"><code>{e(t)}</code></a>: {e(m)}</li>'
                              for t, m in sorted(bad.items())) + '</ul></div>')
        body = (f'{broken}<p class="muted">Every {noun} the practice runs, under change control: a change is proposed, '
                f'and a policy owner who did not write it approves and signs it.</p>'
                f'<div class="tablewrap"><table><thead><tr><th scope="col">{e(noun.capitalize())}</th>'
                f'<th scope="col">What it has</th><th scope="col">Active</th><th scope="col">From</th></tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table></div>')
        structure = '<a class="btn secondary" href="/define/structure">Structure</a>' if kind == "type" else ""
        return self.layout(req, title, body, actions=structure + f'<a class="btn primary" href="/define/{kinds}/new">'
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

    # ------------------------------------------------------------------------------------------ change one
    def _in_force(self, kind: str, did: str | None) -> str | None:
        """The text of the version in force (or the newest one), None for a new definition."""
        from ..hub import NotFound
        if not did:
            return None
        versions = self.hub.definition_versions(kind, did)
        if not versions:
            raise NotFound(f"there is no {KINDS[kind + 's'][2]} {did!r}")
        return next((v for v in versions if v["status"] == "active"), versions[0])["source"]

    @staticmethod
    def _module_aside(additions: list[tuple[str, str]]) -> str:
        """What modules added to the definition, read only, beside the editor: kept as it is on save."""
        if not additions:
            return ""
        parts = []
        for mid, text in additions:
            try:
                doc = tomllib.loads(text)
            except tomllib.TOMLDecodeError:
                doc = {}
            fields = "".join(f'<li><b>{e(str(f.get("label") or schema.default_label(str(f.get("name", "")))))}</b> '
                             f'<code>{e(str(f.get("name", "")))}</code> <span class="muted">'
                             f'{e(str(f.get("type", "string")))}{", required" if f.get("required") else ""}</span></li>'
                             for f in doc.get("field", []) if isinstance(f, dict))
            parts.append(f'<section class="fb-module" aria-label="Added by the {e(mid)} module"><h3>Added by the '
                         f'<span class="loz new">{e(mid)}</span> module</h3>'
                         + (f'<ul class="fb-mod-fields">{fields}</ul>' if fields else "")
                         + f'<details><summary>Its text</summary><pre class="code">{e(text)}</pre></details></section>')
        return ('<aside class="fb-aside card" aria-labelledby="fb-mods-h"><h2 id="fb-mods-h">From modules</h2>'
                '<p class="muted">Read only. What a module adds stays as it is when you save, after the practice\'s '
                'own text; it changes with the module (upgrade or remove it).</p>' + "".join(parts) + '</aside>')

    def page_definition_edit(self, req, kinds, did=None, *, source=None, note="", problems=None):
        """The editor: for a record type the form-based builder (``?as=toml``: the text); for others the text. Only
        what the practice owns is edited; what modules added is shown beside it, read only."""
        kind, title, noun = KINDS[kinds]
        in_force = self._in_force(kind, did)
        additions = schema.module_additions(in_force or "")
        if kind == "type" and source is None and req["query"].get("as") != "toml":
            own = schema.own_text(in_force) if in_force else ""
            doc = tomllib.loads(own) if own.strip() else {}
            notice = ("This type's text has comments. The form writes the text afresh, without them; to keep them, "
                      "edit it as TOML." if re.search(r"^\s*#", own, re.M) else "")
            return self._builder(req, did, doc, note=note, notice=notice)
        if source is None:
            source = schema.own_text(in_force) if in_force else ""
        probs = ('<div class="flash error" role="alert"><p>It was not saved. Fix these first:</p><ul>'
                 + "".join(f"<li>{e(p)}</li>" for p in problems) + "</ul></div>") if problems else ""
        to_form = ('<button class="secondary" formaction="/define/types/build" formnovalidate name="op" value="form">'
                   'Edit in the form</button>') if kind == "type" else ""
        if kind == "view":
            to_form = ('<button class="secondary" formaction="/define/views/design-from-text" formnovalidate>'
                       'Edit on the canvas</button>')
        editor = (f'<form method="post" action="/define/{kinds}/propose" class="card"><label>{e(noun.capitalize())} '
                  f'(TOML) *<textarea name="source" rows="28" class="code" spellcheck="false" required '
                  f'data-check="/api/define/{kinds}/check" data-status="def-status" aria-describedby="def-status">'
                  f'{e(source)}</textarea></label><div class="status" id="def-status" aria-live="polite" '
                  f'data-checked="1">{self._check_status(kind, source)}</div>'
                  + (f'<input type="hidden" name="did" value="{e(did)}">' if did else "") +
                  f'<label>What changes and why *<input name="note" value="{e(note)}" required></label>'
                  f'<div class="actions"><button class="primary">Propose</button>{to_form}</div></form>')
        body = (f'<p class="muted">Saving is a proposal: a policy owner who did not write it approves and signs it '
                f'before the practice uses it.</p>{probs}'
                f'<details class="card"><summary>How to write a {e(noun)}</summary><ul>{HELP[kind]}</ul></details>'
                + (f'<div class="fb-layout">{editor}{self._module_aside(additions)}</div>' if additions else editor))
        crumbs = [(f"/define/{kinds}", title)] + ([(f"/define/{kinds}/{did}", did)] if did else [])
        return self.layout(req, f"Propose a change: {did}" if did else f"New {noun}", body, crumbs=crumbs)

    def _check_status(self, kind, source) -> str:
        """The check's verdict on the text as the page opens, rendered here so the page never depends on a
        request it may not get to finish (the script re-checks as you type)."""
        if not source.strip():
            return ""
        try:
            self.hub.check_definition(kind, source)
        except Exception as exc:  # noqa: BLE001 - every problem comes back in plain words
            problems = list(getattr(exc, "problems", [str(exc)]))
            n = len(problems)
            return (f'<span class="loz bad">{n} problem{"" if n == 1 else "s"}</span><ul class="ed-problems">'
                    + "".join(f"<li>{e(p)}</li>" for p in problems) + "</ul>")
        return '<span class="loz done">ready</span> The hub accepts this.'

    def post_definition_propose(self, req, kinds):
        from ..hub import Invalid
        kind, _, _ = KINDS[kinds]
        f = self.form(req)
        try:
            out = self.hub.propose(kind, req["me"], f.get("source", ""), f.get("note", ""))
        except Invalid as exc:
            try:
                did = tomllib.loads(schema.own_text(f.get("source", ""))).get("id")
            except tomllib.TOMLDecodeError:
                did = f.get("did")
            did = did if isinstance(did, str) and self.hub.definition_versions(kind, did) else None
            return self.page_definition_edit(req, kinds, did, source=f.get("source", ""), note=f.get("note", ""),
                                             problems=exc.problems or [str(exc)])
        raise self._redirect(f"/define/{kinds}/{out[f'{kind}_id']}#v{out['version']}")

    # ------------------------------------------------------------------------------------------ the type builder
    def post_type_build(self, req, kinds):
        """The builder's every button: add, remove or move a part (the form comes back with it), check, switch to the
        text, or propose. Works as plain form posts; the TOML it proposes is what the text editor would."""
        from ..hub import Invalid
        f = self.form(req)
        op = str(_one(f, "op") or "check")
        did = _one(f, "t.did") or _one(f, "did") or None
        did = did if did and self.hub.definition_versions("type", did) else None
        note = _one(f, "note")
        if op == "form":  # from the text editor
            src = _one(f, "source")
            try:
                doc = tomllib.loads(schema.own_text(src))
            except tomllib.TOMLDecodeError as exc:
                return self.page_definition_edit(req, "types", did, source=src, note=note,
                                                 problems=[f"The record type is not valid TOML: {exc}"])
            return self._builder(req, did, doc, note=note)
        doc = doc_from_form(f)
        verb, _, arg = op.partition(":")
        if apply_op(doc, verb, arg):
            return self._builder(req, did, doc, note=note, focus=focus_after(doc, verb, arg))
        source = toml_dumps(doc)
        if verb == "toml":
            return self.page_definition_edit(req, "types", did, source=source, note=note)
        try:
            if verb == "propose":
                out = self.hub.propose("type", req["me"], source, note)
            else:
                self.hub.check_definition("type", self.hub.with_module_additions("type", source))
        except Invalid as exc:
            return self._builder(req, did, doc, note=note, problems=exc.problems or [str(exc)])
        except Exception as exc:  # noqa: BLE001 - a DefinitionError: every problem, in plain words
            return self._builder(req, did, doc, note=note, problems=list(getattr(exc, "problems", [str(exc)])))
        if verb == "propose":
            raise self._redirect(f"/define/types/{out['type_id']}#v{out['version']}")
        return self._builder(req, did, doc, note=note, notice="The hub accepts this. Propose it when it is ready.",
                             ok=True)

    def _builder(self, req, did, doc, *, note="", problems=None, notice="", ok=False, focus=""):
        hub = self.hub
        in_force = self._in_force("type", did)
        additions = schema.module_additions(in_force or "")
        mod_fields = [str(x.get("name")) for _, t in additions for x in _toml(t).get("field", [])
                      if isinstance(x, dict) and x.get("name")]
        types = sorted(set(hub.record_types()) | set(schema.bases()))
        classes = [(d["id"], d.get("name") or d["id"]) for d in hub.data_classes()]
        body = render_builder(doc, did=did, types=types, classes=classes, module_fields=mod_fields,
                              problems=problems or [], note=note, notice=notice, ok=ok, focus=focus)
        body = (f'<p class="muted">Saving is a proposal: a policy owner who did not write it approves and signs it '
                f'before the practice uses it.</p>'
                + (f'<div class="fb-layout"><div class="fb-main">{body}</div>{self._module_aside(additions)}</div>' if additions else body))
        crumbs = [("/define/types", "Record types")] + ([(f"/define/types/{did}", did)] if did else [])
        return self.layout(req, f"Propose a change: {did}" if did else "New record type", body, crumbs=crumbs)

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


# ============================================================================================== the type builder
# A record type as a form: its basics, fields, what it is part of, its lifecycle and its screens. The form is read
# into the same document the TOML holds (``doc_from_form``), written as TOML (``toml_dumps``) and checked by the same
# validator; what the form does not show (a field's path, a type's signatures...) rides along, unchanged, in hidden
# inputs and is listed, so nothing is lost on the way through. Every button is a plain submit (``op``).
TOP_FORM = ("id", "label", "plural", "summary", "data_class", "order", "field", "lifecycle", "screen")
FIELD_FORM = ("name", "label", "type", "required", "choices", "target", "help")
LC_FORM = ("field", "states", "initial", "locked", "transition")
TR_FORM = ("from", "to", "by", "label", "requires")
SCREEN_FORM = ("id", "on", "tab", "fields", "label")
_BARE = re.compile(r"[A-Za-z0-9_-]+")


def _one(f: dict, k: str) -> str:
    v = f.get(k, "")
    return str(v[-1] if isinstance(v, list) else v)


def _many(f: dict, k: str) -> list[str]:
    v = f.get(k)
    vals = v if isinstance(v, list) else [] if v is None else [v]
    out = []
    for x in vals:
        if x and x not in out:
            out.append(x)
    return out


def _count(f: dict, k: str) -> int:
    v = _one(f, k)
    return min(int(v), 500) if v.isdigit() else 0


def _split(words: str) -> list[str]:
    return [w.strip() for w in words.split(",") if w.strip()]


def _json(v: str) -> dict:
    try:
        out = json.loads(v) if v else {}
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def _toml(text: str) -> dict:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}


def _aot(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def _tk(k: str) -> str:
    return k if _BARE.fullmatch(k) else json.dumps(k, ensure_ascii=False)


def _tv(v) -> str:
    """A TOML value: JSON's strings, numbers and arrays are TOML's; a table is written inline."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_tv(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_tk(str(k))} = {_tv(x)}" for k, x in v.items()) + " }" if v else "{}"
    return json.dumps(v if isinstance(v, str) else str(v), ensure_ascii=False)


def toml_dumps(doc: dict) -> str:
    """A record type's document as TOML: its own keys, then its fields, lifecycle, other tables and screens."""
    out: list[str] = []

    def table(d: dict, path: str, top: bool):
        for k, v in d.items():
            if not (_aot(v) or (top and isinstance(v, dict))):
                out.append(f"{_tk(str(k))} = {_tv(v)}")
        for k, v in d.items():
            name = path + _tk(str(k))
            if top and isinstance(v, dict):
                out.extend(["", f"[{name}]"])
                table(v, name + ".", False)
            elif _aot(v):
                for x in v:
                    out.extend(["", f"[[{name}]]"])
                    table(x, name + ".", False)
    rank = {"field": 1, "lifecycle": 2, "screen": 4}
    keys = sorted(doc, key=lambda k: (0 if not (_aot(doc[k]) or isinstance(doc[k], dict)) else rank.get(k, 3)))
    table({k: doc[k] for k in keys}, "", True)
    return "\n".join(out).strip() + "\n"


def doc_from_form(f: dict) -> dict:
    """The builder's form, read into a record type's document (what ``tomllib`` would read from its TOML)."""
    s = lambda k: _one(f, k).strip()  # noqa: E731
    doc: dict = {}
    for k in ("id", "label", "plural", "summary", "data_class"):
        if s(f"t.{k}"):
            doc[k] = s(f"t.{k}")
    if s("t.order"):
        doc["order"] = int(s("t.order")) if re.fullmatch(r"-?\d{1,9}", s("t.order")) else s("t.order")
    keep = _json(_one(f, "t.keep"))
    for k, v in keep.items():
        if not (isinstance(v, dict) or _aot(v)) and k not in TOP_FORM:
            doc.setdefault(k, v)
    fields = []
    for i in range(_count(f, "f.n")):
        p = f"f.{i}."
        fd: dict = {"name": s(p + "name")}
        if s(p + "label"):
            fd["label"] = s(p + "label")
        if s(p + "type") and s(p + "type") != "string":
            fd["type"] = s(p + "type")
        if s(p + "required"):
            fd["required"] = True
        choices = _many(f, p + "choices")
        choices += [c for c in _split(s(p + "add")) if c not in choices]
        if choices:
            fd["choices"] = choices
        target = _many(f, p + "target")
        if target:
            fd["target"] = target[0] if len(target) == 1 else target
        if s(p + "help"):
            fd["help"] = s(p + "help")
        for k, v in _json(_one(f, p + "more")).items():
            fd.setdefault(k, v)
        fields.append(fd)
    owner, pf = s("t.owner"), s("t.part_field")
    if owner:  # part of a record of the owner type: through a required reference to it
        name = pf or owner[:1].lower() + re.sub(r"\W", "_", owner[1:])
        fd = next((x for x in fields if x["name"] == name), None)
        if fd is None:
            fields.append({"name": name, "type": "reference", "required": True, "target": owner})
        else:
            fd["type"] = "reference"
            have = fd.get("target")
            if owner not in ([have] if isinstance(have, str) else list(have or [])):
                fd["target"] = owner
            fd["required"] = True
        doc["part_of"] = name
    elif pf:
        doc["part_of"] = pf
    if fields:
        doc["field"] = fields
    if s("lc.on"):
        lc: dict = {"field": s("lc.field") or "status"}
        states = _many(f, "lc.states")
        states += [x for x in _split(s("lc.add")) if x not in states]
        fd = next((x for x in fields if x["name"] == lc["field"]), None)
        if fd is not None and fd.get("type") == "choice":
            fd["choices"] = states  # the states are its field's choices: one list, kept in one place
        elif states:
            lc["states"] = states
        if s("lc.initial"):
            lc["initial"] = s("lc.initial")
        if _many(f, "lc.locked"):
            lc["locked"] = _many(f, "lc.locked")
        for k, v in _json(_one(f, "lc.more")).items():
            lc.setdefault(k, v)
        moves = []
        for i in range(_count(f, "tr.n")):
            p = f"tr.{i}."
            t: dict = {}
            if _many(f, p + "from"):
                t["from"] = _many(f, p + "from")
            t["to"] = s(p + "to")
            if s(p + "by") and s(p + "by") != "anyone":
                t["by"] = s(p + "by")
            if s(p + "label"):
                t["label"] = s(p + "label")
            if _many(f, p + "requires"):
                t["requires"] = _many(f, p + "requires")
            for k, v in _json(_one(f, p + "more")).items():
                t.setdefault(k, v)
            moves.append(t)
        if moves:
            lc["transition"] = moves
        doc["lifecycle"] = lc
    for k, v in keep.items():
        if (isinstance(v, dict) or _aot(v)) and k not in TOP_FORM:
            doc.setdefault(k, v)
    screens = []
    for i in range(_count(f, "s.n")):
        p = f"s.{i}."
        sc: dict = {"id": s(p + "id")}
        if _many(f, p + "on"):
            sc["on"] = _many(f, p + "on")
        tabs = []
        for j in range(_count(f, p + "tn")):
            q = f"{p}t.{j}."
            tab: dict = {"label": s(q + "label")} if s(q + "label") else {}
            tab["fields"] = _many(f, q + "fields")
            tabs.append(tab)
        if tabs:
            sc["tab"] = tabs
        for k, v in _json(_one(f, p + "more")).items():
            sc.setdefault(k, v)
        screens.append(sc)
    if screens:
        doc["screen"] = screens
    return doc


def apply_op(doc: dict, verb: str, arg: str) -> bool:
    """Add, remove or move a part of the document, for a button of the builder; False for any other button."""
    nums = [int(x) for x in arg.split(".") if x.isdigit()]
    i = nums[0] if nums else -1
    fields = doc.get("field") if isinstance(doc.get("field"), list) else []
    lc = doc.get("lifecycle") if isinstance(doc.get("lifecycle"), dict) else None
    screens = doc.get("screen") if isinstance(doc.get("screen"), list) else []
    moves = (lc or {}).get("transition") if isinstance((lc or {}).get("transition"), list) else []
    if verb == "add-field":
        doc["field"] = fields + [{"name": ""}]
    elif verb in ("up-field", "down-field", "del-field"):
        if not 0 <= i < len(fields):
            return True
        j = i - 1 if verb == "up-field" else i + 1
        if verb == "del-field":
            fields.pop(i)
        elif 0 <= j < len(fields):
            fields[i], fields[j] = fields[j], fields[i]
    elif verb == "add-lifecycle":
        if lc is None:
            st = next((x for x in fields if x.get("name") == "status"), None)
            if st is None:
                st = {"name": "status", "type": "choice", "choices": ["open", "done"]}
                doc["field"] = fields + [st]
            states = list(st.get("choices") or ["open", "done"])
            doc["lifecycle"] = {"field": "status", "initial": states[0]}
            if st.get("type") != "choice":
                doc["lifecycle"]["states"] = states
            if len(states) > 1:
                doc["lifecycle"]["transition"] = [{"from": [states[0]], "to": states[1]}]
    elif verb == "del-lifecycle":
        doc.pop("lifecycle", None)
    elif verb == "add-trans":
        if lc is not None:
            lc["transition"] = moves + [{"to": ""}]
    elif verb == "del-trans":
        if 0 <= i < len(moves):
            moves.pop(i)
    elif verb == "add-screen":
        if not any("create" in (x.get("on") or []) or x.get("id") == "create" for x in screens):
            new = {"id": "create", "on": ["create"],
                   "tab": [{"label": "Main", "fields": [x["name"] for x in fields if x.get("required")]}]}
        else:
            new = {"id": "", "tab": [{"fields": []}]}
        doc["screen"] = screens + [new]
    elif verb == "del-screen":
        if 0 <= i < len(screens):
            screens.pop(i)
    elif verb == "add-tab":
        if 0 <= i < len(screens):
            screens[i]["tab"] = list(screens[i].get("tab") or []) + [{"fields": []}]
    elif verb == "del-tab":
        if 0 <= i < len(screens) and len(nums) > 1 and 0 <= nums[1] < len(screens[i].get("tab") or []):
            screens[i]["tab"].pop(nums[1])
    else:
        return False
    return True


def focus_after(doc: dict, verb: str, arg: str) -> str:
    """Where the keyboard goes after a button that changed the form: the part it added or moved."""
    i = int(arg.split(".")[0]) if arg.split(".")[0].isdigit() else 0
    n = lambda k, d=doc: len(d.get(k) or [])  # noqa: E731
    return {"add-field": f"b-f{n('field') - 1}-name", "up-field": f"b-f{max(i - 1, 0)}-up",
            "down-field": f"b-f{min(i + 1, max(n('field') - 1, 0))}-down", "add-lifecycle": "b-lc-field",
            "add-trans": f"b-tr{n('transition', doc.get('lifecycle') or {}) - 1}-to",
            "add-screen": f"b-s{n('screen') - 1}-id", "add-tab": f"b-s{i}-add-tab"}.get(verb, "")


def _screen_tabs(sc: dict) -> list[dict]:
    """A screen's tabs; its own ``fields`` (and ``label``) are its first tab."""
    tabs = [t for t in (sc.get("tab") or []) if isinstance(t, dict)]
    if "fields" in sc:
        tabs.insert(0, {"label": sc.get("label", ""), "fields": sc["fields"]})
    return tabs


def locate(problem: str, doc: dict) -> str | None:
    """The form control (or part) a validator problem is about: ``field status: a choice needs choices`` is the
    choices of the field named status. None: the problem is about the type as a whole."""
    fields = [x for x in doc.get("field") or [] if isinstance(x, dict)]
    names = [str(x.get("name", "")) for x in fields]
    lc = doc.get("lifecycle") if isinstance(doc.get("lifecycle"), dict) else {}
    moves = [t for t in lc.get("transition") or [] if isinstance(t, dict)]
    screens = [x for x in doc.get("screen") or [] if isinstance(x, dict)]
    m = re.match(r"field (\S+?):(.*)", problem, re.S)
    if m:
        tok, rest = m.group(1), m.group(2)
        i = names.index(tok) if tok in names else int(tok) - 1 if tok.isdigit() and 0 < int(tok) <= len(fields) \
            else None
        if i is None:
            return None
        sub = ("choices" if "choice" in rest else "target" if "target" in rest or "record type" in rest
               else "type" if "unknown type" in rest else "name" if "a name is" in rest or "share this name" in rest
               else "label" if "label" in rest else "help" if rest.strip().startswith("help") else "")
        return f"b-f{i}-{sub}" if sub else f"b-f{i}"
    if problem.startswith("lifecycle"):
        m = re.match(r"lifecycle\.transition to (['\"])(.*?)\1", problem)
        if m:
            i = next((k for k, t in enumerate(moves) if str(t.get("to", "")) == m.group(2)), None)
            return f"b-tr{i}" if i is not None else "b-lc"
        m = re.match(r"lifecycle\.transition: '(.*?)' is not one", problem)
        if m:
            i = next((k for k, t in enumerate(moves) if m.group(1) == t.get("to") or m.group(1) in (
                [t["from"]] if isinstance(t.get("from"), str) else t.get("from") or [])), None)
            return f"b-tr{i}" if i is not None else "b-lc"
        for k in ("initial", "field", "states", "locked"):
            if problem.startswith(f"lifecycle.{k}") or (k == "states" and "list its states" in problem):
                return f"b-lc-{k}"
        return "b-lc"
    m = re.match(r"screen ([a-z0-9-]+)", problem)
    if m:
        i = next((k for k, sc in enumerate(screens) if sc.get("id") == m.group(1)), None)
        return f"b-s{i}" if i is not None else None
    if problem.startswith("screen:"):
        i = next((k for k, sc in enumerate(screens) if f" {sc.get('id')} " in f" {problem} "
                  or repr(str(sc.get("id"))) in problem), None)
        return f"b-s{i}" if i is not None else None
    for k in ("id", "label", "plural", "summary", "data_class", "order"):
        if problem.startswith(f"{k}:"):
            return f"b-{k}"
    if problem.startswith("part_of:"):
        return "b-owner"
    return None


def render_builder(doc: dict, *, did, types, classes, module_fields, problems, note, notice, ok, focus) -> str:
    """The builder's form for ``doc``: what ``doc_from_form`` reads back into the same document."""
    fields = [x for x in doc.get("field") or [] if isinstance(x, dict)]
    names = [str(x.get("name", "")) for x in fields]
    pick = [n for n in names if n] + [n for n in module_fields if n not in names]
    pick_opts = [(n, n + (" (module)" if n not in names else "")) for n in pick]
    lc = doc.get("lifecycle") if isinstance(doc.get("lifecycle"), dict) else None
    lcf = str((lc or {}).get("field") or "status")
    lcfd = next((x for x in fields if x.get("name") == lcf), None)
    lc_owns = lc is not None and lcfd is not None and lcfd.get("type") == "choice"
    states = [str(x) for x in ((lc or {}).get("states") or (lcfd or {}).get("choices") or [])] if lc else []
    po = doc.get("part_of")
    pofd = next((x for x in fields if x.get("name") == po and x.get("type") == "reference"), None)
    owner = ""
    if pofd is not None:
        tg = pofd.get("target")
        owner = tg if isinstance(tg, str) else (tg or [""])[0]
    keep = {k: v for k, v in doc.items() if k not in TOP_FORM and not (k == "part_of" and pofd is not None)}
    errs: dict = {}
    loose = []
    for p in problems:
        a = locate(p, doc)
        (errs.setdefault(a, []) if a else loose).append(p)

    def err(a):
        return (f'<ul class="fb-errors" id="{a}-err">' + "".join(f"<li>{e(p)}</li>" for p in errs[a]) + "</ul>"
                if a in errs else "")

    def at(a, help_id=""):
        by = " ".join(x for x in (help_id, f"{a}-err" if a in errs else "") if x)
        return (f' id="{a}"' + (' aria-invalid="true"' if a in errs else "")
                + (f' aria-describedby="{by}"' if by else "") + (" autofocus" if a == focus else ""))

    def text(a, name, label, value, *, help_="", req=False, extra=""):
        hid = f"{a}-help" if help_ else ""
        ctl = (f'<input name="{name}" value="{e(str(value or ""))}"{at(a, hid)}{" required" if req else ""}{extra}>')
        return (f'<div class="field"><label>{e(label)}{" *" if req else ""}{ctl}</label>'
                + (f'<small class="help" id="{hid}">{help_}</small>' if help_ else "") + f'{err(a)}</div>')

    def select(a, name, label, options, chosen, *, blank=None, multiple=False, help_=""):
        chosen = [chosen] if isinstance(chosen, str) else [str(c) for c in chosen or []]
        opts = list(options) + [(c, c) for c in chosen if c and c not in [v for v, _ in options]]
        html_ = ((f'<option value="">{e(blank)}</option>' if blank is not None else "")
                 + "".join(f'<option value="{e(v)}"{" selected" if v in chosen else ""}>{e(t)}</option>'
                           for v, t in opts))
        hid = f"{a}-help" if help_ else ""
        size = f' size="{min(max(len(opts), 2), 6)}"' if multiple else ""
        return (f'<div class="field"><label>{e(label)}<select name="{name}"{at(a, hid)}'
                f'{" multiple" + size if multiple else ""}>{html_}</select></label>'
                + (f'<small class="help" id="{hid}">{help_}</small>' if help_ else "") + f'{err(a)}</div>')

    def chips(a, name, legend, options, chosen, *, help_=""):
        chosen = [str(c) for c in chosen or []]
        order = chosen + [v for v, _ in options if v not in chosen]
        words = dict(options)
        boxes = "".join(f'<label class="radio multi"><input type="checkbox" class="opt-input" name="{name}" '
                        f'value="{e(v)}"{" checked" if v in chosen else ""}> <span>{e(words.get(v, v))}</span></label>'
                        for v in order)
        hid = f"{a}-help" if help_ else ""
        return (f'<fieldset class="choices options fb-chips"{at(a, hid)}><legend>{e(legend)}</legend>'
                + (boxes or '<span class="muted">None yet.</span>') + '</fieldset>'
                + (f'<small class="help" id="{hid}">{help_}</small>' if help_ else "") + err(a))

    def btn(op, words, *, cls="subtle", aria="", id_=""):
        return (f'<button type="submit" class="{cls}" name="op" value="{e(op)}" formnovalidate'
                + (f' aria-label="{e(aria)}"' if aria else "") + (f'{at(id_)}' if id_ else "") + f'>{e(words)}</button>')

    def more(name, d, keys):
        rest = {k: v for k, v in d.items() if k not in keys}
        if not rest:
            return ""
        return (f'<input type="hidden" name="{name}" value="{e(json.dumps(rest, ensure_ascii=False, default=str))}">'
                f'<p class="fb-more muted">Also in its text (edit as TOML to change): '
                + " · ".join(f"<code>{e(_tk(str(k)))} = {e(_tv(v))}</code>" for k, v in rest.items()) + "</p>")

    out = []
    if notice:
        out.append(f'<div class="flash {"success" if ok else "warning"}" role="status"><p>{e(notice)}</p></div>')
    if problems:
        out.append('<div class="flash error" role="alert"><p>It was not saved. Fix these first:</p><ul>' + "".join(
            f'<li><a href="#{locate(p, doc)}">{e(p)}</a></li>' if locate(p, doc) else f"<li>{e(p)}</li>"
            for p in problems) + "</ul></div>")
    form = ['<form method="post" action="/define/types/build" class="card fb" id="type-builder">',
            '<button type="submit" name="op" value="check" formnovalidate class="visually-hidden" tabindex="-1" '
            'aria-hidden="true">Check</button>',
            f'<input type="hidden" name="t.did" value="{e(did or "")}">',
            '<div class="fb-head"><h2>Basics</h2>' + btn("toml", "Edit as TOML", cls="secondary") + '</div>']
    if did:
        idf = text("b-id", "t.id", "Id", doc.get("id", did), extra=" readonly",
                   help_="The id stays: records of the type carry it.")
    else:
        idf = text("b-id", "t.id", "Id", doc.get("id", ""), req=True,
                   help_="Letters, digits and dashes, starting with a letter: <code>StockItem</code>.")
    form.append('<div class="fb-grid">' + idf
                + text("b-label", "t.label", "Label", doc.get("label", ""), help_="What one record is called, e.g. Visit.")
                + text("b-plural", "t.plural", "Plural", doc.get("plural", ""), help_="What several are called, e.g. Visits.")
                + select("b-data_class", "t.data_class", "Data class", classes, doc.get("data_class", ""),
                         blank="Choose…", help_="Who may read and write its records.")
                + text("b-order", "t.order", "Order", doc.get("order", ""), extra=' inputmode="numeric"',
                       help_="Where it sits on a subject's page (lower first).")
                + '</div>')
    form.append(f'<div class="field"><label>Summary<textarea name="t.summary" rows="2"{at("b-summary")}>'
                f'{e(str(doc.get("summary", "")))}</textarea></label>{err("b-summary")}</div>')
    form.append('<fieldset class="fb-part"><legend>Part of</legend><div class="fb-grid">'
                + select("b-owner", "t.owner", "Owner type", [(t, t) for t in types], owner,
                         blank="Nothing: its records stand on their own",
                         help_="A record that exists only inside one record of this type.")
                + text("b-part-field", "t.part_field", "Through the field", po if pofd is not None else "",
                       help_="A required reference to the owner; added if it has none.")
                + '</div></fieldset>')
    # ---- fields
    form.append(f'<h2 class="fb-h">Fields</h2><input type="hidden" name="f.n" value="{len(fields)}">')
    ftypes = [(t, t) for t in sorted(schema.FIELD_TYPES)]
    for i, fd in enumerate(fields):
        p, a = f"f.{i}.", f"b-f{i}"
        name, typ = str(fd.get("name", "")), str(fd.get("type", "string"))
        title = str(fd.get("label") or (schema.default_label(name) if name else "New field"))
        tg = fd.get("target")
        tg = [tg] if isinstance(tg, str) else [str(x) for x in tg or []]
        is_lc = lc is not None and lc_owns and name == lcf
        show_ch = typ == "choice" or bool(fd.get("choices"))
        show_tg = typ == "reference" or bool(tg)
        if is_lc:
            ch = '<p class="muted fb-note">Its choices are the lifecycle\'s states, below.</p>'
        else:
            ch = (chips(f"{a}-choices", p + "choices", "Choices", [(str(c), str(c)) for c in fd.get("choices") or []],
                        fd.get("choices") or [], help_="Untick a choice to drop it.")
                  + text(f"{a}-add", p + "add", "Add choices", "", help_="Separate them with commas."))
        dis = lambda on: "" if on else " hidden"  # noqa: E731
        opened = " open" if (not name or any(k == a or k.startswith(a + "-") for k in errs)
                             or focus.startswith(a + "-")) else ""
        gist = " · ".join([typ] + (["required"] if fd.get("required") else [])
                          + ([", ".join(map(str, fd.get("choices") or []))] if fd.get("choices") and not is_lc else [])
                          + (["→ " + ", ".join(tg)] if tg else []))
        form.append(
            f'<div class="fb-field fb-fd"{at(a)}><details{opened}><summary><b>{i + 1}. {e(title)}</b>'
            + (f' <code>{e(name)}</code>' if name else "") + f' <span class="muted">{e(gist)}</span></summary>'
            f'<div class="fb-body" role="group" aria-label="{e(title)}">{err(a)}<div class="fb-grid">'
            + text(f"{a}-name", p + "name", "Name", name, req=False, help_="The key in the record: letters, digits, _.")
            + text(f"{a}-label", p + "label", "Label", fd.get("label", ""))
            + select(f"{a}-type", p + "type", "Type", ftypes, typ).replace("<select ", "<select data-fb-type ", 1)
            + f'<div class="field fb-req"><label class="switch-row"><input type="checkbox" class="switch" '
              f'name="{p}required" value="1"{" checked" if fd.get("required") else ""}> Required</label></div>'
            + '</div>'
            + f'<div class="fb-when" data-fb-for="choice"{dis(show_ch)}>{ch}</div>'
            + f'<div class="fb-when" data-fb-for="reference"{dis(show_tg)}>'
            + select(f"{a}-target", p + "target", "Points at", [(t, t) for t in types], tg, multiple=True,
                     help_="The record types it may refer to (Ctrl or ⌘ to pick several).") + '</div>'
            + text(f"{a}-help", p + "help", "Help", fd.get("help", ""))
            + more(p + "more", fd, FIELD_FORM) + '</div></details>'
            + '<div class="actions fb-row-actions">'
            + (btn(f"up-field:{i}", "Move up", aria=f"Move {title} up", id_=f"{a}-up") if i else "")
            + (btn(f"down-field:{i}", "Move down", aria=f"Move {title} down", id_=f"{a}-down")
               if i < len(fields) - 1 else "")
            + btn(f"del-field:{i}", "Remove", aria=f"Remove {title}") + '</div></div>')
    if module_fields:
        form.append(f'<p class="muted">Fields a module adds ({e(", ".join(module_fields))}) are listed beside the '
                    f'form: they are kept as they are.</p>')
    form.append('<div class="actions">' + btn("add-field", "Add field", cls="secondary") + '</div>')
    # ---- lifecycle
    form.append('<h2 class="fb-h">Lifecycle</h2>')
    if lc is None:
        form.append('<p class="muted">No lifecycle: its records have no states to move through.</p>'
                    '<div class="actions">' + btn("add-lifecycle", "Add a lifecycle", cls="secondary") + '</div>')
    else:
        moves = [t for t in lc.get("transition") or [] if isinstance(t, dict)]
        st_opts = [(x, x) for x in states]
        form.append(f'<fieldset class="fb-lc"{at("b-lc")}><legend class="visually-hidden">Lifecycle</legend>'
                    f'{err("b-lc")}<input type="hidden" name="lc.on" value="1"><div class="fb-grid">'
                    + select("b-lc-field", "lc.field", "State field", pick_opts, lcf,
                             help_="The field that holds a record's state.")
                    + select("b-lc-initial", "lc.initial", "Starts in", st_opts, str(lc.get("initial") or ""),
                             blank="Choose…") + '</div>'
                    + chips("b-lc-states", "lc.states", "States", st_opts, states,
                            help_="In order. Untick a state to drop it.")
                    + text("b-lc-add", "lc.add", "Add states", "", help_="Separate them with commas.")
                    + chips("b-lc-locked", "lc.locked", "Locked in", st_opts, lc.get("locked") or [],
                            help_="States in which people change nothing but the state.")
                    + more("lc.more", lc, LC_FORM) + '</fieldset>')
        own = doc.get("workflow")
        own = str((own.get("id") if isinstance(own, dict) else own) or "")
        if own:  # its own workflow's edges are its moves too: said where they are changed, not listed here
            form.append(f'<p class="muted" id="b-lc-own">The moves its workflow makes are that workflow\'s edges, changed '
                        f'at <a href="/workflows/{e(urllib.parse.quote(own))}">/workflows/{e(own)}</a>. The moves '
                        f'listed here are the ones people make by hand.</p>')
        form.append(f'<h3 class="fb-h3" id="b-lc-trans">Moves</h3><input type="hidden" name="tr.n" value="{len(moves)}">')
        for i, t in enumerate(moves):
            p, a = f"tr.{i}.", f"b-tr{i}"
            frm = t.get("from") or []
            frm = [frm] if isinstance(frm, str) else [str(x) for x in frm]
            words = str(t.get("label") or (f"Move to {t.get('to')}" if t.get("to") else "New move"))
            form.append(
                f'<fieldset class="fb-field"{at(a)}><legend>{e(words)}</legend>{err(a)}'
                + chips(f"{a}-from", p + "from", "From", st_opts + [("*", "Any state")], frm,
                        help_="None ticked: from any state.")
                + '<div class="fb-grid">'
                + select(f"{a}-to", p + "to", "To", st_opts, str(t.get("to") or ""), blank="Choose…")
                + select(f"{a}-by", p + "by", "Who moves it",
                         [("anyone", "Anyone who may write it"), ("workflow", "Not by hand (a workflow or a signature)")],
                         str(t.get("by") or "anyone"))
                + text(f"{a}-label", p + "label", "Button label", t.get("label", "")) + '</div>'
                + chips(f"{a}-requires", p + "requires", "Fields it requires", pick_opts, t.get("requires") or [],
                        help_="Filled in before the move is made.")
                + more(p + "more", t, TR_FORM)
                + '<div class="actions fb-row-actions">' + btn(f"del-trans:{i}", "Remove", aria=f"Remove {words}")
                + '</div></fieldset>')
        form.append('<div class="actions">' + btn("add-trans", "Add a move", cls="secondary")
                    + btn("del-lifecycle", "Remove the lifecycle") + '</div>')
    # ---- screens
    screens = [x for x in doc.get("screen") or [] if isinstance(x, dict)]
    form.append(f'<h2 class="fb-h">Screens</h2><input type="hidden" name="s.n" value="{len(screens)}">')
    if not screens:
        form.append('<p class="muted">No screens: forms show every field, in order.</p>')
    for i, sc in enumerate(screens):
        p, a = f"s.{i}.", f"b-s{i}"
        tabs = _screen_tabs(sc)
        on = sc.get("on") or []
        on = [on] if isinstance(on, str) else on
        part = [f'<fieldset class="fb-field"{at(a)}><legend>Screen {e(str(sc.get("id") or i + 1))}</legend>{err(a)}'
                + '<div class="fb-grid">' + text(f"{a}-id", p + "id", "Id", sc.get("id", ""),
                                                 help_="Lower case: create, edit, view, or a move's own.") + '</div>'
                + chips(f"{a}-on", p + "on", "Shown to", [(o, o) for o in schema.OPS], on),
                f'<input type="hidden" name="{p}tn" value="{len(tabs)}">']
        for j, tab in enumerate(tabs):
            q = f"{p}t.{j}."
            part.append(f'<div class="fb-tab">' + text(f"{a}-t{j}-label", q + "label", f"Tab {j + 1}",
                                                         tab.get("label", ""))
                        + chips(f"{a}-t{j}-fields", q + "fields", "Fields on it, in order", pick_opts,
                                tab.get("fields") or [])
                        + '<div class="actions">' + btn(f"del-tab:{i}.{j}", "Remove tab",
                                                        aria=f"Remove tab {j + 1}") + '</div></div>')
        part.append(more(p + "more", sc, SCREEN_FORM) + '<div class="actions fb-row-actions">'
                    + btn(f"add-tab:{i}", "Add tab", id_=f"{a}-add-tab")
                    + btn(f"del-screen:{i}", "Remove screen", aria=f"Remove screen {sc.get('id') or i + 1}")
                    + '</div></fieldset>')
        form.append("".join(part))
    form.append('<div class="actions">' + btn("add-screen", "Add screen", cls="secondary") + '</div>')
    if keep:
        form.append(f'<input type="hidden" name="t.keep" value="{e(json.dumps(keep, ensure_ascii=False, default=str))}">'
                    '<details class="fb-keep"><summary>Other settings (edit as TOML to change them)</summary>'
                    f'<pre class="code">{e(toml_dumps(keep))}</pre></details>')
    if loose:
        form.append('<ul class="fb-errors">' + "".join(f"<li>{e(p)}</li>" for p in loose) + '</ul>')
    form.append(f'<h2 class="fb-h">Propose it</h2><label>What changes and why *<input name="note" value="{e(note)}" '
                f'required></label><div class="actions">'
                '<button type="submit" class="primary" name="op" value="propose">Propose</button>'
                + btn("check", "Check", cls="secondary") + btn("toml", "Edit as TOML", cls="secondary")
                + '</div></form>')
    out.append("".join(form))
    return "".join(out)
