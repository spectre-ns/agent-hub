"""Each clinic workflow walks its own record from open to closed: the record type names the workflow, creating the
record starts it, the record's lifecycle state is where the work is, and the patient is the record's subject, never
the workflow's record. The visit (Encounter) is in test_record_workflow.py; these are the rest of the
general-practice pack's: a renewal request, a lab result, a patient's question, a referral, a registration and a
privacy directive."""
import pytest

from practicehub.hub import Denied, Invalid
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


def test_nobody_issues_a_renewal_request_by_hand_and_a_hand_written_one_only_by_a_physicians_signature(hub):
    """A renewal request is issued only by its workflow. An order a physician writes by hand starts no workflow and
    is issued by their signature, "I prescribe this medication as written", with its directions and quantity: never
    by setting its status, and never by a nurse's signature."""
    amara, noor = M(hub, "dr-amara"), M(hub, "noor")
    run = run_of(hub, "prescription-renewal")
    with pytest.raises((Denied, Invalid)):
        hub.update_record(amara, run["record"], {"status": "active"})
    assert _read(hub, run["record"])["status"] == "draft"
    rx = hub.create_record(amara, "MedicationRequest", {
        "status": "draft", "intent": "order", "subject": {"reference": _pat(hub)},
        "medicationCodeableConcept": {"text": "Amoxicillin 500 mg capsule"}})
    ref = f"MedicationRequest/{rx['id']}"
    assert not hub.engine.runs(record=ref)
    with pytest.raises((Denied, Invalid)):
        hub.update_record(amara, ref, {"status": "active"})
    meaning = "I prescribe this medication as written"
    with pytest.raises((Denied, Invalid)):  # no directions or quantity yet: nothing to sign for
        hub.sign_record(amara, ref, meaning)
    hub.update_record(amara, ref, {"dosageInstruction": [{"text": "1 capsule three times daily for 7 days"}],
                                   "dispenseRequest": {"quantity": {"value": 21, "unit": "capsule"}}})
    with pytest.raises(Denied):
        hub.sign_record(noor, ref, meaning)
    hub.sign_record(amara, ref, meaning)
    assert _read(hub, ref)["status"] == "active"
    sig = hub.signatures(amara, ref)
    assert any(x["meaning"] == meaning and x["by"] == amara["id"] for x in sig)


def test_a_renewal_not_on_the_chart_is_written_signed_as_written_sent_and_the_patient_told(hub):
    """No current order on the chart: the physician chooses to write one, writes it (medication, directions,
    quantity, repeats, days supply), signs it as written; the request becomes that order, it goes to the pharmacy
    and the patient is told. Not written, the patient is told too."""
    amara = M(hub, "dr-amara")
    pat = _pat(hub)
    run = hub.engine.start(M(hub, "jo"), "prescription-renewal", record=pat,
                           inputs={"medication": "Levothyroxine 50 mcg", "pharmacy": "Spring Garden Pharmacy"})
    run_all_local(hub, log=quiet)
    hub.engine.decide(amara, item_for(hub, run, "decide"), "renew", "")
    run_all_local(hub, log=quiet)
    hub.engine.complete(amara, item_for(hub, run, "not-on-chart"), {"decision": "written", "notes": "new start"})
    hub.engine.complete(amara, item_for(hub, run, "write-rx"), {
        "medication": "Levothyroxine 50 mcg tablet", "directions": "1 tablet by mouth every morning",
        "quantity": 90, "unit": "tablet", "repeats": 3, "days_supply": 90})
    hub.engine.decide(amara, item_for(hub, run, "sign-new"), "prescribe", "")
    run_all_local(hub, log=quiet)
    ref = hub.engine.run(run["id"])["record"]
    rx = _read(hub, ref)
    assert rx["status"] == "active" and rx["intent"] == "order"
    assert rx["dosageInstruction"][0]["text"] == "1 tablet by mouth every morning"
    assert rx["medicationCodeableConcept"]["text"] == "Levothyroxine 50 mcg tablet"
    assert hub.engine.run(run["id"])["status"] == "completed"
    sent = list(hub.db.outbox.find({"run_id": run["id"]}))
    assert any(m["channel"] == "fax" and "Levothyroxine 50 mcg tablet" in m["body"] for m in sent), sent
    assert any(m["channel"] == "patient-portal" and "sent to your pharmacy" in m["body"] for m in sent), sent


