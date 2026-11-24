"""BPMN 2.0 interchange: every shipped workflow survives export and import, a diagram changed in another tool brings
back its title, summary and trigger and never its steps, a file from another tool is refused with what it holds,
hostile XML is refused, and an import only ever becomes a proposal."""

import dataclasses
import glob
import json
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from conftest import M
from practicehub import bpmn, cli
from practicehub import workflows as wfmod
from practicehub.hub import Denied, Invalid
from practicehub.web.records_pages import Download
from practicehub.web.server import App

ROOT = Path(__file__).resolve().parent.parent
SHIPPED = sorted(glob.glob(str(ROOT / "packs/*/workflows/*.toml")) + glob.glob(str(ROOT / "modules/*/workflows/*.toml"))
                 + glob.glob(str(ROOT / "tests/packs/*/workflows/*.toml")))
B = f"{{{bpmn.BPMN}}}"


def same(a: wfmod.Workflow, b: wfmod.Workflow) -> bool:
    x, y = dataclasses.asdict(a), dataclasses.asdict(b)
    x.pop("source"), y.pop("source")
    return x == y


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: Path(p).stem)
def test_every_shipped_workflow_round_trips(path):
    src = Path(path).read_text()
    wf = wfmod.parse(src)
    xml = bpmn.export_bpmn(wf)
    root = ET.fromstring(xml)
    assert root.tag == B + "definitions"
    assert root.find(f"{{{bpmn.BPMNDI}}}BPMNDiagram") is not None  # a diagram BPMN tools can show
    out = bpmn.import_bpmn(xml)
    assert out.problems == []
    assert same(wf, wfmod.parse(out.source))
    assert tomllib.loads(out.source) == tomllib.loads(src)  # nothing lost, not even a default left out


def test_the_export_maps_each_kind_of_step():
    wf = wfmod.parse((ROOT / "packs/general-practice/workflows/prescription-renewal.toml").read_text())
    root = ET.fromstring(bpmn.export_bpmn(wf))
    proc = root.find(B + "process")
    assert proc.find(f"{B}userTask[@id='decide']") is not None          # a decision
    assert proc.find(f"{B}serviceTask[@id='details']") is not None      # an automation
    # two steps follow the same outcome, so the split is inclusive (every step whose condition holds runs)
    assert proc.find(f"{B}inclusiveGateway[@id='Gateway_split_decide']") is not None
    cond = [f.find(B + "conditionExpression").text for f in proc.iter(B + "sequenceFlow")
            if f.get("targetRef") == "renew--to"]  # the renew edge's move, which the lookup follows
    assert cond == ["decide.outcome == 'renew'"]
    due = proc.find(f"{B}boundaryEvent[@attachedToRef='decide']")
    assert due.get("cancelActivity") == "false" and due.find(f"{B}timerEventDefinition/{B}timeDuration").text == "P2D"
    lanes = {ln.get("name") for ln in proc.iter(B + "lane")}
    assert {"Physician", "Clinical assistant", "Automatic"} <= lanes
    start = proc.find(f"{B}startEvent[@id='Start_event']/{B}messageEventDefinition")
    msg = root.find(f"{B}message[@id='{start.get('messageRef')}']")
    assert msg.get("name") == "renewal.requested"
    capa = ET.fromstring(bpmn.export_bpmn(wfmod.parse((ROOT / "modules/qms/workflows/qms-capa.toml").read_text())))
    wait = capa.find(f"{B}process/{B}intermediateCatchEvent[@id='documents-approved']")
    assert "each_record.status == 'approved'" in wait.find(f"{B}conditionalEventDefinition/{B}condition").text


def test_schedules_and_durations_convert_both_ways():
    for s in ("hourly", "daily 07:00", "weekly mon 08:00", "weekly sun 23:30"):
        assert bpmn.schedule_from_iso(bpmn.schedule_iso(s)) == s
    for d in ("30m", "4h", "2d", "1w"):
        assert bpmn.duration_from_iso(bpmn.duration_iso(d)) == d
    assert bpmn.duration_from_iso("PT1H30M") == "90m" and bpmn.duration_from_iso("P1DT12H") == "36h"
    assert bpmn.duration_from_iso("P1M") is None  # a month is not a fixed length


