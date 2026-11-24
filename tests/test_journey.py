"""A record's journey (``Hub.record_journey``): the stages it was in, the work done in each, and the moves between
them with what they ran."""

from datetime import datetime, timedelta

from conftest import M, item_for, quiet
from practicehub.agents.runner import run_all_local


def _pat(hub, family="MacLeod"):
    pat = next(p for p in hub.records.search("Patient") if p["name"][0]["family"] == family)
    return f"Patient/{pat['id']}"


def _at_the_visit(hub, ref, minutes=0):
    start = datetime.fromisoformat(hub.records.read(*ref.split("/", 1))["plannedStartDate"][:16])
    hub.engine._now = start + timedelta(minutes=minutes)


def _visit(hub, upto="onleave", family="MacLeod"):
    """A visit asked for at the desk, read by the nurse, booked by the scheduling agent, then checked in, triaged,
    started and stepped out of by hand: its run and its reference."""
    jo, noor, amara = M(hub, "jo"), M(hub, "noor"), M(hub, "dr-amara")
    run = hub.engine.start(jo, "appointment-request", record=_pat(hub, family),
                           inputs={"reason": "my knee hurts when I run", "kind": "new-concern"})
    ref = hub.engine.run(run["id"])["record"]
    if upto == "reading":
        return run, ref
    hub.engine.complete(noor, item_for(hub, run, "triage"), {"outcome": "book-a-slot"})
    if upto == "requested":
        return run, ref
    run_all_local(hub, log=quiet)  # the scheduling agent proposes a time; the visit is booked
    if upto == "planned":
        return run, ref
    _at_the_visit(hub, ref, -30)
    hub.update_record(jo, ref, {"status": "arrived"})
    hub.update_record(jo, ref, {"status": "triaged"})
    hub.update_record(amara, ref, {"status": "in-progress"})
    if upto == "in-progress":
        return run, ref
    hub.update_record(amara, ref, {"status": "onleave"})
    return run, ref


def _labels(j):
    return [st["label"] for st in j["stages"]]


def _stage(j, label, nth=0):
    return [st for st in j["stages"] if st["label"] == label][nth]


def _work(stage):
    return [ln for ln in stage["lines"] if ln["kind"] == "work"]


def test_a_visit_s_journey_nests_its_work_and_moves_under_the_stages_it_was_in(hub):
    """Asked for at the desk, read by a nurse, booked by an agent, then moved by hand through states its workflow
    has no work for: each stretch is a stage, with the work done there (and by whom: a person, an agent, the
    workflow) and the move out of it with what that move ran."""
    run, ref = _visit(hub)
    amara = M(hub, "dr-amara")
    j = hub.record_journey(amara, ref)
    assert _labels(j) == ["Requested", "Being read", "Requested", "Booked", "Checked in", "Triaged", "In progress",
                          "Patient stepped out"]
    assert [st["now"] for st in j["stages"]] == [False] * 7 + [True] and j["stages"][-1]["until"] is None
    assert [st["key"] for st in j["stages"]] == [f"s{n}" for n in range(8)]
    first = j["stages"][0]
    assert first["entered"]["how"] == "created" and first["entered"]["by"] == {"id": "jo", "name": "Jo Tremblay", "kind": "person"}
    (asked,) = first["moves"]  # the entry: what ran as the request came in, and where that put it
    assert (asked["how"], asked["label"], asked["to_label"], asked["status"]) == ("edge", "Asked for", "Being read",
                                                                                  "done")
    assert [s["title"] for s in asked["steps"]] == ["Screen the reason for red flags",
                                                    "Tell the patient what to do in an emergency",
                                                    "Move to Being read"]
    assert asked["steps"][1]["message"] == "queued" and asked["steps"][2]["lands"]
    assert asked["not_needed"]["count"] == len(asked["not_needed"]["titles"]) >= 5  # the other ways a request starts
    # a nurse read it and chose the way on; the ways out she did not take are the move's, by their names
    reading = _stage(j, "Being read")
    (read,) = _work(reading)
    assert (read["what"], read["status"], read["by"]) == ("task", "done", {"id": "noor", "name": "Noor Siddiqui", "kind": "person"})
    assert read["chose"] == "An ordinary slot is fine"
    (slot,) = reading["moves"]
    assert slot["label"] == "An ordinary slot is fine" and slot["not_taken"] == ["Book it soon", "I will call and book"]
    assert [s["title"] for s in slot["steps"]] == ["Move to Requested"] and slot["not_needed"]["count"] == 3
    # an agent did the next piece of work; what was not needed there is one fold
    asked_again = _stage(j, "Requested", 1)
    (propose,) = _work(asked_again)
    assert propose["by"] == {"id": "scheduling-agent", "name": "Scheduling agent", "kind": "agent"} and propose["status"] == "done"
    assert [ln["titles"] for ln in asked_again["lines"] if ln["kind"] == "fold"] == [["Book the visit"]]
    (booked,) = asked_again["moves"]
    assert booked["label"] == "Booked" and booked["by"]["kind"] == "agent" and booked["status"] == "done"
    assert [s["title"] for s in booked["steps"]][:3] == ["Book the visit", "Confirm with the patient",
                                                         "Tell whoever took the request that it is booked"]
    assert any(label == "Physician" for label, _ in booked["values"])  # what the move wrote, in words
    # the waits sit where they began, however late they ended; the moves by hand carry the type's own words
    assert [(w["what"], w["status"]) for w in _work(_stage(j, "Booked"))] == [("wait", "done"), ("wait", "done")]
    hand = [st["moves"][-1] for st in j["stages"][3:7]]
    assert [(m["how"], m["label"]) for m in hand] == [("hand", "Check in"), ("hand", "Mark triaged"),
                                                      ("hand", "Start the visit"), ("hand", "Patient stepped out")]
    assert [m["by"]["name"] for m in hand] == ["Jo Tremblay", "Jo Tremblay", "Dr. Amara Okafor", "Dr. Amara Okafor"]
    assert all(m["then"] == [] for m in hand)  # the workflow followed the start and ran nothing: nothing to tell
    assert not _work(j["stages"][-1]) or _work(j["stages"][-1])[0]["status"] == "open"
    import json
    blob = json.dumps(j)
    assert run["id"] not in blob and ref.split("/")[1] not in blob and "--" not in blob  # no ids, no step ids
    assert "run.start_state" not in blob and "==" not in blob  # no condition is ever told


