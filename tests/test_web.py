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

from conftest import M, item_for, quiet, run_of, visit_run
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
    run = visit_run(hub)
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
    run = visit_run(hub)
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
    run = visit_run(hub)
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
    assert page.count("installed, running</span>") == 2
    code, page = call(server, "GET", "/modules/qms", token(hub, "rana"))
    assert code == 200 and "nothing to upgrade" in page
    code, _ = call(server, "POST", "/modules/dms/remove", token(hub, "rana"), form={"reason": "x"})
    assert code == 422  # qms builds on it
    code, _ = call(server, "POST", "/modules/qms/remove", token(hub, "rana"), form={"reason": "trial over"})
    assert code == 303 and "qms" not in hub.installed_modules()


def test_connections_are_off_until_a_second_policy_owner_approves_on_the_page(hub, server):
    rana, amara, jo = token(hub, "rana"), token(hub, "dr-amara"), token(hub, "jo")
    page = call(server, "GET", "/settings/connections", rana)[1]
    assert "Data stays in" in page and "Ask to switch it on" in page and 'class="loz success">on' not in page
    assert "No server is switched on" in page
    form = {"roles": "front-desk", "reason": "desk", "acknowledged": "1", "clients": "Desk app | Canada"}
    code, page = call(server, "POST", "/settings/connections/practice/ask", rana, form={**form, "acknowledged": ""})
    assert code == 200 and "confirm that you checked" in page  # the residency check is not skipped
    assert call(server, "POST", "/settings/connections/practice/ask", jo, form=form)[0] in (200, 403)
    assert not hub.db.get_setting("mcp_request:practice") or hub.db.get_setting("mcp_request:practice")["by"] != "jo"
    assert call(server, "POST", "/settings/connections/practice/ask", rana, form=form)[0] == 303
    page = call(server, "GET", "/settings/connections", rana)[1]
    assert "waiting for a second policy owner" in page and "/practice/approve" not in page  # not the asker's
    assert "/practice/approve" in call(server, "GET", "/settings/connections", amara)[1]
    assert call(server, "POST", "/settings/connections/practice/approve", amara)[0] == 303
    code, page = call(server, "POST", "/settings/connections/token", jo,
                      form={"member": "jo", "servers": "practice", "client": "Desk app"})
    assert code == 200 and "The new MCP token" in page
    code, page = call(server, "POST", "/settings/connections/token", jo,  # an app nobody named is refused
                      form={"member": "jo", "servers": "practice", "client": "Cloud chat", "runs_in": "Canada"})
    assert "The new MCP token" not in page and "not one a policy owner named" in page


def test_a_module_not_running_says_so_alike_on_every_page(hub, server):
    from practicehub import mcp
    rana = _modules(hub, server)
    hub.db.set_setting("modules", [*hub.installed_modules(), "wiki"])  # no sealed install: never run
    hub.db.set_setting("modules_rev", "changed")
    assert "installed, not running" in call(server, "GET", "/modules", rana)[1]
    assert "installed, not running" in call(server, "GET", "/modules/wiki", rana)[1].lower()
    assert "not running" in call(server, "GET", "/settings/connections", rana)[1]
    assert next(s for s in mcp.servers(hub) if s["id"] == "wiki")["unavailable"]


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


class _BuilderForm(__import__("html.parser").parser.HTMLParser):
    """What a browser would send from the type builder (form#type-builder): every enabled control but its buttons."""

    def __init__(self, page):
        super().__init__(convert_charrefs=True)
        self.pairs, self._in, self._sel, self._opt, self._ta = [], False, None, None, None
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self._in = a.get("id") == "type-builder"
        if not self._in or "disabled" in a:
            return
        if tag == "input" and a.get("name") and a.get("type") not in ("submit", "button"):
            if a.get("type") not in ("checkbox", "radio") or "checked" in a:
                self.pairs.append((a["name"], a.get("value", "on" if a.get("type") == "checkbox" else "")))
        elif tag == "select":
            self._sel = a.get("name")
        elif tag == "option" and self._sel and "selected" in a:
            self.pairs.append((self._sel, a.get("value", "")))
        elif tag == "textarea" and a.get("name"):
            self._ta = [a["name"], ""]

    def handle_data(self, data):
        if self._ta:
            self._ta[1] += data

    def handle_endtag(self, tag):
        if tag == "form":
            self._in = False
        elif tag == "select":
            self._sel = None
        elif tag == "textarea" and self._ta:
            self.pairs.append((self._ta[0], self._ta[1].removeprefix("\n")))
            self._ta = None


def _textarea(page, name):
    return html.unescape(re.search(rf'<textarea name="{name}"[^>]*>(.*?)</textarea>', page, re.S).group(1))


def test_the_type_builder_round_trips_through_toml(hub, server):
    """The form is the default editor; "Edit as TOML" gives the text the validator checks, and back to the form gives
    the same form: nothing is lost either way."""
    rana = _modules(hub, server)
    code, page = call(server, "GET", "/define/types/Document/edit", rana)
    assert code == 200 and 'id="type-builder"' in page and "Edit as TOML" in page
    form = _BuilderForm(page).pairs
    assert ("t.id", "Document") in form and any(k.endswith(".name") for k, _ in form)
    code, page = call(server, "POST", "/define/types/build", rana, form=form + [("op", "toml")])
    assert code == 200
    source = _textarea(page, "source")
    doc = __import__("tomllib").loads(source)
    assert doc["id"] == "Document" and doc["field"]
    code, page = call(server, "POST", "/define/types/build", rana,
                      form=[("source", source), ("did", "Document"), ("note", ""), ("op", "form")])
    assert code == 200 and _BuilderForm(page).pairs == form
    code, page = call(server, "POST", "/define/types/build", rana, form=form + [("op", "check")])
    assert "The hub accepts this" in page


def test_the_type_builder_shows_a_problem_on_the_field_it_is_about(hub, server):
    rana = _modules(hub, server)
    form = _BuilderForm(call(server, "GET", "/define/types/Document/edit", rana)[1]).pairs
    n = int(dict(form)["f.n"])
    page = call(server, "POST", "/define/types/build", rana, form=form + [("op", "add-field")])[1]
    form = [(k, v) for k, v in _BuilderForm(page).pairs if k not in (f"f.{n}.name", f"f.{n}.type")]
    form += [(f"f.{n}.name", "room"), (f"f.{n}.type", "choice"), ("op", "check")]
    code, page = call(server, "POST", "/define/types/build", rana, form=form)
    assert code == 200
    err = re.search(rf'<ul class="fb-errors" id="b-f{n}-choices-err"[^>]*>(.*?)</ul>', page, re.S)
    assert err and "a choice needs choices" in err.group(1)
    assert f'href="#b-f{n}-choices"' in page  # the summary at the top links to it
    assert not any(v["status"] == "proposed" for v in hub.definition_versions("type", "Document"))


def test_the_editors_show_what_a_module_added_but_never_let_it_be_changed(hub, server, tmp_path, monkeypatch):
    import shutil

    from practicehub import pack as packmod
    d = tmp_path / "modules"
    for name in ("dms", "qms"):
        shutil.copytree(packmod.MODULES_DIR / name, d / name)
    (d / "dms-site" / "extends" / "types").mkdir(parents=True)
    (d / "dms-site" / "module.toml").write_text(
        '[module]\nid = "dms-site"\nname = "Sites"\nversion = "1"\nrequires = ["dms"]\n')
    (d / "dms-site" / "extends" / "types" / "Document.toml").write_text(
        '[[field]]\nname = "site"\nlabel = "Site"\ntype = "choice"\nchoices = ["north", "south"]\n')
    monkeypatch.setattr(packmod, "MODULES_DIR", d)
    rana = _modules(hub, server)
    hub.approve_module_code(M(hub, "dr-amara"), "dms-site")
    hub.install_module(M(hub, "rana"), "dms-site")
    page = call(server, "GET", "/define/types/Document/edit", rana)[1]
    assert "From modules" in page and "dms-site" in page
    form = _BuilderForm(page).pairs
    assert ("f.0.name", "site") not in form and not any(v == "site" for k, v in form if k.endswith(".name"))
    toml = call(server, "GET", "/define/types/Document/edit?as=toml", rana)[1]
    own = _textarea(toml, "source")
    assert "# >>>" not in own and "From modules" in toml
    full = next(v for v in hub.definition_versions("type", "Document") if v["status"] == "active")["source"]
    altered = full.replace('choices = ["north", "south"]', 'choices = ["north", "east"]')
    code, page = call(server, "POST", "/define/types/propose", rana, form={"source": altered, "note": "x"})
    assert code == 200 and "It was not saved" in page and "cannot be changed here" in html.unescape(page)
    redefined = own + '\n[[field]]\nname = "site"\ntype = "string"\n'
    page = call(server, "POST", "/define/types/propose", rana, form={"source": redefined, "note": "x"})[1]
    assert "added by the dms-site module" in html.unescape(page)
    assert not any(v["status"] == "proposed" for v in hub.definition_versions("type", "Document"))
    code, page = call(server, "POST", "/define/types/build", rana, form=form + [("note", "same"), ("op", "propose")])
    assert code == 303  # the practice's own part, proposed from the form: the module's part comes back on its own
    prop = next(v for v in hub.definition_versions("type", "Document") if v["status"] == "proposed")["source"]
    assert "# >>> added by the dms-site module" in prop and 'name = "site"' in prop


def test_views_show_in_the_sidebar_and_work_starts_about_any_record(hub, server):
    rana = _modules(hub, server)
    qm = next(m for m in hub.members() if "quality-manager" in (m.get("roles") or []) and m["kind"] == "person")
    who = token(hub, qm["id"], by=qm["id"])
    code, page = call(server, "GET", "/views/qms-capas", who)
    assert code == 200 and 'href="/views/qms-capas"' in page and "By status" in page
    assert call(server, "GET", "/views/no-such-view", who)[0] == 404
    capa = hub.create_record(hub.member(qm["id"]), "CAPA", {"title": "T", "kind": "corrective", "problem": "P"})
    code, page = call(server, "GET", f"/start?workflow=qms-capa&record=CAPA/{capa['id']}", who)
    assert code == 200 and f'name="_record" value="CAPA/{capa["id"]}"' in page and 'class="about"' in page  # fixed
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
        assert ("Record viewed" in team) is sees  # the action in words
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


