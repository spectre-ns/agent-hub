"""Time in workflows: waits on a duration or a moment, waits that time out, run deadlines, and richer schedules.
Every time is the practice's (America/Halifax in the demo pack), counted in real time from instants."""

import re
from datetime import datetime, timedelta, timezone

import pytest

from conftest import M, item_for
from practicehub.engine import _slot
from practicehub.workflows import DefinitionError, parse


def _utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


TOP = 'id = "t"\ntitle = "T"\n'
STEP = '[[steps]]\nid = "w"\ntitle = "W"\nkind = "wait"\n'
HEAD = TOP + STEP


def _problems(src):
    with pytest.raises(DefinitionError) as exc:
        parse(src)
    return exc.value.problems


def _install(hub, src):
    wid = src.split('"')[1]
    p = hub.propose_workflow(M(hub, "jo"), src, "timing")
    hub.approve_workflow(M(hub, "rana"), wid, p["version"])
    return wid


def _wf(wid, steps, *, extra="", inputs=""):
    return (f'id = "{wid}"\ntitle = "Timing check"\n[trigger]\nstarters = ["front-desk"]\n{extra}{inputs}{steps}')


TASK = '[[steps]]\nid = "{id}"\ntitle = "{title}"\nkind = "task"\nrole = "front-desk"\nafter = ["{after}"]\n{when}' \
       '[[steps.fields]]\nname = "done"\ntype = "boolean"\n'


def _task(sid, after, when=""):
    return TASK.format(id=sid, title=sid.title(), after=after, when=f'when = "{when}"\n' if when else "")


def _state(hub, run, sid):
    return hub.engine.run(run["id"])["steps"][sid]["state"]


# ------------------------------------------------------------------------------------------------ definitions
def test_a_wait_waits_for_one_thing_and_says_how():
    assert parse(HEAD + 'for = "3d"\n').steps[0].wait_for == "3d"
    assert parse(HEAD + 'at = "{{input.appointment}} - 1d"\n').steps[0].at == "{{input.appointment}} - 1d"
    assert parse(HEAD + 'until = "false"\ntimeout = "2d"\n').steps[0].timeout == "2d"
    assert any("a wait waits for one of until, for or at" in p for p in _problems(HEAD + 'for = "1d"\nat = "{{x}}"\n'))
    assert any("for is how long it waits" in p for p in _problems(HEAD + 'for = "soon"\n'))
    assert any("at is one placeholder" in p for p in _problems(HEAD + 'at = "tomorrow"\n'))
    assert any("at is one placeholder" in p for p in _problems(HEAD + 'at = "{{input.x}} - 1 day"\n'))
    assert any("timeout ends a wait on until" in p for p in _problems(HEAD + 'for = "1d"\ntimeout = "2d"\n'))
    assert any("timeout is how long" in p for p in _problems(HEAD + 'until = "false"\ntimeout = "a while"\n'))
    task = 'id = "t"\ntitle = "T"\n[[steps]]\nid = "a"\ntitle = "A"\nkind = "task"\nrole = "r"\nfor = "1d"\n' \
           '[[steps.fields]]\nname = "x"\ntype = "string"\n'
    assert any("for is for wait steps" in p for p in _problems(task))


def test_a_deadline_is_a_duration_and_a_role():
    src = STEP + 'for = "1d"\n'
    wf = parse(TOP + '[deadline]\nwithin = "5d"\nescalate_to = "practice-manager"\n' + src)
    assert (wf.deadline, wf.deadline_escalate_to) == ("5d", "practice-manager")
    assert parse(TOP + '[deadline]\n' + src).deadline is None  # an empty table (the diagram's, say) is no deadline
    assert any("within is how long a run may take" in p for p in _problems(TOP + '[deadline]\nwithin = "soon"\n' + src))
    assert any("deadline: takes within" in p for p in _problems(TOP + '[deadline]\nwithin = "1d"\nby = "x"\n' + src))


@pytest.mark.parametrize("schedule", ["hourly", "daily 07:00", "weekly mon 08:00", "weekdays 07:30",
                                      "weekly mon,wed,fri 08:00", "monthly 1 09:00", "monthly 28 09:00",
                                      "monthly last 17:00", "every 15m", "every 2h", "every 24h"])
def test_schedules_the_clock_knows(schedule):
    assert parse(TOP + f'[trigger]\nschedule = "{schedule}"\n' + STEP + 'for = "1d"\n').schedule == schedule


