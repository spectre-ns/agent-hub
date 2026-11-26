"""MCP servers: generated from the definitions in force, filtered to the member, every call through the hub's rules
and on the track, and agents never sign or approve."""
import ast
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from conftest import M
from practicehub import mcp
from practicehub.hub import Denied
from practicehub.web.server import App, make_handler


def install_dms(hub):
    hub.approve_module_code(M(hub, "dr-amara"), "dms")
    hub.install_module(M(hub, "rana"), "dms")


def enable(hub, sid, *, roles=None, members=(), clients=()):
    """Rana asks, Dr Amara (a second policy owner) approves: the server is on for these roles (every role if none)."""
    roles = [r["id"] for r in hub.roles()] if roles is None else roles
    clients = list(clients) or [{"name": "test client", "runs_in": mcp.residency(hub)}]
    mcp.ask_access(hub, M(hub, "rana"), sid, roles=roles, members=members, clients=clients, reason="test",
                   acknowledged=True)
    if mcp.request_of(hub, sid):
        mcp.approve_access(hub, M(hub, "dr-amara"), sid)


def token(hub, mid, servers=("core", "practice"), *, client="test client", runs_in=None, on=True):
    m = hub.member(mid)
    by = mid if m["kind"] == "person" else m["sponsor"]
    if on:
        for sid in servers:
            if not mcp.server(hub, sid):
                enable(hub, sid)
    return mcp.issue_token(hub, M(hub, by), mid, servers_=list(servers), client=client,
                           runs_in=runs_in or "", label="test")


@pytest.fixture
def server(hub):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(hub, dev=False)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def rpc(base, sid, tok, method, params=None, *, mid=1, headers=None, raw=None):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **(headers or {})}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    body = raw if raw is not None else json.dumps({"jsonrpc": "2.0", "id": mid, "method": method,
                                                   "params": params or {}}).encode()
    req = urllib.request.Request(f"{base}/mcp/{sid}", data=body, method="POST", headers=h)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode()


def names(hub, sid, mid):
    return {t["name"] for t in mcp.tools(hub, sid, M(hub, mid))}


def call(hub, sid, mid, tool, args):
    return mcp.call_tool(hub, sid, M(hub, mid), tool, args)


