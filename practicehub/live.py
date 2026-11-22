"""Live co-editing of formatted text, comments anchored in it, and comparing and restoring versions.

A record type opts a markdown field in with ``live = true``. Everyone who may write the record and opens it to edit
joins one *session* for that field (``live_sessions``): the text as it stands, a sequence number, and who is in it.
Each change is an operation (``ot``) tagged with the sequence number it was made on. The server transforms it past
every operation logged since (``live_ops``), applies it, numbers it, and wakes everyone waiting for news. Clients wait
for news with a long poll; an operation is acknowledged by coming back in that ordered stream, so a client that lost
its connection sends its unacknowledged operation again, with the same id, and the server keeps it once.

The record itself changes only at a *checkpoint*: when the last editor leaves, after two quiet minutes, or when
someone presses Done. A checkpoint is one version, made through ``Hub.update_record``, that names everyone who wrote
it (its ``authors``), so a review's "not by the record's editors" counts each of them. A change made to the record
outside the session (a form, an import, a workflow) is merged into the session as one more operation; a record that
was locked meanwhile (sent for review) ends the session, and its editors are told and keep what they wrote.

Whoever may write the record may edit; the check is made on every operation, so a grant taken away mid-session stops
the next keystroke. Whoever may read it may watch, and may comment: reading a page is enough to comment on it (no
separate grant), and resolving a comment is for its author or anyone who may write the page.
"""

from __future__ import annotations

import difflib
import re
import secrets
import threading
import time

from . import ot, schema
from .hub import Conflict, Denied, Invalid, NotFound
from .store import DuplicateKey, utcnow

PRESENCE_TIMEOUT = 60      # seconds without a word from an editor before they are taken out of the session
IDLE_CHECKPOINT = 120      # seconds without a change before what was written is saved as a version
POLL_WAIT = 25             # how long a long poll is held
MAX_TEXT = 400_000         # characters a live text may grow to
MAX_OP_PARTS = 2000

INDEXES = {
    "live_sessions": ["ref"],
    "live_ops": [{"fields": ["session", "seq"], "unique": True}, {"fields": ["session", "cid"], "unique": True}],
    "live_comments": [("ref", "at")],
}



def names(people: list[str]) -> str:
    """"Jo", "Jo and Noor", "Jo, Noor and Sam"."""
    people = [p for p in people if p]
    if len(people) <= 1:
        return "".join(people)
    return ", ".join(people[:-1]) + " and " + people[-1]


def color_of(mid: str) -> int:
    """Which of the avatar colours a member has (the same one the pages give their avatar)."""
    return sum(map(ord, mid)) % 8