# An ordinary workflow: two states with a person's work, a decision between two edges, automations on the edges
RENEWAL = """id = "renewal"
title = "Renewal"
summary = "A renewal is prepared, then a physician decides on it."
record = "MedicationRequest"

[trigger]
starters = ["physician"]

[start]
to = "requested"

[[state]]
id = "requested"
[[state.work]]
id = "prepare"
title = "Prepare the renewal"
role = "clinical-assistant"
due = "1d"
skill = "renewal-prep"

[[state]]
id = "deciding"
[[state.work]]
id = "decide"
title = "Decide on the renewal"
kind = "gate"
role = "physician"
signer = "person"
meaning = "I authorize this renewal"

[[state]]
id = "renewed"

[[state]]
id = "declined"

[[edge]]
id = "prepared"
from = "requested"
to = "deciding"

[[edge]]
id = "renew"
from = "deciding"
to = "renewed"
[[edge.do]]
id = "prescribe"
title = "Write the prescription"
action = "record.update"
[edge.do.params.set]
intent = "order"

[[edge]]
id = "decline"
from = "deciding"
to = "declined"
[[edge.do]]
id = "tell-declined"
title = "Tell the patient"
action = "message.send"
[edge.do.params]
channel = "patient-portal"
to = "{{run.subject}}"
subject = "Your renewal request"
body = "Not renewed."
"""

# A workflow that only acts: its [start] has automations, and it has no states and no edges
SWEEP = """id = "renewals-due"
title = "Renewals that have come due"
record = "MedicationRequest"

[trigger]
schedule = "daily 07:00"
for_each = "MedicationRequest"
where = "record.status == 'active'"

[start]
label = "The renewal came due"
[[start.do]]
id = "remind"
title = "Remind the patient"
action = "message.send"
[start.do.params]
channel = "patient-portal"
to = "{{run.subject}}"
body = "Your prescription is due for renewal."
"""

# One state and no edge: the entry's automation runs, and the record ends where it entered
NOTED = """id = "renewal-noted"
title = "Note a renewal request"
record = "MedicationRequest"

[start]
to = "noted"
[[start.do]]
id = "note"
action = "record.update"
[start.do.params.set]
note = "A renewal was asked for"

[[state]]
id = "noted"
"""
WRITTEN = ("start", "state", "edge")  # where a workflow's states and edges are written


def _exported(src) -> tuple[ET.Element, ET.Element]:
    root = ET.fromstring(bpmn.export_bpmn(src))
    return root, root.find(B + "process")


@pytest.mark.parametrize("src", [RENEWAL, SWEEP, NOTED], ids=["states-and-edges", "only-acts", "one-state-no-edge"])
def test_a_file_we_exported_read_back_unchanged_gives_the_same_workflow(src):
    wf = wfmod.parse(src)
    out = bpmn.import_bpmn(bpmn.export_bpmn(wf))
    assert out.problems == []
    assert same(wf, wfmod.parse(out.source))
    assert tomllib.loads(out.source) == tomllib.loads(src)


