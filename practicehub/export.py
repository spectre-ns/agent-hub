"""Records out of the hub, in standard formats: what another clinic's system, a spreadsheet or a word processor reads.

    one record      FHIR R4 JSON (a type with ``standard = "fhir-r4"``), else JSON in the hub's own shape;
                    an OpenDocument text (.odt) of its fields as a labelled table; a print view (the browser saves PDF)
    a list          CSV and an OpenDocument spreadsheet (.ods) of the columns the list shows; FHIR NDJSON (Bulk Data:
                    one resource a line) for a FHIR type; a print view
    a subject's     a FHIR ``Bundle`` (type ``collection``) of the subject, its parts and the records linked to it: the
    file            access-request and transfer-of-care export; the same as a document (.odt) or a print view

The rules are the hub's, whatever the format:

- Every record that goes into an export is a read on the track (``record.viewed``, ``how = "export"``, the format),
  each time: an export is never folded into an earlier view.
- What the member may not read is left out, and counted, never named.
- Exporting a list or a file is itself an event (``records.exported``: who, what, the format, how many).
- A bulk export (every record of a type, or the whole hub) is for a policy owner: ``practicehub export --as <owner>``.

Only the standard library: zipfile and XML text for OpenDocument, json and csv for the rest.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import zipfile
from dataclasses import dataclass
from xml.sax.saxutils import escape as _xml_escape

from . import schema, visibility
from .db import utcnow
from .hub import Invalid, NotFound
from . import decimals

FHIR = "fhir-r4"
ODT = "application/vnd.oasis.opendocument.text"
ODS = "application/vnd.oasis.opendocument.spreadsheet"

# format -> (its name in plain words, Content-Type, file extension, what it is for)
FORMATS = {
    "fhir": ("FHIR (for another clinic's system)", "application/fhir+json", "json",
             "FHIR R4 JSON, the standard health records are exchanged in"),
    "ndjson": ("FHIR bulk data (.ndjson)", "application/fhir+ndjson", "ndjson",
               "One FHIR resource a line, as FHIR Bulk Data exports them"),
    "json": ("JSON (for another system)", "application/json", "json", "The record as the hub keeps it"),
    "csv": ("CSV", "text/csv; charset=utf-8", "csv", "Plain rows and columns, for any spreadsheet"),
    "ods": ("Spreadsheet (.ods)", ODS, "ods", "Opens in LibreOffice, Excel or Google Sheets"),
    "odt": ("Document (.odt)", ODT, "odt", "Opens in LibreOffice, Word or Google Docs"),
    "print": ("Print / save as PDF", "text/html; charset=utf-8", "html",
              "A page to print; choose Save as PDF in the print dialog"),
}


def is_fhir(rt) -> bool:
    return bool(rt is not None and getattr(rt, "standard", None) == FHIR)


def offered(hub, rtype: str, scope: str) -> list[str]:
    """The formats a record (``record``), a list (``list``) or a subject's file (``file``) of this type exports to.
    A type that is not a FHIR resource offers no FHIR format."""
    fhir = is_fhir(hub.record_type_for_reading(rtype))
    if scope == "record":
        return (["fhir"] if fhir else ["json"]) + ["odt", "print"]
    if scope == "list":
        return ["csv", "ods"] + (["ndjson"] if fhir else []) + ["print"]
    if scope == "file":
        return (["fhir"] if fhir else ["json"]) + ["odt", "print"]
    raise ValueError(scope)


@dataclass
class Export:
    body: bytes
    format: str
    filename: str
    count: int        # records in it
    left_out: int     # records the member may not read, left out
    other: int = 0    # records left out of a FHIR export because their type is not a FHIR resource

    @property
    def content_type(self) -> str:
        return FORMATS[self.format][1]

    def headers(self) -> dict:
        how = "inline" if self.format == "print" else "attachment"
        h = {"Content-Type": self.content_type, "Content-Disposition": f'{how}; filename="{self.filename}"',
             "X-Records": str(self.count), "X-Left-Out": str(self.left_out)}
        if self.other:  # in the file, but not FHIR resources: in the document and print versions
            h["X-Not-Fhir"] = str(self.other)
        if self.format == "print":  # the record's words, escaped; a style and the Print button, nothing fetched
            h["Content-Security-Policy"] = ("default-src 'none'; style-src 'unsafe-inline'; "
                                            "script-src 'unsafe-inline'; img-src data:")
        return h


def left_out_words(n: int, other: int = 0) -> str:
    """'2 records you may not read were left out.' (and those of types that are not FHIR resources)."""
    out = []
    if n:
        out.append(f"{n} record{'s' if n != 1 else ''} you may not read {'were' if n != 1 else 'was'} left out.")
    if other:
        out.append(f"{other} record{'s' if other != 1 else ''} of a kind that is not a FHIR resource "
                   f"{'are' if other != 1 else 'is'} in the document and print versions only.")
    return " ".join(out)


def safe_filename(base: str, ext: str) -> str:
    """A file name any system takes: letters, digits, dot, dash and underscore. Exports are named by type and id,
    never by whose record it is: a name in a file name is personal information in a downloads folder."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-.")[:80] or "export"
    return f"{stem}.{ext}"


