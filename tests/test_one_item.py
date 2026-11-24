"""One item walks each run's workflow: one key and one history from start to finish, reassigned to each step's
role; steps side by side (and a step once per person) are child items that close back into it; between people's
steps it says what it waits on and is in nobody's to-do; open runs from before are folded into one item."""

import pytest

from conftest import M, item_for, one_item_per_run
from practicehub.engine import _item_doc
from practicehub.hub import Denied
from practicehub.store import utcnow

FIELD = '[[state.work.fields]]\nname = "done"\ntype = "boolean"\n'


def _task(sid, title, extra=""):
    return f'[[state.work]]\nid = "{sid}"\ntitle = "{title}"\nkind = "task"\nrole = "physician"\n{extra}{FIELD}'


def _walk(*stops):
    """The record stops at each in turn (one state each; several works on one are done side by side), then is done."""
    out = '[start]\nto = "s1"\n'
    for i, works in enumerate(stops, 1):
        nxt = f"s{i + 1}" if i < len(stops) else "done"
        out += f'[[state]]\nid = "s{i}"\n{works}[[edge]]\nid = "on-{i}"\nfrom = "s{i}"\nto = "{nxt}"\n'
    return out + '[[state]]\nid = "done"\n'


def _install(hub, wid, steps, inputs=""):
    src = f'id = "{wid}"\ntitle = "Walk check"\nrecord = "Patient"\n[trigger]\nstarters = ["front-desk"]\n{inputs}{steps}'
    p = hub.propose_workflow(M(hub, "jo"), src, "one item")
    hub.approve_workflow(M(hub, "rana"), wid, p["version"])
    return wid


def _pat(hub):
    """What the run is about: a Patient has no lifecycle, so the run keeps where it is itself."""
    return "Patient/" + hub.records.search("Patient", limit=1)[0]["id"]


def _people(item):
    """An item's history without the moves along the edges (``<edge>--to``, the workflow's)."""
    return [h for h in item["history"] if "--" not in h["step"]]


def _item(hub, iid):
    return hub.engine.item(iid)


def test_one_item_walks_the_steps_keeping_its_key_and_history(hub):
    wid = _install(hub, "walk", _walk(_task("a", "Write it"), _task("b", "Check it", 'not_by = ["a"]\n')))
    run = hub.engine.start(M(hub, "jo"), wid, record=_pat(hub))
    first = item_for(hub, run, "a")
    assert first == hub.engine.run(run["id"])["item"]
    it = _item(hub, first)
    assert it["title"] == run["title"] and it["step_title"] == "Write it" and it["role"] == "physician"
    amara, liam = M(hub, "dr-amara"), M(hub, "dr-liam")
    hub.engine.claim(amara, first)
    hub.engine.complete(amara, first, {"done": True})
    assert item_for(hub, run, "b") == first                 # the same item moved on, not a new one per step
    it = _item(hub, first)
    assert it["step_title"] == "Check it" and it["claimed_by"] is None and it["status"] == "open"
    # the record's moves along the edges are in it too, done by the workflow
    assert [h["step"] for h in it["history"]] == ["start--to", "a", "on-1--to", "b"]
    assert [(h["step"], h["by"]) for h in _people(it)] == [("a", "dr-amara"), ("b", None)]
    assert _people(it)[0]["taken_at"] and _people(it)[0]["done_at"]
    assert not hub.engine.eligibility(amara, it)[0]          # separation of duties, from the item's history
    hub.engine.claim(liam, first)
    hub.engine.complete(liam, first, {"done": True})
    it = _item(hub, first)
    assert it["status"] == "done" and hub.engine.run(run["id"])["status"] == "completed"
    assert [h["by"] for h in _people(it)] == ["dr-amara", "dr-liam"]
    assert hub.db.items.count({"run_id": run["id"], "kind": {"$nin": ["notice", "hold"]}}) == 1


def test_separation_of_duties_survives_a_reassignment(hub):
    """Whoever did the first step does not check it, even when it was taken over from someone else first."""
    wid = _install(hub, "walk-sod", _walk(_task("a", "Write it"), _task("b", "Check it", 'not_by = ["a"]\n')))
    run = hub.engine.start(M(hub, "jo"), wid, record=_pat(hub))
    iid = item_for(hub, run, "a")
    amara, liam = M(hub, "dr-amara"), M(hub, "dr-liam")
    hub.engine.claim(amara, iid)
    hub.engine.take_over(M(hub, "rana"), iid, "away today", to="dr-liam")
    hub.engine.complete(liam, iid, {"done": True})
    assert not hub.engine.eligibility(liam, _item(hub, iid))[0]
    assert hub.engine.eligibility(amara, _item(hub, iid))[0]


