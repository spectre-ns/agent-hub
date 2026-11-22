"""Mentions: ``@someone`` in a record's text or in a comment on it, for people and agents working together.

A mention names a member by id (``@noor``, ``@scribe-agent``): it is stored as that member's reference, with the
record, where in it (a quote and the comment thread, the *anchor*), the words around it, and who wrote it. Nothing
here knows what kind of record it is on: a page, a document, anything with text.

An anchor is one of two kinds. *Text*: a passage of a field of the record (``field``, with the ``quote``). *File*: a
file the record holds in an attachment field (``field`` and the file's name, ``file``), with an optional quote of its
words; the mentioned member is given the file's text (:func:`practicehub.filetext.text_of`), read as they may read it.

Mentioning someone gives them a notice in their inbox, on a run of its own (the built-in ``mention`` workflow), so a
person sees it where they see the rest of their work and an agent finds it through ``/api/inbox``. What the notice
carries is only where to look; the record, the passage and the thread come from :meth:`Mentions.context`, which asks
again whether that member may read the record. An agent nobody let read the record is not mentioned at all: the
person typing is told so (:class:`~practicehub.hub.Invalid`), and nothing is sent.

An agent answers in the thread with its own token, and its answer is marked as an agent's. It may propose a change to
the text as a *suggestion*; a person who may change the record accepts it (it is then saved as that person's change,
naming the agent) or rejects it. On a file, nobody's suggestion is applied by the hub: the person makes the change in
the file themselves, and accepting it says they did. An agent never changes the text itself here, and nothing here signs or approves.
Every step is on the track: ``mention.created``, ``mention.refused``, ``mention.answered``,
``suggestion.proposed``, ``suggestion.accepted``, ``suggestion.rejected``.
"""

from __future__ import annotations

from . import filetext, visibility

import re
import secrets

from .hub import Denied, Invalid, NotFound, member_ref
from .store import utcnow

MENTION_WORKFLOW = "mention"
MENTION_SOURCE = (f'id = "{MENTION_WORKFLOW}"\ntitle = "You were mentioned"\n'
                  'summary = "Someone named a member in a record\'s text or in a comment on it."\n'
                  '[[steps]]\nid = "look"\ntitle = "Read where you were mentioned"\nkind = "wait"\nuntil = "false"\n')

_AT = re.compile(r"(?<![\w@/])@([a-z0-9][a-z0-9_.-]*[a-z0-9])")


def mentioned_ids(text: str) -> list[str]:
    """The ids written as ``@id`` in a text, in the order first written (not checked against the members)."""
    seen: list[str] = []
    for m in _AT.findall(text or ""):
        if m not in seen:
            seen.append(m)
    return seen


def _around(text: str, mid: str, width: int = 80) -> str:
    i = (text or "").find("@" + mid)
    if i < 0:
        return ""
    return text[max(0, i - width): i + len(mid) + 1 + width].strip()


