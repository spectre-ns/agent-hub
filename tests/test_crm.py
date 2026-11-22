"""The relationships module (CRM), installed in a practice that is not a clinic: the accounting firm of
tests/packs/accounting. Organisations and contacts, their interactions, enquiries, follow-ups and consent (CASL)."""

from pathlib import Path

import pytest

from conftest import item_for, quiet, snapshot_hub
from practicehub import export, mcp, schema
from practicehub import views as viewmod
from practicehub.agents.runner import run_all_local
from practicehub.expr import shift_date
from practicehub.hub import Denied, Invalid, member_ref

ROOT = Path(__file__).parent.parent
PACK = str(Path(__file__).parent / "packs" / "accounting")
INSTALLER = {"id": "installer", "kind": "installer", "name": "Installer", "status": "active"}


@pytest.fixture
def firm(monkeypatch):
    monkeypatch.setenv("PRACTICEHUB_SECRET", "test-secret")

    def build(h):
        h.install(PACK)
        h.install_module(INSTALLER, "crm")
        h.authz.write([("role:relationship-manager", "assignee", member_ref("lee"))])
    return snapshot_hub("crm:firm", PACK, build, modules=["crm"])


def make(h, actor, rtype, values):
    """A record from its fields' names (``phone`` goes to the telecom entry for phones), as a form would."""
    rt, rec = h.record_type(rtype), {}
    for name, v in values.items():
        f = rt.field(name)
        rec = schema.set_in(rec, schema.locate(rec, f["path"], f["with"]).split("."), v, f["with"])
    r = h.create_record(actor, rtype, rec)
    return f"{rtype}/{r['id']}"


def today(h):
    return h.engine.now().date().isoformat()


def drafter(h):
    h._create_member(h.member("lee"), {"id": "rel-agent", "kind": "agent", "name": "Relationship agent",
                                       "sponsor": "lee", "roles": ["follow-up-drafter"],
                                       "profile": {"model": "standin", "max_risk": "low",
                                                   "skills": ["crm-follow-up-draft"]}})
    return h.member("rel-agent")


def test_its_types_and_which_are_fhir(firm):
    rts = firm.record_types()
    assert {"Organization", "Person", "Interaction", "Enquiry"} <= set(rts)
    assert rts["Organization"].standard == "fhir-r4" and rts["Person"].standard == "fhir-r4"
    assert rts["Interaction"].standard is None and rts["Enquiry"].standard is None  # no FHIR resource fits
    assert rts["Organization"].label == "Organisation" and rts["Person"].label == "Contact"
    lee = firm.member("lee")
    org = make(firm, lee, "Organization", {"name": "Harbour Credit Union", "kind": "partner", "crm_status": "active",
                                           "phone": "902-555-0140", "account_owner": "lee"})
    res = export.fhir_resource(firm, rts["Organization"], firm.read_record(lee, org))
    assert res["type"] == [{"text": "partner"}] and res["telecom"] == [{"value": "902-555-0140", "system": "phone"}]
    ext = {x["url"].rsplit(":", 1)[1]: x for x in res["extension"]}
    assert ext["account_owner"] == {"url": "urn:practicehub:Organization:account_owner",
                                    "valueReference": {"reference": f"{firm.member_ref_type()}/lee"}}
    assert ext["crm_status"]["valueString"] == "active"
    assert all(schema.FHIR_ELEMENT.fullmatch(k) for k in res if k not in ("resourceType", "id", "meta"))


def test_an_interaction_is_part_of_its_contact_or_organisation(firm):
    lee = firm.member("lee")
    org = make(firm, lee, "Organization", {"name": "Bluenose Supply", "kind": "supplier", "crm_status": "active"})
    ana = make(firm, lee, "Person", {"name": "Ana Roy", "works_at": org, "job_title": "Account rep"})
    assert [rt.id for rt in firm.part_types("Organization")] == ["Interaction"]
    assert [rt.id for rt in firm.part_types("Person")] == ["Interaction"]
    it = make(firm, lee, "Interaction", {"with": org, "kind": "call", "at": f"{today(firm)}T09:30:00",
                                         "contact": ana, "summary": "Delivery dates for next quarter."})
    parts, _ = firm.parts(lee, org)
    assert [f"Interaction/{r['id']}" for r in parts["Interaction"]] == [it]
    rec = firm.read_record(lee, it)
    assert rec["logged_by"] == "lee"  # its workflow notes who logged it
    assert firm.owner_of(rec) == org
    with pytest.raises(Invalid):  # an interaction stays with the one it was with
        firm.update_record(lee, it, {"with": ana})