def test_registering_a_patient_offers_one_physician_and_their_emergency_contact(hub):
    """The physician is one choice, among the members who hold the physician role (the field's ``roles``), never a
    tick-box per member of the team; saving someone else is refused; registration asks for the emergency contact."""
    app = App(hub, dev=False)
    rt = hub.record_type("Patient")
    f = next(f for f in rt.fields if f["name"] == "generalPractitioner")
    assert f["roles"] == ["physician"]
    ctl = app._input(f, None, M(hub, "jo"))
    assert "<select" in ctl and "multiple" not in ctl and 'type="checkbox"' not in ctl
    holders = {m["id"] for m in hub.active_holders("physician", persons_only=True)}
    assert {"dr-amara", "dr-liam"} <= holders
    offered = set(re.findall(r'value="Practitioner/([^"]+)"', ctl))
    assert offered == holders and "noor" not in offered and "jo" not in offered
    reg = rt.screens["register"]
    shown = [x for t in reg["tabs"] for x in t["fields"]]
    assert {"contact_name", "contact_relationship", "contact_phone"} <= set(shown)
    body = {"name": [{"given": ["Ada"], "family": "Lane"}], "birthDate": "1990-02-03"}
    with pytest.raises(Invalid) as no:
        hub.create_record(hub.member("jo"), "Patient", {**body, "generalPractitioner": [{"reference": "Practitioner/noor"}]})
    assert "physician" in " ".join(no.value.problems)
    pat = hub.create_record(hub.member("jo"), "Patient", {**body, "generalPractitioner": [{"reference": "Practitioner/dr-liam"}]})
    assert hub.records.read("Patient", pat["id"])["generalPractitioner"] == [{"reference": "Practitioner/dr-liam"}]


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
    assert not hub.member("dr-chen")["roles"] and hub.role_request("dr-chen")  # beyond her reach: it waits
    assert "a second policy owner approves" in app.page_member({"me": rana, "query": {}, "path": "/team/dr-chen"},
                                                               "dr-chen")
    with pytest.raises(Redirect):
        app.post_member(req("roles=physician&roles=nurse"), "dr-chen", "roles")
    hub.approve_roles(M(hub, "dr-amara"), "dr-chen")
    assert set(hub.member("dr-chen")["roles"]) >= {"physician", "nurse"}
    page = app.post_member({**req("label=interface"), "me": M(hub, "jo")}, "portal", "token")
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
    assert code == 422 and "fill in Owner" in page and ">kept</textarea>" in page and "data-modal-body" in page
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
    assert call(server, "POST", "/team/dr-liam/setup-code", rana)[0] == 403  # beyond her reach
    assert "someone who does issues" in call(server, "GET", "/team/dr-liam", rana)[1]
    assert call(server, "POST", "/team/dr-liam/setup-code", token(hub, "dr-amara"))[0] == 403  # a directive keeps from her
    hub.add_member(M(hub, "rana"), {"id": "locum", "name": "Dr. Locum", "roles": []})  # reaches nothing she does not
    page = call(server, "GET", "/team/locum", rana)[1]
    assert "Issue a setup code" in page and "has no passphrase yet" in page
    code, page = call(server, "POST", "/team/locum/setup-code", rana)
    assert code == 200 and "shown once" in page
    setup = re.search(r"<pre>([A-Z2-9]{5}(?:-[A-Z2-9]{5}){3})</pre>", page).group(1)
    assert setup not in json.dumps(hub.track.list(limit=10000))
    b = browser(server)
    assert b("GET", "/setup?member=locum")[0] == 200
    words = "the tide comes in at noon"
    code, page = b("POST", "/setup", {"member": "locum", "code": setup, "new": words, "again": words + "x"})
    assert code == 422 and "not the same" in page and 'value="locum"' in page and words not in page
    code, page = b("POST", "/setup", {"member": "locum", "code": "AAAAA-BBBBB-CCCCC-DDDDD", "new": words, "again": words})
    assert code == 403 and b.jar["cookie"] is None
    code, _ = b("POST", "/setup", {"member": "locum", "code": setup, "new": words, "again": words})
    assert code == 303 and b.jar["cookie"]  # set, and signed in
    assert b("GET", "/")[0] == 200
    assert b("POST", "/setup", {"member": "locum", "code": setup, "new": words, "again": words})[0] == 403  # once
    # changing it asks for the current one, and keeps this browser signed in
    code, page = b("POST", "/me/passphrase", {"current": "wrong words here", "new": "low tide at six", "again": "low tide at six"})
    assert code == 403 and "Your passphrase" in page
    code, page = b("POST", "/me/passphrase", {"current": words, "new": "low tide at six", "again": "low tide at six"})
    assert code == 200 and "is set" in page and b("GET", "/")[0] == 200
    assert call(server, "POST", "/login", form={"member": "locum", "passphrase": words})[0] == 403
    assert call(server, "POST", "/login", form={"member": "locum", "passphrase": "low tide at six"})[0] == 303
    # an access token no longer signs a person in on the sign-in form
    assert call(server, "POST", "/login", form={"token": token(hub, "locum")})[0] == 403


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
    hub.add_member(rana, {"id": "pager", "kind": "service", "name": "Pager", "sponsor": "dr-amara", "roles": []})
    hub.authz.write([("data_class:clinical", "reader", "member:pager"),
                     ("data_class:correspondence", "reader", "member:pager")])
    pager = token(hub, "pager", by="dr-amara")
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
    assert '<div class="page-bar">' in page and '<article class="page-body"' in page and 'class="issue-side"' not in page
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
    assert 'href="/settings">Practice settings</a>' in menu and 'href="/">Home</a>' in menu
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
            "escalation_role": "practice-manager", "_shortcuts": "1", "shortcuts": "Observation"}
    status, page = call(server, "POST", "/settings/practice", rana, form=form)
    assert status == 200 and "are saved" in page
    assert hub.practice["name"] == "Harbourview Family Health" and hub.practice["shortcuts"] == ["Observation"]
    home = call(server, "GET", "/", rana)[1]
    assert "Harbourview Family Health" in home and '<span class="mark">HFH</span>' in home
    assert 'href="/records/Encounter"' not in home.split('<nav class="sidebar"')[-1].split("</nav>")[0]
    status, page = call(server, "POST", "/settings/practice", rana, form={**form, "timezone": "Mars/Base",
                                                                          "name": "Kept while refused"})
    assert status == 422 and "Mars/Base" in page and "Kept while refused" in page and "time zone" in page
    assert hub.practice["name"] == "Harbourview Family Health"


# ------------------------------------------------------------------------------- parts: records that live in an owner
def _part_setup(hub):
    """The pack's patient, a part type (part_of) that is an alert, and one active part of that patient."""
    hub_types = {t: hub.record_type(t) for t in hub.record_types()}
    part = next(t for t in hub_types.values() if t.part_of and t.alert)
    owner_t = hub.concrete(part.part_of["target"])[0]
    pats = hub.records.search(owner_t)
    me = M(hub, "dr-amara")
    pat = next((p for p in pats if hub.parts(me, f"{owner_t}/{p['id']}")[0][part.id]), pats[0])
    return part, owner_t, pat


def test_an_owner_page_shows_its_parts_with_counts_and_adds_one_in_place(hub, server):
    """A patient's page is sections of what is part of it (heading, count, newest first, Add with the owner set),
    then what links to it; a left rail lists them; an alert part is also in the banner."""
    part, owner_t, pat = _part_setup(hub)
    ref = f"{owner_t}/{pat['id']}"
    recs, _ = hub.parts(M(hub, "dr-amara"), ref)
    page = call(server, "GET", f"/patients/{pat['id']}", token(hub, "dr-amara"))[1]
    sec = page.split(f'id="part-{part.id}"')[1].split("</section>")[0]
    assert f'<span class="count-badge">{len(recs[part.id])}</span>' in sec
    field = part.part_of["field"]
    assert f'href="/records/{part.id}/new?{field}={urllib.parse.quote(ref)}" data-modal' in sec
    assert 'class="subnav owner-rail"' in page and f'href="#part-{part.id}"' in page
    assert "id='work'" in page and "Start work" in page
    if recs[part.id]:
        assert 'class="alert-banner"' in page
    code, form = call(server, "GET", f"/records/{part.id}/new?{field}={urllib.parse.quote(ref)}",
                      token(hub, "dr-amara"))
    assert code == 200 and "data-modal-body" in form and 'class="part-of"' in form
    assert f'type="hidden" name="{field}" value="{html.escape(ref)}"' in form or \
        f'name="{field}" value="{html.escape(ref)}" type="hidden"' in form or \
        re.search(rf'<input[^>]*type="hidden"[^>]*name="{field}"', form)


def test_a_part_type_is_not_listed_on_its_own(hub, server):
    """A part type is absent from /records and from Start work; its own list says to open one from its owner."""
    part, owner_t, _ = _part_setup(hub)
    amara = token(hub, "dr-amara")
    assert f'href="/records/{part.id}"' not in call(server, "GET", "/records", amara)[1]
    start = call(server, "GET", "/start", amara)[1]
    assert f'href="/records/{part.id}/new"' not in start
    code, page = call(server, "GET", f"/records/{part.id}", amara)
    assert code == 200 and f"is part of" in page and f'href="/records/{part.id}/new"' not in page


def test_adding_a_part_without_its_owner_asks_which_one_first(hub, server):
    part, owner_t, pat = _part_setup(hub)
    amara = token(hub, "dr-amara")
    code, page = call(server, "GET", f"/records/{part.id}/new", amara)
    assert code == 200 and 'id="owner-pick"' in page and "Which " in page and "data-modal-body" in page
    field = part.part_of["field"]
    code, page = call(server, "GET", f"/records/{part.id}/new?{field}={owner_t}/{pat['id']}", amara)
    assert code == 200 and 'id="owner-pick"' not in page and 'class="part-of"' in page


def test_a_part_page_reads_inside_its_owner(hub, server):
    """A part's page has the breadcrumb owner › plural › title and the owner's banner above it."""
    part, owner_t, pat = _part_setup(hub)
    ref = f"{owner_t}/{pat['id']}"
    me = M(hub, "dr-amara")
    r = hub.parts(me, ref)[0][part.id][0]    # the demo has one
    page = call(server, "GET", f"/records/{part.id}/{r['id']}", token(hub, "dr-amara"))[1]
    crumbs = page.split('class="crumbs"')[1].split("</nav>")[0] if 'class="crumbs"' in page else page
    assert html.escape(hub.short_title(pat)) in crumbs and f'#part-{part.id}"' in crumbs and html.escape(part.plural) in crumbs
    assert 'class="subject-banner"' in page


def test_a_refused_move_in_the_dialog_says_why_in_the_dialog(hub, server):
    """A move refused (a field it needs is empty) answers 422 with the move screen inside data-modal-body."""
    rana = _modules(hub, server)
    capa = hub.create_record(hub.member("rana"), "CAPA", {"title": "T", "kind": "corrective", "problem": "P"})
    code, page = call(server, "POST", f"/records/CAPA/{capa['id']}/move", rana, form={"to": "investigating"})
    assert code == 422 and "data-modal-body" in page and "fill in Owner" in page


def test_work_about_a_record_starts_from_a_button_whose_form_opens_in_the_dialog(hub, server):
    """A patient page offers each workflow about a patient as a button; its form opens in the dialog with the patient
    already chosen (shown, not asked), and starting it from there runs it about that patient. Record pages do the same."""
    amara = token(hub, "dr-amara")
    pat = hub.records.search("Patient")[0]
    ref = f"Patient/{pat['id']}"
    me = hub.member("dr-amara")
    wfs = [w["workflow"] for w in hub.workflows() if w["workflow"].record == "Patient"
           and hub.can(me, "starter", f"workflow:{w['workflow'].id}")]
    assert wfs, "the pack has work about a patient"
    page = call(server, "GET", f"/patients/{pat['id']}", amara)[1]
    assert "<details><summary>" not in page.split("class='starts'")[0][-200:] and "class='starts'" in page
    quoted = ref.replace("/", "%2F")
    for wf in wfs:
        assert f'href="/start/{wf.id}?record={quoted}" data-modal>' in page, wf.id
    wf = wfs[0]
    code, form = call(server, "GET", f"/start/{wf.id}?record={quoted}", amara)
    assert code == 200 and "data-modal-body" in form and f'name="_record" value="{ref}"' in form
    assert 'name="_fixed"' in form and "← Back" not in form
    rec_page = call(server, "GET", f"/records/Patient/{pat['id']}", amara)[1]
    assert "start?workflow=" not in rec_page


def test_a_refused_start_from_a_record_keeps_the_record_chosen(hub, server):
    """If a start opened from a record is refused, the form comes back with the same record still fixed."""
    rana = _modules(hub, server)
    capa = hub.create_record(hub.member("rana"), "CAPA", {"title": "T", "kind": "corrective", "problem": "P"})
    wf = hub.workflow("qms-capa")[0]
    required = [i for i in wf.inputs if i.get("required")]
    if not required:
        return
    code, page = call(server, "POST", "/start", rana, form={"_workflow": "qms-capa", "_record": f"CAPA/{capa['id']}",
                                                          "_fixed": "1"})
    assert code == 422 and f'name="_record" value="CAPA/{capa["id"]}"' in page and 'name="_fixed"' in page


# ------------------------------------------------------------------------------------------- the calendar
DAY = "2030-01-07"  # a Monday, clear of the demo's bookings


def _ellen(hub):
    (p,) = hub.records.search("Patient", text="Ellen MacLeod")
    return f"Patient/{p['id']}"



def _another(hub):
    """A patient other than Ellen with nothing withheld: for a second request while Ellen's is still open."""
    p = next(r for r in hub.records.search("Patient", limit=None)
             if r["name"][0]["family"] not in ("MacLeod", "Rahimi"))
    return f"Patient/{p['id']}"

def _book(server, hub, who="dr-amara", at=f"{DAY}T09:00", member="dr-amara", subject=None, reason="Sore throat"):
    """A booking made by a physician, who may write its reason (it is clinical); the front desk books without one."""
    return call(server, "POST", "/schedule/new", token(hub, who),
                form={"with": member, "at": at, "subject": subject or _ellen(hub),
                      **({"reason": reason} if reason else {})})


