"""The practice's records, behind one small interface.

Records are JSON documents with a ``resourceType`` and an ``id``. A general practice uses FHIR R4 resources (Patient,
Appointment, Composition, MedicationRequest...), so the same records can live in the hub's own database (the local
backend) or on a FHIR server such as Medplum, without the workflows noticing. Other kinds of practice define their own
record types, which stay on the local backend (or a back-office system such as ERPNext, later).

Every record type belongs to a *data class* (demographics, clinical, billing...): permissions are granted per data
class, so a scheduling agent can be given appointments and contact details but never a clinical note.
"""

from __future__ import annotations

import json
import re
import secrets
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol

from .db import Database, utcnow


def subject_of(rec: dict, subject_types=frozenset()) -> str | None:
    """The record this one is about (``Patient/<id>``, ``Client/<id>``), used to gather a file or chart. The hub
    passes its own answer (from the record types' ``subject`` flag and ``rel = "subject"`` references) to each store;
    this is the fallback for a store used on its own."""
    if rec.get("resourceType") in subject_types:
        return f"{rec['resourceType']}/{rec['id']}" if rec.get("id") else None
    def refs(v, depth=0):
        if depth > 3:
            return
        if isinstance(v, dict):
            if isinstance(v.get("reference"), str):
                yield v["reference"]
            for x in v.values():
                yield from refs(x, depth + 1)
        elif isinstance(v, list):
            for x in v:
                yield from refs(x, depth + 1)

    for ref in refs({k: v for k, v in rec.items() if k not in ("meta", "id", "resourceType")}):
        if ref.split("/")[0] in subject_types:
            return ref
    return None


def new_id() -> str:
    return secrets.token_hex(6)


def fold(text: str) -> str:
    """Text as a search compares it: accents taken off (NFKD, combining marks dropped) and case folded, so "cote",
    "Côté" and "COTE" are the same word."""
    return "".join(c for c in unicodedata.normalize("NFKD", str(text)) if not unicodedata.combining(c)).casefold()


def search_text(rec: dict) -> str:
    """What a search over a record looks in: the whole record, as written, folded."""
    return fold(json.dumps(rec, ensure_ascii=False))


_WORD = re.compile(r"\w+")


def words_in(rec) -> list[str]:
    """The words a record is found by: every word of its values (not its keys or ``meta``), folded, once each. A
    search word matches a word that starts with it ("hel" finds "Hélène"), so an index answers it."""
    out: set[str] = set()

    def walk(v, depth=0):
        if depth > 12:
            return
        if isinstance(v, dict):
            for k, x in v.items():
                if k != "meta":
                    walk(x, depth + 1)
        elif isinstance(v, list):
            for x in v:
                walk(x, depth + 1)
        elif isinstance(v, (str, int, float)) and not isinstance(v, bool):
            out.update(_WORD.findall(fold(v)))
    walk(rec)
    return sorted(out)


def _prefix(word: str) -> dict:
    return {"$regex": "^" + re.escape(word)}


class RecordStore(Protocol):
    def create(self, rtype: str, data: dict) -> dict: ...
    def read(self, rtype: str, rid: str) -> dict | None: ...
    def update(self, rtype: str, rid: str, data: dict) -> dict: ...
    def search(self, rtype: str | None = None, *, subject: str | None = None, limit: int | None = 200,
               text: str | None = None, field: str | None = None, since: str | None = None,
               until: str | None = None) -> list[dict]:
        """Records, newest first. ``limit`` None is every one. ``text``: every word appears in the record. ``field``
        with ``since``/``until``: a top-level field (a time, as ISO text) in [since, until)."""
        ...


