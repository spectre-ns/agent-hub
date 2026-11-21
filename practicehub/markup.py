"""Formatted text (a ``markdown`` field): a small, safe subset of Markdown, rendered to HTML.

What it knows:

    # Heading  ## Heading  ### Heading
    **bold**  *italic*  `code`  ~~struck~~
    - a list item          1. a numbered item
    > a quotation
    ```                    a block of code, shown as written
    | a | table |          (a row of dashes under the first row makes it the header)
    ---                    a rule
    [words](https://...)   a link: http, https, mailto, or a path in this hub (/records/...)
    [[Type/id]]            a link to a record; [[Type/id|words]] names it
    [[Its title]]          a link to the record of the same type with that title
    [[work:<run id>]]      a link to a piece of work

Raw HTML is never passed through: every character of the text is escaped, and only the tags made here are emitted.
A link to anything but http(s), mailto or a path is shown as text. The renderer does not look anything up: whoever
calls it passes ``resolve(target) -> (href, words, exists) | None``, so that it shows only what the reader may see.
"""

from __future__ import annotations

import html
import re

MENTION = re.compile(r"\[\[([^\[\]\n|]{1,200})(?:\|([^\[\]\n]{1,200}))?\]\]")
_SAFE_URL = re.compile(r"(https?://|mailto:|/(?![/\\]))[^\s\"'<>\\]*", re.I)  # "/\\host" is "//host" to a browser
_INLINE = re.compile(
    r"(?P<code>`[^`\n]+`)"
    r"|(?P<mention>\[\[[^\[\]\n]{1,400}\]\])"
    r"|(?P<link>\[(?P<lt>[^\[\]\n]{1,300})\]\((?P<lu>[^()\s]{1,2000})\))"
    r"|(?P<bold>\*\*(?P<bt>[^*\n]+?)\*\*)"
    r"|(?P<strike>~~(?P<st>[^~\n]+?)~~)"
    r"|(?P<em>(?<![\w*])\*(?P<et>[^*\s][^*\n]*?)\*(?![\w*]))"
)


def mentions(text: str) -> list[tuple[str, str | None]]:
    """Every ``[[target]]`` or ``[[target|words]]`` in the text, outside code: (target, words)."""
    out = []
    for line in _outside_code(text or ""):
        line = re.sub(r"`[^`\n]+`", "", line)
        out += [(m.group(1).strip(), (m.group(2) or "").strip() or None) for m in MENTION.finditer(line)]
    return out


def _outside_code(text: str):
    fenced = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            yield line


