"""Block automations as jobs: where a worker runs (``Hub.jobs_async``, set by ``practicehub serve``), a block is queued,
taken by a worker (running), run with no transaction open, and its answer written (done or failed). The tests drive
the three phases by hand (``Engine.job_start``, ``job_call``, ``job_finish``): no threads, nothing left to timing."""

import threading

import pytest

from conftest import M, item_for
from practicehub import backends
from test_clinic_records import LAB, _pat


def _lab(hub, **more):
    """A lab result filed while a worker runs jobs: its run, its record, and the range check waiting in the queue."""
    hub.jobs_async = True
    run = hub.engine.start(M(hub, "lab-feed"), "lab-result-review", record=_pat(hub), inputs={**LAB, **more})
    run = hub.engine.run(run["id"])
    return run, run["record"]


def _state(hub, run, sid="check"):
    return hub.engine.run(run["id"])["steps"][sid]


def _events(hub, run, sid="check"):
    return [e for e in hub.db.events.find({"run_id": run["id"]}, sort="seq") if (e.get("data") or {}).get("step") == sid]


def test_a_job_is_queued_then_running_then_done_with_its_events_in_order(hub):
    run, ref = _lab(hub)
    st = _state(hub, run)
    assert st["state"] == "queued" and st["engine"] == "local" and st["attempt"] == 1 and st["queued_at"]
    assert hub.db.runs.get(run["id"])["jobs"] == ["check"] and hub.engine.jobs() == [(run["id"], "check")]
    assert not hub.db.items.find({"run_id": run["id"], "step_id": "sort"})  # nothing goes on until it has run
    job = hub.engine.job_start(run["id"], "check")
    st = _state(hub, run)
    assert st["state"] == "running" and st["started_at"] and st["job"] == job["token"] and ":" in st["worker"]
    assert hub.engine.jobs() == [] and hub.db.runs.get(run["id"])["jobs"] == ["check"]  # taken, and still a job
    res, exc, ms = hub.engine.job_call(job)
    assert exc is None and res["output"] and ms >= 0
    hub.engine.job_finish(job, res, exc, ms)
    st = _state(hub, run)
    assert st["state"] == "done" and st["done_at"] and st["ms"] == ms and "job" not in st and "worker" not in st
    assert hub.db.runs.get(run["id"])["jobs"] is None
    assert hub.engine.run(run["id"])["ctx"]["check"]["band"] == "normal"
    acts = [(e["action"], e["cause"]) for e in _events(hub, run)]
    assert [a for a, _ in acts] == ["step.queued", "step.started", "action.ran"]
    seqs = [e["seq"] for e in _events(hub, run)]
    assert acts[1][1] == seqs[0] and acts[2][1] == seqs[1]  # each follows from the one before
    ran = _events(hub, run)[-1]["data"]
    assert ran["block"] == "lab-range-check" and ran["engine"] == "local" and ran["ms"] == ms and ran["attempt"] == 1
    # the run went on from it: the next state's work is open
    assert item_for(hub, run, "sort")


def test_what_waits_on_a_job_stays_put_and_the_run_goes_on_once_it_is_done(hub):
    hub.jobs_async = True
    jo = M(hub, "jo")
    run = hub.engine.start(jo, "appointment-request", record=_pat(hub),
                           inputs={"reason": "my knee hurts when I run", "kind": "new-concern"})
    steps = hub.engine.run(run["id"])["steps"]
    assert steps["screen"]["state"] == "queued"
    assert steps["safety-net"]["state"] == "pending" and steps["start--to"]["state"] == "pending"  # they read it
    assert steps["triage"]["state"] == "pending"
    ref = hub.engine.run(run["id"])["record"]
    assert hub.records.read(*ref.split("/", 1))["status"] == "requested"  # made, and not yet where it starts
    assert hub.engine.run_jobs() == 1
    steps = hub.engine.run(run["id"])["steps"]
    assert steps["screen"]["state"] == "done" and steps["safety-net"]["state"] == "done"
    assert steps["triage"]["state"] == "active" and hub.records.read(*ref.split("/", 1))["status"] == "reading"


def test_no_transaction_is_open_and_the_step_reads_running_while_a_block_runs(hub, monkeypatch):
    run, ref = _lab(hub)
    seen = {}
    real = backends.LocalPython.run

    def watched(self, block, inputs, meta):
        seen["in_transaction"] = hub.db.in_transaction()
        seen["state"] = hub.db.runs.get(run["id"])["steps"]["check"]["state"]  # another reader's view, mid-call
        seen["inputs"] = dict(inputs)
        return real(self, block, inputs, meta)
    monkeypatch.setattr(backends.LocalPython, "run", watched)
    assert hub.engine.run_jobs() == 1
    assert seen["in_transaction"] is False and seen["state"] == "running"
    assert set(seen["inputs"]) == {"test", "value", "unit", "range"}  # only what the block declares, as in place