def live_fields(rt) -> list[dict]:
    return [f for f in (rt.fields if rt else []) if f.get("live") and f["type"] == "markdown"
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", f["path"])]


class Live:
    """The live sessions of one hub. ``hub.live`` (made on first use) is the one the pages use."""

    def __init__(self, hub, clock=time.time):
        self.hub = hub
        self.db = hub.db
        self.clock = clock
        self._conds: dict[str, threading.Condition] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()
        self.db.ensure_indexes(INDEXES)

    # ------------------------------------------------------------------ helpers
    def _cond(self, sid: str) -> threading.Condition:
        with self._guard:
            c = self._conds.get(sid)
            if c is None:
                c = self._conds[sid] = threading.Condition(threading.Lock())
            return c

    def _lock(self, sid: str) -> threading.RLock:
        with self._guard:
            lk = self._locks.get(sid)
            if lk is None:
                lk = self._locks[sid] = threading.RLock()
            return lk

    def _wake(self, sid: str) -> None:
        c = self._cond(sid)
        with c:
            c.notify_all()

    @staticmethod
    def sid(ref: str, field: str) -> str:
        return f"{ref}#{field}"

    def _field(self, ref: str, field: str):
        rtype = ref.split("/", 1)[0]
        rt = self.hub.record_type(rtype)
        if not rt:
            raise NotFound(f"there is no record type {rtype}")
        f = next((f for f in live_fields(rt) if f["name"] == field), None)
        if f is None:
            raise Invalid(f"{rt.label}: {field!r} is not edited live")
        return rt, f

    def _record(self, ref: str) -> dict:
        rec = self.hub.records.read(*ref.split("/", 1))
        if not rec:
            raise NotFound(f"there is no {ref}")
        return rec

    @staticmethod
    def _text(rec: dict, f: dict) -> str:
        v = rec.get(f["path"])
        return v if isinstance(v, str) else ""

    @staticmethod
    def _version(rec: dict) -> str:
        return str((rec.get("meta") or {}).get("versionId", ""))

    def _locked_state(self, rt, rec) -> str | None:
        lc = rt.lifecycle
        if not lc:
            return None
        st = schema.first(rec, lc["path"])
        return st if st in lc.get("locked", []) else None

    def _name(self, mid: str) -> str:
        try:
            return self.hub.member(mid)["name"]
        except NotFound:
            return mid

    def _may_edit(self, member: dict, rt, rec) -> str | None:
        """Why this member may not edit now, or None."""
        if member.get("kind") not in ("person", "agent"):
            return "only people and agents edit"
        if not self.hub.may_write(member, rt.id):
            return f"you may read {rt.plural.lower()} but not change them"
        st = self._locked_state(rt, rec)
        if st:
            moves = [t["label"] for t in rt.lifecycle["transition"] if st in t["from"] and t["by"] != "workflow"]
            return (f"a {rt.label.lower()} that is {st} is not edited"
                    + (f": {' or '.join(moves)} first" if moves else ""))
        return None

    def _members(self, s: dict) -> list[dict]:
        return [{"id": mid, "name": p.get("name", mid), "color": color_of(mid), "caret": p.get("caret"),
                 "end": p.get("end"), "since": p.get("joined")}
                for mid, p in sorted((s.get("members") or {}).items(), key=lambda kv: kv[1].get("joined", 0))]

    def _state(self, s: dict | None, *, mode: str, rec: dict | None = None, f: dict | None = None) -> dict:
        if s is None:
            return {"mode": mode, "active": False, "seq": 0, "text": self._text(rec, f) if rec else "",
                    "version": self._version(rec) if rec else "", "members": [], "pv": 0}
        return {"mode": mode, "active": True, "session": s["_id"], "seq": s["seq"], "text": s["text"],
                "version": s["base_version"], "members": self._members(s), "pv": s.get("pv", 0),
                "dirty": s["text"] != s["base_text"], "contributors": list(s.get("contributors") or [])}

    def session(self, ref: str, field: str) -> dict | None:
        return self.db.live_sessions.get(self.sid(ref, field))

    def editing(self, ref: str) -> list[dict]:
        """Who is editing the record live now, across its fields (for "Jo is editing" on the page)."""
        seen, out = set(), []
        for s in self.db.live_sessions.find({"ref": ref}):
            for m in self._members(s):
                if m["id"] not in seen:
                    seen.add(m["id"])
                    out.append(m)
        return out

    # ------------------------------------------------------------------ joining and leaving
    def join(self, member: dict, ref: str, field: str) -> dict:
        """Open the field to edit together (or, for someone who may only read it, to watch). The state to start
        from: the text, its sequence number, and who else is in."""
        rt, f = self._field(ref, field)
        self.hub.require(member, "reader", f"data_class:{rt.data_class}", f"read {rt.id} records")
        sid = self.sid(ref, field)
        with self._lock(sid):
            rec = self._record(ref)
            why = self._may_edit(member, rt, rec)
            s = self.db.live_sessions.get(sid)
            if why:
                out = self._state(s, mode="watch", rec=rec, f=f)
                out["why"] = why
                return out
            now = self.clock()
            if s is None:
                s = {"_id": sid, "ref": ref, "field": field, "base_version": self._version(rec),
                     "base_text": self._text(rec, f), "base_seq": 0, "text": self._text(rec, f), "seq": 0,
                     "members": {}, "contributors": [], "pv": 0, "started": utcnow(), "last_op": now,
                     "checkpoints": 0}
                try:
                    self.db.live_sessions.insert(s)
                except DuplicateKey:  # another worker started it a moment ago
                    s = self.db.live_sessions.get(sid)
            else:
                s = self._sync_outside(s, rt, f) or s
                if s.get("ended"):
                    return {**self._state(None, mode="watch", rec=self._record(ref), f=f), "why": s["ended"]}
            first = member["id"] not in (s.get("members") or {})
            self.db.live_sessions.update({"_id": sid}, {
                "$set": {f"members.{member['id']}": {"name": member["name"], "joined": now, "seen": now,
                                                      "caret": None, "end": None}},
                "$inc": {"pv": 1}})
            if first:
                self.hub.track.append(member, "live.joined", ref, data={"field": field})
            s = self.db.live_sessions.get(sid)
        self._wake(sid)
        return self._state(s, mode="edit")

    def watch(self, member: dict, ref: str, field: str) -> dict:
        """The session as it stands, for someone reading the page while others edit it (nothing joined)."""
        rt, f = self._field(ref, field)
        self.hub.require(member, "reader", f"data_class:{rt.data_class}", f"read {rt.id} records")
        s = self.session(ref, field)
        return self._state(s, mode="watch", rec=None if s else self._record(ref), f=f)

    def leave(self, member: dict, ref: str, field: str) -> dict:
        """Out of the session (the tab closed, or the editor left the page). The last one out saves."""
        sid = self.sid(ref, field)
        with self._lock(sid):
            s = self.db.live_sessions.get(sid)
            if not s or member["id"] not in (s.get("members") or {}):
                return {"left": True}
            self.db.live_sessions.update({"_id": sid}, {"$unset": {f"members.{member['id']}": ""}, "$inc": {"pv": 1}})
            s = self.db.live_sessions.get(sid)
            saved = None
            if not s.get("members"):
                saved = self._end(s, member, "everyone left")
        self._wake(sid)
        return {"left": True, "saved": saved}

    def done(self, member: dict, ref: str, field: str) -> dict:
        """Save what has been written now, as one version, and leave."""
        sid = self.sid(ref, field)
        with self._lock(sid):
            s = self.db.live_sessions.get(sid)
            saved = self.checkpoint(s, member, "done") if s else None
        out = self.leave(member, ref, field)
        return {**out, "saved": saved or out.get("saved")}

    def _end(self, s: dict, member: dict | None, reason: str) -> int | None:
        """Save, then close the session. A session whose record was locked meanwhile closes without saving."""
        saved = None
        if not s.get("ended"):
            saved = self.checkpoint(s, member, reason)
        self.db.live_sessions.delete({"_id": s["_id"]})
        self.db.live_ops.delete({"session": s["_id"]})
        self.db.live_ended.replace({"_id": s["_id"], "reason": s.get("ended") or reason, "at": self.clock(),
                                    "text": s["text"], "seq": s["seq"]})
        return saved

    # ------------------------------------------------------------------ changes
    def submit(self, member: dict, ref: str, field: str, cid: str, base: int, op, caret=None) -> dict:
        """One change, made on the text at sequence number ``base``. Returns its sequence number. The same ``cid``
        sent twice (after a lost connection) is kept once."""
        rt, f = self._field(ref, field)
        sid = self.sid(ref, field)
        if not isinstance(cid, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{6,80}", cid):
            raise Invalid("an operation needs an id")
        if not isinstance(op, list) or len(op) > MAX_OP_PARTS:
            raise Invalid("an operation is a list of at most 2000 parts")
        try:
            op = ot.normalize(op)
        except ot.OTError as exc:
            raise Invalid(str(exc)) from None
        with self._lock(sid):
            rec = self._record(ref)
            why = self._may_edit(member, rt, rec)  # checked on every operation: a grant taken away stops the next one
            if why:
                self.hub.track.append(member, "access.denied", ref, data={"what": f"edit {field} live"})
                raise Denied(why)
            s = self.db.live_sessions.get(sid)
            if s is None:
                raise NotFound("the editing session ended: open the page again")
            s = self._sync_outside(s, rt, f) or s
            if s.get("ended"):
                raise Invalid(s["ended"])
            dup = self.db.live_ops.find_one({"session": sid, "cid": cid})
            if dup:
                return {"seq": dup["seq"], "duplicate": True}
            if not isinstance(base, int) or isinstance(base, bool) or base < 0 or base > s["seq"]:
                raise Invalid("the operation was made on a text the session never had: open the page again")
            for past in self.db.live_ops.find({"session": sid, "seq": {"$gt": base}}, sort="seq"):
                op, _ = ot.transform(op, past["op"])
            try:
                ot.check(op, ot.u16len(s["text"]))
                text = ot.apply(op, s["text"])
                text.encode("utf-8")
            except (ot.OTError, UnicodeEncodeError) as exc:
                raise Invalid(f"the change does not fit the text: {exc}") from None
            if len(text) > MAX_TEXT:
                raise Invalid(f"a page is at most {MAX_TEXT} characters")
            seq = s["seq"] + 1
            now = self.clock()
            self.db.live_ops.insert({"_id": f"{sid}@{seq}", "session": sid, "seq": seq, "cid": cid, "op": op,
                                     "by": member["id"], "at": now})
            members = s.get("members") or {}
            moved = {}
            for mid, p in members.items():  # everyone's caret moves with the text
                if mid == member["id"]:
                    continue
                if isinstance(p.get("caret"), int):
                    moved[f"members.{mid}.caret"] = ot.transform_index(op, p["caret"])
                if isinstance(p.get("end"), int):
                    moved[f"members.{mid}.end"] = ot.transform_index(op, p["end"])
            mine = {}
            if isinstance(caret, list) and len(caret) == 2 and all(isinstance(x, int) and 0 <= x <= ot.u16len(text)
                                                                   for x in caret):
                mine = {f"members.{member['id']}.caret": caret[0], f"members.{member['id']}.end": caret[1]}
            if member["id"] not in members:
                mine[f"members.{member['id']}"] = {"name": member["name"], "joined": now, "seen": now,
                                                   "caret": None, "end": None}
            else:
                mine[f"members.{member['id']}.seen"] = now
            done = self.db.live_sessions.find_one_and_update(
                {"_id": sid, "seq": s["seq"]},
                {"$set": {"text": text, "seq": seq, "last_op": now, **moved, **mine},
                 "$addToSet": {"contributors": member["id"]}, "$inc": {"pv": 1}})
            if done is None:  # another worker numbered an operation first: undo ours, the client sends it again
                self.db.live_ops.delete({"_id": f"{sid}@{seq}"})
                raise Conflict("the text changed as you typed: sending again")
        self._wake(sid)
        return {"seq": seq}

    def caret(self, member: dict, ref: str, field: str, at: int, end: int, seq: int) -> dict:
        """Where an editor's caret (or selection) is, in the text at ``seq``: shown to the others."""
        sid = self.sid(ref, field)
        with self._lock(sid):
            s = self.db.live_sessions.get(sid)
            if not s or member["id"] not in (s.get("members") or {}):
                return {"ok": False}
            if not all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in (at, end, seq)):
                raise Invalid("a caret is a position in the text")
            for past in self.db.live_ops.find({"session": sid, "seq": {"$gt": seq}}, sort="seq"):
                at, end = ot.transform_index(past["op"], at), ot.transform_index(past["op"], end)
            n = ot.u16len(s["text"])
            self.db.live_sessions.update({"_id": sid}, {"$set": {
                f"members.{member['id']}.caret": min(at, n), f"members.{member['id']}.end": min(end, n),
                f"members.{member['id']}.seen": self.clock()}, "$inc": {"pv": 1}})
        self._wake(sid)
        return {"ok": True}

    def poll(self, member: dict, ref: str, field: str, since: int, pv: int = -1, wait: float = POLL_WAIT) -> dict:
        """What happened after ``since``: the operations in order (each with the id its sender gave it, which is
        how a sender knows it was kept), and who is in the session. Waits up to ``wait`` seconds for news."""
        rt, f = self._field(ref, field)
        self.hub.require(member, "reader", f"data_class:{rt.data_class}", f"read {rt.id} records")
        sid = self.sid(ref, field)
        cond = self._cond(sid)
        deadline = self.clock() + max(0.0, min(wait, POLL_WAIT + 5))
        touched = False
        while True:
            s = self.db.live_sessions.get(sid)
            if s is None:
                gone = self.db.live_ended.get(sid)
                return {"ended": (gone or {}).get("reason") or "the session ended", "seq": since,
                        "text": (gone or {}).get("text")}
            if not touched and member["id"] in (s.get("members") or {}):
                self.db.live_sessions.update({"_id": sid}, {"$set": {f"members.{member['id']}.seen": self.clock()}})
                touched = True
            if since > s["seq"]:
                return {"reset": True, **self._state(s, mode="edit")}
            if s["seq"] > since or s.get("pv", 0) != pv:
                break
            left = deadline - self.clock()
            if left <= 0:
                break
            with cond:
                cond.wait(min(left, 5.0))
        ops = self.db.live_ops.find({"session": sid, "seq": {"$gt": since}}, sort="seq", limit=500)
        return {"seq": ops[-1]["seq"] if ops else since, "ops": [{"seq": o["seq"], "op": o["op"], "by": o["by"],
                                                                   "cid": o["cid"]} for o in ops],
                "members": self._members(s), "pv": s.get("pv", 0), "dirty": s["text"] != s["base_text"],
                "more": bool(ops) and ops[-1]["seq"] < s["seq"]}

    # ------------------------------------------------------------------ saving
    def checkpoint(self, s: dict, member: dict | None, reason: str) -> int | None:
        """What was written since the last checkpoint, saved as one version naming everyone who wrote it. Returns
        the version, or None when nothing changed."""
        if s is None:
            return None
        sid = s["_id"]
        with self._lock(sid):
            s = self.db.live_sessions.get(sid)
            if not s or s.get("ended") or s["text"] == s["base_text"]:
                return None
            rt, f = self._field(s["ref"], s["field"])
            authors = [a for a in (s.get("contributors") or [])]
            by = None
            for cand in ([member["id"]] if member and member.get("id") in authors else []) + authors:
                try:
                    m = self.hub.member(cand)
                except NotFound:
                    continue
                if self.hub.may_write(m, rt.id):
                    by = m
                    break
            if by is None:
                return None  # nobody who wrote it may still write it: nothing is saved in their name
            authors = [a for a in authors if self.db.members.exists({"_id": a})
                       and self.hub.may_write(self.hub.member(a), rt.id)]
            comment = "Edited by " + names([self._name(a) for a in authors])
            for attempt in range(2):
                try:
                    rec = self.hub.update_record(by, s["ref"], {f["path"]: s["text"]}, comment=comment,
                                                 expected_version=s["base_version"], authors=authors)
                    break
                except Conflict:
                    if attempt:
                        raise
                    s = self._sync_outside(s, rt, f, saving=False) or s
                    if s.get("ended"):
                        return None
                except Invalid as exc:  # the record was locked meanwhile
                    self._mark_ended(s, str(exc))
                    return None
            version = int(self._version(rec) or 0)
            from .mentions import mentioned_ids
            before = set(mentioned_ids(s["base_text"]))
            if set(mentioned_ids(s["text"])) - before:  # someone was newly named in the text: tell them
                fresh = "\n".join(line for line in s["text"].splitlines()
                                   if set(mentioned_ids(line)) - before)
                self.mentions.mention(by, s["ref"], fresh, anchor=f"field:{f['name']}", quote=fresh[:500],
                                      field=f["name"], skip_refused=True)
            self.db.live_sessions.update({"_id": sid}, {"$set": {
                "base_text": s["text"], "base_version": self._version(rec), "base_seq": s["seq"], "contributors": [],
                "checkpoints": s.get("checkpoints", 0) + 1, "saved_at": utcnow(), "saved_reason": reason},
                "$inc": {"pv": 1}})
        self._wake(sid)
        return version

    def _mark_ended(self, s: dict, why: str) -> None:
        self.db.live_sessions.update({"_id": s["_id"]}, {"$set": {"ended": why}, "$inc": {"pv": 1}})
        s["ended"] = why
        self._wake(s["_id"])

    def _sync_outside(self, s: dict, rt, f, *, saving: bool = True) -> dict | None:
        """A change made to the record outside the session (a form, a workflow, an import) is merged in as one more
        operation, then saved, so the record and the session agree. Nothing anyone wrote is dropped."""
        rec = self._record(s["ref"])
        if self._version(rec) == s["base_version"]:
            return None
        if self._locked_state(rt, rec):
            st = self._locked_state(rt, rec)
            self._mark_ended(s, f"the {rt.label.lower()} became {st} while you were editing: what you wrote since it "
                                f"was last saved is not saved (copy it below), and it is edited again once revised")
            return self.db.live_sessions.get(s["_id"]) or s
        ext = self._text(rec, f)
        sid = s["_id"]
        upd = {"base_version": self._version(rec)}
        if ext != s["base_text"]:
            op = ot.diff(s["base_text"], ext)
            for past in self.db.live_ops.find({"session": sid, "seq": {"$gt": s.get("base_seq", 0)}}, sort="seq"):
                op, _ = ot.transform(op, past["op"])
            text = ot.apply(op, s["text"])
            seq = s["seq"] + 1
            last = self.db.record_versions.find_one({"type": rt.id, "id": rec["id"]}, sort=[("version", -1)])
            self.db.live_ops.insert({"_id": f"{sid}@{seq}", "session": sid, "seq": seq,
                                     "cid": f"outside-{secrets.token_hex(6)}", "op": op,
                                     "by": (last or {}).get("actor", ""), "at": self.clock(), "outside": True})
            # the record now says ext; what the session has beyond it is saved just below
            upd.update({"text": text, "seq": seq, "base_text": ext, "base_seq": seq})
        else:
            upd.update({"base_seq": s["seq"]} if s["text"] == s["base_text"] else {})
        upd["outside_at"] = utcnow()
        self.db.live_sessions.update({"_id": sid}, {"$set": upd, "$inc": {"pv": 1}})
        s = self.db.live_sessions.get(sid)
        if saving and s["text"] != s["base_text"]:
            self.checkpoint(s, None, "merged a change made outside")
            s = self.db.live_sessions.get(sid)
        self._wake(sid)
        return s

    def sweep(self, now: float | None = None) -> dict:
        """What time makes due: editors silent for a minute leave; text quiet for two minutes is saved; a session
        nobody is in closes. The server runs this every few seconds."""
        now = self.clock() if now is None else now
        left, saved, ended = [], [], []
        for s in self.db.live_sessions.find({}):
            sid = s["_id"]
            with self._lock(sid):
                s = self.db.live_sessions.get(sid)
                if not s:
                    continue
                gone = [mid for mid, p in (s.get("members") or {}).items()
                        if now - float(p.get("seen") or 0) > PRESENCE_TIMEOUT]
                if gone:
                    self.db.live_sessions.update({"_id": sid}, {"$unset": {f"members.{m}": "" for m in gone},
                                                                "$inc": {"pv": 1}})
                    left += [(sid, m) for m in gone]
                    s = self.db.live_sessions.get(sid)
                if not s.get("members"):
                    v = self._end(s, None, "everyone left")
                    ended.append(sid)
                    if v:
                        saved.append((sid, v))
                elif s["text"] != s["base_text"] and now - float(s.get("last_op") or 0) > IDLE_CHECKPOINT:
                    v = self.checkpoint(s, None, "quiet for two minutes")
                    if v:
                        saved.append((sid, v))
            self._wake(sid)
        for row in self.db.live_ended.find({"at": {"$lt": now - 3600}}):
            self.db.live_ended.delete({"_id": row["_id"]})
        return {"left": left, "saved": saved, "ended": ended}

    def start_sweeper(self, every: float = 5.0) -> threading.Thread:
        """A daemon thread that sweeps every few seconds (the web server starts one)."""
        def loop():
            while True:
                time.sleep(every)
                try:
                    self.sweep()
                except Exception:  # noqa: BLE001 - a failed sweep is tried again on the next one
                    pass
        t = threading.Thread(target=loop, name="live-sweep", daemon=True)
        t.start()
        return t

    # ------------------------------------------------------------------ comments
    def comments(self, member: dict, ref: str) -> list[dict]:
        rtype = ref.split("/", 1)[0]
        self.hub.require(member, "reader", f"data_class:{self.hub.class_of(rtype)}", f"read {rtype} records")
        return self.db.live_comments.find({"ref": ref}, sort=[("at", 1)])

    @property
    def mentions(self):
        from .mentions import mentions_of
        return mentions_of(self.hub)

    def comment(self, member: dict, ref: str, body: str, *, quote: str = "", before: str = "", after: str = "",
                field: str = "") -> dict:
        """A comment on the record: on a quoted piece of its text (with a little of the text either side, to find it
        again after edits), or on the whole page. Anyone who may read the record may comment."""
        rtype, rid = ref.split("/", 1)
        self.hub.require(member, "reader", f"data_class:{self.hub.class_of(rtype)}", f"read {rtype} records")
        self._record(ref)
        if member.get("kind") not in ("person", "agent"):
            raise Denied("only people and agents comment")
        body = (body or "").strip()
        if not body:
            raise Invalid("write the comment")
        if len(body) > 5000:
            raise Invalid("a comment is at most 5000 characters")
        quote = (quote or "").strip()[:500]
        who = self.mentions.check(member, ref, body)  # a member who may not read the record is not mentioned
        cid = "c-" + secrets.token_hex(6)
        doc = {"_id": cid, "id": cid, "ref": ref, "field": field or "", "quote": quote,
               "before": (before or "")[-60:], "after": (after or "")[:60], "body": body, "by": member["id"],
               "agent": member["kind"] == "agent", "at": utcnow(), "replies": [], "resolved": False,
               "mentions": [m["id"] for m in who]}
        with self.db:
            self.db.live_comments.insert(doc)
            seq = self.hub.track.append(member, "comment.added", ref, data={"comment": cid, "on": "text" if quote
                                                                             else "page"})
            self.mentions.mention(member, ref, body, anchor=cid, quote=quote, field=field or "", cause=seq)
        return doc

    def reply(self, member: dict, ref: str, cid: str, body: str) -> dict:
        c = self._comment(member, ref, cid)
        body = (body or "").strip()
        if not body:
            raise Invalid("write the reply")
        if len(body) > 5000:
            raise Invalid("a reply is at most 5000 characters")
        return self._reply(member, ref, c, body)

    def _reply(self, member, ref, c, body, suggestion=None) -> dict:
        if member.get("kind") not in ("person", "agent"):
            raise Denied("only people and agents comment")
        cid = c["id"]
        who = self.mentions.check(member, ref, body)
        r = {"id": "r-" + secrets.token_hex(5), "by": member["id"], "body": body, "at": utcnow(),
             "agent": member["kind"] == "agent", "mentions": [m["id"] for m in who]}
        if suggestion:
            r["suggestion"] = suggestion
        with self.db:
            self.db.live_comments.update({"_id": cid}, {"$push": {"replies": r}, "$set": {"resolved": False}})
            seq = self.hub.track.append(member, "comment.replied", ref,
                                        data={"comment": cid, "reply": r["id"], "agent": r["agent"]})
            if suggestion:
                self.hub.track.append(member, "suggestion.proposed", ref, cause=seq,
                                      data={"comment": cid, "reply": r["id"], "field": suggestion["field"]})
            self.mentions.mention(member, ref, body, anchor=cid, quote=c.get("quote") or "", field=c.get("field") or "",
                                  cause=seq)
            self.mentions.answered(member, ref, cid, cause=seq)
        return self.db.live_comments.get(cid)

    def suggest(self, member: dict, ref: str, cid: str, field: str, find: str, text: str, note: str = "") -> dict:
        """A proposed change to the record's text, as a reply in a thread: ``find`` (words now in ``field``, once)
        becomes ``text`` only when a person who may change the record accepts it. Whoever proposes it changes
        nothing; this is how an agent edits."""
        c = self._comment(member, ref, cid)
        rt, f = self._field(ref, field)
        cur = str(self._record(ref).get(f["path"]) or "")
        if not find or cur.count(find) != 1:
            raise Invalid("a suggestion replaces words that are in the text exactly once: quote them as written")
        if len(text or "") > 20000:
            raise Invalid("a suggestion is at most 20000 characters")
        sug = {"field": f["name"], "find": find, "text": text or "", "status": "open"}
        return self._reply(member, ref, c, (note or "").strip() or "Suggested a change.", suggestion=sug)

    def decide_suggestion(self, member: dict, ref: str, cid: str, rid: str, accept: bool) -> dict:
        """A person who may change the record accepts a suggestion (saved as their change, naming who proposed it)
        or rejects it. An agent never decides one, its own or another's."""
        c = self._comment(member, ref, cid)
        r = next((x for x in c["replies"] if x["id"] == rid and x.get("suggestion")), None)
        if not r:
            raise NotFound("there is no such suggestion")
        if member["kind"] != "person":
            raise Denied("a person accepts or rejects a suggestion")
        rtype = ref.split("/", 1)[0]
        self.hub.require(member, "writer", f"data_class:{self.hub.class_of(rtype)}", f"change {rtype} records")
        sug = r["suggestion"]
        if sug["status"] != "open":
            raise Invalid(f"this suggestion was {sug['status']} already")
        if accept:
            rt, f = self._field(ref, sug["field"])
            if self.editing(ref):
                raise Invalid("people are editing it now: accept the suggestion once they are done")
            cur = self._record(ref)
            now = str(cur.get(f["path"]) or "")
            if now.count(sug["find"]) != 1:
                raise Invalid("the text it changes is no longer there as it was: it cannot be applied")
            proposer = self._name(r["by"])
            self.hub.update_record(member, ref, {f["path"]: now.replace(sug["find"], sug["text"], 1)},
                                   expected_version=self._version(cur),
                                   comment=f"Accepted a suggestion from {proposer}")
        status = "accepted" if accept else "rejected"
        with self.db:
            self.db.live_comments.update({"_id": cid, "replies.id": rid}, {"$set": {
                "replies.$.suggestion.status": status, "replies.$.suggestion.by": member["id"],
                "replies.$.suggestion.at": utcnow()}})
            self.hub.track.append(member, f"suggestion.{status}", ref,
                                  data={"comment": cid, "reply": rid, "proposed_by": r["by"]})
        return self.db.live_comments.get(cid)

    def resolve(self, member: dict, ref: str, cid: str, resolved: bool = True) -> dict:
        """Resolve a thread, or open it again: its author, or anyone who may write the record."""
        c = self._comment(member, ref, cid)
        if c["by"] != member["id"] and not self.hub.may_write(member, ref.split("/", 1)[0]):
            raise Denied("a comment is resolved by whoever wrote it, or by someone who may change the page")
        with self.db:
            self.db.live_comments.set(cid, {"resolved": bool(resolved), "resolved_by": member["id"] if resolved
                                            else None, "resolved_at": utcnow() if resolved else None})
            self.hub.track.append(member, "comment.resolved" if resolved else "comment.reopened", ref,
                                  data={"comment": cid})
        return self.db.live_comments.get(cid)

    def _comment(self, member, ref, cid) -> dict:
        rtype = ref.split("/", 1)[0]
        self.hub.require(member, "reader", f"data_class:{self.hub.class_of(rtype)}", f"read {rtype} records")
        c = self.db.live_comments.get(cid)
        if not c or c["ref"] != ref:
            raise NotFound("there is no such comment")
        return c

    @staticmethod
    def anchored(c: dict, plain: str) -> bool:
        """Whether a comment's quote is still in the text (shown read as plain words)."""
        q = re.sub(r"\s+", " ", c.get("quote") or "").strip()
        return not q or q in re.sub(r"\s+", " ", plain)

    # ------------------------------------------------------------------ versions
    def restore(self, member: dict, ref: str, version: int) -> dict:
        """Make an earlier version's content the record's again, as a new version (nothing in between is lost: it
        stays in the history). Not while people are editing it live; not its place in its lifecycle, nor what only
        workflows set."""
        rtype, rid = ref.split("/", 1)
        rt = self.hub.record_type(rtype)
        if not rt:
            raise NotFound(f"there is no record type {rtype}")
        self.hub.require(member, "writer", f"data_class:{rt.data_class}", f"write {rtype} records")
        if self.editing(ref):
            raise Invalid("people are editing it now: restore a version once they are done")
        row = self.db.record_versions.get(f"{ref}@{int(version)}")
        if not row:
            raise NotFound(f"{ref} has no version {version}")
        cur = self._record(ref)
        keep = {rt.lifecycle["path"].split(".")[0]} if rt.lifecycle else set()
        keep |= {f["path"].split(".")[0] for f in rt.fields if f.get("by") == "workflow"}
        old = row["data"]
        data = {k: v for k, v in old.items() if k not in ("resourceType", "id", "meta") and k not in keep}
        for k in cur:  # a field the old version did not have is cleared
            if k not in old and k not in ("resourceType", "id", "meta") and k not in keep:
                data[k] = None
        return self.hub.update_record(member, ref, data, expected_version=self._version(cur),
                                      comment=f"Restored version {int(version)}")


def word_diff(a: str, b: str) -> list[tuple[str, str]]:
    """The change from a to b, word by word: ("same" | "ins" | "del", text) runs, whitespace kept."""
    ta, tb = re.findall(r"\s+|[^\s]+", a or ""), re.findall(r"\s+|[^\s]+", b or "")
    out: list[tuple[str, str]] = []

    def add(kind, words):
        if not words:
            return
        if out and out[-1][0] == kind:
            out[-1] = (kind, out[-1][1] + words)
        else:
            out.append((kind, words))
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if tag == "equal":
            add("same", "".join(ta[i1:i2]))
        else:
            add("del", "".join(ta[i1:i2]))
            add("ins", "".join(tb[j1:j2]))
    return out


def live_of(hub) -> Live:
    lv = getattr(hub, "_live", None)
    if lv is None:
        lv = hub._live = Live(hub)
    return lv
