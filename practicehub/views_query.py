"""A view's numbers, worked out in the database: a measure, dates by month, quarter or year, one reference followed,
and a value over time (``views.py`` says what a widget may hold; this is how its numbers are made).

    [[widget]]
    kind = "line"                    # count | chart | line
    type = "Order"
    measure = "sum(total)"           # sum | avg | min | max of a number or decimal field (min, max: a date too);
                                     # none: how many records
    x = "placed_on:month"            # line: a date or time field by month, quarter or year
    series = "customer.owner"        # line, optional: a line per value (the 8 largest, the rest as "Other")
    where = "record.status != 'void'"

A path is a field of the type, by its name or its path (a table's column at ``lines.*.amount``), or one reference
followed to a field of the record it names (``customer.owner``: the reference must be one value naming one type). A
date or time ends in ``:month``, ``:quarter`` or ``:year`` to group by it; a time with a zone is grouped in the
practice's own time zone.

What it reads is what the member may read. The type, and the type a reference reaches, must be in data classes they
read, and so must every field it reads, or the widget says what it needs instead of a number. What a subject's
restriction withholds from them is null here, as it is missing from the records they are shown, so a withheld
record is still counted as its row is still listed, and none of its values is summed.

A total is exact: a decimal field's sum, least and most are exact, and its average is rounded half up to the field's
places. A number field's are floats, and compare in the database to 15 digits. A table's column is summed in each
record, then over the records (its average is not said: of the lines, or of each record's total?). A computed
widget's ``where`` compares fields with values and ``today``; arithmetic over a record's fields is for a table or a
list. At most 1000 groups are shown.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from bson.decimal128 import Decimal128

from . import decimals, expr, schema

BUCKETS = ("month", "quarter", "year")
MEASURES = ("sum", "avg", "min", "max")
NUMERIC = ("number", "decimal")
DATES = ("date", "datetime")
MAX_GROUPS = 1000
MAX_SERIES = 8
OTHER = "Other"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_\-]*(\.([A-Za-z0-9_\-]+|\*))*")
_MEASURE = re.compile(r"\s*([A-Za-z_]+)\s*\((.*)\)\s*")
_ZONED = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]+)?)?(Z|[+-][0-9]{2}:[0-9]{2})$"
_WORDS = {"sum": "Total", "avg": "Average", "min": "Least", "max": "Most"}
_KEPT = {"resourceType", "id", "meta"}  # what a withheld record's stub keeps, besides whose it is


class Untranslatable(ValueError):
    """A condition the database cannot work out as the hub does."""


# ============================================================================================ paths and columns
def parse_path(text) -> tuple[str, str | None]:
    """``placed_on:month`` as ``("placed_on", "month")``. Raises ValueError, in words."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("a path is a field's name or its path (placed_on, customer.owner); a date or time may end "
                         "in :month, :quarter or :year")
    path, colon, bucket = text.strip().partition(":")
    if not _PATH.fullmatch(path):
        raise ValueError(f"{text!r} is not a path: a field's name, or names joined by dots (customer.owner)")
    if colon and bucket not in BUCKETS:
        raise ValueError(f"{text!r}: a date or time is grouped by :month, :quarter or :year")
    return path, bucket or None


def parse_measure(text) -> tuple[str, str]:
    """``sum(total)`` as ``("sum", "total")``. Raises ValueError, in words."""
    m = _MEASURE.fullmatch(text) if isinstance(text, str) else None
    if not m or m.group(1) not in MEASURES:
        raise ValueError("measure is sum(field), avg(field), min(field) or max(field)")
    path, bucket = parse_path(m.group(2))
    if bucket:
        raise ValueError(f"measure: {m.group(1)}() reads a field as it is, not by {bucket}")
    return m.group(1), path


@dataclass
class Col:
    """What a widget's path reads: where its value is, in the record it is read from, and what kind of value."""
    text: str
    path: str                # its place in the record it is read from (lines.*.amount)
    label: str
    owner: str               # the type of that record
    type: str | None = None  # the field's type; None: a key of the record that is no field of it
    field: dict | None = None
    bucket: str | None = None
    hop: dict | None = None  # the reference field followed to reach it
    target: str | None = None
    table: bool = False      # a table's column: a value in each row
    many: bool = False       # more than one value in a record

    @property
    def unit(self) -> str:
        return str((self.field or {}).get("unit") or "")


def _col(f: dict, rt, *, table: bool = False) -> Col:
    many = table or bool(f.get("list")) or "*" in f["path"] or f["type"] in ("list", "table")
    return Col(text=f["path"], path=f["path"], label=f["label"], owner=rt.id, type=f["type"], field=f, table=table,
               many=many)