def test_a_renewal_not_on_the_chart_not_written_closes_and_tells_the_patient(hub):
    amara = M(hub, "dr-amara")
    run = hub.engine.start(M(hub, "jo"), "prescription-renewal", record=_pat(hub),
                           inputs={"medication": "Levothyroxine 50 mcg", "pharmacy": "Spring Garden Pharmacy"})
    run_all_local(hub, log=quiet)
    hub.engine.decide(amara, item_for(hub, run, "decide"), "renew", "")
    run_all_local(hub, log=quiet)
    hub.engine.complete(amara, item_for(hub, run, "not-on-chart"), {"decision": "not-written", "notes": "see me"})
    run_all_local(hub, log=quiet)
    assert _read(hub, run["record"])["status"] == "cancelled"
    assert hub.engine.run(run["id"])["status"] == "completed"
    sent = list(hub.db.outbox.find({"run_id": run["id"], "channel": "patient-portal"}))
    assert any("has not written a prescription" in m["body"] for m in sent), sent


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


def test_a_second_renewal_for_the_same_drug_is_flagged_to_the_physician(hub, worked):
    """The pharmacy asked for Ramipril; the patient asks again on the portal later (not the same submission, so its
    own request). Its preparation names the other open request, and an answer that leaves it out is refused."""
    first = run_of(hub, "prescription-renewal")
    hub.db.runs.set(first["id"], {"created_at": "2000-01-01T00:00:00"})  # asked a while ago
    second =hub.engine.start(M(hub, "jo"), "prescription-renewal", record=_pat(hub),
                              inputs={"medication": "Ramipril 5 mg", "pharmacy": "Quinpool Pharmacy"})
    assert second["record"] != first["record"]
    run_all_local(hub, log=quiet)
    prep = hub.engine.run(second["id"])["ctx"]["prepare"]
    assert any(first["record"] in c and "open" in c for c in prep["concerns"]), prep["concerns"]
    guard = hub.pack.guards["renewal-prep"]
    ctxview = hub.engine.context(M(hub, "dr-amara"), item_for(hub, second, "decide"))
    ctxview["context"]["record:clinical"] = [hub.records.read(*first["record"].split("/", 1)) | {
        "resourceType": "MedicationRequest", "id": first["record"].split("/", 1)[1]}]
    ctxview["context"]["input.medication"] = "Ramipril 5 mg"
    problems = guard({**prep, "concerns": []}, ctxview)
    assert problems and first["record"] in problems[0]
    assert not guard({**prep, "found_in_chart": False, "current_order": ""}, ctxview)


def test_a_renewal_request_marked_entered_in_error_stops_its_run(hub, worked):
    """The request was filed on the wrong patient: marked entered in error, its run stops (the physician's decision
    goes with it) and the track says who stopped it; nothing renews it afterwards."""
    run = run_of(hub, "prescription-renewal")
    iid = item_for(hub, run, "decide")
    hub.update_record(M(hub, "noor"), run["record"], {"status": "entered-in-error"})
    assert hub.engine.run(run["id"])["status"] == "cancelled"
    assert hub.db.items.get(iid)["status"] == "cancelled"
    assert hub.db.events.find_one({"action": "run.stopped", "run_id": run["id"]})
    with pytest.raises((Denied, Invalid)):
        hub.engine.decide(M(hub, "dr-amara"), iid, "renew", "")
    assert _read(hub, run["record"])["status"] == "entered-in-error"


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
    assert hub.engine.relink_runs() == {"moved": [], "failed": [], "skipped": []}
    hub.engine.decide(amara, item_for(hub, after, "decide"), "renew", "")
    run_all_local(hub, log=quiet)
    assert hub.engine.run(run["id"])["status"] == "completed"
    assert _read(hub, ref)["status"] == "cancelled"  # its older version wrote the order separately
    assert not hub.engine.runs(record=ref, status="running")  # and nothing starts on it again
    orders = [o for o in hub.records.search("MedicationRequest", limit=None)
              if o["medicationCodeableConcept"].get("text") == "Atorvastatin 20 mg" and o["status"] == "active"]
    assert len(orders) == 1