def test_a_diagram_changed_in_another_tool_brings_back_its_title_summary_and_trigger():
    """What a BPMN tool may change: the process's name (the title), its documentation (the summary) and its start
    events (a timer start is the schedule, a message start the event). The states and edges stay the extension's."""
    was = tomllib.loads(RENEWAL)
    root, proc = _exported(RENEWAL)
    proc.set("name", "Renewal of a prescription")
    proc.find(B + "documentation").text = "A physician decides on each renewal."
    timer = ET.SubElement(ET.SubElement(proc, B + "startEvent", {"id": "nightly"}), B + "timerEventDefinition")
    ET.SubElement(timer, B + "timeCycle").text = "R/2026-01-05T07:00:00/P1D"
    ET.SubElement(root, B + "message", {"id": "asked", "name": "renewal.requested"})
    ET.SubElement(ET.SubElement(proc, B + "startEvent", {"id": "on-asked"}), B + "messageEventDefinition",
                  {"messageRef": "asked"})
    out = bpmn.import_bpmn(ET.tostring(root))
    doc = tomllib.loads(out.source)
    assert out.problems == []
    assert doc["title"] == "Renewal of a prescription" and doc["summary"] == "A physician decides on each renewal."
    assert doc["trigger"] == {"starters": ["physician"], "schedule": "daily 07:00", "event": "renewal.requested"}
    assert {k: doc[k] for k in WRITTEN} == {k: was[k] for k in WRITTEN}

    root, proc = _exported(SWEEP)   # a schedule changed in the tool, then one a workflow cannot keep, then none
    cycle = proc.find(f"{B}startEvent/{B}timerEventDefinition/{B}timeCycle")
    cycle.text = "R/PT1H"
    doc = tomllib.loads(bpmn.import_bpmn(ET.tostring(root)).source)
    assert doc["trigger"] == {**tomllib.loads(SWEEP)["trigger"], "schedule": "hourly"}
    cycle.text = "R/PT5M"
    assert any("is not a schedule a workflow can keep" in p for p in bpmn.import_bpmn(ET.tostring(root)).problems)
    proc.remove(proc.find(f"{B}startEvent[@id='Start_schedule']"))
    assert "trigger" not in tomllib.loads(bpmn.import_bpmn(ET.tostring(root)).source)  # nor for_each, with no schedule


def test_a_diagram_never_brings_back_its_steps():
    """The steps come from the states and edges. A task renamed, moved to another lane, given another condition or
    due time, or written in another place in the file changes nothing; tasks added or removed are reported, and the
    states and edges are still the extension's."""
    was = tomllib.loads(RENEWAL)
    said = "steps added or removed in the diagram are not imported"
    root, proc = _exported(RENEWAL)
    prepare = proc.find(f"{B}userTask[@id='prepare']")
    prepare.set("name", "Get the renewal ready")
    for f in proc.iter(B + "sequenceFlow"):
        if f.get("targetRef") == "tell-declined":
            f.find(B + "conditionExpression").text = "decide.outcome in ['decline']"
    lanes = {ln.get("name"): ln for ln in proc.iter(B + "lane")}
    ref = next(r for r in lanes["Clinical assistant"] if r.text == "prepare")
    lanes["Clinical assistant"].remove(ref)
    lanes["Physician"].append(ref)
    proc.find(f"{B}boundaryEvent[@attachedToRef='prepare']/{B}timerEventDefinition/{B}timeDuration").text = "PT8H"
    proc.remove(prepare)
    proc.append(prepare)   # a tool may write the tasks in another order: none was added or removed
    out = bpmn.import_bpmn(ET.tostring(root))
    assert not any(said in p for p in out.problems)
    assert tomllib.loads(out.source) == was
    step = wfmod.parse(out.source).step("prepare")
    assert (step.title, step.role, step.due) == ("Prepare the renewal", "clinical-assistant", "1d")
    assert wfmod.parse(out.source).step("tell-declined").when == "decide.outcome == 'decline'"

    ET.SubElement(proc, B + "userTask", {"id": "Activity_1", "name": "Call the patient"})  # drawn in the tool
    out = bpmn.import_bpmn(ET.tostring(root))
    assert any(said in p for p in out.problems)
    assert tomllib.loads(out.source) == was

    root, proc = _exported(RENEWAL)
    proc.remove(proc.find(f"{B}serviceTask[@id='prescribe']"))
    out = bpmn.import_bpmn(ET.tostring(root))
    assert any(said in p for p in out.problems)
    assert tomllib.loads(out.source) == was and wfmod.parse(out.source).step("prescribe").action == "record.update"