class Mentions:
    def __init__(self, hub):
        self.hub = hub
        self.db = hub.db

    # ------------------------------------------------------------------ who may be mentioned
    def _class(self, ref: str) -> str:
        return self.hub.class_of(ref.split("/", 1)[0])

    def may_see(self, member: dict, ref: str) -> bool:
        return self.hub.can(member, "reader", f"data_class:{self._class(ref)}")

    def candidates(self, member: dict, ref: str, q: str = "") -> list[dict]:
        """Whom ``member`` may mention on ``ref``: active people and agents, each with whether they may read it."""
        self.hub.require(member, "reader", f"data_class:{self._class(ref)}", "read this record")
        q = (q or "").casefold()
        out = []
        for m in self.hub.members():
            if m.get("status") != "active" or m["kind"] not in ("person", "agent"):
                continue
            if q and q not in m["id"].casefold() and q not in m["name"].casefold():
                continue
            ok = self.may_see(m, ref)
            out.append({"id": m["id"], "name": m["name"], "kind": m["kind"], "title": m.get("title") or "",
                        "may_read": ok, "why": "" if ok else f"{m['name']} may not read this"})
        out.sort(key=lambda r: (r["kind"] != "person", not r["may_read"], r["name"].casefold()))
        return out

    def resolve(self, ref: str, text: str) -> tuple[list[dict], list[dict]]:
        """The members a text mentions: (those who may read ``ref``, those who may not). Ids of nobody are words."""
        ok, refused = [], []
        for mid in mentioned_ids(text):
            try:
                m = self.hub.member(mid)
            except NotFound:
                continue
            if m.get("status") != "active" or m["kind"] not in ("person", "agent"):
                continue
            (ok if self.may_see(m, ref) else refused).append(m)
        return ok, refused

    def check(self, author: dict, ref: str, text: str) -> list[dict]:
        """Before a comment is saved: every member it mentions, or :class:`Invalid` naming any who may not read
        ``ref`` (an agent is never handed what its grants do not let it read; a person is told so as they write)."""
        ok, refused = self.resolve(ref, text)
        if refused:
            who = ", ".join(m["name"] for m in refused)
            self.hub.track.append(author, "mention.refused", ref, data={"who": [m["id"] for m in refused]})
            raise Invalid(f"{who} may not read this, so cannot be mentioned here. Take the mention out, or ask "
                          f"whoever looks after {'them' if len(refused) > 1 else 'that member'} to let "
                          f"{'them' if len(refused) > 1 else 'it' if refused[0]['kind'] == 'agent' else 'them'} "
                          f"read it.")
        return ok

    # ------------------------------------------------------------------ mentioning
    def mention(self, author: dict, ref: str, text: str, *, anchor: str = "", quote: str = "", field: str = "",
                file: str = "", cause: int | None = None, skip_refused: bool = False) -> list[dict]:
        """Record and send each mention in ``text`` (written by ``author`` on ``ref``). ``anchor`` is where it was
        written (a comment thread's id, or a field of the record); ``quote`` the passage it is about; ``file`` the name of
the file in ``field`` it is about, when it is about a file rather than the field's text. With
        ``skip_refused`` (text saved by several people at once), a member who may not read ``ref`` is left out and
        the refusal is tracked, instead of stopping the save."""
        if skip_refused:
            members, refused = self.resolve(ref, text)
            if refused:
                self.hub.track.append(author, "mention.refused", ref, cause=cause,
                                      data={"who": [m["id"] for m in refused], "field": field})
        else:
            members = self.check(author, ref, text)
        out = []
        for m in members:
            if m["id"] == author["id"]:
                continue
            out.append(self._send(author, ref, m, text=text, anchor=anchor, quote=quote, field=field, file=file,
                                  cause=cause))
        return out

    def _send(self, author, ref, m, *, text, anchor, quote, field, file="", cause) -> dict:
        hub = self.hub
        from .engine import _item_doc, _new_id
        mid = "mn-" + secrets.token_hex(6)
        rtype, rid = ref.split("/", 1)
        rec = hub.records.read(rtype, rid)
        title = hub.short_title(rec) if rec else ref
        with self.db:
            seq = hub.track.append(author, "mention.created", ref, cause=cause,
                                   data={"mention": mid, "who": m["id"], "kind": m["kind"], "anchor": anchor,
                                         "field": field, **({"file": file} if file else {})})
            run_id = _new_id("run")
            self.db.runs.insert({"_id": run_id, "id": run_id, "workflow_id": MENTION_WORKFLOW, "version": 0,
                                 "title": f"You were mentioned: {title}", "record": ref, "status": "completed",
                                 "ctx": {"mention": mid}, "steps": {"look": {"state": "skipped"}},
                                 "started_by": author["id"], "root_event": seq, "created_at": utcnow(),
                                 "updated_at": utcnow()})
            item_id = _new_id("item")
            what = "asked you" if m["kind"] == "agent" else "mentioned you"
            self.db.items.insert(_item_doc(
                hub, id=item_id, run_id=run_id, step_id="look", kind="notice", role=None, assignee=m["id"],
                persons_only=0, status="open", cause=seq,
                title=f"{author['name']} {what} on “{title}”",
                output={"about": ref, "mention": mid}))
            doc = {"_id": mid, "id": mid, "ref": ref, "who": member_ref(m["id"]), "member": m["id"],
                   "kind": m["kind"], "by": author["id"], "anchor": anchor, "field": field, "file": file or "",
                   "quote": quote[:500],
                   "around": _around(text, m["id"]), "at": utcnow(), "item": item_id, "run": run_id,
                   "answered": False}
            self.db.mentions.insert(doc)
        if m["kind"] == "agent":
            hub.wake_agents()
        return doc

    def get(self, mid: str) -> dict:
        row = self.db.mentions.get(mid)
        if not row:
            raise NotFound(f"there is no mention {mid}")
        return row

    def thread(self, ref: str, anchor: str) -> dict | None:
        """The comment thread at ``anchor`` (comments on any record are kept in ``live_comments``)."""
        c = self.db.live_comments.get(anchor) if anchor else None
        if not c or c["ref"] != ref:
            return None
        return {k: c.get(k) for k in ("id", "quote", "body", "by", "at", "resolved", "replies", "field", "file")}

    # ------------------------------------------------------------------ a file as an anchor
    @staticmethod
    def attachment_named(rec: dict, path: str, name: str) -> dict | None:
        """The file called ``name`` that ``rec`` holds at ``path`` (an attachment field, one file or a list)."""
        now = rec.get(path.removesuffix(".*")) if rec else None
        for a in (now if isinstance(now, list) else [now]):
            if isinstance(a, dict) and isinstance(a.get("blob"), str) and (a.get("name") or "") == name:
                return a
        return None

    def file_text(self, member: dict, ref: str, field: str, name: str, rec: dict | None = None) -> dict:
        """The words of the file ``name`` in ``field`` of ``ref``, as ``member`` may read them: the record read as
        theirs (a field of a class they may not read is not there, so neither is its file), then the stored file."""
        rt = self.hub.record_type_for_reading(ref.split("/", 1)[0])
        f = rt.field(field) if rt else None
        out = {"field": field, "name": name, "available": False, "text": "", "why": "text not available"}
        if not f or f["type"] != "attachment":
            return out
        rec = rec if rec is not None else self.hub.read_record(member, ref)
        a = self.attachment_named(rec, f["path"], name)
        if not a:
            out["why"] = "the record does not hold this file now"
            return out
        data, meta = self.hub.read_file(member, ref, a["blob"])
        text = filetext.text_of(data, name, a.get("contentType") or meta.get("contentType") or "")
        out["blob"] = a["blob"]
        if text is None:
            return out
        return {**out, "available": True, "text": text, "why": ""}

    def context(self, member: dict, mid: str) -> dict:
        """What the mentioned member is given: the record (only if they may still read it), the passage, the words
        around the mention, and the thread it is in."""
        row = self.get(mid)
        if row["member"] != member["id"]:
            raise Denied("this mention is for someone else")
        ref = row["ref"]
        rec = self.hub.read_record(member, ref)  # asks again: a grant taken away since is respected (and redacted)
        thread = self.thread(ref, row["anchor"])
        mention = {k: row[k] for k in ("id", "ref", "by", "anchor", "field", "quote", "around", "at")}
        mention["file"] = row.get("file") or ""
        rt = self.hub.record_type_for_reading(ref.split("/", 1)[0])
        hidden = bool(row.get("field")) and any(f["name"] == row["field"] or f["path"] == row["field"]
                                                for f in visibility.hidden_fields(self.hub, member, rt))
        if hidden:
            mention["quote"], thread = "", None  # a passage of a field they may not read is not theirs to see
        out = {"mention": mention,
               "record": {k: v for k, v in rec.items() if k != "meta"}, "thread": thread,
               "reply": f"/api/mentions/{mid}/reply", "suggest": f"/api/mentions/{mid}/suggest"}
        if mention["file"]:
            out["document"] = ({"field": row["field"], "name": mention["file"], "available": False, "text": "",
                                "why": "text not available"} if hidden
                               else self.file_text(member, ref, row["field"], mention["file"], rec=rec))
            out["suggest_how"] = ("A suggestion on a file is words to replace (find, quoted from its text) and what "
                                  "to put instead (text). The hub never changes the file: a person makes the change.")
        return out

    def answered(self, member: dict, ref: str, anchor: str, cause: int | None = None) -> None:
        """``member`` answered in the thread at ``anchor``: its open mentions there are done."""
        for row in self.db.mentions.find({"ref": ref, "anchor": anchor, "member": member["id"], "answered": False}):
            self.db.mentions.set(row["_id"], {"answered": True, "answered_at": utcnow()})
            item = self.db.items.get(row["item"])
            if item and item["status"] in ("open", "claimed"):
                self.db.items.set(row["item"], {"status": "done", "claimed_by": member["id"], "updated_at": utcnow()})
            self.hub.track.append(member, "mention.answered", ref, cause=cause, run_id=row["run"],
                                  data={"mention": row["_id"], "anchor": anchor})


def mentions_of(hub) -> Mentions:
    m = getattr(hub, "_mentions", None)
    if m is None:
        m = hub._mentions = Mentions(hub)
    return m
