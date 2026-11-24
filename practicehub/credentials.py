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
from .workflows import acts_only_source

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
MIN_LENGTH = 12
MAX_LENGTH = 1024
MAX_FAILURES = 5                       # failed attempts in WINDOW from one address (or none known) before it locks
MEMBER_MAX_FAILURES = 20               # failed attempts on one member in WINDOW, from several addresses, before the
                                       # member is locked: one place guessing locks itself out, not the member
WINDOW = timedelta(minutes=15)
LOCKOUT = timedelta(minutes=15)
SESSION_LIFETIME = timedelta(hours=12)
SETUP_LIFETIME = timedelta(hours=72)
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I: read aloud or copied by hand
SIGN_IN = {"id": "sign-in", "kind": "automation", "name": "Sign-in"}  # who records an attempt nobody is signed in for
ACCOUNT_WORKFLOW = "account-notice"  # the home of a notice to a member about their own account
ACCOUNT_SOURCE = acts_only_source(
    ACCOUNT_WORKFLOW, "About your account",
    "Something was done to a member's own account that they should know of at once.",
    "look", "Check that you expected it")


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

    @staticmethod
    def _client_key(client: str | None) -> str | None:
        """Where failures from one address are counted: never a member id (those have no space)."""
        return f"client {str(client)[:64]}" if client else None

    def _bump_failures(self, key: str, now: datetime, limit: int) -> tuple[int, str | None]:
        row = self.db.signin_failures.get(key)
        count, first = 1, now
        if row and now - _parse(row["first_at"]) < WINDOW:
            count, first = row["count"] + 1, _parse(row["first_at"])
        locked = _stamp(now + LOCKOUT) if count >= limit else None
        self.db.signin_failures.replace({"_id": key, "member": key, "count": count, "first_at": _stamp(first),
                                         "locked_until": locked})
        return count, locked

    def _record_failure(self, mid: str, what: str, actor: dict | None = None, client: str | None = None) -> None:
        """Count a failed attempt for ``mid`` and put it on the track; the count and the event outlive a rollback.
        With the ``client`` address it came from, that address is counted too and locks first (at MAX_FAILURES):
        the member is locked only by failures from several places (MEMBER_MAX_FAILURES), so someone guessing from
        one place cannot lock a colleague out. Without one (signing, the command line) the member locks at
        MAX_FAILURES. A member who is locked is told, by a notice on their work."""
        mid = str(mid)[:64]
        ckey = self._client_key(client)
        limit = MEMBER_MAX_FAILURES if ckey else MAX_FAILURES

        def write():
            now = datetime.now(timezone.utc)
            extra = {}
            if ckey:
                _, c_locked = self._bump_failures(ckey, now, MAX_FAILURES)
                extra = {"from": str(client)[:64], **({"address_locked_until": c_locked} if c_locked else {})}
            count, locked = self._bump_failures(mid, now, limit)
            seq = self.track.append(actor or SIGN_IN, "signin.failed", f"member:{mid}",
                                    data={"what": what, "attempt": count, **extra,
                                          **({"locked_until": locked} if locked else {})})
            if locked and count == limit:  # just locked: tell them, so a lockout by someone else is noticed
                target = self.db.members.get(mid)
                if target and target.get("kind") == "person" and target.get("status") == "active":
                    self._account_notice(
                        SIGN_IN, target, seq, "Your account is locked for a while after failed attempts",
                        f"There were {count} failed attempts to {what} as you, with a wrong passphrase or setup "
                        f"code, so your account is locked until {self._shown_time(locked)}. If that was not you, "
                        f"tell a policy owner now; if it was, wait, or ask a member admin for a setup code.",
                        {"locked_until": locked}, "account locked")
        self.db.surviving(write)

    def _shown_time(self, stamp: str) -> str:
        """A track time as a person reads it: the hour in the practice's time zone, as every page shows times."""
        from . import clock
        try:
            return clock.shown(stamp, self.engine.zone())[11:16]
        except clock.ZoneError:
            return stamp[11:16] + " UTC"

    def _clear_failures(self, mid: str) -> None:
        self.db.signin_failures.delete({"_id": mid})

    def _check_locked(self, mid: str, what: str, actor: dict | None = None, client: str | None = None) -> None:
        from .hub import Denied
        now = datetime.now(timezone.utc)
        ckey = self._client_key(client)
        here = self._locked_until(ckey, now) if ckey else None
        until = here or self._locked_until(str(mid)[:64], now)
        if until:
            self.db.surviving(lambda: self.track.append(actor or SIGN_IN, "signin.refused", f"member:{str(mid)[:64]}",
                                                        data={"what": what, "locked_until": _stamp(until),
                                                              **({"from": str(client)[:64]} if client else {})}))
            raise Denied(f"too many failed attempts{' from here' if here else ''}: try again after "
                         f"{self._shown_time(_stamp(until))}, or ask a member admin for a setup code")

    def _verify(self, mid: str, passphrase: str, what: str, actor: dict | None = None,
                client: str | None = None) -> dict:
        """The member, if ``passphrase`` is theirs; else the attempt is counted, recorded and refused."""
        from .hub import Denied, NotFound
        self._check_locked(mid, what, actor, client)
        row = self.db.passphrases.get(str(mid))
        ok = check_passphrase(str(passphrase or ""), row["hash"] if row else _dummy()) and row is not None
        member = None
        if ok:
            try:
                member = self.member(mid)
            except NotFound:
                member = None
        if not member or member["status"] != "active" or member["kind"] != "person":
            self._record_failure(mid, what, actor, client)
            raise Denied("that member id and passphrase do not match an active person")
        self._clear_failures(mid)
        return member

    # ------------------------------------------------------------------ signing in
    def sign_in(self, mid: str, passphrase: str, client: str | None = None) -> dict:
        """A person signs in with their member id and passphrase. Returns the member; raises Denied. ``client``: the
        address the attempt came from, counted (and locked) before the member is."""
        member = self._verify(str(mid or "").strip(), passphrase, "sign in", client=client)
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
        # a passphrase changed in a session the code's issuer could have opened is still theirs to know: whoever
        # issued the code stays on it, so roles given later that they do not reach still clear it
        prev = self.db.passphrases.get(mid) or {}
        self._store_passphrase(actor, mid, new, how="set by the member", issued_by=prev.get("issued_by"))

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
        unused code for the member stops working. The member is told at once (a notice on their work), so a code
        they did not ask for is noticed before it is used, not only at their next sign-in."""
        with self.reach_memo():  # reach is asked of the issuer and of every policy owner: read it once each
            return self._issue_setup_code(actor, mid)

    def setup_code_refusal(self, actor: dict, mid: str, _who: bool = True) -> dict | None:
        """Why ``actor`` may not issue ``mid`` a setup code, or None when they may. The Team page's card and the
        action both ask this one question, so a button is never shown that is then refused. Returns
        ``{"why", "beyond", "who", "reach"}``: ``reach`` is true when the actor manages members or owns policy but
        the member reaches further (or approves changes); ``who`` names the active people who may issue it."""
        target = self.member(mid)
        if actor.get("kind") == "installer":
            return None
        if actor["id"] == mid:
            return {"why": "a member admin does not issue a setup code to themselves: another member admin does, "
                           "or change your passphrase with your current one", "beyond": [], "who": [], "reach": False}
        admin = self.can(actor, "member_admin", "practice:main")
        if not admin and not self.is_policy_owner(actor):
            return {"why": "issuing a passphrase setup code needs a member admin", "beyond": [], "who": [],
                    "reach": False}
        # whoever issues it can sign in as the member: they must reach as far as the member does. A member admin
        # does for most; for a clinician's account, someone who reads what they read issues it instead
        person = target["kind"] == "person"
        waiting = set((self.role_request(mid) or {}).get("roles") or []) if person else set()
        beyond = self.reach_beyond(actor, mid, set(target["roles"]) | waiting) if person else []
        privileged = bool(set(target["roles"]) & self.privileged_roles())
        if not beyond and (admin or (person and not privileged)):
            return None
        who = [m["name"] for m in self.members("person") if m["status"] == "active" and m["id"] not in (mid, actor["id"])
               and self.setup_code_refusal(m, mid, _who=False) is None] if _who else []
        if beyond:
            why = (f"{target['name']} reaches information {actor['name']} does not ({', '.join(beyond)}): someone "
                   f"who does issues their setup code")
        else:
            why = f"{target['name']} approves changes or manages members: a member admin issues their setup code"
        why += (f" ({', '.join(who)})" if who else
                f". Nobody on the team may, so the server holder issues it on the hub's computer with "
                f"`{self.holder_setup_command(mid)}`")
        return {"why": why, "beyond": beyond, "who": who, "reach": True}

    @staticmethod
    def holder_setup_command(mid: str) -> str:
        """How the server holder issues a member's setup code when nobody on the team may (the command line)."""
        return f"practicehub member passphrase-link {mid} --as {mid}"

    def _issue_setup_code(self, actor: dict, mid: str) -> dict:
        from .hub import Denied, Invalid
        target = self.member(mid)
        refusal = self.setup_code_refusal(actor, mid)
        if refusal:
            if refusal["reach"]:
                self.track.append(actor, "access.denied", f"member:{mid}",
                                  data={"relation": "member_admin", "what": "issue a setup code"})
                raise Denied(refusal["why"])
            if actor["id"] == mid:
                raise Denied(refusal["why"])
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
            seq = self.track.append(actor, "credential.setup_issued", f"member:{mid}", data={"expires_at": expires})
            if actor.get("kind") != "installer":  # a first code from the command line is the member's own start
                self._tell_member(actor, target, seq)
        return {"member": mid, "code": code, "expires_at": expires}

    def _tell_member(self, actor: dict, target: dict, cause: int) -> str:
        """A notice to the member that a setup code for their account was issued, and by whom: whoever holds it can
        set their passphrase and sign in as them, and it ends their sessions once used."""
        who = actor.get("name") or actor["id"]
        return self._account_notice(
            actor, target, cause, f"A setup code for your account was issued by {who}",
            f"{who} issued a setup code for your account. Whoever uses it sets your passphrase and can sign in as you; "
            f"your sessions end when it is used. If you did not ask for it, tell a policy owner now.",
            {"issued_by": actor["id"]}, "setup code issued", run_title="A setup code for your account",
            ctx={"setup_issued": cause})

    def _account_notice(self, actor: dict, target: dict, cause: int, title: str, summary: str, data: dict,
                        what: str, run_title: str = "About your account", ctx: dict | None = None) -> str:
        """An urgent notice, on the member's own work, about their account (a setup code issued, a lockout)."""
        from .engine import _item_doc, _new_id, home_run
        item_id = _new_id("item")
        run_id = home_run(self, ACCOUNT_WORKFLOW, title=run_title, started_by=actor["id"], cause=cause,
                          ctx=ctx or {"account": cause})
        self.db.items.insert(_item_doc(
            self, id=item_id, run_id=run_id, step_id="look", kind="notice", role=None, assignee=target["id"],
            persons_only=1, status="open", cause=cause, priority="urgent", title=title,
            output={"summary": summary, "member": target["id"], **data}))
        self.track.append(actor, "credential.member_told", f"member:{target['id']}", cause=cause,
                          data={"item": item_id, "what": what})
        return item_id

    def redeem_setup_code(self, mid: str, code: str, new: str, client: str | None = None) -> dict:
        """Set a passphrase with a setup code. Wrong codes count as failed attempts for the member (and for the
        ``client`` address they came from)."""
        from .hub import Denied, Invalid, NotFound
        mid = str(mid or "").strip()
        self._check_locked(mid, "use a setup code", client=client)
        row = self.db.setup_codes.find_one({"_id": _sha(_norm_code(code)), "member": mid})
        try:
            member = self.member(mid)
        except NotFound:
            member = None
        if (not row or row["used_at"] or _parse(row["expires_at"]) <= datetime.now(timezone.utc) or not member
                or member["status"] != "active" or member["kind"] != "person"):
            self._record_failure(mid, "use a setup code", client=client)
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
