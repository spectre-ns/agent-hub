"""BPMN 2.0 interchange: every shipped workflow survives export and import, a foreign file's unsupported parts are
named rather than guessed at, hostile XML is refused, and an import only ever becomes a proposal."""

import dataclasses
import glob
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


# Written as steps (a workflow written as states and edges takes its steps from them, so a diagram's changes to its
# steps are not imported: bpmn.import_bpmn says so)
STEPS_FORM = """id = "renewal"
title = "Renewal"
record = "MedicationRequest"
[trigger]
starters = ["physician"]
[[steps]]
id = "prepare"
title = "Prepare the renewal"
kind = "task"
role = "clinical-assistant"
due = "1d"
skill = "renewal-prep"
[[steps]]
id = "decide"
title = "Decide on the renewal"
kind = "gate"
role = "physician"
after = ["prepare"]
signer = "person"
outcomes = ["renew", "book-visit", "decline"]
meaning = "I authorize this renewal"
[[steps]]
id = "not-on-chart"
title = "Write a new prescription"
kind = "task"
role = "physician"
after = ["decide"]
when = "decide.outcome == 'renew'"
[[steps.fields]]
name = "notes"
type = "text"
[[steps]]
id = "prescribe"
title = "Write the prescription"
kind = "automation"
after = ["not-on-chart"]
action = "record.update"
[steps.params.set]
intent = "order"
[[steps]]
id = "tell-declined"
title = "Tell the patient"
kind = "automation"
after = ["decide"]
when = "decide.outcome == 'decline'"
action = "message.send"
[steps.params]
channel = "patient-portal"
to = "{{run.subject}}"
subject = "Your renewal request"
body = "Not renewed."
"""


def test_what_the_diagram_says_wins_over_the_extension():
    """A task renamed, a condition changed and a step moved to another lane in a BPMN tool come back changed; what
    BPMN has no place for (params, outcomes) comes back from the extension."""
    wf = wfmod.parse(STEPS_FORM, stored=True)  # a version kept from before the states form
    root = ET.fromstring(bpmn.export_bpmn(wf))
    proc = root.find(B + "process")
    proc.find(f"{B}userTask[@id='prepare']").set("name", "Get the renewal ready")
    for f in proc.iter(B + "sequenceFlow"):
        if f.get("targetRef") == "tell-declined":
            f.find(B + "conditionExpression").text = "decide.outcome in ['decline']"
    lanes = {ln.get("name"): ln for ln in proc.iter(B + "lane")}
    ref = next(r for r in lanes["Physician"] if r.text == "not-on-chart")
    lanes["Physician"].remove(ref)
    lanes["Clinical assistant"].append(ref)
    due = proc.find(f"{B}boundaryEvent[@attachedToRef='prepare']")
    due.find(f"{B}timerEventDefinition/{B}timeDuration").text = "PT8H"
    out = bpmn.import_bpmn(ET.tostring(root))
    wf2 = wfmod.parse(out.source, stored=True)
    assert wf2.step("prepare").title == "Get the renewal ready" and wf2.step("prepare").due == "8h"
    assert wf2.step("tell-declined").when == "decide.outcome in ['decline']"
    assert wf2.step("not-on-chart").role == "clinical-assistant"
    assert wf2.step("decide").outcomes == ["renew", "book-visit", "decline"]
    assert wf2.step("prescribe").params == wf.step("prescribe").params


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


def test_a_foreign_file_maps_what_it_can_and_names_the_rest():
    out = bpmn.import_bpmn(FOREIGN)
    text = "\n".join(out.problems)
    for words in ("Message flows", "sub-process", "compensation", "default path", "script", "another BPMN tool",
                  "several pools"):
        assert words in text, words
    doc = tomllib.loads(out.source)  # always TOML the editor can open, even when it still needs work
    steps = {s["id"]: s for s in doc["steps"]}
    assert doc["title"] == "Claim handling"
    assert steps["review-the-claim"]["role"] == "billing-clerk" and steps["review-the-claim"]["due"] == "3d"
    assert steps["review-the-claim"]["instructions"] == "Check the codes."
    assert steps["send-the-claim"] == {"id": "send-the-claim", "title": "Send the claim", "kind": "automation",
                                       "after": ["review-the-claim"], "when": "review.ok == true"}
    assert "inner" not in steps and "appeal" not in steps  # never guessed at
    assert any(p.startswith("Still to fix:") for p in out.problems)  # what parse still wants, in its words


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