@pytest.mark.parametrize("schedule", ["every 10m", "every 25h", "monthly 29 09:00", "monthly 0 09:00",
                                      "weekly mon,xyz 08:00", "daily 25:00", "weekdays", "fortnightly 08:00"])
def test_schedules_the_clock_does_not_know_are_refused_in_words(schedule):
    probs = _problems(TOP + f'[trigger]\nschedule = "{schedule}"\n' + STEP + 'for = "1d"\n')
    assert any(p.startswith("trigger.schedule: say 'hourly', 'every 15m'") and "monthly 1 09:00" in p for p in probs)


def test_each_schedule_fires_once_per_slot():
    mon = datetime(2026, 11, 23, 8, 0)  # a Monday
    assert _slot("weekdays 07:30", mon) == "2026-11-23"
    assert _slot("weekdays 07:30", mon.replace(hour=7)) is None
    assert _slot("weekdays 07:30", mon + timedelta(days=5)) is None             # Saturday
    assert _slot("weekly mon,wed,fri 08:00", mon) == "2026-11-23"
    assert _slot("weekly mon,wed,fri 08:00", mon + timedelta(days=1)) is None   # Tuesday
    assert _slot("weekly mon,wed,fri 08:00", mon + timedelta(days=2)) == "2026-11-25"
    assert _slot("weekly mon 08:00", mon) == "2026-W48"                        # the old form keeps its label
    assert _slot("monthly 23 07:00", mon) == "2026-11"
    assert _slot("monthly 22 07:00", mon) is None
    assert _slot("monthly last 07:00", datetime(2026, 11, 30, 7, 0)) == "2026-11"
    assert _slot("monthly last 07:00", datetime(2026, 2, 28, 7, 0)) == "2026-02"
    assert _slot("monthly last 07:00", datetime(2026, 11, 29, 7, 0)) is None
    assert _slot("every 15m", datetime(2026, 11, 23, 10, 14)) == "2026-11-23T10:00"
    assert _slot("every 15m", datetime(2026, 11, 23, 10, 15)) == "2026-11-23T10:15"
    assert _slot("every 2h", datetime(2026, 11, 23, 13, 59)) == "2026-11-23T12:00"
    assert _slot("daily 07:00", mon) == "2026-11-23" and _slot("hourly", mon) == "2026-11-23T08"


def test_a_schedule_every_15_minutes_starts_one_run_per_stretch(hub):
    wid = _install(hub, 'id = "pulse"\ntitle = "Pulse"\n[trigger]\nschedule = "every 15m"\n'
                        '[[steps]]\nid = "w"\ntitle = "Pause"\nkind = "wait"\nfor = "1h"\n')
    runs = lambda: hub.db.runs.count({"workflow_id": wid})
    hub.engine.tick(datetime(2026, 11, 23, 10, 1))
    assert runs() == 1
    hub.engine.tick(datetime(2026, 11, 23, 10, 14))
    assert runs() == 1
    hub.engine.tick(datetime(2026, 11, 23, 10, 16))
    assert runs() == 2


# ------------------------------------------------------------------------------------------------ timer waits
def test_a_wait_for_a_duration_ends_on_the_tick_after_it(hub):
    """A wait for 3 days started at 20:30 in Halifax (00:30 UTC) ends at 00:30 UTC three days on, by the tick."""
    hub.engine._now = _utc(2026, 11, 23, 0, 30)
    wid = _install(hub, _wf("cool-off", '[[steps]]\nid = "w"\ntitle = "Cool off"\nkind = "wait"\nfor = "3d"\n'
                            + _task("call", "w")))
    run = hub.engine.start(M(hub, "jo"), wid)
    st = hub.engine.run(run["id"])["steps"]["w"]
    assert st["state"] == "waiting" and st["wake_at"] == "2026-11-26T00:30:00Z"
    hub.engine.tick(_utc(2026, 11, 26, 0, 29))
    assert _state(hub, run, "w") == "waiting"
    hub.engine.tick(_utc(2026, 11, 26, 0, 31))   # within the 15 minutes the tick otherwise waits between checks
    assert _state(hub, run, "w") == "done" and _state(hub, run, "call") == "active"
    assert hub.db.events.count({"action": "step.done", "run_id": run["id"], "data.step": "w"}) == 1