def test_an_enquiry_moves_through_the_pipeline_and_a_lost_one_says_why(firm):
    lee, kim = firm.member("lee"), firm.member("kim")
    client = firm.create_record(kim, "Client", {"name": [{"text": "Avery Boudreau"}]})
    e = make(firm, lee, "Enquiry", {"title": "Bookkeeping for a café"})
    assert firm.read_record(lee, e)["status"] == "new"
    for st in ("contacted", "qualified"):
        firm.update_record(lee, e, {"status": st})
    with pytest.raises(Invalid):
        firm.update_record(lee, e, {"status": "new"})  # no move back
    firm.update_record(lee, e, {"status": "won", "became": f"Client/{client['id']}"})  # whatever type it became
    assert schema.ref_of(firm.read_record(lee, e)["became"]) == f"Client/{client['id']}"
    lost = make(firm, lee, "Enquiry", {"title": "Audit of a charity"})
    with pytest.raises(Invalid):
        firm.update_record(lee, lost, {"status": "lost"})
    firm.update_record(lee, lost, {"status": "lost", "lost_reason": "Needs an auditor licensed elsewhere."})
    board = next(w for w in viewmod.compute(firm, lee, firm.view("crm-pipeline")) if w["kind"] == "board")
    assert board["counts"] == {"lost": 1, "won": 1}


def test_reception_reads_the_directory_and_not_the_pipeline(firm):
    lee, kim = firm.member("lee"), firm.member("kim")
    org = make(firm, lee, "Organization", {"name": "Maritime Mutual", "kind": "insurer", "crm_status": "prospect"})
    e = make(firm, lee, "Enquiry", {"title": "Payroll", "from": org})
    assert firm.read_record(kim, org)["name"] == "Maritime Mutual"
    with pytest.raises(Denied):
        firm.read_record(kim, e)
    with pytest.raises(Denied):
        make(firm, kim, "Organization", {"name": "X", "kind": "other", "crm_status": "active"})


def _follow_up(firm, consent: dict):
    lee = firm.member("lee")
    org = make(firm, lee, "Organization", {"name": "South Shore League", "kind": "referrer", "crm_status": "active",
                                           "account_owner": "lee"})
    raj = make(firm, lee, "Person", {"name": "Raj Pillai", "works_at": org, "email": "raj@example.com", **consent})
    make(firm, lee, "Interaction", {"with": org, "kind": "meeting", "at": f"{today(firm)}T08:00:00", "contact": raj,
                                    "summary": "Three members asked for a call.", "follow_up": today(firm)})
    return raj


def test_a_due_follow_up_goes_to_the_owner_an_agent_drafts_and_a_person_sends(firm):
    agent = drafter(firm)
    lee = firm.member("lee")
    raj = _follow_up(firm, {"consent": "express", "consent_date": shift_date(today(firm), "-", 30, "d"),
                            "consent_source": "Signed form"})
    due = next(w for w in viewmod.compute(firm, lee, firm.view("crm-follow-ups")) if w["title"] == "Due now")
    assert due["total"] == 1
    firm.engine.tick(firm.engine.now())
    run = next(r for r in firm.engine.runs() if r["workflow_id"] == "crm-follow-up")
    assert run["ctx"]["who"]["owner"] == "lee" and run["ctx"]["who"]["to"] == raj
    run_all_local(firm, log=quiet)
    run = firm.engine.run(run["id"])
    assert run["steps"]["draft"]["by"] == "rel-agent" and "Three members" in run["ctx"]["draft"]["body"]
    send = item_for(firm, run, "send")
    assert firm.engine.item(send)["assignee"] == "lee"
    assert not firm.engine.eligibility(agent, firm.engine.item(send))[0]  # an agent never sends
    firm.engine.complete(lee, send, {"how": "send", "subject": "Following up", "body": "Hello Raj"})
    row = firm.db.outbox.find_one({"run_id": run["id"]})
    assert row["recipient"] == raj and row["purpose"] == "commercial" and row["status"] == "queued"
    assert firm.engine.run(run["id"])["status"] == "completed"
    firm.engine.tick(firm.engine.now())  # asked once for this date
    assert len([r for r in firm.engine.runs() if r["workflow_id"] == "crm-follow-up"]) == 1
    due = next(w for w in viewmod.compute(firm, lee, firm.view("crm-follow-ups")) if w["title"] == "Due now")
    assert due["total"] == 0


