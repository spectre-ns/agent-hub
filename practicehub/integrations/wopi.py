"""The hub as a WOPI host for Collabora Online: a record's office attachment, opened and edited in the browser.

Collabora calls these (``/wopi/files/<file_id>``, every call with ``?access_token=``):

* **CheckFileInfo** (GET): the file's name, size and version, who the user is, and whether they may write.
* **GetFile** (GET ``/contents``): the bytes, read through the record like any download (permission, audit).
* **PutFile** (POST ``/contents``): a save. See *Saving* below.
* **Lock, Unlock, RefreshLock, GetLock** (POST, ``X-WOPI-Override``): one lock per document while it is open.
  PutRelativeFile ("save as") is not offered (``UserCanNotWriteRelative``).

**Identity.** ``file_id`` is opaque: an HMAC of the record, its attachment field and the blob first opened. Everyone
who opens that attachment while a session on it is open joins the same ``file_id``, so Collabora puts them in one
editing session. The **access token** is short-lived (10 hours; a page load issues a new one), an HMAC with the hub's
secret over the member, the ``file_id``, whether it may write and the hub's origin, and is checked on every call. The
member must still be active and still pass the hub's read (and write) checks: a kill switch or a role taken away
stops the next call.

**Saving.** Collabora saves often (autosave). An autosave keeps the bytes as the session's working copy (a blob in
GridFS, named in the ``office_drafts`` collection) and makes no record version. An explicit save, the save when the
last person leaves (``IsExitSave``), or the unlock that ends the session (or a working copy left idle for an hour)
makes one: the record's attachment is changed to the new blob through ``Hub.update_record``, so the history keeps
every saved version, who saved it, and a comment naming everyone whose edits went into it. Collabora sends each save
with the token of the person who edited last, so those are the names it gives.

A signature covers a record's contents, the attachment's blob included, so a new version is not signed by a
signature on the old one; the record page says so.

**Conflicts.** A save carries ``X-COOL-WOPI-Timestamp``, the ``LastModifiedTime`` Collabora last had. If the file
changed since (another save, or a new file attached on the record page), the save is refused with 409 and
``COOLStatusCode: 1010``, and Collabora asks the user whether to overwrite or reload.

**Proof keys.** When Collabora's discovery has a proof key, every call must carry a valid proof (``collabora.py``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

from .. import schema
from ..hub import Denied, HubError, NotFound
from . import collabora as cool
from .. import decimals

TOKEN_TTL = 10 * 3600  # seconds
LOCK_TTL = 30 * 60
IDLE_COMMIT = 3600  # a working copy idle this long becomes a version (the session ended without saying so)
DRAFTS = "office_drafts"
EDITED_BY = "Edited in Collabora Online by "


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Wopi:
    def __init__(self, hub, collabora: cool.Collabora):
        self.hub, self.cool = hub, collabora
        self.key = hmac.new(hub.track.secret.encode(), b"practicehub wopi v1", hashlib.sha256).digest()

    @property
    def db(self):
        return self.hub.db[DRAFTS]

    # ------------------------------------------------------------------------------------------ ids and tokens
    def file_id(self, ref: str, field: str, blob: str) -> str:
        return hmac.new(self.key, f"file|{ref}|{field}|{blob}".encode(), hashlib.sha256).hexdigest()[:40]

    def issue(self, member: dict, file_id: str, *, write: bool, origin: str, ttl: int = TOKEN_TTL) -> tuple[str, int]:
        """An access token and its ``access_token_ttl``: its expiry, in milliseconds since the epoch."""
        exp = int((time.time() + ttl) * 1000)
        body = _b64(json.dumps({"m": member["id"], "f": file_id, "w": int(bool(write)), "o": origin, "exp": exp},
                               separators=(",", ":"), sort_keys=True, default=decimals.jsonable).encode())
        mac = _b64(hmac.new(self.key, f"token|{body}".encode(), hashlib.sha256).digest())
        return f"{body}.{mac}", exp

    def verify(self, token: str, file_id: str) -> dict | None:
        """The token's claims, if it is ours, unexpired, and for this file; else None."""
        body, _, mac = (token or "").partition(".")
        want = _b64(hmac.new(self.key, f"token|{body}".encode(), hashlib.sha256).digest())
        if not body or not hmac.compare_digest(mac.encode(), want.encode()):
            return None
        try:
            claims = json.loads(_unb64(body))
        except ValueError:
            return None
        if not isinstance(claims, dict) or claims.get("f") != file_id or claims.get("exp", 0) <= time.time() * 1000:
            return None
        return claims

    # ------------------------------------------------------------------------------------------ the record's file
    def _field(self, rtype: str, field: str) -> dict:
        rt = self.hub.record_type(rtype)
        f = rt.field(field) if rt else None
        if not f or f["type"] != "attachment":
            raise NotFound(f"{rtype} has no attachment field {field!r}")
        return f

    def attachment(self, rec: dict, field: str, blob: str) -> dict | None:
        """The attachment in the record's field that names this blob, as the record holds it now."""
        f = self._field(rec["resourceType"], field)
        now = rec.get(f["path"].removesuffix(".*"))
        for a in (now if isinstance(now, list) else [now]):
            if isinstance(a, dict) and a.get("blob") == blob:
                return a
        return None

    def locked(self, rec: dict) -> bool:
        rt = self.hub.record_type(rec["resourceType"])
        lc = rt.lifecycle if rt else None
        return bool(lc and schema.first(rec, lc["path"]) in lc.get("locked", []))

    def may_edit(self, member: dict, rec: dict) -> bool:
        """The hub's own write check for the record, and its lifecycle: a released document opens read-only."""
        return (member.get("status") == "active" and self.hub.may_write(member, rec["resourceType"])
                and not self.locked(rec))

    def open(self, member: dict, ref: str, field: str, blob: str) -> dict:
        """The session for this attachment: the open one whose current blob this is, else a new one. Reading the
        record is checked here; the attachment must be the one the record holds now."""
        rec = self.hub.read_record(member, ref)
        a = self.attachment(rec, field, blob)
        if not a:
            raise NotFound("the record does not hold that file now (it may have been saved again): reload the record")
        sess = self.db.find_one({"ref": ref, "field": field, "base": blob})
        if sess:
            return sess
        fid = self.file_id(ref, field, blob)  # a session on this blob that moved on, and is back: it starts again
        sess = {"_id": fid, "ref": ref, "field": field, "origin": blob, "base": blob, "working": None,
                "name": a.get("name") or f"{blob[:12]}", "contentType": a.get("contentType") or "",
                "modified": _iso(_now()), "editors": [], "lock": None, "updated_at": _iso(_now())}
        self.db.replace(sess)
        return sess

    def current_blob(self, sess: dict) -> str:
        return sess.get("working") or sess["base"]

    # ------------------------------------------------------------------------------------------ versions
    def commit(self, member: dict, sess: dict, *, reason: str = "save") -> dict | None:
        """Make the session's working copy a version of the record: the attachment now names its blob."""
        work = sess.get("working")
        if not work:
            return None
        hub, ref = self.hub, sess["ref"]
        rtype = ref.split("/", 1)[0]
        f = self._field(rtype, sess["field"])
        path = f["path"].removesuffix(".*")
        rec = hub.read_record(member, ref)
        meta = hub.db.file_meta(work) or {}
        new = {**(self.attachment(rec, sess["field"], sess["base"]) or {}), "blob": work, "size": meta.get("size"),
               "contentType": sess.get("contentType") or meta.get("contentType") or "application/octet-stream",
               "name": sess["name"]}
        now = rec.get(path)
        if isinstance(now, list):
            value = [new if isinstance(x, dict) and x.get("blob") == sess["base"] else x for x in now]
            if new not in value:
                value.append(new)
        else:
            value = new
        names = [self._name(m) for m in (sess.get("editors") or [member["id"]])]
        # the working copy was saved by one of the session's editors; whoever's save ends the session commits it
        rec = hub.update_record(member, ref, {path: value}, comment=EDITED_BY + ", ".join(names), _files=(work,))
        self.db.update({"_id": sess["_id"]}, {"$set": {"base": work, "working": None, "editors": [],
                                                        "updated_at": _iso(_now())}})
        return rec

    def _name(self, mid: str) -> str:
        try:
            return self.hub.member(mid)["name"]
        except HubError:
            return mid

    def settle(self, ref: str) -> None:
        """Working copies of this record left idle (Collabora went away without its exit save) become versions,
        saved as the last person who edited them."""
        cutoff = _iso(_now() - timedelta(seconds=IDLE_COMMIT))
        for sess in self.db.find({"ref": ref, "working": {"$ne": None}, "updated_at": {"$lt": cutoff}}):
            lock = sess.get("lock")
            if lock and lock.get("exp", "") > _iso(_now()):
                continue  # still open in Collabora
            editors = sess.get("editors") or []
            try:
                m = self.hub.member(editors[-1]) if editors else None
                if m and m["status"] == "active":
                    self.commit(m, sess, reason="idle")
            except HubError:
                pass

    # ------------------------------------------------------------------------------------------ WOPI
    def handle(self, method: str, path: str, query: dict, headers: dict, body: bytes, raw_path: str):
        """One WOPI call: (status, headers, body). ``path`` is ``/wopi/files/<id>[/contents]``."""
        parts = path.rstrip("/").split("/")
        if len(parts) not in (4, 5) or parts[:3] != ["", "wopi", "files"] or (len(parts) == 5 and parts[4] != "contents"):
            return self._json(404, {"error": "no such WOPI endpoint"})
        fid, contents = parts[3], len(parts) == 5
        token = query.get("access_token", "")
        d = self.cool.discovery()
        if d and d.proof and not cool.check_proof(d, token, self.cool.wopi_base + raw_path, headers):
            self.cool.forget()  # keys may have rotated: the next call reads them again
            d = self.cool.discovery()
            if not (d and d.proof and cool.check_proof(d, token, self.cool.wopi_base + raw_path, headers)):
                return self._json(401, {"error": "the WOPI proof is not valid"})
        claims = self.verify(token, fid)
        if not claims:
            return self._json(401, {"error": "the access token is not valid, or has expired"})
        sess = self.db.get(fid)
        if not sess:
            return self._json(404, {"error": "no such file"})
        try:
            member = self.hub.member(claims["m"])
        except HubError:
            return self._json(401, {"error": "no such member"})
        if member.get("status") != "active":
            return self._json(401, {"error": "the member is not active"})
        try:
            if method == "GET" and not contents:
                return self._json(200, self.check_file_info(member, claims, sess))
            if method == "GET" and contents:
                return self.get_file(member, sess)
            if method == "POST" and contents:
                return self.put_file(member, claims, sess, headers, body)
            if method == "POST":
                return self.override(member, claims, sess, headers)
        except Denied as exc:
            return self._json(403, {"error": str(exc)})
        except NotFound as exc:
            return self._json(404, {"error": str(exc)})
        except HubError as exc:
            return self._json(409, {"error": str(exc)})
        return self._json(405, {"error": "method not allowed"})

    @staticmethod
    def _json(code: int, obj, extra: dict | None = None):
        return code, {"Content-Type": "application/json", **(extra or {})}, json.dumps(obj, default=decimals.jsonable).encode()

    def check_file_info(self, member: dict, claims: dict, sess: dict) -> dict:
        rec = self.hub.read_record(member, sess["ref"])
        blob = self.current_blob(sess)
        meta = self.hub.db.file_meta(blob) or {}
        write = bool(claims.get("w")) and self.may_edit(member, rec)
        if write and member["id"] not in (sess.get("joined") or []):
            self.db.update({"_id": sess["_id"]}, {"$addToSet": {"joined": member["id"]}})
        rtype, rid = sess["ref"].split("/", 1)
        first = self.hub.db.record_versions.find_one({"type": rtype, "id": rid}, sort=[("version", 1)])
        self.hub.note_view(member, sess["ref"], how="office")
        return {
            "BaseFileName": sess["name"], "Size": int(meta.get("size") or 0), "Version": blob,
            "OwnerId": (first or {}).get("actor") or "practice", "UserId": member["id"],
            "UserFriendlyName": member["name"], "UserCanWrite": write, "ReadOnly": not write,
            "UserCanNotWriteRelative": True, "UserCanRename": False, "SupportsRename": False,
            "SupportsLocks": True, "SupportsUpdate": True, "SupportsGetLock": True,
            "PostMessageOrigin": claims.get("o") or "", "LastModifiedTime": sess["modified"],
            "DisablePrint": False, "DisableExport": False, "DisableCopy": False, "HidePrintOption": False,
            "HideExportOption": False, "HideSaveOption": False, "EnableShare": False, "IsAdminUser": False,
            "UserExtraInfo": {},
        }

    def get_file(self, member: dict, sess: dict):
        if sess.get("working"):  # the session's working copy: no record names it yet, so read the record instead
            self.hub.read_record(member, sess["ref"])
            got = self.hub.db.get_file(sess["working"])
            if not got:
                raise NotFound("the working copy is missing")
            data = got[0]
            self.hub.note_view(member, sess["ref"], how="file")
        else:
            data, _ = self.hub.read_file(member, sess["ref"], sess["base"])
        return 200, {"Content-Type": "application/octet-stream", "X-WOPI-ItemVersion": self.current_blob(sess)}, data

    def _lock_of(self, sess: dict) -> str:
        lock = sess.get("lock") or {}
        return lock.get("id", "") if lock.get("exp", "") > _iso(_now()) else ""

    def put_file(self, member: dict, claims: dict, sess: dict, headers: dict, body: bytes):
        hdr = lambda name: headers.get(f"x-cool-wopi-{name}", headers.get(f"x-lool-wopi-{name}", ""))
        held = self._lock_of(sess)
        if held and headers.get("x-wopi-lock", "") != held:
            return self._json(409, {"error": "the file is locked"}, {"X-WOPI-Lock": held})
        rec = self.hub.read_record(member, sess["ref"])
        if not claims.get("w") or not self.may_edit(member, rec):
            raise Denied("you may read this document but not change it"
                         + (": revise the record first" if self.locked(rec) else ""))
        stamp = hdr("timestamp")
        changed = self.attachment(rec, sess["field"], sess["base"]) is None  # replaced on the record page since
        if (stamp and stamp != sess["modified"]) or (changed and stamp):
            return self._json(409, {"COOLStatusCode": 1010, "LOOLStatusCode": 1010,
                                    "error": "the file changed since it was opened"})
        if changed:  # an overwrite the user chose, after the conflict: it replaces what the record holds now
            f = self._field(sess["ref"].split("/", 1)[0], sess["field"])
            now = rec.get(f["path"].removesuffix(".*"))
            cur = now[-1] if isinstance(now, list) and now else now
            if isinstance(cur, dict) and cur.get("blob"):
                self.db.update({"_id": sess["_id"]}, {"$set": {"base": cur["blob"]}})
                sess["base"] = cur["blob"]
        if len(body) > self.hub.MAX_FILE:
            return self._json(413, {"error": "the file is too large"})
        rtype = sess["ref"].split("/", 1)[0]
        att = self.hub.add_file(member, rtype, body, sess["name"], sess.get("contentType") or cool.mime_of(
            cool.extension(sess["name"])))
        modified = _iso(_now())
        editors = [m for m in (sess.get("editors") or []) if m != member["id"]] + [member["id"]]
        self.db.update({"_id": sess["_id"]}, {"$set": {"working": att["blob"], "modified": modified,
                                                        "editors": editors, "updated_at": modified}})
        sess = self.db.get(sess["_id"])
        autosave = hdr("isautosave").lower() == "true"
        exitsave = hdr("isexitsave").lower() == "true"
        if exitsave or not autosave:
            if sess["working"] == sess["base"]:  # saved, but unchanged: no new version
                self.db.update({"_id": sess["_id"]}, {"$set": {"working": None, "editors": []}})
            else:
                self.commit(member, sess, reason="exit" if exitsave else "save")
        return self._json(200, {"LastModifiedTime": modified}, {"X-WOPI-ItemVersion": att["blob"]})

    def override(self, member: dict, claims: dict, sess: dict, headers: dict):
        op = headers.get("x-wopi-override", "").upper()
        want, held = headers.get("x-wopi-lock", ""), self._lock_of(sess)
        exp = _iso(_now() + timedelta(seconds=LOCK_TTL))
        conflict = lambda: self._json(409, {"error": "lock mismatch"}, {"X-WOPI-Lock": held})
        if op == "GET_LOCK":
            return 200, {"X-WOPI-Lock": held}, b""
        if op == "LOCK" and headers.get("x-wopi-oldlock"):  # unlock and relock
            if held != headers["x-wopi-oldlock"]:
                return conflict()
            self.db.update({"_id": sess["_id"]}, {"$set": {"lock": {"id": want, "exp": exp, "by": member["id"]}}})
            return 200, {}, b""
        if op in ("LOCK", "REFRESH_LOCK"):
            if not want or (held and held != want) or (op == "REFRESH_LOCK" and held != want):
                return conflict()
            self.db.update({"_id": sess["_id"]}, {"$set": {"lock": {"id": want, "exp": exp, "by": member["id"]}}})
            return 200, {}, b""
        if op == "UNLOCK":
            if held != want:
                return conflict()
            self.db.update({"_id": sess["_id"]}, {"$set": {"lock": None}})
            sess = self.db.get(sess["_id"])
            if sess.get("working"):  # the session ended with edits not yet a version
                editors = sess.get("editors") or [member["id"]]
                try:
                    by = self.hub.member(editors[-1])
                except HubError:
                    by = member
                rec = self.hub.read_record(by, sess["ref"])
                if self.may_edit(by, rec):
                    self.commit(by, sess, reason="closed")
            return 200, {}, b""
        if op == "PUT_USER_INFO":
            return 200, {}, b""
        return self._json(501, {"error": f"{op or 'that operation'} is not supported"})