# ------------------------------------------------------------------------------------------------ lab result review
LAB = {"test": "Sodium", "value": "139", "unit": "mmol/L", "range": "135-145", "flag": "normal"}


def _lab_obs(hub, pat, **extra):
    rec = hub.create_record(M(hub, "lab-feed"), "Observation", {
        "status": "preliminary", "category": [{"text": "laboratory"}], "subject": {"reference": pat},
        "code": {"text": "Sodium"}, "valueQuantity": {"value": 131, "unit": "mmol/L"},
        "referenceRange": [{"text": "135-145"}], "interpretation": [{"text": "abnormal"}], **extra})
    return f"Observation/{rec['id']}"


def test_a_result_sent_for_the_patient_is_filed_and_walked_from_received_to_closed(hub):
    """The lab sends a result for the patient: the run files it (waiting for review) and walks it: reviewed, the
    patient told, closed, with one item. The review is the result's own lifecycle; the lab's status is the lab's."""
    pat = _pat(hub)
    run = hub.engine.run(hub.engine.start(M(hub, "lab-feed"), "lab-result-review", record=pat, inputs=LAB)["id"])
    ref = run["record"]
    assert ref.startswith("Observation/") and run["subject"] == pat
    ob = _read(hub, ref)
    assert ob["review"] == "received" and ob["subject"]["reference"] == pat
    assert ob["code"]["text"] == "Sodium" and ob["valueQuantity"]["value"] == 139
    hub.engine.complete(M(hub, "clinical-admin-agent"), item_for(hub, run, "sort"),
                        {"category": "normal", "rationale": "in range"})
    hub.engine.decide(M(hub, "dr-amara"), item_for(hub, run, "review"), "file-normal", "")
    run_all_local(hub, log=quiet)
    ob = _read(hub, ref)
    assert ob["review"] == "closed" and ob["status"] == "final"
    assert hub.engine.run(run["id"])["status"] == "completed"
    assert len([i for i in _items(hub, run) if i["parent_id"] is None]) == 1


def test_a_result_the_lab_files_starts_its_review_and_the_lab_still_corrects_it(hub):
    """Filing a lab result starts its review on that result; the same result sent again walks the same run; the lab
    may still move its own status while a physician reviews it; a vital sign starts no review."""
    pat = _pat(hub)
    ref = _lab_obs(hub, pat)
    (run,) = hub.engine.runs(record=ref, status="running")
    assert run["workflow_id"] == "lab-result-review" and run["subject"] == pat
    same = {**LAB, "value": "131", "flag": "abnormal", "observation": ref}  # the result as filed, sent again
    hub.engine.emit(M(hub, "lab-feed"), "lab.result", record=pat, data=same)
    assert len(hub.engine.runs(record=ref)) == 1
    hub.update_record(M(hub, "lab-feed"), ref, {"status": "final"})  # the lab's final report, mid-review
    assert _read(hub, ref)["review"] == "received"
    with pytest.raises(Denied):
        hub.update_record(M(hub, "dr-amara"), ref, {"review": "closed"})  # its review closes it, not a hand move
    vital = hub.create_record(M(hub, "noor"), "Observation", {
        "status": "final", "category": [{"text": "vital-signs"}], "subject": {"reference": pat},
        "code": {"text": "Blood pressure"}, "valueString": "128/82"})
    assert not hub.engine.runs(record=f"Observation/{vital['id']}")


def test_a_result_named_for_another_patient_is_refused(hub):
    from practicehub.hub import Invalid
    other = next(p for p in hub.records.search("Patient", limit=None) if p["name"][0]["family"] != "MacLeod")
    ref = _lab_obs(hub, f"Patient/{other['id']}")
    with pytest.raises(Invalid) as refused:
        hub.engine.start(M(hub, "lab-feed"), "lab-result-review", record=_pat(hub), inputs={**LAB, "observation": ref})
    assert any("someone else" in p for p in refused.value.problems)


