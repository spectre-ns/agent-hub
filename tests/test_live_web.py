"""The live wiki over HTTP: the editor for writers, comments, versions compared and restored, two editors at once,
a SOP's banner and read-and-sign, and an agent mentioned in a comment answering in the thread."""

import json
import re

import pytest

from conftest import M
from practicehub.hub import member_ref
from test_web import call, passphrase, server, token  # noqa: F401 - the fixture


@pytest.fixture
def wiki(hub, server):  # noqa: F811
    rana, amara = token(hub, "rana"), token(hub, "dr-amara")
    for name in ("dms", "wiki"):
        assert call(server, "POST", f"/modules/{name}/review-code", amara)[0] == 303
        assert call(server, "POST", "/modules", rana, form={"name": name})[0] == 303
    for m in hub.pack.modules:
        m.module.demo(hub)
    return hub, server


def _page(hub, title, rtype="WikiPage"):
    return next(p for p in hub.records.search(rtype, limit=None) if p["title"] == title)


def _live_cfg(page):
    m = re.search(r'data-live="([^"]*)"', page)
    return json.loads(m.group(1).replace("&quot;", '"')) if m else None


def test_writers_get_the_live_editor_and_readers_do_not(wiki):
    hub, srv = wiki
    p = _page(hub, "On-call guide")
    page = call(srv, "GET", f"/records/WikiPage/{p['id']}", token(hub, "jo"))[1]
    cfg = _live_cfg(page)
    assert cfg["may"] is True and cfg["field"] == "body" and "data-live-edit" in page
    hub.authz.delete([("role:wiki-editor", "assignee", member_ref("sam"))])
    page = call(srv, "GET", f"/records/WikiPage/{p['id']}", token(hub, "sam"))[1]
    assert "data-live-edit" not in page and (_live_cfg(page) or {}).get("may") is not True
    assert 'id="c-new"' in page  # a reader still comments


def test_two_people_edit_at_once_over_http(wiki):
    hub, srv = wiki
    p = _page(hub, "Booking rules")
    base = f"/api/live/WikiPage/{p['id']}/body"
    jo, noor = token(hub, "jo"), token(hub, "noor")
    a = json.loads(call(srv, "POST", base + "/join", jo, body={})[1])
    b = json.loads(call(srv, "POST", base + "/join", noor, body={})[1])
    assert a["mode"] == b["mode"] == "edit"
    n = len(a["text"].encode("utf-16-le")) // 2
    assert call(srv, "POST", base + "/ops", jo, body={"cid": "jo-h-000001", "base": 0, "op": ["A. ", n]})[0] == 200
    code, out = call(srv, "POST", base + "/ops", noor, body={"cid": "no-h-000001", "base": 0, "op": [n, " Z."]})
    assert code == 200 and json.loads(out)["seq"] == 2
    polled = json.loads(call(srv, "GET", base + "/poll?since=0&wait=0", noor)[1])
    assert [o["cid"] for o in polled["ops"]] == ["jo-h-000001", "no-h-000001"]
    again = call(srv, "POST", base + "/ops", jo, body={"cid": "jo-h-000001", "base": 0, "op": ["A. ", n]})
    assert json.loads(again[1]).get("duplicate")  # sent twice after a lost answer: kept once
    call(srv, "POST", base + "/done", jo, body={})
    call(srv, "POST", base + "/done", noor, body={})
    assert hub.records.read("WikiPage", p["id"])["body"] == "A. " + a["text"] + " Z."


def test_a_comment_is_made_on_words_and_resolved(wiki):
    hub, srv = wiki
    p = _page(hub, "On-call guide")
    url = f"/records/WikiPage/{p['id']}"
    jo = token(hub, "jo")
    code, _ = call(srv, "POST", url + "/comments", jo, form={"body": "Still right, @noor?", "quote": "17:00"})
    assert code == 303
    c = hub.db.live_comments.find_one({"ref": f"WikiPage/{p['id']}"})
    page = call(srv, "GET", url, jo)[1]
    assert f'id="{c["id"]}"' in page and 'class="at">@noor' in page and 'data-quote="17:00"' in page
    assert call(srv, "POST", f"{url}/comments/{c['id']}/resolve", jo, form={})[0] == 303
    assert hub.db.live_comments.get(c["id"])["resolved"] is True
    assert "Resolved (1)" in call(srv, "GET", url, jo)[1]


