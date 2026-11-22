"""Process analytics: medians, the bottleneck, missed deadlines and open work by age, for policy owners only, and
never a word about who the work is about."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import M
from practicehub import analytics
from practicehub.engine import _item_doc
from practicehub.hub import Denied
from practicehub.web.server import App

WF = "new-patient-intake"  # its steps: review ("Check the paperwork"), then the accept gate


def _z(t):
    return t.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _event(hub, n, action, at, run_id, subject=""):
    hub.db.events.insert({"_id": 10_000_000 + n, "seq": 10_000_000 + n, "at": _z(at), "actor": "hub",
                          "actor_kind": "automation", "action": action, "subject": subject, "run_id": run_id,
                          "cause": None, "data": {}, "prev": "", "hash": f"seeded-{n}", "signature": None, "mac": None})


def _seed(hub, now):
    """Three finished intakes (10h, 20h, 30h), one cancelled, one open and late, one from 40 days ago. The paperwork
    check waits 4h, 6h and 8h before someone takes it; the gate after it waits 10 minutes."""
    hub.db.runs.delete({})
    hub.db.items.delete({})
    patient = hub.records.search("Patient", limit=1)[0]
    ref = f"Patient/{patient['id']}"
    base = now - timedelta(days=5)
    version = hub.workflow(WF)[1]["version"]
    ev = iter(range(1, 1000))

    def run(rid, status, created, **extra):
        hub.db.runs.insert({"_id": rid, "id": rid, "workflow_id": WF, "version": version, "title": "New patient intake",
                            "record": ref, "status": status, "ctx": {"input": {"note": patient["id"]}}, "steps": {},
                            "started_by": "jo", "refs": [ref], "root_event": None, "created_at": _z(created),
                            "updated_at": _z(created), **extra})

    def step(iid, rid, step, created, *, claimed=None, done=None, due=None, escalated=0):
        """One step in the run's item's history (one item walks each run's steps)."""
        if claimed:
            _event(hub, next(ev), "item.claimed", claimed, rid, f"item:{iid}")
        return {"step": step, "title": step, "kind": "task", "role": None, "assignee": None,
                "started_at": _z(created), "taken_at": _z(claimed) if claimed else None,
                "done_at": _z(done) if done else None, "by": "jo" if done else None,
                "outcome": "done" if done else None, "due_at": _z(due) if due else None, "escalated": escalated,
                "item": iid}

    def item(iid, rid, *history):
        last = history[-1]
        hub.db.items.insert(_item_doc(hub, id=iid, run_id=rid, step_id=last["step"], kind="task",
                                      title=f"About {patient['id']}", status="done" if last["done_at"] else
                                      ("claimed" if last["taken_at"] else "open"), created_at=history[0]["started_at"],
                                      updated_at=last["done_at"] or last["taken_at"] or last["started_at"],
                                      step_started_at=last["started_at"], due_at=last["due_at"],
                                      escalated=last["escalated"], history=list(history)))

    for i, (hours, wait) in enumerate(((10, 4), (20, 6), (30, 8))):
        rid = f"run-seed{i}"
        extra = {"deadline_at": _z(base + timedelta(hours=5)), "deadline_missed_at": _z(base + timedelta(hours=5))} \
            if i == 0 else {}
        run(rid, "completed", base, **extra)
        late = i == 2
        iid = f"item-seed{i}"
        start = base + timedelta(hours=9)
        item(iid, rid,
             step(iid, rid, "review", base, claimed=base + timedelta(hours=wait),
                  done=base + timedelta(hours=wait + 1), due=base + timedelta(hours=2) if late else None,
                  escalated=1 if late else 0),
             step(iid, rid, "accept", start, claimed=start + timedelta(minutes=10),
                  done=start + timedelta(minutes=40)))
        _event(hub, next(ev), "run.completed", base + timedelta(hours=hours), rid)
    run("run-seedc", "cancelled", base)
    run("run-seedo", "running", base)
    item("item-seedo", "run-seedo", step("item-seedo", "run-seedo", "review", base, due=base + timedelta(days=3)))
    run("run-seedold", "completed", now - timedelta(days=40))
    hub.db.items.insert(_item_doc(hub, id="item-seedn", run_id="run-seedo", step_id="review", kind="notice",
                                  title=f"Overdue: {hub.title(patient)}", created_at=_z(base)))
    return patient


@pytest.fixture
def seeded(hub):
    now = datetime.now(timezone.utc)
    return hub, _seed(hub, now), now


def test_the_report_counts_runs_and_times_steps(seeded):
    hub, _, now = seeded
    rep = analytics.report(hub, M(hub, "rana"), days=30, now=now)
    (w,) = rep["workflows"]
    assert w["title"] == "New patient intake"
    assert (w["started"], w["finished"], w["cancelled"], w["open"]) == (5, 3, 1, 1)
    assert w["cycle_median"] == 20 * 3600 and w["cycle_p90"] == 30 * 3600
    review, accept = w["steps"]
    assert review["title"] == "Check the paperwork" and accept["title"] == "Accept the patient"
    assert review["count"] == 4  # the notice is not work
    assert review["wait_median"] == 6 * 3600 and review["work_median"] == 3600
    assert accept["wait_median"] == 600 and accept["work_median"] == 1800
    assert review["overdue"] == 2 and review["overdue_rate"] == 0.5  # one done late, one open past its due time
    assert review["escalations"] == 1 and accept["overdue"] == 0
    assert w["bottleneck"]["step"] == "review" and w["bottleneck"]["phase"] == "waiting"
    assert analytics.bottleneck_words(w["bottleneck"]) == \
        "Most time is spent waiting for ‘Check the paperwork’ to be taken: median 6h."
    assert (w["with_deadline"], w["deadline_missed"]) == (1, 1)
    assert w["open_now"] == 1 and [a["count"] for a in w["open_by_age"]] == [0, 0, 1, 0]
    assert analytics.report(hub, M(hub, "rana"), days=90, now=now)["workflows"][0]["started"] == 6
    assert analytics.report(hub, M(hub, "rana"), days=7, now=now)["workflows"][0]["started"] == 5
    capped = analytics.report(hub, M(hub, "rana"), days=30, now=now, max_runs=2)
    assert capped["capped"] and capped["runs"] == 2


def test_only_a_policy_owner_sees_it(seeded):
    hub, _, _ = seeded
    assert not hub.is_policy_owner(M(hub, "jo"))
    with pytest.raises(Denied):
        analytics.report(hub, M(hub, "jo"))
    app = App(hub, dev=True)
    jo, rana = {"cookie": "ph_as=jo"}, {"cookie": "ph_as=rana"}
    for path in ("/settings/analytics", "/api/analytics"):
        code, _, body = app.handle("GET", path, jo, b"")
        assert code == 403 and b"Check the paperwork" not in body
    code, _, body = app.handle("GET", "/settings", jo, b"")
    assert b"Process analytics" not in body
    code, _, body = app.handle("GET", "/settings", rana, b"")
    assert b"/settings/analytics" in body


def test_the_page_shows_the_bottleneck_and_nothing_about_anyone(seeded):
    hub, patient, _ = seeded
    app = App(hub, dev=True)
    rana = {"cookie": "ph_as=rana"}
    code, _, body = app.handle("GET", "/settings/analytics?days=30", rana, b"")
    page = body.decode()
    assert code == 200
    assert "Most time is spent waiting for ‘Check the paperwork’ to be taken: median 6h." in page
    assert 'aria-current=page>Last 30 days' in page
    code, _, api = app.handle("GET", "/api/analytics?days=90", rana, b"")
    assert code == 200 and json.loads(api)["days"] == 90
    for text in (page, api.decode()):
        assert hub.title(patient) not in text and patient["id"] not in text
        assert "run-seed" not in text and "item-seed" not in text
    main = page.split('<main id="main"', 1)[1].split("</main>", 1)[0]  # not the menus (the reader's own name)
    name = patient["name"][0]
    for part in [*name.get("given", []), name.get("family", "")]:
        assert part and part not in main


def test_the_command_line_prints_the_same(seeded):
    hub, _, now = seeded
    text = "\n".join(analytics.lines(analytics.report(hub, M(hub, "rana"), now=now)))
    assert "New patient intake" in text and "3 finished" in text and "median 20h" in text
    assert "waiting for ‘Check the paperwork’" in text