class LocalRecordStore:
    """Records in the hub's own database: the ``records`` collection, one document per record (``_id`` "Type/id"),
    the record itself in ``data`` and, beside it, its subject, version and ``search``, the record folded (no accents,
    no case) so a search finds "Hélène" by "helene"."""

    def __init__(self, db: Database):
        self.db = db
        self.subject_fn = subject_of  # the hub sets its own, from its record types
        # the hub sets this too: for a record whose type gives some fields a data class of their own, the record as
        # each reader sees it (``[("_", without any of them), (class, with only that class's)]``) and a stamp of the
        # type's classes; a search by someone who may not read a class looks only in the parts they may read
        self.parts_fn = lambda rec: ([], "")

    def _indexed(self, rec: dict) -> dict:
        parts, stamp = self.parts_fn(rec)
        return {"words": words_in(rec), "ix": stamp,
                "search_parts": [{"c": c, "w": words_in(view)} for c, view in parts] or None}

    def reindex(self, rtype: str, stamp: str) -> int:
        """Rewrite the search words of ``rtype``'s records indexed under another stamp (their type's data classes
        changed, or they were written before the words were kept). Returns how many were rewritten."""
        n = 0
        for row in self.db.records.find({"type": rtype, "ix": {"$ne": stamp}}, fields=["data"]):
            self.db.records.set(row["_id"], self._indexed(row["data"]))
            n += 1
        return n

    def create(self, rtype, data):
        rec = {**data, "resourceType": rtype, "id": data.get("id") or new_id()}
        rec["meta"] = {"versionId": "1", "lastUpdated": utcnow()}
        self.db.records.insert({"_id": f"{rtype}/{rec['id']}", "type": rtype, "id": rec["id"],
                                "subject": self.subject_fn(rec), "data": rec, "version": 1,
                                "updated_at": rec["meta"]["lastUpdated"], **self._indexed(rec)})
        return rec

    def read(self, rtype, rid):
        row = self.db.records.get(f"{rtype}/{rid}")
        return row["data"] if row else None

    def update(self, rtype, rid, data):
        with self.db:
            row = self.db.records.get(f"{rtype}/{rid}")
            if not row:
                raise KeyError(f"{rtype}/{rid}")
            version = row["version"] + 1
            rec = {**data, "resourceType": rtype, "id": rid, "meta": {"versionId": str(version), "lastUpdated": utcnow()}}
            self.db.records.set(row["_id"], {"data": rec, "subject": self.subject_fn(rec), "version": version,
                                             "updated_at": rec["meta"]["lastUpdated"], **self._indexed(rec)})
            return rec

    def search(self, rtype=None, *, subject=None, limit=200, text=None, field=None, since=None, until=None,
               offset=0, visible=None, plain=None):
        """``visible`` (with ``plain``): the reader may not read some fields' data classes. Each word must then be
        in a part of the record they may read: one with a data class in ``visible``, or a record of a type in
        ``plain`` (whose fields have no classes of their own). A hidden field's value never matches. ``offset``:
        skip that many (a page further on), counted in the database."""
        flt: dict = {}
        if rtype:
            flt["type"] = rtype
        if subject:
            flt["subject"] = subject
        words = [t for w in words_of(text) for t in _WORD.findall(w)][:8]
        if words:
            # every word starts a word of the record, folded as the record is: "helene" finds "Hélène"
            if visible is None:
                flt["$and"] = [{"words": _prefix(w)} for w in words]
            else:
                seen = sorted(set(visible) | {"_"})
                flt["$and"] = [{"$or": [{"type": {"$in": sorted(plain or [])}, "search_parts": None, "words": _prefix(w)},
                                        {"search_parts": {"$elemMatch": {"c": {"$in": seen}, "w": _prefix(w)}}}]}
                               for w in words]
        if field and (since or until):
            if not FIELD.fullmatch(field):
                raise ValueError(f"cannot search by {field!r}")
            rng = {}
            if since:
                rng["$gte"] = since
            if until:
                rng["$lt"] = until
            flt[f"data.{field}"] = rng
        return [r["data"] for r in self.db.records.find(flt, sort=[("updated_at", -1)], limit=limit,
                                                         skip=offset or 0, fields=["data"])]


