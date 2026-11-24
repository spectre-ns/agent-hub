"""The visual workflow editor edits a workflow as states and edges: the hub writes the text from the editor's model
(practicehub/web/workflow_editor.py), checks it, shows it as text, and proposes it. A workflow in the old form (a list
of steps) is never opened in the diagram, nor converted: the page says so and offers the text editor."""

import html
import json
import re
import tomllib

from practicehub.web.assets import JS
from practicehub.web.workflow_editor import condition_words, editor_toml, is_old_form, source_of
from practicehub.workflows import parse
from test_web import call, server, token  # noqa: F401

# what the editor holds after someone built a workflow in it: the controls' leftovers (blank text, empty lists,
# false checkboxes) included
MODEL = {
    "id": "built-in-editor", "title": "Built in the editor", "record": "Patient", "summary": "",
    "trigger": {"starters": ["front-desk"], "event": "", "schedule": ""},
    "input": [{"name": "reason", "type": "text", "required": True, "label": "Reason"}],
    "start": {"to": "checking", "label": "Filed", "do": []},
    "state": [
        {"id": "checking", "label": "Checking", "tone": "active", "work": [
            {"id": "wait-a-day", "title": "Wait a day", "kind": "wait", "for": "1d", "until": "", "when": ""}]},
        {"id": "sorting", "label": "To sort", "tone": "waiting", "help": "", "work": [
            {"id": "sort", "title": "Sort it", "kind": "task", "role": "front-desk", "risk": "low",
             "persons_only": False, "not_by": [], "context": ["input"],
             "fields": [{"name": "urgent", "type": "boolean", "label": "Urgent", "required": False},
                        {"name": "note", "type": "text", "required": True}]}]},
        {"id": "deciding", "label": "To decide", "work": [
            {"id": "decide", "title": "Decide", "kind": "gate", "role": "practice-manager", "signer": "person",
             "meaning": "I approve this"}]},
        {"id": "closed", "label": "Closed", "tone": "done", "work": []},
    ],
    "edge": [
        {"id": "checked", "label": "Checked", "from": "checking", "to": "sorting", "when": "", "do": []},
        {"id": "sorted", "label": "Sorted", "from": "sorting", "to": "deciding", "do": [
            {"id": "tell", "title": "Tell the manager", "action": "message.send",
             "params": {"channel": "internal", "to": "role:practice-manager", "body": "Sorted: {{sort.note}}"},
             "when": "sort.urgent == true", "after": []},
            {"id": "straight-to-close", "title": "Close the trivial ones", "action": "message.send",
             "params": {"channel": "internal", "to": "role:front-desk", "body": "Closed"},
             "when": "sort.note == 'nothing'", "to": "closed", "on_fail": "sorting", "after": ["tell"]}]},
        {"id": "approve", "label": "Approve", "from": "deciding", "to": "closed", "when": "", "do": []},
        {"id": "send-back", "label": "Send it back", "from": "deciding", "to": "sorting",
         "when": "sort.urgent == false", "help": "", "do": []},
    ],
}


def test_the_editors_model_is_written_as_states_and_edges():
    source = editor_toml(MODEL)
    assert "[[steps]]" not in source and "[start]" in source and "[[state.work]]" in source
    assert "[[state.work.fields]]" in source and "[[edge.do]]" in source and "[edge.do.params]" in source
    wf = parse(source)
    assert wf.shape and [s["id"] for s in wf.shape["states"]] == ["checking", "sorting", "deciding", "closed"]
    assert wf.step("decide").kind == "gate" and wf.step("sort").fields[1]["required"]
    tell = wf.step("straight-to-close")
    assert tell.state == "closed" and "tell" in tell.after
    # what an empty control leaves is not written; what was said reads back the same
    back = tomllib.loads(source)
    assert "summary" not in back and "event" not in back["trigger"] and "do" not in back["start"]
    assert "persons_only" not in back["state"][1]["work"][0] and "required" not in back["state"][1]["work"][0]["fields"][0]
    assert back["edge"][1]["do"][1] == {k: v for k, v in MODEL["edge"][1]["do"][1].items()}
    # each state is followed by the ways out of it, as a person would write it
    assert source.index('id = "sorted"') < source.index('id = "deciding"') < source.index('id = "approve"')
    assert editor_toml(back) == source  # written out again, it does not move


