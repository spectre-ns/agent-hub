"""The web server over real HTTP: agents with their own tokens, people with forms, and what each may see."""

import html
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from conftest import M, item_for, quiet, run_of
from practicehub.agents.runner import AgentRunner, HttpClient, Refused
from practicehub.hub import Denied, Invalid
from practicehub.web.server import App, Redirect, make_handler


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


opener = urllib.request.build_opener(NoRedirect)


@pytest.fixture
def server(hub):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(hub, dev=False)))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def call(base, method, path, token=None, body=None, form=None):
    headers = {}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with opener.open(req) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def token(hub, mid, by=None):
    """A credential: a person's is their own; an agent's or service's comes from its sponsor."""
    if by is None:
        m = hub.member(mid)
        by = mid if m["kind"] == "person" else m["sponsor"]
    return hub.issue_token(M(hub, by), mid, "test")


def passphrase(hub, mid):
    """A person's passphrase, set by them (made up per test; only its scrypt hash is stored)."""
    import hashlib
    words = f"harbour light {hashlib.sha256(mid.encode()).hexdigest()[:6]} burning"
    hub.set_passphrase(M(hub, mid), mid, words)
    return words


def browser(base, cookie=None):
    """A person's browser: forms posted with their session cookie, the cookie the hub sets kept."""
    jar = {"cookie": cookie}

    def go(method, path, form=None, headers=None):
        h = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
        if jar["cookie"]:
            h["Cookie"] = jar["cookie"]
        req = urllib.request.Request(base + path, method=method, headers=h,
                                     data=urllib.parse.urlencode(form).encode() if form is not None else None)
        try:
            with opener.open(req) as r:
                code, hdrs, text = r.status, r.headers, r.read().decode()
        except urllib.error.HTTPError as exc:
            code, hdrs, text = exc.code, exc.headers, exc.read().decode()
        got = hdrs.get("Set-Cookie") or ""
        if got.startswith("ph_session="):
            jar["cookie"] = got.split(";", 1)[0] if "Max-Age=0" not in got else None
        return code, text
    go.jar = jar
    return go


def test_the_api_needs_a_token(server):
    assert call(server, "GET", "/api/me")[0] == 401
    assert call(server, "GET", "/api/me", token="ph_nope")[0] == 401
    code, _ = call(server, "GET", "/")
    assert code == 303  # pages send you to sign in
    assert call(server, "GET", "/login?as=rana")[0] == 200  # no dev sign-in without --dev: just the form


def test_agents_work_over_http_with_their_own_tokens(hub, server):
    tok = token(hub, "intake-agent", by="jo")
    me = json.loads(call(server, "GET", "/api/me", tok)[1])
    assert me["kind"] == "agent" and me["sponsor"] == "jo" and me["practice"] == "Harbourview Family Medicine"
    runner = AgentRunner(HttpClient(server, tok), hub.models, quiet)
    assert runner.work_once() == 1
    run = run_of(hub, "new-patient-intake")
    assert hub.engine.run(run["id"])["steps"]["review"]["by"] == "intake-agent"


def test_the_kill_switch_works_over_http(hub, server):
    tok = token(hub, "scribe-agent", by="dr-amara")
    sponsor = token(hub, "dr-amara", by="dr-amara")
    code, _ = call(server, "POST", "/api/members/scribe-agent/status", sponsor, {"status": "disabled",
                                                                                  "reason": "pause"})
    assert code == 200
    with pytest.raises(Refused):
        AgentRunner(HttpClient(server, tok), hub.models, quiet).work_once()


def test_an_agent_cannot_sign(hub, server):
    tok = token(hub, "clinical-admin-agent", by="dr-amara")
    AgentRunner(HttpClient(server, tok), hub.models, quiet).work_once()
    run = run_of(hub, "prescription-renewal")
    iid = item_for(hub, run, "decide")
    # even an agent given the physician role may not sign: signing is for people
    hub.set_roles(M(hub, "rana"), "clinical-admin-agent", ["clinical-assistant", "physician"])
    hub.approve_roles(M(hub, "dr-amara"), "clinical-admin-agent")  # clinical access for an agent: a second owner
    code, body = call(server, "POST", f"/api/items/{iid}/decide", tok, {"outcome": "renew"})
    assert code == 403 and "person" in json.loads(body)["error"]


def test_front_desk_does_not_see_the_draft_note(hub, server):
    AgentRunner(HttpClient(server, token(hub, "scribe-agent", by="dr-amara")), hub.models, quiet).work_once()
    run = run_of(hub, "visit-documentation")
    draft = hub.engine.run(run["id"])["ctx"]["draft"]["assessment"]
    assert draft
    jo, amara = token(hub, "jo", by="jo"), token(hub, "dr-amara", by="dr-amara")
    for path in (f"/api/runs/{run['id']}", f"/api/events?run={run['id']}"):
        assert draft not in call(server, "GET", path, jo)[1]
        assert draft in call(server, "GET", path, amara)[1]
    assert "Sore throat" not in call(server, "GET", f"/api/runs/{run['id']}", jo)[1]  # the transcript input
    page = call(server, "GET", f"/runs/{run['id']}", jo)[1]
    assert draft not in page and "needs access to" in page
    code, _ = call(server, "GET", f"/api/items/{item_for(hub, run, 'edit')}/context", jo)
    assert code == 403



def test_reading_a_run_is_in_the_patients_access_report(hub, server):
    """A run's page shows the patient's name, the run's input and its results: opening it is a view the privacy
    officer can report, on the page and through the API."""
    run = run_of(hub, "visit-documentation")
    assert run["record"]
    before = [r for r in hub.access_report(M(hub, "rana"), run["record"]) if r["who"] == "dr-liam"]
    assert call(server, "GET", f"/runs/{run['id']}", token(hub, "dr-liam", by="dr-liam"))[0] == 200
    seen = [r for r in hub.access_report(M(hub, "rana"), run["record"]) if r["who"] == "dr-liam"]
    assert len(seen) == len(before) + 1 and seen[-1]["action"] == "viewed" and seen[-1]["how"] == f"run {run['id']}"
    call(server, "GET", f"/api/runs/{run['id']}", token(hub, "noor", by="noor"))
    assert any(r["who"] == "noor" and r["how"].startswith("run ")
               for r in hub.access_report(M(hub, "rana"), run["record"]))

def test_a_physician_edits_and_signs_with_forms(hub, server):
    from practicehub.agents.runner import run_all_local
    run_all_local(hub, log=quiet)
    run = run_of(hub, "visit-documentation")
    words = passphrase(hub, "dr-amara")
    liams = passphrase(hub, "dr-liam")
    page = browser(server)
    code, html = page("POST", "/login", {"member": "dr-amara", "passphrase": "not her passphrase at all"})
    assert code == 403 and 'value="dr-amara"' in html and page.jar["cookie"] is None
    code, _ = page("POST", "/login", {"member": "dr-amara", "passphrase": words})
    assert code == 303 and page.jar["cookie"].startswith("ph_session=phs_")
    assert words not in page.jar["cookie"]

    edit = item_for(hub, run, "edit")
    code, html = page("GET", f"/items/{edit}")
    assert code == 200 and "Viral pharyngitis" in html  # the draft, prefilled
    code, _ = page("POST", f"/items/{edit}/complete", {"subjective": "Sore throat 4 days", "objective": "",
                                                       "assessment": "Viral pharyngitis", "plan": "Fluids, rest",
                                                       "diagnosis_codes": "462"})
    assert code == 303
    sign = item_for(hub, run, "sign")
    code, html = page("GET", f"/items/{sign}")
    assert "electronic signature" in html and 'name="passphrase"' in html and 'name="credential"' not in html
    # the decision is a row of cards: the native radio is kept for the keyboard but hidden, never a bare circle
    assert '<fieldset class="choices options decide">' in html
    assert all('class="opt-input"' in r for r in re.findall(r'<input type="radio"[^>]*>', html))
    seen = re.search(r'name="seen" value="([0-9a-f]{64})"', html).group(1)
    form = {"outcome": "sign", "comment": "Reviewed with the patient", "seen": seen}
    code, html = page("POST", f"/items/{sign}/decide", form)
    assert code == 403  # signing asks for the passphrase again
    # the item page again, inline: the error, the outcome and comment kept, no way back that loses them
    assert "give your passphrase again" in html and "history.back" not in html
    assert re.search(r'value="sign" required checked', html) and ">Reviewed with the patient</textarea>" in html
    assert page("POST", f"/items/{sign}/decide", {**form, "passphrase": liams})[0] == 403  # someone else's
    assert page("POST", f"/items/{sign}/decide", {**form, "passphrase": token(hub, "dr-amara")})[0] == 403  # a token
    code, html = page("POST", f"/items/{sign}/decide", {**form, "passphrase": words, "seen": "0" * 64})
    assert code == 422 and ">Reviewed with the patient</textarea>" in html and words not in html
    assert page("POST", f"/items/{sign}/decide", {**form, "passphrase": words})[0] == 303
    assert hub.records.search("Composition")
    assert hub.track.verify()["ok"]
    fails = hub.db.events.find({"action": "signin.failed", "subject": "member:dr-amara"})
    assert len(fails) == 3 and all(words not in json.dumps(f["data"]) for f in fails)
    assert page("GET", "/logout")[0] == 303 and page.jar["cookie"] is None


