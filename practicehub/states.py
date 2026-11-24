"""A workflow written as its record's states and the edges between them, as an issue tracker's workflow is.

A **state** is where the record waits. Its ``work`` is what someone does while it is there (a person's task or a
signed decision; an agent may do a task). An **edge** is a way out of a state: what the person who did the work
chose. People pick the edge, never the state it leads to. An edge says where the record goes (``to``), may be
offered only when a condition holds (``when``, an edge's condition), and carries the automations that run as the
record moves along it (``do``). An automation may **divert** the record to another state (``to`` on it, with its
``when``): the edge still runs its other automations, and the record lands where the last diversion that ran sent
it, else where the edge goes. ``[start]`` is the edge a new record (or one the workflow starts on again) enters by.

    [start]
    to = "sorting"
    [[start.do]]
    id = "check"
    block = "lab-range-check"
    [[start.do]]
    id = "urgent"
    when = "check.band == 'critical'"
    to = "received"            # a diversion: straight to review

    [[state]]
    id = "sorting"
    [[state.work]]
    id = "sort"
    kind = "task"
    role = "clinical-assistant"
    skill = "lab-triage"

    [[edge]]
    id = "sorted"
    from = "sorting"
    to = "received"

A state with one edge out takes it once its work is done; a state with several asks the person which (its work's
outcomes are its edges, in order). A person's task on a state with one plain edge out (no ``when``, no diversion) is
that move: moving the record along it on the record's page does the task (a signed move is signed there), and the
task done moves the record before the edge's automations run. A state may also be **automated**: its work is an
automation (``kind = "automation"``, an action or a block), and the record leaves it on its own, by the first edge out whose ``when``
holds once the work is done (an edge with no ``when``, the last, is the way out otherwise). That choice is made once,
by a step of its own (``<state>--route``, its output ``{"edge": "<id>"}``) before any edge moves the record; the
edges' steps read it, never the conditions, so an edge that changes what they read cannot route twice. A wait (``kind =
"wait"``: until a condition holds, for a while, or at a moment) is automated work too. There, an edge's ``when``
routes the record; on a person's state it says whether the edge is offered. A record type with no state field of its
own may still be walked: the run keeps where it is (``run["at"]``). A state with no work and no edge out is where the record ends. A state may name
several pieces of work, each with its own ``when`` (a critical result's review is urgent): the one that holds is
done. The record's type still says which values its state field takes; the moves the workflow makes are the
workflow's, not listed on the type.

A lifecycle may loop (a document returned to its author, revised after it was approved). States are listed in the
order a pass goes through them: an edge back to a state listed at or before the one it leaves, from which the record
can come back round, ends the pass. The record moves there and the run ends; the record's own workflow walks the next
pass, a run of its own (``run.previous`` is this one), so a run never loops. A person's task whose ``moves`` names
where its state's one edge goes is done by that move made by hand on the record's page (an author sending a draft
for review there has done the task that asked them to).

It is compiled to ordinary steps (``compile_states``), so a run, its history and its analytics read as any
workflow's: each work is a step; each automation of an edge a step after that work, run when that edge was chosen;
each edge ends with a step that moves the record to where it goes unless a diversion sent it elsewhere. An edge's
automations run side by side: each waits only for the work and for an earlier automation whose output it reads, so
one that fails holds up only what needs it; the record moves once they are all done. One that must wait for an
earlier one on its edge without reading it names it: ``after = ["prescribe"]`` (only automations of the same edge).
An automation that may fail names where the record goes if it does: ``on_fail = "callback"`` (a state). Its failure
is still recorded and told to the practice, but the record moves there and the run goes on from there; what was
waiting on the automation to move the record along its edge does not run.

A workflow may also only act (a sweep that must never move its record): its ``[start]`` has automations
(``[[start.do]]``) and no ``to``, and it has no states and no edges. Its automations run as any edge's; nothing moves
the record, and the run ends once they are done.
"""

from __future__ import annotations

import json
import re