def test_a_job_that_fails_stops_its_step_and_tells_the_practice(hub, monkeypatch):
    run, ref = _lab(hub)

    def down(self, block, inputs, meta):
        raise backends.EngineError("the sandbox is down")
    monkeypatch.setattr(backends.LocalPython, "run", down)
    assert hub.engine.run_jobs() == 1
    st = _state(hub, run)
    assert st["state"] == "failed" and "sandbox is down" in st["error"] and st["attempt"] == 1 and st["ms"] >= 0
    assert hub.db.runs.get(run["id"])["jobs"] is None and hub.engine.jobs() == []
    assert [e["action"] for e in _events(hub, run)] == ["step.queued", "step.started", "step.failed"]
    assert hub.db.items.find({"run_id": run["id"], "kind": "notice"})  # the practice is told, as for any failure
    # retried, it is queued again (counted), and runs
    monkeypatch.undo()
    hub.jobs_async = True
    hub.engine.retry(M(hub, "rana"), run["id"], "check", "the sandbox is back")
    st = _state(hub, run)
    assert st["state"] == "queued" and st["attempt"] == 2
    assert hub.engine.run_jobs() == 1 and _state(hub, run)["state"] == "done"


def test_a_job_whose_work_was_cancelled_changes_nothing_and_the_track_says_its_answer_came(hub):
    run, ref = _lab(hub)
    job = hub.engine.job_start(run["id"], "check")
    res, exc, ms = hub.engine.job_call(job)
    hub.engine.cancel(M(hub, "rana"), run["id"], "filed twice")
    before = hub.records.read(*ref.split("/", 1))
    hub.engine.job_finish(job, res, exc, ms)
    row = hub.engine.run(run["id"])
    assert row["status"] == "cancelled" and row["steps"]["check"]["state"] == "done" and row["steps"]["check"]["late"]
    assert "check" not in row["ctx"]  # its output is no one's
    assert hub.records.read(*ref.split("/", 1)) == before
    late = _events(hub, run)[-1]
    assert late["action"] == "step.late" and late["data"]["why"] == "cancelled" and late["data"]["outcome"] == "done"
    # one still in the queue when its work is cancelled never starts
    run2, _ = _lab(hub, value="131", flag="abnormal")
    hub.engine.cancel(M(hub, "rana"), run2["id"], "filed twice")
    assert hub.engine.job_start(run2["id"], "check") is None and hub.engine.jobs() == []