def test_a_wait_at_a_moment_reads_the_run_and_the_practices_zone(hub):
    """``at = "{{input.appointment}} - 1d"``: the day before the appointment, at the appointment's wall time in
    Halifax (an appointment at 09:30 on 1 Dec is 13:30 UTC; the wait ends at 13:30 UTC on 30 Nov)."""
    hub.engine._now = _utc(2026, 11, 23, 12, 0)
    wid = _install(hub, _wf("remind", '[[steps]]\nid = "w"\ntitle = "Until the day before"\nkind = "wait"\n'
                            'at = "{{input.appointment}} - 1d"\n' + _task("remind", "w"),
                            inputs='[[input]]\nname = "appointment"\ntype = "datetime"\nrequired = true\n'))
    run = hub.engine.start(M(hub, "jo"), wid, inputs={"appointment": "2026-12-01T09:30"})
    assert hub.engine.run(run["id"])["steps"]["w"]["wake_at"] == "2026-11-30T13:30:00Z"
    hub.engine.tick(_utc(2026, 11, 30, 13, 29))
    assert _state(hub, run, "w") == "waiting"
    hub.engine.tick(datetime(2026, 11, 30, 9, 30))  # a naive time is Halifax's wall time
    assert _state(hub, run, "w") == "done" and _state(hub, run, "remind") == "active"


def test_a_wait_at_a_date_ends_at_its_midnight_in_the_practice(hub):
    hub.engine._now = _utc(2026, 11, 23, 12, 0)
    wid = _install(hub, _wf("on-day", '[[steps]]\nid = "w"\ntitle = "Until the day"\nkind = "wait"\n'
                            'at = "{{input.day}} + 2h"\n',
                            inputs='[[input]]\nname = "day"\ntype = "date"\nrequired = true\n'))
    run = hub.engine.start(M(hub, "jo"), wid, inputs={"day": "2026-12-01"})
    assert hub.engine.run(run["id"])["steps"]["w"]["wake_at"] == "2026-12-01T06:00:00Z"  # 02:00 AST


def test_a_wait_at_a_moment_that_is_not_a_time_fails_and_asks_a_person(hub):
    hub.engine._now = _utc(2026, 11, 23, 12, 0)
    wid = _install(hub, _wf("bad-at", '[[steps]]\nid = "w"\ntitle = "Until then"\nkind = "wait"\n'
                            'at = "{{input.note}}"\n', inputs='[[input]]\nname = "note"\ntype = "string"\n'))
    run = hub.engine.start(M(hub, "jo"), wid, inputs={"note": "after lunch"})
    st = hub.engine.run(run["id"])["steps"]["w"]
    assert st["state"] == "failed" and "not a date or a time" in st["error"]


# ------------------------------------------------------------------------------------------------ timeouts
CHASE = ('[[steps]]\nid = "chase"\ntitle = "Wait for the form"\nkind = "wait"\nuntil = "input.ready == true"\n'
         'timeout = "2d"\n' + _task("call", "chase", "chase.timed_out") + _task("file", "chase", "not chase.timed_out"))
READY = '[[input]]\nname = "ready"\ntype = "boolean"\n'


def test_a_wait_that_times_out_ends_as_timed_out_and_later_steps_branch_on_it(hub):
    hub.engine._now = _utc(2026, 11, 23, 12, 0)
    wid = _install(hub, _wf("chase", CHASE, inputs=READY))
    run = hub.engine.start(M(hub, "jo"), wid, inputs={"ready": False})
    st = hub.engine.run(run["id"])["steps"]["chase"]
    assert st["state"] == "waiting" and st["timeout_at"] == "2026-11-25T12:00:00Z"
    hub.engine.tick(_utc(2026, 11, 25, 11, 59))
    assert _state(hub, run, "chase") == "waiting"
    hub.engine.tick(_utc(2026, 11, 25, 12, 1))
    after = hub.engine.run(run["id"])
    assert after["steps"]["chase"]["state"] == "done" and after["ctx"]["chase"]["timed_out"] is True
    assert (after["steps"]["call"]["state"], after["steps"]["file"]["state"]) == ("active", "skipped")
    assert hub.db.events.count({"action": "step.timed_out", "run_id": run["id"]}) == 1


def test_a_wait_that_ends_in_time_has_not_timed_out(hub):
    wid = _install(hub, _wf("chase", CHASE, inputs=READY))
    run = hub.engine.start(M(hub, "jo"), wid, inputs={"ready": True})
    after = hub.engine.run(run["id"])
    assert after["ctx"]["chase"]["timed_out"] is False
    assert (after["steps"]["call"]["state"], after["steps"]["file"]["state"]) == ("skipped", "active")


# ------------------------------------------------------------------------------------------------ run deadlines
DEADLINE = '[deadline]\nwithin = "1d"\nescalate_to = "practice-manager"\n'
ONE_TASK = ('[[steps]]\nid = "do"\ntitle = "Do it"\nkind = "task"\nrole = "front-desk"\n'
            '[[steps.fields]]\nname = "done"\ntype = "boolean"\n')