def _flagged_review(hub, outcome):
    """An abnormal sodium the physician has reviewed with ``outcome``: (run, result ref)."""
    run = hub.engine.start(M(hub, "lab-feed"), "lab-result-review", record=_pat(hub),
                           inputs={**LAB, "value": "131", "flag": "abnormal"})
    hub.engine.complete(M(hub, "clinical-admin-agent"), item_for(hub, run, "sort"),
                        {"category": "abnormal", "rationale": "low"})
    hub.engine.decide(M(hub, "dr-amara"), item_for(hub, run, "review-flagged"), outcome, "")
    run_all_local(hub, log=quiet)
    return run, hub.engine.run(run["id"])["record"]


@pytest.mark.parametrize("action,want", [("reached-the-patient", "closed"), ("letter-sent", "not-told"),
                                         ("booked-a-visit", "not-told"), ("no-further-action", "not-told")])
def test_a_result_is_closed_only_when_the_patient_was_told(hub, action, want):
    """The nurse could not reach the patient: only the physician reaching them closes the result. Any other way the
    review ends leaves it "Patient not told"."""
    run, ref = _flagged_review(hub, "call-patient")
    hub.engine.complete(M(hub, "noor"), item_for(hub, run, "call"), {"reached": False, "notes": "no answer"})
    hub.engine.complete(M(hub, "dr-amara"), item_for(hub, run, "not-reached"), {"action": action, "notes": "done"})
    run_all_local(hub, log=quiet)
    assert _read(hub, ref)["review"] == want
    assert hub.engine.run(run["id"])["status"] == "completed"


def test_a_result_with_a_visit_booked_is_not_told_until_a_physician_signs_it_closed(hub):
    """A visit booked to talk a result over has not told the patient: the result is "Patient not told" until a
    physician signs that they were told. Nobody else closes it, by hand or by signature."""
    run, ref = _flagged_review(hub, "book-visit")
    assert _read(hub, ref)["review"] == "not-told"
    assert hub.record_type("Observation").state_label("not-told") == "Patient not told"
    with pytest.raises(Denied):
        hub.update_record(M(hub, "dr-amara"), ref, {"review": "closed"})  # not a hand move
    with pytest.raises(Denied):
        hub.sign_record(M(hub, "noor"), ref, "Patient told")  # a nurse does not close it
    hub.sign_record(M(hub, "dr-amara"), ref, "Patient told", "Told at the visit")
    assert _read(hub, ref)["review"] == "closed"


def test_a_result_in_review_is_its_own_work_and_a_visit_booked_from_it_names_it(hub):
    """A result's review is the result's work: it reads "In review", its work is named for the result (the test and
    its value), not for the workflow. The physician asking to see the patient asks for a visit that names the result
    it was asked for, and the result's page lists it."""
    run = hub.engine.start(M(hub, "lab-feed"), "lab-result-review", record=_pat(hub),
                           inputs={**LAB, "value": "131", "flag": "abnormal"})
    ref = hub.engine.run(run["id"])["record"]
    rt = hub.record_type("Observation")
    assert _read(hub, ref)["review"] == "received" and rt.state_label("received") == "In review"
    title = hub.run_title(M(hub, "dr-amara"), hub.engine.run(run["id"]))
    assert title.startswith(f"Lab result {LAB['test']} 131") and "MacLeod" in title and "review" not in title
    assert hub.run_title(M(hub, "scheduling-agent"), hub.engine.run(run["id"])).startswith("Lab result · ")  # no value
    run, ref = _flagged_review(hub, "book-visit")
    (visit,) = [v for v in hub.records.search("Encounter", limit=None)
                if (v.get("reasonReference") or [{}])[0].get("reference") == ref]
    assert visit["status"] in ("requested", "planned") and visit["reasonCode"][0]["text"].startswith("Follow-up on")
    assert any(x["ref"] == f"Encounter/{visit['id']}" and x["rel"] == "reason"
               for x in hub.linked(M(hub, "dr-amara"), ref))