def _find(type_of, rt, path: str, hop: bool) -> Col:
    f = next((f for f in rt.fields if path in (f["name"], f["path"])), None)
    if f is not None:
        return _col(f, rt)
    c = next((c for c in rt.cells() if c["path"] == path), None)
    if c is not None:
        return _col(c, rt, table=True)
    if path == "meta.lastUpdated":
        return Col(text=path, path=path, label="Last changed", owner=rt.id, type="datetime")
    head, _, rest = path.partition(".")
    ref = next((f for f in rt.fields if f["name"] == head and f["type"] == "reference"), None)
    if ref is not None and rest:
        if not hop:
            raise ValueError(f"{path!r}: a view follows one reference, not two")
        target = type_of(ref["target"][0]) if len(ref["target"]) == 1 else None
        if ref.get("list") or "*" in ref["path"] or target is None:
            raise ValueError(f"{path!r}: {ref['label']} is followed only when it is one reference to one type")
        if rest in ("reference", "display"):
            return Col(text=path, path=f"{ref['path']}.{rest}", label=ref["label"], owner=rt.id)
        inner = _find(type_of, target, rest, False)
        if inner.table:
            raise ValueError(f"{path!r}: a table's column is not read through a reference")
        inner.hop, inner.target, inner.label = ref, target.id, f"{ref['label']} › {inner.label}"
        return inner
    if head in rt.top_keys():
        return Col(text=path, path=path, label=path, owner=rt.id)
    names = [f["name"] for f in rt.fields]
    import difflib
    near = difflib.get_close_matches(head, names, n=1)
    raise ValueError(f"{rt.label} has no field {path!r}" + (f" (did you mean {near[0]!r}?)" if near else
                                                           f" (its fields: {', '.join(names)})"))


def resolve(type_of, rt, text: str) -> Col:
    """What a widget's path (``customer.owner``, ``placed_on:month``) reads of ``rt``'s records; ``type_of`` gives
    a record type by its id. Raises ValueError, in words."""
    path, bucket = parse_path(text)
    col = _find(type_of, rt, path, True)
    col.text, col.bucket = text, bucket
    if bucket and col.type not in DATES:
        raise ValueError(f"{text!r}: only a date or a time is grouped by {bucket} ({col.label} is "
                         f"{col.type or 'no field'})")
    return col


def columns_of(type_of, rt, w: dict) -> dict:
    """A widget's paths, resolved: ``{"fn", "m", "g", "s", "x"}`` (a measure's function and column, the group's,
    the series' and the x axis' columns, or None). Raises ValueError, in words."""
    out = {"fn": None, "m": None, "g": None, "s": None, "x": None}
    if w.get("measure"):
        fn, p = parse_measure(w["measure"])
        c = resolve(type_of, rt, p)
        ok = NUMERIC + (DATES if fn in ("min", "max") else ())
        if c.type not in ok:
            raise ValueError(f"measure: {fn}() reads a {' or '.join(ok)} field; {c.label} is "
                             f"{c.type or 'no field of ' + rt.label}")
        if c.many and not c.table:
            raise ValueError(f"measure: {c.label} holds a list; a measure reads one value of each record, or a "
                             f"table's column")
        if c.table and fn == "avg":
            raise ValueError(f"measure: avg() over a table's column is not said (the average of the lines, or of "
                             f"each record's total?): use sum(), or a field of the record")
        out["fn"], out["m"] = fn, c
    for key, name in (("group_by", "g"), ("series", "s"), ("x", "x")):
        if w.get(key) and not (key == "group_by" and w["kind"] not in ("chart", "board")):
            c = resolve(type_of, rt, w[key])
            if c.many:
                raise ValueError(f"{key}: {c.label} holds more than one value in a record; use a field that holds "
                                 f"one")
            out[name] = c
    return out


def computed(hub, w: dict) -> bool:
    """Whether the widget's numbers are worked out here, not by counting the rows it lists."""
    if w["kind"] not in ("count", "chart", "line") or w["source"] not in ("records", "signatures"):
        return False
    if w["kind"] == "line" or w.get("measure") or w.get("series"):
        return True
    if w["source"] == "signatures":  # a chart of sign-offs: by who signed in their names, by record in its title
        return w["kind"] == "chart"
    g = w.get("group_by") if w["kind"] == "chart" else None
    if not g:
        return False
    if ":" in g:
        return True
    rt = hub.record_type(w["type"]) if w["source"] == "records" and w.get("type") else None
    if rt is None:
        return False
    try:
        return resolve(hub.record_type, rt, g).hop is not None
    except ValueError:  # a path through a reference that cannot be followed: worked out here, so it is said
        head, _, rest = g.partition(".")
        return bool(rest) and any(f["name"] == head and f["type"] == "reference" for f in rt.fields)


# ============================================================================================ dates in buckets
def bucket_of(v, unit: str, tz=None, aware: bool = False) -> str | None:
    """The bucket of a date or time (``2025-03``, ``2025-Q1``, ``2025``), as the database works it out: a time with
    a zone (``aware``) on its date in ``tz``; anything that does not begin with a date, None."""
    if not isinstance(v, str):
        return None
    d = v
    if aware and re.fullmatch(_ZONED, v):
        from datetime import datetime
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(tz).date().isoformat()
        except (ValueError, OverflowError):
            return None
    if not re.match(r"[0-9]{4}" if unit == "year" else r"[0-9]{4}-[0-9]{2}", d):
        return None
    if unit == "year":
        return d[:4]
    if unit == "month":
        return d[:7]
    return f"{d[:4]}-Q{math.ceil(int(d[5:7]) / 3)}"


