import copy
import os
import re
import sys
from pathlib import Path

import pymongo
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The suite needs no model: an agent added without one is given the pack's stand-ins, not the hub's default (Ollama).
# A test of the default itself unsets this.
os.environ["PRACTICEHUB_DEFAULT_MODEL"] = "standin"

from practicehub.agents.runner import run_all_local  # noqa: E402
from practicehub.hub import Hub  # noqa: E402


def quiet(*_):
    pass


# Tests run against a MongoDB replica set: PRACTICEHUB_TEST_DATABASE, else a one-node set on 127.0.0.1:27118
# (scripts/test-mongo.sh starts one in docker).
TEST_URI = os.environ.get("PRACTICEHUB_TEST_DATABASE",
                          "mongodb://127.0.0.1:27118/?replicaSet=rs0&directConnection=true")

# Every test still has databases of its own, but a test process makes them once and hands them on: making a database
# means dozens of collections and index files, seconds a test and thousands of files on the server. A test asks for
# a new database as the hub does (``ph_<random>``: PRACTICEHUB_DATABASE_NAME, scratch_store, Hub(None)); each name
# it uses is given one of this process's databases instead, and after the test each is put back as it was handed
# out, in place: an empty one emptied, a prepared hub's (below) given back its documents. Its collections and
# indexes are kept; no test sees what another left.
_PREFIX = f"pht{os.getpid()}"
_RANDOM = re.compile(r"ph_[0-9a-f]{12}")
_EMPTY_MD5 = "d41d8cd98f00b204e9800998ecf8427e"
_DATABASES: set = set()   # every database this process made: dropped when it ends
_BASELINE: dict = {}      # database -> (snapshot it is put back to, or None for empty; dbHash of that state)
_TEST: dict = {}          # this test's: random name -> database given, "used" -> databases to put back


def _db(name: str):
    from practicehub.store import client_for
    return client_for(TEST_URI)[name]


def _hashes(name: str) -> dict:
    return _db(name).command("dbHash")["collections"]


def _put_back(name: str) -> None:
    """``name`` as it was handed out: each collection that differs (one dbHash says which) refilled or emptied."""
    snap, want = _BASELINE[name]
    db = _db(name)
    for coll, md5 in _hashes(name).items():
        if coll.startswith("system.") or md5 == want.get(coll, _EMPTY_MD5):
            continue
        db[coll].delete_many({})
        docs = snap[coll][0] if snap and coll in snap else None
        if docs:
            db[coll].insert_many(docs, ordered=False)
    if set(want) - set(db.list_collection_names()):  # the test dropped some (or all) of it: made again
        _fill(name, snap)


def _fill(name: str, snap: dict | None) -> None:
    """Make ``name`` hold ``snap`` (None: nothing), the state it is put back to after each test from now on."""
    db = _db(name)
    have = set(db.list_collection_names())
    for coll in have:
        if not coll.startswith("system."):
            db[coll].delete_many({})
    for coll, (docs, idx) in (snap or {}).items():
        if coll not in have:
            db.create_collection(coll)
        if idx:
            db[coll].create_indexes([pymongo.IndexModel(k, **o) for k, o in idx])
        if docs:
            db[coll].insert_many(docs, ordered=False)
    _BASELINE[name] = (snap, _hashes(name))


def _take(name: str) -> str:
    """Hand out database ``name`` to this test (made, empty, the first time)."""
    if name not in _DATABASES:
        _db(name).client.drop_database(name)  # one a process with this pid left
        _DATABASES.add(name)
        _BASELINE[name] = (None, {})
    _TEST["used"].append(name)
    return name


def _scratch() -> str:
    """An empty database for this test: the first of this process's not handed out yet."""
    k = 0
    while f"{_PREFIX}_s{k}" in _TEST["used"]:
        k += 1
    name = _take(f"{_PREFIX}_s{k}")
    if _BASELINE[name][0] is not None:  # it held a second copy of a prepared hub (snapshot_hub)
        _fill(name, None)
    return name


def _given(name: str) -> str:
    """The database a test that asked for ``name`` gets: a new name (``ph_<random>``) is one of the pool's."""
    if not _TEST or not _RANDOM.fullmatch(name):
        return name
    if name not in _TEST["names"]:
        _TEST["names"][name] = _TEST.pop("next", None) or _scratch()
    return _TEST["names"][name]


