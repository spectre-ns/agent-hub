"""The hub's storage: a MongoDB database (a replica set: transactions need one; one node will do), files in GridFS.

``store["members"]`` (or ``store.members``) is a collection with a small, explicit set of methods. Writes are
grouped with ``with store:``, which nests. The outermost block is one MongoDB transaction. MongoDB has no savepoints,
so an inner block keeps a log of every document it changed, as it was before; if the inner block raises, the log is
played back, newest first, inside the same transaction, and the work around it stands. That is how a failed
automation step leaves nothing behind while the step's failure is still recorded. ``surviving`` writes something that
must outlast a rollback: the track's record of a refusal.

Every operation holds the store's lock: one writer at a time in a hub process, so the track's chain and the counters
never race within it. Across processes (the server, and a CLI export, verify or tick beside it) the outermost block
first takes the database's writer lease (``locks``/"writer", outside the transaction): a second process waits for the
first one's transaction to end instead of failing on its writes. The lease names its holder's host and process, so a
process that died holding it is noticed at once on the same host, and every lease expires (twice MongoDB's transaction
lifetime limit), so a holder on another host that died is waited out, never forever.

A transaction can still fail for a reason that goes away (MongoDB's TransientTransactionError: a failover, a write
conflict with something outside the hub): the store raises ``Transient`` and marks the transaction lost, so a block
that catches it cannot go on writing into a transaction that is gone. ``atomic(fn)`` runs ``fn`` as a whole
transaction and runs it again, a few times with a growing pause, when it was ``Transient``; it is for units of work
that only write to the database (a block that also sends a message or writes a file must not run twice). A commit
whose result is unknown is committed again, which MongoDB makes safe.
"""

from __future__ import annotations

import os
import random
import re
import secrets
import socket
import threading
import time
from datetime import datetime, timezone

import gridfs
import pymongo
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

ASC, DESC = pymongo.ASCENDING, pymongo.DESCENDING

DEFAULT_URI = "mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true"


class DuplicateKey(Exception):
    """A document with that ``_id`` (or a unique field) is there already."""


class Transient(Exception):
    """The transaction was lost for a reason that goes away (another writer, a failover): run the work again."""


WRITE_CONFLICT, NO_SUCH_TRANSACTION = 112, 251


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, Transient):
        return True
    if not isinstance(exc, PyMongoError):
        return False
    return exc.has_error_label("TransientTransactionError") or getattr(exc, "code", None) in (
        WRITE_CONFLICT, NO_SUCH_TRANSACTION)


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sort(sort) -> list[tuple[str, int]]:
    if not sort:
        return []
    if isinstance(sort, str):
        return [(sort, ASC)]
    if isinstance(sort, tuple) and len(sort) == 2 and isinstance(sort[1], int):
        return [sort]
    return [(s, ASC) if isinstance(s, str) else (s[0], s[1]) for s in sort]


def _get(doc, path: str):
    for part in path.split("."):
        if not isinstance(doc, dict):
            return None
        doc = doc.get(part)
    return doc


def _guarded(fn):
    """A MongoDB error that loses the transaction marks it lost and is raised as ``Transient``."""
    def run(self, *a, **k):
        try:
            return fn(self, *a, **k)
        except PyMongoError as exc:
            if is_transient(exc) and self.store._lost():
                raise Transient(str(exc)) from exc
            raise
    run.__name__, run.__doc__ = fn.__name__, fn.__doc__
    return run


