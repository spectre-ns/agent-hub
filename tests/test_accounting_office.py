"""The accounting-office pack: a T1 walked from the engagement to the notice of assessment, the agents doing what
can be automated (the organizer, reading slips, drafting, the client's letter), people reviewing and signing, the
client approving on the portal, and filing refused to an agent.

Two rules a firm relies on are stated here. Filing is a recorded fact: the hub transmits nothing and makes up no
confirmation number; a partner records the one the tax software gave, signed. And what was reviewed is what the
client authorises and what is filed: a reviewed return is locked, and changing it (a person reopening it, a slip
that comes late) means the review and the client's authorisation again."""

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
    # an agent never files; the partner files from the firm's tax software and records it here, signed: the hub
    # transmits nothing, so the confirmation number on the return is the one the partner recorded
    filed = {"efile_confirmation": "CRA-7K2M9Q41", "filed_on": "2026-04-21"}
    with pytest.raises(Denied, match="only a person signs"):
        firm.move_record(firm.member("tax-agent"), ref, "filing", filed, signature=FILE)
    with pytest.raises(Denied):
        firm.move_record(rob, ref, "filing", filed, signature=FILE)  # a manager is not a partner
    firm.move_record(anne, ref, "filing", filed, signature=FILE)
    run_all_local(firm, log=quiet)
    ret = firm.records.read("T1Return", ret["id"])
    assert ret["status"] == "filed"
    assert (ret["efile_confirmation"], ret["filed_on"]) == ("CRA-7K2M9Q41", "2026-04-21")
    assert _run_on(firm, ref, "t1-return")["ctx"]["filing-check"]["signed_by"] == "anne"
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


def test_the_filing_check_refuses_a_return_no_partner_signed_for_filing(firm):
    mod = firm.pack.module
    cref = _client(firm)
    firm.engine.start(firm.member("client-portal"), "client-sign", record=cref,
                      inputs={"what": "engagement", "answer": "approve"})
    run_all_local(firm, log=quiet)
    ret = next(r for r in firm.records.search("T1Return", subject=cref, limit=None) if r["year"] == 2025)
    run = _run_on(firm, f"T1Return/{ret['id']}", "t1-return")
    with pytest.raises(Exception, match="partner"):
        mod.act_t1_filing_recorded(firm.engine, run, None, {}, firm.engine.automation_actor(run))


T4 = ("T4 Statement of Remuneration Paid\nYear: 2025\nEmployer: Harbour Logistics Ltd.\nBox 14: 64,500.00\n"
      "Box 22: 12,400.00")
LATE_T5 = "T5 Statement of Investment Income\nYear: 2025\nPayer: Scotiabank\nBox 13: 8,000.00"
FILED = {"efile_confirmation": "CRA-7K2M9Q41", "filed_on": "2026-04-21"}


def _the_return(firm, cref):
    return next(r for r in firm.records.search("T1Return", subject=cref, limit=None) if r["year"] == 2025)


def _draft(firm, cref):
    """The agents draft what is on file; the sponsor lets a draft over the risk line through. To in-review."""
    run_all_local(firm, log=quiet)
    for hold in firm.db.items.find({"kind": "hold", "status": "open", "assignee": "rob"}):
        firm.engine.decide(firm.member("rob"), hold["id"], "approve", "Read the flags.")
    run_all_local(firm, log=quiet)
    ret = _the_return(firm, cref)
    assert ret["status"] == "in-review", ret["status"]
    return ret


def _in_review(firm):
    portal = firm.member("client-portal")
    cref = _client(firm)
    firm.engine.start(portal, "client-sign", record=cref, inputs={"what": "engagement", "answer": "approve"})
    run_all_local(firm, log=quiet)
    firm.create_record(portal, "TaxSlip", {"client": cref, "status": "received", "source": "portal", "text": T4})
    return cref, f"T1Return/{_draft(firm, cref)['id']}"


def _review_and_authorise(firm, cref, ref):
    """Rob signs the review; the client authorises on the portal. To client-signed."""
    firm.move_record(firm.member("rob"), ref, "client-approval", {"review_notes": "Checked."}, signature=REVIEW)
    run_all_local(firm, log=quiet)
    assert _the_return(firm, cref)["status"] == "with-client"
    firm.engine.start(firm.member("client-portal"), "client-sign", record=cref,
                      inputs={"what": "t183", "answer": "approve"})
    run_all_local(firm, log=quiet)
    ret = _the_return(firm, cref)
    assert ret["status"] == "client-signed" and ret["t183_signed_on"]
    return ret