def test_a_review_cancelled_after_the_physician_reviewed_leaves_the_patient_not_told(hub):
    """Cancelled before anyone told the patient, a reviewed result is "Patient not told", never closed."""
    run, ref = _flagged_review(hub, "call-patient")
    assert _read(hub, ref)["review"] == "reviewed"
    hub.engine.cancel(M(hub, "rana"), run["id"], "the patient moved away")
    assert _read(hub, ref)["review"] == "not-told"


@pytest.mark.parametrize("outcome,before", [("no-action-needed", "closed"), ("book-visit", "not-told")])
def test_a_reviewed_result_the_lab_corrects_is_reviewed_again(hub, outcome, before):
    """The lab corrects a result already reviewed (closed, or the patient not told) and sends it again naming it: a
    new review walks it from waiting for review, and the physician reviews the corrected value."""
    run, ref = _flagged_review(hub, outcome)
    assert _read(hub, ref)["review"] == before and hub.engine.run(run["id"])["status"] == "completed"
    hub.update_record(M(hub, "lab-feed"), ref, {"status": "corrected", "valueQuantity": {"value": 128, "unit": "mmol/L"}})
    pat = _read(hub, ref)["subject"]["reference"]
    (again,) = hub.engine.emit(M(hub, "lab-feed"), "lab.result", record=pat,
                               data={**LAB, "value": "128", "flag": "abnormal", "observation": ref})
    assert again["id"] != run["id"] and hub.engine.run(again["id"])["record"] == ref
    assert _read(hub, ref)["review"] == "received"
    hub.engine.complete(M(hub, "clinical-admin-agent"), item_for(hub, again, "sort"),
                        {"category": "abnormal", "rationale": "lower still"})
    hub.engine.decide(M(hub, "dr-liam"), item_for(hub, again, "review-flagged"), "no-action-needed", "")
    run_all_local(hub, log=quiet)
    assert _read(hub, ref)["review"] == "closed" and hub.engine.run(again["id"])["status"] == "completed"


def test_a_late_critical_review_reaches_the_medical_director_by_name_and_a_chaser(hub):
    """A critical review nobody has taken waits on every physician. Overdue, it still names the medical director
    (who is one of those physicians), and since that tells no one outside the physicians, the practice manager
    hears too. With the practice manager away it still reaches the director: never nobody. Told again, each person
    keeps one notice row, which counts the repeats."""
    from datetime import datetime, timedelta, timezone
    run = hub.engine.start(M(hub, "lab-feed"), "lab-result-review", record=_pat(hub), inputs={**LAB, "value": "118"})
    iid = item_for(hub, run, "review-critical")
    hub.db.items.set(iid, {"due_at": "2000-01-01T01:00:00Z", "created_at": "2000-01-01T00:00:00Z"})
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    assert iid in hub.engine.tick(now)["overdue"]

    def notes():
        return hub.db.items.find({"kind": "notice", "run_id": run["id"], "escalates": iid})
    first = notes()
    assert {(n["assignee"], n["role"]) for n in first} >= {("dr-amara", None)}
    assert any(n["role"] == "practice-manager" or n["assignee"] == "rana" for n in first)
    assert all(n["assignee"] != "dr-liam" for n in first)  # the pool is not told it is late, the director is
    hub.db.members.set("rana", {"status": "inactive"})
    for hours in (2, 4):
        assert iid in hub.engine.tick(now + timedelta(hours=hours))["overdue"]
    amara = [n for n in notes() if n["assignee"] == "dr-amara"]
    assert len(amara) == 1 and amara[0]["repeats"] == 3 and amara[0]["title"].startswith("Still not done (3)")
    assert amara[0]["priority"] == "urgent"


