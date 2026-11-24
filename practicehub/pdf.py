"""Formatted text (a ``markdown`` field) as a PDF: a fixed copy of a page, to put under document control or to send.

Only the standard library. The PDF uses the fonts every reader has (Helvetica, Helvetica-Bold, Helvetica-Oblique and
Courier), in WinAnsi encoding: a character those fonts do not have is written as ``?``. What it keeps of the text:

    headings (larger, bold), paragraphs (wrapped), bulleted and numbered lists, task lists ([x] / [ ]),
    quotations and callouts (indented, italic), code (Courier, as written), tables (one line a row, cells apart),
    rules; **bold**, *italic* and links become their words; [[Type/id|words]] becomes its words

Every page has a header (the title) and a footer (what the copy is, and "page n of m"). The same text, title and
footer always make the same bytes: there is no creation time in the file, so a copy is checked by its SHA-256.
"""

from __future__ import annotations

import re

PAGE_W, PAGE_H = 595.28, 841.89          # A4, in points
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 56.0, 64.0, 60.0
BODY = 10.5
LEADING = 1.38
FONTS = {"R": "Helvetica", "B": "Helvetica-Bold", "I": "Helvetica-Oblique", "C": "Courier"}
# average advance of a character, in ems: Courier is exact; Helvetica is generous, so a wrapped line never runs over
_EM = {"R": 0.54, "B": 0.59, "I": 0.54, "C": 0.6}
_HEAD = {1: 18.0, 2: 14.0, 3: 12.0}


def _inline(s: str) -> str:
    """The words of a line of formatted text, without its marks."""
    s = re.sub(r"\[\[([^\[\]\n|]{1,200})\|([^\[\]\n]{1,200})\]\]", r"\2", s)
    s = re.sub(r"\[\[([^\[\]\n]{1,200})\]\]", r"\1", s)  # [[Its title]] reads as its title
    s = re.sub(r"\[([^\[\]\n]{1,300})\]\(([^()\s]{1,2000})\)",
               lambda m: m.group(1) if m.group(2).startswith("/") else f"{m.group(1)} ({m.group(2)})", s)
    s = re.sub(r"\*\*([^*\n]+?)\*\*", r"\1", s)
    s = re.sub(r"~~([^~\n]+?)~~", r"\1", s)
    s = re.sub(r"(?<![\w*])\*([^*\s][^*\n]*?)\*(?![\w*])", r"\1", s)
    s = re.sub(r"`([^`\n]+)`", r"\1", s)
    return s


def blocks(text: str) -> list[tuple[str, str, int]]:
    """The text as (kind, words, level): kind is h, p, li, quote, code, row, rule or gap."""
    out: list[tuple[str, str, int]] = []
    lines = (text or "").replace("\r\n", "\n").split("\n")
    para: list[str] = []

    def flush():
        if para:
            out.append(("p", _inline(" ".join(x.strip() for x in para)), 0))
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        st = line.strip()
        if st.startswith("```"):
            flush()
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                out.append(("code", lines[i].rstrip().expandtabs(4), 0))
                i += 1
            out.append(("gap", "", 0))
            i += 1
            continue
        if not st:
            flush()
            i += 1
            continue
        m = re.match(r"(#{1,3})\s+(.*)$", st)
        if m:
            flush()
            out.append(("h", _inline(m.group(2).strip()), len(m.group(1))))
        elif re.fullmatch(r"-{3,}|\*{3,}|_{3,}", st):
            flush()
            out.append(("rule", "", 0))
        elif st.startswith(">"):
            flush()
            words = re.sub(r"^>\s?", "", st)
            words = re.sub(r"^\[!(\w+)\]\s*", lambda m: m.group(1).capitalize() + ": ", words)
            if words:
                out.append(("quote", _inline(words), 0))
        elif st.startswith("|"):
            flush()
            cells = [c.strip() for c in st.strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                out.append(("row", "    ".join(_inline(c) for c in cells), 0))
        else:
            m = re.match(r"(\s*)(?:([-*+])|(\d{1,3})[.)])\s+(.*)$", line)
            if m:
                flush()
                level = len(m.group(1).expandtabs(4)) // 2
                words = m.group(4)
                task = re.match(r"\[([ xX])\]\s+(.*)$", words)
                if task:
                    mark = "[x]" if task.group(1).lower() == "x" else "[ ]"
                    words = task.group(2)
                else:
                    mark = "-" if m.group(2) else f"{m.group(3)}."
                out.append(("li", f"{mark} {_inline(words)}", level))
            else:
                para.append(line)
        i += 1
    flush()
    return out


def _wrap(words: str, width: float, size: float, font: str) -> list[str]:
    per = max(8, int(width / (size * _EM[font])))
    lines, cur = [], ""
    for w in words.split(" "):
        while len(w) > per:  # a word longer than the line is broken
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(w[:per])
            w = w[per:]
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= per:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur or not lines:
        lines.append(cur)
    return lines


def _esc(s: str) -> bytes:
    b = s.encode("cp1252", errors="replace")
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)").replace(b"\r", b"")


