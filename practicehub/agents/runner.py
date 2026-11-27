"""The agent worker: one agent member doing its inbox, with its own credentials.

An agent is a member like any other. It sees only its own inbox, claims an item, reads the item's context (only the
data its roles may read), asks its model, and submits the answer through the same calls a person's form uses, so
the same validation, guards and audit apply. A refused answer is asked again once, with why and the answer itself
(to change only what was refused). When the model is unsure, or its answer is refused twice, the item is escalated
to people. The runner never decides what an agent may do; the hub does, on every call.

A notice that it was mentioned is answered in the thread it was mentioned in (:meth:`AgentRunner.answer_mention`):
from what it was given there, and when it cannot answer, it says so there, for a person to take up. Other notices
are for people.

The kill switch is the member's status: disable the agent (or its sponsor) and its next call is refused; the runner
notices and stops.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from ..models import ModelError, ModelGateway
from .. import decimals


class Refused(Exception):
    """The hub said no (403) or the agent is not active."""


class Rejected(Exception):
    """The hub did not accept the answer (422); ``problems`` says why."""

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = problems or []


# ----------------------------------------------------------------------------------------------------- clients
class LocalClient:
    """Calls the hub in-process, as the given member (tests, single-process deployments)."""

    def __init__(self, hub, member_id: str):
        self.hub, self.mid = hub, member_id

    def _me(self):
        return self.hub.member(self.mid)

    def _call(self, fn, *args, **kw):
        from ..hub import Denied, Invalid
        try:
            return fn(*args, **kw)
        except Denied as exc:
            raise Refused(str(exc)) from None
        except Invalid as exc:
            raise Rejected(str(exc), getattr(exc, "problems", None)) from None

    def me(self):
        m = self._me()
        return {**m, "practice": self.hub.practice.get("name", "the practice")}

    def inbox(self):
        return self._call(self.hub.engine.inbox, self._me())

    def claim(self, item_id):
        return self._call(self.hub.engine.claim, self._me(), item_id)

    def context(self, item_id):
        return self._call(self.hub.engine.context, self._me(), item_id)

    def complete(self, item_id, output):
        return self._call(self.hub.engine.complete, self._me(), item_id, output)

    def decide(self, item_id, outcome, comment):
        return self._call(self.hub.engine.decide, self._me(), item_id, outcome, comment)

    def release(self, item_id, note, escalate):
        return self._call(self.hub.engine.release, self._me(), item_id, note, escalate=escalate)

    def acknowledge(self, item_id):
        return self._call(self.hub.engine.acknowledge, self._me(), item_id)

    def file(self, ref, blob):
        """A file a record names, as this agent may read it (on the track, like opening the record)."""
        from ..hub import NotFound
        try:
            return self._call(self.hub.read_file, self._me(), ref, blob)[0]
        except NotFound as exc:
            raise Refused(str(exc)) from None

    def reply_mention(self, mid, body):
        from ..hub import NotFound
        from ..mentions import mentions_of
        try:
            return self._call(mentions_of(self.hub).reply, self._me(), mid, body)
        except NotFound as exc:
            raise Refused(str(exc)) from None


class HttpClient:
    """Calls the hub's JSON API with the agent's bearer token."""

    def __init__(self, base_url: str, token: str, timeout: float = 60):
        self.base, self.token, self.timeout = base_url.rstrip("/"), token, timeout

    def _req(self, method, path, body=None, *, raw: bool = False):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body, default=decimals.jsonable).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = resp.read()
                return data if raw else json.loads(data or b"null")
        except urllib.error.HTTPError as exc:
            try:
                err = json.loads(exc.read())
            except ValueError:
                err = {}
            msg = err.get("error") or f"HTTP {exc.code}"
            if exc.code in (401, 403):
                raise Refused(msg) from None
            if exc.code == 422:
                raise Rejected(msg, err.get("problems")) from None
            raise ModelError(f"{method} {path}: {msg}") from None

    def me(self):
        return self._req("GET", "/api/me")

    def inbox(self):
        return self._req("GET", "/api/inbox")["items"]

    def claim(self, item_id):
        return self._req("POST", f"/api/items/{item_id}/claim", {})

    def context(self, item_id):
        return self._req("GET", f"/api/items/{item_id}/context")

    def complete(self, item_id, output):
        return self._req("POST", f"/api/items/{item_id}/complete", {"output": output})

    def decide(self, item_id, outcome, comment):
        return self._req("POST", f"/api/items/{item_id}/decide", {"outcome": outcome, "comment": comment})

    def release(self, item_id, note, escalate):
        return self._req("POST", f"/api/items/{item_id}/release", {"note": note, "escalate": escalate})

    def acknowledge(self, item_id):
        return self._req("POST", f"/api/items/{item_id}/ack", {})

    def file(self, ref, blob):
        return self._req("GET", f"/api/records/{ref}/files/{blob}", raw=True)

    def reply_mention(self, mid, body):
        return self._req("POST", f"/api/mentions/{mid}/reply", {"body": body})


# ------------------------------------------------------------------------------------------------------ runner
class AgentRunner:
    def __init__(self, client, gateway: ModelGateway, log=print):
        self.client, self.gateway, self.log = client, gateway, log

    def work_once(self) -> int:
        """Do every item in the inbox once. Returns how many items were handled."""
        me = self.client.me()
        if me.get("status") != "active":
            raise Refused(f"{me.get('name')} is not active")
        if me.get("kind") != "agent":
            raise Refused(f"{me.get('name')} is not an agent")
        handled = 0
        for item in self.client.inbox():
            try:
                if item["kind"] == "notice":
                    if not (item.get("output") or {}).get("mention"):
                        continue  # other notices are for people
                    self.answer_mention(me, item)
                else:
                    self.handle(me, item)
                handled += 1
            except Rejected as exc:  # e.g. another member took it first
                self.log(f"[{me['id']}] skipped {item['id']}: {exc}")
        return handled

    def answer_mention(self, me: dict, item: dict) -> str:
        """Answer in the thread it was mentioned in, from what it was given there. A reply the hub does not take is
        tried once more with why; when the model escalates, fails or is refused twice, the agent says in the thread
        that it cannot answer and why, so a person sees it there. Either way the notice is done."""
        iid = item["id"]
        try:
            ctx = self.client.context(iid)["context"]["mention"]
        except Refused as exc:  # no longer its to read (a grant taken away, a directive made since)
            self.log(f"[{me['id']}] cannot read where it was mentioned ({exc}); notice put aside")
            try:
                self.client.acknowledge(iid)
            except (Refused, Rejected) as exc2:
                self.log(f"[{me['id']}] could not put the notice aside: {exc2}")
            return "unreadable"
        mid, feedback, why = ctx["mention"]["id"], [], "I am not sure"
        for attempt in (1, 2):
            try:
                out = self.gateway.reply(me, ctx, me.get("practice", "the practice"), feedback=feedback)
            except ModelError as exc:
                self.log(f"[{me['id']}] model failed on mention {mid}: {exc}")
                why = "my model did not answer"
                break
            body = "" if out["escalate"] else str((out.get("answer") or {}).get("body") or "").strip()
            if not body:
                why = out.get("reason") or why
                break
            try:
                self.client.reply_mention(mid, body)
                self.log(f"[{me['id']}] answered mention {mid}. {out.get('reason', '')}")
                return "answered"
            except Rejected as exc:
                feedback = exc.problems or [str(exc)]
                why = "my answer was not accepted: " + "; ".join(feedback)
                self.log(f"[{me['id']}] reply refused (attempt {attempt}): {'; '.join(feedback)}")
        why = why.replace("@", "").strip().rstrip(".")  # says why, naming nobody: never refused for a name in it
        self.client.reply_mention(mid, f"I can't answer this: {why}. A person should look.")
        self.log(f"[{me['id']}] could not answer mention {mid}: {why}")
        return "declined"

    def _pictures(self, me: dict, ctx: dict) -> list[dict]:
        """The pictures the step's context names (``file:<field>`` entries that are pictures). For an agent whose
        profile names a model that can see, each is fetched as the agent (``data``); one it may not read, or any for
        another agent, goes without, and the model is told there is a picture it is not shown."""
        found = [v for k, v in (ctx.get("context") or {}).items()
                 if k.startswith("file:") and isinstance(v, dict) and v.get("picture")]
        sees = bool((me.get("profile") or {}).get("vision_model"))
        out = []
        for v in found:
            p = {"name": v.get("name"), "contentType": v["picture"], "size": v.get("size")}
            if sees:
                try:
                    p["data"] = self.client.file(v["ref"], v["blob"])
                except (Refused, Rejected, ModelError) as exc:
                    self.log(f"[{me['id']}] could not read {v.get('name')}: {exc}")
            out.append(p)
        return out

    def handle(self, me: dict, item: dict) -> str:
        iid = item["id"]
        self.client.claim(iid)
        ctx = self.client.context(iid)
        pictures = self._pictures(me, ctx)
        feedback = ans = None
        for attempt in (1, 2):
            if feedback:  # the answer itself goes back too: told only what was wrong, a model starts over and loses what it had right
                was = json.dumps(ans, default=str, ensure_ascii=False)[:8000]
                ctx = {**ctx, "step": {**ctx["step"], "instructions": (ctx["step"].get("instructions") or "")
                                       + "\n\nYour previous answer was not accepted: " + "; ".join(feedback)
                                       + f"\nIt was: {was}\nKeep what it had right; change only what was refused."}}
            try:
                out = self.gateway.answer(me, ctx, me.get("practice", "the practice"), pictures=pictures)
            except ModelError as exc:
                return self._escalate(me, iid, f"model failed: {exc}")
            if out["escalate"]:
                return self._escalate(me, iid, out["reason"] or "the agent is not sure")
            ans = out["answer"]
            try:
                if ctx["step"]["kind"] == "gate":
                    self.client.decide(iid, ans.get("outcome", ""), ans.get("comment", ""))
                else:
                    self.client.complete(iid, ans)
                self.log(f"[{me['id']}] {ctx['step']['title']} ({ctx['run']['title']}): done. {out['reason']}")
                return "done"
            except Rejected as exc:
                feedback = exc.problems or [str(exc)]
                self.log(f"[{me['id']}] answer refused (attempt {attempt}): {'; '.join(feedback)}")
        return self._escalate(me, iid, "answer refused twice: " + "; ".join(feedback or []))

    def _escalate(self, me, iid, why):
        self.client.release(iid, why, True)
        self.log(f"[{me['id']}] escalated {iid} to people: {why}")
        return "escalated"

    def run_forever(self, interval: float = 5.0) -> None:
        while True:
            try:
                self.work_once()
            except Refused as exc:
                self.log(f"stopping: {exc}")
                return
            time.sleep(interval)


def run_all_local(hub, gateway: ModelGateway | None = None, *, rounds: int = 5, log=print) -> int:
    """Let every active agent work its inbox until nothing is left (demos and tests)."""
    gateway = gateway or hub.models
    total = 0
    for _ in range(rounds):
        n = 0
        for m in hub.members("agent"):
            if m["status"] != "active":
                continue
            try:
                n += AgentRunner(LocalClient(hub, m["id"]), gateway, log).work_once()
            except Refused:
                continue
        total += n
        if not n:
            break
    return total
