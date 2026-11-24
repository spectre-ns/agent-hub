"""BPMN 2.0 interchange for workflows: export a workflow as BPMN XML, and read a file we exported back into
workflow TOML.

A workflow is written as its record's states and the edges between them (``practicehub/states.py``). Export draws it
for BPMN tools (Camunda Modeler, bpmn.io) as the steps it runs as: a pool with one lane per role (and one for the
automatic steps), a start event per way it starts (by hand, on an event: a message start, on a schedule: a timer
start), a ``userTask`` for each task and decision, a ``serviceTask`` for each automation, an ``intermediateCatchEvent``
(conditional, or timer) for each wait, ``sequenceFlow`` for ``after`` with the step's ``when`` as its
``conditionExpression``, gateways where conditional branches split (exclusive when they test different outcomes of one
decision, inclusive otherwise) and where a step waits for several, a non-interrupting timer on a task or decision with
a ``due`` time, and diagram coordinates (BPMNDI) in a simple layered layout.

The workflow itself (its ``start``, ``state`` and ``edge`` tables and everything else it says) travels as JSON in a
namespaced ``extensionElements`` block on the process. Each step's settings are on its element the same way, for
whoever reads the diagram; the import reads only the step's id there, to tell which steps the diagram still shows.

Import reads a file we exported. The states and edges come back from the extension; the title, the summary and how
the workflow starts (a timer start: its schedule; a message start: its event) come back from what the diagram shows,
so those can be changed in a BPMN tool. A file exported and read back unchanged gives the same workflow. The steps
are never read from the diagram, because they come from the states and edges: the import says so when tasks were
added or removed there, and names, in plain words, what else was added that a workflow has no place for
(sub-processes, message flows, more pools...). A file from another tool carries no states and edges, and is refused
rather than guessed at: the refusal lists the tasks it holds, by lane, for whoever writes the workflow by hand. The
TOML an import gives is never installed: it goes to the editor or to ``Hub.propose_workflow`` like any edit, so a
policy owner who did not write it approves it.

Untrusted XML is read defensively: a size cap, an element cap, and no DOCTYPE or entity declarations at all (so no
entity expansion and no external entities). No code in the XML is ever run.
"""

from __future__ import annotations

import json
import re
import tomllib
import xml.etree.ElementTree as ET
import xml.parsers.expat
from dataclasses import dataclass, field

from . import workflows as wfmod
from .states import compile_states

BPMN = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI = "http://www.omg.org/spec/BPMN/20100524/DI"
DC = "http://www.omg.org/spec/DD/20100524/DC"
DI = "http://www.omg.org/spec/DD/20100524/DI"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
PH = "urn:practicehub:bpmn:1"
for _p, _u in (("bpmn", BPMN), ("bpmndi", BPMNDI), ("dc", DC), ("di", DI), ("xsi", XSI), ("ph", PH)):
    ET.register_namespace(_p, _u)

MAX_BYTES = 2 * 1024 * 1024   # a workflow's BPMN is a few tens of kilobytes
MAX_ELEMENTS = 20000


class BpmnError(ValueError):
    """The file cannot be imported at all: it is not BPMN, too large or unsafe XML, or it is a diagram from another
    tool, which carries no states and edges."""

    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass
class Imported:
    source: str                                       # workflow TOML, for the editor or propose_workflow
    problems: list[str] = field(default_factory=list)  # what the diagram holds that is not imported, and what to fix


def q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _ns(tag) -> str:
    return tag[1:].split("}", 1)[0] if isinstance(tag, str) and tag.startswith("{") else ""