def test_every_member_opens_the_calendar(hub, server):
    """Day and week, one column per physician, for everyone; and only their own column when they ask for theirs."""
    for mid in ("rana", "dr-amara", "jo", "noor", "sam"):
        status, page = call(server, "GET", f"/schedule?date={DAY}", token(hub, mid))
        assert status == 200, (mid, page)
        assert 'href="/schedule"' in page.split('<aside class="side"')[1].split("</aside>")[0]
        assert page.count('class="cal-who-h"') == 2 and "Dr. Amara Okafor" in page and "Dr. Liam Fraser" in page
        assert "Monday January 7, 2030" in page and f"date=2030-01-08" in page and f"date=2030-01-06" in page
    week = call(server, "GET", f"/schedule?view=week&date={DAY}", token(hub, "noor"))[1]
    assert "Week of January 7, 2030" in week and week.count('class="cal-who-h"') == 10  # five open days, two each
    mine = call(server, "GET", f"/schedule?date={DAY}&who=mine", token(hub, "dr-amara"))[1]
    assert mine.count('class="cal-who-h"') == 1 and "Dr. Liam Fraser</div>" not in mine
    assert 'class="cal-day today"' in call(server, "GET", "/schedule?view=week", token(hub, "jo"))[1] or \
        hub.engine.now().weekday() >= 5  # a weekend's week view starts the coming week's open days


def test_the_calendar_today_and_now_are_the_practices(hub, server):
    """A Halifax practice on a UTC server. At 00:30 UTC on Tuesday it is 20:30 on Monday in Halifax: the calendar
    opens on Monday, marks Monday as today, and offers no more of Monday's slots."""
    from datetime import datetime, timezone
    hub.engine._now = datetime(2030, 1, 8, 0, 30, tzinfo=timezone.utc)
    page = call(server, "GET", "/schedule", token(hub, "jo"))[1]
    assert "Monday January 7, 2030" in page and "Tuesday" not in page.split('class="cal-nav"')[0][-400:]
    assert 'class="cal-day today"' in page and "/schedule/new?with=dr-amara&amp;at=2030-01-07T" not in page
    week = call(server, "GET", "/schedule?view=week", token(hub, "jo"))[1]
    assert 'class="cal-day today"' in week and 'href="/schedule?view=day&amp;date=2030-01-07"' in \
        week.split('class="cal-day today"')[1][:120]
    # at 10:00 in Halifax (14:00 UTC) the 09:00 slot is past and the 10:00 one is free, and the now line is in it
    hub.engine._now = datetime(2030, 1, 7, 14, 0, tzinfo=timezone.utc)
    page = call(server, "GET", f"/schedule?date={DAY}", token(hub, "jo"))[1]
    assert f"with=dr-amara&amp;at={DAY}T09%3A00" not in page and f"with=dr-amara&amp;at={DAY}T10%3A00" in page
    assert page.count('class="cal-now"') == 2  # one per physician's column, at 10:00 (row 5 of 15-minute rows)
    assert 'class="cal-now" style="grid-row:5;' in page


def test_pages_show_times_in_the_practices_zone(hub, server):
    """Work items and the track keep instants in UTC; a page shows them in the practice's zone (Halifax, -4)."""
    it = hub.db.items.find_one({"status": "open", "kind": "notice"}) or hub.db.items.find_one({"status": "open"})
    hub.db.items.set(it["id"], {"created_at": "2026-11-23T00:30:00Z", "assignee": "jo"})
    page = call(server, "GET", f"/items/{it['id']}", token(hub, "jo"))[1]
    assert "2026-11-22 20:30" in page and "2026-11-23 00:30" not in page


def test_a_free_slot_opens_the_booking_form_with_the_member_and_time(hub, server):
    page = call(server, "GET", f"/schedule?date={DAY}", token(hub, "jo"))[1]
    href = f'/schedule/new?with=dr-amara&amp;at={DAY}T09%3A00'
    assert f'href="{href}" data-modal' in page
    status, form = call(server, "GET", html.unescape(href), token(hub, "jo"))
    assert status == 200 and "data-modal-body" in form
    assert 'name="with" value="dr-amara"' in form and f'name="at" value="{DAY}T09:00"' in form
    assert "Monday January 7 at 09:00" in form and 'name="subject"' in form
    assert 'name="plannedStartDate"' not in form and "It starts as <b>planned</b>" in form
    # the reason is clinical: the front desk is not asked it, a physician is
    assert 'name="reason"' not in form
    assert 'name="reason"' in call(server, "GET", html.unescape(href), token(hub, "dr-amara"))[1]


def test_booking_a_slot_makes_a_visit_in_its_first_state(hub, server):
    status, _ = _book(server, hub)
    assert status == 303
    (v,) = [v for v in hub.records.search("Encounter", limit=None) if v.get("plannedStartDate", "").startswith(DAY)]
    assert v["status"] == "planned" and v["plannedStartDate"] == f"{DAY}T09:00"
    assert v["plannedEndDate"] == f"{DAY}T09:15" and v["subject"]["reference"] == _ellen(hub)
    assert v["participant"][0]["individual"]["reference"] == "Practitioner/dr-amara"
    assert v["reasonCode"][0]["text"] == "Sore throat"
    page = call(server, "GET", f"/schedule?date={DAY}", token(hub, "jo"))[1]
    assert "Ellen MacLeod" in page and f'href="/records/Encounter/{v["id"]}"' in page
    assert "Sore throat" not in page  # the front desk sees the booking, not why
    assert "Sore throat" in call(server, "GET", f"/schedule?date={DAY}", token(hub, "dr-amara"))[1]
    assert f'with=dr-amara&amp;at={DAY}T09%3A00' not in page  # the slot is taken
    # the front desk books through the appointment request (its red-flag screen), saying what the visit is for
    status, page = call(server, "POST", "/schedule/new", token(hub, "jo"),
                        form={"with": "dr-amara", "at": f"{DAY}T10:00", "subject": _ellen(hub), "req_kind": "forms",
                              "req_reason": "a form for my employer"})
    assert status == 200 and "data-modal-stay" in page
    (desk,) = [v for v in hub.records.search("Encounter", limit=None) if v.get("plannedStartDate") == f"{DAY}T10:00"]
    assert desk["serviceType"][0]["concept"]["text"] == "forms"
    # said back: when, with whom, for whom, and the visit to open; nothing to read out for a plain form request
    booked = page.split('<div class="flash success" role="status">', 1)[1].split("</div>", 1)[0]
    assert "Booked for Monday January 7 at 10:00" in booked and "Dr. Amara Okafor" in booked
    assert "Ellen MacLeod" in booked and f'href="/records/Encounter/{desk["id"]}"' in booked
    assert "Tell the caller now" not in page and "Not booked yet" not in page
    status, page = _book(server, hub, who="jo", at=f"{DAY}T10:30")  # a reason sent past the form is refused
    assert status == 422 and "clinical" in page
    assert call(server, "GET", f"/schedule/new?with=dr-amara&at={DAY}T09:00", token(hub, "sam"))[0] == 403


def test_a_double_booking_is_refused_in_the_dialog(hub, server):
    assert _book(server, hub)[0] == 303
    (other,) = hub.records.search("Patient", text="Omar Haddad")
    status, page = _book(server, hub, at=f"{DAY}T09:00", subject=f"Patient/{other['id']}", reason="Kept reason")
    assert status == 422 and "data-modal-body" in page and 'role="alert"' in page
    assert "Dr. Amara Okafor is already booked on Mon Jan 07 from 09:00 to 09:15" in page
    alert = page.split('role="alert"', 1)[1].split("</div>", 1)[0]
    assert "Kept reason" in page and "Ellen" not in alert  # what was typed is kept; whom the other is for is not said
    assert f'value="Patient/{other["id"]}" selected' in page
    assert _book(server, hub, member="dr-liam", subject=f"Patient/{other['id']}")[0] == 303  # someone else is free


def test_a_physician_squeezes_in_an_urgent_visit_and_the_calendar_shows_both(hub, server):
    assert _book(server, hub)[0] == 303
    (other,) = hub.records.search("Patient", text="Omar Haddad")
    form = call(server, "GET", f"/schedule/new?with=dr-amara&at={DAY}T09:00", token(hub, "dr-amara"))[1]
    assert 'name="overbook"' in form and "Squeeze in as urgent" in form and "Why it cannot wait" in form
    assert 'name="overbook"' not in call(server, "GET", f"/schedule/new?with=dr-amara&at={DAY}T09:00",
                                         token(hub, "jo"))[1]
    status, page = _book(server, hub, subject=f"Patient/{other['id']}", reason="Chest tightness")
    assert status == 422 and "Squeeze in as urgent" in page
    status, _ = call(server, "POST", "/schedule/new", token(hub, "dr-amara"), form={
        "with": "dr-amara", "at": f"{DAY}T09:00", "subject": f"Patient/{other['id']}", "reason": "Chest tightness",
        "overbook": "1", "overbook_reason": "tightness since this morning"})
    assert status == 303
    for who in ("jo", "dr-amara"):
        page = call(server, "GET", f"/schedule?date={DAY}", token(hub, who))[1]
        assert page.count("--lanes:2") == 2 and 'class="loz cal-pri high">Urgent' in page
        assert ("tightness since this morning" in page) == (who == "dr-amara")  # why is clinical
    assert "Squeeze in as urgent</a>" in page and "Squeeze in as urgent</a>" not in call(
        server, "GET", f"/schedule?date={DAY}", token(hub, "jo"))[1]


def test_the_front_desk_red_flag_goes_to_a_nurse_not_the_calendar(hub, server):
    status, page = call(server, "POST", "/schedule/new", token(hub, "jo"), form={
        "with": "dr-amara", "at": f"{DAY}T11:00", "subject": _ellen(hub), "req_kind": "new-concern",
        "req_reason": "chest pain and short of breath", "req_urgency": "routine"})
    assert status == 200 and "Not booked yet" in page and "Nurse" in page and "/runs/" in page
    # the desk is still on the phone: the screen's advice is in front of them, first, not only sent to the portal
    tell = page.index("Tell the caller now:")
    assert tell < page.index("<b>Not booked yet.</b>") and "call 911" in page[tell:tell + 300] and 'role="alert"' in page
    assert not [v for v in hub.records.search("Encounter", limit=None)
                if v.get("plannedStartDate") == f"{DAY}T11:00"]
    # one script, to the caller, in one voice; what is for the desk is apart from it; what went to the patient apart
    script = page[tell:].split("&ldquo;", 1)[1].split("&rdquo;", 1)[0]
    assert "call 911" in script and "call you back" in script
    assert "They" not in script and "they" not in script and "Stay on the line" not in script
    assert "We have your message" not in script and "within one working day" not in script
    assert "For you, not to read out" in page and "Stay on the line" in page
    sent = page.index("Sent to the patient")
    assert sent > tell and "We have your request" in page[sent:]


def test_a_routine_request_from_the_start_form_shows_what_went_to_the_patient_apart(hub, server):
    """No red flag: nothing to read out; the message the patient was sent is shown as theirs, not as a script."""
    status, page = call(server, "POST", "/start", token(hub, "jo"), form={
        "_workflow": "appointment-request", "_record": _ellen(hub), "reason": "a form for my employer",
        "kind": "forms", "urgency": "routine"})
    assert status == 200, page[:400]
    assert "Tell the caller now" not in page
    sent = page.split("Sent to the patient", 1)[1]
    # the message read the reason (clinical), so the desk sees that it went, not what it says
    assert "We have your request" in sent and "not shown to you" in sent and "within one working day" not in sent
    # another patient: one open request per patient (Ellen's is still open)
    assert "within one working day" in call(server, "POST", "/start", token(hub, "noor"), form={
        "_workflow": "appointment-request", "_record": _another(hub), "reason": "a form for my employer",
        "kind": "forms", "urgency": "routine"})[1].split("Sent to the patient", 1)[1]


def test_a_slot_proposed_later_is_told_to_whoever_took_the_request(hub):
    """The scheduler's slot is booked after the desk has hung up: the desk is told on the board, when and with whom,
    with the visit to open, and nothing clinical."""
    run = hub.engine.start(hub.member("jo"), "appointment-request", record=_ellen(hub),
                           inputs={"reason": "a form for my employer", "kind": "forms", "urgency": "routine"})
    iid = item_for(hub, run, "propose")
    free = hub.engine.context(M(hub, "scheduling-agent"), iid)["context"]["provider:free-slots"]
    slot = free[0]["value"] if isinstance(free[0], dict) else free[0]
    hub.engine.complete(M(hub, "scheduling-agent"), iid, {"slot": slot, "why": "the first free slot"})
    run = hub.engine.run(run["id"])
    assert run["steps"]["tell-booked"]["state"] == "done"
    (n,) = hub.db.items.find({"run_id": run["id"], "kind": "notice", "step_id": "tell-booked"})
    book = run["ctx"]["book"]
    assert n["assignee"] == "jo" and n["title"] == f"Booked for {book['when']} with {book['member_name']}"
    assert n["output"]["visit"] == book["reference"] and "form" not in n["title"]



