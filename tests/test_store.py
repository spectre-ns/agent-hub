"""The storage layer on MongoDB: nested blocks undo only themselves, refusals survive, files round-trip."""

import pytest

from practicehub.store import DuplicateKey


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
