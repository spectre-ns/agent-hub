"""MCP over Streamable HTTP (``POST /mcp/<server>``) and the Connections (MCP) settings page.

The protocol and the tools are ``practicehub.mcp``; this is the transport. In order: the ``Origin`` is checked (a page
on another site may not drive a member's agent: 403), the caller shows a member's token (``Authorization: Bearer
ph_...``, never a cookie: 401), and the server must exist and be switched on (404). Replies are JSON, or a short
event stream when a ``tools/list_changed`` notification goes first and the client accepts one.
"""

from __future__ import annotations

import html
import json
import os
import urllib.parse

from .. import mcp
from ..hub import Denied

e = html.escape
JSON = {"Content-Type": "application/json"}
MAX_BODY = 4 * 1024 * 1024


def _rpc(code: int, message: str, mid=None) -> bytes:
    return json.dumps(mcp._err(mid, code, message)).encode()


class McpPages:
    def mcp_routes(self, R):
        self.mcp_sessions = mcp.Sessions()
        R("GET", r"/settings/connections", self.page_connections)
        R("POST", r"/settings/connections/(?P<sid>[a-z][a-z0-9-]*)/(?P<verb>enable|disable)", self.post_connection)

    # ------------------------------------------------------------------------------------------ transport
    def _origin_ok(self, headers: dict) -> bool:
        origin = headers.get("origin")
        if not origin:
            return True  # not a browser page: a client program (it still needs a token)
        allowed = {o.strip().rstrip("/") for o in os.environ.get("PRACTICEHUB_MCP_ORIGINS", "").split(",") if o.strip()}
        if origin.rstrip("/") in allowed:
            return True
        host = urllib.parse.urlsplit(origin).netloc
        return bool(host) and host == headers.get("host", "")

    def handle_mcp(self, method: str, raw_path: str, headers: dict, body: bytes):
        path = urllib.parse.urlsplit(raw_path).path.rstrip("/")
        sid = path[len("/mcp/"):]
        if not self._origin_ok(headers):
            return 403, JSON, _rpc(mcp.INVALID_REQUEST, "this origin may not call the hub's MCP servers")
        auth = headers.get("authorization", "")
        member = self.hub.authenticate(auth[7:].strip()) if auth.lower().startswith("bearer ") else None
        if not member:
            return 401, {**JSON, "WWW-Authenticate": 'Bearer realm="practicehub"'}, _rpc(
                mcp.INVALID_REQUEST, "a member's token is needed (Authorization: Bearer ph_...)")
        if "/" in sid or not mcp.server(self.hub, sid):
            return 404, JSON, _rpc(mcp.INVALID_REQUEST, "there is no such MCP server here, or it is switched off")
        if method != "POST":
            return 405, {**JSON, "Allow": "POST"}, _rpc(mcp.INVALID_REQUEST, "send JSON-RPC messages with POST")
        pv = headers.get("mcp-protocol-version")
        if pv and pv not in mcp.VERSIONS:
            return 400, JSON, _rpc(mcp.INVALID_REQUEST, f"unsupported MCP-Protocol-Version {pv[:40]}")
        if len(body or b"") > MAX_BODY:
            return 413, JSON, _rpc(mcp.INVALID_REQUEST, "the message is too large")
        try:
            msg = json.loads(body or b"")
        except ValueError:
            return 400, JSON, _rpc(mcp.PARSE_ERROR, "Parse error")
        if isinstance(msg, list):
            return 400, JSON, _rpc(mcp.INVALID_REQUEST, "send one message per request (no batches)")
        session = headers.get("mcp-session-id")
        if session:
            row = self.mcp_sessions.get(session)
            if not row or row["member"] != member["id"] or row["server"] != sid:
                return 404, JSON, _rpc(mcp.INVALID_REQUEST, "this session is not known (initialize again)")
        resp, notes, new = mcp.handle(self.hub, sid, member, msg, session=session, sessions=self.mcp_sessions)
        h = dict(JSON)
        if new:
            h["Mcp-Session-Id"] = new
        if resp is None:
            return 202, h, b""
        if notes and "text/event-stream" in headers.get("accept", ""):
            h["Content-Type"] = "text/event-stream"
            h["Cache-Control"] = "no-cache"
            stream = "".join(f"event: message\ndata: {json.dumps(m)}\n\n" for m in [*notes, resp])
            return 200, h, stream.encode()
        return 200, h, json.dumps(resp, default=str).encode()

    # ------------------------------------------------------------------------------------------ settings
    def _base_url(self, req) -> str:
        host = req["headers"].get("host") or "localhost"
        scheme = req["headers"].get("x-forwarded-proto") or ("http" if host.startswith(("127.", "localhost")) else "https")
        return f"{scheme}://{host}"

    def page_connections(self, req):
        me, hub = req["me"], self.hub
        owner = me["kind"] == "person" and hub.is_policy_owner(me)
        base = self._base_url(req)
        rows = []
        first = None
        for s in mcp.servers(hub):
            url = f"{base}/mcp/{s['id']}"
            first = first or (s if s["id"] != mcp.CORE else None)
            n = len(mcp.tools(hub, s["id"], me))
            state = ('<span class="loz success">on</span>' if s["enabled"] else '<span class="loz removed">off</span>')
            act = ""
            if owner:
                verb = "disable" if s["enabled"] else "enable"
                act = (f'<form method="post" action="/settings/connections/{e(s["id"])}/{verb}">'
                       f'<button class="btn{" subtle" if s["enabled"] else ""}" type="submit">'
                       f'{"Switch off" if s["enabled"] else "Switch on"}</button></form>')
            rows.append(f'<tr><td><b>{e(s["name"])}</b><br><span class="muted">{e(s["summary"])}</span></td>'
                        f'<td><code style="white-space:nowrap;word-break:normal">{e(url)}</code></td><td>{n}</td><td>{state}</td><td>{act}</td></tr>')
        sample = first or {"id": mcp.CORE}
        http_cfg = {"mcpServers": {f"practicehub-{sample['id']}": {
            "type": "http", "url": f"{base}/mcp/{sample['id']}", "headers": {"Authorization": "Bearer ph_YOUR_TOKEN"}}}}
        stdio_cfg = {"mcpServers": {f"practicehub-{sample['id']}": {
            "command": "practicehub", "args": ["mcp", sample["id"], "--url", base],
            "env": {"PRACTICEHUB_TOKEN": "ph_YOUR_TOKEN"}}}}
        note = ("" if owner else "<p class='muted'>A policy owner switches servers on and off.</p>")
        body = ("<p class='muted'>Each installed module, the practice's own definitions and your work are offered as "
                "Model Context Protocol servers. Their tools are made from the record types, lifecycles, workflows "
                "and views in force, and only change when a definition change is approved. Each caller sees only what "
                "its member may do; every call is on the track. Agents never sign or approve.</p>" + note +
                "<div class='tablewrap'><table><thead><tr><th scope='col'>Server</th><th scope='col'>URL</th>"
                "<th scope='col' class='nowrap'>Tools (yours)</th><th scope='col'>State</th><th scope='col'><span "
                "class='visually-hidden'>Action</span></th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
                "</div><div class='section'><h2>How to connect</h2><p>Give the client a member's token (an agent's "
                "token is issued by its sponsor on the member's page). Over HTTP:</p>"
                f"<pre>{e(json.dumps(http_cfg, indent=2))}</pre>"
                "<p>Or through the command line bridge, for clients that speak stdio:</p>"
                f"<pre>{e(json.dumps(stdio_cfg, indent=2))}</pre></div>")
        return self.layout(req, "Connections (MCP)", body, crumbs=[("/settings", "Settings"), (None, "Connections")])

    def post_connection(self, req, sid, verb):
        me = req["me"]
        if not (me["kind"] == "person" and self.hub.is_policy_owner(me)):
            raise Denied("a policy owner switches MCP servers on and off")
        mcp.set_enabled(self.hub, me, sid, verb == "enable")
        from .server import Redirect
        raise Redirect("/settings/connections")