def test_a_physician_takes_over_a_late_critical_call_from_its_notice(hub):
    """A critical potassium: the physician asks for the patient to be called, the nurse gives the call back, and it
    goes overdue. The physician's notice offers Take it over first (Acknowledge second), and it hands them the call."""
    from datetime import datetime, timezone

    from practicehub.web.server import App, Redirect
    run = hub.engine.start(M(hub, "lab-feed"), "lab-result-review", record=_pat(hub),
                           inputs={"test": "Potassium", "value": "6.9", "unit": "mmol/L", "range": "3.5-5.0",
                                   "flag": "critical"})
    hub.engine.decide(M(hub, "dr-amara"), item_for(hub, run, "review-critical"), "call-patient", "call now")
    run_all_local(hub, log=quiet)
    call = item_for(hub, run, "call-critical")
    hub.engine.claim(M(hub, "noor"), call)
    hub.engine.release(M(hub, "noor"), call, "Going on break; please take this one")
    hub.db.items.set(call, {"due_at": "2000-01-01T01:00:00Z", "created_at": "2000-01-01T00:00:00Z"})
    hub.engine.tick(datetime.now(timezone.utc))
    liam = M(hub, "dr-liam")
    note = next(n for n in hub.db.items.find({"kind": "notice", "escalates": call})
                if hub.engine.eligibility(liam, hub.engine.item(n["_id"]))[0])
    app = App(hub, dev=False)
    page = app.page_item({"me": liam, "query": {}, "path": f"/items/{note['_id']}"}, note["_id"])
    assert page.index("Take it over") < page.index("Acknowledge")
    assert '<button class="secondary">Acknowledge' in page
    with pytest.raises(Redirect) as went:
        app.post_item({"me": liam, "body": b"", "path": f"/items/{note['_id']}/take-over"}, note["_id"], "take-over")
    assert went.value.to == f"/items/{call}"
    assert hub.engine.item(call)["claimed_by"] == "dr-liam"


# ------------------------------------------------------------------------------------------------ patient question
def _handling(hub, ref):
    return _read(hub, ref)["handling"]


def test_a_patients_message_is_filed_and_walked_from_received_to_closed(hub):
    """The patient writes in: the run files the message on their chart and walks it: triaged once screened,
    answered when the reply goes, closed. One run, one item; the patient is the message's subject."""
    pat = _pat(hub)
    run = hub.engine.run(hub.engine.start(M(hub, "jo"), "patient-question", record=pat,
                                          inputs={"question": "What are your hours on Saturday?"})["id"])
    ref = run["record"]
    assert ref.startswith("Communication/") and run["subject"] == pat
    msg = _read(hub, ref)
    assert msg["payload"][0]["contentString"] == "What are your hours on Saturday?"
    assert msg["subject"]["reference"] == pat and _handling(hub, ref) == "triaged"
    hub.engine.complete(M(hub, "clinical-admin-agent"), item_for(hub, run, "draft"),
                        {"reply": "We are open 9 to 12 on Saturdays.", "needs_clinician": False})
    hub.engine.decide(M(hub, "jo"), item_for(hub, run, "review"), "send", "")
    run_all_local(hub, log=quiet)
    assert _handling(hub, ref) == "closed" and _read(hub, ref)["status"] == "completed"
    assert hub.engine.run(run["id"])["status"] == "completed"
    assert len([i for i in _items(hub, run) if i["parent_id"] is None]) == 1
    moves = [e["data"]["to"] for e in hub.db.events.find({"subject": ref, "action": "record.transitioned"}, sort="seq")]
    assert moves == ["triaged", "answered", "closed"]


def test_a_new_question_is_its_own_message_but_the_same_one_twice_is_one(hub):
    """A second question while the first waits is its own message and run, screened like any (an urgent one is
    never refused or folded in); only the same words sent twice a moment ago are refused, naming the first."""
    from practicehub.hub import Duplicate
    jo, pat = M(hub, "jo"), _pat(hub)
    first = hub.engine.start(jo, "patient-question", record=pat, inputs={"question": "Can I book a physical?"})
    with pytest.raises(Duplicate) as exc:
        hub.engine.start(jo, "patient-question", record=pat, inputs={"question": "Can I book a physical?"})
    assert exc.value.run["id"] == first["id"]
    urgent = hub.engine.run(hub.engine.start(jo, "patient-question", record=pat, inputs={
        "question": "Crushing chest pain since this morning"})["id"])
    assert urgent["record"] != hub.engine.run(first["id"])["record"] and urgent["ctx"]["screen"]["red_flag"] is True
    # the patient's chart shows every open message, the urgent one marked as such
    from practicehub.web.server import App
    app = App(hub, dev=False)
    noor = M(hub, "noor")
    app._local.me = noor
    panel = app.work_panel(noor, pat, rt=hub.record_type("Patient"), st=None)
    assert "Urgent" in panel and panel.count('class="wcard"') >= 2


