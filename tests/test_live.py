"""Live co-editing sessions: two people in one page, their changes merged and saved as one version naming both."""

import threading

import pytest

from conftest import M
from practicehub import ot
from practicehub.hub import Denied, Invalid, member_ref
from practicehub.live import Live, word_diff
from test_rules import install


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def wiki(hub):
    install(hub, "wiki")
    for m in hub.pack.modules:
        m.module.demo(hub)
    return hub


@pytest.fixture
def live(wiki):
    lv = Live(wiki, clock=Clock())
    wiki._live = lv
    return lv


def _page(hub, title):
    rec = next(p for p in hub.records.search("WikiPage", limit=None) if p["title"] == title)
    return f"WikiPage/{rec['id']}", rec


def ins(text, at, words):
    return [at, words, ot.u16len(text) - at]


def test_two_editors_type_at_once_and_the_saved_version_names_both(live):
    hub = live.hub
    jo, noor = M(hub, "jo"), M(hub, "noor")
    ref, rec = _page(hub, "On-call guide")
    a, b = live.join(jo, ref, "body"), live.join(noor, ref, "body")
    assert a["mode"] == b["mode"] == "edit" and [m["id"] for m in b["members"]] == ["jo", "noor"]
    text = a["text"]
    # both made a change on the same text (seq 0); the second is transformed past the first
    live.submit(jo, ref, "body", "jo-1-aaaa", 0, ins(text, 0, "Read this first. "))
    live.submit(noor, ref, "body", "noor-1-bbbb", 0, ins(text, len(text), "\n\nAsk the nurse in charge."))
    s = live.session(ref, "body")
    assert s["text"] == "Read this first. " + text + "\n\nAsk the nurse in charge."
    # each sees the other's change in order, and their own as its acknowledgement
    got = live.poll(jo, ref, "body", 0, wait=0)
    assert [(o["seq"], o["cid"]) for o in got["ops"]] == [(1, "jo-1-aaaa"), (2, "noor-1-bbbb")]
    replay = text
    for o in got["ops"]:
        replay = ot.apply(o["op"], replay)
    assert replay == s["text"]
    # the record is not touched per keystroke; the last one out saves one version naming both
    assert hub.records.read(*ref.split("/"))["body"] == text
    live.leave(jo, ref, "body")
    assert live.session(ref, "body") is not None
    live.leave(noor, ref, "body")
    assert live.session(ref, "body") is None
    after = hub.records.read(*ref.split("/"))
    assert after["body"] == s["text"]
    v = hub.db.record_versions.find_one({"type": "WikiPage", "id": rec["id"]}, sort=[("version", -1)])
    assert v["comment"].startswith("Edited by ") and set(v["authors"]) == {"jo", "noor"}
    assert {"jo", "noor"} <= hub.revision_authors(ref)


def test_an_operation_sent_twice_after_a_lost_connection_is_kept_once(live):
    hub, jo = live.hub, M(live.hub, "jo")
    ref, _ = _page(hub, "Booking rules")
    st = live.join(jo, ref, "body")
    op = ins(st["text"], 0, "X")
    assert live.submit(jo, ref, "body", "jo-dup-0001", 0, op) == {"seq": 1}
    assert live.submit(jo, ref, "body", "jo-dup-0001", 0, op) == {"seq": 1, "duplicate": True}
    assert live.session(ref, "body")["text"] == "X" + st["text"]


def test_a_grant_taken_away_mid_session_stops_the_next_change(live):
    hub, jo = live.hub, M(live.hub, "jo")
    ref, _ = _page(hub, "Onboarding")
    st = live.join(jo, ref, "body")
    live.submit(jo, ref, "body", "jo-g-00001", 0, ins(st["text"], 0, "A"))
    hub.authz.delete([("role:wiki-editor", "assignee", member_ref("jo"))])
    with pytest.raises(Denied):
        live.submit(jo, ref, "body", "jo-g-00002", 1, ins("A" + st["text"], 0, "B"))
    assert live.session(ref, "body")["text"] == "A" + st["text"]


def test_a_reader_watches_and_a_locked_record_is_not_edited_live(live):
    hub = live.hub
    ref, _ = _page(hub, "On-call guide")
    hub.authz.delete([("role:wiki-editor", "assignee", member_ref("jo"))])
    watch = live.join(M(hub, "jo"), ref, "body")
    assert watch["mode"] == "watch" and "not change" in watch["why"]
    with pytest.raises(Denied):
        live.submit(M(hub, "jo"), ref, "body", "jo-w-00001", 0, ["x"])