def render(title: str, text: str, footer: str = "") -> bytes:
    """A PDF of the formatted ``text``, headed ``title`` on every page; ``footer`` says what the copy is."""
    width = PAGE_W - 2 * MARGIN_X
    pages: list[list[tuple[str, float, float, float, str]]] = []  # per page: (font, size, x, y, words); "rule" lines
    page: list = []
    y = PAGE_H - MARGIN_TOP

    def new_page():
        nonlocal page, y
        page = []
        pages.append(page)
        y = PAGE_H - MARGIN_TOP

    def room(h: float):
        if y - h < MARGIN_BOTTOM:
            new_page()

    def put(lines, font, size, indent=0.0, before=0.0, after=0.0):
        nonlocal y
        step = size * LEADING
        first = True
        for ln in lines:
            room(step + (before if first else 0))
            if first and page:
                y -= before
            y -= step
            page.append((font, size, MARGIN_X + indent, y, ln))
            first = False
        y -= after

    new_page()
    put(_wrap(title, width, _HEAD[1], "B"), "B", _HEAD[1], after=6)
    for kind, words, level in blocks(text):
        if kind == "h":
            size = _HEAD.get(level, BODY)
            room(size * LEADING * 3)  # a heading is not left alone at the foot of a page
            put(_wrap(words, width, size, "B"), "B", size, before=8, after=2)
        elif kind == "p":
            put(_wrap(words, width, BODY, "R"), "R", BODY, after=5)
        elif kind == "li":
            ind = 14.0 + 14.0 * level
            put(_wrap(words, width - ind, BODY, "R"), "R", BODY, indent=ind, after=1)
        elif kind == "quote":
            put(_wrap(words, width - 24, BODY, "I"), "I", BODY, indent=24, after=3)
        elif kind == "code":
            put(_wrap(words or " ", width - 12, BODY - 1, "C"), "C", BODY - 1, indent=12)
        elif kind == "row":
            put(_wrap(words, width, BODY, "R"), "R", BODY, after=1)
        elif kind == "rule":
            room(14)
            y -= 7
            page.append(("rule", 0.0, MARGIN_X, y, ""))
            y -= 7
        elif kind == "gap":
            y -= 5

    n = len(pages)
    objs: list[bytes] = []  # object k is objs[k - 1]
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(b"")  # the page tree, written once the pages are numbered
    font_ids = {}
    for key, name in FONTS.items():
        objs.append(f"<< /Type /Font /Subtype /Type1 /BaseFont /{name} /Encoding /WinAnsiEncoding >>".encode())
        font_ids[key] = len(objs)
    fonts = " ".join(f"/F{k} {v} 0 R" for k, v in font_ids.items()).encode()
    kids = []
    head = _wrap(title, width, 8, "R")[0]
    for num, items in enumerate(pages, 1):
        ops = [b"0.4 g", b"BT /FR 8 Tf", f"{MARGIN_X:.2f} {PAGE_H - 36:.2f} Td".encode(),
               b"(" + _esc(head) + b") Tj ET"]
        foot = f"{footer}    Page {num} of {n}" if footer else f"Page {num} of {n}"
        for i, ln in enumerate(_wrap(foot, width, 8, "R")[:2]):
            ops += [b"BT /FR 8 Tf", f"{MARGIN_X:.2f} {34 - 10 * i:.2f} Td".encode(), b"(" + _esc(ln) + b") Tj ET"]
        ops.append(b"0 g")
        for font, size, x, yy, words in items:
            if font == "rule":
                ops.append(f"0.7 G 0.5 w {x:.2f} {yy:.2f} m {PAGE_W - MARGIN_X:.2f} {yy:.2f} l S 0 G".encode())
                continue
            ops += [f"BT /F{font} {size:g} Tf {x:.2f} {yy:.2f} Td".encode(), b"(" + _esc(words) + b") Tj ET"]
        stream = b"\n".join(ops)
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        content = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] /Contents {content} 0 R "
                    f"/Resources << /Font << ".encode() + fonts + b" >> >> >>")
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {n} >>".encode()
    objs.append(b"<< /Title (" + _esc(title) + b") /Producer (Practice Hub) >>")
    info = len(objs)

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for k, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{k} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info {info} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
