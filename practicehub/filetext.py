"""The words of a stored file, as plain text, with the standard library only.

A file's text is what a member is shown about a file without opening it: an agent asked about a document reads it
here. OpenDocument (``.odt``, ``.ods``, ``.odp``) and Word (``.docx``) files are zip archives of XML, read with
``zipfile`` and ``xml.etree``; a plain text file (``.txt``, ``.csv``, ``.md``, any ``text/*``) is its own text.
Anything else has no text here: :func:`text_of` returns None and the caller says "text not available".

Nothing here runs or follows what a file says: no entities, no external references (a file declaring a DTD is not
read), and no more than :data:`MAX_XML` bytes of XML are inflated, so a small archive cannot grow without end.
"""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

MAX_XML = 20 * 1024 * 1024  # bytes of XML inflated from one archive
MAX_TEXT = 200_000  # characters handed on; the rest is cut, and said to be
PLAIN = {"txt", "csv", "tsv", "md", "markdown", "text"}
ODF = {"odt", "ods", "odp", "odg"}

_ODF_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_ODF_TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def extension(name: str) -> str:
    n = (name or "").rsplit("/", 1)[-1]
    return n.rsplit(".", 1)[-1].lower() if "." in n else ""


def _xml(zf: zipfile.ZipFile, member: str):
    try:
        info = zf.getinfo(member)
    except KeyError:
        return None
    if info.file_size > MAX_XML:
        return None
    with zf.open(info) as fh:
        raw = fh.read(MAX_XML + 1)
    if len(raw) > MAX_XML or b"<!DOCTYPE" in raw[:4096] or b"<!ENTITY" in raw:
        return None
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        return None


def _odf_text(root) -> str:
    """Paragraphs and headings, in order; a table's cells on one line, separated by tabs."""
    T = "{%s}" % _ODF_TEXT
    out: list[str] = []

    def inline(el) -> str:
        s = [el.text or ""]
        for c in el:
            if c.tag == T + "s":
                s.append(" " * int(c.get(T + "c", "1") or 1))
            elif c.tag == T + "tab":
                s.append("\t")
            elif c.tag == T + "line-break":
                s.append("\n")
            elif c.tag in (T + "note", "{%s}annotation" % "urn:oasis:names:tc:opendocument:xmlns:office:1.0"):
                pass  # footnotes and comments in the file are not its text
            else:
                s.append(inline(c))
            s.append(c.tail or "")
        return "".join(s)

    def walk(el) -> None:
        for c in el:
            if c.tag in (T + "p", T + "h"):
                out.append(inline(c))
            elif c.tag == "{%s}table-row" % _ODF_TABLE:
                cells = ["\n".join(inline(p) for p in cell.iter() if p.tag in (T + "p", T + "h"))
                         for cell in c if cell.tag.endswith("}table-cell")]
                while cells and not cells[-1]:
                    cells.pop()
                if cells:
                    out.append("\t".join(cells))
            else:
                walk(c)

    walk(root)
    return "\n".join(out)


def _docx_text(root) -> str:
    W = "{%s}" % _W
    out: list[str] = []
    for p in root.iter(W + "p"):
        s = []
        for el in p.iter():
            if el.tag == W + "t":
                s.append(el.text or "")
            elif el.tag == W + "tab":
                s.append("\t")
            elif el.tag in (W + "br", W + "cr"):
                s.append("\n")
        out.append("".join(s))
    return "\n".join(out)


def text_of(data: bytes, name: str = "", content_type: str = "") -> str | None:
    """The file's words, or None when this kind of file has no text here (or it cannot be read)."""
    ext = extension(name)
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    text = None
    if ext in ODF or ext == "docx" or ctype.startswith("application/vnd.oasis.opendocument") or \
            ctype == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except (zipfile.BadZipFile, ValueError):
            return None
        with zf:
            if ext == "docx" or "wordprocessingml" in ctype:
                root = _xml(zf, "word/document.xml")
                text = _docx_text(root) if root is not None else None
            else:
                root = _xml(zf, "content.xml")
                body = root.find("{urn:oasis:names:tc:opendocument:xmlns:office:1.0}body") if root is not None else None
                text = _odf_text(body) if body is not None else None
    elif ext in PLAIN or ctype.startswith("text/"):
        if b"\x00" in data[:8192]:
            return None
        text = data.decode("utf-8-sig", errors="replace")
    if text is None:
        return None
    text = re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n")).strip()
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + "\n[the rest of the file is not shown]"
    return text