def _bucket_expr(v, unit: str, tz: str, aware: bool):
    d = "$$b"
    if aware:
        d = {"$cond": [{"$regexMatch": {"input": "$$b", "regex": _ZONED}},
                       {"$dateToString": {"date": {"$dateFromString": {"dateString": "$$b", "onError": None,
                                                                       "onNull": None}},
                                          "format": "%Y-%m-%d", "timezone": tz, "onNull": None}},
                       "$$b"]}
    if unit == "year":
        cut, pat = {"$substrCP": ["$$d", 0, 4]}, "^[0-9]{4}"
    elif unit == "month":
        cut, pat = {"$substrCP": ["$$d", 0, 7]}, "^[0-9]{4}-[0-9]{2}"
    else:
        q = {"$toInt": {"$ceil": {"$divide": [{"$toInt": {"$substrCP": ["$$d", 5, 2]}}, 3]}}}
        cut, pat = {"$concat": [{"$substrCP": ["$$d", 0, 4]}, "-Q", {"$toString": q}]}, "^[0-9]{4}-[0-9]{2}"
    inner = {"$let": {"vars": {"d": d}, "in": {"$cond": [
        {"$and": [{"$eq": [{"$type": "$$d"}, "string"]}, {"$regexMatch": {"input": {"$ifNull": ["$$d", ""]},
                                                                          "regex": pat}}]}, cut, None]}}}
    return {"$let": {"vars": {"b": v}, "in": {"$cond": [{"$eq": [{"$type": "$$b"}, "string"]}, inner, None]}}}


def bucket_label(k: str, unit: str) -> str:
    """``2025-03`` as Mar 2025, ``2025-Q1`` as Q1 2025."""
    if unit == "month" and len(k) == 7 and k[5:].isdigit() and 1 <= int(k[5:]) <= 12:
        return f"{MONTHS[int(k[5:]) - 1]} {k[:4]}"
    if unit == "quarter" and len(k) == 7:
        return f"{k[5:]} {k[:4]}"
    return k


def _next(k: str, unit: str) -> str:
    if unit == "year":
        return f"{int(k) + 1:04d}"
    y, n = int(k[:4]), int(k[6:] if unit == "quarter" else k[5:7])
    last = 4 if unit == "quarter" else 12
    y, n = (y + 1, 1) if n >= last else (y, n + 1)
    return f"{y:04d}-Q{n}" if unit == "quarter" else f"{y:04d}-{n:02d}"


def fill(keys, unit: str) -> list[str]:
    """Every bucket from the first to the last, those with nothing in them too."""
    ks = sorted(k for k in keys if k is not None)
    if not ks:
        return []
    out, k = [], ks[0]
    try:
        while k <= ks[-1] and len(out) < MAX_GROUPS:
            out.append(k)
            k = _next(k, unit)
    except ValueError:
        return ks
    return out if k > ks[-1] else ks


# ============================================================================================ the pipeline
def _walk(base, path: str, *, index: bool = True):
    """The value at ``path`` below ``base``, null when it is missing; ``index``: a digit takes a list's item (a
    field's path), else it is only a key (a condition reads paths as keys)."""
    e = base
    for p in path.split("."):
        step = {"$cond": [{"$eq": [{"$type": "$$o"}, "object"]},
                          {"$getField": {"field": {"$literal": p}, "input": "$$o"}}, None]}
        if index and p.isdigit():
            step = {"$cond": [{"$isArray": "$$o"}, {"$arrayElemAt": ["$$o", int(p)]}, step]}
        e = {"$let": {"vars": {"o": e}, "in": step}}
    return {"$ifNull": [e, None]}


def _ref(v):
    """A reference as the record it names (``Client/7``), whether written plainly or with its display or pin."""
    return {"$let": {"vars": {"r": v}, "in": {"$ifNull": [{"$cond": [
        {"$eq": [{"$type": "$$r"}, "object"]}, {"$getField": {"field": "reference", "input": "$$r"}}, "$$r"]}, None]}}}


def _num(x):
    """A number as an exact decimal, so a decimal field and a number compare by value (0.10 == 0.1)."""
    return {"$let": {"vars": {"n": x}, "in": {"$cond": [{"$isNumber": "$$n"}, {"$toDecimal": "$$n"}, "$$n"]}}}


def _lit(v):
    if isinstance(v, Decimal):
        return Decimal128(v)
    if isinstance(v, list):
        return {"$literal": [Decimal128(x) if isinstance(x, Decimal) else x for x in v]}
    return {"$literal": v}


def _truthy(v):
    return {"$let": {"vars": {"t": v}, "in": {"$and": [
        "$$t", {"$ne": ["$$t", ""]}, {"$ne": ["$$t", {"$literal": []}]}, {"$ne": ["$$t", {"$literal": {}}]}]}}}


def _paths_in(n) -> list[str]:
    out = []
    if isinstance(n, tuple) and n and n[0] == "path":
        return [n[1]]
    for child in (n[1:] if isinstance(n, tuple) else n):
        if isinstance(child, (tuple, list)):
            out += _paths_in(child)
    return out


def _reads_record(n) -> bool:
    return any(p.split(".")[0] in ("record", "row") for p in _paths_in(n))