def test_a_run_past_its_deadline_is_flagged_once_and_counted(hub):
    hub.engine._now = _utc(2026, 11, 23, 12, 0)
    wid = _install(hub, _wf("sla", ONE_TASK, extra=DEADLINE))
    run = hub.engine.start(M(hub, "jo"), wid)
    row = hub.db.runs.get(run["id"])
    assert row["deadline_at"] == "2026-11-24T12:00:00Z" and row["deadline_missed_at"] is None
    assert run["id"] not in hub.engine.tick(_utc(2026, 11, 24, 11, 59))["deadlines_missed"]
    assert run["id"] in hub.engine.tick(_utc(2026, 11, 24, 12, 1))["deadlines_missed"]
    assert hub.db.runs.get(run["id"])["deadline_missed_at"] == "2026-11-24T12:01:00Z"
    assert hub.db.events.count({"action": "run.deadline_missed", "run_id": run["id"]}) == 1
    notes = list(hub.db.items.find({"run_id": run["id"], "kind": "notice", "role": "practice-manager"}))
    assert len(notes) == 1 and notes[0]["title"].startswith("Not done in time: Timing check")
    assert run["id"] not in hub.engine.tick(_utc(2026, 11, 25, 12, 0))["deadlines_missed"]  # once
    assert hub.db.events.count({"action": "run.deadline_missed", "run_id": run["id"]}) == 1


def test_a_run_finished_late_before_a_tick_still_counts_as_late(hub):
    hub.engine._now = _utc(2026, 11, 23, 12, 0)
    wid = _install(hub, _wf("sla", ONE_TASK, extra=DEADLINE))
    jo = M(hub, "jo")
    run = hub.engine.start(jo, wid)
    hub.engine._now = _utc(2026, 11, 25, 12, 0)
    iid = item_for(hub, run, "do")
    hub.engine.claim(jo, iid)
    hub.engine.complete(jo, iid, {"done": True})
    row = hub.db.runs.get(run["id"])
    assert row["status"] == "completed" and row["deadline_missed_at"] == "2026-11-25T12:00:00Z"


def test_a_run_done_in_time_is_not_late(hub):
    hub.engine._now = _utc(2026, 11, 23, 12, 0)
    wid = _install(hub, _wf("sla", ONE_TASK, extra=DEADLINE))
    jo = M(hub, "jo")
    run = hub.engine.start(jo, wid)
    iid = item_for(hub, run, "do")
    hub.engine.claim(jo, iid)
    hub.engine.complete(jo, iid, {"done": True})
    assert hub.db.runs.get(run["id"])["deadline_missed_at"] is None
    assert run["id"] not in hub.engine.tick(_utc(2026, 11, 30, 12, 0))["deadlines_missed"]


def test_a_deadline_names_a_role_the_practice_has(hub):
    with pytest.raises(Exception) as exc:
        hub.propose_workflow(M(hub, "jo"), _wf("sla", ONE_TASK, extra='[deadline]\nwithin = "1d"\n'
                                                                       'escalate_to = "nobody-here"\n'), "x")
    assert "deadline: there is no role 'nobody-here'" in str(getattr(exc.value, "problems", "")) + str(exc.value)


# ------------------------------------------------------------------------------------------------ on the page
def test_the_workflow_page_shows_its_schedule_deadline_and_timers_in_words(hub):
    import threading
    from http.server import ThreadingHTTPServer
    from test_web import call, token
    from practicehub.web.server import App, make_handler
    src = ('id = "timed"\ntitle = "Timed"\n[trigger]\nstarters = ["front-desk"]\nschedule = "weekly mon,wed,fri 08:00"\n'
           + DEADLINE + '[[steps]]\nid = "chase"\ntitle = "Wait for the form"\nkind = "wait"\nuntil = "false"\n'
           'timeout = "2d"\n[[steps]]\nid = "pause"\ntitle = "Pause"\nkind = "wait"\nfor = "3d"\nafter = ["chase"]\n')
    wid = _install(hub, src)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(hub, dev=False)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        code, page = call(f"http://127.0.0.1:{httpd.server_port}", "GET", f"/workflows/{wid}", token(hub, "rana"))
    finally:
        httpd.shutdown()
    assert code == 200
    assert "every Monday, Wednesday and Friday at 08:00" in page
    assert re.search(r"done within 1 day; if not, [A-Z][a-z ]+ is told", page)
    assert "gives up after 2 days" in page and "waits 3 days" in page
