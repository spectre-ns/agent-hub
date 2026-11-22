"""Pages for any record of any type (a patient, a controlled document, a CAPA), and for modules.

Nothing here knows a type: the page is drawn from the record type (its fields, lifecycle and signatures), and every
read, write, move and signature goes through the hub, which checks it. The server adds no permissions.
"""

from __future__ import annotations

import html
import itertools
import json
import re
import urllib.parse

from .. import pack as packmod
from .. import export as exportmod
from .. import schema, visibility
from ..hub import Conflict, HubError, Denied, Invalid, NotFound

e = html.escape
SIMPLE, NESTED, SCALAR = schema.FORM_SIMPLE, schema.FORM_NESTED, schema.SCALAR  # what a form can edit
# shown in the browser; anything else is downloaded. Every file is served sandboxed, with no scripts.
INLINE = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp", "text/plain"}


class Download(tuple):
    """(headers, body): bytes a route returns as they are, on a page or on the API."""


def file_response(data: bytes, meta: dict) -> Download:
    ctype = meta["contentType"]
    name = meta["name"]
    ascii_name = re.sub(r'[^A-Za-z0-9._ -]', "_", name)[:100] or "file"
    how = "inline" if ctype in INLINE else "attachment"
    csp = "default-src 'none'" + ("" if ctype == "application/pdf" else "; sandbox")  # a PDF viewer needs no sandbox
    return Download(({"Content-Type": ctype + ("; charset=utf-8" if ctype == "text/plain" else ""),
                      "Content-Disposition": f'{how}; filename="{ascii_name}"; '
                                             f"filename*=UTF-8''{urllib.parse.quote(name)}",
                      "Content-Security-Policy": csp}, data))


def multipart(req) -> dict:
    """A multipart/form-data body: {name: str} for fields, {name: (filename, content_type, bytes)} for files."""
    import email.parser
    import email.policy
    ctype = (req.get("headers") or {}).get("content-type", "")
    if not ctype.lower().startswith("multipart/form-data"):
        raise Invalid("send the file as multipart/form-data")
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + ctype.encode("latin-1") + b"\r\n\r\n" + (req["body"] or b""))
    out: dict = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        data = part.get_payload(decode=True) or b""
        if part.get_filename() is not None:
            out[name] = (part.get_filename(), part.get_content_type(), data)
        else:
            out[name] = data.decode("utf-8", "replace")
    return out


PICK = 300  # options a picker lists; past this it is a search box
REQ = '<span class="req" aria-hidden="true"> *</span>'  # a required field's marker (the control says required)
REQ_NOTE = f'<p class="muted req-note">{REQ.replace(" *", "*")} required</p>'
_IDS = itertools.count(1)  # control ids: unique on a page, and against a page a dialog opens over


def a_an(words: str) -> str:
    """The words with "a" or "an" before them, by how they are said: an appointment, a unit, an SOP."""
    w = words.strip()
    first = w.split(" ", 1)[0] if w else ""
    if len(first) > 1 and first.isupper():  # an acronym is said letter by letter
        return ("an " if first[0] in "AEFHILMNORSX" else "a ") + w
    low = w.lower()
    if low.startswith(("uni", "use", "usu", "uti", "eu", "one", "once")):
        return "a " + w
    if low.startswith(("hour", "honest", "honour", "heir")):
        return "an " + w
    return ("an " if low[:1] in "aeiou" else "a ") + w


class _Without:
    """A record type seen without some of its fields (those a form does not offer)."""

    def __init__(self, rt, skip):
        self._rt, self.fields = rt, [f for f in rt.fields if f["name"] not in skip]

    def __getattr__(self, k):
        return getattr(self._rt, k)