def test_work_still_open_is_in_the_stage_the_record_is_in_and_says_where_it_began(hub):
    run, ref = _visit(hub)
    j = hub.record_journey(M(hub, "dr-amara"), ref)
    (end,) = _work(j["stages"][-1])  # opened when the visit started, still open now that the patient stepped out
    assert (end["title"], end["status"], end["since"]) == ("End the visit", "open", "In progress")
    assert end["for"] == "physician role" and end["by"] is None


def test_a_move_that_did_not_go_through_is_told_where_it_was_tried_with_its_retries(hub):
    run, ref = _visit(hub)
    amara = M(hub, "dr-amara")
    hub.engine.complete(amara, item_for(hub, run, "end"), {"transcript": "x" * 50, "visit_type": "minor", "minutes": 10})
    j = hub.record_journey(amara, ref)
    out = j["stages"][-1]
    assert out["label"] == "Patient stepped out" and out["now"]
    (end,) = _work(out)
    assert end["status"] == "done" and end["error"] is None  # the work was done; its move says what went wrong, once
    (ended,) = out["moves"]
    assert (ended["label"], ended["status"], ended["to_label"]) == ("Visit ended", "failed", "Note being drafted")
    (step,) = ended["steps"]
    assert step["status"] == "failed" and "does not move from" in step["error"] and step["retried"] == []
    assert not j["live"] or not j["busy"]  # stuck, not under way: nothing here will change by itself
    hub.engine.retry(M(hub, "rana"), run["id"], "ended--to", "try again")
    (step,) = hub.record_journey(amara, ref)["stages"][-1]["moves"][0]["steps"]
    assert step["status"] == "failed" and [(r["by"]["name"], r["reason"]) for r in step["retried"]] == [
        ("Rana Haddad", "try again")]
    # a correction by hand ends the stage; its work was stopped, and the stage it is in now says so
    hub.move_record(amara, ref, "entered-in-error")
    j = hub.record_journey(amara, ref)
    assert _labels(j)[-2:] == ["Patient stepped out", "Entered in error"]
    assert [(m["how"], m["status"]) for m in j["stages"][-2]["moves"]] == [("edge", "failed"), ("hand", "done")]
    (note,) = [ln for ln in j["stages"][-1]["lines"] if ln["kind"] == "note"]
    assert note["what"] == "stopped" and note["title"] == "Appointment request" and "correction" in note["why"]


def test_a_move_by_hand_carries_what_the_workflow_ran_after_it(hub):
    """A no-show marked on the visit's page: the workflow follows the move and runs its policy. Those automations
    belong to that move, nested under it, though they ran after the record had already moved."""
    jo = M(hub, "jo")
    run = hub.engine.start(jo, "appointment-request", record=_pat(hub), inputs={
        "reason": "a form for work", "kind": "forms", "slot": "2027-03-09T11:00|dr-amara"})
    ref = hub.engine.run(run["id"])["record"]
    _at_the_visit(hub, ref, 5)
    hub.update_record(jo, ref, {"status": "noshow"})
    j = hub.record_journey(M(hub, "dr-amara"), ref)
    assert _labels(j) == ["Requested", "Booked", "No-show"]
    (asked,) = j["stages"][0]["moves"]  # one edge, one move, though an automation of it moved the record early
    assert asked["to_label"] == "Booked" and [s["title"] for s in asked["steps"]][-1] == "Booked: wait for the visit"
    (mark,) = j["stages"][1]["moves"]
    assert (mark["how"], mark["label"], mark["by"]["name"]) == ("hand", "Mark no-show", "Jo Tremblay")
    (missed,) = mark["then"]
    assert missed["label"] == "Missed or cancelled" and missed["status"] == "done" and missed["by"] is None
    assert [s["title"] for s in missed["steps"]][:3] == ["Count the patient's missed appointments this year",
                                                         "Apply the missed-appointment policy", "Tell the patient"]
    assert missed["not_taken"] == ["Entered in error", "Visit started"] and missed["not_needed"]["count"] == 4
    (note,) = [ln for ln in j["stages"][2]["lines"] if ln["kind"] == "note"]
    assert note["what"] == "finished"