def csv_cell(v) -> str:
    """A value for a CSV cell that a spreadsheet must not read as a formula (=, +, -, @, tab, return first)."""
    if isinstance(v, decimals.Decimal):  # its exact text: 12.30, never 12.3 or 1.23E+1
        return decimals.text(v)
    v = str(v if v is not None else "")
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


# ================================================================================================ FHIR
def _walk(value, parts: list[str], fn):
    """``value`` with ``fn`` applied to what is at ``parts`` (keys, list positions, ``*`` for every item)."""
    if not parts:
        return fn(value)
    head, rest = parts[0], parts[1:]
    if isinstance(value, list):
        if head == "*":
            return [_walk(x, rest, fn) for x in value]
        if head.isdigit() and int(head) < len(value):
            out = list(value)
            out[int(head)] = _walk(value[int(head)], rest, fn)
            return out
        return value
    if isinstance(value, dict) and head in value:
        return {**value, head: _walk(value[head], rest, fn)}
    return value


def fhir_resource(hub, rt, rec: dict) -> dict:
    """A record as a FHIR R4 resource: its own elements (the keys its fields declare), ``resourceType``, ``id`` and
    ``meta.versionId``/``meta.lastUpdated``; references as ``{"reference": "Type/id"}``, a member as a reference to
    the practice's member type, a file the hub keeps as an Attachment (its name, type and size)."""
    if not is_fhir(rt):
        raise Invalid(f"{rt.label if rt else rec.get('resourceType')} records are not FHIR resources")
    keys = {f["path"].split(".")[0] for f in rt.fields}
    out = {"resourceType": rt.id, "id": rec.get("id")}
    meta = {k: (rec.get("meta") or {}).get(k) for k in ("versionId", "lastUpdated") if (rec.get("meta") or {}).get(k)}
    gone = visibility.withheld(rec)
    if gone:  # FHIR's own word for it: the resource was redacted (and the reader is told why)
        meta["security"] = [{"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationValue",
                             "code": "REDACTED", "display": gone["note"]}]
    if meta:
        out["meta"] = meta
    out.update({k: v for k, v in rec.items() if k in keys and not k.startswith("_") and v not in (None, "", [], {})})
    member_type = hub.member_ref_type()

    def as_ref(v):
        if isinstance(v, str) and schema.REF_TEXT.fullmatch(v):
            return {"reference": v}
        return v

    def as_member(v):
        if isinstance(v, str) and v:
            return {"reference": f"{member_type}/{v.removeprefix('member:')}"}
        return v

    def as_number(v):  # FHIR's decimal is a JSON number: the same value (15 digits at most), its trailing zeros gone
        return float(v) if isinstance(v, decimals.Decimal) else v

    def as_attachment(v):
        if isinstance(v, dict) and "blob" in v:
            return {k: x for k, x in (("contentType", v.get("contentType")), ("title", v.get("name")),
                                     ("size", v.get("size"))) if x not in (None, "")}
        return v
    own = [k for k in list(out) if k not in ("resourceType", "id", "meta") and not schema.FHIR_ELEMENT.fullmatch(k)]
    for f in rt.fields + rt.cells():
        fn = {"reference": as_ref, "member": as_member, "attachment": as_attachment, "decimal": as_number}.get(f["type"])
        if fn:
            parts = f["path"].split(".")
            if parts[0] in out:
                out = _walk(out, parts, lambda v, fn=fn: [fn(x) for x in v] if isinstance(v, list) else fn(v))
    if own:  # the practice's own fields (plan_code): FHIR keeps them as extensions, never as unknown elements
        tables = {f["path"] for f in rt.fields if f["type"] == "table"}
        ext = []
        for k in own:
            v = out.pop(k)
            if k in tables and isinstance(v, list):  # a table: one extension for each row, its cells inside it
                ext += [{"url": f"urn:practicehub:{rt.id}:{k}",
                         "extension": [{"url": c, **_extension_value(x)} for c, x in row.items()
                                       if x not in (None, "", [], {})]}
                        for row in v if isinstance(row, dict)]
            else:
                ext.append({"url": f"urn:practicehub:{rt.id}:{k}", **_extension_value(v)})
        out["extension"] = list(out.get("extension") or []) + ext
    return out


