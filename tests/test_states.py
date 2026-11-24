"""A workflow written as its record's states and the edges between them (practicehub/states.py): people choose the
edge, never the state; an edge's condition says whether it is offered; automations run on the edge and may divert
the record; an automated state leaves by the first edge whose condition holds."""
import pytest

from practicehub import workflows as wfmod

HEAD = '''id = "w"
title = "W"
record = "Observation"
[trigger]
starters = ["physician"]
'''


def parse(body):
    return wfmod.parse(HEAD + body)


def problems(body):
    with pytest.raises(wfmod.DefinitionError) as bad:
        parse(body)
    return " | ".join(bad.value.problems)


PICK = '''
[start]
to = "open"
[[state]]
id = "open"
[[state.work]]
id = "decide"
kind = "gate"
role = "physician"
meaning = "I decided"
[[edge]]
id = "yes"
from = "open"
to = "done"
when = "input.ok == true"
[[edge.do]]
id = "tell"
action = "notice.send"
params = { role = "physician", text = "yes" }
[[edge.do]]
id = "escalate"
action = "notice.send"
when = "input.big == true"
to = "held"
params = { role = "physician", text = "big" }
[[edge]]
id = "no"
from = "open"
to = "done"
[[state]]
id = "held"
[[state]]
id = "done"
'''


def test_the_work_offers_its_edges_and_the_edge_runs_its_automations():
    wf = parse(PICK)
    d = wf.step("decide")
    assert d.outcomes == ["yes", "no"] and d.outcome_when == {"yes": "input.ok == true"}
    assert d.after == ["start--to"]
    tell = wf.step("tell")
    assert tell.on_edge == "yes" and tell.needs == ["decide"] and tell.when == "decide.outcome == 'yes'"
    esc = wf.step("escalate")
    assert esc.state == "held" and esc.after == ["decide"]  # it reads nothing "tell" made: it does not wait for it
    assert arrive_after(wf) == ["decide", "tell", "escalate"]
    arrive = wf.step("yes--to")
    assert arrive.state == "done" and "not (input.big == true)" in arrive.when  # a diversion wins
    assert [x["id"] for x in wf.shape["edges"]] == ["yes", "no"]
    assert wf.shape["edges"][0]["lands"] == ["done", "held"]


def test_an_automated_state_leaves_by_the_first_edge_whose_condition_holds():
    wf = parse('''
[start]
to = "checking"
[[state]]
id = "checking"
[[state.work]]
id = "check"
kind = "automation"
action = "clock.now"
[[edge]]
id = "hot"
from = "checking"
to = "urgent"
when = "check.now > '2'"
[[edge]]
id = "cold"
from = "checking"
to = "normal"
[[state]]
id = "urgent"
[[state]]
id = "normal"
''')
    assert wf.step("check").outcomes == [] and wf.step("check").after == ["start--to"]
    assert wf.step("checking--route").route == [["hot", "check.now > '2'"], ["cold", None]]
    assert wf.step("hot--to").when == "checking--route.edge == 'hot'"
    assert wf.step("cold--to").when == "checking--route.edge == 'cold'"
    assert wf.shape["states"][0]["automated"] is True


def test_what_a_states_workflow_may_not_say():
    assert "no edge leads out" in problems('''
[start]
to = "a"
[[state]]
id = "a"
[[state.work]]
id = "w"
kind = "task"
role = "physician"
''')
    assert "says when it diverts" in problems(PICK.replace('when = "input.big == true"\n', ""))
    assert "not as a list of [[steps]]" in problems(PICK + '[[steps]]\nid = "x"\ntitle = "x"\nkind = "task"\n')
    assert "worked by people or automated" in problems(PICK.replace(
        '[[state.work]]\nid = "decide"', '[[state.work]]\nid = "auto"\nkind = "automation"\naction = "clock.now"\n'
        '[[state.work]]\nid = "decide"'))
    assert "give edge first its when" in problems('''
[start]
to = "a"
[[state]]
id = "a"
[[state.work]]
id = "w"
kind = "automation"
action = "clock.now"
[[edge]]
id = "first"
from = "a"
to = "b"
[[edge]]
id = "second"
from = "a"
to = "b"
[[state]]
id = "b"
''')


def arrive_after(wf):
    return wf.step("yes--to").after