class MedplumRecordStore:
    """FHIR R4 records on a Medplum server, reached with the hub's own client credentials.

    Medplum's access policies are a second line of defence; the hub checks data-class permissions before any call.
    """

    SUBJECT_PARAM = {"Patient": None, "Appointment": "patient", "AllergyIntolerance": "patient",
                     "MedicationRequest": "subject", "MedicationStatement": "subject"}

    def __init__(self, base_url: str, client_id: str, client_secret: str):
        self.base = base_url.rstrip("/")
        self.client_id, self.client_secret = client_id, client_secret
        self._token = None

    def _auth(self) -> str:
        if not self._token:
            body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": self.client_id,
                                           "client_secret": self.client_secret}).encode()
            req = urllib.request.Request(self.base + "/oauth2/token", data=body, method="POST",
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                self._token = json.loads(resp.read())["access_token"]
        return self._token

    def _call(self, method: str, path: str, body: dict | None = None, retry: bool = True) -> dict | None:
        req = urllib.request.Request(self.base + "/fhir/R4/" + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/fhir+json",
                                              "Authorization": f"Bearer {self._auth()}"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and retry:
                self._token = None
                return self._call(method, path, body, retry=False)
            if exc.code == 404:
                return None
            raise

    def create(self, rtype, data):
        rec = {k: v for k, v in data.items() if k not in ("id", "meta")}
        return self._call("POST", rtype, {**rec, "resourceType": rtype})

    def read(self, rtype, rid):
        return self._call("GET", f"{rtype}/{urllib.parse.quote(rid)}")

    def update(self, rtype, rid, data):
        rec = {k: v for k, v in data.items() if k != "meta"}
        return self._call("PUT", f"{rtype}/{urllib.parse.quote(rid)}", {**rec, "resourceType": rtype, "id": rid})

    def search(self, rtype=None, *, subject=None, limit=200, text=None, field=None, since=None, until=None,
               offset=0, **_):
        """FHIR search; the text and time filters are applied here, over pages of results, so none is cut short."""
        if offset:
            got = self.search(rtype, subject=subject, limit=None if limit is None else limit + offset, text=text,
                              field=field, since=since, until=until)
            return got[offset:]
        if text or field:
            out, want = [], limit
            words = words_of(text)
            # every page, matched here: FHIR's name search is by prefix, and not every server ignores accents
            for rec in self._search(rtype, subject=subject, limit=None):
                if words and not all(w in search_text(rec) for w in words):
                    continue
                if field and not _in_range(rec.get(field), since, until):
                    continue
                out.append(rec)
                if want is not None and len(out) >= want:
                    break
            return out
        return self._search(rtype, subject=subject, limit=limit)

    def _search(self, rtype=None, *, subject=None, limit=200, name=None):
        if limit is None:  # every page
            if not rtype:
                return self._page(None, subject=subject, limit=100000)
            out, page = [], 0
            while True:
                batch = self._page(rtype, subject=subject, limit=1000, name=name, _offset=page * 1000)
                out += batch
                if len(batch) < 1000:
                    return out
                page += 1
        return self._page(rtype, subject=subject, limit=limit, name=name)

    def _page(self, rtype=None, *, subject=None, limit=200, name=None, _offset=0):
        if rtype is None:
            if not subject:
                return []
            pid = subject.split("/", 1)[1]
            bundle = self._call("GET", f"Patient/{urllib.parse.quote(pid)}/$everything?_count={limit}") or {}
            return [e["resource"] for e in bundle.get("entry", []) if e["resource"].get("resourceType") != "Patient"]
        params = {"_count": str(limit), "_sort": "-_lastUpdated", **({"_offset": str(_offset)} if _offset else {}),
                  **({"name": name} if name else {})}
        if subject:
            if rtype == "Patient":
                params["_id"] = subject.split("/", 1)[1]
            else:
                params[self.SUBJECT_PARAM.get(rtype) or "subject"] = subject
        bundle = self._call("GET", f"{rtype}?{urllib.parse.urlencode(params)}") or {}
        return [e["resource"] for e in bundle.get("entry", [])]


FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)*")


def words_of(text: str | None) -> list[str]:
    """The words of a search, folded as ``fold`` folds a record (at most eight)."""
    return [w for w in fold(text or "").split() if w][:8]


def _in_range(value, since, until) -> bool:
    v = str(value or "")
    return bool(v) and (not since or v >= since) and (not until or v < until)
