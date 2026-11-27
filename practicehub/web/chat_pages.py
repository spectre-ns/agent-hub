"""Talking to an agent, on every page a person works in: the button bottom right, and the panel it opens.

A conversation is between one person and one agent, and is not tied to any one record (``practicehub/chat.py`` keeps
it, and holds its rules). The panel asks the hub for the agents the person may talk with, their past conversations,
and one conversation's messages; the agent's answer comes while the page waits for it (``GET`` with ``since``, held
open until there is something new, like the live editor's poll). What each tool call did shows as one quiet line, and
the records the conversation used are listed with links, the person seeing each as they may.
"""

from __future__ import annotations

import html
import threading

from ..hub import Denied, Invalid
from .. import visibility

e = html.escape
C = r"(?P<cid>ch-[0-9a-f]{12})"


class ChatPages:
    def chat_routes(self, R) -> None:
        R("GET", r"/api/chats", self.api_chats)
        R("POST", r"/api/chats", self.api_chat_start)
        R("GET", rf"/api/chats/{C}", self.api_chat)
        R("POST", rf"/api/chats/{C}/messages", self.api_chat_say)

    @property
    def chat(self):
        from ..chat import chat_of
        return chat_of(self.hub)

    @staticmethod
    def _chatter(req) -> dict:
        me = req["me"]
        if me["kind"] != "person":
            raise Denied("only a person talks with an agent")
        return me

    # ------------------------------------------------------------------------------------------------ routes
    def api_chats(self, req):
        me = self._chatter(req)
        names = {a["id"]: a["name"] for a in self.hub.members("agent")}
        return {"agents": self.chat.agents(me),
                "chats": [{**c, "agent_name": names.get(c["agent"], c["agent"])} for c in self.chat.list(me)]}

    def api_chat_start(self, req):
        me = self._chatter(req)
        agent = self.body_json(req).get("agent")
        if not isinstance(agent, str) or not agent:
            raise Invalid("choose the agent to talk with")
        return {"chat": self._chat_out(me, self.chat.start(me, agent))}

    def api_chat(self, req, cid):
        """The conversation and its messages after ``since``; with ``wait``, held until there is a message after it,
        or the agent starts or stops answering (``busy`` is what the page last saw), or, with ``tick`` (the tick of
        the thinking the page shows, 0 for none), its model has thought more, for at most 25 seconds."""
        me = self._chatter(req)
        q = req["query"]
        try:
            since, wait = int(q.get("since", 0)), float(q.get("wait", 0))
            tick = int(q["tick"]) if q.get("tick") not in (None, "") else None
        except ValueError:
            raise Invalid("since, wait and tick are numbers") from None
        c = (self.chat.wait(me, cid, since, q.get("busy") == "1", wait, tick) if wait > 0
             else self.chat.get(me, cid))
        return {"chat": self._chat_out(me, c),
                "messages": [self._chat_msg(m) for m in self.chat.messages(me, cid, since)]}

    def api_chat_say(self, req, cid):
        """The person's message, stored; the agent answers on a thread of its own, and the page waits for it."""
        me = self._chatter(req)
        msg, seq = self.chat.ask(me, cid, self.body_json(req).get("body"))
        threading.Thread(target=self.chat.answer, args=(me["id"], cid, seq), name=f"chat-{cid}",
                         daemon=True).start()
        return {"message": self._chat_msg(msg)}

    # ------------------------------------------------------------------------------------------------ shown
    def _chat_out(self, me: dict, c: dict) -> dict:
        """A conversation for its person: each record it used named as they may see it (a record they may no longer
        read, or one withheld from them, is only its reference)."""
        try:
            agent = self.hub.member(c["agent"])
            who = {"agent_name": agent["name"], "agent_title": agent.get("title") or ""}
        except Exception:  # noqa: BLE001 - an agent removed since: the conversation is still theirs to read
            who = {"agent_name": c["agent"], "agent_title": ""}
        used, seen = [], {}
        for r in c.get("resources") or []:
            ref = r["ref"]
            if ref not in seen:
                seen[ref] = self._chat_ref(me, ref)
            used.append({**r, **seen[ref]})
        return {**c, **who, "resources": used}

    def _chat_ref(self, me: dict, ref: str) -> dict:
        rtype, _, rid = ref.partition("/")
        rt = self.hub.record_type_for_reading(rtype)
        if not rt or not self.hub.may_read(me, rtype) or self.hub.restrictions.withholds_from(me, ref, reaching=True):
            return {"label": ref}
        rec = self.hub.records.read(rtype, rid)
        if not rec:
            return {"label": ref}
        rec = self.hub.redact(me, rec)
        key = (rec.get("meta") or {}).get("key")
        title = "" if visibility.withheld(rec) else self.hub.title(rec)
        label = " · ".join(x for x in (key, title) if x) or ref
        return {"label": label, "type": rt.label, "href": self.record_href(ref)}

    def _chat_msg(self, m: dict) -> dict:
        out = {k: v for k, v in m.items() if v not in (None, [], False)}
        if m["role"] == "tool":
            out["line"] = self._chat_line(m)
            out["links"] = [{"ref": r, "href": self.record_href(r)} for r in m.get("refs") or []]
        return out

    def _chat_line(self, m: dict) -> str:
        """What a tool call did, in a few words."""
        a = m.get("args") if isinstance(m.get("args"), dict) else {}
        s = lambda k: str(a.get(k) or "").strip()
        plural = lambda t: ((self.hub.record_type(t) and self.hub.record_type(t).plural) or t or "records").lower()
        one = lambda t: ((self.hub.record_type(t) and self.hub.record_type(t).label) or t or "record").lower()
        line = {"find": lambda: f"Looked up {s('key').upper()}",
                "search": lambda: (f"Searched {plural(s('type'))}" + (f" about {s('about')}" if s("about") else "")
                                   + (f" for “{s('text')}”" if s("text") else "")),
                "read": lambda: f"Read {s('ref')}",
                "describe": lambda: f"Looked at how {plural(s('type'))} are kept",
                "create": lambda: f"Created a {one(s('type'))}",
                "update": lambda: f"Changed {s('ref')}",
                "move": lambda: f"Moved {s('ref')} to {s('to')}",
                "start": lambda: f"Started {s('workflow')}" + (f" on {s('record')}" if s("record") else "")
                }.get(m.get("tool") or "")
        text = line() if line else f"Tried {m.get('tool') or 'a tool'}"
        return text + (": refused" if m.get("error") else "")

    def chat_dock(self, me: dict | None) -> str:
        """The button bottom right and its panel, on a person's pages only (not an agent's or a service's session,
        and not signed out)."""
        if not me or me.get("kind") != "person":
            return ""
        return ('<div class="chat-dock" data-chat-dock>'
                '<button type="button" class="chat-fab" aria-expanded="false" aria-controls="chat-panel" '
                'aria-keyshortcuts="Alt+K" accesskey="k" title="Talk to an agent">'
                '<svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" '
                'stroke-width="2" stroke-linejoin="round"><path d="M4 5h16v11H9l-5 4z"/><path d="M8 9h8M8 12h5"/>'
                '</svg><span class="visually-hidden">Talk to an agent</span></button>'
                '<section id="chat-panel" class="chat-panel" role="dialog" aria-modal="false" '
                'aria-labelledby="chat-head" hidden>'
                '<header class="chat-top"><button type="button" class="btn subtle chat-back" hidden>'
                '<span aria-hidden="true">&larr;</span> Conversations</button>'
                '<h2 id="chat-head">Talk to an agent</h2><span class="spacer"></span>'
                '<button type="button" class="btn subtle chat-close" aria-label="Close">&times;</button></header>'
                '<div class="chat-body" aria-live="polite"></div>'
                '<form class="chat-say" hidden><label class="visually-hidden" for="chat-text">Your message</label>'
                '<textarea id="chat-text" name="body" rows="2" maxlength="4000" '
                'placeholder="Ask, or tell it what to do"></textarea>'
                '<button type="submit" class="btn primary">Send</button></form>'
                '</section></div>')