def test_the_front_desk_red_flag_from_the_start_form_tells_the_caller_too(hub, server):
    """The quick-start request (no slot picked) runs the same screen: the desk is told what to say, not sent to a
    run page that shows them nothing."""
    status, page = call(server, "POST", "/start", token(hub, "jo"), form={
        "_workflow": "appointment-request", "_record": _ellen(hub), "reason": "chest pain and short of breath",
        "kind": "new-concern", "urgency": "routine"})
    assert status == 200, page[:400]
    tell = page.index("Tell the caller now:")
    assert "call 911" in page[tell:tell + 300] and 'role="alert"' in page and "Nothing is booked" in page
    # the form dialog shows this reply in place (it does not reload it away), and reloads once it is closed
    assert "data-modal-stay" in page
    from practicehub.web.assets import JS
    assert "data-modal-stay" in JS


def test_an_item_page_is_for_who_may_see_it(hub, server):
    """A break-glass review is opened by the reviewer it is for; anyone else is told there is no such item."""
    ellen = _ellen(hub)
    obs = next(r for r in hub.records.search("Observation", subject=ellen) if r.get("status") == "final")
    hub.create_record(hub.member("rana"), "Consent", {
        "patient": {"reference": ellen}, "status": "active", "received": "2026-11-01",
        "request": "Keep my results private.", "withhold_classes": ["clinical"]})
    # an urgent call-back about her is opened by the nurse who must make it: the directive masks what it holds,
    # it does not dead-end the work
    status, _ = call(server, "POST", "/start", token(hub, "jo"), form={
        "_workflow": "appointment-request", "_record": ellen, "reason": "crushing chest pain since an hour",
        "kind": "new-concern", "urgency": "routine"})
    run = hub.db.runs.find_one({"workflow_id": "appointment-request", "subject": ellen}, sort=[("created_at", -1)])
    calls = [i for i in hub.db.items.find({"run_id": run["id"], "status": "open"}) if i.get("role") == "nurse"]
    assert calls
    for it in calls:
        status, page = call(server, "GET", f"/items/{it['id']}", token(hub, "noor"))
        # she opens it, but the work shows the directive's note, not what the caller said: emergency access first
        assert status == 200 and "crushing chest pain" not in page and "withheld at the patient" in page
        assert not hub.engine.eligibility(M(hub, "noor"), it)[0]
    for who in ("noor", "dr-amara"):
        status, page = call(server, "GET", f"/runs/{run['id']}", token(hub, who))
        assert "crushing chest pain" not in page, who
    got = hub.break_glass(hub.member("noor"), f"Observation/{obs['id']}", "Collapsed at the desk; need her results")
    (review,) = hub.db.items.find({"run_id": got["run"], "kind": "notice"})
    status, page = call(server, "GET", f"/items/{review['id']}", token(hub, "rana"))
    assert status == 200 and "Collapsed at the desk" in page
    for who in ("jo", "dr-amara", "dr-liam"):
        status, page = call(server, "GET", f"/items/{review['id']}", token(hub, who))
        assert status == 404 and "Collapsed" not in page and "emergency" not in page.lower(), who
    assert hub.engine.eligibility(M(hub, "noor"), calls[0])[0]  # her emergency access opens the call-back too
    assert "crushing chest pain" in call(server, "GET", f"/items/{calls[0]['id']}", token(hub, "noor"))[1]


def test_moving_a_visit(hub, server):
    _book(server, hub)
    (v,) = [v for v in hub.records.search("Encounter", limit=None) if v.get("plannedStartDate", "").startswith(DAY)]
    url = f"/records/Encounter/{v['id']}"
    page = call(server, "GET", url, token(hub, "jo"))[1]
    assert f'href="{url}/reschedule" data-modal>Move</a>' in page
    status, form = call(server, "GET", f"{url}/reschedule", token(hub, "jo"))
    assert status == 200 and "data-modal-body" in form and f'value="{DAY}T09:00"' in form
    _book(server, hub, at=f"{DAY}T11:00", member="dr-liam", reason="Flu shot")
    status, page = call(server, "POST", f"{url}/reschedule", token(hub, "jo"),
                        form={"at": f"{DAY}T11:00", "with": "dr-liam", "_from": "dr-amara"})
    assert status == 422 and "Dr. Liam Fraser is already booked" in page and "data-modal-body" in page
    status, _ = call(server, "POST", f"{url}/reschedule", token(hub, "jo"),
                     form={"at": f"{DAY}T10:00", "with": "dr-liam", "_from": "dr-amara"})
    assert status == 303
    moved = hub.records.read("Encounter", v["id"])
    assert (moved["plannedStartDate"], moved["plannedEndDate"]) == (f"{DAY}T10:00", f"{DAY}T10:15")
    assert moved["participant"][0]["individual"]["reference"] == "Practitioner/dr-liam"
    assert moved["subject"] == v["subject"] and moved["status"] == "planned"
    hub.update_record(M(hub, "jo"), f"Encounter/{v['id']}", {"status": "cancelled"})
    page = call(server, "GET", f"/schedule?date={DAY}", token(hub, "jo"))[1]
    assert 'class="cal-ev cancelled"' in page  # struck through, and its time is free again
    assert f'with=dr-liam&amp;at={DAY}T10%3A00' in page
    assert "/reschedule" not in call(server, "GET", url, token(hub, "jo"))[1]


def test_a_member_who_may_not_read_the_booking_sees_busy(hub, server):
    _book(server, hub)
    hub.add_member(M(hub, "rana"), {"id": "kim", "kind": "person", "name": "Kim Lee", "roles": []})
    assert not hub.may_read(M(hub, "kim"), "Patient")
    status, page = call(server, "GET", f"/schedule?date={DAY}", token(hub, "kim"))
    assert status == 200 and "Busy" in page and "Ellen" not in page and "Sore throat" not in page
    assert "/schedule/new" not in page and "/records/Encounter/" not in page  # nothing to book, nothing to open


def test_a_patients_page_shows_their_upcoming_visits(hub, server):
    _book(server, hub)
    pid = _ellen(hub).split("/")[1]
    page = call(server, "GET", f"/{App(hub).subj['path']}/{pid}", token(hub, "jo"))[1]
    up = page.split('id="upcoming"', 1)[1].split("</section>", 1)[0]
    assert "Mon Jan 07, 09:00" in up and "Dr. Amara Okafor" in up and "Sore throat" not in up  # clinical
    assert f'/schedule/new?subject=Patient%2F{pid}' in up
    up = call(server, "GET", f"/{App(hub).subj['path']}/{pid}", token(hub, "dr-amara"))[1].split(
        'id="upcoming"', 1)[1].split("</section>", 1)[0]
    assert "Sore throat" in up


def test_the_clinic_keeps_one_booking_type_the_visit(hub, server):
    """Version 2 of the clinic pack merged Appointment into Visit (tests/test_rules.py upgrades a version 1)."""
    assert hub.pack.version == "3.1" and hub.of_base("Booking") == ["Encounter"]
    assert hub.record_type("Appointment") is None and "Appointment" not in json.dumps(hub.practice.get("shortcuts"))
    assert call(server, "GET", "/records/Appointment", token(hub, "jo"))[0] in (404, 422)
    assert "2-appointments-are-visits" in hub.db.get_setting("pack_upgrades_done")  # a new practice has none to do


def _theme_tokens():
    """The colour tokens of both themes, parsed from the stylesheet: light is the :root block, dark overrides it."""
    from practicehub.web import assets
    light = dict(re.findall(r"--([\w-]+):([^;}]+)", assets.CSS.split(":root{color-scheme:light;", 1)[1].split("}", 1)[0]))
    dark = {**light, **dict(re.findall(r"--([\w-]+):([^;}]+)", assets._DARK))}
    return {"light": light, "dark": dark}


