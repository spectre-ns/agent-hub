"""@-mentions on office documents: threads on a file a record holds, an agent asked in them, its reply and its
suggested change (words a person makes in the document themselves). No Collabora runs: the panel is on the record
page when Collabora is not set up."""

import io
import json
import urllib.parse
import zipfile

import pytest

from conftest import M, snapshot_onto
from practicehub import filetext
from practicehub.hub import Denied, Invalid, member_ref
from practicehub.integrations import collabora as cool
from practicehub.live import Live
from practicehub.mentions import mentions_of
from practicehub.web.server import App

AGENT = "scribe-agent"
TEXT = "Visitors sign in at the front desk. Badges are returned before 17:00."


def _odt(*paras: str) -> bytes:
    buf = io.BytesIO()
    body = "".join(f"<text:p>{p}</text:p>" for p in paras).encode()
    with zipfile.ZipFile(io.BytesIO(cool.blank_document("odt"))) as src, zipfile.ZipFile(buf, "w") as out:
        for i in src.infolist():
            data = src.read(i)
            if i.filename == "content.xml":
                data = data.replace(b"<text:p/>", body)
            out.writestr(i, data)
    return buf.getvalue()


@pytest.fixture
def doc(hub):
    """Document control installed, and a draft document holding a text document with words in it."""
    def build(hub):
        hub.approve_module_code(M(hub, "dr-amara"), "dms")
        hub.install_module(M(hub, "rana"), "dms")
        for m in hub.pack.modules:
            m.module.demo(hub)
        rana = M(hub, "rana")
        att = hub.add_file(rana, "Document", _odt(TEXT, "Ask the duty nurse."), "Visitors.odt", cool.mime_of("odt"))
        rec = hub.create_record(rana, "Document", {"title": "Visitors", "number": "SOP-021", "status": "draft",
                                                   "file": att})
        return f"Document/{rec['id']}", att
    ref, att = snapshot_onto(hub, "office_mentions:doc", build, modules=["dms"])
    hub._live = Live(hub)
    return hub, ref, att


READ = ("data_class:documents", "reader", member_ref(AGENT))


def _let_read(hub):
    hub.authz.write([READ])


def _mention_item(hub, mid):
    return next(i for i in hub.engine.inbox(M(hub, mid)) if (i.get("output") or {}).get("mention"))


def _page(app, path, mid="rana", method="GET", body=b"", ctype="application/x-www-form-urlencoded"):
    return app.handle(method, path, {"cookie": f"ph_as={mid}", "host": "127.0.0.1:8204",
                                     "origin": "http://127.0.0.1:8204", "content-type": ctype}, body)


def _form(**kv) -> bytes:
    return urllib.parse.urlencode(kv).encode()


