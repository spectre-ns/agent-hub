"""The Track: the practice's append-only, hash-chained record of everything that happened.

Every event names its actor (a person or an agent, never "the system" without one), what it did, and its cause: the
event that led to it. Following causes back always reaches a root, a person's action, an inbound event or a schedule,
so an agent's work is attributable even when no person was in the loop.

Each event's hash covers the previous event's hash, and each event is sealed with an HMAC of its hash keyed with the
hub's secret (``mac``): someone who can write the database but does not hold the secret cannot change, remove or
re-chain events without ``verify`` noticing. A decision at a gate is an electronic signature: the event additionally
carries an HMAC over (who, what, meaning, when).

Removing the newest events leaves a shorter chain that still verifies, so the head is also written to an anchor file
(``PRACTICEHUB_ANCHORS``, else ``<state>/track.anchors``; keep it, or a copy, off the database's volume). ``verify``
checks that every anchored event is still there, unchanged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from .db import Database, utcnow
from .store import DESC

GENESIS = "0" * 64


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hub_secret(state_dir: Path | None) -> str:
    """PRACTICEHUB_SECRET, else <state>/.secret, created once (mode 0600)."""
    env = os.environ.get("PRACTICEHUB_SECRET")
    if env:
        return env
    if state_dir is None:
        return "test-secret-not-for-use"
    path = Path(state_dir) / ".secret"
    if path.exists():
        return path.read_text().strip()
    value = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(value)
    return value


class Track:
    def __init__(self, db: Database, secret: str, anchors: Path | str | None = None):
        self.db = db
        self.secret = secret
        self.anchors = Path(anchors) if anchors else None

    def seal(self, digest: str) -> str:
        return hmac.new(self.secret.encode(), f"event:{digest}".encode(), hashlib.sha256).hexdigest()

    def seal_unsealed(self) -> int:
        """Seal events recorded before events were sealed, once, and only if the chain verifies as it is. Never run
        on start: anyone who can write the database could rebuild a chain without seals and have it blessed. It is
        an explicit step (``practicehub track seal-legacy``) taken by whoever holds the secret."""
        rows = self.db.events.find({"mac": None}, sort="seq", fields=["seq", "hash"])
        if not rows:
            return 0
        # Once anything is sealed, an unsealed event is a forgery (or a row whose seal was stripped), never one to
        # bless: only a track that was never sealed, and never anchored, is sealed here.
        if self.db.events.exists({"mac": {"$ne": None}}) or self._anchors():
            return 0
        check = self.verify(require_seal=False)
        if not check["ok"]:
            return 0  # leave it for verify to report; sealing would bless a broken chain
        with self.db:
            for r in rows:
                self.db.events.set(r["seq"], {"mac": self.seal(r["hash"])})
        return len(rows)

    def sealed(self, row) -> bool:
        """True if this event row carries this hub's seal over its hash: it was written here, not into the file."""
        return bool(row["mac"]) and hmac.compare_digest(row["mac"], self.seal(row["hash"]))

    def authentic(self, row) -> bool:
        """True if this full event row is the one this hub wrote: its hash is recomputed from every column (so its
        data, actor or action were not rewritten beside a kept hash) and that hash carries this hub's seal."""
        if not self.sealed(row):
            return False
        try:
            body = {"at": row["at"], "actor": row["actor"], "actor_kind": row["actor_kind"], "action": row["action"],
                    "subject": row["subject"], "run_id": row["run_id"], "cause": row["cause"],
                    "data": row["data"], "prev": row["prev"]}
        except (ValueError, TypeError, IndexError, KeyError):
            return False
        return hmac.compare_digest(self._hash(body), row["hash"])

    def trusted(self, flt: dict, *, sort=None, limit: int | None = None) -> list:
        """Events matching ``flt`` that this hub wrote, unchanged, in ``sort`` order (default: oldest first). A
        decision made from the track (what code is in force, who reviewed it) uses these: each is checked with
        ``authentic``, which recomputes its hash from every field and checks the seal over it, so a filter on any
        field only ever selects events whose fields are the ones the hub wrote."""
        out = []
        for full in self.db.events.find(flt, sort=sort or "seq"):
            if self.authentic(full):
                out.append(full)
                if limit is not None and len(out) >= limit:
                    break
        return out

    def anchor(self) -> dict | None:
        """Write the newest event to the anchor file, if it moved on since the last anchor."""
        if not self.anchors:
            return None
        head = self.head()
        if not head:
            return None
        last = self._anchors()[-1:] or [None]
        if last[0] and last[0].get("seq") == head["seq"]:
            return last[0]
        line = {"seq": head["seq"], "hash": head["hash"], "at": utcnow()}
        line["mac"] = self.seal(f"anchor:{line['seq']}:{line['hash']}")
        self.anchors.parent.mkdir(parents=True, exist_ok=True)
        with open(self.anchors, "a") as fh:
            fh.write(canonical(line) + "\n")
        return line

    def _anchors(self) -> list[dict]:
        if not self.anchors or not self.anchors.exists():
            return []
        out = []
        for raw in self.anchors.read_text().splitlines():
            try:
                out.append(json.loads(raw))
            except ValueError:
                out.append({"bad": raw})
        return out

    def _hash(self, fields: dict) -> str:
        return hashlib.sha256(canonical(fields).encode()).hexdigest()

    def sign(self, fields: dict) -> str:
        return "hmac-sha256:" + hmac.new(self.secret.encode(), canonical(fields).encode(), hashlib.sha256).hexdigest()

    def append(self, actor: dict, action: str, subject: str = "", *, run_id: str | None = None,
               cause: int | None = None, data: dict | None = None, signed: dict | None = None) -> int:
        """Append one event and return its sequence number. ``actor`` is a member row (id, kind).

        ``signed`` makes the event an electronic signature over those fields plus the actor and time."""
        if signed is not None:
            data = {**(data or {}), "signed": signed}
        with self.db:
            last = self.head()
            prev = last["hash"] if last else GENESIS
            seq = (last["seq"] if last else 0) + 1
            at = utcnow()
            body = {"at": at, "actor": actor["id"], "actor_kind": actor["kind"], "action": action, "subject": subject,
                    "run_id": run_id, "cause": cause, "data": data or {}, "prev": prev}
            digest = self._hash(body)
            signature = None
            if signed is not None:
                signature = self.sign({**signed, "by": actor["id"], "at": at, "event_hash": digest})
            # the event's number is the head's plus one, and _id is that number: two writers that read the same
            # head cannot both append (the second insert is refused), so the chain never forks
            self.db.events.insert({"_id": seq, "seq": seq, "at": at, "actor": actor["id"], "actor_kind": actor["kind"],
                                   "action": action, "subject": subject, "run_id": run_id, "cause": cause,
                                   "data": data or {}, "prev": prev, "hash": digest, "signature": signature,
                                   "mac": self.seal(digest)})
            return seq

    def head(self) -> dict | None:
        """The newest event."""
        return self.db.events.find_one(sort=[("_id", DESC)])

    def get(self, seq: int) -> dict | None:
        return self.db.events.get(seq) if seq is not None else None

    def list(self, *, run_id: str | None = None, actor: str | None = None, limit: int = 200) -> list[dict]:
        flt: dict = {}
        if run_id:
            flt["run_id"] = run_id
        if actor:
            flt["actor"] = actor
        return self.db.events.find(flt, sort=[("_id", DESC)], limit=limit)

    def chain(self, seq: int) -> list[dict]:
        """The causal chain from the root down to this event."""
        out, seen = [], set()
        cur = self.get(seq)
        while cur and cur["seq"] not in seen:
            seen.add(cur["seq"])
            out.append(cur)
            cur = self.get(cur["cause"]) if cur["cause"] else None
        return list(reversed(out))

    CHUNK = 500  # events read at a time by verify: the database lock is held for one chunk, never the whole track

    def rows_after(self, seq: int, chunk: int | None = None):
        """Every event after ``seq``, in order, read a chunk at a time so that other requests run in between."""
        chunk = chunk or self.CHUNK
        while True:
            rows = self.db.events.find({"_id": {"$gt": seq}}, sort="_id", limit=chunk)
            yield from rows
            if len(rows) < chunk:
                return
            seq = rows[-1]["seq"]

    def verify(self, *, require_seal: bool = True, since: dict | None = None) -> dict:
        """Recompute the chain, every seal and signature, and check the anchors. Returns {'ok': bool, 'events': n,
        'problems': [...], 'head': {seq, hash}}.

        ``since`` ({seq, hash}, a head an earlier verify found good) checks only the events after it, and that the
        event at its seq is still the one it was; anchors are always all checked. ``practicehub verify`` checks the
        whole track."""
        problems, prev, n, start = [], GENESIS, 0, 0
        if since and since.get("seq"):
            row = self.get(since["seq"])
            if not row or row["hash"] != since.get("hash"):
                problems.append(f"event {since['seq']} was verified before and is missing or changed: events were "
                                f"removed or the database was replaced")
            start, prev = int(since["seq"]), since.get("hash") or GENESIS
        anchors, wanted = self._anchors_in_force(problems)
        last, last_hash = start, prev
        for row in self.rows_after(start):
            if row["seq"] in wanted:
                wanted[row["seq"]] = row["hash"]
            if require_seal and not (row.get("mac") and hmac.compare_digest(row["mac"], self.seal(row["hash"]))):
                problems.append(f"event {row['seq']}: its seal does not verify (written without the hub's secret)")
            n += 1
            body = {"at": row["at"], "actor": row["actor"], "actor_kind": row["actor_kind"], "action": row["action"],
                    "subject": row["subject"], "run_id": row["run_id"], "cause": row["cause"],
                    "data": row["data"], "prev": row["prev"]}
            if row["prev"] != prev:
                problems.append(f"event {row['seq']}: does not follow the event before it")
            if self._hash(body) != row["hash"]:
                problems.append(f"event {row['seq']}: its content was changed after it was recorded")
            if row["signature"] and not self.verify_signature({**row, "data": body["data"]}):
                problems.append(f"event {row['seq']}: its signature does not verify")
            elif "signed" in body["data"] and not row["signature"]:
                problems.append(f"event {row['seq']}: it is a signature with no signature")
            prev = last_hash = row["hash"]
            last = row["seq"]
        for seq in [s for s, h in wanted.items() if h is None]:  # anchored before this verify's start, or after its end
            row = self.get(seq)
            wanted[seq] = row["hash"] if row else ""
        for a in anchors:
            if wanted.get(a["seq"]) != a["hash"]:
                problems.append(f"event {a['seq']} was anchored on {a.get('at', '?')} and is missing or changed: "
                                f"events were removed or the database was replaced")
        return {"ok": not problems, "events": n, "problems": problems, "head": {"seq": last, "hash": last_hash}}

    def _anchors_in_force(self, problems: list) -> tuple[list[dict], dict]:
        """The anchor lines verify holds the track to, and {seq: None} for each. A line written before a sealed
        restore marker, for an event after the restored head, names an event the restore lost: the track records that
        loss (``track.restored``), so it is not reported again. Every other line must still match."""
        lines = self._anchors()
        keep: list[dict] = []
        for i, a in enumerate(lines):
            if "bad" in a:
                problems.append("an anchor line is damaged or was not written by this hub")
            elif "restored" in a:
                ev = self.get(a.get("restored"))
                if not (hmac.compare_digest(str(a.get("mac", "")), self.seal(
                        f"restored:{a.get('lost_after')}:{a.get('restored')}:{a.get('event_hash')}"))
                        and ev and ev["hash"] == a.get("event_hash") and self.authentic(ev)
                        and ev["action"] == "track.restored"):
                    problems.append("a restore marker in the anchors is damaged, was not written by this hub, or "
                                    "its track.restored event is missing")
                    continue
                keep = [k for k in keep if k["seq"] <= int(a["lost_after"])]
            elif not hmac.compare_digest(str(a.get("mac", "")), self.seal(f"anchor:{a.get('seq')}:{a.get('hash')}")):
                problems.append("an anchor line is damaged or was not written by this hub")
            else:
                keep.append(a)
        return keep, {a["seq"]: None for a in keep}

    def record_restore(self, actor: dict, lost: dict | None, note: str = "") -> int:
        """After a restore from a backup: record in the track, as ``actor``, that the events after the restored head
        were lost (up to the newest anchored one), and write a sealed marker to the anchors so verify holds the track
        to the anchors from here on. The anchors are never truncated or rewritten."""
        head = self.head() or {"seq": 0, "hash": GENESIS}
        keep, _ = self._anchors_in_force([])
        def holds(a):
            row = self.get(a["seq"])
            return bool(row) and row["hash"] == a["hash"]
        gone = [a["seq"] for a in keep if not holds(a)]
        # the events the anchors still vouch for end at the newest anchor that holds, before the first that does not
        # (events written since the restore may have taken the lost ones' numbers)
        lost_after = max([a["seq"] for a in keep if holds(a) and (not gone or a["seq"] < min(gone))], default=0)
        if gone and any(holds(a) and a["seq"] > min(gone) for a in keep):
            raise ValueError("an anchored event is missing between ones that are still there: that is not a restore "
                             "from a backup, so it is not recorded as one")
        data = {"lost_after": lost_after, "restored_head": head["hash"], "restored_seq": head["seq"], "note": note,
                "lost_through": (lost or {}).get("seq"), "lost_head": (lost or {}).get("hash")}
        seq = self.append(actor, "track.restored", "track", data=data,
                          signed={"meaning": f"I restored the track from a backup: events after {lost_after} may not "
                                             f"be the ones that were anchored", "lost_after": lost_after})
        ev = self.get(seq)
        line = {"restored": seq, "lost_after": lost_after, "event_hash": ev["hash"], "at": utcnow()}
        line["mac"] = self.seal(f"restored:{line['lost_after']}:{seq}:{ev['hash']}")
        if self.anchors:
            self.anchors.parent.mkdir(parents=True, exist_ok=True)
            with open(self.anchors, "a") as fh:
                fh.write(canonical(line) + "\n")
        self.anchor()
        return seq

    def anchored_head(self) -> dict | None:
        """The newest anchor line that verifies (seq, hash), if any."""
        problems: list = []
        keep, _ = self._anchors_in_force(problems)
        return max(keep, key=lambda a: a["seq"]) if keep else None

    def holds_secret(self) -> bool:
        """Whether this hub's secret is the one that sealed the track: its newest event carries this secret's seal.
        A step that only the server holder may take (recovering after a restore, granting an owner when none is
        left) is refused without it."""
        head = self.head()
        return bool(head) and self.authentic(head)

    def verify_signature(self, event: dict) -> bool:
        signed = (event.get("data") or {}).get("signed")
        if not event.get("signature") or signed is None:
            return False
        expected = self.sign({**signed, "by": event["actor"], "at": event["at"], "event_hash": event["hash"]})
        return hmac.compare_digest(expected, event["signature"])
