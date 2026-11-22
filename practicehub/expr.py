"""A tiny, safe condition language for workflows (no eval).

    intake.urgent == true
    billing.total < 200 and billing.codes != []
    review.outcome in ['approve', 'approve-with-changes']
    not sign.outcome == 'decline'

    record.review_by <= run.today + 30d

Paths are dotted lookups into the run context; a missing path is null. Literals: numbers, 'strings' or "strings",
true, false, null, [lists]. Operators: == != < <= > >= in, and, or, not, parentheses. A date (``YYYY-MM-DD``, or the
date of a time) may be moved on or back by days, weeks, months or years: ``+ 30d``, ``- 2w``, ``+ 6m``, ``+ 2y``; what
is not a date moves to null, which compares as false.
"""

from __future__ import annotations

import re

TOKEN = re.compile(r"""\s*(?:(?P<shift>[+-]\s*\d{1,4}\s*[dwmy](?![A-Za-z0-9_]))|(?P<num>-?\d+(?:\.\d+)?)|(?P<str>'[^']*'|"[^"]*")|(?P<op>==|!=|<=|>=|<|>|\(|\)|\[|\]|,)"""
                   r"""|(?P<word>[A-Za-z_][A-Za-z0-9_\-\.]*))""")


SHIFT = re.compile(r"([+-])\s*(\d{1,4})\s*([dwmy])")


class ExprError(ValueError):
    pass


def tokenize(src: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    src = src.strip()
    while pos < len(src):
        m = TOKEN.match(src, pos)
        if not m or m.end() == pos:
            raise ExprError(f"cannot read the condition near {src[pos:pos + 12]!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
        while pos < len(src) and src[pos].isspace():
            pos += 1
    return out


class Parser:
    def __init__(self, src: str):
        self.toks = tokenize(src)
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self, value=None):
        tok = self.peek()
        if tok[0] is None or (value is not None and tok[1] != value):
            raise ExprError(f"expected {value!r} in the condition")
        self.i += 1
        return tok

    def parse(self):
        node = self.or_()
        if self.i != len(self.toks):
            raise ExprError(f"unexpected {self.toks[self.i][1]!r} in the condition")
        return node

    def or_(self):
        node = self.and_()
        while self.peek() == ("word", "or"):
            self.take()
            node = ("or", node, self.and_())
        return node

    def and_(self):
        node = self.not_()
        while self.peek() == ("word", "and"):
            self.take()
            node = ("and", node, self.not_())
        return node

    def not_(self):
        if self.peek() == ("word", "not"):
            self.take()
            return ("not", self.not_())
        return self.cmp()

    def cmp(self):
        left = self.shifted()
        kind, val = self.peek()
        if kind == "op" and val in ("==", "!=", "<", "<=", ">", ">="):
            self.take()
            return ("cmp", val, left, self.shifted())
        if (kind, val) == ("word", "in"):
            self.take()
            return ("cmp", "in", left, self.shifted())
        return ("truthy", left)

    def shifted(self):
        """An atom, and any date shifts after it (``run.today + 30d``)."""
        node = self.atom()
        while self.peek()[0] == "shift":
            m = SHIFT.fullmatch(self.take()[1])
            node = ("shift", node, m.group(1), int(m.group(2)), m.group(3))
        return node

    def atom(self):
        kind, val = self.peek()
        if kind == "num":
            self.take()
            return ("lit", float(val) if "." in val else int(val))
        if kind == "str":
            self.take()
            return ("lit", val[1:-1])
        if (kind, val) == ("op", "("):
            self.take()
            node = self.or_()
            self.take(")")
            return node
        if (kind, val) == ("op", "["):
            self.take()
            items = []
            while self.peek() != ("op", "]"):
                items.append(self.atom())
                if self.peek() == ("op", ","):
                    self.take()
            self.take("]")
            return ("list", items)
        if kind == "word":
            self.take()
            if val in ("true", "false"):
                return ("lit", val == "true")
            if val == "null":
                return ("lit", None)
            return ("path", val)
        raise ExprError("the condition ends too early")


def lookup(ctx: dict, path: str):
    cur = ctx
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def evaluate(node, ctx: dict):
    op = node[0]
    if op == "lit":
        return node[1]
    if op == "list":
        return [evaluate(n, ctx) for n in node[1]]
    if op == "path":
        return lookup(ctx, node[1])
    if op == "shift":
        return shift_date(evaluate(node[1], ctx), node[2], node[3], node[4])
    if op == "truthy":
        return bool(evaluate(node[1], ctx))
    if op == "not":
        return not evaluate(node[1], ctx)
    if op == "and":
        return bool(evaluate(node[1], ctx)) and bool(evaluate(node[2], ctx))
    if op == "or":
        return bool(evaluate(node[1], ctx)) or bool(evaluate(node[2], ctx))
    if op == "cmp":
        a, b = evaluate(node[2], ctx), evaluate(node[3], ctx)
        sym = node[1]
        if sym == "==":
            return a == b
        if sym == "!=":
            return a != b
        if sym == "in":
            return a in (b or [])
        if a is None or b is None:
            return False
        try:
            return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[sym]
        except TypeError:
            return False
    raise ExprError(f"unknown node {op}")


def shift_date(value, sign: str, n: int, unit: str):
    """A date (``YYYY-MM-DD``, or the date of a time) moved by n days, weeks, months or years. A month or year from
    the 31st, or from 29 February, lands on the last day of the shorter month. Anything else is None."""
    import calendar
    from datetime import date, timedelta
    try:
        d = date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None
    k = n if sign == "+" else -n
    if unit in ("d", "w"):
        return (d + timedelta(days=k * (7 if unit == "w" else 1))).isoformat()
    months = d.year * 12 + d.month - 1 + (k * 12 if unit == "y" else k)
    y, m = divmod(months, 12)
    if not 1 <= y <= 9999:
        return None
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1])).isoformat()


