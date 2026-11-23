"""The accounting-office pack: a T1 walked from the engagement to the notice of assessment, the agents doing what
can be automated (the organizer, reading slips, drafting, the client's letter), people reviewing and signing, the
client approving on the portal, and filing refused to an agent."""

import pytest

from conftest import quiet, snapshot_hub
from practicehub.agents.runner import run_all_local
from practicehub.hub import Denied, Invalid

PACK = "accounting-office"
REVIEW = "I reviewed this return and it is ready for the client"
FILE = "I authorise filing this return with the CRA"
IMPORTER = {"id": "import:test", "kind": "automation", "name": "Import", "status": "active"}


@pytest.fixture
def firm(monkeypatch):
    monkeypatch.setenv("PRACTICEHUB_SECRET", "test-secret")
    return snapshot_hub("accounting_office", PACK, lambda h: h.install(PACK))


def _client(firm):
    """A client with last year's return assessed, and this year's engagement proposed."""
    sophie = firm.member("sophie")
    c = firm.create_record(sophie, "Client", {"kind": "person", "name": "Sarah MacNeil", "client_number": "TW-0001",
                                              "email": "sarah@example.ca", "birth_date": "1980-02-03",
                                              "services": ["t1"], "partner_in_charge": "anne"})
    cref = f"Client/{c['id']}"
    firm.create_record(IMPORTER, "TaxSlip", {"client": cref, "year": 2024, "status": "extracted", "slip_type": "T4",
                                             "issuer": "Harbour Logistics Ltd.", "amount": 62000.0,
                                             "tax_deducted": 11800.0})
    firm.create_record(IMPORTER, "T1Return", {"client": cref, "client_name": "Sarah MacNeil", "year": 2024,
                                              "status": "assessed", "total_income": 62000.0, "taxable_income": 62000.0,
                                              "net_refund": 900.0, "assessed_refund": 900.0, "noa_date": "2025-05-20",
                                              "filed_on": "2025-04-10"})
    firm.create_record(sophie, "Engagement", {"client": cref, "client_name": "Sarah MacNeil", "service": "t1",
                                              "year": 2025, "fee": 350, "status": "proposed"})
    run_all_local(firm, log=quiet)
    return cref


def _run_on(firm, ref, wid):
    return firm.db.runs.find_one({"workflow_id": wid, "record": ref}, sort=[("started_at", -1)])


def _by(firm, ref, wid, step):
    return (_run_on(firm, ref, wid)["steps"][step] or {}).get("by")


