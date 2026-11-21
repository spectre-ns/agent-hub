"""The hub's storage: a MongoDB database (a replica set: transactions need one; one node will do), files in GridFS.

``store["members"]`` (or ``store.members``) is a collection with a small, explicit set of methods. Writes are
grouped with ``with store:``, which nests. The outermost block is one MongoDB transaction. MongoDB has no savepoints,
so an inner block keeps a log of every document it changed, as it was before; if the inner block raises, the log is
played back, newest first, inside the same transaction, and the work around it stands. That is how a failed
automation step leaves nothing behind while the step's failure is still recorded. ``surviving`` writes something that
must outlast a rollback: the track's record of a refusal.

Every operation holds the store's lock: one writer at a time in a hub process, so the track's chain and the counters
never race within it. Run one hub process per database.
"""

from __future__ import annotations

import os
import re
import secrets
import threading
from datetime import datetime, timezone

import gridfs
import pymongo
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

ASC, DESC = pymongo.ASCENDING, pymongo.DESCENDING

DEFAULT_URI = "mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true"


class DuplicateKey(Exception):
    """A document with that ``_id`` (or a unique field) is there already."""


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


class Collection:
    def __init__(self, store: "Store", name: str):
        self.store, self.name = store, name
        self.c = store.db[name]

    # reads ---------------------------------------------------------------------------------------------------------
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

    def get(self, _id) -> dict | None:
        with self.store.lock:
            return self.c.find_one({"_id": _id}, session=self.store.session())

    def exists(self, flt: dict | None = None) -> bool:
        return self.find_one(flt, fields=["_id"]) is not None

    def count(self, flt: dict | None = None) -> int:
        with self.store.lock:
            return self.c.count_documents(flt or {}, session=self.store.session())

    def distinct(self, field: str, flt: dict | None = None) -> list:
        with self.store.lock:
            return sorted(self.c.distinct(field, flt or {}, session=self.store.session()), key=lambda v: (str(type(v)), v))

    def max(self, field: str, flt: dict | None = None, default=None):
        row = self.find_one({**(flt or {}), field: {"$ne": None}} if field not in (flt or {}) else flt,
                            sort=[(field, DESC)], fields=[field])
        return _get(row, field) if row else default

    def aggregate(self, pipeline: list[dict]) -> list[dict]:
        with self.store.lock:
            return list(self.c.aggregate(pipeline, session=self.store.session()))

    # writes --------------------------------------------------------------------------------------------------------
    def insert(self, doc: dict) -> dict:
        if "_id" not in doc:
            raise ValueError(f"a document for {self.name} needs an _id")
        with self.store.lock:
            self.store._dup(lambda: self.c.insert_one(doc, session=self.store.session()))
            self.store._log(self.name, [(doc["_id"], None)])
        return doc

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

    def replace(self, doc: dict, *, upsert: bool = True) -> None:
        with self.store.lock:
            s = self.store.session()
            before = self.c.find_one({"_id": doc["_id"]}, session=s) if self.store.logging() else None
            r = self.store._dup(lambda: self.c.replace_one({"_id": doc["_id"]}, doc, upsert=upsert, session=s))
            if r.matched_count or r.upserted_id is not None:
                self.store._log(self.name, [(doc["_id"], before)])

    def delete(self, flt: dict, *, many: bool = True) -> int:
        with self.store.lock:
            s = self.store.session()
            before = list(self.c.find(flt, session=s, limit=0 if many else 1)) if self.store.logging() else []
            r = (self.c.delete_many if many else self.c.delete_one)(flt, session=s)
            self.store._log(self.name, [(d["_id"], d) for d in before])
            return r.deleted_count

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


class Store:
    """One MongoDB database."""

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
        """``{collection: [field | (field, ...) | {"fields": [...], "unique": True}]}``."""
        for coll, idx in spec.items():
            for i in idx:
                if isinstance(i, str):
                    i = {"fields": [i]}
                elif isinstance(i, tuple):
                    i = {"fields": list(i)}
                self.db[coll].create_index([(f, ASC) for f in i["fields"]], unique=bool(i.get("unique")))

    def drop_database(self) -> None:
        self.client.drop_database(self.name)

    def close(self) -> None:
        """Done with this database. The connection pool is shared by every Store on the same server, and stays."""
        self._colls.clear()

    def collections(self) -> list[str]:
        return sorted(n for n in self.db.list_collection_names() if not n.startswith(("files.", "system.")))

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

    def __enter__(self):
        self.lock.acquire()
        try:
            if self._depth == 0:
                self._session = self.client.start_session()
                self._session.start_transaction(read_concern=ReadConcern("snapshot"),
                                                write_concern=WriteConcern("majority"))
                self._owner = threading.get_ident()
                self._undo = []
            else:
                self._undo.append([])
        except BaseException:
            self.lock.release()
            raise
        self._depth += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self._depth -= 1
        replay = []
        try:
            if self._depth == 0:
                s, self._session = self._session, None
                try:
                    if exc_type:
                        if s.in_transaction:
                            s.abort_transaction()
                    else:
                        s.commit_transaction()
                finally:
                    s.end_session()
                    self._owner = None
                    self._undo = []
                    survivors, self._survivors = self._survivors, []
                replay = [x[1] for x in survivors if exc_type or x[2]]
            elif exc_type:
                log = self._undo.pop()
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
                with self:
                    fn()
        finally:
            self.lock.release()
        return False

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