class Where:
    """A view's condition as a database expression that holds exactly where ``expr.holds`` would over the
    member's records: ``column(path)`` gives the expression of ``record.<path>``. Raises Untranslatable."""

    def __init__(self, today: str, column):
        self.today, self.column = today, column

    def cond(self, n):
        op = n[0]
        if op == "truthy":
            return _truthy(self.val(n[1]))
        if op == "not":
            return {"$not": [self.cond(n[1])]}
        if op in ("and", "or"):
            return {f"${op}": [self.cond(n[1]), self.cond(n[2])]}
        if op == "cmp":
            return self.cmp(n[1], self.val(n[2]), self.val(n[3]))
        return _truthy(self.val(n))

    def cmp(self, sym: str, a, b):
        if sym == "==":
            return {"$eq": [_num(a), _num(b)]}
        if sym == "!=":
            return {"$ne": [_num(a), _num(b)]}
        if sym == "in":
            both = {"$and": [{"$eq": [{"$type": "$$a"}, "string"]}, {"$eq": [{"$type": "$$b"}, "string"]}]}
            return {"$let": {"vars": {"a": _num(a), "b": b}, "in": {"$cond": [
                {"$isArray": "$$b"}, {"$in": ["$$a", {"$map": {"input": "$$b", "as": "x", "in": _num("$$x")}}]},
                {"$cond": [both, {"$gte": [{"$indexOfCP": ["$$b", "$$a"]}, 0]}, False]}]}}}
        same = {"$or": [{"$and": [{"$isNumber": "$$a"}, {"$isNumber": "$$b"}]},
                        {"$and": [{"$eq": [{"$type": "$$a"}, "string"]}, {"$eq": [{"$type": "$$b"}, "string"]}]}]}
        op = {"<": "$lt", "<=": "$lte", ">": "$gt", ">=": "$gte"}[sym]
        return {"$let": {"vars": {"a": _num(a), "b": _num(b)}, "in": {"$cond": [same, {op: ["$$a", "$$b"]}, False]}}}

    def val(self, n):
        op = n[0]
        if op == "lit":
            return _lit(n[1])
        if op == "list":
            if all(x[0] == "lit" for x in n[1]):
                return _lit([x[1] for x in n[1]])
            return [self.val(x) for x in n[1]]
        if op == "path":
            root, _, rest = n[1].partition(".")
            if root in ("record", "row"):
                if not rest:
                    raise Untranslatable("a computed widget's where compares fields with values and today, not "
                                         "the whole record")
                return self.column(rest)
            if root == "today":
                return _lit(None if rest else self.today)
            return _lit(None)  # nothing else is in a view's condition
        if op in ("shift", "arith", "neg", "call"):
            if _reads_record(n):
                raise Untranslatable("a computed widget's where compares fields with values and today (a sum or a "
                                     "sum of fields is for a table or a list widget)")
            return _lit(expr.evaluate(n, {"today": self.today}))
        return self.cond(n)