def _extension_value(v) -> dict:
    """An extension's value, typed as FHIR types it: a reference (an owner) as a Reference, a yes/no as a boolean, a
    number as a decimal, anything else as text."""
    if isinstance(v, dict) and set(v) <= {"reference", "display"} and v.get("reference"):
        return {"valueReference": v}
    if isinstance(v, bool):
        return {"valueBoolean": v}
    if isinstance(v, (int, float, decimals.Decimal)):
        return {"valueDecimal": float(v) if isinstance(v, decimals.Decimal) else v}
    return {"valueString": v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=decimals.jsonable)}


def plain_json(rec: dict) -> dict:
    """A record of a type that is not a FHIR resource, as the hub keeps it (its type, id and version too)."""
    meta = rec.get("meta") or {}
    return {"resourceType": rec.get("resourceType"), "id": rec.get("id"),
            **({"meta": {k: meta[k] for k in ("versionId", "lastUpdated", "withheld") if k in meta}} if meta else {}),
            **{k: v for k, v in rec.items() if k not in schema.RESERVED and not k.startswith("_")}}


def bundle(resources: list[dict]) -> dict:
    """A FHIR Bundle of type ``collection``: resources kept together, to be read, not a transaction to apply."""
    return {"resourceType": "Bundle", "type": "collection", "timestamp": _fhir_instant(utcnow()),
            "meta": {"lastUpdated": _fhir_instant(utcnow())},
            "entry": [{"fullUrl": f"{r['resourceType']}/{r['id']}", "resource": r} for r in resources]}


def _fhir_instant(at: str) -> str:
    return at if "T" in at else at + "T00:00:00Z"


# ================================================================================================ words
def _item_words(hub, member, f, item) -> str:
    """A value as plain words for a cell: a record by its title (when the member may read it), a member by name."""
    if item in (None, "", [], {}):
        return ""
    if f and f["type"] == "table" and isinstance(item, list):  # a cell holds its count; the record shows the rows
        return f"{len(item)} row" + ("" if len(item) == 1 else "s")
    if isinstance(item, decimals.Decimal):
        return decimals.text(item) + (f" {f['unit']}" if f and f.get("unit") else "")
    if isinstance(item, list):
        return "; ".join(w for w in (_item_words(hub, member, f, x) for x in item) if w)
    if f and f["type"] == "member" and isinstance(item, str):
        mid = item.removeprefix("member:")
        try:
            return hub.member(mid)["name"]
        except (NotFound, KeyError):
            return mid
    if f and f["type"] == "attachment" and isinstance(item, dict):
        return str(item.get("name") or item.get("title") or "file")
    ref = schema.ref_of(item)
    if ref and (not isinstance(item, dict) or set(item) <= {"reference", "display", "type"}):
        return ref_words(hub, member, ref)
    if isinstance(item, bool):
        return "yes" if item else "no"
    if isinstance(item, dict):
        return schema.words(item)
    return str(item)


def ref_words(hub, member, ref: str) -> str:
    t, _, rid = ref.partition("/")
    if t in hub.member_types():
        try:
            return hub.member(rid)["name"]
        except NotFound:
            return ref
    rt = hub.record_type_for_reading(t)
    if rt is None or not hub.may_read(member, t):
        return ref
    rec = hub.records.read(t, rid)
    return f"{hub.title(rec)} ({ref})" if rec else ref


def value_words(hub, member, f, rec) -> str:
    vals = [v for v in schema.get(rec, schema.locate(rec, f["path"], f.get("with"))) if v not in (None, "", [], {})]
    return "; ".join(w for w in (_item_words(hub, member, f, v) for v in vals) if w)


def record_rows(hub, member, rt, rec) -> list[tuple[str, str]]:
    """A record's fields as (label, words), in the order its page shows them, with a value; then what it is."""
    lc = rt.lifecycle
    view = rt.screen("view")
    if view is not None:
        fields = [rt.field(n) for t in view for n in t["fields"] if rt.field(n)]
    else:
        fields = [f for f in rt.fields if f.get("show", True) is not False and not f.get("advanced")
                  and not (lc and f["path"] == lc["path"])
                  and not (f["type"] == "list" and any(g["path"] == f["path"] + ".*" for g in rt.fields))]
    fields = visibility.shown(hub, member, rt, fields)
    rows = []
    gone = visibility.withheld(rec)
    if gone:  # never silently incomplete
        rows.append(("Withheld", gone["note"] + ("" if gone.get("whole") else f": {', '.join(gone.get('fields') or [])}")))
        if gone.get("whole"):
            return rows
    if lc:
        rows.append(("Status", str(schema.first(rec, lc["path"]) or "")))
    for f in fields:
        w = value_words(hub, member, f, rec)
        if w:
            rows.append((f["label"], w))
    meta = rec.get("meta") or {}
    rows.append(("Record", f"{rec.get('resourceType')}/{rec.get('id')} · version "
                           f"{meta.get('versionId', '')} · {str(meta.get('lastUpdated', ''))[:16].replace('T', ' ')}"))
    return rows


