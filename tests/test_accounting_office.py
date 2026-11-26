"""The accounting-office pack: a T1 walked from the engagement to the notice of assessment, the agents doing what
can be automated (the organizer, reading slips, drafting, the client's letter), people reviewing and signing, the
client approving on the portal, and filing refused to an agent.

Two rules a firm relies on are stated here. Filing is a recorded fact: the hub transmits nothing and makes up no
confirmation number; a partner records the one the tax software gave, signed. And what was reviewed is what the
client authorises and what is filed: a reviewed return is locked, and changing it (a person reopening it, a slip
that comes late) means the review and the client's authorisation again."""

from contextlib import contextmanager
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

import pytest

from conftest import quiet, snapshot_hub
from practicehub.agents.runner import run_all_local
from practicehub.hub import Denied, Invalid

PACK = "accounting-office"
REVIEW = "I reviewed this return and it is ready for the client"
FILE = "I authorise filing this return with the CRA"
ACCEPT = "I accept this engagement for the firm"
IMPORTER = {"id": "import:test", "kind": "automation", "name": "Import", "status": "active"}


@contextmanager
def _refused(text):
    """Refused (Invalid), with ``text`` among the reasons given."""
    with pytest.raises(Invalid) as exc:
        yield
    assert any(text in p for p in exc.value.problems), exc.value.problems


def _install_with_rates(hub):
    """The pack installed, and the rates a firm enters (the pack's seed: 2024 and 2025, and Nova Scotia's HST)."""
    hub.install(PACK)
    hub.pack.module.seed_rates(hub, IMPORTER)


@pytest.fixture
def firm(monkeypatch):
    monkeypatch.setenv("PRACTICEHUB_SECRET", "test-secret")
    return snapshot_hub("accounting_office", PACK, _install_with_rates)


