"""Each clinic workflow walks its own record from open to closed: the record type names the workflow, creating the
record starts it, the record's lifecycle state is where the work is, and the patient is the record's subject, never
the workflow's record. The visit (Encounter) is in test_record_workflow.py; these are the rest of the
general-practice pack's: a renewal request, a lab result, a patient's question, a referral, a registration and a
privacy directive."""
import pytest

from practicehub.hub import Denied
from tests.conftest import M, item_for, quiet, run_all_local, run_of


def _pat(hub, family="MacLeod"):
    pat = next(p for p in hub.records.search("Patient", limit=None) if p["name"][0]["family"] == family)
    return f"Patient/{pat['id']}"


def _read(hub, ref):
    return hub.records.read(*ref.split("/", 1))


def _items(hub, run):
    return list(hub.db.items.find({"run_id": run["id"], "kind": {"$ne": "notice"}}))


# ------------------------------------------------------------------------------------------- prescription renewal
def test_a_renewal_request_is_a_proposal_that_becomes_the_order_it_renews(hub, worked):
    """Asked for on the patient, the run makes the request (a draft proposal on the chart) and walks it: renewed, the
    same record is the active order, and the order it replaces is completed. One run, one item."""
    pat = _pat(hub)
    run = run_of(hub, "prescription-renewal")  # the demo's: the pharmacy asked through the portal
    ref = run["record"]
    assert ref.startswith("MedicationRequest/") and run["subject"] == pat and run["ctx"]["run"]["subject"] == pat
    req = _read(hub, ref)
    assert req["status"] == "draft" and req["intent"] == "proposal" and req["subject"]["reference"] == pat
    assert req["medicationCodeableConcept"]["text"] == "Ramipril 5 mg"
    hub.engine.decide(M(hub, "dr-amara"), item_for(hub, run, "decide"), "renew", "")
    run_all_local(hub, log=quiet)
    req = _read(hub, ref)
    assert req["status"] == "active" and req["intent"] == "order" and req["authoredOn"]
    assert req["dispenseRequest"]["quantity"]["value"]
    prior = _read(hub, req["priorPrescription"]["reference"])
    assert prior["status"] == "completed"
    assert hub.engine.run(run["id"])["status"] == "completed"
    assert len([i for i in _items(hub, run) if i["parent_id"] is None]) == 1
    fax = hub.db.outbox.find_one({"channel": "fax"}, sort="id")["body"]
    assert ref in fax and "Ellen MacLeod" in fax


def test_nobody_issues_a_renewal_request_by_hand_but_a_hand_written_prescription_is_theirs(hub):
    from practicehub.hub import Invalid
    amara = M(hub, "dr-amara")
    run = run_of(hub, "prescription-renewal")
    with pytest.raises((Denied, Invalid)):
        hub.update_record(amara, run["record"], {"status": "active"})
    assert _read(hub, run["record"])["status"] == "draft"
    # an order the physician writes (not a proposal) starts no renewal, and they issue it themselves
    rx = hub.create_record(amara, "MedicationRequest", {
        "status": "draft", "intent": "order", "subject": {"reference": _pat(hub)},
        "medicationCodeableConcept": {"text": "Amoxicillin 500 mg"}})
    ref = f"MedicationRequest/{rx['id']}"
    assert not hub.engine.runs(record=ref)
    hub.update_record(amara, ref, {"status": "active"})
    assert _read(hub, ref)["status"] == "active"


@pytest.mark.parametrize("outcome", ["decline", "book-visit"])
def test_a_renewal_not_given_closes_its_request(hub, worked, outcome):
    pat = _pat(hub)
    run = run_of(hub, "prescription-renewal")
    visits = len([v for v in hub.records.search("Encounter", limit=None) if v["status"] == "requested"])
    hub.engine.decide(M(hub, "dr-amara"), item_for(hub, run, "decide"), outcome, "see me first")
    run_all_local(hub, log=quiet)
    assert _read(hub, run["record"])["status"] == "cancelled"
    assert hub.engine.run(run["id"])["status"] == "completed"
    asked = [v for v in hub.records.search("Encounter", limit=None) if v["status"] == "requested"]
    if outcome == "book-visit":  # the visit asked for is about the patient, not the prescription
        assert len(asked) == visits + 1 and any(v["subject"]["reference"] == pat for v in asked)
    else:
        assert len(asked) == visits