def list_table(hub, member, rt, recs) -> tuple[list[str], list[list]]:
    """The list's columns (its title, status and the fields the list shows), then id and dates: header and rows."""
    lc = rt.lifecycle
    cols = visibility.shown(hub, member, rt, schema.list_columns(rt))
    header = [rt.label] + (["Status"] if lc else []) + [f["label"] for f in cols] + ["id", "Date", "Last updated",
                                                                                  "Version"]
    rows = []
    for r in recs:
        meta = r.get("meta") or {}
        row = [hub.title(r)] + ([str(schema.first(r, lc["path"]) or "")] if lc else [])
        for f in cols:
            v = schema.first(r, f["path"]) if f["type"] in ("number", "decimal") and "*" not in f["path"] else None
            row.append(v if isinstance(v, (int, float, decimals.Decimal)) and not isinstance(v, bool)
                       else value_words(hub, member, f, r))
        row += [r.get("id", ""), schema.record_date(rt, r), meta.get("lastUpdated", ""), meta.get("versionId", "")]
        rows.append(row)
    return header, rows


# ================================================================================================ documents
# A document is a list of blocks, drawn as OpenDocument text or as a print page:
#   ("h1" | "h2" | "p" | "note", words), ("kv", [(label, words)]), ("table", header, rows)
def _x(text) -> str:
    """Text for XML: escaped, and without the control characters XML does not allow."""
    return _xml_escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]", "", str(text)), {'"': "&quot;"})


NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
      'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
      'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
      'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
      'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
      'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
      'xmlns:dc="http://purl.org/dc/elements/1.1/"')


def _paras(text, style: str = "") -> str:
    st = f' text:style-name="{style}"' if style else ""
    return "".join(f"<text:p{st}>{_x(line)}</text:p>" for line in (str(text).split("\n") or [""]))


def _cell(v, style: str = "C", bold: bool = False) -> str:
    if isinstance(v, (int, float, decimals.Decimal)) and not isinstance(v, bool):
        t = decimals.text(v) if isinstance(v, decimals.Decimal) else v  # exact, as written: 12.30
        return (f'<table:table-cell table:style-name="{style}" office:value-type="float" office:value="{t}">'
                f"<text:p>{t}</text:p></table:table-cell>")
    return (f'<table:table-cell table:style-name="{style}" office:value-type="string">'
            + _paras(v, "B" if bold else "") + "</table:table-cell>")


def _odf(mimetype: str, content: str, title: str) -> bytes:
    """An OpenDocument package: ``mimetype`` first and stored (not compressed), so a reader knows it from its first
    bytes; then the content, styles, meta and the manifest that lists them."""
    styles = f'<?xml version="1.0" encoding="UTF-8"?>\n<office:document-styles {NS} office:version="1.3"/>'
    meta = (f'<?xml version="1.0" encoding="UTF-8"?>\n<office:document-meta {NS} office:version="1.3"><office:meta>'
            f"<meta:generator>Practice Hub</meta:generator><dc:title>{_x(title)}</dc:title>"
            f"<dc:date>{_x(utcnow())}</dc:date></office:meta></office:document-meta>")
    manifest = ('<?xml version="1.0" encoding="UTF-8"?>\n<manifest:manifest '
                'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.3">'
                f'<manifest:file-entry manifest:full-path="/" manifest:version="1.3" manifest:media-type="{mimetype}"/>'
                + "".join(f'<manifest:file-entry manifest:full-path="{p}" manifest:media-type="text/xml"/>'
                          for p in ("content.xml", "styles.xml", "meta.xml")) + "</manifest:manifest>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype", (1980, 1, 1, 0, 0, 0)), mimetype, compress_type=zipfile.ZIP_STORED)
        for name, text in (("content.xml", content), ("styles.xml", styles), ("meta.xml", meta),
                           ("META-INF/manifest.xml", manifest)):
            z.writestr(zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)), text.encode(), compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()


ODT_STYLES = ('<office:automatic-styles>'
              '<style:style style:name="B" style:family="paragraph"><style:text-properties fo:font-weight="bold"/>'
              '</style:style>'
              '<style:style style:name="N" style:family="paragraph"><style:text-properties fo:color="#505f79" '
              'fo:font-size="9pt"/></style:style>'
              '<style:style style:name="T" style:family="table"><style:table-properties style:width="17cm" '
              'table:align="left" fo:margin-bottom="0.4cm"/></style:style>'
              '<style:style style:name="K" style:family="table-column"><style:table-column-properties '
              'style:column-width="5cm"/></style:style>'
              '<style:style style:name="V" style:family="table-column"><style:table-column-properties '
              'style:column-width="12cm"/></style:style>'
              '<style:style style:name="C" style:family="table-cell"><style:table-cell-properties fo:padding="0.1cm" '
              'fo:border="0.5pt solid #c1c7d0"/></style:style>'
              '<style:style style:name="H" style:family="table-cell"><style:table-cell-properties fo:padding="0.1cm" '
              'fo:background-color="#f1f2f4" fo:border="0.5pt solid #c1c7d0"/></style:style>'
              '</office:automatic-styles>')


