"""Rules the expert review panel of 2026-11-24 found broken. Each test names the rule it holds the hub to."""

import fnmatch
import tomllib
from pathlib import Path

import pytest

from conftest import M
from practicehub.hub import Denied, Invalid
from practicehub.web.server import App
from test_rules import _ellen_and_her_result, _lockbox

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------------------------------- an installed hub is whole
def test_every_file_the_core_reads_from_beside_its_code_is_installed_with_it():
    """The image runs the installed package, not the source: a file that is not Python is installed only when
    pyproject.toml declares it as package data. Without its base types the hub cannot be set up."""
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]
    files = [p for p in (ROOT / "practicehub").rglob("*") if p.is_file() and p.suffix not in (".py", ".pyc")]
    assert {p.name for p in files} >= {"Booking.toml", "Restriction.toml", "Subject.toml"}
    for p in files:
        package = ".".join(p.parent.relative_to(ROOT).parts)
        assert any(fnmatch.fnmatch(p.name, pat) for pat in declared.get(package, [])), \
            f"{p.relative_to(ROOT)} is not declared as package data of {package}"


# ------------------------------------------------------------------------------------------- a lockbox holds in the outbox
def _critical_potassium_for_locked_ellen(hub):
    """Ellen keeps her clinical information to Dr. Liam; a critical result then pages the physicians."""
    ref, obs = _ellen_and_her_result(hub)
    _lockbox(hub, ref, withhold_classes=["clinical"], allow_members=[{"reference": "Practitioner/dr-liam"}])
    run = hub.engine.start(M(hub, "dr-liam"), "lab-result-review", record=ref,
                           inputs={"test": "Potassium", "value": "7.2", "unit": "mmol/L", "range": "3.5-5.0",
                                   "flag": "critical", "observation": obs})
    return ref, run


def test_a_message_about_a_restricted_file_is_kept_from_those_the_directive_excludes(hub):
    ref, run = _critical_potassium_for_locked_ellen(hub)
    rows = {m["recipient"]: m for m in hub.db.outbox.find({"run_id": run["id"], "channel": "page"})}
    full, neutral = rows["member:dr-liam"], rows["member:dr-amara"]
    liam, amara, noor = M(hub, "dr-liam"), M(hub, "dr-amara"), M(hub, "noor")
    assert "7.2" in full["body"]
    assert hub.may_read_message(liam, full)
    # they read the message's data classes; the directive is what keeps it from them
    for excluded in (amara, noor):
        assert all(hub.can(excluded, "reader", f"data_class:{c}") for c in full["classes"])
        assert not hub.may_read_message(excluded, full)
        assert not hub.may_deliver_by_hand(excluded, full)
        with pytest.raises(Denied):
            hub.outbox_delivered_by_hand(excluded, full["id"], "phoned him")
    # the wording written for the excluded physician says nothing, and is hers to read
    assert hub.may_read_message(amara, neutral)


def test_the_outbox_page_shows_neither_the_words_nor_the_subject_line_of_a_kept_message(hub):
    _, run = _critical_potassium_for_locked_ellen(hub)
    app = App(hub, dev=False)
    for who in ("noor", "dr-amara"):
        page = str(app.page_outbox({"me": M(hub, who), "query": {}, "path": "/outbox"}))
        assert "7.2" not in page and "Potassium" not in page, who
        assert "withheld at the patient" in page
    page = str(app.page_outbox({"me": M(hub, "dr-liam"), "query": {}, "path": "/outbox"}))
    assert "7.2" in page


def test_the_connector_still_carries_a_kept_message_to_whom_it_is_for(hub):
    """A lockbox keeps a file from people and agents; the pager gateway delivers the page to the physician it is
    addressed to, as long as it reads the message's data classes (the existing rule)."""
    _, run = _critical_potassium_for_locked_ellen(hub)
    full = hub.db.outbox.find_one({"run_id": run["id"], "channel": "page", "recipient": "member:dr-liam"})
    rana = M(hub, "rana")
    hub.add_member(rana, {"id": "pager", "kind": "service", "name": "Pager gateway", "sponsor": "dr-amara",
                          "roles": []})
    hub.grant_channel(rana, "pager", "page")
    pager = M(hub, "pager")
    assert not hub.message_restricted(pager, full)
    assert hub.may_read_message(pager, full) == all(
        hub.can(pager, "reader", f"data_class:{c}") for c in full["classes"])


