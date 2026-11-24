"""The storage layer on MongoDB: nested blocks undo only themselves, refusals survive, files round-trip."""

import pytest

from practicehub.store import DuplicateKey

pytestmark = pytest.mark.own_shapes  # the store is tried with documents made for the trial, not the hub's


def test_an_inner_block_that_fails_is_undone_and_the_work_around_it_stands(store):
    store.items.insert({"_id": "keep", "n": 1})
    with store:
        store.items.insert({"_id": "a", "n": 1})
        with pytest.raises(RuntimeError):
            with store:
                store.items.insert({"_id": "b", "n": 1})
                store.items.update({"_id": "a"}, {"$inc": {"n": 10}})
                store.items.delete({"_id": "keep"})
                store.items.replace({"_id": "c", "n": 3})
                store.next_seq("ev")
                assert store.items.get("a")["n"] == 11  # an inner block reads its own writes
                raise RuntimeError("step failed")
        store.items.update({"_id": "a"}, {"$set": {"after": True}})
    assert sorted(d["_id"] for d in store.items.find()) == ["a", "keep"]
    assert store.items.get("a") == {"_id": "a", "n": 1, "after": True}
    assert store.next_seq("ev") == 1


def test_a_failed_outer_block_leaves_nothing_but_what_must_survive(store):
    with pytest.raises(ValueError):
        with store:
            store.items.insert({"_id": "x"})
            with store:
                store.surviving(lambda: store.events.insert({"_id": store.next_seq("ev"), "what": "refused"}))
            raise ValueError("refused")
    assert store.items.count() == 0
    assert [e["what"] for e in store.events.find()] == ["refused"]


def test_inner_blocks_that_succeed_are_undone_with_the_block_around_them(store):
    with store:
        with pytest.raises(KeyError):
            with store:
                with store:
                    store.items.insert({"_id": "deep"})
                raise KeyError("outer inner")
        assert store.items.get("deep") is None
        store.items.insert({"_id": "ok"})
    assert [d["_id"] for d in store.items.find()] == ["ok"]


def test_queries_sort_page_count_and_compare_and_set(store):
    for i, (t, w) in enumerate([("P", ["helene", "cote"]), ("P", ["ellen"]), ("Q", ["helene"])]):
        store.recs.insert({"_id": f"r{i}", "type": t, "words": w, "v": 1, "at": f"2026-01-0{i + 1}"})
    assert [d["_id"] for d in store.recs.find({"words": "helene"}, sort=[("at", -1)])] == ["r2", "r0"]
    assert [d["_id"] for d in store.recs.find({"words": {"$all": ["cote", "helene"]}})] == ["r0"]
    assert store.recs.count({"type": "P"}) == 2
    assert store.recs.distinct("type") == ["P", "Q"]
    assert store.recs.max("at") == "2026-01-03"
    assert [d["_id"] for d in store.recs.find(sort="at", skip=1, limit=1)] == ["r1"]
    assert store.recs.find_one_and_update({"_id": "r0", "v": 1}, {"$set": {"v": 2}})["v"] == 2
    assert store.recs.find_one_and_update({"_id": "r0", "v": 1}, {"$set": {"v": 3}}) is None
    with pytest.raises(DuplicateKey):
        store.recs.insert({"_id": "r0"})


def test_files_round_trip_through_gridfs(store):
    store.put_file("abc", b"x" * 300_000, {"content_type": "application/pdf"})
    store.put_file("abc", b"ignored", {})
    data, meta = store.get_file("abc")
    assert len(data) == 300_000 and meta["content_type"] == "application/pdf"
    assert store.get_file("nope") is None


# ------------------------------------------------------------------------------------------- two writers at once
def _track(store):
    from practicehub.track import Track
    return Track(store, "test-secret")