def test_an_edge_automation_may_wait_for_an_earlier_one_on_its_edge_it_does_not_read():
    """``after`` on an edge's automation: it waits for that earlier automation of the same edge (as well as for those
    whose output it reads); naming one of another edge, or a later one, is a problem."""
    waits = PICK.replace('id = "escalate"\n', 'id = "escalate"\nafter = ["tell"]\n')
    assert parse(waits).step("escalate").after == ["decide", "tell"]
    assert "not an earlier automation of edge yes" in problems(PICK.replace('id = "tell"\n', 'id = "tell"\nafter = ["escalate"]\n'))
    assert "not an earlier automation of edge yes" in problems(PICK.replace('id = "escalate"\n', 'id = "escalate"\nafter = ["decide"]\n'))
    assert "a list of earlier automations" in problems(PICK.replace('id = "escalate"\n', 'id = "escalate"\nafter = 3\n'))


def test_an_edge_automation_may_say_where_the_record_goes_if_it_fails():
    """``on_fail`` on an edge's automation: a step the engine runs only when that automation fails, moving the record
    to the state named; it lands there like any move (that state's work follows it)."""
    wf = parse(PICK.replace('id = "tell"\n', 'id = "tell"\non_fail = "held"\n').replace(
        '[[state]]\nid = "held"\n', '[[state]]\nid = "held"\n[[state.work]]\nid = "look"\nkind = "task"\n'
        'role = "physician"\nskill = "x"\n') + '''
[[edge]]
id = "looked"
from = "held"
to = "done"
''')
    fb = wf.step("tell--failed")
    assert fb.state == "held" and fb.on_edge == "yes" and fb.after == ["tell"] and fb.needs == ["tell"]
    assert fb.when == "false"  # never on its own: the automation done, it is skipped
    assert "tell--failed" in wf.step("look").after
    assert "held" in wf.shape["edges"][0]["lands"]
    assert "on_fail 'nowhere' is not one of the workflow's states" in problems(
        PICK.replace('id = "tell"\n', 'id = "tell"\non_fail = "nowhere"\n'))


def test_an_edge_back_to_its_own_state_does_not_hold_up_its_work():
    """A state's work waits for every move that may land the record there; an edge from a state back to itself ends
    the pass, so its move is not one the work waits for (it could never come before the work)."""
    wf = parse('''
[start]
to = "open"
[[state]]
id = "open"
[[state.work]]
id = "write"
kind = "task"
role = "physician"
[[edge]]
id = "written"
from = "open"
to = "done"
[[edge]]
id = "not-needed"
from = "open"
to = "open"
[[state]]
id = "done"
''')
    assert wf.step("write").after == ["start--to"]
    assert wf.shape["edges"][1]["ends_pass"] == ["open"]


ROUTED = '''id = "routed"
title = "Routed"
record = "Patient"
[trigger]
starters = ["front-desk"]
[start]
to = "checking"
[[state]]
id = "checking"
[[state.work]]
id = "look"
kind = "automation"
action = "context.set"
params = { looked = true }
[[edge]]
id = "local"
from = "checking"
to = "near"
when = "record.gender != 'other'"
[[edge.do]]
id = "mark"
action = "record.update"
[edge.do.params]
ref = "{{run.record}}"
[edge.do.params.set]
gender = "other"
[[edge]]
id = "away"
from = "checking"
to = "far"
[[state]]
id = "near"
[[state]]
id = "far"
'''


def test_an_automated_state_chooses_its_edge_once():
    """The edge out of an automated state is chosen once, when its work is done (``<state>--route``): the edges'
    steps read that choice, so an edge that changes what the conditions read cannot send the record down a second."""
    wf = wfmod.parse(ROUTED)
    route = wf.step("checking--route")
    assert route.after == ["look"] and route.route == [["local", "record.gender != 'other'"], ["away", None]]
    assert wf.step("mark").when == "checking--route.edge == 'local'" and "checking--route" in wf.step("mark").after
    assert wf.step("away--to").when == "checking--route.edge == 'away'"


def test_a_record_routed_on_its_own_field_goes_one_way(hub):
    from conftest import M
    p = hub.propose_workflow(M(hub, "jo"), ROUTED, "routing")
    hub.approve_workflow(M(hub, "rana"), "routed", p["version"])
    pat = "Patient/" + hub.records.search("Patient", limit=1)[0]["id"]
    run = hub.engine.run(hub.engine.start(M(hub, "jo"), "routed", record=pat)["id"])
    assert hub.records.read(*pat.split("/"))["gender"] == "other"  # the local edge changed what its condition read
    assert run["status"] == "completed" and run["at"] == "near"
    assert run["ctx"]["checking--route"] == {"edge": "local"}
    assert run["steps"]["away--to"]["state"] == "skipped" and run["steps"]["local--to"]["state"] == "done"