def test_a_follow_up_to_a_contact_without_consent_is_refused_and_the_person_told(firm):
    drafter(firm)
    lee = firm.member("lee")
    _follow_up(firm, {})
    firm.engine.tick(firm.engine.now())
    run_all_local(firm, log=quiet)
    run = next(r for r in firm.engine.runs() if r["workflow_id"] == "crm-follow-up")
    firm.engine.complete(lee, item_for(firm, run, "send"), {"how": "send", "subject": "Hi", "body": "Hello"})
    run = firm.engine.run(run["id"])
    assert run["steps"]["email"]["state"] == "failed" and "no consent" in run["steps"]["email"]["error"]
    assert firm.db.outbox.find_one({"run_id": run["id"]}) is None
    assert any("no consent" in i["title"] for i in firm.engine.inbox(lee) if i["kind"] == "notice")


def test_implied_consent_lapsing_within_30_days_is_flagged_for_its_owner(firm):
    lee = firm.member("lee")
    t = today(firm)
    soon = make(firm, lee, "Person", {"name": "Soon", "account_owner": "lee", "consent": "implied",
                                      "consent_date": shift_date(shift_date(t, "-", 2, "y"), "+", 20, "d"),
                                      "consent_source": "Contract"})
    later = make(firm, lee, "Person", {"name": "Later", "account_owner": "lee", "consent": "implied",
                                       "consent_date": shift_date(shift_date(t, "-", 2, "y"), "+", 40, "d"),
                                       "consent_source": "Contract"})
    make(firm, lee, "Person", {"name": "Express", "consent": "express", "consent_source": "Form",
                               "consent_date": shift_date(t, "-", 3, "y")})
    firm.engine.tick(firm.engine.now())
    runs = [r for r in firm.engine.runs() if r["workflow_id"] == "crm-consent-expiry"]
    assert [r["record"] for r in runs] == [soon]
    assert firm.read_record(lee, soon)["consent_flag"].startswith("implied consent lapses ")
    assert "consent_flag" not in firm.read_record(lee, later)
    task = item_for(firm, runs[0], "renew")
    assert firm.engine.item(task)["assignee"] == "lee" and firm.engine.item(task)["persons_only"]
    firm.engine.complete(lee, task, {"outcome": "express", "date": t, "source": "Email reply"})
    rec = firm.read_record(lee, soon)
    assert rec["consent"] == "express" and rec["consent_date"] == t and not rec.get("consent_flag")
    assert firm.consent_problem(soon, "commercial") is None


def test_its_mcp_server_offers_records_moves_and_views_never_sending(firm):
    agent = drafter(firm)
    names = {t["name"] for t in mcp.tools(firm, "crm", firm.member("lee"))}
    assert {"create_organization", "update_person", "create_interaction", "move_enquiry",
            "view_crm_pipeline", "view_crm_follow_ups"} <= names
    assert not any("send" in n or "sign" in n for n in names)
    # the drafting agent reads, and changes nothing
    assert {t["name"] for t in mcp.tools(firm, "crm", agent)} == {
        "search_organization", "read_organization", "search_person", "read_person",
        "search_interaction", "read_interaction", "search_enquiry", "read_enquiry"}


def test_it_holds_no_health_information_and_names_no_practice():
    mod = ROOT / "modules" / "crm"
    assert "NO PERSONAL HEALTH INFORMATION" in (mod / "module.toml").read_text()
    text = "\n".join(p.read_text() for p in mod.rglob("*") if p.is_file() and p.suffix in (".toml", ".py"))
    for word in ("patient", "diagnos", "physician", "Patient", "Practitioner", "Observation", "lighthouse"):
        assert word not in text, word
    # optional: no clinic installs it by default
    import tomllib
    for p in (ROOT / "packs").rglob("pack.toml"):
        assert "crm" not in tomllib.loads(p.read_text()).get("modules", []), p


def test_a_condition_moves_a_date_by_days_weeks_months_or_years():
    from practicehub import expr
    ctx = {"run": {"today": "2026-11-21"}, "record": {"given": "2024-12-10", "none": None}}
    assert expr.holds("record.given + 2y > run.today and record.given + 2y <= run.today + 30d", ctx)
    assert not expr.holds("record.given + 2y <= run.today", ctx)
    assert not expr.holds("record.none + 2y <= run.today", ctx)  # no date: no comparison holds
    assert expr.holds("run.today - 1w == '2026-11-14' and run.today + 3m == '2027-02-21'", ctx)
    assert expr.holds("-3 < 2", ctx)  # a minus sign before a number is still a number