def test_a_task_done_by_moving_the_record_is_told_above_that_move_and_a_stop_is_a_stage(hub):
    from test_rules import _job, _jobs
    _jobs(hub)
    amara, liam = M(hub, "dr-amara"), M(hub, "dr-liam")
    ref, run = _job(hub)
    hub.update_record(amara, ref, {"title": "Fix the big autoclave"})
    hub.move_record(amara, ref, "doing")
    hub.move_record(liam, ref, "done", {"outcome": "New seal fitted", "hours": 3})
    hub.engine.complete(amara, item_for(hub, run, "file"), {"filed": True})
    hub.move_record(amara, ref, "approved", signature="Approved")
    j = hub.record_journey(amara, ref)
    assert _labels(j) == ["Open", "Doing", "Done", "Filed", "Approved"]
    assert [st["kind"] for st in j["stages"]] == ["lifecycle", "lifecycle", "lifecycle", "stop", "lifecycle"]
    assert [ln["changed"] for ln in j["stages"][0]["lines"] if ln["kind"] == "edit"] == [["Title"]]
    (work,) = _work(j["stages"][1])
    assert (work["title"], work["via_move"], work["by"]["name"], work["chose"]) == ("Do the work", True,
                                                                                    liam["name"], None)
    (finish,) = j["stages"][1]["moves"]
    assert finish["label"] == "Finish" and finish["values"] == [["Outcome", "New seal fitted"], ["Hours worked", "3"]]
    assert finish["then"] == []  # its workflow followed and ran nothing of its own
    (approve,) = j["stages"][3]["moves"]
    assert approve["signed"] == {"meaning": "Approved", "by": {"id": "dr-amara", "name": amara["name"], "kind": "person"}}


def test_a_record_with_a_lifecycle_and_no_workflow_has_a_journey_of_its_moves(hub):
    from test_rules import _jobs
    _jobs(hub, walked=False)
    amara = M(hub, "dr-amara")
    ref = f"Job/{hub.create_record(amara, 'Job', {'title': 'Fix the autoclave'})['id']}"
    hub.update_record(amara, ref, {"status": "doing"})
    hub.update_record(amara, ref, {"status": "done", "outcome": "Fixed"})
    j = hub.record_journey(amara, ref)
    assert _labels(j) == ["Open", "Doing", "Done"] and not j["live"] and not j["busy"]
    assert [(m["how"], m["label"], m["values"]) for st in j["stages"] for m in st["moves"]] == [
        ("hand", "Start", []), ("hand", "Finish", [["Outcome", "Fixed"]])]


def test_the_token_changes_with_the_record_and_its_work_and_not_with_a_view(hub):
    run, ref = _visit(hub, upto="reading")
    amara, jo = M(hub, "dr-amara"), M(hub, "jo")
    token = hub.record_journey_token(amara, ref)
    assert hub.record_journey(amara, ref)["token"] == token
    hub.note_view(amara, ref, how="opened")
    assert hub.record_journey_token(amara, ref) == token  # looking is not a change
    assert hub.record_journey_token(jo, ref) != token  # what one member is shown is not another's
    hub.engine.claim(M(hub, "noor"), item_for(hub, run, "triage"))
    taken = hub.record_journey_token(amara, ref)
    assert taken != token  # a step of its work changed
    hub.update_record(amara, ref, {"reasonCode": [{"text": "my knee, and now my ankle"}]})
    assert hub.record_journey_token(amara, ref) != taken  # the record itself changed


def test_work_an_agent_may_take_keeps_the_journey_live_and_work_a_person_holds_does_not(hub):
    run, ref = _visit(hub, upto="requested")  # the scheduling agent's step is open: it will be taken by itself
    amara = M(hub, "dr-amara")
    j = hub.record_journey(amara, ref)
    (propose,) = _work(j["stages"][-1])
    assert propose["status"] == "open" and j["live"] and not j["busy"]
    other = next(p["name"][0]["family"] for p in hub.records.search("Patient") if p["name"][0]["family"] != "MacLeod")
    run, ref = _visit(hub, upto="reading", family=other)  # a nurse's step waits on a person: nothing changes by itself
    j = hub.record_journey(M(hub, "jo"), ref)  # (who may not see the message still on its way)
    assert not j["live"]
    hub.engine.claim(M(hub, "noor"), item_for(hub, run, "triage"))
    (read,) = _work(hub.record_journey(amara, ref)["stages"][-1])
    assert (read["status"], read["by"]) == ("taken", {"id": "noor", "name": "Noor Siddiqui", "kind": "person"})