def test_filing_is_recorded_with_the_confirmation_the_partner_gives_never_one_the_hub_makes_up(firm):
    """The hub has no EFILE connector. The signed filing move asks for the CRA's confirmation number and the date
    filed, and without them the return is not marked filed and the client is not invoiced."""
    anne = firm.member("anne")
    cref, ref = _in_review(firm)
    _review_and_authorise(firm, cref, ref)
    screen = next(m for m in firm.transition_screen(anne, ref)["may"] if m["to"] == "filing")
    asked = {a["name"]: a["required"] for a in screen["asks"]}
    assert screen["sign"] == FILE and asked["efile_confirmation"] and asked["filed_on"], screen
    for values in ({}, {"efile_confirmation": "CRA-7K2M9Q41"}, {"filed_on": "2026-04-21"},
                   {"efile_confirmation": "  ", "filed_on": "2026-04-21"}):  # a blank number is no number
        with pytest.raises(Invalid, match="fill in"):
            firm.move_record(anne, ref, "filing", values, signature=FILE)
        run_all_local(firm, log=quiet)
        ret = _the_return(firm, cref)
        assert ret["status"] == "client-signed" and not ret.get("efile_confirmation") and not ret.get("filed_on")
        assert not firm.records.search("Invoice", subject=cref, limit=None)
    with pytest.raises(Invalid, match="signed"):  # and never without the partner's signature
        firm.move_record(anne, ref, "filing", FILED)
    firm.move_record(anne, ref, "filing", FILED, signature=FILE)
    run_all_local(firm, log=quiet)
    ret = _the_return(firm, cref)
    assert (ret["status"], ret["efile_confirmation"], ret["filed_on"]) == ("filed", "CRA-7K2M9Q41", "2026-04-21")
    assert len(firm.records.search("Invoice", subject=cref, limit=None)) == 1
    # nothing in the pack transmits or invents a confirmation, and nothing tells staff that it does
    mod = firm.pack.module
    assert not hasattr(mod, "efile_stub") and "efile.transmit" not in mod.ACTIONS
    rt = firm.record_type("T1Return")
    told = " ".join(str(t.get("confirm") or "") + " " + t["label"] for t in rt.lifecycle["transition"])
    assert "sends nothing to the CRA" in told and "is transmitted to the CRA" not in told


def test_a_reviewed_return_is_locked_what_was_reviewed_is_what_is_authorised_and_filed(firm):
    """Once the review is signed nobody edits the return's figures: not before the client authorises it, not after,
    and not once it is filed."""
    ethan, rob, anne = firm.member("ethan"), firm.member("rob"), firm.member("anne")
    cref, ref = _in_review(firm)

    def edit(who, **values):
        return firm.update_record(who, ref, {**firm.records.read(*ref.split("/")), **values})

    edit(ethan, review_notes="Ethan: box 14 agrees with the pay stub.")  # in review it is still worked on
    firm.move_record(rob, ref, "client-approval", {"review_notes": "Checked."}, signature=REVIEW)
    reviewed = firm.records.read(*ref.split("/"))
    for who in (ethan, rob, anne):
        with pytest.raises(Invalid, match="is not edited"):
            edit(who, net_refund=9999.0, total_income=1.0)
    run_all_local(firm, log=quiet)
    assert _the_return(firm, cref)["status"] == "with-client"
    with pytest.raises(Invalid, match="is not edited"):
        edit(ethan, net_refund=9999.0)
    firm.engine.start(firm.member("client-portal"), "client-sign", record=cref,
                      inputs={"what": "t183", "answer": "approve"})
    run_all_local(firm, log=quiet)
    assert _the_return(firm, cref)["status"] == "client-signed"
    for who in (ethan, rob, anne):
        with pytest.raises(Invalid, match="is not edited"):
            edit(who, net_refund=9999.0, total_income=1.0)
    with pytest.raises(Invalid, match="is not edited"):  # nor is the client's authorisation written by hand
        edit(ethan, t183_signed_on="2026-01-01")
    firm.move_record(anne, ref, "filing", FILED, signature=FILE)
    run_all_local(firm, log=quiet)
    ret = _the_return(firm, cref)
    assert ret["status"] == "filed"
    for k in ("total_income", "taxable_income", "tax_deducted", "net_refund"):
        assert ret[k] == reviewed[k], k  # what was filed is what was reviewed
    with pytest.raises(Invalid, match="is not edited"):
        edit(anne, net_refund=9999.0, efile_confirmation="OTHER")


