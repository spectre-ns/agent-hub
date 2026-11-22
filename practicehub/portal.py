"""The online booking portal: what the people a practice serves see of it, from the internet (``docs/portal.md``).

The portal is a small web app of its own (``practicehub/web/portal_app.py``, ``practicehub portal serve``). It acts
as one member of the practice, a service (``[portal] member``): it does only what that member's roles may do, so it
writes contact details and starts the workflows the practice lets it start, and never reads what the practice keeps
from it. Everything here is the pack's to say, in ``[portal]``:

* ``kinds``: what a person may book. Each kind has a length (``minutes``), the roles whose calendars it books, how far
  ahead (``horizon_days``), whether it books straight away (``mode = "book"``) or goes in as a request (``"request"``),
  the workflow it starts, the questions it asks (that workflow's own inputs) and what it says back.
* ``identity``: how a person proves who they are: the date of birth field of the practice's subject type, its
  identifier (the core's ``Subject.identifier``) and a one-time code sent to its contact (``Subject.contact``).
* ``register``: the fields a new person gives, and ``intake_workflow``, the workflow that takes them in.

Every booking starts a workflow (``Portal._book``): the portal never writes a booking itself. A cancellation starts
the kind's ``cancel_workflow`` about the booking. The core names no pack, type or workflow here.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import date, datetime, timedelta, timezone

from . import expr, schema

KINDS_KEYS = {"label", "description", "mode", "workflow", "roles", "minutes", "horizon_days", "slot", "slot_input",
              "inputs", "questions", "advice", "alarm", "booked", "booking_kind", "cancel_workflow",
              "cancel_before_hours", "max_slots", "done"}
TOP_KEYS = {"member", "title", "welcome", "privacy", "contact_line", "wording", "identity", "code", "register",
            "intake_workflow", "session_minutes", "kinds"}
MODES = ("book", "request")
SLOTS = ("required", "optional", "none")
ID = re.compile(r"[a-z][a-z0-9-]*")

CODE_MINUTES = 10          # how long a one-time code holds, unless [portal.code] minutes says
CODE_LENGTH = 6
CODE_ATTEMPTS = 5          # wrong codes against one sign-in before it is dead (the address and the person lock too)
SESSION_MINUTES = 30
CLIENT_LIMIT = 5           # failed attempts in the lockout window from one address
PERSON_LIMIT = 10          # ... on one identifier, from anywhere
REGISTER_LIMIT = 5         # registration requests from one address in the window

WORDING = {  # what the pages say, unless [portal.wording] says otherwise
    "booking": "Booking",
    "sent": "If what you gave matches our records, we have sent a code to the contact details we have for you. "
            "It expires in {minutes} minutes.",
    "no_match": "That code is not right, or it has expired. Ask for a new one.",
    "duplicate": "You already have an open request with us. We will be in touch about it; you do not need to ask "
                 "again.",
    "clash": "Someone has just taken that time. Here are the times free now.",
    "requested": "We have your request. We will contact you about it.",
    "booked": "You are booked on {when} with {with}.",
    "registered": "Thank you. We have your details and will contact you.",
    "cancelled": "Your booking on {when} is cancelled.",
}


class AlreadyOpen(Exception):
    """The kind's workflow already has an open run for this person (its ``one_open_per``): nothing is said of it."""


class Clash(Exception):
    """The time chosen is no longer free; ``slots`` are the ones free now."""

    def __init__(self, slots: list[dict]):
        super().__init__("that time is no longer free")
        self.slots = slots


# ------------------------------------------------------------------------------------------ the pack's [portal]
def config(pack) -> dict:
    """The practice's ``[portal]``, with the kinds its modules add (a module adds kinds, never changes the rest)."""
    if pack is None:
        return {}
    cfg = dict(pack.portal or {})
    kinds = dict(cfg.get("kinds") or {})
    for m in pack.modules:
        for kid, k in ((m.portal or {}).get("kinds") or {}).items():
            kinds.setdefault(kid, k)
    if kinds:
        cfg["kinds"] = kinds
    return cfg


def _type_doc(pack, tid: str) -> dict:
    import tomllib
    for p in pack.all_parts():
        src = p.type_sources.get(tid)
        if src:
            try:
                return tomllib.loads(src)
            except tomllib.TOMLDecodeError:
                return {}
    return {}