ARRIVE = "{edge}--to"           # the step that moves the record along an edge (an id no author writes: ``--``)
ROUTE = "{state}--route"        # the step that decides, once, which edge an automated state's record leaves by
FAILED = "{step}--failed"       # the step that moves the record where an automation's on_fail says, if it fails
START = "start"
# what the workflow works out from its states and edges, and no author writes on work or on an automation
WORKED_OUT = ("state", "route", "needs", "on_edge", "at_state", "outcome_when", "outcome_labels")
# what a workflow says, and where: a key that is none of these is a slip of the pen, refused rather than ignored
KEYS = {
    "workflow": {"id", "title", "summary", "record", "trigger", "input", "deadline", "new_record", "on_cancel",
                 "cancel_by", "start", "state", "edge"},
    "trigger": {"starters", "event", "when", "schedule", "for_each", "where", "one_open_per", "allow_duplicate",
                "one_open_while", "one_open_within", "one_open_same"},
    "start": {"to", "label", "do"},
    "work": {"id", "title", "kind", "role", "when", "risk", "skill", "instructions", "context", "fields", "review_of",
             "not_by", "signer", "meaning", "meanings", "meanings_if_did", "agent_criteria", "action", "params",
             "persons_only", "block", "due", "escalate_to", "priority", "assign_to", "people_role", "for_each",
             "where", "until", "for", "at", "timeout", "moves", "requires_links"},
    "automation": {"id", "title", "kind", "when", "action", "block", "params", "for_each", "where", "context",
                   "after", "to", "on_fail"},
}


class _Bad(Exception):
    pass


def _cond(*parts) -> str | None:
    parts = [p for p in parts if p]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else " and ".join(f"({p})" for p in parts)


def _either(parts) -> str | None:
    parts = [p for p in parts if p]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else "(" + " or ".join(parts) + ")"


def _reads(step: dict, sid: str) -> bool:
    """Whether an automation reads what an earlier one on its edge produced (``{{sid.x}}``, or ``sid.x`` in its
    condition): only then does it wait for it, so one that fails holds up only what needs it."""
    text = json.dumps({k: v for k, v in step.items() if k != "id"}, default=str)
    return re.search(rf"(?<![A-Za-z0-9_\-]){re.escape(sid)}\.", text) is not None


def _loops(edges: list[dict], order: list[str]) -> set:
    """The (edge, state) moves that go back round a loop: to a state listed at or before the one the edge leaves,
    from which the record can come back to it. Such a move ends the pass: the record moves, and the run ends there
    (its type's workflow walks the next pass, a run of its own), so a run never loops."""
    pos = {sid: i for i, sid in enumerate(order)}
    nxt: dict = {}
    for e in edges:
        nxt.setdefault(e["from"], set()).update(
            [e["to"], *[a.get(k) for a in e["do"] if isinstance(a, dict) for k in ("to", "on_fail") if a.get(k)]])

    def reaches(a, b) -> bool:
        seen, todo = set(), [a]
        while todo:
            x = todo.pop()
            if x == b:
                return True
            if x not in seen:
                seen.add(x)
                todo += list(nxt.get(x, ()))
        return False

    out = set()
    for e in edges:
        for to in [e["to"], *[a.get(k) for a in e["do"] if isinstance(a, dict) for k in ("to", "on_fail") if a.get(k)]]:
            if to in pos and e["from"] in pos and pos[to] <= pos[e["from"]] and reaches(to, e["from"]):
                out.add((e["id"], to))
    return out


def _additions(raw_states: list, raw_edges: list, problems: list[str]) -> tuple[list, list]:
    """What a module adds to a workflow another part of the practice owns (``extends/workflows/<id>.toml``,
    appended to its text): new states and edges, and work on a state it has (``[[state]] state = "<id>"`` with its
    ``[[state.work]]``) or automations on an edge it has (``[[edge]] edge = "<id>"`` with its ``[[edge.do]]``). An
    addition names what it adds to and adds; it changes nothing the workflow says."""
    states = [dict(x) for x in raw_states if "state" not in x]
    edges = [dict(x) for x in raw_edges if "edge" not in x]
    for x in raw_states:
        if "state" not in x:
            continue
        to = next((s for s in states if s.get("id") == x["state"]), None)
        if to is None or set(x) - {"state", "work"}:
            problems.append(f"state {x['state']}: work added to a state names one the workflow has "
                            f"([[state]] state = \"<id>\") and adds only its work ([[state.work]])")
            continue
        have = to.get("work") or []
        to["work"] = ([have] if isinstance(have, dict) else list(have)) + list(x.get("work") or [])
    for x in raw_edges:
        if "edge" not in x:
            continue
        to = next((e for e in edges if e.get("id") == x["edge"]), None)
        if to is None or set(x) - {"edge", "do"}:
            problems.append(f"edge {x['edge']}: automations added to an edge name one the workflow has "
                            f"([[edge]] edge = \"<id>\") and add only automations ([[edge.do]])")
            continue
        to["do"] = list(to.get("do") or []) + list(x.get("do") or [])
    return states, edges