# ------------------------------------------------------------------------------------------- a file is read only by those it is for
def _a_file_on_ellens_result(hub, who="dr-liam"):
    """A physician attaches a tracing to Ellen's lab result."""
    ref, obs = _ellen_and_her_result(hub)
    att = hub.add_file(M(hub, who), "Observation", b"ECG tracing: sinus rhythm", "ecg.txt", "text/plain")
    code = hub.records.read(*obs.split("/"))["code"]
    hub.update_record(M(hub, who), obs, {"code": {**code, "blob": att["blob"]}})
    return ref, obs, att["blob"]


def _charge_naming(hub, ref, blob):
    visit = hub.records.search("Encounter", subject=ref)[0]
    return {"status": "planned", "code": {"text": "A007", "blob": blob}, "subject": {"reference": ref},
            "context": {"reference": f"Encounter/{visit['id']}"}}


def test_naming_a_files_hash_in_a_record_of_ones_own_is_not_a_way_to_read_it(hub):
    """Files are read through the records that name them, so a record names a file only for a writer who could
    already get at it. The billing clerk reads no clinical records."""
    ref, obs, blob = _a_file_on_ellens_result(hub)
    sam = M(hub, "sam")
    with pytest.raises(Denied):
        hub.read_file(sam, obs, blob)
    with pytest.raises(Denied):
        hub.create_record(sam, "ChargeItem", _charge_naming(hub, ref, blob))
    assert hub.db.events.find_one({"action": "access.denied", "actor": "sam", "subject": f"file:{blob}"})
    made = hub.create_record(sam, "ChargeItem", _charge_naming(hub, ref, None) | {"code": {"text": "A007"}})
    with pytest.raises(Denied):
        hub.update_record(sam, f"ChargeItem/{made['id']}", {"code": {"text": "A007", "blob": blob}})
    assert not any(e["actor"] == "sam" and e["action"] == "record.viewed" and (e.get("data") or {}).get("how") == "file"
                   for e in hub.db.events.find({"subject": obs}))


def test_a_writer_attaches_a_file_they_added_or_one_from_a_record_they_read(hub):
    ref, obs, blob = _a_file_on_ellens_result(hub)
    sam, amara = M(hub, "sam"), M(hub, "dr-amara")
    # their own upload, in a record of their own
    mine = hub.add_file(sam, "ChargeItem", b"remittance advice", "ra.txt", "text/plain")["blob"]
    made = hub.create_record(sam, "ChargeItem", _charge_naming(hub, ref, mine))
    assert hub.read_file(sam, f"ChargeItem/{made['id']}", mine)[0] == b"remittance advice"
    # another physician reads the result that names the tracing, so she may attach it elsewhere
    other = next(r for r in hub.records.search("Observation", subject=ref) if f"Observation/{r['id']}" != obs)
    hub.update_record(amara, f"Observation/{other['id']}", {"code": {**other["code"], "blob": blob}})
    # and a workflow's step carries files from record to record as the workflow says
    step = {"id": "workflow:test", "kind": "automation", "name": "A workflow", "status": "active"}
    hub.create_record(step, "ChargeItem", _charge_naming(hub, ref, blob))


def test_a_directive_keeps_a_restricted_files_hash_from_opening_it_elsewhere(hub):
    """Dr. Amara reads clinical records, but Ellen keeps hers to Dr. Liam: knowing the tracing's hash does not let
    Dr. Amara attach it to another patient's result and read it there."""
    ref, obs, blob = _a_file_on_ellens_result(hub)
    _lockbox(hub, ref, withhold_classes=["clinical"], allow_members=[{"reference": "Practitioner/dr-liam"}])
    amara = M(hub, "dr-amara")
    other = next(r for r in hub.records.search("Encounter")
                 if hub.subject_of(r) != ref and not hub.restrictions.of_subject(hub.subject_of(r)))
    visit = f"Encounter/{other['id']}"
    hub.update_record(amara, visit, {"class": {**other["class"]}})  # the visit is hers to write
    with pytest.raises(Denied):
        hub.update_record(amara, visit, {"class": {**other["class"], "blob": blob}})


def test_a_files_hash_is_on_the_track_only_for_those_who_read_what_it_was_added_for(hub):
    from practicehub.visibility import Viewer
    _, _, blob = _a_file_on_ellens_result(hub)
    added = [e for e in hub.db.events.find({"action": "file.added"}) if e["subject"] == f"file:{blob}"]
    assert added
    assert blob not in str(Viewer(hub, M(hub, "sam")).events(added))
    assert blob in str(Viewer(hub, M(hub, "dr-amara")).events(added))


