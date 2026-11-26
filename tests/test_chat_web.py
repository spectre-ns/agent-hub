"""Talking to an agent in the browser: the button on a person's pages only, a turn posted and the answer waited for,
and a conversation nobody else reads."""

import json
import time

from practicehub.chat import chat_of
from practicehub.models import ModelGateway
from practicehub.web.server import App
from test_portal import Browser, _sign_in, person, portal_app
from test_rules import _ellen_and_her_result
from test_web import token

DOCK = "data-chat-dock"


def _get(app, path, tok=None):
    st, h, body = app.handle("GET", path, {"authorization": f"Bearer {tok}"} if tok else {}, b"")
    return st, body.decode()


def _api(app, method, path, tok, body=None):
    st, _, out = app.handle(method, path, {"authorization": f"Bearer {tok}", "content-type": "application/json"},
                            json.dumps(body).encode() if body is not None else b"")
    return st, json.loads(out)


def _scripted(hub, *script):
    steps = list(script)

    def chat(ctx):
        return steps.pop(0) if steps else {"text": "That is all."}
    chat_of(hub, ModelGateway(hub, standins={**hub.pack.standins, "chat": chat}))


def test_the_chat_button_is_on_a_person_s_pages_only(hub):
    app = App(hub)
    amara = token(hub, "dr-amara")
    assert DOCK in _get(app, "/", amara)[1]  # the work
    assert DOCK in _get(app, "/settings", amara)[1]  # the settings, in their own shell
    assert DOCK not in _get(app, "/login")[1]  # signed out
    st, page = _get(app, "/", token(hub, "scribe-agent"))  # an agent's session (it talks with nobody here)
    assert DOCK not in page
    rx = hub.records.search("MedicationRequest")[0]
    st, page = _get(app, f"/records/MedicationRequest/{rx['id']}/export?format=print", amara)
    assert st == 200 and "window.print()" in page and DOCK not in page
    person(hub)
    b = Browser(portal_app(hub))
    _sign_in(b)
    assert DOCK not in b.go("GET", "/me")[2]  # the portal: a patient talks with the practice, not its agents
    assert ".chat-fab" in app.handle("GET", "/static/app.css", {}, b"")[2].decode()


def test_a_chat_turn_is_posted_and_its_answer_waited_for(hub):
    _, oref = _ellen_and_her_result(hub)
    _scripted(hub, {"calls": [{"name": "read", "args": {"ref": oref}}]}, {"text": "Her result is in."})
    app = App(hub)
    amara = token(hub, "dr-amara")
    st, got = _api(app, "GET", "/api/chats", amara)
    assert st == 200 and "clinical-admin-agent" in {a["id"] for a in got["agents"]} and got["chats"] == []
    st, got = _api(app, "POST", "/api/chats", amara, {"agent": "clinical-admin-agent"})
    assert st == 200
    cid = got["chat"]["id"]
    st, got = _api(app, "POST", f"/api/chats/{cid}/messages", amara, {"body": "What came back for Ellen?"})
    assert st == 200 and got["message"]["n"] == 1 and got["message"]["role"] == "person"
    since, seen, deadline = 1, [], time.monotonic() + 30
    while True:  # as the panel does: each wait returns once there is something new
        st, got = _api(app, "GET", f"/api/chats/{cid}?since={since}&busy=1&wait=5", amara)
        assert st == 200
        seen += got["messages"]
        since = max([since] + [m["n"] for m in got["messages"]])
        if not got["chat"]["busy"] or time.monotonic() > deadline:
            break
    assert not got["chat"]["busy"]
    tool, answer = [m for m in seen if m["role"] == "tool"], seen[-1]
    assert answer["role"] == "agent" and answer["body"] == "Her result is in."
    assert tool[0]["line"] == f"Read {oref}" and tool[0]["links"][0]["ref"] == oref
    used = got["chat"]["resources"]
    assert [(r["ref"], r["how"]) for r in used] == [(oref, "read")] and used[0]["href"].endswith(oref.split("/")[1])
    st, got = _api(app, "GET", "/api/chats", amara)
    assert got["chats"][0]["id"] == cid and got["chats"][0]["title"] == "What came back for Ellen?"


def test_another_person_s_chat_is_not_found_and_an_agent_has_none(hub):
    _scripted(hub)
    app = App(hub)
    amara = token(hub, "dr-amara")
    cid = _api(app, "POST", "/api/chats", amara, {"agent": "clinical-admin-agent"})[1]["chat"]["id"]
    rana = token(hub, "rana")
    assert _api(app, "GET", f"/api/chats/{cid}", rana)[0] == 404
    assert _api(app, "POST", f"/api/chats/{cid}/messages", rana, {"body": "hello"})[0] == 404
    assert cid not in {c["id"] for c in _api(app, "GET", "/api/chats", rana)[1]["chats"]}
    assert _api(app, "GET", "/api/chats", token(hub, "scribe-agent"))[0] == 403
    assert hub.db.chat_messages.find_one({"chat": cid}) is None  # nothing was said