ACTS = '''id = "acts"
title = "Acts"
record = "Patient"
[trigger]
starters = ["front-desk"]
[start]
label = "Swept"
[[start.do]]
id = "note"
action = "context.set"
params = { swept = true }
[[start.do]]
id = "again"
action = "context.set"
params = { seen = "{{note.swept}}" }
'''


def test_a_workflow_that_only_acts_never_moves_its_record():
    """A sweep: ``[start]`` has automations and no ``to``, and there are no states or edges. Its automations are its
    only steps; no step moves the record, so the run ends once they are done."""
    wf = wfmod.parse(ACTS)
    assert [s.id for s in wf.steps] == ["note", "again"]
    assert not any(s.state for s in wf.steps)
    assert wf.step("again").after == ["note"]  # it reads note's output
    assert wf.shape["start"]["to"] is None and wf.shape["states"] == [] and wf.shape["edges"] == []
    # it only acts when it has nothing else: with no automations, or with states, [start] must say where to go
    assert "say which state a record enters in" in problems('[start]\nlabel = "x"\n')
    assert "say which state a record enters in" in problems(
        '[start]\n[[start.do]]\nid = "n"\naction = "context.set"\nparams = { a = 1 }\n[[state]]\nid = "a"\n')


def test_a_run_of_a_workflow_that_only_acts_ends_once_its_automations_are_done(hub):
    from conftest import M
    p = hub.propose_workflow(M(hub, "jo"), ACTS, "sweeping")
    hub.approve_workflow(M(hub, "rana"), "acts", p["version"])
    pat = "Patient/" + hub.records.search("Patient", limit=1)[0]["id"]
    before = hub.records.read(*pat.split("/"))
    run = hub.engine.run(hub.engine.start(M(hub, "jo"), "acts", record=pat)["id"])
    assert run["status"] == "completed" and not run.get("at")
    assert run["steps"]["note"]["state"] == "done" and run["steps"]["again"]["state"] == "done"
    assert hub.records.read(*pat.split("/")) == before  # nothing moved, nothing written


def test_the_hubs_own_workflows_are_written_as_workflows_that_only_act(hub):
    """The homes of the hub's own notices (an event no workflow took, an emergency access, a member's account, a
    change waiting for approval, a mention) are about no record type: each is a workflow that only acts, read by
    the same rules as any workflow, with the one step its notices are about. None is a list of steps."""
    from practicehub.credentials import ACCOUNT_SOURCE, ACCOUNT_WORKFLOW
    from practicehub.engine import UNHANDLED_SOURCE, UNHANDLED_WORKFLOW
    from practicehub.hub import APPROVAL_SOURCE, APPROVAL_WORKFLOW
    from practicehub.mentions import MENTION_SOURCE, MENTION_WORKFLOW
    from practicehub.restrictions import OVERRIDE_SOURCE, OVERRIDE_WORKFLOW
    homes = {UNHANDLED_WORKFLOW: (UNHANDLED_SOURCE, "look"), OVERRIDE_WORKFLOW: (OVERRIDE_SOURCE, "review"),
             ACCOUNT_WORKFLOW: (ACCOUNT_SOURCE, "look"), APPROVAL_WORKFLOW: (APPROVAL_SOURCE, "look"),
             MENTION_WORKFLOW: (MENTION_SOURCE, "look")}
    for wid, (source, step) in homes.items():
        assert "[[steps]]" not in source
        wf = wfmod.parse(source)  # by today's rules, as a proposal would be
        assert wf.id == wid and not wf.record and [s.id for s in wf.steps] == [step]
        assert wf.shape["start"]["to"] is None and wf.shape["states"] == [] and wf.shape["edges"] == []
        got, row = hub.workflow(wid, 0)  # what a page that shows one of its runs reads
        assert [s.id for s in got.steps] == [step] and row["version"] == 0 and row["source"] == source
    assert wfmod.acts_only_source("x", 'A "quoted" title', "It's a summary", "look", "Look") == (
        'id = "x"\ntitle = "A \\"quoted\\" title"\nsummary = "It\'s a summary"\n\n[start]\nlabel = "Told"\n\n'
        '[[start.do]]\nid = "look"\ntitle = "Look"\naction = "context.set"\n')


WORKED_OUT = {"state": '"done"', "route": '[["yes", "true"]]', "needs": '["decide"]', "on_edge": '"yes"',
              "at_state": '"open"', "outcome_when": '{ yes = "true" }', "outcome_labels": '{ yes = "Yes" }',
              "outcomes": '["yes", "no"]'}