class Pipeline:
    """The aggregation that works out a widget's groups over the records of ``rt`` the member may see: of its type
    and every type under it (a widget reads the fields the named type has)."""

    def __init__(self, hub, member, rt, *, today: str, tz: str = "UTC"):
        self.hub, self.member, self.rt, self.today, self.tz = hub, member, rt, today, tz
        self.coll = hub.db.records.c.name
        kin = hub.family(rt.id)
        self.stages: list[dict] = [{"$match": {"type": kin[0] if len(kin) == 1 else {"$in": kin}}}]
        self.hops: dict[str, int] = {}
        self.where_cols: dict[str, tuple[str, dict]] = {}
        self._masks: dict[str, dict] = {}

    # ---------------------------------------------------------------- what a restriction withholds
    def masks(self, rt) -> dict:
        """What is withheld of records of ``rt``'s family: each type's, together."""
        if rt.id not in self._masks:
            out: dict = {"subjects": set(), "records": set(), "fields": {}}
            for t in self.hub.family(rt.id):
                one = self.hub.restrictions.masks_for(self.member, self.hub.record_type(t) or rt)
                out["subjects"] |= set(one["subjects"])
                out["records"] |= set(one["records"])
                for fp, ss in one["fields"].items():
                    out["fields"].setdefault(fp, set()).update(ss)
            self._masks[rt.id] = out
        return self._masks[rt.id]

    @staticmethod
    def _kept(rt) -> set[str]:
        return _KEPT | {f["path"].split(".")[0] for f in rt.fields if f["type"] == "reference" and (
            f.get("rel") == "subject" or (rt.part_of or {}).get("field") == f["name"])}

    def masked(self, v, rt, path: str, subj: str, rid: str):
        """``v`` (the value at ``path`` of a record of ``rt``), or null where the member is not shown it."""
        from .visibility import _inside
        mk, conds = self.masks(rt), []
        if path.split(".")[0] not in self._kept(rt):
            if mk["subjects"]:
                conds.append({"$and": [{"$in": [subj, sorted(mk["subjects"])]}, {"$ne": [rid, subj]}]})
            if mk["records"]:
                conds.append({"$in": [{"$concat": [rid, "|", {"$ifNull": [subj, ""]}]}, sorted(mk["records"])]})
        subs = sorted({s for fp, ss in mk["fields"].items()
                       if path == fp or _inside(path, fp) or _inside(fp, path) for s in ss})
        if subs:
            conds.append({"$in": [subj, subs]})
        return {"$cond": [{"$or": conds}, None, v]} if conds else v

    # ---------------------------------------------------------------- values
    def _hop(self, f: dict) -> int:
        if f["name"] not in self.hops:
            i = len(self.hops)
            ref = self.masked(_ref(_walk("$data", f["path"])), self.rt, f["path"], "$subject", "$_id")
            self.stages += [
                {"$set": {f"r{i}": ref}},
                {"$lookup": {"from": self.coll, "localField": f"r{i}", "foreignField": "_id",
                             "pipeline": [{"$project": {"type": 1, "subject": 1, "data": 1}}], "as": f"h{i}"}},
                {"$set": {f"h{i}": {"$arrayElemAt": [f"$h{i}", 0]}}}]
            self.hops[f["name"]] = i
        return self.hops[f["name"]]

    def read(self, col: Col):
        """A column's value in each record (a table's column: its values in the record's rows), masked."""
        if col.hop:
            i = self._hop(col.hop)
            base = {"$cond": [{"$in": [f"$h{i}.type", self.hub.family(col.target)]}, f"$h{i}.data", None]}
            rt, subj, rid = self.hub.record_type(col.target), f"$h{i}.subject", f"$h{i}._id"
        else:
            base, rt, subj, rid = "$data", self.rt, "$subject", "$_id"
        if col.table:
            tpath, _, name = col.path.rpartition(".*.")
            rows = _walk(base, tpath)
            v = {"$map": {"input": {"$cond": [{"$isArray": rows}, rows, []]}, "as": "row",
                          "in": _walk("$$row", name)}}
        else:
            v = _walk(base, col.path)
            if col.type == "reference":
                v = _ref(v)
        return self.masked(v, rt, col.path, subj, rid)

    def key(self, col: Col):
        v = self.read(col)
        return _bucket_expr(v, col.bucket, self.tz, col.type == "datetime") if col.bucket else v

    def measure(self, fn: str, col: Col):
        v = self.read(col)

        def ok(x):
            return {"$isNumber": x} if col.type in NUMERIC else {"$eq": [{"$type": x}, "string"]}
        if col.table:
            return {"$let": {"vars": {"cells": v}, "in": {"$cond": [
                {"$isArray": "$$cells"},
                {f"${'sum' if fn == 'sum' else fn}": {"$filter": {"input": "$$cells", "as": "c", "cond": ok("$$c")}}},
                None]}}}
        return {"$let": {"vars": {"m": v}, "in": {"$cond": [ok("$$m"), "$$m", None]}}}

    def where_col(self, path: str):
        if path not in self.where_cols:
            v = self.masked(_walk("$data", path, index=False), self.rt, path, "$subject", "$_id")
            self.where_cols[path] = (f"w{len(self.where_cols)}", v)
        return "$" + self.where_cols[path][0]

    # ---------------------------------------------------------------- the stages
    def build(self, cols: dict, node=None, *, group: bool = True) -> list[dict]:
        """The pipeline; ``group=False``: each record's projected row (``g``, ``s``, ``x``, ``m``), for checking."""
        proj: dict = {"_id": 0}
        for k in ("g", "s", "x"):
            proj[k] = self.key(cols[k]) if cols.get(k) else {"$literal": None}
        proj["m"] = self.measure(cols["fn"], cols["m"]) if cols.get("m") else {"$literal": None}
        match = Where(self.today, self.where_col).cond(node) if node is not None else None
        for name, e in self.where_cols.values():
            proj[name] = e
        out = [*self.stages, {"$project": proj}]
        if match is not None:
            out.append({"$match": {"$expr": match}})
        if group:
            out += [{"$group": {"_id": {"g": "$g", "s": "$s", "x": "$x"}, "n": {"$sum": 1}, "sum": {"$sum": "$m"},
                                "c": {"$sum": {"$cond": [{"$isNumber": "$m"}, 1, 0]}},
                                "lo": {"$min": "$m"}, "hi": {"$max": "$m"}}},
                    {"$sort": {"_id": 1}}, {"$limit": MAX_GROUPS + 1}]
        return out


# ============================================================================================ groups in Python
def _hkey(v):
    n = decimals.number(v)
    return ("n", n) if n is not None else ("v", json.dumps(v, sort_keys=True, default=str))


def _add(a, b):
    """a + b: exactly when either is a decimal (a float through its text), else as Python adds."""
    if a is None:
        return b
    if b is None:
        return a
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        return decimals.CTX.add(Decimal(decimals.number(a)), Decimal(decimals.number(b)))
    return a + b


def _sum(nums: list):
    if any(isinstance(n, Decimal) for n in nums):
        total = Decimal(0)
        for n in nums:
            total = decimals.CTX.add(total, Decimal(decimals.number(n)))
        return total
    if any(isinstance(n, float) for n in nums):
        return math.fsum(nums)  # as the database adds floats
    return sum(nums)