def odt(title: str, blocks: list[tuple]) -> bytes:
    """An OpenDocument text: headings, paragraphs, and each record's fields as a two-column labelled table."""
    out, n = [], 0
    for b in blocks:
        kind = b[0]
        if kind in ("h1", "h2"):
            out.append(f'<text:h text:outline-level="{kind[1]}">{_x(b[1])}</text:h>')
        elif kind in ("p", "note"):
            out.append(_paras(b[1], "N" if kind == "note" else ""))
        elif kind == "kv":
            n += 1
            out.append(f'<table:table table:name="Table{n}" table:style-name="T">'
                       '<table:table-column table:style-name="K"/><table:table-column table:style-name="V"/>'
                       + "".join(f"<table:table-row>{_cell(k, 'H', True)}{_cell(v)}</table:table-row>" for k, v in b[1])
                       + "</table:table>")
        elif kind == "table":
            n += 1
            head, rows = b[1], b[2]
            out.append(f'<table:table table:name="Table{n}" table:style-name="T">'
                       f'<table:table-column table:number-columns-repeated="{len(head)}"/>'
                       f'<table:table-header-rows><table:table-row>{"".join(_cell(h, "H", True) for h in head)}'
                       f'</table:table-row></table:table-header-rows>'
                       + "".join(f"<table:table-row>{''.join(_cell(v) for v in r)}</table:table-row>" for r in rows)
                       + "</table:table>")
    content = (f'<?xml version="1.0" encoding="UTF-8"?>\n<office:document-content {NS} office:version="1.3">'
               f'{ODT_STYLES}<office:body><office:text>{"".join(out)}</office:text></office:body>'
               f'</office:document-content>')
    return _odf(ODT, content, title)


def ods(sheet: str, header: list[str], rows: list[list]) -> bytes:
    """An OpenDocument spreadsheet of one sheet. Every text is a text cell, never a formula; numbers are numbers."""
    name = re.sub(r"[\[\]*?:/\\']", " ", sheet).strip()[:31] or "Sheet1"
    body = (f'<table:table table:name="{_x(name)}"><table:table-column table:number-columns-repeated="{len(header)}"/>'
            f'<table:table-header-rows><table:table-row>{"".join(_cell(h, "H", True) for h in header)}'
            f'</table:table-row></table:table-header-rows>'
            + "".join(f"<table:table-row>{''.join(_cell(v) for v in r)}</table:table-row>" for r in rows)
            + "</table:table>")
    content = (f'<?xml version="1.0" encoding="UTF-8"?>\n<office:document-content {NS} office:version="1.3">'
               f'{ODT_STYLES}<office:body><office:spreadsheet>{body}</office:spreadsheet></office:body>'
               f'</office:document-content>')
    return _odf(ODS, content, sheet)


def csv_bytes(header: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([csv_cell(h) for h in header])
    for r in rows:
        w.writerow([csv_cell(v) for v in r])
    return buf.getvalue().encode("utf-8")


PRINT_CSS = """
:root{color-scheme:light;--fg:#172b4d;--muted:#505f79;--line:#c1c7d0;--head:#f1f2f4}
body{font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;color:var(--fg);
background:#fff;margin:0 auto;max-width:900px;padding:24px 16px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:24px 0 8px;break-after:avoid}
p.note{color:var(--muted);font-size:12px;margin:4px 0}
table{border-collapse:collapse;width:100%;margin:0 0 12px;break-inside:auto}
th,td{border:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top;white-space:pre-wrap;
overflow-wrap:anywhere}
th{background:var(--head);font-weight:600}table.kv th{width:30%}
tr{break-inside:avoid}.bar{display:flex;gap:8px;justify-content:flex-end;margin-bottom:16px}
.bar button{font:inherit;padding:6px 12px;border-radius:3px;border:1px solid var(--line);background:#0c66e4;
color:#fff;cursor:pointer}
@media print{.bar{display:none}body{padding:0;max-width:none;font-size:11pt}@page{margin:18mm 15mm}
thead{display:table-header-group}}
"""


def print_html(title: str, blocks: list[tuple]) -> str:
    """A page made to print: the browser's print dialog saves it as a PDF. No script but the Print button's."""
    e = html.escape
    out = []
    for b in blocks:
        kind = b[0]
        if kind in ("h1", "h2"):
            out.append(f"<{kind}>{e(str(b[1]))}</{kind}>")
        elif kind == "p":
            out.append(f"<p>{e(str(b[1]))}</p>")
        elif kind == "note":
            out.append(f'<p class="note">{e(str(b[1]))}</p>')
        elif kind == "kv":
            out.append('<table class="kv">' + "".join(f'<tr><th scope="row">{e(str(k))}</th><td>{e(str(v))}</td></tr>'
                                                     for k, v in b[1]) + "</table>")
        elif kind == "table":
            out.append("<table><thead><tr>" + "".join(f'<th scope="col">{e(str(h))}</th>' for h in b[1])
                       + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{e(str(v))}</td>" for v in r)
                                                          + "</tr>" for r in b[2]) + "</tbody></table>")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{e(title)}</title><style>{PRINT_CSS}</style>'
            f'</head><body><div class="bar"><button type="button" onclick="window.print()">Print or save as PDF'
            f'</button></div>{"".join(out)}</body></html>')


