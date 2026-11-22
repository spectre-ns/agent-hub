"""A person's own credential: a passphrase, for signing in to the pages and for every electronic signature.

Tokens stay what agents, services and API clients use. A person sets their passphrase themselves, once signed in, or
with a one-time setup code a member admin issues them (or the CLI does, for the first one). Passphrases are stored
salted and hashed with scrypt and compared in constant time; setup codes and sign-in sessions are stored as hashes
only. Failed attempts are counted per member: too many in a short time and that member is locked out for a while.
Every failure, every lock and every change is on the track; no passphrase or code ever is.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from .db import utcnow

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
MIN_LENGTH = 12
MAX_LENGTH = 1024
MAX_FAILURES = 5                       # failed attempts in WINDOW before the member is locked out
WINDOW = timedelta(minutes=15)
LOCKOUT = timedelta(minutes=15)
SESSION_LIFETIME = timedelta(hours=12)
SETUP_LIFETIME = timedelta(hours=72)
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I: read aloud or copied by hand
SIGN_IN = {"id": "sign-in", "kind": "automation", "name": "Sign-in"}  # who records an attempt nobody is signed in for


def hash_passphrase(passphrase: str, salt: bytes | None = None, n: int = SCRYPT_N, r: int = SCRYPT_R,
                    p: int = SCRYPT_P) -> str:
    salt = salt if salt is not None else secrets.token_bytes(16)
    dk = hashlib.scrypt(passphrase.encode(), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=64 * 1024 * 1024)
    return f"scrypt${n}${r}${p}${salt.hex()}${dk.hex()}"


def check_passphrase(passphrase: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, want = stored.split("$")
        if algo != "scrypt":
            return False
        got = hash_passphrase(passphrase, bytes.fromhex(salt), int(n), int(r), int(p)).split("$")[-1]
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


_DUMMY = None


def _dummy() -> str:
    """A hash to check against when there is none, so an unknown member takes as long as a wrong passphrase."""
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_passphrase(secrets.token_hex(16))
    return _DUMMY


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _norm_code(code: str) -> str:
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _stamp(t: datetime) -> str:
    return t.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class CredentialsMixin:
    """Passphrases, setup codes and sign-in sessions, mixed into the Hub."""

    # ------------------------------------------------------------------ checks
    def has_passphrase(self, mid: str) -> bool:
        return self.db.passphrases.exists({"_id": mid})

    def _passphrase_problem(self, mid: str, passphrase: str) -> str | None:
        if not isinstance(passphrase, str) or len(passphrase) < MIN_LENGTH:
            return f"a passphrase is at least {MIN_LENGTH} characters: a few words you will remember"
        if len(passphrase) > MAX_LENGTH:
            return "that passphrase is too long"
        if len(set(passphrase)) < 5 or mid.lower() in passphrase.lower():
            return "that passphrase is too easy to guess: use a few words, not your id or one repeated character"
        return None

    def _locked_until(self, mid: str, now: datetime) -> datetime | None:
        row = self.db.signin_failures.get(mid)
        if row and row["locked_until"] and _parse(row["locked_until"]) > now:
            return _parse(row["locked_until"])
        return None

    def _record_failure(self, mid: str, what: str, actor: dict | None = None) -> None:
        """Count a failed attempt for ``mid`` and put it on the track; the count and the event outlive a rollback."""
        mid = str(mid)[:64]

        def write():
            now = datetime.now(timezone.utc)
            row = self.db.signin_failures.get(mid)
            count, first = 1, now
            if row and now - _parse(row["first_at"]) < WINDOW:
                count, first = row["count"] + 1, _parse(row["first_at"])
            locked = _stamp(now + LOCKOUT) if count >= MAX_FAILURES else None
            self.db.signin_failures.replace({"_id": mid, "member": mid, "count": count, "first_at": _stamp(first),
                                             "locked_until": locked})
            self.track.append(actor or SIGN_IN, "signin.failed", f"member:{mid}",
                              data={"what": what, "attempt": count, **({"locked_until": locked} if locked else {})})
        self.db.surviving(write)

    def _clear_failures(self, mid: str) -> None:
        self.db.signin_failures.delete({"_id": mid})

    def _check_locked(self, mid: str, what: str, actor: dict | None = None) -> None:
        from .hub import Denied
        until = self._locked_until(str(mid)[:64], datetime.now(timezone.utc))
        if until:
            self.db.surviving(lambda: self.track.append(actor or SIGN_IN, "signin.refused", f"member:{str(mid)[:64]}",
                                                        data={"what": what, "locked_until": _stamp(until)}))
            raise Denied(f"too many failed attempts: try again after {_stamp(until)[11:16]} UTC, or ask a member "
                         f"admin for a setup code")

    def _verify(self, mid: str, passphrase: str, what: str, actor: dict | None = None) -> dict:
        """The member, if ``passphrase`` is theirs; else the attempt is counted, recorded and refused."""
        from .hub import Denied, NotFound
        self._check_locked(mid, what, actor)
        row = self.db.passphrases.get(str(mid))
        ok = check_passphrase(str(passphrase or ""), row["hash"] if row else _dummy()) and row is not None
        member = None
        if ok:
            try:
                member = self.member(mid)
            except NotFound:
                member = None
        if not member or member["status"] != "active" or member["kind"] != "person":
            self._record_failure(mid, what, actor)
            raise Denied("that member id and passphrase do not match an active person")
        self._clear_failures(mid)
        return member

    # ------------------------------------------------------------------ signing in
    def sign_in(self, mid: str, passphrase: str) -> dict:
        """A person signs in with their member id and passphrase. Returns the member; raises Denied."""
        member = self._verify(str(mid or "").strip(), passphrase, "sign in")
        self.track.append(member, "signin.succeeded", f"member:{member['id']}")
        return member

    def reauthenticate(self, member: dict, passphrase: str, what: str = "sign") -> None:
        """A signature asks for the signer's passphrase again. Agents and services never sign."""
        from .hub import Denied
        if member.get("kind") != "person":
            raise Denied("only a person signs")
        if not passphrase:
            self.db.surviving(lambda: self.track.append(member, "access.denied", f"member:{member['id']}",
                                                        data={"what": f"{what} without giving their passphrase"}))
            raise Denied("to sign, give your passphrase again")
        if not self.has_passphrase(member["id"]):
            raise Denied("set a passphrase first (Your passphrase, from your name at the top of the page): "
                         "a signature asks for it")
        self._verify(member["id"], passphrase, what, actor=member)

    # ------------------------------------------------------------------ setting it
    def set_passphrase(self, actor: dict, mid: str, new: str, current: str | None = None) -> None:
        """A person sets their own passphrase; changing one asks for the current one."""
        from .hub import Denied, Invalid
        if actor.get("id") != mid or actor.get("kind") != "person" or actor.get("status") != "active":
            raise Denied("a passphrase is set by the person it belongs to (a member admin can issue a setup code)")
        if self.has_passphrase(mid):
            self._verify(mid, current or "", "change passphrase", actor=actor)
        problem = self._passphrase_problem(mid, new)
        if problem:
            raise Invalid(problem)
        self._store_passphrase(actor, mid, new, how="set by the member")

    def _store_passphrase(self, actor: dict, mid: str, new: str, how: str, cause: int | None = None,
                          issued_by: str | None = None) -> None:
        with self.db:
            self.db.passphrases.replace({"_id": mid, "member": mid, "hash": hash_passphrase(new), "set_at": utcnow(),
                                         "set_by": actor["id"], "issued_by": issued_by})
            self.db.sessions.update({"member": mid, "revoked": 0}, {"$set": {"revoked": 1}}, many=True)
            self._clear_failures(mid)
            self.track.append(actor, "credential.passphrase_set", f"member:{mid}", cause=cause, data={"how": how})

    def issue_setup_code(self, actor: dict, mid: str) -> dict:
        """A one-time code with which a person sets their passphrase (a first one, or a forgotten one). A member admin
        issues it, never for themselves; the CLI issues it as the installer. Only its hash is kept, and any earlier
        unused code for the member stops working."""
        from .hub import Denied, Invalid
        target = self.member(mid)
        if actor.get("kind") != "installer":
            if actor["id"] == mid:
                raise Denied("a member admin does not issue a setup code to themselves: another member admin does, "
                             "or change your passphrase with your current one")
            # whoever issues it can sign in as the member: they must reach as far as the member does. A member admin
            # does for most; for a clinician's account, a policy owner who reads what they read issues it instead
            waiting = set((self.role_request(mid) or {}).get("roles") or []) if target["kind"] == "person" else set()
            beyond = (self.reach_beyond(actor, mid, set(target["roles"]) | waiting)
                      if target["kind"] == "person" else [])
            # and a directive that lets this member see what it keeps from the actor: the actor signing in as them
            # would open the file the patient kept from them
            beyond += [f"a file whose directive keeps it from {actor['name']}"
                       for _ in [1] if target["kind"] == "person" and self.restrictions.allows_beyond(target, actor)]
            if beyond:
                if not self.can(actor, "member_admin", "practice:main") and not self.is_policy_owner(actor):
                    self.require(actor, "member_admin", "practice:main", "issue a passphrase setup code")
                self.track.append(actor, "access.denied", f"member:{mid}",
                                  data={"relation": "member_admin", "what": "issue a setup code"})
                who = [m["name"] for m in self.members("person") if m["status"] == "active" and m["id"] != mid
                       and self.is_policy_owner(m) and not self.reach_beyond(m, mid, set(target["roles"]) | waiting)
                       and not self.restrictions.allows_beyond(target, m)]
                raise Denied(f"{target['name']} reaches information {actor['name']} does not ("
                             f"{', '.join(beyond)}): a policy owner who does issues their "
                             f"setup code" + (f" ({', '.join(who)})" if who else ""))
            if not self.can(actor, "member_admin", "practice:main") and not (
                    self.is_policy_owner(actor) and target["kind"] == "person"
                    and not set(target["roles"]) & self.privileged_roles()):
                self.require(actor, "member_admin", "practice:main", "issue a passphrase setup code")
        if target["kind"] != "person":
            raise Invalid(f"{target['name']} is not a person: agents and services use tokens")
        if target["status"] != "active":
            raise Invalid(f"{target['name']} is switched off")
        raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(20))
        code = "-".join(raw[i:i + 5] for i in range(0, 20, 5))
        expires = _stamp(datetime.now(timezone.utc) + SETUP_LIFETIME)
        with self.db:
            self.db.setup_codes.update({"member": mid, "used_at": None}, {"$set": {"used_at": "superseded " + utcnow()}},
                                       many=True)
            code_hash = _sha(_norm_code(code))
            self.db.setup_codes.insert({"_id": code_hash, "code_hash": code_hash, "member": mid, "issued_by": actor["id"],
                                        "created_at": utcnow(), "expires_at": expires, "used_at": None})
            self.track.append(actor, "credential.setup_issued", f"member:{mid}", data={"expires_at": expires})
        return {"member": mid, "code": code, "expires_at": expires}

    def redeem_setup_code(self, mid: str, code: str, new: str) -> dict:
        """Set a passphrase with a setup code. Wrong codes count as failed attempts for the member."""
        from .hub import Denied, Invalid, NotFound
        mid = str(mid or "").strip()
        self._check_locked(mid, "use a setup code")
        row = self.db.setup_codes.find_one({"_id": _sha(_norm_code(code)), "member": mid})
        try:
            member = self.member(mid)
        except NotFound:
            member = None
        if (not row or row["used_at"] or _parse(row["expires_at"]) <= datetime.now(timezone.utc) or not member
                or member["status"] != "active" or member["kind"] != "person"):
            self._record_failure(mid, "use a setup code")
            raise Denied("that setup code is not valid for this member (it may have been used, or expired)")
        problem = self._passphrase_problem(mid, new)
        if problem:
            raise Invalid(problem)
        with self.db:
            self.db.setup_codes.set(row["code_hash"], {"used_at": utcnow()})
            self._store_passphrase(member, mid, new, how=f"with a setup code issued by {row['issued_by']}",
                                   issued_by=row["issued_by"])
        return member

    # ------------------------------------------------------------------ sessions (the pages' cookie)
    def start_session(self, member: dict) -> str:
        raw = "phs_" + secrets.token_urlsafe(32)
        h = _sha(raw)
        self.db.sessions.insert({"_id": h, "session_hash": h, "member": member["id"], "created_at": utcnow(),
                                 "expires_at": _stamp(datetime.now(timezone.utc) + SESSION_LIFETIME), "revoked": 0})
        return raw

    def session_member(self, raw: str) -> dict | None:
        if not raw or not raw.startswith("phs_"):
            return None
        row = self.db.sessions.find_one({"_id": _sha(raw), "revoked": 0})
        if not row or _parse(row["expires_at"]) <= datetime.now(timezone.utc):
            return None
        try:
            m = self.member(row["member"])
        except Exception:  # noqa: BLE001
            return None
        return m if m["status"] == "active" else None

    def end_session(self, raw: str) -> None:
        if raw:
            self.db.sessions.set(_sha(raw), {"revoked": 1})

    def end_sessions(self, mid: str) -> int:
        return self.db.sessions.update({"member": mid, "revoked": 0}, {"$set": {"revoked": 1}}, many=True)