@pytest.mark.parametrize("key", sorted(WORKED_OUT))
def test_what_the_workflow_works_out_is_never_written_on_work_or_on_an_automation(key):
    """Which edge a step runs on, where it moves the record, what it waits for and which outcomes it offers come
    from the states and edges. Written by hand on a state's work or an edge's automation, each is refused: a file
    that said ``on_edge`` or ``at_state`` of its own would be read by the record's history as something else."""
    said = f"{key} = {WORKED_OUT[key]}\n"
    on_work = PICK.replace('id = "decide"\n', 'id = "decide"\n' + said, 1)
    on_automation = PICK.replace('id = "tell"\n', 'id = "tell"\n' + said, 1)
    assert on_work != PICK and on_automation != PICK
    assert f"work decide: {key} is the workflow's to work out" in problems(on_work)
    assert f"automation tell: {key} is the workflow's to work out" in problems(on_automation)


def test_an_id_with_a_double_dash_is_the_workflows_own_to_make():
    """``<edge>--to``, ``<state>--route`` and ``<step>--failed`` are the steps the workflow makes itself: no state,
    work or automation is given an id with a double dash, as no edge is."""
    for old, new, where in (('id = "open"\n[[state.work]]', 'id = "op--en"\n[[state.work]]', "state op--en"),
                            ('id = "decide"', 'id = "de--cide"', "work de--cide"),
                            ('id = "tell"', 'id = "tell--to"', "automation tell--to")):
        assert old in PICK
        assert f"{where}: an id has single dashes only" in problems(PICK.replace(old, new))


def test_do_that_is_not_a_list_of_automations_is_a_problem_said_in_words():
    """A file is read from anywhere (the editor, an import): whatever stands where an edge's automations go, the
    answer is a problem in words, never an error of the hub's own."""
    from practicehub.states import compile_states
    for doc, where in (({"start": {"to": "a"}, "state": [{"id": "a"}], "edge": [{"id": "e", "from": "a", "to": "a",
                                                                                 "do": 5}]}, "edge e"),
                       ({"start": {"to": "a", "do": "x"}, "state": [{"id": "a"}]}, "start")):
        found: list = []
        compile_states(doc, found)
        assert any(p.startswith(f"{where}: do is a list of automations") for p in found), found
    one: list = []  # a single table where a list of them goes is that one automation
    steps, _ = compile_states({"start": {"do": {"id": "n", "action": "context.set"}}}, one)
    assert [s["id"] for s in steps] == ["n"] and not one


def test_a_key_a_workflow_does_not_have_is_refused_not_ignored():
    """A setting misspelt was dropped without a word, and the workflow ran without it (a wait with no ``until``, a
    task with no ``due``). Each table of a workflow takes the keys it has; any other is named, with the nearest."""
    for old, new, says in (
            ('title = "W"\n', 'title = "W"\nsumary = "S"\n', 'workflow: unknown key "sumary" (did you mean "summary"?)'),
            ('starters = ["physician"]\n', 'starters = ["physician"]\nscedule = "daily 07:00"\n',
             'trigger: unknown key "scedule" (did you mean "schedule"?)'),
            ('[start]\nto = "open"\n', '[start]\nto = "open"\nlable = "In"\n',
             'start: unknown key "lable" (did you mean "label"?)'),
            ('meaning = "I decided"\n', 'meaning = "I decided"\ndue_in = "2d"\n',
             'state open: work decide: unknown key "due_in"'),
            ('id = "tell"\n', 'id = "tell"\nparms = { role = "physician" }\n',
             'edge yes: automation tell: unknown key "parms" (did you mean "params"?)')):
        text = (HEAD + PICK).replace(old, new, 1)
        assert text != HEAD + PICK
        with pytest.raises(wfmod.DefinitionError) as bad:
            wfmod.parse(text)
        assert any(p.startswith(says) for p in bad.value.problems), bad.value.problems
    # what the workflow works out is refused in its own words, once, not also as a key it does not know
    said = " | ".join(p for p in problems(PICK.replace('id = "decide"\n', 'id = "decide"\non_edge = "yes"\n', 1))
                      .split(" | ") if "on_edge" in p)
    assert said == "state open: work decide: on_edge is the workflow's to work out from its states and edges"
    with pytest.raises(wfmod.DefinitionError) as bad:  # a trigger that is no table is said so, not an error
        wfmod.parse('id = "w"\ntitle = "W"\ntrigger = "daily"\n[start]\n[[start.do]]\nid = "n"\naction = "context.set"\n')
    assert any(p.startswith("trigger: a table") for p in bad.value.problems)