# ================================================================================================ the rules
LOG_BATCH = 500  # record.viewed events written per transaction by an export (about a second's work)


def _log(hub, member, refs: list[str], fmt: str, *, what: str | None = None, data: dict | None = None) -> None:
    """Each record exported is a read on the track, every time (an export is not folded into an earlier view); an
    export of a list or a file is an event of its own.

    The reads are written LOG_BATCH at a time, each batch a transaction of its own (run again if another writer got
    in first), so an export of the whole hub never becomes one transaction past MongoDB's time limit, and other work
    goes on between batches. Every read is on the track before the export is handed out; an export that stops part
    way leaves the reads it wrote (more recorded than was seen, never less), and the event for the export, written
    last, says it was whole. Each read is an event of its own, as any other read is, so a patient's access report
    finds it by the record."""
    for i in range(0, len(refs), LOG_BATCH):
        batch = refs[i:i + LOG_BATCH]

        def write(batch=batch):
            for ref in batch:
                hub.track.append(member, "record.viewed", ref, data={"how": "export", "format": fmt})
        hub.db.atomic(write)
    if what is not None:
        hub.track.append(member, "records.exported", what,
                         data={"how": "export", "format": fmt, "count": len(refs), **(data or {})})


def _check_format(fmt: str, allowed: list[str], what: str) -> None:
    if fmt not in allowed:
        names = ", ".join(allowed)
        if fmt in ("fhir", "ndjson"):
            raise Invalid(f"{what} is not a FHIR resource, so it has no FHIR export; it exports to {names}")
        raise Invalid(f"{what} exports to {names}, not {fmt!r}")


def _stamp(hub, member) -> str:
    return (f"Exported by {member.get('name') or member['id']} on {utcnow()[:16].replace('T', ' ')} UTC from "
            f"{hub.practice.get('name') or 'the hub'}. Every record in it was recorded as read.")


def record(hub, member, ref: str, fmt: str) -> Export:
    """One record. The member must be able to read it (else Denied, as opening it is)."""
    rtype, _, rid = ref.partition("/")
    rt = hub.record_type_for_reading(rtype)
    if rt is None or rtype in hub.member_types():
        raise NotFound(f"there is no {ref}")
    _check_format(fmt, offered(hub, rtype, "record"), f"{a_label(rt)}")
    hub.require(member, "reader", f"data_class:{rt.data_class}", f"read {rtype} records")
    rec = hub.redact(member, hub.records.read(rtype, rid))  # without the fields they may not read
    if not rec:
        raise NotFound(f"there is no {ref}")
    base = f"{rtype}-{rid}"
    if fmt == "fhir":
        body = json.dumps(fhir_resource(hub, rt, rec), indent=1, ensure_ascii=False, default=decimals.jsonable).encode()
    elif fmt == "json":
        body = json.dumps(plain_json(rec), indent=1, ensure_ascii=False, default=decimals.jsonable).encode()
    else:
        title = hub.title(rec)
        blocks = [("h1", title), ("note", f"{rt.label} · {_stamp(hub, member)}"), ("kv", record_rows(hub, member, rt, rec))]
        body = odt(title, blocks) if fmt == "odt" else print_html(title, blocks).encode()
    _log(hub, member, [ref], fmt)
    return Export(body, fmt, safe_filename(base, FORMATS[fmt][2]), 1, 0)


def a_label(rt) -> str:
    return f"{rt.label} ({rt.id})" if rt.label != rt.id else rt.id