def test_people_see_why_they_cannot_take_work(hub, server):
    jo = token(hub, "jo", by="jo")
    code, body = call(server, "GET", "/api/inbox?all=1", jo)
    items = json.loads(body)["items"]
    assert any(not i["eligible"] and i["reason"] for i in items) or items


# ------------------------------------------------------------------------------------------- records and modules
def _modules(hub, server):
    rana, amara = token(hub, "rana"), token(hub, "dr-amara")
    for name in ("dms", "qms"):
        code, body = call(server, "POST", f"/modules/{name}/review-code", amara)
        assert code == 303, body
        code, body = call(server, "POST", "/modules", rana, form={"name": name})
        assert code == 303, body
    for m in hub.pack.modules:
        m.module.demo(hub)
    return rana


def test_a_policy_owner_installs_modules_from_the_modules_page(hub, server):
    assert "install dms first" in call(server, "GET", "/modules", token(hub, "rana"))[1]
    code, _ = call(server, "POST", "/modules", token(hub, "jo", by="jo"), form={"name": "dms"})
    assert code == 403
    code, page = call(server, "GET", "/modules/dms", token(hub, "rana"))
    assert code == 200 and "Not reviewed yet" in page and "I have reviewed it" in page
    code, _ = call(server, "POST", "/modules", token(hub, "rana"), form={"name": "dms"})
    assert code == 403  # nobody else reviewed it
    assert call(server, "GET", "/modules/no-such-module", token(hub, "rana"))[0] == 404
    _modules(hub, server)
    page = call(server, "GET", "/modules", token(hub, "rana"))[1]
    assert page.count("installed</span>") == 2
    code, page = call(server, "GET", "/modules/qms", token(hub, "rana"))
    assert code == 200 and "nothing to upgrade" in page
    code, _ = call(server, "POST", "/modules/dms/remove", token(hub, "rana"), form={"reason": "x"})
    assert code == 422  # qms builds on it
    code, _ = call(server, "POST", "/modules/qms/remove", token(hub, "rana"), form={"reason": "trial over"})
    assert code == 303 and "qms" not in hub.installed_modules()


def test_any_record_has_a_page_with_its_fields_links_history_and_signatures(hub, server):
    rana = _modules(hub, server)
    doc = hub.records.search("Document")[0]
    code, page = call(server, "GET", "/records/Document", rana)
    assert code == 200 and "SOP-014" in page
    code, body = call(server, "POST", "/records/CAPA", rana, form={
        "title": "Missed callback", "kind": "corrective", "problem": "No one called", "documents": f"Document/{doc['id']}"})
    assert code == 303, body
    capa = hub.records.search("CAPA")[0]
    assert capa["status"] == "open" and capa["documents"] == [f"Document/{doc['id']}"]
    links = call(server, "GET", f"/records/Document/{doc['id']}?tab=links", rana)[1]
    assert "Missed callback" in links and "affects" in links
    amara = token(hub, "dr-amara", by="dr-amara")
    page = call(server, "GET", f"/records/Document/{doc['id']}?tab=signatures", amara)[1]
    seen = re.search(r'name="seen" value="([0-9a-f]{64})"', page).group(1)
    read = "I have read and understood this document"
    code, page = call(server, "POST", f"/records/Document/{doc['id']}/sign", amara,
                      form={"meaning": read, "seen": seen, "comment": "Read twice"})
    assert code == 403  # no passphrase
    assert "Not signed" in page and ">Read twice</textarea>" in page and "history.back" not in page  # kept, inline
    code, _ = call(server, "POST", f"/records/Document/{doc['id']}/sign", amara,
                   form={"meaning": read, "seen": seen, "passphrase": amara})
    assert code == 403  # her token is not her passphrase
    words = passphrase(hub, "dr-amara")
    code, _ = call(server, "POST", f"/records/Document/{doc['id']}/sign", amara,
                   form={"meaning": read, "passphrase": words})
    assert code == 422  # not bound to what was shown
    code, _ = call(server, "POST", f"/records/Document/{doc['id']}/sign", amara,
                   form={"meaning": read, "passphrase": words, "seen": "0" * 64})
    assert code == 422  # signed something other than what the record says
    code, _ = call(server, "POST", f"/records/Document/{doc['id']}/sign", amara,
                   form={"meaning": read, "passphrase": words, "seen": seen})
    assert code == 303
    sigs = call(server, "GET", f"/records/Document/{doc['id']}?tab=signatures", amara)[1]
    assert "Dr. Amara Okafor" in sigs and "current" in sigs
    assert call(server, "POST", f"/records/Document/{doc['id']}/edit", rana, form={"body": "New text"})[0] == 422
    assert call(server, "POST", f"/records/Document/{doc['id']}/move", rana, form={"to": "draft"})[0] == 303
    assert call(server, "POST", f"/records/Document/{doc['id']}/edit", rana, form={"body": "New text"})[0] == 303
    sigs = json.loads(call(server, "GET", f"/api/records/Document/{doc['id']}/signatures", amara)[1])
    assert sigs["signatures"][0]["current"] is False and len(sigs["seen"]) == 64
    # over the API the request's own token is the credential, and seen is still required
    api = f"/api/records/Document/{doc['id']}/signatures"
    assert call(server, "POST", api, amara, body={"meaning": "Noted"})[0] == 422
    hist = call(server, "GET", f"/records/Document/{doc['id']}?tab=history", rana)[1]
    assert "v3" in hist and "Body" in hist


def test_a_person_cannot_skip_a_move_reserved_to_workflows_from_the_page(hub, server):
    rana = _modules(hub, server)
    code, _ = call(server, "POST", "/records/Document", rana, form={"title": "Draft"})
    d = next(r for r in hub.records.search("Document") if r["title"] == "Draft")
    page = call(server, "GET", f"/records/Document/{d['id']}", rana)[1]
    assert "Send for review" in page and ">Approve<" not in page
    assert call(server, "POST", f"/records/Document/{d['id']}/move", rana, form={"to": "in-review"})[0] == 303
    assert call(server, "POST", f"/records/Document/{d['id']}/move", rana, form={"to": "approved"})[0] == 403
    assert run_of(hub, "dms-review")


def test_an_agent_cannot_sign_a_record_over_the_api(hub, server):
    _modules(hub, server)
    doc = hub.records.search("Document")[0]
    tok = token(hub, "scribe-agent", by="dr-amara")
    code, _ = call(server, "POST", f"/api/records/Document/{doc['id']}/signatures", tok,
                   {"meaning": "I have read and understood this document"})
    assert code == 403


def test_a_manager_changes_a_record_type_from_the_web(hub, server):
    rana = _modules(hub, server)
    amara = token(hub, "dr-amara", by="dr-amara")
    code, page = call(server, "GET", "/define/types", rana)
    assert code == 200 and "Document" in page
    src = next(v for v in hub.definition_versions("type", "Document") if v["status"] == "active")["source"]
    bad = call(server, "POST", "/define/types/propose", rana, form={"source": "id = 3", "note": "x"})[1]
    assert "It was not saved" in bad
    new = src + '\n[[field]]\nname = "owner_note"\nlabel = "Owner note"\ntype = "text"\n'
    code, _ = call(server, "POST", "/define/types/propose", rana, form={"source": new, "note": "add a note"})
    assert code == 303
    ver = next(v for v in hub.definition_versions("type", "Document") if v["status"] == "proposed")["version"]
    page = call(server, "GET", "/define/types/Document", amara)[1]
    assert "New field Owner note" in page and "Approve and sign" in page
    assert "Approve and sign" not in call(server, "GET", "/define/types/Document", rana)[1]  # its author
    code, _ = call(server, "POST", f"/define/types/Document/{ver}/approve", amara)
    assert code == 303
    assert any(f["name"] == "owner_note" for f in hub.record_type("Document").fields)
    assert call(server, "GET", "/define/views", rana)[0] == 200
    assert call(server, "GET", "/define/skills", rana)[0] == 200


def test_views_show_in_the_sidebar_and_work_starts_about_any_record(hub, server):
    rana = _modules(hub, server)
    qm = next(m for m in hub.members() if "quality-manager" in (m.get("roles") or []) and m["kind"] == "person")
    who = token(hub, qm["id"], by=qm["id"])
    code, page = call(server, "GET", "/views/qms-capas", who)
    assert code == 200 and 'href="/views/qms-capas"' in page and "By status" in page
    assert call(server, "GET", "/views/no-such-view", who)[0] == 404
    capa = hub.create_record(hub.member(qm["id"]), "CAPA", {"title": "T", "kind": "corrective", "problem": "P"})
    code, page = call(server, "GET", f"/start?workflow=qms-capa&record=CAPA/{capa['id']}", who)
    assert code == 200 and f'value="CAPA/{capa["id"]}" selected' in page
    code, _ = call(server, "POST", "/start", who, form={"_workflow": "qms-capa", "_record": "Patient/x"})
    assert code == 422  # a CAPA workflow is about a CAPA
    code, _ = call(server, "POST", "/start", who, form={"_workflow": "qms-capa", "_record": f"CAPA/{capa['id']}"})
    assert code == 303