# ------------------------------------------------------------------ protocol
def test_initialize_negotiates_the_protocol_version(hub, server):
    tok = token(hub, "jo")
    st, h, body = rpc(server, "practice", tok, "initialize",
                      {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    out = json.loads(body)["result"]
    assert st == 200 and out["protocolVersion"] == "2025-06-18"
    assert out["capabilities"]["tools"]["listChanged"] is True
    assert h.get("Mcp-Session-Id")
    _, _, body = rpc(server, "practice", tok, "initialize", {"protocolVersion": "1999-01-01"})
    assert json.loads(body)["result"]["protocolVersion"] == mcp.LATEST  # an unknown version: the server's latest
    st, _, _ = rpc(server, "practice", tok, "ping", headers={"MCP-Protocol-Version": "1999-01-01"})
    assert st == 400
    st, _, body = rpc(server, "practice", tok, "ping")
    assert st == 200 and json.loads(body)["result"] == {}
    st, _, body = rpc(server, "practice", tok, "nope/nothing")
    assert json.loads(body)["error"]["code"] == mcp.METHOD_NOT_FOUND
    st, _, body = rpc(server, "practice", tok, "", raw=b"{not json")
    assert st == 400 and json.loads(body)["error"]["code"] == mcp.PARSE_ERROR
    st, _, body = rpc(server, "practice", tok, "", raw=b'{"jsonrpc":"2.0","method":"notifications/initialized"}')
    assert st == 202 and body == ""
    _, _, body = rpc(server, "practice", tok, "prompts/list")
    assert json.loads(body)["result"] == {"prompts": []}


def test_a_bad_token_is_401_and_a_bad_origin_403(hub, server):
    st, h, _ = rpc(server, "practice", None, "ping")
    assert st == 401 and "Bearer" in h.get("WWW-Authenticate", "")
    st, _, _ = rpc(server, "practice", "ph_not-a-token", "ping")
    assert st == 401
    tok = token(hub, "jo")
    st, _, _ = rpc(server, "practice", tok, "ping", headers={"Origin": "https://evil.example"})
    assert st == 403
    st, _, _ = rpc(server, "practice", tok, "ping", headers={"Origin": server})
    assert st == 200
    # a page's cookie is not a credential here: not a signed-in session, and not a token put in one
    req = urllib.request.Request(f"{server}/mcp/practice", method="POST", data=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
                                 headers={"Content-Type": "application/json",
                                          "Cookie": f"ph_session={hub.start_session(M(hub, 'jo'))}; ph_token={tok}"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 401


def test_a_disabled_or_missing_module_server_is_404(hub, server):
    install_dms(hub)
    tok = token(hub, "rana", ["dms"])
    assert rpc(server, "dms", tok, "ping")[0] == 200
    assert rpc(server, "nosuch", tok, "ping")[0] == 404
    with pytest.raises(Exception):
        mcp.switch_off(hub, M(hub, "jo"), "dms", "no")  # not a policy owner
    mcp.switch_off(hub, M(hub, "rana"), "dms", "testing")
    assert rpc(server, "dms", tok, "ping")[0] == 404
    assert any(e["action"] == "mcp.disabled" and e["subject"] == "mcp:dms" for e in hub.track.list(limit=500))
    enable(hub, "dms")
    assert rpc(server, "dms", tok, "ping")[0] == 200


# ------------------------------------------------------------------ off by default, who may connect, residency
def test_every_server_is_off_until_two_policy_owners_switch_it_on(hub, server):
    assert not [s for s in mcp.servers(hub) if s["enabled"]]
    rana, amara, jo = M(hub, "rana"), M(hub, "dr-amara"), M(hub, "jo")
    with pytest.raises(Exception):  # nobody may connect: no token for it either
        token(hub, "jo", ["practice"], on=False)
    plain = hub.issue_token(jo, "jo", "api")
    assert rpc(server, "practice", plain, "ping")[0] == 404  # off: as if it were not there
    with pytest.raises(Exception):
        mcp.ask_access(hub, jo, "practice", roles=["front-desk"], reason="x", acknowledged=True)  # not a policy owner
    with pytest.raises(Exception):  # the residency acknowledgement is required
        mcp.ask_access(hub, rana, "practice", roles=["front-desk"], reason="x")
    out = mcp.ask_access(hub, rana, "practice", roles=["front-desk", "nurse"], reason="scheduling", acknowledged=True)
    assert out["waiting"] and not mcp.server(hub, "practice")
    with pytest.raises(Exception):
        mcp.approve_access(hub, rana, "practice")  # not the one who asked
    mcp.approve_access(hub, amara, "practice")
    s = mcp.server(hub, "practice")
    assert s["policy"]["roles"] == ["front-desk", "nurse"] and s["policy"]["approved_by"] == "dr-amara"
    ev = next(e for e in hub.track.list(limit=500) if e["action"] == "mcp.enabled")
    assert ev["actor"] == "dr-amara" and ev["data"]["residency"] == mcp.residency(hub)
    assert hub.track.trusted(ev)
    # narrowing applies at once; widening waits again
    mcp.ask_access(hub, rana, "practice", roles=["front-desk"], reason="front desk only", acknowledged=True)
    assert mcp.server(hub, "practice")["policy"]["roles"] == ["front-desk"] and not mcp.request_of(hub, "practice")
    mcp.ask_access(hub, rana, "practice", roles=["front-desk"], members=["noor"], reason="noor too", acknowledged=True)
    assert mcp.server(hub, "practice")["policy"]["members"] == [] and mcp.request_of(hub, "practice")


def test_an_mcp_token_opens_only_its_servers_for_allowed_members(hub, server):
    enable(hub, "practice", roles=["front-desk", "physician"])
    enable(hub, "core", roles=["front-desk"])
    tok = token(hub, "jo", ["practice"], on=False)
    assert rpc(server, "practice", tok, "ping")[0] == 200
    assert rpc(server, "core", tok, "ping")[0] == 403  # on, but not this token's server
    api = hub.issue_token(M(hub, "jo"), "jo", "api")
    assert rpc(server, "practice", api, "ping")[0] == 403  # an API token is not for MCP
    assert hub.authenticate(api)["id"] == "jo" and hub.authenticate(tok) is None  # nor an MCP token for the API
    with pytest.raises(Exception):
        token(hub, "noor", ["practice"], on=False)  # a nurse is not let in
    # a member let in and later left out is refused on the next message, and the refusal is on the track
    mcp.ask_access(hub, M(hub, "rana"), "practice", roles=["physician"], reason="doctors only", acknowledged=True)
    assert rpc(server, "practice", tok, "ping")[0] == 403
    ref = [e for e in hub.track.list(limit=500) if e["action"] == "mcp.refused"]
    assert ref and ref[0]["data"]["stage"] == "connect" and ref[0]["data"]["client"]["name"] == "test client"


def test_a_client_outside_the_residency_is_refused_unless_attested(hub, server):
    home = mcp.residency(hub)
    assert home  # the pack names it; the core names no place
    enable(hub, "practice")
    with pytest.raises(Exception):
        token(hub, "jo", ["practice"], client="Cloud chat", runs_in="Elsewhere")
    with pytest.raises(Denied):  # saying it runs at home does not let an app in: every client is named
        token(hub, "jo", ["practice"], client="Cloud chat", runs_in=home)
    mcp.ask_access(hub, M(hub, "rana"), "practice", roles=[r["id"] for r in hub.roles()],
                   clients=[{"name": "Cloud chat", "runs_in": "Elsewhere"}], reason="vendor agreement",
                   acknowledged=True)
    assert mcp.request_of(hub, "practice")  # attesting a client widens: a second policy owner approves
    mcp.approve_access(hub, M(hub, "dr-amara"), "practice")
    tok = token(hub, "jo", ["practice"], client="Cloud chat", runs_in="Elsewhere")
    assert rpc(server, "practice", tok, "ping")[0] == 200
    issued = next(e for e in hub.track.list(limit=500) if e["action"] == "credential.issued"
                  and e["data"].get("scope") == "mcp")
    assert issued["data"]["client"] == {"name": "Cloud chat", "runs_in": "Elsewhere"}
    # the attestation is taken away: the same token stops at once
    mcp.ask_access(hub, M(hub, "rana"), "practice", roles=[r["id"] for r in hub.roles()], reason="ended",
                   acknowledged=True)
    assert rpc(server, "practice", tok, "ping")[0] == 403


def test_connections_and_calls_are_on_the_track_with_the_client(hub, server):
    tok = token(hub, "jo", ["practice"])
    _, h, _ = rpc(server, "practice", tok, "initialize",
                  {"protocolVersion": "2025-06-18", "clientInfo": {"name": "Desk app", "version": "2.1"}})
    sess = {"Mcp-Session-Id": h["Mcp-Session-Id"]}
    _, _, body = rpc(server, "practice", tok, "tools/call", {"name": "search_patient", "arguments": {}}, headers=sess)
    found = json.loads(body)["result"]["structuredContent"]["records"]
    assert found
    evs = hub.track.list(limit=5000)
    conn = next(e for e in evs if e["action"] == "mcp.connected")
    assert conn["actor"] == "jo" and conn["data"]["client_info"] == {"name": "Desk app", "version": "2.1"}
    assert conn["data"]["client"] == {"name": "test client", "runs_in": mcp.residency(hub)}
    called = next(e for e in evs if e["action"] == "mcp.called" and e["data"]["tool"] == "search_patient")
    assert called["data"]["client_info"]["name"] == "Desk app" and called["data"]["credential"]
    # the search is on each listed patient's access report, as a list page's is
    listed = next(e for e in evs if e["action"] == "records.listed" and e["actor"] == "jo")
    assert listed["data"]["how"] == "mcp" and listed["subject"] == "records:Patient"
    assert {r["ref"] for r in found} <= set(listed["data"]["refs"])
    pid = found[0]["ref"]
    rows = [r for r in hub.access_report(M(hub, "rana"), pid) if r["who"] == "jo"]
    assert any("mcp" in r["how"] for r in rows)
    # opening one is a view of its own
    rpc(server, "practice", tok, "tools/call", {"name": "read_patient", "arguments": {"id": pid.split("/")[1]}},
        headers=sess)
    assert any(e["action"] == "record.viewed" and e["subject"] == pid and e["actor"] == "jo"
               for e in hub.track.list(limit=5000))


def test_the_tool_list_is_made_once_per_message(hub, server, monkeypatch):
    tok = token(hub, "jo", ["practice"])
    _, h, _ = rpc(server, "practice", tok, "initialize", {"protocolVersion": "2025-06-18"})
    sess = {"Mcp-Session-Id": h["Mcp-Session-Id"]}
    made = []
    real = mcp.tools
    monkeypatch.setattr(mcp, "tools", lambda *a, **k: made.append(1) or real(*a, **k))
    rpc(server, "practice", tok, "tools/call", {"name": "search_patient", "arguments": {}}, headers=sess)
    assert len(made) == 1
    made.clear()
    rpc(server, "practice", tok, "tools/list", headers=sess)
    assert len(made) == 1


# ------------------------------------------------------------------ tools from definitions
def test_every_installed_module_has_a_server_with_tools_from_its_definitions(hub):
    install_dms(hub)
    hub.set_roles(M(hub, "rana"), "jo", M(hub, "jo")["roles"] + ["document-author"])
    hub.approve_roles(M(hub, "dr-amara"), "jo")  # documents are beyond the practice manager's own reach
    assert [s["id"] for s in mcp.servers(hub)] == ["core", "practice", "dms"]
    jo = {t["name"]: t for t in mcp.tools(hub, "dms", M(hub, "jo"))}
    assert {"search_document", "read_document", "create_document", "update_document", "move_document",
            "view_dms_documents"} <= set(jo)
    create = jo["create_document"]["inputSchema"]
    assert create["required"] == ["title"] and create["properties"]["revision"]["type"] == "number"
    assert "status" not in create["properties"]  # the lifecycle moves with move_, not by writing it
    move = jo["move_document"]["inputSchema"]["properties"]["to"]["enum"]
    assert "in-review" in move and "approved" not in move  # only the review workflow approves
    assert jo["search_document"]["annotations"]["readOnlyHint"] is True
    assert jo["move_document"]["annotations"]["destructiveHint"] is True
    # what the practice's own types say, on the practice server, and nothing of the module's
    assert "search_patient" in names(hub, "practice", "jo") and "search_document" not in names(hub, "practice", "jo")
    # references are Type/id strings; choices are enums
    appt = {t["name"]: t for t in mcp.tools(hub, "practice", M(hub, "jo"))}["create_encounter"]["inputSchema"]
    assert "reasonCode" not in appt["properties"]  # a visit's reason is clinical: not the front desk's to write
    refs = [p for p in appt["properties"].values() if "pattern" in p and "Patient" in p["pattern"]]
    every = [p for t in mcp.tools(hub, "practice", M(hub, "jo")) if t["name"].startswith("create_")
             for p in t["inputSchema"]["properties"].values()]
    assert refs and any("enum" in p or "enum" in p.get("items", {}) for p in every)


def test_the_tool_list_is_filtered_to_the_member(hub):
    jo, agent = names(hub, "practice", "jo"), names(hub, "practice", "intake-agent")
    assert "create_patient" in jo and "create_patient" not in agent
    assert "start_new_patient_intake" in jo
    assert "search_claim" not in names(hub, "practice", "jo")  # billing data: not the front desk's
    assert "propose_decision" in names(hub, "core", "intake-agent") and "propose_decision" not in names(hub, "core", "jo")
    every = {n for sid in ("core", "practice") for m in ("rana", "jo", "intake-agent", "dr-amara")
             for n in names(hub, sid, m)}
    assert not [n for n in every if n.startswith(("sign", "approve", "decide"))]


def test_an_approved_definition_change_changes_the_tools(hub, server):
    tok = token(hub, "jo")
    _, h, _ = rpc(server, "practice", tok, "initialize", {"protocolVersion": "2025-06-18"})
    sess = {"Mcp-Session-Id": h["Mcp-Session-Id"]}
    _, _, body = rpc(server, "practice", tok, "tools/list", headers=sess)
    before = {t["name"]: t for t in json.loads(body)["result"]["tools"]}
    assert "plan_code" not in before["create_coverage"]["inputSchema"]["properties"]
    src = hub.record_type("Coverage").source + '\n[[field]]\nname = "plan_code"\nlabel = "Plan code"\n'
    p = hub.propose("type", M(hub, "scribe-agent"), src, "add a plan code")
    assert "plan_code" not in mcp.tools(hub, "practice", M(hub, "jo"))[0]["inputSchema"]["properties"]  # proposed only
    st, _, body = rpc(server, "practice", tok, "ping", headers=sess)
    assert "list_changed" not in body
    hub.approve(M(hub, "rana"), "type", "Coverage", p["version"])
    st, h2, body = rpc(server, "practice", tok, "ping", headers=sess)
    assert h2["Content-Type"].startswith("text/event-stream") and "notifications/tools/list_changed" in body
    _, _, body = rpc(server, "practice", tok, "tools/list", headers=sess)
    after = {t["name"]: t for t in json.loads(body)["result"]["tools"]}
    assert "plan_code" in after["create_coverage"]["inputSchema"]["properties"]
    assert "list_changed" not in rpc(server, "practice", tok, "ping", headers=sess)[2]  # told once


# ------------------------------------------------------------------ calls go through the hub
def test_create_search_and_move_go_through_the_hub_and_are_logged(hub, server):
    install_dms(hub)
    hub.set_roles(M(hub, "rana"), "jo", M(hub, "jo")["roles"] + ["document-author"])
    hub.approve_roles(M(hub, "dr-amara"), "jo")  # documents are beyond the practice manager's own reach
    out = call(hub, "dms", "jo", "create_document", {"title": "Hand hygiene", "number": "SOP-001"})
    assert not out["isError"], out
    ref = out["structuredContent"]["ref"]
    assert out["structuredContent"]["state"] == "draft"
    found = call(hub, "dms", "jo", "search_document", {"text": "hygiene"})["structuredContent"]["records"]
    assert [r["ref"] for r in found] == [ref]
    rid = ref.split("/")[1]
    read = call(hub, "dms", "jo", "read_document", {"id": rid})["structuredContent"]
    moved = call(hub, "dms", "jo", "move_document", {"id": rid, "version": read["version"], "to": "in-review"})
    assert not moved["isError"] and moved["structuredContent"]["state"] == "in-review"
    # a stale version is refused, as on the API
    stale = call(hub, "dms", "jo", "move_document", {"id": rid, "version": read["version"], "to": "draft"})
    assert stale["isError"]
    # required fields and arguments are the hub's / the schema's
    bad = call(hub, "dms", "jo", "create_document", {"number": "SOP-002"})
    assert bad["isError"] and "title" in bad["content"][0]["text"]
    evs = hub.track.list(limit=5000)
    called = [e for e in evs if e["action"] == "mcp.called"]
    assert {e["data"]["tool"] for e in called} >= {"create_document", "search_document", "move_document"}
    assert all(e["data"]["how"] == "mcp" and e["data"]["server"] == "dms" for e in called)
    created = next(e for e in evs if e["action"] == "record.created" and e["subject"] == ref)
    assert any(c["seq"] == created["cause"] for c in called)
    assert any(e["action"] == "record.viewed" and "mcp dms/read_document" in json.dumps(e["data"]) for e in evs)
    assert "SOP-001" not in json.dumps([e["data"] for e in called])  # arguments are not logged
    # over HTTP the same
    tok = token(hub, "jo", ["dms"])
    _, _, body = rpc(server, "dms", tok, "tools/call", {"name": "search_document", "arguments": {}})
    res = json.loads(body)["result"]
    assert res["isError"] is False and res["structuredContent"]["records"] and res["content"][0]["type"] == "text"
    _, _, body = rpc(server, "dms", tok, "tools/call", {"name": "made_up_tool", "arguments": {}})
    assert json.loads(body)["error"]["code"] == mcp.INVALID_PARAMS


def test_unreadable_records_are_refused(hub):
    pat = hub.records.search("Patient", limit=1)[0]
    assert "read_claim" not in names(hub, "practice", "scheduling-agent")  # not even listed
    out = call(hub, "practice", "scheduling-agent", "read_claim", {"id": "x"})
    assert out["isError"] and out["structuredContent"]["error"] == "denied"
    claims = hub.records.search("Claim", limit=1)
    if claims:
        out = call(hub, "practice", "jo", "read_claim", {"id": claims[0]["id"]})
        assert out["isError"]
    ok = call(hub, "practice", "jo", "read_patient", {"id": pat["id"]})
    assert not ok["isError"]
    assert any(e["action"] == "mcp.refused" for e in hub.track.list(limit=5000))


def test_resources_are_types_and_documents_in_force(hub):
    install_dms(hub)
    rs = mcp.resources(hub, "dms", M(hub, "jo"))
    assert any(r["uri"] == "practicehub://dms/types/Document" for r in rs)
    got = mcp.read_resource(hub, "dms", M(hub, "jo"), "practicehub://dms/types/Document")
    sch = json.loads(got["contents"][0]["text"])
    assert sch["properties"]["title"]["type"] == "string" and "title" in sch["required"]
    with pytest.raises(mcp.RpcError):
        mcp.read_resource(hub, "practice", M(hub, "scheduling-agent"), "practicehub://practice/types/Claim")


# ------------------------------------------------------------------ never signs, never approves
def test_an_agent_cannot_sign_or_approve(hub):
    install_dms(hub)
    hub.set_roles(M(hub, "rana"), "jo", M(hub, "jo")["roles"] + ["document-author"])
    hub.approve_roles(M(hub, "dr-amara"), "jo")  # documents are beyond the practice manager's own reach
    for m in ("intake-agent", "scheduling-agent", "clinical-admin-agent"):
        for sid in ("core", "practice", "dms"):
            assert not [n for n in names(hub, sid, m) if n.startswith(("sign", "approve", "decide"))]
    rt = hub.record_type("Document")
    # a move into force is never offered to an agent, even a hand move a person may make
    assert all(not mcp.puts_in_force(rt, t) for t in mcp.moves_for(rt, {"kind": "agent"}))
    out = call(hub, "core", "jo", "propose_decision", {"item": "x", "outcome": "y", "seen": "z"})
    assert out["isError"]  # people decide on the item's page
    out = call(hub, "dms", "intake-agent", "move_document", {"id": "x", "version": "1", "to": "approved"})
    assert out["isError"]


GATE = '''id = "mcp-check"
title = "MCP check"
record = "Patient"
summary = "A low-risk check an agent may decide within its criteria."
[trigger]
starters = ["intake-coordinator"]
[[input]]
name = "kind"
type = "string"
required = true
[start]
to = "checking"
[[state]]
id = "checking"
[[state.work]]
id = "check"
title = "Check it"
kind = "gate"
role = "%s"
signer = "any"
agent_criteria = "input.kind == 'routine'"
skill = "%s"
risk = "low"
meaning = "I confirm the check"
context = ["input.kind"]
[[edge]]
id = "ok"
from = "checking"
to = "checked"
[[edge]]
id = "not-ok"
from = "checking"
to = "checked"
[[state]]
id = "checked"
'''


def test_an_agents_decision_is_only_a_proposal_its_sponsor_signs(hub):
    agent = M(hub, "intake-agent")
    role, skill = agent["roles"][0], agent["profile"]["skills"][0]
    p = hub.propose_workflow(M(hub, "jo"), GATE % (role, skill), "a check")
    hub.approve_workflow(M(hub, "rana"), "mcp-check", p["version"])
    pat = "Patient/" + hub.records.search("Patient", limit=1)[0]["id"]
    run = hub.engine.start(M(hub, "jo"), "mcp-check", record=pat, inputs={"kind": "routine"})
    iid = hub.db.items.find_one({"run_id": run["id"], "step_id": "check"})["id"]
    ctx = call(hub, "core", "intake-agent", "read_item", {"item": iid})
    assert not ctx["isError"], ctx
    out = call(hub, "core", "intake-agent", "propose_decision",
               {"item": iid, "outcome": "ok", "seen": ctx["structuredContent"]["seen"]})
    assert not out["isError"], out
    assert hub.engine.item(iid)["status"] == "held"  # not decided: low risk or not, it waits
    hold = hub.db.items.find_one({"run_id": run["id"], "kind": "hold"})
    assert hold["assignee"] == agent["sponsor"] and hold["persons_only"]
    assert "check" not in hub.engine.run(run["id"])["ctx"]


# ------------------------------------------------------------------ the core names no module or practice
def test_the_mcp_core_names_no_module_or_practice(hub):
    import practicehub
    from practicehub import pack as packmod
    words = set(packmod.MODULES_DIR.glob("*/module.toml"))
    banned = {d.parent.name for d in words} | {"general-practice", "Document", "Patient", "clinic"}
    root = Path(practicehub.__file__).parent
    for f in (root / "mcp.py", root / "mcp_bridge.py", root / "web" / "mcp_pages.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for w in banned:
                    assert w not in node.value.split("/") and node.value != w, (f.name, node.value)


def test_a_call_the_hub_cannot_finish_is_an_error_to_the_agent_and_a_failure_on_the_track(hub, monkeypatch):
    from practicehub.store import Transient
    pat = hub.records.search("Patient", limit=1)[0]

    def broken(*a, **k):
        raise KeyError("a bug")
    monkeypatch.setattr(mcp, "_run", broken)
    out = call(hub, "practice", "jo", "read_patient", {"id": pat["id"]})
    assert out["isError"] and out["structuredContent"]["error"] == "failed"
    assert "a bug" not in json.dumps(out)  # the detail goes to the log, not to the agent
    (failed,) = [e for e in hub.track.list(limit=5000) if e["action"] == "mcp.failed"]
    called = next(e for e in hub.track.list(limit=5000) if e["action"] == "mcp.called" and e["seq"] == failed["cause"])
    assert failed["data"]["tool"] == "read_patient" and failed["data"]["error"] == "KeyError"
    assert called["data"]["tool"] == "read_patient"

    def busy(*a, **k):
        raise Transient("the database stayed busy")
    monkeypatch.setattr(mcp, "_run", busy)
    out = call(hub, "practice", "jo", "read_patient", {"id": pat["id"]})
    assert out["isError"] and out["structuredContent"]["error"] == "busy"
    assert hub.track.verify()["ok"]


def test_find_by_key_names_a_record_or_work_and_nothing_to_who_may_not_read_it(hub):
    """``find_by_key``: a record's key gives its ref, title and state, as a search lists it (and the listing is on
    the subject's access report); work on no record gives the item to read; a member who may not read the record, or
    a key nothing has, gets nothing."""
    assert "find_by_key" in names(hub, "core", "jo")
    p = hub.db.records.find_one({"type": "Patient"}, sort="kn")
    before = hub.db.events.count({"action": "records.listed", "actor": "dr-amara"})
    out = call(hub, "core", "dr-amara", "find_by_key", {"key": f" {p['key'].lower()} "})
    assert not out["isError"]
    got = out["structuredContent"]
    assert (got["key"], got["ref"]) == (p["key"], p["_id"]) and got["title"] and "href" not in got
    assert hub.db.events.count({"action": "records.listed", "actor": "dr-amara"}) == before + 1
    hub.add_member(M(hub, "rana"), {"id": "kim", "kind": "person", "name": "Kim Lee", "roles": []})
    assert not hub.may_read(M(hub, "kim"), "Patient")
    for mid, key in (("kim", p["key"]), ("dr-amara", "PAT-99999"), ("dr-amara", "not a key")):
        out = call(hub, "core", mid, "find_by_key", {"key": key})
        assert out["isError"] and "nothing you may read has the key" in out["content"][0]["text"], (mid, key)
    code = hub.practice_code()
    hub.db.runs.insert({"_id": "run-loose", "id": "run-loose", "record": None, "status": "running"})
    hub.db.items.insert({"_id": "item-loose", "id": "item-loose", "n": 999, "run_id": "run-loose", "kind": "notice",
                         "role": "physician", "status": "open", "title": "On no record"})
    got = call(hub, "core", "dr-amara", "find_by_key", {"key": f"{code}-999"})["structuredContent"]
    assert got == {"key": f"{code}-999", "item": "item-loose"}