def records_list(hub, member, rtype: str, fmt: str, *, text: str | None = None, limit: int | None = 200,
                 every: bool = False) -> Export:
    """A list of a type's records, as its list page finds them (``text``, the ``limit`` most recently changed).
    ``every``: every record of the type, a bulk export, for a policy owner only."""
    rt = hub.record_type_for_reading(rtype)
    if rt is None or rt.abstract or rtype in hub.member_types():
        raise NotFound(f"there is no record type {rtype}")
    _check_format(fmt, offered(hub, rtype, "list"), a_label(rt))
    if every:
        hub.require(member, "policy_owner", "practice:main", f"export every {rt.label.lower()} record (a bulk export)")
    hub.require(member, "reader", f"data_class:{rt.data_class}", f"read {rtype} records")
    recs = hub.search_records(member, rtype, limit=None if every else limit, text=None if every else (text or None))
    refs = [f"{rtype}/{r['id']}" for r in recs]
    stamp = utcnow()[:10]
    if fmt == "ndjson":
        body = "".join(json.dumps(fhir_resource(hub, rt, r), ensure_ascii=False, default=decimals.jsonable) + "\n" for r in recs).encode()
    else:
        header, rows = list_table(hub, member, rt, recs)
        if fmt == "csv":
            body = csv_bytes(header, rows)
        elif fmt == "ods":
            body = ods(rt.plural, header, rows)
        else:
            what = rt.plural + (f' matching "{text}"' if text and not every else "")
            body = print_html(rt.plural, [("h1", what), ("note", f"{len(recs)} records. {_stamp(hub, member)}"),
                                          ("table", header, rows)]).encode()
    _log(hub, member, refs, fmt, what=f"records:{rtype}",
         data={"type": rtype, "bulk": every, **({"search": text} if text and not every else {}), "left_out": 0})
    return Export(body, fmt, safe_filename(f"{rtype}-{'all' if every else 'list'}-{stamp}", FORMATS[fmt][2]),
                  len(recs), 0)


def gather(hub, member, ref: str) -> tuple[list[dict], int]:
    """A subject's whole file, as the member may read it: the subject, every record about it (its ``subject``), the
    records that link to it, and the parts of each of those (a visit's notes). The records the member may not read
    are counted, once each. Nothing is logged here: the export that uses it logs what it holds."""
    rtype, _, rid = ref.partition("/")
    if rtype not in hub.subject_types():
        raise Invalid(f"{rtype} is not what the practice's work is about; export one record instead")
    hub.require(member, "reader", f"data_class:{hub.class_of(rtype)}", f"read {rtype} records")
    subject = hub.redact(member, hub.records.read(rtype, rid))
    if not subject:
        raise NotFound(f"there is no {ref}")
    members = hub.member_types()
    got: dict[str, dict] = {ref: subject}
    hidden: set[str] = set()
    readable: dict[str, bool] = {}

    def may(t: str) -> bool:
        if t not in readable:
            rt = hub.record_type_for_reading(t)
            readable[t] = bool(rt) and not rt.abstract and hub.may_read(member, t)
        return readable[t]

    def take(r: str, rec: dict | None = None) -> bool:
        t = r.split("/", 1)[0]
        if r in got or r in hidden or t in members or not hub.record_type_for_reading(t):
            return False
        if not may(t):
            hidden.add(r)
            return False
        rec = rec or hub.records.read(*r.split("/", 1))
        if rec:
            got[r] = hub.redact(member, rec)
            return True
        return False
    for rec in hub.records.search(subject=ref, limit=None):
        take(f"{rec['resourceType']}/{rec['id']}", rec)
    for x in hub.db.links.find({"to_ref": ref}, fields=["from_ref"]):
        take(x["from_ref"])
    todo, depth = list(got), 0
    while todo and depth < 4:  # the parts of what is in the file (a visit's notes, a note's addenda)
        nxt = []
        for owner in todo:
            for pt in hub.part_types(owner.split("/", 1)[0]):
                for x in hub.db.links.find({"to_ref": owner, "from_ref": {"$regex": f"^{re.escape(pt.id)}/"}},
                                           fields=["from_ref"]):
                    if take(x["from_ref"]):
                        nxt.append(x["from_ref"])
        todo, depth = nxt, depth + 1

    def kind(t: str):  # the subject first, then each kind by where it sits on the subject's page
        rt = hub.record_type_for_reading(t)
        return (t != rtype, rt.order if rt else 100, t)
    out: list[dict] = [subject]
    for t in sorted({r.split("/", 1)[0] for r in got if r != ref}, key=kind):  # newest first within a kind
        rt = hub.record_type_for_reading(t)
        out += sorted((got[r] for r in got if r != ref and r.split("/", 1)[0] == t),
                      key=lambda x: schema.record_date(rt, x), reverse=True)
    return out, len(hidden)