def test_a_slip_that_comes_after_the_review_reopens_the_return_it_is_never_filed_with_the_slip_unread(firm):
    """A late slip is not a flag on a return someone can still file: the return goes back to be compared and drafted
    again with the slip in it, the client's T183 is cleared, and filing waits for the review and the client again."""
    portal, rob, anne = firm.member("client-portal"), firm.member("rob"), firm.member("anne")
    cref, ref = _in_review(firm)
    signed = _review_and_authorise(firm, cref, ref)
    firm.create_record(portal, "TaxSlip", {"client": cref, "status": "received", "source": "portal", "text": LATE_T5})
    run_all_local(firm, log=quiet)
    ret = _the_return(firm, cref)
    assert ret["status"] not in ("client-signed", "filing", "filed"), ret["status"]
    assert not ret.get("t183_signed_on") and not ret.get("t183_how")
    assert "T5 · Scotiabank" in ret["note"]
    with pytest.raises((Invalid, Denied)):  # the partner cannot record a filing of the return as it was
        firm.move_record(anne, ref, "filing", FILED, signature=FILE)
    # the next pass drafts it with the slip in it; the earlier review does not stand in for this one
    ret = _draft(firm, cref)
    assert ret["total_income"] == signed["total_income"] + 8000.0
    assert "T5" in (ret.get("flags") or "")
    with pytest.raises((Invalid, Denied)):
        firm.move_record(anne, ref, "filing", FILED, signature=FILE)
    firm.move_record(rob, ref, "client-approval", {"review_notes": "T5 added."}, signature=REVIEW)
    run_all_local(firm, log=quiet)
    assert _the_return(firm, cref)["status"] == "with-client"  # waiting for the client again
    with pytest.raises((Invalid, Denied)):
        firm.move_record(anne, ref, "filing", FILED, signature=FILE)
    firm.engine.start(portal, "client-sign", record=cref, inputs={"what": "t183", "answer": "approve"})
    run_all_local(firm, log=quiet)
    firm.move_record(anne, ref, "filing", FILED, signature=FILE)
    run_all_local(firm, log=quiet)
    ret = _the_return(firm, cref)
    assert ret["status"] == "filed" and ret["total_income"] == signed["total_income"] + 8000.0
    assert len(firm.records.search("Invoice", subject=cref, limit=None)) == 1


@pytest.mark.parametrize("at", ["in-review", "with-client"])
def test_a_slip_that_comes_late_reopens_the_return_wherever_it_waits_before_filing(firm, at):
    portal, rob = firm.member("client-portal"), firm.member("rob")
    cref, ref = _in_review(firm)
    if at == "with-client":
        firm.move_record(rob, ref, "client-approval", {"review_notes": "Checked."}, signature=REVIEW)
        run_all_local(firm, log=quiet)
    before = _the_return(firm, cref)
    assert before["status"] == at
    firm.create_record(portal, "TaxSlip", {"client": cref, "status": "received", "source": "portal", "text": LATE_T5})
    ret = _draft(firm, cref)  # back through comparing and the draft, to a review that has not been signed
    assert ret["total_income"] == before["total_income"] + 8000.0 and not ret.get("t183_signed_on")
    runs = [r["id"] for r in firm.db.runs.find({"workflow_id": "t1-return", "record": ref, "status": "running"})]
    assert len(runs) == 1  # one pass walks it now: the one the slip started
    assert firm.db.items.find_one({"run_id": runs[0], "step_id": "review", "status": {"$in": ["open", "claimed"]}})


def test_a_slip_that_comes_after_filing_is_flagged_for_a_person(firm):
    cref, ref = _in_review(firm)
    _review_and_authorise(firm, cref, ref)
    firm.move_record(firm.member("anne"), ref, "filing", FILED, signature=FILE)
    run_all_local(firm, log=quiet)
    firm.create_record(firm.member("client-portal"), "TaxSlip", {"client": cref, "status": "received",
                                                                 "source": "portal", "text": LATE_T5})
    run_all_local(firm, log=quiet)
    ret = _the_return(firm, cref)
    assert ret["status"] == "filed" and "after the return was filed" in ret["attention"]
    assert ret["efile_confirmation"] == "CRA-7K2M9Q41"