def check(src: str) -> None:
    Parser(src).parse()


def holds(src: str | None, ctx: dict) -> bool:
    if not src:
        return True
    return bool(evaluate(Parser(src).parse(), ctx))


def paths(src: str) -> list[str]:
    """The context paths a condition reads (for validation and the diagram)."""
    out = []

    def walk(n):
        if n[0] == "path":
            out.append(n[1])
        for child in n[1:]:
            if isinstance(child, tuple):
                walk(child)
            elif isinstance(child, list):
                for c in child:
                    walk(c)

    walk(Parser(src).parse())
    return out


def words(src: str, name=None) -> str:
    """A condition as a plain sentence for people reading a page, never the code: ``input.flag == 'critical'`` reads
    "flag is critical". ``name(path)`` says a path in words (a field's label); by default its last part, as words.
    A condition that cannot be read is "a condition is met"."""
    def say_path(p: str) -> str:
        got = name(p) if name else None
        if got:
            return got
        last = p.split(".")[-1]
        return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", last).replace("_", " ").replace("-", " ").lower()

    def lit(v) -> str:
        if v is True:
            return "yes"
        if v is False:
            return "no"
        if v is None:
            return "empty"
        return str(v).replace("-", " ") if isinstance(v, str) else str(v)

    def say(n) -> str:
        op = n[0]
        if op == "lit":
            return lit(n[1])
        if op == "list":
            items = [say(x) for x in n[1]]
            if not items:
                return "nothing"
            return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " or " + items[-1]
        if op == "path":
            return say_path(n[1])
        if op == "shift":
            unit = {"d": "day", "w": "week", "m": "month", "y": "year"}[n[4]]
            return f"{n[3]} {unit}{'s' if n[3] != 1 else ''} {'after' if n[2] == '+' else 'before'} {say(n[1])}"
        if op == "truthy":
            return say(n[1])
        if op == "not":
            inner = n[1]
            if inner[0] == "truthy" and inner[1][0] != "path":
                inner = inner[1]
            if inner[0] == "or":
                return f"neither {say(inner[1])} nor {say(inner[2])}"
            if inner[0] == "cmp" and inner[1] in ("==", "in"):
                return say(("cmp", "!=" if inner[1] == "==" else "not in", inner[2], inner[3]))
            return f"not {say(inner)}" if inner[0] in ("truthy", "path") else f"not ({say(inner)})"
        if op in ("and", "or"):
            return f"{say(n[1])} {op} {say(n[2])}"
        if op == "cmp":
            sym, a, b = n[1], n[2], n[3]
            left = say(a)
            if b[0] == "lit" and isinstance(b[1], bool) and sym in ("==", "!="):
                return left if (sym == "==") == b[1] else f"not {left}"
            if (b[0] == "lit" and b[1] is None) or (b[0] == "list" and not b[1] and sym in ("==", "!=")):
                return f"{left} is {'empty' if sym == '==' else 'given'}"
            verb = {"==": "is", "!=": "is not", "<": "is under", "<=": "is at most", ">": "is over",
                    ">=": "is at least", "in": "is one of", "not in": "is not one of"}[sym]
            if sym in ("in", "not in") and (b[0] != "list" or len(b[1]) == 1):
                verb = ("is" if sym == "in" else "is not") if b[0] == "list" else (
                    "is in" if sym == "in" else "is not in")
            return f"{left} {verb} {say(b)}"
        return "a condition is met"

    try:
        return say(Parser(src).parse())
    except (ExprError, KeyError, IndexError):
        return "a condition is met"