def test_two_stores_on_one_database_both_write_and_the_track_never_forks(store):
    """A CLI export (store A, a transaction appending reads) and the server (store B, a booking) at once: B waits for
    A's transaction instead of failing on the track's next number, both commit, and the chain verifies."""
    import threading
    import time
    from practicehub.store import Store
    other = Store(store.uri, store.name)
    a, b = _track(store), _track(other)
    a.append({"id": "rana", "kind": "person"}, "start", "x")
    done, errors = threading.Event(), []

    def server():
        try:
            with other:
                other.bookings.insert({"_id": "v1"})
                b.append({"id": "jo", "kind": "person"}, "record.created", "Encounter/v1")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        done.set()
    with store:  # the export's batch, open while the server writes
        for i in range(5):
            a.append({"id": "rana", "kind": "person"}, "record.viewed", f"Patient/{i}")
        t = threading.Thread(target=server)
        t.start()
        time.sleep(0.3)
        assert not done.is_set()  # it waits for the lease, it does not fail
    t.join(10)
    assert not errors and other.bookings.get("v1")
    assert [e["seq"] for e in store.events.find(sort="seq")] == list(range(1, 8))
    assert store.events.find_one(sort=[("seq", -1)])["action"] == "record.created"
    assert a.verify()["ok"]


def test_two_processes_append_at_once_and_every_event_is_kept(store, tmp_path):
    """A second process (a CLI command) appends in batched transactions while this one appends one at a time."""
    import subprocess
    import sys
    from pathlib import Path
    script = tmp_path / "writer.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})\n"
        "from practicehub.store import Store\nfrom practicehub.track import Track\n"
        "s = Store(sys.argv[1], sys.argv[2]); t = Track(s, 'test-secret')\n"
        "def batch(i):\n"
        "    for j in range(20):\n"
        "        t.append({'id': 'cli', 'kind': 'person'}, 'record.viewed', f'P/{i}-{j}')\n"
        "for i in range(10):\n"
        "    s.atomic(batch, i)\n")
    proc = subprocess.Popen([sys.executable, str(script), store.uri, store.name])
    t = _track(store)
    mine = 0
    while proc.poll() is None or mine < 30:
        t.append({"id": "jo", "kind": "person"}, "record.created", f"Encounter/{mine}")
        mine += 1
    assert proc.wait(60) == 0
    events = store.events.find(sort="seq")
    assert len([e for e in events if e["actor"] == "cli"]) == 200
    assert len([e for e in events if e["actor"] == "jo"]) == mine
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert t.verify()["ok"]


def test_a_lost_transaction_is_run_again_and_a_swallowed_loss_still_fails(store):
    """A writer outside the hub (no lease) takes the track's next number in a transaction of its own: the hub's
    append conflicts with it, and atomic() runs it again once that writer commits. A block that catches the loss and
    goes on is not committed as if nothing happened."""
    import threading
    import time
    from practicehub.store import Transient
    t = _track(store)
    t.append({"id": "rana", "kind": "person"}, "start", "x")
    raw = store.client.start_session()
    raw.start_transaction()
    store.db["events"].insert_one({"_id": 2, "seq": 2, "hash": "outside"}, session=raw)
    threading.Timer(0.15, raw.commit_transaction).start()
    seq = t.append({"id": "jo", "kind": "person"}, "record.created", "Encounter/v1")
    assert seq == 3
    time.sleep(0.05)
    raw.end_session()

    raw = store.client.start_session()
    raw.start_transaction()
    store.db["events"].insert_one({"_id": 4, "seq": 4, "hash": "outside again"}, session=raw)
    with pytest.raises(Transient):
        with store:
            store.items.insert({"_id": "kept?"})
            try:
                t.append({"id": "jo", "kind": "person"}, "record.updated", "Encounter/v1")
            except Transient:
                pass  # swallowed: the block goes on as if the event were written
    raw.abort_transaction()
    raw.end_session()
    assert store.items.get("kept?") is None


def test_a_lease_left_by_a_process_that_died_is_taken_at_once(store):
    import socket
    import subprocess
    import time
    from datetime import datetime, timedelta
    dead = subprocess.Popen(["true"])
    dead.wait()
    store.db["locks"].replace_one({"_id": "writer"}, {"_id": "writer", "owner": "gone", "host": socket.gethostname(),
                                                       "pid": dead.pid, "until": datetime.now() + timedelta(hours=1)},
                                  upsert=True)
    start = time.monotonic()
    with store:
        store.items.insert({"_id": "x"})
    assert time.monotonic() - start < 2 and store.items.get("x")
