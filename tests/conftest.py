import copy
import os
import secrets
import sys
from pathlib import Path

import pymongo
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


# Installing a pack (and its demo) takes seconds of database round trips; the result is the same every time. So the
# first test that asks for a given hub builds it for real, its database is copied (raw BSON, every collection and its
# indexes), and every later test gets a new database of its own with that copy put back into it: same documents,
# same indexes, a new Hub object over them. Nothing is shared between tests but the bytes of the copy.
# PH_SNAPSHOTS (scripts/test-parallel.sh sets it, one directory per run): the copies are files there, so the shards
# of one run build each of them once between them (the first shard to need one builds it; the others wait for it).
_SNAPSHOTS: dict = {}


def _shared(key: str, make):
    """The copy under ``key``: from memory, else the run's shared directory, else ``make()`` (and kept)."""
    got = _SNAPSHOTS.get(key)
    if got is not None:
        return got, False
    where = os.environ.get("PH_SNAPSHOTS")
    if not where:
        got = _SNAPSHOTS[key] = make()
        return got, True
    import fcntl
    import pickle
    from bson.raw_bson import RawBSONDocument
    path = Path(where) / f"{key.replace(':', '-')}.pickle"
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            snap, out = pickle.loads(path.read_bytes())
            got = ({n: ([RawBSONDocument(d) for d in docs], idx) for n, (docs, idx) in snap.items()}, out)
            made = False
        else:
            got, made = make(), True
            snap = {n: ([bytes(d.raw) for d in docs], idx) for n, (docs, idx) in got[0].items()}
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(pickle.dumps((snap, got[1])))
            tmp.replace(path)
    _SNAPSHOTS[key] = got
    return got, made


def _snapshot(h) -> dict:
    from bson.codec_options import CodecOptions
    from bson.raw_bson import RawBSONDocument
    raw = CodecOptions(document_class=RawBSONDocument)
    snap = {}
    for name in h.db.db.list_collection_names():
        if name.startswith("system."):
            continue
        idx = [(list(i["key"].items()), {k: v for k, v in i.items() if k not in ("key", "v", "ns")})
               for i in h.db.db[name].list_indexes() if i["name"] != "_id_"]
        snap[name] = (list(h.db.db.get_collection(name, codec_options=raw).find()), idx)
    return snap


def _restore(h, snap: dict, modules, *, fresh: bool) -> None:
    """Put a snapshot into ``h``'s database: a new Hub's (``fresh``: Hub() made the INDEXES ones already), or one
    that is emptied first. Then the hub's memory as the build left it: the modules it installed, no cached parse."""
    from practicehub import pack as packmod
    from practicehub.db import INDEXES
    if not fresh:
        h.db.drop_database()
    have = set(h.db.db.list_collection_names())
    for name, (docs, idx) in snap.items():
        if name not in have:
            h.db.db.create_collection(name)
        idx = [(k, o) for k, o in idx if not (fresh and name in INDEXES)]
        if idx:
            h.db.db[name].create_indexes([pymongo.IndexModel(k, **o) for k, o in idx])
        if docs:
            h.db.db[name].insert_many(docs, ordered=False)
    for name in modules:  # what install_module did in memory: the module's code runs in this hub
        packmod.add_module(h.pack, packmod.load_module(name))
    h._defs_reset()
    h._sealed_ok = set()
    h._subjects()


def snapshot_hub(key: str, pack: str, build, modules=(), *, result: bool = False):
    """A new Hub with ``pack``, as ``build(hub)`` leaves it: built once under ``key``, then copied.
    ``modules``: the modules ``build`` installs (install_module), which a copy loads again. ``result``: return
    ``(hub, what build returned)``; the value is kept with the copy, so it must be plain data (ids, refs)."""
    h = Hub(None, pack=pack)
    out = []

    def make():
        out.append(build(h))
        return _snapshot(h), copy.deepcopy(out[0])
    got, made = _shared(key, make)
    if not made:
        _restore(h, got[0], modules, fresh=True)
        out.append(copy.deepcopy(got[1]))
    return (h, out[0]) if result else h


def snapshot_onto(h, key: str, build, modules=()):
    """``h`` (a hub fixture just made) as ``build(h)`` leaves it, from a copy after the first time. What ``build``
    returns is kept with the copy (plain data: ids, refs) and returned."""
    out = []

    def make():
        out.append(build(h))
        return _snapshot(h), copy.deepcopy(out[0])
    got, made = _shared(key, make)
    if made:
        return out[0]
    _restore(h, got[0], modules, fresh=False)
    return copy.deepcopy(got[1])


def _demo(h):
    h.install("general-practice", demo=True)


def _worked(h):
    _demo(h)
    run_all_local(h, log=quiet)


@pytest.fixture
def hub(monkeypatch, request):
    monkeypatch.setenv("PRACTICEHUB_SECRET", "test-secret")
    monkeypatch.delenv("PRACTICEHUB_ALLOW_EXTERNAL_MODELS", raising=False)
    if "worked" in request.fixturenames:  # the test wants the worked hub: copy that one, and worked() returns it
        h = snapshot_hub("worked", "general-practice", _worked)
        h._test_worked = True
        return h
    return snapshot_hub("demo", "general-practice", _demo)


@pytest.fixture
def worked(hub):
    """The demo hub after every agent has worked its inbox once."""
    assert getattr(hub, "_test_worked", False)
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
