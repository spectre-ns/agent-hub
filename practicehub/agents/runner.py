"""The agent worker: one agent member doing its inbox, with its own credentials.

An agent is a member like any other. It sees only its own inbox, claims an item, reads the item's context (only the
data its roles may read), asks its model, and submits the answer through the same calls a person's form uses, so
the same validation, guards and audit apply. When the model is unsure, or its answer is refused twice, the item is
escalated to people. The runner never decides what an agent may do; the hub does, on every call.

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


class HttpClient:
    """Calls the hub's JSON API with the agent's bearer token."""

    def __init__(self, base_url: str, token: str, timeout: float = 60):
        self.base, self.token, self.timeout = base_url.rstrip("/"), token, timeout

    def _req(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body, default=decimals.jsonable).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"null")
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
            if item["kind"] == "notice":
                continue  # notices are for people
            try:
                self.handle(me, item)
                handled += 1
            except Rejected as exc:  # e.g. another member took it first
                self.log(f"[{me['id']}] skipped {item['id']}: {exc}")
        return handled

    def handle(self, me: dict, item: dict) -> str:
        iid = item["id"]
        self.client.claim(iid)
        ctx = self.client.context(iid)
        feedback = None
        for attempt in (1, 2):
            if feedback:
                ctx = {**ctx, "step": {**ctx["step"], "instructions": (ctx["step"].get("instructions") or "")
                                       + "\n\nYour previous answer was not accepted: " + "; ".join(feedback)}}
            try:
                out = self.gateway.answer(me, ctx, me.get("practice", "the practice"))
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
