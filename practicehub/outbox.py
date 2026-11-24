"""The outbox: messages a workflow sent, and their delivery.

A workflow's ``message.send`` queues a message. A delivery connector (a *service* member with a token, granted the
channels it delivers on) lists what is queued for its channels, claims a message, and marks it sent or failed. A
message records the data classes it quotes; a connector, like a person, sees its body only if it reads all of them,
and claims only what it can read. Every change of status is on the track.

A message still undelivered past its channel's deadline is escalated by the clock (``practicehub tick``); on an
urgent channel (a page) a person is also given the work of delivering it by hand ("Phone the physician"), as they are
whenever a connector reports a failure. A person who reads the message may mark it delivered by hand, with a note.

A message says its ``purpose``. A *commercial* one (Canada's anti-spam law, CASL) goes only to a record whose type
declares ``[consent]`` and whose recorded consent holds on the day: express until withdrawn, implied for as long as the
type says (two years), with its date and source on record, and not withdrawn. ``Hub.consent_problem`` is the rule; it
is checked when the message is queued and again when it is delivered, so a withdrawal in between stops it (the
message is *refused*). A message to a record that declares consent must say its purpose; nothing is assumed.

Deadlines and who delivers by hand come from the practice's ``[practice.outbox.<channel>]`` settings:
``deadline_minutes``, ``urgent``, ``deliver_role``, ``task`` (the work's wording; ``{to}`` is the recipient).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .db import utcnow

DEFAULTS = {
    "page": {"deadline_minutes": 5, "urgent": True, "task": "Phone {to}"},
    "fax": {"deadline_minutes": 120, "task": "Fax this to {to}"},
    "default": {"deadline_minutes": 240, "task": "Deliver this {channel} message to {to}"},
}
LEASE = timedelta(minutes=10)  # a claim not finished in this time may be claimed again
OPEN = ("queued", "sending", "failed")  # not delivered yet
PURPOSES = ("commercial", "transactional")  # commercial: consent is checked; transactional: what they asked for, or owe


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _stamp(t: datetime) -> str:
    return t.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class OutboxMixin:
    """The outbox's rules, mixed into the Hub."""

    # ------------------------------------------------------------------ consent (CASL)
    def consent_problem(self, recipient: str, purpose: str | None, today: str | None = None) -> str | None:
        """Why a message for ``purpose`` may not go to ``recipient`` today, or None when it may. A commercial message
        needs the recipient's consent, recorded and holding; a message to a record whose type declares consent must
        say its purpose."""
        from datetime import date
        from .expr import shift_date
        from . import schema
        today = today or self.engine.now().date().isoformat()
        rtype, _, rid = str(recipient or "").partition("/")
        rt = self.record_type(rtype) if rid else None
        if purpose not in (None, "", *PURPOSES):
            return f"a message's purpose is {' or '.join(PURPOSES)}, not {purpose!r}"
        if not purpose:
            return (f"say whether a message to {rt.label.lower()} {rid} is commercial or transactional: "
                    f"its consent to commercial messages is on record") if rt and rt.consent else None
        if purpose != "commercial":
            return None
        if not rt or not rt.consent:
            return (f"a commercial message goes only to someone whose consent is on record; {recipient or 'nobody'} "
                    f"is not a record that holds consent")
        rec = self.records.read(rtype, rid)
        if rec is None:
            return f"there is no {rt.label.lower()} {rid}"
        c = rt.consent
        name = self.title(rec) or f"{rt.label} {rid}"
        kind, given = schema.first(rec, c["kind"]), str(schema.first(rec, c["date"]) or "")[:10]
        source, withdrawn = schema.first(rec, c["source"]), str(schema.first(rec, c["withdrawn"]) or "")[:10]
        if not kind:
            return f"{name} has no consent to commercial messages on record"
        if kind not in c["lasts"]:
            return f"{name}'s consent is {kind!r}, which does not permit commercial messages"
        if not given or not str(source or "").strip():
            return f"{name}'s {kind} consent has no {'date' if not given else 'source'} on record"
        try:
            date.fromisoformat(given)
        except ValueError:
            return f"{name}'s consent date {given!r} is not a date"
        if given > today:
            return f"{name}'s consent is dated {given}, after today"
        if withdrawn and withdrawn <= today:
            return f"{name} withdrew consent on {withdrawn}"
        lasts = c["lasts"][kind]
        if lasts:
            ends = shift_date(given, "+", int(lasts[:-1]), lasts[-1])
            if ends <= today:
                return f"{name}'s {kind} consent (given {given}) lapsed on {ends}"
        return None

    def _refuse_if_no_consent(self, member: dict, row: dict) -> None:
        """At delivery, a commercial message whose consent no longer holds is refused, not delivered."""
        from .hub import Invalid
        if row.get("purpose") != "commercial":
            return
        why = self.consent_problem(row["recipient"], "commercial")
        if not why:
            return
        with self.db:
            self.db.outbox.set(row["id"], {"status": "refused", "done_at": utcnow(), "done_by": member["id"],
                                           "note": why[:2000]})
            row = self._message(row["id"])
            seq = self._outbox_event(member, "outbox.refused", row, reason=why[:2000])
            self._close_delivery_work(row, member, seq)
        raise Invalid(f"message {row['id']} is refused: {why}")

    # ------------------------------------------------------------------ configuration
    def channel_settings(self, channel: str) -> dict:
        conf = (self.practice.get("outbox") or {}) if isinstance(self.practice.get("outbox"), dict) else {}
        base = {**DEFAULTS["default"], **(conf.get("default") or {})}
        out = {**base, **DEFAULTS.get(channel, {}), **(conf.get(channel) or {})}
        try:
            out["deadline_minutes"] = max(1, int(out.get("deadline_minutes") or 240))
        except (TypeError, ValueError):
            out["deadline_minutes"] = 240
        out["urgent"] = bool(out.get("urgent"))
        return out

    def message_classes_of(self, row: dict) -> list[str]:
        """The data classes a message's words come from (``engine.message_classes``): never none."""
        return list(row["classes"])

    def may_read_message(self, member: dict, row: dict) -> bool:
        return all(self.can(member, "reader", f"data_class:{c}") for c in self.message_classes_of(row))

    def _message(self, oid) -> dict:
        from .hub import NotFound
        try:
            row = self.db.outbox.get(int(oid))
        except (TypeError, ValueError):
            row = None
        if not row:
            raise NotFound(f"there is no message {oid}")
        return row

    def _outbox_event(self, actor: dict, action: str, row: dict, cause: int | None = None, **data) -> int:
        return self.track.append(actor, action, f"outbox:{row['id']}", run_id=row.get("run_id"), cause=cause,
                                 data={"message": row["id"], "channel": row["channel"],
                                       "classes": self.message_classes_of(row), **data})

    # ------------------------------------------------------------------ grants
    def grant_channel(self, actor: dict, mid: str, channel: str, *, revoke: bool = False) -> None:
        """A member admin lets a service deliver on a channel (or stops it)."""
        from .hub import Invalid, member_ref
        self.require(actor, "member_admin", "practice:main", "grant delivery channels")
        target = self.member(mid)
        channel = str(channel or "").strip()
        if not channel or not channel.replace("-", "").replace("_", "").isalnum():
            raise Invalid("a channel is a name like fax or page: letters, digits, - and _")
        if target["kind"] != "service" and not revoke:
            raise Invalid("only a service delivers messages: a person marks one delivered by hand on the outbox page")
        t = [(f"channel:{channel}", "sender", member_ref(mid))]
        with self.db:
            (self.authz.delete if revoke else self.authz.write)(t)
            self.track.append(actor, "outbox.channel_revoked" if revoke else "outbox.channel_granted",
                              member_ref(mid), data={"channel": channel})

    def channels_of(self, member: dict) -> list[str]:
        known = set(self.db.outbox.distinct("channel"))
        known |= {o.split(":", 1)[1] for (o, _, _) in self.authz.read(relation="sender") if o.startswith("channel:")}
        known |= set(DEFAULTS) - {"default"}
        return sorted(c for c in known if self.can(member, "sender", f"channel:{c}"))

    # ------------------------------------------------------------------ the connector's API
    def outbox_pending(self, member: dict) -> list[dict]:
        """What this connector may deliver: queued messages on its channels, and the ones it has claimed. The body
        only of a message whose every data class it reads."""
        from .hub import Denied
        chans = self.channels_of(member)
        if not chans:
            raise Denied(f"{member['name']} delivers on no channel: a member admin grants a service its channels")
        rows = self.db.outbox.find({"channel": {"$in": chans},
                                    "$or": [{"status": "queued"}, {"status": "sending", "claimed_by": member["id"]}]},
                                   sort="id", limit=200)
        return [self._as_seen(member, r) for r in rows]

    def _as_seen(self, member: dict, r: dict) -> dict:
        readable = self.may_read_message(member, r)
        out = {k: r.get(k) for k in ("id", "at", "channel", "recipient", "subject", "status", "run_id", "claimed_by")}
        out["classes"] = self.message_classes_of(r)
        if readable:
            out["body"] = r["body"]
        else:
            out["body"] = None
            out["withheld"] = sorted(c for c in out["classes"] if not self.can(member, "reader", f"data_class:{c}"))
        return out

    def _require_channel(self, member: dict, row: dict, what: str) -> None:
        if not self.can(member, "sender", f"channel:{row['channel']}"):
            self.require(member, "sender", f"channel:{row['channel']}", what)

    def outbox_claim(self, member: dict, oid) -> dict:
        from .hub import Denied, Invalid
        row = self._message(oid)
        self._require_channel(member, row, f"deliver {row['channel']} messages")
        if not self.may_read_message(member, row):
            missing = sorted(c for c in self.message_classes_of(row) if not self.can(member, "reader", f"data_class:{c}"))
            self.track.append(member, "access.denied", f"outbox:{row['id']}",
                              data={"what": "claim a message it may not read", "classes": missing})
            raise Denied(f"{member['name']} may not deliver this message: it holds {', '.join(missing)} information "
                         f"it does not read")
        if row["status"] in OPEN:
            self._refuse_if_no_consent(member, row)
        now = datetime.now(timezone.utc)
        stale = _stamp(now - LEASE)
        with self.db:
            # compare-and-set: only one claimer finds it queued (or its lease run out)
            n = self.db.outbox.update(
                {"_id": row["id"], "$or": [{"status": "queued"},
                                           {"status": "sending", "$or": [{"claimed_at": None}, {"claimed_at": {"$lt": stale}}]}]},
                {"$set": {"status": "sending", "claimed_by": member["id"], "claimed_at": utcnow()}})
            if n != 1:
                raise Invalid(f"message {row['id']} is {row['status']}: it is not waiting to be sent")
            row = self._message(row["id"])
            self._outbox_event(member, "outbox.claimed", row)
        return self._as_seen(member, row)

    def _finish_claim(self, member: dict, oid, status: str, note: str, field: str) -> dict:
        from .hub import Invalid
        row = self._message(oid)
        self._require_channel(member, row, f"deliver {row['channel']} messages")
        if row["status"] != "sending" or row.get("claimed_by") != member["id"]:
            raise Invalid(f"message {row['id']} is not being sent by {member['name']}: claim it first")
        with self.db:
            self.db.outbox.set(row["id"], {"status": status, "done_at": utcnow(), "done_by": member["id"],
                                           "note": note[:2000]})
            row = self._message(row["id"])
            seq = self._outbox_event(member, f"outbox.{status}", row, **({field: note[:2000]} if note else {}))
            if status == "sent":
                self._close_delivery_work(row, member, seq)
            else:
                self._escalate_message(row, seq, why=f"the {row['channel']} connector could not deliver it")
        return self._as_seen(member, row)

    def outbox_sent(self, member: dict, oid, reference: str = "") -> dict:
        return self._finish_claim(member, oid, "sent", str(reference or ""), "reference")

    def outbox_failed(self, member: dict, oid, reason: str) -> dict:
        from .hub import Invalid
        if not str(reason or "").strip():
            raise Invalid("say why it could not be delivered")
        return self._finish_claim(member, oid, "failed", str(reason).strip(), "reason")

    # ------------------------------------------------------------------ people
    def may_deliver_by_hand(self, member: dict, row: dict) -> bool:
        return (member.get("kind") == "person" and member.get("status") == "active" and row["status"] in OPEN
                and self.may_read_message(member, row))

    def outbox_delivered_by_hand(self, member: dict, oid, note: str) -> dict:
        """A person who reads the message delivered it themselves (phoned, faxed): it is marked delivered."""
        from .hub import Denied, Invalid
        row = self._message(oid)
        if member.get("kind") != "person":
            raise Denied("a message is marked delivered by hand by a person")
        if row["status"] not in OPEN:
            raise Invalid(f"message {row['id']} is already {row['status']}")
        if not self.may_read_message(member, row):
            self.track.append(member, "access.denied", f"outbox:{row['id']}",
                              data={"what": "mark delivered a message they may not read"})
            raise Denied(f"{member['name']} may not deliver this message: it holds information they do not read")
        note = str(note or "").strip()
        if not note:
            raise Invalid("say how it was delivered (who you reached, and when)")
        self._refuse_if_no_consent(member, row)
        with self.db:
            self.db.outbox.set(row["id"], {"status": "delivered", "done_at": utcnow(), "done_by": member["id"],
                                           "note": note[:2000]})
            row = self._message(row["id"])
            seq = self._outbox_event(member, "outbox.delivered", row, how="by hand", note=note[:2000])
            self._close_delivery_work(row, member, seq)
        return row

    # ------------------------------------------------------------------ escalation
    def _to_words(self, recipient: str) -> str:
        r = str(recipient or "")
        if r.startswith("role:"):
            rid = r.split(":", 1)[1]
            row = self.db.roles.get(rid)
            return "the " + (row["name"] if row else rid).lower()
        if r.startswith("member:"):
            try:
                return self.member(r.split(":", 1)[1])["name"]
            except Exception:  # noqa: BLE001
                return r
        if "/" in r:  # a record (a patient): not named in a work title
            return f"the {r.split('/', 1)[0].lower()}"
        return r[:120]

    def _escalate_message(self, row: dict, cause: int, why: str) -> list[str]:
        """Tell the escalation role, and give a person the work of delivering it by hand (urgent channels, and every
        failure)."""
        if not row.get("run_id") or not self.db.runs.exists({"_id": row["run_id"]}):
            return []
        conf = self.channel_settings(row["channel"])
        run = self.engine.run(row["run_id"])
        failed = row["status"] == "failed"
        urgent = conf["urgent"] or failed
        prio = "urgent" if conf["urgent"] else "normal"
        to = self._to_words(row["recipient"])
        out = self.engine.alert(run, None, f"{'Urgent: ' if conf['urgent'] else ''}{row['channel']} message "
                                           f"#{row['id']} to {to} was not delivered: {why}.",
                                role=conf.get("escalate_to") or self.practice.get("escalation_role"), cause=cause,
                                priority=prio, about=f"outbox:{row['id']}")
        if urgent:
            task = str(conf.get("task") or DEFAULTS["default"]["task"])
            title = task.replace("{to}", to).replace("{channel}", row["channel"])
            out += self.engine.alert(run, None, f"{title}: {row['channel']} message #{row['id']} ({why}). "
                                                f"Deliver it, then mark it delivered.",
                                     role=conf.get("deliver_role") or self.practice.get("escalation_role"),
                                     cause=cause, priority="urgent", about=f"outbox:{row['id']}")
        return out

    def _close_delivery_work(self, row: dict, actor: dict, cause: int) -> list[str]:
        """The notices about a message (late, failed, deliver it by hand) are done once it is delivered."""
        ids = [r["id"] for r in self.db.items.find({"kind": "notice", "status": {"$in": ["open", "claimed"]},
                                                    "output.about": f"outbox:{row['id']}"}, fields=["id"])]
        for iid in ids:
            self.db.items.set(iid, {"status": "done", "updated_at": utcnow()})
            self.track.append(actor, "notice.closed", f"item:{iid}", run_id=row.get("run_id"), cause=cause,
                              data={"why": f"message {row['id']} was delivered"})
        return ids

    def outbox_tick(self, now: datetime | None = None) -> list[int]:
        """Escalate every message still undelivered past its channel's deadline, once."""
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        late = []
        for row in self.db.outbox.find({"status": {"$in": ["queued", "sending"]}, "escalated": 0}):
            conf = self.channel_settings(row["channel"])
            if _parse(row["at"]) + timedelta(minutes=conf["deadline_minutes"]) > now:
                continue
            with self.db:
                self.db.outbox.set(row["id"], {"escalated": 1})
                seq = self._outbox_event({"id": "clock", "kind": "automation", "name": "Practice Hub"},
                                         "outbox.overdue", row, status=row["status"],
                                         deadline_minutes=conf["deadline_minutes"])
                self._escalate_message(row, seq, why=f"still {'queued' if row['status'] == 'queued' else 'not sent'} "
                                                     f"after {conf['deadline_minutes']} minutes")
            late.append(row["id"])
        return late
