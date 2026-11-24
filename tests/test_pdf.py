"""The PDF writer: a well-formed file (its cross-reference table points at each object), every page, the text, and
the same bytes each time for the same page."""

import re
import zlib

from practicehub import pdf

PAGE = """# Hand hygiene

## When

Clean your hands **before** touching a patient and *after* touching their surroundings. See [[Backups|the backups]]
and [the guide](https://example.org/guide).

1. Use alcohol hand rub.
2. Wash with soap when your hands are visibly dirty.

- [x] Hand rub at the door
- [ ] Poster at the sink

> [!note]
> Gloves are not a substitute for clean hands.

| Surface | With |
|---|---|
| Floors | mop |

```
practicehub verify
```

Résumé (café) – done, and a non-Latin word: 手.
"""


def _text(data: bytes) -> str:
    """Every page's text drawn, from its content streams."""
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        raw = m.group(1)
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        out.append(raw.decode("latin-1").replace("\\(", "(").replace("\\)", ")"))
    return "\n".join(out)


def test_a_page_renders_as_a_well_formed_pdf_whose_cross_reference_table_finds_every_object():
    data = pdf.render("SOP-021 Hand hygiene", PAGE, "SOP-021: made from the wiki page")
    assert data.startswith(b"%PDF-1.") and data.rstrip().endswith(b"%%EOF")
    start = int(re.search(rb"startxref\s+(\d+)", data).group(1))
    assert data[start:start + 4] == b"xref"
    head = re.match(rb"xref\s+0 (\d+)\s+", data[start:])
    count = int(head.group(1))
    rows = data[start + head.end():].split(b"\n")[:count]
    for n, row in enumerate(rows[1:], 1):  # object 0 is the free list's head
        off = int(row[:10])
        assert re.match(rb"%d 0 obj" % n, data[off:off + 20]), f"object {n} is not at {off}"
    assert b"/Title (SOP-021 Hand hygiene)" in data


def test_the_text_is_drawn_without_its_marks_on_every_page_with_its_footer():
    data = pdf.render("SOP-021 Hand hygiene", PAGE + "\n\nMore words. " * 400, "the footer")
    pages = int(re.search(rb"/Type /Pages /Kids \[[^\]]*\] /Count (\d+)", data).group(1))
    assert pages > 1
    text = _text(data)
    for words in ("Hand hygiene", "Clean your hands", "the backups", "the guide (https://example.org/guide)",
                  "Note: ", "Floors", "practicehub verify", "R\xe9sum\xe9 (caf\xe9)", "word: ?"):
        assert words in text, words
    assert "**" not in text and "[[" not in text and "[!note]" not in text
    assert text.count("the footer") == pages and f"Page {pages} of {pages}" in text


def test_the_same_page_makes_the_same_bytes():
    assert pdf.render("T", PAGE, "f") == pdf.render("T", PAGE, "f")
    assert pdf.render("T", "", "")[:5] == b"%PDF-"  # an empty page is still a document
