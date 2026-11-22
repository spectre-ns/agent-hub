"""A record type names the workflow that moves it from open to closed: creating the record starts it, about that
record; the record's lifecycle state is where the work is; one run, one item, one record for the whole of its life.
The general-practice pack's visit (Encounter) and its appointment workflow are the worked example."""
import pytest

from practicehub import schema
from tests.conftest import M, item_for, quiet, run_all_local


TRANSCRIPT = "\n".join([
    "Patient: Sore throat for four days, no fever, eating and drinking fine.",
    "Exam: Temp 37.1. Throat mildly red, no exudate.",
    "Assessment: Viral pharyngitis.",
    "Plan: Fluids, rest, lozenges. No antibiotics.",
    "Follow-up: if not better in a week."])


def _pat(hub, family=None):
    pats = hub.records.search("Patient")
    pat = next(p for p in pats if not family or p["name"][0]["family"] == family)
    return f"Patient/{pat['id']}"


def _status(hub, ref):
    return hub.records.read(*ref.split("/", 1))["status"]


def _items(hub, run):
    """The run's work items (its one item and any children), notices apart."""
    return list(hub.db.items.find({"run_id": run["id"], "kind": {"$ne": "notice"}}))


def test_starting_a_request_makes_the_requested_visit_on_the_chart(hub):
    from practicehub.web.server import App
    jo = M(hub, "jo")
    pat = _pat(hub, "MacLeod")
    run = hub.engine.start(jo, "appointment-request", record=pat, inputs={"reason": "my knee hurts when I run",
                                                                          "kind": "new-concern"})
    run = hub.engine.run(run["id"])
    ref = run["record"]
    assert ref.startswith("Encounter/") and run["subject"] == pat and run["ctx"]["run"]["subject"] == pat
    visit = hub.records.read(*ref.split("/", 1))
    assert visit["status"] == "requested" and visit["subject"]["reference"] == pat
    assert visit["reasonCode"][0]["text"] == "my knee hurts when I run" and visit["requested_by"] == "jo"
    assert not visit.get("plannedStartDate")  # asked for: no time yet, and still a valid visit
    rt = hub.record_type("Encounter")
    assert not schema.validate(rt, visit, members=hub._member_ids(), is_a=hub.is_a)
    # the run's one item points at the visit; the chart lists the visit
    (item,) = [i for i in _items(hub, run) if i["parent_id"] is None]
    assert item["id"] == run["item"] and item["step_id"] == "triage"  # the nurse reads the reason first
    listed = [i for i in hub.engine.queue(M(hub, "noor")) + hub.engine.inbox(M(hub, "noor"))
              if i["run_id"] == run["id"]]
    assert listed and all(i["record"] == ref for i in listed)  # the hand-off is about the visit
    page = App(hub, dev=False).page_record({"me": M(hub, "dr-amara"), "query": {}, "path": "/records/" + pat},
                                           *pat.split("/", 1))
    assert ref.split("/", 1)[1] in page and "Requested" in page


def test_a_routine_request_from_a_slot_books_the_same_visit(hub):
    jo = M(hub, "jo")
    pat = _pat(hub, "MacLeod")
    run = hub.engine.start(jo, "appointment-request", record=pat, inputs={
        "reason": "a form for work", "kind": "forms", "slot": "2027-03-09T11:00|dr-liam"})
    run = hub.engine.run(run["id"])
    ref = run["record"]
    visit = hub.records.read(*ref.split("/", 1))
    assert visit["status"] == "planned" and visit["plannedStartDate"] == "2027-03-09T11:00"
    assert visit["participant"][0]["individual"]["reference"] == "Practitioner/dr-liam"
    assert run["steps"]["book-chosen"]["state"] == "done" and run["steps"]["checked-in"]["state"] == "waiting"
    # one visit for the request: none made apart from it
    assert [v for v in hub.records.search("Encounter", limit=None) if v.get("plannedStartDate") == "2027-03-09T11:00"] \
        == [visit]


def test_cancelling_the_request_cancels_the_visit(hub):
    jo = M(hub, "jo")
    run = hub.engine.start(jo, "appointment-request", record=_pat(hub, "MacLeod"),
                           inputs={"reason": "my knee hurts when I run", "kind": "new-concern"})
    ref = hub.engine.run(run["id"])["record"]
    assert _status(hub, ref) == "requested"
    hub.engine.cancel(jo, run["id"], "the patient called back: no longer needed")
    assert _status(hub, ref) == "cancelled" and hub.engine.run(run["id"])["status"] == "cancelled"