def test_a_new_renewal_request_made_by_hand_starts_its_workflow(hub):
    noor = M(hub, "noor")
    rx = hub.create_record(noor, "MedicationRequest", {
        "status": "draft", "intent": "proposal", "subject": {"reference": _pat(hub)},
        "medicationCodeableConcept": {"text": "Metformin 500 mg"}})
    ref = f"MedicationRequest/{rx['id']}"
    (run,) = hub.engine.runs(record=ref, status="running")
    assert run["workflow_id"] == "prescription-renewal" and run["ctx"]["input"]["medication"] == "Metformin 500 mg"


OLD_RENEWAL = """id = "prescription-renewal"
title = "Prescription renewal"
record = "Patient"
[trigger]
starters = ["front-desk", "nurse", "physician"]
[[input]]
name = "medication"
type = "string"
required = true
[[steps]]
id = "decide"
title = "Decide on the renewal"
kind = "gate"
role = "physician"
signer = "person"
outcomes = ["renew", "decline"]
meaning = "I authorize this renewal"
[[steps]]
id = "prescribe"
title = "Write the prescription"
kind = "automation"
after = ["decide"]
when = "decide.outcome == 'renew'"
action = "record.create"
[steps.params]
type = "MedicationRequest"
[steps.params.data]
status = "active"
intent = "order"
subject = { reference = "{{run.record}}" }
medicationCodeableConcept = { text = "{{input.medication}}" }
"""


def test_a_renewal_from_before_the_request_had_its_own_workflow_moves_onto_a_request_and_closes_it(hub):
    """A run about the patient moves onto a request of its own (keeping its version, item and steps); its older
    steps write the order as they did, and when it ends the request it walked is closed, not handed on again."""
    jo, rana, amara = M(hub, "jo"), M(hub, "rana"), M(hub, "dr-amara")
    pat = _pat(hub)
    cur = hub.workflow("prescription-renewal")[1]["source"]
    old = hub.propose_workflow(jo, OLD_RENEWAL, "the shape before")
    hub.approve_workflow(rana, "prescription-renewal", old["version"])
    run = hub.engine.start(jo, "prescription-renewal", record=pat, inputs={"medication": "Atorvastatin 20 mg"})
    before = hub.engine.run(run["id"])
    assert before["record"] == pat
    new = hub.propose_workflow(jo, cur, "its own request")
    hub.approve_workflow(rana, "prescription-renewal", new["version"])
    assert run["id"] in hub.engine.relink_runs()["moved"]
    after = hub.engine.run(run["id"])
    ref = after["record"]
    assert ref.startswith("MedicationRequest/") and after["subject"] == pat and after["item"] == before["item"]
    assert after["version"] == before["version"] and after["ctx"]["run"]["record"] == pat
    assert _read(hub, ref)["status"] == "draft"
    assert hub.engine.relink_runs() == {"moved": [], "failed": []}
    hub.engine.decide(amara, item_for(hub, after, "decide"), "renew", "")
    run_all_local(hub, log=quiet)
    assert hub.engine.run(run["id"])["status"] == "completed"
    assert _read(hub, ref)["status"] == "cancelled"  # its older version wrote the order separately
    assert not hub.engine.runs(record=ref, status="running")  # and nothing starts on it again
    orders = [o for o in hub.records.search("MedicationRequest", limit=None)
              if o["medicationCodeableConcept"].get("text") == "Atorvastatin 20 mg" and o["status"] == "active"]
    assert len(orders) == 1
