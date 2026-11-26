"""A tiny, safe condition language for workflows (no eval).

    intake.urgent == true
    billing.total < 200 and billing.codes != []
    review.outcome in ['approve', 'approve-with-changes']
    not sign.outcome == 'decline'

    record.review_by <= run.today + 30d
    record.total == record.amount + record.fee
    sum(record.lines.*.amount) == record.amount
    count(parts.Point.*, it.record.status != 'closed') == 0

Paths are dotted lookups into the run context; a missing path is null. Literals: numbers, 'strings' or "strings",
true, false, null, [lists]. Operators: == != < <= > >= in, and, or, not, parentheses. A date (``YYYY-MM-DD``, or the
date of a time) may be moved on or back by days, weeks, months or years: ``+ 30d``, ``- 2w``, ``+ 6m``, ``+ 2y``; what
is not a date moves to null, which compares as false. A date is written in quotes ('2025-01-31').

Numbers add, subtract, multiply and divide (``+ - * /``, and ``-x``), exactly: 0.1 + 0.2 is 0.3, 10 / 4 is 2.5. A
whole number with a whole number stays whole (except by ``/``); anything else is an exact decimal (``decimals.py``).
What is not a number, a missing value, or a division by zero gives null. Numbers compare by value: 0.10 == 0.1.

A path through a list (``*``) is read by a total: ``sum(P)``, ``min(P)``, ``max(P)``, ``count(P)``. Each may take a
second argument over each item, named ``it``: ``sum(record.lines.*, it.qty * it.price)``, ``count(P, it.done ==
false)`` (count: a condition; the others: a value). A total skips what is not a number; the sum of nothing is 0, the
least or most of nothing is null. ``round(x, 2)`` rounds half up to two places.
"""

from __future__ import annotations

import re
from decimal import Decimal, DecimalException

from . import decimals

TOKEN = re.compile(r"""\s*(?:(?P<date>\d{4}-\d{2}-\d{2})|(?P<shift>[+-]\s*\d{1,4}\s*[dwmy](?![A-Za-z0-9_]))|(?P<num>\d+(?:\.\d+)?)|(?P<str>'[^']*'|"[^"]*")|(?P<op>==|!=|<=|>=|<|>|\(|\)|\[|\]|,|\+|-|\*|/)"""
                   r"""|(?P<word>[A-Za-z_](?:[A-Za-z0-9_\-]|\.\*(?![A-Za-z0-9_])|\.)*))""")


SHIFT = re.compile(r"([+-])\s*(\d{1,4})\s*([dwmy])")
TOTALS = ("sum", "min", "max", "count")
CALLS = (*TOTALS, "round")


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
        if kind == "date":
            raise ExprError(f"a date is written in quotes: '{m.group(kind)}'")
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
        left = self.sum_()
        kind, val = self.peek()
        if kind == "op" and val in ("==", "!=", "<", "<=", ">", ">="):
            self.take()
            return ("cmp", val, left, self.sum_())
        if (kind, val) == ("word", "in"):
            self.take()
            return ("cmp", "in", left, self.sum_())
        return ("truthy", left)

    def sum_(self):
        """Terms added and taken away, and any date shifts after one (``run.today + 30d``)."""
        node = self.product()
        while True:
            kind, val = self.peek()
            if kind == "shift":
                m = SHIFT.fullmatch(self.take()[1])
                node = ("shift", node, m.group(1), int(m.group(2)), m.group(3))
            elif kind == "op" and val in ("+", "-"):
                self.take()
                node = ("arith", val, node, self.product())
            else:
                return node

    def product(self):
        node = self.unary()
        while self.peek()[0] == "op" and self.peek()[1] in ("*", "/"):
            op = self.take()[1]
            node = ("arith", op, node, self.unary())
        return node

    def unary(self):
        if self.peek() == ("op", "-"):
            self.take()
            inner = self.unary()
            if inner[0] == "lit" and isinstance(inner[1], (int, Decimal)) and not isinstance(inner[1], bool):
                return ("lit", -inner[1])  # -5 is the number -5, as it always was
            return ("neg", inner)
        return self.atom()

    def atom(self):
        kind, val = self.peek()
        if kind == "num":
            self.take()
            return ("lit", Decimal(val) if "." in val else int(val))
        if kind == "str":
            self.take()
            return ("lit", val[1:-1])
        if (kind, val) == ("op", "("):
            self.take()
            node = self.or_()
            self.take(")")
            return node[1] if node[0] == "truthy" else node  # (7 - 2) * 3: in brackets, a value stays a value
        if (kind, val) == ("op", "["):
            self.take()
            items = []
            while self.peek() != ("op", "]"):
                items.append(self.unary())
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
            if val in CALLS and self.peek() == ("op", "("):
                return self.call(val)
            if "*" in val:
                raise ExprError(f"{val} reads every item of a list: read it with sum(), count(), min() or max()")
            return ("path", val)
        raise ExprError("the condition ends too early")

    def call(self, name: str):
        self.take("(")
        if name == "round":
            x = self.sum_()
            self.take(",")
            kind, places = self.take()
            if kind != "num" or "." in places or int(places) > decimals.MAX_SCALE:
                raise ExprError(f"round(x, n): n is a whole number of places, at most {decimals.MAX_SCALE}")
            self.take(")")
            return ("call", "round", [x, ("lit", int(places))])
        kind, over = self.take()
        if kind != "word" or over in ("true", "false", "null"):
            raise ExprError(f"{name}() totals a path, e.g. {name}(record.lines.*.amount)")
        args = [("path", over)]
        if self.peek() == ("op", ","):
            self.take()
            args.append(self.or_() if name == "count" else self.sum_())
        self.take(")")
        return ("call", name, args)