class Collection:
    def __init__(self, store: "Store", name: str):
        self.store, self.name = store, name
        self.c = store.db[name]

    # reads ---------------------------------------------------------------------------------------------------------
    @_guarded
    def find(self, flt: dict | None = None, *, sort=None, limit: int | None = None, skip: int = 0,
             fields: list[str] | None = None) -> list[dict]:
        if limit == 0:
            return []
        sort = _sort(sort)
        with self.store.lock:
            cur = self.c.find(flt or {}, {f: 1 for f in fields} if fields else None, session=self.store.session())
            # always a full order: the insertion order (_id for counters) breaks ties, so a page is the same each time
            cur = cur.sort(sort + ([("_id", ASC)] if all(f != "_id" for f, _ in sort) else []))
            if skip:
                cur = cur.skip(skip)
            if limit is not None:
                cur = cur.limit(limit)
            return list(cur)

    def find_one(self, flt: dict | None = None, *, sort=None, fields: list[str] | None = None) -> dict | None:
        rows = self.find(flt, sort=sort, limit=1, fields=fields)
        return rows[0] if rows else None

    @_guarded
    def get(self, _id) -> dict | None:
        with self.store.lock:
            return self.c.find_one({"_id": _id}, session=self.store.session())

    def exists(self, flt: dict | None = None) -> bool:
        return self.find_one(flt, fields=["_id"]) is not None

    @_guarded
    def count(self, flt: dict | None = None) -> int:
        with self.store.lock:
            return self.c.count_documents(flt or {}, session=self.store.session())

    @_guarded
    def distinct(self, field: str, flt: dict | None = None) -> list:
        with self.store.lock:
            return sorted(self.c.distinct(field, flt or {}, session=self.store.session()), key=lambda v: (str(type(v)), v))

    def max(self, field: str, flt: dict | None = None, default=None):
        row = self.find_one({**(flt or {}), field: {"$ne": None}} if field not in (flt or {}) else flt,
                            sort=[(field, DESC)], fields=[field])
        return _get(row, field) if row else default

    @_guarded
    def aggregate(self, pipeline: list[dict]) -> list[dict]:
        with self.store.lock:
            return list(self.c.aggregate(pipeline, session=self.store.session()))

    # writes --------------------------------------------------------------------------------------------------------
    @_guarded
    def insert(self, doc: dict) -> dict:
        if "_id" not in doc:
            raise ValueError(f"a document for {self.name} needs an _id")
        with self.store.lock:
            self.store._dup(lambda: self.c.insert_one(doc, session=self.store.session()))
            self.store._log(self.name, [(doc["_id"], None)])
        return doc

    @_guarded
    def update(self, flt: dict, ops: dict, *, many: bool = False, upsert: bool = False) -> int:
        """How many documents matched (an upsert that inserted counts one)."""
        with self.store.lock:
            s = self.store.session()
            if self.store.logging():
                before = list(self.c.find(flt, session=s, limit=0 if many else 1))
                if before:
                    ids = [d["_id"] for d in before]
                    self.store._dup(lambda: self.c.update_many({"_id": {"$in": ids}}, ops, session=s))
                    self.store._log(self.name, [(d["_id"], d) for d in before])
                    return len(before)
                if not upsert:
                    return 0
            fn = self.c.update_many if many else self.c.update_one
            r = self.store._dup(lambda: fn(flt, ops, upsert=upsert, session=s))
            if r.upserted_id is not None:
                self.store._log(self.name, [(r.upserted_id, None)])
                return 1
            return r.matched_count

    def set(self, _id, fields: dict) -> int:
        return self.update({"_id": _id}, {"$set": fields})

    @_guarded
    def replace(self, doc: dict, *, upsert: bool = True) -> None:
        with self.store.lock:
            s = self.store.session()
            before = self.c.find_one({"_id": doc["_id"]}, session=s) if self.store.logging() else None
            r = self.store._dup(lambda: self.c.replace_one({"_id": doc["_id"]}, doc, upsert=upsert, session=s))
            if r.matched_count or r.upserted_id is not None:
                self.store._log(self.name, [(doc["_id"], before)])

    @_guarded
    def delete(self, flt: dict, *, many: bool = True) -> int:
        with self.store.lock:
            s = self.store.session()
            before = list(self.c.find(flt, session=s, limit=0 if many else 1)) if self.store.logging() else []
            r = (self.c.delete_many if many else self.c.delete_one)(flt, session=s)
            self.store._log(self.name, [(d["_id"], d) for d in before])
            return r.deleted_count

    @_guarded
    def find_one_and_update(self, flt: dict, ops: dict, *, sort=None, upsert: bool = False) -> dict | None:
        """Change the first match and return it as it is after the change: compare-and-set, claims, counters."""
        with self.store.lock:
            s = self.store.session()
            sort = _sort(sort) or None
            before = self.c.find_one(flt, sort=sort, session=s) if self.store.logging() else None
            after = self.store._dup(lambda: self.c.find_one_and_update(
                flt if before is None else {"_id": before["_id"]}, ops, sort=sort, upsert=upsert,
                return_document=ReturnDocument.AFTER, session=s))
            if after is not None:
                self.store._log(self.name, [(after["_id"], before)])
            return after


_CLIENTS: dict[str, pymongo.MongoClient] = {}
_CLIENTS_LOCK = threading.Lock()