def test_a_request_closed_without_a_visit_cancels_it(hub):
    """The urgent branch: the nurse calls, advises and books nothing; the request is closed, the visit cancelled."""
    jo, noor = M(hub, "jo"), M(hub, "noor")
    run = hub.engine.start(jo, "appointment-request", record=_pat(hub, "MacLeod"),
                           inputs={"reason": "my knee hurts when I run", "urgency": "urgent"})
    ref = hub.engine.run(run["id"])["record"]
    hub.engine.complete(noor, item_for(hub, run, "nurse-call"), {"reached": True, "advice": "ice and rest"})
    run = hub.engine.run(run["id"])
    assert run["steps"]["not-booked"]["state"] == "done" and _status(hub, ref) == "cancelled"
    assert run["status"] == "completed"


def test_the_nurse_books_an_urgent_request_on_the_same_visit(hub):
    jo, noor = M(hub, "jo"), M(hub, "noor")
    run = hub.engine.start(jo, "appointment-request", record=_pat(hub, "MacLeod"),
                           inputs={"reason": "my knee hurts when I run", "urgency": "urgent"})
    ref = hub.engine.run(run["id"])["record"]
    hub.engine.complete(noor, item_for(hub, run, "nurse-call"), {"reached": True, "advice": "seen today",
                                                                 "book": True})
    item = item_for(hub, run, "nurse-book")
    slot = hub.engine.context(noor, item)["context"]
    free = next(v for k, v in slot.items() if "free" in k)
    choice = free[0]["value"] if isinstance(free[0], dict) else free[0]
    hub.engine.complete(noor, item, {"slot": choice})
    assert _status(hub, ref) == "planned" and hub.engine.run(run["id"])["steps"]["not-booked"]["state"] == "skipped"


def test_a_second_request_for_the_same_patient_is_refused(hub):
    from practicehub.hub import Invalid
    jo = M(hub, "jo")
    pat = _pat(hub, "MacLeod")
    first = hub.engine.start(jo, "appointment-request", record=pat, inputs={"reason": "my knee hurts when I run"})
    with pytest.raises(Invalid):
        hub.engine.start(M(hub, "noor"), "appointment-request", record=pat, inputs={"reason": "the same knee"})
    # once the first is booked, the patient may ask for another visit
    hub.engine.cancel(jo, first["id"], "asked twice")
    hub.engine.start(jo, "appointment-request", record=pat, inputs={"reason": "the same knee"})
    # a visit booked straight on the calendar is never refused, and does not block a request
    hub.create_record(jo, "Encounter", {
        "status": "planned", "subject": {"reference": pat}, "plannedStartDate": "2027-03-10T09:00",
        "plannedEndDate": "2027-03-10T09:15", "participant": [{"individual": {"reference": "Practitioner/dr-liam"}}]})


def test_one_visit_walks_from_requested_to_completed_with_one_item(hub):
    """The whole life of one visit: asked for, booked, checked in on its own page, seen, ended, documented, signed,
    completed. One run, one item, one record; its status says where the work is at each step."""
    jo, amara = M(hub, "jo"), M(hub, "dr-amara")
    pat = _pat(hub, "MacLeod")
    run = hub.engine.start(jo, "appointment-request", record=pat, inputs={"reason": "a form for work",
                                                                          "kind": "forms"})
    rid = run["id"]
    ref = hub.engine.run(rid)["record"]
    item_id = hub.engine.run(rid)["item"]
    assert _status(hub, ref) == "requested"  # routine paperwork: the scheduler proposes a slot
    propose = item_for(hub, run, "propose")
    assert hub.engine.item(propose)["id"] == item_id  # the same item, now at the scheduler's step
    run_all_local(hub, log=quiet)
    assert _status(hub, ref) == "planned" and hub.records.read(*ref.split("/", 1))["plannedStartDate"]
    # checking in on the visit's own page (its lifecycle transition) moves the workflow on: no button of its own
    from practicehub.web.server import App
    page = App(hub, dev=False).page_record({"me": jo, "query": {}, "path": "/records/" + ref}, *ref.split("/", 1))
    assert "Check in" in page
    hub.update_record(jo, ref, {"status": "arrived"})
    run = hub.engine.run(rid)
    assert run["steps"]["checked-in"]["state"] == "done" and run["steps"]["seen"]["state"] == "waiting"
    assert _status(hub, ref) == "arrived"
    hub.update_record(amara, ref, {"status": "in-progress"})
    run = hub.engine.run(rid)
    assert run["steps"]["seen"]["state"] == "done" and _status(hub, ref) == "in-progress"
    hub.engine.complete(amara, item_for(hub, run, "end"), {
        "transcript": TRANSCRIPT, "visit_type": "minor", "minutes": 10})
    run_all_local(hub, log=quiet)  # the scribe agent drafts the note
    item = item_for(hub, run, "edit")
    draft = hub.engine.context(amara, item)["draft"]
    fields = {k: draft.get(k) or "x" for k in ("subjective", "assessment", "plan")}
    hub.engine.complete(amara, item, {**fields, "diagnosis_codes": ["V68"]})
    assert _status(hub, ref) == "in-progress"  # documented, not yet signed: the visit is not completed
    hub.engine.decide(amara, item_for(hub, run, "sign"), "sign", "")
    assert _status(hub, ref) == "finished"
    visit = hub.records.read(*ref.split("/", 1))
    assert visit["period"]["start"] and visit["diagnosis"][0]["condition"][0]["concept"]["text"] == "ICD-9 V68"
    notes = [c for c in hub.records.search("Composition", limit=None)
             if (c.get("encounter") or {}).get("reference") == ref]
    assert len(notes) == 1 and notes[0]["subject"]["reference"] == pat
    # one item for the whole life: the run's own, never another one per step
    tops = [i for i in _items(hub, run) if i["parent_id"] is None]
    assert [i["id"] for i in tops] == [item_id]
    assert len(hub.engine.runs(record=ref)) == 1
    transitions = [e["data"].get("to") for e in hub.db.events.find({"subject": ref, "action": "record.transitioned"},
                                                                   sort="seq")]
    assert transitions == ["planned", "arrived", "in-progress", "finished"]