# ------------------------------------------------------------------------------------------- a guard fails closed
GUARD = '''

def guard_income(values, ctx):
    return ["income may not be over 1000"] if float(values.get("income") or 0) > 1000 else []


GUARDS = {"return-prep": guard_income}
'''


def test_an_answer_is_not_taken_unchecked_while_the_code_that_checks_it_waits_for_review(tmp_path, monkeypatch):
    """A guard is a pack's hard rule on a step's answers, and it lives in the pack's code. Code that changed on disk
    is not run until two policy owners have reviewed and upgraded it. Meanwhile its guards fail closed, as its
    actions do: the guarded step takes no answer, and work that code never checked goes on."""
    import shutil
    from practicehub.hub import Hub
    monkeypatch.setenv("PRACTICEHUB_SECRET", "test-secret")
    copy = tmp_path / "packs" / "accounting"
    shutil.copytree(ROOT / "tests" / "packs" / "accounting", copy, ignore=shutil.ignore_patterns("__pycache__"))
    code = copy / "pack.py"
    code.write_text(code.read_text() + GUARD)
    state = tmp_path / "state"
    Hub(state, pack=str(copy)).install(str(copy))

    def a_return(hub, name):
        client = hub.create_record(M(hub, "kim"), "Client", {"name": [{"text": name}]})
        run = hub.engine.start(M(hub, "kim"), "personal-return", record=f"Client/{client['id']}", inputs={"year": 2026})
        return run, hub.db.items.find_one({"run_id": run["id"], "step_id": "prepare"})["id"]

    hub = Hub(state)
    agent = M(hub, "prep-agent")
    assert hub.pack_problem is None and "return-prep" in hub.pack.guards
    first, item = a_return(hub, "Avery")
    with pytest.raises(Invalid, match="does not pass"):
        hub.engine.complete(agent, item, {"income": 52000, "summary": "over the line"})
    hub.engine.complete(agent, item, {"income": 900, "summary": "within the line"})  # on to the partner's review
    waiting, item = a_return(hub, "Blake")

    code.write_text(code.read_text() + "\n# a newer image ships a newer pack.py\n")
    hub = Hub(state)
    agent = M(hub, "prep-agent")
    assert hub.pack_problem and hub.pack.guards == {}  # the changed code is not run
    with pytest.raises(Invalid, match="cannot be checked"):
        hub.engine.complete(agent, item, {"income": 52000, "summary": "over the line"})
    with pytest.raises(Invalid, match="cannot be checked"):
        hub.engine.complete(agent, item, {"income": 900, "summary": "nothing checks this either"})
    assert hub.engine.run(waiting["id"])["steps"]["prepare"]["state"] != "done"
    # the partner's review has no skill, so no guard: it never depended on that code
    review = hub.db.items.find_one({"run_id": first["id"], "step_id": "review", "kind": "task"})
    hub.engine.complete(M(hub, "lee"), review["id"], {"income": 900, "summary": "agreed"})
    assert hub.engine.run(first["id"])["steps"]["review"]["state"] == "done"


# ------------------------------------------------------------------------------------------- an agent writes through its work
def _sams_charge(hub):
    ref, _ = _ellen_and_her_result(hub)
    body = _charge_naming(hub, ref, None) | {"code": {"text": "A007"}}
    return body, f"ChargeItem/{hub.create_record(M(hub, 'sam'), 'ChargeItem', body)['id']}"


def test_an_agent_changes_records_only_through_the_work_it_is_given(hub):
    """An agent's skills, its highest risk, the sponsor's hold and who else must check its work are all checked on
    the work it is given. Its roles' write grants are the same as a person's in that role (the billing agent holds
    the billing role, as Sam does), so a write outside any work would have none of those limits: there is none,
    unless its approved profile allows direct writes."""
    from practicehub import mcp
    body, charge = _sams_charge(hub)
    agent = M(hub, "billing-agent")
    assert hub.can(agent, "writer", "data_class:billing") and not agent["profile"].get("direct_writes")
    assert not hub.may_write(agent, "ChargeItem")
    with pytest.raises(Denied, match="through the work it is given"):
        hub.update_record(agent, charge, {"code": {"text": "9999"}})
    with pytest.raises(Denied):
        hub.create_record(agent, "ChargeItem", body)
    with pytest.raises(Denied):
        hub.move_record(agent, charge, "entered-in-error", {})
    with pytest.raises(Denied):
        hub.add_file(agent, "ChargeItem", b"x", "x.txt", "text/plain")
    assert hub.records.read(*charge.split("/"))["code"] == {"text": "A007"}
    assert hub.db.events.find_one({"action": "access.denied", "actor": "billing-agent",
                                   "data.why": "an agent without direct writes"})
    # a tool-using harness is not offered what it may not do
    offered = {t["name"] for t in mcp.tools(hub, "practice", agent)}
    assert not {n for n in offered if n.startswith(("create_", "update_", "move_"))}, offered
    assert {"read_chargeitem", "search_chargeitem"} <= offered


