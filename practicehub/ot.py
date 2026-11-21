"""Operational transformation for plain text: what lets two people edit the same text at once and keep both edits.

An operation walks the whole text from its start, as a list of parts:

    n > 0      keep the next n characters             (retain)
    "words"    insert these characters here            (insert)
    n < 0      delete the next -n characters           (delete)

``[5, "big ", -3, 10]`` keeps 5 characters, inserts "big ", deletes 3, and keeps the last 10: it applies to a text of
exactly 18 characters (its *base length*), and covers all of it. The same functions, part for part, are in the editor's script
(``assets.LIVE_JS``): the server and every browser transform the same way, so they reach the same text.

``transform(a, b)``: two operations made on the same text, concurrently. It returns ``(a', b')`` such that applying
``a`` then ``b'`` gives the same text as ``b`` then ``a'``. When both insert at the same place, ``a``'s insertion goes
first. The server always calls it with the incoming (client's) operation first, and so does the client with its own
unacknowledged operation first, so both put the same insertion first.

Lengths count UTF-16 code units, as JavaScript strings do, so that an offset means the same in the browser and here.
"""

from __future__ import annotations


class OTError(ValueError):
    pass


def u16len(s: str) -> int:
    return len(s.encode("utf-16-le", "surrogatepass")) // 2


def _u16slice(s: str, start: int, end: int | None = None) -> str:
    b = s.encode("utf-16-le", "surrogatepass")
    seg = b[start * 2:] if end is None else b[start * 2:end * 2]
    return seg.decode("utf-16-le", "surrogatepass")


def _fits(s: str) -> bool:
    return all(ord(c) < 0x10000 for c in s)


def _cut(s: str, n: int) -> tuple[str, str]:
    """The first n UTF-16 units of s, and the rest."""
    if _fits(s):
        return s[:n], s[n:]
    return _u16slice(s, 0, n), _u16slice(s, n)


def normalize(op) -> list:
    """Adjacent parts of the same kind merged, empty ones dropped, an insert put before a delete it touches: one
    spelling for each operation. A malformed operation is an OTError."""
    if not isinstance(op, list):
        raise OTError("an operation is a list")
    out: list = []
    for p in op:
        if isinstance(p, bool) or not isinstance(p, (int, str)):
            raise OTError(f"an operation part is a number or text, not {p!r}")
        if p == 0 or p == "":
            continue
        if isinstance(p, str):
            if out and isinstance(out[-1], int) and out[-1] < 0:  # insert, then delete: the same thing
                if len(out) >= 2 and isinstance(out[-2], str):
                    out[-2] += p
                else:
                    out.insert(len(out) - 1, p)
            elif out and isinstance(out[-1], str):
                out[-1] += p
            else:
                out.append(p)
        elif out and isinstance(out[-1], int) and (out[-1] > 0) == (p > 0):
            out[-1] += p
        else:
            out.append(p)
    return out


def base_length(op) -> int:
    """The length of the text the operation applies to."""
    return sum(abs(p) for p in op if isinstance(p, int))


def target_length(op) -> int:
    return sum(p for p in op if isinstance(p, int) and p > 0) + sum(u16len(p) for p in op if isinstance(p, str))


def apply(op, text: str) -> str:
    """The text after the operation, which covers the whole text (its base length is the text's length)."""
    n = u16len(text)
    if base_length(op) != n:
        raise OTError(f"the operation is for a text of {base_length(op)} characters; this one has {n}")
    out, pos = [], 0
    simple = _fits(text)
    for p in op:
        if isinstance(p, str):
            out.append(p)
        elif p > 0:
            out.append(text[pos:pos + p] if simple else _u16slice(text, pos, pos + p))
            pos += p
        else:
            pos -= p
    return "".join(out)


def _len(p) -> int:
    return u16len(p) if isinstance(p, str) else abs(p)


def _split(p, n):
    """The first n units of a part, and the rest (None when nothing is left)."""
    if isinstance(p, str):
        h, r = _cut(p, n)
        return h, (r or None)
    if p > 0:
        return n, (p - n or None)
    return -n, (p + n or None)