def test_what_a_tool_added_that_a_workflow_has_no_place_for_is_named():
    """A sub-process, a data object, a second pool, a message flow and a start on a signal drawn into a file we
    exported are named in words and left out, never guessed at; the workflow comes back as it was."""
    root, proc = _exported(RENEWAL)
    appeal = ET.SubElement(proc, B + "subProcess", {"id": "sp", "name": "Appeal"})
    ET.SubElement(appeal, B + "task", {"id": "inner", "name": "Inner"})
    ET.SubElement(proc, B + "dataObjectReference", {"id": "chart", "name": "The chart"})
    recall = ET.SubElement(proc, B + "startEvent", {"id": "recall", "name": "Recall"})
    ET.SubElement(recall, B + "signalEventDefinition")
    collab = root.find(B + "collaboration")
    ET.SubElement(collab, B + "participant", {"id": "p2", "name": "Pharmacy"})
    ET.SubElement(collab, B + "messageFlow", {"id": "mf1", "sourceRef": "prescribe", "targetRef": "p2"})
    out = bpmn.import_bpmn(ET.tostring(root))
    text = "\n".join(out.problems)
    for words in ("'Appeal' is a sub-process", "'The chart' is a data object", "'Recall' starts on a signal",
                  "several pools", "Message flows"):
        assert words in text, words
    assert tomllib.loads(out.source) == tomllib.loads(RENEWAL)


FOREIGN = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
    xmlns:camunda="http://camunda.org/schema/1.0/bpmn" id="d1" targetNamespace="http://example.com">
  <bpmn:collaboration id="c1">
    <bpmn:participant id="p1" name="Claims" processRef="claims"/>
    <bpmn:participant id="p2" name="Insurer"/>
    <bpmn:messageFlow id="mf1" sourceRef="send" targetRef="p2"/>
  </bpmn:collaboration>
  <bpmn:process id="claims" name="Claim handling">
    <bpmn:laneSet id="ls"><bpmn:lane id="l1" name="Billing clerk"><bpmn:flowNodeRef>review</bpmn:flowNodeRef>
      </bpmn:lane></bpmn:laneSet>
    <bpmn:startEvent id="s"/>
    <bpmn:userTask id="review" name="Review the claim" camunda:assignee="bob">
      <bpmn:documentation>Check the codes.</bpmn:documentation>
    </bpmn:userTask>
    <bpmn:boundaryEvent id="late" attachedToRef="review" cancelActivity="false">
      <bpmn:timerEventDefinition><bpmn:timeDuration>P3D</bpmn:timeDuration></bpmn:timerEventDefinition>
    </bpmn:boundaryEvent>
    <bpmn:boundaryEvent id="comp" attachedToRef="review">
      <bpmn:compensateEventDefinition/>
    </bpmn:boundaryEvent>
    <bpmn:exclusiveGateway id="g" default="f4"/>
    <bpmn:serviceTask id="send" name="Send the claim"/>
    <bpmn:subProcess id="sp" name="Appeal"><bpmn:task id="inner" name="Inner"/></bpmn:subProcess>
    <bpmn:scriptTask id="calc" name="Work out the fee"><bpmn:script>import os</bpmn:script></bpmn:scriptTask>
    <bpmn:endEvent id="e"/>
    <bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="review"/>
    <bpmn:sequenceFlow id="f2" sourceRef="review" targetRef="g"/>
    <bpmn:sequenceFlow id="f3" sourceRef="g" targetRef="send">
      <bpmn:conditionExpression>${review.ok == true}</bpmn:conditionExpression></bpmn:sequenceFlow>
    <bpmn:sequenceFlow id="f4" sourceRef="g" targetRef="calc"/>
    <bpmn:sequenceFlow id="f5" sourceRef="g" targetRef="sp"><bpmn:conditionExpression>amount &gt; 100</bpmn:conditionExpression></bpmn:sequenceFlow>
    <bpmn:sequenceFlow id="f6" sourceRef="send" targetRef="e"/>
  </bpmn:process>