@pytest.fixture(scope="session", autouse=True)
def _databases():
    yield
    from practicehub.store import client_for
    for name in _DATABASES:
        client_for(TEST_URI).drop_database(name)


@pytest.fixture(autouse=True)
def _database(monkeypatch, request):
    _TEST.update(names={}, used=[])
    monkeypatch.setenv("PRACTICEHUB_DATABASE", TEST_URI)
    monkeypatch.setenv("PRACTICEHUB_DATABASE_NAME", _scratch())  # the name itself: a subprocess opens it too
    import practicehub.store as st
    real = st.Store.__init__

    def given(self, uri=None, database=None, **k):
        real(self, uri, database, **k)
        if _given(self.name) != self.name:
            real(self, uri, _given(self.name), **k)
    monkeypatch.setattr(st.Store, "__init__", given)
    try:
        yield
        for name in dict.fromkeys(_TEST["used"]):  # every workflow any test walked: one item from start to finish
            one_item_per_run(_db(name))
            if not request.node.get_closest_marker("own_shapes"):  # and nothing stored that db.py does not name
                stored_as_written_down(name)
    finally:
        used, _ = _TEST.pop("used"), _TEST.clear()
        for name in dict.fromkeys(used):
            _put_back(name)


def one_item_per_run(db) -> None:
    """Each run that has its item has that one item at the top (one key, one history), and every other piece of
    work on it (parallel steps, a step once per person) is a child of it. Notices and sponsor holds are apart."""
    for run in db.runs.find({"item": {"$type": "string"}}, {"item": 1}):
        top = [r["_id"] for r in db.items.find({"run_id": run["_id"], "parent_id": None,
                                                 "kind": {"$nin": ["notice", "hold"]}}, {"_id": 1})]
        assert top == [run["item"]], f"run {run['_id']}: top items {top}, not its one item {run['item']}"
        stray = db.items.count_documents({"run_id": run["_id"], "parent_id": {"$nin": [None, run["item"]]}})
        assert not stray, f"run {run['_id']}: {stray} child item(s) of another item"


_KEYS = [{"$project": {"k": {"$objectToArray": "$$ROOT"}}}, {"$unwind": "$k"}, {"$group": {"_id": "$k.k"}}]
_READ: set = set()  # (collection, dbHash of its documents): documents already read, by any test of this process


def stored_as_written_down(name: str) -> None:
    """Every collection in the database ``name``, and every field of its documents, is one ``practicehub/db.py``
    names (``SHAPES``): the data model is what is written down there, and a field added in passing fails here.
    Only what a test changed is read (one dbHash says what); the same documents are not read twice.
    A test that stores documents of its own making, not the hub's, says so: ``@pytest.mark.own_shapes``."""
    from practicehub import db as model
    database = _db(name)
    for coll, md5 in _hashes(name).items():
        if coll.startswith(("system.", "files.")) or md5 == _EMPTY_MD5 or (coll, md5) in _READ:
            continue
        assert coll in model.SHAPES, f"the collection {coll} is not written down in practicehub/db.py (SHAPES)"
        extra = sorted({r["_id"] for r in database[coll].aggregate(_KEYS)} - model.fields(coll))
        assert not extra, f"{coll}: {', '.join(extra)} is not written down in practicehub/db.py (SHAPES)"
        _READ.add((coll, md5))


@pytest.fixture
def store():
    from practicehub.store import scratch_store
    return scratch_store()


# Installing a pack (and its demo) takes seconds of database round trips; the result is the same every time. So the
# first test that asks for a given hub builds it for real, in a database this process keeps for it, and its
# documents are kept (raw BSON, every collection and its indexes); after every test that used it, that database is
# put back to them, and the next test gets a new Hub object over it.
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


def _as_built(h, modules) -> None:
    """The hub's memory as the build left it: the modules it installed (in memory), no cached parse."""
    from practicehub import pack as packmod
    for name in modules:  # what install_module did in memory: the module's code runs in this hub
        packmod.add_module(h.pack, packmod.load_module(name))
    h._defs_reset()
    h._sealed_ok = set()
    h._subjects()