@pytest.mark.parametrize("at", ["with-client", "client-signed"])
def test_reopening_a_reviewed_return_for_changes_takes_the_review_and_the_client_s_authorisation_again(firm, at):
    """The way back for a person: "Reopen for changes", by whoever holds the return's work where it waits (the
    partner once the client has authorised it). The T183 is cleared, and the return is not filed until it has been
    reviewed, and authorised by the client, again."""
    portal, rob, anne, ethan = (firm.member(m) for m in ("client-portal", "rob", "anne", "ethan"))
    cref, ref = _in_review(firm)
    if at == "client-signed":
        _review_and_authorise(firm, cref, ref)
        who = anne
        with pytest.raises(Denied):  # the partner holds the work there: it is theirs to reopen
            firm.move_record(ethan, ref, "comparing", comment="A typo.")
    else:
        firm.move_record(rob, ref, "client-approval", {"review_notes": "Checked."}, signature=REVIEW)
        run_all_local(firm, log=quiet)
        who = rob
    assert _the_return(firm, cref)["status"] == at
    screen = firm.transition_screen(who, ref)
    assert "Reopen for changes" in [m["label"] for m in screen["may"]], screen
    firm.move_record(who, ref, "comparing", comment="The client's address changed: the province of residence too.")
    ret = _draft(firm, cref)
    assert not ret.get("t183_signed_on") and not ret.get("t183_how")
    with pytest.raises((Invalid, Denied)):
        firm.move_record(anne, ref, "filing", FILED, signature=FILE)
    firm.update_record(ethan, ref, {**firm.records.read(*ref.split("/")), "review_notes": "Province checked."})
    _review_and_authorise(firm, cref, ref)
    firm.move_record(anne, ref, "filing", FILED, signature=FILE)
    run_all_local(firm, log=quiet)
    assert _the_return(firm, cref)["status"] == "filed"
    # two reviews and two authorisations are on the record: the second of each is the one the filing rests on
    signed = [e["data"]["signed"]["meaning"] for e in firm.db.events.find({"action": "record.signed", "subject": ref},
                                                                           sort=[("seq", 1)])]
    assert signed[-2:] == [REVIEW, FILE] and signed.count(REVIEW) == 2


def test_a_locked_return_still_takes_the_client_s_authorisation_recorded_from_paper(firm):
    """A locked return takes what its own moves ask for: a T183 signed on paper is recorded on the return's page
    (an unsigned move that writes the two fields it asks for), and nothing else of the return changes with it."""
    rob, ethan = firm.member("rob"), firm.member("ethan")
    cref, ref = _in_review(firm)
    firm.move_record(rob, ref, "client-approval", {"review_notes": "Checked."}, signature=REVIEW)
    run_all_local(firm, log=quiet)
    before = _the_return(firm, cref)
    assert before["status"] == "with-client"
    firm.move_record(ethan, ref, "client-signed", {"t183_signed_on": "2026-04-18", "t183_how": "paper"})
    run_all_local(firm, log=quiet)
    ret = _the_return(firm, cref)
    assert (ret["status"], ret["t183_signed_on"], ret["t183_how"]) == ("client-signed", "2026-04-18", "paper")
    assert ret["net_refund"] == before["net_refund"] and ret["total_income"] == before["total_income"]


def test_the_demo_has_every_t1_state_and_work_in_each_person_s_inbox(monkeypatch):
    monkeypatch.setenv("PRACTICEHUB_SECRET", "test-secret")
    hub = snapshot_hub("accounting_office_demo", PACK, lambda h: h.install(PACK, demo=True))
    states = {r["status"] for r in hub.records.search("T1Return", limit=None) if r["year"] == hub.engine.now().year - 1}
    # Kevin's slips are still missing after the reminder; Mei has the summary; Fatima's assessment differs
    assert {"slips-missing", "preparing", "in-review", "with-client", "client-signed", "filed",
            "assessment-differs"} <= states, states
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
    # a return the demo shows as filed carries a confirmation marked as demo data: the hub made none of its own
    filed = [r for r in hub.records.search("T1Return", limit=None) if r.get("efile_confirmation")]
    assert filed and all(r["efile_confirmation"].startswith("DEMO-") and r.get("filed_on") for r in filed)