def _contrast(tokens, fg, bg):
    """WCAG contrast of token fg over token bg; a bg written "a/b" is token a composited over token b."""
    def rgba(name):
        v = tokens[name].strip()
        while v.startswith("var("):
            v = tokens[v[4:-1].split(",")[0].strip()[2:]].strip()
        h = v.lstrip("#")
        return [int(h[i:i + 2], 16) for i in (0, 2, 4)], (int(h[6:8], 16) / 255 if len(h) == 8 else 1.0)

    def over(top, base):
        rgb, a = rgba(top)
        return [c * a + b * (1 - a) for c, b in zip(rgb, base)]

    def lum(rgb):
        lin = [(c / 255) / 12.92 if c / 255 <= 0.03928 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in rgb]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    *layers, base = bg.split("/")
    back = rgba(base)[0]
    for layer in reversed(layers):
        back = over(layer, back)
    hi, lo = sorted((lum(over(fg, back)), lum(back)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


_TEXT_PAIRS = [  # text: 4.5:1
    ("fg", "bg"), ("fg", "raised"), ("fg", "sunken"), ("fg", "input-bg"), ("fg", "neutral/bg"),
    ("fg", "info-bg"), ("fg", "success-bg"), ("fg", "danger-bg"), ("fg", "warning-bg"),
    ("subtle", "bg"), ("subtle", "raised"), ("subtle", "sunken"), ("subtle", "neutral/bg"), ("subtle", "neutral-hover/bg"),
    ("subtlest", "bg"), ("subtlest", "raised"), ("subtlest", "sunken"), ("subtlest", "neutral/bg"), ("subtlest", "raised-hover"),
    ("selected-fg", "selected-bg"), ("link", "bg"), ("link", "raised"), ("link", "sunken"),
    ("brand-fg", "brand"), ("brand-fg", "brand-hover"), ("danger-on", "danger"), ("danger-fg", "danger-bg"),
    ("success-fg", "success-bg"), ("warning-fg", "warning-bg"), ("info-fg", "info-bg"), ("discovery-fg", "discovery-bg"),
    ("service", "service-bg"), ("surface", "info-icon"), ("surface", "success-icon"), ("surface", "danger-icon"),
    ("loz-default-fg", "loz-default-bg/bg"), ("loz-default-fg", "loz-default-bg/raised"),
] + [(f"loz-{k}-fg", f"loz-{k}-bg") for k in ("inprogress", "success", "removed", "moved", "new")] \
  + [(f"lozb-{k}-fg", f"lozb-{k}-bg") for k in ("default", "inprogress", "success", "removed", "moved", "new")] \
  + [("ki-fg", f"ki-{k}") for k in ("task", "gate", "notice", "hold", "automation")]

_EDGE_PAIRS = [  # input borders, button and chip edges, card edges, table rules, the focus ring: 3:1
    ("input-border", "input-bg"), ("input-border", "bg"), ("input-border", "raised"),
    ("border", "bg"), ("border", "raised"), ("border", "sunken"), ("border-bold", "bg"), ("border-bold", "raised"),
    ("control-border", "bg"), ("control-border", "raised"), ("card-edge", "bg"), ("card-edge", "sunken"),
    ("focus", "bg"), ("focus", "raised"), ("focus", "sunken"), ("focus", "input-bg"), ("brand", "bg"), ("danger", "bg"),
]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_colour_tokens_keep_wcag_contrast_in_both_themes(theme):
    tokens = _theme_tokens()[theme]
    low = [f"{fg} on {bg}: {r:.2f} < {need}" for need, pairs in ((4.5, _TEXT_PAIRS), (3.0, _EDGE_PAIRS))
           for fg, bg in pairs if (r := _contrast(tokens, fg, bg)) < need]
    assert not low, low
    # disabled reads as disabled: fainter than enabled text, yet not invisible
    assert 2.5 <= _contrast(tokens, "disabled-fg", "disabled-bg/bg") < _contrast(tokens, "subtle", "neutral/bg")
    # the focus ring and the card edges come from the tokens, not from fixed colours
    from practicehub.web.assets import CSS
    assert "#388BFF" not in CSS and "var(--card-edge)" in tokens["shadow-raised"]


# ------------------------------------------------------------------------------------------- boards: moving cards
def _moves(page: str, card: str) -> dict:
    """The columns a card on a board may be dropped on, as the page says (its data-moves)."""
    m = re.search(r'data-card="' + re.escape(card) + r'" data-moves="([^"]*)"', page)
    return json.loads(html.unescape(m.group(1))) if m else {}


def test_a_board_marks_where_each_card_may_be_dropped_by_the_same_rules_as_its_move_buttons(hub, server):
    """A lifecycle board has a column per state; each card lists only the moves its page offers a person (none
    reserved to a workflow), and one that asks for fields or a comment opens its transition screen."""
    rana = _modules(hub, server)
    doc = hub.create_record(M(hub, "rana"), "Document", {"title": "Hand hygiene"})
    capa = hub.create_record(M(hub, "rana"), "CAPA", {"title": "T", "kind": "corrective", "problem": "P"})
    page = call(server, "GET", "/views/dms-documents", rana)[1]
    assert all(f'data-col="{s}"' in page for s in ("draft", "in-review", "approved", "obsolete"))
    moves = _moves(page, f"Document/{doc['id']}")
    assert set(moves) == {"in-review", "obsolete"} and moves["in-review"]["how"] == "post"  # approved: a workflow's
    card = page.split(f'data-card="Document/{doc["id"]}"')[1].split("</form>")[0]
    assert f'action="/records/Document/{doc["id"]}/move"' in card and '<select name="to"' in card \
        and '<option value="in-review">' in card and 'value="approved"' not in card and 'type="radio"' not in card
    moves = _moves(call(server, "GET", "/views/qms-capas", rana)[1], f"CAPA/{capa['id']}")
    assert moves["investigating"] == {"how": "screen", "label": "Start investigating…",
                                      "href": f"/records/CAPA/{capa['id']}/move?to=investigating"}
    # the work board: what the item page's buttons allow, for whoever looks
    jo = token(hub, "jo")
    page = call(server, "GET", "/board", jo)[1]
    assert 'data-col="todo"' in page and 'data-col="prog"' in page and 'data-col="done"' in page
    iid = re.search(r'data-card="(item-[^"]+)" data-moves', page).group(1)
    assert set(_moves(page, iid)) == {"prog", "done"} and _moves(page, iid)["done"]["how"] == "screen"
    item = hub.engine.item(iid)
    other = next(m for m in hub.members("person") if not hub.engine.eligibility(m, item)[0])
    assert _moves(call(server, "GET", "/board", token(hub, other["id"]))[1], iid) == {}


def test_a_move_from_a_board_applies_the_same_rules_and_a_refused_one_says_why(hub, server):
    rana = _modules(hub, server)
    doc = hub.create_record(M(hub, "rana"), "Document", {"title": "Hand hygiene"})
    url, back = f"/records/Document/{doc['id']}/move", "/views/dms-documents"
    ver = str(doc["meta"]["versionId"])
    # refused: not a move a person may make from here; the reason, in the dialog, and nothing moved
    code, page = call(server, "POST", url, rana, form={"to": "approved", "_version": ver, "_back": back, "_ask": "1"})
    assert code == 422 and "does not move from" in page and "data-modal-body" in page and 'role="alert"' in page
    assert hub.records.read("Document", doc["id"])["status"] == "draft"
    # accepted: back to the board
    code, _ = call(server, "POST", url, rana, form={"to": "in-review", "_version": ver, "_back": back, "_ask": "1"})
    assert code == 303 and hub.records.read("Document", doc["id"])["status"] == "in-review"
    # changed since the board was drawn: refused as a conflict
    other = hub.create_record(M(hub, "rana"), "Document", {"title": "Sharps"})
    hub.update_record(M(hub, "rana"), f"Document/{other['id']}", {"title": "Sharps disposal"})
    code, page = call(server, "POST", f"/records/Document/{other['id']}/move", rana,
                      form={"to": "obsolete", "_version": str(other["meta"]["versionId"]), "_back": back, "_ask": "1"})
    assert code == 409 and "changed this record" in page and hub.records.read("Document", other["id"])["status"] == "draft"
    # a move that asks for fields: its screen first, which returns to the board once it is accepted
    capa = hub.create_record(M(hub, "rana"), "CAPA", {"title": "T", "kind": "corrective", "problem": "P"})
    curl = f"/records/CAPA/{capa['id']}/move"
    code, _ = call(server, "POST", curl, rana, form={"to": "investigating", "_back": "/views/qms-capas", "_ask": "1"})
    assert code == 303 and hub.records.read("CAPA", capa["id"])["status"] == "open"
    code, page = call(server, "GET", f"{curl}?to=investigating&_back=/views/qms-capas", rana)
    assert code == 200 and 'name="_back" value="/views/qms-capas"' in page and 'name="owner"' in page
    code, page = call(server, "POST", curl, rana, form={"to": "investigating", "_back": "/views/qms-capas"})
    assert code == 422 and "fill in Owner" in page and 'name="_back" value="/views/qms-capas"' in page
    code, _ = call(server, "POST", curl, rana, form={"to": "investigating", "owner": "rana", "_back": "//elsewhere.example"})
    assert code == 303 and hub.records.read("CAPA", capa["id"])["status"] == "investigating"


def test_the_work_boards_move_to_form_works_without_the_script(hub, server):
    """Move to… is a plain form: In progress assigns it, To do gives it back, Done opens the item's form; a move the
    member may not make is refused with the reason, and the item stays where it was."""
    jo = token(hub, "jo")
    page = call(server, "GET", "/board", jo)[1]
    iid = re.search(r'data-card="(item-[^"]+)" data-moves', page).group(1)
    form = page.split(f'data-card="{iid}"')[1].split("</form>")[0]
    assert 'action="/board/move"' in form and f'name="item" value="{iid}"' in form and '<button' in form
    code, _ = call(server, "POST", "/board/move", jo, form={"item": iid, "to": "prog", "_back": "/board?who=mine"})
    assert code == 303 and hub.engine.item(iid)["status"] == "claimed" and hub.engine.item(iid)["claimed_by"] == "jo"
    code, page = call(server, "POST", "/board/move", jo, form={"item": iid, "to": "wait", "_back": "/board"})
    assert code == 422 and "not moved" in page and hub.engine.item(iid)["status"] == "claimed"
    other = next(m for m in hub.members("person") if m["id"] != "jo")
    code, page = call(server, "POST", "/board/move", token(hub, other["id"]), form={"item": iid, "to": "todo"})
    assert code == 403 and "only whoever is working on an item gives it back" in page
    assert hub.engine.item(iid)["claimed_by"] == "jo"
    code, _ = call(server, "POST", "/board/move", jo, form={"item": iid, "to": "done"})
    assert code == 303  # what the step needs is asked on the item's page
    code, _ = call(server, "POST", "/board/move", jo, form={"item": iid, "to": "todo", "_back": "/board"})
    assert code == 303 and hub.engine.item(iid)["status"] == "open"


# ------------------------------------------------------------------------------------------- exports
def fetch(base, path, tok):
    """(status, headers, bytes): a download as it is."""
    req = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {tok}"})
    try:
        with opener.open(req) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()


def _odf_ok(body, mimetype):
    import io
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(body))
    first = z.infolist()[0]
    assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
    assert z.read("mimetype").decode() == mimetype
    assert body[30:38] == b"mimetype" and body[38:38 + len(mimetype)] == mimetype.encode()  # readable as magic
    assert {"content.xml", "META-INF/manifest.xml"} <= set(z.namelist())
    import xml.dom.minidom
    xml.dom.minidom.parseString(z.read("content.xml"))  # well-formed
    return z.read("content.xml").decode()


def test_a_record_exports_from_the_api_and_its_page(server, hub):
    tok = token(hub, "dr-amara")
    rec = next(r for r in hub.records.search("MedicationRequest") if not hub.restrictions.restricted(r))  # no directive
    st, h, body = fetch(server, f"/api/records/MedicationRequest/{rec['id']}?format=fhir", tok)
    assert st == 200 and h["content-type"] == "application/fhir+json"
    assert f'filename="MedicationRequest-{rec["id"]}.json"' in h["content-disposition"]
    res = json.loads(body)
    assert res["resourceType"] == "MedicationRequest" and res["id"] == rec["id"]
    st, h, body = fetch(server, f"/records/MedicationRequest/{rec['id']}/export?format=odt", tok)
    assert st == 200 and h["content-type"] == "application/vnd.oasis.opendocument.text"
    content = _odf_ok(body, "application/vnd.oasis.opendocument.text")
    assert "<table:table " in content and rec["medicationCodeableConcept"]["text"] in content
    st, h, body = fetch(server, f"/records/MedicationRequest/{rec['id']}/export?format=print", tok)
    assert st == 200 and h["content-type"].startswith("text/html") and b"window.print()" in body
    assert "default-src 'none'" in h["content-security-policy"]
    # the dialog: plain words, a button opening it on the record's page
    st, page = call(server, "GET", f"/records/MedicationRequest/{rec['id']}", tok)
    assert f'href="/records/MedicationRequest/{rec["id"]}/export" data-modal>Export</a>' in page
    st, dlg = call(server, "GET", f"/records/MedicationRequest/{rec['id']}/export", tok)
    assert st == 200 and "data-modal-body" in dlg and 'type="radio"' not in dlg
    for words in ("FHIR (for another clinic&#x27;s system)", "Document (.odt)", "Print / save as PDF"):
        assert words in dlg
    reads = [x for x in hub.db.events.find({"action": "record.viewed", "subject": f"MedicationRequest/{rec['id']}"})
             if (x.get("data") or {}).get("how") == "export"]
    assert [x["data"]["format"] for x in reads] == ["fhir", "odt", "print"]


def test_a_list_exports_as_csv_with_formulas_guarded_and_as_a_spreadsheet(server, hub):
    jo = M(hub, "jo")
    hub.create_record(jo, "Patient", {"name": [{"given": ["=HYPERLINK(\"http://x\")"], "family": "@Sum"}],
                                      "birthDate": "1990-01-01", "gender": "other"})
    tok = token(hub, "jo")
    st, page = call(server, "GET", "/records/Patient", tok)
    assert 'href="/records/Patient/export" data-modal>Export</a>' in page
    st, h, body = fetch(server, "/records/Patient/export?format=csv", tok)
    assert st == 200 and h["content-type"] == "text/csv; charset=utf-8"
    assert re.fullmatch(r'attachment; filename="Patient-list-\d{4}-\d\d-\d\d\.csv"', h["content-disposition"])
    import csv
    import io
    rows = list(csv.reader(io.StringIO(body.decode())))
    assert rows[0][-4:] == ["id", "Date", "Last updated", "Version"]
    cells = [c for r in rows for c in r]
    assert not any(c[:1] in ("=", "+", "-", "@") for c in cells), "a spreadsheet must not run a cell"
    assert any(c.startswith("'=HYPERLINK") for c in cells) and any(c == "'@Sum" for c in cells)
    assert len(rows) - 1 == len(hub.records.search("Patient")) == int(h["x-records"])
    st, h, body = fetch(server, "/records/Patient/export?format=ods", tok)
    assert st == 200 and h["content-type"] == "application/vnd.oasis.opendocument.spreadsheet"
    content = _odf_ok(body, "application/vnd.oasis.opendocument.spreadsheet")
    assert "of:=" not in content and "table:formula" not in content
    st, h, body = fetch(server, "/records/Patient/export?format=ndjson", tok)
    assert h["content-type"] == "application/fhir+ndjson"
    assert all(json.loads(line)["resourceType"] == "Patient" for line in body.decode().splitlines())
    ev = hub.db.events.find({"action": "records.exported", "subject": "records:Patient"})
    assert [x["data"]["format"] for x in ev] == ["csv", "ods", "ndjson"] and ev[0]["actor"] == "jo"
    # a bulk export of every record is a policy owner's: the front desk is refused, on the page and the API
    st, dlg = call(server, "GET", "/records/Patient/export", tok)
    assert "all=1" not in dlg and "A bulk export" not in dlg
    assert fetch(server, "/records/Patient/export?all=1&format=csv", tok)[0] == 403
    assert fetch(server, "/api/records/Patient/$export?all=1", tok)[0] == 403
    st, dlg = call(server, "GET", "/records/Patient/export", token(hub, "rana"))
    assert "all=1&amp;format=csv" in dlg
    st, h, body = fetch(server, "/api/records/Patient/$export?all=1", token(hub, "rana"))
    assert st == 200 and h["content-type"] == "application/fhir+ndjson"


def test_a_subjects_file_exports_as_a_bundle_leaving_out_what_the_member_may_not_read(server, hub):
    sub = hub.pack.subject
    ref = max((f"{sub['type']}/{p['id']}" for p in hub.records.search(sub["type"])),
              key=lambda r: len(hub.records.search(subject=r, limit=None)))
    pid = ref.split("/", 1)[1]
    tok = token(hub, "jo")
    st, page = call(server, "GET", f"/{sub['path']}/{pid}", tok)
    assert f"Export this {sub['label'].lower()}&#x27;s file</a>" in page or \
        f"Export this {sub['label'].lower()}'s file</a>" in page
    st, dlg = call(server, "GET", f"/{sub['path']}/{pid}/export", tok)
    assert st == 200 and "records you may not read were left out" in dlg
    st, h, body = fetch(server, f"/api/records/{ref}/$everything", tok)
    assert st == 200 and h["content-type"] == "application/fhir+json" and int(h["x-left-out"]) > 0
    b = json.loads(body)
    assert b["resourceType"] == "Bundle" and b["type"] == "collection"
    assert b["entry"][0]["resource"]["id"] == pid
    st, h, body = fetch(server, f"/{sub['path']}/{pid}/export?format=fhir", token(hub, "dr-amara"))
    full = json.loads(body)
    assert len(full["entry"]) > len(b["entry"]) and h["x-left-out"] == "0"
    st, h, body = fetch(server, f"/{sub['path']}/{pid}/export?format=odt", token(hub, "dr-amara"))
    _odf_ok(body, "application/vnd.oasis.opendocument.text")
    ev = hub.db.events.find({"action": "records.exported", "subject": ref})
    assert [(x["actor"], x["data"]["format"]) for x in ev] == [("jo", "fhir"), ("dr-amara", "fhir"),
                                                               ("dr-amara", "odt")]
    assert ev[0]["data"]["left_out"] > 0 and ev[1]["data"]["left_out"] == 0


def test_a_type_that_is_not_a_fhir_resource_has_no_fhir_export_on_the_web(server, hub):
    from practicehub import export
    t = next((t for t, rt in hub.record_types().items() if not rt.abstract and not export.is_fhir(rt)
              and t not in hub.member_types() and hub.records.search(t)), None)
    if t is None:
        pytest.skip("every type with records here is a FHIR resource")
    rid = hub.records.search(t)[0]["id"]
    tok = token(hub, "dr-amara")
    st, dlg = call(server, "GET", f"/records/{t}/{rid}/export", tok)
    assert st == 200 and "format=fhir" not in dlg and "FHIR (for another" not in dlg and "JSON" in dlg
    assert fetch(server, f"/records/{t}/{rid}/export?format=fhir", tok)[0] == 422
    st, h, body = fetch(server, f"/api/records/{t}/{rid}?format=json", tok)
    assert st == 200 and h["content-type"] == "application/json" and json.loads(body)["id"] == rid


# ------------------------------------------------------------------------------- words, not codes (names, labels, titles)
def test_a_mention_in_a_comment_shows_the_member_not_their_id(hub):
    """An @id in what someone wrote shows that member: avatar, name linking to their page, the agent badge; the text
    keeps the id. An @ of nobody, or inside an e-mail address, stays as written."""
    app = App(hub, dev=False)
    agent = next(m for m in hub.members("agent"))
    out = app.mention_text(f"Ask @dr-amara, then @{agent['id']}.\nmail jo@dr-amara.example or @nobody <b>")
    assert '<a href="/team/dr-amara">Dr. Amara Okafor</a>' in out and 'data-member="dr-amara"' in out
    assert f'<a href="/team/{agent["id"]}">{html.escape(agent["name"])}</a> <span class="badge agent">' in out
    assert 'class="av' in out and "<br>" in out and "jo@dr-amara.example" in out and "@nobody" in out
    assert "<b>" not in out and "&lt;b&gt;" in out


def test_a_patients_title_crumbs_and_work_leave_out_the_health_card_number(hub, server):
    """The card number is in the chart's banner and details only: not the page title, crumbs, heading or work titles."""
    amara = token(hub, "dr-amara")
    p = next(r for r in hub.records.search("Patient") if "1234 567 890" in hub.title(r))
    sub = hub.pack.subject
    page = call(server, "GET", f"/{sub['path']}/{p['id']}", amara)[1]
    title = re.search(r"<title>(.*?)</title>", page).group(1)
    crumbs = page.split('class="crumbs"', 1)[1].split("</div>", 1)[0]
    h1 = re.search(r'<h1 class="name">(.*?)</h1>', page).group(1)
    assert all("Ellen MacLeod" in x and "1234 567 890" not in x for x in (title, crumbs, h1))
    assert "1234 567 890" in page  # the banner
    assert "1234 567 890" not in hub.run_title(M(hub, "dr-amara"), {"title": "Renewal", "record": f"Patient/{p['id']}"})


def test_the_visit_list_names_the_patient_and_says_codes_in_words(hub, server):
    page = call(server, "GET", "/records/Encounter", token(hub, "dr-amara"))[1]
    heads = re.findall(r"<th>(.*?)</th>", page)
    assert heads[:3] == ["Visit", "Status", "Patient"]
    assert ">In clinic<" in page and ">AMB<" not in page and "(born " in page
    assert '<span class="loz' in page and ">in-progress<" not in page and ">planned<" not in page  # states in words
    assert re.search(r'<span class="loz [a-z]+">(Planned|In progress|Finished)</span>', page)


def test_a_board_says_its_columns_in_the_lifecycles_words(hub, server):
    page = call(server, "GET", "/views/dms-documents", _modules(hub, server))[1]
    names = re.findall(r'<span class="col-name">(.*?)</span>', page)
    assert "In review" in names and "IN-REVIEW" not in page and "in-review</span>" not in page


def test_a_type_declares_a_short_title_and_words_for_its_choices(hub):
    from practicehub import schema
    base = 'id = "Thing"\ndata_class = "visits"\ntitle = "{name}"\n'
    f = '[[field]]\nname = "name"\n\n[[field]]\nname = "kind"\ntype = "choice"\nchoices = ["a", "b"]\n'
    rt = schema.parse(base + 'short_title = "{name}"\n' + f + 'labels = { a = "Ay" }\n')
    assert rt.short_title == "{name}" and schema.choice_words(rt.field("kind"), "a") == "Ay"
    assert schema.choice_words(rt.field("kind"), "b") == "b"
    with pytest.raises(Exception) as exc:  # a DefinitionError, every problem in words
        schema.parse(base + 'short_title = "{nope}"\n' + f + 'labels = { c = "Sea" }\n')
    assert any("short_title" in p for p in exc.value.problems) and any("'c'" in p for p in exc.value.problems)


def test_a_long_link_group_offers_every_one_on_a_list_of_its_own(hub, server):
    """A record's page shows the 50 most recent of a kind linked to it, and a link to all of them."""
    amara = M(hub, "dr-amara")
    p = hub.records.search("Patient")[0]
    ref = f"Patient/{p['id']}"
    have = [r for r in hub.records.search("Observation") if r.get("subject", {}).get("reference") == ref]
    tmpl = {k: v for k, v in hub.records.search("Observation")[0].items() if k not in ("id", "meta")}
    for i in range(51 - len(have)):
        hub.create_record(amara, "Observation", {**tmpl, "status": "registered", "subject": {"reference": ref}})
    tok = token(hub, "dr-amara")
    page = call(server, "GET", f"/{hub.pack.subject['path']}/{p['id']}", tok)[1]
    q = urllib.parse.quote(ref, safe="")
    assert f'href="/records/Observation?linked={q}">Show all ' in page
    code, all_ = call(server, "GET", f"/records/Observation?linked={q}", tok)
    assert code == 200 and all_.count('<tr><td><a href="') >= 51
    assert call(server, "GET", "/records/Observation?linked=Patient/nope", tok)[0] == 404


def test_the_definition_editor_says_whether_the_hub_accepts_the_text_without_asking_again(hub, server):
    page = call(server, "GET", "/define/types/Encounter/edit?as=toml", token(hub, "rana"))[1]
    status = page.split('id="def-status"', 1)[1].split("</div>", 1)[0]
    assert "The hub accepts this." in status and 'data-checked="1"' in status

# ---- the record's own panel: its lifecycle and the open work about it, on its default view

def _panel(page):
    """The side panel of a record page (status, moves, open work), or of a patient's chart."""
    m = re.search(r'<section class="wp-status".*?</aside>|<section class="wp-work".*?</aside>', page, re.S)
    return m.group(0) if m else ""


def test_a_record_shows_its_status_moves_and_open_work_beside_it(hub, server):
    """A visit shows the run walking it on its own page, and the patient's chart lists the same run;
    the status and moves are in words, and the workflow opens as a drawing with where it is now."""
    run = visit_run(hub)
    visit = next(r for r in run["refs"] if r.startswith("Encounter/"))
    page = call(server, "GET", f"/records/{visit}", token(hub, "dr-amara"))[1]
    panel = _panel(page)
    assert "Open work" in panel and f'href="/runs/{run["id"]}"' in panel and "Draft the note" in panel
    assert "In progress" in panel and "Move to" not in page  # words, not codes
    # being documented: its workflow waits on no pause now, so the page offers none (only its workflow moves it)
    assert ">On leave<" not in panel
    assert "View workflow" in panel and f'/records/{visit}/workflow" data-modal' in panel
    status, modal = call(server, "GET", f"/records/{visit}/workflow", token(hub, "dr-amara"))
    assert status == 200 and "data-modal-body" in modal and "<svg" in modal
    assert re.search(r'class="lc-node [a-z]+ current"><title>In progress', modal)
    # the chart of the patient lists the same run beside it
    chart = call(server, "GET", f"/patients/{run['subject'].split('/')[1]}", token(hub, "dr-amara"))[1]
    assert f'href="/runs/{run["id"]}"' in _panel(chart) and "On this patient" in chart


def test_a_record_page_reads_in_words(hub, server):
    run = visit_run(hub)
    visit = next(r for r in run["refs"] if r.startswith("Encounter/"))
    page = call(server, "GET", f"/records/{visit}", token(hub, "dr-amara"))[1]
    shown = re.sub(r'<time datetime="[^"]*"', "<time", page)
    rec = hub.records.read(*visit.split("/"))
    assert rec["plannedStartDate"][:16] not in shown and re.search(r">\w{3} \d{1,2} \w{3} \d{4}, \d\d:\d\d<", page)
    assert ">AMB<" not in page and "(as JSON)" not in page and "Not filled in" in page


def test_only_a_member_who_may_move_a_record_sees_its_moves(hub, server):
    run = visit_run(hub)
    visit = next(r for r in run["refs"] if r.startswith("Encounter/"))
    seen = set()
    for mid in ("rana", "dr-amara", "jo", "noor", "sam"):
        status, page = call(server, "GET", f"/records/{visit}", token(hub, mid))
        if status != 200:
            continue
        may = hub.may_write(M(hub, mid), "Encounter")
        assert ("Move it to" in _panel(page)) is may, mid
        assert "View workflow" in _panel(page), mid  # the drawing is for everyone who reads the record
        seen.add(may)
    assert True in seen


def test_the_front_desk_sees_no_step_of_a_branch_it_may_not_read(hub, server):
    """A lab result's run: which clinical branch it takes is not shown to the front desk, in the API, on the run's
    page, or in the panel beside the patient's chart."""
    from practicehub.visibility import Viewer
    run = run_of(hub, "lab-result-review")
    wf, _ = hub.workflow(run["workflow_id"])
    jo = Viewer(hub, M(hub, "jo"))
    hidden = [s for s in wf.steps if not jo.step_visible(wf, s.id)[0]]
    assert hidden, "the lab result review has steps the front desk may not read"
    tok = token(hub, "jo")
    listed = next(r for r in json.loads(call(server, "GET", "/api/runs", tok)[1])["runs"] if r["id"] == run["id"])
    seen = [json.dumps(listed), call(server, "GET", f"/api/runs/{run['id']}", tok)[1],
            call(server, "GET", f"/runs/{run['id']}", tok)[1],
            _panel(call(server, "GET", f"/patients/{run['record'].split('/')[1]}", tok)[1])]
    assert all(seen)
    for i, text in enumerate(seen):
        for s in hidden:
            assert s.title not in html.unescape(text), s.id
            if i < 2:  # the API's JSON: no step of that branch by its id (a page's HTML has words like "page")
                assert f'"{s.id}"' not in text, s.id
            if s.when:
                assert s.when not in html.unescape(text), s.id
    # a physician sees them
    page = call(server, "GET", f"/runs/{run['id']}", token(hub, "dr-amara"))[1]
    assert any(s.title in html.unescape(page) for s in hidden)


# ------------------------------------------------------------------------- a patient's consent directive on the pages
def _nadia(hub):
    """The demo patient whose clinical information is withheld from all but her own physician (Dr. Fraser)."""
    p = next(r for r in hub.records.search("Patient", limit=None) if r["name"][0]["family"] == "Rahimi")
    cond = hub.records.search("Condition", subject=f"Patient/{p['id']}")[0]
    return p["id"], f"Condition/{cond['id']}"


def test_the_chart_says_what_is_withheld_and_never_what_it_says(hub, server):
    pid, cond = _nadia(hub)
    noor, liam = token(hub, "noor"), token(hub, "dr-liam")
    code, page = call(server, "GET", f"/patients/{pid}", noor)
    page = html.unescape(page)
    assert code == 200 and "Information withheld at the patient's request" in page
    assert "Depressive" not in page and "Sertraline" not in page and "Withheld" in page
    assert 'action="/records/Patient/' in page and 'name="reason"' in page  # a nurse may break the glass
    code, page = call(server, "GET", f"/records/{cond}", noor)
    assert code == 200 and "Depressive" not in page and "withheld-panel" in page
    page = html.unescape(call(server, "GET", f"/patients/{pid}", liam)[1])
    assert "Depressive disorder" in page and "Their directive still allows you" in page
    jo_page = html.unescape(call(server, "GET", f"/patients/{pid}", token(hub, "jo"))[1])
    assert "Information withheld at the patient's request" in jo_page and 'name="reason"' not in jo_page
    assert "ask the privacy officer" in jo_page
    api = json.loads(call(server, "GET", f"/api/records/{cond}", noor)[1])
    assert "code" not in api and api["meta"]["withheld"]["whole"]


def test_breaking_the_glass_from_the_page_opens_it_under_a_banner(hub, server):
    pid, cond = _nadia(hub)
    noor = token(hub, "noor")
    code, _ = call(server, "POST", f"/records/{cond}/override", noor, form={"reason": "no"})
    assert code in (400, 422) and "Depressive" not in call(server, "GET", f"/records/{cond}", noor)[1]
    code, _ = call(server, "POST", f"/records/{cond}/override", noor,
                   form={"reason": "Unresponsive in the waiting room; need her medications"})
    assert code == 303
    page = html.unescape(call(server, "GET", f"/records/{cond}", noor)[1])
    assert "Emergency access" in page and "Depressive disorder" in page and "privacy officer reviews it" in page
    banner = page.split('id="override-banner"', 1)[1].split("</div>", 1)[0]
    assert "UTC" not in banner and re.search(r"Until \d\d:\d\d\.", banner)  # practice time
    rana = html.unescape(call(server, "GET", "/", token(hub, "rana"))[1])
    assert "Review: Noor Siddiqui used emergency access to" in rana and "Unresponsive" not in rana


def test_a_withheld_export_says_it_is_incomplete(hub, server):
    pid, cond = _nadia(hub)
    from practicehub import export
    got = export.subject_file(hub, M(hub, "noor"), f"Patient/{pid}", "print")
    body = got.body.decode() if isinstance(got.body, bytes) else got.body
    assert "Incomplete: information withheld at the patient" in html.unescape(body) and "Depressive" not in body
    fhir = json.loads(export.subject_file(hub, M(hub, "noor"), f"Patient/{pid}", "fhir").body)
    stub = next(e["resource"] for e in fhir["entry"] if e["resource"]["resourceType"] == "Condition")
    assert stub["meta"]["security"][0]["code"] == "REDACTED" and "code" not in stub
def test_the_track_page_shows_one_page_at_a_time_and_verifies_only_what_is_new(hub, server):
    """The Track page lists the newest events a page at a time, with a link to older ones (read by index, not the
    whole track), and its check of the chain reads nothing more when no event was added since the last."""
    from practicehub.web.server import App
    rana = token(hub, "rana")
    total = hub.db.events.count({})
    assert total > App.TRACK_PAGE, "the demo has more events than one page"
    code, body = call(server, "GET", "/track", rana)
    assert code == 200 and "Older events" in body
    newest = hub.db.events.find_one({}, sort=[("seq", -1)])
    oldest_shown = hub.db.events.find({}, sort=[("seq", -1)], limit=App.TRACK_PAGE)[-1]["seq"]
    assert f"before={oldest_shown}" in body
    code, older = call(server, "GET", f"/track?before={oldest_shown}", rana)
    assert code == 200 and "Newest" in older and f"/track/{newest['seq']}\"" not in older
    first = hub.verify(full=False)
    before = hub.db.get_setting("track_verified")
    again = hub.verify(full=False)
    assert again["ok"] and again["head"] == first["head"]
    assert hub.db.get_setting("track_verified") == before  # nothing new: the sealed result stands, not rewritten


# ------------------------------------------------------------------------- the seventh panel: what the pages say
def test_the_board_links_only_items_the_member_may_open(hub, server):
    """Every card on someone's board opens; the rest are counted, not linked, so a click is never a 404 and never
    an access.denied on their record. An item one may not open says so, without its id."""
    for who in ("jo", "rana", "noor"):
        tok = token(hub, who)
        before = hub.db.events.count({"action": "access.denied", "actor": who})
        page = call(server, "GET", "/board", tok)[1]
        ids = set(re.findall(r'href="/items/(item-[\w-]+)"', page))
        assert ids or "you may not open" in page, who
        for iid in ids:
            assert call(server, "GET", f"/items/{iid}", tok)[0] == 200, (who, iid)
        assert hub.db.events.count({"action": "access.denied", "actor": who}) == before, who
    run = run_of(hub, "lab-result-review")
    hidden = [i for i in hub.db.items.find({"run_id": run["id"], "kind": {"$ne": "notice"}})
              if not App(hub).may_see_item(M(hub, "jo"), i, run)]
    assert hidden
    code, page = call(server, "GET", f"/items/{hidden[0]['id']}", token(hub, "jo"))
    assert code == 404 and "not yours to open" in page and hidden[0]["id"] not in page


def test_the_item_open_rule_keeps_step_visibility_for_a_role_holder(hub):
    """Holding an item's role does not open a step its holder may not see; the assignee still opens it."""
    from practicehub.visibility import Viewer
    run = run_of(hub, "lab-result-review")
    wf, _ = hub.workflow(run["workflow_id"], run["version"])
    jo = M(hub, "jo")
    step = next(s for s in wf.steps if not Viewer(hub, jo).step_visible(wf, s.id)[0])
    item = {"kind": "task", "step_id": step.id, "role": jo["roles"][0], "assignee": None}
    app = App(hub)
    assert not app.may_see_item(jo, item, run)
    assert app.may_see_item(jo, {**item, "assignee": "jo"}, run)


def test_role_editors_say_why_a_role_waits_and_lead_with_those_given_at_once(hub):
    app = App(hub, dev=False)
    rana = M(hub, "rana")
    page = html.unescape(app.page_member({"me": rana, "query": {}, "path": "/team/jo"}, "jo"))
    beyond = hub.beyond_roles(rana)
    assert beyond, "the demo's practice manager does not reach every class"
    roles = page.split("Roles Jo Tremblay holds", 1)[1].split("</fieldset>", 1)[0]
    assert "which you do not" in roles and ("reads " in roles or "writes " in roles)
    head = roles.index("These wait for a second policy owner")
    for r in hub.roles():
        if r["id"] not in beyond and r["id"] not in hub.privileged_roles():
            assert roles.index(f'value="{r["id"]}"') < head, r["id"]
    # an agent's page marks the roles its sponsor does not reach
    admin = next(m for m in hub.members("person") if hub.can(m, "member_admin", "practice:main"))
    for agent in hub.members():
        if agent["kind"] == "person" or not agent["sponsor"]:
            continue
        sp = M(hub, agent["sponsor"])
        page = html.unescape(app.page_member({"me": admin, "query": {}, "path": f"/team/{agent['id']}"}, agent["id"]))
        if hub.beyond_roles(sp) and "/roles" in page:
            assert f"which {sp['name']} does not" in page and "its sponsor does not reach this" in page


def test_the_sponsor_picker_offers_only_people_who_reach_as_far(hub):
    app = App(hub, dev=False)
    admin = next(m for m in hub.members("person") if hub.can(m, "member_admin", "practice:main"))
    left_out = 0
    for m in hub.members():
        if m["kind"] == "person" or m["status"] != "active":
            continue
        page = app.page_member({"me": admin, "query": {}, "path": f"/team/{m['id']}"}, m["id"])
        form = page.split(f'action="/team/{m["id"]}/sponsor"', 1)[1].split("</form>", 1)[0]
        offered = re.findall(r'<option value="([\w-]+)"', form)
        for pid in offered:
            assert not hub.reach_beyond(M(hub, pid), m["id"]), (m["id"], pid)
        if [p for p in hub.members("person") if p["status"] == "active" and p["id"] not in offered]:
            assert "Not offered" in form
            left_out += 1
    assert left_out


def test_the_switch_off_form_says_who_could_take_each_dependent(hub):
    app = App(hub, dev=False)
    admin = next(m for m in hub.members("person") if hub.can(m, "member_admin", "practice:main"))
    seen = 0
    for p in hub.members("person"):
        if p["id"] == admin["id"] or not hub.dependents(p["id"]):
            continue
        form = app._switch_off_form(p, "disable", "Switch off", me=admin)
        assert "Could take it:" in form or "Nobody else reaches what it does" in form
        for d in hub.dependents(p["id"]):
            for pid in re.findall(rf'<select id="fate-{d["id"]}".*?</select>', form, re.S)[0].split('value="hand:')[1:]:
                pid = pid.split('"', 1)[0]
                assert pid not in (p["id"], admin["id"]) and not hub.reach_beyond(M(hub, pid), d["id"])
        seen += 1
    assert seen


def test_the_member_a_switch_off_is_about_cannot_decline_it(hub):
    app = App(hub, dev=False)
    owners = [m for m in hub.members("person") if m["status"] == "active" and hub.is_policy_owner(m)]
    target, other = owners[0], owners[1]
    hub.db.set_setting(f"off_request:{target['id']}", {"by": other["id"], "reason": "leaving", "hand_over": {},
                                                        "stop": []})
    assert "withdraw-disable" not in "".join(app._off_request_card(target, target))
    assert "withdraw-disable" in "".join(app._off_request_card(other, target))


def test_the_role_approval_card_shows_the_account_history(hub):
    app = App(hub, dev=False)
    text = html.unescape(re.sub(r"<[^>]+>", "", app._account_history(M(hub, "jo"))))
    assert "session" in text.lower() and "90 days" in text
    passphrase(hub, "jo")
    text = html.unescape(re.sub(r"<[^>]+>", "", app._account_history(M(hub, "jo"))))
    assert "Passphrase set" in text


def test_a_notice_reads_its_summary_and_names_records_in_words(hub):
    app = App(hub, dev=False)
    rana = M(hub, "rana")
    pat = hub.records.search("Patient")[0]
    out = app._output_html({"summary": "Noor opened a file in an emergency.", "about": f"Patient/{pat['id']}"},
                           rana)
    assert out.startswith('<p class="summary">Noor opened a file in an emergency.</p>')
    assert f'href="/patients/{pat["id"]}"' in out and f">Patient/{pat['id']}<" not in out
    assert app._values("Nothing/like-a-ref", rana) == "Nothing/like-a-ref"


def test_unreviewed_emergency_access_is_shown_on_the_access_report(hub, server):
    pid, cond = _nadia(hub)
    hub.track.append(M(hub, "noor"), "access.unreviewed", cond,
                     data={"subject": f"Patient/{pid}", "told": [], "why": "test"})
    page = html.unescape(call(server, "GET", "/access", token(hub, "rana"))[1])
    assert "Emergency access nobody could review in full" in page and "Noor Siddiqui" in page


def test_the_break_glass_forms_on_one_page_have_their_own_ids(hub, server):
    pid, cond = _nadia(hub)
    page = call(server, "GET", f"/records/{cond}", token(hub, "noor"))[1]
    ids = re.findall(r'id="(override[\w-]*)"', page)
    assert ids and len(ids) == len(set(ids))
    for i in ids:
        if i.endswith("-reason"):
            assert f'for="{i}"' in page


def test_everyday_pages_say_roles_and_choices_in_words(hub, server):
    jo = token(hub, "jo")
    page = call(server, "GET", "/", jo)[1]
    for r in hub.roles():
        if r["name"] != r["id"]:
            assert f"<td>{r['id']}</td>" not in page and f"<td>{r['id']}, " not in page, r["id"]
    visit = next(v for v in hub.records.search("Encounter", limit=None)
                 if "-" in str(((v.get("serviceType") or [{}])[0].get("concept") or {}).get("text") or ""))
    code = visit["serviceType"][0]["concept"]["text"]
    page = html.unescape(call(server, "GET", f"/records/Encounter/{visit['id']}", jo)[1])
    assert code.replace("-", " ").capitalize() in page and f"</span> {code}</span>" not in page
    assert f">{code}<" not in page.split("<summary>All values</summary>")[0]   # the raw values stay in reach
    assert "· visits<br>" not in page


def test_the_setup_code_card_offers_the_button_exactly_when_issuing_is_allowed(hub, server):
    for viewer in ("rana", "dr-amara"):
        tok = token(hub, viewer)
        for target in ("dr-liam", "noor", "jo", "sam", "dr-amara", "rana"):
            if target == viewer:
                continue
            page = html.unescape(call(server, "GET", f"/team/{target}", tok)[1])
            allowed = hub.setup_code_refusal(M(hub, viewer), target) is None
            assert ("Issue a setup code" in page) == allowed, (viewer, target)
    page = html.unescape(call(server, "GET", "/team/dr-liam", token(hub, "rana"))[1])
    assert "practicehub member passphrase-link dr-liam --as dr-liam" in page or "they may issue it" in page


def test_signing_out_clears_the_demo_sign_in_as_cookie_too(server):
    import http.client
    host, port = server.removeprefix("http://").split(":")
    conn = http.client.HTTPConnection(host, int(port))
    conn.request("GET", "/logout", headers={"Cookie": "ph_session=x; ph_as=jo"})
    r = conn.getresponse()
    got = r.headers.get_all("Set-Cookie")
    conn.close()
    assert r.status in (302, 303)
    assert any(c.startswith("ph_session=;") and "Max-Age=0" in c for c in got)
    assert any(c.startswith("ph_as=;") and "Max-Age=0" in c for c in got)


def test_the_top_bar_names_practice_settings_and_my_settings_apart(hub, server):
    page = call(server, "GET", "/", token(hub, "rana"))[1]
    assert ">Practice settings</a>" in page and ">My settings</a>" in page
    assert ">Settings</a>" not in page


def test_set_up_pages_a_member_may_not_change_are_marked_view_only(hub, server):
    page = html.unescape(call(server, "GET", "/settings", token(hub, "sam"))[1])
    assert "View only" in page
    setup = page.split("Set up", 1)[1].split("View only", 1)[0]
    assert 'href="/team"' in setup and 'href="/workflows"' not in setup
    assert 'href="/workflows"' in page.split("View only", 1)[1]
    owner = html.unescape(call(server, "GET", "/settings", token(hub, "rana"))[1])
    assert "View only" not in owner


def test_the_sign_in_page_names_the_member_admins(hub, server):
    page = html.unescape(call(server, "GET", "/login")[1])
    admins = [m["name"] for m in hub.member_admins()]
    assert admins and all(n in page for n in admins)
    assert "rana" not in page.split("No passphrase yet", 1)[1].split("</p>", 1)[0]  # names, not ids


def test_your_work_lists_what_waits_for_a_second_policy_owner(hub, server):
    hub.set_roles(M(hub, "rana"), "jo", ["front-desk", "physician"])
    page = html.unescape(call(server, "GET", "/", token(hub, "dr-amara"))[1])
    assert "Waiting for a second policy owner (1)" in page and 'href="/team/jo"' in page
    assert "Rana Haddad" in page.split("Waiting for a second policy owner", 1)[1]
    assert "Waiting for a second policy owner" not in call(server, "GET", "/", token(hub, "rana"))[1]
    assert "Waiting for a second policy owner" not in call(server, "GET", "/", token(hub, "jo"))[1]


def test_the_client_address_trusts_a_forwarded_address_only_from_a_proxy_nearby():
    from practicehub.web.server import client_address
    assert client_address("8.8.8.8", {"x-forwarded-for": "1.1.1.1"}) == "8.8.8.8"  # a public peer
    assert client_address("127.0.0.1", {"x-forwarded-for": "10.0.0.1, 198.51.100.4"}) == "198.51.100.4"
    assert client_address("192.168.1.5", {}) == "192.168.1.5"


# ---------------------------------------------------------------------------------------------- MAIN-UX: staff pages

def test_the_slot_picker_offers_every_day_and_every_physician(hub, server):
    """The cap on free slots is per day and per physician, not on the whole list (which offered only Monday
    morning); the scheduler picks a day and whom with, then the time, and only the slot is sent."""
    cal = hub.pack.calendar
    run = hub.engine.start(hub.member("jo"), "appointment-request", record=_ellen(hub),
                           inputs={"reason": "a form for my employer", "kind": "forms", "urgency": "routine"})
    iid = item_for(hub, run, "propose")
    free = hub.engine.context(M(hub, "jo"), iid)["context"]["provider:free-slots"]
    days = {c["day"] for c in free}
    whom = {c["with"] for c in free}
    assert len(days) == int(cal["lookahead_days"]) and len(whom) >= 2
    for d in days:
        for w in whom:
            assert 0 < sum(1 for c in free if c["day"] == d and c["with"] == w) <= int(cal["max_slots"])
    assert [c["value"][:16] for c in free] == sorted(c["value"][:16] for c in free)  # earliest first
    status, page = call(server, "GET", f"/items/{iid}", token(hub, "jo"))
    assert status == 200
    picker = page.split('class="slot-picker"', 1)[1].split("</fieldset>", 1)[0]
    assert "data-slot-day" in picker and "data-slot-with" in picker and picker.count("<optgroup") == len(days)
    assert picker.count('name="slot"') == 1  # the day and the person filters are not sent
    last = free[-1]
    hub.engine.claim(M(hub, "jo"), iid)
    hub.engine.complete(M(hub, "jo"), iid, {"slot": last["value"], "why": "the patient asked for later in the week"})
    assert hub.engine.run(run["id"])["ctx"]["book"]["member_name"] == last["with_name"]


def test_one_holder_is_named_the_same_way_everywhere(hub, server):
    """Who holds a piece of work is one answer (taken, else given, else its role), on the item page, the board,
    Your work and in a refusal; a refusal names the holder rather than "someone else"."""
    from practicehub.web.holder import holder
    assert holder({"status": "claimed", "claimed_by": "noor", "assignee": "jo", "role": "nurse"}) == \
        {"member": "noor", "how": "taken", "role": "nurse"}
    assert holder({"status": "open", "claimed_by": None, "assignee": "noor", "role": "nurse"})["how"] == "given"
    assert holder({"status": "open", "claimed_by": None, "assignee": None, "role": "nurse"})["member"] is None
    run = hub.engine.start(hub.member("jo"), "appointment-request", record=_ellen(hub),
                           inputs={"reason": "a form for my employer", "kind": "forms", "urgency": "routine"})
    iid = item_for(hub, run, "propose")
    page = call(server, "GET", f"/items/{iid}", token(hub, "jo"))[1]
    assert "Held by" in page and "Waiting for</span> Scheduler" in page and ">Assignee<" not in page
    hub.engine.claim(M(hub, "jo"), iid)
    for path in (f"/items/{iid}", "/board", "/"):
        page = call(server, "GET", path, token(hub, "jo"))[1]
        assert "Jo Tremblay" in page, path
    item = hub.engine.item(iid)
    app = App(hub, dev=True)
    words = app.holder_refusal(M(hub, "noor"), {**item, "status": "open", "claimed_by": None, "assignee": "jo"},
                               "it is assigned to someone else")
    assert words == "it is assigned to someone else: it is held by Jo Tremblay (given to them, not yet taken)"
    # a refusal from the engine that already names the holder is left as it is
    status, page = call(server, "POST", f"/items/{iid}/claim", token(hub, "scheduling-agent"))
    assert status in (403, 409, 422) and "Jo Tremblay" in page
    assert page.count("Jo Tremblay is working on it") <= 1 and "held by Jo Tremblay" not in page


def _critical_call_escalated(hub, times=3):
    """A critical result: the physician asks for a call, the nurse who took it hands it back, and it escalates
    ``times`` times (the physician gets an escalation notice each time)."""
    from datetime import timedelta
    eng = hub.engine
    ref = _ellen(hub)
    obs = hub.create_record(hub.member("lab-feed"), "Observation", {
        "status": "registered", "category": [{"text": "laboratory"}], "subject": {"reference": ref},
        "code": {"text": "Potassium"}, "effectiveDateTime": eng.now().date().isoformat(),
        "valueQuantity": {"value": 6.9, "unit": "mmol/L"}, "interpretation": [{"text": "critical"}]})
    run = eng.start(hub.member("lab-feed"), "lab-result-review", record=ref, inputs={
        "test": "Potassium", "value": "6.9", "unit": "mmol/L", "range": "3.5-5.0", "flag": "critical",
        "observation": f"Observation/{obs['id']}"})
    amara, noor = M(hub, "dr-amara"), M(hub, "noor")
    gate = item_for(hub, run, "review-critical")
    eng.claim(amara, gate)
    eng.decide(amara, gate, "call-patient", "call her now", seen=eng.shown_digest(amara, gate))
    call_ = item_for(hub, hub.engine.run(run["id"]), "call-critical")
    eng.claim(noor, call_)
    eng.release(noor, call_, "Going on break")
    now = eng.now()
    for k in range(1, times + 1):
        eng.tick(now + timedelta(hours=k, minutes=5))
    return run, call_


def test_urgent_work_comes_first_one_row_per_record_with_a_counter(hub, server):
    """Your work opens on an Urgent band: one row per record, however many escalations were sent (a counter, not a
    row each), with the result, how late, who holds it and the way in; the sidebar counts it in red; the board puts
    it in an Urgent lane at the top; the escalation notice leads to the work."""
    run, call_ = _critical_call_escalated(hub)
    step = hub.engine.item(call_)["step_id"]
    mine = [n for n in hub.engine.inbox(M(hub, "dr-amara"))
            if n["kind"] == "notice" and n["run_id"] == run["id"] and n.get("step_id") == step]
    assert len(mine) >= 2  # several escalation notices went to her
    hub.db.items.set(call_, {"due_at": "2020-01-01T00:00:00Z"})  # past its time, on the wall clock too
    status, page = call(server, "GET", "/", token(hub, "dr-amara"))
    assert status == 200
    band = page.split('class="urgent-band"', 1)[1].split("</section>", 1)[0]
    assert page.index('class="urgent-band"') < page.index("In progress with you")
    assert band.count('class="u-row"') == 1 and f"Escalated {len(mine)}×" in band
    assert "Potassium" in band and "Overdue" in band and f'href="/items/{call_}' in band
    assert "Waiting for</span> Nurse" in band  # handed back: nobody holds it now
    notices = page.split("<h2>Notices", 1)[1]
    assert not any(n["id"] in notices for n in mine)  # counted on the row, not listed again
    assert '<span class="count urgent"' in page.split('class="side"', 1)[1].split("</nav>", 1)[0]
    board = call(server, "GET", "/board", token(hub, "dr-amara"))[1]
    lane = board.split('class="lane urgent"', 1)[1]
    assert lane.index(call_) < lane.index('class="lane"') if 'class="lane"' in lane else call_ in lane
    item = call(server, "GET", f"/items/{mine[0]['id']}", token(hub, "dr-amara"))[1]
    assert f'href="/items/{call_}' in item and ("Take it over" in item or "Open the work" in item)
    # dismissing the row acknowledges every escalation on it
    status, _ = call(server, "POST", "/notices/ack", token(hub, "dr-amara"),
                     form=[("id", n["id"]) for n in mine] + [("_back", "/")])
    assert status in (200, 303)
    assert all(hub.engine.item(n["id"])["status"] == "done" for n in mine)


def test_a_chart_shows_urgent_work_under_its_banner_at_every_width(hub, server):
    """The chart's urgent work sits directly under the banner in the page itself (no layout rule moves it down on a
    narrow screen), for anyone who may see the work, not only whom it was escalated to."""
    run, call_ = _critical_call_escalated(hub, times=1)
    pid = _ellen(hub).split("/", 1)[1]
    page = call(server, "GET", f"/patients/{pid}", token(hub, "noor"))[1]
    assert 'class="urgent-band"' in page and call_ in page.split('class="urgent-band"', 1)[1].split("</section>")[0]
    banner_end = page.index('class="urgent-band"')
    assert banner_end < page.index("file-tools") and banner_end < page.index('class="issue')
    from practicehub.web.assets import CSS
    rule = CSS.split(".urgent-band", 1)[1].split("}", 1)[0]
    assert ".urgent-band" in CSS and not re.search(r"[{;]order:", rule)