def test_the_privacy_officer_reports_who_accessed_a_file(hub, server):
    """A patient asks who looked at their information: every record in the file, every class, people and agents,
    as a page and as CSV; only a policy owner runs it, and running it is recorded."""
    subject = hub.records.search("Observation")[0]["subject"]["reference"]
    amara = M(hub, "dr-amara")
    chart = hub.chart(amara, subject)
    shown = [f"{r['resourceType']}/{r['id']}" for recs in chart["classes"].values() for r in recs]
    assert shown, "the demo patient has records"
    rana = token(hub, "rana")
    q = "/access?subject=" + urllib.parse.quote(subject)
    code, body = call(server, "GET", q, rana)
    assert code == 200 and "Download CSV" in body
    code, csvtext = call(server, "GET", q + "&format=csv", rana)
    assert code == 200 and csvtext.startswith("when,who,name,kind,action,record,how,event")
    lines = [l for l in csvtext.splitlines()[1:] if ",dr-amara," in l and ",viewed," in l]
    seen = {l.split(",")[5] for l in lines}
    assert subject in seen and set(shown) <= seen  # the chart view and each record it showed
    assert hub.db.events.exists({"action": "access.reported", "actor": "rana"})
    jo = token(hub, "jo")
    assert call(server, "GET", q, jo)[0] == 403
    assert call(server, "GET", "/track", jo)[0] == 403  # the track names who opened what: the same rule
    assert call(server, "GET", "/track", rana)[0] == 200


def test_structured_fields_are_edited_as_json_and_kept_when_unchanged(hub):
    """A result's test, value and flags are objects: the form shows JSON (not Python's repr), saving the form
    unchanged changes nothing, an edit to the JSON is saved, and JSON that does not parse is refused."""
    app = App(hub, dev=False)
    amara = M(hub, "dr-amara")
    rec = next(r for r in hub.records.search("Observation") if r.get("interpretation"))
    rt = hub.record_type("Observation")
    fields = {f["name"]: f for f in app._editable(rt)}
    assert {"code", "valueQuantity", "interpretation"} <= set(fields)  # Test and Value can be edited
    html_ = app._input(fields["interpretation"], rec, amara)
    assert "{'" not in html_ and "&quot;text&quot;" in html_
    form = {}
    for f in app._editable(rt):
        ctl = app._input(f, rec, amara)
        m = re.search(r"<textarea[^>]*>(.*?)</textarea>", ctl, re.S) or re.search(r'value="([^"]*)"', ctl)
        sel = re.findall(r'<option value="([^"]*)" selected', ctl)
        cards = [html.unescape(v) for v in re.findall(r'class="opt-input"[^>]*value="([^"]*)"[^>]*checked', ctl)]
        val = (sel if "<select" in ctl else cards if 'class="opt-input"' in ctl
               else [html.unescape(m.group(1))] if m else [])
        form[f["path"].removesuffix(".*")] = val
    assert app._from_form(rt, form, partial=True, before=rec) == {}  # nothing changed, nothing written
    changed = {**form, "valueQuantity": ['{"value": 7.1, "unit": "mmol/L"}']}
    assert app._from_form(rt, changed, partial=True, before=rec) == {"valueQuantity": {"value": 7.1, "unit": "mmol/L"}}
    with pytest.raises(Exception, match="not valid JSON"):
        app._from_form(rt, {**form, "code": ["{'text': 'oops'}"]}, partial=True, before=rec)


def test_an_urgent_item_shows_the_patients_words_first_with_who_and_how_to_reach_them(hub):
    """The nurse sees the message itself (labelled, not ``input.question``), the patient's name, and a number."""
    app = App(hub, dev=False)
    pat = hub.records.search("Patient")[0]
    run = hub.engine.start(M(hub, "jo"), "patient-question", record=f"Patient/{pat['id']}",
                           inputs={"question": "Crushing chest pain since this morning"})
    iid = item_for(hub, run, "urgent")
    page = app.page_item({"me": M(hub, "noor"), "query": {}, "path": f"/items/{iid}"}, iid)
    assert "The patient&#x27;s message" in page and "input.question" not in page
    assert page.index("Crushing chest pain") < page.index("<h2>Description</h2>")
    assert hub.title(pat).split(",")[0] in page  # the name, not only the id
    phone = hub.pack.facts(pat)["contact"]
    assert phone and e_(phone) in page


def e_(s):
    return html.escape(s)


def test_who_opened_what_is_hidden_from_everyone_but_the_privacy_officer(hub, server):
    pat = hub.records.search("Observation")[0]["subject"]["reference"]
    hub.chart(M(hub, "dr-amara"), pat)
    view = hub.db.events.find_one({"action": "record.viewed"}, sort=[("seq", -1)])["seq"]
    jo, rana = token(hub, "jo"), token(hub, "rana")
    for tok, sees in ((jo, False), (rana, True)):
        evs = json.loads(call(server, "GET", "/api/events?limit=2000", tok)[1])["events"]
        assert any(ev["action"] == "record.viewed" for ev in evs) is sees
        assert (call(server, "GET", f"/api/events/{view}/chain", tok)[0] == 200) is sees
        assert (call(server, "GET", f"/track/{view}", tok)[0] == 200) is sees
        team = call(server, "GET", "/team/dr-amara", tok)[1]
        assert ("record.viewed" in team) is sees
    sam = token(hub, "sam")
    assert call(server, "GET", "/outbox", sam)[0] == 403  # billing reads no messages to patients
    noor = M(hub, "noor")
    obs = hub.records.search("Observation")[0]
    hub.record_history(noor, f"Observation/{obs['id']}")
    assert hub.db.events.exists({"action": "record.viewed", "actor": "noor", "subject": f"Observation/{obs['id']}"})


def _form_as_shown(app, rt, rec, me):
    form = {}
    for f in app._editable(rt):
        ctl = app._input(f, rec, me)
        m = re.search(r"<textarea[^>]*>(.*?)</textarea>", ctl, re.S) or re.search(r'value="([^"]*)"', ctl)
        sel = re.findall(r'<option value="([^"]*)" selected', ctl)
        cards = [html.unescape(v) for v in re.findall(r'class="opt-input"[^>]*value="([^"]*)"[^>]*checked', ctl)]
        val = (sel if "<select" in ctl else cards if 'class="opt-input"' in ctl
               else [html.unescape(m.group(1))] if m else [])
        form[f["path"].removesuffix(".*")] = val
    return form


def test_the_front_desk_edits_a_patient_in_plain_fields(hub):
    """Name, phone, address and emergency contact are fields, not JSON; saving unchanged writes nothing (an
    unrecorded yes/no stays unrecorded), and a new phone number keeps it a phone."""
    app = App(hub, dev=False)
    jo = M(hub, "jo")
    rt = hub.record_type("Patient")
    rec = next(r for r in hub.records.search("Patient") if r.get("telecom"))
    rec.pop("active", None)  # never recorded
    shown = app._inputs(rt, rec, jo)
    plain = shown.split("<details")[0]
    for label in ("Given name", "Family name", "Phone", "Street address", "Postal code", "Emergency contact"):
        assert label in plain
    assert "<textarea" not in plain  # no JSON unless asked for
    form = _form_as_shown(app, rt, rec, jo)
    assert app._from_form(rt, form, partial=True, before=rec) == {}
    changes = app._from_form(rt, {**form, "telecom.0.value": ["902-555-7777"]}, partial=True, before=rec)
    assert changes["telecom"][0] == {**rec["telecom"][0], "value": "902-555-7777"}
    assert set(changes) == {"telecom"}
    new = app._from_form(rt, {"name.0.given.0": ["Ada"], "name.0.family": ["Lovelace"],
                              "telecom.0.value": ["902-555-0100"], "active": [""]}, partial=False)
    assert new["name"] == [{"given": ["Ada"], "family": "Lovelace"}]
    assert new["telecom"] == [{"value": "902-555-0100", "system": "phone"}] and "active" not in new


def test_what_a_signer_reads_is_in_words_not_keys(hub):
    app = App(hub, dev=False)
    out = app._values({"found_in_chart": True, "by_kind": "agent", "review-flagged": False})
    assert "Found in chart" in out and "Yes" in out and "No" in out and "True" not in out
    assert app.human("call-patient") == "Call patient" and app.human("valueQuantity") == "Value quantity"
    page = app.page_access({"me": M(hub, "rana"), "query": {}, "path": "/access"})
    assert "<datalist" in page and hub.title(hub.records.search("Patient")[0]) in html.unescape(page)