def test_steps_side_by_side_are_child_items_and_the_item_waits_on_them(hub):
    wid = _install(hub, "walk-par", _walk(_task("a", "Start"), _task("b", "Left") + _task("c", "Right"),
                                          _task("d", "Join")))
    run = hub.engine.start(M(hub, "jo"), wid, record=_pat(hub))
    top = item_for(hub, run, "a")
    amara = M(hub, "dr-amara")
    hub.engine.complete(amara, top, {"done": True})
    parent = _item(hub, top)
    assert parent["status"] == "waiting" and parent["waiting"] == "waiting on 2 of 2"
    assert not hub.engine.eligibility(amara, parent)[0]
    assert top not in [i["id"] for i in hub.engine.queue(amara)]          # in nobody's to-do while it waits
    left, right = item_for(hub, run, "b"), item_for(hub, run, "c")
    kids = [_item(hub, left), _item(hub, right)]
    assert all(k["parent_id"] == top and k["title"] == run["title"] for k in kids)
    assert set(parent["children"]) == {left, right} and left != right != top
    hub.engine.complete(amara, left, {"done": True})
    assert _item(hub, top)["waiting"] == "waiting on 1 of 2" and _item(hub, left)["status"] == "done"
    hub.engine.complete(amara, right, {"done": True})
    assert item_for(hub, run, "d") == top                                  # back on the run's item for the join
    assert [h["step"] for h in _people(_item(hub, top))][:1] == ["a"]
    assert {"b", "c"} <= {h["step"] for h in _item(hub, top)["history"]}  # the children's steps closed back in
    one_item_per_run(hub.db.db)


def test_a_task_once_per_person_is_a_child_item_each(hub):
    wid = _install(hub, "walk-each", _walk(_task("a", "Each look", extra='for_each = "{{input.who}}"\n')),
                   inputs='[[input]]\nname = "who"\ntype = "list"\n')
    run = hub.engine.start(M(hub, "jo"), wid, record=_pat(hub), inputs={"who": ["dr-amara", "dr-liam"]})
    top = hub.engine.run(run["id"])["item"]
    kids = hub.db.items.find({"parent_id": top}, sort="n")
    assert sorted(k["assignee"] for k in kids) == ["dr-amara", "dr-liam"]
    assert _item(hub, top)["waiting"] == "waiting on 2 of 2"
    for k in kids:
        hub.engine.complete(M(hub, k["assignee"]), k["_id"], {"done": True})
    run = hub.engine.run(run["id"])
    assert run["status"] == "completed"
    assert sorted(x["person"] for x in run["ctx"]["a"]["each"]) == ["dr-amara", "dr-liam"]


def test_a_wait_shows_on_the_item_and_is_in_no_ones_list(hub):
    wid = _install(hub, "walk-wait", _walk(_task("a", "Before"),
                                          '[[state.work]]\nid = "w"\ntitle = "Cool off"\nkind = "wait"\nfor = "2d"\n',
                                          _task("b", "After")))
    run = hub.engine.start(M(hub, "jo"), wid, record=_pat(hub))
    iid = item_for(hub, run, "a")
    hub.engine.complete(M(hub, "dr-amara"), iid, {"done": True})
    it = _item(hub, iid)
    assert it["status"] == "waiting" and it["waiting"].startswith("waiting until ") and it["step_title"] == "Cool off"
    assert it["role"] is None and it["claimed_by"] is None
    with pytest.raises(Denied):
        hub.engine.claim(M(hub, "dr-liam"), iid)


