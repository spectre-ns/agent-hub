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
    limit = 25

Every widget is computed for the member looking at it: records only from data classes they may read, items and
runs only with what visibility allows.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field

from . import expr
from . import schema
from .workflows import DefinitionError

ID = re.compile(r"[a-z][a-z0-9\-]*")
WIDGETS = ("count", "table", "list", "board", "chart", "tree")
SOURCES = ("items", "runs", "records")
ITEM_STATUS = ("open", "claimed", "held", "done", "cancelled")
RUN_STATUS = ("running", "completed", "cancelled")
RESERVED = {"new"}


@dataclass
class View:
    id: str
    title: str
    summary: str = ""
    nav: bool = True
    roles: list[str] = field(default_factory=list)
    widgets: list[dict] = field(default_factory=list)
    source: str = ""


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
        where = f"widget {w.get('title') or i + 1}"
        kind, src = w.get("kind", "table"), w.get("source", "records")
        if kind not in WIDGETS:
            problems.append(f"{where}: kind is one of {', '.join(WIDGETS)}")
        if src not in SOURCES:
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
            if not c.get("path"):
                problems.append(f"{where}: every column needs a path")
                continue
            cols.append({"label": c.get("label") or c["path"].split(".")[-1].replace("_", " ").capitalize(),
                         "path": c["path"]})
        widgets.append({"kind": kind, "source": src, "title": str(w.get("title", "")), "type": w.get("type"),
                        "workflow": w.get("workflow"), "status": status, "role": w.get("role"),
                        "where": w.get("where"), "columns": cols, "group_by": w.get("group_by"),
                        "limit": int(w.get("limit", 500 if kind == "tree" else 25))})
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
        rows = hub.records.search(w["type"], limit=None)
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


def compute(hub, member: dict, view: View) -> list[dict]:
    out = []
    for w in view.widgets:
        rows, note = rows_for(hub, member, w)
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
        d["value"] = _words_value(hub, member, w["type"]) if w["source"] == "records" else _value
        out.append(d)
    return out
