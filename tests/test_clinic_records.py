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