# ------------------------------------------------------------------------------------------------ the upgrade
def _legacy(hub, wid):
    """A run as the engine left it before: an item per step, the first done, the second open."""
    run = hub.engine.start(M(hub, "jo"), wid, record=_pat(hub))
    hub.db.runs.update({"_id": run["id"]}, {"$unset": {"item": ""}})
    hub.db.items.delete({"run_id": run["id"]})
    now = utcnow()
    first = _item_doc(hub, id="item-old1", run_id=run["id"], step_id="a", kind="task", title="Write it",
                      status="done", claimed_by="dr-amara", role="physician", created_at=now, updated_at=now)
    second = _item_doc(hub, id="item-old2", run_id=run["id"], step_id="b", kind="task", title="Check it",
                       status="open", role="physician", created_at=now, updated_at=now)
    for doc in (first, second):
        doc.pop("history", None), doc.pop("parent_id", None), doc.pop("children", None)
        hub.db.items.insert(doc)
    steps = hub.engine.run(run["id"])["steps"]
    steps["a"] = {"state": "done", "item": "item-old1", "by": "dr-amara"}
    steps["b"] = {"state": "active", "item": "item-old2"}
    hub.db.runs.set(run["id"], {"steps": steps, "ctx": {**hub.engine.run(run["id"])["ctx"],
                                                         "a": {"done": True, "by": "dr-amara"}}})
    return run


def test_open_runs_from_before_fold_into_their_earliest_item_once(hub):
    wid = _install(hub, "walk-old", _walk(_task("a", "Write it"), _task("b", "Check it", 'not_by = ["a"]\n')))
    run = _legacy(hub, wid)
    key = hub.db.items.get("item-old1")["n"]
    assert hub.engine.fold_run_items() == [run["id"]]
    kept = _item(hub, "item-old1")
    assert kept["n"] == key and kept["status"] == "open" and kept["step_id"] == "b"   # the earliest key, now on b
    assert [(h["step"], h["by"]) for h in kept["history"]] == [("a", "dr-amara"), ("b", None)]
    assert hub.db.items.get("item-old2")["status"] == "folded"
    assert hub.db.items.get("item-old2")["folded_into"] == "item-old1"
    assert hub.engine.run(run["id"])["item"] == "item-old1"
    assert hub.engine.run(run["id"])["steps"]["b"]["item"] == "item-old1"
    assert not hub.engine.eligibility(M(hub, "dr-amara"), kept)[0]     # the history still keeps duties apart
    assert hub.engine.fold_run_items() == []                             # again: nothing to do
    assert _item(hub, "item-old1")["history"] == kept["history"]
    one_item_per_run(hub.db.db)
    hub.engine.complete(M(hub, "dr-liam"), "item-old1", {"done": True})
    assert hub.engine.run(run["id"])["status"] == "completed"


def test_the_hub_folds_open_runs_when_it_opens(hub, monkeypatch):
    from practicehub.hub import Hub
    wid = _install(hub, "walk-open", _walk(_task("a", "Write it"), _task("b", "Check it")))
    run = _legacy(hub, wid)
    import conftest
    conftest._TEST["next"] = hub.db.name  # the same practice, opened again
    again = Hub(None, pack="general-practice")
    assert again.engine.run(run["id"])["item"] == "item-old1"
    assert again.db.items.get("item-old2")["status"] == "folded"


def test_a_run_at_a_step_its_version_no_longer_has_still_folds_and_a_failure_is_tried_again(hub, monkeypatch):
    """Found on the demo hub: open runs sat at a step their workflow version does not define, and the fold stopped
    on it. The item keeps its own title; a run whose fold did fail is folded at the next start."""
    wid = _install(hub, "walk-gone", _walk(_task("a", "Write it"), _task("b", "Check it")))
    run = _legacy(hub, wid)
    hub.db.items.set("item-old2", {"step_id": "gone", "title": "A step since removed"})
    real = hub.engine._fold
    monkeypatch.setattr(hub.engine, "_fold", lambda r: (_ for _ in ()).throw(RuntimeError("boom")))
    assert hub.engine.fold_run_items() == []
    assert hub.engine.run(run["id"])["item"] is None and hub.engine.run(run["id"])["fold_failed"]
    monkeypatch.setattr(hub.engine, "_fold", real)
    from practicehub.hub import Hub
    import conftest
    conftest._TEST["next"] = hub.db.name  # the same practice, opened again: it tries the failed fold again
    again = Hub(None, pack="general-practice")
    folded = again.engine.run(run["id"])
    assert folded["item"] == "item-old1" and "fold_failed" not in folded
    assert [h["title"] for h in _item(hub, "item-old1")["history"]][-1] == "A step since removed"