def test_versions_are_compared_word_by_word_and_one_is_restored(wiki):
    hub, srv = wiki
    p = _page(hub, "Backups")
    url = f"/records/WikiPage/{p['id']}"
    hub.update_record(M(hub, "jo"), f"WikiPage/{p['id']}", {"body": p["body"].replace("every night", "twice a day")})
    vs = sorted(v["version"] for v in hub.db.record_versions.find({"type": "WikiPage", "id": p["id"]}))
    page = call(srv, "GET", f"{url}/compare?a={vs[0]}&b={vs[-1]}", token(hub, "jo"))[1]
    assert "<del>" in page and "<ins>twice" in page and "Restore" in page
    assert call(srv, "POST", f"{url}/restore", token(hub, "jo"), form={"version": vs[0]})[0] == 303
    assert hub.records.read("WikiPage", p["id"])["body"] == p["body"]
    assert hub.record_history(M(hub, "jo"), f"WikiPage/{p['id']}")[0]["comment"] == f"Restored version {vs[0]}"


def test_a_sop_being_revised_shows_readers_the_released_text_and_asks_them_to_read_and_sign(wiki):
    hub, srv = wiki
    sop = _page(hub, "Hand hygiene", "Procedure")
    ref, url = f"Procedure/{sop['id']}", f"/records/Procedure/{sop['id']}"
    hub.authz.delete([("role:wiki-editor", "assignee", member_ref("sam"))])
    sam = token(hub, "sam")
    page = call(srv, "GET", url, sam)[1]
    assert "Read and sign" in page
    seen = re.search(r'class="read-sign".*?name="seen" value="([0-9a-f]{64})"', page, re.S).group(1)
    meaning = re.search(r'class="read-sign".*?name="meaning" value="([^"]*)"', page, re.S).group(1)
    code, _ = call(srv, "POST", url + "/sign", sam, form={"meaning": meaning.replace("&#x27;", "'"), "seen": seen,
                                                          "passphrase": passphrase(hub, "sam")})
    assert code == 303
    assert "You signed" in call(srv, "GET", url, sam)[1]
    rana = M(hub, "rana")
    hub.update_record(rana, ref, {"status": "draft"}, comment="new poster")
    hub.update_record(rana, ref, {"body": sop["body"] + "\n\nA NEW LINE."})
    page = call(srv, "GET", url, sam)[1]
    assert "A new revision is being written" in page and "A NEW LINE" not in page  # readers work from the released
    page = call(srv, "GET", url, token(hub, "rana"))[1]
    assert "You are reading the" in page and "A NEW LINE" in page and "show=in-use" in page


def test_an_agent_mentioned_in_a_comment_answers_in_the_thread_over_the_api(wiki):
    hub, srv = wiki
    p = _page(hub, "Backups")
    url, agent = f"/records/WikiPage/{p['id']}", "scribe-agent"
    jo = token(hub, "jo")
    code, page = call(srv, "POST", url + "/comments", jo, form={"body": f"@{agent} is this right?"})
    assert code == 422 and "may not read this" in page  # nobody let it read the wiki: refused, said why
    picker = json.loads(call(srv, "GET", f"/api/mentions/candidates?ref=WikiPage/{p['id']}&q=scribe", jo)[1])
    assert picker["members"][0]["kind"] == "agent" and picker["members"][0]["may_read"] is False
    hub.authz.write([("role:wiki-reader", "assignee", member_ref(agent))])
    assert call(srv, "POST", url + "/comments", jo, form={"body": f"@{agent} is this right?",
                                                          "quote": "IT only"})[0] == 303
    at = token(hub, agent)
    item = next(i for i in json.loads(call(srv, "GET", "/api/inbox", at)[1])["items"]
                if (i.get("output") or {}).get("mention"))
    ctx = json.loads(call(srv, "GET", f"/api/items/{item['id']}/context", at)[1])["context"]["mention"]
    assert ctx["record"]["body"] == p["body"] and ctx["mention"]["quote"] == "IT only" and ctx["thread"]
    mid = ctx["mention"]["id"]
    assert call(srv, "POST", f"/api/mentions/{mid}/reply", at, body={"body": "Yes."})[0] == 200
    code, out = call(srv, "POST", f"/api/mentions/{mid}/suggest", at,
                     body={"field": "body", "find": "IT only", "text": "the IT contractor only"})
    assert code == 200
    c = json.loads(out)
    sug = c["replies"][-1]
    page = call(srv, "GET", url, jo)[1]
    assert 'class=by-agent' in page and "badge agent" in page and f"suggestions/{sug['id']}/accept" in page
    assert call(srv, "POST", f"{url}/comments/{c['id']}/suggestions/{sug['id']}/accept", at, form={})[0] == 403
    assert hub.records.read("WikiPage", p["id"])["body"] == p["body"]
    assert call(srv, "POST", f"{url}/comments/{c['id']}/suggestions/{sug['id']}/accept", jo, form={})[0] == 303
    assert "the IT contractor only" in hub.records.read("WikiPage", p["id"])["body"]