def test_a_no_show_applies_the_policy_and_closes_the_run(hub):
    jo = M(hub, "jo")
    run = hub.engine.start(jo, "appointment-request", record=_pat(hub, "MacLeod"), inputs={
        "reason": "a form for work", "kind": "forms", "slot": "2027-03-09T11:00|dr-amara"})
    ref = hub.engine.run(run["id"])["record"]
    hub.update_record(jo, ref, {"status": "noshow"})
    run = hub.engine.run(run["id"])
    assert run["steps"]["no-show"]["state"] == "done" and run["steps"]["tell-missed"]["state"] == "done"
    assert run["steps"]["end"]["state"] == "skipped" and run["status"] == "completed"
    assert _status(hub, ref) == "noshow"


def test_a_visit_booked_on_the_calendar_starts_its_workflow_booked(hub):
    jo = M(hub, "jo")
    pat = _pat(hub, "MacLeod")
    visit = hub.create_record(jo, "Encounter", {
        "status": "planned", "subject": {"reference": pat}, "plannedStartDate": "2027-03-10T09:00",
        "plannedEndDate": "2027-03-10T09:15", "participant": [{"individual": {"reference": "Practitioner/dr-liam"}}],
        "serviceType": [{"concept": {"text": "forms"}}]})
    ref = f"Encounter/{visit['id']}"
    (run,) = hub.engine.runs(record=ref)
    assert run["workflow_id"] == "appointment-request" and run["subject"] == pat
    assert run["ctx"]["input"]["kind"] == "forms"  # the start form is what the record says
    assert run["steps"]["screen"]["state"] == "skipped" and run["steps"]["checked-in"]["state"] == "waiting"
    hub.update_record(jo, ref, {"status": "cancelled"})
    run = hub.engine.run(run["id"])
    assert run["status"] == "completed" and _status(hub, ref) == "cancelled"
    # a visit filed after the fact (finished) starts nothing
    imported = {"id": "import:test", "kind": "automation", "name": "Chart import", "status": "active"}
    done = hub.create_record(imported, "Encounter", {
        "status": "finished", "subject": {"reference": pat}, "plannedStartDate": "2026-01-10T09:00"})
    assert not hub.engine.runs(record=f"Encounter/{done['id']}")


def test_the_run_page_and_the_visit_show_the_one_item_and_where_it_is(hub):
    """The run page opens on its one item: its key, its title, the step it is at and whose it is, and the visit in
    its state. The visit's page shows its state and the hand-off (who holds the work); the board shows the visit."""
    from practicehub.web.server import App
    jo, amara, app = M(hub, "jo"), M(hub, "dr-amara"), App(hub, dev=False)
    pat = _pat(hub, "MacLeod")
    run = hub.engine.start(jo, "appointment-request", record=pat, inputs={"reason": "a form for work",
                                                                          "kind": "forms"})
    run = hub.engine.run(run["id"])
    ref = run["record"]
    req = lambda path: {"me": amara, "query": {}, "path": path}
    app._local.me = amara  # who reads, as the server sets it per request
    page = app.page_run(req(f"/runs/{run['id']}"), run["id"])
    head = page.split('class="card run-head"', 1)[1].split("</div>", 1)[0]
    assert app._key(run["item"]) in head and f'href="/items/{run["item"]}"' in head
    step = hub.workflow("appointment-request")[0].step(hub.engine.item(run["item"])["step_id"])
    assert f"now at <b>{step.title}</b>" in head
    assert f'/records/{ref}' in head and "Requested" in head
    visit = app.page_record(req(f"/records/{ref}"), *ref.split("/", 1))
    assert "Requested" in visit and "Open work" in visit and f'href="/runs/{run["id"]}"' in visit
    assert "waiting for someone in" in visit or "with " in visit  # who holds it
    board = app.page_board(req("/board"))
    assert f'/items/{run["item"]}' in board