def _workflow(pack, wid: str):
    """The pack's workflow ``wid``, parsed; None when there is none; a list of problems when it does not parse."""
    from .workflows import DefinitionError, parse
    for p in pack.all_parts():
        src = p.workflow_sources.get(wid)
        if src:
            try:
                return parse(src)
            except DefinitionError as exc:
                return [str(x) for x in exc.args[0]] if exc.args and isinstance(exc.args[0], list) else [str(exc)]
    return None


def _fields_of(doc: dict) -> set[str]:
    return {f.get("name") for f in doc.get("field", []) or [] if isinstance(f, dict)}


def check_config(pack) -> list[str]:
    """Every problem with the pack's ``[portal]``, in words, each saying where: empty when it is right (or absent)."""
    cfg = config(pack)
    if not cfg:
        return []
    problems: list[str] = []
    for k in sorted(set(cfg) - TOP_KEYS):
        problems.append(f"{k}: not a portal setting (settings: {', '.join(sorted(TOP_KEYS))})")
    roles = pack.role_ids()
    members = {m.get("id"): m for p in pack.all_parts() for m in p.members}
    mid = cfg.get("member")
    portal_roles: set[str] = set()
    if not mid or mid not in members:
        problems.append(f"member: name the service member the portal acts as (one of the pack's members), not {mid!r}")
    else:
        if members[mid].get("kind") != "service":
            problems.append(f"member: {mid} is a {members[mid].get('kind')}: the portal acts as a service")
        portal_roles = set(members[mid].get("roles") or [])
    for key in ("title", "welcome", "privacy", "contact_line"):
        if key in cfg and not isinstance(cfg[key], str):
            problems.append(f"{key}: words, in quotes")
    if not str(cfg.get("privacy") or "").strip():
        problems.append("privacy: say, in a line, what is done with what people give here (every page shows it)")
    wording = cfg.get("wording") or {}
    if not isinstance(wording, dict):
        problems.append("wording: a table of what the pages say")
    else:
        problems += [f"wording.{k}: not something the portal says (it says: {', '.join(sorted(WORDING))})"
                     for k in sorted(set(wording) - set(WORDING))]
    try:
        session = int(cfg.get("session_minutes", SESSION_MINUTES))
        if not 5 <= session <= 120:
            problems.append("session_minutes: between 5 and 120")
    except (TypeError, ValueError):
        problems.append("session_minutes: a number of minutes")

    subject = (pack.subject or {}).get("type")
    sdoc = _type_doc(pack, subject) if subject else {}
    sfields = _fields_of(sdoc)
    ident = cfg.get("identity") or {}
    if not isinstance(ident, dict):
        problems.append("identity: a table ([portal.identity])")
        ident = {}
    born = ident.get("born")
    if not born or born not in sfields:
        problems.append(f"identity.born: the field of {subject or 'the subject type'} that holds the date of birth, "
                        f"not {born!r}")
    base = sdoc.get("base") or {}
    for need in ("identifier", "contact"):
        if sdoc and not base.get(need):
            problems.append(f"identity: {subject} keeps no {need} for the core's Subject ([base] {need}): a person "
                            f"proves who they are by it")
    code = cfg.get("code") or {}
    if not isinstance(code, dict):
        problems.append("code: a table ([portal.code])")
        code = {}
    if not re.fullmatch(r"[a-z0-9_-]+", str(code.get("channel") or "")):
        problems.append("code.channel: the outbox channel a one-time code goes out on (sms, email)")
    for key, lo, hi in (("minutes", 2, 30), ("length", 6, 10)):
        if key in code:
            try:
                if not lo <= int(code[key]) <= hi:
                    problems.append(f"code.{key}: between {lo} and {hi}")
            except (TypeError, ValueError):
                problems.append(f"code.{key}: a number")

    def wf_problem(where: str, wid, record=None):
        wf = _workflow(pack, wid) if isinstance(wid, str) else None
        if wf is None:
            problems.append(f"{where}: there is no workflow {wid!r}")
            return None
        if isinstance(wf, list):
            problems.append(f"{where}: the workflow {wid} does not read: {'; '.join(wf)}")
            return None
        if portal_roles and not set(wf.starters) & portal_roles:
            problems.append(f"{where}: {mid} may not start {wid} (its trigger.starters name none of its roles)")
        if record is not None and wf.record != record:
            problems.append(f"{where}: {wid} is about a {wf.record}, not a {record}")
        return wf

    reg = cfg.get("register")
    if reg is not None:
        if not isinstance(reg, dict) or not isinstance(reg.get("fields"), list) or not reg.get("fields"):
            problems.append("register.fields: the fields of the subject type a new person gives")
        else:
            problems += [f"register.fields: {subject} has no field {f!r}" for f in reg["fields"] if f not in sfields]
    if cfg.get("intake_workflow"):
        wf_problem("intake_workflow", cfg["intake_workflow"], subject)
        if reg is None:
            problems.append("register: say the fields a new person gives ([portal.register] fields), or leave out "
                            "intake_workflow")

    kinds = cfg.get("kinds")
    if not isinstance(kinds, dict) or not kinds:
        problems.append("kinds: at least one kind of booking ([portal.kinds.<id>])")
        kinds = {}
    slot_minutes = int((pack.calendar or {}).get("slot_minutes", 15) or 15)
    for kid, k in kinds.items():
        w = f"kinds.{kid}"
        if not ID.fullmatch(str(kid)):
            problems.append(f"{w}: an id is lower-case letters, digits and dashes")
        if not isinstance(k, dict):
            problems.append(f"{w}: a table")
            continue
        problems += [f"{w}.{x}: not a setting of a kind (settings: {', '.join(sorted(KINDS_KEYS))})"
                     for x in sorted(set(k) - KINDS_KEYS)]
        if not str(k.get("label") or "").strip():
            problems.append(f"{w}.label: what a person sees it as")
        mode = k.get("mode")
        if mode not in MODES:
            problems.append(f"{w}.mode: book (straight away) or request (it goes in as a request), not {mode!r}")
        slot = k.get("slot", "required" if mode == "book" else "optional")
        if slot not in SLOTS:
            problems.append(f"{w}.slot: one of {', '.join(SLOTS)}")
        if mode == "book" and slot != "required":
            problems.append(f"{w}.slot: a kind booked straight away needs a time (slot = \"required\")")
        rl = k.get("roles")
        if slot != "none":
            if not isinstance(rl, list) or not rl:
                problems.append(f"{w}.roles: whose calendars it books (roles)")
            else:
                problems += [f"{w}.roles: there is no role {r!r}" for r in rl if r not in roles]
        for key, lo, hi in (("minutes", 5, 480), ("horizon_days", 1, 365), ("max_slots", 1, 200)):
            if key in k or (key in ("minutes", "horizon_days") and slot != "none"):
                try:
                    v = int(k.get(key))
                    if not lo <= v <= hi:
                        problems.append(f"{w}.{key}: between {lo} and {hi}")
                    elif key == "minutes" and v % slot_minutes:
                        problems.append(f"{w}.minutes: a multiple of the calendar's slot ({slot_minutes} minutes)")
                except (TypeError, ValueError):
                    problems.append(f"{w}.{key}: a whole number")
        for key in ("alarm",):
            if k.get(key):
                try:
                    expr.check(str(k[key]))
                except expr.ExprError as exc:
                    problems.append(f"{w}.{key}: {exc}")
        wf = wf_problem(f"{w}.workflow", k.get("workflow"), subject)
        if wf is not None:
            names = {i["name"]: i for i in wf.inputs}
            fixed = k.get("inputs") or {}
            if not isinstance(fixed, dict):
                problems.append(f"{w}.inputs: a table of input = value, given to the workflow as they are")
                fixed = {}
            qs = []
            for q in k.get("questions") or []:
                name = q if isinstance(q, str) else q.get("input") if isinstance(q, dict) else None
                if not name:
                    problems.append(f"{w}.questions: each is an input's name, or {{ input = ..., label = ... }}")
                    continue
                qs.append(name)
            given = {*fixed, *qs}
            if slot != "none":
                si = k.get("slot_input", "slot")
                given.add(si)
                if si not in names:
                    problems.append(f"{w}.slot_input: {wf.id} has no input {si!r} to give the time to")
            problems += [f"{w}: {wf.id} has no input {n!r}" for n in sorted({*fixed, *qs} - set(names))]
            missing = [n for n, i in names.items() if i.get("required") and i.get("default") is None and n not in given]
            if missing:
                problems.append(f"{w}: {wf.id} needs {', '.join(missing)}: ask it (questions) or give it (inputs)")
            for key in ("advice", "booked"):
                if k.get(key) and str(k[key]).split(".")[0] not in {s.id for s in wf.steps}:
                    problems.append(f"{w}.{key}: {wf.id} has no step {str(k[key]).split('.')[0]!r}")
        if mode == "book" and not k.get("booked"):
            problems.append(f"{w}.booked: the step of {k.get('workflow')} that books it (its output says when)")
        if k.get("cancel_workflow"):
            cw = wf_problem(f"{w}.cancel_workflow", k["cancel_workflow"])
            if cw is not None and (not cw.record or cw.record == pack.subject_type):
                # about the booking, not the person (which type books is the pack's: the portal checks the booking
                # is gone after the workflow ran, and says so when it is not)
                problems.append(f"{w}.cancel_workflow: {cw.id} is about {cw.record or 'nothing'}, not a booking")
            if not k.get("booking_kind"):
                problems.append(f"{w}.booking_kind: what its bookings are for (the Booking's kind), so the portal "
                                f"knows which may be cancelled")
            try:
                float(k.get("cancel_before_hours", 0))
            except (TypeError, ValueError):
                problems.append(f"{w}.cancel_before_hours: a number of hours")
    return problems


