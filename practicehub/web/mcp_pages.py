"""MCP over Streamable HTTP (``POST /mcp/<server>``) and the Connections (MCP) settings page.

The protocol and the tools are ``practicehub.mcp``; this is the transport. In order: the ``Origin`` is checked (a page
on another site may not drive a member's agent: 403), the caller shows a member's MCP token (``Authorization: Bearer
ph_...``, never a cookie: 401), the server must exist and be switched on (404), and the token must be for MCP and this
server, its member one a policy owner let connect, and its client one that runs where the practice keeps its data or
was attested (403, on the track as ``mcp.refused``). Replies are JSON, or a short event stream when a
``tools/list_changed`` notification goes first and the client accepts one.
"""

from __future__ import annotations

import html
import json
import os
import urllib.parse

from .. import mcp
from ..hub import Denied, Invalid, NotFound

e = html.escape
JSON = {"Content-Type": "application/json"}
MAX_BODY = 4 * 1024 * 1024


def _rpc(code: int, message: str, mid=None) -> bytes:
    return json.dumps(mcp._err(mid, code, message)).encode()


class McpPages:
    def mcp_routes(self, R):
        self.mcp_sessions = mcp.Sessions()
        R("GET", r"/settings/connections", self.page_connections)
        R("POST", r"/settings/connections/token", self.post_connection_token)
        R("POST", r"/settings/connections/(?P<sid>[a-z][a-z0-9-]*)/(?P<verb>ask|approve|withdraw|off)",
          self.post_connection)

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
        got = self.hub.credential(auth[7:].strip()) if auth.lower().startswith("bearer ") else None
        if not got:
            return 401, {**JSON, "WWW-Authenticate": 'Bearer realm="practicehub"'}, _rpc(
                mcp.INVALID_REQUEST, "a member's MCP token is needed (Authorization: Bearer ph_...)")
        member, cred = got
        if "/" in sid or not mcp.server(self.hub, sid):
            return 404, JSON, _rpc(mcp.INVALID_REQUEST, "there is no such MCP server here, or it is switched off")
        conn = {"client": cred.get("client") or {}, "credential": cred["token_hash"][:12]}
        refused = mcp.admit(self.hub, sid, member, cred)
        if refused:
            self.hub.track.append(member, "mcp.refused", f"mcp:{sid}",
                                  data={"how": "mcp", "server": sid, "stage": "connect", "reason": refused[1],
                                        **mcp._conn_data(conn)})
            return refused[0], JSON, _rpc(mcp.INVALID_REQUEST, refused[1])
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
        resp, notes, new = mcp.handle(self.hub, sid, member, msg, session=session, sessions=self.mcp_sessions,
                                      conn=conn)
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

    def _who_may(self, pol: dict | None, *, inline: bool = False) -> str:
        if not pol:
            return "<span class='muted'>nobody</span>"
        names = {r["id"]: r["name"] for r in self.hub.roles()}
        parts = [e(names.get(r, r)) for r in pol.get("roles", [])] + [self.who(m) for m in pol.get("members", [])]
        out = ", ".join(parts) or "<span class='muted'>nobody</span>"
        if pol.get("clients"):
            clients = ", ".join(f"{e(c['name'])} ({e(c['runs_in'])})" for c in pol["clients"])
            out += (f", with the attested clients {clients}" if inline
                    else f"<br><span class='muted'>attested clients: {clients}</span>")
        return out

    def _connectable(self, me) -> list[dict]:
        """Who the signed-in person issues MCP tokens for: themselves, and the agents and services they sponsor."""
        return [me] + [m for m in self.hub.members() if m["kind"] != "person" and m.get("sponsor") == me["id"]
                       and m["status"] == "active"]

    def page_connections(self, req, new_token: str | None = None, error: str | None = None):
        me, hub = req["me"], self.hub
        owner = me["kind"] == "person" and hub.is_policy_owner(me)
        base = self._base_url(req)
        home = mcp.residency(hub)
        rows, manage, waiting = [], [], []
        everyone = self._connectable(me)
        reachable = []
        for s in mcp.servers(hub):
            url = f"{base}/mcp/{s['id']}"
            req_ = mcp.request_of(hub, s["id"])
            if s.get("unavailable"):
                state = (f'<span class="loz removed">not running</span><br><span class="muted">'
                         f'{e(s["unavailable"])}</span>')
                n = "—"
            else:
                n = str(len(mcp.tools(hub, s["id"], me)))
                state = ('<span class="loz success">on</span>' if s["enabled"] else '<span class="loz">off</span>')
                if req_:
                    state += ' <span class="loz moved">waiting for approval</span>'
            if s["enabled"] and any(mcp.may_connect(s["policy"], m) for m in everyone):
                reachable.append(s)
            rows.append(f'<tr><td data-label="Server"><b>{e(s["name"])}</b><br><span class="muted">'
                        f'{e(s["summary"])}</span><br><code class="url">{e(url)}</code></td>'
                        f'<td data-label="Who may connect">{self._who_may(s["policy"])}</td>'
                        f'<td class="num" data-label="Tools (yours)">{n}</td><td data-label="State">{state}</td></tr>')
            if req_:
                btns = ""
                if owner and me["id"] not in {req_["by"], *req_.get("asked_by", [])}:
                    btns += (f'<form method="post" action="/settings/connections/{e(s["id"])}/approve" '
                             f'class="inline"><button data-confirm="Let them connect to {e(s["name"])}? You sign '
                             f'it.">Approve</button></form>')
                if owner:
                    btns += (f'<form method="post" action="/settings/connections/{e(s["id"])}/withdraw" '
                             f'class="inline"><button class="secondary">Withdraw</button></form>')
                waiting.append(f'<div class="card"><p><b>{e(s["name"])}: waiting for a second policy owner.</b> '
                               f'{self.member_name(req_["by"])} asked to let {self._who_may(req_, inline=True)} '
                               f'connect, because: {e(req_["reason"])}</p><div class="mcp-btns">{btns}</div></div>')
            if owner and not s.get("unavailable"):
                manage.append(self._manage_form(s, req_ or s["policy"] or {}, home))
        warn = (f'<div class="flash warning" role="note"><p><b>Data stays in {e(home or "the practice")}.</b> An MCP client '
                f'reads and changes whatever its member may, the most private records included, and many AI apps send what '
                f'they read to servers elsewhere. Every server starts switched off. A policy owner names who may '
                f'connect and which AI apps (clients) they may use, with where each sends what it reads; a second policy '
                f'owner approves. Every connection, call and record read is on the track.</p></div>')
        token = f'<div class="flash error" role="alert"><p>{e(error)}</p></div>' if error else ""
        if new_token:  # shown in this response only: never put in a URL, where history and logs keep it
            token = (f'<div class="flash success" role="status"><p>The new MCP token, shown once (only its hash is '
                     f'stored). Copy it now:</p><pre>{e(new_token)}</pre></div>')
        body = (warn + token + "".join(waiting) +
                "<p class='muted'>Each installed module, the practice's own definitions and your work are offered as "
                "Model Context Protocol servers. Their tools are made from the record types, lifecycles, workflows "
                "and views in force, and change only when a definition change is approved. Each caller sees only what "
                "its member may do. Agents never sign or approve.</p>"
                "<div class='tablewrap'><table class='connections'><thead><tr><th scope='col'>Server</th>"
                "<th scope='col'>Who may connect</th><th scope='col' class='nowrap'>Tools (yours)</th>"
                "<th scope='col'>State</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")
        body += self._token_section(everyone, reachable, home, base)
        if manage:
            body += ("<div class='section'><h2>Who may connect</h2><p class='muted'>Switching a server on, or "
                     "letting more roles, members or clients in, waits for a second policy owner. Narrowing it, or "
                     "switching it off, applies at once.</p>" + "".join(manage) + "</div>")
        elif not owner:
            body += "<p class='muted'>A policy owner decides who may connect, and a second one approves.</p>"
        return self.layout(req, "Connections (MCP)", body, crumbs=[("/settings", "Settings"), (None, "Connections")])

    def _manage_form(self, s: dict, pol: dict, home: str) -> str:
        sid = e(s["id"])
        roles = "".join(
            f'<label class="radio multi"><input type="checkbox" class="opt-input" name="roles" value="{e(r["id"])}"'
            f'{" checked" if r["id"] in pol.get("roles", []) else ""}> {e(r["name"])}</label>' for r in self.hub.roles())
        members = "".join(
            f'<label class="radio multi"><input type="checkbox" class="opt-input" name="members" value="{e(m["id"])}"'
            f'{" checked" if m["id"] in pol.get("members", []) else ""}> {e(m["name"])}'
            f'{" <small>(agent)</small>" if m["kind"] == "agent" else ""}</label>'
            for m in self.hub.members() if m["status"] == "active" and m["kind"] in ("person", "agent"))
        clients = "\n".join(f'{c["name"]} | {c["runs_in"]}' for c in pol.get("clients", []))
        off = ""
        if s["enabled"]:
            off = (f'<form method="post" action="/settings/connections/{sid}/off" class="inline-form">'
                   f'<label>Why switch it off *<input name="reason" required></label>'
                   f'<button class="danger" data-confirm="Switch {e(s["name"])} off? Clients connected to it stop.">'
                   f'Switch off</button></form>')
        where = e(home or "the practice's place")
        return (f'<details class="card"><summary><b>{e(s["name"])}</b> · '
                f'{"on" if s["enabled"] else "off"}</summary>'
                f'<form method="post" action="/settings/connections/{sid}/ask">'
                f'<fieldset class="choices options"><legend>Roles that may connect</legend>{roles}</fieldset>'
                f'<fieldset class="choices options"><legend>Members who may connect</legend>{members}</fieldset>'
                f'<label>Clients (AI apps or programs) that may connect <small class="help">one per line: name | '
                f'where it sends what it reads (where its model runs). No client connects until it is named here.'
                f'</small>'
                f'<textarea name="clients" rows="2">{e(clients)}</textarea></label>'
                f'<label>Why *<input name="reason" required value="{e(pol.get("reason", ""))}"></label>'
                f'<label class="radio multi"><input type="checkbox" class="opt-input" name="acknowledged" value="1" '
                f'required> I checked where each client above sends what it reads, and any outside {where} is '
                f'one the practice agreed to</label>'
                f'<button>{"Change who may connect" if s["enabled"] else "Ask to switch it on"}</button></form>'
                f'{off}</details>')

    def _token_section(self, everyone: list[dict], reachable: list[dict], home: str, base: str) -> str:
        if not reachable:
            return ("<div class='section'><h2>Connect a client</h2><p class='muted'>No server is switched on for you "
                    "(or for an agent you sponsor) yet.</p></div>")
        sample = next((s for s in reachable if s["id"] != mcp.CORE), reachable[0])
        who = "".join(f'<option value="{e(m["id"])}">{e(m["name"])}{" (you)" if i == 0 else ""}</option>'
                      for i, m in enumerate(everyone))
        names = {}
        for s in reachable:
            for c in (s.get("policy") or {}).get("clients", []):
                names.setdefault(c["name"].casefold(), c)
        apps = "".join(f'<option value="{e(c["name"])}">{e(c["name"])} (sends to {e(c["runs_in"])})</option>'
                       for c in names.values()) or '<option value="">No client named yet</option>'
        boxes = "".join(f'<label class="radio multi"><input type="checkbox" class="opt-input" name="servers" '
                        f'value="{e(s["id"])}"> {e(s["name"])}</label>' for s in reachable)
        http_cfg = {"mcpServers": {f"practicehub-{sample['id']}": {
            "type": "http", "url": f"{base}/mcp/{sample['id']}", "headers": {"Authorization": "Bearer ph_YOUR_TOKEN"}}}}
        stdio_cfg = {"mcpServers": {f"practicehub-{sample['id']}": {
            "command": "practicehub", "args": ["mcp", sample["id"], "--url", base],
            "env": {"PRACTICEHUB_TOKEN": "ph_YOUR_TOKEN"}}}}
        return ("<div class='section'><h2>Connect a client</h2><p>An MCP token opens only the servers it names, for "
                "the one client it names, from those the policy owners named for the server; any other is "
                "refused there. An API token is not for an AI app: it is for the practice's own programs.</p>"
                '<form method="post" action="/settings/connections/token" class="card">'
                f'<label>For<select name="member">{who}</select></label>'
                f'<fieldset class="choices options"><legend>Servers</legend>{boxes}</fieldset>'
                f'<label>The AI app or program *<select name="client" required>{apps}</select></label>'
                '<button>Issue an MCP token</button></form>'
                "<p>Then, over HTTP:</p>"
                f"<pre>{e(json.dumps(http_cfg, indent=2))}</pre>"
                "<p>Or through the command line bridge, for clients that speak stdio:</p>"
                f"<pre>{e(json.dumps(stdio_cfg, indent=2))}</pre></div>")

    def post_connection_token(self, req):
        f, me = self.form(req), req["me"]
        many = lambda k: f.get(k, []) if isinstance(f.get(k), list) else ([f[k]] if f.get(k) else [])  # noqa: E731
        try:
            tok = mcp.issue_token(self.hub, me, str(f.get("member") or me["id"]), servers_=many("servers"),
                                  client=str(f.get("client", "")), runs_in=str(f.get("runs_in", "")))
        except (Denied, Invalid, NotFound) as exc:
            return self.page_connections(req, error=str(exc))
        return self.page_connections(req, new_token=tok)

    def post_connection(self, req, sid, verb):
        f, me, hub = self.form(req), req["me"], self.hub
        many = lambda k: f.get(k, []) if isinstance(f.get(k), list) else ([f[k]] if f.get(k) else [])  # noqa: E731
        try:
            if verb == "ask":
                clients = []
                for line in str(f.get("clients", "")).splitlines():
                    if line.strip():
                        name, _, where = line.partition("|")
                        clients.append({"name": name.strip(), "runs_in": where.strip()})
                mcp.ask_access(hub, me, sid, roles=many("roles"), members=many("members"), clients=clients,
                               reason=str(f.get("reason", "")), acknowledged=f.get("acknowledged") == "1")
            elif verb == "approve":
                mcp.approve_access(hub, me, sid)
            elif verb == "withdraw":
                mcp.withdraw_access(hub, me, sid)
            else:
                if not (me["kind"] == "person" and hub.is_policy_owner(me)):
                    raise Denied("a policy owner switches MCP servers off")
                mcp.switch_off(hub, me, sid, str(f.get("reason", "")))
        except (Denied, Invalid, NotFound) as exc:
            return self.page_connections(req, error=str(exc))
        from .server import Redirect
        raise Redirect("/settings/connections")