def client_for(uri: str) -> pymongo.MongoClient:
    """One client (one connection pool) per server address in this process, checked once to be a replica set."""
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(uri)
        if client is None:
            client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=10000)
            hello = client.admin.command("hello")
            if not hello.get("setName") and hello.get("msg") != "isdbgrid":
                client.close()
                raise RuntimeError("Practice Hub needs MongoDB as a replica set (one node will do: start mongod with "
                                   "--replSet rs0, then run rs.initiate()): a standalone server has no transactions")
            _CLIENTS[uri] = client
        return client


_HOST = socket.gethostname()
_HELD: dict[str, tuple[int, str]] = {}  # lease token -> (thread, database) for the leases this process holds now


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError, ValueError, TypeError):
        return True  # there, but not ours to signal (or not a pid): taken as alive
    return True


class Store:
    """One MongoDB database."""

    ATTEMPTS = 6        # atomic(): how many times a unit of work is tried before its Transient is raised
    LEASE_SECONDS = None  # the writer lease's length; None: twice the server's transactionLifetimeLimitSeconds

    def __init__(self, uri: str | None = None, database: str | None = None, *, client=None):
        uri = uri or os.environ.get("PRACTICEHUB_DATABASE") or DEFAULT_URI
        self.uri = uri
        self.client = client or client_for(uri)
        self.name = database or pymongo.uri_parser.parse_uri(uri).get("database") or "practicehub"
        self.db = self.client[self.name]
        self.fs = gridfs.GridFSBucket(self.db, bucket_name="files")
        self.lock = threading.RLock()
        self._depth = 0
        self._owner = None
        self._session = None
        self._undo: list[list] = []   # one log per open inner block: [(collection, _id, document before or None)]
        self._survivors: list[list] = []  # [depth, fn, rolled_back]
        self._colls: dict[str, Collection] = {}
        self._doomed = False  # the open transaction was lost (Transient): nothing more is written into it
        self._token = f"{_HOST}:{os.getpid()}:{secrets.token_hex(6)}"
        self._lease_ms = None

    def __getitem__(self, name: str) -> Collection:
        c = self._colls.get(name)
        if c is None:
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
                raise ValueError(f"bad collection name {name!r}")
            c = self._colls[name] = Collection(self, name)
        return c

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def ensure_indexes(self, spec: dict[str, list]) -> None:
        """``{collection: [field | (field, ...) | {"fields": [...], "unique": True, "partial": {filter}}]}``.
        ``partial``: the index holds only the documents that match the filter (a unique key that only some have)."""
        for coll, idx in spec.items():
            models = []
            for i in idx:
                if isinstance(i, str):
                    i = {"fields": [i]}
                elif isinstance(i, tuple):
                    i = {"fields": list(i)}
                extra = {"partialFilterExpression": i["partial"]} if i.get("partial") else {}
                models.append(pymongo.IndexModel([(f, ASC) for f in i["fields"]], unique=bool(i.get("unique")), **extra))
            if models:  # one createIndexes command per collection: one round trip, not one per index
                self.db[coll].create_indexes(models)

    def drop_database(self) -> None:
        self.client.drop_database(self.name)

    def close(self) -> None:
        """Done with this database. The connection pool is shared by every Store on the same server, and stays."""
        self._colls.clear()

    def collections(self) -> list[str]:
        return sorted(n for n in self.db.list_collection_names()
                      if not n.startswith(("files.", "system.")) and n != "locks")

    # counters and settings ------------------------------------------------------------------------------------------
    def next_seq(self, name: str) -> int:
        """The next number of a sequence (1, 2, ...); given back if the transaction it was taken in is undone."""
        return int(self["counters"].find_one_and_update({"_id": name}, {"$inc": {"n": 1}}, upsert=True)["n"])

    def get_setting(self, key: str, default=None):
        row = self["settings"].get(key)
        return row["value"] if row else default

    def set_setting(self, key: str, value) -> None:
        self["settings"].replace({"_id": key, "value": value})

    def del_setting(self, key: str) -> None:
        self["settings"].delete({"_id": key})

    # transactions ---------------------------------------------------------------------------------------------------
    def session(self):
        """The open transaction's session, for this thread; None outside one."""
        return self._session if self._depth and self._owner == threading.get_ident() else None

    def logging(self) -> bool:
        return bool(self._undo) and self._owner == threading.get_ident()

    def _log(self, coll: str, before: list[tuple]) -> None:
        if self.logging():
            self._undo[-1].extend((coll, i, d) for i, d in before)

    @staticmethod
    def _dup(fn):
        try:
            return fn()
        except DuplicateKeyError as exc:
            raise DuplicateKey(str(exc)) from None

    # the writer lease: one transaction at a time on this database, across processes -------------------------------
    def _lease_length_ms(self) -> int:
        if self._lease_ms is None:
            secs = self.LEASE_SECONDS
            if secs is None:
                try:
                    limit = self.client.admin.command("getParameter", 1, transactionLifetimeLimitSeconds=1)
                    secs = 2 * int(limit.get("transactionLifetimeLimitSeconds") or 60)
                except PyMongoError:
                    secs = 120
            self._lease_ms = int(secs * 1000)
        return self._lease_ms

    def _take_lease(self) -> None:
        locks = self.db["locks"]
        ms = self._lease_length_ms()
        pause = 0.001
        _HELD[self._token] = (threading.get_ident(), self.name)  # before it is ours: no thread here takes it as stale
        while True:
            try:
                got = locks.find_one_and_update(
                    {"_id": "writer", "$or": [{"owner": self._token}, {"$expr": {"$lt": ["$until", "$$NOW"]}}]},
                    [{"$set": {"owner": self._token, "host": _HOST, "pid": os.getpid(),
                               "until": {"$add": ["$$NOW", ms]}}}])
            except BaseException:
                _HELD.pop(self._token, None)
                raise
            if got is not None:
                return
            holder = locks.find_one({"_id": "writer"})
            if holder is None:  # a new database: the lease is made, free
                try:
                    locks.insert_one({"_id": "writer", "owner": None, "until": datetime(1970, 1, 1)})
                except DuplicateKeyError:
                    pass
                continue
            if holder.get("host") == _HOST and holder.get("pid") is not None:
                mine = holder.get("pid") == os.getpid()
                held = _HELD.get(holder.get("owner"))
                if mine and held and held[0] == threading.get_ident():
                    _HELD.pop(self._token, None)
                    raise RuntimeError("this thread already has a transaction open on this database through another "
                                       "Store: it would wait for itself")
                if (mine and held is None) or (not mine and not _alive(holder["pid"])):
                    # its holder is gone (a process that died, or a block of this one that never finished): free it
                    locks.update_one({"_id": "writer", "owner": holder.get("owner")},
                                     {"$set": {"until": datetime(1970, 1, 1)}})
                    continue
            time.sleep(pause * random.uniform(0.5, 1.5))
            pause = min(pause * 2, 0.05)

    def _give_lease(self) -> None:
        _HELD.pop(self._token, None)
        try:
            self.db["locks"].update_one({"_id": "writer", "owner": self._token},
                                        {"$set": {"until": datetime(1970, 1, 1)}})
        except PyMongoError:
            pass  # it runs out on its own

    def _lost(self) -> bool:
        """The open transaction was lost (Transient): mark it, so nothing more goes into it. False outside one."""
        if self.in_transaction():
            self._doomed = True
            return True
        return False

    def __enter__(self):
        self.lock.acquire()
        try:
            if self._depth == 0:
                self._take_lease()
                try:
                    self._session = self.client.start_session()
                    self._session.start_transaction(read_concern=ReadConcern("snapshot"),
                                                    write_concern=WriteConcern("majority"))
                except BaseException:
                    self._session = None
                    self._give_lease()
                    raise
                self._owner = threading.get_ident()
                self._undo = []
                self._doomed = False
            elif self._doomed and self._owner == threading.get_ident():
                raise Transient("the transaction was lost; the work is run again")
            else:
                self._undo.append([])
        except BaseException:
            self.lock.release()
            raise
        self._depth += 1
        return self

    def _commit(self, s) -> None:
        for attempt in range(4):
            try:
                s.commit_transaction()
                return
            except PyMongoError as exc:
                if exc.has_error_label("UnknownTransactionCommitResult") and attempt < 3:
                    time.sleep(0.01 * 2 ** attempt)
                    continue  # committing again is safe: MongoDB applies a transaction once
                if is_transient(exc):
                    raise Transient(str(exc)) from exc
                raise

    def __exit__(self, exc_type, exc, tb):
        self._depth -= 1
        replay = []
        lost = None
        if exc is not None and is_transient(exc) and self._owner == threading.get_ident():
            self._doomed = True
        try:
            if self._depth == 0:
                s, self._session = self._session, None
                doomed, self._doomed = self._doomed, False
                try:
                    if exc_type or doomed:
                        if s.in_transaction:
                            try:
                                s.abort_transaction()
                            except PyMongoError:
                                pass  # the server dropped it already
                        if doomed and not isinstance(exc, Transient):
                            # the body caught what lost the transaction and went on: what it did is not there
                            lost = Transient("the transaction was lost (another writer, or a failover); the work "
                                             "is run again")
                    else:
                        self._commit(s)
                finally:
                    s.end_session()
                    self._owner = None
                    self._undo = []
                    survivors, self._survivors = self._survivors, []
                    self._give_lease()
                # a lost transaction is run again by atomic(), which records its refusals afresh
                if not doomed:
                    replay = [x[1] for x in survivors if exc_type or x[2]]
            elif exc_type:
                log = self._undo.pop()
                if not self._doomed:  # in a lost transaction there is nothing to undo, and nothing can be written
                    s = self._session
                    for coll, _id, before in reversed(log):
                        if before is None:
                            self.db[coll].delete_one({"_id": _id}, session=s)
                        else:
                            self.db[coll].replace_one({"_id": _id}, before, upsert=True, session=s)
                    for x in self._survivors:
                        if x[0] > self._depth:
                            x[2] = True
            else:
                done = self._undo.pop()
                if self._undo:
                    self._undo[-1].extend(done)  # the block around it may still be undone
            for fn in replay:  # writes that must outlive a rollback (a refusal in the audit track)
                self.atomic(fn)
        finally:
            self.lock.release()
        if lost is not None:
            raise lost
        if exc is not None and not isinstance(exc, Transient) and is_transient(exc):
            raise Transient(str(exc)) from exc
        return False

    def atomic(self, fn, *args, **kwargs):
        """``fn(*args, **kwargs)`` as one transaction; when it is the outermost one and is lost for a reason that goes
        away (``Transient``), run again from the start, up to ATTEMPTS times with a growing, jittered pause. Inside a
        transaction already, it is one block of that transaction (the outermost one is what is run again). ``fn``
        must only write to the database: anything else it does would be done again."""
        if self.in_transaction():
            with self:
                return fn(*args, **kwargs)
        for attempt in range(self.ATTEMPTS):
            try:
                with self:
                    return fn(*args, **kwargs)
            except Transient:
                if attempt == self.ATTEMPTS - 1:
                    raise
                time.sleep(min(0.02 * 2 ** attempt, 0.5) * random.uniform(0.5, 1.5))

    def in_transaction(self) -> bool:
        return self._depth > 0 and self._owner == threading.get_ident()

    def surviving(self, fn) -> None:
        """Run ``fn`` now; if the transaction around it is rolled back, run it again afterwards. For audit events
        about a refusal, which must stay in the track although the refused operation is undone."""
        with self.lock:
            fn()
            if self.in_transaction():
                self._survivors.append([self._depth, fn, False])

    # files: GridFS, named by their id. Written outside any transaction: they are content-addressed, so one left by a
    # rolled-back write is harmless and is the one the next write of the same content finds.
    def put_file(self, file_id: str, data: bytes, meta: dict) -> None:
        if self.file_meta(file_id) is None:
            self.fs.upload_from_stream_with_id(file_id, file_id, data, metadata=meta)

    def note_file(self, file_id: str, **sets) -> None:
        """Add to what is known of a file: ``adders`` (who added its bytes), ``records`` (which records named it).
        Outside any transaction, like the file itself: a record noted here whose write was rolled back never named
        the file, and whoever relies on the note reads the record's versions to see that it does."""
        add = {f"metadata.{k}": v for k, v in sets.items() if v}
        if add:
            self.db["files.files"].update_one({"_id": file_id}, {"$addToSet": add})

    def get_file(self, file_id: str) -> tuple[bytes, dict] | None:
        meta = self.file_meta(file_id)
        if meta is None:
            return None
        return self.fs.open_download_stream(file_id).read(), meta

    def file_meta(self, file_id: str) -> dict | None:
        doc = self.db["files.files"].find_one({"_id": file_id})
        return dict(doc.get("metadata") or {}) if doc else None

    def file_ids(self) -> list[str]:
        return sorted(d["_id"] for d in self.db["files.files"].find({}, {"_id": 1}))


def scratch_store(prefix: str = "ph") -> Store:
    """A new, empty database on the configured server, for a test or a throwaway hub."""
    return Store(database=f"{prefix}_{secrets.token_hex(6)}")
