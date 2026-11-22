"""Views and dashboards: screens a practice or a module defines as data, under change control (``kind = "view"``).

    id = "results-today"
    title = "Results to review"
    nav = true                     # show it in the sidebar
    roles = ["physician"]          # who sees it in the sidebar (empty: everyone); records still need read access
    summary = "What the lab sent today"

    [[widget]]
    kind = "count"                 # count | table | list | board | chart | tree (records of a type with a parent)
    title = "Waiting for a physician"
    source = "items"               # items | runs | records
    workflow = "lab-result-review" # items and runs: of this workflow
    status = ["open", "claimed"]   # items: open claimed held done; runs: running completed cancelled
    role = "physician"             # items: for this role

    [[widget]]
    kind = "table"
    source = "records"
    type = "Observation"
    where = "record.status == 'final'"     # a condition over ``record`` (records) or ``row`` (items and runs), and
                                           # ``today`` (the practice's date: ``record.due <= today + 7d``)
    columns = [{ label = "Test", path = "code.text" }, { label = "Value", path = "valueQuantity.value" }]
    group_by = "status"            # board and chart: one column or bar per value
    sort = "valueQuantity.value"   # table and list: the field the rows are ordered by (else newest first)
    descending = true              # with sort: largest or latest first
    limit = 25
    grid = { x = 0, y = 2, w = 8, h = 4 }  # where it sits on a 12-column grid (x, w: columns; y, h: rows)

    [[widget]]
    kind = "links"                 # links: places to go, not records
    title = "Shortcuts"
    links = [{ label = "Results", href = "/records/Observation" }]

Every widget is computed for the member looking at it: records only from data classes they may read, items and
runs only with what visibility allows.

The graphical editor (``practicehub/web/dashboard_pages.py``) works on the document ``doc_of`` reads from the text
and writes it back with ``to_toml``; what it offers (kinds, sources, operators) is ``editor_spec``, from the lists
below, so the editor and the checks never disagree. A filter it builds with pickers is ``{"match": "all"|"any",
"rules": [{"path", "op", "value"|"today"}]}``, written as a ``where`` condition by ``where_of`` and read back by
``where_parts`` (None when a condition says more than pickers can: it stays as written).
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field

from . import expr
from . import schema
from .workflows import DefinitionError

ID = re.compile(r"[a-z][a-z0-9\-]*")
WIDGETS = ("count", "table", "list", "board", "chart", "tree", "links")
SOURCES = ("items", "runs", "records")
ITEM_STATUS = ("open", "claimed", "held", "done", "cancelled")
RUN_STATUS = ("running", "completed", "cancelled")
RESERVED = {"new"}
COLUMNS = 12          # the grid's columns
MAX_ROWS = 200        # how far down a widget may sit
MAX_HEIGHT = 24       # rows one widget may span
HREF = re.compile(r"(/(?!/)[^\s\"'<>]*|https://[^\s\"'<>]+)")  # a page of the hub, or a secure address elsewhere

# What each kind of widget shows and which keys it uses: the editor's palette and side panel are built from this.
KINDS = {
    "count": {"label": "Number", "about": "How many records or items match", "sources": SOURCES,
              "options": ["filter"], "size": [3, 2]},
    "table": {"label": "Table", "about": "Rows with the columns you choose", "sources": SOURCES,
              "options": ["filter", "columns", "sort", "limit"], "size": [12, 4]},
    "list": {"label": "List", "about": "A short list: each row's first column, the others beside it",
             "sources": SOURCES, "options": ["filter", "columns", "sort", "limit"], "size": [6, 4]},
    "board": {"label": "Board", "about": "A column per value, with a card per row", "sources": SOURCES,
              "options": ["filter", "group_by", "columns", "limit"], "size": [12, 5]},
    "chart": {"label": "Bar chart", "about": "How many rows have each value", "sources": SOURCES,
              "options": ["filter", "group_by"], "size": [6, 4]},
    "tree": {"label": "Tree", "about": "Records of a type with a parent, as an outline", "sources": ("records",),
             "options": ["filter", "columns", "limit"], "size": [6, 5]},
    "links": {"label": "Links", "about": "Places to go: other views, lists, pages", "sources": (),
              "options": ["links"], "size": [4, 3]},
}
SOURCE_LABELS = {"records": "Records", "items": "Work items", "runs": "Workflow runs"}
# What a filter over work items or runs may look at (records: the fields of their type).
ROW_FIELDS = {
    "items": [{"name": "title", "path": "title", "label": "Title", "type": "string"},
              {"name": "role", "path": "role", "label": "Role", "type": "role"},
              {"name": "kind", "path": "kind", "label": "Kind", "type": "choice",
               "choices": ["task", "gate", "notice", "hold"]},
              {"name": "priority", "path": "priority", "label": "Priority", "type": "string"},
              {"name": "claimed_by", "path": "claimed_by", "label": "Claimed by", "type": "string"},
              {"name": "run_title", "path": "run_title", "label": "About", "type": "string"},
              {"name": "created_at", "path": "created_at", "label": "Created", "type": "datetime"},
              {"name": "due_at", "path": "due_at", "label": "Due", "type": "datetime"}],
    "runs": [{"name": "title", "path": "title", "label": "Title", "type": "string"},
             {"name": "workflow_id", "path": "workflow_id", "label": "Workflow", "type": "string"},
             {"name": "status", "path": "status", "label": "Status", "type": "choice", "choices": list(RUN_STATUS)},
             {"name": "started_by", "path": "started_by", "label": "Started by", "type": "string"},
             {"name": "created_at", "path": "created_at", "label": "Started", "type": "datetime"}],
}
# Filter operators, as the editor names them, and the condition each writes.
# (a date field's editor says "is before" for <, "is on or after" for >=...)
OPS = {"==": "is", "!=": "is not", "in": "is one of", "not in": "is none of", "<": "is less than",
       "<=": "is at most", ">": "is more than", ">=": "is at least", "empty": "is empty", "set": "is not empty"}


@dataclass
class View:
    id: str
    title: str
    summary: str = ""
    nav: bool = True
    roles: list[str] = field(default_factory=list)
    widgets: list[dict] = field(default_factory=list)
    source: str = ""


def editor_spec() -> dict:
    """What the graphical editor offers, from this module's own lists."""
    return {"kinds": {k: {**v, "sources": list(v["sources"])} for k, v in KINDS.items()},
            "sources": SOURCE_LABELS, "item_status": list(ITEM_STATUS), "run_status": list(RUN_STATUS),
            "row_fields": ROW_FIELDS, "ops": OPS, "columns": COLUMNS, "max_height": MAX_HEIGHT}


