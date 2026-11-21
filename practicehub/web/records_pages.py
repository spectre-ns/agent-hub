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
from .. import schema
from ..hub import HubError, Denied, Invalid

e = html.escape
SIMPLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.\*)?")  # a field a form can edit: a top-level key, or a list of them
# or one plain value inside a structure, by keys and list positions (name.0.given.0, telecom.0.value)
NESTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.([0-9]+|[A-Za-z_][A-Za-z0-9_]*))+")
SCALAR = ("string", "text", "markdown", "date", "datetime", "choice", "number", "boolean")
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


class RecordPages:
    def record_routes(self, R):
        R("GET", r"/records", self.page_record_types)
        R("GET", r"/records/(?P<rtype>[A-Za-z][\w]*)", self.page_records)
        R("POST", r"/records/(?P<rtype>[A-Za-z][\w]*)", self.post_record_create)
        R("GET", r"/records/(?P<rtype>[A-Za-z][\w]*)/new", self.page_record_new)
        R("GET", r"/create", self.page_create)
        R("GET", r"/records/(?P<rtype>[A-Za-z][\w]*)/(?P<rid>[\w.-]+)", self.page_record)
        R("POST", r"/records/(?P<rtype>[A-Za-z][\w]*)/(?P<rid>[\w.-]+)/(?P<verb>edit|move|sign)", self.post_record)
        R("GET", r"/records/(?P<rtype>[A-Za-z][\w]*)/(?P<rid>[\w.-]+)/move", self.page_move)
        R("POST", r"/records/(?P<rtype>[A-Za-z][\w]*)/(?P<rid>[\w.-]+)/attach", self.post_attach)
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

    def record_link(self, ref: str) -> str:
        t, _, rid = ref.partition("/")
        if t in self.hub.member_types():
            return self.who(rid) if self.hub.db.members.exists({"_id": rid}) else e(ref)
        me = self.reader()
        rt = self.hub.record_type(t)
        if rt and (me is None or not self.hub.may_read(me, t)):  # its title is what it holds: not for this reader
            return f'<span class="muted">{e(rt.label)} you may not open</span>'
        rec = self.hub.records.read(t, rid) if self.hub.record_type(t) else None
        text = self.hub.title(rec) if rec else ref
        rt = self.hub.record_type(t)
        return (f'<a href="{e(self.record_href(ref))}">{e(text)}</a> '
                f'<span class="muted">{e(rt.label if rt else t)}</span>')

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
        return [f for f in rt.fields if (SIMPLE.fullmatch(f["path"]) or (NESTED.fullmatch(f["path"])
                                                                          and f["type"] in SCALAR))
                and f.get("show", True) is not False and f.get("by") != "workflow"
                and f["type"] != "attachment" and not (rt.lifecycle and f["path"] == rt.lifecycle["path"])
                and not (f["type"] == "list" and any(g["path"] == f["path"] + ".*" for g in rt.fields))]

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
                elif isinstance(item, (dict, list)):
                    out.append(self._plain_html(item))
                elif isinstance(item, str) and "\n" in item:
                    out.append(f"<pre class='wrap'>{e(item)}</pre>")
                else:
                    out.append(e(str(item)))
        return "<br>".join(out) if out else "<span class='muted'>—</span>"

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

    def _attachment_html(self, rec, a: dict) -> str:
        name = str(a.get("name") or a.get("title") or "file")
        if isinstance(a.get("blob"), str) and re.fullmatch(r"[0-9a-f]{64}", a["blob"]) and rec.get("id"):
            size = a.get("size")
            kb = f' <span class="muted">{max(1, round(size / 1024))} KB</span>' if isinstance(size, int) else ""
            href = f"/records/{urllib.parse.quote(str(rec.get('resourceType', '')))}/{urllib.parse.quote(rec['id'])}/files/{a['blob']}"
            return f'<a href="{e(href)}">{e(name)}</a>{kb}'
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
            found = self.hub.records.search(t, limit=50, text=words)  # every word typed is in the record
            name = lambda r: self.hub.title(r).strip().lower()
            exact = [r for r in found if r["id"] == typed or typed.lower() in (name(r), lead(name(r)))]
            hits += [f"{t}/{r['id']}" for r in (exact or found)]
        if len(hits) == 1:
            return hits[0]
        raise Invalid(f"{f['label']}: " + (f"no record is called {typed!r}" if not hits else
                                           f"{len(hits)} records are called {typed!r}: type more of it (a date of "
                                           f"birth or number), or open the one you mean and link it from there"))

    def _input(self, f, rec, me) -> str:
        """A form control for a field, filled from the record."""
        name = f["path"].removesuffix(".*")
        vals = [v for v in schema.get(rec, schema.locate(rec, f["path"], f.get("with"))) if v not in (None, "")] \
            if rec else []
        spec = {**f, "name": name}
        if f["type"] == "reference":
            refs = [schema.ref_of(v) for v in vals if schema.ref_of(v)]
            options, many = [], False
            members = self.hub.member_types()
            for t in self.hub.concrete(f["target"]):
                if t in members:  # a reference to a member of the practice: the people on the team
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
        choices = [{"value": c, "label": c} for c in f["choices"]] if f["type"] == "choice" else None
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

    def _inputs(self, rt, rec, me, op: str = "edit") -> str:
        """The form's controls: the type's screen for ``op`` (create, edit) in its tabs, or, with no screen, every
        field with the pack's ``advanced`` ones (whole structures, as JSON) folded away below."""
        tabs = self._on_screen(rt, op)
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
            if not self._may_read(me, t):
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
        recs = hub.records.search(rtype, limit=SHOWN + 1, text=q or None)
        more, recs = len(recs) > SHOWN, recs[:SHOWN]
        lc = rt.lifecycle
        heads = [f for f in self._editable(rt) if f["type"] in ("string", "choice", "date", "member", "number")][:3]
        rows = "".join(
            f'<tr><td><a href="{e(self.record_href(f"{rtype}/{r["id"]}"))}">{e(hub.title(r))}</a></td>'
            + (f'<td>{self._state_loz(schema.first(r, lc["path"]))}</td>' if lc else "")
            + "".join(f"<td>{self._value_html(f, r)}</td>" for f in heads) + "</tr>" for r in recs)
        table = (f'<div class="tablewrap"><table><tr><th>{e(rt.label)}</th>' + ("<th>Status</th>" if lc else "")
                 + "".join(f"<th>{e(f['label'])}</th>" for f in heads) + f"</tr>{rows}</table></div>") if recs else (
            f"<p class='muted'>No {e(self._lower(rt.plural))} yet.</p>")
        if req["query"].get("parent"):  # the old way to add under a record: its create form now has its own page
            raise self._redirect(f"/records/{rtype}/new?parent={urllib.parse.quote(req['query']['parent'])}")
        create = (f'<a class="btn primary" href="/records/{e(rtype)}/new" data-modal>Create</a>'
                  if hub.may_write(me, rtype) and rtype != self.subj["type"] else "")
        search = (f'<form class="inline"><input name="q" value="{e(q)}" placeholder="Search {e(self._lower(rt.plural))}" aria-label="Search {e(self._lower(rt.plural))}">'
                  f'<button class="secondary">Search</button></form>')
        note = (f"<p class='muted'>The {SHOWN} most recently changed are shown: search to find any other.</p>"
                if more else "")
        return self.layout(req, rt.plural, search + table + note,
                           crumbs=[("/records", "Records"), (None, rt.plural)], subnav=self.navigator(me, rtype),
                           actions=create)

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
        under = req["query"].get("parent", "") or (kept or {}).get("_parent", "")
        seed = {k: v for k, v in (kept or {}).items() if k != "_parent"} or None
        if rt.parent and under.startswith(rtype + "/"):
            seed = {"resourceType": rtype, **(seed or {}), **schema.put({}, rt.parent, {"reference": under})}
        start = f' It starts as <b>{e(lc["initial"])}</b>.' if lc and lc.get("initial") else ""
        err = f'<div class="flash error" role="alert"><p>{e(str(error))}</p></div>' if error else ""
        inputs = self._inputs(rt, seed, me, "create")
        body = (f'<div data-modal-body>{err}<form method="post" action="/records/{e(rtype)}" class="card stack" id="new">'
                f'<p class="muted">{e(rt.summary)}{start}</p>{self._req_note(inputs)}{inputs}'
                f'<div class="filters"><button class="primary">Create</button>'
                f'<a class="btn secondary" href="/records/{e(rtype)}" data-modal-close>Cancel</a>'
                f'<a class="btn subtle back" href="/start" data-modal>← Back</a></div></form></div>')
        return self.layout(req, f"Create {a_an(self._lower(rt.label))}", body,
                           crumbs=[("/records", "Records"), (f"/records/{rtype}", rt.plural), (None, "Create")])

    def _create_pick(self, me) -> str:
        """What the member may add, the practice's shortcuts first; each opens its create screen in the dialog."""
        hub = self.hub
        short = self.shortcuts(me)
        rest = [t for t, rt in sorted(hub.record_types().items(), key=lambda kv: (kv[1].order, kv[0]))
                if t not in short and t not in hub.member_types() and not rt.abstract and t != self.subj["type"]
                and self._may_write(me, t)]

        def li(t):
            rt = hub.record_type(t)
            return (f'<li><a href="/records/{e(t)}/new" data-modal><b>{e(rt.label)}</b>'
                    f'<span class="muted">{e(rt.summary)}</span></a></li>')
        short = [t for t in short if self._may_write(me, t)]
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
                and self.hub.record_type(t) and self._may_read(me, t)]

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
        for p in (self.hub.pack.all_parts() if self.hub.pack else []):
            for t in p.practice.get("shortcuts", []) or []:
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
        try:
            rec = self.hub.create_record(me, rtype, self._from_form(rt, f, partial=False, op="create"))
        except Invalid as exc:
            from .server import Rendered
            kept = self._typed(rt, f, op="create")
            if isinstance(f.get("_parent"), str) and f["_parent"]:
                kept["_parent"] = f["_parent"]
            raise Rendered(422, self.page_record_new(req, rtype, kept=kept, error=exc)) from None
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

    def _state_loz(self, st) -> str:
        """A status lozenge. Its tone comes from common, generic state words; a state it does not know is grey."""
        if st is None:
            return ""
        s = str(st).lower()
        tone = ("success" if s in self.SETTLED else
                "removed" if s in self.UNDONE else
                "inprogress" if "review" in s or s in self.UNDERWAY else
                "default")
        return f'<span class="loz {tone}">{e(str(st))}</span>'

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
        prose = [f for f in rt.fields if f["type"] == "markdown" and f.get("show", True) is not False]
        row = lambda f: ("" if f in prose else
                         f"<tr><th>{e(f['label'])}</th><td>{self._value_html(f, rec)}</td></tr>")
        view = rt.screen("view")
        if view is not None:  # the type's view screen: its tabs as headed groups, its fields in its order
            fields = "".join((f"<tr><th colspan=2 scope=colgroup class='group'>{e(t['label'])}</th></tr>"
                              if t["label"] and len(view) > 1 else "") + "".join(row(rt.field(n)) for n in t["fields"])
                             for t in view)
        else:
            fields = "".join(row(f) for f in rt.fields if f.get("show", True) is not False and not f.get("advanced")
                             and not (lc and f["path"] == lc["path"])
                             and not (f["type"] == "list" and any(g["path"] == f["path"] + ".*" for g in rt.fields)))
        adv_rows = "".join(f"<tr><th>{e(f['label'])}</th><td>{self._value_html(f, rec)}</td></tr>"
                           for f in rt.fields if f.get("advanced") and f.get("show", True) is not False)
        if rtype == self.subj["type"] and hub.is_policy_owner(me):
            fields += (f"<tr><th>Privacy</th><td><a href='/access?subject={e(urllib.parse.quote(ref))}'>Who opened or "
                       f"changed this file</a></td></tr>")
        main = [f"<table class='kv'>{fields}</table>"
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
        editable = may_write and not locked and bool(self._editable(rt))
        # one place for a record: it is read, and an Edit button turns it into its form where it stood (?edit=1)
        editing = tab == "details" and editable and bool(req["query"].get("edit"))
        typed = (kept or {}).get("_typed") if editing else None  # a refused edit: what was typed, not what is saved
        shown = typed or rec
        refused = (f'<div class="flash error" role="alert"><p>{e(str(error))}</p></div>'
                   if editing and error else "")
        is_page = rt.layout == "page"
        edit_btn = (f'<a class="btn primary" href="{url}?edit=1#edit" accesskey="e" aria-keyshortcuts="Alt+E">Edit</a>'
                    if editable and not editing else "")
        if tab == "details":
            used = hub.in_use_version(me, ref)
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
                main.append(f'<div class="flash info"><p>{e(a_an(rt.label)[0].upper() + a_an(rt.label)[1:])} that is <b>{e(str(st))}</b> is not edited. '
                            + e("; ".join(t["label"] for t in lc["transition"] if st in t["from"] and t["by"] != "workflow")
                                or "A workflow moves it on") + ' to change it.</p></div>')
            elif editing and not is_page:
                inputs = self._inputs(rt, shown, me, "edit")
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
            if rt.parent and not editing:
                kids = hub.children(me, ref)
                add = (f' <a class="btn secondary" href="/records/{e(rtype)}/new?parent={e(urllib.parse.quote(ref))}" data-modal>'
                       f'Add {e(a_an(self._lower(rt.label)))} under this one</a>' if may_write else "")
                main.append(f"<section class='children'><h2>Under this {e(self._lower(rt.label))}{add}</h2>" + (
                    "<ul class='plain'>" + "".join(
                        f'<li><a href="{e(self.record_href(f"{rtype}/{k["id"]}"))}">{e(hub.title(k))}</a></li>'
                        for k in kids) + "</ul>" if kids else "<p class='muted'>Nothing yet.</p>") + "</section>")
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
                f'<td class="muted">{e(r["created_at"][:16].replace("T", " "))}</td></tr>' for r in runs)
                + "</table></div>") if runs else "<p class='muted'>No work about this record.</p>")
            from urllib.parse import quote
            starts = [w["workflow"] for w in hub.workflows() if w["workflow"].record == rtype
                      and hub.can(me, "starter", f"workflow:{w['workflow'].id}")]
            if starts:
                main.append("<div class='filters'>" + "".join(
                    f'<a class="btn" href="/start?workflow={e(wf.id)}&amp;record={e(quote(ref))}#">Start: '
                    f'{e(wf.title)}</a>' for wf in starts) + "</div>")

        def move(t):
            kind = "danger-subtle" if self._undoing(t) else "primary"
            if t["fields"] or t["comment"]:  # a transition screen asks for what the move needs first
                return (f'<a class="btn {kind}" href="{url}/move?to={e(urllib.parse.quote(t["to"]))}" data-modal>'
                        f'{e(t["label"])}…</a>')
            confirm = f' data-confirm="Move it to {e(t["to"])}?"' if kind != "primary" else ""
            return (f'<form method="post" action="{url}/move" class="inline">'
                    f'<input type="hidden" name="to" value="{e(t["to"])}"><button class="{kind}"{confirm}>'
                    f'{e(t["label"])}</button></form>')

        moves = [t for t in lc.get("transition", []) if (st in t["from"] or "*" in t["from"]) and t["to"] != st] \
            if lc else []
        mine = [t for t in moves if t["by"] != "workflow"] if may_write else []
        auto = [t for t in moves if t["by"] == "workflow"]
        moves_html = "".join(move(t) for t in mine) if not editing else ""
        sigs = hub.signatures(me, ref)
        cur = [s for s in sigs if s["current"]]
        if is_page:
            body = refused + self._page_body(req, rt, shown if editing else rec, main, prose, st, tab, editing, url,
                                             ver, moves_html, edit_btn, auto, sigs, cur, may_write)
            above = [(self.record_href(f"{rtype}/{a['id']}"), hub.title(a)) for a in hub.ancestors(me, ref)]
            return self.layout(req, hub.title(rec), body, heading=not editing,
                               crumbs=[("/records", "Records"), (f"/records/{rtype}", rt.plural), *above,
                                       (None, hub.title(rec))], subnav=self.navigator(me, rtype, ref))

        # side column: state, moves a person may make, signatures at a glance
        side = []
        if lc:
            side.append(f"<h2>Status</h2><p>{self._state_loz(st)}</p>")
            if moves_html:
                side.append(moves_html)
            if auto:
                side.append("<p class='muted'>A workflow moves it to " + e(", ".join(t["to"] for t in auto)) + ".</p>")
        side.append(f"<h2>Signatures</h2><p>{len(cur)} on this version"
                    + (f", {len(sigs) - len(cur)} on earlier ones" if len(sigs) > len(cur) else "") + "</p>")
        side.append(f'<h2>Record</h2><p class="muted">{e(rt.label)} · {e(rt.data_class)}<br>version '
                    f'{e(str(rec.get("meta", {}).get("versionId", "")))} · {e(rec.get("meta", {}).get("lastUpdated", "")[:16].replace("T", " "))}</p>')
        from .. import markup
        heads = [h for f in prose for v in schema.get(rec, f["path"]) if isinstance(v, str)
                 for h in markup.headings(v)]
        if len(heads) >= 3 and tab == "details":
            side.insert(0, "<h2>On this page</h2><ul class='plain toc'>" + "".join(
                f'<li class="l{lvl}"><a href="#{e(anchor)}">{e(words)}</a></li>' for lvl, words, anchor in heads)
                + "</ul>")
        title = hub.title(rec)
        body = (f'<div class="issue"><div>{"".join(main)}</div><aside class="issue-side">{"".join(side)}</aside></div>')
        above = [(self.record_href(f"{rtype}/{a['id']}"), hub.title(a)) for a in hub.ancestors(me, ref)]
        return self.layout(req, title, body, crumbs=[("/records", "Records"), (f"/records/{rtype}", rt.plural),
                                                       *above, (None, title)], subnav=self.navigator(me, rtype, ref),
                           actions=edit_btn)

    def _page_body(self, req, rt, rec, main, prose, st, tab, editing, url, ver, moves_html, edit_btn, auto, sigs, cur,
                   may_write) -> str:
        """A type with ``layout = "page"``: its formatted text is the page. Above it, one bar holds what it is (status,
        its other fields, version, signatures) and what can be done (the sections, moves, Edit). Edit turns the page
        itself into its editor, where it stood, with Save and Cancel in the bar."""
        from .. import markup
        me = req["me"]
        lc = rt.lifecycle
        meta = [self._state_loz(st)] if lc else []
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
                        f'{e(str(m.get("lastUpdated", ""))[:16].replace("T", " "))}</span>')
            meta.append(f'<a class="meta-item" href="?tab=signatures">{len(cur)} signature{"s" if len(cur) != 1 else ""}'
                        + (f' ({len(sigs) - len(cur)} on earlier versions)' if len(sigs) > len(cur) else "") + "</a>")
            if auto:
                meta.append(f'<span class="meta-item">a workflow moves it to {e(", ".join(t["to"] for t in auto))}</span>')
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
            if tab == "details":  # its text is the page; the other fields are in the bar
                rest[1] = '<article class="page-body">' + "".join(
                    (f"<h2 class='prose-label'>{e(f['label'])}</h2>" if len(prose) > 1 else "")
                    + self._value_html(f, rec) for f in prose) + "</article>"
            return bar + "".join(rest)
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
        link = lambda r: f"{rtype}/{r['id']}"

        def a(r, cls=""):
            here = ' aria-current="page"' if link(r) == ref else ""
            return f'<a class="{cls}" href="{e(self.record_href(link(r)))}"{here}>{e(hub.title(r))}</a>'
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
            pages = (f'<div class="sec">{e(hub.title(top))}</div>{add}'
                     + (f'<ul class="pagetree">{inner}</ul>' if inner else '<p class="muted">No pages under it yet.</p>'))
        return (f'<nav class="subnav" aria-label="{e(label)} and pages"><details class="spaces" '
                f'{"" if top is not None else "open"}><summary class="sec">{e(label)}</summary><ul class="tops">{tops}'
                f'</ul></details>{pages}</nav>')

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
                    f'<td class="muted">{e(s["at"][:16].replace("T", " "))}</td><td>{e(s["comment"])}</td>'
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
            rows.append(f'<tr><td>v{v["version"]}</td><td>{self.who(v["actor"]) if v["actor"] else ""}{via}</td>'
                        f'<td>{e(", ".join(changed) or "—")}</td><td class="muted">{e(v["at"][:16].replace("T", " "))}</td>'
                        f'<td><details><summary>record</summary><pre>{e(json.dumps(v["data"], indent=1))}</pre></details></td></tr>')
        out = ("<div class='tablewrap'><table><tr><th>Version</th><th>By</th><th>Changed</th><th>When</th><th><span class='visually-hidden'>Record</span></th></tr>"
               + "".join(rows) + "</table></div>") if rows else "<p class='muted'>No history.</p>"
        if self.hub.is_policy_owner(me):  # the privacy officer answers "who looked at my chart?"
            seen = self.hub.views_of(me, ref)
            vrows = "".join(f'<tr><td>{self.who(v["actor"])}</td><td>{e((v.get("data") or {}).get("how", ""))}</td>'
                            f'<td class="muted">{e(v["at"][:16].replace("T", " "))}</td></tr>' for v in seen)
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
        return self._move_page(req, rt, rec, t)

    def _move_page(self, req, rt, rec, t, *, form: dict | None = None, problem: str = ""):
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
        body = (f'{err}<p>{e(self.hub.title(rec))}: from {self._state_loz(st)} to {self._state_loz(t["to"])}.</p>'
                f'<form method="post" action="/records/{e(rt.id)}/{e(rec["id"])}/move" class="card stack">'
                f'<input type="hidden" name="to" value="{e(t["to"])}"><input type="hidden" name="_version" '
                f'value="{ver}">{self._req_note(inputs)}{inputs}{comment}<div class="filters"><button class="primary">{e(t["label"])}</button>'
                f'<a class="btn secondary" href="/records/{e(rt.id)}/{e(rec["id"])}">Cancel</a></div></form>')
        return self.layout(req, t["label"], body, crumbs=[("/records", "Records"),
                                                          (f"/records/{rt.id}", rt.plural),
                                                          (f"/records/{ref}", self.hub.title(rec)), (None, t["label"])])

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
            try:
                changes = self._from_form(rt, f, partial=True, before=rec, only=asked) if asked else {}
                after = schema.put({**rec, **changes}, rt.lifecycle["path"], to)
                hub.update_record(me, ref, after, comment=f.get("_comment") if t and t["comment"] else None,
                                  expected_version=f.get("_version") or None)
            except Invalid as exc:
                if not (t and (asked or t["comment"])):
                    raise
                return self._move_page(req, rt, rec, t, form=f, problem=str(exc))
            raise self._redirect(f"/records/{rtype}/{rid}")
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
        from .. import views as viewmod
        me, hub = req["me"], self.hub
        v = hub.view(vid)
        if v is None:
            raise NotFound(f"there is no view {vid}")
        out = [f"<p class='muted'>{e(v.summary)}</p>"] if v.summary else []
        for w in viewmod.compute(hub, me, v):
            head = f"<h2>{e(w['title'] or v.title)} <span class='badge'>{w['total']}</span></h2>"
            if w["note"]:
                out.append(f"<div class='section'>{head}<p class='muted'>Not shown: {e(w['note'])}.</p></div>")
                continue
            if w["kind"] == "tree":
                items = "".join(
                    f'<li style="--depth:{d}"><a href="{e(self._view_href(w, r))}">{e(hub.title(r))}</a>'
                    + "".join(f' <span class="muted">{e(str(x))}</span>' for x in
                              (w["value"](r, c["path"]) for c in w["columns"]) if x not in (None, ""))
                    + "</li>" for d, r in w["tree"])
                out.append(f"<div class='section'>{head}" + (f"<ul class='tree'>{items}</ul>" if items else
                                                             "<p class='muted'>Nothing yet.</p>") + "</div>")
                continue
            if w["kind"] == "count":
                out.append(f"<div class='section card'><h2>{e(w['title'])}</h2><p class='stat'>{w['total']}</p></div>")
            elif w["kind"] in ("board", "chart"):
                if w["kind"] == "chart":
                    top = max(w["counts"].values() or [1])
                    bars = "".join(f"<tr><th scope='row'>{e(k)}</th><td><span class='bar' style='width:"
                                   f"{max(4, 100 * n // top)}%'></span> {n}</td></tr>" for k, n in w["counts"].items())
                    out.append(f"<div class='section'>{head}<table class='chart'><tbody>{bars}</tbody></table></div>")
                    continue
                cols = "".join(
                    f'<section class="col" aria-label="{e(k)}"><header>{self._state_loz(k)}<span class="n">'
                    f'{w["counts"][k]}</span></header>'
                    + "".join(self._view_tile(w, r) for r in rows) + "</section>" for k, rows in w["groups"].items())
                out.append(f"<div class='section'>{head}<div class='board'>{cols or '<p class=muted>Nothing yet.</p>'}"
                           f"</div></div>")
            else:
                cols = w["columns"] or [{"label": "Title", "path": "title"}]
                heads = "".join(f"<th scope='col'>{e(c['label'])}</th>" for c in cols)
                body = "".join("<tr>" + "".join(
                    f"<td>{self._view_cell(w, r, c, i == 0)}</td>" for i, c in enumerate(cols)) + "</tr>"
                    for r in w["rows"])
                out.append(f"<div class='section'>{head}" + (
                    f"<div class='tablewrap'><table><thead><tr>{heads}</tr></thead><tbody>{body}</tbody></table></div>"
                    if w["rows"] else "<p class='muted'>Nothing yet.</p>") + "</div>")
        owner = me["kind"] == "person" and hub.is_policy_owner(me)
        tree = next((w.get("type") for w in v.widgets if w.get("kind") == "tree"), None) if hasattr(v, "widgets") else None
        return self.layout(req, v.title, "".join(out), subnav=self.navigator(me, tree) if tree else "",
                           actions=f'<a class="btn" href="/define/views/{e(vid)}">Change this view</a>' if owner else "")

    def _view_href(self, w, r) -> str:
        if w["source"] == "records":
            return self.record_href(f"{w['type']}/{r['id']}")
        return f"/items/{r['id']}" if w["source"] == "items" else f"/runs/{r['id']}"

    def _view_cell(self, w, r, c, first) -> str:
        val = w["value"](r, c["path"])
        text = e("" if val is None else str(val))
        return f'<a href="{e(self._view_href(w, r))}">{text or e(r.get("id", ""))}</a>' if first else text

    def _view_tile(self, w, r) -> str:
        cols = w["columns"] or [{"label": "Title", "path": "title"}]
        first = w["value"](r, cols[0]["path"])
        rest = " · ".join(str(x) for x in (w["value"](r, c["path"]) for c in cols[1:]) if x not in (None, ""))
        return (f'<a class="tile" href="{e(self._view_href(w, r))}"><div class="t">{e(str(first or r.get("id", "")))}'
                f'</div>' + (f'<div class="about">{e(rest)}</div>' if rest else "") + "</a>")

    # ------------------------------------------------------------------------------------------ modules
    def page_modules(self, req):
        me, hub = req["me"], self.hub
        have = {m.id: m for m in hub.pack.modules}
        installed = hub.installed_modules()
        may = me["kind"] == "person" and hub.is_policy_owner(me)
        rows = []
        for d in sorted(packmod.MODULES_DIR.glob("*/module.toml")):
            try:  # read what it says it is; its code is not run to list it
                m = packmod.load_module(d.parent.name, run_code=False)
            except Exception as exc:  # noqa: BLE001 - a broken module on disk is shown, not fatal
                rows.append(f"<tr><td>{e(d.parent.name)}</td><td colspan=4 class='error'>{e(str(exc))}</td></tr>")
                continue
            missing = [r for r in m.requires if r not in have]
            if m.id in hub.module_problems:
                act = (f'<span class="loz removed">not running</span> <a href="/modules/{e(m.id)}">Review</a>')
            elif m.id in have:
                changed = m.digest != hub._module_digest(m.id)
                act = ('<span class="loz success">installed</span>'
                       + (f' <a href="/modules/{e(m.id)}">{"Upgrade to version " + e(m.version) if changed else "Manage"}'
                          f'</a>' if may else ""))
            elif m.id in installed:
                act = '<span class="loz moved">installed, not loaded</span>'
            elif not may:
                act = '<span class="muted">a policy owner installs it</span>'
            elif missing:
                why = [f"{r} is not running" if r in hub.module_problems else f"install {r} first" for r in missing]
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
        installed = mid in hub.installed_modules()
        try:
            plan = hub.module_plan(mid)
        except FileNotFoundError:
            raise NotFound(f"there is no module {mid} in the modules directory") from None
        except ValueError as exc:
            plan = None
            hub.module_problems.setdefault(mid, str(exc))
        out = []
        if mid in hub.module_problems:
            out.append(f'<div class="flash error" role="alert"><p>{e(hub.module_problems[mid])}</p></div>')
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
                   f"Version {e(plan['version'])} is running")
        out.append(f"<div class='section'><h2>{heading}</h2>")
        if installed and not plan["needs_review"]:
            out.append("<p class='muted'>The practice runs this module as it is on disk: there is nothing to "
                       "upgrade.</p>")
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
