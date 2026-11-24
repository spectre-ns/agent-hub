"""What a move set going (practicehub/activity.py): per move of a record, the automations on the edge it took, each
with its outcome, the records they started and the messages they queued; and where a started run came from
(``ctx.run.origin``, ``Engine.started_by``). On the demo hub's lab-result review."""
from practicehub import engine as engmod
from tests.conftest import M, follow_up_run, visit_run
from tests.test_clinic_records import _flagged_review, _pat

CRITICAL = {"test": "Potassium", "value": "6.9", "unit": "mmol/L", "range": "3.5-5.0", "flag": "critical"}


def _move(moves, edge):
    (m,) = [m for m in moves if m["edge"] == edge]
    return m


def _send(hub, run):
    """The page a critical result queued, sent by a pager connector."""
    from tests.test_rules import _connector
    msg = hub.db.outbox.find_one({"run_id": run["id"], "channel": "page"}, sort="id")
    pager = _connector(hub)
    hub.outbox_claim(pager, msg["id"])
    hub.outbox_sent(pager, msg["id"], "pager-ref-1")


def test_needs_a_call_lists_its_edges_automations_and_the_follow_up_it_started(hub):
    run, ref = _flagged_review(hub, "call-patient", "please call")
    amara = M(hub, "dr-amara")
    moves = hub.record_activity(amara, ref)
    assert [m["edge"] for m in moves] == ["start", "to-sort", "sorted", "call-patient"]  # oldest first
    assert [m["after_person"] for m in moves] == [True, False, True, True]  # "not critical" followed on from the check
    assert [m["seq"] for m in moves] == sorted(m["seq"] for m in moves)
    m = _move(moves, "call-patient")
    assert m["run"] == run["id"] and m["label"] == "Call the patient" and m["to"] == "closed"
    assert m["who"] == "dr-amara" and m["at"]
    decided = hub.db.events.find_one({"seq": m["seq"]})
    assert decided["action"] == "gate.decided" and decided["run_id"] == run["id"]
    # that edge's automations only, each done: the critical variant (its own when false) and the arrive step left out
    assert [r["step"] for r in m["ran"]] == ["call-patient-noted", "call-patient-filed", "follow-up"]
    assert all(r["state"] == "done" and not r["hidden"] and r["title"] and r["at"] and r["error"] is None
               for r in m["ran"])
    assert _move(moves, "start")["ran"] == []  # a move that only moved the record ran nothing
    fu = follow_up_run(hub, run)
    started = [s for r in m["ran"] for s in r["started"]]
    assert started == [{"ref": fu["record"], "run": fu["id"], "readable": True}]
    assert fu["record"].startswith("Task/")
    assert m["settled"] is False  # the call is still to be made
    # where the follow-up came from: the move that set it going
    assert fu["ctx"]["run"]["origin"] == {"step": "follow-up", "edge": "call-patient"}
    ev = hub.db.events.find_one({"action": "run.started", "run_id": fu["id"]})
    assert ev["data"]["origin"] == {"step": "follow-up", "edge": "call-patient"}
    assert hub.engine.started_by(fu) == {"ref": ref, "label": "Call the patient"}
    assert hub.engine.started_by(hub.engine.run(run["id"])) is None  # the lab started the result's own review


def test_a_critical_result_is_one_cascade_and_its_page_shows_its_status(hub):
    run = hub.engine.start(M(hub, "lab-feed"), "lab-result-review", record=_pat(hub), inputs=CRITICAL)
    ref = hub.engine.run(run["id"])["record"]
    amara = M(hub, "dr-amara")
    moves = hub.record_activity(amara, ref)
    assert [m["edge"] for m in moves] == ["start", "critical"]
    assert [m["after_person"] for m in moves] == [True, False]  # filed, then on its own to review: one cascade
    m = _move(moves, "critical")
    assert m["to"] == "received" and m["label"] == "Critical"
    assert [r["step"] for r in m["ran"]] == ["page", "tell-physicians"]
    page = m["ran"][0]
    assert page["state"] == "done" and page["message"] == "queued" and m["settled"] is False
    _send(hub, run)
    m = _move(hub.record_activity(amara, ref), "critical")
    assert m["ran"][0]["message"] == "sent" and m["settled"] is True
    assert "to-sort" not in [m["edge"] for m in hub.record_activity(amara, ref)]  # the edge not taken


def test_an_automation_that_fails_holds_what_waits_on_it_until_it_is_retried(hub, monkeypatch):
    def down(*_a, **_k):
        raise ValueError("pager network down")
    send = engmod.CORE_ACTIONS["message.send"]
    monkeypatch.setitem(engmod.CORE_ACTIONS, "message.send", down)
    run = hub.engine.start(M(hub, "lab-feed"), "lab-result-review", record=_pat(hub), inputs=CRITICAL)
    ref = hub.engine.run(run["id"])["record"]
    amara = M(hub, "dr-amara")
    m = _move(hub.record_activity(amara, ref), "critical")
    states = {r["step"]: r for r in m["ran"]}
    assert list(states) == ["page", "tell-physicians"]  # the arrive step, held too, is the move: not listed
    assert states["page"]["state"] == "failed" and "pager network down" in states["page"]["error"]
    assert states["tell-physicians"]["state"] == "held" and states["tell-physicians"]["error"] is None
    assert m["settled"] is False and m["to"] == "received"  # where it goes once what it ran is through
    assert hub.records.read(*ref.split("/", 1))["review"] == "checking"
    monkeypatch.setitem(engmod.CORE_ACTIONS, "message.send", send)  # not undo(): the hub fixture's env is on it too
    hub.engine.retry(M(hub, "rana"), run["id"], "page", "pager back")
    m = _move(hub.record_activity(amara, ref), "critical")
    assert [r["state"] for r in m["ran"]] == ["done", "done"] and m["ran"][0]["error"] is None
    assert hub.records.read(*ref.split("/", 1))["review"] == "received"
    _send(hub, run)
    assert _move(hub.record_activity(amara, ref), "critical")["settled"] is True


def test_a_member_without_the_clinical_class_sees_automations_folded_with_nothing_named(hub):
    jo, amara = M(hub, "jo"), M(hub, "dr-amara")
    ref = visit_run(hub)["record"]
    assert hub.may_read(jo, ref.split("/", 1)[0]) and not hub.can(jo, "reader", "data_class:clinical")
    seen = [r for m in hub.record_activity(amara, ref) for r in m["ran"]]
    assert seen and any(not r["hidden"] and r["title"] for r in seen)
    folded = [r for m in hub.record_activity(jo, ref) for r in m["ran"]]
    assert len(folded) == len(seen)
    for r in folded:  # the visit's workflow reads clinical records: every automation of it is a step she may not see
        assert r["hidden"] and r["title"] == "" and r["error"] is None and r["started"] == [] and r["message"] is None