def transform(a, b) -> tuple[list, list]:
    """(a', b'): a and b were made on the same text; a' applies after b, b' after a, and both orders agree.
    When both insert at the same place, a's insertion comes first."""
    a, b = normalize(a), normalize(b)
    if base_length(a) != base_length(b):
        raise OTError("both operations must be made on the same text")
    out_a, out_b = [], []
    i = j = 0
    x = a[0] if a else None
    y = b[0] if b else None

    def na():
        nonlocal i
        i += 1
        return a[i] if i < len(a) else None

    def nb():
        nonlocal j
        j += 1
        return b[j] if j < len(b) else None

    while x is not None or y is not None:
        if isinstance(x, str):
            out_a.append(x)
            out_b.append(u16len(x))
            x = na()
            continue
        if isinstance(y, str):
            out_a.append(u16len(y))
            out_b.append(y)
            y = nb()
            continue
        if x is None or y is None:
            raise OTError("both operations must be made on the same text")
        n = min(abs(x), abs(y))
        xh, xr = _split(x, n)
        yh, yr = _split(y, n)
        if xh > 0 and yh > 0:
            out_a.append(n)
            out_b.append(n)
        elif xh < 0 and yh > 0:
            out_a.append(xh)
        elif xh > 0 and yh < 0:
            out_b.append(yh)
        # both delete it: neither has to
        x = xr if xr is not None else na()
        y = yr if yr is not None else nb()
    return normalize(out_a), normalize(out_b)


def compose(a, b) -> list:
    """One operation that does a, then b."""
    a, b = normalize(a), normalize(b)
    if target_length(a) != base_length(b):
        raise OTError("the second operation must apply to the text the first one makes")
    out = []
    i = j = 0
    x = a[0] if a else None
    y = b[0] if b else None

    def na():
        nonlocal i
        i += 1
        return a[i] if i < len(a) else None

    def nb():
        nonlocal j
        j += 1
        return b[j] if j < len(b) else None

    while x is not None or y is not None:
        if isinstance(x, int) and x < 0:
            out.append(x)
            x = na()
            continue
        if isinstance(y, str):
            out.append(y)
            y = nb()
            continue
        if x is None or y is None:
            raise OTError("the second operation must apply to the text the first one makes")
        n = min(_len(x), abs(y))
        xh, xr = _split(x, n)
        yh, yr = _split(y, n)
        if yh > 0:
            out.append(xh)          # b keeps what a inserted or kept
        elif isinstance(xh, int):
            out.append(yh)          # b deletes what a kept
        # b deletes what a inserted: nothing of either
        x = xr if xr is not None else na()
        y = yr if yr is not None else nb()
    return normalize(out)


def diff(old: str, new: str) -> list:
    """The operation that turns old into new: the common start and end kept, the middle replaced."""
    a, b = old.encode("utf-16-le", "surrogatepass"), new.encode("utf-16-le", "surrogatepass")
    na, nb = len(a) // 2, len(b) // 2
    p = 0
    while p < na and p < nb and a[2 * p:2 * p + 2] == b[2 * p:2 * p + 2]:
        p += 1
    s = 0
    while s < na - p and s < nb - p and a[2 * (na - 1 - s):2 * (na - s)] == b[2 * (nb - 1 - s):2 * (nb - s)]:
        s += 1
    # never split a surrogate pair
    while p and 0xD800 <= int.from_bytes(a[2 * p - 2:2 * p], "little") <= 0xDBFF:
        p -= 1
    while s and 0xDC00 <= int.from_bytes(a[2 * (na - s):2 * (na - s) + 2], "little") <= 0xDFFF:
        s -= 1
    ins = b[2 * p:2 * (nb - s)].decode("utf-16-le", "surrogatepass")
    return normalize([p, ins, -(na - p - s), s])


def is_noop(op) -> bool:
    return all(isinstance(p, int) and p > 0 for p in normalize(op))


def transform_index(op, index: int, *, before_insert: bool = False) -> int:
    """Where a position (a caret) is after the operation. An insert at the caret goes before it, unless
    ``before_insert`` (the caret stays in front of text inserted where it is)."""
    pos = 0
    out = index
    for p in normalize(op):
        if pos > index or (pos == index and before_insert and isinstance(p, str)):
            break
        if isinstance(p, str):
            out += u16len(p)
        elif p > 0:
            pos += p
        else:
            out -= min(-p, index - pos)
            pos -= p
    return max(out, 0)


def check(op, length: int) -> list:
    """The operation, normalized, if it applies to a text of this length; else OTError."""
    op = normalize(op)
    if base_length(op) != length:
        raise OTError(f"the operation is for a text of {base_length(op)} characters; this one has {length}")
    return op