# ------------------------------------------------------------------------------------------ helpers
def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _stamp(t: datetime) -> str:
    return t.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))


def norm_identifier(v) -> str:
    """An identifier as a person types it, as it is compared: letters and digits, upper case (spaces, dashes go)."""
    return "".join(ch for ch in str(v or "").upper() if ch.isalnum())


def norm_date(v) -> str | None:
    try:
        return date.fromisoformat(str(v or "").strip()[:10]).isoformat()
    except ValueError:
        return None


# ------------------------------------------------------------------------------------------ the portal
class Portal:
    """The portal's work, as its service member: identity, slots, bookings through workflows, the person's own view."""

    def __init__(self, hub, *, dev: bool = False):
        self.hub, self.dev = hub, dev
        problems = check_config(hub.pack)
        if problems:
            raise ValueError("[portal]: " + "; ".join(problems))
        self.cfg = config(hub.pack)
        if not self.cfg:
            raise ValueError("this practice has no [portal]: its pack says what people may book online")

    # --- configuration
    @property
    def member(self) -> dict:
        return self.hub.member(self.cfg["member"])

    def say(self, key: str, **values) -> str:
        text = str((self.cfg.get("wording") or {}).get(key) or WORDING[key])
        for k, v in values.items():
            text = text.replace("{" + k + "}", str(v))
        return text

    def kinds(self) -> dict[str, dict]:
        return {kid: {"id": kid, **k} for kid, k in (self.cfg.get("kinds") or {}).items()}

    def kind(self, kid: str) -> dict:
        from .hub import NotFound
        k = self.kinds().get(kid)
        if not k:
            raise NotFound(f"there is no kind {kid!r}")
        return k

    def slot_mode(self, k: dict) -> str:
        return k.get("slot", "required" if k.get("mode") == "book" else "optional")

    @property
    def subject_type(self) -> str:
        return self.hub.pack.subject_type

    def _rt(self):
        return self.hub.record_type(self.subject_type)

    def code_minutes(self) -> int:
        return int((self.cfg.get("code") or {}).get("minutes", CODE_MINUTES))

    def questions(self, k: dict) -> list[dict]:
        """The kind's questions: its workflow's inputs, as that workflow says them, with the pack's own words."""
        wf, _ = self.hub.workflow(k["workflow"])
        by = {i["name"]: i for i in wf.inputs}
        out = []
        for q in k.get("questions") or []:
            name = q if isinstance(q, str) else q["input"]
            if name not in by:
                continue
            spec = dict(by[name])
            if isinstance(q, dict):
                spec.update({x: q[x] for x in ("label", "help") if q.get(x)})
            out.append(spec)
        return out

    def register_fields(self) -> list[dict]:
        rt = self._rt()
        reg = self.cfg.get("register") or {}
        return [rt.field(n) for n in reg.get("fields") or [] if rt.field(n)]

    # --- lockout: counted as the staff sign-in counts (``credentials``), under keys of the portal's own
    def _keys(self, client: str | None, identifier: str | None) -> list[tuple[str, int]]:
        out = []
        if client:
            out.append((f"portal client {str(client)[:64]}", CLIENT_LIMIT))
        if identifier:  # the identifier typed, hashed: it locks whether or not anyone has it, so it says nothing
            out.append((f"portal id {_sha(norm_identifier(identifier))[:32]}", PERSON_LIMIT))
        return out

    def _check_locked(self, client, identifier, also=()) -> None:
        from .hub import Denied
        now = datetime.now(timezone.utc)
        for key, _ in [*self._keys(client, identifier), *also]:
            if self.hub._locked_until(key, now):
                raise Denied("Too many attempts. Wait a quarter of an hour and try again, or call the practice.")

    def _failed(self, client, identifier, what: str) -> None:
        now = datetime.now(timezone.utc)

        def write():
            for key, limit in self._keys(client, identifier):
                self.hub._bump_failures(key, now, limit)
            self.hub.track.append(self.member, "portal.signin_failed", "portal", data={"what": what})
        self.hub.db.surviving(write)

    # --- identity
    def _find(self, born: str | None, identifier: str) -> str | None:
        """The one active record of the subject type with this date of birth and identifier, as ``Type/id``."""
        rt = self._rt()
        bf = rt.field(self.cfg["identity"]["born"])
        want = norm_identifier(identifier)
        if not born or not want or bf is None:
            return None
        path = bf["path"]
        if schema.FIELD_PATH.fullmatch(path) and "." not in path:
            found = self.hub.records.search(rt.id, limit=None, field=path, since=born, until=born + "~")
        else:
            found = self.hub.records.search(rt.id, limit=None, text=born)
        hits = []
        for rec in found:
            seen = schema.project(rt, rec, self.hub.member_types())
            if (norm_date(schema.first(rec, path)) == born and norm_identifier(seen.get("identifier")) == want
                    and schema.is_active(rt, rec)):
                hits.append(f"{rt.id}/{rec['id']}")
        return hits[0] if len(hits) == 1 else None  # two records with one identifier: neither is signed into

    def begin_sign_in(self, born: str, identifier: str, client: str | None = None) -> dict:
        """Step one: a date of birth and an identifier. The answer is the same whether or not they match anyone:
        a sign-in to finish (``challenge``, for the browser to keep) and, only on a dev hub, the code itself."""
        self._check_locked(client, identifier)
        self._sweep()
        born_iso = norm_date(born)
        ref = self._find(born_iso, identifier)
        raw = "ppc_" + secrets.token_urlsafe(24)
        cid = _sha(raw)
        length = int((self.cfg.get("code") or {}).get("length", CODE_LENGTH))
        code = "".join(secrets.choice("0123456789") for _ in range(length))
        now = datetime.now(timezone.utc)
        outbox_id = None
        contact = None
        if ref:
            contact = schema.project(self._rt(), self.hub.records.read(*ref.split("/", 1)),
                                     self.hub.member_types()).get("contact")
        with self.hub.db:
            if ref and contact:
                outbox_id = self._send_code(ref, code)
                self.hub.track.append(self.member, "portal.code_sent", ref, data={"message": outbox_id})
            else:  # no one, or no one we can reach: counted as a failure, and said no differently
                self._failed(client, identifier, "no match" if not ref else "no contact on file")
            self.hub.db.portal_codes.insert({
                "_id": cid, "record": ref if contact else None, "code_hash": _sha(f"{cid}:{code}"),
                "identifier": _sha(norm_identifier(identifier))[:32], "outbox": outbox_id, "attempts": 0,
                "created_at": _stamp(now), "expires_at": _stamp(now + timedelta(minutes=self.code_minutes())),
                "used_at": None})
        return {"challenge": raw, "dev_code": code if self.dev and outbox_id else None}

    def _send_code(self, ref: str, code: str) -> int:
        """The code, on the outbox to the person's contact: a connector of the practice's delivers it."""
        hub = self.hub
        channel = self.cfg["code"]["channel"]
        practice = (hub.pack.practice or {}) if hub.pack else {}
        cls = practice.get("message_class") or "messages"
        name = self.cfg.get("title") or practice.get("name") or "the practice"
        oid = hub.db.next_seq("outbox")
        hub.db.outbox.insert({"_id": oid, "id": oid, "at": _stamp(datetime.now(timezone.utc)), "channel": channel,
                              "recipient": ref, "subject": "Your sign-in code",
                              "body": f"Your code to sign in to {name} is {code}. It expires in "
                                      f"{self.code_minutes()} minutes. If you did not ask for it, ignore this.",
                              "run_id": None, "sent_by": self.member["id"], "status": "queued", "classes": cls,
                              "claimed_by": None, "claimed_at": None, "done_at": None, "done_by": None, "note": "",
                              "escalated": 0, "purpose": "sign-in"})
        return oid

    def _scrub(self, row: dict) -> None:
        """A code used or expired is taken out of the outbox message that carried it."""
        if row.get("outbox") is not None:
            self.hub.db.outbox.set(row["outbox"], {"body": "A sign-in code (removed: it was used or has expired)."})

    def _sweep(self) -> None:
        now = _stamp(datetime.now(timezone.utc))
        for row in self.hub.db.portal_codes.find({"expires_at": {"$lte": now}}, limit=200):
            self._scrub(row)
            self.hub.db.portal_codes.delete({"_id": row["_id"]})

    def finish_sign_in(self, challenge: str, code: str, client: str | None = None) -> str:
        """Step two: the code. A session (for the browser's cookie), or Invalid, the same for every wrong answer."""
        from .hub import Invalid
        cid = _sha(str(challenge or ""))
        row = self.hub.db.portal_codes.get(cid) if challenge else None
        person_key = [(f"portal id {row['identifier']}", PERSON_LIMIT)] if row else []
        self._check_locked(client, None, also=person_key)
        now = datetime.now(timezone.utc)
        typed = "".join(ch for ch in str(code or "") if ch.isdigit())
        good = (row is not None and row.get("record") and row.get("used_at") is None
                and _parse(row["expires_at"]) > now and row.get("attempts", 0) < CODE_ATTEMPTS
                and secrets.compare_digest(row["code_hash"], _sha(f"{cid}:{typed}")))
        if not good:
            if row is not None:
                self.hub.db.surviving(lambda: self.hub.db.portal_codes.update(
                    {"_id": cid}, {"$inc": {"attempts": 1}}))

            def write():
                for k, limit in self._keys(client, None) + person_key:
                    self.hub._bump_failures(k, now, limit)
                self.hub.track.append(self.member, "portal.signin_failed", "portal", data={"what": "code"})
            self.hub.db.surviving(write)
            raise Invalid(self.say("no_match"))
        with self.hub.db:
            self.hub.db.portal_codes.set(cid, {"used_at": _stamp(now)})
            self._scrub(row)
            raw = "pps_" + secrets.token_urlsafe(32)
            minutes = int(self.cfg.get("session_minutes", SESSION_MINUTES))
            self.hub.db.portal_sessions.insert({"_id": _sha(raw), "record": row["record"], "created_at": _stamp(now),
                                                "expires_at": _stamp(now + timedelta(minutes=minutes)), "revoked": 0,
                                                "form": secrets.token_urlsafe(16)})
            self.hub.track.append(self.member, "portal.signed_in", row["record"])
        return raw

    def session(self, raw: str | None) -> dict | None:
        """The signed-in person's session ``{record, form}``, or None."""
        if not raw or not str(raw).startswith("pps_"):
            return None
        row = self.hub.db.portal_sessions.get(_sha(raw))
        if not row or row.get("revoked") or _parse(row["expires_at"]) <= datetime.now(timezone.utc):
            return None
        return row

    def end_session(self, raw: str | None) -> None:
        if raw and str(raw).startswith("pps_"):
            self.hub.db.portal_sessions.set(_sha(raw), {"revoked": 1})

    # --- free time
    def _holders(self, k: dict) -> list[dict]:
        seen, out = set(), []
        for r in k.get("roles") or []:
            for m in self.hub.role(r)["holders"]:
                if m["id"] not in seen and m["kind"] == "person" and m["status"] == "active":
                    seen.add(m["id"])
                    out.append(m)
        return out

    def free_slots(self, kid: str, *, limit: int | None = None) -> list[dict]:
        """The times free for this kind: its length, with the members of its roles, between the practice's hours,
        from now to its horizon. Each is ``{value, label, day, time, with}``; ``value`` is ``YYYY-MM-DDTHH:MM|member``,
        as the core's ``booking.book`` takes it."""
        from .engine import bookings
        k = self.kind(kid)
        if self.slot_mode(k) == "none":
            return []
        cal = (self.hub.pack.calendar or {}) if self.hub.pack else {}
        hours = cal.get("hours") or {}
        step = int(k.get("minutes") or cal.get("slot_minutes", 15))
        size = int(cal.get("slot_minutes", 15))
        now = self.hub.engine.now().replace(second=0, microsecond=0)
        last = now.date() + timedelta(days=int(k.get("horizon_days", 14)))
        busy: dict[str, list[tuple[datetime, datetime]]] = {}
        for b in bookings(self.hub, since=now.date().isoformat(), until=(last + timedelta(days=1)).isoformat()):
            span = self.hub.booking_span(b, size)
            if span:
                for who in b["with"]:
                    busy.setdefault(who, []).append(span)
        cap = limit or int(k.get("max_slots", 60))
        people = self._holders(k)
        out = []
        day = now.date()
        while day <= last and len(out) < cap:
            key = day.strftime("%a").lower()[:3]
            if key in hours:
                opens, closes = (datetime.combine(day, datetime.strptime(x, "%H:%M").time()) for x in hours[key])
                t = opens
                while t + timedelta(minutes=step) <= closes and len(out) < cap:
                    end = t + timedelta(minutes=step)
                    if t > now:
                        for m in people:
                            if not any(s < end and t < e for s, e in busy.get(m["id"], [])):
                                out.append({"value": f"{t:%Y-%m-%dT%H:%M}|{m['id']}", "day": f"{t:%A %B} {t.day}",
                                            "time": f"{t:%H:%M}", "with": m["name"],
                                            "label": f"{t:%a %b} {t.day} {t:%H:%M} with {m['name']}"})
                    t = end
            day += timedelta(days=1)
        return out[:cap]

    def slot_free(self, kid: str, value: str) -> bool:
        return any(s["value"] == value for s in self.free_slots(kid, limit=10000))

    # --- booking: always through the kind's workflow
    def book(self, person: str, kid: str, slot: str | None, answers: dict) -> dict:
        """Book (or ask for) this kind for the signed-in person. The time must be free now (else Clash, with the
        times that are); the kind's workflow is started (``_book``). What the person is told comes back:
        ``{run, mode, advice, alarm, when, with, failed}``."""
        from .hub import Invalid
        k = self.kind(kid)
        mode = self.slot_mode(k)
        slot = (slot or "").strip() or None
        if mode == "required" and not slot:
            raise Invalid("choose a time", ["choose one of the times shown"])
        if slot and (mode == "none" or not self.slot_free(kid, slot)):
            raise Clash(self.free_slots(kid))
        run = self._book(k, person, slot, answers)
        ctx = run.get("ctx") or {}
        out = {"run": run["id"], "mode": k["mode"], "advice": None, "alarm": False, "when": None, "with": None,
               "failed": False}
        if k.get("advice"):
            got = expr.lookup(ctx, str(k["advice"]))
            out["advice"] = str(got).strip() if got not in (None, "") else None
        if k.get("alarm"):
            try:
                out["alarm"] = bool(expr.holds(str(k["alarm"]), ctx))
            except expr.ExprError:
                out["alarm"] = True  # a check that cannot be read says the advice loudly, never quietly
        if k.get("booked"):
            done = ctx.get(str(k["booked"])) or {}
            if done.get("when"):
                out["when"], out["with"] = done["when"], done.get("member_name")
            elif k["mode"] == "book":
                out["failed"] = True  # the workflow could not book it: its step failed, and a person is told
        return out

    def _book(self, k: dict, person: str, slot: str | None, answers: dict) -> dict:
        """The one place the portal makes a booking: it starts the kind's workflow, as the portal's member, about the
        person, with the kind's own inputs, the answers to its questions and the time chosen."""
        from .hub import Duplicate
        inputs = dict(k.get("inputs") or {})
        for q in self.questions(k):
            if q["name"] in answers:
                inputs[q["name"]] = answers[q["name"]]
        if slot:
            inputs[k.get("slot_input", "slot")] = slot
        try:
            return self.hub.engine.start(self.member, k["workflow"], record=person, inputs=inputs)
        except Duplicate:
            raise AlreadyOpen() from None

    # --- the person's own view
    def upcoming(self, person: str) -> list[dict]:
        """The person's bookings still to come: when, with whom, what kind (in the portal's words), and its state in
        its type's words. Never why: the reason and anything clinical stay with the practice."""
        from .engine import bookings
        now = self.hub.engine.now()
        by_kind = {k.get("booking_kind"): k for k in self.kinds().values() if k.get("booking_kind")}
        out = []
        for b in bookings(self.hub, since=now.isoformat(timespec="minutes")):
            if b["subject"] != person or b["cancelled"]:
                continue
            rt = self.hub.record_type(b["type"])
            if not schema.is_active(rt, b["record"]):
                continue
            k = by_kind.get(b["kind"])
            state = None
            lc = rt.lifecycle if rt else None
            if lc:
                st = schema.first(b["record"], lc["path"])
                state = rt.state_label(st) if st is not None else None
            try:
                start = datetime.fromisoformat(b["start"][:16])
            except ValueError:
                continue
            names = []
            for who in b["with"]:
                try:
                    names.append(self.hub.member(who)["name"])
                except Exception:  # noqa: BLE001 - someone since removed: no name
                    pass
            ok = bool(k and k.get("cancel_workflow")
                      and start - now >= timedelta(hours=float(k.get("cancel_before_hours", 0))))
            out.append({"ref": f"{b['type']}/{b['record']['id']}", "start": start,
                        "when": f"{start:%A %B} {start.day} at {start:%H:%M}", "with": names,
                        "kind": (k or {}).get("label") or self.say("booking"), "state": state, "can_cancel": ok,
                        "kind_id": (k or {}).get("id")})
        return sorted(out, key=lambda x: x["start"])

    def open_requests(self, person: str) -> list[dict]:
        """The person's requests still open: what (the kind's words) and since when. Only the portal's own workflows."""
        titles = {k["workflow"]: k["label"] for k in self.kinds().values() if k.get("mode") == "request"}
        out = []
        for r in self.hub.engine.runs(record=person, status="running", limit=50):
            if r["workflow_id"] in titles:
                out.append({"what": titles[r["workflow_id"]], "since": str(r.get("created_at") or "")[:10]})
        return out

    def cancel(self, person: str, booking: str) -> dict:
        """Cancel one of the person's own upcoming bookings, where its kind lets them (``_cancel``)."""
        from .hub import Denied
        mine = {b["ref"]: b for b in self.upcoming(person)}
        b = mine.get(booking)
        if not b or not b["can_cancel"]:
            raise Denied("That booking cannot be cancelled online. Call the practice.")
        self._cancel(self.kind(b["kind_id"]), booking)
        still = {x["ref"] for x in self.upcoming(person)}
        if booking in still:
            raise Denied("That booking could not be cancelled online. Call the practice.")
        return b

    def _cancel(self, k: dict, booking: str) -> dict:
        """The one place the portal cancels: the kind's cancel workflow, about the booking (a lifecycle move made by
        that workflow, never a write of the portal's)."""
        return self.hub.engine.start(self.member, k["cancel_workflow"], record=booking)

    # --- someone new
    def register(self, values: dict, client: str | None = None) -> dict | None:
        """A new person's details: a record of the subject type, and the intake workflow about it. Counted per
        address, so the form is not a way to fill the practice's files. Returns the run, or None (no intake)."""
        from .hub import Denied, Invalid
        wid = self.cfg.get("intake_workflow")
        if not wid or not self.cfg.get("register"):
            raise Denied("New registrations are not taken online here. Call the practice.")
        key = f"portal new {str(client or 'unknown')[:64]}"
        now = datetime.now(timezone.utc)
        if self.hub._locked_until(key, now):
            raise Denied("Too many requests from here. Try again later, or call the practice.")
        self.hub.db.surviving(lambda: self.hub._bump_failures(key, now, REGISTER_LIMIT))
        rt = self._rt()
        rec: dict = {}
        problems = []
        for f in self.register_fields():
            v = str(values.get(f["name"]) or "").strip()
            if not v:
                if f.get("required"):
                    problems.append(f"{f['label']} is required")
                continue
            if f["type"] == "date":
                v = norm_date(v)
                if v is None:
                    problems.append(f"{f['label']}: a date")
                    continue
            rec = schema.set_in(rec, schema.locate(rec, f["path"], f.get("with")).split("."), v, f.get("with"))
        if problems:
            raise Invalid("the form has problems", problems)
        with self.hub.db:
            made = self.hub.create_record(self.member, rt.id, rec)
            ref = f"{rt.id}/{made['id']}"
            return self.hub.engine.start(self.member, wid, record=ref)