# ================================================================================================ conversions
_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")
_DUR_UNITS = {"m": "M", "h": "H", "d": "D", "w": "W"}
_ISO_DUR = re.compile(r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_ISO_CYCLE = re.compile(r"R\d*/(?:(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?/)?"
                        r"(PT1H|P1D|P1W|P7D)")


def _clean(text: str) -> str:
    return _BAD_XML.sub("", str(text))


def _norm(text) -> str | None:
    return None if text is None else str(text).replace("\r\n", "\n").strip()


def duration_iso(text: str) -> str:
    """'30m' -> PT30M, '4h' -> PT4H, '2d' -> P2D, '1w' -> P1W; anything else as it is."""
    m = wfmod.DURATION.fullmatch(str(text).strip())
    if not m:
        return str(text)
    n, u = m.groups()
    return f"PT{n}{_DUR_UNITS[u]}" if u in "mh" else f"P{n}{_DUR_UNITS[u]}"


def duration_from_iso(text: str) -> str | None:
    """An ISO 8601 duration as a workflow duration (the largest unit that says it exactly), or None."""
    t = (text or "").strip()
    if not t:
        return None
    m = _ISO_DUR.fullmatch(t)
    if not m or not any(m.groups()):
        return None
    w, d, h, mi, s = (int(x or 0) for x in m.groups())
    if s % 60:
        return None
    minutes = ((w * 7 + d) * 24 + h) * 60 + mi + s // 60
    if not minutes:
        return None
    for unit, size in (("w", 10080), ("d", 1440), ("h", 60), ("m", 1)):
        if minutes % size == 0:
            return f"{minutes // size}{unit}"
    return None


def schedule_iso(text: str) -> str:
    """'hourly' -> R/PT1H; 'daily 07:00' -> R/2026-01-05T07:00:00/P1D; 'weekly mon 08:00' -> .../P1W (on that day of
    the week of 5-11 January 2026); any other schedule as it is."""
    t = str(text).strip()
    if t == "hourly":
        return "R/PT1H"
    m = re.fullmatch(r"daily (\d{2}):(\d{2})", t)
    if m:
        return f"R/2026-01-05T{m.group(1)}:{m.group(2)}:00/P1D"
    m = re.fullmatch(r"weekly (mon|tue|wed|thu|fri|sat|sun) (\d{2}):(\d{2})", t)
    if m:
        return f"R/2026-01-{5 + DAYS.index(m.group(1)):02d}T{m.group(2)}:{m.group(3)}:00/P1W"
    return t


def schedule_from_iso(text: str) -> str | None:
    t = (text or "").strip()
    if wfmod.SCHEDULE.fullmatch(t):
        return t
    m = _ISO_CYCLE.fullmatch(t)
    if not m:
        return None
    y, mo, d, hh, mm, period = m.groups()
    if period == "PT1H":
        return "hourly"
    if hh is None:
        return None
    if period == "P1D":
        return f"daily {hh}:{mm}"
    import datetime as _dt
    try:
        day = _dt.date(int(y), int(mo), int(d)).weekday()
    except ValueError:
        return None
    return f"weekly {DAYS[day]} {hh}:{mm}"


def _pretty(role: str) -> str:
    """A role as a lane's name: 'quality-manager' -> 'Quality manager'."""
    words = str(role).replace("-", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def slug(text: str, fallback: str = "step") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:48].strip("-")
    if not s or not s[0].isalpha():
        s = f"{fallback}-{s}" if s else fallback
    return s


_OUTCOME = re.compile(r"\s*([a-z][a-z0-9\-]*)\.outcome\s*==\s*'([^']*)'\s*")


# ================================================================================================ TOML writer
_BARE = re.compile(r"[A-Za-z0-9_\-]+")


def _key(k) -> str:
    k = str(k)
    return k if _BARE.fullmatch(k) else json.dumps(k, ensure_ascii=False)


def _str(s: str, inline: bool) -> str:
    if "\n" in s and not inline:
        body = s.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r")
        body = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", lambda m: f"\\u{ord(m.group()):04X}", body)
        return f'"""\n{body}"""'
    return json.dumps(s, ensure_ascii=False).replace("\x7f", "\\u007F")


def _value(v, inline: bool = True) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        if isinstance(v, float) and v != v:
            return "nan"
        if isinstance(v, float) and v in (float("inf"), float("-inf")):
            return "inf" if v > 0 else "-inf"
        return repr(v)
    if isinstance(v, str):
        return _str(v, inline)
    if isinstance(v, dict):
        if not v:
            return "{}"
        return "{ " + ", ".join(f"{_key(k)} = {_value(x)}" for k, x in v.items()) + " }"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    if hasattr(v, "isoformat"):  # a TOML date or time
        return v.isoformat()
    return _str(str(v), inline)


def dumps_toml(doc: dict) -> str:
    """A TOML document a person can read: top-level and step-level tables as [sections], deeper ones inline."""
    lines: list[str] = []

    def table(path: list[str], t: dict, depth: int):
        subs, arrays = [], []
        for k, v in t.items():
            if v is None:
                continue
            if isinstance(v, dict) and v and depth < 3:
                subs.append((k, v))
            elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v) and depth < 2:
                arrays.append((k, v))
            else:
                lines.append(f"{_key(k)} = {_value(v, inline=False)}")
        for k, v in subs:
            lines.extend(["", f"[{'.'.join(_key(p) for p in path + [k])}]"])
            table(path + [k], v, depth + 1)
        for k, items in arrays:
            for item in items:
                lines.extend(["", f"[[{'.'.join(_key(p) for p in path + [k])}]]"])
                table(path + [k], item, depth + 1)

    table([], doc, 0)
    return "\n".join(lines).strip() + "\n"


# ================================================================================================ export
def _raw(workflow) -> tuple[dict, wfmod.Workflow]:
    source = workflow.source if isinstance(workflow, wfmod.Workflow) else str(workflow)
    wf = workflow if isinstance(workflow, wfmod.Workflow) else wfmod.parse(source)
    return tomllib.loads(source), wf