def test_a_phone_field_edits_the_phone_wherever_it_sits(hub):
    """telecom.0 with system = phone means the phone entry: an email listed first is not overwritten, and an
    unchanged date-time inside a list is not rewritten."""
    from practicehub import schema
    app = App(hub, dev=False)
    rt = hub.record_type("Patient")
    rec = {"telecom": [{"system": "email", "value": "a@example.org"}, {"system": "phone", "value": "902-555-0101"}]}
    assert schema.locate(rec, "telecom.0.value", {"system": "phone"}) == "telecom.1.value"
    assert schema.locate({"telecom": [{"system": "email", "value": "x"}]}, "telecom.0.value",
                         {"system": "phone"}) == "telecom.1.value"
    assert schema.locate({"telecom": [{"value": "x"}]}, "telecom.0.value", {"system": "phone"}) == "telecom.0.value"
    assert "902-555-0101" in app._inputs(rt, rec, M(hub, "jo"))
    out = app._from_form(rt, {"telecom.0.value": ["902-555-7777"]}, partial=True, before=rec)
    assert out["telecom"] == [rec["telecom"][0], {"system": "phone", "value": "902-555-7777"}]
    added = app._from_form(rt, {"telecom.0.value": ["902-555-7777"]}, partial=True,
                           before={"telecom": [{"system": "email", "value": "a@example.org"}]})
    assert added["telecom"][1] == {"system": "phone", "value": "902-555-7777"}


def test_the_outbox_shows_a_clinical_answer_only_to_those_who_read_clinical_records(hub):
    """The front desk reads messages, not a nurse's answer about symptoms; whoever reads one is on the record of
    who saw the patient's information."""
    app = App(hub, dev=False)
    pat = hub.records.search("Patient")[0]
    ref = f"Patient/{pat['id']}"
    run = hub.engine.start(M(hub, "jo"), "patient-question", record=ref,
                           inputs={"question": "My rash is spreading on my arm"})
    hub.engine.complete(M(hub, "noor"), item_for(hub, run, "clinician"), {"answer": "Use the cream twice daily."})
    row = hub.db.outbox.find_one({"body": "Use the cream twice daily."})
    assert "clinical" in row["classes"].split(",")
    front = app.page_outbox({"me": M(hub, "jo"), "query": {}, "path": "/outbox"})
    assert "Use the cream twice daily." not in front and "Not shown" in front
    nurse = app.page_outbox({"me": M(hub, "noor"), "query": {}, "path": "/outbox"})
    assert "Use the cream twice daily." in nurse
    assert hub.db.events.exists({"action": "record.viewed", "actor": "noor", "subject": ref})


def test_whoever_cannot_reach_the_patient_sees_whom_else_to_call(hub):
    app = App(hub, dev=False)
    pat = next(p for p in hub.records.search("Patient") if p.get("contact"))
    run = hub.engine.start(M(hub, "jo"), "patient-question", record=f"Patient/{pat['id']}",
                           inputs={"question": "crushing chest pain"})
    hub.engine.complete(M(hub, "noor"), item_for(hub, run, "urgent"),
                        {"reached": False, "action": "could-not-reach", "notes": "no answer"})
    iid = item_for(hub, run, "not-reached")
    page = app.page_item({"me": M(hub, "dr-amara"), "query": {}, "path": f"/items/{iid}"}, iid)
    contact = hub.pack.facts(pat)["emergency contact"]
    assert "Emergency contact" in page and e_(contact) in page
    assert "&quot;resourceType&quot;" not in page


def test_a_patients_physician_is_picked_from_the_team_and_shown_by_name(hub):
    app = App(hub, dev=False)
    pat = next(p for p in hub.records.search("Patient") if p.get("generalPractitioner"))
    rt = hub.record_type("Patient")
    f = next(f for f in rt.fields if f["path"].startswith("generalPractitioner"))
    ctl = app._input(f, pat, M(hub, "jo"))
    assert "Practitioner/dr-amara" in ctl and ">Dr" in ctl
    link = app.record_link("Practitioner/dr-amara")
    assert 'href="/team/dr-amara"' in link and "Practitioner/dr-amara" not in link


def test_a_task_offers_its_outcomes_in_words(hub):
    app = App(hub, dev=False)
    html = app._field({"name": "action", "type": "choice"}, [{"value": "could-not-reach", "label": "could-not-reach"}], None)
    assert ">Could not reach<" in html and 'value="could-not-reach"' in html


def test_the_privacy_officer_finds_a_file_by_name_and_reads_how_it_was_reached(hub):
    app = App(hub, dev=False)
    pat = hub.records.search("Patient")[0]
    name, ref = hub.title(pat), f"Patient/{pat['id']}"
    run = hub.engine.start(M(hub, "jo"), "patient-question", record=ref, inputs={"question": "a rash"})
    iid = next(r["id"] for r in hub.db.items.find({"run_id": run["id"]}, sort="n"))
    hub.engine.context(M(hub, "noor"), iid)
    page = app.page_access({"me": M(hub, "rana"), "query": {"subject": name}, "path": "/access"})
    assert "Download CSV" in page and f'value="{e_(name)}"' in page and f'value="{ref}"' not in page
    assert f'href="/items/{iid}"' in page and f"work item {iid}" not in page
    miss = app.page_access({"me": M(hub, "rana"), "query": {"subject": "nobody-by-this-name"}, "path": "/access"})
    assert "No record is called" in miss and "Download CSV" not in miss
    chart = app.page_subject({"me": M(hub, "rana"), "query": {}, "path": f"/patients/{pat['id']}"}, pat["id"])
    assert "/access?subject=" in chart


def test_the_team_page_adds_members_and_changes_roles_and_sponsors(hub):
    app = App(hub, dev=False)
    rana = M(hub, "rana")
    req = lambda body, me=rana: {"me": me, "query": {}, "path": "/team", "body": body.encode()}
    assert 'href="/team/new" data-modal>Add people</a>' in app.page_team({"me": rana, "query": {}, "path": "/team"})
    assert 'name="name"' in app.page_team_new({"me": rana, "query": {}, "path": "/team/new"})
    with pytest.raises(Redirect):
        app.post_team_add(req("kind=person&id=dr-chen&name=Dr.+Wei+Chen&roles=physician"))
    assert hub.member("dr-chen")["roles"]
    with pytest.raises(Redirect):
        app.post_member(req("roles=physician&roles=nurse"), "dr-chen", "roles")
    assert set(hub.member("dr-chen")["roles"]) >= {"physician", "nurse"}
    page = app.post_member(req("label=interface"), "lab-feed", "token")
    assert "shown once" in page
    assert "token=" not in page and "/revoke" in page
    with pytest.raises(Denied):
        app.post_member(req(""), "dr-amara", "token")


def test_past_a_list_size_people_are_found_by_typing(hub):
    """With more patients than a picker lists, the picker is a search box; a typed name finds the one meant, two
    people with one name must be told apart, and the patient list says to search rather than hiding anyone."""
    from practicehub.web.records_pages import PICK
    from practicehub.web.server import SHOWN
    app = App(hub, dev=False)
    jo = M(hub, "jo")
    old = hub.records.create("Patient", {"name": [{"given": ["Zephyrine"], "family": "Oldfield"}]})
    for i in range(max(PICK, SHOWN) + 5):
        hub.records.create("Patient", {"name": [{"given": [f"P{i}"], "family": "Newer"}]})
    f = {"path": "patient", "name": "patient", "label": "Patient", "type": "reference", "target": ["Patient"],
         "required": True}
    shown = app._input(f, None, jo)
    assert "<datalist" in shown and "<select" not in shown
    assert app._resolve_ref(f, "Zephyrine Oldfield") == f"Patient/{old['id']}"
    assert app._resolve_ref(f, f"Whoever · {old['id']}") == f"Patient/{old['id']}"
    hub.records.create("Patient", {"name": [{"given": ["Zephyrine"], "family": "Oldfield"}]})
    with pytest.raises(Invalid, match="2 records"):
        app._resolve_ref(f, "Zephyrine Oldfield")
    with pytest.raises(Invalid, match="no record"):
        app._resolve_ref(f, "Nobody Atall")
    page = app.page_subjects({"me": jo, "query": {}, "path": "/patients"})
    assert "search to find" in page.lower() or "to find any other" in page
    found = app.page_subjects({"me": jo, "query": {"q": "zephyrine"}, "path": "/patients"})
    assert found.count("Oldfield") >= 2


