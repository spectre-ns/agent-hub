"""Talking to an agent: a conversation between one person and one agent, about no one record.

A person opens a chat with an agent (any active agent whose sponsor is active) and asks it things in their own words.
The agent answers with its profile's model, and may use a small fixed set of tools: find a record by its key, search
a type, read a record, describe a type, make, change or move a record, and start a workflow. Each one is the agent's
own MCP tool for that type or workflow (:func:`practicehub.mcp.run_tool`), run as the agent: its grants, its approved
profile (``direct_writes``), the lifecycle's ``by`` and the moves that put something into force (never an agent's)
apply as they do over MCP, and a workflow it starts gives its steps to whoever they name, under the usual risk tiers
and reviews. Nothing here signs or approves.

Both members: in a chat the agent reaches a record only when the person may too (:meth:`Chat.may_reach`). A type, a
workflow or a move is offered only when both members' tool lists have it; a call on a record a subject's directive
keeps from the person is refused before it is made; a write sets only fields both may write; what comes back is
redacted for the person as well, and a search hit the person would not find is dropped. The model sees only what
both may see.

A chat is its person's: nobody else reads it. It keeps which records it used (``resources``: listed, read, created,
updated, moved or started), and the track has each step: ``chat.started`` and ``chat.asked`` (the person), each tool
as ``mcp.called`` with how ``chat``, caused by the question it answers, and ``chat.answered`` or ``chat.failed`` (the
agent). Chat events carry ids, never what was said.

A conversation is only itself: nothing carries over to another, and nothing said in one changes the agent. What an
agent is and may do changes only through its approved profile and skills.

The model's tool calls are data: a name from the list it was given, with arguments checked against that tool's schema.
Anything else goes back to the model as an error, and nothing is run.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import threading

from . import mcp, visibility
from .db import utcnow
from .hub import Denied, HubError, Invalid, NotFound

MAX_CALLS = 8          # tool calls in one answer
HISTORY = 20           # messages of the conversation the model is given
RESULT_CHARS = 4000    # of one tool's result
MAX_BODY = 4000        # of one message from the person
SEARCH_LIMIT = 25
STALE_SECONDS = 900    # a turn another machine left busy this long ago is taken to have died with it

REF_TEXT = re.compile(r"[A-Z][A-Za-z0-9]*/[A-Za-z0-9_.-]+")
REF = {"type": "string", "pattern": r"^[A-Z][A-Za-z0-9]*/[A-Za-z0-9_.-]+$", "description": "the record, as Type/id"}
TEXT = {"type": "string", "maxLength": 500}
FIELDS = {"type": "object", "description": "the fields by name, as describe gives them"}
COMMENT = {"type": "string", "maxLength": 2000, "description": "why (kept with the version)"}
VERSION = {"type": "string", "description": "the version you read (from read): a change to another one is refused"}

_RUNNING: set[str] = set()  # the chats this process is answering now
_LOCK = threading.Lock()


class Offer:
    """What the person and the agent may both do: each of the agent's MCP tools that the person's list also has,
    by what it does (``("search", type)``, ``("start", workflow)``), with the person's own copy of it."""

    def __init__(self, hub, person: dict, agent: dict):
        self.calls: dict[tuple, tuple[str, dict, dict]] = {}
        self.ts: dict[str, list[dict]] = {}
        for s in mcp.servers(hub):
            sid = s["id"]
            if s.get("unavailable"):
                continue
            mine = mcp.tools(hub, sid, agent)
            theirs = {t["_call"]: t for t in mcp.tools(hub, sid, person)}
            self.ts[sid] = mine
            for t in mine:
                if t["_call"] in theirs and t["_call"][0] in ("key", "search", "read", "create", "update", "move",
                                                              "start"):
                    self.calls.setdefault(t["_call"], (sid, t, theirs[t["_call"]]))

    def having(self, what: str) -> list[str]:
        return sorted(k[1] for k in self.calls if k[0] == what)

    def get(self, what: str, target, label: str) -> tuple[str, dict, dict]:
        got = self.calls.get((what, target))
        if got is None:
            raise Denied(f"you and the person you are talking with may not both {label}")
        return got