# ------------------------------------------------------------------------------------------------ the file's text
def test_a_files_text_is_read_with_the_standard_library():
    assert filetext.text_of(_odt("One", "Two"), "a.odt") == "One\nTwo"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/'
                   '2006/main"><w:body><w:p><w:r><w:t>Hello</w:t></w:r><w:r><w:tab/><w:t>there</w:t></w:r></w:p>'
                   '<w:p><w:r><w:t>Second</w:t></w:r></w:p></w:body></w:document>')
    assert filetext.text_of(buf.getvalue(), "a.docx") == "Hello\tthere\nSecond"
    assert filetext.text_of(b"plain words\r\n", "notes.txt") == "plain words"
    assert filetext.text_of(b"\x89PNG\r\n", "photo.png", "image/png") is None
    assert filetext.text_of(b"not a zip", "broken.odt") is None
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w") as z:  # a file that declares entities is not read
        z.writestr("content.xml", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>')
    assert filetext.text_of(bomb.getvalue(), "x.odt") is None


# ------------------------------------------------------------------------------------------------ mentioning
def test_mentioning_an_agent_on_a_document_gives_it_the_files_text_the_quote_and_the_thread(doc):
    hub, ref, att = doc
    _let_read(hub)
    jo = M(hub, "jo")
    c = hub._live.comment(jo, ref, f"@{AGENT} is the time right?", field="file", file="Visitors.odt",
                          quote="before 17:00")
    assert c["file"] == "Visitors.odt" and c["mentions"] == [AGENT]
    row = hub.db.mentions.find_one({"anchor": c["id"]})
    assert row["file"] == "Visitors.odt" and row["field"] == "file" and row["kind"] == "agent"
    item = _mention_item(hub, AGENT)
    assert item["record"] == ref and item["kind"] == "notice"
    ctx = hub.engine.context(M(hub, AGENT), item["id"])["context"]["mention"]
    assert ctx["document"]["available"] and ctx["document"]["text"] == TEXT + "\nAsk the duty nurse."
    assert ctx["mention"]["quote"] == "before 17:00" and ctx["mention"]["file"] == "Visitors.odt"
    assert ctx["thread"]["id"] == c["id"] and "time right" in ctx["thread"]["body"]
    ev = hub.db.events.find_one({"action": "mention.created", "data.who": AGENT})
    assert ev and ev["data"]["file"] == "Visitors.odt"
    assert hub.db.events.find_one({"action": "comment.added", "data.on": "file"})
    # a file that has no text here says so
    png = hub.add_file(M(hub, "rana"), "Document", b"\x89PNG\r\n\x1a\n", "scan.png", "image/png")
    assert mentions_of(hub).file_text(M(hub, AGENT), ref, "file", "scan.png")["why"]  # not held: said
    hub.update_record(M(hub, "rana"), ref, {"file": png})
    got = mentions_of(hub).file_text(M(hub, AGENT), ref, "file", "scan.png")
    assert not got["available"] and got["why"] == "text not available"


def test_the_agents_context_asks_again_whether_it_may_read_the_document(doc):
    hub, ref, _ = doc
    _let_read(hub)
    hub._live.comment(M(hub, "jo"), ref, f"@{AGENT} please check", field="file", file="Visitors.odt")
    item = _mention_item(hub, AGENT)
    hub.authz.delete([READ])  # its grant is taken away after it was asked
    with pytest.raises(Denied):
        mentions_of(hub).context(M(hub, AGENT), item["output"]["mention"])
    with pytest.raises(Denied):  # and the mention is nobody else's to read
        mentions_of(hub).context(M(hub, "noor"), item["output"]["mention"])


def test_an_agent_that_may_not_read_the_document_is_not_mentioned(doc, monkeypatch):
    hub, ref, _ = doc
    jo = M(hub, "jo")
    with pytest.raises(Invalid) as exc:
        hub._live.comment(jo, ref, f"@{AGENT} what do you think?", field="file", file="Visitors.odt")
    assert "may not read this" in str(exc.value)
    assert not hub.db.live_comments.count({"ref": ref})
    assert not [i for i in hub.engine.inbox(M(hub, AGENT)) if (i.get("output") or {}).get("mention")]
    assert hub.db.events.find_one({"action": "mention.refused", "data.who": AGENT})
    cands = {c["id"]: c for c in mentions_of(hub).candidates(jo, ref)}
    assert not cands[AGENT]["may_read"] and cands["noor"]["may_read"]
    # the record page refuses it the same way: 422, and nothing is saved
    monkeypatch.delenv("PRACTICEHUB_COLLABORA_URL", raising=False)
    code = _page(App(hub, dev=True), f"/records/{ref}/comments", "jo", "POST",
                 _form(body=f"@{AGENT} hello", field="file", file="Visitors.odt"))[0]
    assert code == 422 and not hub.db.live_comments.count({"ref": ref})


def test_an_agent_replies_in_the_thread_marked_as_an_agents(doc, monkeypatch):
    hub, ref, _ = doc
    _let_read(hub)
    c = hub._live.comment(M(hub, "jo"), ref, f"@{AGENT} is this still right?", field="file", file="Visitors.odt")
    item = _mention_item(hub, AGENT)
    monkeypatch.delenv("PRACTICEHUB_COLLABORA_URL", raising=False)
    app = App(hub, dev=True)
    code, _, body = _page(app, f"/api/mentions/{item['output']['mention']}/reply", AGENT, "POST",
                          json.dumps({"body": "Yes, 17:00 matches the rota."}).encode(), "application/json")
    assert code == 200, body
    r = hub.db.live_comments.get(c["id"])["replies"][-1]
    assert r["by"] == AGENT and r["agent"] is True
    assert hub.engine.item(item["id"])["status"] == "done"
    assert hub.db.events.find_one({"action": "mention.answered", "data.anchor": c["id"]})
    page = _page(app, f"/records/{ref}", "jo")[2].decode()
    assert "Comments on Visitors.odt" in page and "17:00 matches the rota" in page and "by-agent" in page


def test_a_suggestion_on_a_file_is_made_by_a_person_accepted_or_rejected(doc, monkeypatch):
    hub, ref, att = doc
    _let_read(hub)
    agent, rana = M(hub, AGENT), M(hub, "rana")
    c = hub._live.comment(M(hub, "jo"), ref, f"@{AGENT} tidy the times?", field="file", file="Visitors.odt")
    with pytest.raises(Invalid):  # the words it replaces must be in the file
        hub._live.suggest(agent, ref, c["id"], "", "before 18:00", "by 18:00")
    sug = hub._live.suggest(agent, ref, c["id"], "", "before 17:00", "by 17:00", "Shorter.")["replies"][-1]
    assert sug["agent"] and sug["suggestion"]["status"] == "open" and sug["suggestion"]["by_hand"]
    assert hub.db.events.find_one({"action": "suggestion.proposed", "data.file": "Visitors.odt"})

    monkeypatch.delenv("PRACTICEHUB_COLLABORA_URL", raising=False)
    app = App(hub, dev=True)
    page = _page(app, f"/records/{ref}")[2].decode()
    assert "The hub does not edit the file" in page and "I made this change" in page
    assert "I made this change" not in _page(app, f"/records/{ref}", "jo")[2].decode()  # jo may only read it

    with pytest.raises(Denied):  # an agent never decides one, its own included
        hub._live.decide_suggestion(agent, ref, c["id"], sug["id"], True)
    with pytest.raises(Denied):  # nor does a person who may not change the document
        hub._live.decide_suggestion(M(hub, "jo"), ref, c["id"], sug["id"], True)
    versions = len(hub.record_history(rana, ref))
    code = _page(app, f"/records/{ref}/comments/{c['id']}/suggestions/{sug['id']}/accept", "rana", "POST",
                 _form(next=f"/records/{ref}/office/file/{att['blob']}"))
    assert code[0] == 303 and code[1]["Location"].startswith(f"/records/{ref}/office/file/")
    rec = hub.records.read(*ref.split("/", 1))
    assert rec["file"]["blob"] == att["blob"] and len(hub.record_history(rana, ref)) == versions  # file untouched
    ev = hub.db.events.find_one({"action": "suggestion.accepted"})
    assert ev["actor"] == "rana" and ev["data"]["proposed_by"] == AGENT and ev["data"]["made_by"] == "rana"
    assert "Made in the file by" in _page(app, f"/records/{ref}")[2].decode()

    other = hub._live.suggest(agent, ref, c["id"], "", "Ask the duty nurse.", "Ask the nurse in charge.")
    other = other["replies"][-1]
    hub._live.decide_suggestion(rana, ref, c["id"], other["id"], False)
    assert hub.db.events.find_one({"action": "suggestion.rejected", "data.file": "Visitors.odt"})
    with pytest.raises(Invalid):
        hub._live.decide_suggestion(rana, ref, c["id"], other["id"], True)
    # a redirect elsewhere is not followed: the record page instead
    loc = _page(app, f"/records/{ref}/comments/{c['id']}/resolve", "rana", "POST",
                _form(next="//evil.example/x"))[1]["Location"]
    assert loc.startswith(f"/records/{ref}#")


def test_an_agent_never_saves_signs_or_approves_the_document(doc):
    hub, ref, att = doc
    _let_read(hub)
    agent = M(hub, AGENT)
    with pytest.raises(Denied):
        hub.update_record(agent, ref, {"title": "Changed by an agent"})
    with pytest.raises(Denied):
        hub.add_file(agent, "Document", b"new bytes", "Visitors.odt")
    for meaning in ("I have read and understood this document", "Approved for use"):
        with pytest.raises((Denied, Invalid)):
            hub.sign_record(agent, ref, meaning)