def test_a_change_made_outside_the_session_is_merged_not_lost(live):
    hub = live.hub
    noor, sam = M(hub, "noor"), M(hub, "sam")
    ref, rec = _page(hub, "Backups")
    st = live.join(noor, ref, "body")
    text = st["text"]
    live.submit(noor, ref, "body", "noor-o-0001", 0, ins(text, len(text), "\n\nAsk IT."))
    # meanwhile someone saves the page from its form
    hub.update_record(sam, ref, {"body": "Backups. " + text}, expected_version=st["version"])
    live.submit(noor, ref, "body", "noor-o-0002", 1, ins(text + "\n\nAsk IT.", 0, ""))  # any change syncs
    s = live.session(ref, "body")
    assert s["text"] == "Backups. " + text + "\n\nAsk IT."
    assert hub.records.read(*ref.split("/"))["body"] == s["text"]  # merged and saved at once


def test_idle_text_is_saved_and_silent_editors_leave(live):
    hub, jo = live.hub, M(live.hub, "jo")
    ref, _ = _page(hub, "Onboarding")
    st = live.join(jo, ref, "body")
    live.submit(jo, ref, "body", "jo-i-00001", 0, ins(st["text"], 0, "Hello. "))
    for _ in range(3):  # jo is still there (polling), and nothing more is typed
        live.clock.t += 44
        live.poll(jo, ref, "body", 1, pv=-2, wait=0)
        if live.clock.t - 1_000_000 < 120:
            live.sweep()
            assert hub.records.read(*ref.split("/"))["body"] == st["text"]  # not yet
    out = live.sweep()
    assert out["saved"] and hub.records.read(*ref.split("/"))["body"].startswith("Hello. ")
    live.clock.t += 61
    out = live.sweep()
    assert out["left"] == [(f"{ref}#body", "jo")] and live.session(ref, "body") is None


def test_a_long_poll_wakes_when_someone_types(live):
    hub, jo, noor = live.hub, M(live.hub, "jo"), M(live.hub, "noor")
    live.clock = __import__("time").time
    ref, _ = _page(hub, "Booking rules")
    st = live.join(jo, ref, "body")
    live.join(noor, ref, "body")
    pv = live.session(ref, "body")["pv"]
    got = {}

    def wait():
        got.update(live.poll(noor, ref, "body", 0, pv=pv, wait=5))
    t = threading.Thread(target=wait)
    t.start()
    live.submit(jo, ref, "body", "jo-p-00001", 0, ins(st["text"], 0, "Z"))
    t.join(6)
    assert got["ops"][0]["cid"] == "jo-p-00001"


def test_comments_anchor_reply_resolve_and_mention(live):
    hub = live.hub
    ref, _ = _page(hub, "On-call guide")
    jo = M(hub, "jo")
    c = live.comment(jo, ref, "Is this still 17:00, @noor?", quote="from 17:00 to 08:00",
                     before="on-call phone ", after=", and all")
    assert c["mentions"] == ["noor"]
    assert hub.db.events.find_one({"action": "mention.created", "data.who": "noor"})
    assert any(i["output"]["about"] == ref for i in hub.engine.inbox(M(hub, "noor")))  # a notice, like other work
    live.reply(M(hub, "noor"), ref, c["id"], "Yes.")
    with pytest.raises(Denied):  # a reader who did not write it does not resolve it
        hub.authz.delete([("role:wiki-editor", "assignee", member_ref("sam"))])
        live.resolve(M(hub, "sam"), ref, c["id"])
    assert live.resolve(jo, ref, c["id"])["resolved"] is True
    assert live.anchored(c, "carries the on-call phone from 17:00 to 08:00, and all weekend")
    assert not live.anchored(c, "carries the phone at night")


def test_restore_makes_a_new_version_and_a_word_diff_shows_the_change(live):
    hub, sam = live.hub, M(live.hub, "sam")
    ref, rec = _page(hub, "Backups")
    old = rec["body"]
    hub.update_record(sam, ref, {"body": old + "\n\nNew line."})
    v1 = hub.db.record_versions.find_one({"type": "WikiPage", "id": rec["id"]}, sort=[("version", 1)])["version"]
    after = live.restore(sam, ref, v1)
    assert after["body"] == old
    last = hub.db.record_versions.find_one({"type": "WikiPage", "id": rec["id"]}, sort=[("version", -1)])
    assert last["comment"] == f"Restored version {v1}"
    assert word_diff("call the patient today", "call the patient the same day") == [
        ("same", "call the patient "), ("del", "today"), ("ins", "the same day")]
    live.join(sam, ref, "body")
    with pytest.raises(Invalid):
        live.restore(sam, ref, v1)