class RecordPages:
    def record_routes(self, R):
        R("GET", r"/records", self.page_record_types)
        R("GET", r"/records/(?P<rtype>[A-Za-z][\w]*)", self.page_records)
        R("POST", r"/records/(?P<rtype>[A-Za-z][\w]*)", self.post_record_create)
        R("GET", r"/records/(?P<rtype>[A-Za-z][\w]*)/new", self.page_record_new)
        R("GET", r"/create", self.page_create)
        R("GET", r"/records/(?P<rtype>[A-Za-z][\w]*)/export", self.page_export_list)  # before a record's own page
        R("GET", r"/records/(?P<rtype>[A-Za-z][\w]*)/(?P<rid>[\w.-]+)/export", self.page_export_record)
        R("GET", r"/records/(?P<rtype>[A-Za-z][\w]*)/(?P<rid>[\w.-]+)", self.page_record)
        R("POST", r"/records/(?P<rtype>[A-Za-z][\w]*)/(?P<rid>[\w.-]+)/(?P<verb>edit|move|sign)", self.post_record)
        R("GET", r"/records/(?P<rtype>[A-Za-z][\w]*)/(?P<rid>[\w.-]+)/move", self.page_move)
        R("POST", r"/records/(?P<rtype>[A-Za-z][\w]*)/(?P<rid>[\w.-]+)/attach", self.post_attach)
        R("POST", r"/records/(?P<rtype>[A-Za-z][\w]*)/(?P<rid>[\w.-]+)/override", self.post_override)
        R("GET", r"/records/(?P<rtype>[A-Za-z][\w]*)/(?P<rid>[\w.-]+)/files/(?P<blob>[0-9a-f]{64})", self.get_file)
        R("GET", r"/api/records/(?P<rtype>[A-Za-z][\w]*)/(?P<rid>[\w.-]+)/files/(?P<blob>[0-9a-f]{64})", self.get_file)
        R("POST", r"/api/files/(?P<rtype>[A-Za-z][\w]*)", self.api_add_file)
        R("GET", r"/api/records/(?P<rtype>[A-Za-z][\w]*)/(?P<rid>[\w.-]+)/signatures", self.api_signatures)
        R("POST", r"/api/records/(?P<rtype>[A-Za-z][\w]*)/(?P<rid>[\w.-]+)/signatures", self.api_sign)
        R("GET", r"/api/records/(?P<rtype>[A-Za-z][\w]*)/(?P<rid>[\w.-]+)/history", self.api_history)
        R("GET", r"/views/(?P<vid>[a-z][a-z0-9-]*)", self.page_view)
        R("GET", r"/modules", self.page_modules)
        R("POST", r"/modules", self.post_module)
        R("GET", r"/modules/(?P<mid>[a-z][a-z0-9-]*)", self.page_module)
        R("POST", r"/modules/(?P<mid>[a-z][a-z0-9-]*)/(?P<verb>upgrade|remove|review-code)", self.post_module_verb)

    # ------------------------------------------------------------------------------------------ export
    # Each is a small dialog (``data-modal``) listing the formats in plain words; choosing one downloads the file
    # (``?format=``). What goes out is a read on the track, as opening it is (practicehub/export.py).
    def _export_pick(self, items: list[tuple[str, str, str]]) -> str:
        """``items``: (href, format, hint). The print view opens in a tab of its own, the rest download."""
        out = []
        for href, fmt, hint in items:
            label, _, ext, what = exportmod.FORMATS[fmt]
            how = ' target="_blank" rel="noopener"' if fmt == "print" else " download"
            out.append(f'<li><a href="{e(href)}"{how} data-export="{e(fmt)}"><b>{e(label)}</b>'
                       f'<span class="muted">{e(hint or what)}</span></a></li>')
        return f'<ul class="pick">{"".join(out)}</ul>'

    def _export_page(self, req, title: str, intro: str, picks: str, crumbs, note: str = "") -> str:
        body = (f'<div data-modal-body><p>{intro}</p>{picks}'
                + (f'<p class="muted" role="note">{e(note)}</p>' if note else "")
                + '<p class="muted">Each record in the file is recorded as read by you, as opening it is. Keep the '
                  'file as safely as the record itself.</p></div>')
        return self.layout(req, title, body, crumbs=crumbs)

    @staticmethod
    def _download(x) -> Download:
        return Download((x.headers(), x.body))

    def _format(self, req) -> str | None:
        return (req["query"].get("format") or "").strip().lower() or None

    def page_export_record(self, req, rtype, rid):
        me, hub = req["me"], self.hub
        ref = f"{rtype}/{rid}"
        fmt = self._format(req)
        if fmt:
            return self._download(exportmod.record(hub, me, ref, fmt))
        rt = hub.record_type(rtype)
        if not rt or rtype in hub.member_types():
            raise NotFound(f"there is no {ref}")
        hub.require(me, "reader", f"data_class:{rt.data_class}", f"read {rtype} records")
        rec = hub.redact(me, hub.records.read(rtype, rid))
        if not rec:
            raise NotFound(f"there is no {ref}")
        base = f"/records/{rtype}/{urllib.parse.quote(rid)}/export?format="
        hints = {"json": "This kind of record is not a FHIR resource: its fields as JSON, for another system"}
        picks = self._export_pick([(base + f, f, hints.get(f, "")) for f in exportmod.offered(hub, rtype, "record")])
        return self._export_page(req, "Export", f"Export <b>{e(hub.short_title(rec))}</b> ({e(self._lower(rt.label))}):",
                                 picks, [("/records", "Records"), (f"/records/{rtype}", rt.plural),
                                         (self.record_href(ref), hub.short_title(rec)), (None, "Export")])

    def page_export_list(self, req, rtype):
        me, hub, q = req["me"], self.hub, req["query"]
        from .server import SHOWN
        text = (q.get("q") or "").lower() or None
        every = q.get("all") in ("1", "true", "yes")
        fmt = self._format(req)
        if fmt:
            return self._download(exportmod.records_list(hub, me, rtype, fmt, text=text, limit=SHOWN, every=every))
        rt = hub.record_type(rtype)
        if not rt or rt.abstract or rtype in hub.member_types():
            raise NotFound(f"there is no record type {rtype}")
        hub.require(me, "reader", f"data_class:{rt.data_class}", f"read {rtype} records")
        n = len(hub.search_records(me, rtype, limit=SHOWN, text=text))
        qs = f"q={urllib.parse.quote(text)}&" if text else ""
        fmts = exportmod.offered(hub, rtype, "list")
        hints = {"ndjson": "One FHIR resource a line, for another clinic's system (FHIR Bulk Data)"}
        what = (f'the {n} {e(self._lower(rt.plural if n != 1 else rt.label))} matching “{e(text)}”' if text
                else f'the {n} {e(self._lower(rt.plural if n != 1 else rt.label))} this list shows')
        picks = self._export_pick([(f"/records/{rtype}/export?{qs}format={f}", f, hints.get(f, "")) for f in fmts])
        if hub.is_policy_owner(me):  # a bulk export: every record of the type, for the privacy officer
            total = hub.db.records.count({"type": rtype})
            picks += (f'<h2 class="pick-head">Every {e(self._lower(rt.label))} record ({total})</h2>'
                      f'<p class="muted">A bulk export, which a policy owner may make. It is recorded as one.</p>'
                      + self._export_pick([(f"/records/{rtype}/export?all=1&format={f}", f, hints.get(f, ""))
                                           for f in fmts if f != "print"]))
        return self._export_page(req, f"Export {self._lower(rt.plural)}", f"Export {what}, with the list's columns, "
                                 "id, status and dates:", picks,
                                 [("/records", "Records"), (f"/records/{rtype}", rt.plural), (None, "Export")])

    def page_export_subject(self, req, pid):
        """A subject's whole file: what a person asks for under PHIA, or what goes to the clinic taking over care."""
        me, hub = req["me"], self.hub
        st = self.subj["type"]
        ref = f"{st}/{pid}"
        fmt = self._format(req)
        if fmt:
            return self._download(exportmod.subject_file(hub, me, ref, fmt))
        recs, hidden = exportmod.gather(hub, me, ref)
        word = self.subj["label"].lower()
        base = f"/{self.subj['path']}/{urllib.parse.quote(pid)}/export?format="
        others = sum(1 for r in recs if not exportmod.is_fhir(hub.record_type(r["resourceType"])))
        hints = {"fhir": f"A FHIR Bundle of the whole file, for the clinic taking over care"
                         + (f" ({others} record{'s' if others != 1 else ''} of other kinds go in the document only)"
                            if others else ""),
                 "odt": "Every record's fields as a labelled table, to give to the person who asked",
                 "json": "The whole file as JSON, for another system"}
        picks = self._export_pick([(base + f, f, hints.get(f, "")) for f in exportmod.offered(hub, st, "file")])
        name = hub.title(recs[0])
        return self._export_page(req, f"Export this {word}'s file",
                                 f"The whole file of <b>{e(name)}</b>: {len(recs)} record{'s' if len(recs) != 1 else ''}"
                                 f" — the {e(word)}, what is part of the file and what links to it.", picks,
                                 [(f"/{self.subj['path']}", self.subj["plural"]),
                                  (f"/{self.subj['path']}/{pid}", name), (None, "Export")],
                                 note=exportmod.left_out_words(hidden))

    # ------------------------------------------------------------------------------------------ helpers
    @staticmethod
    def _redirect(to: str):
        from .server import Redirect
        return Redirect(to)

    def record_href(self, ref: str) -> str:
        t, _, rid = ref.partition("/")
        if t in self.hub.member_types():  # a member of the practice: their page on the team
            return f"/team/{rid}"
        if t == self.subj["type"]:
            return f'/{self.subj["path"]}/{rid}'
        return f"/records/{t}/{rid}"

    def reader(self) -> dict | None:
        """Who this request is for: set per request, per thread."""
        local = getattr(self, "_local", None)
        return getattr(local, "me", None) if local is not None else None

    def start_buttons(self, me, rtype: str, ref: str) -> str:
        """The workflows this member may start about a record of this type: one button each, opening its start
        form in the dialog with the record already chosen. Every page that offers work about a record uses these."""
        hub = self.hub
        return "".join(
            f'<a class="btn" href="/start/{e(wf.id)}?record={e(urllib.parse.quote(ref, safe=""))}" data-modal>{e(wf.title)}</a>'
            for wf in (w["workflow"] for w in hub.workflows())
            if wf.record == rtype and hub.can(me, "starter", f"workflow:{wf.id}"))

    def record_link(self, ref: str) -> str:
        t, _, rid = ref.partition("/")
        if t in self.hub.member_types():
            return self.who(rid) if self.hub.db.members.exists({"_id": rid}) else e(ref)
        me = self.reader()
        rt = self.hub.record_type(t)
        if rt and (me is None or not self.hub.may_read(me, t)):  # its title is what it holds: not for this reader
            return f'<span class="muted">{e(rt.label)} you may not open</span>'
        rec = self.hub.records.read(t, rid) if self.hub.record_type(t) else None
        if rec:  # named by its title; the field's label says what it is
            return f'<a href="{e(self.record_href(ref))}">{e(self.hub.short_title(rec))}</a>'
        return f'<a href="{e(self.record_href(ref))}">{e(rt.label if rt else ref)}</a>'

    @staticmethod
    def _lower(label: str) -> str:
        """Lower-case a label for a sentence, keeping acronyms (CAPA, SOP) as they are."""
        return " ".join(w if w.isupper() and len(w) > 1 or w[:-1].isupper() and len(w) > 2 else w.lower()
                        for w in label.split())

    @staticmethod
    def _control_id(name: str, described: bool) -> tuple[str, str]:
        """An id for a form control, and its attributes (``aria-describedby`` its help, when it has some)."""
        cid = f"f-{re.sub(r'[^A-Za-z0-9_-]', '-', name)}-{next(_IDS)}"
        return cid, f' id="{cid}"' + (f' aria-describedby="{cid}-help"' if described else "")

    @staticmethod
    def _label_words(f) -> str:
        return e(str(f.get("label") or f.get("name") or "")) + (REQ if f.get("required") else "")

    def _labelled(self, f, ctl: str, cid: str, helps: list[str]) -> str:
        """A control in its label; its help below the label, read with the control (``aria-describedby``)."""
        if not helps:
            return f"<label>{self._label_words(f)}{ctl}</label>"
        return (f'<div class="field"><label>{self._label_words(f)}{ctl}</label>'
                f'<small id="{cid}-help" class="help">{" · ".join(e(h) for h in helps)}</small></div>')

    CARDS = 5   # a single choice of this many or fewer is a row of option cards, not a drop-down
    CHIPS = 12  # a choice of several, of this many or fewer, is a row of chips to tick

    def _option_cards(self, f, options: list[tuple[str, str]], chosen: list[str], *, multi: bool = False) -> str:
        """A short choice as option cards (one of them) or chips (several): the native radio or checkbox is kept,
        hidden, for the keyboard, screen readers, ``required`` and posting; the card around it is what shows."""
        name = f["name"]
        cid, ids = self._control_id(name, bool(f.get("help")))
        kind, req = ("checkbox", "") if multi else ("radio", " required" if f.get("required") else "")
        if not multi and not f.get("required"):  # an optional choice can be left unset, as the empty option was
            options = [("", "None")] + list(options)
            chosen = chosen or [""]
        cards = "".join(f'<label class="radio{" multi" if multi else ""}"><input type="{kind}" class="opt-input" '
                        f'name="{e(name)}" value="{e(v)}"{req}{" checked" if v in chosen else ""}> <span>{e(lbl)}</span></label>'
                        for v, lbl in options)
        group = (f'<fieldset class="choices options"{ids}><legend>{self._label_words(f)}</legend>{cards}</fieldset>')
        if not f.get("help"):
            return group
        return f'<div class="field">{group}<small id="{cid}-help" class="help">{e(f["help"])}</small></div>'

    @staticmethod
    def _req_note(controls: str) -> str:
        """The one "* required" note a form with a required field starts with."""
        return REQ_NOTE if REQ in controls else ""

    @staticmethod
    def _editable(rt) -> list[dict]:
        return schema.form_fields(rt)

    def _value_html(self, f, rec) -> str:
        vals = [v for v in schema.get(rec, schema.locate(rec, f["path"], f.get("with"))) if v not in (None, "", [], {})]
        out = []
        for v in vals:
            for item in (v if isinstance(v, list) else [v]):
                if f["type"] == "attachment" and isinstance(item, dict):
                    out.append(self._attachment_html(rec, item))
                elif f["type"] == "markdown" and isinstance(item, str):
                    out.append(self.markdown_html(item, rec))
                elif f["type"] == "reference" and schema.ref_of(item):
                    out.append(self.record_link(schema.ref_of(item)))
                elif f["type"] == "member" and isinstance(item, str):
                    out.append(self.who(item.removeprefix("member:")))
                elif f["type"] == "datetime" and isinstance(item, str) and self._datetime_words(item):
                    out.append(f'<time datetime="{e(item)}">{e(self._datetime_words(item))}</time>')
                elif (f["type"] == "choice" and f["path"].endswith(".code") and str(item) not in (f.get("labels") or {})
                      and self._display_of(rec, f, item)):  # the type's own words for a code come first
                    out.append(e(self._display_of(rec, f, item)))  # a coded value: its display, not its code
                elif isinstance(item, (dict, list)):
                    out.append(self._plain_html(item))
                elif isinstance(item, str) and "\n" in item:
                    out.append(f"<pre class='wrap'>{e(item)}</pre>")
                elif f["type"] == "choice":
                    out.append(e(self._choice_label(f, item)))
                else:
                    out.append(e(str(item)))
        return "<br>".join(out) if out else "<span class='muted'>—</span>"

    @staticmethod
    def _datetime_words(v: str) -> str:
        """A stored date and time in words: Sun 22 Nov 2026, 09:44 (as kept; one with a zone in local time)."""
        from datetime import datetime
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return ""
        if d.tzinfo is not None:
            d = d.astimezone()
        return f"{d:%a} {d.day} {d:%b %Y}, {d:%H:%M}"

    @staticmethod
    def _display_of(rec, f, code) -> str:
        """The words a coding keeps beside its code (``class.display`` beside ``class.code``), if any."""
        for c in schema.get(rec, f["path"][:-5]):
            for x in (c if isinstance(c, list) else [c]):
                if isinstance(x, dict) and x.get("code") == code and isinstance(x.get("display"), str) and x["display"]:
                    return x["display"]
        return ""

    def markdown_html(self, text: str, rec: dict | None, me: dict | None = None) -> str:
        """Formatted text as HTML for the member reading it: a ``[[link]]`` to what they may not read shows only its
        words, as a link that goes nowhere."""
        from .. import markup
        hub = self.hub
        me = me or self.reader()
        rt = hub.record_type((rec or {}).get("resourceType", "")) if rec else None

        def resolve(target):
            if target.startswith("work:"):
                rid = target[5:].strip()
                try:  # anyone may open a piece of work; its title names its record only to who may read it
                    run = hub.engine.run(rid) if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", rid) else None
                except Exception:  # noqa: BLE001 - no such work
                    run = None
                return (f"/runs/{rid}", hub.run_title(me, run), True) if run else None
            ref = hub.mention_ref(rt, target) if rt else (target if schema.REF_TEXT.fullmatch(target) else None)
            if not ref:
                return None
            t = ref.split("/")[0]
            if me is None or not hub.may_read(me, t):
                return "", target, False
            r = hub.records.read(*ref.split("/", 1))
            return (self.record_href(ref), hub.title(r), True) if r else None
        return f'<div class="prose">{markup.render(text, resolve)}</div>'

    @staticmethod
    def _has_value(f, rec) -> bool:
        return any(v not in (None, "", [], {}) for v in schema.get(rec, schema.locate(rec, f["path"], f.get("with"))))

    def _attachment_html(self, rec, a: dict) -> str:
        name = str(a.get("name") or a.get("title") or "file")
        if isinstance(a.get("blob"), str) and re.fullmatch(r"[0-9a-f]{64}", a["blob"]) and rec.get("id"):
            size = a.get("size")
            kb = f' <span class="muted">{max(1, round(size / 1024))} KB</span>' if isinstance(size, int) else ""
            href = f"/records/{urllib.parse.quote(str(rec.get('resourceType', '')))}/{urllib.parse.quote(rec['id'])}/files/{a['blob']}"
            office = self.office_buttons(rec, a) if hasattr(self, "office_buttons") else ""  # Collabora, if set up
            return f'<a href="{e(href)}">{e(name)}</a>{kb}{office}'
        url = str(a.get("url") or "")
        if re.match(r"https?://", url):
            return f'<a href="{e(url)}" rel="noopener noreferrer">{e(name if a.get("name") else url)}</a>'
        return e(name)

    HIDDEN_KEYS = {"resourceType", "id", "meta", "implicitRules", "contained", "extension", "modifierExtension"}

    @staticmethod
    def _plain_of(item):
        """A common shape as one short phrase, or None: a code, an amount, a name, a span of time."""
        if not isinstance(item, dict):
            return None
        keys = set(item)
        if keys & {"coding", "text"} and keys <= {"coding", "text"}:  # a coded concept: its words
            codes = item.get("coding") or []
            return item.get("text") or next((c.get("display") or c.get("code") for c in codes if isinstance(c, dict)
                                             and (c.get("display") or c.get("code"))), None)
        if "code" in keys and keys <= {"system", "code", "display", "version", "userSelected"}:
            return item.get("display") or item.get("code")
        if "value" in keys and keys <= {"value", "unit", "system", "code", "comparator"}:  # an amount
            return " ".join(str(x) for x in (item.get("comparator"), item.get("value"), item.get("unit") or
                                             item.get("code")) if x not in (None, ""))
        if keys & {"given", "family"} and keys <= {"given", "family", "prefix", "suffix", "use", "text"}:
            return item.get("text") or " ".join([*(item.get("prefix") or []), *(item.get("given") or []),
                                                 item.get("family") or "", *(item.get("suffix") or [])]).strip()
        if keys & {"start", "end"} and keys <= {"start", "end"}:
            return " – ".join(RecordPages._when(item.get(k)) for k in ("start", "end") if item.get(k))
        if "value" in keys and keys <= {"system", "value", "use", "rank", "period", "type"}:  # a phone, an id
            how = item.get("system")
            return f"{item['value']}" + (f" ({how}{', ' + item['use'] if item.get('use') else ''})" if how else "")
        return None

    @staticmethod
    def _when(v) -> str:
        v = str(v)
        return v[:16].replace("T", " ") if re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d.*", v) else v

    def _key_words(self, k) -> str:
        k = str(k)
        if re.fullmatch(r"(value|effective|onset|abatement|deceased|multipleBirth|occurrence|performed|born)[A-Z]\w*", k):
            k = re.match(r"[a-z]+(?:Birth)?", k).group(0)  # valueQuantity is the value; effectiveDateTime is when
        return {"effective": "When", "issued": "Reported", "authoredOn": "Written"}.get(k, self.human(k))

    def _plain_html(self, item, depth: int = 0) -> str:
        """A nested value in words, not JSON: a {title, text} section as a heading and its text, a {text} as its
        text, a reference as a link, a code or amount or name as its words, other objects as labelled lines."""
        if isinstance(item, list):
            return "".join(f"<div>{self._plain_html(x, depth + 1)}</div>" for x in item if x not in (None, "", {}, []))
        if not isinstance(item, dict):
            if isinstance(item, bool):
                return "Yes" if item else "No"
            text = "" if item is None else self._when(item) if isinstance(item, str) else str(item)
            return f"<span class='wrap'>{e(text)}</span>" if "\n" not in text else f"<pre class='wrap'>{e(text)}</pre>"
        if schema.ref_of(item) and set(item) <= {"reference", "display", "type"}:
            return self.record_link(schema.ref_of(item))
        if "text" in item and set(item) <= {"title", "text", "status", "div"}:
            head = f"<h4 class='section-title'>{e(item['title'])}</h4>" if item.get("title") else ""
            body = str(item.get("text") or "")
            return head + (f"<p class='wrap'>{e(body)}</p>" if body else "<p class='muted'>—</p>")
        phrase = self._plain_of(item)
        if phrase:
            return f"<span class='wrap'>{e(phrase)}</span>"
        shown = {k: v for k, v in item.items() if v not in (None, "", {}, []) and k not in self.HIDDEN_KEYS}
        if depth > 3:  # deep: its words on one line, never JSON
            return "<span class='wrap'>" + e("; ".join(f"{self._key_words(k)}: {self._flat(v)}" for k, v in shown.items())) + "</span>"
        rows = "".join(f"<div><span class='muted'>{e(self._key_words(k))}:</span> {self._plain_html(v, depth + 1)}</div>"
                       for k, v in shown.items())
        return f"<div class='kv'>{rows}</div>"

    def _flat(self, v) -> str:
        if isinstance(v, dict):
            if schema.ref_of(v):
                return v.get("display") or schema.ref_of(v)
            return self._plain_of(v) or ", ".join(self._flat(x) for k, x in v.items() if k not in self.HIDDEN_KEYS)
        if isinstance(v, list):
            return ", ".join(self._flat(x) for x in v)
        return self._when(v) if isinstance(v, str) else str(v)

    def _resolve_ref(self, f, typed: str) -> str:
        """A reference as the form sent it: ``Type/id``, or a name typed into a search box, found in the store."""
        typed = str(typed).strip()
        targets = self.hub.concrete(f["target"])  # a base type's target (Subject) takes each type extending it
        if "/" in typed and typed.split("/", 1)[0] in (set(targets) | self.hub.member_types()):
            return typed
        hits = []
        if " · " in typed:  # picked from the suggestions: the id after the dot decides
            rid = typed.rsplit(" · ", 1)[1].strip()
            for t in targets:
                if t != "*" and t not in self.hub.member_types() and self.hub.records.read(t, rid):
                    return f"{t}/{rid}"
        lead = lambda text: re.split(r" \(| · ", text, maxsplit=1)[0].strip()  # "Name (born …) · card …": its name part
        for t in targets:
            if t == "*" or t in self.hub.member_types() or not self.hub.record_type(t):
                continue
            words = lead(typed)  # a title as shown is searched by its name part
            found = self.hub.search_records(None, t, limit=50, text=words)  # words seen by every reader
            name = lambda r: self.hub.title(r).strip().lower()
            exact = [r for r in found if r["id"] == typed or typed.lower() in (name(r), lead(name(r)))]
            hits += [f"{t}/{r['id']}" for r in (exact or found)]
        if len(hits) == 1:
            return hits[0]
        raise Invalid(f"{f['label']}: " + (f"no record is called {typed!r}" if not hits else
                                           f"{len(hits)} records are called {typed!r}: type more of it (a date of "
                                           f"birth or number), or open the one you mean and link it from there"))

    def _input(self, f, rec, me) -> str:
        """A form control for a field, filled from the record (none for a field of a class they may not write)."""
        if not visibility.may_field(self.hub, me, f, write=True):
            return ""
        name = f["path"].removesuffix(".*")
        vals = [v for v in schema.get(rec, schema.locate(rec, f["path"], f.get("with"))) if v not in (None, "")] \
            if rec else []
        spec = {**f, "name": name}
        if f["type"] == "reference":
            refs = [schema.ref_of(v) for v in vals if schema.ref_of(v)]
            options, many = [], False
            members = self.hub.member_types()
            for t in self.hub.concrete(f["target"]):
                if t in members and f.get("roles"):  # only the people who hold one of the field's roles
                    held = {f"{t}/{m['id']}": m["name"] for rid in f["roles"]
                            for m in self.hub.active_holders(rid, persons_only=True)}
                    options += sorted(held.items(), key=lambda o: o[1])
                elif t in members:  # a reference to a member of the practice: the people on the team
                    options += [(f"{t}/{m['id']}", m["name"]) for m in self.hub.members("person")
                                if m["status"] == "active"]
                elif t != "*" and self.hub.record_type(t) and self._may_read(me, t):
                    found = self.hub.records.search(t, limit=PICK + 1)
                    many = many or len(found) > PICK
                    options += [(f"{t}/{r['id']}", self.hub.title(r)) for r in found[:PICK]]
            # what the record points at now is always an option, even beyond the list or not readable by this
            # member: saving the form never drops a link the member did not touch
            have = {v for v, _ in options}
            def shown(r):
                t, _, rid = r.partition("/")
                if t in members:
                    m = self.hub.db.members.find_one({"_id": rid}, fields=["name"])
                    return m["name"] if m else r
                return (self.hub.title(self.hub.records.read(t, rid) or {}) or r) if self._may_read(me, t) else r
            options = [(r, shown(r)) for r in refs if r not in have] + options
            own = self.hub.record_type((rec or {}).get("resourceType", ""))
            if own and own.parent == f["path"] and rec.get("id"):  # a record is never its own parent
                options = [(v, lbl) for v, lbl in options if v != f"{own.id}/{rec['id']}"]
            multi = f["path"].endswith(".*")
            if many and not multi:  # too many to list: type a name; the store is searched when the form is saved
                dl = f"dl-{abs(hash(name)) % 10**8}"
                cur = refs[0] if refs else ""
                tag = lambda v, l: f"{l} · {v.split('/', 1)[1]}"  # a name and its id: two people may share a name
                lbl = tag(cur, dict(options).get(cur, cur)) if cur else ""
                req = " required" if f.get("required") else ""
                cid, ids = self._control_id(name, True)
                ctl = (f'<input name="{e(name)}"{ids} list="{dl}" value="{e(lbl)}" autocomplete="off"{req} '
                       f'placeholder="type a name or number"><datalist id="{dl}">'
                       + "".join(f'<option value="{e(tag(v, l))}">' for v, l in options) + '</datalist>')
                helps = ([f["help"]] if f.get("help") else []) + [
                    "Type a name; the closest few are suggested, and any record can be found."]
                return self._labelled(f, ctl, cid, helps)
            if multi and len(options) <= self.CHIPS and options:  # a few to choose several of: chips to tick
                return self._option_cards({**f, "name": name}, options, refs, multi=True)
            opts = "".join(f'<option value="{e(v)}"{" selected" if v in refs else ""}>{e(lbl)}</option>'
                           for v, lbl in options)
            req = " required" if f.get("required") else ""
            cid, ids = self._control_id(name, bool(f.get("help")))
            ctl = (f'<select name="{e(name)}"{ids}{" multiple size=4" if multi else ""}{req}>'
                   f'{"" if multi else "<option value=></option>"}{opts}</select>')
            return self._labelled(f, ctl, cid, [f["help"]] if f.get("help") else [])
        val = vals[0] if vals else None
        if self._as_json(f, val):  # structured values are edited as JSON, never as Python's repr
            txt = json.dumps(val, indent=1, ensure_ascii=False) if val not in (None, "", [], {}) else ""
            req = " required" if f.get("required") else ""
            cid, ids = self._control_id(name, True)
            ctl = (f'<textarea class="mono" name="{e(name)}"{ids} rows="{min(12, txt.count(chr(10)) + 2)}" '
                   f'spellcheck="false"{req}>{e(txt)}</textarea>')
            return self._labelled(f, ctl, cid, ["JSON"] + ([f["help"]] if f.get("help") else []))
        if f["type"] == "datetime" and isinstance(val, str):
            if "T" in val:
                val = val[:16]  # what a datetime-local input shows; seconds and zone are kept unless changed
            elif val:  # a date-only value: a date input, so saving does not blank it
                spec = {**spec, "type": "date"}
        if f["type"] == "member" and isinstance(val, str):
            val = val.removeprefix("member:")
        if f["type"] == "boolean" and not f.get("required"):  # not ticked is not "no": unknown stays unknown
            cur = "" if val is None else ("true" if val else "false")
            opts = "".join(f'<option value="{v}"{" selected" if v == cur else ""}>{lbl}</option>'
                           for v, lbl in (("", "Not recorded"), ("true", "Yes"), ("false", "No")))
            cid, ids = self._control_id(name, bool(f.get("help")))
            return self._labelled(f, f'<select name="{e(name)}"{ids}>{opts}</select>', cid,
                                  [f["help"]] if f.get("help") else [])
        choices = ([{"value": c, "label": self._choice_label(f, c)} for c in f["choices"]]
                   if f["type"] == "choice" else None)
        if f["type"] == "markdown":  # the editor starts from the text as its reader sees it, and offers the titles
            rt = self.hub.record_type((rec or {}).get("resourceType", "")) if rec else None
            spec["_start"] = self.markdown_html(str(val or ""), rec if rt else None, me)
            if rt and self._may_read(me, rt.id):
                spec["_titles"] = sorted({self.hub.title(r) for r in self.hub.records.search(rt.id, limit=300)})
        return self._field(spec, choices, val)

    def _on_screen(self, rt, op: str) -> list[dict] | None:
        """The tabs of the type's screen for ``op``, each with the editable fields it shows (None: no screen)."""
        tabs = rt.screen(op)
        if tabs is None:
            return None
        editable = {f["name"]: f for f in self._editable(rt)}
        return [{"label": t["label"], "fields": [editable[n] for n in t["fields"] if n in editable]} for t in tabs]

    def _advanced_off_screen(self, rt, op: str) -> list[dict]:
        shown = {n for t in rt.screen(op) or [] for n in t["fields"]}
        return [f for f in self._editable(rt) if f.get("advanced") and f["name"] not in shown]

    def _tabbed(self, tabs: list[dict], draw, key: str) -> str:
        """Tabs drawn as labelled groups, with links between them when there are several (no script needed)."""
        tabs = [t for t in tabs if t["fields"]]
        if len(tabs) == 1 and not tabs[0]["label"]:
            return "".join(draw(f) for f in tabs[0]["fields"])
        ids = [f"{key}-{i}" for i in range(len(tabs))]
        nav = ('<nav class="tabs form-tabs" aria-label="Parts of the form">' + "".join(
            f'<a href="#{i}">{e(t["label"] or "Details")}</a>' for i, t in zip(ids, tabs)) + "</nav>") \
            if len(tabs) > 1 else ""
        return nav + "".join(f'<fieldset id="{i}" class="tabpanel"><legend>{e(t["label"] or "Details")}</legend>'
                             f'{"".join(draw(f) for f in t["fields"])}</fieldset>' for i, t in zip(ids, tabs))

    def _inputs(self, rt, rec, me, op: str = "edit", skip=()) -> str:
        """The form's controls: the type's screen for ``op`` (create, edit) in its tabs, or, with no screen, every
        field with the pack's ``advanced`` ones (whole structures, as JSON) folded away below. ``skip``: names of
        fields the form does not offer (a part's owner, set already)."""
        tabs = self._on_screen(rt, op)
        if skip:
            tabs = [{**t, "fields": [f for f in t["fields"] if f["name"] not in skip]} for t in tabs] \
                if tabs is not None else None
            rt = _Without(rt, skip)
        if tabs is not None:
            out = self._tabbed(tabs, lambda f: self._input(f, rec, me), f"{op}-{rt.id.lower()}")
            if op == "edit":  # the whole values (as JSON) stay in reach below the screen, as without one
                adv = "".join(self._input(f, rec, me) for f in self._advanced_off_screen(rt, op))
                if adv:
                    out += f'<details class="advanced"><summary>All values, as JSON</summary>{adv}</details>'
            return out
        plain = "".join(self._input(f, rec, me) for f in self._editable(rt) if not f.get("advanced"))
        adv = "".join(self._input(f, rec, me) for f in self._editable(rt) if f.get("advanced"))
        if adv:
            plain += (f'<details class="advanced"><summary>All values, as JSON</summary><p class="muted">Only for what '
                      f'the fields above do not cover (a second phone, an old address).</p>{adv}</details>')
        return plain

    @staticmethod
    def _as_json(f, val) -> bool:
        return f["type"] in ("object", "json") or (f["type"] == "list" and isinstance(val, list)
                                                   and any(isinstance(x, (dict, list)) for x in val))

    def _coerce(self, f, raw):
        """A form value as the field's type; None clears it."""
        if f["type"] == "reference":
            vals = raw if isinstance(raw, list) else [raw]
            vals = [self._resolve_ref(f, v) for v in vals if v]
            if f["path"].endswith(".*"):
                return vals
            return vals[-1] if vals else None
        raw = raw[-1] if isinstance(raw, list) else raw  # a checked box sends "false" then "true"
        if raw in (None, ""):
            return None
        if f["type"] == "number":
            try:
                n = float(raw)
            except ValueError:
                raise Invalid(f"{f['label']}: {raw!r} is not a number") from None
            return int(n) if n.is_integer() else n
        if f["type"] == "boolean":
            return raw == "true"
        text = str(raw).strip()
        if f["type"] in ("object", "json") or (f["type"] == "list" and text.startswith("[")):
            try:
                val = json.loads(text)
            except ValueError as exc:
                raise Invalid(f"{f['label']}: this is not valid JSON ({exc.msg}, line {exc.lineno})") from None
            if f["type"] == "list" and not isinstance(val, list):
                raise Invalid(f"{f['label']}: a list, in [ ]")
            if f["type"] == "object" and not isinstance(val, dict):
                raise Invalid(f"{f['label']}: an object, in {{ }}")
            return val
        if f["type"] == "list":
            return [x.strip() for x in text.splitlines() if x.strip()]
        return raw

    def _from_form(self, rt, form: dict, *, partial: bool, before: dict | None = None, op: str | None = None,
                   only: list[str] | None = None, lenient: bool = False) -> dict:
        """The values a form sent, for the fields it showed: the screen for ``op``, or ``only`` these names.
        ``lenient``: a value that is not of its field's type is left out, not refused (to show a refused form again)."""
        def coerce(f, raw):
            try:
                return True, self._coerce(f, raw)
            except Invalid:
                if not lenient:
                    raise
                return False, None
        data = {}
        fields = self._editable(rt)
        if only is None and op is not None and rt.screen(op) is not None:
            only = [n for t in rt.screen(op) for n in t["fields"]]
            if op == "edit":
                only += [f["name"] for f in self._advanced_off_screen(rt, op)]
        if only is not None:
            fields = [f for f in fields if f["name"] in only]
        # whole values first, then the plain values inside them, so a field for name.0.family edits the name
        fields = [f for f in fields if not NESTED.fullmatch(f["path"])] + [f for f in fields if NESTED.fullmatch(f["path"])]
        for f in fields:
            key = f["path"].removesuffix(".*")
            if NESTED.fullmatch(f["path"]):
                if key not in form:
                    continue
                ok, val = coerce(f, form.get(key, []))
                if not ok:
                    continue
                top = key.split(".", 1)[0]
                current = {top: data[top]} if top in data else (before or {})
                where = schema.locate(current, key, f.get("with"))
                old = schema.first(before or {}, schema.locate(before or {}, key, f.get("with")))
                if f["type"] == "reference":  # one inside a list or object (participant.0.actor): sent as Type/id
                    if val == schema.ref_of(old):
                        continue
                    if isinstance(old, dict) and val:  # kept in the shape the record holds it in, display dropped
                        val = {**{k: v for k, v in old.items() if k != "display"}, "reference": val}
                if f["type"] == "datetime" and isinstance(old, str) and isinstance(val, str) and old.startswith(val):
                    continue  # the input shows minutes; an unchanged value keeps its seconds and zone
                if val == old or (val in (None, "") and old in (None, "")):
                    continue
                rest = where.split(".", 1)[1]
                data[top] = schema.set_in(current.get(top), rest.split("."), val, f.get("with"))
                continue
            if key not in form and not (f["type"] == "reference" and f["path"].endswith(".*") and partial):
                continue
            ok, val = coerce(f, form.get(key, []))
            if not ok:
                continue
            if before is not None:
                old = before.get(key)
                if f["type"] == "datetime" and isinstance(old, str) and isinstance(val, str) and old.startswith(val):
                    continue  # the input shows minutes; an unchanged value keeps its seconds and zone
                if f["type"] == "reference" and old is not None:  # the form sends Type/id; the record holds {reference}
                    old = [schema.ref_of(x) for x in old] if isinstance(old, list) else schema.ref_of(old)
                if val == old or (val in (None, []) and old in (None, "", [], {})):
                    continue  # unchanged: leave it as it is
            data[key] = val
        if not partial:
            data = {k: v for k, v in data.items() if v is not None}
        return data

    # ------------------------------------------------------------------------------------------ lists
    def page_record_types(self, req):
        me, hub = req["me"], self.hub
        rows = []
        for t, rt in sorted(hub.record_types().items(), key=lambda kv: (kv[1].order, kv[0])):
            if not self._may_read(me, t) or rt.part_of:  # a part is reached through its owner
                continue
            n = {"n": hub.db.records.count({"type": t})}
            rows.append(f'<tr><td><a href="/records/{e(t)}">{e(rt.plural)}</a></td><td>{e(rt.summary)}</td>'
                        f'<td class="muted">{e(rt.data_class)}</td><td>{n["n"] if n else ""}</td></tr>')
        body = ('<div class="tablewrap"><table><tr><th>Records</th><th>What they are</th><th>Data class</th>'
                '<th>Count</th></tr>' + "".join(rows) + "</table></div>") if rows else (
            "<p class='muted'>You may not read any record types.</p>")
        return self.layout(req, "Records", body)

    def page_records(self, req, rtype):
        me, hub = req["me"], self.hub
        if rtype in hub.member_types():  # members are the team, not records
            from .server import Redirect
            raise Redirect("/team")
        rt = hub.record_type(rtype)
        if not rt:
            raise Invalid(f"there is no record type {rtype}")
        if not hub.may_read(me, rtype):
            raise Denied(f"{me['name']} may not read {rt.plural.lower()}")
        q = req["query"].get("q", "").lower()
        from .server import SHOWN
        linked = req["query"].get("linked", "")
        if linked:  # every one linked to a record ("Show all" under its links): the record's page shows 50
            recs, more = self._linked_records(me, rtype, rt, linked), False
        else:
            recs = hub.search_records(me, rtype, limit=SHOWN + 1, text=q or None)  # without what they may not read
            more, recs = len(recs) > SHOWN, recs[:SHOWN]
        hub.note_list(me, recs, how="search" if q else "list", what=f"records:{rtype}", text=q or None)
        lc = rt.lifecycle
        heads = visibility.shown(hub, me, rt, schema.list_columns(rt))  # a part: which record it is part of, first; the export has the same
        rows = "".join(
            f'<tr><td><a href="{e(self.record_href(f"{rtype}/{r["id"]}"))}">{e(hub.title(r))}</a></td>'
            + (f'<td>{self._state_loz(schema.first(r, lc["path"]), rt)}</td>' if lc else "")
            + "".join(f"<td>{self._value_html(f, r)}</td>" for f in heads) + "</tr>" for r in recs)
        table = (f'<div class="tablewrap"><table><tr><th>{e(rt.label)}</th>' + ("<th>Status</th>" if lc else "")
                 + "".join(f"<th>{e(f['label'])}</th>" for f in heads) + f"</tr>{rows}</table></div>") if recs else (
            f"<p class='muted'>No {e(self._lower(rt.plural))} yet.</p>")
        if req["query"].get("parent"):  # the old way to add under a record: its create form now has its own page
            raise self._redirect(f"/records/{rtype}/new?parent={urllib.parse.quote(req['query']['parent'])}")
        create = (f'<a class="btn primary" href="/records/{e(rtype)}/new" data-modal>Create</a>'
                  if hub.may_write(me, rtype) and rtype != self.subj["type"] and not rt.part_of else "")
        if rt.part_of:
            ol = self._owner_label(rt)
            table = (f'<div class="flash info"><p>Each {e(self._lower(rt.label))} is part of {e(a_an(ol))}: open one '
                     f'from its {e(ol)}, and add one there.</p></div>' + table)
        search = (f'<form class="inline"><input name="q" value="{e(q)}" placeholder="Search {e(self._lower(rt.plural))}" aria-label="Search {e(self._lower(rt.plural))}">'
                  f'<button class="secondary">Search</button></form>')
        note = (f"<p class='muted'>The {SHOWN} most recently changed are shown: search to find any other.</p>"
                if more else "")
        qs = f"?q={urllib.parse.quote(q)}" if q else ""
        export = (f'<a class="btn secondary" href="/records/{e(rtype)}/export{e(qs)}" data-modal>Export</a>'
                  if recs or hub.is_policy_owner(me) else "")
        if linked:
            owner = hub.short_title(hub.redact(me, hub.records.read(*linked.split("/", 1))) or {})
            title = f"{rt.plural} linked to {owner}"
            back = f'<p><a href="{e(self.record_href(linked))}">Back to {e(owner)}</a></p>'
            return self.layout(req, title, back + table, crumbs=[("/records", "Records"), (f"/records/{rtype}", rt.plural),
                                                                 (self.record_href(linked), owner), (None, "Linked")],
                               subnav=self.navigator(me, rtype))
        return self.layout(req, rt.plural, search + table + note,
                           crumbs=[("/records", "Records"), (None, rt.plural)], subnav=self.navigator(me, rtype),
                           actions=export + create)

    def _linked_records(self, me, rtype, rt, ref) -> list[dict]:
        """Every record of ``rtype`` that points at ``ref`` and that the member may read, newest first."""
        hub = self.hub
        t, _, rid = ref.partition("/")
        if not rid or not hub.record_type(t):
            raise Invalid("say which record, as Type/id")
        if not hub.may_read(me, t) or not hub.redact(me, hub.records.read(t, rid)):
            raise NotFound(f"there is no record {ref}")
        out: dict = {}
        for x in hub.linked(me, ref):
            xt, _, xid = x["ref"].partition("/")
            if xt != rtype or xid in out:
                continue
            r = hub.redact(me, hub.records.read(xt, xid))
            if r:
                out[xid] = r
                hub.note_view(me, x["ref"], how=f"linked to {ref}")
        return sorted(out.values(), key=lambda r: schema.record_date(rt, r), reverse=True)

    def page_record_new(self, req, rtype, kept: dict | None = None, error: Exception | None = None):
        """A record type's create form, on its own: the Create dialog shows it over the page (``data-modal``), and
        without the script it is a page. A refusal shows it again, saying why, with what was typed kept."""
        me, hub = req["me"], self.hub
        rt = hub.record_type(rtype)
        if not rt or rtype in hub.member_types():
            raise Invalid(f"there is no record type {rtype}")
        if not hub.may_write(me, rtype):
            raise Denied(f"{me['name']} may not add {rt.plural.lower()}")
        lc = rt.lifecycle
        q = req["query"]
        under = q.get("parent", "") or (kept or {}).get("_parent", "")
        seed = {k: v for k, v in (kept or {}).items() if k not in ("_parent", "_owner")} or None
        if rt.parent and under.startswith(rtype + "/"):
            seed = {"resourceType": rtype, **(seed or {}), **schema.put({}, rt.parent, {"reference": under})}
        for f in rt.fields:  # a link named in the address (New visit, from a patient's page): filled in
            got = q.get(f["name"]) or q.get(f["path"])
            if (f["type"] == "reference" and not f["path"].endswith(".*") and isinstance(got, str)
                    and got.split("/", 1)[0] in self.hub.concrete(f["target"]) and not (kept or {}).get(f["path"])):
                seed = schema.put({"resourceType": rtype, **(seed or {})}, f["path"], {"reference": got})
        err = f'<div class="flash error" role="alert"><p>{e(str(error))}</p></div>' if error else ""
        crumbs = [("/records", "Records"), (f"/records/{rtype}", rt.plural), (None, "Create")]
        skip, own, cancel, back = (), "", f"/records/{e(rtype)}", '<a class="btn subtle back" href="/start" data-modal>← Back</a>'
        if rt.part_of:  # a part is added to its owner: which one, first
            of = self._owner_field(rt)
            typed = q.get(of["name"]) or q.get(of["path"]) or (kept or {}).get("_owner") or ""
            owner, problem = None, None
            if typed:
                try:
                    owner = self._resolve_ref(of, typed)
                    self.hub.read_record(me, owner)
                except (Invalid, Denied, HubError) as exc:
                    owner, problem = None, exc
            if owner is None:
                return self._owner_pick(req, rt, of, problem)
            o = self.hub.records.read(*owner.split("/", 1))
            seed = schema.put({"resourceType": rtype, **(seed or {})}, of["path"], {"reference": owner})
            skip = (of["name"],)
            href = self.record_href(owner)
            own = (f'<input type="hidden" name="{e(of["path"])}" value="{e(owner)}">'
                   f'<p class="part-of">Part of <a href="{e(href)}">{e(self.hub.short_title(o))}</a></p>')
            cancel, back = e(href), ""
            crumbs = [(href, self.hub.short_title(o)), (f"{href}#part-{rtype}", rt.plural), (None, "Add")]
        start = f' It starts as <b>{e(lc["initial"])}</b>.' if lc and lc.get("initial") else ""
        inputs = self._inputs(rt, seed, me, "create", skip=skip)
        verb = "Add" if rt.part_of else "Create"
        body = (f'<div data-modal-body>{err}<form method="post" action="/records/{e(rtype)}" class="card stack" id="new">'
                f'{own}<p class="muted">{e(rt.summary)}{start}</p>{self._req_note(inputs)}{inputs}'
                f'<div class="filters"><button class="primary">{verb}</button>'
                f'<a class="btn secondary" href="{cancel}" data-modal-close>Cancel</a>{back}</div></form></div>')
        return self.layout(req, f"{verb} {a_an(self._lower(rt.label))}", body, crumbs=crumbs)

    def _owner_pick(self, req, rt, of, problem=None) -> str:
        """Adding a part with no owner named: which one, found as a reference picker finds it, then the form."""
        me, ol = req["me"], self._owner_label(rt)
        ctl = self._input({**of, "label": f"Which {ol}?", "help": "", "required": True}, None, me)
        err = f'<div class="flash error" role="alert"><p>{e(str(problem))}</p></div>' if problem else ""
        body = (f'<div data-modal-body>{err}<form method="get" action="/records/{e(rt.id)}/new" class="card stack" '
                f'id="owner-pick"><p class="muted">Each {e(self._lower(rt.label))} is part of {e(a_an(ol))}. '
                f'Choose the {e(ol)} first; the form opens next.</p>{ctl}<div class="filters">'
                f'<button class="primary">Next</button><a class="btn secondary" href="/records/{e(rt.id)}" '
                f'data-modal-close>Cancel</a><a class="btn subtle back" href="/start" data-modal>← Back</a></div>'
                f'</form></div>')
        return self.layout(req, f"Add {a_an(self._lower(rt.label))}", body,
                           crumbs=[("/records", "Records"), (f"/records/{rt.id}", rt.plural), (None, "Add")])

    def _create_pick(self, me) -> str:
        """What the member may add, the practice's shortcuts first; each opens its create screen in the dialog."""
        hub = self.hub
        short = self.shortcuts(me)
        rest = [t for t, rt in sorted(hub.record_types().items(), key=lambda kv: (kv[1].order, kv[0]))
                if t not in short and t not in hub.member_types() and not rt.abstract and t != self.subj["type"]
                and not rt.part_of and self._may_write(me, t)]

        def li(t):
            rt = hub.record_type(t)
            return (f'<li><a href="/records/{e(t)}/new" data-modal><b>{e(rt.label)}</b>'
                    f'<span class="muted">{e(rt.summary)}</span></a></li>')
        short = [t for t in short if self._may_write(me, t) and not hub.record_type(t).part_of]
        return ((f'<ul class="pick">{"".join(li(t) for t in short)}</ul>' if short else "")
                + (f'<details{" open" if not short else ""}><summary>Everything else</summary><ul class="pick">'
                   f'{"".join(li(t) for t in rest)}</ul></details>' if rest else ""))

    def page_create(self, req):
        """Create: the same choice as Start work (a record type, then its create screen in the same dialog)."""
        return self.page_start(req)

    def _may_write(self, me, rtype) -> bool:
        try:
            return bool(self.hub.may_write(me, rtype))
        except (Denied, Invalid):
            return False

    def shortcuts(self, me) -> list[str]:
        """The record types the practice works in most, as the pack and the modules installed name them
        (``shortcuts`` in pack.toml or module.toml): the Practice section's links. Only those the member may read."""
        return [t for t in self._shortcut_entries() if not t.startswith("start:")
                and self.hub.record_type(t) and not self.hub.record_type(t).part_of and self._may_read(me, t)]

    def quick_starts(self, me) -> list:
        """Workflows the pack or a module puts on the top bar (``shortcuts = ["start:<workflow>"]``), that this
        member may start: each a button that opens its start form."""
        out = []
        for t in self._shortcut_entries():
            if t.startswith("start:"):
                try:
                    wf = self.hub.workflow(t[6:])[0]
                except HubError:
                    continue
                if self.hub.can(me, "starter", f"workflow:{wf.id}"):
                    out.append(wf)
        return out

    def _shortcut_entries(self) -> list[str]:
        out: list[str] = []
        local = (self.hub.db.get_setting("practice_local", {}) or {}).get("shortcuts")
        for p in (self.hub.pack.all_parts() if self.hub.pack else []):
            # the practice's own shortcuts as its policy owners last set them, else as its pack says; modules add theirs
            own = local if (local is not None and p is self.hub.pack) else p.practice.get("shortcuts", [])
            for t in own or []:
                if isinstance(t, str) and t not in out:
                    out.append(t)
        return out

    def _typed(self, rt, form: dict, *, op: str | None = None, before: dict | None = None,
               only: list[str] | None = None) -> dict:
        """What a refused form had typed, in the record's shape (names, phones, addresses, lists), to fill the form
        again: each field read as the save reads it, those that do not read as their type left out."""
        got = self._from_form(rt, form, partial=True, before=before, op=op, only=only, lenient=True)
        return {**(before or {"resourceType": rt.id}), **got}

    def post_record_create(self, req, rtype):
        me = req["me"]
        rt = self.hub.record_type(rtype)
        if not rt:
            raise Invalid(f"there is no record type {rtype}")
        f = self.form(req)
        of = self._owner_field(rt)
        try:
            data = self._from_form(rt, f, partial=False, op="create")
            if of and of["path"] not in data and f.get(of["path"]):  # the owner, set before the form (not on its screen)
                data[of["path"]] = self._coerce(of, f[of["path"]])
            rec = self.hub.create_record(me, rtype, data)
        except Invalid as exc:
            from .server import Rendered
            kept = self._typed(rt, f, op="create")
            if isinstance(f.get("_parent"), str) and f["_parent"]:
                kept["_parent"] = f["_parent"]
            if of and isinstance(f.get(of["path"]), str):
                kept["_owner"] = f[of["path"]]
            raise Rendered(409 if isinstance(exc, Conflict) else 422,
                           self.page_record_new({**req, "query": {}}, rtype, kept=kept, error=exc)) from None
        owner = self.hub.owner_of(rec)
        if owner:  # added to its owner: back to the owner, at the section it was added to
            raise self._redirect(f"{self.record_href(owner)}#part-{rtype}")
        raise self._redirect(f"/records/{rtype}/{rec['id']}")

    UNDOING = ("cancelled", "obsolete", "discontinued", "entered-in-error", "withdrawn", "revoked", "retired",
               "voided", "stopped")
    SETTLED = ("released", "published", "stocked", "posted", "approved", "active", "final", "completed", "finished",
               "closed", "done", "effective", "resolved", "signed")
    UNDONE = ("obsolete", "cancelled", "rejected", "entered-in-error", "discontinued", "withdrawn", "revoked", "voided",
              "retired", "stopped", "failed")
    UNDERWAY = ("in-progress", "investigating", "verification", "reviewing", "pending", "draft-review")

    def _undoing(self, t) -> bool:
        """A move that undoes or ends a record (cancel, make obsolete, discontinue, entered in error)."""
        words = t["label"].lower()
        return t["to"] in self.UNDOING or words.startswith(("cancel", "discontinue", "make obsolete", "withdraw",
                                                            "revoke", "retire", "void", "mark entered in error"))

    def _state_loz(self, st, rt=None) -> str:
        """A status lozenge, in words (the lifecycle's label for it). Its tone comes from common, generic state words;
        a state it does not know is grey."""
        if st is None:
            return ""
        s = str(st).lower()
        tone = ("success" if s in self.SETTLED else
                "removed" if s in self.UNDONE else
                "inprogress" if "review" in s or s in self.UNDERWAY else
                "default")
        words = rt.state_label(st) if rt is not None else schema.state_words(st)
        return f'<span class="loz {tone}">{e(words)}</span>'

    @staticmethod
    def _state_words(rt, st) -> str:
        """A lifecycle state in words, as a column or a Move to… choice says it: the lifecycle's label for it where
        the type gives one, else its code with dashes and underscores as spaces, capitalised (in-progress: In progress)."""
        if st is None:
            return ""
        if rt is not None and hasattr(rt, "state_label"):
            return rt.state_label(st)
        return schema.state_words(st)

    # ------------------------------------------------------------------------------------------ parts and links
    def _status_of(self, rt, r) -> str:
        """A record's state in a list: its lifecycle state, else active or inactive when its type says which."""
        if rt.lifecycle:
            return self._state_loz(schema.first(r, rt.lifecycle["path"]), rt)
        if rt.active:
            return self._state_loz("active" if schema.is_active(rt, r) else "inactive")
        return ""

    @staticmethod
    def _flag_of(rt, r) -> str:
        v = schema.first(r, rt.flag) if rt.flag else None
        if isinstance(v, dict):
            v = v.get("text") or (v.get("coding") or [{}])[0].get("display")
        if v in (None, ""):
            return ""
        s = str(v).lower()
        tone = ("critical" if s in ("critical", "high", "hh", "ll", "urgent", "overdue") else
                "normal" if s in ("normal", "low", "n", "routine") else "abnormal")
        return f'<span class="flag flag-{tone}">{e(str(v))}</span>'

    @staticmethod
    def _day(v) -> str:
        v = str(v or "")
        return v[:16].replace("T", " ") if re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d.*", v) else v[:10]

    def _owner_field(self, rt) -> dict | None:
        return rt.field(rt.part_of["field"]) if rt.part_of else None

    def _owner_label(self, rt) -> str:
        """What owns a part, in words: "patient", "visit", "patient or group"."""
        names = []
        for t in self.hub.concrete(rt.part_of["target"]):
            ot = self.hub.record_type(t)
            names.append(self._lower(ot.label if ot else t))
        return " or ".join(names)

    def _part_rows(self, rt, recs, *, peek: bool = True) -> str:
        """Records in a compact table: title (it opens it; a peek reads it here), date, status, flag."""
        lower = self._lower(rt.label)
        def row(r):
            ref = f"{rt.id}/{r['id']}"
            if (visibility.withheld(r) or {}).get("whole"):  # a stub: what it is, never what it says
                return (f'<tr class="withheld"><td class="t"><a href="{e(self.record_href(ref))}">{e(self.hub.title(r))}'
                        f'</a></td><td class="muted nowrap"></td><td colspan=2><span class="loz moved">Withheld'
                        f'</span></td></tr>')
            look = (f'<details class="peek"><summary><span class="visually-hidden">Read this {e(lower)} here'
                    f'</span></summary>{self._plain_html(r)}</details>' if peek else "")
            return (f'<tr><td class="t"><a href="{e(self.record_href(ref))}">{e(self.hub.title(r, seen=True))}</a>{look}</td>'
                    f'<td class="muted nowrap">{e(self._day(schema.record_date(rt, r)))}</td>'
                    f'<td>{self._status_of(rt, r)}</td><td>{self._flag_of(rt, r)}</td></tr>')
        return ('<div class="tablewrap"><table class="parts"><tr><th>' + e(rt.label) + '</th><th>Date</th>'
                '<th>Status</th><th><span class="visually-hidden">Flag</span></th></tr>'
                + "".join(row(r) for r in recs) + "</table></div>")

    def owner_sections(self, me, ref: str) -> tuple[str, list[tuple[str, str, int]], int]:
        """A record's parts, one section per part type (by ``order``): heading and count, newest first, and an Add
        button that opens the create dialog with the owner set. Then the records that link to it, grouped by type,
        each with its own New. Returns (html, rail entries (anchor, words, count), records hidden from this member)."""
        hub = self.hub
        rtype = ref.split("/", 1)[0]
        q = urllib.parse.quote(ref)
        parts, hidden = hub.parts(me, ref)
        out, rail = [], []
        for t, recs in parts.items():
            rt = hub.record_type(t)
            f = self._owner_field(rt)
            add = (f'<a class="btn secondary" href="/records/{e(t)}/new?{e(f["name"])}={e(q)}" data-modal>'
                   f'Add {e(self._lower(rt.label))}</a>' if self._may_write(me, t) else "")
            none = "" if recs else f'<span class="muted none">None recorded</span>'
            out.append(f'<section class="chart-section{"" if recs else " empty-part"}" id="part-{e(t)}" '
                       f'aria-labelledby="part-{e(t)}-h"><header>'
                       f'<h2 id="part-{e(t)}-h">{e(rt.plural)} <span class="count-badge">{len(recs)}</span></h2>'
                       f'{none}{add}</header>{self._part_rows(rt, recs) if recs else ""}</section>')
            rail.append((f"part-{t}", rt.plural, len(recs)))
        groups: dict[str, dict] = {}
        for x in hub.linked(me, ref):
            t, _, rid = x["ref"].partition("/")
            rt = hub.record_type(t)
            if (not rt or rt.part_of or t in hub.member_types() or (t == rtype and rt.parent)
                    or rt.abstract):
                continue  # parts are under their own owner; a tree's children are under it already
            g = groups.setdefault(t, {"rels": set(), "recs": {}})
            g["rels"].add(x["rel"])
            if rid not in g["recs"]:
                r = hub.redact(me, hub.records.read(t, rid))
                if r:
                    g["recs"][rid] = r
                    if not (visibility.withheld(r) or {}).get("whole"):
                        hub.note_view(me, x["ref"], how=f"linked to {ref}")
        links = []
        for t in sorted(groups, key=lambda t: (hub.record_type(t).order, t)):
            rt, g = hub.record_type(t), groups[t]
            recs = sorted(g["recs"].values(), key=lambda r: schema.record_date(rt, r), reverse=True)
            # the field a new one names this record in: a single reference, of a rel seen, that may point here
            fld = next((f for f in rt.fields if f["type"] == "reference" and not f["path"].endswith(".*")
                        and f["rel"] in g["rels"] and rtype in hub.concrete(f["target"])), None)
            new = (f'<a class="btn subtle" href="/records/{e(t)}/new?{e(fld["name"])}={e(q)}" data-modal>'
                   f'New {e(self._lower(rt.label))}</a>' if fld and self._may_write(me, t) else "")
            links.append(f'<section class="chart-section" id="link-{e(t)}" aria-labelledby="link-{e(t)}-h"><header>'
                         f'<h3 id="link-{e(t)}-h">{e(rt.plural)} <span class="count-badge">{len(recs)}</span></h3>'
                         f'{new}</header>{self._part_rows(rt, recs[:50])}'
                         + (f'<p class="muted body">The 50 most recent of {len(recs)}. <a href="/records/{e(t)}?linked='
                            f'{e(urllib.parse.quote(ref, safe=""))}">Show all {len(recs)}</a></p>' if len(recs) > 50 else "")
                         + '</section>')
            rail.append((f"link-{t}", rt.plural, len(recs)))
        if links:
            out.append('<h2 class="links-head" id="links">Linked here</h2>' + "".join(links))
        hidden += sum(1 for x in hub.db.links.find({"to_ref": ref}, fields=["from_ref"])
                      if hub.record_type(x["from_ref"].split("/")[0])
                      and not self._may_read(me, x["from_ref"].split("/")[0]))
        return "".join(out), rail, hidden

    def owner_rail(self, title: str, rail: list[tuple[str, str, int]], extra: list | tuple = (), label: str = "record") -> str:
        """The owner page's second column: its part sections, then its link groups, with counts (anchors)."""
        if not rail:
            return ""
        a = lambda anchor, words, n: (f'<li><a href="#{e(anchor)}">{e(words)}'
                                      + (f'<span class="count">{n}</span>' if n is not None else "") + '</a></li>')
        own = [x for x in rail if x[0].startswith("part-")]
        linked = [x for x in rail if x[0].startswith("link-")]
        return (f'<nav class="subnav owner-rail" aria-label="Sections of {e(title)}">'
                + (f'<div class="sec">On this {e(self._lower(label))}</div><ul>{"".join(a(*x) for x in own)}</ul>' if own else "")
                + (f'<div class="sec">Linked here</div><ul>{"".join(a(*x) for x in linked)}</ul>' if linked else "")
                + (f'<div class="sec">Work</div><ul>{"".join(a(*x) for x in extra)}</ul>' if extra else "")
                + "</nav>")

    def alerts(self, me, ref: str) -> str:
        """Active records of ``alert`` types that are parts of (or link to) this record: one banner."""
        hub = self.hub
        rtype = ref.split("/", 1)[0]
        found = []
        for rt in hub.part_types(rtype):
            if not rt.alert or not self._may_read(me, rt.id):
                continue
            f = self._owner_field(rt)
            for x in hub.db.links.find({"to_ref": ref, "rel": f["rel"], "from_ref": {"$regex": f"^{re.escape(rt.id)}/"}}):
                r = hub.redact(me, hub.records.read(*x["from_ref"].split("/", 1)))
                if r and schema.is_active(rt, r):
                    found.append((rt, r))
        if not found:
            return ""
        by: dict = {}
        for rt, r in found:
            by.setdefault(rt.id, (rt, []))[1].append(r)
        return '<div class="alert-banner" role="note">' + "; ".join(
            f'{e(rt.plural)}: ' + ", ".join(f'<a href="{e(self.record_href(f"{rt.id}/{r["id"]}"))}">{e(self.hub.title(r))}</a>'
                                           for r in recs) for rt, recs in by.values()) + "</div>"

    def subject_banner(self, me, ref: str, *, heading: bool = False) -> str:
        """The subject's banner: their name (a link to their page), headline facts, and alerts."""
        hub = self.hub
        p = hub.redact(me, hub.records.read(*ref.split("/", 1)))
        if not p:
            return ""
        facts = "".join(f'<div><dt>{e(self.human(k))}</dt><dd>{self._fact_html(v)}</dd></div>'
                        for k, v in hub.facts(p).items() if v not in (None, ""))
        name = (f'<h1 class="name">{e(hub.short_title(p))}</h1>' if heading else
                f'<p class="name"><a href="{e(self.record_href(ref))}">{e(hub.short_title(p))}</a></p>')
        return (f'<div class="subject-banner">{name}<dl class="facts">{facts}</dl></div>' + self.alerts(me, ref)
                + self.withheld_banner(me, ref))

    def record_banner(self, me, rec: dict) -> str:
        """An owner that is not a subject (a visit), as the banner over its parts: its name (a link), status and
        date, and the subject it links to, with that subject's alerts."""
        hub = self.hub
        rt = hub.record_type(rec["resourceType"])
        ref = f"{rt.id}/{rec['id']}"
        facts = [(rt.label, self._status_of(rt, rec))]
        when = schema.record_date(rt, rec) if rt.date else ""
        if when:
            facts.append(("Date", e(self._day(when))))
        subs = self._subjects_of(rt, rec)
        facts += [(lbl, f'<a href="{e(self.record_href(s))}">{e(hub.short_title(hub.records.read(*s.split("/", 1)) or {}) or s)}</a>')
                  for lbl, s in subs]
        dl = "".join(f'<div><dt>{e(k)}</dt><dd>{v}</dd></div>' for k, v in facts if v)
        return (f'<div class="subject-banner"><p class="name"><a href="{e(self.record_href(ref))}">'
                f'{e(hub.short_title(rec))}</a></p><dl class="facts">{dl}</dl></div>'
                + "".join(self.alerts(me, s) for _, s in subs[:1]))

    def _subjects_of(self, rt, rec) -> list[tuple[str, str]]:
        """The subjects a record links to by its own single reference fields: (field label, ref)."""
        st, out = self.subj["type"], []
        for f in rt.fields:
            if f["type"] != "reference" or f["path"].endswith(".*") or st not in self.hub.concrete(f["target"]):
                continue
            r = schema.ref_of(schema.first(rec, f["path"]))
            if r and r.startswith(st + "/") and r not in [x for _, x in out]:
                out.append((f["label"], r))
        return out

    def owner_crumbs(self, me, rt, rec) -> list | None:
        """A part's breadcrumb: its owner (and the owner's own owner, for a part of a part), then the section."""
        owner = self.hub.owner_of(rec)
        if not owner or not self._may_read(me, owner.split("/", 1)[0]):
            return None
        o = self.hub.redact(me, self.hub.records.read(*owner.split("/", 1)))
        if not o:
            return None
        ort = self.hub.record_type(owner.split("/", 1)[0])
        up = (self.owner_crumbs(me, ort, o) or []) if ort and ort.part_of else []
        up = up[:-1] if up else []
        href = self.record_href(owner)
        return [*up, (href, self.hub.short_title(o)), (f"{href}#part-{rt.id}", rt.plural), (None, self.hub.short_title(rec))]

    def owner_banner(self, me, owner: str) -> str:
        t = owner.split("/", 1)[0]
        if t == self.subj["type"]:
            return self.subject_banner(me, owner)
        o = self.hub.redact(me, self.hub.records.read(*owner.split("/", 1)))
        return self.record_banner(me, o) if o and self._may_read(me, t) else ""

    # ------------------------------------------------------------------------------------------ restrictions
    def withheld_banner(self, me, subject: str) -> str:
        """Above every page of a file a restriction limits: that something is withheld at the subject's request
        (and the emergency form, for a role that may override), that an emergency access is in force (and that
        it is reviewed), or that the reader is one the restriction still allows."""
        info = self.hub.withheld(me, subject)
        if not info:
            return ""
        word = self.subj["label"].lower() if subject.split("/")[0] == self.subj["type"] else "subject"
        over = info.get("override")
        if over:
            from .. import clock
            until = clock.shown(over["until"], self.hub.engine.zone())[11:16]  # practice time, as the hub shows times
            return (f'<div class="flash warning restriction" role="status" id="override-banner">'
                    f'<h2>Emergency access: you are seeing information this {e(word)} asked to have withheld</h2>'
                    f'<p>Until {e(until)}. Your reason: “{e(over["reason"])}”. This access is on the track, and '
                    f'the privacy officer reviews it. Use only what the emergency needs.</p></div>')
        if info["allowed"]:
            return (f'<div class="flash info restriction" role="status"><p>This {e(word)} limited who sees part of their '
                    f'file. Their directive still allows you; everyone else sees '
                    f'“{e(info["note"].lower())}”.</p></div>')
        return (f'<div class="flash warning restriction" role="status" id="withheld-banner"><h2>{e(info["note"])}</h2>'
                f'<p>Part of this file is not shown to you: the {e(word)} limited who sees it. What is withheld is '
                f'listed as <span class="loz moved">Withheld</span>, and nothing of it is given to an agent.'
                f'</p>{self.override_form(me, subject, info)}</div>')

    def override_form(self, me, ref: str, info: dict, *, open_: bool = False) -> str:
        """Break the glass: a role that may override opens what is withheld, giving a reason the privacy officer
        reviews. Anyone else is told who to ask."""
        if not info.get("may_override"):
            return "<p class='muted'>If you need it, ask the privacy officer.</p>"
        t, _, rid = ref.partition("/")
        mins = int(self.hub.restrictions.window.total_seconds() // 60)
        idp = f"override-{next(_IDS)}"  # the banner and the panel may both be on one page: each its own ids
        return (f'<details class="break-glass"{" open" if open_ else ""}><summary>Open it in an emergency…</summary>'
                f'<form method="post" action="/records/{e(t)}/{e(rid)}/override" class="stack break-glass-form">'
                f'<label for="{idp}-reason">Why you need it now <span class="req" aria-hidden="true">*</span>'
                f'</label><textarea id="{idp}-reason" name="reason" required minlength="8" rows="3" '
                f'aria-describedby="{idp}-help"></textarea>'
                f'<p class="muted" id="{idp}-help">You will see the whole file for {mins} minutes. Your name, the '
                f'time and this reason go on the track, and the privacy officer is asked to review it.</p>'
                f'<div class="filters"><button class="danger">Open the withheld information</button></div>'
                f'</form></details>')

    def withheld_panel(self, me, rec: dict, ref: str) -> str:
        """A record a restriction withholds from the member, in place of its fields."""
        gone = visibility.withheld(rec) or {}
        subject = gone.get("subject") or self.hub.restrictions.subject_of(rec) or ref
        info = self.hub.withheld(me, subject) or {"note": gone.get("note", "")}
        if gone.get("whole"):
            return (f'<section class="card withheld-panel" aria-labelledby="withheld-h"><h3 id="withheld-h">'
                    f'{e(gone["note"])}</h3><p>What this record says is not shown to you. Its existence is shown so '
                    f'the file is never mistaken for complete.</p>{self.override_form(me, subject, info, open_=True)}'
                    f'</section>')
        return (f'<div class="flash info" role="status"><p>{e(gone["note"])}: '
                f'{e(", ".join(gone.get("fields") or []))}.</p></div>')

    def post_override(self, req, rtype, rid):
        """Break the glass (``hub.break_glass``), then back to the record, now open, under the banner."""
        reason = str(self.form(req).get("reason") or "")
        got = self.hub.break_glass(req["me"], f"{rtype}/{rid}", reason)
        raise self._redirect(self.record_href(f"{rtype}/{rid}") if rtype != self.subj["type"]
                             else self.record_href(got["subject"]))

    # ------------------------------------------------------------------------------------------ one record
    def page_record(self, req, rtype, rid, kept: dict | None = None, error: Exception | None = None):
        me, hub = req["me"], self.hub
        if rtype in hub.member_types():
            from .server import Redirect
            raise Redirect(f"/team/{urllib.parse.quote(rid)}")
        ref = f"{rtype}/{rid}"
        rt = hub.record_type(rtype)
        if not rt:
            raise Invalid(f"there is no record type {rtype}")
        rec = hub.read_record(me, ref)
        lc = rt.lifecycle
        st = schema.first(rec, lc["path"]) if lc else None
        may_write = hub.may_write(me, rtype)
        tab = req["query"].get("tab", "details")

        # main column: the fields, then the tabs
        unseen = visibility.hidden_fields(hub, me, rt)  # a field of a class they may not read: no row at all
        prose = [f for f in rt.fields if f["type"] == "markdown" and f.get("show", True) is not False
                 and f not in unseen]
        empty: list = []  # read, a field with nothing in it is not a row: they are named once, below

        def row(f):
            if f in prose or f in unseen:
                return ""
            if not self._has_value(f, rec) and not req["query"].get("edit"):
                empty.append(f["label"])
                return ""
            return f"<tr><th>{e(f['label'])}</th><td>{self._value_html(f, rec)}</td></tr>"
        view = rt.screen("view")
        if view is not None:  # the type's view screen: its tabs as headed groups, its fields in its order
            fields = "".join((f"<tr><th colspan=2 scope=colgroup class='group'>{e(t['label'])}</th></tr>"
                              if t["label"] and len(view) > 1 else "") + "".join(row(rt.field(n)) for n in t["fields"])
                             for t in view)
        else:
            fields = "".join(row(f) for f in rt.fields if f.get("show", True) is not False and not f.get("advanced")
                             and not (lc and f["path"] == lc["path"])
                             and not (f["type"] == "list" and any(g["path"] == f["path"] + ".*" for g in rt.fields)))
        # all values: a raw field another field already shows in words (its parts) is left out, as is an empty one
        # (a list is not covered: a field on its first entry leaves the others unshown)
        covered = lambda f: (not any(isinstance(v, list) for v in schema.get(rec, f["path"]))
                             and any(g is not f and g["path"].startswith(f["path"] + ".") for g in rt.fields))
        adv_rows = "".join(f"<tr><th>{e(re.sub(r'\s*\(as JSON\)$', '', f['label']))}</th>"
                           f"<td>{self._value_html(f, rec)}</td></tr>"
                           for f in rt.fields if f.get("advanced") and f.get("show", True) is not False
                           and f not in unseen and not covered(f) and self._has_value(f, rec))
        if rtype == self.subj["type"] and hub.is_policy_owner(me):
            fields += (f"<tr><th>Privacy</th><td><a href='/access?subject={e(urllib.parse.quote(ref))}'>Who opened or "
                       f"changed this file</a></td></tr>")
        if empty:
            fields += (f"<tr><th>Not filled in</th><td class='muted'>{e(', '.join(dict.fromkeys(empty)))}</td></tr>")
        gone = visibility.withheld(rec)
        if gone and gone.get("whole"):
            fields, prose, adv_rows = "", [], ""
        main = [(self.withheld_panel(me, rec, ref) if gone else "") + f"<table class='kv'>{fields}</table>"
                + "".join((f"<h2 class='prose-label'>{e(f['label'])}</h2>" if len(prose) > 1 else "")
                          + self._value_html(f, rec) for f in prose)
                + (f"<details class='advanced'><summary>All values</summary><table class='kv'>{adv_rows}</table>"
                   f"</details>" if adv_rows else "")]
        tabs = [("details", "Details"), ("signatures", "Signatures"), ("links", "Linked"), ("history", "History"),
                ("work", "Work")]
        main.insert(0, '<nav class="tabs" aria-label="Sections">' + "".join(
            f'<a href="?tab={t}"{" aria-current=page" if t == tab else ""}>{e(lbl)}</a>' for t, lbl in tabs) + "</nav>")
        main.insert(1, '<h2 class="visually-hidden">' + e(dict(tabs)[tab] if tab in dict(tabs) else "Details") + "</h2>")
        locked = bool(lc and st in lc.get("locked", []))
        url = f"/records/{e(rtype)}/{e(rid)}"
        ver = e(str((rec.get("meta") or {}).get("versionId", "")))
        editable = may_write and not locked and bool(self._editable(rt)) and not (gone and gone.get("whole"))
        # one place for a record: it is read, and an Edit button turns it into its form where it stood (?edit=1)
        editing = tab == "details" and editable and bool(req["query"].get("edit"))
        typed = (kept or {}).get("_typed") if editing else None  # a refused edit: what was typed, not what is saved
        shown = typed or rec
        refused = (f'<div class="flash error" role="alert"><p>{e(str(error))}</p></div>'
                   if editing and error else "")
        is_page = rt.layout == "page"
        edit_btn = (f'<a class="btn secondary" href="{url}/export" data-modal>Export</a>' if not editing else "") + (
            f'<a class="btn primary" href="{url}?edit=1#edit" accesskey="e" aria-keyshortcuts="Alt+E">Edit</a>'
            if editable and not editing else "")
        if editable and not editing and rt.is_a("Booking") and schema.is_active(rt, rec):  # another time, member
            edit_btn = (f'<a class="btn secondary" href="{url}/reschedule" data-modal>Move</a>' + edit_btn)
        rail: list = []
        if tab == "details":
            used = hub.in_use_version(me, ref) if not is_page else None  # a page says so in its banner
            if used:
                d = used["data"]
                body = "".join(f"<tr><th>{e(f['label'])}</th><td>{self._value_html(f, d)}</td></tr>"
                               for f in rt.fields if f.get("show", True) is not False
                               and not (lc and f["path"] == lc["path"]) and schema.first(d, schema.locate(d, f["path"], f.get("with"))) not in (None, ""))
                main.append(f'<section class="card in-use"><h3>In use: the {e(lc["in_use"])} version</h3>'
                            f'<p class="muted">This {e(rt.label.lower())} is being revised ({e(str(st))}). Until the '
                            f'revision is {e(lc["in_use"])}, work from this version (saved {e(str(used["at"])[:16])}).'
                            f'</p><table class="kv">{body}</table></section>')
            if locked:
                main.append(f'<div class="flash info"><p>{e(a_an(rt.label)[0].upper() + a_an(rt.label)[1:])} that is <b>{e(rt.state_label(st))}</b> is not edited. '
                            + e("; ".join(t["label"] for t in lc["transition"] if st in t["from"] and t["by"] != "workflow")
                                or "A workflow moves it on") + ' to change it.</p></div>')
            elif editing and not is_page:
                inputs = self._inputs(rt, shown, me, "edit", skip=(rt.part_of["field"],) if rt.part_of else ())
                main[2] = (f'{refused}<form method="post" action="{url}/edit" class="card stack editing" id="edit">'
                           f'<input type="hidden" name="_version" value="{ver}">{self._req_note(inputs)}{inputs}'
                           f'<div class="filters"><button class="primary">Save</button>'
                           f'<a class="btn secondary" href="{url}">Cancel</a></div></form>')
            elif not may_write and not is_page:
                main.append("<p class='muted'>You may read this record but not change it.</p>")
            files = [f for f in rt.fields if f["type"] == "attachment" and SIMPLE.fullmatch(f["path"])
                     and f.get("show", True) is not False]
            if may_write and not locked and files and not editing:
                pick = ("".join(f'<option value="{e(f["name"])}">{e(f["label"])}</option>' for f in files))
                main.append(f'<form method="post" action="/records/{e(rtype)}/{e(rid)}/attach" '
                            f'enctype="multipart/form-data" class="card stack">'
                            f'<input type="hidden" name="_version" value="{ver}">'
                            + (f'<label>Attach as <select name="field">{pick}</select></label>' if len(files) > 1 else
                               f'<input type="hidden" name="field" value="{e(files[0]["name"])}">')
                            + f'<label>File (up to {self.hub.MAX_FILE // (1024 * 1024)} MB) '
                              f'<input type="file" name="file" required></label>'
                              f'<button>Attach</button></form>')
                if hasattr(self, "office_new_html"):  # a blank office document, when Collabora is set up
                    main.append(self.office_new_html(rt, rec, files))
            if hasattr(self, "office_comments_html") and not editing:  # threads on each document, Collabora or not
                main.append(self.office_comments_html(me, rt, rec))
            if rt.parent and not editing:
                kids = hub.children(me, ref)
                add = (f' <a class="btn secondary" href="/records/{e(rtype)}/new?parent={e(urllib.parse.quote(ref))}" data-modal>'
                       f'Add {e(a_an(self._lower(rt.label)))} under this one</a>' if may_write else "")
                main.append(f"<section class='children'><h2>Under this {e(self._lower(rt.label))}{add}</h2>" + (
                    "<ul class='plain'>" + "".join(
                        f'<li><a href="{e(self.record_href(f"{k["resourceType"]}/{k["id"]}"))}">{e(hub.title(k))}</a>'
                        f'{self._kind_badge(rtype, k)}</li>'
                        for k in kids) + "</ul>" if kids else "<p class='muted'>Nothing yet.</p>") + "</section>")
            if not editing:  # what is part of it, then what links to it
                sections, rail, hidden = self.owner_sections(me, ref) if not is_page else ("", [], 0)
                main.append(sections)
                if hidden:
                    main.append('<p class="muted">Some records are kept from your role and are not shown.</p>')
        elif tab == "signatures":
            main.append(self._signatures_panel(me, rt, rec, ref, st, None if typed else kept, error))
        elif tab == "links":
            main.append(self._links_panel(me, rt, rec, ref))
        elif tab == "history":
            main.append(self._history_panel(me, rt, ref))
        else:
            runs = hub.engine.runs(mentions=ref)
            main.append(("<div class='tablewrap'><table><tr><th>Work</th><th>Status</th><th>Started</th></tr>" + "".join(
                f'<tr><td><a href="/runs/{e(r["id"])}">{e(self.hub.run_title(req["me"], r))}</a></td><td>{e(r["status"])}</td>'
                f'<td class="muted">{self._at(r["created_at"])}</td></tr>' for r in runs)
                + "</table></div>") if runs else "<p class='muted'>No work about this record.</p>")
            starts = self.start_buttons(me, rtype, ref)
            if starts:
                main.append(f"<div class='starts' role='group' aria-label='Start work'>{starts}</div>")

        def move(t):
            # only the forward move is loud; the others (a no-show, a pause) are secondary, undoing ones in red
            kind = "danger-subtle" if self._undoing(t) else "primary" if t is ahead else "secondary"
            if t["fields"] or t["comment"]:  # a transition screen asks for what the move needs first
                return (f'<a class="btn {kind}" href="{url}/move?to={e(urllib.parse.quote(t["to"]))}" data-modal>'
                        f'{e(t["label"])}…</a>')
            confirm = f' data-confirm="Move it to {e(rt.state_label(t["to"]).lower())}?"' if kind != "primary" else ""
            return (f'<form method="post" action="{url}/move" class="inline">'
                    f'<input type="hidden" name="to" value="{e(t["to"])}"><button class="{kind}"{confirm}>'
                    f'{e(t["label"])}</button></form>')

        moves = [t for t in lc.get("transition", []) if (st in t["from"] or "*" in t["from"]) and t["to"] != st] \
            if lc else []
        mine = [t for t in moves if t["by"] != "workflow"] if may_write else []
        auto = [t for t in moves if t["by"] == "workflow"]
        ahead = self.forward_move(lc, st, mine) if mine else None
        moves_html = "".join(move(t) for t in mine) if not editing else ""
        sigs = hub.signatures(me, ref)
        cur = [s for s in sigs if s["current"]]
        title = hub.short_title(rec, seen=True)
        owner = hub.owner_of(rec)
        part_crumbs = self.owner_crumbs(me, rt, rec)
        banner = ""
        if owner:  # a part sits under its owner: the owner's banner (a patient's, with their alerts) above it
            banner = self.owner_banner(me, owner)
        elif rtype != self.subj["type"]:  # a record about a subject (a visit): the subject, as its first fact
            subs = self._subjects_of(rt, rec)
            if subs and self._may_read(me, self.subj["type"]):
                banner = self.subject_banner(me, subs[0][1])
        nav = self.navigator(me, rtype, ref)
        if not nav and tab == "details" and not editing and len(rail) >= 3:  # a few sections need no column
            nav = self.owner_rail(title, rail, label=rt.label)
        if is_page:
            lp = self.live_parts(req, rt, rec, ref, st, tab, may_write, locked) if not editing else {}
            if lc and not editing:  # its lifecycle, drawn, beside its moves
                moves_html = f'<a class="btn subtle" href="{url}/workflow" data-modal>View workflow</a>' + moves_html
            work = self.work_panel(me, ref) if not editing and tab == "details" else ""
            body = refused + self._page_body(req, rt, shown if editing else lp.get("rec", rec), main, prose, st, tab,
                                             editing, url, ver, moves_html, lp.get("edit") or edit_btn, auto, sigs,
                                             cur, may_write, live=lp, work=work)
            above = [(self.record_href(f"{a['resourceType']}/{a['id']}"), hub.short_title(a)) for a in hub.ancestors(me, ref)]
            return self.layout(req, hub.short_title(rec), body, heading=not editing,
                               crumbs=part_crumbs or [("/records", "Records"), (f"/records/{rtype}", rt.plural), *above,
                                                      (None, hub.short_title(rec))], subnav=nav, above=banner)

        # side column: state, moves a person may make, signatures at a glance
        # side column: the lifecycle (state, moves a person may make, the diagram), the open work about it, then
        # signatures at a glance
        side = [self.work_panel(me, ref, rt=rt, st=st, moves_html=moves_html, auto=auto, url=url)]
        side.append(f"<h2>Signatures</h2><p>{len(cur)} on this version"
                    + (f", {len(sigs) - len(cur)} on earlier ones" if len(sigs) > len(cur) else "") + "</p>")
        side.append(f'<h2>Record</h2><p class="muted">{e(rt.label)}, kept with {e(self._class_name(rt.data_class))}<br>version '
                    f'{e(str(rec.get("meta", {}).get("versionId", "")))} · {self._at(rec.get("meta", {}).get("lastUpdated", ""))}</p>')
        from .. import markup
        heads = [h for f in prose for v in schema.get(rec, f["path"]) if isinstance(v, str)
                 for h in markup.headings(v)]
        if len(heads) >= 3 and tab == "details":
            side.insert(0, "<h2>On this page</h2><ul class='plain toc'>" + "".join(
                f'<li class="l{lvl}"><a href="#{e(anchor)}">{e(words)}</a></li>' for lvl, words, anchor in heads)
                + "</ul>")
        body = (f'<div class="issue"><div>{"".join(main)}</div><aside class="issue-side">{"".join(side)}'
                f'</aside></div>')
        above = [(self.record_href(f"{rtype}/{a['id']}"), hub.short_title(a)) for a in hub.ancestors(me, ref)]
        return self.layout(req, title, body, crumbs=part_crumbs or [("/records", "Records"), (f"/records/{rtype}", rt.plural),
                                                                    *above, (None, title)], subnav=nav,
                           actions=edit_btn, above=banner)

    def _page_body(self, req, rt, rec, main, prose, st, tab, editing, url, ver, moves_html, edit_btn, auto, sigs, cur,
                   may_write, live=None, work: str = "") -> str:
        """A type with ``layout = "page"``: its formatted text is the page. Above it, one bar holds what it is (status,
        its other fields, version, signatures) and what can be done (the sections, moves, Edit). Edit turns the page
        itself into its editor, where it stood, with Save and Cancel in the bar."""
        from .. import markup
        me = req["me"]
        lc = rt.lifecycle
        meta = [self._state_loz(st, rt)] if lc else []
        if editing:
            meta.insert(0, '<span class="lozenge editing-flag">Editing</span>')
        else:
            for f in rt.fields:
                if (f in prose or f.get("show", True) is False or f.get("advanced") or (lc and f["path"] == lc["path"])
                        or f["path"] == rt.parent or (f["type"] == "list"
                                                     and any(g["path"] == f["path"] + ".*" for g in rt.fields))):
                    continue
                if not [v for v in schema.get(rec, schema.locate(rec, f["path"], f.get("with"))) if v not in (None, "")]:
                    continue
                meta.append(f'<span class="meta-item"><span class="k">{e(f["label"])}</span> '
                            f'{self._value_html(f, rec)}</span>')
            m = rec.get("meta") or {}
            meta.append(f'<span class="meta-item">version {e(str(m.get("versionId", "")))} · '
                        f'{self._at(m.get("lastUpdated", ""))}</span>')
            meta.append(f'<a class="meta-item" href="?tab=signatures">{len(cur)} signature{"s" if len(cur) != 1 else ""}'
                        + (f' ({len(sigs) - len(cur)} on earlier versions)' if len(sigs) > len(cur) else "") + "</a>")
            if auto:
                meta.append(f'<span class="meta-item">a workflow moves it to {e(", ".join(rt.state_label(t["to"]) for t in auto))}</span>')
            if not may_write:
                meta.append('<span class="meta-item">read only</span>')
        if editing:
            controls = (f'<span class="grow"></span><a class="btn secondary" href="{url}">Cancel</a>'
                        f'<button class="primary">Save</button>')
        else:
            heads = [h for f in prose for v in schema.get(rec, f["path"]) if isinstance(v, str)
                     for h in markup.headings(v)] if tab == "details" else []
            toc = ('<details class="menu-pop"><summary class="btn secondary">On this page</summary>'
                   '<ul class="plain toc">' + "".join(f'<li class="l{lvl}"><a href="#{e(a)}">{e(w)}</a></li>'
                                                      for lvl, w, a in heads) + "</ul></details>") if len(heads) >= 3 else ""
            controls = f'{main[0]}<span class="grow"></span>{toc}{moves_html}{edit_btn}'
        bar = (f'<div class="page-bar"><div class="page-meta">{"".join(meta)}</div>'
               f'<div class="page-controls">{controls}</div></div>')
        if not editing:
            rest = main[1:]
            live = live or {}
            if tab == "details":  # its text is the page; the other fields are in the bar
                rest[1] = f'<article class="page-body"{live.get("article", "")}>' + "".join(
                    (f"<h2 class='prose-label'>{e(f['label'])}</h2>" if len(prose) > 1 else "")
                    + self._value_html(f, rec) for f in prose) + "</article>"
                if live.get("side"):  # its comments beside it
                    rest[1] = f'<div class="page-with-side">{rest[1]}{live["side"]}</div>'
            work = f'<div class="wp-inline">{work}</div>' if work else ""
            return bar + live.get("banner", "") + work + "".join(rest) + live.get("after", "")
        fields = self._editable(rt)
        title_f = next((f for f in fields if f["name"] == "title"), None)
        texts = [f for f in fields if f["type"] == "markdown"]
        others = [f for f in fields if f is not title_f and f not in texts]
        need = any(f.get("required") and not schema.first(rec, f["path"]) for f in others)
        return (f'<form method="post" action="{url}/edit" class="page-edit" id="edit">'
                f'<input type="hidden" name="_version" value="{ver}">{bar}{"".join(main[3:4])}'
                + (f'<div class="page-title-field">{self._input(title_f, rec, me)}</div>' if title_f else "")
                + (f'<details class="page-details"{" open" if need else ""}><summary>Page details</summary>'
                   f'<div class="page-details-grid">{"".join(self._input(f, rec, me) for f in others)}</div></details>'
                   if others else "")
                + f'<div class="page-text">{"".join(self._input(f, rec, me) for f in texts)}</div></form>')

    def navigator(self, me, rtype: str, ref: str | None = None) -> str:
        """A tree type's second navigation column: its spaces, then the open space's pages as a tree."""
        nav = self.hub.navigator(me, rtype, ref)
        if not nav:
            return ""
        hub, label = self.hub, nav["label"]
        rtype = nav["type"]
        link = lambda r: f"{r['resourceType']}/{r['id']}"

        def a(r, cls=""):
            here = ' aria-current="page"' if link(r) == ref else ""
            return (f'<a class="{cls}" href="{e(self.record_href(link(r)))}"{here}>{e(hub.title(r))}'
                    f'{self._kind_badge(rtype, r)}</a>')
        tops = "".join(f'<li>{a(r, "top")}</li>' for r in nav["tops"]) or '<li class="muted">None yet</li>'
        top = next((r for r in nav["tops"] if link(r) == nav["top"]), None)
        rows = nav["tree"]

        def branch(i: int, depth: int) -> tuple[str, int]:  # the rows from i at this depth, nested: (html, next i)
            out = []
            while i < len(rows) and rows[i][0] == depth:
                r = rows[i][1]
                i += 1
                if i < len(rows) and rows[i][0] > depth:
                    inner, i = branch(i, depth + 1)
                    opened = " open" if link(r) in nav["open"] else ""
                    out.append(f'<li><details{opened}><summary><span class="visually-hidden">Show or hide what is under it</span>'
                               f'</summary>{a(r)}<ul>{inner}</ul></details></li>')
                else:
                    out.append(f"<li>{a(r, 'leaf')}</li>")
            return "".join(out), i
        pages = ""
        if top is not None:
            inner, _ = branch(0, 0)
            add = (f'<a class="btn subtle add" href="/records/{e(rtype)}/new?parent={e(urllib.parse.quote(nav["top"]))}" data-modal>'
                   f'+ Add under {e(hub.title(top))}</a>' if hub.may_write(me, rtype) else "")
            add += "".join(f'<a class="btn subtle add" href="/records/{e(t)}/new?parent={e(urllib.parse.quote(nav["top"]))}"'
                           f' data-modal>+ New {e(hub.record_type(t).label)}</a>'
                           for t in hub.tree_types(rtype)[1:] if hub.may_write(me, t))
            pages = (f'<div class="sec">{e(hub.title(top))}</div>{add}'
                     + (f'<ul class="pagetree">{inner}</ul>' if inner else '<p class="muted">No pages under it yet.</p>'))
        return (f'<nav class="subnav" aria-label="{e(label)} and pages"><details class="spaces" '
                f'{"" if top is not None else "open"}><summary class="sec">{e(label)}</summary><ul class="tops">{tops}'
                f'</ul></details>{pages}</nav>')

    def _kind_badge(self, rtype: str, r: dict) -> str:
        """In a tree of several types, a record of another type than the tree's carries its type's name."""
        t = r.get("resourceType")
        if not t or t == rtype:
            return ""
        other = self.hub.record_type(t)
        return f' <span class="kind-badge">{e(other.label if other else t)}</span>' if other else ""

    def _signatures_panel(self, me, rt, rec, ref, st, kept: dict | None = None, error: Exception | None = None) -> str:
        kept = kept or {}
        hub = self.hub
        sigs = hub.signatures(me, ref)
        def row(s):
            decided = (f' <span class="loz">{e(s["outcome"])}</span> <a href="/runs/{e(s["run"])}">in the work</a>'
                       if s.get("outcome") else "")
            covers = ('<span class="loz success">current</span>' if s["current"]
                      else '<span class="loz moved">earlier version</span>')
            if not s["valid"]:
                covers += ' <span class="loz removed">does not verify</span>'
            return (f'<tr><td>{self.who(s["by"])}</td><td>“{e(s["meaning"] or "")}”{decided}</td>'
                    f'<td>v{e(str(s["version"]))}</td><td>{covers}</td>'
                    f'<td class="muted">{self._at(s["at"])}</td><td>{e(s["comment"])}</td>'
                    f'<td><a href="/track/{s["seq"]}">#{s["seq"]}</a></td></tr>')
        rows = "".join(row(s) for s in sigs)
        out = [("<div class='tablewrap'><table><tr><th>Who</th><th>Meaning</th><th>Version</th><th>Covers</th><th>When</th>"
                "<th>Comment</th><th>Track</th></tr>" + rows + "</table></div>") if sigs else
               "<p class='muted'>No one has signed this record.</p>"]
        if me["kind"] != "person":
            out.append("<p class='muted'>Only people sign records.</p>")
            return "".join(out)
        open_work = [r for r in hub.engine.runs(mentions=ref, status="running")]
        if open_work:
            out.append('<div class="flash warning"><p><b>There is open work on this record.</b> A review, decision or '
                       'approval is made in that work, not by signing here: a signature on the record does not '
                       'complete it.</p><ul class="plain">' + "".join(
                           f'<li><a href="/runs/{e(r["id"])}">{e(self.hub.run_title(req["me"], r))}</a></li>' for r in open_work) + "</ul></div>")
        if rt.signatures:
            open_ = [g for g in rt.signatures if (not g["states"] or st in g["states"])
                     and (not g["roles"] or set(g["roles"]) & set(me["roles"]))]
            if not open_:
                need = "; ".join(f'“{g["meaning"]}”' + (f' by {" or ".join(g["roles"])}' if g["roles"] else "")
                                 + (f' while {" or ".join(g["states"])}' if g["states"] else "") for g in rt.signatures)
                out.append(f"<p class='muted'>You cannot sign it now. It is signed: {e(need)}.</p>")
                return "".join(out)
            choice = "".join(
                f'<label class="radio"><input type="radio" class="opt-input" name="meaning" value="{e(g["meaning"])}" required'
                f'{" checked" if len(open_) == 1 or kept.get("meaning") == g["meaning"] else ""}> “{e(g["meaning"])}”'
                + (f' <small>moves it to {e(g["moves_to"])}</small>' if g.get("moves_to") else "")
                + "</label>" for g in open_)
            meaning = f'<fieldset class="choices options"><legend>What your signature means</legend>{choice}</fieldset>'
        else:
            meaning = ('<p class="muted">This kind of record has no set signatures. You may sign it with a meaning '
                       'of your own; it is recorded and bound to this version, and it starts no work.</p>'
                       '<label>What your signature means *<input name="meaning" required value="'
                       + e(str(kept.get("meaning", ""))) + '"></label>')
        err = (f'<div class="error" role="alert"><p>Not signed: {e(str(error))}</p><p>What you typed is kept below.</p>'
               f'</div>' if error is not None else "")
        out.append(f'<h2>Sign this version</h2>{err}<form method="post" action="/records/{e(rt.id)}/{e(rec["id"])}/sign" '
                   f'class="card stack"><input type="hidden" name="seen" value="{hub._record_digest(rec)}">{meaning}<label>Comment<textarea name="comment" rows="2">{e(str(kept.get("comment", "")))}</textarea></label>'
                   f'<p class="sign">Signing is your electronic signature. It covers this version of the record; if the '
                   f'record changes, the signature stays but no longer covers it.</p>{self._sign_prompt(me)}'
                   f'<button class="primary">Sign</button></form>')
        return "".join(out)

    def _links_panel(self, me, rt, rec, ref) -> str:
        out = []
        outgoing = schema.links(rt, rec) + self.hub.mention_links(rt, rec)
        if outgoing:
            out.append("<h2>This record points at</h2><ul class='plain'>" + "".join(
                f"<li><span class='muted'>{e(rel)}</span> {self.record_link(to)}</li>" for rel, to in outgoing) + "</ul>")
        incoming = self.hub.linked(me, ref)
        if incoming:
            out.append("<h2>Records that point here</h2><ul class='plain'>" + "".join(
                f"<li>{self.record_link(x['ref'])} <span class='muted'>{e(x['rel'])}</span></li>" for x in incoming) + "</ul>")
        return "".join(out) or "<p class='muted'>Nothing links to or from this record.</p>"

    def _history_panel(self, me, rt, ref) -> str:
        versions = self.hub.record_history(me, ref)
        rows = []
        for i, v in enumerate(versions):
            prev = versions[i + 1]["data"] if i + 1 < len(versions) else {}
            changed = [f["label"] for f in rt.fields
                       if schema.get(v["data"], schema.locate(v["data"], f["path"], f.get("with")))
                       != schema.get(prev, schema.locate(prev, f["path"], f.get("with")))]
            via = f' · <a href="/runs/{e(v["run_id"])}">in a run</a>' if v.get("run_id") else ""
            note = f'<br><span class="muted">{e(v["comment"])}</span>' if v.get("comment") else ""
            cmp_ = (f' <a href="/records/{e(rt.id)}/{e(ref.split("/", 1)[1])}/compare?a={versions[i + 1]["version"]}'
                    f'&amp;b={v["version"]}">Compare</a>') if i + 1 < len(versions) else ""
            rows.append(f'<tr><td>v{v["version"]}</td><td>{self.who(v["actor"]) if v["actor"] else ""}{via}</td>'
                        f'<td>{e(", ".join(changed) or "—")}{note}{cmp_}</td><td class="muted">{self._at(v["at"])}</td>'
                        f'<td><details><summary>record</summary><pre>{e(json.dumps(v["data"], indent=1))}</pre></details></td></tr>')
        out = ("<div class='tablewrap'><table><tr><th>Version</th><th>By</th><th>Changed</th><th>When</th><th><span class='visually-hidden'>Record</span></th></tr>"
               + "".join(rows) + "</table></div>") if rows else "<p class='muted'>No history.</p>"
        if self.hub.is_policy_owner(me):  # the privacy officer answers "who looked at my chart?"
            seen = self.hub.views_of(me, ref)
            vrows = "".join(f'<tr><td>{self.who(v["actor"])}</td><td>{e((v.get("data") or {}).get("how", ""))}</td>'
                            f'<td class="muted">{self._at(v["at"])}</td></tr>' for v in seen)
            out += ("<h3>Who viewed it</h3>" + (f"<div class='tablewrap'><table><tr><th scope='col'>Who</th>"
                    f"<th scope='col'>How</th><th scope='col'>When</th></tr>{vrows}</table></div>" if vrows
                    else "<p class='muted'>No one has viewed it.</p>"))
        return out

    def page_move(self, req, rtype, rid):
        """A transition screen: what a move asks for (its fields, a comment) before the record moves."""
        me, hub = req["me"], self.hub
        rt = hub.record_type(rtype)
        if not rt or not rt.lifecycle:
            raise Invalid(f"a {rtype} has no lifecycle")
        rec = hub.read_record(me, f"{rtype}/{rid}")
        t = rt.transition(schema.first(rec, rt.lifecycle["path"]), req["query"].get("to", ""))
        if t is None or t["by"] == "workflow":
            raise Invalid(f"{a_an(rt.label.lower())} does not move there from here")
        return self._move_page(req, rt, rec, t, back=self.safe_back(req["query"].get("_back")))

    def _move_page(self, req, rt, rec, t, *, form: dict | None = None, problem: str = "", back: str = ""):
        me = req["me"]
        ref = f"{rt.id}/{rec['id']}"
        shown = rec
        if form is not None:  # after a refusal: what the member typed, not what is saved
            shown = self._typed(rt, form, before=rec, only=t["fields"])
        need = set(t["require"])
        editable = {f["name"] for f in self._editable(rt)}
        inputs = "".join(self._input({**rt.field(n), "required": rt.field(n)["required"] or n in need}, shown, me)
                         for n in t["fields"] if n in editable)  # in the transition's order
        comment = ""
        if t["comment"]:
            typed = (form or {}).get("_comment", "")
            comment = (f'<label>Comment<textarea name="_comment" rows="3">{e(typed if isinstance(typed, str) else "")}'
                       f'</textarea><small>Kept with the move in the record\'s history.</small></label>')
        err = f'<div class="flash error" role="alert"><p>{e(problem)}</p></div>' if problem else ""
        ver = e(str((rec.get("meta") or {}).get("versionId", "")))
        st = schema.first(rec, rt.lifecycle["path"])
        body = (f'<div data-modal-body>{err}<p>{e(self.hub.short_title(rec))}: from {self._state_loz(st, rt)} to {self._state_loz(t["to"], rt)}.</p>'
                f'<form method="post" action="/records/{e(rt.id)}/{e(rec["id"])}/move" class="card stack">'
                f'<input type="hidden" name="to" value="{e(t["to"])}"><input type="hidden" name="_version" '
                f'value="{ver}">' + (f'<input type="hidden" name="_back" value="{e(back)}">' if back else "")
                + f'{self._req_note(inputs)}{inputs}{comment}<div class="filters"><button class="primary">{e(t["label"])}</button>'
                f'<a class="btn secondary" href="{e(back or f"/records/{rt.id}/{rec['id']}")}" data-modal-close>Cancel</a></div>'
                f'</form></div>')
        return self.layout(req, t["label"], body, crumbs=[("/records", "Records"),
                                                          (f"/records/{rt.id}", rt.plural),
                                                          (f"/records/{ref}", self.hub.short_title(rec)), (None, t["label"])])

    def post_record(self, req, rtype, rid, verb):
        me, hub = req["me"], self.hub
        ref = f"{rtype}/{rid}"
        rt = hub.record_type(rtype)
        if not rt:
            raise Invalid(f"there is no record type {rtype}")
        f = self.form(req)
        if verb == "edit":
            before = hub.read_record(me, ref)
            try:
                changes = self._from_form(rt, f, partial=True, before=before, op="edit")
                if changes:
                    hub.update_record(me, ref, changes, expected_version=f.get("_version") or None)
            except Invalid as exc:  # the form again, saying why, with everything typed kept
                from .server import Rendered
                typed = self._typed(rt, f, op="edit", before=before)
                page = self.page_record({**req, "query": {"edit": "1"}}, rtype, rid, kept={"_typed": typed},
                                        error=exc)
                raise Rendered(422, page) from None
            raise self._redirect(f"/records/{rtype}/{rid}")
        if verb == "move":
            if not rt.lifecycle:
                raise Invalid(f"{a_an(rt.label.lower())} has no lifecycle")
            rec = hub.read_record(me, ref)
            st, to = schema.first(rec, rt.lifecycle["path"]), f.get("to", "")
            t = rt.transition(st, to)
            asked = t["fields"] if t else []
            back = self.safe_back(f.get("_back"))  # from a board: back to it
            if f.get("_ask") and t and t["by"] != "workflow" and (asked or t["comment"]):
                # a board's Move to…: a move that asks for fields or a comment shows its screen first
                q = urllib.parse.urlencode({"to": to, **({"_back": back} if back else {})})
                raise self._redirect(f"/records/{rtype}/{rid}/move?{q}")
            try:
                changes = self._from_form(rt, f, partial=True, before=rec, only=asked) if asked else {}
                after = schema.put({**rec, **changes}, rt.lifecycle["path"], to)
                hub.update_record(me, ref, after, comment=f.get("_comment") if t and t["comment"] else None,
                                  expected_version=f.get("_version") or None)
            except (Denied, Invalid) as exc:
                if not (t and (asked or t["comment"])) or isinstance(exc, Denied):
                    if back:  # from a board: why, in the dialog, the card back where it was
                        raise self.refused_move(req, hub.title(rec), exc, back) from None
                    raise
                from .server import Rendered  # the move's screen again, in the dialog, saying why
                raise Rendered(409 if isinstance(exc, Conflict) else 422,
                               self._move_page(req, rt, rec, t, form=f, problem=str(exc), back=back)) from None
            raise self._redirect(back or f"/records/{rtype}/{rid}")
        try:
            self._confirm_signer(req, f)
            hub.sign_record(me, ref, f.get("meaning", ""), f.get("comment", ""), seen=f.get("seen") or "-")
        except (Denied, Invalid) as exc:  # the signatures tab again, saying why, with the meaning and comment kept
            from .server import Rendered
            kept = {k: v for k, v in f.items() if k in ("meaning", "comment")}
            page = self.page_record({**req, "query": {"tab": "signatures"}}, rtype, rid, kept=kept, error=exc)
            raise Rendered(403 if isinstance(exc, Denied) else 422, page) from None
        raise self._redirect(f"/records/{rtype}/{rid}?tab=signatures")

    def post_attach(self, req, rtype, rid):
        """A file from the record page: kept in the hub, then put in the record's attachment field (a list field
        gets one more)."""
        me, hub = req["me"], self.hub
        ref = f"{rtype}/{rid}"
        rt = hub.record_type(rtype)
        if not rt:
            raise Invalid(f"there is no record type {rtype}")
        form = multipart(req)
        f = rt.field(str(form.get("field") or "")) if form.get("field") else None
        if not f or f["type"] != "attachment" or not SIMPLE.fullmatch(f["path"]):
            raise Invalid("pick the field to attach the file to")
        if not isinstance(form.get("file"), tuple) or not form["file"][2]:
            raise Invalid("choose a file to attach")
        name, ctype, data = form["file"]
        att = hub.add_file(me, rtype, data, name, ctype)
        before = hub.read_record(me, ref)
        path = f["path"].removesuffix(".*")
        now = before.get(path)
        value = ([*now, att] if isinstance(now, list) else [att]) if f["path"].endswith(".*") else att
        hub.update_record(me, ref, {path: value}, expected_version=form.get("_version") or None)
        raise self._redirect(f"/records/{rtype}/{rid}")

    def get_file(self, req, rtype, rid, blob):
        data, meta = self.hub.read_file(req["me"], f"{rtype}/{rid}", blob)
        return file_response(data, meta)

    def api_add_file(self, req, rtype):
        """The file is the request body; its name in ``X-Filename`` (URL-encoded) and its type in Content-Type.
        Returns the attachment to put in a record's attachment field."""
        h = req.get("headers") or {}
        return self.hub.add_file(req["me"], rtype, req["body"] or b"",
                                 urllib.parse.unquote(h.get("x-filename", "")), h.get("content-type", ""))

    # ------------------------------------------------------------------------------------------ API
    def api_signatures(self, req, rtype, rid):
        """The signatures, and ``seen``: what a new signature on the record as it is now would cover."""
        ref = f"{rtype}/{rid}"
        return {"signatures": self.hub.signatures(req["me"], ref), "seen": self.hub.record_digest(req["me"], ref)}

    def api_sign(self, req, rtype, rid):
        d = self.body_json(req)
        self._confirm_signer(req, d)
        return self.hub.sign_record(req["me"], f"{rtype}/{rid}", d.get("meaning", ""), d.get("comment", ""),
                                    seen=d.get("seen") or "-")

    def api_history(self, req, rtype, rid):
        return self.hub.record_history(req["me"], f"{rtype}/{rid}")

    # ------------------------------------------------------------------------------------------ views
    def page_view(self, req, vid):
        """A view or dashboard the practice defined: each widget computed for the member looking at it."""
        from ..hub import NotFound
        me, hub = req["me"], self.hub
        v = hub.view(vid)
        if v is None:
            raise NotFound(f"there is no view {vid}")
        out = [f"<p class='muted'>{e(v.summary)}</p>"] if v.summary else []
        out.append(self.view_widgets(req, me, v))
        owner = me["kind"] == "person" and hub.is_policy_owner(me)
        tree = next((w.get("type") for w in v.widgets if w.get("kind") == "tree"), None) if hasattr(v, "widgets") else None
        return self.layout(req, v.title, "".join(out), subnav=self.navigator(me, tree) if tree else "",
                           actions=f'<a class="btn" href="/define/views/{e(vid)}">Change this view</a>' if owner else "")

    def view_widgets(self, req, me, v, *, live: bool = True) -> str:
        """A view's widgets, computed for this member: on its grid when it has one (a widget without a place goes
        below, in order), else one after another. ``live=False`` (the editor's preview): nothing in it moves records."""
        from .. import views as viewmod
        computed = viewmod.compute(self.hub, me, v)
        if not any(w.get("grid") for w in computed):
            return "".join(self.view_widget(req, me, w, v.title, live=live) for w in computed)
        cells = []
        for w, g in sorted(zip(computed, viewmod.placed(computed)), key=lambda p: (p[1]["y"], p[1]["x"])):
            cells.append(f'<div class="dash-cell k-{e(w["kind"])}" style="grid-column:{g["x"] + 1}/span {g["w"]};'
                         f'grid-row:{g["y"] + 1}/span {g["h"]}">{self.view_widget(req, me, w, v.title, live=live)}</div>')
        return f'<div class="dash-grid">{"".join(cells)}</div>'

    def view_widget(self, req, me, w, fallback: str = "", *, live: bool = True) -> str:
        """One computed widget (``views.compute``) as HTML."""
        hub = self.hub
        head = f"<h2>{e(w['title'] or fallback)} <span class='badge'>{w['total']}</span></h2>"
        if w["note"]:
            return f"<div class='section'>{head}<p class='muted'>Not shown: {e(w['note'])}.</p></div>"
        if w["kind"] == "links":
            items = "".join(f'<li><a href="{e(x["href"])}"' + (' rel="noopener noreferrer" target="_blank"'
                                                               if x["href"].startswith("https://") else "")
                            + f'>{e(x["label"])}</a></li>' for x in w["links"])
            return f"<div class='section'><h2>{e(w['title'] or fallback)}</h2><ul class='dash-links'>{items}</ul></div>"
        if w["kind"] == "tree":
            items = "".join(
                f'<li style="--depth:{d}"><a href="{e(self._view_href(w, r))}">{e(hub.title(r))}</a>'
                + "".join(f' <span class="muted">{e(str(x))}</span>' for x in
                          (w["value"](r, c["path"]) for c in w["columns"]) if x not in (None, ""))
                + "</li>" for d, r in w["tree"])
            return f"<div class='section'>{head}" + (f"<ul class='tree'>{items}</ul>" if items else
                                                     "<p class='muted'>Nothing yet.</p>") + "</div>"
        if w["kind"] == "count":
            return f"<div class='section card'><h2>{e(w['title'])}</h2><p class='stat'>{w['total']}</p></div>"
        if w["kind"] == "chart":
            top = max(w["counts"].values() or [1])
            bars = "".join(f"<tr><th scope='row'>{e(k)}</th><td><span class='bar' style='width:"
                           f"{max(4, 100 * n // top)}%'></span> {n}</td></tr>" for k, n in w["counts"].items())
            return (f"<div class='section'>{head}" + (f"<table class='chart'><tbody>{bars}</tbody></table>" if bars
                                                      else "<p class='muted'>Nothing yet.</p>") + "</div>")
        if w["kind"] == "board":
            groups, counts = w["groups"], w["counts"]
            moving = self._board_moves(me, w) if live else None
            if moving:  # a board of a lifecycle: every state is a column, in the lifecycle's order
                order = moving.states()
                groups = {k: groups.get(k, []) for k in order + [k for k in groups if k not in order]}
                counts = {k: counts.get(k, 0) for k in groups}
            back = self.here(req)
            srt = hub.record_type(w["type"]) if w["source"] == "records" and w.get("type") else None
            if not (srt and srt.lifecycle and w["group_by"] in (srt.lifecycle["path"], srt.lifecycle.get("field"))):
                srt = None  # grouped by something else: its values as they are, in words
            cols = "".join(
                f'<section class="col" data-col="{e(k)}" aria-label="{e(self._state_words(srt, k))}"><header>'
                f'<span class="col-name">{e(self._state_words(srt, k))}</span>'
                f'<span class="n">{counts[k]}</span></header>'
                + "".join(self._view_tile(w, r, moving, back) for r in rows) + "</section>"
                for k, rows in groups.items())
            return (f"<div class='section'>{head}<div class='board'>{cols or '<p class=muted>Nothing yet.</p>'}"
                    f"</div></div>")
        cols = w["columns"] or [{"label": "Title", "path": "title"}]
        if w["kind"] == "list":
            items = "".join(
                f'<li>{self._view_cell(w, r, cols[0], True)}'
                + "".join(f' <span class="muted">{e(str(x))}</span>' for x in
                          (w["value"](r, c["path"]) for c in cols[1:]) if x not in (None, ""))
                + "</li>" for r in w["rows"])
            return f"<div class='section'>{head}" + (f"<ul class='dash-list'>{items}</ul>" if items else
                                                     "<p class='muted'>Nothing yet.</p>") + "</div>"
        heads = "".join(f"<th scope='col'>{e(c['label'])}</th>" for c in cols)
        body = "".join("<tr>" + "".join(
            f"<td>{self._view_cell(w, r, c, i == 0)}</td>" for i, c in enumerate(cols)) + "</tr>"
            for r in w["rows"])
        return f"<div class='section'>{head}" + (
            f"<div class='tablewrap'><table><thead><tr>{heads}</tr></thead><tbody>{body}</tbody></table></div>"
            if w["rows"] else "<p class='muted'>Nothing yet.</p>") + "</div>"

    def _view_href(self, w, r) -> str:
        if w["source"] == "records":
            return self.record_href(f"{w['type']}/{r['id']}")
        return f"/items/{r['id']}" if w["source"] == "items" else f"/runs/{r['id']}"

    def _view_cell(self, w, r, c, first) -> str:
        val = w["value"](r, c["path"])
        text = e("" if val is None else str(val))
        return f'<a href="{e(self._view_href(w, r))}">{text or e(r.get("id", ""))}</a>' if first else text

    def _board_moves(self, me, w):
        """The record type whose lifecycle a board widget shows (grouped by its state), when the member may move
        its records; else None."""
        if w["kind"] != "board" or w["source"] != "records":
            return None
        rt = self.hub.record_type(w["type"])
        lc = rt.lifecycle if rt else None
        if not lc or w["group_by"] not in (lc["path"], lc.get("field")) or not self._may_write(me, rt.id):
            return None
        return rt

    def _record_moves(self, rt, rec) -> dict:
        """Where a record's card may go: the moves its page offers a person (not those only a workflow makes);
        one that asks for fields or a comment opens its transition screen."""
        st = schema.first(rec, rt.lifecycle["path"])
        out = {}
        for t in rt.lifecycle.get("transition", []):
            if t["by"] == "workflow" or t["to"] == st or not (st in t["from"] or "*" in t["from"]) or t["to"] in out:
                continue
            asks = bool(t["fields"] or t["comment"])
            out[t["to"]] = {"how": "screen" if asks else "post",
                            "label": (t["label"] if t.get("label") and not t["label"].startswith("Move to ")
                                      else self._state_words(rt, t["to"])) + ("…" if asks else ""),
                            **({"href": f"/records/{rt.id}/{rec['id']}/move?"
                                        + urllib.parse.urlencode({"to": t["to"]})} if asks else {})}
        return out

    def _view_tile(self, w, r, moving=None, back: str = "") -> str:
        cols = w["columns"] or [{"label": "Title", "path": "title"}]
        first = w["value"](r, cols[0]["path"])
        rest = " · ".join(str(x) for x in (w["value"](r, c["path"]) for c in cols[1:]) if x not in (None, ""))
        inner = (f'<div class="t">{e(str(first or r.get("id", "")))}</div>'
                 + (f'<div class="about">{e(rest)}</div>' if rest else ""))
        if not moving:
            return self.board_card(inner, self._view_href(w, r), "", "", "", {}, "", {})
        ver = str((r.get("meta") or {}).get("versionId", ""))
        return self.board_card(inner, self._view_href(w, r), "", f"{moving.id}/{r['id']}", str(first or r["id"]),
                               self._record_moves(moving, r), f"/records/{moving.id}/{r['id']}/move",
                               {"_version": ver, "_back": back, "_ask": "1"})

    # ------------------------------------------------------------------------------------------ modules
    def page_modules(self, req):
        me, hub = req["me"], self.hub
        installed = hub.installed_modules()
        may = me["kind"] == "person" and hub.is_policy_owner(me)
        rows = []
        for d in sorted(packmod.MODULES_DIR.glob("*/module.toml")):
            try:  # read what it says it is; its code is not run to list it
                m = packmod.load_module(d.parent.name, run_code=False)
            except Exception as exc:  # noqa: BLE001 - a broken module on disk is shown, not fatal
                rows.append(f"<tr><td>{e(d.parent.name)}</td><td colspan=4 class='error'>{e(str(exc))}</td></tr>")
                continue
            st = hub.module_status(m.id)
            missing = [r for r in m.requires if hub.module_status(r)["state"] != "running"]
            if st["state"] == "not_running":
                act = (f'<span class="loz removed">installed, not running</span><br><span class="muted">'
                       f'{e(st["why"])}: {e(st["next"])}.</span>'
                       + (f' <a href="/modules/{e(m.id)}">Review</a>' if may else ""))
            elif st["state"] == "running":
                changed = m.digest != hub._module_digest(m.id)
                act = ('<span class="loz success">installed, running</span>'
                       + (f' <a href="/modules/{e(m.id)}">{"Upgrade to version " + e(m.version) if changed else "Manage"}'
                          f'</a>' if may else ""))
            elif not may:
                act = '<span class="muted">a policy owner installs it</span>'
            elif missing:
                why = [f"{r} is installed but not running" if r in installed else f"install {r} first"
                       for r in missing]
                act = f'<span class="muted">{e("; ".join(why))}</span>'
            else:
                act = f'<a class="btn" href="/modules/{e(m.id)}">Review and install</a>'
            adds = sorted(m.type_sources) + [f"+ {did}" for (_, did) in m.extensions]
            rows.append(f'<tr><td><b>{e(m.practice.get("name", m.id))}</b><br><span class="muted">{e(m.id)} '
                        f'v{e(str(m.version))}</span></td><td>{e(m.practice.get("summary", ""))}</td>'
                        f'<td>{e(", ".join(adds))}</td><td>{e(", ".join(m.requires) or "—")}</td><td>{act}</td></tr>')
        warn = (f'<div class="card error" role="alert"><b>The practice pack</b>: {e(hub.pack_problem)}</div>'
                if hub.pack_problem and may else "")
        body = (warn + "<p class='muted'>A module adds roles, record types, workflows and views, and may add fields to "
                "types another module owns (shown with +). Modules build on each other: a CAPA in Quality points at "
                "controlled documents in Document control, and its workflow sends them for review there.</p>"
                "<div class='tablewrap'><table><thead><tr><th scope='col'>Module</th><th scope='col'>What it adds</th>"
                "<th scope='col'>Types</th><th scope='col'>Builds on</th><th scope='col'><span class='visually-hidden'>"
                "Status</span></th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")
        return self.layout(req, "Modules", body)

    def page_module(self, req, mid):
        from ..hub import Denied, NotFound
        me, hub = req["me"], self.hub
        if not (me["kind"] == "person" and hub.is_policy_owner(me)):
            raise Denied("a policy owner manages modules")
        st = hub.module_status(mid)
        installed = st["installed"]
        try:
            plan = hub.module_plan(mid)
        except FileNotFoundError:
            raise NotFound(f"there is no module {mid} in the modules directory") from None
        except ValueError as exc:
            plan = None
            hub.module_problems.setdefault(mid, str(exc))
        out = []
        if st["state"] == "not_running":
            out.append(f'<div class="flash error" role="alert"><p><b>Installed, not running.</b> {e(st["why"])}: '
                       f'{e(st["next"])}.</p></div>')
        if plan is None:
            return self.layout(req, f"Module {mid}", "".join(out), crumbs=[("/modules", "Modules")])
        rows = [("clashes", "Refused: these belong to someone else, so the module cannot change them"),
                ("new", "Arrive active"), ("changed", "Proposed, for another policy owner to approve"),
                ("kept", "Proposed: the practice had changed these itself, so compare before approving"),
                ("pending", "Already proposed, waiting for approval"),
                ("removed", "Retired: this version no longer has them"),
                ("roles", "New roles"), ("data_classes", "New data classes"),
                ("grants_added", "Access it gives"), ("grants_removed", "Access it takes back")]
        reviewers = [r for r in plan["reviewed_by"]]
        review = ("Reviewed by " + ", ".join(self.who(r) for r in reviewers) + "." if reviewers else
                  "Not reviewed yet: a policy owner reviews the module (its code and definitions) before another one "
                  "puts it into force.")
        what = "Install" if not installed else "Upgrade"
        heading = (f"Install version {e(plan['version'])}" if not installed else
                   f"Upgrade to version {e(plan['version'])}" if plan["needs_review"] else
                   f"Version {e(plan['version'])} is running" if st["running"] else
                   f"Version {e(plan['version'])} is installed, not running")
        out.append(f"<div class='section'><h2>{heading}</h2>")
        if installed and not plan["needs_review"]:
            out.append("<p class='muted'>" + ("The practice runs this module as it is on disk: there is nothing to "
                       "upgrade." if st["running"] else "Its files are the ones put into force, but this hub is not "
                       "running its code.") + "</p>")
        else:
            out.append(f"<p>Its code (pack.py) is <b>{'changed' if plan['code_changed'] else 'unchanged'}</b>. "
                       f"{review}</p>")
            for key, label in rows:
                if plan[key]:
                    items = "".join(f"<li>{e(' '.join(x) if isinstance(x, list) else x)}</li>" for x in plan[key])
                    out.append(f"<h3>{e(label)}</h3><ul>{items}</ul>")
            buttons = ""
            if me["id"] not in reviewers:
                buttons += (f'<form method="post" action="/modules/{e(mid)}/review-code" class="inline"><button '
                            f'class="secondary" data-confirm="Sign that you reviewed {e(mid)} {e(plan["version"])}, '
                            f'its code and definitions?">I have reviewed it</button></form>')
            if [r for r in reviewers if r != me["id"]] and not plan["clashes"]:
                action = "/modules" if not installed else f"/modules/{e(mid)}/upgrade"
                buttons += (f'<form method="post" action="{action}" class="inline"><input type="hidden" name="name" '
                            f'value="{e(mid)}"><button class="primary" data-confirm="{what} {e(mid)}? You sign it '
                            f'in.">{what}</button></form>')
            elif reviewers == [me["id"]]:
                buttons += "<p class='muted'>You reviewed it: another policy owner puts it into force.</p>"
            out.append(f"<div class='filters'>{buttons}</div>")
        out.append("</div>")
        if installed:
            out.append(f'<div class="section"><h2>Remove</h2><p class="muted">Its workflows, views and types are '
                       f'retired, and what it added to other definitions is taken out. Records stay, with their '
                       f'history and signatures. Access it gave is taken back.</p><form method="post" '
                       f'action="/modules/{e(mid)}/remove"><label>Why *<input name="reason" required></label>'
                       f'<button class="danger" data-confirm="Remove {e(mid)}?">Remove the module</button></form></div>')
        return self.layout(req, f"Module {mid}", "".join(out), crumbs=[("/modules", "Modules")])

    def post_module(self, req):
        self.hub.install_module(req["me"], self.form(req).get("name", ""))
        raise self._redirect("/modules")

    def post_module_verb(self, req, mid, verb):
        if verb == "review-code":
            self.hub.approve_module_code(req["me"], mid)
            raise self._redirect(f"/modules/{mid}" if mid in self.hub.installed_modules() else "/modules")
        if verb == "upgrade":
            self.hub.upgrade_module(req["me"], mid)
            raise self._redirect("/modules")
        self.hub.uninstall_module(req["me"], mid, self.form(req).get("reason", ""))
        raise self._redirect("/modules")