class Chat:
    def __init__(self, hub, gateway=None):
        self.hub = hub
        self._gateway = gateway

    @property
    def gateway(self):
        if self._gateway is None:
            from .models import ModelGateway
            self._gateway = ModelGateway(self.hub)
        return self._gateway

    @property
    def db(self):
        return self.hub.db

    # ------------------------------------------------------------------------------------------------ who
    def _pair(self, person_id: str, agent_id: str) -> tuple[dict, dict]:
        """The person and the agent, both as they are now: each turn and each call asks again."""
        person, agent = self.hub.member(person_id), self.hub.member(agent_id)
        if person["kind"] != "person" or person["status"] != "active":
            raise Denied(f"{person['name']} is not an active person: only a person talks with an agent")
        if agent["kind"] != "agent" or agent["status"] != "active":
            raise Denied(f"{agent['name']} is not an active agent")
        sponsor = self.db.members.get(agent.get("sponsor") or "")
        if not sponsor or sponsor["kind"] != "person" or sponsor["status"] != "active":
            raise Denied(f"{agent['name']}'s sponsor is not active: it does nothing until it has one")
        return person, agent

    def agents(self, person: dict) -> list[dict]:
        """The agents this person may talk with."""
        out = []
        for a in self.hub.members("agent"):
            try:
                self._pair(person["id"], a["id"])
            except HubError:
                continue
            out.append({"id": a["id"], "name": a["name"], "title": a.get("title") or ""})
        return out

    def may_reach(self, person: dict, agent: dict, ref: str) -> bool:
        """Whether, in a chat between them, the agent may reach this record: both read its type, and no subject's
        directive keeps it from either (what each would otherwise read)."""
        rtype = ref.split("/", 1)[0]
        if self.hub.record_type_for_reading(rtype) is None:
            return False
        return all(self.hub.may_read(m, rtype) and not self.hub.restrictions.withholds_from(m, ref, reaching=True)
                   for m in (person, agent))

    def _reach(self, person, agent, ref: str) -> None:
        if not self.may_reach(person, agent, ref):
            self.hub.track.append(agent, "access.denied", ref, data={"relation": "reader", "what": "chat",
                                                                      "with": person["id"]})
            raise Denied(f"{ref} is not one you and {person['name']} may both read")

    # ------------------------------------------------------------------------------------------------ the chat
    def start(self, person: dict, agent_id: str) -> dict:
        person, agent = self._pair(person["id"], agent_id)
        now = utcnow()
        cid = "ch-" + secrets.token_hex(6)
        doc = {"_id": cid, "id": cid, "person": person["id"], "agent": agent["id"], "title": "", "at": now,
               "updated_at": now, "busy": None, "n": 0, "resources": []}
        self.db.chats.insert(doc)
        self.hub.track.append(person, "chat.started", f"chat:{cid}", data={"chat": cid, "agent": agent["id"]})
        return self._out(doc)

    def list(self, person: dict, limit: int = 50) -> list[dict]:
        return [self._out(c) for c in self.db.chats.find({"person": person["id"]}, sort=[("updated_at", -1)],
                                                          limit=limit)]

    def get(self, person: dict, cid: str) -> dict:
        """The chat, for its person only (anyone else is told there is none). A turn left unfinished by a hub that
        stopped is closed here, said so in the chat."""
        c = self.db.chats.get(cid) if isinstance(cid, str) else None
        if not c or c["person"] != person["id"]:
            raise NotFound("there is no such conversation")
        if c.get("busy") and self._stale(cid, c["busy"]):
            if self.db.chats.update({"_id": cid, "busy": c["busy"]}, {"$set": {"busy": None}}):
                self._add(cid, "agent", "My answer was cut off: the hub stopped while I was working on it. Please "
                                        "ask again.", error=True)
            c = self.db.chats.get(cid)
        return self._out(c)

    def messages(self, person: dict, cid: str, since: int = 0) -> list[dict]:
        self.get(person, cid)
        return [self._msg(m) for m in self.db.chat_messages.find({"chat": cid, "n": {"$gt": int(since)}},
                                                                 sort=[("n", 1)])]

    @staticmethod
    def _out(c: dict) -> dict:
        return {k: c.get(k) for k in ("id", "person", "agent", "title", "at", "updated_at", "n", "resources")} | {
            "busy": bool(c.get("busy"))}

    @staticmethod
    def _msg(m: dict) -> dict:
        return {k: m.get(k) for k in ("id", "n", "role", "body", "tool", "args", "refs", "error", "at")}

    def _stale(self, cid: str, busy: dict) -> bool:
        if cid in _RUNNING:
            return False
        if busy.get("host") != socket.gethostname():
            from datetime import datetime, timezone
            try:
                at = datetime.fromisoformat(str(busy.get("at")).replace("Z", "+00:00"))
            except ValueError:
                return True
            return (datetime.now(timezone.utc) - at).total_seconds() > STALE_SECONDS
        if busy.get("pid") == os.getpid():
            return True  # this process is not answering it: the thread that was is gone
        try:
            os.kill(int(busy.get("pid") or 0), 0)
        except (OSError, ValueError):
            return True
        return False

    def _add(self, cid: str, role: str, body: str, **extra) -> dict:
        c = self.db.chats.find_one_and_update({"_id": cid}, {"$inc": {"n": 1}, "$set": {"updated_at": utcnow()}})
        mid = "cm-" + secrets.token_hex(8)
        doc = {"_id": mid, "id": mid, "chat": cid, "n": c["n"], "role": role, "body": body, "at": utcnow(),
               **{k: v for k, v in extra.items() if v not in (None, [], False)}}
        self.db.chat_messages.insert(doc)
        return self._msg(doc)

    def _used(self, cid: str, ref: str, how: str, seq: int) -> None:
        """A record the chat used, once for each way it used it."""
        self.db.chats.update({"_id": cid, "resources": {"$not": {"$elemMatch": {"ref": ref, "how": how}}}},
                             {"$push": {"resources": {"ref": ref, "how": how, "at": utcnow(), "seq": seq}}})

    # ------------------------------------------------------------------------------------------------ a turn
    def ask(self, person: dict, cid: str, body: str) -> tuple[dict, int]:
        """The person's message, stored, and the chat marked busy until the agent has answered: returns the message
        and its ``chat.asked`` event (what the answer's calls are caused by). One answer at a time in a chat."""
        c = self.get(person, cid)
        body = str(body or "").strip()
        if not body:
            raise Invalid("say something to the agent")
        if len(body) > MAX_BODY:
            raise Invalid(f"a message is at most {MAX_BODY} characters")
        person, agent = self._pair(person["id"], c["agent"])
        busy = {"at": utcnow(), "host": socket.gethostname(), "pid": os.getpid()}
        with _LOCK:
            if not self.db.chats.update({"_id": cid, "busy": None}, {"$set": {"busy": busy}}):
                raise Invalid(f"{agent['name']} is still answering: wait for it")
            _RUNNING.add(cid)
        try:
            if not c["title"]:
                self.db.chats.set(cid, {"title": " ".join(body.split())[:80]})
            msg = self._add(cid, "person", body)
            seq = self.hub.track.append(person, "chat.asked", f"chat:{cid}",
                                        data={"chat": cid, "agent": agent["id"], "message": msg["id"]})
        except BaseException:
            self._idle(cid)
            raise
        return msg, seq

    def say(self, person: dict, cid: str, body: str) -> dict:
        """Ask and wait for the answer: the agent's last message."""
        _, seq = self.ask(person, cid, body)
        return self.answer(person["id"], cid, seq)

    def _idle(self, cid: str) -> None:
        self.db.chats.set(cid, {"busy": None})
        _RUNNING.discard(cid)

    def answer(self, person_id: str, cid: str, cause: int) -> dict:
        """The agent's turn: it asks its model, runs the calls the model makes (at most ``MAX_CALLS``), and ends
        with its answer. Returns the agent's message. The chat is free again however the turn ends."""
        from .models import ModelError, chat_system
        c = self.db.chats.get(cid)
        agent_id, made, spec = c["agent"], 0, None
        try:
            person, agent = self._pair(person_id, agent_id)
            while True:
                offer = Offer(self.hub, person, agent)
                tools = self.tools(offer) if made < MAX_CALLS else []
                system = chat_system(agent, person, self.hub.practice.get("name") or "the practice",
                                     self._rules(), self._menu(offer))
                out = self.gateway.chat(agent, system, self._history(cid), tools, {"chat": cid})
                spec = out.get("model")
                calls = out["calls"][:MAX_CALLS - made] if tools else []
                text = (out.get("text") or "").strip()
                if not calls:
                    msg = self._add(cid, "agent", text or ("I used as many tools as I may for one answer: ask me "
                                                            "to go on." if made >= MAX_CALLS else "(no answer)"))
                    self.hub.track.append(agent, "chat.answered", f"chat:{cid}", cause=cause,
                                          data={"chat": cid, "message": msg["id"], "calls": made, "model": spec})
                    return msg
                if text:
                    self._add(cid, "agent", text)
                for call in calls:
                    person, agent = self._pair(person_id, agent_id)
                    got = self.call(person, agent, offer, cid, call, cause)
                    self._add(cid, "tool", got["body"][:RESULT_CHARS], tool=str(call.get("name") or "")[:64],
                              args=call.get("args") if isinstance(call.get("args"), dict) else None,
                              refs=got.get("refs"), error=got.get("error"))
                    made += 1
        except Exception as exc:  # noqa: BLE001 - however the turn fails, the person is told, and the track has it
            if isinstance(exc, ModelError):
                why = "my model did not answer"
            elif isinstance(exc, HubError):
                why = str(exc).rstrip(".")
            else:  # a failure of the hub itself: the detail goes to the server's log
                import traceback
                traceback.print_exc()
                why = "the hub could not finish it (an internal error, logged)"
            msg = self._add(cid, "agent", f"I can't answer this: {why}.", error=True)
            try:
                actor = self.hub.member(agent_id)
            except HubError:
                actor = {"id": agent_id, "kind": "agent"}
            self.hub.track.append(actor, "chat.failed", f"chat:{cid}", cause=cause,
                                  data={"chat": cid, "message": msg["id"], "calls": made, "model": spec,
                                        "error": type(exc).__name__})
            return msg
        finally:
            self._idle(cid)

    def _rules(self) -> list[str]:
        pack = self.hub.pack
        return list((pack.practice.get("agent_rules") if pack else None) or [])

    def _menu(self, offer: Offer) -> dict:
        hub = self.hub
        types = [{"id": t, "label": hub.record_type(t).plural} for t in offer.having("search")]
        flows = []
        for w in offer.having("start"):
            try:
                flows.append({"id": w, "label": hub.workflow(w)[0].title})
            except HubError:
                continue
        return {"types": types, "workflows": flows}

    def _history(self, cid: str) -> list[dict]:
        """The conversation the model is given: its last ``HISTORY`` messages, from a message of the person's."""
        rows = list(reversed(self.db.chat_messages.find({"chat": cid}, sort=[("n", -1)], limit=HISTORY)))
        while rows and rows[0]["role"] != "person":
            rows.pop(0)
        return [{k: m[k] for k in ("id", "role", "body", "tool", "args", "error") if m.get(k) is not None}
                for m in rows]

    # ------------------------------------------------------------------------------------------------ the tools
    def tools(self, offer: Offer) -> list[dict]:
        """The tools the model is given: ``{name, description, schema}``, each over what both members may do."""
        types = offer.having("search")
        out = [{"name": "find", "description": "What a key names (like T1-12): its record's ref, title and state.",
                "schema": _obj({"key": {"type": "string", "maxLength": 20}}, ["key"])}]
        if types:
            ty = {"type": "string", "enum": types}
            out += [
                {"name": "search", "description": "Search one type's records by words (and state). Gives each "
                                                  "one's ref (Type/id), key, title and state.",
                 "schema": _obj({"type": ty, "text": TEXT, "state": {"type": "string", "maxLength": 80},
                                 "limit": {"type": "integer", "minimum": 1, "maximum": SEARCH_LIMIT}}, ["type"])},
                {"name": "read", "description": "Read one record, whole, by its ref.",
                 "schema": _obj({"ref": REF}, ["ref"])},
                {"name": "describe", "description": "A type's fields, states and moves, and what you may do with "
                                                    "it.", "schema": _obj({"type": ty}, ["type"])}]
        if offer.having("create"):
            out.append({"name": "create", "description": "Make a record of a type, with its fields.",
                        "schema": _obj({"type": {"type": "string", "enum": offer.having("create")},
                                        "fields": FIELDS}, ["type", "fields"])})
        if offer.having("update"):
            out.append({"name": "update", "description": "Change fields of a record (read it first for its "
                                                         "version); fields left out stay, null clears one.",
                        "schema": _obj({"ref": REF, "version": VERSION, "fields": FIELDS, "comment": COMMENT},
                                       ["ref", "version", "fields"])})
        if offer.having("move"):
            out.append({"name": "move", "description": "Move a record along its lifecycle to a state (describe "
                                                       "gives its moves).",
                        "schema": _obj({"ref": REF, "version": VERSION, "to": {"type": "string", "maxLength": 80},
                                        "fields": FIELDS, "comment": COMMENT}, ["ref", "version", "to"])})
        if offer.having("start"):
            out.append({"name": "start", "description": "Start a workflow: its steps go to whoever they name, "
                                                        "checked as all work is.",
                        "schema": _obj({"workflow": {"type": "string", "enum": offer.having("start")},
                                        "record": REF, "input": {"type": "object"}}, ["workflow"])})
        return out

    def call(self, person: dict, agent: dict, offer: Offer, cid: str, call: dict, cause: int) -> dict:
        """One of the model's calls, as data: ``{"body", "refs", "error"}``. A call that is not one of its tools,
        or whose arguments are not that tool's, is told so and not run; a refusal is told why."""
        name, args = call.get("name"), call.get("args")
        spec = next((t for t in self.tools(offer) if t["name"] == name), None)
        if spec is None:
            return {"body": f"there is no tool called {str(name)[:60]!r}: use one of the tools you were given",
                    "error": True}
        problems = mcp.check_args(spec["schema"], args)
        if problems:
            return {"body": "the arguments have problems: " + "; ".join(problems), "error": True}
        try:
            return getattr(self, f"_{name}")(person, agent, offer, cid, args, cause)
        except (HubError, mcp.Refused) as exc:
            return {"body": str(exc) + (": " + "; ".join(exc.problems) if getattr(exc, "problems", None) else ""),
                    "error": True}

    def _run(self, agent, offer, sid, tool, a, cause) -> tuple[int, dict]:
        seq, res = mcp.run_tool(self.hub, sid, agent, tool["name"], a, ts=offer.ts[sid], how="chat", cause=cause)
        if res["isError"]:
            raise mcp.Refused(res["content"][0]["text"])
        return seq, res["structuredContent"]

    def _seen(self, person, agent, rec: dict | None) -> dict | None:
        """A record as both members may see it: redacted for each (a title made of a field either may not read
        would tell the model that field)."""
        return self.hub.redact(person, self.hub.redact(agent, rec)) if rec else None

    def _record_for(self, person, agent, ref: str) -> dict | None:
        return self._seen(person, agent, self.hub.records.read(*ref.split("/", 1)))

    def _summary(self, person, agent, ref: str) -> dict:
        rec = self._record_for(person, agent, ref)
        rt = self.hub.record_type_for_reading(ref.split("/", 1)[0])
        return mcp._summary(self.hub, rt, rec) if rec else {"ref": ref}

    def _fields(self, person, agent, tools: tuple[dict, dict], fields: dict, fixed=()) -> dict:
        """The fields a write sets: only those both members' tools take, and no record either may not reach."""
        _, mine, theirs = tools
        both = set(mine["inputSchema"]["properties"]) & set(theirs["inputSchema"]["properties"]) - set(fixed)
        bad = sorted(set(fields or {}) - both)
        if bad:
            raise Invalid("these are not fields you and the person may both write here", bad)
        self._refs_reached(person, agent, fields)
        return dict(fields or {})

    def _refs_reached(self, person, agent, value) -> None:
        """Every record a write names: one either member may not reach is refused."""
        if isinstance(value, dict):
            for v in value.values():
                self._refs_reached(person, agent, v)
        elif isinstance(value, list):
            for v in value:
                self._refs_reached(person, agent, v)
        elif isinstance(value, str) and REF_TEXT.fullmatch(value) and self.hub.resolve_ref(value):
            self._reach(person, agent, self.hub.resolve_ref(value))

    def _find(self, person, agent, offer, cid, a, cause) -> dict:
        sid, tool, _ = offer.get("key", None, "look up keys")
        none = NotFound(f"nothing you and {person['name']} may both read has the key {a['key'].strip().upper()}")
        theirs = self.hub.find_key(person, a["key"])
        if not theirs:
            raise none
        if theirs.get("ref"):
            self._reach(person, agent, theirs["ref"])
        seq, out = self._run(agent, offer, sid, tool, a, cause)
        if theirs.get("ref") != out.get("ref") or theirs.get("item") != out.get("item"):
            raise none
        if out.get("item"):
            return {"body": f"{out['key']} is a work item, not a record: it is in the inbox of whoever has it"}
        self._used(cid, out["ref"], "listed", seq)
        return {"body": _json(self._summary(person, agent, out["ref"])), "refs": [out["ref"]]}

    def _search(self, person, agent, offer, cid, a, cause) -> dict:
        rtype = a["type"]
        sid, tool, _ = offer.get("search", rtype, f"search {rtype} records")
        limit = min(int(a.get("limit") or 10), SEARCH_LIMIT)
        q = {k: v for k, v in {"text": a.get("text"), "state": a.get("state"), "limit": limit}.items() if v}
        seq, out = self._run(agent, offer, sid, tool, q, cause)
        theirs = None
        if a.get("text"):  # words match only what each may read: a hit only the agent's reading finds is dropped
            theirs = {f"{r['resourceType']}/{r['id']}" for r in
                      self.hub.search_records(person, rtype, text=a["text"], limit=mcp.MAX_LIMIT)}
        recs = [x for x in (self._record_for(person, agent, r["ref"]) for r in out["records"]
                            if (theirs is None or r["ref"] in theirs) and self.may_reach(person, agent, r["ref"])) if x]
        refs = [f"{x['resourceType']}/{x['id']}" for x in recs]
        found = [mcp._summary(self.hub, self.hub.record_type_for_reading(rtype), x) for x in recs]
        self.hub.note_list(person, recs, how="chat", what=f"records:{rtype}", text=a.get("text") or None)
        for r in refs:
            self._used(cid, r, "listed", seq)
        return {"body": _json({"records": found, "count": len(found)}), "refs": refs}

    def _read(self, person, agent, offer, cid, a, cause) -> dict:
        ref = self.hub.resolve_ref(a["ref"])
        if not ref:
            raise NotFound(f"there is no {a['ref']}")
        rtype, rid = ref.split("/", 1)
        self._reach(person, agent, ref)
        sid, tool, _ = offer.get("read", rtype, f"read {rtype} records")
        seq, out = self._run(agent, offer, sid, tool, {"id": rid}, cause)
        rec = self.hub.redact(person, out["record"])  # what both may see: the agent's reading, then the person's
        gone = visibility.withheld(rec)
        self.hub.note_view(person, ref, how="withheld" if gone and gone.get("whole") else "chat")
        self._used(cid, ref, "read", seq)
        return {"body": _json({**mcp._summary(self.hub, self.hub.record_type_for_reading(rtype), rec),
                               "record": _bare(rec)}), "refs": [ref]}

    def _describe(self, person, agent, offer, cid, a, cause) -> dict:
        rtype = a["type"]
        offer.get("search", rtype, f"read {rtype} records")
        rt = self.hub.record_type(rtype)
        hidden = {f["path"] for m in (person, agent) for f in visibility.hidden_fields(self.hub, m, rt)}
        fields = [{k: f[k] for k in ("name", "type", "label", "required", "help", "choices") if f.get(k)}
                  for f in rt.fields if f["path"] not in hidden and mcp.SIMPLE.fullmatch(f["path"])]

        def writes(what):
            got = offer.calls.get((what, rtype))
            return sorted(set(got[1]["inputSchema"]["properties"]) & set(got[2]["inputSchema"]["properties"])
                          - {"id", "version", "comment", "to"}) if got else None
        move = offer.calls.get(("move", rtype))
        out = {"type": rtype, "label": rt.label, "summary": rt.summary, "states": rt.states(), "fields": fields,
               "you_may": [w for w in ("search", "read", "create", "update", "move") if (w, rtype) in offer.calls],
               **({"create_fields": writes("create")} if writes("create") is not None else {}),
               **({"update_fields": writes("update")} if writes("update") is not None else {}),
               **({"moves": move[1]["description"]} if move else {})}
        return {"body": _json(out)}

    def _create(self, person, agent, offer, cid, a, cause) -> dict:
        got = offer.get("create", a["type"], f"make {a['type']} records")
        fields = self._fields(person, agent, got, a["fields"])
        seq, out = self._run(agent, offer, got[0], got[1], fields, cause)
        self._used(cid, out["ref"], "created", seq)
        return {"body": _json(self._summary(person, agent, out["ref"])), "refs": [out["ref"]]}

    def _change(self, person, agent, offer, cid, a, cause, what: str) -> dict:
        ref = self.hub.resolve_ref(a["ref"])
        if not ref:
            raise NotFound(f"there is no {a['ref']}")
        rtype, rid = ref.split("/", 1)
        self._reach(person, agent, ref)
        got = offer.get(what, rtype, f"{what} {rtype} records")
        q = {"id": rid, "version": a["version"],
             **self._fields(person, agent, got, a.get("fields") or {}, ("id", "version", "comment", "to"))}
        if what == "move":
            both = set(got[1]["inputSchema"]["properties"]["to"]["enum"]) & set(
                got[2]["inputSchema"]["properties"]["to"]["enum"])
            if a["to"] not in both:
                raise Denied(f"moving {ref} to {a['to']!r} is not a move you and {person['name']} may both make")
            q["to"] = a["to"]
        if a.get("comment"):
            q["comment"] = a["comment"]
        seq, out = self._run(agent, offer, got[0], got[1], q, cause)
        self._used(cid, ref, "moved" if what == "move" else "updated", seq)
        return {"body": _json(self._summary(person, agent, ref)), "refs": [ref]}

    def _update(self, person, agent, offer, cid, a, cause) -> dict:
        return self._change(person, agent, offer, cid, a, cause, "update")

    def _move(self, person, agent, offer, cid, a, cause) -> dict:
        return self._change(person, agent, offer, cid, a, cause, "move")

    def _start(self, person, agent, offer, cid, a, cause) -> dict:
        got = offer.get("start", a["workflow"], f"start {a['workflow']}")
        q = {"input": a.get("input") or {}}
        if a.get("record"):
            ref = self.hub.resolve_ref(a["record"])
            if not ref:
                raise NotFound(f"there is no {a['record']}")
            self._reach(person, agent, ref)
            q["record"] = ref
        self._refs_reached(person, agent, q["input"])
        seq, out = self._run(agent, offer, got[0], got[1], q, cause)
        run = self.hub.engine.run(out["run"]["id"])
        ref = run.get("record") or q.get("record")
        if ref:
            self._used(cid, ref, "started", seq)
        return {"body": _json({"run": out["run"]["id"], "status": out["run"]["status"], "record": ref}),
                "refs": [ref] if ref else []}


def _obj(props: dict, required=()) -> dict:
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


def _bare(rec: dict) -> dict:
    """A record as the model is given it: its fields, its version and key, and what is withheld from it."""
    meta = rec.get("meta") or {}
    return {**{k: v for k, v in rec.items() if k != "meta"},
            "meta": {k: meta[k] for k in ("versionId", "key", "withheld") if meta.get(k)}}


def _json(v) -> str:
    from . import decimals
    text = json.dumps(v, default=decimals.jsonable, ensure_ascii=False)
    return text if len(text) <= RESULT_CHARS else text[:RESULT_CHARS - 40] + " ... (cut: ask for less)"


def chat_of(hub, gateway=None) -> Chat:
    c = getattr(hub, "_chat", None)
    if c is None:
        c = hub._chat = Chat(hub, gateway)
    elif gateway is not None:
        c._gateway = gateway
    return c