def plain(text: str, limit: int = 200) -> str:
    """The words of formatted text, without its marks: for a list, a search result or a summary."""
    s = MENTION.sub(lambda m: m.group(2) or m.group(1), text or "")
    s = re.sub(r"\[([^\]\n]{1,300})\]\([^)\s]{1,2000}\)", r"\1", s)
    s = re.sub(r"[#>*`~|]|^\s*[-+]\s+|^\s*\d+\.\s+", " ", s, flags=re.M)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def _inline(s: str, resolve, in_link: bool = False) -> str:
    out, pos = [], 0
    for m in _INLINE.finditer(s):
        out.append(_esc(s[pos:m.start()]))
        pos = m.end()
        if m.group("code"):
            out.append(f"<code>{_esc(m.group('code')[1:-1])}</code>")
        elif m.group("mention"):
            mm = MENTION.fullmatch(m.group("mention"))
            if not mm:
                out.append(_esc(m.group("mention")))
                continue
            target, words = mm.group(1).strip(), (mm.group(2) or "").strip() or None
            source = _esc(target + (f"|{words}" if words else ""))  # what an editor writes back
            found = resolve(target) if resolve else None
            shown = words or (found[1] if found else target)
            if in_link:  # inside a link's words: only the words
                out.append(_esc(shown))
            elif found and found[2]:
                out.append(f'<a class="mention" data-mention="{source}" href="{_esc(found[0])}">{_esc(shown)}</a>')
            else:  # nothing there, or nothing this reader may see: the words, marked as a missing link
                out.append(f'<span class="mention missing" data-mention="{source}">{_esc(shown)}'
                           f'<span class="visually-hidden"> (no page or record found)</span></span>')
        elif m.group("link"):
            url = m.group("lu")
            if _SAFE_URL.fullmatch(url) and not in_link:
                ext = ' rel="noopener noreferrer"' if url.lower().startswith("http") else ""
                out.append(f'<a href="{_esc(url)}"{ext}>{_inline(m.group("lt"), resolve, True)}</a>')
            else:
                out.append(_inline(m.group("lt"), resolve, in_link))
        elif m.group("bold"):
            out.append(f"<strong>{_inline(m.group('bt'), resolve, in_link)}</strong>")
        elif m.group("strike"):
            out.append(f"<del>{_inline(m.group('st'), resolve, in_link)}</del>")
        else:
            out.append(f"<em>{_inline(m.group('et'), resolve, in_link)}</em>")
    out.append(_esc(s[pos:]))
    return "".join(out)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def slug(words: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", words.lower()).strip("-")[:60] or "section"


_HEADING = re.compile(r"\s*(#{1,4})\s+(.*)")
MAX_QUOTE_DEPTH = 4


def _heading(line: str) -> tuple[int, str] | None:
    """(marks, words) for a heading line; linear in the line's length."""
    h = _HEADING.match(line)
    words = h.group(2).rstrip().rstrip("#").rstrip() if h else ""
    return (len(h.group(1)), words) if words else None


def _anchor(words: str, seen: dict) -> str:
    """A heading's id: its words, made unique on the page, and never one of the page's own ids."""
    base = "s-" + slug(words)
    seen[base] = seen.get(base, 0) + 1
    return base if seen[base] == 1 else f"{base}-{seen[base]}"


def render(text: str, resolve=None, _depth: int = 0) -> str:
    """Formatted text as HTML. ``resolve(target)``: (href, words, exists) for a ``[[target]]``, or None."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    seen: dict = {}
    out: list[str] = []
    para: list[str] = []
    i = 0

    def flush():
        if para:
            out.append("<p>" + "<br>".join(_inline(p, resolve) for p in para) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("```"):
            flush()
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            out.append(f"<pre><code>{_esc(chr(10).join(code))}</code></pre>")
            i += 1
            continue
        if not s:
            flush()
            i += 1
            continue
        h = _heading(s)
        if h:
            flush()
            n = max(2, h[0])  # a page's own title is its h1: # and ## are both its top headings
            anchor = f' id="{_anchor(h[1], seen)}"' if _depth == 0 else ""  # a quoted heading is not in the contents
            out.append(f'<h{n}{anchor}>{_inline(h[1], resolve)}</h{n}>')
            i += 1
            continue
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", s):
            flush()
            out.append("<hr>")
            i += 1
            continue
        if s.startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].lstrip())
                i += 1
            inner = (render(chr(10).join(quote), resolve, _depth + 1) if _depth < MAX_QUOTE_DEPTH
                     else "<p>" + "<br>".join(_inline(q, resolve) for q in quote if q) + "</p>")
            out.append(f"<blockquote>{inner}</blockquote>")
            continue
        if s.startswith("|") and i + 1 < len(lines) and re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?",
                                                                     lines[i + 1].strip()):
            flush()
            head = _cells(s)
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            out.append("<div class='tablewrap'><table><thead><tr>" + "".join(
                f"<th scope='col'>{_inline(c, resolve)}</th>" for c in head) + "</tr></thead><tbody>" + "".join(
                "<tr>" + "".join(f"<td>{_inline(c, resolve)}</td>" for c in r) + "</tr>" for r in rows)
                + "</tbody></table></div>")
            continue
        lm = re.match(r"([-*+]|\d{1,3}\.)\s+", s)
        if lm:
            flush()
            ordered = lm.group(1)[0].isdigit()
            items = []
            while i < len(lines):
                m2 = re.match(r"\s*([-*+]|\d{1,3}\.)\s+(.*)", lines[i])
                if m2 and m2.group(1)[0].isdigit() == ordered:
                    items.append(m2.group(2))
                elif lines[i].startswith(("  ", "\t")) and lines[i].strip() and items:
                    items[-1] += " " + lines[i].strip()  # a wrapped line continues its item
                else:
                    break
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(x, resolve)}</li>" for x in items) + f"</{tag}>")
            continue
        para.append(s)
        i += 1
    flush()
    return "".join(out)


def headings(text: str) -> list[tuple[int, str, str]]:
    """(level, words, anchor) for each heading outside code: a page's contents."""
    out, seen = [], {}
    for line in _outside_code(text or ""):
        h = _heading(line)
        if h:
            out.append((h[0], plain(h[1], 200), _anchor(h[1], seen)))
    return out
