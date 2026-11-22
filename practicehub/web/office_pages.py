"""Office documents in Collabora Online: the record page's Open and Edit together buttons, the editor page, blank
documents, and the WOPI endpoints Collabora calls (``integrations/wopi.py`` does the work).

Nothing here shows unless ``PRACTICEHUB_COLLABORA_URL`` is set and Collabora's discovery answers, except the
comments on a document (:meth:`OfficePages.office_comments_html`): threads on the file as a whole, with an optional
quote of its words, beside the editor and on the record page whether or not Collabora is set up. They are the hub's
comments (``practicehub.live``) with an ``@`` to mention a person or an agent (``practicehub.mentions``); an agent
answers in the thread and may suggest a change as words, which a person makes in the document themselves.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse

from .. import schema
from ..hub import Invalid, NotFound
from ..integrations import collabora as cool
from ..integrations.wopi import Wopi

e = html.escape
_FIELD = r"(?P<field>[A-Za-z_][A-Za-z0-9_]*)"
_REC = r"/records/(?P<rtype>[A-Za-z][\w]*)/(?P<rid>[\w.-]+)/office"

EDITOR_JS = r"""(() => {
  const form = document.getElementById('office-form'), frame = document.getElementById('office-frame');
  const origin = document.body.dataset.origin, back = document.body.dataset.back;
  const users = document.getElementById('office-users');
  const views = new Map();
  const post = (id, values) => frame.contentWindow.postMessage(JSON.stringify(
    {MessageId: id, SendTime: Date.now(), Values: values || {}}), origin);
  const draw = () => {
    const list = [...views.values()];
    users.textContent = list.length ? 'Open now: ' + list.map(v => v.UserName + ([true, 'true', '1', 1].includes(v.ReadOnly) ? ' (reading)' : '')).join(', ') : '';
  };
  const side = document.getElementById('office-side'), toggle = document.getElementById('office-side-toggle');
  if (side && toggle) toggle.addEventListener('click', () => {
    side.hidden = !side.hidden; toggle.setAttribute('aria-expanded', String(!side.hidden));
  });
  window.addEventListener('message', ev => {
    if (ev.origin !== origin || ev.source !== frame.contentWindow) return;
    let msg; try { msg = typeof ev.data === 'string' ? JSON.parse(ev.data) : ev.data; } catch (_) { return; }
    const v = msg.Values || {};
    if (msg.MessageId === 'App_LoadingStatus' && v.Status === 'Frame_Ready') post('Host_PostmessageReady');
    if (msg.MessageId === 'App_LoadingStatus' && v.Status === 'Document_Loaded') { post('Host_PostmessageReady'); post('Get_Views'); }
    if (msg.MessageId === 'Get_Views_Resp' && Array.isArray(v)) { views.clear(); v.forEach(x => views.set(x.ViewId, x)); draw(); }
    if (msg.MessageId === 'View_Added') { views.set(v.ViewId, v); draw(); }
    if (msg.MessageId === 'View_Removed') { views.delete(v.ViewId); draw(); }
    if (msg.MessageId === 'UI_Close') location.href = back;
  });
  form.submit();
})();
"""

EDITOR_CSS = """
html,body{height:100%}body.office{display:flex;flex-direction:column;margin:0;overflow:hidden}
.office-bar{display:flex;align-items:center;gap:12px;padding:6px 12px;border-bottom:1px solid var(--border);
background:var(--surface);min-height:44px;flex-wrap:wrap}
.office-bar .title{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;flex:1 1 200px}
.office-bar .muted{color:var(--subtle)}#office-users{color:var(--subtle);font-size:12px}
.office-note{font-size:12px;padding:2px 8px;border-radius:3px;background:var(--warning-bg)}
#office-frame{flex:1;border:0;width:100%;min-width:0;background:var(--bg)}
.office-main{flex:1;display:flex;min-height:0}
.office-main .office-side{flex:0 0 340px;border-left:1px solid var(--border);background:var(--surface);overflow:auto;
padding:12px}
.office-side[hidden]{display:none}
@media (max-width:760px){.office-main{flex-direction:column;overflow:auto}#office-frame{flex:0 0 70vh}
.office-main .office-side{flex:none;border-left:0;border-top:1px solid var(--border)}}
"""


class OfficePages:
    def office_routes(self, R):
        self.collabora = cool.Collabora.from_env()
        self.wopi = Wopi(self.hub, self.collabora) if self.collabora else None
        R("GET", r"/static/office.js", self.office_js, auth=False)
        R("GET", _REC + rf"/{_FIELD}/(?P<blob>[0-9a-f]{{64}})", self.page_office)
        R("POST", _REC + rf"/{_FIELD}/new", self.post_office_new)

    def office_js(self, req):
        from .records_pages import Download
        return Download(({"Content-Type": "text/javascript; charset=utf-8"}, EDITOR_JS.encode()))

    def office_ready(self) -> bool:
        return bool(self.wopi) and self.collabora.discovery() is not None

    # ------------------------------------------------------------------------------------------ record page
    def _office_field_of(self, rt, rec, blob: str):
        for f in rt.fields:
            if (f["type"] == "attachment" and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.\*)?", f["path"])
                    and self.wopi.attachment(rec, f["name"], blob)):
                return f
        return None

    def office_buttons(self, rec: dict, a: dict) -> str:
        """Open and Edit together beside an office attachment on its record's page; nothing when Collabora is not
        configured, cannot be reached, or does not open this kind of file."""
        if not self.wopi or not rec.get("id") or not rec.get("resourceType"):
            return ""
        me = self.reader()
        ext = cool.extension(str(a.get("name") or ""), str(a.get("contentType") or ""))
        if not me or not ext or not self.office_ready():
            return ""
        view, edit = self.collabora.action(ext, "view"), self.collabora.action(ext, "edit")
        if not view and not edit:
            return ""
        rt = self.hub.record_type(rec["resourceType"])
        f = self._office_field_of(rt, rec, a["blob"]) if rt else None
        if not f:
            return ""
        ref = f"{rec['resourceType']}/{rec['id']}"
        self.wopi.settle(ref)
        base = f"/records/{urllib.parse.quote(rec['resourceType'])}/{urllib.parse.quote(rec['id'])}/office/{f['name']}/{a['blob']}"
        out = [f'<a class="btn secondary" href="{e(base)}?view=1">Open</a>']
        if edit and self.wopi.may_edit(me, rec):
            out.append(f'<a class="btn primary" href="{e(base)}">Edit together</a>')
        note = self._unsigned_note(me, ref, f, a["blob"])
        return (' <span class="office-actions" style="display:inline-flex;gap:4px;margin-left:8px;vertical-align:middle">'
                + "".join(out) + "</span>" + (f' <span class="lozenge">{e(note)}</span>' if note else ""))

    def _unsigned_note(self, me, ref: str, f: dict, blob: str) -> str:
        """When an earlier version of this file was signed and this one is not: a new blob is a new version."""
        try:
            sigs = [s for s in self.hub.signatures(me, ref) if s.get("valid")]
        except Exception:  # noqa: BLE001 - no signatures to read is no note
            return ""
        if not sigs or any(s["current"] for s in sigs):
            return ""
        rtype, rid = ref.split("/", 1)
        for s in sigs:
            v = self.hub.db.record_versions.get(f"{ref}@{s.get('version')}") if s.get("version") else None
            if not v:
                continue
            was = v["data"].get(f["path"].removesuffix(".*"))
            blobs = {x.get("blob") for x in (was if isinstance(was, list) else [was]) if isinstance(x, dict)}
            if blobs and blob not in blobs:
                return "edited since it was signed: this version is not signed"
        return ""

    def office_new_html(self, rt, rec: dict, files: list[dict]) -> str:
        """New document: a blank text document, spreadsheet or presentation in an attachment field, opened to edit.
        Offered for a list field, or a single field that holds no file yet."""
        if not self.wopi or not self.office_ready():
            return ""
        free = [f for f in files if f["path"].endswith(".*") or not rec.get(f["path"])]
        kinds = [(k, lbl) for k, lbl in cool.BLANK_KINDS.items() if self.collabora.action(k, "edit")]
        if not free or not kinds:
            return ""
        url = f"/records/{urllib.parse.quote(rec['resourceType'])}/{urllib.parse.quote(rec['id'])}/office"
        pick = "".join(f'<option value="{e(f["name"])}">{e(f["label"])}</option>' for f in free)
        return (f'<form method="post" action="{e(url)}/{e(free[0]["name"])}/new" class="card stack" '
                f'data-office-new><h3 style="margin:0">New document</h3>'
                + (f'<label>In <select name="field">{pick}</select></label>' if len(free) > 1 else "")
                + '<label>Name <input name="name" maxlength="120" placeholder="Untitled"></label>'
                + '<div class="filters">' + "".join(
                    f'<button name="kind" value="{k}" class="secondary">{e(lbl)}</button>' for k, lbl in kinds)
                + "</div></form>")

    def post_office_new(self, req, rtype, rid, field):
        me, hub = req["me"], self.hub
        if not self.wopi or not self.office_ready():
            raise NotFound("office documents are not set up here")
        form = self.form(req)
        field = str(form.get("field") or field)
        kind = str(form.get("kind") or "")
        if kind not in cool.BLANK_KINDS:
            raise Invalid("pick a text document, a spreadsheet or a presentation")
        ref = f"{rtype}/{rid}"
        rt = hub.record_type(rtype)
        f = rt.field(field) if rt else None
        if not f or f["type"] != "attachment":
            raise Invalid("pick the field to put the document in")
        rec = hub.read_record(me, ref)
        path = f["path"].removesuffix(".*")
        if not f["path"].endswith(".*") and rec.get(path):
            raise Invalid(f"{f['label']} already holds a file")
        name = re.sub(r"[\x00-\x1f\x7f/\\]", "", str(form.get("name") or "")).strip()[:120] or "Untitled"
        name = name if name.lower().endswith("." + kind) else f"{name}.{kind}"
        att = hub.add_file(me, rtype, cool.blank_document(kind), name, cool.mime_of(kind))
        now = rec.get(path)
        value = [*now, att] if isinstance(now, list) else [att] if f["path"].endswith(".*") else att
        hub.update_record(me, ref, {path: value}, comment=f"New {cool.BLANK_KINDS[kind].lower()}: {name}")
        raise self._redirect(f"/records/{rtype}/{rid}/office/{field}/{att['blob']}")

    # ------------------------------------------------------------------------------------------ comments on a document
    def office_documents(self, rt, rec: dict) -> list[tuple[dict, dict]]:
        """The documents a record holds (field, attachment), as the member reads it: the kinds an office suite opens
        and plain text. They are commented on whether or not Collabora is set up."""
        out = []
        for f in rt.fields:
            if f["type"] != "attachment" or f.get("show", True) is False:
                continue
            now = rec.get(f["path"].removesuffix(".*"))
            for a in (now if isinstance(now, list) else [now]):
                if (isinstance(a, dict) and isinstance(a.get("blob"), str) and a.get("name")
                        and cool.extension(str(a["name"]), str(a.get("contentType") or ""))):
                    out.append((f, a))
        return out

    def office_comments_html(self, me, rt, rec: dict, only: tuple[dict, dict] | None = None, back: str = "") -> str:
        """Threads on each document of the record (or ``only`` one), and a form to start one: on the document as a
        whole, with words of it quoted if the writer wants. ``back``: the page the forms return to."""
        if not me or not rec.get("id"):
            return ""
        ref = f"{rt.id}/{rec['id']}"
        docs = [only] if only else self.office_documents(rt, rec)
        if not docs:
            return ""
        live = self.live
        url = f"/records/{e(rt.id)}/{e(rec['id'])}"
        may_write = self.hub.may_write(me, rt.id)
        nxt = f'<input type="hidden" name="next" value="{e(back)}">' if back else ""
        out = []
        for f, a in docs:
            name = str(a["name"])
            cs = live.comments(me, ref, field=f["name"], file=name)
            opened = [c for c in cs if not c["resolved"]]
            done = [c for c in cs if c["resolved"]]
            th = lambda c: self._thread_html(me, c, ref=ref, url=url, may_write=may_write, back=back)  # noqa: E731
            hid = "dc-" + __import__("hashlib").sha1(f"{f['name']}|{name}".encode()).hexdigest()[:8]
            out.append(
                f'<section class="doc-comments" aria-labelledby="{hid}"><h2 id="{hid}">Comments on {e(name)} '
                f'<span class="muted">{len(opened)}</span></h2>'
                f'<p class="muted doc-note">On the document as a whole. Write @ to ask a person or an agent: they are '
                f'told in their inbox. An agent answers here and may suggest a change as words; it never edits, saves, '
                f'signs or approves the document. A person makes any change in the document.</p>'
                + (f'<ul class="plain threads">{"".join(th(c) for c in opened)}</ul>' if opened else "")
                + (f'<details><summary>Resolved ({len(done)})</summary><ul class="plain threads">'
                   f'{"".join(th(c) for c in done)}</ul></details>' if done else "")
                + f'<form method="post" action="{url}/comments" class="stack c-new">{nxt}'
                  f'<input type="hidden" name="field" value="{e(f["name"])}">'
                  f'<input type="hidden" name="file" value="{e(name)}">'
                  f'<details class="c-quote-in"><summary>Quote words from the document (optional)</summary>'
                  f'<label>Words from the document<textarea name="quote" rows="2" maxlength="500" '
                  f'placeholder="Paste or type the words the comment is about"></textarea></label></details>'
                  f'<label>Comment<textarea name="body" rows="3" required data-at="{e(ref)}"></textarea></label>'
                  f'<div class="filters"><button class="primary">Comment</button></div></form></section>')
        return "".join(out)

    # ------------------------------------------------------------------------------------------ the editor page
    def _origin(self, req) -> str:
        h = req.get("headers") or {}
        proto = (h.get("x-forwarded-proto") or "http").split(",")[0].strip()
        host = (h.get("x-forwarded-host") or h.get("host") or "").split(",")[0].strip()
        return f"{proto}://{host}" if re.fullmatch(r"[A-Za-z0-9.\-\[\]:]+", host) and proto in ("http", "https") else ""

    def page_office(self, req, rtype, rid, field, blob):
        from .records_pages import Download
        me, hub = req["me"], self.hub
        if not self.wopi or not self.office_ready():
            raise NotFound("office documents are not set up here")
        ref = f"{rtype}/{rid}"
        self.wopi.settle(ref)
        sess = self.wopi.open(me, ref, field, blob)
        rec = hub.read_record(me, ref)
        ext = cool.extension(sess["name"], sess.get("contentType") or "")
        want_view = bool(req["query"].get("view"))
        write = not want_view and self.wopi.may_edit(me, rec)
        action = self.collabora.action(ext, "edit" if write else "view") if ext else None
        if not action:
            raise Invalid("Collabora does not open this kind of file")
        token, ttl = self.wopi.issue(me, sess["_id"], write=write, origin=self._origin(req))
        src = urllib.parse.quote(f"{self.collabora.wopi_base}/wopi/files/{sess['_id']}", safe="")
        prefs = hub.preferences(me["id"])
        lang = re.sub(r"[^A-Za-z-]", "", str(prefs.get("lang") or "en"))[:10] or "en"
        action_url = f"{action}WOPISrc={src}&lang={lang}&closebutton=1" + ("" if write else "&permission=readonly")
        back = f"/records/{urllib.parse.quote(rtype)}/{urllib.parse.quote(rid)}"
        notes = []
        if not want_view and not write:
            notes.append(f"Read only: {self._why_read_only(me, rec)}")
        if write and any(s["current"] for s in hub.signatures(me, ref)):
            notes.append("This version is signed. Saving a change makes a new version, which is not signed.")
        theme = f' data-theme="{e(prefs["theme"])}"' if prefs.get("theme") in ("light", "dark") else ""
        title = hub.title(rec)
        rt = hub.record_type(rtype)
        a = self.wopi.attachment(rec, field, blob) or {"blob": blob, "name": sess["name"]}
        here = req.get("path") or f"{back}/office/{field}/{blob}"
        here += "?view=1" if want_view else ""
        panel = self.office_comments_html(me, rt, rec, only=(rt.field(field), {**a, "name": sess["name"]}),
                                          back=here)
        n = len([c for c in self.live.comments(me, ref, field=field, file=sess["name"]) if not c["resolved"]])
        page = (f'<!doctype html><html lang="en"{theme}><head><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width, initial-scale=1">'
                f'<title>{e(sess["name"])} · {e(title)}</title><link rel="stylesheet" href="/static/app.css">'
                f'<style>{EDITOR_CSS}</style><script src="/static/app.js" defer></script>'
                f'<script src="/static/office.js" defer></script></head>'
                f'<body class="office" data-origin="{e(self.collabora.origin)}" data-back="{e(back)}">'
                f'<header class="office-bar"><a class="btn secondary" href="{e(back)}">&larr; Back to the record</a>'
                f'<span class="title">{e(title)} <span class="muted">· {e(sess["name"])}</span></span>'
                f'<span class="lozenge">{"Editing together" if write else "Reading"}</span>'
                + "".join(f'<span class="office-note" role="status">{e(n)}</span>' for n in notes)
                + '<span id="office-users" aria-live="polite"></span>'
                f'<button type="button" class="secondary" id="office-side-toggle" aria-controls="office-side" '
                f'aria-expanded="true">Comments <span class="muted">{n}</span></button></header>'
                f'<div class="office-main">'
                f'<form id="office-form" method="post" target="office-frame" action="{e(action_url)}">'
                f'<input type="hidden" name="access_token" value="{e(token)}">'
                f'<input type="hidden" name="access_token_ttl" value="{ttl}"></form>'
                f'<iframe id="office-frame" name="office-frame" title="{e(sess["name"])}" '
                f'allow="clipboard-read *; clipboard-write *; fullscreen"></iframe>'
                f'<aside class="office-side" id="office-side" aria-label="Comments">{panel}</aside></div>'
                f'</body></html>')
        o = self.collabora.origin
        csp = (f"default-src 'self'; frame-src {o}; form-action 'self' {o}; script-src 'self'; "
               f"style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; "
               f"base-uri 'none'; frame-ancestors 'self'")
        return Download(({"Content-Type": "text/html; charset=utf-8", "Content-Security-Policy": csp,
                          "Referrer-Policy": "no-referrer"}, page.encode()))

    def _why_read_only(self, me, rec) -> str:
        rt = self.hub.record_type(rec["resourceType"])
        lc = rt.lifecycle if rt else None
        st = schema.first(rec, lc["path"]) if lc else None
        if lc and st in lc.get("locked", []):
            moves = [t["label"] for t in lc["transition"] if st in t["from"] and t["by"] != "workflow"]
            return (f"a {rt.label.lower()} that is {st} is not edited"
                    + (f"; {' or '.join(moves)} it first" if moves else ""))
        return "you may read this record but not change it"

    # ------------------------------------------------------------------------------------------ WOPI
    def handle_wopi(self, method, raw_path, headers, body):
        url = urllib.parse.urlsplit(raw_path)
        if not self.wopi:
            return 404, {"Content-Type": "application/json"}, json.dumps({"error": "WOPI is not set up"}).encode()
        query = dict(urllib.parse.parse_qsl(url.query))
        return self.wopi.handle(method, url.path, query, headers, body, raw_path)