def lookup(ctx: dict, path: str):
    cur = ctx
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def gather(ctx: dict, path: str) -> list:
    """Every value at a path that may go through lists (``lines.*.amount``): a list item that lacks it adds
    nothing; a path ending in ``*`` gives the items themselves."""
    cur = [ctx]
    for part in path.split("."):
        nxt = []
        for v in cur:
            if part == "*":
                if isinstance(v, list):
                    nxt.extend(v)
            elif isinstance(v, dict) and part in v:
                nxt.append(v[part])
            elif isinstance(v, list) and part.isdigit() and int(part) < len(v):
                nxt.append(v[int(part)])
        cur = nxt
    return cur


def _comparable(v):
    """A number as its exact value (a float through its text), so 0.10 == 0.1; anything else as it is."""
    n = decimals.number(v)
    if n is not None:
        return n
    if isinstance(v, list):
        return [_comparable(x) for x in v]
    return v


def arith(op: str, a, b):
    """``a op b``, exactly; null when either is not a number, or for a division by zero."""
    a, b = decimals.number(a), decimals.number(b)
    if a is None or b is None:
        return None
    if isinstance(a, int) and isinstance(b, int) and op != "/":
        return {"+": a + b, "-": a - b, "*": a * b}[op]
    try:
        a, b = Decimal(a), Decimal(b)
        if op == "/":
            return None if b == 0 else decimals.CTX.divide(a, b)
        return {"+": decimals.CTX.add, "-": decimals.CTX.subtract, "*": decimals.CTX.multiply}[op](a, b)
    except DecimalException:
        return None


def _total(name: str, args: list, ctx: dict):
    items = gather(ctx, args[0][1])
    if len(args) > 1:
        each = args[1]
        if name == "count":
            return sum(1 for it in items if evaluate(each, {**ctx, "it": it}))
        items = [evaluate(each, {**ctx, "it": it}) for it in items]
    elif name == "count":
        return sum(1 for it in items if it not in (None, "", [], {}))
    nums = [n for n in (decimals.number(v) for v in items) if n is not None]
    if name == "sum":
        total = 0
        for n in nums:
            total = arith("+", total, n)
        return total
    if not nums:
        return None
    return (min if name == "min" else max)(nums)


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
    if op == "arith":
        return arith(node[1], evaluate(node[2], ctx), evaluate(node[3], ctx))
    if op == "neg":
        return arith("-", 0, evaluate(node[1], ctx))
    if op == "call":
        if node[1] == "round":
            x = decimals.number(evaluate(node[2][0], ctx))
            if x is None:
                return None
            if isinstance(x, int):
                return x
            try:
                return decimals.to_decimal(x, node[2][1][1])
            except ValueError:
                return None
        return _total(node[1], node[2], ctx)
    if op == "truthy":
        return bool(evaluate(node[1], ctx))
    if op == "not":
        return not evaluate(node[1], ctx)
    if op == "and":
        return bool(evaluate(node[1], ctx)) and bool(evaluate(node[2], ctx))
    if op == "or":
        return bool(evaluate(node[1], ctx)) or bool(evaluate(node[2], ctx))
    if op == "cmp":
        a, b = _comparable(evaluate(node[2], ctx)), _comparable(evaluate(node[3], ctx))
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


def value(src: str, ctx: dict):
    """What an expression comes to (a number, a date, text...), not only whether it holds."""
    node = Parser(src).parse()
    if node[0] == "truthy":  # a bare value is parsed as a condition on it: what it comes to is the value
        node = node[1]
    return evaluate(node, ctx)


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
    """The context paths a condition reads (for validation and the diagram). Inside a total, ``it.x`` is read as the
    total's path and x (``count(parts.Point.*, it.record.status)`` reads ``parts.Point.*.record.status``)."""
    out = []

    def walk(n, over=None):
        if n[0] == "path":
            p = n[1]
            if over and (p == "it" or p.startswith("it.")):
                p = over + p[2:]
            out.append(p)
            return
        if n[0] == "call" and n[1] in TOTALS:
            base = n[2][0][1]
            out.append(base)
            for a in n[2][1:]:
                walk(a, base if base.endswith(".*") else base + ".*")
            return
        for child in n[1:]:
            if isinstance(child, tuple):
                walk(child, over)
            elif isinstance(child, list):
                for c in child:
                    walk(c, over)

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
        if op == "arith":
            return f"{say(n[2])} {({'+': 'plus', '-': 'minus', '*': 'times', '/': 'divided by'})[n[1]]} {say(n[3])}"
        if op == "neg":
            return f"minus {say(n[1])}"
        if op == "call":
            name, args = n[1], n[2]
            if name == "round":
                return f"{say(args[0])} rounded to {args[1][1]} places"
            what = say_path(args[0][1].replace(".*", ""))
            if len(args) > 1:
                return (f"the number of {what} where {say(args[1])}" if name == "count" else
                        f"the {({'sum': 'total', 'min': 'least', 'max': 'most'})[name]} of {say(args[1])} over {what}")
            return {"sum": f"the total of {what}", "count": f"the number of {what}", "min": f"the least {what}",
                    "max": f"the most {what}"}[name]
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