def test_two_workers_never_start_one_job(hub):
    run, ref = _lab(hub)
    got = []
    threads = [threading.Thread(target=lambda: got.append(hub.engine.job_start(run["id"], "check"))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len([g for g in got if g]) == 1
    assert [e["action"] for e in _events(hub, run)].count("step.started") == 1


def test_a_python_job_cut_short_is_queued_again_once_and_a_late_answer_does_not_overwrite_it(hub):
    run, ref = _lab(hub)
    first = hub.engine.job_start(run["id"], "check")
    answer = hub.engine.job_call(first)
    hub.db.atomic(hub.engine._cut_short, run["id"], "check", first["token"])  # its worker is gone
    st = _state(hub, run)
    assert st["state"] == "queued" and st["attempt"] == 2
    assert _events(hub, run)[-1]["action"] == "step.requeued"
    second = hub.engine.job_start(run["id"], "check")
    hub.engine.job_finish(first, *answer)  # the first worker was only slow: its answer comes now
    st = _state(hub, run)
    assert st["state"] == "running" and st["job"] == second["token"]  # the second attempt stands
    late = _events(hub, run)[-1]
    assert late["action"] == "step.late" and late["data"]["why"] == "superseded"
    hub.db.atomic(hub.engine._cut_short, run["id"], "check", second["token"])  # cut short a second time: no third
    st = _state(hub, run)
    assert st["state"] == "failed" and st["interrupted"] and "cut short" in st["error"]


def test_the_tick_runs_a_job_no_worker_took_and_fails_one_that_ran_past_its_time(hub):
    run, ref = _lab(hub)
    hub.engine.tick()
    assert _state(hub, run)["state"] == "queued"  # just queued: a worker will take it
    row = hub.engine.run(run["id"])
    row["steps"]["check"]["queued_at"] = "2020-01-01T00:00:00Z"
    hub.engine._save(row)
    hub.engine.tick()
    assert _state(hub, run)["state"] == "done"  # no worker had: the tick ran it
    run2, _ = _lab(hub, value="131", flag="abnormal")
    job = hub.engine.job_start(run2["id"], "check")
    row = hub.engine.run(run2["id"])
    row["steps"]["check"].update(started_at="2020-01-01T00:00:00Z", engine="webhook")
    hub.engine._save(row)
    assert hub.engine.recover_jobs() == 1
    st = _state(hub, run2)
    assert st["state"] == "failed" and st["interrupted"] and "not known" in st["error"]
    assert hub.engine.jobs() == []  # an engine's job is never run again by itself
    hub.engine.job_finish(job, {"output": {}}, None, 5)  # and its answer, should it ever come, changes nothing
    assert _state(hub, run2)["state"] == "failed"


def test_a_caller_that_reads_what_a_block_found_runs_it_in_place(hub):
    """``Engine.jobs_inline``: the portal answers a patient from the screen of their message in the same request."""
    hub.jobs_async = True
    with hub.engine.jobs_inline():
        run = hub.engine.start(M(hub, "jo"), "appointment-request", record=_pat(hub),
                               inputs={"reason": "my knee hurts when I run", "kind": "new-concern"})
    row = hub.engine.run(run["id"])
    assert row["steps"]["screen"]["state"] == "done" and "queued_at" not in row["steps"]["screen"]
    assert row["ctx"]["screen"] and row["steps"]["triage"]["state"] == "active" and hub.engine.jobs() == []
    assert not hub.engine._in_background.__self__._local.inline  # and only there: the next start queues again


def test_without_a_worker_a_block_runs_where_it_becomes_ready(hub):
    assert hub.jobs_async is False
    run = hub.engine.start(M(hub, "lab-feed"), "lab-result-review", record=_pat(hub), inputs=dict(LAB))
    row = hub.engine.run(run["id"])
    assert row["steps"]["check"] == {"state": "done", "by": row["steps"]["check"]["by"]} and row.get("jobs") is None


def test_the_page_shows_a_job_in_the_queue_under_way_and_done(hub):
    """The record's journey and what its latest move set going both tell a job as it is."""
    run, ref = _lab(hub)
    amara = M(hub, "dr-amara")

    def check(j):
        return next(ln for st in j["stages"] for ln in st["lines"] if ln["kind"] == "work"
                    and ln["title"] == "Check the value against its range")
    j = hub.record_journey(amara, ref)
    assert check(j)["status"] == "queued" and j["busy"] and j["live"]
    token = j["token"]
    job = hub.engine.job_start(run["id"], "check")
    j = hub.record_journey(amara, ref)
    assert check(j)["status"] == "running" and j["busy"] and j["token"] != token
    hub.engine.job_finish(job, *hub.engine.job_call(job))
    j = hub.record_journey(amara, ref)
    assert check(j)["status"] == "done" and not j["busy"]
    # an edge's own block: the move is told while it is still under way
    hub.jobs_async = True
    run = hub.engine.start(M(hub, "jo"), "appointment-request", record=_pat(hub),
                           inputs={"reason": "my knee hurts when I run", "kind": "new-concern"})
    visit = hub.engine.run(run["id"])["record"]
    (move,) = hub.record_activity(amara, visit)
    assert [(a["title"], a["state"]) for a in move["ran"]][:2] == [
        ("Screen the reason for red flags", "queued"), ("Tell the patient what to do in an emergency", "held")]
    assert not move["settled"]
    asked = hub.record_journey(amara, visit)["stages"][0]["moves"][0]
    assert asked["status"] == "going" and asked["steps"][0]["status"] == "queued"
    hub.engine.run_jobs()
    (move,) = hub.record_activity(amara, visit)
    assert move["ran"][0]["state"] == "done" and move["ran"][0]["ms"] is not None


@pytest.mark.parametrize("who,sees", [("dr-amara", True), ("jo", False)])
def test_no_one_is_shown_which_worker_has_a_job(hub, who, sees):
    from practicehub import visibility
    run, ref = _lab(hub)
    hub.engine.job_start(run["id"], "check")
    row = hub.engine.run(run["id"])
    view = visibility.Viewer(hub, M(hub, who))
    shown = view.run_steps(view.workflow_of_run(run["id"]), row)
    assert ("check" in shown) is sees
    if sees:
        assert shown["check"]["state"] == "running" and "worker" not in shown["check"] and "job" not in shown["check"]
