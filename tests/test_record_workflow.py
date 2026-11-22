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


def test_the_same_request_twice_is_refused_but_a_new_concern_is_its_own_screened_visit(hub):
    """Only the same submission twice (a double press, two people taking one call) is refused, and the refusal
    names the open request. Any other request, above all an urgent one, makes its own visit and is screened."""
    from practicehub.hub import Duplicate
    jo, noor = M(hub, "jo"), M(hub, "noor")
    pat = _pat(hub, "MacLeod")
    first = hub.engine.start(jo, "appointment-request", record=pat, inputs={"reason": "my knee hurts when I run"})
    with pytest.raises(Duplicate) as exc:  # the same call taken twice: refused, pointing at the first
        hub.engine.start(noor, "appointment-request", record=pat, inputs={"reason": "my knee hurts when I run"})
    assert exc.value.run["id"] == first["id"]
    # a new complaint while the knee waits: its own visit, screened for red flags like any request
    chest = hub.engine.start(noor, "appointment-request", record=pat,
                             inputs={"reason": "crushing chest pain since this morning", "urgency": "urgent"})
    chest = hub.engine.run(chest["id"])
    assert chest["record"] != hub.engine.run(first["id"])["record"]
    assert chest["steps"]["screen"]["state"] == "done"
    assert _status(hub, chest["record"]) == "requested" and _status(hub, hub.engine.run(first["id"])["record"]) == \
        "requested"
    # the patient's chart keeps both pieces of work, each with its own item
    runs = list(hub.db.runs.find({"workflow_id": "appointment-request", "subject": pat, "status": "running"}))
    assert {r["id"] for r in runs} >= {first["id"], chest["id"]}
    assert len({r["item"] for r in runs}) == len(runs)
    from practicehub.web.server import App
    app = App(hub, dev=False)
    nurse = M(hub, "noor")
    app._local.me = nurse
    panel = app.work_panel(nurse, pat, rt=hub.record_type("Patient"), st=None)
    assert panel.count('class="wcard"') >= 2  # two separate pieces of work on the chart, not one
    assert "Urgent" in panel  # the urgent one is marked urgent there
    # once the first is closed, the same words again are a new request
    hub.engine.cancel(jo, first["id"], "asked twice")
    hub.engine.start(jo, "appointment-request", record=pat, inputs={"reason": "my knee hurts when I run"})
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


def _to_the_visit(hub, by):
    """A visit asked for, booked, checked in and started, its run at the physician's "end the visit"."""
    jo = M(hub, "jo")
    run = hub.engine.start(jo, "appointment-request", record=_pat(hub, "MacLeod"), inputs={
        "reason": "a form for work", "kind": "forms", "slot": "2027-03-09T11:00|dr-amara"})
    ref = hub.engine.run(run["id"])["record"]
    hub.update_record(jo, ref, {"status": "arrived"})
    hub.update_record(by, ref, {"status": "in-progress"})
    return run, ref


def test_only_its_workflow_moves_the_visit_and_a_person_only_through_a_move_it_waits_on(hub):
    """A type that names its workflow lets that workflow alone move its state: a person's move goes through only when
    the open step waits on it (a wait's until, a step's moves), never another workflow's run, never a move the work
    has gone past. The record's move and the run's step are one transaction."""
    from practicehub.hub import Denied
    amara = M(hub, "dr-amara")
    run, ref = _to_the_visit(hub, amara)
    hub.update_record(amara, ref, {"status": "onleave"})  # the end step lists it: the physician steps out
    hub.update_record(amara, ref, {"status": "in-progress"})
    hub.engine.complete(amara, item_for(hub, run, "end"), {"transcript": TRANSCRIPT, "visit_type": "minor",
                                                           "minutes": 10})
    with pytest.raises(Denied, match="not waiting for"):  # the visit has ended: being documented, not paused
        hub.update_record(amara, ref, {"status": "onleave"})
    # the page offers no move its workflow is not waiting for
    from practicehub.web.server import App
    app = App(hub, dev=False)
    app._local.me = amara
    page = app.page_record({"me": amara, "query": {}, "path": "/records/" + ref}, *ref.split("/", 1))
    assert 'name="to" value="onleave"' not in page
    # another workflow's run never moves it, whoever runs it
    other = hub.engine.start(M(hub, "jo"), "appointment-request", record=_pat(hub, "MacLeod"),
                             inputs={"reason": "another thing"})
    with pytest.raises(Denied, match="its own workflow"):
        hub.update_record(amara, ref, {"status": "onleave"}, run_id=other["id"])
    assert _status(hub, ref) == "in-progress"