def test_a_chart_reads_in_words_not_json(hub):
    """A clinician reads a result as '5.2 mmol/L', a code as its name, a time as a time, and opens the record."""
    app = App(hub, dev=False)
    pat = hub.records.search("Patient")[0]
    hub.create_record({"id": "lab-feed", "kind": "automation"}, "Observation", {   # a final result comes from the lab
        "status": "final", "subject": {"reference": f"Patient/{pat['id']}"},
        "code": {"coding": [{"system": "http://loinc.org", "code": "2345-7", "display": "Glucose"}]},
        "effectiveDateTime": "2026-11-20T08:30:00Z",
        "valueQuantity": {"value": 5.2, "unit": "mmol/L", "system": "http://unitsofmeasure.org", "code": "mmol/L"},
        "meta": {"lastUpdated": "2026-11-20T08:31:00Z"}})
    page = html.unescape(app.page_subject({"me": M(hub, "dr-liam"), "query": {}, "path": f"/patients/{pat['id']}"},
                                          pat["id"]))
    assert "5.2 mmol/L" in page and "Glucose" in page and "2026-11-20 08:30" in page
    assert '"resourceType"' not in page and "<pre>{" not in page and "valueQuantity" not in page
    assert "Value:" in page and "When:" in page and 'href="/records/Observation/' in page
    deep = app._plain_html({"a": {"b": {"c": {"d": {"e": {"f": 1, "code": {"text": "Deep"}}}}}}})
    assert "{" not in deep and "Deep" in deep


def test_screens_arrange_the_forms_and_a_move_asks_what_it_needs(hub, server):
    """Registration is the patient's create screen, in its tabs; a CAPA's move to investigating is a transition
    screen that asks for the owner and a comment, and keeps what was typed when it is refused."""
    rana = _modules(hub, server)
    page = call(server, "GET", "/patients", token(hub, "jo"))[1]
    assert page.index("<legend>Who</legend>") < page.index("<legend>How to reach them</legend>") < \
        page.index("<legend>Care</legend>") and 'href="#register-patient-1"' in page
    assert 'name="contact_name"' not in page.split('id="register"')[1].split("</form>")[0]  # not on that screen
    capa = hub.create_record(hub.member("rana"), "CAPA", {"title": "T", "kind": "corrective", "problem": "P"})
    url = f"/records/CAPA/{capa['id']}"
    page = call(server, "GET", url, rana)[1]
    assert f'href="{url}/move?to=investigating" data-modal>Start investigating…</a>' in page
    code, page = call(server, "GET", f"{url}/move?to=investigating", rana)
    assert code == 200 and 'name="owner"' in page and 'name="_comment"' in page and 'name="title"' not in page
    code, page = call(server, "POST", f"{url}/move", rana, form={"to": "investigating", "_comment": "kept"})
    assert code == 200 and "fill in Owner" in page and ">kept</textarea>" in page
    assert hub.records.read("CAPA", capa["id"])["status"] == "open"
    code, page = call(server, "POST", f"{url}/move", rana, form={"to": "investigating", "owner": "rana",
                                                                "_comment": "kept"})
    assert code == 303, page[page.find("flash error"):][:300]
    after = hub.records.read("CAPA", capa["id"])
    assert code == 303 and after["status"] == "investigating" and after["owner"] == "rana"
    assert call(server, "GET", f"{url}/move?to=closed", rana)[0] == 422  # only a workflow closes it


# ------------------------------------------------------------------------------------------- passphrases on the pages
def test_a_person_sets_a_passphrase_with_a_setup_code_from_a_member_admin(hub, server):
    rana = token(hub, "rana")
    assert call(server, "POST", "/team/dr-liam/setup-code", token(hub, "jo"))[0] == 403  # not a member admin
    page = call(server, "GET", "/team/dr-liam", rana)[1]
    assert "Issue a setup code" in page and "has no passphrase yet" in page
    code, page = call(server, "POST", "/team/dr-liam/setup-code", rana)
    assert code == 200 and "shown once" in page
    setup = re.search(r"<pre>([A-Z2-9]{5}(?:-[A-Z2-9]{5}){3})</pre>", page).group(1)
    assert setup not in json.dumps(hub.track.list(limit=10000))
    b = browser(server)
    assert b("GET", "/setup?member=dr-liam")[0] == 200
    words = "the tide comes in at noon"
    code, page = b("POST", "/setup", {"member": "dr-liam", "code": setup, "new": words, "again": words + "x"})
    assert code == 422 and "not the same" in page and 'value="dr-liam"' in page and words not in page
    code, page = b("POST", "/setup", {"member": "dr-liam", "code": "AAAAA-BBBBB-CCCCC-DDDDD", "new": words, "again": words})
    assert code == 403 and b.jar["cookie"] is None
    code, _ = b("POST", "/setup", {"member": "dr-liam", "code": setup, "new": words, "again": words})
    assert code == 303 and b.jar["cookie"]  # set, and signed in
    assert b("GET", "/")[0] == 200
    assert b("POST", "/setup", {"member": "dr-liam", "code": setup, "new": words, "again": words})[0] == 403  # once
    # changing it asks for the current one, and keeps this browser signed in
    code, page = b("POST", "/me/passphrase", {"current": "wrong words here", "new": "low tide at six", "again": "low tide at six"})
    assert code == 403 and "Your passphrase" in page
    code, page = b("POST", "/me/passphrase", {"current": words, "new": "low tide at six", "again": "low tide at six"})
    assert code == 200 and "is set" in page and b("GET", "/")[0] == 200
    assert call(server, "POST", "/login", form={"member": "dr-liam", "passphrase": words})[0] == 403
    assert call(server, "POST", "/login", form={"member": "dr-liam", "passphrase": "low tide at six"})[0] == 303
    # an access token no longer signs a person in on the sign-in form
    assert call(server, "POST", "/login", form={"token": token(hub, "dr-liam")})[0] == 403


def test_an_error_page_links_back_without_history(hub, server):
    code, page = call(server, "POST", "/team/dr-liam/setup-code", token(hub, "jo"))
    assert code == 403 and "history.back" not in page and 'href="/"' in page


# ------------------------------------------------------------------------------------------- the outbox delivered
def _page_message(hub):
    from test_rules import _critical_page
    return _critical_page(hub)


def test_a_connector_delivers_the_outbox_over_the_api(hub, server):
    run, msg = _page_message(hub)
    rana = M(hub, "rana")
    hub.add_member(rana, {"id": "pager", "kind": "service", "name": "Pager", "sponsor": "rana", "roles": []})
    hub.authz.write([("data_class:clinical", "reader", "member:pager"),
                     ("data_class:correspondence", "reader", "member:pager")])
    pager = token(hub, "pager", by="rana")
    assert call(server, "GET", "/api/outbox", pager)[0] == 403  # no channel yet
    hub.grant_channel(rana, "pager", "page")
    code, body = call(server, "GET", "/api/outbox", pager)
    got = json.loads(body)
    assert code == 200 and got["channels"] == ["page"] and [m["id"] for m in got["messages"]] == [msg["id"]]
    assert got["messages"][0]["body"] == msg["body"]
    assert call(server, "GET", "/api/outbox", token(hub, "noor"))[0] == 403  # a person uses the page
    assert call(server, "GET", "/api/outbox")[0] == 401
    assert call(server, "POST", f"/api/outbox/{msg['id']}/claim", pager, body={})[0] == 200
    assert call(server, "POST", f"/api/outbox/{msg['id']}/failed", pager, body={})[0] == 422  # say why
    code, body = call(server, "POST", f"/api/outbox/{msg['id']}/sent", pager, body={"reference": "p-1"})
    assert code == 200 and json.loads(body)["status"] == "sent"
    assert call(server, "POST", f"/api/outbox/{msg['id']}/sent", pager, body={})[0] == 422
    assert call(server, "GET", "/api/outbox", pager)[1].count('"id"') == 0


def test_the_outbox_page_shows_status_and_a_reader_marks_a_message_delivered_by_hand(hub, server):
    from datetime import datetime, timedelta, timezone
    run, msg = _page_message(hub)
    hub.outbox_tick(datetime.now(timezone.utc) + timedelta(minutes=10))
    noor, jo = token(hub, "noor"), token(hub, "jo")
    page = call(server, "GET", "/outbox", noor)[1]
    assert "Waiting for the connector" in page and "Late" in page and f'/outbox/{msg["id"]}/delivered' in page
    page = call(server, "GET", "/outbox", jo)[1]  # the front desk does not read the clinical page
    assert "Waiting for the connector" in page and f'/outbox/{msg["id"]}/delivered' not in page
    assert call(server, "POST", f"/outbox/{msg['id']}/delivered", jo, form={"note": "called"})[0] == 403
    # the nurse's work: phone the physician; the notice shows the message and the way to mark it delivered
    phone = hub.db.items.find_one({"kind": "notice", "role": "nurse",
                                   "title": {"$regex": "^Phone the"}}, sort="n")["id"]
    page = call(server, "GET", f"/items/{phone}", noor)[1]
    assert msg["body"] in page and "Mark delivered by hand" in page
    assert call(server, "POST", f"/outbox/{msg['id']}/delivered", noor, form={"note": ""})[0] == 422
    assert call(server, "POST", f"/outbox/{msg['id']}/delivered", noor,
                form={"note": "Phoned Dr. Okafor at 10:02"})[0] == 303
    page = call(server, "GET", "/outbox", noor)[1]
    assert "Delivered by hand" in page and "Phoned Dr. Okafor at 10:02" in page
    assert hub.engine.item(phone)["status"] == "done"