def _client(firm):
    """A client with last year's return assessed, and this year's engagement accepted and sent to them."""
    sophie = firm.member("sophie")
    c = firm.create_record(sophie, "Client", {"kind": "person", "name": "Sarah MacNeil",
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
    eng = firm.create_record(sophie, "Engagement", {"client": cref, "client_name": "Sarah MacNeil", "service": "t1",
                                                    "year": 2025, "fee": 350, "status": "acceptance"})
    _accept(firm, f"Engagement/{eng['id']}")
    return cref


def _accept(firm, eref):
    """Anne (partner) accepts the engagement for the firm, signed; the agent writes and sends the letter."""
    firm.move_record(firm.member("anne"), eref, "drafting", {"acceptance_note": "No conflicts; work we do."},
                     signature=ACCEPT)
    run_all_local(firm, log=quiet)


def _engagement(firm, cref, year=2025):
    eng = next(e for e in firm.records.search("Engagement", subject=cref, limit=None) if e["year"] == year)
    return f"Engagement/{eng['id']}"


def _sign(firm, cref, ref, answer="approve"):
    """The client answers on the portal about ``ref``, the one record of theirs they read and chose."""
    return firm.engine.start(firm.member("client-portal"), "client-sign", record=cref,
                             inputs={"about": ref, "answer": answer})


def _run_on(firm, ref, wid):
    """The latest run of ``wid`` on ``ref`` (a run has no started_at: sorting on it picked either of two)."""
    return firm.db.runs.find_one({"workflow_id": wid, "record": ref}, sort=[("created_at", -1), ("root_event", -1)])


def _by(firm, ref, wid, step):
    return (_run_on(firm, ref, wid)["steps"][step] or {}).get("by")


def test_a_t1_walks_from_organizer_to_assessed_agents_doing_the_routine_and_people_signing(firm):
    portal, rob, anne = firm.member("client-portal"), firm.member("rob"), firm.member("anne")
    cref = _client(firm)
    # the client signs the engagement on the portal: the year's return opens, and an agent sends the organizer
    _sign(firm, cref, _engagement(firm, cref))
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
    _sign(firm, cref, ref)
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
    (inv,) = firm.records.search("Invoice", subject=cref, limit=None)
    # numbered by the hub as it was sent: the first of its year's series, found by that number
    year = inv["issued_on"][:4]
    assert inv["number"] == f"TW-{year}-0001" == firm.find_key(anne, inv["number"].lower())["number"]
    # the fee as one line, and HST at the rate in force the day it was issued (Nova Scotia's 14% since April 2025)
    assert [(ln["description"], ln["amount"]) for ln in inv["lines"]] == [(inv["description"], Decimal("350"))]
    assert (inv["hst_rate"], inv["hst"], inv["total"]) == (Decimal("0.14"), Decimal("49"), Decimal("399"))
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
    cref = _signed_up(firm)
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


def _signed_up(firm):
    """The client, with this year's engagement signed on the portal: the year's return is open, gathering."""
    cref = _client(firm)
    _sign(firm, cref, _engagement(firm, cref))
    run_all_local(firm, log=quiet)
    return cref


def _in_review(firm):
    cref = _signed_up(firm)
    firm.create_record(firm.member("client-portal"), "TaxSlip", {"client": cref, "status": "received",
                                                                 "source": "portal", "text": T4})
    return cref, f"T1Return/{_draft(firm, cref)['id']}"


def _authorise(firm, cref, year=2025):
    """The client authorises their return for ``year`` (T183) on the portal."""
    ret = next(r for r in firm.records.search("T1Return", subject=cref, limit=None) if r["year"] == year)
    _sign(firm, cref, f"T1Return/{ret['id']}")
    run_all_local(firm, log=quiet)


def _review_and_authorise(firm, cref, ref):
    """Rob signs the review; the client authorises on the portal. To client-signed."""
    firm.move_record(firm.member("rob"), ref, "client-approval", {"review_notes": "Checked."}, signature=REVIEW)
    run_all_local(firm, log=quiet)
    assert _the_return(firm, cref)["status"] == "with-client"
    _authorise(firm, cref)
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

    with pytest.raises(Invalid, match="is not edited"):  # a review does not change the figures: it sends them back
        edit(rob, net_refund=9999.0)
    firm.move_record(rob, ref, "client-approval", {"review_notes": "Checked."}, signature=REVIEW)
    reviewed = firm.records.read(*ref.split("/"))
    for who in (ethan, rob, anne):
        with pytest.raises(Invalid, match="is not edited"):
            edit(who, net_refund=9999.0, total_income=1.0)
    run_all_local(firm, log=quiet)
    assert _the_return(firm, cref)["status"] == "with-client"
    with pytest.raises(Invalid, match="is not edited"):
        edit(ethan, net_refund=9999.0)
    _authorise(firm, cref)
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
    assert ret["total_income"] == signed["total_income"] + Decimal("8000")
    assert "T5" in (ret.get("flags") or "")
    with pytest.raises((Invalid, Denied)):
        firm.move_record(anne, ref, "filing", FILED, signature=FILE)
    firm.move_record(rob, ref, "client-approval", {"review_notes": "T5 added."}, signature=REVIEW)
    run_all_local(firm, log=quiet)
    assert _the_return(firm, cref)["status"] == "with-client"  # waiting for the client again
    with pytest.raises((Invalid, Denied)):
        firm.move_record(anne, ref, "filing", FILED, signature=FILE)
    _authorise(firm, cref)
    firm.move_record(anne, ref, "filing", FILED, signature=FILE)
    run_all_local(firm, log=quiet)
    ret = _the_return(firm, cref)
    assert ret["status"] == "filed" and ret["total_income"] == signed["total_income"] + Decimal("8000")
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
    assert ret["total_income"] == before["total_income"] + Decimal("8000") and not ret.get("t183_signed_on")
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


def test_a_return_that_was_assessed_is_not_started_again_and_its_page_does_not_offer_it(firm):
    """Starting a return's workflow puts it back at the organizer, whatever state it was in. For last year's return,
    filed and assessed, that would open its figures to changes and walk it to a second filing: it is refused, for
    whoever asks, and the button is not on its page. A return still being gathered keeps its button."""
    from practicehub.web.server import App
    ethan, anne = firm.member("ethan"), firm.member("anne")
    cref = _client(firm)
    last = next(r for r in firm.records.search("T1Return", subject=cref, limit=None) if r["year"] == 2024)
    ref = f"T1Return/{last['id']}"
    for who in (ethan, anne):
        with pytest.raises(Invalid, match="assessed is not started again"):
            firm.engine.start(who, "t1-return", record=ref)
    assert firm.records.read("T1Return", last["id"]) == last
    assert not firm.db.runs.find_one({"record": ref})
    app = App(firm, dev=False)
    assert "/start/t1-return" not in app.start_buttons(ethan, "T1Return", ref)
    # nor does its page say its workflow moves it anywhere: where a workflow begins is no way on from here
    page = str(app.page_record({"me": ethan, "query": {}, "path": "/records/" + ref}, *ref.split("/", 1)))
    assert "is not edited. It is kept as it is." in page and "moves it to" not in page
    assert "/start/t1-return" not in page
    # a return not locked yet (its run cancelled while the slips were coming in) can still be started
    _sign(firm, cref, _engagement(firm, cref))
    run_all_local(firm, log=quiet)
    now = f"T1Return/{_the_return(firm, cref)['id']}"
    firm.engine.cancel(anne, _run_on(firm, now, "t1-return")["id"], "Opened by mistake")
    assert _the_return(firm, cref)["status"] == "gathering"
    assert "/start/t1-return" in app.start_buttons(ethan, "T1Return", now)


def _first_year_client(firm):
    """A client with nothing on file from last year, their engagement signed: this year's return is open."""
    sophie = firm.member("sophie")
    c = firm.create_record(sophie, "Client", {"kind": "person", "name": "Daniel Boudreau",
                                              "email": "daniel@example.ca", "birth_date": "1991-06-12",
                                              "services": ["t1"], "partner_in_charge": "anne"})
    cref = f"Client/{c['id']}"
    eng = firm.create_record(sophie, "Engagement", {"client": cref, "client_name": "Daniel Boudreau", "service": "t1",
                                                    "year": 2025, "fee": 350, "status": "acceptance"})
    _accept(firm, f"Engagement/{eng['id']}")
    _sign(firm, cref, f"Engagement/{eng['id']}")
    return cref


def test_a_first_year_client_s_organizer_expects_no_slips_and_the_return_waits_for_the_first_one(firm):
    cref = _first_year_client(firm)
    run_all_local(firm, log=quiet)
    ret = _the_return(firm, cref)
    ref = f"T1Return/{ret['id']}"
    # the agent wrote the organizer though nothing came last year: it asks for everything, and expects no slip by name
    assert _by(firm, ref, "t1-return", "organizer") == "documents-agent"
    assert ret["status"] == "gathering" and not ret.get("expected_slips") and not ret.get("slips_complete")
    sent = firm.db.outbox.find_one({"run_id": _run_on(firm, ref, "t1-return")["id"]})
    assert "every slip and receipt" in sent["body"]
    # the first income slip is what it was waiting for
    firm.create_record(firm.member("client-portal"), "TaxSlip", {"client": cref, "status": "received",
                                                                 "source": "portal", "text": T4})
    run_all_local(firm, log=quiet)
    assert _the_return(firm, cref)["status"] in ("preparing", "in-review")


def test_what_the_documents_agent_hands_to_people_is_work_the_office_administrator_may_take(firm):
    """The organizer, the chasing and the T183 go to the office administrator when no agent does them. They sit on
    the return, a tax record, so the administrator reads tax records (and changes nothing on one)."""
    sophie, agent = firm.member("sophie"), firm.member("documents-agent")
    cref = _first_year_client(firm)
    ref = f"T1Return/{_the_return(firm, cref)['id']}"
    item = firm.db.items.find_one({"run_id": _run_on(firm, ref, "t1-return")["id"], "step_id": "organizer"})
    firm.engine.claim(agent, item["id"])
    firm.engine.release(agent, item["id"], "the client's file has two addresses", escalate=True)
    item = firm.db.items.get(item["id"])
    assert item["role"] == "admin" and item["persons_only"]
    assert firm.engine.eligibility(sophie, item) == (True, "")
    assert firm.may_read(sophie, "T1Return") and not firm.may_write(sophie, "T1Return")
    # and the agent's sponsor reaches what the agent does
    assert firm.can(sophie, "reader", "data_class:tax") and agent["sponsor"] == "sophie"


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


def test_a_return_is_keyed_once_and_all_its_work_and_a_reopen_show_that_key(firm):
    """A return is T1-n from the day it is made, counted among T1 returns only (an engagement is ENG-n), as a Jira
    issue is in its project. Every piece of its work shows that key, and so does the next pass when a late slip reopens
    it: staff quote one key for the return, whatever is being done to it."""
    cref, ref = _in_review(firm)
    key = firm.key_of(ref)
    returns = sorted(r["meta"]["key"] for r in firm.records.search("T1Return", limit=None))
    assert key.startswith("T1-") and returns == sorted(f"T1-{n}" for n in range(1, len(returns) + 1))
    assert all(e["meta"]["key"].startswith("ENG-") for e in firm.records.search("Engagement", limit=None))
    first = [i["_id"] for r in firm.db.runs.find({"record": ref}) for i in firm.db.items.find({"run_id": r["id"]})]
    firm.create_record(firm.member("client-portal"), "TaxSlip", {"client": cref, "status": "received",
                                                                 "source": "portal", "text": LATE_T5})
    _draft(firm, cref)  # the late slip reopens it: a new pass
    runs = list(firm.db.runs.find({"workflow_id": "t1-return", "record": ref}))
    work = [i["_id"] for r in runs for i in firm.db.items.find({"run_id": r["id"]})]
    assert len(runs) > 1 and set(work) > set(first)
    assert {firm.item_key(i) for i in work} == {key} == {firm.key_of(ref)}
    assert firm.read_record(firm.member("anne"), ref)["meta"]["key"] == key


def test_document_control_installs_beside_the_pack_and_keeps_a_client_s_documents_apart(firm):
    """A client's slips are client documents, so the document control module (whose class is the firm's controlled
    documents) installs beside the pack: everyone who works here reads the firm's procedures, and a client's slips
    stay with those who work on them."""
    anne, rob, marc = firm.member("anne"), firm.member("rob"), firm.member("marc")
    firm.approve_module_code(rob, "dms")
    firm.install_module(anne, "dms")
    assert {"client-documents", "documents"} <= {d["id"] for d in firm.pack.all_data_classes()}
    assert firm.class_of("TaxSlip") == "client-documents" and firm.class_of("Document") == "documents"
    assert firm.may_read(marc, "Document") and not firm.may_read(marc, "TaxSlip")
    assert firm.may_read(firm.member("ethan"), "TaxSlip")


def test_a_client_has_one_return_a_year_and_one_engagement_for_a_service_a_year(firm):
    """A second T1 return for a client's year, or a second engagement for the same service and year, is refused,
    however the client is written: the firm works on one, and a reopened return is the same return walked again."""
    sophie = firm.member("sophie")
    cref = _client(firm)
    with _refused("another t1 return already has"):
        firm.create_record(IMPORTER, "T1Return", {"client": {"reference": cref, "display": "S. MacNeil"},
                                                  "client_name": "Sarah MacNeil", "year": 2024, "status": "assessed",
                                                  "total_income": 62000.0, "taxable_income": 62000.0,
                                                  "net_refund": 900.0, "assessed_refund": 900.0,
                                                  "noa_date": "2025-05-20", "filed_on": "2025-04-10"})
    with _refused("another engagement already has"):
        firm.create_record(sophie, "Engagement", {"client": cref, "client_name": "Sarah MacNeil", "service": "t1",
                                                  "year": 2025, "fee": 350, "status": "acceptance"})
    firm.create_record(sophie, "Engagement", {"client": cref, "client_name": "Sarah MacNeil", "service": "bookkeeping",
                                              "year": 2025, "status": "acceptance"})
    assert len(firm.records.search("T1Return", subject=cref, limit=None)) == 1  # the refused one was not made


# ------------------------------------------------------------------ slips counted, and who did what (accounting 1.7)
def _open(firm, ref, *steps):
    """The open work of the return's current pass at one of ``steps`` (not a notice about it, nor a hold)."""
    run = _run_on(firm, ref, "t1-return")
    return firm.db.items.find_one({"run_id": run["id"], "step_id": {"$in": list(steps)},
                                   "kind": {"$nin": ["notice", "hold"]}, "status": {"$in": ["open", "claimed"]}})


def _the_slip(firm, cref):
    slip = next(s for s in firm.records.search("TaxSlip", subject=cref, limit=None) if s.get("year") == 2025)
    return f"TaxSlip/{slip['id']}"


def _partner_and_manager(firm):
    """Morgan holds both roles: one person who could review a return and file it, and must not do both."""
    firm.add_member(firm.member("anne"), {"id": "morgan", "kind": "person", "name": "Morgan Reid",
                                          "roles": ["manager", "partner"]})
    if not {"manager", "partner"} <= set(firm.member("morgan")["roles"]):  # roles that approve wait for a second owner
        firm.approve_roles(firm.member("rob"), "morgan")
    return firm.member("morgan")


def _today(firm):
    return firm.engine.now().date().isoformat()


def test_a_read_slip_is_not_edited_and_one_read_wrong_is_read_again(firm):
    """A slip that was read is what the return counts: nobody edits it. One read wrong is read again (back to
    received, read once more), and the return drafted from it is compared and drafted again, the slip its review
    point."""
    priya = firm.member("priya")
    cref, ref = _in_review(firm)
    sref = _the_slip(firm, cref)
    with pytest.raises(Invalid, match="is not edited"):
        firm.update_record(priya, sref, {**firm.records.read(*sref.split("/")), "amount": 1.0})
    firm.move_record(priya, sref, "received")
    ret = _draft(firm, cref)
    assert firm.records.read(*sref.split("/"))["status"] == "extracted"
    assert "T4 · Harbour Logistics Ltd." in ret["review_points"]
    assert ret["draft_summary"].startswith("Review points answered first")


def test_a_return_keeps_the_slips_it_counted_and_a_slip_read_again_holds_its_signatures(firm):
    """The return lists the slips it was compared and drafted from, each pinned as it was then, and a signature on it
    covers them: a counted slip read again leaves the review not current, and the partner signs no filing until the
    return has been compared again with the slip as it reads now."""
    rob, anne, priya = (firm.member(m) for m in ("rob", "anne", "priya"))
    cref, ref = _in_review(firm)
    sref = _the_slip(firm, cref)
    (pin,) = _the_return(firm, cref)["slips"]
    assert (pin["reference"], pin["version"]) == (sref, firm.record_version(sref)) and pin["sha256"]

    def review_current():
        return [s["current"] for s in firm.signatures(anne, ref) if s["meaning"] == REVIEW]

    firm.move_record(rob, ref, "client-approval", {"review_notes": "Checked."}, signature=REVIEW)
    assert review_current() == [True]
    firm.move_record(priya, sref, "received")  # read again: what the review covered is not what the return counts
    assert review_current() == [False]
    _draft(firm, cref)  # compared again: the pin is moved on to the slip as it reads now
    (pin,) = _the_return(firm, cref)["slips"]
    assert pin["version"] == firm.record_version(sref)
    assert not firm.pins_behind(firm.record_type("T1Return"), _the_return(firm, cref))
    _review_and_authorise(firm, cref, ref)
    firm.move_record(priya, sref, "received")
    with pytest.raises(Invalid, match="update the pin, then sign"):
        firm.move_record(anne, ref, "filing", FILED, signature=FILE)
    assert _the_return(firm, cref)["status"] == "client-signed"


def test_a_review_approves_the_figures_as_drafted_and_each_move_says_who_made_it(firm):
    """The reviewer approves the draft or sends it back: the approval asks for the reviewer's notes, not the figures.
    The draft, the review and the filing each stamp who made them (and the review, when), and nobody writes those by
    hand."""
    rob, anne, ethan = firm.member("rob"), firm.member("anne"), firm.member("ethan")
    cref = _signed_up(firm)
    ref = f"T1Return/{_the_return(firm, cref)['id']}"
    with pytest.raises(Invalid, match=r"Prepared by is set by the move that stamps it \(Ready for review\)"):
        firm.update_record(ethan, ref, {**firm.records.read(*ref.split("/")), "prepared_by": "ethan"})
    firm.create_record(firm.member("client-portal"), "TaxSlip", {"client": cref, "status": "received",
                                                                 "source": "portal", "text": T4})
    ret = _draft(firm, cref)
    assert ret["prepared_by"] == "tax-agent" and not ret.get("reviewed_by")
    with pytest.raises(Invalid, match="asks for Reviewer's notes, not Refund"):
        firm.move_record(rob, ref, "client-approval", {"review_notes": "Fixed it.", "net_refund": 1.0},
                         signature=REVIEW)
    firm.move_record(rob, ref, "client-approval", {"review_notes": "Checked."}, signature=REVIEW)
    ret = _the_return(firm, cref)
    assert (ret["reviewed_by"], ret["reviewed_on"]) == ("rob", _today(firm))
    run_all_local(firm, log=quiet)
    _authorise(firm, cref)
    firm.move_record(anne, ref, "filing", FILED, signature=FILE)
    run_all_local(firm, log=quiet)
    ret = _the_return(firm, cref)
    assert (ret["status"], ret["prepared_by"], ret["reviewed_by"], ret["filed_by"]) == \
        ("filed", "tax-agent", "rob", "anne")


def test_a_return_sent_back_with_review_points_is_drafted_again_answering_them_first(firm):
    """Not ready: the reviewer sends it back with review points (required), and it is compared and drafted again,
    the next draft answering them first, then comes back for review."""
    rob = firm.member("rob")
    cref, ref = _in_review(firm)
    screen = next(m for m in firm.transition_screen(rob, ref)["may"] if m["to"] == "comparing")
    assert screen["label"] == "Back with review points" and not screen.get("sign")
    with pytest.raises(Invalid, match="fill in"):
        firm.move_record(rob, ref, "comparing", {})
    firm.move_record(rob, ref, "comparing", {"review_points": "Box 22 does not agree with the pay stub."})
    ret = _draft(firm, cref)
    assert ret["review_points"] == "Box 22 does not agree with the pay stub."
    assert ret["draft_summary"].startswith("Review points answered first") and ret["prepared_by"] == "tax-agent"
    assert _open(firm, ref, "review")  # back for review, in the next pass
    assert not [s for s in firm.signatures(rob, ref) if s["meaning"] == REVIEW]


def test_whoever_reviewed_a_return_does_not_file_it(firm):
    """A member who is manager and partner may review a return or file it, not both: the partner who files did not
    draft, review or change it."""
    morgan, anne = _partner_and_manager(firm), firm.member("anne")
    cref, ref = _in_review(firm)
    firm.db.items.set(_open(firm, ref, "review")["id"], {"assignee": "morgan"})
    firm.move_record(morgan, ref, "client-approval", {"review_notes": "Checked."}, signature=REVIEW)
    run_all_local(firm, log=quiet)
    _authorise(firm, cref)
    assert _the_return(firm, cref)["status"] == "client-signed"
    iid = _open(firm, ref, "file")["id"]
    firm.db.items.set(iid, {"assignee": "morgan"})
    assert firm.engine.eligibility(morgan, firm.engine.item(iid)) == \
        (False, "whoever did 'Review the return' does not also do this")
    with pytest.raises(Denied, match="whoever did 'Review the return'"):
        firm.move_record(morgan, ref, "filing", FILED, signature=FILE)
    firm.db.items.set(iid, {"assignee": "anne"})
    firm.move_record(anne, ref, "filing", FILED, signature=FILE)
    assert _the_return(firm, cref)["filed_by"] == "anne"


def test_whoever_drafted_a_return_does_not_review_it(firm):
    """With the tax agent switched off, a person drafts the return; that person does not also review it."""
    morgan, anne = _partner_and_manager(firm), firm.member("anne")
    firm.set_status(anne, "tax-agent", "disabled", "drafting by hand this week")
    cref = _signed_up(firm)
    firm.create_record(firm.member("client-portal"), "TaxSlip", {"client": cref, "status": "received",
                                                                 "source": "portal", "text": T4})
    run_all_local(firm, log=quiet)
    ref = f"T1Return/{_the_return(firm, cref)['id']}"
    assert _the_return(firm, cref)["status"] == "preparing"
    draft = _open(firm, ref, "draft", "draft-flagged")
    firm.db.items.set(draft["id"], {"assignee": "morgan"})
    reasons = _run_on(firm, ref, "t1-return")["ctx"]["compare"]["reasons"]
    firm.engine.claim(morgan, draft["id"])
    firm.engine.complete(morgan, draft["id"], {"total_income": 64500.0, "taxable_income": 64500.0,
                                               "tax_deducted": 12400.0, "net_refund": 100.0,
                                               "flags": "; ".join(reasons) or "Nothing changed.",
                                               "draft_summary": "Drafted from the T4.", "summary": "Drafted."})
    ret = _the_return(firm, cref)
    assert (ret["status"], ret["prepared_by"]) == ("in-review", "morgan")
    iid = _open(firm, ref, "review")["id"]
    firm.db.items.set(iid, {"assignee": "morgan"})
    assert firm.engine.eligibility(morgan, firm.engine.item(iid))[1].startswith("whoever did 'Draft the return")
    with pytest.raises(Denied, match="whoever did 'Draft the return"):
        firm.move_record(morgan, ref, "client-approval", {"review_notes": "Checked."}, signature=REVIEW)


def test_an_agent_drafts_the_figures_worked_out_from_the_slips_read_and_never_counts_one_not_read(firm):
    """An agent's draft copies the comparison's estimate, from the slips read so far. A model that adds up a
    document still waiting to be read, from its text, is refused (found running qwen3 on the demo), and so is an
    agent's draft when nothing was estimated. A person drafts from the firm's tax software and may differ."""
    anne, agent, portal = firm.member("anne"), firm.member("tax-agent"), firm.member("client-portal")
    firm.set_status(anne, "tax-agent", "disabled", "held while the slips are read")
    cref = _signed_up(firm)
    firm.create_record(portal, "TaxSlip", {"client": cref, "status": "received", "source": "portal", "text": T4})
    firm.create_record(portal, "TaxSlip", {"client": cref, "status": "received", "source": "portal",
                                           "text": "Photo of a pay slip, cut off: Atlantic Ferries Ltd., "
                                                   "48,250.00 earned, 7,120.00 withheld"})
    run_all_local(firm, log=quiet)  # the T4 is read; the photo goes to a person, unread
    ref = f"T1Return/{_the_return(firm, cref)['id']}"
    assert sorted(s["status"] for s in firm.records.search("TaxSlip", subject=cref, limit=None)
                  if s.get("year") != 2024) == ["extracted", "received"]
    assert _the_return(firm, cref)["status"] == "preparing"
    firm.set_status(anne, "tax-agent", "active")
    draft = _open(firm, ref, "draft", "draft-flagged")
    cmp = _run_on(firm, ref, "t1-return")["ctx"]["compare"]
    est = {k: Decimal(str(cmp["estimate"][k])) for k in ("total_income", "taxable_income", "tax_deducted",
                                                          "net_refund")}
    assert est["total_income"] == Decimal("64500.00")  # the T4 read, and not the photo
    words = {"flags": "\n".join(cmp["reasons"]) + "\nA pay slip from Atlantic Ferries is not read yet.",
             "draft_summary": "Drafted from the T4 read.", "summary": "Drafted."}
    firm.engine.claim(agent, draft["id"])
    with _refused("total_income is $112,750.00; the slips read so far give $64,500.00 (compare.estimate)"):
        firm.engine.complete(agent, draft["id"], {**est, "total_income": est["total_income"] + Decimal("48250"),
                                                  "taxable_income": est["taxable_income"] + Decimal("48250"),
                                                  **words})
    firm.engine.complete(agent, draft["id"], {**est, **words})  # the estimate's figures, copied: taken (held)
    assert firm.engine.item(draft["id"])["status"] != "open"
    # nothing estimated (no rates for the year): an agent does not draft; a person may, with their own figures
    guard = firm.pack.guards["t1-draft"]
    view = {"context": {"compare": {"reasons": [], "estimate": None}}}
    answer = {**est, "net_refund": Decimal("100.00"), "flags": "", "draft_summary": "x", "summary": "x"}
    assert guard(answer, {**view, "by": {"id": "tax-agent", "kind": "agent"}}) == \
        ["nothing was estimated (no rates on file for the year): escalate; a person drafts it"]
    assert guard(answer, {**view, "by": {"id": "morgan", "kind": "person"}}) == []
    assert guard({**answer, "flags": "\n".join(cmp["reasons"])},
                 {"context": {"compare": cmp}, "by": {"id": "morgan", "kind": "person"}}) == []


def test_filing_waits_for_the_client_s_authorisation_and_never_writes_it(firm):
    """The client's T183 is a precondition of the filing move, not something it asks for: the filing cannot record
    one, nor overwrite the one on the return."""
    anne = firm.member("anne")
    cref, ref = _in_review(firm)
    ret = _review_and_authorise(firm, cref, ref)
    screen = next(m for m in firm.transition_screen(anne, ref)["may"] if m["to"] == "filing")
    assert "t183_signed_on" not in {a["name"] for a in screen["asks"]}
    with pytest.raises(Invalid, match="asks for"):
        firm.move_record(anne, ref, "filing", {**FILED, "t183_signed_on": "2020-01-01"}, signature=FILE)
    firm.move_record(anne, ref, "filing", FILED, signature=FILE)
    assert _the_return(firm, cref)["t183_signed_on"] == ret["t183_signed_on"]
    rt = firm.record_type("T1Return")
    (t,) = [t for t in rt.lifecycle["transition"] if t["to"] == "filing"]
    assert [c["message"] for c in t["check"]] == ["the client's authorisation (T183) is not on the return"]


# ------------------------------------------------------------------ the client signs what they read (accounting 1.7)
def test_the_client_s_authorisation_is_recorded_on_the_return_they_chose_and_no_other(firm):
    """With two years' returns waiting for the client, the portal offers each, and the one they approve is the one
    marked: the T183 binds to the return they read, never to the latest one waiting."""
    from practicehub.portal import Portal
    cref, ref = _in_review(firm)
    firm.move_record(firm.member("rob"), ref, "client-approval", {"review_notes": "Checked."}, signature=REVIEW)
    run_all_local(firm, log=quiet)
    old = firm.create_record(IMPORTER, "T1Return", {"client": cref, "client_name": "Sarah MacNeil", "year": 2023,
                                                    "status": "with-client", "total_income": 60000.0,
                                                    "net_refund": 700.0})
    oref = f"T1Return/{old['id']}"
    portal = Portal(firm)
    kind = portal.kind("approve-return")
    assert sorted(c["ref"] for c in portal.about_choices(cref, kind)) == sorted([ref, oref])
    portal.book(cref, "approve-return", None, {"answer": "approve"}, oref)
    run_all_local(firm, log=quiet)
    assert firm.records.read(*oref.split("/"))["t183_signed_on"] == _today(firm)
    ret = _the_return(firm, cref)
    assert ret["status"] == "with-client" and not ret.get("t183_signed_on")
    assert [c["ref"] for c in portal.about_choices(cref, kind)] == [ref]  # signed: no longer offered
    portal.book(cref, "approve-return", None, {"answer": "approve"}, ref)
    run_all_local(firm, log=quiet)
    assert _the_return(firm, cref)["status"] == "client-signed"


def test_an_approval_of_a_record_not_the_client_s_or_not_waiting_for_them_writes_nothing(firm):
    """The workflow takes only the client's own records, and the action that records an approval writes nothing on
    one that is not theirs, not waiting for them, or signed already: the answer goes to the office."""
    mod, sophie = firm.pack.module, firm.member("sophie")
    cref, ref = _in_review(firm)
    other = firm.create_record(sophie, "Client", {"kind": "person", "name": "Kevin Thibodeau",
                                                  "email": "kevin@example.ca", "services": ["t1"],
                                                  "partner_in_charge": "anne"})
    kref = f"Client/{other['id']}"
    with _refused("is about someone else"):
        _sign(firm, kref, ref)
    out = mod.act_client_sign(firm.engine, {"id": "r-1", "subject": kref, "record": kref}, None,
                              {"about": ref, "answer": "approve"}, IMPORTER)
    assert out["signed"] is False and "not theirs" in out["summary"]
    before = firm.records.read(*ref.split("/"))
    run = _sign(firm, cref, ref)  # in review: not waiting for the client yet
    run_all_local(firm, log=quiet)
    assert firm.records.read(*ref.split("/")) == before
    assert firm.db.items.find_one({"run_id": run["id"], "step_id": "office", "status": {"$in": ["open", "claimed"]}})
    _review_and_authorise(firm, cref, ref)
    signed = firm.records.read(*ref.split("/"))
    run = _sign(firm, cref, ref)  # signed already
    run_all_local(firm, log=quiet)
    assert firm.records.read(*ref.split("/")) == signed
    assert firm.db.items.find_one({"run_id": run["id"], "step_id": "office", "status": {"$in": ["open", "claimed"]}})


# ------------------------------------------------------------------ the firm accepts an engagement (accounting 1.7)
def _new_engagement(firm, *, status="acceptance"):
    """A new client and this year's T1 engagement for them, opened by the office; or ``planned``, as the engagement
    workflow makes next year's (no person opens one past acceptance)."""
    sophie = firm.member("sophie")
    c = firm.create_record(sophie, "Client", {"kind": "person", "name": "Noah Gallant", "email": "noah@example.ca",
                                              "services": ["t1"], "partner_in_charge": "anne"})
    cref = f"Client/{c['id']}"
    e = firm.create_record(sophie if status == "acceptance" else IMPORTER, "Engagement",
                           {"client": cref, "client_name": "Noah Gallant", "service": "t1", "year": 2025, "fee": 350,
                            "status": status})
    run_all_local(firm, log=quiet)
    return cref, f"Engagement/{e['id']}"


def test_no_engagement_letter_goes_out_before_a_partner_accepts_it_for_the_firm(firm):
    """An engagement waits for a partner to accept it (independence, conflicts, competence), signed: until then no
    letter is written or sent and the client has nothing to sign. Accepting it says who did and when."""
    from practicehub.portal import Portal
    anne = firm.member("anne")
    cref, eref = _new_engagement(firm)
    eng = firm.records.read(*eref.split("/"))
    assert eng["status"] == "acceptance" and not eng.get("letter") and not eng.get("sent_on")
    run = _run_on(firm, eref, "engagement")
    assert not firm.db.outbox.find_one({"run_id": run["id"]})
    item = firm.db.items.find_one({"run_id": run["id"], "step_id": "accept", "status": {"$in": ["open", "claimed"]}})
    assert item["role"] == "partner" and item["persons_only"]
    portal = Portal(firm)
    assert portal.about_choices(cref, portal.kind("sign-engagement")) == []
    with pytest.raises(Invalid, match="fill in"):
        firm.move_record(anne, eref, "drafting", {}, signature=ACCEPT)
    firm.move_record(anne, eref, "drafting", {"acceptance_note": "New client, identity checked, no conflicts."},
                     signature=ACCEPT)
    run_all_local(firm, log=quiet)
    eng = firm.records.read(*eref.split("/"))
    assert eng["status"] == "proposed" and eng["letter"] and eng["sent_on"]
    assert (eng["accepted_by"], eng["accepted_on"]) == ("anne", _today(firm))
    assert firm.db.outbox.find_one({"run_id": run["id"]})
    assert [c["ref"] for c in portal.about_choices(cref, portal.kind("sign-engagement"))] == [eref]
    ev = firm.db.events.find_one({"action": "record.signed", "subject": eref})
    assert ev["actor"] == "anne" and ev["data"]["signed"]["meaning"] == ACCEPT


def test_only_a_partner_accepts_an_engagement_never_a_manager_or_an_agent(firm):
    """Accepting is a partner's signature; and nobody skips it by opening an engagement already past it."""
    rob, agent, sophie = firm.member("rob"), firm.member("documents-agent"), firm.member("sophie")
    cref, eref = _new_engagement(firm)
    with pytest.raises(Invalid, match="starts as 'acceptance'"):
        firm.create_record(sophie, "Engagement", {"client": cref, "client_name": "Noah Gallant",
                                                  "service": "bookkeeping", "year": 2025, "status": "proposed"})
    with pytest.raises(Denied, match="needs the partner role"):
        firm.move_record(rob, eref, "drafting", {"acceptance_note": "Fine."}, signature=ACCEPT)
    with pytest.raises(Denied, match="only a person signs"):
        firm.move_record(agent, eref, "drafting", {"acceptance_note": "Fine."}, signature=ACCEPT)
    assert firm.records.read(*eref.split("/"))["status"] == "acceptance"


def test_an_engagement_the_firm_does_not_accept_ends_there(firm):
    """Not accepted: the partner says why, no letter goes out, and nothing more is done on it."""
    anne = firm.member("anne")
    cref, eref = _new_engagement(firm)
    run = _run_on(firm, eref, "engagement")
    with pytest.raises(Invalid, match="fill in"):
        firm.move_record(anne, eref, "not-accepted", {})
    firm.move_record(anne, eref, "not-accepted", {"acceptance_note": "A conflict: we act for the other party."})
    run_all_local(firm, log=quiet)
    eng = firm.records.read(*eref.split("/"))
    assert eng["status"] == "not-accepted" and not eng.get("letter") and not eng.get("accepted_by")
    assert firm.engine.run(run["id"])["status"] != "running"
    assert not firm.db.runs.find_one({"record": eref, "status": "running"})
    assert not firm.db.outbox.find_one({"run_id": run["id"]})
    assert not firm.records.search("T1Return", subject=cref, limit=None)
    rt = firm.record_type("Engagement")
    # no move leaves it: only its workflow starting on it again would (the entry every state has)
    assert not [t for t in rt.lifecycle["transition"] if "not-accepted" in t["from"] and not t.get("entry")]


def test_next_year_s_engagement_is_accepted_again_when_its_season_comes(firm):
    """A planned engagement (next year's, made when this year's closes) waits for its season, then comes back to a
    partner to accept, every year, before its letter goes out."""
    from datetime import timedelta
    cref, eref = _new_engagement(firm, status="planned")
    eng = firm.records.read(*eref.split("/"))
    assert eng["status"] == "planned"
    firm.engine.tick(firm.engine.now() + timedelta(days=400))
    run_all_local(firm, log=quiet)
    eng = firm.records.read(*eref.split("/"))
    assert eng["status"] == "acceptance" and not eng.get("letter")
    run = _run_on(firm, eref, "engagement")
    assert firm.db.items.find_one({"run_id": run["id"], "step_id": "accept", "status": {"$in": ["open", "claimed"]}})
    assert not firm.db.outbox.find_one({"run_id": run["id"]})


def test_a_client_is_given_the_next_client_number_and_none_is_written_by_hand(firm):
    sophie = firm.member("sophie")
    one = firm.create_record(sophie, "Client", {"kind": "person", "name": "Sarah MacNeil", "email": "s@example.ca"})
    two = firm.create_record(IMPORTER, "Client", {"kind": "person", "name": "Noah Gallant", "email": "n@example.ca"})
    assert (one["client_number"], two["client_number"]) == ("TW-0001", "TW-0002")
    assert firm.find_key(sophie, "tw-0002")["number"] == "TW-0002"
    with pytest.raises(Invalid, match="the hub gives"):
        firm.create_record(sophie, "Client", {"kind": "person", "name": "Ava Pike", "client_number": "TW-0900"})
    with pytest.raises(Invalid, match="the hub gives"):
        firm.update_record(sophie, f"Client/{one['id']}", {"client_number": "TW-0007"})
    assert firm.records.read("Client", one["id"])["client_number"] == "TW-0001"


def test_rates_are_records_partners_and_managers_enter_and_staff_read(firm):
    staff, rob = firm.member("ethan"), firm.member("rob")
    rows = firm.records.search("TaxRate", limit=None)
    assert {(r["kind"], r["jurisdiction"], r.get("year")) for r in rows} >= {
        ("income-tax", "federal", 2025), ("income-tax", "NS", 2025), ("cpp", "federal", 2025), ("ei", "federal", 2025),
        ("income-tax", "federal", 2024), ("sales-tax", "NS", None)}
    ei = {"kind": "ei", "jurisdiction": "federal", "year": 2026, "rate": "0.0163", "max_earnings": "68900"}
    with pytest.raises(Denied):
        firm.create_record(staff, "TaxRate", ei)
    made = firm.create_record(rob, "TaxRate", ei)
    assert firm.read_record(staff, f"TaxRate/{made['id']}")["rate"] == Decimal("0.0163")
    # one of a kind, place and year; one sales tax a day
    with pytest.raises(Invalid):
        firm.create_record(rob, "TaxRate", {**ei, "rate": "0.0170"})
    with pytest.raises(Invalid):
        firm.create_record(rob, "TaxRate", {"kind": "sales-tax", "jurisdiction": "NS", "effective_from": "2025-04-01",
                                            "rate": "0.13"})
    # what each kind says
    with _refused("no tax year"):
        firm.create_record(rob, "TaxRate", {"kind": "sales-tax", "jurisdiction": "NB", "year": 2026, "rate": "0.15",
                                            "effective_from": "2026-01-01"})
    with _refused("brackets, the first from 0"):
        firm.create_record(rob, "TaxRate", {"kind": "income-tax", "jurisdiction": "NB", "year": 2026, "rate": "0.094",
                                            "basic_amount": "13664", "brackets": [{"over": "51306", "rate": "0.14"}]})
    with _refused("maximum earnings"):
        firm.create_record(rob, "TaxRate", {"kind": "cpp", "jurisdiction": "federal", "year": 2026, "rate": "0.0595"})


def test_an_invoice_charges_the_hst_in_force_on_the_day_it_is_issued_and_none_on_no_rate(firm):
    mod = firm.pack.module
    assert mod.sales_tax_rate(firm, date(2025, 3, 31)) == Decimal("0.15")
    assert mod.sales_tax_rate(firm, date(2025, 4, 1)) == Decimal("0.14")
    assert mod.sales_tax_rate(firm, date(2010, 6, 30)) is None
    # a province's new rate is a record: invoices from its day on charge it
    firm.create_record(firm.member("anne"), "TaxRate", {"kind": "sales-tax", "jurisdiction": "NS", "rate": "0.13",
                                                        "effective_from": "2027-01-01"})
    assert mod.sales_tax_rate(firm, date(2026, 12, 31)) == Decimal("0.14")
    assert mod.sales_tax_rate(firm, date(2027, 1, 1)) == Decimal("0.13")
    # no rate on file for the day: nothing is invoiced, and the step fails to a person
    cref = _client(firm)
    (ret,) = firm.records.search("T1Return", subject=cref, limit=None)  # last year's, assessed
    firm.engine.now = lambda: datetime(2009, 12, 1, 9, 0)
    with pytest.raises(ValueError, match="no HST rate"):
        mod.act_invoice(firm.engine, {"id": "r-1", "record": f"T1Return/{ret['id']}"}, None, {}, IMPORTER,
                        description="2025 T1")
    assert not firm.records.search("Invoice", subject=cref, limit=None)


def _invoice(firm, cref, **data):
    return firm.create_record(IMPORTER, "Invoice", {"client": cref, "client_name": "Sarah MacNeil",
                                                    "description": "2025 T1", "status": "draft", **data})


def test_an_invoice_s_lines_add_up_to_its_fee_its_hst_is_the_fee_at_its_rate_and_its_total_both(firm):
    cref = _client(firm)
    lines = [{"description": "T1 preparation", "amount": "300.00"}, {"description": "Rental schedule", "amount": "50"}]
    inv = _invoice(firm, cref, lines=lines, amount="350", hst_rate="0.14", hst="49.00", total="399.00")
    assert inv["amount"] == Decimal("350.00")
    with _refused("the fee is the sum of the lines"):
        _invoice(firm, cref, lines=lines, amount="400", hst_rate="0.14", hst="56.00", total="456.00")
    with _refused("HST is the fee at the HST rate"):
        _invoice(firm, cref, lines=lines, amount="350", hst_rate="0.14", hst="52.50", total="402.50")
    with _refused("the total is the fee plus HST"):
        _invoice(firm, cref, lines=lines, amount="350", hst_rate="0.14", hst="49.00", total="400.00")
    with _refused("the fee is the sum of the lines"):
        firm.update_record(IMPORTER, f"Invoice/{inv['id']}", {"lines": lines[:1]})


def test_a_return_is_estimated_with_its_own_year_s_rates_and_a_year_with_none_goes_to_a_person(firm):
    mod = firm.pack.module
    slips = [{"resourceType": "TaxSlip", "year": y, "status": "extracted", "slip_type": "T4", "amount": "64500",
              "tax_deducted": "12400"} for y in (2023, 2024, 2025)]
    e24, e25 = (mod.estimate(slips[i:i + 1], y, mod.tax_rates(firm, y)) for i, y in ((1, 2024), (2, 2025)))
    assert e24["total_income"] == e25["total_income"] == Decimal("64500")
    assert e24["net_refund"] != e25["net_refund"]  # the brackets, basic amounts and EI differ by year
    # a year's estimate never borrows another's rates
    with pytest.raises(ValueError, match="no 2025 rates"):
        mod.estimate(slips[2:], 2025, mod.tax_rates(firm, 2024))
    # 2023: nothing on file. Nothing is estimated, and the comparison puts the return over the risk line
    out = mod.compare({"year": 2023}, slips, [], mod.tax_rates(firm, 2023))
    assert out["estimate"] is None and out["over_line"]
    assert any(r.startswith("No 2023 rates on file") for r in out["reasons"])
    # the draft stand-in hands it to a person
    got = mod.standin_t1_draft({"context": {"compare": out, "record:tax": [{"resourceType": "T1Return", "year": 2023}]}})
    assert got["escalate"]


def test_an_upgrade_to_1_7_enters_the_rates_the_pack_had_in_its_code_once(monkeypatch):
    monkeypatch.setenv("PRACTICEHUB_SECRET", "test-secret")
    hub = snapshot_hub("accounting_office_without_rates", PACK, lambda h: h.install(PACK))
    assert not hub.records.search("TaxRate", limit=None)  # a firm installing the pack enters its own
    hub.db.set_setting("pack_upgrades_done", [])  # as a hub on 1.6 has it
    (done,) = hub.run_pack_upgrades()
    assert done["upgrade"] == "1.7-rates-are-records"
    assert len(hub.records.search("TaxRate", limit=None)) == len(hub.pack.module.SEED_RATES)
    assert hub.run_pack_upgrades() == [] and hub.pack.module.seed_rates(hub, IMPORTER) == []


def _numbers(firm, who, vid):
    """The view's widgets as ``who`` sees them, by title."""
    from practicehub import views as viewmod
    view = firm.view(vid)
    assert firm.check_view(view) == []
    return {w["title"]: w for w in viewmod.compute(firm, firm.member(who), view)}


def _hst(amount):
    return (Decimal(amount) * Decimal("0.14")).quantize(Decimal("0.01"), ROUND_HALF_UP)


def _billed(firm, cref, name, amount, issued, due=None, status="sent"):
    from datetime import timedelta
    issued_on = date.fromisoformat(_today(firm)) + timedelta(days=issued)
    hst = _hst(amount)
    return firm.create_record(IMPORTER, "Invoice", {
        "client": cref, "client_name": name, "description": "Work", "amount": amount, "hst_rate": "0.14", "hst": hst,
        "total": Decimal(amount) + hst, "issued_on": issued_on.isoformat(), "status": status,
        "due_on": (issued_on + timedelta(days=30 if due is None else due)).isoformat()})


def test_the_firm_s_receivables_are_totalled_to_the_cent_in_their_ageing_bands(firm):
    cref = _client(firm)
    # due in: 10 days; 5 and 30 days ago; 31; 75; 120 (each issued 30 days before it was due); one paid, one void
    for amount, due_ago in (("100.10", -10), ("200.20", 5), ("0.10", 30), ("300.30", 31), ("50.05", 75),
                            ("10.00", 120)):
        _billed(firm, cref, "Sarah MacNeil", amount, -30 - due_ago)
    _billed(firm, cref, "Sarah MacNeil", "999.99", -200, status="paid")
    w = _numbers(firm, "anne", "firm-receivables")
    total = lambda *a: sum(Decimal(x) + _hst(x) for x in a)  # noqa: E731
    assert w["Owed to the firm"]["number"] == total("100.10", "200.20", "0.10", "300.30", "50.05", "10.00")
    assert w["Not yet due"]["number"] == total("100.10")
    assert w["1 to 30 days past due"]["number"] == total("200.20", "0.10")
    assert w["31 to 60 days past due"]["number"] == total("300.30")
    assert w["61 to 90 days past due"]["number"] == total("50.05")
    assert w["Over 90 days past due"]["number"] == total("10.00")
    assert [r["total"] for r in w["Past due"]["rows"]] == [total(a) for a in ("10.00", "50.05", "300.30", "0.10",
                                                                             "200.20")]
    # the tax agent reads no invoices: it is told what the numbers need, not a number
    agent = _numbers(firm, "tax-agent", "firm-receivables")["Owed to the firm"]
    assert agent["note"].startswith("needs access") and agent.get("number") is None


def test_the_firm_s_billing_is_drawn_month_by_month_for_each_partner_and_its_work_by_partner(firm):
    cref = _client(firm)
    rob_client = firm.create_record(firm.member("sophie"), "Client", {"kind": "person", "name": "Noah Gallant",
                                                                      "email": "n@example.ca", "partner_in_charge": "rob"})
    nref = f"Client/{rob_client['id']}"
    _billed(firm, cref, "Sarah MacNeil", "350.00", -40)
    _billed(firm, cref, "Sarah MacNeil", "0.10", -40)
    _billed(firm, nref, "Noah Gallant", "650.00", 0)
    _billed(firm, nref, "Noah Gallant", "800.00", -400)  # more than a year ago: not drawn
    w = _numbers(firm, "anne", "firm-billing")
    assert w["Billed in the last year"]["number"] == Decimal("1000.10")
    line = w["Billed each month, by partner"]["line"]
    by = {s["name"]: s["values"] for s in line["series"]}
    assert set(by) == {firm.member("anne")["name"], firm.member("rob")["name"]}
    assert sum(by[firm.member("anne")["name"]]) == Decimal("350.10")
    assert sum(by[firm.member("rob")["name"]]) == Decimal("650.00")
    # the work under way: the engagement accepted and sent is not yet under way; the open return is Anne's
    _sign(firm, cref, _engagement(firm, cref))
    run_all_local(firm, log=quiet)
    wip = _numbers(firm, "rob", "firm-wip")
    assert wip["Open returns by partner"]["counts"] == {firm.member("anne")["name"]: 1}
    assert wip["Fees under way by partner"]["counts"] == {firm.member("anne")["name"]: Decimal("350")}


def test_the_partners_sign_offs_are_listed_and_counted(firm):
    _client(firm)  # Anne accepted the engagement, signed
    w = _numbers(firm, "anne", "firm-sign-offs")
    assert [(r["by"], r["meaning"]) for r in w["Latest sign-offs"]["rows"]] == [("anne", ACCEPT)]
    assert w["Sign-offs by person"]["counts"] == {firm.member("anne")["name"]: 1}
    assert firm.view("firm-sign-offs").roles == ["partner"]