def _grid(g, where: str, problems: list[str]) -> dict | None:
    if g is None:
        return None
    if not isinstance(g, dict) or not all(isinstance(g.get(k), int) and not isinstance(g.get(k), bool)
                                          for k in ("x", "y", "w", "h")):
        problems.append(f"{where}: grid is {{ x = column, y = row, w = columns wide, h = rows high }}, in whole numbers")
        return None
    x, y, w, h = g["x"], g["y"], g["w"], g["h"]
    if not (0 <= x < COLUMNS and 1 <= w <= COLUMNS and x + w <= COLUMNS):
        problems.append(f"{where}: grid: it must fit the {COLUMNS} columns (x from 0, x + w at most {COLUMNS})")
    if not (0 <= y <= MAX_ROWS and 1 <= h <= MAX_HEIGHT):
        problems.append(f"{where}: grid: y is 0 to {MAX_ROWS} and h 1 to {MAX_HEIGHT}")
    return {"x": x, "y": y, "w": w, "h": h}


def widget(w, i: int) -> tuple[dict | None, list[str]]:
    """One widget as the hub uses it, and what is wrong with it (in words, naming it)."""
    problems: list[str] = []
    if not isinstance(w, dict):
        return None, [f"widget {i + 1}: a widget is a table of keys ([[widget]])"]
    where = f"widget {w.get('title') or i + 1}"
    kind = w.get("kind", "table")
    src = None if kind == "links" else w.get("source", "records")
    if kind not in WIDGETS:
        problems.append(f"{where}: kind is one of {', '.join(WIDGETS)}")
    if kind != "links" and src not in SOURCES:
        problems.append(f"{where}: source is one of {', '.join(SOURCES)}")
    if src == "records" and not w.get("type"):
        problems.append(f"{where}: say which record type it shows")
    status = w.get("status") or []
    status = [status] if isinstance(status, str) else list(status)
    allowed = ITEM_STATUS if src == "items" else RUN_STATUS if src == "runs" else None
    if allowed:
        for s in status:
            if s not in allowed:
                problems.append(f"{where}: status {s!r} is not one of {', '.join(allowed)}")
    if w.get("where"):
        try:
            expr.check(w["where"])
        except expr.ExprError as exc:
            problems.append(f"{where}: where: {exc}")
    if kind == "tree" and src != "records":
        problems.append(f"{where}: a tree shows records")
    if kind in ("board", "chart") and not w.get("group_by"):
        problems.append(f"{where}: a {kind} needs group_by: the path whose values make its columns")
    cols = []
    for c in w.get("columns", []) or []:
        if isinstance(c, str):
            c = {"path": c}
        if not isinstance(c, dict) or not c.get("path"):
            problems.append(f"{where}: every column needs a path")
            continue
        cols.append({"label": c.get("label") or c["path"].split(".")[-1].replace("_", " ").capitalize(),
                     "path": c["path"]})
    links = []
    for ln in w.get("links", []) or []:
        if not isinstance(ln, dict) or not ln.get("label") or not HREF.fullmatch(str(ln.get("href", ""))):
            problems.append(f"{where}: every link needs a label and an href: a page of the hub (/...) or an "
                            f"https:// address")
            continue
        links.append({"label": str(ln["label"]), "href": str(ln["href"])})
    if kind == "links" and not links:
        problems.append(f"{where}: a links widget needs at least one link")
    sort = w.get("sort")
    if sort is not None and (not isinstance(sort, str) or not sort):
        problems.append(f"{where}: sort is the path of the field the rows are ordered by")
        sort = None
    try:
        limit = int(w.get("limit", 500 if kind == "tree" else 25))
    except (TypeError, ValueError):
        problems.append(f"{where}: limit is a whole number")
        limit = 25
    grid = _grid(w.get("grid"), where, problems)
    return ({"kind": kind, "source": src, "title": str(w.get("title", "")), "type": w.get("type"),
             "workflow": w.get("workflow"), "status": status, "role": w.get("role"), "where": w.get("where"),
             "columns": cols, "group_by": w.get("group_by"), "sort": sort, "descending": bool(w.get("descending")),
             "limit": limit, "links": links, "grid": grid}, problems)