def extension_problems(base: dict, ext: dict) -> list[str]:
    """What a module's additions (``ext``, its ``extends/workflows/<id>.toml``) would redefine in the workflow ``base``
    says. An addition takes ids of its own for its states, edges, work and automations: a state or edge it adds to it
    names (``state = "<id>"``, ``edge = "<id>"``), and it never says again what the workflow says."""
    def listed(v) -> list:
        v = v or []
        return [x for x in ([v] if isinstance(v, dict) else v if isinstance(v, list) else []) if isinstance(x, dict)]
    states, edges = listed(base.get("state")), listed(base.get("edge"))
    start = base.get("start") if isinstance(base.get("start"), dict) else {}
    have_states = {str(s.get("id")) for s in states if "state" not in s}
    have_edges = {str(e.get("id")) for e in edges if "edge" not in e} | {START}
    have_steps = ({str(w.get("id")) for s in states for w in listed(s.get("work"))}
                  | {str(d.get("id")) for e in [*edges, start] for d in listed(e.get("do"))})
    out = []
    for s in listed(ext.get("state")):
        sid = str(s.get("id"))
        if "state" not in s and sid in have_states:
            out.append(f"state {sid}: the workflow has this state; a module adds work to it ([[state]] state = "
                       f"\"{sid}\" with its [[state.work]]) and never redefines it")
        for w in listed(s.get("work")):
            if str(w.get("id")) in have_steps:
                out.append(f"work {w.get('id')}: the workflow has work or an automation with this id; what a module "
                           f"adds takes ids of its own")
    for e in listed(ext.get("edge")):
        eid = str(e.get("id"))
        if "edge" not in e and eid in have_edges:
            out.append(f"edge {eid}: the workflow has this edge; a module adds automations to it ([[edge]] edge = "
                       f"\"{eid}\" with its [[edge.do]]) and never changes where it goes from or to, or when")
        for d in listed(e.get("do")):
            if str(d.get("id")) in have_steps:
                out.append(f"automation {d.get('id')}: the workflow has work or an automation with this id; what a "
                           f"module adds takes ids of its own")
    return out


def unknown(where: str, table, kind: str, also=()) -> list[str]:
    """``where: unknown key "untill" (did you mean "until"?)`` for each key a table of this kind does not have
    (``also``: keys refused in words of their own)."""
    import difflib
    out = []
    for k in table if isinstance(table, dict) else ():
        if k not in KEYS[kind] and k not in also:
            near = difflib.get_close_matches(str(k), sorted(KEYS[kind]), n=1)
            out.append(f'{where}: unknown key "{k}"' + (f' (did you mean "{near[0]}"?)' if near else ""))
    return out


def _automations(table: dict, where: str, problems: list[str]) -> list:
    """``do`` of an edge (or of ``[start]``): a list of automations, whatever was written there."""
    do = table.get("do") or []
    do = [do] if isinstance(do, dict) else do
    if not isinstance(do, list):
        problems.append(f"{where}: do is a list of automations ([[{'start' if where == 'start' else 'edge'}.do]])")
        return []
    return list(do)


def _plain_id(what: str, sid, problems: list[str]) -> None:
    """Ids with a double dash are the ones the workflow makes for its own steps (``<edge>--to``): none is written."""
    if "--" in str(sid or ""):
        problems.append(f"{what}: an id has single dashes only (a double dash marks the steps the workflow makes "
                        f"itself)")


