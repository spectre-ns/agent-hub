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
from practicehub.web.server import App, make_handler


def install_dms(hub):
    hub.approve_module_code(M(hub, "dr-amara"), "dms")
    hub.install_module(M(hub, "rana"), "dms")


def token(hub, mid):
    m = hub.member(mid)
    by = mid if m["kind"] == "person" else m["sponsor"]
    return hub.issue_token(M(hub, by), mid, "test")


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
    # a page's cookie is not a credential here
    req = urllib.request.Request(f"{server}/mcp/practice", method="POST", data=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
                                 headers={"Content-Type": "application/json", "Cookie": f"ph_token={tok}"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 401


def test_a_disabled_or_missing_module_server_is_404(hub, server):
    install_dms(hub)
    tok = token(hub, "rana")
    assert rpc(server, "dms", tok, "ping")[0] == 200
    assert rpc(server, "nosuch", tok, "ping")[0] == 404
    with pytest.raises(Exception):
        mcp.set_enabled(hub, M(hub, "jo"), "dms", False)  # not a policy owner
    mcp.set_enabled(hub, M(hub, "rana"), "dms", False, "testing")
    assert rpc(server, "dms", tok, "ping")[0] == 404
    assert any(e["action"] == "mcp.disabled" and e["subject"] == "mcp:dms" for e in hub.track.list(limit=500))
    mcp.set_enabled(hub, M(hub, "rana"), "dms", True)
    assert rpc(server, "dms", tok, "ping")[0] == 200


# ------------------------------------------------------------------ tools from definitions
def test_every_installed_module_has_a_server_with_tools_from_its_definitions(hub):
    install_dms(hub)
    hub.set_roles(M(hub, "rana"), "jo", M(hub, "jo")["roles"] + ["document-author"])
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
    tok = token(hub, "jo")
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
summary = "A low-risk check an agent may decide within its criteria."
[trigger]
starters = ["intake-coordinator"]
[[input]]
name = "kind"
type = "string"
required = true
[[steps]]
id = "check"
title = "Check it"
kind = "gate"
role = "%s"
signer = "any"
agent_criteria = "input.kind == 'routine'"
skill = "%s"
risk = "low"
outcomes = ["ok", "not-ok"]
meaning = "I confirm the check"
context = ["input.kind"]
'''


def test_an_agents_decision_is_only_a_proposal_its_sponsor_signs(hub):
    agent = M(hub, "intake-agent")
    role, skill = agent["roles"][0], agent["profile"]["skills"][0]
    p = hub.propose_workflow(M(hub, "jo"), GATE % (role, skill), "a check")
    hub.approve_workflow(M(hub, "rana"), "mcp-check", p["version"])
    run = hub.engine.start(M(hub, "jo"), "mcp-check", inputs={"kind": "routine"})
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