def _ext(parent, tag: str, data=None, **attrs):
    ee = ET.SubElement(parent, q(BPMN, "extensionElements"))
    el = ET.SubElement(ee, q(PH, tag), {k: str(v) for k, v in attrs.items()})
    if data is not None:
        el.text = json.dumps(data, ensure_ascii=True, default=str, sort_keys=False)
    return el


def _doc(parent, text):
    if text:
        ET.SubElement(parent, q(BPMN, "documentation")).text = _clean(text)


def _expr(parent, tag: str, text: str):
    el = ET.SubElement(parent, q(BPMN, tag), {q(XSI, "type"): "bpmn:tFormalExpression"})
    el.text = _clean(text)
    return el


def export_bpmn(workflow) -> str:
    """A workflow (a parsed Workflow, or its TOML) as BPMN 2.0 XML with a diagram."""
    raw, wf = _raw(workflow)
    # drawn as the steps it runs as; the states and edges travel in the workflow's own extension, and come back
    # from it on import
    steps = [dict(s) for s in compile_states(raw, [])[0]]
    by_id = {s["id"]: s for s in steps}
    trig = dict(raw.get("trigger", {}) or {})
    wid = wf.id
    defs = ET.Element(q(BPMN, "definitions"), {
        "id": f"Definitions_{wid}", "targetNamespace": "urn:practicehub:workflow",
        "exporter": "Practice Hub", "exporterVersion": "1"})
    collab = ET.SubElement(defs, q(BPMN, "collaboration"), {"id": f"Collaboration_{wid}"})
    pid = f"Process_{wid}"
    ET.SubElement(collab, q(BPMN, "participant"), {"id": f"Participant_{wid}", "name": wf.title, "processRef": pid})
    msg_id = None
    if trig.get("event"):
        msg_id = "Message_event"
        ET.SubElement(defs, q(BPMN, "message"), {"id": msg_id, "name": str(trig["event"])})
    proc = ET.SubElement(defs, q(BPMN, "process"), {"id": pid, "name": wf.title, "isExecutable": "false"})
    _doc(proc, raw.get("summary"))
    _ext(proc, "workflow", raw)

    # ---- lanes: one per role, in order of appearance, then the automatic steps (and the start and end)
    def lane_of(s) -> str:
        return s.get("role") or ""

    lanes: list[str] = []
    for s in steps:
        if lane_of(s) not in lanes:
            lanes.append(lane_of(s))
    if "" not in lanes:
        lanes.append("")
    lane_ids = {r: f"Lane_{i + 1}" for i, r in enumerate(lanes)}
    lane_set = ET.SubElement(proc, q(BPMN, "laneSet"), {"id": f"LaneSet_{wid}"})
    lane_els = {}
    for r in lanes:
        attrs = {"id": lane_ids[r], "name": _pretty(r) if r else "Automatic"}
        if r:
            attrs[q(PH, "role")] = r
        lane_els[r] = ET.SubElement(lane_set, q(BPMN, "lane"), attrs)

    # ---- layers (columns) and nodes
    depth: dict[str, int] = {}

    def d(sid: str) -> int:
        if sid not in depth:
            s = by_id[sid]
            depth[sid] = 0 if not s.get("after") else 1 + max(d(a) for a in s["after"])
        return depth[sid]

    for s in steps:
        d(s["id"])
    nodes: dict[str, dict] = {}   # element id -> {lane, col, row, kind, w, h, dx}
    flows: list[tuple[str, str, str | None]] = []

    def node(eid, lane, col, w, h, dx=0.0, under=None):
        nodes[eid] = {"lane": lane, "col": col, "w": w, "h": h, "dx": dx, "under": under}
        ET.SubElement(lane_els[lane], q(BPMN, "flowNodeRef")).text = eid

    # start events
    starts = []
    start_lane = lane_of(steps[0])
    if trig.get("starters") or not (trig.get("event") or trig.get("schedule")):
        ev = ET.SubElement(proc, q(BPMN, "startEvent"), {"id": "Start_manual", "name": "Started by hand"})
        if trig.get("starters"):
            _doc(ev, "Started by: " + ", ".join(_pretty(r) for r in trig["starters"]))
        starts.append("Start_manual")
    if trig.get("event"):
        ev = ET.SubElement(proc, q(BPMN, "startEvent"), {"id": "Start_event", "name": f"On {trig['event']}"})
        if trig.get("when"):
            _doc(ev, f"Only when {trig['when']}")
        ET.SubElement(ev, q(BPMN, "messageEventDefinition"), {"id": "Start_event_def", "messageRef": msg_id})
        starts.append("Start_event")
    if trig.get("schedule"):
        ev = ET.SubElement(proc, q(BPMN, "startEvent"), {"id": "Start_schedule", "name": str(trig["schedule"]).capitalize()})
        if trig.get("for_each"):
            _doc(ev, f"Once for each {trig['for_each']}" + (f" where {trig['where']}" if trig.get("where") else ""))
        ted = ET.SubElement(ev, q(BPMN, "timerEventDefinition"), {"id": "Start_schedule_def"})
        _expr(ted, "timeCycle", schedule_iso(trig["schedule"]))
        starts.append("Start_schedule")
    for i, sid in enumerate(starts):
        node(sid, start_lane, -2, 36, 36, under=i)
    start_node = starts[0]
    if len(starts) > 1:
        g = ET.SubElement(proc, q(BPMN, "exclusiveGateway"), {"id": "Gateway_start"})
        _ext(g, "gateway")
        node("Gateway_start", start_lane, -1, 50, 50, dx=-60)
        for sid in starts:
            flows.append((sid, "Gateway_start", None))
        start_node = "Gateway_start"

    # steps
    for s in steps:
        sid, kind = s["id"], s.get("kind", "task")
        name = s.get("title") or sid
        if kind == "wait":
            el = ET.SubElement(proc, q(BPMN, "intermediateCatchEvent"), {"id": sid, "name": name})
            w, h = 36, 36
        else:
            tag = "serviceTask" if kind == "automation" else "userTask"
            el = ET.SubElement(proc, q(BPMN, tag), {"id": sid, "name": name})
            w, h = 100, 80
        _doc(el, s.get("instructions"))
        _ext(el, "step", s)
        if kind == "wait":
            if s.get("until"):
                ced = ET.SubElement(el, q(BPMN, "conditionalEventDefinition"), {"id": f"Until_{sid}"})
                _expr(ced, "condition", s["until"])
            if s.get("for") is not None:
                ted = ET.SubElement(el, q(BPMN, "timerEventDefinition"), {"id": f"For_{sid}"})
                _expr(ted, "timeDuration", duration_iso(s["for"]))
            if s.get("at") is not None:
                ted = ET.SubElement(el, q(BPMN, "timerEventDefinition"), {"id": f"At_{sid}"})
                _expr(ted, "timeDate", str(s["at"]))
        node(sid, lane_of(s), depth[sid], w, h)
        if s.get("due") and kind != "wait":
            b = ET.SubElement(proc, q(BPMN, "boundaryEvent"), {
                "id": f"Due_{sid}", "name": f"Due in {s['due']}" + (f", then {_pretty(s['escalate_to'])}"
                                                                     if s.get("escalate_to") else ""),
                "attachedToRef": sid, "cancelActivity": "false"})
            ted = ET.SubElement(b, q(BPMN, "timerEventDefinition"), {"id": f"Due_{sid}_def"})
            _expr(ted, "timeDuration", duration_iso(s["due"]))
            nodes[f"Due_{sid}"] = {"boundary": sid, "w": 36, "h": 36}

    # ---- flows: splits where conditional branches leave one step, joins where a step waits for several
    def sources(s):
        return list(s.get("after") or []) or [start_node]

    cond_succ: dict[str, list[dict]] = {}
    for s in steps:
        src = sources(s)
        if len(src) == 1 and s.get("when"):
            cond_succ.setdefault(src[0], []).append(s)
    split = {}
    for p, succ in cond_succ.items():
        if len(succ) < 2:
            continue
        ms = [_OUTCOME.fullmatch(x["when"]) for x in succ]
        exclusive = all(ms) and len({m.group(1) for m in ms}) == 1 and len({m.group(2) for m in ms}) == len(ms)
        gid = f"Gateway_split_{p}"
        g = ET.SubElement(proc, q(BPMN, "exclusiveGateway" if exclusive else "inclusiveGateway"), {"id": gid})
        _ext(g, "gateway")
        pn = nodes[p]
        if p in by_id:
            node(gid, lane_of(by_id[p]), depth[p], 50, 50, dx=pn["w"] + 30)
        else:
            node(gid, start_lane, -1, 50, 50, dx=20)
        flows.append((p, gid, None))
        split[p] = gid
    for s in steps:
        sid, src = s["id"], sources(s)
        if len(src) > 1:
            gid = f"Gateway_join_{sid}"
            g = ET.SubElement(proc, q(BPMN, "parallelGateway"), {"id": gid})
            _ext(g, "gateway")
            node(gid, lane_of(s), depth[sid], 50, 50, dx=-80)
            for p in src:
                flows.append((p, gid, None))
            flows.append((gid, sid, s.get("when")))
        elif src[0] in split and s.get("when"):
            flows.append((split[src[0]], sid, s["when"]))
        else:
            flows.append((src[0], sid, s.get("when")))
    referenced = {a for s in steps for a in (s.get("after") or [])}
    leaves = [s for s in steps if s["id"] not in referenced]
    end_col = max(depth.values()) + 1
    ET.SubElement(proc, q(BPMN, "endEvent"), {"id": "End", "name": "Done"})
    node("End", lane_of(leaves[0]) if leaves else start_lane, end_col, 36, 36, dx=-40)
    for s in leaves:
        flows.append((s["id"], "End", None))
    for i, (a, b, cond) in enumerate(flows):
        attrs = {"id": f"Flow_{i + 1}", "sourceRef": a, "targetRef": b}
        m = _OUTCOME.fullmatch(cond) if cond else None
        if m:
            attrs["name"] = m.group(2)
        f = ET.SubElement(proc, q(BPMN, "sequenceFlow"), attrs)
        if cond:
            _expr(f, "conditionExpression", cond)

    _diagram(defs, wid, collab, lanes, lane_ids, nodes, flows)
    ET.indent(defs, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(defs, encoding="unicode") + "\n"


COL, ROW, LANE_X, POOL_X, FIRST = 320, 130, 130, 100, 260


def _diagram(defs, wid, collab, lanes, lane_ids, nodes, flows):
    """A layered layout: a column per layer of steps, a band per lane, a row per step sharing a lane and a column."""
    rows: dict[tuple[str, int], int] = {}
    for eid, n in nodes.items():
        if "boundary" in n:
            continue
        if n["under"] is not None:   # a start event: stacked
            n["row"] = n["under"]
            rows[(n["lane"], n["col"])] = max(rows.get((n["lane"], n["col"]), 0), n["row"] + 1)
            continue
        if n["dx"]:               # a gateway or the end: on the row of the step it belongs to, found below
            continue
        key = (n["lane"], n["col"])
        n["row"] = rows.get(key, 0)
        rows[key] = n["row"] + 1
    lane_rows = {r: 1 for r in lanes}
    for (lane, _), k in rows.items():
        lane_rows[lane] = max(lane_rows[lane], k)
    # the starts stack at 60px, not a full row
    tops, y = {}, 60
    for r in lanes:
        tops[r] = y
        y += lane_rows[r] * ROW + 20
    total_h = y - 60
    max_col = max(n["col"] for n in nodes.values() if "col" in n)
    width = FIRST + (max_col + 1) * COL + 40

    def center(eid):
        n = nodes[eid]
        if n["under"] is not None:
            return LANE_X + 50, tops[n["lane"]] + 25 + 40 + n["row"] * 60
        x = LANE_X + FIRST + n["col"] * COL + 50 if n["col"] >= 0 else LANE_X + 50 + (n["col"] + 2) * 90
        return x, tops[n["lane"]] + 25 + 40 + n.get("row", 0) * ROW

    # a gateway sits by its step: a split right of it, a join left of it, the end right of the last column
    for eid, n in nodes.items():
        if "boundary" in n or not n["dx"]:
            continue
        owner = None
        if eid.startswith("Gateway_split_"):
            owner = eid[len("Gateway_split_"):]
        elif eid.startswith("Gateway_join_"):
            owner = eid[len("Gateway_join_"):]
        n["row"] = nodes[owner]["row"] if owner in nodes and "row" in nodes[owner] else 0
    pos = {}
    for eid, n in nodes.items():
        if "boundary" in n:
            continue
        cx, cy = center(eid)
        if n["dx"]:
            owner = eid.split("_", 2)[-1]
            if eid.startswith("Gateway_split_") and owner in nodes and nodes[owner].get("under") is None:
                ocx, _ = center(owner)
                cx = ocx + nodes[owner]["w"] / 2 + 55
            elif eid.startswith("Gateway_join_"):
                cx = cx - 50 - 55
            elif eid == "End":
                cx = cx - 50
            else:
                cx = LANE_X + 130 if eid == "Gateway_start" else LANE_X + 200
        pos[eid] = (cx - n["w"] / 2, cy - n["h"] / 2, n["w"], n["h"])
    for eid, n in nodes.items():
        if "boundary" in n:
            x, y0, w, h = pos[n["boundary"]]
            pos[eid] = (x + w - 30, y0 + h - 18, 36, 36)
    di = ET.SubElement(defs, q(BPMNDI, "BPMNDiagram"), {"id": f"Diagram_{wid}"})
    plane = ET.SubElement(di, q(BPMNDI, "BPMNPlane"), {"id": f"Plane_{wid}", "bpmnElement": collab.get("id")})

    def shape(eid, x, y, w, h, horizontal=False):
        attrs = {"id": f"{eid}_di", "bpmnElement": eid}
        if horizontal:
            attrs["isHorizontal"] = "true"
        sh = ET.SubElement(plane, q(BPMNDI, "BPMNShape"), attrs)
        ET.SubElement(sh, q(DC, "Bounds"), {"x": _n(x), "y": _n(y), "width": _n(w), "height": _n(h)})

    shape(f"Participant_{wid}", POOL_X, 60, width + LANE_X - POOL_X, total_h, True)
    for r in lanes:
        shape(lane_ids[r], LANE_X, tops[r], width, lane_rows[r] * ROW + 20, True)
    for eid, (x, y, w, h) in pos.items():
        shape(eid, x, y, w, h)
    for i, (a, b, _) in enumerate(flows):
        ax, ay, aw, ah = pos[a]
        bx, by, bw, bh = pos[b]
        sx, sy, tx, ty = ax + aw, ay + ah / 2, bx, by + bh / 2
        pts = [(sx, sy)]
        if abs(sy - ty) > 1:
            mx = (sx + tx) / 2 if tx > sx else sx + 20
            pts += [(mx, sy), (mx, ty)]
        pts.append((tx, ty))
        ed = ET.SubElement(plane, q(BPMNDI, "BPMNEdge"), {"id": f"Flow_{i + 1}_di", "bpmnElement": f"Flow_{i + 1}"})
        for x, y in pts:
            ET.SubElement(ed, q(DI, "waypoint"), {"x": _n(x), "y": _n(y)})


def _n(v) -> str:
    return str(int(round(v)))


# ================================================================================================ safe parsing
def parse_xml(data) -> ET.Element:
    """Untrusted XML, read defensively: at most MAX_BYTES, no DOCTYPE, no entities, at most MAX_ELEMENTS elements."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if len(data) > MAX_BYTES:
        raise BpmnError([f"The file is too large for a workflow (over {MAX_BYTES // (1024 * 1024)} MB)."])
    if not data.strip():
        raise BpmnError(["The file is empty."])

    def refuse(*_):
        raise BpmnError(["The file declares a document type or entities; for safety such files are not read."])

    count = [0]

    def start(*_):
        count[0] += 1
        if count[0] > MAX_ELEMENTS:
            raise BpmnError([f"The file has more than {MAX_ELEMENTS} elements; it is not a workflow."])

    scan = xml.parsers.expat.ParserCreate()
    scan.SetParamEntityParsing(xml.parsers.expat.XML_PARAM_ENTITY_PARSING_NEVER)
    scan.StartDoctypeDeclHandler = refuse
    scan.EntityDeclHandler = refuse
    scan.UnparsedEntityDeclHandler = refuse
    scan.ExternalEntityRefHandler = refuse
    scan.StartElementHandler = start
    try:
        scan.Parse(data, True)
    except xml.parsers.expat.ExpatError as exc:
        raise BpmnError([f"The file is not readable XML ({exc})."]) from None
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise BpmnError([f"The file is not readable XML ({exc})."]) from None


# ================================================================================================ import
TASKS = {"userTask", "manualTask", "task", "serviceTask", "scriptTask", "sendTask", "businessRuleTask", "receiveTask"}
STEPS = TASKS | {"intermediateCatchEvent"}   # what a step is drawn as: a task, or a catch event for a wait
DRAWN = STEPS | {"sequenceFlow", "startEvent", "endEvent", "boundaryEvent", "exclusiveGateway", "inclusiveGateway",
                 "parallelGateway"}           # everything export draws a workflow with
SUB_PROCESSES = {"subProcess", "transaction", "adHocSubProcess"}
IGNORED = {"documentation", "extensionElements", "laneSet", "textAnnotation", "association", "group", "category",
           "incoming", "outgoing", "property", "ioSpecification"}
UNSUPPORTED = {
    "subProcess": "a sub-process", "transaction": "a transaction", "adHocSubProcess": "an ad-hoc sub-process",
    "callActivity": "a call to another process", "intermediateThrowEvent": "an intermediate throw event",
    "eventBasedGateway": "an event-based gateway", "complexGateway": "a complex gateway",
    "dataObject": "a data object", "dataObjectReference": "a data object", "dataStoreReference": "a data store",
    "dataStore": "a data store", "dataInputAssociation": "a data association",
    "dataOutputAssociation": "a data association", "choreographyTask": "a choreography",
}
EVENT_DEFS = {"messageEventDefinition": "a message", "timerEventDefinition": "a timer",
              "conditionalEventDefinition": "a condition", "signalEventDefinition": "a signal",
              "errorEventDefinition": "an error", "escalationEventDefinition": "an escalation",
              "compensateEventDefinition": "compensation", "cancelEventDefinition": "a cancellation",
              "linkEventDefinition": "a link", "terminateEventDefinition": "a terminate end"}
LISTED = 60   # the tasks named when a file from another tool is refused


def _children(el, tag):
    return [c for c in el if _ns(c.tag) == BPMN and _local(c.tag) == tag]


def _one(el, tag):
    c = _children(el, tag)
    return c[0] if c else None


def _ph(el, tag):
    """The JSON a Practice Hub extension element holds, or None (also when it is not a valid object)."""
    ee = _one(el, "extensionElements")
    if ee is None:
        return None
    for c in ee:
        if c.tag == q(PH, tag):
            if not (c.text or "").strip():
                return {}
            try:
                data = json.loads(c.text)
            except ValueError:
                return None
            return data if isinstance(data, dict) else None
    return None


def _keep(ext: dict, key: str, derived, to_bpmn=str):
    """What the diagram says wins; the extension's value is kept when the diagram still says the same."""
    have = ext.get(key)
    if have is not None and derived is not None and _norm(to_bpmn(have)) == _norm(derived):
        return
    if derived is None:
        ext.pop(key, None)
    else:
        ext[key] = derived


def _carried(proc) -> dict | None:
    """The workflow a process carries in our extension, when it holds states and edges: only a file we exported
    has one."""
    ext = _ph(proc, "workflow")
    return ext if ext and any(k in ext for k in ("start", "state", "edge")) else None


def _plain(text, limit: int = 80) -> str:
    """A name from the file, for a message: one line of printable characters, and not too long."""
    s = " ".join("".join(c if c.isprintable() else " " for c in str(text)).split())
    return s if len(s) <= limit else s[:limit - 3].rstrip() + "..."


def _tasks_by_lane(root) -> dict[str, list[str]]:
    """The names of the file's tasks under the name of the lane each is drawn in ("" for none), in the file's order.
    A task inside a sub-process is in the sub-process's lane. The file is untrusted and may nest as deep as it
    likes, so it is walked with a list, never by recursion."""
    found: dict[str, list[str]] = {}
    for proc in _children(root, "process"):
        lane_of: dict[str, str] = {}
        for ls in _children(proc, "laneSet"):
            for lane in ls.iter(q(BPMN, "lane")):  # outer lanes first, so a task is in its innermost lane
                for ref in _children(lane, "flowNodeRef"):
                    lane_of[(ref.text or "").strip()] = _plain(lane.get("name") or "") or _plain(lane.get("id") or "")
        todo = [(iter(proc), "")]
        while todo:
            inside, lane = todo[-1]
            el = next(inside, None)
            if el is None:
                todo.pop()
            elif _ns(el.tag) == BPMN:
                here = lane_of.get(el.get("id"), lane)
                if _local(el.tag) in TASKS:
                    name = _plain(el.get("name") or "") or _plain(el.get("id") or "") or "a task with no name"
                    found.setdefault(here, []).append(name)
                elif _local(el.tag) in SUB_PROCESSES:
                    todo.append((iter(el), here))
    return found


def _refusal(root) -> list[str]:
    """Why a file that carries no states and edges is not imported, and the tasks it holds, by lane, for whoever
    writes the workflow by hand."""
    lanes = _tasks_by_lane(root)
    total = sum(len(names) for names in lanes.values())
    out = ["A workflow here is written as its record's states and the edges between them ([start], [[state]], "
           "[[edge]]), and this file carries none: a diagram drawn in another tool cannot be imported as it is."]
    if total:
        out.append(f"The file holds {total} task{'s' if total != 1 else ''}, listed here by lane to help you write "
                   f"the workflow by hand.")
        named = 0
        for lane in sorted(lanes, key=lambda name: not name):  # the lanes in the file's order, then the tasks in none
            names = lanes[lane][:LISTED - named]
            if names:
                out.append(f"{lane or 'In no lane'}: {', '.join(names)}")
                named += len(names)
        if named < total:
            out.append(f"And {total - named} more, not listed here.")
    else:
        out.append("The file holds no tasks.")
    out.append("A lane is usually a role, and a task is the work on a state or an automation on an edge: see "
               "\"States and edges\" in docs/workflows.md.")
    return out


def import_bpmn(data, catalog=None) -> Imported:
    """A BPMN file we exported as workflow TOML, and the problems: what the diagram holds that is not imported, then
    what the workflow still needs (the checks ``workflows.parse`` makes). The states and edges are the extension's;
    the title, the summary and how the workflow starts are the diagram's. Raises BpmnError when the file cannot be
    read at all, or is a diagram from another tool."""
    root = parse_xml(data)
    if root.tag != q(BPMN, "definitions"):
        raise BpmnError(["The file is not BPMN 2.0: it has no BPMN definitions."])
    procs = _children(root, "process")
    if not procs:
        raise BpmnError(["The file has no process to import."])
    proc = wdoc = None
    for p in procs:
        wdoc = _carried(p)
        if wdoc is not None:
            proc = p
            break
    if proc is None:
        raise BpmnError(_refusal(root))
    problems: list[str] = []
    if len(procs) > 1:
        problems.append(f"The file has {len(procs)} processes; only {proc.get('name') or proc.get('id')!r} is "
                        f"imported (a workflow is one process).")
    participant_name = None
    for collab in _children(root, "collaboration"):
        parts = _children(collab, "participant")
        if len(parts) > 1:
            problems.append("The file has several pools (participants); a workflow is one practice's work, so only "
                            "the one process is imported.")
        for p in parts:
            if p.get("processRef") == proc.get("id"):
                participant_name = p.get("name")
        if _children(collab, "messageFlow"):
            problems.append("Message flows between pools are not supported: a workflow sends a message with an "
                            "automation (message.send) and waits with a wait step. They are left out.")
        for c in collab:
            if _local(c.tag) not in ("participant", "messageFlow", "documentation", "extensionElements",
                                     "textAnnotation", "association", "group"):
                problems.append(f"{_local(c.tag)} in the collaboration is not supported; left out.")
    messages = {m.get("id"): m.get("name") or m.get("id") for m in _children(root, "message")}

    # what the diagram holds: its steps (by the id export wrote on each), its starts, and what is not imported
    drawn: list[str] = []
    starts = []
    for el in proc:
        tag = _local(el.tag)
        if _ns(el.tag) != BPMN:
            continue
        if tag in STEPS:
            sid = (_ph(el, "step") or {}).get("id")
            drawn.append(sid if isinstance(sid, str) else "")  # "": added in another tool, so it has no id of ours
        elif tag == "startEvent":
            starts.append(el)
        elif tag in UNSUPPORTED:
            problems.append(f"{el.get('name') or el.get('id')!r} is {UNSUPPORTED[tag]}, which a workflow does not "
                            f"support; it is left out" + (" (steps inside it too)." if "rocess" in tag else "."))
        elif tag not in DRAWN and tag not in IGNORED:
            problems.append(f"{el.get('name') or el.get('id')!r} ({tag}) is not supported; it is left out.")

    # the workflow: title, summary, trigger
    wid = wdoc.get("id") if isinstance(wdoc.get("id"), str) else None
    if not wid:
        wid = slug(proc.get("name") or participant_name or proc.get("id") or "imported", "workflow")
    wdoc["id"] = wid
    _keep(wdoc, "title", (proc.get("name") or participant_name or "").strip() or None)
    if not wdoc.get("title"):
        wdoc["title"] = wid.replace("-", " ").capitalize()
    pdoc = _one(proc, "documentation")
    _keep(wdoc, "summary", (pdoc.text or "").strip() if pdoc is not None and (pdoc.text or "").strip() else None)
    trig = dict(wdoc["trigger"]) if isinstance(wdoc.get("trigger"), dict) else {}
    schedule = event = None
    for el in starts:
        where = repr(el.get("name") or el.get("id"))
        for dfn in el:
            lt = _local(dfn.tag)
            if lt == "timerEventDefinition":
                cyc = _one(dfn, "timeCycle")
                text = (cyc.text or "").strip() if cyc is not None else ""
                conv = schedule_from_iso(text)
                if trig.get("schedule") and schedule_iso(trig["schedule"]) == text:
                    conv = trig["schedule"]
                if conv is None:
                    problems.append(f"The timer start {where} ({text or 'no repeat'}) is not a schedule a workflow "
                                    f"can keep: say hourly, daily 07:00 or weekly mon 08:00.")
                schedule = conv or text or None
            elif lt == "messageEventDefinition":
                event = messages.get(dfn.get("messageRef")) or (el.get("name") or "").removeprefix("On ").strip()
            elif lt.endswith("EventDefinition"):
                problems.append(f"The start {where} starts on {EVENT_DEFS.get(lt, lt)}, which a workflow cannot; it "
                                f"starts by hand, on an event or on a schedule.")
    for key, val in (("schedule", schedule), ("event", event)):
        if val is None:
            trig.pop(key, None)
        else:
            trig[key] = val
    if trig.get("for_each") and not trig.get("schedule"):
        trig.pop("for_each", None)
        trig.pop("where", None)
    if trig.get("when") and not trig.get("event"):
        trig.pop("when", None)
    if trig:
        wdoc["trigger"] = trig
    else:
        wdoc.pop("trigger", None)
    order = ("id", "title", "record", "summary", "trigger", "input")
    out = {**{k: wdoc[k] for k in order if k in wdoc}, **{k: v for k, v in wdoc.items() if k not in order}}
    # the steps are the states' and edges', never the diagram's: say so when the diagram no longer shows the same ones
    if sorted(drawn) != sorted(str(s.get("id")) for s in compile_states(out, [])[0]):
        problems.append("A workflow's steps come from its states and edges, so steps added or removed in the diagram "
                        "are not imported. Change its states and edges instead.")
    source = dumps_toml(out)
    try:
        wfmod.parse(source, catalog)
    except wfmod.DefinitionError as exc:
        problems += [f"Still to fix: {p}" for p in exc.problems]
    return Imported(source=source, problems=list(dict.fromkeys(problems)))