def test_a_move_its_workflow_waits_on_and_the_step_it_takes_are_one_transaction(hub, monkeypatch):
    jo = M(hub, "jo")
    run = hub.engine.start(jo, "appointment-request", record=_pat(hub, "MacLeod"), inputs={
        "reason": "a form for work", "kind": "forms", "slot": "2027-03-09T11:00|dr-amara"})
    ref = hub.engine.run(run["id"])["record"]
    real = hub.engine._advance

    def broken(run_id, cause):
        raise RuntimeError("the database went away")
    monkeypatch.setattr(hub.engine, "_advance", broken)
    with pytest.raises(RuntimeError):
        hub.update_record(jo, ref, {"status": "arrived"})
    # neither happened: the visit is still booked and the run still waits for the check-in
    assert _status(hub, ref) == "planned"
    assert hub.engine.run(run["id"])["steps"]["checked-in"]["state"] == "waiting"
    monkeypatch.setattr(hub.engine, "_advance", real)
    hub.update_record(jo, ref, {"status": "arrived"})
    assert _status(hub, ref) == "arrived" and hub.engine.run(run["id"])["steps"]["checked-in"]["state"] == "done"


def test_the_note_names_who_wrote_it_and_who_signed_it_with_what_the_signature_means(hub):
    """A note one physician wrote and another reviewed and signed: the writer is its author (and saw the patient),
    the signer its legal attester, with the reviewer's meaning, never the reviewer as author."""
    amara, liam = M(hub, "dr-amara"), M(hub, "dr-liam")
    run, ref = _to_the_visit(hub, amara)
    hub.engine.complete(amara, item_for(hub, run, "end"), {"transcript": TRANSCRIPT, "visit_type": "minor",
                                                           "minutes": 10})
    run_all_local(hub, log=quiet)
    item = item_for(hub, run, "edit")
    draft = hub.engine.context(amara, item)["draft"]
    fields = {k: draft.get(k) or "x" for k in ("subjective", "assessment", "plan")}
    hub.engine.complete(amara, item, {**fields, "diagnosis_codes": ["V68"]})
    hub.engine.decide(liam, item_for(hub, run, "sign"), "sign", "")
    run_all_local(hub, log=quiet)
    (note,) = [c for c in hub.records.search("Composition", limit=None)
               if (c.get("encounter") or {}).get("reference") == ref]
    assert note["author"] == [{"reference": "Practitioner/dr-amara"}]
    (att,) = note["attester"]
    assert att["party"]["reference"] == "Practitioner/dr-liam"
    assert att["mode"]["coding"][0]["code"] == "legal" and att["mode"]["text"] == "I reviewed this note and it is accurate"
    visit = hub.records.read(*ref.split("/", 1))
    assert visit["participant"][0]["individual"]["reference"] == "Practitioner/dr-amara"


def test_the_record_page_says_who_holds_it_what_is_next_and_when_it_is_due_even_when_a_step_stopped(hub, monkeypatch):
    from practicehub import engine
    from practicehub.web.server import App
    amara, jo = M(hub, "dr-amara"), M(hub, "jo")
    run, ref = _to_the_visit(hub, amara)
    app = App(hub, dev=False)
    app._local.me = jo
    app._local.me = amara  # the clinical steps are the clinical team's to see
    panel = app.work_panel(amara, ref, rt=hub.record_type("Encounter"), st="in-progress")
    app._local.me = jo
    assert "End the visit" in panel and "is told" in panel  # who, and who hears when it is late
    assert "Due" in panel or "due within" in panel
    assert ">Next<" in panel  # what comes after it
    # a step that stopped keeps saying who is fixing it, and why it stopped
    other = hub.engine.start(jo, "appointment-request", record=_pat(hub, "MacLeod"), inputs={
        "reason": "a form for work", "kind": "forms", "slot": "2027-03-09T14:00|dr-amara"})
    vref = hub.engine.run(other["id"])["record"]

    def down(*a, **k):
        raise RuntimeError("the portal is down")
    monkeypatch.setitem(engine.CORE_ACTIONS, "message.send", down)
    hub.update_record(jo, vref, {"status": "noshow"})
    assert hub.engine.run(other["id"])["steps"]["tell-missed"]["state"] == "failed"
    app._local.me = amara
    panel = app.work_panel(amara, vref, rt=hub.record_type("Encounter"), st="noshow")
    assert "Stopped" in panel and "being fixed by" in panel and "the portal is down" in panel
    assert "tell-missed" not in panel  # step titles, never step ids