def compile_states(doc: dict, problems: list[str]) -> tuple[list[dict], dict]:
    """The steps a states-and-edges workflow runs as, and its shape for its page:
    ``{"start": edge, "states": [state], "edges": [edge]}``, each edge with ``steps`` (its automations' ids) and
    ``lands`` (the states it can end in)."""
    raw_states = doc.get("state") or []
    raw_edges = doc.get("edge") or []
    start = doc.get("start") or {}
    if not isinstance(raw_states, list) or not all(isinstance(s, dict) for s in raw_states):
        problems.append("state: a list of states ([[state]]), each with its id")
        return [], {}
    if not isinstance(raw_edges, list) or not all(isinstance(e, dict) for e in raw_edges):
        problems.append("edge: a list of edges ([[edge]]), each with its id, from and to")
        return [], {}
    raw_states, raw_edges = _additions(raw_states, raw_edges, problems)
    acts_only = isinstance(start, dict) and not start.get("to") and bool(start.get("do")) \
        and not raw_states and not raw_edges  # a workflow that only acts (a sweep): it ends once [start] has run
    problems += unknown("start", start, "start")
    if not isinstance(start, dict) or not start.get("to") and not acts_only:
        problems.append("start: say which state a record enters in ([start] to = \"<state>\")")
        start = {"to": None}
    if doc.get("steps"):
        problems.append("steps: a workflow written as states and edges has no steps of its own: put the work on "
                        "its states and the automations on its edges")
    state_ids = [str(s.get("id", "")) for s in raw_states]
    known = set(state_ids)
    for sid in state_ids:
        if not sid:
            problems.append("state: give every state its id, the value its record's state field takes")
        _plain_id(f"state {sid}", sid, problems)
    if len(known) != len(state_ids):
        problems.append("state: two states share an id")
    edges: list[dict] = []
    edge_ids: set = set()
    for i, e in enumerate(raw_edges):
        eid = str(e.get("id", ""))
        where = f"edge {eid or i + 1}"
        frm, to = e.get("from"), e.get("to")
        if not eid or "--" in eid:
            problems.append(f"{where}: an edge's id is lower-case letters, digits and single dashes")
        if eid in edge_ids or eid == START:
            problems.append(f"{where}: two edges share this id (or it is 'start', the entry's)")
        edge_ids.add(eid)
        if frm not in known:
            problems.append(f"{where}: from {frm!r} is not one of the workflow's states")
        if to not in known:
            problems.append(f"{where}: to {to!r} is not one of the workflow's states")
        if set(e) - {"id", "label", "from", "to", "when", "do", "help"}:
            problems.append(f"{where}: an edge takes id, label, from, to, when (offered only when it holds), help "
                            f"and do (its automations), not {', '.join(sorted(set(e) - {'id', 'label', 'from', 'to', 'when', 'do', 'help'}))}")
        edges.append({"id": eid, "label": str(e.get("label") or eid.replace("-", " ").capitalize()),
                      "from": frm, "to": to, "when": e.get("when") or None, "help": str(e.get("help") or ""),
                      "do": _automations(e, where, problems)})
    if start.get("to") is not None and start["to"] not in known:
        problems.append(f"start: to {start['to']!r} is not one of the workflow's states")
    entry = {"id": START, "label": str(start.get("label") or "Filed"), "from": None, "to": start.get("to"),
             "when": None, "help": "", "do": _automations(start, "start", problems)}
    out_of: dict = {sid: [e for e in edges if e["from"] == sid] for sid in state_ids}
    names = {str(s.get("id", "")): str(s.get("label") or s.get("id", "")) for s in raw_states}
    loops = _loops(edges, state_ids)
    steps: list[dict] = []
    works: dict[str, list[dict]] = {}
    routes: dict[str, dict] = {}  # automated state -> the step that decides which edge it leaves by
    lands: dict[str, list[str]] = {sid: [] for sid in state_ids}  # state -> steps that may move the record there
    shape_states = []
    for s in raw_states:
        sid = str(s.get("id", ""))
        if set(s) - {"id", "label", "tone", "work", "help"}:
            problems.append(f"state {sid}: a state takes id, label, tone, help and work (what is done there)")
        ws = s.get("work") or []
        ws = [ws] if isinstance(ws, dict) else ws
        if not isinstance(ws, list) or not all(isinstance(w, dict) for w in ws):
            problems.append(f"state {sid}: work is what someone does while the record is here ([[state.work]])")
            ws = []
        outs = out_of.get(sid, [])
        if ws and not outs:
            problems.append(f"state {sid}: work is done here, but no edge leads out of it")
        if outs and not ws:
            problems.append(f"state {sid}: edges lead out of it, but no one works it: give it work, or move the "
                            f"record on with an automation's diversion instead")
        compiled = []
        kinds = {w.get("kind", "task") for w in ws}
        automated = bool(ws) and kinds <= {"automation", "wait"}
        if kinds & {"automation", "wait"} and not automated:
            problems.append(f"state {sid}: a state is worked by people or automated, not both: give the automation "
                            f"a state of its own, or put it on an edge")
        if automated and len(outs) > 1:
            for e in outs[:-1]:
                if not e["when"]:
                    problems.append(f"state {sid}: it is automated, so the record leaves by the first edge whose "
                                    f"when holds: give edge {e['id']} its when (only the last edge may go without)")
        for w in ws:
            w = dict(w)
            where = f"state {sid}: work {w.get('id', '?')}"
            if w.get("kind", "task") not in ("task", "gate", "automation", "wait"):
                problems.append(f"{where}: the work on a state is a person's task, a signed decision, an "
                                f"automation or a wait (kind = \"task\", \"gate\", \"automation\" or \"wait\")")
            _plain_id(where, w.get("id"), problems)
            problems += unknown(where, w, "work", ("outcomes", "after", *WORKED_OUT))
            for k in ("outcomes", "after", *WORKED_OUT):
                if k in w:
                    problems.append(f"{where}: {k} is the workflow's to work out from its states and edges")
            if automated:
                pass  # it leaves by its edges' conditions, not by anyone's choice
            elif len(outs) > 1:
                w["outcomes"] = [e["id"] for e in outs]
                conds = {e["id"]: e["when"] for e in outs if e["when"]}
                if conds:
                    w["outcome_when"] = conds
                labels = {e["id"]: e["label"] for e in outs}
                w["outcome_labels"] = labels
            elif w.get("kind") == "gate":
                problems.append(f"{where}: a decision chooses between edges; this state has "
                                f"{'one' if outs else 'none'}")
            elif outs and w.get("kind", "task") == "task" and outs[0]["to"] in (w.get("moves") or []) \
                    and not any(isinstance(a, dict) and a.get("to") for a in outs[0]["do"]):
                w["state"] = outs[0]["to"]  # moving the record along its one edge by hand does the task
            w["at_state"] = sid
            if not automated and len(outs) == 1 and w.get("kind", "task") == "task" and not outs[0]["when"] \
                    and not any(isinstance(a, dict) and a.get("to") for a in outs[0]["do"]):
                # one way on: the task is that move, so moving the record along it on its page does the task
                w["state"], w["on_edge"] = outs[0]["to"], outs[0]["id"]
            compiled.append(w)
        works[sid] = compiled
        shape_states.append({"id": sid, "label": s.get("label"), "tone": s.get("tone"), "help": str(s.get("help") or ""),
                             "work": [w.get("id") for w in compiled], "out": [e["id"] for e in outs],
                             "automated": automated})
        if automated and any(e["when"] for e in outs):
            # the first edge whose condition holds, decided once, when the work is done: the edges' own steps read
            # that choice, not the conditions, which an edge that has already moved the record could make hold again
            rid = ROUTE.format(state=sid)
            routes[sid] = {"id": rid, "title": f"Choose the way out of {names.get(sid, sid)}", "kind": "automation",
                           "route": [[e["id"], e["when"]] for e in outs], "after": [w.get("id") for w in compiled],
                           "needs": [w.get("id") for w in compiled]}
            for e in outs:
                e["route"] = f"{rid}.edge == '{e['id']}'"
    # each edge's automations, then the step that moves the record along it
    for e in [entry, *edges]:
        frm_works = works.get(e["from"], []) if e["from"] else []
        anchors = [w.get("id") for w in frm_works]
        first = anchors  # what the edge's steps wait for: the work, and the choice of the way out if one is made
        if "route" in e:
            chosen = e["route"]
            first = anchors + [routes[e["from"]]["id"]]
        elif e["from"] and len(out_of.get(e["from"], [])) > 1:
            chosen = _either([f"{a}.outcome == '{e['id']}'" for a in anchors])
        else:
            chosen = None
        diverts, done = [], []
        e["steps"], e["lands"] = [], [e["to"]] if e["to"] else []
        for j, a in enumerate(e["do"]):
            if not isinstance(a, dict):
                problems.append(f"edge {e['id']}: do is a list of automations ([[edge.do]])")
                continue
            a = dict(a)
            where = f"edge {e['id']}: automation {a.get('id', j + 1)}"
            if a.get("kind", "automation") != "automation":
                problems.append(f"{where}: an edge carries automations; a person's work goes on a state")
            _plain_id(where, a.get("id"), problems)
            problems += unknown(where, a, "automation", ("outcomes", *WORKED_OUT))
            for k in ("outcomes", *WORKED_OUT):
                if k in a:
                    problems.append(f"{where}: {k} is the workflow's to work out from its states and edges")
            waits = a.pop("after", None) or []  # earlier automations of this edge it waits for, besides those it reads
            waits = [waits] if isinstance(waits, str) else waits
            if not isinstance(waits, list) or not all(isinstance(w, str) for w in waits):
                problems.append(f"{where}: after is a list of earlier automations of the same edge")
                waits = []
            for w in waits:
                if w not in done:
                    problems.append(f"{where}: after {w!r} is not an earlier automation of edge {e['id']}: an "
                                    f"automation waits only for one before it on its own edge")
            to = a.pop("to", None)
            on_fail = a.pop("on_fail", None)
            if on_fail is not None and on_fail not in known:
                problems.append(f"{where}: on_fail {on_fail!r} is not one of the workflow's states")
                on_fail = None
            if to is not None and to not in known:
                problems.append(f"{where}: to {to!r} is not one of the workflow's states")
            if to is not None and not a.get("when"):
                problems.append(f"{where}: a diversion says when it diverts the record (when = \"...\"); to always "
                                f"send it there, make it the edge's own to")
            own = a.get("when")
            a.update(kind="automation", after=first + [d for d in done if d in waits or _reads(a, d)], when=_cond(chosen, own),
                     needs=list(anchors),
                     on_edge=e["id"])
            if to:
                a["state"] = to
                diverts.append(own)
                if (e["id"], to) not in loops:
                    lands.setdefault(to, []).append(a.get("id"))
                if to not in e["lands"]:
                    e["lands"].append(to)
            steps.append(a)
            done.append(a.get("id"))
            e["steps"].append(a.get("id"))
            if on_fail:  # never runs on its own: the engine does it when the automation fails
                steps.append({"id": FAILED.format(step=a.get("id")), "kind": "automation", "state": on_fail,
                              "title": f"Move to {names.get(on_fail, on_fail)} ({a.get('title') or a.get('id')} failed)",
                              "after": [a.get("id")], "needs": [a.get("id")], "on_edge": e["id"], "when": "false"})
                if (e["id"], on_fail) not in loops:
                    lands.setdefault(on_fail, []).append(FAILED.format(step=a.get("id")))
                if on_fail not in e["lands"]:
                    e["lands"].append(on_fail)
        if e["to"]:
            arrive = ARRIVE.format(edge=e["id"])
            steps.append({"id": arrive, "title": f"Move to {names.get(e['to'], e['to'])}", "kind": "automation",
                          "state": e["to"],
                          "after": first + done, "needs": list(anchors), "on_edge": e["id"],
                          "when": _cond(chosen, *[f"not ({d})" for d in diverts if d])})
            if (e["id"], e["to"]) not in loops:
                lands.setdefault(e["to"], []).append(arrive)
        e["ends_pass"] = sorted({t for (eid, t) in loops if eid == e["id"]})
    # the work on each state follows every step that may move the record there
    work_steps = []
    for sid in state_ids:
        for w in works.get(sid, []):
            w["after"] = list(lands.get(sid, []))
            if not w["after"]:
                problems.append(f"state {sid}: no edge leads into it, so its work is never done")
            work_steps.append(w)
    shape = {"start": entry, "states": shape_states, "edges": edges}
    return work_steps + list(routes.values()) + steps, shape