def group_rows(rows) -> list[dict]:
    """Rows of ``g``, ``s``, ``x`` and ``m`` in groups, as the pipeline's ``$group`` makes them (the signatures
    source, worked out here, and the check that the two agree)."""
    acc: dict = {}
    for r in rows:
        k = {"g": r.get("g"), "s": r.get("s"), "x": r.get("x")}
        a = acc.setdefault(tuple(_hkey(k[x]) for x in ("g", "s", "x")), {"_id": k, "n": 0, "vals": []})
        a["n"] += 1
        if r.get("m") is not None:
            a["vals"].append(r["m"])
    out = []
    for a in acc.values():
        nums = [v for v in a["vals"] if decimals.number(v) is not None]
        out.append({"_id": a["_id"], "n": a["n"], "sum": _sum(nums), "c": len(nums),
                    "lo": min(a["vals"], default=None), "hi": max(a["vals"], default=None)})
    return out


def merge(accs) -> dict:
    out = {"n": 0, "sum": 0, "c": 0, "lo": None, "hi": None}
    for a in accs:
        out["n"] += a["n"]
        out["sum"] = _add(out["sum"], a["sum"])
        out["c"] += a["c"]
        for k, pick in (("lo", min), ("hi", max)):
            if a[k] is not None:
                out[k] = a[k] if out[k] is None else pick(out[k], a[k])
    return out


def _exact(v, scale) -> Decimal:
    d = Decimal(decimals.number(v)) if not isinstance(v, Decimal) else v
    return d.quantize(Decimal(1).scaleb(-int(scale or 0)), context=decimals.CTX)


def value_of(fn: str | None, col: Col | None, acc: dict | None):
    """A group's number: how many, or the measure (exact for a decimal field)."""
    if acc is None:  # nothing in the group: none of them, a sum of nothing; no average, least or most
        if fn == "sum" and col is not None and col.type == "decimal":
            return _exact(0, (col.field or {}).get("scale"))
        return 0 if fn in (None, "sum") else None
    if fn is None:
        return acc["n"]
    if fn == "avg":
        if not acc["c"]:
            return None
        if col.type != "decimal":
            return float(acc["sum"]) / acc["c"]
        v = decimals.CTX.divide(Decimal(decimals.number(acc["sum"])), Decimal(acc["c"]))
    else:
        v = acc["sum" if fn == "sum" else "lo" if fn == "min" else "hi"]
    if v is None:
        return None
    if col.type == "decimal":
        return _exact(v, (col.field or {}).get("scale"))
    return v


def text_of(v, unit: str = "") -> str:
    """A number as a person reads it: 12,345.60 CAD, 14%; a date as it is; nothing as a dash."""
    if v is None:
        return "–"
    if isinstance(v, bool) or not isinstance(v, (int, float, Decimal)):
        return str(v)
    if isinstance(v, Decimal):
        t = format(v, ",f")
    elif isinstance(v, int):
        t = format(v, ",")
    else:
        t = format(Decimal(repr(round(v, 6))), ",f")
    return t + ("%" if unit == "%" else f" {unit}" if unit else "")


# ============================================================================================ labels
class Labels:
    """A group's value in words, for this member: a record by its title (when they may read it), a member by name,
    a choice or state in its words, a bucket as Mar 2025."""

    def __init__(self, hub, member):
        self.hub, self.member, self.seen = hub, member, []
        self._titles: dict[str, str] = {}

    def title(self, ref: str) -> str:
        if ref in self._titles:
            return self._titles[ref]
        hub, t, rid = self.hub, *ref.partition("/")[::2]
        out = ref
        if rid and t in hub.member_types():
            out = self.member_name(rid)
        elif rid and hub.record_type(t) and hub.may_read(self.member, t):
            rec = hub.records.read(t, rid)
            if rec:
                red = hub.redact(self.member, rec)
                self.seen.append(red)
                out = hub.short_title(red)
        self._titles[ref] = out
        return out

    def member_name(self, mid) -> str:
        mid = str(mid).removeprefix("member:")
        try:
            return self.hub.member(mid)["name"]
        except Exception:  # noqa: BLE001 - gone: its id
            return mid

    def __call__(self, col: Col, k) -> str:
        if k is None:
            return "none"
        if col.bucket:
            return bucket_label(str(k), col.bucket)
        if col.type == "reference":
            return self.title(str(k))
        if col.type == "member":
            return self.member_name(k)
        if col.type == "choice" and col.field is not None:
            rt = self.hub.record_type(col.owner)
            if rt is not None and rt.lifecycle and col.field["path"] == rt.lifecycle["path"]:
                return rt.state_label(k)
            words = schema.choice_words(col.field, k)
            return schema.state_words(words) if words == str(k) and re.fullmatch(r"[a-z][a-z0-9_-]*", words) else words
        if isinstance(k, bool):
            return "Yes" if k else "No"
        if decimals.number(k) is not None:
            return text_of(decimals.number(k))
        return str(k)

    def tag(self, col: Col, k) -> str:
        """What tells apart two values with the same words: a record's key, else the value itself."""
        return (self.hub.key_of(str(k)) or str(k)) if col.type == "reference" else str(k)

    def names(self, col: Col, keys) -> dict:
        out = {k: self(col, k) for k in keys}
        same = Counter(out.values())
        return {k: (f"{v} ({self.tag(col, k)})" if same[v] > 1 and k is not None else v) for k, v in out.items()}