def snapshot_hub(key: str, pack: str, build, modules=(), *, result: bool = False):
    """A new Hub with ``pack``, as ``build(hub)`` leaves it: built once under ``key``, then put back after each test.
    ``modules``: the modules ``build`` installs (install_module), which a later hub loads again. ``result``: return
    ``(hub, what build returned)``; the value is kept with the copy, so it must be plain data (ids, refs)."""
    name = f"{_PREFIX}_{re.sub(r'[^a-z0-9]', '_', key)}"
    if name in _TEST["used"]:  # a second hub of the same kind in one test: an empty database, filled
        name = _scratch()
        _TEST["used"].pop()
        got, _ = _shared(key, lambda: pytest.fail(f"{key} is built in its own database first"))
    elif name in _DATABASES and _BASELINE[name][0] is not None:
        got = _shared(key, None)[0]
    else:
        got = None
    _TEST["next"] = _take(name)
    out = []
    if got is None:  # this process has not got it yet: build it, or take the run's copy
        def make():
            out.append(build(h))
            return _snapshot(h), copy.deepcopy(out[0])
        h = Hub(None, pack=pack)
        got, made = _shared(key, make)
        if made:
            _BASELINE[name] = (got[0], _hashes(name))
            return (h, out[0]) if result else h
        _fill(name, got[0])
    elif _BASELINE[name][0] is not got[0]:
        _fill(name, got[0])
        h = None
    else:
        h = None
    if h is None:
        h = Hub(None, pack=pack)
    else:
        h._defs_reset()
    _as_built(h, modules)
    return (h, copy.deepcopy(got[1])) if result else h


def snapshot_onto(h, key: str, build, modules=()):
    """``h`` (a hub fixture just made) as ``build(h)`` leaves it, from a copy after the first time. What ``build``
    returns is kept with the copy (plain data: ids, refs) and returned. Its database is put back to ``h``'s own
    starting state after the test."""
    out = []

    def make():
        out.append(build(h))
        return _snapshot(h), copy.deepcopy(out[0])
    got, made = _shared(key, make)
    if made:
        return out[0]
    snap, _ = _BASELINE[h.db.name]
    db = h.db.db
    for coll in db.list_collection_names():
        if not coll.startswith("system.") and coll not in got[0]:
            db[coll].delete_many({})
    for coll, (docs, idx) in got[0].items():
        db[coll].delete_many({})
        if idx and not (snap and coll in snap and snap[coll][1] == idx):
            db[coll].create_indexes([pymongo.IndexModel(k, **o) for k, o in idx])
        if docs:
            db[coll].insert_many(docs, ordered=False)
    _as_built(h, modules)
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


def visit_run(hub):
    """The demo's documented visit (Priya's): the appointment run whose visit has ended and is being written up."""
    runs = [r for r in hub.engine.runs(limit=1000) if r["workflow_id"] == "appointment-request"
            and r["steps"].get("end", {}).get("state") == "done"]
    assert runs, "no visit being documented"
    return runs[-1]


def claim_run(hub, run):
    """The visit's charge (a ChargeItem the signed visit made) and its visit-claim run: billing works on it."""
    ref = hub.engine.run(run["id"])["record"]
    kids = [k for k in hub.engine.children_of(ref) if k["ref"].startswith("ChargeItem/")]
    assert kids and kids[-1]["run"], f"no charge for {ref}"
    return kids[-1]["run"]


def item_for(hub, run, step_id, status=("open", "claimed")):
    row = hub.db.items.find_one({"run_id": run["id"], "step_id": step_id, "kind": {"$ne": "notice"},
                                 "status": {"$in": list(status)}}, sort="n")
    assert row, f"no open item for {step_id}"
    return row["id"]


def follow_up_run(hub, run):
    """The follow-up (a Task, walked by patient-follow-up) a lab result's review asked for: its run."""
    ref = hub.engine.run(run["id"])["record"]
    tasks = [t for t in hub.records.search("Task", limit=None) if (t.get("focus") or {}).get("reference") == ref]
    assert tasks, f"no follow-up for {ref}"
    (fu,) = hub.engine.runs(record=f"Task/{tasks[-1]['id']}")[:1]
    return fu


def pytest_collection_modifyitems(config, items):
    """PH_SHARD=i/n keeps every n-th test from the i-th: scripts/test-parallel.sh runs n shards at once."""
    shard = os.environ.get("PH_SHARD")
    if not shard:
        return
    i, n = (int(x) for x in shard.split("/"))
    keep = [t for k, t in enumerate(items) if k % n == i]
    config.hook.pytest_deselected(items=[t for t in items if t not in keep])
    items[:] = keep