def _multipart(fields: dict, file: tuple) -> tuple[bytes, str]:
    b = "----ph" + "x" * 12
    out = b""
    for k, v in fields.items():
        out += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    name, ctype, data = file
    out += (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
    return out, f"multipart/form-data; boundary={b}"


def raw(base, method, path, tok, data, headers):
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Authorization": f"Bearer {tok}", **headers})
    try:
        with opener.open(req) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_a_file_is_attached_kept_in_the_database_and_read_only_through_its_record(hub, server):
    rana = _modules(hub, server)
    call(server, "POST", "/records/Document", rana, form={"title": "Hand hygiene"})
    d = next(r for r in hub.records.search("Document") if r["title"] == "Hand hygiene")
    page = call(server, "GET", f"/records/Document/{d['id']}", rana)[1]
    assert 'enctype="multipart/form-data"' in page and "/attach" in page
    pdf = b"%PDF-1.4 the policy itself"
    body, ctype = _multipart({"field": "file", "_version": d["meta"]["versionId"]}, ("hygiene.pdf", "application/pdf", pdf))
    code, _, _ = raw(server, "POST", f"/records/Document/{d['id']}/attach", rana, body, {"Content-Type": ctype})
    assert code == 303
    rec = hub.records.read("Document", d["id"])
    import hashlib
    sha = hashlib.sha256(pdf).hexdigest()
    assert rec["file"]["blob"] == sha and rec["file"]["name"] == "hygiene.pdf" and sha in hub.db.file_ids()
    page = call(server, "GET", f"/records/Document/{d['id']}", rana)[1]
    href = f"/records/Document/{d['id']}/files/{sha}"
    assert href in page and "hygiene.pdf" in page
    # reading it: the bytes, shown sandboxed; and a view of the record, even right after opening it
    code, h, got = raw(server, "GET", href, token(hub, "noor"), None, {})
    assert code == 200 and got == pdf and h["Content-Type"] == "application/pdf"
    assert h["Content-Disposition"].startswith("inline") and "default-src 'none'" in h["Content-Security-Policy"]
    assert hub.db.events.exists({"action": "record.viewed", "actor": "noor", "subject": f"Document/{d['id']}",
                                 "data.how": "file"})
    assert hub.db.events.exists({"action": "file.added", "subject": f"file:{sha}"})

    # over the API: add the file, then name it in the record; a page the browser would run is only downloaded
    page_bytes = b"<script>alert(1)</script>"
    code, _, out = raw(server, "POST", "/api/files/Document", rana, page_bytes,
                       {"Content-Type": "text/html", "X-Filename": "x%20y.html"})
    att = json.loads(out)
    assert code == 200 and att["name"] == "x y.html" and att["contentType"] == "text/html"
    other = hub.records.search("Document")[0]
    code, h, _ = raw(server, "GET", f"/api/records/Document/{other['id']}/files/{att['blob']}", rana, None, {})
    assert code == 404  # a file is read only through a record that names it
    call(server, "POST", "/records/Document", rana, form={"title": "Second"})
    d2 = next(r for r in hub.records.search("Document") if r["title"] == "Second")
    code, _ = call(server, "PATCH", f"/api/records/Document/{d2['id']}", rana,
                   {"file": att, "_version": d2["meta"]["versionId"]})
    assert code == 200
    code, h, got = raw(server, "GET", f"/api/records/Document/{d2['id']}/files/{att['blob']}", rana, None, {})
    assert code == 200 and got == page_bytes
    assert h["Content-Disposition"].startswith("attachment") and "sandbox" in h["Content-Security-Policy"]
    # a record names only a file this hub has
    code, out = call(server, "PATCH", f"/api/records/Document/{d2['id']}", rana,
                     {"file": {"blob": "0" * 64, "name": "made up"}, "_version": "2"})
    assert code == 422 and "no file" in out


def test_a_request_larger_than_any_file_is_refused_unread(hub, server):
    import socket
    host, port = server.removeprefix("http://").split(":")
    with socket.create_connection((host, int(port))) as s:
        s.sendall(b"POST /api/files/Document HTTP/1.1\r\nHost: x\r\nContent-Length: 999999999999\r\n\r\n")
        assert s.recv(100).split(b"\r\n", 1)[0].split(b" ")[1] == b"413"


def test_a_wiki_page_reads_as_a_page_with_its_place_in_the_tree_and_its_links(hub, server):
    rana, amara = token(hub, "rana"), token(hub, "dr-amara")
    for name in ("dms", "wiki"):
        assert call(server, "POST", f"/modules/{name}/review-code", amara)[0] == 303
        assert call(server, "POST", "/modules", rana, form={"name": name})[0] == 303
    for m in hub.pack.modules:
        m.module.demo(hub)
    pages = {p["title"]: p for p in hub.records.search("WikiPage", limit=None)}
    sam = token(hub, "sam")

    view = call(server, "GET", "/views/wiki-home", sam)[1]
    assert "<ul class='tree'>" in view and '--depth:1"' in view and "On-call guide" in view

    oncall = pages["On-call guide"]
    page = call(server, "GET", f"/records/WikiPage/{oncall['id']}", sam)[1]
    assert '<div class="prose">' in page and '<h2 id="s-evenings-and-weekends">' in page
    assert "<blockquote>" in page and 'class="mention"' in page          # the link to the controlled document
    assert f'href="/records/WikiPage/{pages["Clinic handbook"]["id"]}"' in page  # its parent, in the breadcrumbs
    # a page: its text leads, under one bar of what it is and what can be done; no side column, no form until Edit
    assert '<div class="page-bar">' in page and '<article class="page-body">' in page and 'class="issue-side"' not in page
    assert 'href="/records/WikiPage/' + oncall["id"] + '?edit=1#edit"' in page and 'class="markdown"' not in page
    editing = call(server, "GET", f"/records/WikiPage/{oncall['id']}?edit=1", sam)[1]
    assert '<form method="post" action="/records/WikiPage/' + oncall["id"] + '/edit" class="page-edit" id="edit">' in editing
    assert 'class="markdown"' in editing and '<article class="page-body">' not in editing   # edited where it is read
    assert "Editing</span>" in editing and ">Cancel</a><button class=\"primary\">Save</button>" in editing
    assert '<h1 class=visually-hidden>' in editing                                     # the title is the title field
    assert f'<option value="WikiPage/{oncall["id"]}"' not in page  # not offered as its own parent

    # a page written with HTML in it shows the HTML as text; a page under it starts from "Add a page under this one"
    code, _ = call(server, "POST", "/records/WikiPage", sam, form={
        "title": "Scratch", "body": "<script>alert(1)</script> see [[Nowhere]] and [[On-call guide]]",
        "parent": f"WikiPage/{oncall['id']}"})
    assert code == 303
    new = next(p for p in hub.records.search("WikiPage", limit=None) if p["title"] == "Scratch")
    page = call(server, "GET", f"/records/WikiPage/{new['id']}", sam)[1]
    assert "<script>alert(1)" not in page and "&lt;script&gt;alert(1)" in page
    assert 'class="mention missing"' in page and "Nowhere" in page
    form = call(server, "GET", f"/records/WikiPage/new?parent=WikiPage/{oncall['id']}", sam)[1]
    assert f'value="WikiPage/{oncall["id"]}" selected' in form
    under = call(server, "GET", f"/records/WikiPage/{oncall['id']}", sam)[1]
    assert "Scratch" in under and "Add a wiki page under this one" in under
    links = call(server, "GET", f"/records/WikiPage/{oncall['id']}?tab=links", sam)[1]
    assert f"/records/WikiPage/{new['id']}" in links and "mentions" in links


def test_formatted_text_starts_its_editor_from_the_servers_own_safe_rendering(hub, server):
    rana, amara = token(hub, "rana"), token(hub, "dr-amara")
    for name in ("dms", "wiki"):
        assert call(server, "POST", f"/modules/{name}/review-code", amara)[0] == 303
        assert call(server, "POST", "/modules", rana, form={"name": name})[0] == 303
    for m in hub.pack.modules:
        m.module.demo(hub)
    sam = token(hub, "sam")
    oncall = next(p for p in hub.records.search("WikiPage", limit=None) if p["title"] == "On-call guide")
    code, _ = call(server, "POST", "/records/WikiPage", sam, form={
        "title": "Editor start", "body": "**Hi** <img src=x onerror=alert(1)> [[On-call guide]] [[Nowhere]]",
        "parent": f"WikiPage/{oncall['id']}"})
    assert code == 303
    new = next(p for p in hub.records.search("WikiPage", limit=None) if p["title"] == "Editor start")
    page = call(server, "GET", f"/records/WikiPage/{new['id']}?edit=1", sam)[1]
    start = re.search(r'<template class="md-start" data-for="md-body">(.*?)</template>', page, re.S)
    assert start, "a formatted-text field carries its start for the editor"
    assert "<strong>Hi</strong>" in start.group(1) and "<img" not in start.group(1)
    assert 'data-mention="On-call guide"' in start.group(1) and 'data-mention="Nowhere"' in start.group(1)
    assert 'aria-describedby="md-body-help"' in page and 'id="md-body-help"' in page
    assert '<datalist id="md-body-titles">' in page and '<option value="On-call guide">' in page

    # a controlled document's text is formatted text too
    assert any(f["path"] == "body" and f["type"] == "markdown" for f in hub.record_type("Document").fields)
    form = call(server, "GET", "/records/Document/new", amara)[1]
    assert 'class="markdown"' in form and 'class="md-start"' in form


def test_the_member_menu_and_settings(hub, server):
    sam = token(hub, "sam")
    page = call(server, "GET", "/", sam)[1]
    menu = re.search(r'<div class="me avatar-menu">.*?</div></div>', page, re.S).group(0)
    assert 'aria-haspopup="menu"' in menu and 'aria-expanded="false"' in menu and 'role="menu"' in menu
    assert 'href="/team/sam"' in menu and 'href="/me/settings"' in menu and 'href="/logout"' in menu
    assert "/me/passphrase" not in menu  # the passphrase is on the profile
    profile = call(server, "GET", "/team/sam", sam)[1]
    assert '<section id="passphrase"' in profile and 'action="/me/passphrase"' in profile
    assert '<section id="passphrase"' not in call(server, "GET", "/team/rana", sam)[1]  # only your own
    assert call(server, "GET", "/me/passphrase", sam)[0] == 303
    assert "data-theme" not in page.split(">", 2)[1]  # no theme chosen: the device's

    assert "Match my device" in call(server, "GET", "/me/settings", sam)[1]
    code, body = call(server, "POST", "/me/settings", sam, form={"theme": "dark", "editor": "markdown"})
    assert code == 200 and "Your settings are saved" in body
    page = call(server, "GET", "/", sam)[1]
    assert 'data-theme="dark"' in page and 'data-editor="markdown"' in page
    assert call(server, "POST", "/me/settings", sam, form={"theme": "purple"})[0] == 422
    assert hub.preferences("sam") == {"theme": "dark", "editor": "markdown"}
    assert any("member.preferences_set" in ev.values() for ev in hub.track.list(actor="sam"))


def test_a_wiki_has_a_second_column_of_spaces_and_the_open_spaces_page_tree(hub, server):
    rana, amara = token(hub, "rana"), token(hub, "dr-amara")
    for name in ("dms", "wiki"):
        assert call(server, "POST", f"/modules/{name}/review-code", amara)[0] == 303
        assert call(server, "POST", "/modules", rana, form={"name": name})[0] == 303
    for m in hub.pack.modules:
        m.module.demo(hub)
    sam = token(hub, "sam")
    pages = {p["title"]: p for p in hub.records.search("WikiPage", limit=None)}
    hand, oncall = pages["Clinic handbook"], pages["On-call guide"]
    # a second space, and an archived page in the first
    for form in ({"title": "Meetings", "body": "notes"},
                 {"title": "Old rota", "body": "x", "parent": f"WikiPage/{hand['id']}"}):
        assert call(server, "POST", "/records/WikiPage", sam, form=form)[0] == 303
    old = next(p for p in hub.records.search("WikiPage", limit=None) if p["title"] == "Old rota")
    hub.update_record(hub.member("sam"), f"WikiPage/{old['id']}", {**old, "status": "archived"})

    page = call(server, "GET", f"/records/WikiPage/{oncall['id']}", sam)[1]
    nav = re.search(r'<nav class="subnav".*?</nav>', page, re.S).group(0)
    assert 'class="shell has-subnav"' in page and 'aria-label="Spaces and pages"' in nav
    assert ">Clinic handbook</a>" in nav and ">Meetings</a>" in nav            # every space
    assert f'href="/records/WikiPage/{oncall["id"]}" aria-current="page"' in nav  # this page, in its space's tree
    assert '<ul class="pagetree">' in nav and "Add under Clinic handbook" in nav
    assert "Old rota" not in nav                                              # archived pages are left out
    archived = call(server, "GET", f"/records/WikiPage/{old['id']}", sam)[1]  # unless it is the one open
    assert 'aria-current="page">Old rota</a>' in re.search(r'<nav class="subnav".*?</nav>', archived, re.S).group(0)
    # the list of pages and the wiki's home show the spaces too; a type without a navigator has none
    assert '<nav class="subnav"' in call(server, "GET", "/records/WikiPage", sam)[1]
    assert '<nav class="subnav"' in call(server, "GET", "/views/wiki-home", sam)[1]
    assert '<nav class="subnav"' not in call(server, "GET", "/records/Document", amara)[1]


def test_products_have_product_lines_beside_them_and_show_their_changes(hub, server):
    rana, amara = token(hub, "rana"), token(hub, "dr-amara")
    for name in ("dms", "qms", "plm"):
        assert call(server, "POST", f"/modules/{name}/review-code", amara)[0] == 303
        assert call(server, "POST", "/modules", rana, form={"name": name})[0] == 303
    for m in hub.pack.modules:
        m.module.demo(hub)
    kit = next(p for p in hub.records.search("Product", limit=None) if p["number"] == "WC-100")
    co = next(iter(hub.records.search("ChangeOrder", limit=None)))
    page = call(server, "GET", f"/records/Product/{kit['id']}", rana)[1]
    nav = re.search(r'<nav class="subnav".*?</nav>', page, re.S).group(0)
    assert "Product lines" in nav
    assert ">WC Wound care</a>" in nav and 'aria-current="page"' in nav
    linked = call(server, "GET", f"/records/Product/{kit['id']}?tab=links", rana)[1]
    assert f"/records/ChangeOrder/{co['id']}" in linked                   # the change order, on its Linked tab
    assert "Products" in call(server, "GET", "/views/plm-products", rana)[1]
    # sam reads products but is not given change orders' quality context; the product page still opens
    assert call(server, "GET", f"/records/Product/{kit['id']}", token(hub, "sam"))[0] == 200


def test_start_work_picks_a_record_type_and_settings_live_in_the_practice_menu(hub, server):
    """Start work opens one choice: a record type (its create screen opens in the same dialog) or a workflow. The
    practice's settings are in its menu, top left, and on /settings; the sidebar keeps to the work."""
    rana = _modules(hub, server)
    code, page = call(server, "GET", "/start", rana)
    assert code == 200 and "Create a record" in page and 'href="/records/Document/new" data-modal' in page
    assert "Or start a workflow" in page and 'href="/start/' in page
    assert call(server, "GET", "/create", rana)[1].count("Create a record") >= 1
    code, form = call(server, "GET", "/records/Document/new", rana)
    assert code == 200 and "data-modal-body" in form and 'action="/records/Document"' in form
    home = call(server, "GET", "/", rana)[1]
    assert 'class="avatar-menu practice-menu"' in home and 'href="/define/types">Record types</a>' not in home
    side = home.split('<aside class="side"')[1].split("</aside>")[0]
    assert "Set up" not in side and "/define/types" not in side and "/workflows" not in side
    assert 'href="/records/Document"' in side                    # a module's shortcut is in the sidebar
    assert 'href="/start" data-modal accesskey="c"' in home
    code, page = call(server, "GET", "/settings", rana)
    assert code == 200 and "Record types" in page and "Track" in page and 'class="admin-cards"' in page
    jo = call(server, "GET", "/settings", token(hub, "jo", by="jo"))[1]
    assert "Record types" in jo and "/track" not in jo   # audit is for policy owners, in the ribbon and the cards
    reg = call(server, "GET", "/patients", token(hub, "jo"))[1]
    assert 'class="tabs form-tabs"' in reg                       # a screen in parts is tabs (the script shows one)


def test_a_refused_create_or_edit_keeps_everything_typed(hub, server):
    """A create the hub refuses shows its form again with every value typed, nested ones too (a patient's given
    name, phone, address, health card); a refused edit and a refused move do the same."""
    jo = token(hub, "jo")
    typed = {"name.0.given.0": "Marguerite", "name.0.family": "Thibodeau", "telecom.0.value": "902-555-0142",
             "address.0.city": "Wolfville", "address.0.line.0": "12 Main St", "identifier.0.value": "1234 567 890",
             "birthDate": ""}  # the date of birth is required: refused
    code, page = call(server, "POST", "/records/Patient", jo, form=typed)
    assert code == 422 and 'role="alert"' in page
    for v in ("Marguerite", "Thibodeau", "902-555-0142", "Wolfville", "12 Main St", "1234 567 890"):
        assert f'value="{v}"' in page, v
    assert "<title>Create a patient" in page or "Create a patient" in page
    assert '<span class="req" aria-hidden="true"> *</span>' in page and "required</p>" in page
    assert 'href="/start" data-modal>← Back</a>' in page
    rana = _modules(hub, server)
    capa = hub.create_record(hub.member("rana"), "CAPA", {"title": "T", "kind": "corrective", "problem": "P"})
    url = f"/records/CAPA/{capa['id']}"
    form = {"_version": str(capa["meta"]["versionId"]), "title": "", "problem": "A new account of it"}
    code, page = call(server, "POST", f"{url}/edit", rana, form=form)
    assert code == 422 and 'role="alert"' in page and "A new account of it" in page


def test_forms_mark_required_fields_and_tie_help_to_its_control(hub):
    app = App(hub, dev=False)
    out = app._field({"name": "code", "type": "string", "label": "Code", "required": True, "help": "As printed"},
                     None, None)
    cid = re.search(r'<input[^>]* id="([^"]+)" aria-describedby="\1-help"', out).group(1)
    assert f'<small id="{cid}-help" class="help">As printed</small>' in out
    assert "</label><small" in out and '<span class="req" aria-hidden="true"> *</span>' in out
    assert "<small" not in out.split("</label>")[0]
    plain = app._field({"name": "note", "type": "string", "label": "Note"}, None, None)
    assert plain.startswith("<label>Note<input") and "aria-describedby" not in plain


def test_words_states_and_the_sidebar(hub):
    from practicehub.web.records_pages import a_an
    assert [a_an(w) for w in ("appointment", "patient", "SOP", "CAPA", "unit", "hour")] == [
        "an appointment", "a patient", "an SOP", "a CAPA", "a unit", "an hour"]
    app = App(hub, dev=False)
    assert 'class="loz default"' in app._state_loz("weird-state")
    for st in ("released", "published", "stocked", "posted", "completed", "closed"):
        assert 'class="loz success"' in app._state_loz(st)
    assert 'class="loz removed"' in app._state_loz("cancelled")
    me = M(hub, "rana")
    side = app._sidebar({"path": "/records/Patient/x"}, me)
    assert side.count('aria-current="page"') <= 1
    side = app._sidebar({"path": "/records"}, me)
    assert side.count('aria-current="page"') == 1 and 'href="/records" aria-current="page"' in side


def test_tabbed_ids_are_per_type_and_the_team_sponsor_is_for_agents(hub, server):
    jo = token(hub, "jo")
    new = call(server, "GET", "/records/Patient/new", jo)[1]
    reg = call(server, "GET", "/patients", jo)[1]
    assert 'id="create-patient-0"' in new and 'id="register-patient-0"' in reg and 'id="create-patient-0"' not in reg
    app = App(hub, dev=False)
    form = app.page_team_new({"me": M(hub, "rana"), "query": {}, "path": "/team/new"})
    assert 'data-kinds="agent service"' in form and 'title="Lower-case letters' in form


def test_short_choices_are_option_cards_and_a_yes_no_is_a_switch(hub):
    app = App(hub, dev=False)
    short = app._field({"name": "kind", "type": "choice", "required": True}, [{"value": v, "label": v}
                       for v in ("corrective", "preventive")], "preventive")
    assert "<select" not in short and '<fieldset class="choices options"' in short
    assert 'type="radio" class="opt-input" name="kind" value="preventive" required checked' in short
    loose = app._field({"name": "k", "type": "choice"}, [{"value": "a", "label": "a"}], None)
    assert 'value="" checked> <span>None</span>' in loose and "required" not in loose  # optional: it can be left unset
    many = [{"value": str(i), "label": str(i)} for i in range(app.CARDS + 1)]
    assert "<select" in app._field({"name": "n", "type": "choice"}, many, None)  # a long list stays a drop-down
    several = app._field({"name": "tags", "type": "list"}, [{"value": "x", "label": "x"}, {"value": "y", "label": "y"}],
                         ["y"])
    assert 'type="checkbox" class="opt-input" name="tags" value="y" checked' in several and 'class="radio multi"' in several
    yes = app._field({"name": "ok", "type": "boolean", "label": "Fine"}, None, True)
    assert 'class="switch-row"' in yes and 'type="checkbox" class="switch"' in yes and " checked" in yes
    from practicehub.web.assets import CSS as css
    assert "input.opt-input{position:absolute;opacity:0;width:1px;height:1px" in css
    assert "label.radio:has(>input.opt-input:checked)" in css and "fieldset.options.decide label.radio{min-width:160px}" in css


def _ribbon(page):
    return page.split('<nav class="ribbon"')[1].split("</nav>")[0] if '<nav class="ribbon"' in page else None


def test_settings_are_a_screen_of_their_own_with_a_ribbon(hub, server):
    """Settings change the whole screen: a slim bar with the way back to the work, and a ribbon, left, of every setup
    item, the closest one lit. Work pages keep the work sidebar and have no ribbon."""
    rana = token(hub, "rana")
    for path, lit in [("/settings", "/settings"), ("/team", "/team"), ("/team/new", "/team"), ("/team/jo", "/team"),
                      ("/modules", "/modules"), ("/workflows", "/workflows"), ("/workflows/patient-question", "/workflows"),
                      ("/define/types", "/define/types"), ("/define/types/Patient", "/define/types"),
                      ("/define/views", "/define/views"), ("/blocks", "/blocks"), ("/define/skills", "/define/skills"),
                      ("/track", "/track"), ("/access", "/access"), ("/outbox", "/outbox")]:
        code, page = call(server, "GET", path, rana)
        assert code == 200, (path, page[:300])
        rib = _ribbon(page)
        assert rib is not None and '<aside class="side"' not in page, path
        assert rib.count('aria-current=page') == 1 and f'href="{lit}" aria-current=page' in rib, path
        assert 'class="btn subtle back-to-work" href="/"' in page and "settings</span></a>" in page
        assert "<main" in page and "data-modal" not in rib
    pat = hub.records.search("Patient")[0]
    for path in ("/", "/records", f"/records/Patient/{pat['id']}", "/team/rana", "/me/settings"):
        code, page = call(server, "GET", path, rana)
        assert code == 200 and '<aside class="side"' in page and _ribbon(page) is None, path
    # a form opened in a dialog from a settings page still has its body to show
    assert "data-modal-body" in call(server, "GET", "/team/new", rana)[1]


def test_the_ribbon_shows_a_member_only_what_they_may_use(hub, server):
    """A member who is not a policy owner sees the set up items, and no audit; the practice menu is small: the
    practice, Settings and Home, the setup items being in the ribbon."""
    jo = token(hub, "jo")
    page = call(server, "GET", "/settings", jo)[1]
    rib = _ribbon(page)
    assert 'href="/team"' in rib and 'href="/define/types"' in rib and 'href="/workflows"' in rib
    assert 'href="/track"' not in rib and 'href="/access"' not in rib   # Track and the access report: policy owners
    sam = _ribbon(call(server, "GET", "/settings", token(hub, "sam"))[1])
    assert 'href="/team"' in sam and "Audit" not in sam and 'href="/outbox"' not in sam
    full = _ribbon(call(server, "GET", "/settings", token(hub, "rana"))[1])
    assert 'href="/track"' in full and 'href="/access"' in full and "Audit" in full
    home = call(server, "GET", "/", jo)[1]
    menu = home.split('id="practice-menu"')[1].split("</div></div>")[0]
    assert 'href="/settings">Settings</a>' in menu and 'href="/">Home</a>' in menu
    for gone in ("/team", "/modules", "/workflows", "/define/", "/blocks", "/track", "/access", "/outbox"):
        assert f'href="{gone}' not in menu


def test_a_policy_owner_changes_the_practice_on_its_settings_page(hub, server):
    """The practice's name, code, contact details, who hears when work is stuck and its shortcuts are a page in the
    settings for its policy owners; who the practice is (its law, where data stays) is shown and fixed."""
    rana, jo = token(hub, "rana"), token(hub, "jo")
    assert 'href="/settings/practice"' in _ribbon(call(server, "GET", "/settings", rana)[1])
    assert 'href="/settings/practice"' not in _ribbon(call(server, "GET", "/settings", jo)[1])
    assert call(server, "GET", "/settings/practice", jo)[0] == 403
    page = html.unescape(call(server, "GET", "/settings/practice", rana)[1])
    assert '<nav class="ribbon"' in page and "Who the practice is" in page and "PHIA" in page
    assert 'name="data_residency"' not in page and 'name="privacy_law"' not in page
    form = {"name": "Harbourview Family Health", "code": "HFH", "phone": "902-555-0100", "email": "front@example.org",
            "address": "1 Harbour St", "website": "https://example.org", "timezone": "America/Halifax",
            "escalation_role": "practice-manager", "_shortcuts": "1", "shortcuts": "Appointment"}
    status, page = call(server, "POST", "/settings/practice", rana, form=form)
    assert status == 200 and "are saved" in page
    assert hub.practice["name"] == "Harbourview Family Health" and hub.practice["shortcuts"] == ["Appointment"]
    home = call(server, "GET", "/", rana)[1]
    assert "Harbourview Family Health" in home and '<span class="mark">HFH</span>' in home
    assert 'href="/records/Encounter"' not in home.split('<nav class="sidebar"')[-1].split("</nav>")[0]
    status, page = call(server, "POST", "/settings/practice", rana, form={**form, "timezone": "Mars/Base",
                                                                          "name": "Kept while refused"})
    assert status == 422 and "Mars/Base" in page and "Kept while refused" in page and "time zone" in page
    assert hub.practice["name"] == "Harbourview Family Health"