def order(hub, col: Col, keys, names: dict) -> list:
    """A chart's bars: a bucket's in time, a lifecycle's in its order, others by their words; none last."""
    if col.bucket:
        return sorted(keys, key=lambda k: (k is None, str(k or "")))
    rank: dict = {}
    if col.type == "choice":
        rt = hub.record_type(col.owner)
        if rt is not None and rt.lifecycle and (col.field or {}).get("path") == rt.lifecycle["path"]:
            rank = {s: i for i, s in enumerate(rt.states())}
    return sorted(keys, key=lambda k: (k is None, rank.get(k, len(rank)), names[k].casefold()))


# ============================================================================================ the widget's numbers
def finish(hub, w: dict, cols: dict, groups: list[dict], labels, *, truncated: bool = False) -> dict:
    """The computed part of a widget from its groups: ``stat`` (a count), ``counts`` and ``texts`` (a chart, by
    label), ``line`` (``{"x", "series": [{"name", "values", "texts"}], "unit", "measure"}``)."""
    fn, mc = cols["fn"], cols["m"]
    unit = mc.unit if mc else ""
    out = {"computed": True, "total": sum(g["n"] for g in groups), "rows": [], "counts": {}, "texts": {},
           "stat": None, "line": None, "aside": None}
    asides = [f"only the first {MAX_GROUPS} groups are shown"] if truncated else []
    if w["kind"] == "count":
        v = value_of(fn, mc, merge(groups) if groups else None)
        out["number"], out["stat"] = v, text_of(v, unit)
    elif w["kind"] == "chart":
        gc = cols["g"]
        by: dict = {}
        for g in groups:
            by.setdefault(_hkey(g["_id"]["g"]), []).append(g)
        accs = {v[0]["_id"]["g"]: merge(v) for v in by.values()}
        keys = list(accs)
        if gc.bucket:
            keys = fill(keys, gc.bucket) + ([None] if None in accs else [])
        names = labels.names(gc, keys)
        for k in order(hub, gc, keys, names):
            v = value_of(fn, mc, accs.get(k))
            out["counts"][names[k]] = v
            out["texts"][names[k]] = text_of(v, unit)
    else:
        xc, sc = cols["x"], cols["s"]
        dropped = sum(g["n"] for g in groups if g["_id"]["x"] is None)
        if dropped:
            asides.append(f"{dropped} with no {xc.label.lower()} {'is' if dropped == 1 else 'are'} not drawn")
        xs = fill({g["_id"]["x"] for g in groups}, xc.bucket)
        cells: dict = {}
        for g in groups:
            if g["_id"]["x"] is not None:
                cells.setdefault(_hkey(g["_id"]["s"]), {"key": g["_id"]["s"], "at": {}})["at"].setdefault(
                    g["_id"]["x"], []).append(g)
        series = list(cells.values())
        if sc is not None:
            def size(s):
                total = merge([a for v in s["at"].values() for a in v])
                return value_of(fn, mc, total) if fn in (None, "sum") else total["n"]
            series.sort(key=lambda s: -(decimals.number(size(s)) or 0))
            if len(series) > MAX_SERIES:
                rest, series = series[MAX_SERIES - 1:], series[:MAX_SERIES - 1]
                other = {"key": OTHER, "other": True, "at": {}}
                for s in rest:
                    for x, v in s["at"].items():
                        other["at"].setdefault(x, []).extend(v)
                series.append(other)
            names = labels.names(sc, [s["key"] for s in series if not s.get("other")])
        else:
            names = {}
        drawn = []
        for s in series or ([{"key": None, "at": {}}] if xs else []):
            vals = [value_of(fn, mc, merge(s["at"][x]) if x in s["at"] else None) for x in xs]
            name = OTHER if s.get("other") else names.get(s["key"]) if sc is not None else (
                f"{_WORDS[fn]} of {mc.label.lower()}" if fn else "How many")
            drawn.append({"name": name, "values": vals, "texts": [text_of(v, unit) for v in vals]})
        out["line"] = {"x": [bucket_label(k, xc.bucket) for k in xs], "keys": xs, "series": drawn, "unit": unit,
                       "measure": f"{_WORDS[fn]} of {mc.label.lower()}" if fn else "How many"}
    out["aside"] = "; ".join(asides) or None
    return out


def empty(note: str) -> dict:
    return {"computed": True, "note": note, "total": 0, "rows": [], "counts": {}, "texts": {}, "stat": None,
            "line": None, "aside": None}


def _rights(hub, member, rt, cols: dict, where_paths: list[str]) -> str | None:
    from .visibility import hides_path
    for c in (cols["m"], cols["g"], cols["s"], cols["x"]):
        if c is None:
            continue
        if c.hop:
            if not hub.may_read(member, c.target):
                return f"needs access to {hub.class_of(c.target)} records"
            f = hides_path(hub, member, rt.id, c.hop["path"]) or hides_path(hub, member, c.target, c.path)
        else:
            f = hides_path(hub, member, rt.id, c.path)
        if f:
            return f"needs access to {f.get('data_class')} records"
    for p in where_paths:
        f = hides_path(hub, member, rt.id, p)
        if f:
            return f"needs access to {f.get('data_class')} records"
    return None