def parse(source: str, catalog=None) -> View:
    try:
        doc = tomllib.loads(source)
    except tomllib.TOMLDecodeError as exc:
        raise DefinitionError([f"The view is not valid TOML: {exc}"]) from None
    problems = []
    vid = str(doc.get("id", ""))
    if not ID.fullmatch(vid) or vid in RESERVED:
        problems.append("id: give the view an id of lower-case letters, digits and dashes (not 'new')")
    if not doc.get("title"):
        problems.append("title: give the view a title")
    widgets = []
    for i, w in enumerate(doc.get("widget", []) or []):
        one, wrong = widget(w, i)
        problems += wrong
        if one:
            widgets.append(one)
    if not widgets:
        problems.append("widget: a view needs at least one widget")
    v = View(id=vid, title=str(doc.get("title", "")), summary=str(doc.get("summary", "")).strip(),
             nav=bool(doc.get("nav", True)), roles=list(doc.get("roles", []) or []), widgets=widgets, source=source)
    if catalog is not None:
        try:
            problems += [p for p in catalog.check_view(v) if p not in problems]
        except Exception:  # noqa: BLE001
            pass
    if problems:
        raise DefinitionError(problems)
    return v


def placed(widgets: list[dict]) -> list[dict]:
    """Each widget's place on the grid: its own, else the next free spot below the placed ones, side by side while
    they fit a row, in their kind's usual size."""
    bottom = max([g["y"] + g["h"] for g in (w.get("grid") for w in widgets) if g] or [0])
    out, x, y, row_h = [], 0, bottom, 0
    for w in widgets:
        if w.get("grid"):
            out.append(dict(w["grid"]))
            continue
        gw, gh = KINDS.get(w.get("kind", "table"), KINDS["table"])["size"]
        if x + gw > COLUMNS:
            x, y, row_h = 0, y + row_h, 0
        out.append({"x": x, "y": y, "w": gw, "h": gh})
        x, row_h = x + gw, max(row_h, gh)
    return out


# ============================================================================================ the editor's document
TOP = ("id", "title", "summary", "nav", "roles")
WIDGET_KEYS = ("kind", "title", "source", "type", "workflow", "role", "status", "where", "columns", "group_by",
               "sort", "descending", "limit", "links", "grid")
_BARE = re.compile(r"[A-Za-z0-9_-]+")