def test_direct_writes_are_part_of_an_agents_profile_and_a_second_policy_owner_approves_them(hub):
    _, charge = _sams_charge(hub)
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    out = hub.propose_agent_profile(rana, "billing-agent", {"direct_writes": True}, "it reconciles remittances")
    assert out["status"] == "proposed"  # it lets the agent do more: nothing changes until it is approved
    with pytest.raises(Denied):
        hub.update_record(M(hub, "billing-agent"), charge, {"code": {"text": "A008"}})
    hub.approve_agent_profile(amara, "billing-agent", out["version"])
    agent = M(hub, "billing-agent")
    assert agent["profile"]["direct_writes"] is True and hub.may_write(agent, "ChargeItem")
    hub.update_record(agent, charge, {"code": {"text": "A008"}})
    # taking it away only lets the agent do less: in force at once
    assert hub.propose_agent_profile(rana, "billing-agent", {"direct_writes": False})["status"] == "active"
    with pytest.raises(Denied):
        hub.update_record(M(hub, "billing-agent"), charge, {"code": {"text": "A009"}})
    with pytest.raises(Invalid):
        hub.propose_agent_profile(rana, "billing-agent", {"direct_writes": "sometimes"})


# ------------------------------------------------------------------------------------------- a refused event is not dropped
def test_an_event_a_workflow_refuses_to_start_on_reaches_the_escalation_role(hub):
    """An event from outside (a lab feed) that the listening workflow's start form refuses started nothing: the
    sender gets the refusal, and so that the result is not lost with it, the escalation role is told at once. What
    was wrong is on the track for those who read what the event carries, not in the notice."""
    from practicehub.visibility import Viewer
    ref, _ = _ellen_and_her_result(hub)
    before = {i["id"] for i in hub.db.items.find({"kind": "notice"})}
    with pytest.raises(Invalid) as refusal:
        hub.engine.emit(M(hub, "lab-feed"), "lab.result", record=ref, data={"value": "9.1", "unit": "mmol/L"})
    (ev,) = [e for e in hub.track.list(limit=20) if e["action"] == "event.unhandled"]
    assert ev["data"]["type"] == "lab.result"
    assert ev["data"]["refused"][0]["workflow"] == "lab-result-review"
    assert ev["data"]["refused"][0]["problems"] == refusal.value.problems
    (notice,) = [i for i in hub.db.items.find({"kind": "notice"}) if i["id"] not in before]
    assert notice["role"] == hub.practice.get("escalation_role") and notice["priority"] == "urgent"
    assert "did not start" in notice["title"] and "9.1" not in notice["title"]
    # the billing clerk reads no clinical information: not what was wrong with a lab result either
    assert "refused" not in Viewer(hub, M(hub, "sam")).event(ev)["data"]
    assert "refused" in Viewer(hub, M(hub, "dr-amara")).event(ev)["data"]


def test_an_event_sent_again_for_work_already_open_raises_no_alarm(hub):
    from practicehub.hub import Conflict
    ref, obs = _ellen_and_her_result(hub)
    data = {"test": "Potassium", "value": "5.6", "unit": "mmol/L", "range": "3.5-5.0", "flag": "abnormal",
            "observation": obs}
    hub.engine.emit(M(hub, "lab-feed"), "lab.result", record=ref, data=data)
    before = hub.db.items.count({"kind": "notice", "priority": "urgent"})
    try:
        hub.engine.emit(M(hub, "lab-feed"), "lab.result", record=ref, data=data)
    except Conflict:
        pass
    assert not [e for e in hub.track.list(limit=50) if e["action"] == "event.unhandled"]
    assert hub.db.items.count({"kind": "notice", "priority": "urgent"}) == before
