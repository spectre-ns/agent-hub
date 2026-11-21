"""A tiny, safe condition language for workflows (no eval).

    intake.urgent == true
    billing.total < 200 and billing.codes != []
    review.outcome in ['approve', 'approve-with-changes']
    not sign.outcome == 'decline'

Paths are dotted lookups into the run context; a missing path is null. Literals: numbers, 'strings' or "strings",
true, false, null, [lists]. Operators: == != < <= > >= in, and, or, not, parentheses.
"""

from __future__ import annotations

import re

TOKEN = re.compile(r"""\s*(?:(?P<num>-?\d+(?:\.\d+)?)|(?P<str>'[^']*'|"[^"]*")|(?P<op>==|!=|<=|>=|<|>|\(|\)|\[|\]|,)"""
                   r"""|(?P<word>[A-Za-z_][A-Za-z0-9_\-\.]*))""")


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
        left = self.atom()
        kind, val = self.peek()
        if kind == "op" and val in ("==", "!=", "<", "<=", ">", ">="):
            self.take()
            return ("cmp", val, left, self.atom())
        if (kind, val) == ("word", "in"):
            self.take()
            return ("cmp", "in", left, self.atom())
        return ("truthy", left)

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