def test_a_t1_walks_from_organizer_to_assessed_agents_doing_the_routine_and_people_signing(firm):
    portal, rob, anne = firm.member("client-portal"), firm.member("rob"), firm.member("anne")
    cref = _client(firm)
    # the client signs the engagement on the portal: the year's return opens, and an agent sends the organizer
    firm.engine.start(portal, "client-sign", record=cref, inputs={"what": "engagement", "answer": "approve"})
    run_all_local(firm, log=quiet)
    ret = next(r for r in firm.records.search("T1Return", subject=cref, limit=None) if r["year"] == 2025)
    ref = f"T1Return/{ret['id']}"
    assert ret["status"] == "gathering" and ret["expected_slips"] == ["T4 · Harbour Logistics Ltd."]
    assert _by(firm, ref, "t1-return", "organizer") == "documents-agent"
    # the T4 arrives on the portal: the documents agent reads it; the tax agent drafts the return
    firm.create_record(portal, "TaxSlip", {"client": cref, "status": "received", "source": "portal",
                                           "text": "T4 Statement of Remuneration Paid\nYear: 2025\n"
                                                   "Employer: Harbour Logistics Ltd.\nBox 14: 64,500.00\n"
                                                   "Box 22: 12,400.00"})
    run_all_local(firm, log=quiet)
    slip = next(s for s in firm.records.search("TaxSlip", subject=cref, limit=None) if s.get("year") == 2025)
    assert (slip["status"], slip["slip_type"], slip["amount"]) == ("extracted", "T4", 64500.0)
    assert _by(firm, f"TaxSlip/{slip['id']}", "slip-intake", "extract") == "documents-agent"
    # a refund change over the risk line: the draft is held for the agent's sponsor, who approves it
    hold = firm.db.items.find_one({"kind": "hold", "status": "open", "assignee": "rob"})
    assert hold is not None
    firm.engine.decide(rob, hold["id"], "approve", "Checked the T4 against last year.")
    run_all_local(firm, log=quiet)
    run = _run_on(firm, ref, "t1-return")
    assert run["steps"]["draft-flagged"]["by"] == "tax-agent"
    ret = firm.records.read("T1Return", ret["id"])
    assert ret["status"] == "in-review" and ret["total_income"] == 64500.0
    # only a person signs the review, and not on the item: the move on the return's page
    with pytest.raises(Denied, match="only a person signs"):
        firm.move_record(firm.member("tax-agent"), ref, "client-approval", {"review_notes": "x"}, signature=REVIEW)
    review = firm.db.items.find_one({"run_id": run["id"], "step_id": "review", "status": {"$in": ["open", "claimed"]}})
    with pytest.raises(Invalid, match="signed"):
        firm.engine.complete(rob, review["id"], {"review_notes": "ok"})
    firm.move_record(rob, ref, "client-approval", {"review_notes": "Checked the T4."}, signature=REVIEW)
    run_all_local(firm, log=quiet)
    assert _by(firm, ref, "t1-return", "client-letter") == "tax-agent"
    signed = firm.db.events.find_one({"action": "record.signed", "subject": ref}, sort=[("seq", -1)])
    assert signed["actor"] == "rob" and signed["data"]["signed"]["meaning"] == REVIEW
    # the client approves the T183 on the portal
    firm.engine.start(portal, "client-sign", record=cref, inputs={"what": "t183", "answer": "approve"})
    run_all_local(firm, log=quiet)
    ret = firm.records.read("T1Return", ret["id"])
    assert ret["status"] == "client-signed" and ret["t183_how"] == "portal"
    # an agent never files; the partner signs the filing, and the stubbed EFILE transmits it
    with pytest.raises(Denied, match="only a person signs"):
        firm.move_record(firm.member("tax-agent"), ref, "filing", {}, signature=FILE)
    with pytest.raises(Denied):
        firm.move_record(rob, ref, "filing", {}, signature=FILE)  # a manager is not a partner
    firm.move_record(anne, ref, "filing", {}, signature=FILE)
    run_all_local(firm, log=quiet)
    ret = firm.records.read("T1Return", ret["id"])
    assert ret["status"] == "filed" and ret["efile_confirmation"].startswith("EF")
    assert firm.records.search("Invoice", subject=cref, limit=None)
    # the notice of assessment agrees: assessed as filed, and the engagement closes
    firm.create_record(portal, "TaxSlip", {"client": cref, "status": "received", "source": "portal",
                                           "text": f"Notice of Assessment\nTax year: 2025\nIssued by: Canada Revenue "
                                                   f"Agency\nDate: 2026-05-20\nRefund: {ret['net_refund']:.2f}\n"
                                                   f"Box 26000: {ret['taxable_income']:.2f}"})
    run_all_local(firm, log=quiet)
    ret = firm.records.read("T1Return", ret["id"])
    assert ret["status"] == "assessed" and ret["assessment_notes"] == "Assessed as filed." and not ret.get("attention")
    eng = next(e for e in firm.records.search("Engagement", subject=cref, limit=None) if e["year"] == 2025)
    assert eng["status"] == "closed"
    # no agent signed anything
    for ev in firm.db.events.find({"action": "record.signed"}):
        assert firm.member(ev["actor"])["kind"] == "person"


def test_the_efile_provider_refuses_a_return_no_partner_signed_for_filing(firm):
    mod = firm.pack.module
    cref = _client(firm)
    firm.engine.start(firm.member("client-portal"), "client-sign", record=cref,
                      inputs={"what": "engagement", "answer": "approve"})
    run_all_local(firm, log=quiet)
    ret = next(r for r in firm.records.search("T1Return", subject=cref, limit=None) if r["year"] == 2025)
    run = _run_on(firm, f"T1Return/{ret['id']}", "t1-return")
    with pytest.raises(Exception, match="partner"):
        mod.act_efile_transmit(firm.engine, run, None, {}, firm.engine.automation_actor(run))


def test_the_demo_has_every_t1_state_and_work_in_each_person_s_inbox(monkeypatch):
    monkeypatch.setenv("PRACTICEHUB_SECRET", "test-secret")
    hub = snapshot_hub("accounting_office_demo", PACK, lambda h: h.install(PACK, demo=True))
    states = {r["status"] for r in hub.records.search("T1Return", limit=None) if r["year"] == hub.engine.now().year - 1}
    assert {"gathering", "preparing", "in-review", "client-approval", "client-signed", "filed", "assessed"} <= states
    open_items = list(hub.db.items.find({"status": {"$in": ["open", "claimed"]}}))
    for who in ("anne", "rob", "priya", "ethan", "sophie"):
        assert any(i.get("assignee") == who or i.get("role") in hub.member(who)["roles"] and not i.get("assignee")
                   for i in open_items), who
    # the agents did the routine work; none of them signed anything
    done_by = {s.get("by") for r in hub.db.runs.find({}) for s in r["steps"].values()}
    assert {"documents-agent", "tax-agent"} <= done_by
    assert not hub.db.items.find_one({"status": {"$in": ["open", "claimed"]},
                                      "assignee": {"$in": ["documents-agent", "tax-agent", "bookkeeping-agent"]}})
    for ev in hub.db.events.find({"action": "record.signed"}):
        assert hub.member(ev["actor"])["kind"] == "person"