@pytest.mark.parametrize("action,want", [("reached-the-patient", ["triaged", "answered", "closed"]),
                                         ("welfare-check", ["triaged", "closed"])])
def test_a_red_flag_nobody_reached_is_answered_only_if_the_physician_reaches_them(hub, action, want):
    run = hub.engine.start(M(hub, "jo"), "patient-question", record=_pat(hub),
                           inputs={"question": "I have had crushing chest pain since this morning"})
    ref = hub.engine.run(run["id"])["record"]
    hub.engine.complete(M(hub, "noor"), item_for(hub, run, "urgent"),
                        {"reached": False, "action": "could-not-reach", "notes": "no answer"})
    hub.engine.complete(M(hub, "dr-amara"), item_for(hub, run, "not-reached"), {"action": action, "notes": "done"})
    moves = [e["data"]["to"] for e in hub.db.events.find({"subject": ref, "action": "record.transitioned"}, sort="seq")]
    assert moves == want and hub.engine.run(run["id"])["status"] == "completed"


def test_a_message_filed_by_hand_for_handling_starts_its_workflow_and_a_letter_does_not(hub):
    jo, pat = M(hub, "jo"), _pat(hub)
    msg = hub.create_record(jo, "Communication", {"status": "in-progress", "subject": {"reference": pat},
                                                  "payload": [{"contentString": "Please call me about my forms"}],
                                                  "handling": "received"})
    (run,) = hub.engine.runs(record=f"Communication/{msg['id']}")
    assert run["workflow_id"] == "patient-question" and run["ctx"]["input"]["question"] == "Please call me about my forms"
    letter = hub.create_record(jo, "Communication", {"status": "completed", "subject": {"reference": pat},
                                                     "payload": [{"contentString": "Letter from the hospital"}]})
    assert not hub.engine.runs(record=f"Communication/{letter['id']}")
    with pytest.raises(Denied):
        hub.update_record(jo, f"Communication/{msg['id']}", {"handling": "closed"})  # its run closes it


# ------------------------------------------------------------------------------------------------ privacy request
REQUEST = {"request": "Please keep my lab results from the nurses."}


def _results_of(hub, pat):
    return next(f"Observation/{o['id']}" for o in hub.records.search("Observation", subject=pat)
                if o.get("status") == "final")


def test_a_privacy_request_is_a_directive_walked_from_requested_to_in_force(hub):
    """Asked for at the desk, the run files the request as a directive (requested: nothing is withheld yet); the
    privacy officer reviews it and puts it in force, on that same record. One run, one item."""
    pat = _pat(hub)
    obs = _results_of(hub, pat)
    run = hub.engine.run(hub.engine.start(M(hub, "jo"), "privacy-request", record=pat, inputs=REQUEST)["id"])
    ref = run["record"]
    assert ref.startswith("Consent/") and run["subject"] == pat
    c = _read(hub, ref)
    assert c["status"] == "requested" and c["patient"]["reference"] == pat and c["request"] == REQUEST["request"]
    assert hub.read_record(M(hub, "noor"), obs).get("valueQuantity")  # not in force: nothing withheld
    with pytest.raises((Denied, Invalid)):
        hub.update_record(M(hub, "rana"), ref, {"status": "active"})  # its review puts it in force
    hub.engine.complete(M(hub, "rana"), item_for(hub, run, "directive"), {
        "decision": "put-in-force", "withhold": ["clinical"], "allow_roles": ["physician"],
        "told": "Nurses will not see your results; your physician still does."})
    run_all_local(hub, log=quiet)
    c = _read(hub, ref)
    assert c["status"] == "active" and c["withhold_classes"] == ["clinical"]
    assert "valueQuantity" not in hub.read_record(M(hub, "noor"), obs)
    assert hub.engine.run(run["id"])["status"] == "completed"
    assert len([i for i in _items(hub, run) if i["parent_id"] is None]) == 1
    moves = [e["data"]["to"] for e in hub.db.events.find({"subject": ref, "action": "record.transitioned"}, sort="seq")]
    assert moves == ["reviewed", "active"]
    assert len(hub.records.search("Consent", subject=pat)) == 1  # the request is the directive, not a second one


