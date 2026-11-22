"""Workflow definitions: how a practice writes down the way it works.

A workflow is a TOML document a practice manager can read and edit. Its steps are done by roles, and a role is held
by people, agents or both; the workflow never says which. Three kinds of step:

* ``task``: someone holding the role does the work and fills in the step's fields. An agent does it with the step's
  ``skill``; a person with a form. Same fields, same validation.
* ``gate``: someone decides, choosing one of the ``outcomes``. A decision is an electronic signature. A gate with
  ``signer = "person"`` is never decided by an agent; one with ``signer = "any"`` may be decided by an agent only
  while ``agent_criteria`` holds.
* ``automation``: a deterministic action (send a message, write a record, start another workflow), or a python or
  external block. No model runs in an automation.

Any task or automation may use a **block** (``block = "<id>"``, see ``practicehub/blocks.py``): a reusable,
parameterised step. ``params`` fill the block's inputs. A prompt block makes a task an injected prompt for an agent;
a python or external block makes an automation run code or another engine.

Steps run when every step in ``after`` has finished, and only if ``when`` holds; otherwise they are skipped.

A workflow starts by hand (``trigger.starters``), on an event (``trigger.event``, e.g. ``record.created:Observation``
or one from the practice's event catalogue) when ``trigger.when`` holds, or on a schedule (``trigger.schedule``,
``"hourly"``, ``"every 15m"`` / ``"every 2h"`` (15 minutes to 24 hours), ``"daily 07:00"``, ``"weekdays 07:00"``,
``"weekly mon 08:00"`` / ``"weekly mon,wed,fri 08:00"``, ``"monthly 1 09:00"`` / ``"monthly last 17:00"``; each fires
once per slot), once per record of ``trigger.for_each`` matching ``trigger.where``.

A ``[deadline]`` table (``within = "5d"``, ``escalate_to = "<role>"``) gives every run a time to be done by: a run still
open after it is flagged once (``run.deadline_missed``, a notice to that role) and keeps ``deadline_missed_at``.

**A record's own workflow.** A record type may name the workflow that walks it (``workflow = "<id>"`` in the type,
see ``practicehub/schema.py``): creating a record of that type, by any path, starts that workflow about it. The
record's lifecycle state is where the work is: a step with ``state = "<lifecycle state>"`` puts the record in that
state (a person's or wait step when it starts, an automation when it is done; an automation may have only a
``state``), and a ``wait`` whose ``until`` reads ``record.<state field>`` goes on when someone moves the record on its
own page. The run keeps one item, moved from step to step: the hand-off of that record. A workflow started on what
its record is about (a subject) makes the record first, from ``[new_record]`` (field = value, placeholders over
``run`` and ``input``); ``run.subject`` is then what it is about and ``run.start_state`` the state it began in.
``trigger.one_open_within = "10m"`` and ``trigger.one_open_same = ["input.<name>", ...]`` narrow one_open_per to
the same submission twice (a double press, two people taking one call): only an open run started that recently,
with the same inputs, counts; any other start is a new piece of work with its own record.
A person's or wait step's ``moves = ["<state>", ...]`` lists the states a person may move the record to on its
page while the step is open; a wait also takes the states its ``until`` waits for. When a type names its workflow,
that workflow alone moves the record's state: see ``Hub.state_move_refusal``.
``trigger.one_open_per = "subject"`` with ``trigger.one_open_while = "<condition over record>"`` keeps one open
run per subject while its record is so. When the run ends with the record in a state only workflows move it out of,
the type's ``lifecycle.abandoned`` says where it goes.

Steps read the run's context: ``run`` (``run.record`` is the reference of the run's record), ``input``, each finished
step's output by its id, and ``record``, the run's record as it is when the step runs (``record.documents``).

An automation with ``for_each = "{{record.documents}}"`` runs its action once per item of that list, in order, all or
nothing; ``{{each}}`` is the item (a reference stays a reference) and ``each_record`` the record it points at.
``where = "each_record.status == 'approved'"`` keeps only some items. Its output is ``each`` (one output per item
that ran) and ``passed_over`` (the items ``where`` left out). This is how a workflow acts on linked records,
including records another module owns, without code.

A ``task`` with ``for_each = "{{input.reviewers}}"`` is done once per person of that list (member ids or references to
them): each gets a child item of the run's item, assigned to them when they hold the step's role, and the step is
done when every one of them has done theirs. Its output is ``each``: one answer per person (with ``person``).

A ``wait`` step does nothing until ``until`` holds, checked again whenever a record changes. With ``for_each`` it
waits until ``until`` holds for every item (over ``each`` and ``each_record``), e.g. until every document a CAPA
revised is approved again. Its ``role``, if given, gets a notice saying what the work waits for. Its output is
``waited_for``: the items it waited on. ``timeout = "2d"`` ends a wait on ``until`` anyway after that long; its output's
``timed_out`` says which way it ended (``when = "chase.timed_out"``). A wait may instead wait for a time: ``for = "3d"``,
or ``at = "{{input.appointment}} - 1d"`` (one placeholder giving a date, a wall time or an instant, optionally moved
by ``+``/``-`` a duration); the clock's tick ends it then, in the practice's time zone.

A task or gate may have a deadline (``due = "2d"``, ``"4h"``, ``"30m"``); when it passes, the item is flagged overdue
``not_by`` lists earlier steps whose doer may not also do this one, and may name ``run`` (whoever started the run)
and ``record.editors`` (whoever wrote the record as it stands: created it or changed its fields since it was last in
its lifecycle's ``in_use`` state; an author never approves their own revision, and an earlier one does not count).
With ``escalate_to = "<role>"``, a notice goes to that role. When an agent's work on a task goes back to people
(its sponsor rejected it, or the agent gave it up), it goes to ``people_role`` (to whoever started the run, if they
hold it); with no ``people_role`` and no person holding the step's role, to whoever started the run. ``assign_to = "{{run.record.generalPractitioner}}"``
offers the item first to that member (still only if eligibility allows).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field

from . import expr
from .expr import shift_date  # noqa: F401 - date arithmetic in {{...}}, as in conditions
from .fields import check_spec, normalize

RISKS = ("low", "medium", "high")
KINDS = ("task", "gate", "automation", "wait")
ID = re.compile(r"[a-z][a-z0-9\-]*")
RESERVED_IDS = {"new"}
DURATION = re.compile(r"(\d+)([mhdw])")
_HM = r"([01]\d|2[0-3]):([0-5]\d)"
_DAY = r"(?:mon|tue|wed|thu|fri|sat|sun)"
SCHEDULE = re.compile(rf"(hourly|every (\d+)([mh])|daily {_HM}|weekdays {_HM}|weekly ({_DAY}(?:,{_DAY})*) {_HM}"
                      rf"|monthly ([1-9]|1\d|2[0-8]|last) {_HM})")
SCHEDULE_HELP = ("trigger.schedule: say 'hourly', 'every 15m' or 'every 2h' (from 15 minutes to 24 hours), "
                 "'daily 07:00', 'weekdays 07:00', 'weekly mon 08:00' or 'weekly mon,wed,fri 08:00', or "
                 "'monthly 1 09:00' (a day from 1 to 28, or 'last' for the last day of the month)")
# a wait's ``at``: one placeholder giving a date or a time, optionally moved by a duration: "{{input.when}} - 1d"
AT = re.compile(r"\s*(\{\{[^{}]*\}\})\s*(?:([+-])\s*(\d+[mhdw]))?\s*")


def schedule_problem(text) -> str | None:
    """What is wrong with a ``trigger.schedule``, in words; None when it is one the clock knows."""
    m = SCHEDULE.fullmatch(str(text).strip())
    if not m:
        return SCHEDULE_HELP
    if m.group(2):
        minutes = int(m.group(2)) * (60 if m.group(3) == "h" else 1)
        if not 15 <= minutes <= 1440:
            return SCHEDULE_HELP
    return None


def duration_seconds(text: str) -> int | None:
    m = DURATION.fullmatch(str(text).strip())
    if not m:
        return None
    return int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2)]


class DefinitionError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass
class Step:
    id: str
    title: str
    kind: str
    role: str | None = None
    after: list[str] = field(default_factory=list)
    when: str | None = None
    risk: str = "low"
    skill: str | None = None
    instructions: str = ""
    context: list[str] = field(default_factory=list)
    fields: list[dict] = field(default_factory=list)
    review_of: str | None = None
    not_by: list[str] = field(default_factory=list)
    outcomes: list[str] = field(default_factory=list)
    signer: str = "any"
    meaning: str = ""
    meanings: dict = field(default_factory=dict)   # outcome -> what signing that outcome says
    meanings_if_did: dict = field(default_factory=dict)  # earlier step -> what signing says when its doer signs
    agent_criteria: str | None = None
    action: str | None = None
    params: dict = field(default_factory=dict)
    persons_only: bool = False
    block: str | None = None
    due: str | None = None
    escalate_to: str | None = None
    priority: str = "normal"         # "urgent" puts the work at the top of every queue, marked as urgent
    assign_to: str | None = None
    people_role: str | None = None   # who gets an agent's work here when it goes back to people (rejected, given up)
    for_each: str | None = None      # an automation runs its action once per item of this list
    where: str | None = None         # ... for the items where this holds (over ``each`` and ``each_record``)
    until: str | None = None         # a wait step: done when this holds (for every item of for_each, if given)
    wait_for: str | None = None      # a wait step: done after this long (``for = "3d"``)
    at: str | None = None            # a wait step: done at this moment (``at = "{{input.appointment}} - 1d"``)
    timeout: str | None = None       # a wait with until: done anyway, as timed out, after this long
    state: str | None = None         # the lifecycle state the run's record moves to when this step is done
    moves: list = field(default_factory=list)  # states a person may move the record to on its page meanwhile

    @property
    def agents_may(self) -> bool:
        return not self.persons_only and not (self.kind == "gate" and self.signer == "person")


@dataclass
class Workflow:
    id: str
    title: str
    summary: str
    record: str | None
    starters: list[str]
    event: str | None
    inputs: list[dict]
    steps: list[Step]
    source: str = ""
    when: str | None = None          # trigger.when: a condition over ``event`` (and ``record`` for record events)
    schedule: str | None = None
    for_each: str | None = None      # a record type: one run per matching record when the schedule fires
    where: str | None = None         # trigger.where: a condition over ``record`` for for_each
    deadline: str | None = None      # [deadline] within: a run still open this long after it started is late
    deadline_escalate_to: str | None = None  # [deadline] escalate_to: the role told when it is
    one_open_per: str | None = None  # trigger.one_open_per: "record" or "input.<name>": one open run per value
    allow_duplicate: bool = False    # trigger.allow_duplicate: the starter may confirm another one anyway
    one_open_while: str | None = None  # trigger.one_open_while: ...only while the open run's record is so
    new_record: dict = field(default_factory=dict)  # [new_record]: started on its subject, the record it makes
    one_open_within: str | None = None  # trigger.one_open_within: ...only an open run started this recently
    one_open_same: list = field(default_factory=list)  # trigger.one_open_same: ...and with these same inputs

    def step(self, sid: str) -> Step:
        for s in self.steps:
            if s.id == sid:
                return s
        raise KeyError(sid)

    def order(self) -> list[list[Step]]:
        """Steps in layers: each layer's steps depend only on earlier layers (for the diagram)."""
        depth: dict[str, int] = {}

        def d(s: Step) -> int:
            if s.id not in depth:
                depth[s.id] = 0 if not s.after else 1 + max(d(self.step(a)) for a in s.after)
            return depth[s.id]

        layers: dict[int, list[Step]] = {}
        for s in self.steps:
            layers.setdefault(d(s), []).append(s)
        return [layers[k] for k in sorted(layers)]


