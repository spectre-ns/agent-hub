import os
import secrets
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from practicehub.agents.runner import run_all_local  # noqa: E402
from practicehub.hub import Hub  # noqa: E402


def quiet(*_):
    pass


# Tests run against a MongoDB replica set: PRACTICEHUB_TEST_DATABASE, else a one-node set on 127.0.0.1:27118
# (scripts/test-mongo.sh starts one in docker). Every test gets a database of its own, dropped afterwards.
TEST_URI = os.environ.get("PRACTICEHUB_TEST_DATABASE",
                          "mongodb://127.0.0.1:27118/?replicaSet=rs0&directConnection=true")


@pytest.fixture(autouse=True)
def _database(monkeypatch):
    monkeypatch.setenv("PRACTICEHUB_DATABASE", TEST_URI)
    monkeypatch.setenv("PRACTICEHUB_DATABASE_NAME", f"ph_{secrets.token_hex(6)}")
    made = []
    import practicehub.store as st
    real = st.Store.__init__

    def tracked(self, *a, **k):
        real(self, *a, **k)
        made.append(self)
    monkeypatch.setattr(st.Store, "__init__", tracked)
    yield
    from practicehub.store import client_for
    for name in {s.name for s in made if s.name.startswith("ph_")}:
        client_for(TEST_URI).drop_database(name)


@pytest.fixture
def store():
    from practicehub.store import scratch_store
    return scratch_store()


@pytest.fixture
def hub(monkeypatch):
    monkeypatch.setenv("PRACTICEHUB_SECRET", "test-secret")
    monkeypatch.delenv("PRACTICEHUB_ALLOW_EXTERNAL_MODELS", raising=False)
    h = Hub(None, pack="general-practice")
    h.install("general-practice", demo=True)
    return h


@pytest.fixture
def worked(hub):
    """The demo hub after every agent has worked its inbox once."""
    run_all_local(hub, log=quiet)
    return hub


def M(hub, mid):
    return hub.member(mid)


def run_of(hub, workflow_id):
    runs = [r for r in hub.engine.runs() if r["workflow_id"] == workflow_id]
    assert runs, f"no {workflow_id} run"
    return runs[0]


def item_for(hub, run, step_id, status=("open", "claimed")):
    row = hub.db.items.find_one({"run_id": run["id"], "step_id": step_id, "kind": {"$ne": "notice"},
                                 "status": {"$in": list(status)}}, sort="n")
    assert row, f"no open item for {step_id}"
    return row["id"]


def pytest_collection_modifyitems(config, items):
    """PH_SHARD=i/n keeps every n-th test from the i-th: scripts/test-parallel.sh runs n shards at once."""
    shard = os.environ.get("PH_SHARD")
    if not shard:
        return
    i, n = (int(x) for x in shard.split("/"))
    keep = [t for k, t in enumerate(items) if k % n == i]
    config.hook.pytest_deselected(items=[t for t in items if t not in keep])
    items[:] = keep