def subject_file(hub, member, ref: str, fmt: str) -> Export:
    """A subject's whole file (``gather``): a FHIR Bundle (``$everything``), or a document or print view of every
    record in it. In a FHIR Bundle, records whose type is not a FHIR resource are counted and left to the others."""
    rtype, _, rid = ref.partition("/")
    rt = hub.record_type_for_reading(rtype)
    if rt is None:
        raise NotFound(f"there is no {ref}")
    _check_format(fmt, offered(hub, rtype, "file"), f"{a_label(rt)}'s file")
    recs, hidden = gather(hub, member, ref)
    other = 0
    if fmt == "fhir":
        res = []
        for r in recs:
            t = hub.record_type_for_reading(r["resourceType"])
            if is_fhir(t):
                res.append(fhir_resource(hub, t, r))
            else:
                other += 1
        kept = [f"{r['resourceType']}/{r['id']}" for r in res]
        body = json.dumps(bundle(res), indent=1, ensure_ascii=False, default=decimals.jsonable).encode()
    elif fmt == "json":
        kept = [f"{r['resourceType']}/{r['id']}" for r in recs]
        body = json.dumps({"subject": ref, "records": [plain_json(r) for r in recs]}, indent=1,
                          ensure_ascii=False, default=decimals.jsonable).encode()
    else:
        kept = [f"{r['resourceType']}/{r['id']}" for r in recs]
        title = hub.title(recs[0])
        blocks = [("h1", title), ("note", f"The {rt.label.lower()}'s file: {len(recs)} records. {_stamp(hub, member)}")]
        if hidden:
            blocks.append(("note", left_out_words(hidden)))
        gap = sum(1 for r in recs if visibility.withheld(r))
        if gap:
            blocks.append(("note", f"Incomplete: {hub.restrictions.note(ref).lower()} "
                                   f"({gap} record{'s' if gap != 1 else ''} shown without what is withheld)."))
        for r in recs:
            t = hub.record_type_for_reading(r["resourceType"])
            blocks += [("h2", f"{t.label}: {hub.title(r)}" if r is not recs[0] else f"{t.label}"),
                       ("kv", record_rows(hub, member, t, r))]
        body = odt(title, blocks) if fmt == "odt" else print_html(title, blocks).encode()
    _log(hub, member, kept, fmt, what=ref, data={"type": rtype, "scope": "file", "left_out": hidden,
                                                 "not_fhir": other,
                                                 "withheld": sum(1 for r in recs if visibility.withheld(r))})
    return Export(body, fmt, safe_filename(f"{rtype}-{rid}-file", FORMATS[fmt][2]), len(kept), hidden, other)


def everything(hub, member, fmt: str = "ndjson") -> tuple[dict[str, bytes], dict]:
    """The whole hub, for a policy owner: one file per record type the owner may read (FHIR NDJSON for a FHIR type,
    JSON lines in the hub's own shape for the others; or ``csv``), and a manifest in the shape of a FHIR Bulk Data
    export's. Types the owner may not read are left out and counted."""
    hub.require(member, "policy_owner", "practice:main", "export the whole hub (a bulk export)")
    if fmt not in ("ndjson", "csv"):
        raise Invalid("the whole hub exports as ndjson or csv")
    files: dict[str, bytes] = {}
    output, left_out, refs = [], [], []
    for t, rt in hub.record_types().items():
        if rt.abstract or t in hub.member_types():
            continue
        n = hub.db.records.count({"type": t})
        if not hub.may_read(member, t):
            if n:
                left_out.append({"type": t, "count": n})
            continue
        recs = hub.redact_all(member, hub.records.search(t, limit=None))
        if not recs:
            continue
        if fmt == "csv":
            header, rows = list_table(hub, member, rt, recs)
            name, body = f"{t}.csv", csv_bytes(header, rows)
        else:
            name = f"{t}.ndjson"
            body = "".join(json.dumps(fhir_resource(hub, rt, r) if is_fhir(rt) else plain_json(r),
                                      ensure_ascii=False, default=decimals.jsonable) + "\n" for r in recs).encode()
        files[safe_filename(name.rsplit(".", 1)[0], name.rsplit(".", 1)[1])] = body
        output.append({"type": t, "url": name, "count": len(recs), "fhir": is_fhir(rt)})
        refs += [f"{t}/{r['id']}" for r in recs]
    manifest = {"transactionTime": _fhir_instant(utcnow()), "requiresAccessToken": False, "output": output,
                "error": [], "left_out": left_out, "exported_by": member["id"],
                "practice": hub.practice.get("name")}
    _log(hub, member, refs, fmt, what="records:*", data={"bulk": True, "scope": "hub",
                                                        "left_out": sum(x["count"] for x in left_out)})
    files["manifest.json"] = json.dumps(manifest, indent=1, default=decimals.jsonable).encode()
    return files, manifest


__all__ = ["FORMATS", "Export", "offered", "is_fhir", "fhir_resource", "bundle", "record", "records_list",
           "subject_file", "gather", "everything", "csv_cell", "safe_filename", "odt", "ods", "print_html",
           "left_out_words"]