</bpmn:definitions>"""


def test_a_file_from_another_tool_is_refused_with_its_tasks_by_lane():
    """A workflow is its record's states and the edges between them, which a diagram from another tool does not
    carry, so its tasks and flows are not mapped to anything. The refusal says how a workflow is written and names
    the tasks the file holds, by lane, for whoever writes it by hand."""
    with pytest.raises(bpmn.BpmnError) as err:
        bpmn.import_bpmn(FOREIGN)
    said = err.value.problems
    assert "written as its record's states and the edges between them" in said[0]
    assert "a diagram drawn in another tool cannot be imported as it is" in said[0]
    assert "The file holds 4 tasks" in said[1]
    assert "Billing clerk: Review the claim" in said
    assert "In no lane: Send the claim, Inner, Work out the fee" in said  # in the file's order, a sub-process's too
    assert '"States and edges" in docs/workflows.md' in said[-1]
    assert str(err.value) == "\n".join(said)
    with pytest.raises(bpmn.BpmnError, match="no process"):  # a file with nothing to read is still told so first
        bpmn.import_bpmn(f'<bpmn:definitions xmlns:bpmn="{bpmn.BPMN}"/>')


def test_a_file_we_exported_that_lost_its_states_and_edges_is_refused_too():
    """A tool that drops what it does not know, or a file whose extension holds no states and edges, leaves only a
    diagram: it is refused like any other, never read back as steps."""
    root, proc = _exported(RENEWAL)
    ext = proc.find(f"{B}extensionElements/{{{bpmn.PH}}}workflow")
    ext.text = json.dumps({k: v for k, v in json.loads(ext.text).items() if k not in WRITTEN})
    with pytest.raises(bpmn.BpmnError, match="states and the edges between them") as err:
        bpmn.import_bpmn(ET.tostring(root))
    assert "Clinical assistant: Prepare the renewal" in err.value.problems
    proc.remove(proc.find(B + "extensionElements"))
    with pytest.raises(bpmn.BpmnError, match="states and the edges between them"):
        bpmn.import_bpmn(ET.tostring(root))


def _foreign(inside: str) -> str:
    return f'<bpmn:definitions xmlns:bpmn="{bpmn.BPMN}"><bpmn:process id="p">{inside}</bpmn:process></bpmn:definitions>'


def test_the_refusal_names_only_so_many_tasks_each_on_one_plain_line():
    many = bpmn.LISTED + 440
    with pytest.raises(bpmn.BpmnError) as err:
        bpmn.import_bpmn(_foreign("".join(f'<bpmn:task id="t{i}" name="Task {i}"/>' for i in range(many))))
    said = err.value.problems
    assert f"The file holds {many} tasks" in said[1] and "And 440 more, not listed here." in said
    assert sum(p.count("Task ") for p in said) == bpmn.LISTED
    with pytest.raises(bpmn.BpmnError) as err:  # a name with line breaks, a direction override and no end
        bpmn.import_bpmn(_foreign('<bpmn:task id="t" name="Call&#10;the  patient&#x202E; now"/>'
                                  f'<bpmn:task id="u" name="{"very " * 100}long"/><bpmn:task id="v"/>'))
    names = err.value.problems[2]
    assert names.startswith("In no lane: Call the patient now, very very ") and names.endswith("..., v")
    assert names.isprintable() and len(names) < 200


def test_a_deeply_nested_file_from_another_tool_is_refused_like_any_other():
    """Its tasks are found without recursion, so nesting as deep as the element cap allows cannot exhaust the stack."""
    deep = 5000
    inside = "<bpmn:subProcess>" * deep + '<bpmn:task name="At the bottom"/>' + "</bpmn:subProcess>" * deep
    with pytest.raises(bpmn.BpmnError) as err:
        bpmn.import_bpmn(_foreign(inside))
    assert "In no lane: At the bottom" in err.value.problems


BILLION_LAUGHS = """<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"><bpmn:process id="p" name="&lol3;"/>
</bpmn:definitions>"""
EXTERNAL = """<?xml version="1.0"?>
<!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"><bpmn:process id="p" name="&x;"/>
</bpmn:definitions>"""


@pytest.mark.parametrize("data", [BILLION_LAUGHS, EXTERNAL, BILLION_LAUGHS.encode("utf-16")],
                         ids=["entities", "external", "utf16"])
def test_entities_and_doctypes_are_refused(data):
    with pytest.raises(bpmn.BpmnError, match="document type or entities"):
        bpmn.import_bpmn(data)


def test_huge_and_odd_input_is_refused():
    huge = b'<?xml version="1.0"?><x>' + b" " * (bpmn.MAX_BYTES + 1) + b"</x>"
    with pytest.raises(bpmn.BpmnError, match="too large"):
        bpmn.import_bpmn(huge)
    many = "<a>" + "<b/>" * (bpmn.MAX_ELEMENTS + 1) + "</a>"
    with pytest.raises(bpmn.BpmnError, match="elements"):
        bpmn.import_bpmn(many)
    with pytest.raises(bpmn.BpmnError, match="not BPMN"):
        bpmn.import_bpmn("<html><body/></html>")
    with pytest.raises(bpmn.BpmnError, match="not readable XML"):
        bpmn.import_bpmn("<bpmn:definitions")


def _upload(data: bytes, name="referral.bpmn") -> dict:
    b = "----phtest"
    body = (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            f'Content-Type: application/xml\r\n\r\n').encode() + data + f"\r\n--{b}--\r\n".encode()
    return {"headers": {"content-type": f"multipart/form-data; boundary={b}"}, "body": body, "query": {},
            "path": "/workflows/import-bpmn", "method": "POST"}


def test_download_and_import_only_propose(hub):
    """The download is the workflow in force; importing it back opens the editor and changes nothing; proposing it
    needs a different policy owner to approve, as for any edit."""
    app = App(hub, dev=False)
    rana = M(hub, "rana")
    before = hub.workflow("referral")[1]["version"]
    dl = app.page_workflow_bpmn({"me": rana, "query": {}, "path": "/workflows/referral/bpmn"}, "referral")
    assert isinstance(dl, Download) and "attachment" in dl[0]["Content-Disposition"]
    root = ET.fromstring(dl[1])
    root.find(f"{B}process").set("name", "Referral to a specialist")
    data = ET.tostring(root)
    req = {**_upload(data), "me": rana}
    page = app.post_import_bpmn(req)
    assert "Imported from BPMN" in page and 'id="wf-editor"' in page and "Referral to a specialist" in page
    assert hub.workflow("referral")[1]["version"] == before  # nothing installed
    assert not [v for v in hub.versions("referral") if v["status"] == "proposed"]
    source = bpmn.import_bpmn(data, hub.pack).source
    out = hub.propose_workflow(rana, source, "imported from BPMN")
    with pytest.raises(Denied):
        hub.approve_workflow(rana, "referral", out["version"])  # the proposer never approves
    assert hub.workflow("referral")[1]["version"] == before
    with pytest.raises(Invalid):
        app.post_import_bpmn({**_upload(BILLION_LAUGHS.encode()), "me": rana})
    req = {**_upload(FOREIGN.encode(), "claims.bpmn"), "me": rana}
    with pytest.raises(Invalid) as err:  # a file from another tool: refused, and the page lists the tasks it holds
        app.post_import_bpmn(req)
    page = app.error_page(req, err.value, err.value.problems)
    assert "The file could not be imported." in page and "<li>Billing clerk: Review the claim</li>" in page


def test_the_command_line_prints_toml_or_proposes(hub, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "build_hub", lambda args, pack=None: hub)
    cli.main(["workflow", "export-bpmn", "referral"])
    xml = capsys.readouterr().out
    f = tmp_path / "referral.bpmn"
    f.write_text(xml.replace('name="Referral"', 'name="Referral to a specialist"'))
    before = hub.workflow("referral")[1]["version"]
    cli.main(["workflow", "import-bpmn", str(f)])
    toml_out = capsys.readouterr().out
    assert wfmod.parse(toml_out).title == "Referral to a specialist"
    assert hub.workflow("referral")[1]["version"] == before
    cli.main(["workflow", "import-bpmn", str(f), "--propose", "--as", "rana"])
    assert "proposed referral" in capsys.readouterr().out
    assert hub.workflow("referral")[1]["version"] == before  # proposed, not in force
    assert [v for v in hub.versions("referral") if v["status"] == "proposed"]
    g = tmp_path / "claims.bpmn"
    g.write_text(FOREIGN)
    with pytest.raises(SystemExit, match="error: A workflow here is written as its record's states and the edges"):
        cli.main(["workflow", "import-bpmn", str(g)])  # a file from another tool: refused, nothing printed as TOML
    assert capsys.readouterr().out == ""
