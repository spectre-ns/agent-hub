"""``practicehub mcp``: one of the hub's MCP servers on stdio. Each line from the client is POSTed as it is; each reply
(JSON, or the messages of a short event stream) goes back as a line. The hub does all the work and all the checks;
the bridge keeps only the session id, and pings now and then so a tool list change reaches the client."""

from __future__ import annotations

import itertools
import json
import threading
import urllib.error
import urllib.request
from . import decimals

ACCEPT = "application/json, text/event-stream"


class Bridge:
    def __init__(self, url: str, token: str, out, *, timeout: float = 120):
        self.url, self.token, self.out, self.timeout = url, token, out, timeout
        self.session: str | None = None
        self.version: str | None = None
        self.lock = threading.Lock()
        self.ids = itertools.count(1)

    def write(self, msg: dict) -> None:
        with self.lock:
            self.out.write(json.dumps(msg, default=decimals.jsonable) + "\n")
            self.out.flush()

    def post(self, msg) -> list[dict]:
        """Send one message; the messages that came back (none for a notification)."""
        h = {"Content-Type": "application/json", "Accept": ACCEPT, "Authorization": f"Bearer {self.token}"}
        if self.session:
            h["Mcp-Session-Id"] = self.session
        if self.version:
            h["MCP-Protocol-Version"] = self.version
        req = urllib.request.Request(self.url, data=json.dumps(msg, default=decimals.jsonable).encode(), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310 - the hub the user named
                if r.headers.get("Mcp-Session-Id"):
                    self.session = r.headers["Mcp-Session-Id"]
                body = r.read().decode("utf-8", "replace")
                ctype = r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            try:
                got = json.loads(text)
            except ValueError:
                got = None
            if isinstance(got, dict) and "error" in got and isinstance(msg, dict) and "id" in msg:
                return [{**got, "id": msg["id"]}]
            raise BridgeError(f"HTTP {exc.code}") from None
        if not body.strip():
            return []
        if ctype.startswith("text/event-stream"):
            out = []
            for block in body.split("\n\n"):
                data = "\n".join(ln[5:].lstrip() for ln in block.splitlines() if ln.startswith("data:"))
                if data:
                    out.append(json.loads(data))
            return out
        return [json.loads(body)]

    def relay(self, line: str) -> None:
        try:
            msg = json.loads(line)
        except ValueError:
            self.write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            return
        try:
            replies = self.post(msg)
        except (BridgeError, OSError) as exc:
            if isinstance(msg, dict) and "id" in msg:
                self.write({"jsonrpc": "2.0", "id": msg["id"],
                            "error": {"code": -32603, "message": f"the hub did not answer: {exc}"}})
            return
        for r in replies:
            if isinstance(msg, dict) and msg.get("method") == "initialize" and "result" in r:
                self.version = r["result"].get("protocolVersion")
            self.write(r)

    def ping(self) -> None:
        """A ping of the bridge's own: only the notifications that come with it go to the client."""
        if not self.session:
            return
        try:
            for r in self.post({"jsonrpc": "2.0", "id": f"bridge-{next(self.ids)}", "method": "ping"}):
                if "method" in r and "id" not in r:
                    self.write(r)
        except (BridgeError, OSError, ValueError):
            pass


class BridgeError(Exception):
    pass


def run(url: str, token: str, inp, out, *, ping: float = 30.0) -> int:
    b = Bridge(url, token, out)
    stop = threading.Event()
    if ping > 0:
        def beat():
            while not stop.wait(ping):
                b.ping()
        threading.Thread(target=beat, daemon=True).start()
    try:
        for line in inp:
            if line.strip():
                b.relay(line)
    finally:
        stop.set()
    return 0