def test_a_privacy_request_the_officer_refuses_is_refused_and_the_patient_told(hub):
    pat = _pat(hub)
    run = hub.engine.start(M(hub, "jo"), "privacy-request", record=pat, inputs=REQUEST)
    ref = hub.engine.run(run["id"])["record"]
    hub.engine.complete(M(hub, "rana"), item_for(hub, run, "directive"), {
        "decision": "refuse", "told": "The request came from someone who is not you or your decision-maker."})
    run_all_local(hub, log=quiet)
    assert _read(hub, ref)["status"] == "refused" and hub.engine.run(run["id"])["status"] == "completed"
    sent = hub.db.outbox.find_one({"run_id": run["id"]}, sort="id")
    assert "could not record" in sent["body"]


# ------------------------------------------------------------------------------------------------ referral
REFERRAL = {"specialty": "Cardiology", "reason": "Palpitations on exertion", "urgency": "routine"}


def _referral_signed(hub, outcome="send"):
    amara = M(hub, "dr-amara")
    run = hub.engine.run(hub.engine.start(amara, "referral", record=_pat(hub), inputs=REFERRAL)["id"])
    run_all_local(hub, log=quiet)  # the assistant drafts the letter
    hub.engine.complete(amara, item_for(hub, run, "finalize"), {"letter": "Dear colleague, please see this patient."})
    hub.engine.decide(amara, item_for(hub, run, "sign"), outcome, "")
    run_all_local(hub, log=quiet)
    return hub.engine.run(run["id"])


def test_a_referral_is_its_own_record_walked_from_draft_to_report_received(hub):
    """Started on the patient, the run writes the referral (a draft order about them) and walks it: sent once
    signed, report received once the specialist's report is in. One run, one item; the patient is its subject."""
    pat = _pat(hub)
    run = _referral_signed(hub)
    ref = run["record"]
    assert ref.startswith("ServiceRequest/") and run["subject"] == pat
    rec = _read(hub, ref)
    assert rec["subject"]["reference"] == pat and rec["status"] == "active" and rec["intent"] == "order"
    with pytest.raises(Denied):  # its workflow moves it
        hub.update_record(M(hub, "dr-amara"), ref, {**rec, "status": "completed"})
    hub.engine.complete(M(hub, "jo"), item_for(hub, run, "booked"), {"seen": "booked", "appointment": "2027-01-12T10:00"})
    hub.engine.complete(M(hub, "noor"), item_for(hub, run, "report"), {"summary": "Holter normal; no follow-up."})
    run_all_local(hub, log=quiet)
    assert _read(hub, ref)["status"] == "completed" and hub.engine.run(run["id"])["status"] == "completed"
    moves = [e["data"]["to"] for e in hub.db.events.find({"subject": ref, "action": "record.transitioned"}, sort="seq")]
    assert moves == ["active", "completed"]
    assert len([i for i in _items(hub, run) if i["parent_id"] is None]) == 1


def test_a_referral_not_sent_or_declined_by_the_specialist_is_cancelled(hub):
    run = _referral_signed(hub, "cancel")
    assert _read(hub, run["record"])["status"] == "revoked" and run["status"] == "completed"
    run = _referral_signed(hub)
    hub.engine.complete(M(hub, "jo"), item_for(hub, run, "booked"), {"seen": "declined", "notes": "not accepting"})
    hub.engine.complete(M(hub, "dr-amara"), item_for(hub, run, "declined"), {"notes": "will refer elsewhere"})
    run_all_local(hub, log=quiet)
    assert _read(hub, run["record"])["status"] == "revoked"