def _tk(k: str) -> str:
    return k if _BARE.fullmatch(k) else json.dumps(k, ensure_ascii=False)


def _tv(v) -> str:
    """A TOML value: JSON's strings, numbers and arrays are TOML's; a table is written inline."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_tv(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_tk(str(k))} = {_tv(x)}" for k, x in v.items()) + " }" if v else "{}"
    return json.dumps(v if isinstance(v, str) else str(v), ensure_ascii=False)


def _prefix(source: str | None) -> str:
    return "row." if source in ("items", "runs") else "record."


def doc_of(source: str) -> dict:
    """The editor's document from a view's text (the practice's own part): its keys as written, each widget's
    filter as pickers when its condition is one they can show. Invalid TOML raises DefinitionError."""
    try:
        doc = tomllib.loads(schema.own_text(source) if source.strip() else "")
    except tomllib.TOMLDecodeError as exc:
        raise DefinitionError([f"The view is not valid TOML: {exc}"]) from None
    out = {k: doc[k] for k in doc if k != "widget"}
    widgets = []
    for w in doc.get("widget", []) or []:
        if not isinstance(w, dict):
            continue
        w = dict(w)
        if isinstance(w.get("status"), str):
            w["status"] = [w["status"]]
        cols = []
        for c in w.get("columns", []) or []:
            cols.append({"path": c} if isinstance(c, str) else dict(c) if isinstance(c, dict) else c)
        if "columns" in w:
            w["columns"] = cols
        if w.get("where"):
            parts = where_parts(str(w["where"]), w.get("source", "records"))
            if parts:
                w["filter"] = parts
        widgets.append(w)
    out["widget"] = widgets
    return out


def to_toml(doc: dict) -> str:
    """The view's text from the editor's document. A widget's ``filter`` (pickers) is written as its ``where``;
    defaults are left out, so a view read and written back reads the same."""
    out: list[str] = []
    for k in TOP:
        v = doc.get(k)
        if k == "nav":
            if v is False:
                out.append("nav = false")
        elif k == "roles":
            if v:
                out.append(f"roles = {_tv(list(v))}")
        elif v not in (None, ""):
            out.append(f"{k} = {_tv(v)}")
    for k, v in doc.items():
        if k not in TOP and k != "widget" and v is not None:
            out.append(f"{_tk(k)} = {_tv(v)}")
    for w in doc.get("widget", []) or []:
        w = dict(w)
        if "filter" in w:
            f = w.pop("filter")
            w["where"] = where_of(f, w.get("source", "records")) if f else ""
        out.extend(["", "[[widget]]"])
        kind = w.get("kind", "table")
        for k in WIDGET_KEYS + tuple(x for x in w if x not in WIDGET_KEYS):
            v = w.get(k)
            if v is None or v == "" or v == [] or (k == "descending" and not v):
                continue
            if k == "source" and (v == "records" or kind == "links"):
                continue
            if kind == "links" and k in ("type", "workflow", "role", "status", "where", "columns", "group_by",
                                         "sort", "descending", "limit"):
                continue
            if k == "columns":  # a column with no label of its own is just its path
                v = [c["path"] if isinstance(c, dict) and set(c) == {"path"} or
                     isinstance(c, dict) and set(c) == {"path", "label"} and not c["label"] else c for c in v]
            if k == "grid" and isinstance(v, dict):
                v = {g: v[g] for g in ("x", "y", "w", "h") if g in v}
            out.append(f"{_tk(k)} = {_tv(v)}")
    return "\n".join(out).strip() + "\n"


# ============================================================================================ filters as pickers
def _lit(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    raise DefinitionError([f"a filter value cannot hold both kinds of quote: {s}"])


SHIFT = re.compile(r"([+-])\s*(\d{1,4})\s*([dwmy])")
PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_\-]*(\.[A-Za-z0-9_\-]+)*")


def where_of(f: dict, source: str | None = "records") -> str:
    """The condition a filter of pickers stands for: ``record.status == 'final' and record.due <= today + 7d``."""
    pre, parts = _prefix(source), []
    for r in (f or {}).get("rules") or []:
        path, op = str(r.get("path") or ""), r.get("op", "==")
        if not path:
            continue
        if not PATH.fullmatch(path) or op not in OPS:
            raise DefinitionError([f"a filter cannot use {path!r} {op!r}"])
        left = pre + path
        if op == "empty":
            parts.append(f"{left} == null")
            continue
        if op == "set":
            parts.append(f"{left} != null")
            continue
        if "today" in r:
            shift = str(r.get("today") or "").replace(" ", "")
            m = SHIFT.fullmatch(shift)
            if shift and not m:
                raise DefinitionError([f"a filter's date is today, or today moved by days, weeks, months or years "
                                       f"(+7d, -2w): not {shift!r}"])
            right = "today" + (f" {m.group(1)} {m.group(2)}{m.group(3)}" if m else "")
        elif op in ("in", "not in"):
            vals = r.get("value")
            vals = vals if isinstance(vals, list) else [x.strip() for x in str(vals or "").split(",") if x.strip()]
            right = "[" + ", ".join(_lit(x) for x in vals) + "]"
        else:
            right = _lit(r.get("value"))
        if op == "not in":
            parts.append(f"not {left} in {right}")
        else:
            parts.append(f"{left} {op} {right}")
    join = " or " if (f or {}).get("match") == "any" else " and "
    return join.join(parts)


def _rule(node, pre: str) -> dict | None:
    neg = False
    if node[0] == "not":
        neg, node = True, node[1]
        if node[0] != "cmp" or node[1] != "in":
            return None
    if node[0] != "cmp" or node[2][0] != "path" or not node[2][1].startswith(pre):
        return None
    op, path, right = node[1], node[2][1][len(pre):], node[3]
    if right[0] == "lit":
        if right[1] is None and op in ("==", "!="):
            return {"path": path, "op": "empty" if op == "==" else "set"}
        if op == "in":
            return None
        return {"path": path, "op": op, "value": right[1]}
    if right[0] == "list" and op == "in" and all(x[0] == "lit" and x[1] is not None for x in right[1]):
        return {"path": path, "op": "not in" if neg else "in", "value": [x[1] for x in right[1]]}
    if op == "in":
        return None
    shift = ""
    if right[0] == "shift" and right[1] == ("path", "today"):
        shift = f"{right[2]}{right[3]}{right[4]}"
    elif right != ("path", "today"):
        return None
    return {"path": path, "op": op, "today": shift}


def where_parts(where: str, source: str | None = "records") -> dict | None:
    """A condition as a filter of pickers, when it is one: comparisons of a field with a value (or with today,
    moved or not) joined all by ``and`` or all by ``or``. None for anything else."""
    try:
        node = expr.Parser(where).parse()
    except expr.ExprError:
        return None
    pre = _prefix(source)
    match = "any" if node[0] == "or" else "all"
    leaves: list = []

    def walk(n):
        if n[0] in ("and", "or"):
            if n[0] != ("or" if match == "any" else "and"):
                raise ValueError
            walk(n[1])
            walk(n[2])
        else:
            leaves.append(n)
    try:
        walk(node)
    except ValueError:
        return None
    rules = [_rule(n, pre) for n in leaves]
    if any(r is None for r in rules):
        return None
    out = {"match": match, "rules": rules}
    try:  # only what writes back meaning the same
        if expr.Parser(where_of(out, source)).parse() != node:
            return None
    except (DefinitionError, expr.ExprError):
        return None
    return out


def _value(row: dict, path: str):
    v = schema.first(row, path) if "." in path or path in row else None
    if isinstance(v, dict):
        v = v.get("text") or v.get("display") or v.get("reference") or v.get("value")
    if isinstance(v, list):
        v = ", ".join(str(x.get("text") if isinstance(x, dict) else x) for x in v)
    return v


def _words_value(hub, member: dict, rtype: str | None):
    """A records widget's cell: a reference by its record's title (when the member may read it), a member by name;
    any other value as ``_value`` gives it."""
    rt = hub.record_type(rtype) if rtype else None

    def value(row: dict, path: str):
        f = rt.field(path) if rt else None
        if f is None and rt:
            f = next((g for g in rt.fields if g["path"] == path), None)
        if f is None or f["type"] not in ("reference", "member"):
            return _value(row, f["path"] if f else path)
        raw = schema.first(row, f["path"])
        if raw in (None, ""):
            return None
        if f["type"] == "member":
            mid = str(raw).removeprefix("member:")
            try:
                return hub.member(mid)["name"]
            except Exception:  # noqa: BLE001 - gone: its id
                return mid
        ref = schema.ref_of(raw)
        if not ref or "/" not in ref:
            return _value(row, f["path"])
        t, _, rid = ref.partition("/")
        if t in hub.member_types():
            try:
                return hub.member(rid)["name"]
            except Exception:  # noqa: BLE001
                return ref
        rec = hub.records.read(t, rid) if hub.record_type(t) and hub.may_read(member, t) else None
        return hub.title(rec) if rec else ref
    return value


def rows_for(hub, member: dict, w: dict) -> tuple[list[dict], str | None]:
    """The widget's rows for this member, and a note when some were not shown."""
    note = None
    if w["source"] == "records":
        if not hub.may_read(member, w["type"]):
            return [], f"needs access to {hub.class_of(w['type'])} records"
        rows = hub.search_records(member, w["type"], limit=None)  # a filter sees only what the member sees
        today = hub.engine.now().date().isoformat()
        rows = [r for r in rows if not w["where"] or expr.holds(w["where"], {"record": r, "row": r, "today": today})]
        rt = hub.record_type(w["type"])
        rows.sort(key=lambda r: schema.record_date(rt, r), reverse=True)
        return rows, note
    if w["source"] == "items":
        statuses = w["status"] or ["open", "claimed"]
        rows = []
        for r in hub.db.items.find({"status": {"$in": list(statuses)}}, sort=[("created_at", -1)], limit=1000,
                                   fields=["id"]):
            it = hub.engine.item(r["id"])
            run = hub.engine.run(it["run_id"])
            if w["workflow"] and run["workflow_id"] != w["workflow"]:
                continue
            if w["role"] and it["role"] != w["role"]:
                continue
            row = {**it, "workflow": run["workflow_id"], "run_title": hub.run_title(member, run), "record": run["record"]}
            if w["where"] and not expr.holds(w["where"], {"row": row, "today": hub.engine.now().date().isoformat()}):
                continue
            rows.append(row)
        return rows, note
    statuses = w["status"] or list(RUN_STATUS)
    runs = [r for r in hub.engine.runs(limit=1000) if r["status"] in statuses
            and (not w["workflow"] or r["workflow_id"] == w["workflow"])]
    rows = [{**{k: r[k] for k in ("id", "workflow_id", "version", "record", "status", "started_by", "created_at")},
             "title": hub.run_title(member, r)} for r in runs]
    today = hub.engine.now().date().isoformat()
    rows = [r for r in rows if not w["where"] or expr.holds(w["where"], {"row": r, "today": today})]
    return rows, note


def _sort_key(v):
    """Rows by a value: numbers as numbers, the rest as text, the empty ones last."""
    if v is None or v == "":
        return (2, 0, "")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (0, v, "")
    return (1, 0, str(v).casefold())


def compute(hub, member: dict, view: View) -> list[dict]:
    out = []
    for w in view.widgets:
        if w["kind"] == "links":
            out.append({**w, "note": None, "total": len(w["links"]), "rows": [], "value": _value})
            continue
        rows, note = rows_for(hub, member, w)
        value = _words_value(hub, member, w["type"]) if w["source"] == "records" else _value
        if w.get("sort"):
            filled = [r for r in rows if value(r, w["sort"]) not in (None, "")]
            empty = [r for r in rows if value(r, w["sort"]) in (None, "")]
            rows = sorted(filled, key=lambda r: _sort_key(value(r, w["sort"])), reverse=w.get("descending", False))
            rows += empty
        d = {**w, "note": note, "total": len(rows)}
        if w["kind"] == "tree":
            keep = {r["id"] for r in rows}
            d["tree"] = [(dep, r) for dep, r in hub.tree(member, w["type"], where=lambda r: r["id"] in keep)][: w["limit"]]
            d["rows"] = [r for _, r in d["tree"]]
        elif w["kind"] in ("board", "chart"):
            groups: dict = {}
            for r in rows:
                groups.setdefault(str(_value(r, w["group_by"]) or "none"), []).append(r)
            d["groups"] = {k: v[: w["limit"]] for k, v in sorted(groups.items())}
            d["counts"] = {k: len(v) for k, v in sorted(groups.items())}
        else:
            d["rows"] = rows[: w["limit"]]
        d["value"] = value
        if w["source"] == "records" and w["kind"] != "chart":  # a chart shows counts; the rest show the records
            shown = d.get("rows") if "rows" in d else [r for v in d.get("groups", {}).values() for r in v]
            hub.note_list(member, shown, how="view", what=f"view:{view.id}")
        out.append(d)
    return out
