"""The graphical editor for views and dashboards: widgets placed on a grid, set up with pickers, previewed with the
data of the member who is editing (as ``views.compute`` shows it to them, no more).

The editor works on the document ``views.doc_of`` reads from a view's text and writes it back with
``views.to_toml``; what it offers (kinds of widget, sources, filter operators) is ``views.editor_spec``, and the record
types, workflows and roles come from the hub. Saving proposes the text under the same change control as the text
editor (``definitions_pages``): a policy owner who did not write it approves and signs it. Nothing here activates a
view. The script is ``DASH_JS`` in ``assets.py``; it builds a document, never code, and the server checks every draft.
"""

from __future__ import annotations

import json
import re
from html import escape as e

from .. import schema
from .. import views as viewmod
from ..workflows import DefinitionError


class DashboardPages:
    """Mixed into the web App."""

    def dashboard_routes(self, R):  # before definition_routes: these two pages are the editor for views
        R("GET", r"/define/views/new", self.page_dashboard_edit)
        R("GET", r"/define/views/(?P<did>[\w-]+)/edit", self.page_dashboard_edit)
        R("POST", r"/define/views/design", self.post_dashboard_propose)
        R("POST", r"/define/views/design-from-text", self.post_dashboard_from_text)
        R("POST", r"/api/views/draft", self.api_view_draft)

    # ------------------------------------------------------------------------------------------ what it offers
    def _dashboard_catalog(self) -> dict:
        hub = self.hub
        types = []
        for tid, rt in sorted(hub.record_types().items(), key=lambda kv: kv[1].label.lower()):
            if rt.abstract:
                continue
            states = rt.states()
            fields = []
            for f in rt.fields:
                d = {"name": f["name"], "path": f["path"], "label": f["label"], "type": f["type"],
                     "choices": list(f.get("choices") or [])}
                if rt.lifecycle and f["path"] == rt.lifecycle["path"] and states:
                    d["choices"] = states
                # a filter reads the stored value at a plain path: not a list's entry, a list or an object
                d["filter"] = f["type"] not in ("list", "object", "markdown") and not f.get("list") and not any(
                    p.isdigit() or p == "*" for p in f["path"].split("."))
                fields.append(d)
            fields.append({"name": "meta.lastUpdated", "path": "meta.lastUpdated", "label": "Last changed",
                           "type": "datetime", "choices": [], "filter": True})
            types.append({"id": tid, "label": rt.label, "plural": rt.plural, "fields": fields,
                          "tree": bool(rt.parent), "lifecycle": (rt.lifecycle or {}).get("field")})
        flows = [{"id": w["workflow"].id, "title": w["workflow"].title} for w in hub.workflows()]
        roles = [{"id": r["id"], "name": r.get("name") or r["id"]} for r in hub.roles()]
        pages = [{"label": v.title, "href": f"/views/{v.id}"} for v in
                 (hub.view(i) for i in hub._def_ids("view")) if v]
        pages += [{"label": t["plural"] or t["label"], "href": f"/records/{t['id']}"} for t in types]
        pages += [{"label": "Inbox", "href": "/"}, {"label": "Board", "href": "/board"},
                  {"label": "Start work", "href": "/start"}, {"label": "Runs", "href": "/runs"}]
        return {"types": types, "workflows": flows, "roles": roles, "pages": pages}

    # ------------------------------------------------------------------------------------------ the editor
    def page_dashboard_edit(self, req, did=None, *, source=None, note="", problems=None):
        if req["query"].get("as") == "toml":
            return self.page_definition_edit(req, "views", did, source=source, note=note, problems=problems)
        in_force = self._in_force("view", did)
        additions = schema.module_additions(in_force or "")
        if source is None:
            source = schema.own_text(in_force) if in_force else ""
        try:
            doc = viewmod.doc_of(source) if source.strip() else {"id": "", "title": "", "widget": []}
        except DefinitionError as exc:  # text the canvas cannot read: the text editor, with why
            return self.page_definition_edit(req, "views", did, source=source, note=note,
                                             problems=list(problems or []) + exc.problems)
        for w, g in zip(doc["widget"], viewmod.placed(doc["widget"])):
            w["grid"] = g
        notice = ("This view's text has comments. The canvas writes the text afresh, without them; to keep them, "
                  "edit it as text." if re.search(r"^\s*#", source, re.M) else "")
        boot = json.dumps({"doc": doc, "spec": viewmod.editor_spec(), "catalog": self._dashboard_catalog(),
                           "locked_id": bool(did), "problems": problems or []}, default=str).replace("</", "<\\/")
        probs = ('<div class="flash error" role="alert"><p>It was not proposed. Fix these first:</p><ul>'
                 + "".join(f"<li>{e(p)}</li>" for p in problems) + "</ul></div>") if problems else ""
        text_link = f'/define/views/{e(did)}/edit?as=toml' if did else "/define/views/new?as=toml"
        body = (
            f'<p class="muted">Place widgets on the grid and set each one up on the right. The preview uses the '
            f'records and work you can see. Saving is a proposal: a policy owner who did not write it approves and '
            f'signs it before anyone sees the change.</p>{probs}'
            + (f'<div class="flash warning"><p>{e(notice)}</p></div>' if notice else "")
            + f'<div id="dash-editor" class="dz" data-text="{text_link}">'
            '<div class="dz-bar">'
            '<div class="seg dz-mode" role="group" aria-label="Show"><button type="button" data-mode="canvas" '
            'aria-pressed="true">Canvas</button><button type="button" data-mode="code" aria-pressed="false">Code'
            '</button></div><span class="dz-grow"></span><div class="status" id="dz-status" aria-live="polite"></div>'
            '</div>'
            '<div class="dz-main" id="dz-main">'
            '<aside class="dz-palette" aria-labelledby="dz-pal-h"><h2 id="dz-pal-h">Add a widget</h2>'
            '<div id="dz-palette" class="dz-kinds"></div><p class="muted small">Click one to add it, or drag it '
            'onto the grid.</p></aside>'
            '<div class="dz-stage"><div id="dz-canvas" class="dz-canvas" role="list" aria-label="Widgets on the '
            'grid"></div><p class="dz-empty" id="dz-empty">An empty grid. Add a widget from the palette: a number, '
            'a table of records, a board, a chart or links.</p></div>'
            '<aside class="dz-panel" id="dz-panel" aria-label="Settings"></aside></div>'
            '<div class="dz-code" id="dz-code" hidden><label for="dz-toml">The view as TOML: what is proposed. '
            'Change it here and go back to the canvas to see it.</label><textarea id="dz-toml" class="code" '
            'rows="26" spellcheck="false"></textarea></div>'
            '<form method="post" action="/define/views/design" id="dz-propose" class="dz-foot card">'
            '<input type="hidden" name="source">'
            + (f'<input type="hidden" name="did" value="{e(did)}">' if did else "")
            + f'<label>What changes and why *<input name="note" value="{e(note)}" required></label>'
            '<div class="actions"><button class="primary">Propose</button>'
            f'<a class="btn subtle" href="{text_link}">Edit as text instead</a></div></form></div>'
            f'<noscript><p>The canvas needs JavaScript. <a href="{text_link}">Edit the view as text.</a></p></noscript>'
            f'<script type="application/json" id="dz-boot">{boot}</script>')
        if additions:
            body += self._module_aside(additions)
        crumbs = [("/define/views", "Views")] + ([(f"/define/views/{did}", did)] if did else [])
        return self.layout(req, f"Design: {did}" if did else "New view", body, crumbs=crumbs)

    def post_dashboard_propose(self, req):
        from ..hub import Invalid
        f = self.form(req)
        did = str(f.get("did") or "") or None
        did = did if did and self.hub.definition_versions("view", did) else None
        try:
            out = self.hub.propose("view", req["me"], str(f.get("source", "")), str(f.get("note", "")))
        except Invalid as exc:
            return self.page_dashboard_edit(req, did, source=str(f.get("source", "")), note=str(f.get("note", "")),
                                            problems=exc.problems or [str(exc)])
        raise self._redirect(f"/define/views/{out['view_id']}#v{out['version']}")

    def post_dashboard_from_text(self, req):
        """From the text editor to the canvas, with the text as it was typed (not proposed)."""
        f = self.form(req)
        did = str(f.get("did") or "") or None
        did = did if did and self.hub.definition_versions("view", did) else None
        return self.page_dashboard_edit(req, did, source=str(f.get("source", "")), note=str(f.get("note", "")))

    # ------------------------------------------------------------------------------------------ a draft
    def api_view_draft(self, req):
        """A draft from the editor: its text (from the document, or the document from the text), whether the hub
        accepts it, and each widget as the member sees it now."""
        from ..hub import Invalid
        body = self.body_json(req)
        out: dict = {"problems": []}
        if isinstance(body.get("source"), str):
            source = body["source"]
            try:
                doc = viewmod.doc_of(source)
            except DefinitionError as exc:
                return {"source": source, "problems": exc.problems, "widgets": []}
            for w, g in zip(doc["widget"], viewmod.placed(doc["widget"])):
                w["grid"] = g
            out["doc"] = doc
        else:
            doc = body.get("doc")
            if not isinstance(doc, dict) or not isinstance(doc.get("widget", []), list):
                raise Invalid("send the view as {doc: {...}} or {source: '...'}")
            try:
                source = viewmod.to_toml(doc)
            except DefinitionError as exc:
                return {"source": "", "problems": exc.problems, "widgets": []}
        out["source"] = source
        try:
            self.hub.check_definition("view", self.hub.with_module_additions("view", source))
        except Exception as exc:  # noqa: BLE001 - every problem, in plain words
            out["problems"] = list(getattr(exc, "problems", None) or [str(exc)])
        out["widgets"] = [self._draft_widget(req, raw, i) for i, raw in enumerate(_widgets_of(source))]
        return out

    def _draft_widget(self, req, raw, i: int) -> dict:
        me, hub = req["me"], self.hub
        one, wrong = viewmod.widget(raw, i)
        if one and not wrong:
            wrong = hub.check_view(viewmod.View(id="draft", title="draft", widgets=[one]))
        where = raw.get("where") if isinstance(raw, dict) and isinstance(raw.get("where"), str) else None
        if wrong:
            return {"problems": wrong, "where": where}
        try:
            w = viewmod.compute(hub, me, viewmod.View(id="draft", title="draft", widgets=[one]))[0]
        except Exception as exc:  # noqa: BLE001 - a draft that cannot be shown says why
            return {"problems": [f"cannot be shown yet: {getattr(exc, 'problems', None) or exc}"]}
        return {"total": w["total"], "note": w["note"], "where": where,
                "html": self.view_widget(req, me, w, live=False)}


def _widgets_of(source: str) -> list:
    import tomllib
    try:
        ws = tomllib.loads(source).get("widget", [])
    except tomllib.TOMLDecodeError:
        return []
    return ws if isinstance(ws, list) else []
