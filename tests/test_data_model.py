"""The data model is what ``practicehub/db.py`` writes down: every collection, every field, every index.

After every test, ``conftest.stored_as_written_down`` reads what the test changed and fails on a field or a
collection ``SHAPES`` does not name. These tests state that rule, and read a whole worked hub once.
"""

import pytest

from conftest import M, stored_as_written_down
from practicehub import db as model


def test_everything_a_worked_practice_stores_is_written_down(worked):
    """The demo practice after its agents have worked: every collection and every field of every document."""
    keys = [{"$project": {"k": {"$objectToArray": "$$ROOT"}}}, {"$unwind": "$k"}, {"$group": {"_id": "$k.k"}}]
    found = worked.db.collections()
    assert {"records", "runs", "items", "events", "outbox", "members", "workflow_versions"} <= set(found)
    for coll in found:
        assert coll in model.SHAPES, f"{coll} is not written down"
        extra = {r["_id"] for r in worked.db[coll].aggregate(keys)} - model.fields(coll)
        assert not extra, f"{coll}: {sorted(extra)} not written down"


def test_an_index_is_over_fields_that_are_written_down():
    for coll, indexes in model.INDEXES.items():
        assert coll in model.SHAPES, f"an index on {coll}, which is not written down"
        for ix in indexes:
            names = [ix] if isinstance(ix, str) else ix["fields"] if isinstance(ix, dict) else list(ix)
            for name in names:
                assert name.split(".")[0] in model.fields(coll), f"{coll}: an index over {name}, which is not written down"


def test_a_collection_says_how_its_id_is_built_and_names_no_field_twice():
    for coll, (key, names) in model.SHAPES.items():
        assert key.strip(), f"{coll}: say how its _id is built"
        listed = names.split()
        assert listed and len(listed) == len(set(listed)), f"{coll}: a field is listed twice"


@pytest.mark.own_shapes
def test_a_field_nobody_wrote_down_is_caught(hub):
    """The rule itself: a document with a field ``SHAPES`` does not name, or a collection it does not, fails."""
    stored_as_written_down(hub.db.name)
    run = hub.engine.runs()[0]
    hub.db.runs.set(run["id"], {"colour": "red"})
    with pytest.raises(AssertionError, match="runs: colour is not written down"):
        stored_as_written_down(hub.db.name)
    hub.db.runs.update({"_id": run["id"]}, {"$unset": {"colour": ""}})
    hub.db["sidecar"].insert({"_id": "x", "of": M(hub, "rana")["id"]})
    with pytest.raises(AssertionError, match="the collection sidecar is not written down"):
        stored_as_written_down(hub.db.name)
