"""The words of a stored file, as plain text.

A file's text is what a member is shown about a file without opening it: an agent asked about a document reads it
here, and a field ``from_file`` an attachment is filled with it. OpenDocument (``.odt``, ``.ods``, ``.odp``) and Word
(``.docx``) files are zip archives of XML, read with ``zipfile`` and ``xml.etree``; a plain text file (``.txt``,
``.csv``, ``.md``, any ``text/*``) is its own text; a PDF's words are its text layer, read with pypdf (the one place
the hub uses it), from at most :data:`MAX_PAGES` pages. A PDF with no text layer (a scan) and a picture have no text
here: :func:`text_of` returns None and the caller says "text not available". A picture is for a model that can see
(:func:`picture_type`).

Nothing here runs or follows what a file says: no entities, no external references (a file declaring a DTD is not
read), and no more than :data:`MAX_XML` bytes of XML are inflated, so a small archive cannot grow without end. A PDF
is only read for its text: no scripts, forms or attachments in it are opened, and any error reading it is "no text".
"""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

MAX_XML = 20 * 1024 * 1024  # bytes of XML inflated from one archive
MAX_TEXT = 200_000  # characters handed on; the rest is cut, and said to be
MAX_PAGES = 50  # pages of a PDF read for their words
MAX_PICTURE = 10 * 1024 * 1024  # bytes of a picture shown to a model
PICTURES = (("image/png", b"\x89PNG\r\n\x1a\n"), ("image/jpeg", b"\xff\xd8\xff"))
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


def _pdf_text(data: bytes) -> str | None:
    """The text layer of the first :data:`MAX_PAGES` pages, page after page; None for a scan or a file pypdf cannot
    read (a password, a broken file)."""
    import logging
    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)  # a malformed file is "no text", not a log of warnings
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            return None
        pages = [p.extract_text() or "" for p in reader.pages[:MAX_PAGES]]
    except Exception:  # pypdf raises many kinds for a file it cannot read: each is "this file has no text here"
        return None
    text = "\n\n".join(p.strip() for p in pages if p.strip())
    return text or None


def picture_type(data: bytes, content_type: str = "") -> str | None:
    """The picture's type (PNG, JPEG or WebP, by its first bytes, not its name) when it may be shown to a model that
    can see; None for anything else, or a picture over :data:`MAX_PICTURE`."""
    if not data or len(data) > MAX_PICTURE:
        return None
    for ctype, magic in PICTURES:
        if data.startswith(magic):
            return ctype
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


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
    elif data[:5] == b"%PDF-":  # by its first bytes, whatever it is called
        text = _pdf_text(data)
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