# not_by names, besides earlier steps: whoever started the run, and whoever created or changed the run's record
NOT_BY_PEOPLE = ("run", "record.editors")


def parse(source: str, catalog=None, *, stored: bool = False) -> Workflow:
    """Parse and validate a workflow. ``catalog`` (a Pack) checks roles, skills, actions and record types.
    ``stored``: a version already approved and kept. Rules added since it was approved are not applied to it, so it
    reads and runs as it was approved (a hub on new code must not lose a version it has in force); new versions,
    proposed or installed, get every rule."""
    try:
        doc = tomllib.loads(source)
    except tomllib.TOMLDecodeError as exc:
        raise DefinitionError([f"The workflow is not valid TOML: {exc}"]) from None
    problems: list[str] = []
    wid = str(doc.get("id", ""))
    if not ID.fullmatch(wid) or wid in RESERVED_IDS:
        problems.append("id: give the workflow an id of lower-case letters, digits and dashes (not 'new')")
    if not doc.get("title"):
        problems.append("title: give the workflow a title")
    trig = doc.get("trigger", {}) or {}
    for key in ("when", "where"):
        if trig.get(key):
            try:
                expr.check(trig[key])
            except expr.ExprError as exc:
                problems.append(f"trigger.{key}: {exc}")
    if trig.get("schedule") and schedule_problem(trig["schedule"]):
        problems.append(schedule_problem(trig["schedule"]))
    deadline = doc.get("deadline") or {}
    if not isinstance(deadline, dict):
        problems.append("deadline: a table with within = \"5d\" and, if you like, escalate_to = \"<role>\"")
        deadline = {}
    if deadline:
        if set(deadline) - {"within", "escalate_to"}:
            problems.append("deadline: takes within (how long a run may take) and escalate_to (the role told when "
                            "it takes longer)")
        if duration_seconds(deadline.get("within", "")) is None:
            problems.append("deadline: within is how long a run may take, a duration like '4h', '5d' or '2w'")
    one = trig.get("one_open_per")
    if one is not None and not (isinstance(one, str) and (one in ("record", "subject") or one.startswith("input."))):
        problems.append("trigger.one_open_per: \"record\", \"subject\" or \"input.<name>\", the value only one "
                        "open run may have at a time")
        one = None
    while_ = trig.get("one_open_while")
    if while_ is not None:
        if not one or not isinstance(while_, str):
            problems.append("trigger.one_open_while: a condition over record (the open run's record), with "
                            "one_open_per")
            while_ = None
        else:
            try:
                expr.check(while_)
            except expr.ExprError as exc:
                problems.append(f"trigger.one_open_while: {exc}")
    within = trig.get("one_open_within")
    if within is not None and (not one or duration_seconds(str(within)) is None):
        problems.append("trigger.one_open_within: a duration like '10m', with one_open_per: only a run started "
                        "that recently counts (the same submission twice, not a new concern)")
        within = None
    same = trig.get("one_open_same") or []
    names = {str(i.get("name")) for i in doc.get("input", []) or []}
    if not isinstance(same, list) or not one or not all(
            isinstance(x, str) and x.startswith("input.") and x[6:] in names for x in same):
        if same:
            problems.append("trigger.one_open_same: a list of the workflow's inputs (\"input.<name>\"), with "
                            "one_open_per: only an open run with the same values counts")
        same = []
    new_record = doc.get("new_record") or {}
    if not isinstance(new_record, dict) or not all(isinstance(k, str) for k in new_record):
        problems.append("new_record: a table of field = value (placeholders over run and input), the record a run "
                        "started on its subject makes")
        new_record = {}
    if new_record and not doc.get("record"):
        problems.append("new_record: the workflow is about a record; say which type (record = ...)")
    if one == "record" and not doc.get("record"):
        problems.append("trigger.one_open_per: \"record\" needs a record the workflow is about")
    if one and one.startswith("input.") and one[6:] not in {str(i.get("name")) for i in doc.get("input", []) or []}:
        problems.append(f"trigger.one_open_per: {one[6:]!r} is not one of the workflow's inputs")
    if "allow_duplicate" in trig and not isinstance(trig["allow_duplicate"], bool):
        problems.append("trigger.allow_duplicate: true or false")
    if trig.get("allow_duplicate") is True and not one:
        problems.append("trigger.allow_duplicate: lets a starter start another run anyway; it needs one_open_per")
    if trig.get("for_each") and not trig.get("schedule"):
        problems.append("trigger.for_each: needs a schedule (one run per record each time it fires)")
    if trig.get("where") and not trig.get("for_each"):
        problems.append("trigger.where: says which records of for_each get a run; add for_each")
    inputs = []
    for spec in doc.get("input", []) or []:
        problems += check_spec(spec)
        if spec.get("name"):
            inputs.append(normalize(spec))
    steps: list[Step] = []
    seen = set()
    for i, raw in enumerate(doc.get("steps", []) or []):
        sid = str(raw.get("id", ""))
        where = f"step {sid or i + 1}"
        if not ID.fullmatch(sid):
            problems.append(f"{where}: an id is lower-case letters, digits and dashes")
        if sid in seen:
            problems.append(f"{where}: two steps share this id")
        if sid in ("run", "input", "record", "each", "each_record"):
            problems.append(f"{where}: run, input, record, each and each_record are reserved names")
        seen.add(sid)
        kind = raw.get("kind", "task")
        if kind not in KINDS:
            problems.append(f"{where}: kind is one of {', '.join(KINDS)}")
            continue
        flds = []
        for spec in raw.get("fields", []) or []:
            problems += [f"{where}: {p}" for p in check_spec(spec)]
            if spec.get("name"):
                flds.append(normalize(spec))
        step = Step(
            id=sid, title=str(raw.get("title") or sid), kind=kind, role=raw.get("role"),
            after=list(raw.get("after", []) or []), when=raw.get("when"), risk=raw.get("risk", "low"),
            skill=raw.get("skill"), instructions=str(raw.get("instructions", "")).strip(),
            context=list(raw.get("context", []) or []), fields=flds, review_of=raw.get("review_of"),
            not_by=list(raw.get("not_by", []) or []), outcomes=list(raw.get("outcomes", []) or []),
            signer=raw.get("signer", "any"), meaning=str(raw.get("meaning", "")).strip(),
            meanings={str(k): str(v).strip() for k, v in (raw.get("meanings") or {}).items()} if isinstance(raw.get("meanings"), dict) else {},
            meanings_if_did={str(k): str(v).strip() for k, v in (raw.get("meanings_if_did") or {}).items()}
            if isinstance(raw.get("meanings_if_did"), dict) else {},
            agent_criteria=raw.get("agent_criteria"), action=raw.get("action"), params=dict(raw.get("params", {}) or {}),
            persons_only=bool(raw.get("persons_only", False)), block=raw.get("block") or None,
            due=raw.get("due"), escalate_to=raw.get("escalate_to"), priority=raw.get("priority", "normal"), assign_to=raw.get("assign_to"),
            people_role=raw.get("people_role") or None, for_each=raw.get("for_each") or None, where=raw.get("where") or None,
            until=raw.get("until") or None, wait_for=raw.get("for") or None, at=raw.get("at") or None,
            timeout=raw.get("timeout") or None, state=raw.get("state") or None,
            moves=list(raw.get("moves") or []) if isinstance(raw.get("moves") or [], list) else [None])
        if not all(isinstance(m, str) and m for m in step.moves):
            problems.append(f"{where}: moves lists the lifecycle states a person may move the record to while this "
                            f"step is open, e.g. moves = [\"on-hold\"]")
            step.moves = []
        if step.moves and (kind not in ("task", "gate", "wait") or not doc.get("record")):
            problems.append(f"{where}: moves is for a person's step or a wait of a workflow about a record")
        if step.state is not None and not isinstance(step.state, str):
            problems.append(f"{where}: state is the lifecycle state the record moves to when the step is done")
        if step.state and not doc.get("record"):
            problems.append(f"{where}: state moves the run's record; the workflow is about none")
        hidden = sorted(set(step.params) & {"when", "after", "until", "due", "escalate_to", "timeout", "for_each"})
        if hidden and not stored:  # written after [steps.params] in TOML, a step setting silently becomes a parameter
            problems.append(f"{where}: {', '.join(hidden)} is a step setting, not a parameter: put it above "
                            f"[steps.params], with the step's other settings")
        if step.for_each and kind not in ("automation", "wait", "task"):
            problems.append(f"{where}: for_each is for automations, waits and tasks (a task once per person)")
        if step.where and kind != "automation":
            problems.append(f"{where}: where is for automations")
        timed = [k for k, v in (("until", step.until), ("for", step.wait_for), ("at", step.at)) if v]
        if kind == "wait" and not timed:
            problems.append(f"{where}: a wait needs until, the condition it waits for, e.g. "
                            f"\"each_record.status == 'approved'\"; or for, how long it waits, e.g. \"3d\"; or at, "
                            f"when it ends, e.g. \"{{{{input.appointment}}}} - 1d\"")
        if len(timed) > 1:
            problems.append(f"{where}: a wait waits for one of until, for or at; it has {' and '.join(timed)}")
        for k, v in (("until", step.until), ("for", step.wait_for), ("at", step.at), ("timeout", step.timeout)):
            if v and kind != "wait":
                problems.append(f"{where}: {k} is for wait steps")
        if kind == "wait" and step.wait_for and duration_seconds(step.wait_for) is None:
            problems.append(f"{where}: for is how long it waits, a duration like '30m', '4h', '3d' or '1w'")
        if kind == "wait" and step.timeout:
            if duration_seconds(step.timeout) is None:
                problems.append(f"{where}: timeout is how long it waits at most, a duration like '4h', '2d' or '1w'")
            if not step.until:
                problems.append(f"{where}: timeout ends a wait on until that takes too long; a wait with for or at "
                                f"already ends on time")
        if kind == "wait" and step.at:
            m = AT.fullmatch(str(step.at))
            if not m or not TEMPLATE.fullmatch(m.group(1)):
                problems.append(f"{where}: at is one placeholder giving a date or a time, optionally moved by a "
                                f"duration, e.g. \"{{{{input.appointment}}}} - 1d\" or \"{{{{record.due}}}} + 2h\"")
        if kind == "wait" and step.for_each and not step.until:
            problems.append(f"{where}: for_each is for a wait on until (over each item)")
        if kind == "wait" and (step.action or step.block or step.fields or step.skill):
            problems.append(f"{where}: a wait does nothing but wait; it takes after, when, until (with timeout), for "
                            f"or at, for_each, and a role to tell what it waits for")
        if step.for_each and not TEMPLATE.fullmatch(str(step.for_each).strip()):
            problems.append(f"{where}: for_each is one placeholder naming a list, e.g. '{{{{record.documents}}}}'")
        if step.where and not step.for_each:
            problems.append(f"{where}: where filters the items of for_each; add for_each")
        for op in ("increment", "decrement"):
            inc = (step.params or {}).get(op) if kind == "automation" else None
            if inc is None or (op == "increment" and isinstance(inc, list)
                               and all(isinstance(x, str) and x for x in inc)):
                continue
            if not isinstance(inc, dict) or not inc or not all(
                    k and ((isinstance(v, (int, float)) and not isinstance(v, bool))
                           or (isinstance(v, str) and TEMPLATE.fullmatch(v.strip()))) for k, v in inc.items()):
                problems.append(f"{where}: {op} is a list of field names, like [\"revision\"] (by one), or a table of "
                                f"field = amount, the amount a number or one placeholder, like "
                                f"{{on_hand = \"{{{{record.quantity}}}}\"}}")
        if step.for_each and step.block:
            problems.append(f"{where}: for_each runs an action; a block step runs once")
        if step.due and duration_seconds(step.due) is None:
            problems.append(f"{where}: due is a duration like '30m', '4h', '2d' or '1w'")
        if (step.due or step.escalate_to or step.assign_to) and kind in ("automation", "wait"):
            problems.append(f"{where}: due, escalate_to and assign_to are for tasks and decisions")
        if step.people_role and kind != "task":
            problems.append(f"{where}: people_role is for tasks an agent may do")
        if step.priority not in ("normal", "urgent"):
            problems.append(f"{where}: priority is normal or urgent")
        if step.priority == "urgent" and (kind not in ("task", "gate") or not step.due):
            problems.append(f"{where}: an urgent step is a task or decision with a due time")
        if step.escalate_to and not step.due:
            problems.append(f"{where}: escalate_to needs a due time")
        if step.risk not in RISKS:
            problems.append(f"{where}: risk is one of {', '.join(RISKS)}")
        if kind in ("task", "gate") and not step.role:
            problems.append(f"{where}: say which role does this step")
        if kind == "gate":
            if len(step.outcomes) < 2:
                problems.append(f"{where}: a gate needs at least two outcomes to choose from")
            if step.signer not in ("person", "any"):
                problems.append(f"{where}: signer is 'person' or 'any'")
            if not step.meaning:
                problems.append(f"{where}: a gate needs the meaning of its signature, e.g. 'I approve this renewal'")
            if raw.get("meanings") is not None and not isinstance(raw.get("meanings"), dict):
                problems.append(f"{where}: meanings is a table of outcome = \"what signing it says\"")
            for o, text in step.meanings.items():
                if o not in step.outcomes:
                    problems.append(f"{where}: meanings names {o!r}, which is not one of its outcomes")
                elif not text:
                    problems.append(f"{where}: the meaning for {o!r} is empty")
            if raw.get("meanings_if_did") is not None and not isinstance(raw.get("meanings_if_did"), dict):
                problems.append(f"{where}: meanings_if_did is a table of earlier step = \"what signing says when "
                                f"whoever did that step signs\"")
            for o, text in step.meanings_if_did.items():
                if not text:
                    problems.append(f"{where}: the meaning for whoever did {o!r} is empty")
            if step.agent_criteria and step.signer == "person":
                problems.append(f"{where}: agent_criteria has no effect when only a person may sign")
        if step.block:
            if kind == "gate":
                problems.append(f"{where}: a decision cannot use a block; a block is a task or an automation")
            if step.action:
                problems.append(f"{where}: use an action or a block, not both")
            if step.skill:
                problems.append(f"{where}: a block step takes its skill from the block; remove skill")
        if kind == "automation" and not step.action and not step.block and not step.state:
            problems.append(f"{where}: an automation names its action or block")
        if kind == "task" and not step.fields and not step.skill and not step.block:
            problems.append(f"{where}: a task needs fields (what it produces), a skill, or a prompt block")
        for cond, label in ((step.when, "when"), (step.agent_criteria, "agent_criteria"), (step.where, "where"),
                            (step.until, "until")):
            if cond:
                try:
                    expr.check(cond)
                except expr.ExprError as exc:
                    problems.append(f"{where}: {label}: {exc}")
        steps.append(step)
    ids = {s.id for s in steps}
    for s in steps:
        for a in s.after + [n for n in s.not_by if n not in NOT_BY_PEOPLE] + ([s.review_of] if s.review_of else []):
            if a not in ids:
                problems.append(f"step {s.id}: refers to a step {a!r} that does not exist")
        for cond in (s.when, s.agent_criteria):
            if cond:
                try:
                    for p in expr.paths(cond):
                        head = p.split(".")[0]
                        if head not in ids and head not in ("run", "input", "record"):
                            problems.append(f"step {s.id}: the condition reads {p!r}, but there is no step {head!r}")
                except expr.ExprError:
                    pass
    if not steps:
        problems.append("steps: a workflow needs at least one step")
    by_id = {s.id: s for s in steps}

    def before(sid: str) -> set:
        """Every step a step waits on, directly or through others."""
        out, todo = set(), list(by_id[sid].after) if sid in by_id else []
        while todo:
            a = todo.pop()
            if a not in out and a in by_id:
                out.add(a)
                todo += by_id[a].after
        return out

    for s in steps:
        if s.meanings_if_did and s.kind != "gate":
            problems.append(f"step {s.id}: meanings_if_did is for a gate's signature")
        for o in s.meanings_if_did:
            if o not in ids:
                problems.append(f"step {s.id}: meanings_if_did names a step {o!r} that does not exist")
            elif o not in before(s.id):
                problems.append(f"step {s.id}: meanings_if_did names {o!r}, which does not come before it")
    if not problems:
        # cycles
        state: dict[str, int] = {}

        def visit(sid, trail):
            if state.get(sid) == 1:
                problems.append(f"steps {' -> '.join(trail + [sid])} wait on each other in a loop")
                return
            if state.get(sid) == 2:
                return
            state[sid] = 1
            for a in by_id[sid].after:
                visit(a, trail + [sid])
            state[sid] = 2

        for s in steps:
            visit(s.id, [])
    wf = Workflow(id=wid, title=str(doc.get("title", "")), summary=str(doc.get("summary", "")).strip(),
                  record=doc.get("record"), starters=list(trig.get("starters", []) or []), event=trig.get("event"),
                  inputs=inputs, steps=steps, source=source, when=trig.get("when"), schedule=trig.get("schedule"),
                  for_each=trig.get("for_each"), where=trig.get("where"),
                  deadline=deadline.get("within") or None, deadline_escalate_to=deadline.get("escalate_to") or None,
                  one_open_per=one or None, allow_duplicate=trig.get("allow_duplicate") is True,
                  one_open_while=while_ or None, new_record=dict(new_record),
                  one_open_within=str(within) if within else None, one_open_same=list(same))
    if catalog is not None:  # report every problem at once, so an author fixes them in one go
        try:
            problems += [p for p in catalog.check_workflow(wf) if p not in problems]
        except Exception:  # noqa: BLE001 - a malformed workflow may trip a catalog check; its own problems say why
            pass
    if problems:
        raise DefinitionError(problems)
    return wf


TEMPLATE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_\-\.]*)\s*(?:([+-])\s*(\d{1,4})\s*([dwmy]))?\s*\}\}")


def _fill(m, ctx: dict):
    v = expr.lookup(ctx, m.group(1))
    if m.group(2):  # {{run.today + 1y}}: date arithmetic, in days (d), weeks (w), months (m) or years (y)
        v = shift_date(v, m.group(2), int(m.group(3)), m.group(4))
    return v


def render(value, ctx: dict):
    """Fill {{path}} placeholders from the run context. A string that is exactly one placeholder keeps the value's
    type (a list stays a list). ``{{run.today + 1y}}`` is a date moved on: ``+``/``-`` a number of days, weeks,
    months or years (``30d``, ``2w``, ``6m``, ``1y``)."""
    if isinstance(value, str):
        m = TEMPLATE.fullmatch(value.strip())
        if m:
            return _fill(m, ctx)
        return TEMPLATE.sub(lambda mm: _text(_fill(mm, ctx)), value)
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    return value


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return ", ".join(map(str, v))
    return str(v)