def test_the_form_sends_the_model_or_the_text():
    assert source_of({"source": "id = 'x'\n", "model": json.dumps(MODEL)}) == "id = 'x'\n"
    assert source_of({"source": "", "model": json.dumps(MODEL)}) == editor_toml(MODEL)
    assert source_of({"source": "", "model": "not json"}) == ""
    assert not is_old_form(MODEL) and is_old_form({"id": "x", "steps": [{"id": "a"}]})
    assert condition_words(MODEL)["sort.urgent == true"] == "urgent"


def test_a_workflow_built_in_the_editor_is_proposed_in_states_form(hub, server):  # noqa: F811
    jo = token(hub, "jo")
    code, _ = call(server, "POST", "/workflows/propose", jo, form={"source": "", "model": json.dumps(MODEL),
                                                                     "note": "built in the editor"})
    assert code == 303
    v = hub.versions("built-in-editor")[0]
    assert v["status"] == "proposed" and "[[steps]]" not in v["source"]
    wf = parse(v["source"])  # an authored workflow: every rule applies
    assert wf.shape and wf.shape["start"]["to"] == "checking"


def test_the_check_writes_the_text_and_says_the_conditions(hub, server):  # noqa: F811
    code, page = call(server, "POST", "/api/workflows/check", token(hub, "jo"), body={"model": MODEL})
    res = json.loads(page)
    assert code == 200 and res["ok"], res["problems"]
    assert res["source"] == editor_toml(MODEL) and res["data"]["state"][0]["id"] == "checking" and not res["old"]
    assert res["words"]["sort.note == 'nothing'"] == "note is nothing"
    broken = {**MODEL, "edge": [dict(MODEL["edge"][0], to="nowhere"), *MODEL["edge"][1:]]}
    res = json.loads(call(server, "POST", "/api/workflows/check", token(hub, "jo"), body={"model": broken})[1])
    assert not res["ok"] and any("nowhere" in p for p in res["problems"])


def _text(page: str) -> str:
    page = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", page))


def test_the_editor_opens_a_workflow_as_states(hub, server):  # noqa: F811
    code, page = call(server, "GET", "/workflows/new", token(hub, "jo"))
    assert code == 200
    boot = json.loads(re.search(r'id="wf-boot">(.*?)</script>', page, re.S).group(1))
    assert boot["data"]["start"]["to"] and boot["data"]["state"] and not boot["old"]
    assert 'data-add="state"' in page and 'name="model"' in page and 'id="wf-old"' not in page
    seen = _text(page)
    assert "input." not in seen and "==" not in seen
    # the editor's script edits states and edges, never a list of steps
    assert "wf.steps" not in JS and "modelIn.value = JSON.stringify(wf)" in JS and "function edgePanel" in JS


OLD = '''id = "old-form"
title = "Old form"
[trigger]
starters = ["front-desk"]
[[steps]]
id = "do-it"
title = "Do it"
kind = "task"
role = "no-such-role"
'''


def test_the_old_form_is_never_opened_in_the_diagram(hub, server):  # noqa: F811
    code, page = call(server, "POST", "/workflows/propose", token(hub, "jo"), form={"source": OLD, "note": "x"})
    assert code == 200  # refused, and shown again to be fixed
    boot = json.loads(re.search(r'id="wf-boot">(.*?)</script>', page, re.S).group(1))
    assert boot["old"] and boot["data"] is None  # not converted
    assert 'id="wf-old"' in page and "written in the old form" in page and 'href="?plain=1"' in page
    assert html.escape('[[steps]]', quote=False) in page.split('id="wf-toml"', 1)[1]  # the text, as written
    assert '<div id="wf-visual" hidden>' in page
    res = json.loads(call(server, "POST", "/api/workflows/check", token(hub, "jo"), body={"source": OLD})[1])
    assert res["old"]
