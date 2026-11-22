"""A subject's limits on who sees what of their file: consent directives (a lockbox, masking), and the emergency
override ("break the glass").

Privacy law lets the person a file is about limit or withdraw consent to the use and disclosure of their information
(Nova Scotia's PHIA, for one), and the custodian must then honour it and tell whoever gets the file that it is
incomplete. The core knows such a limit as a record of a type that extends its base type ``Restriction``
(``practicehub/base/Restriction.toml``): whose file (``subject``), what it masks (data ``classes``, record ``types``,
particular ``records``), who is still allowed (``members``, holders of ``roles``), and whether it is ``active``.

While a restriction is active, for every member it does not allow:

* a record of the file it masks is replaced by a stub that keeps only what says which record it is and whose
  (its type, id, the reference to its subject) and says that information is withheld at the subject's request
  (``meta.withheld``): never left out silently, so nobody takes the file for complete;
* in the file's other records (the subject's own record among them), a field whose own data class it masks is left
  out, as ``visibility.strip`` leaves out a field of a class the member may not read, and the record says so;
* a write that would change what is withheld is refused (``visibility.guard_write``).

This holds wherever a record reaches a member, because each path goes through ``visibility.redact``: pages, lists,
searches (a search by words matches only what the member sees), the chart, history, exports, the API, MCP and an
agent's context. An agent is never given what a restriction masks unless the restriction names it (or its role).
The hub itself (a workflow's automation) is not limited: it acts on the file, and the track records what it does.

A masked record is named neutrally for everyone ("Result, restricted at the patient's request"): a title travels
(notices, pickers, the track), so it never says what a masked record holds.

The override: a person whose role the practice grants ``break_glass`` may open what a restriction withholds from
them, giving a reason. The view is on the track (``record.viewed`` with ``how = "override"`` and the reason, so it is
in the subject's access report), each policy owner on duty (the privacy officer) is given a notice to review it, and
for ``OVERRIDE_WINDOW`` the person sees the file with a banner saying that they opened it in an emergency.

Who creates or changes a restriction is whoever may write its type's data class (the pack grants it to its privacy
officer); each change is a record change, versioned and on the track like any other.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from . import schema

BASE = "Restriction"
OVERRIDE_WINDOW = timedelta(hours=1)
OVERRIDE_WORKFLOW = "emergency-access"
OVERRIDE_SOURCE = (f'id = "{OVERRIDE_WORKFLOW}"\ntitle = "Emergency access to restricted information"\n'
                   'summary = "Someone opened information a subject asked to have withheld, giving a reason. The '
                   'privacy officer reviews it."\n[[steps]]\nid = "review"\ntitle = "Review the emergency access"\n'
                   'kind = "wait"\nuntil = "false"\n')
MIN_REASON = 8
_TTL = 2.0  # seconds a process keeps the restrictions it read (a change in this process clears it at once)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Restrictions:
    """The practice's active restrictions, by subject, and what they mask of a record for a member."""

    window = OVERRIDE_WINDOW

    def __init__(self, hub):
        self.hub = hub
        self._at = -1e9
        self._by_subject: dict[str, list[dict]] = {}
        self._types: set[str] = set()
        self._subjects: set[str] = set()
        self._rts: dict = {}
        self._over: dict = {}

    def reset(self) -> None:
        self._at = -1e9
        self._over = {}

    # ------------------------------------------------------------------ what is in force
    def _load(self) -> dict[str, list[dict]]:
        now = time.monotonic()
        if now - self._at < _TTL:
            return self._by_subject
        hub = self.hub
        types = hub.of_base(BASE) if BASE in schema.bases() else []
        out: dict[str, list[dict]] = {}
        for t in types:
            for rec in hub.records.search(t, limit=None):
                p = hub.project(rec)
                if p.get("active") is False or not p.get("subject"):
                    continue
                d = {"ref": f"{t}/{rec['id']}", "subject": p["subject"],
                     "classes": set(p.get("classes") or []), "types": set(p.get("types") or []),
                     "records": set(p.get("records") or []), "members": set(p.get("members") or []),
                     "roles": set(p.get("roles") or [])}
                if d["classes"] or d["types"] or d["records"]:
                    out.setdefault(p["subject"], []).append(d)
        self._types, self._by_subject, self._rts, self._over = set(types), out, {}, {}
        self._subjects = hub.subject_types() if out else set()
        self._at = now
        return out

    def is_restriction(self, rtype: str) -> bool:
        self._load()
        return rtype in self._types

    def of_subject(self, subject: str) -> list[dict]:
        return list(self._load().get(subject) or [])

    def any(self) -> bool:
        return bool(self._load())

    def _rt(self, rtype: str):
        if rtype not in self._rts:
            self._rts[rtype] = self.hub.record_type_for_reading(rtype)
        return self._rts[rtype]

    def subject_of(self, rec: dict) -> str | None:
        """``Hub.subject_of``, with the practice's subject types read once per ``_TTL``."""
        rtype = rec.get("resourceType") or ""
        if rtype in self._subjects:
            return f"{rtype}/{rec['id']}" if rec.get("id") else None
        rt = self._rt(rtype)
        if rt is not None:
            for rel, ref in schema.links(rt, rec):
                if rel == "subject" and ref.split("/")[0] in self._subjects:
                    return ref
        from .records import subject_of
        return subject_of(rec, frozenset(self._subjects))

    # ------------------------------------------------------------------ who is allowed
    def _allowed(self, member: dict | None, d: dict) -> bool:
        if not member or member.get("status", "active") != "active":
            return False
        if member.get("id") in d["members"]:
            return True
        roles = member.get("roles")
        if roles is None and member.get("id"):
            roles = self.hub.roles_of(member["id"])
        return bool(set(roles or []) & d["roles"])

    def allows_beyond(self, member: dict, other: dict) -> bool:
        """Whether some directive lets the member see what it keeps from the other member (who could read it
        otherwise): acting as the member would open that file to them."""
        for ds in self._load().values():
            for d in ds:
                if self._allowed(member, d) and not self._allowed(other, d) and self.reaches_withheld(other, d):
                    return True
        return False

    def withholds_from(self, member: dict | None, subject: str | None, reaching: bool = False) -> bool:
        """Whether some directive on the subject's file withholds anything from the member now (no emergency access
        in force): copies of that file's content made elsewhere (a step's output, a draft) are then not shown."""
        if not subject or (member or {}).get("kind") == "automation":
            return False
        if not self._load():  # no directive anywhere: nothing is withheld
            return False
        if subject.split("/", 1)[0] not in self._subjects:
            rec = self.hub.records.read(*subject.split("/", 1)) if "/" in subject else None
            subject = self.subject_of(rec) if rec else None  # a record about a subject: that subject's file
            if not subject:
                return False
        ds = [d for d in self.of_subject(subject) if not self._allowed(member, d)
              and (not reaching or self.reaches_withheld(member, d))]  # reaching: only what they would read otherwise
        return bool(ds) and not self.override(member, subject)

    def override(self, member: dict | None, subject: str) -> dict | None:
        """The member's emergency access to the subject's file still in force: the track's event, or None."""
        if not member or member.get("kind") != "person" or not subject:
            return None
        if not self.may_override(member):  # a role that lost break_glass loses the access it opened, at once
            return None
        key = (member["id"], subject)
        got = self._over.get(key)
        if got is not None and time.monotonic() - got[0] < _TTL:
            return got[1]
        since = _iso(_now() - OVERRIDE_WINDOW)
        ev = self.hub.db.events.find_one({"actor": member["id"], "action": "record.viewed", "data.how": "override",
                                          "data.subject": subject, "at": {"$gte": since}}, sort=[("seq", -1)])
        row = None
        if ev:
            until = datetime.fromisoformat(ev["at"].replace("Z", "+00:00")) + OVERRIDE_WINDOW
            row = {"seq": ev["seq"], "at": ev["at"], "until": _iso(until), "reason": ev["data"].get("reason", ""),
                   "record": ev["subject"]}
        self._over[key] = (time.monotonic(), row)
        return row

    # ------------------------------------------------------------------ what is masked
    def _covers(self, d: dict, rt, ref: str, subject: str) -> tuple[bool, list[dict]]:
        """(whole record masked, fields masked) by one restriction, for anyone it does not allow."""
        if ref != subject and (rt.id in d["types"] or rt.data_class in d["classes"] or ref in d["records"]):
            return True, []
        return False, [f for f in rt.fields if f.get("data_class") and f["data_class"] != rt.data_class
                       and f["data_class"] in d["classes"]]

    def mask(self, member: dict | None, rec: dict | None) -> dict | None:
        """What the restrictions on its subject withhold of ``rec`` from ``member``: ``{"whole", "fields",
        "subject", "restrictions"}``, or None when nothing is (no restriction, or the member is allowed, or is
        within an emergency access). The hub's own automation is never limited."""
        if not rec or not isinstance(rec, dict) or (member and member.get("kind") == "automation"):
            return None
        by = self._load()
        if not by:
            return None
        rtype = rec.get("resourceType") or ""
        if rtype in self._types or not rec.get("id"):
            return None
        subject = self.subject_of(rec)
        ds = by.get(subject or "")
        rt = self._rt(rtype)
        if not ds or rt is None:
            return None
        ref = f"{rtype}/{rec['id']}"
        whole, fields, which = False, [], []
        for d in ds:
            w, fs = self._covers(d, rt, ref, subject)
            if not (w or fs) or self._allowed(member, d):
                continue
            which.append(d["ref"])
            whole = whole or w
            fields += [f for f in fs if f not in fields]
        if not which or (not whole and not [f for f in fields if [v for v in schema.get(rec, f["path"])
                                                                   if v not in (None, "", [], {})]]):
            return None
        if self.override(member, subject):
            return None
        return {"whole": whole, "fields": fields, "subject": subject, "restrictions": which}

    def restricted(self, rec: dict | None) -> bool:
        """Whether some restriction masks the whole record for somebody (its title is then neutral, for everyone)."""
        if not rec or not isinstance(rec, dict) or not self._load():
            return False
        rtype = rec.get("resourceType") or ""
        if rtype in self._types or not rec.get("id"):
            return False
        subject = self.subject_of(rec)
        rt = self._rt(rtype)
        if rt is None:
            return False
        return any(self._covers(d, rt, f"{rtype}/{rec['id']}", subject)[0]
                   for d in self._load().get(subject or "") or [])

    def note(self, subject: str | None) -> str:
        """``Information withheld at the patient's request``: the subject type's own word."""
        rt = self._rt((subject or "").split("/")[0]) if subject else None
        who = rt.label.lower() if rt is not None else "subject"
        return f"Information withheld at the {who}'s request"

    def incomplete(self, subject: str | None, classes=()) -> str | None:
        """The line a message or export about ``subject`` carries when what it was written from may be incomplete
        at the subject's request: a restriction masks one of ``classes`` (or masks by type or record, which may be
        any of them). None when nothing is withheld."""
        ds = self.of_subject(subject) if subject else []
        if not ds or not any(not classes or d["types"] or d["records"] or set(d["classes"]) & set(classes) for d in ds):
            return None
        return f"This record is incomplete: {self.note(subject)[0].lower()}{self.note(subject)[1:]}."

    def neutral_title(self, rec: dict) -> str:
        rt = self._rt(rec.get("resourceType") or "")
        subject = self.subject_of(rec)
        srt = self._rt((subject or "").split("/")[0]) if subject else None
        who = srt.label.lower() if srt is not None else "subject"
        return f"{rt.label if rt is not None else rec.get('resourceType')}, restricted at the {who}'s request"

    def apply(self, member: dict | None, rec: dict | None) -> dict | None:
        """``rec`` as the restrictions let ``member`` see it (``visibility.redact`` calls it)."""
        m = self.mask(member, rec)
        if m is None:
            return rec
        from .visibility import strip
        rt = self._rt(rec["resourceType"])
        meta = dict(rec.get("meta") or {})
        note = self.note(m["subject"])
        if m["whole"]:
            keep = {k: rec[k] for k in ("resourceType", "id") if k in rec}
            for f in rt.fields:  # what says whose it is: the reference to its subject (or its owner)
                if f["type"] == "reference" and (f.get("rel") == "subject" or (rt.part_of or {}).get("field") == f["name"]):
                    top = f["path"].split(".")[0]
                    if top in rec:
                        keep[top] = rec[top]
            meta["withheld"] = {"note": note, "whole": True}
            return {**keep, "meta": meta}
        out = strip(rec, m["fields"])
        meta["withheld"] = {"note": note, "whole": False, "fields": [f["label"] for f in m["fields"]]}
        return {**out, "meta": meta}

    def problems(self, rt, rec: dict) -> list[str]:
        """What is wrong with a restriction as written: what it masks must be the practice's own data classes,
        record types and roles (a misspelt one would mask nothing, silently), and it must mask something."""
        hub = self.hub
        p = schema.project(rt, rec, hub.member_types())
        out = []
        for what, known, word in (("classes", hub.data_class_ids(), "data class"), ("types", hub.type_ids(), "record type"),
                                  ("roles", hub.role_ids(), "role")):
            bad = [x for x in p.get(what) or [] if x not in known]
            if bad:
                out.append(f"{rt.label}: {', '.join(map(repr, bad))} {'is' if len(bad) == 1 else 'are'} not a {word} "
                           f"of this practice (its {word}s: {', '.join(sorted(known))})")
        if p.get("active") is not False and not (p.get("classes") or p.get("types") or p.get("records")):
            out.append(f"{rt.label}: say what it withholds (data classes, record types or records)")
        subject = p.get("subject")
        for r in p.get("records") or []:
            got = hub.records.read(*r.split("/", 1)) if "/" in r else None
            if got and self.hub.subject_of(got) != subject:
                out.append(f"{rt.label}: {r} is not in {subject}'s file")
        return out

    # ------------------------------------------------------------------ the emergency override
    def may_override(self, member: dict | None) -> bool:
        return bool(member) and member.get("kind") == "person" and self.hub.can(member, "break_glass", "practice:main")

    def reaches_withheld(self, m: dict, d: dict) -> bool:
        """Whether the member could read some of what the directive withholds, were it not for the directive."""
        hub = self.hub
        classes = set(d["classes"]) | {hub.class_of(t) for t in d["types"]} | {
            hub.class_of(r.split("/", 1)[0]) for r in d["records"] if "/" in r}
        return any(hub.can(m, "reader", f"data_class:{c}") for c in classes - {None, ""})

    def excluded_by(self, m: dict, ds: list[dict]) -> bool:
        """Whether a directive keeps from the member something they would otherwise read."""
        return any(not self._allowed(m, d) and self.reaches_withheld(m, d) for d in ds)

    def _reviewers(self, member: dict, ds: list[dict], rtype: str) -> list[dict]:
        """Who reviews an emergency access: the people who record directives (the privacy role), else the policy
        owners, else the member admins; never the member who used it, and never anyone a directive on the file
        excludes from what it withholds (the review would tell them what the patient asked to keep from them, as
        the reason often does). When nobody fits, nobody in the practice is told: the track records it unreviewed
        for the server holder, rather than telling an excluded member."""
        hub = self.hub
        people = [m for m in hub.members("person") if m["status"] == "active" and m["id"] != member["id"]]
        classes = {hub.class_of(t) for t in (hub.of_base(BASE) if BASE in schema.bases() else [])} - {None, ""}
        everyone = self.of_subject(ds[0]["subject"]) if ds else []
        pools = ([m for m in people if any(hub.can(m, "writer", f"data_class:{c}") for c in classes)],
                 [m for m in people if hub.is_policy_owner(m)],
                 [m for m in people if hub.can(m, "member_admin", "practice:main")])
        for pool in pools:
            fit = [m for m in pool if not self.excluded_by(m, everyone or ds)]
            if fit:
                return fit
        return []

    def _blind_reviewers(self, member: dict) -> list[dict]:
        """Who is still told of an emergency access nobody may fully review: the privacy role, else the policy
        owners, else the member admins, shown who opened which file and when, never why."""
        hub = self.hub
        people = [m for m in hub.members("person") if m["status"] == "active" and m["id"] != member["id"]]
        classes = {hub.class_of(t) for t in (hub.of_base(BASE) if BASE in schema.bases() else [])} - {None, ""}
        for pool in ([m for m in people if any(hub.can(m, "writer", f"data_class:{c}") for c in classes)],
                     [m for m in people if hub.is_policy_owner(m)],
                     [m for m in people if hub.can(m, "member_admin", "practice:main")]):
            if pool:
                return pool
        return []

    def break_glass(self, member: dict, ref: str, reason: str) -> dict:
        """Open what restrictions withhold of ``ref``'s file from the member, in an emergency: on the track with the
        reason, a notice to each policy owner on duty to review it, in force for ``OVERRIDE_WINDOW``."""
        from .hub import Denied, Invalid, NotFound
        hub = self.hub
        rtype, _, rid = (ref or "").partition("/")
        if not rid:
            raise Invalid("name the record as Type/id")
        hub.require(member, "reader", f"data_class:{hub.class_of(rtype)}", f"read {rtype} records")
        if not self.may_override(member):
            hub.track.append(member, "access.denied", ref, data={"relation": "break_glass",
                                                                 "what": "open restricted information"})
            raise Denied(f"{member.get('name') or member['id']} may not open information withheld at a "
                         f"subject's request (no role of theirs may override a restriction)")
        reason = " ".join(str(reason or "").split())[:500]
        if len(reason) < MIN_REASON:
            raise Invalid("say why you need to open it (a few words at least): the privacy officer reviews it")
        rec = hub.records.read(rtype, rid)
        if not rec:
            raise NotFound(f"there is no {ref}")
        subject = self.subject_of(rec) if self._load() else None
        ds = [d for d in self.of_subject(subject or "") if not self._allowed(member, d)]
        if not ds:
            raise Invalid("nothing in this file is withheld from you: there is nothing to override")
        from .engine import _item_doc, _new_id
        from .store import utcnow
        title = hub.title(hub.records.read(*subject.split("/", 1)) or {}) or subject
        with hub.db:
            seq = hub.track.append(member, "record.viewed", ref,
                                   data={"how": "override", "reason": reason, "subject": subject,
                                         "restrictions": [d["ref"] for d in ds]})
            run_id = _new_id("run")
            hub.db.runs.insert({"_id": run_id, "id": run_id, "workflow_id": OVERRIDE_WORKFLOW, "version": 0,
                                "title": f"Emergency access: {title}", "record": subject, "status": "completed",
                                "ctx": {"override": seq}, "steps": {"review": {"state": "skipped"}},
                                "started_by": member["id"], "root_event": seq, "created_at": utcnow(),
                                "updated_at": utcnow()})
            owners = self._reviewers(member, ds, rtype)
            blind = [] if owners else self._blind_reviewers(member)
            if not owners:
                hub.track.append(member, "access.unreviewed", ref,
                                 data={"subject": subject, "told": [o["id"] for o in blind],
                                       "why": "nobody in the practice may see why without seeing what the subject "
                                              "withheld from them: who opened which file and when is reviewed"})
            for o in blind:
                # the directive keeps the file from them too: they review who opened it and when, not why
                hub.db.items.insert(_item_doc(
                    hub, id=_new_id("item"), run_id=run_id, step_id="review", kind="notice", role=None,
                    assignee=o["id"], persons_only=1, status="open", cause=seq, priority="urgent",
                    title=f"Review: {member['name']} used emergency access to {title}'s withheld information",
                    output={"about": subject, "opened by": member["name"],
                            "why": "not shown to you: the file's directive keeps what it withholds from you too. "
                                   "Ask them, or the server holder can read it on the track"}))
            for o in owners:
                # the notice names the file, never why it was opened (that can be the very thing withheld): the
                # reason is on the item's page, which only the reviewer it is for may open
                hub.db.items.insert(_item_doc(
                    hub, id=_new_id("item"), run_id=run_id, step_id="review", kind="notice", role=None,
                    assignee=o["id"], persons_only=1, status="open", cause=seq, priority="urgent",
                    title=f"Review: {member['name']} used emergency access to {title}'s withheld information",
                    output={"about": subject, "record": ref, "reason": reason}))
        self._over.pop((member["id"], subject), None)
        return {"seq": seq, "subject": subject, "run": run_id, "until": _iso(_now() + OVERRIDE_WINDOW),
                "told": [o["id"] for o in owners + blind]}