OLD_SHAPE = """id = "appointment-request"
title = "Appointment request"
record = "Patient"
[trigger]
starters = ["front-desk", "scheduler"]
[[input]]
name = "reason"
type = "text"
required = true
[[steps]]
id = "propose"
title = "Propose a slot"
kind = "task"
role = "scheduler"
[[steps.fields]]
name = "slot"
type = "string"
required = true
[[steps]]
id = "book"
title = "Book the visit"
kind = "automation"
after = ["propose"]
action = "booking.book"
[steps.params]
slot = "{{propose.slot}}"
subject = "{{run.record}}"
reason = "{{input.reason}}"
defaults = { status = "planned" }   # as the step was written then: a booked visit
"""


def test_a_run_from_before_the_visit_had_its_own_workflow_moves_onto_its_own_visit(hub, monkeypatch):
    """Today's runs about the patient move onto a visit of their own: once, each in its own transaction, keeping
    their version, item, key and steps so far; a failure is noted and tried again; the run then books that visit
    (not another one) and, when its older version ends, the visit's current workflow walks it from there."""
    jo, rana = M(hub, "jo"), M(hub, "rana")
    noor = jo  # the scheduler
    pat = _pat(hub, "MacLeod")
    old = hub.propose_workflow(jo, OLD_SHAPE, "the shape before")
    hub.approve_workflow(rana, "appointment-request", old["version"])
    run = hub.engine.start(jo, "appointment-request", record=pat, inputs={"reason": "my knee"})
    rid = run["id"]
    propose = item_for(hub, run, "propose")
    hub.engine.claim(noor, propose)
    before = hub.engine.run(rid)
    assert before["record"] == pat  # about the patient, as runs were before the visit had its own
    cur = hub.propose_workflow(jo, hub.pack.workflow_sources["appointment-request"], "its own visit")
    hub.approve_workflow(rana, "appointment-request", cur["version"])
    visits = len(hub.records.search("Encounter", limit=None))
    # a failure: noted, the run left as it was, and tried again next time
    real = hub.engine._make_record

    def down(*a, **k):
        raise RuntimeError("the database went away")
    monkeypatch.setattr(hub.engine, "_make_record", down)
    assert hub.engine.relink_runs()["failed"] == [rid]
    assert hub.engine.run(rid)["record"] == pat
    assert hub.db.events.find({"action": "run.relink_failed", "subject": rid})
    monkeypatch.setattr(hub.engine, "_make_record", real)
    hub.engine.tick()  # every tick tries again
    after = hub.engine.run(rid)
    ref = after["record"]
    assert ref.startswith("Encounter/") and after["subject"] == pat and _status(hub, ref) == "requested"
    assert after["version"] == before["version"] and after["item"] == before["item"]
    assert after["steps"] == before["steps"] and after["ctx"]["run"]["record"] == pat  # its pinned steps say so
    assert len(hub.records.search("Encounter", limit=None)) == visits + 1
    assert hub.engine.relink_runs() == {"moved": [], "failed": []}  # once only
    # its older step books that visit, not another one; when it ends, the visit's own workflow takes it on
    hub.engine.complete(noor, propose, {"slot": "2027-03-09T15:00|dr-amara"})
    assert _status(hub, ref) == "planned" and len(hub.records.search("Encounter", limit=None)) == visits + 1
    assert hub.engine.run(rid)["status"] == "completed"
    (walk,) = hub.engine.runs(record=ref, status="running")
    assert walk["version"] == cur["version"] and walk["steps"]["checked-in"]["state"] == "waiting"
    hub.update_record(jo, ref, {"status": "arrived"})
    assert hub.engine.run(walk["id"])["steps"]["checked-in"]["state"] == "done"