def records_numbers(hub, member, w: dict) -> tuple[dict, list[dict]]:
    """A records widget's numbers for the member, and the records whose titles it shows (to be noted as listed)."""
    from . import clock
    from .records import LocalRecordStore
    rt = hub.record_type(w["type"]) if w.get("type") else None
    if rt is None:
        return empty(f"there is no record type {w.get('type')!r}"), []
    if not hub.may_read(member, rt.id):
        return empty(f"needs access to {hub.class_of(rt.id)} records"), []
    if not isinstance(hub.records, LocalRecordStore):
        return empty("it is worked out in the hub's own record store, and this hub keeps its records elsewhere"), []
    try:
        cols = columns_of(hub.record_type, rt, w)
        node = expr.Parser(w["where"]).parse() if w.get("where") else None
    except (ValueError, expr.ExprError) as exc:
        return empty(f"it cannot be worked out: {exc}"), []
    wpaths = [p.split(".", 1)[1] for p in (_paths_in(node) if node else [])
              if p.split(".")[0] in ("record", "row") and "." in p]
    note = _rights(hub, member, rt, cols, wpaths)
    if note:
        return empty(note), []
    tz = "UTC"
    if any(c is not None and c.bucket and c.type == "datetime" for c in (cols["g"], cols["s"], cols["x"])):
        try:
            tz = hub.engine.zone().key
        except clock.ZoneError as exc:
            return empty(str(exc)), []
    p = Pipeline(hub, member, rt, today=hub.engine.now().date().isoformat(), tz=tz)
    try:
        stages = p.build(cols, node)
    except Untranslatable as exc:
        return empty(str(exc)), []
    groups = list(hub.db.records.aggregate(stages))
    labels = Labels(hub, member)
    out = finish(hub, w, cols, groups[:MAX_GROUPS], labels, truncated=len(groups) > MAX_GROUPS)
    out["note"] = None
    return out, labels.seen


# ============================================================================================ sign-offs
def signature_col(text: str) -> Col:
    """What a sign-offs widget's path reads of each sign-off (``views.ROW_FIELDS["signatures"]``). Raises
    ValueError, in words."""
    from .views import ROW_FIELDS
    path, bucket = parse_path(text)
    fields = [f for f in ROW_FIELDS["signatures"] if f.get("filter") is not False]
    f = next((f for f in fields if f["path"] == path), None)
    if f is None:
        import difflib
        near = difflib.get_close_matches(path, [g["path"] for g in fields], n=1)
        raise ValueError(f"a sign-off has no {path!r}" + (f" (did you mean {near[0]!r}?)" if near else
                                                         f" (it has: {', '.join(g['path'] for g in fields)})"))
    if bucket and f["type"] not in DATES:
        raise ValueError(f"{text!r}: only a date or a time is grouped by {bucket} ({f['label']} is {f['type']})")
    return Col(text=text, path=path, label=f["label"], owner="", type=f["type"], field=f, bucket=bucket)


def signature_columns(w: dict) -> dict:
    """``columns_of`` for a sign-offs widget: a sign-off is counted, so there is no measure."""
    if w.get("measure"):
        raise ValueError("measure reads the fields of records; a sign-off is counted")
    out = {"fn": None, "m": None, "g": None, "s": None, "x": None}
    for key, name in (("group_by", "g"), ("series", "s"), ("x", "x")):
        if w.get(key) and not (key == "group_by" and w["kind"] not in ("chart", "board")):
            out[name] = signature_col(w[key])
    return out


def signature_numbers(hub, member, w: dict, rows: list[dict], note: str | None) -> tuple[dict, list[dict]]:
    """A sign-offs widget's numbers from the sign-offs the member may see (``views.signature_rows``), grouped here
    as the database groups records: by who signed, the record (its title), its type (in words), a date by month.
    Returns them and the records whose titles they show."""
    from . import clock
    try:
        cols = signature_columns(w)
    except ValueError as exc:
        return empty(f"it cannot be worked out: {exc}"), []
    tz = None
    if any(c is not None and c.bucket for c in (cols["g"], cols["s"], cols["x"])):
        try:
            tz = hub.engine.zone()
        except clock.ZoneError as exc:
            return empty(str(exc)), []

    def key(c, r):
        if c is None:
            return None
        v = r.get(c.path)
        if c.bucket:
            return bucket_of(v, c.bucket, tz, aware=True)
        if c.path == "type":
            rt = hub.record_type(v or "")
            return rt.label if rt else v
        return v

    groups = group_rows({"g": key(cols["g"], r), "s": key(cols["s"], r), "x": key(cols["x"], r), "m": None}
                        for r in rows)
    labels = Labels(hub, member)
    out = finish(hub, w, cols, groups[:MAX_GROUPS], labels, truncated=len(groups) > MAX_GROUPS)
    out["note"] = note
    return out, labels.seen
