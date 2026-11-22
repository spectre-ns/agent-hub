"""Mentions: people and agents named with @ in a record's text or comments, and an agent answering in the thread."""

import pytest

from conftest import M, snapshot_onto
from practicehub.hub import Denied, Invalid, member_ref
from practicehub.live import Live
from practicehub.mentions import mentioned_ids, mentions_of
from test_rules import install, item_for

AGENT = "scribe-agent"


@pytest.fixture
def wiki(hub):
    snapshot_onto(hub, "mentions:wiki", _wiki, modules=["wiki"])
    hub._live = Live(hub)
    return hub


def _wiki(hub):
    install(hub, "wiki")
    for m in hub.pack.modules:
        m.module.demo(hub)


def _let_read(hub, mid=AGENT):
    hub.authz.write([("role:wiki-reader", "assignee", member_ref(mid))])


def _page(hub, title):
    rec = next(p for p in hub.records.search("WikiPage", limit=None) if p["title"] == title)
    return f"WikiPage/{rec['id']}", rec


def _mention_item(hub, mid):
    return next(i for i in hub.engine.inbox(M(hub, mid)) if (i.get("output") or {}).get("mention"))


def test_mentioning_an_agent_puts_the_page_the_passage_and_the_thread_in_its_inbox(wiki):
    hub, jo = wiki, M(wiki, "jo")
    _let_read(hub)
    ref, rec = _page(hub, "On-call guide")
    c = hub._live.comment(jo, ref, f"@{AGENT} is this still right?", quote="17:00", before="from ", after=" to")
    assert c["mentions"] == [AGENT]
    row = hub.db.mentions.find_one({"anchor": c["id"]})
    assert row["who"] == member_ref(AGENT) and row["kind"] == "agent" and row["by"] == "jo"
    item = _mention_item(hub, AGENT)
    assert item["record"] == ref and item["kind"] == "notice"
    ctx = hub.engine.context(M(hub, AGENT), item["id"])["context"]["mention"]
    assert ctx["record"]["body"] == rec["body"] and ctx["mention"]["quote"] == "17:00"
    assert ctx["thread"]["id"] == c["id"] and "is this still right" in ctx["thread"]["body"]
    assert hub.db.events.find_one({"action": "mention.created", "data.who": AGENT})
    # a person mentioned is told the same way (a notice), and nobody else is
    hub._live.comment(jo, ref, "Thanks @noor")
    assert _mention_item(hub, "noor")["record"] == ref
    assert not [i for i in hub.engine.inbox(M(hub, "sam")) if (i.get("output") or {}).get("mention")]


def test_an_agent_answers_in_the_thread_marked_as_an_agent(wiki):
    hub, jo = wiki, M(wiki, "jo")
    _let_read(hub)
    ref, _ = _page(hub, "On-call guide")
    c = hub._live.comment(jo, ref, f"@{AGENT} please check")
    item = _mention_item(hub, AGENT)
    after = hub._live.reply(M(hub, AGENT), ref, c["id"], "It is still right.")
    r = after["replies"][-1]
    assert r["by"] == AGENT and r["agent"] is True
    assert hub.engine.item(item["id"])["status"] == "done"
    assert hub.db.events.find_one({"action": "mention.answered", "data.anchor": c["id"]})


def test_an_agents_suggestion_changes_nothing_until_a_person_accepts_it(wiki):
    hub, jo = wiki, M(wiki, "jo")
    _let_read(hub)
    agent = M(hub, AGENT)
    ref, rec = _page(hub, "Backups")
    c = hub._live.comment(jo, ref, f"@{AGENT} tidy this?", quote="IT only")
    after = hub._live.suggest(agent, ref, c["id"], "body", "IT only", "the IT contractor only", "Clearer.")
    sug = after["replies"][-1]
    assert sug["agent"] and sug["suggestion"]["status"] == "open"
    assert hub.records.read("WikiPage", rec["id"])["body"] == rec["body"]  # nothing changed yet
    with pytest.raises(Denied):  # an agent never accepts one, its own included
        hub._live.decide_suggestion(agent, ref, c["id"], sug["id"], True)
    hub._live.decide_suggestion(jo, ref, c["id"], sug["id"], True)
    now = hub.records.read("WikiPage", rec["id"])["body"]
    assert "the IT contractor only" in now and "by IT only" not in now
    assert hub.record_history(jo, ref)[0]["comment"] == "Accepted a suggestion from Scribe agent"
    ev = hub.db.events.find_one({"action": "suggestion.accepted"})
    assert ev and ev["data"]["proposed_by"] == AGENT
    with pytest.raises(Invalid):
        hub._live.decide_suggestion(jo, ref, c["id"], sug["id"], False)
    other = hub._live.suggest(agent, ref, c["id"], "body", "every night", "every night at 02:00")["replies"][-1]
    hub._live.decide_suggestion(jo, ref, c["id"], other["id"], False)
    assert "02:00" not in hub.records.read("WikiPage", rec["id"])["body"]
    assert hub.db.events.find_one({"action": "suggestion.rejected"})


def test_an_agent_that_may_not_read_the_page_is_not_mentioned(wiki):
    hub, jo = wiki, M(wiki, "jo")
    ref, _ = _page(hub, "On-call guide")
    before = hub.db.live_comments.count({"ref": ref})
    with pytest.raises(Invalid) as exc:
        hub._live.comment(jo, ref, f"@{AGENT} what do you think?")
    assert "may not read this" in str(exc.value)
    assert hub.db.live_comments.count({"ref": ref}) == before
    assert not hub.engine.inbox(M(hub, AGENT)) or not [i for i in hub.engine.inbox(M(hub, AGENT))
                                                        if (i.get("output") or {}).get("mention")]
    assert hub.db.events.find_one({"action": "mention.refused"})
    cands = {c["id"]: c for c in mentions_of(hub).candidates(jo, ref)}
    assert cands[AGENT]["kind"] == "agent" and not cands[AGENT]["may_read"] and cands["noor"]["may_read"]


def test_a_name_written_in_the_page_text_is_a_mention_too(wiki):
    hub, jo = wiki, M(wiki, "jo")
    _let_read(hub)
    ref, rec = _page(hub, "Onboarding")
    lv = hub._live
    st = lv.join(jo, ref, "body")
    n = len(st["text"].encode("utf-16-le")) // 2
    lv.submit(jo, ref, "body", "jo-m-000001", 0, [n, f"\n\nAsk @{AGENT} and @nobody-here."])
    lv.leave(jo, ref, "body")
    row = hub.db.mentions.find_one({"ref": ref, "member": AGENT})
    assert row and row["anchor"] == "field:body" and "Ask @" in row["quote"]
    assert mentioned_ids("mail a@b.ca, then @noor") == ["noor"]


def test_an_agent_never_releases_a_sop(wiki):
    hub = wiki
    _let_read(hub)
    hub.authz.write([("role:sop-approver", "assignee", member_ref(AGENT))])  # even holding the role
    assert "sop-approver" in M(hub, AGENT)["roles"]
    run = next(r for r in hub.engine.runs() if r["workflow_id"] == "wiki-sop-release")
    i = item_for(hub, run, "release")
    ok, why = hub.engine.eligibility(M(hub, AGENT), hub.engine.item(i))
    assert not ok and "person" in why
    with pytest.raises(Denied):
        hub.engine.claim(M(hub, AGENT), i)
