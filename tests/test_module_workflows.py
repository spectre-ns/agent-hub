"""Each module's records are walked by their own workflow: the record type names it (``workflow = {id, when}``),
making a record starts it, and the record's lifecycle state is the run's position. One run per pass of the
lifecycle, one item per run, reassigned at each step; a lifecycle that loops (a revision, a CAPA reopened, a change
order sent back) is walked by the next run, which knows who did what before. Workflows that are not a lifecycle (a
scheduled sweep, a reaction) start on their schedule or event and never move a state another workflow owns."""

import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import M, item_for, snapshot_onto
from practicehub.hub import Denied, Invalid
from test_web import call, server, token  # noqa: F401 - server is a fixture: the pages a signer reads

ROOT = Path(__file__).parent.parent


def install(hub, name, by="rana"):
    hub.approve_module_code(M(hub, "dr-amara" if by != "dr-amara" else "rana"), name)
    return hub.install_module(M(hub, by), name)


def demo(hub, name):
    next(m for m in hub.pack.modules if m.id == name).module.demo(hub)


def runs(hub, wid, ref, status=None):
    q = {"workflow_id": wid, "record": ref, **({"status": status} if status else {})}
    return sorted(hub.db.runs.find(q), key=lambda r: r["created_at"])


def open_run(hub, wid, ref):
    got = runs(hub, wid, ref, "running")
    assert len(got) == 1, f"{len(got)} open {wid} runs about {ref}"
    return got[0]


def step_state(hub, run, sid):
    return hub.engine.run(run["id"])["steps"][sid]["state"]


def items_of(hub, run):
    return list(hub.db.items.find({"run_id": run["id"], "kind": {"$nin": ["notice", "hold"]}}))


@pytest.fixture
def docs(hub):
    def build(h):
        install(h, "dms")
        install(h, "qms")
        for m in h.pack.modules:
            m.module.demo(h)
    snapshot_onto(hub, "modwf:docs", build, modules=["dms", "qms"])
    return hub


# ------------------------------------------------------------------------------------------- dms: Document
def test_a_document_revision_is_walked_by_its_own_workflow_from_draft_to_in_force(docs):
    hub = docs
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    d = hub.create_record(rana, "Document", {"title": "Hand hygiene", "body": "Wash.", "owner": "rana"})
    ref = f"Document/{d['id']}"
    run = open_run(hub, "dms-review", ref)  # making it started its workflow, about it
    assert step_state(hub, run, "revise") == "active"  # a task, with its owner (or a document author)
    w = hub.engine.item(item_for(hub, run, "revise"))
    assert w["role"] == "document-author" and w.get("due_at") and w["assignee"] == "rana"  # its owner, who made it
    hub.update_record(rana, ref, {"status": "in-review"})  # sent for review on its page: that does the task
    assert step_state(hub, run, "revise") == "done"
    i = item_for(hub, run, "approve")
    with pytest.raises(Denied):  # whoever wrote the revision does not approve it
        hub.engine.claim(rana, i)
    hub.engine.claim(amara, i)
    hub.engine.decide(amara, i, "approve")
    rec = hub.records.read("Document", d["id"])
    assert rec["status"] == "approved" and rec["effective"]
    assert step_state(hub, run, "in-force") == "waiting"
    assert len(items_of(hub, run)) == 1  # one item walked the whole revision
    with pytest.raises(Denied):  # a person never approves it by hand
        hub.update_record(rana, ref, {"status": "in-review"})
    # revised: this revision's run ends, numbering the new revision, and the next one walks it, knowing who did what
    before = hub.records.read("Document", d["id"]).get("revision") or 0
    hub.update_record(rana, ref, {"status": "draft"})
    assert hub.engine.run(run["id"])["status"] == "completed"
    assert hub.records.read("Document", d["id"])["revision"] == before + 1
    nxt = open_run(hub, "dms-review", ref)
    assert nxt["ctx"]["run"]["previous"] == run["id"] and step_state(hub, nxt, "revise") == "active"
    hub.update_record(amara, ref, {"body": "Wash for 20 seconds."})  # amara writes this revision
    hub.update_record(amara, ref, {"status": "in-review"})
    i = item_for(hub, nxt, "approve")
    with pytest.raises(Denied):
        hub.engine.claim(amara, i)


def test_a_returned_document_goes_back_to_its_author_and_the_next_pass_is_reviewed_again(docs):
    hub = docs
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    d = hub.create_record(rana, "Document", {"title": "Sharps", "body": "Bin."})
    ref = f"Document/{d['id']}"
    hub.update_record(rana, ref, {"status": "in-review"})
    first = open_run(hub, "dms-review", ref)
    i = item_for(hub, first, "approve")
    hub.engine.claim(amara, i)
    hub.engine.decide(amara, i, "return", "Say which bin")
    rec = hub.records.read("Document", d["id"])
    assert rec["status"] == "draft" and rec["returned_because"] == "Say which bin"  # the reason, on the document
    assert not rec.get("revision")  # returned, not revised after approval: the same revision
    assert hub.engine.run(first["id"])["status"] == "completed"
    second = open_run(hub, "dms-review", ref)
    hub.update_record(rana, ref, {"body": "The yellow bin."})
    hub.update_record(rana, ref, {"status": "in-review"})
    i = item_for(hub, second, "approve")
    hub.engine.claim(amara, i)
    hub.engine.decide(amara, i, "approve")
    assert hub.records.read("Document", d["id"])["returned_because"] == ""  # approved: no longer returned
    # the history of each pass keeps who decided, when, how, and what they signed
    hist = hub.db.items.get(items_of(hub, first)[0]["id"])["history"]
    gate = next(h for h in hist if h["step"] == "approve")
    assert gate["by"] == "dr-amara" and gate.get("outcome") == "return"


def test_only_the_document_workflow_moves_it_and_another_workflow_only_through_a_move_it_waits_on(docs):
    hub = docs
    rana = M(hub, "rana")
    d = hub.create_record(rana, "Document", {"title": "Rota"})
    ref = f"Document/{d['id']}"
    hub.update_record(rana, ref, {"status": "in-review"})
    rt = hub.record_type("Document")
    other = next(r for r in hub.db.runs.find({"status": "running"}) if r["workflow_id"] != "dms-review")
    t = rt.transition("in-review", "approved")
    assert "its own workflow" in hub.state_move_refusal(rt, ref, t, hub.records.read("Document", d["id"]),
                                                        other["id"])
    with pytest.raises(Invalid):  # one run of its own workflow at a time
        hub.engine.start(rana, "dms-review", record=ref, via_automation=True)


def test_making_a_document_obsolete_is_a_controllers_signed_decision_with_a_reason(docs):
    hub = docs
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    d = hub.create_record(rana, "Document", {"title": "Old form"})
    ref = f"Document/{d['id']}"
    run = open_run(hub, "dms-review", ref)
    with pytest.raises((Denied, Invalid)):  # nobody makes it obsolete by hand: no move from draft goes there
        hub.update_record(rana, ref, {"status": "obsolete"})
    with pytest.raises(Invalid):  # proposing it needs a reason
        hub.update_record(rana, ref, {"status": "retiring"})
    hub.update_record(rana, ref, {"status": "retiring", "obsolete_reason": "Replaced by the online form"})
    assert step_state(hub, run, "revise") == "skipped"  # the revision it was writing is withdrawn
    g = item_for(hub, run, "retire")
    assert hub.engine.item(g)["role"] == "document-controller"
    hub.engine.claim(amara, g)
    hub.engine.decide(amara, g, "obsolete")
    assert hub.records.read("Document", d["id"])["status"] == "obsolete"
    assert hub.engine.run(run["id"])["status"] == "completed"
    assert not runs(hub, "dms-review", ref, "running")
    # kept: it goes back to where it was
    d2 = hub.create_record(rana, "Document", {"title": "Still used"})
    ref2 = f"Document/{d2['id']}"
    hub.update_record(rana, ref2, {"status": "retiring", "obsolete_reason": "Maybe old"})
    g = item_for(hub, open_run(hub, "dms-review", ref2), "retire")
    hub.engine.claim(amara, g)
    hub.engine.decide(amara, g, "keep", "Still used at reception")
    assert hub.records.read("Document", d2["id"])["status"] == "draft"


def test_the_signer_reads_the_record_and_what_changed_and_each_pass_says_how_the_one_before_ended(docs, server):
    hub = docs
    i, third, ref = _three_passes(hub)
    amara = token(hub, "dr-amara")
    page = call(server, "GET", f"/items/{i}", amara)[1]
    assert "Pass 3" in page and "Say where the bins are" in page and "Previous pass" in page  # how pass 2 ended
    sign = page.split("What you sign")[1]
    assert "Use the yellow bin by the sink." in sign  # the record, open, above Decide
    assert "What changed since the version in use" in sign and "Use a bin." in sign  # against the approved one
    run_page = call(server, "GET", f"/runs/{third['id']}", amara)[1]
    assert "pass 3" in run_page and "Previous pass" in run_page
    hist = call(server, "GET", f"/records/{ref}?tab=history", token(hub, "rana"))[1]
    assert "Body: Use the yellow bin. → <b>Use the yellow bin by the sink.</b>" in hist
    assert "Say where the bins are" in hist and "Control a document, pass 2" in hist  # decisions, by pass
    assert "Changed by the Control a document workflow" in hist and ">module:dms<" not in hist
    assert "<summary>Raw</summary>" in hist


def _three_passes(hub):
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    d = hub.create_record(rana, "Document", {"title": "Sharps bins", "body": "Use a bin.", "owner": "rana"})
    ref = f"Document/{d['id']}"
    hub.update_record(rana, ref, {"status": "in-review"})
    first = open_run(hub, "dms-review", ref)
    i = item_for(hub, first, "approve")
    hub.engine.claim(amara, i)
    hub.engine.decide(amara, i, "approve")
    hub.update_record(rana, ref, {"status": "draft"})  # Revise
    hub.update_record(rana, ref, {"body": "Use the yellow bin."})
    hub.update_record(rana, ref, {"status": "in-review"})
    second = open_run(hub, "dms-review", ref)
    i = item_for(hub, second, "approve")
    hub.engine.claim(amara, i)
    hub.engine.decide(amara, i, "return", "Say where the bins are")
    third = open_run(hub, "dms-review", ref)
    hub.update_record(rana, ref, {"body": "Use the yellow bin by the sink."})
    hub.update_record(rana, ref, {"status": "in-review"})
    i = item_for(hub, third, "approve")
    return i, third, ref


def test_when_separation_of_duties_keeps_every_holder_out_a_policy_owner_is_told_who_and_why(docs):
    hub = docs
    rana = M(hub, "rana")
    holders = hub.active_holders("document-controller", persons_only=True)
    d = hub.create_record(rana, "Document", {"title": "Nobody to approve", "body": "B"})
    ref = f"Document/{d['id']}"
    for m in holders:  # every document controller writes part of it
        hub.update_record(M(hub, m["id"]), ref, {"body": f"B, {m['id']}"})
    hub.update_record(rana, ref, {"status": "in-review"})
    run = open_run(hub, "dms-review", ref)
    i = item_for(hub, run, "approve")
    blocked = hub.engine.blocked_holders(hub.engine.item(i))
    assert blocked and {m["id"] for m, _ in blocked} == {m["id"] for m in holders}
    ev = hub.db.events.find_one({"action": "item.blocked", "subject": f"item:{i}"})
    assert ev and {k["member"] for k in ev["data"]["kept_out"]} == {m["id"] for m in holders}
    told = list(hub.db.items.find({"kind": "notice", "run_id": run["id"]}))
    assert any("Nobody may take" in (n.get("note") or n.get("title") or n.get("step_title") or str(n)) for n in told)


def _v1(tmp_path, name):
    """The module as it was before its records named their workflows (git, at the commit before the port)."""
    d = tmp_path / "v1" / name
    out = subprocess.run(["git", "-C", str(ROOT), "archive", "46766b3", f"modules/{name}"], capture_output=True,
                         check=True).stdout
    (tmp_path / "v1").mkdir(exist_ok=True)
    subprocess.run(["tar", "-x", "-C", str(tmp_path / "v1"), "--strip-components=1"], input=out, check=True)
    return d


@pytest.fixture
def old_modules(tmp_path, monkeypatch):
    """A modules directory holding each module as version 1 was; ``upgrade(name)`` puts the current one there."""
    from practicehub import pack as packmod
    d = tmp_path / "modules"
    d.mkdir()
    for name in ("dms", "qms", "plm", "wiki", "inventory", "crm"):
        shutil.copytree(_v1(tmp_path, name), d / name)
    monkeypatch.setattr(packmod, "MODULES_DIR", d)

    def upgrade(hub, name):
        shutil.rmtree(d / name)
        shutil.copytree(ROOT / "modules" / name, d / name)
        hub.approve_module_code(M(hub, "dr-amara"), name)
        out = hub.upgrade_module(M(hub, "rana"), name)
        return {(p["kind"], p["id"]): p["version"] for p in out["proposed"]}
    return upgrade


def test_upgrading_dms_proposes_the_type_and_workflow_and_work_under_way_goes_on(hub, old_modules):
    install(hub, "dms")
    demo(hub, "dms")
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    d = hub.create_record(rana, "Document", {"title": "Cover rota", "body": "Name cover."})
    ref = f"Document/{d['id']}"
    assert not hub.db.runs.find_one({"record": ref})  # version 1: nothing walks a draft
    hub.update_record(rana, ref, {"status": "in-review"})
    old = open_run(hub, "dms-review", ref)  # started by its event, version 1
    proposed = old_modules(hub, "dms")
    assert ("type", "Document") in proposed and ("workflow", "dms-review") in proposed
    with pytest.raises(Invalid, match="record type first"):  # else new ones start nothing
        hub.approve_workflow(amara, "dms-review", proposed[("workflow", "dms-review")])
    hub.approve(amara, "type", "Document", proposed[("type", "Document")])
    # the type now names the workflow, whose new version is not approved yet: the old one goes on alone
    d2 = hub.create_record(rana, "Document", {"title": "Interim"})
    assert not hub.db.runs.find_one({"record": f"Document/{d2['id']}"})
    hub.approve_workflow(amara, "dms-review", proposed[("workflow", "dms-review")])
    # the run under way keeps its version, and finishes its review as it began
    i = item_for(hub, old, "approve")
    hub.engine.claim(amara, i)
    hub.engine.decide(amara, i, "approve")
    assert hub.records.read("Document", d["id"])["status"] == "approved"
    assert hub.engine.run(old["id"])["status"] == "completed"
    new = open_run(hub, "dms-review", ref)  # handed on to the new version, which walks it from in force
    assert new["version"] > old["version"] and step_state(hub, new, "in-force") == "waiting"
    # a record filed before is picked up where it is when the workflow is approved, not only at its next write
    walk = open_run(hub, "dms-review", f"Document/{d2['id']}")
    assert step_state(hub, walk, "revise") in ("active", "waiting")
    assert hub.walk_unwalked() == {}  # once


def test_a_module_upgrade_is_rolled_back_and_records_go_back_to_the_older_workflow(hub, old_modules):
    """Rolling back an upgrade withdraws what it proposed that is not approved yet and proposes again what was in
    force before what was approved; once the type in force stops naming the workflow its open runs on the type's
    records stop, and the older version starts as it did (on its events)."""
    install(hub, "dms")
    demo(hub, "dms")
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    proposed = old_modules(hub, "dms")
    hub.approve(amara, "type", "Document", proposed[("type", "Document")])
    hub.approve_workflow(amara, "dms-review", proposed[("workflow", "dms-review")])
    d = hub.create_record(rana, "Document", {"title": "Cover rota", "body": "Name cover."})
    ref = f"Document/{d['id']}"
    walk = open_run(hub, "dms-review", ref)
    with pytest.raises(Denied):
        hub.roll_back_module(hub.engine.automation_actor(walk), "dms")
    back = hub.roll_back_module(rana, "dms", "the new review is not ready")
    got = {(p["kind"], p["id"]): p["version"] for p in back["proposed"]}
    assert set(got) == {("type", "Document"), ("workflow", "dms-review")} and not back["withdrawn"]
    hub.approve(amara, "type", "Document", got[("type", "Document")])
    assert hub.engine.run(walk["id"])["status"] == "cancelled"  # its type no longer names it
    assert hub.db.events.find({"action": "run.stopped", "run_id": walk["id"]})
    hub.approve_workflow(amara, "dms-review", got[("workflow", "dms-review")])
    d2 = hub.create_record(rana, "Document", {"title": "Interim"})
    assert not hub.db.runs.find_one({"record": f"Document/{d2['id']}"})  # as version 1 was: nothing walks a draft
    hub.update_record(rana, f"Document/{d2['id']}", {"status": "in-review"})
    assert open_run(hub, "dms-review", f"Document/{d2['id']}")  # started by its event, as before


def test_rolling_back_an_upgrade_not_yet_approved_withdraws_what_it_proposed(hub, old_modules):
    install(hub, "dms")
    rana = M(hub, "rana")
    proposed = old_modules(hub, "dms")
    back = hub.roll_back_module(rana, "dms")
    assert {(w["kind"], w["id"]) for w in back["withdrawn"]} == set(proposed) and not back["proposed"]
    assert not hub.db.workflow_versions.find_one({"workflow_id": "dms-review", "status": "proposed"})


def approve_all(hub, proposed, by):
    for (kind, did), version in proposed.items():
        if kind == "workflow":
            hub.approve_workflow(by, did, version)
        else:
            hub.approve(by, kind, did, version)


# ------------------------------------------------------------------------------------------- qms: CAPA
def _approve_docs(hub, refs, by):
    for ref in refs:
        if hub.records.read(*ref.split("/"))["status"] == "draft":
            hub.update_record(M(hub, "rana"), ref, {"status": "in-review"})
        i = item_for(hub, open_run(hub, "dms-review", ref), "approve")
        hub.engine.claim(by, i)
        hub.engine.decide(by, i, "approve")


def test_a_capa_is_walked_by_its_workflow_and_a_pass_found_not_effective_is_walked_again(docs):
    from practicehub.hub import member_ref
    hub = docs
    hub.authz.write([("role:quality-manager", "assignee", member_ref("dr-liam")),
                     ("role:document-controller", "assignee", member_ref("dr-liam"))])
    rana, amara, liam = M(hub, "rana"), M(hub, "dr-amara"), M(hub, "dr-liam")
    dref = f"Document/{hub.records.search('Document')[0]['id']}"
    capa = hub.create_record(rana, "CAPA", {"title": "Missed callback", "kind": "corrective",
                                            "problem": "A critical result was not called", "documents": [dref]})
    ref = f"CAPA/{capa['id']}"
    run = open_run(hub, "qms-capa", ref)
    i = item_for(hub, run, "investigate")
    # starting the investigation on its page is a move the open step lets people make; the step stays open
    hub.update_record(rana, ref, {"status": "investigating", "owner": "rana"})
    assert step_state(hub, run, "investigate") == "active"
    hub.engine.claim(rana, i)
    hub.engine.complete(rana, i, {"root_cause": "No cover named", "plan": "Name cover in the SOP"})
    assert hub.records.read("CAPA", capa["id"])["status"] == "actions"
    # the CAPA's run moved the document through a move its own workflow waited on; that workflow walks the revision
    assert hub.records.read(*dref.split("/"))["status"] == "draft"
    assert step_state(hub, open_run(hub, "dms-review", dref), "revise") == "active"
    with pytest.raises(Denied):  # the root cause and plan are the workflow's, locked from actions on
        hub.update_record(rana, ref, {"root_cause": "Something else"})
    _approve_docs(hub, [dref], amara)
    v = item_for(hub, run, "verify")
    with pytest.raises(Denied):
        hub.engine.claim(rana, v)
    hub.engine.claim(amara, v)
    hub.engine.decide(amara, v, "not-effective", "Still missed on weekends")
    rec = hub.records.read("CAPA", capa["id"])
    assert rec["status"] == "actions" and rec["not_effective_because"] == "Still missed on weekends"
    assert hub.engine.run(run["id"])["status"] == "completed"
    again = open_run(hub, "qms-capa", ref)  # the next pass
    assert again["ctx"]["run"]["previous"] == run["id"]
    a = item_for(hub, again, "act")
    hub.engine.claim(amara, a)
    hub.engine.complete(amara, a, {"plan": "Name weekend cover too", "reopen": [dref]})
    assert hub.records.read(*dref.split("/"))["status"] == "draft"  # the one document its action names
    _approve_docs(hub, [dref], liam)
    v = item_for(hub, again, "verify")
    for who in (rana, amara):  # rana investigated (the pass before), amara took the further action
        with pytest.raises(Denied):
            hub.engine.claim(who, v)
    hub.engine.claim(liam, v)
    hub.engine.decide(liam, v, "effective")
    rec = hub.records.read("CAPA", capa["id"])
    assert rec["status"] == "closed" and rec["not_effective_because"] == ""
    assert len(rec["actions"]) == 2 and "Name cover in the SOP" in rec["actions"][0] \
        and "Name weekend cover too" in rec["actions"][1]  # every pass's action, kept
    assert not runs(hub, "qms-capa", ref, "running")


def test_a_later_capa_pass_reopens_only_the_documents_its_action_names(docs):
    hub = docs
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    approved = [f"Document/{d['id']}" for d in hub.records.search("Document", limit=None) if d["status"] == "approved"]
    if len(approved) < 2:
        pytest.skip("the demo has fewer than two approved documents")
    d1, d2 = approved[:2]
    capa = hub.create_record(rana, "CAPA", {"title": "Two docs", "kind": "corrective", "problem": "P",
                                            "documents": [d1, d2]})
    ref = f"CAPA/{capa['id']}"
    run = open_run(hub, "qms-capa", ref)
    i = item_for(hub, run, "investigate")
    hub.engine.claim(rana, i)
    hub.engine.complete(rana, i, {"root_cause": "R", "plan": "P"})
    _approve_docs(hub, [d1, d2], amara)
    v = item_for(hub, run, "verify")
    hub.engine.claim(amara, v)
    hub.engine.decide(amara, v, "not-effective", "Only the first was wrong")
    a = item_for(hub, open_run(hub, "qms-capa", ref), "act")
    hub.engine.claim(rana, a)
    hub.engine.complete(rana, a, {"plan": "Fix the first again", "reopen": [d1]})
    assert hub.records.read(*d1.split("/"))["status"] == "draft"
    assert hub.records.read(*d2.split("/"))["status"] == "approved"  # not named: left in force


def test_cancelling_a_capa_on_its_page_stops_its_work_and_actions_under_way_are_its_workflows(docs):
    hub = docs
    rana = M(hub, "rana")
    capa = hub.create_record(rana, "CAPA", {"title": "Duplicate", "kind": "preventive", "problem": "P"})
    ref = f"CAPA/{capa['id']}"
    run = open_run(hub, "qms-capa", ref)
    hub.update_record(rana, ref, {"status": "cancelled", "cancel_reason": "A duplicate"})
    assert hub.engine.run(run["id"])["status"] == "cancelled"
    assert hub.db.events.find_one({"action": "run.stopped", "run_id": run["id"]})
    assert not [i for i in items_of(hub, run) if i["status"] in ("open", "claimed", "waiting")]
    c2 = hub.create_record(rana, "CAPA", {"title": "T", "kind": "preventive", "problem": "P"})
    i = item_for(hub, open_run(hub, "qms-capa", f"CAPA/{c2['id']}"), "investigate")
    hub.engine.claim(rana, i)
    hub.engine.complete(rana, i, {"root_cause": "R", "plan": "P"})
    assert hub.records.read("CAPA", c2["id"])["status"] == "verification"  # no documents: straight on
    with pytest.raises(Denied):
        hub.update_record(rana, f"CAPA/{c2['id']}", {"status": "closed"})


def test_upgrading_qms_hands_a_capa_under_way_on_to_the_new_version(hub, old_modules):
    install(hub, "dms")
    install(hub, "qms")
    demo(hub, "dms")
    demo(hub, "qms")
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    capa = hub.create_record(rana, "CAPA", {"title": "Old", "kind": "preventive", "problem": "P"})
    ref = f"CAPA/{capa['id']}"
    old = open_run(hub, "qms-capa", ref)  # version 1, started by its event
    approve_all(hub, old_modules(hub, "dms"), amara)
    proposed = old_modules(hub, "qms")
    assert ("type", "CAPA") in proposed and ("workflow", "qms-capa") in proposed
    approve_all(hub, proposed, amara)
    i = item_for(hub, old, "investigate")
    hub.engine.claim(rana, i)
    hub.engine.complete(rana, i, {"root_cause": "R", "plan": "P"})
    assert hub.engine.run(old["id"])["version"] == old["version"]  # pinned
    assert hub.records.read("CAPA", capa["id"])["status"] == "verification"
    v = item_for(hub, old, "verify")
    hub.engine.claim(amara, v)
    hub.engine.decide(amara, v, "not-effective")
    new = open_run(hub, "qms-capa", ref)  # version 1 ended in actions with nothing to walk it; now handed on
    assert new["version"] > old["version"] and item_for(hub, new, "act")


# ------------------------------------------------------------------------------------------- plm: ChangeOrder
@pytest.fixture
def products(hub):
    def build(h):
        for name in ("dms", "qms", "plm"):
            install(h, name)
        for m in h.pack.modules:
            m.module.demo(h)
    snapshot_onto(hub, "modwf:products", build, modules=["dms", "qms", "plm"])
    return hub


def _board(hub, run, step, who, outcome, note=None):
    i = item_for(hub, run, step)
    hub.engine.claim(who, i)
    hub.engine.decide(who, i, outcome, note)


def test_a_change_order_is_walked_from_draft_and_a_rejected_or_returned_one_is_walked_again(products):
    hub = products
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    co = hub.records.search("ChangeOrder", limit=None)[0]
    cref = f"ChangeOrder/{co['id']}"
    first = open_run(hub, "plm-change", cref)  # made as a draft: its workflow holds it with its author
    assert step_state(hub, first, "submit") == "waiting"
    hub.update_record(rana, cref, {"reason": co["reason"] + " Agreed."})
    hub.update_record(rana, cref, {"status": "review"})
    with pytest.raises(Denied):  # whoever wrote it does not review it
        hub.engine.claim(rana, item_for(hub, first, "review"))
    _board(hub, first, "review", amara, "reject", "Say what is done with stock on hand")
    rec = hub.records.read("ChangeOrder", co["id"])
    assert rec["status"] == "rejected" and rec["decision"] == "Rejected: Say what is done with stock on hand"
    assert hub.engine.run(first["id"])["status"] == "completed"
    second = open_run(hub, "plm-change", cref)  # back with its author: the next pass holds it
    assert second["ctx"]["run"]["previous"] == first["id"]
    assert step_state(hub, second, "submit") == "waiting"
    hub.update_record(rana, cref, {"status": "draft"})  # Revise
    assert step_state(hub, second, "submit") == "waiting"
    hub.update_record(rana, cref, {"impact": "Use up the 10x10 stock first."})
    hub.update_record(rana, cref, {"status": "review"})
    _board(hub, second, "review", amara, "approve")
    kit = next(p for p in hub.records.search("Product", limit=None) if p["number"] == "WC-100")
    assert kit["status"] == "design" and kit["revision"] == 2
    spec = co["documents"][0]
    assert hub.records.read(*spec.split("/"))["status"] == "draft"  # opened; the document's own workflow numbers it
    hub.update_record(rana, spec, {"status": "in-review"})
    _board(hub, open_run(hub, "dms-review", spec), "approve", amara, "approve")
    _board(hub, second, "release", rana, "return", "Packing photo missing")
    rec = hub.records.read("ChangeOrder", co["id"])
    assert rec["status"] == "draft" and rec["decision"] == "Returned at release: Packing photo missing"
    third = open_run(hub, "plm-change", cref)  # returned: the next pass, which does not open the kit again
    hub.update_record(rana, cref, {"status": "review"})
    _board(hub, third, "review", amara, "approve")
    assert hub.records.read("Product", kit["id"])["revision"] == 2
    assert step_state(hub, third, "open-products") == "skipped"
    with pytest.raises(Denied):  # amara reviewed this pass: she does not release it
        hub.engine.claim(amara, item_for(hub, third, "release"))
    _board(hub, third, "release", rana, "release")
    assert hub.records.read("ChangeOrder", co["id"])["status"] == "implemented"
    assert hub.records.read("Product", kit["id"])["status"] == "released"
    assert not runs(hub, "plm-change", cref, "running")


def test_a_change_order_under_review_moves_only_by_its_workflow_and_cancelled_work_goes_back(products):
    hub = products
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    co = hub.create_record(rana, "ChangeOrder", {"title": "T", "reason": "R"})
    ref = f"ChangeOrder/{co['id']}"
    run = open_run(hub, "plm-change", ref)
    hub.update_record(rana, ref, {"status": "review"})
    with pytest.raises(Denied):
        hub.update_record(rana, ref, {"status": "approved"})
    with pytest.raises((Denied, Invalid)):  # not while the board has it
        hub.update_record(rana, ref, {"status": "cancelled", "cancel_reason": "Duplicate"})
    hub.engine.cancel(amara, run["id"], "Raised twice")
    assert hub.records.read("ChangeOrder", co["id"])["status"] == "draft"  # abandoned: back to its author
    nxt = open_run(hub, "plm-change", ref)  # and walked from there
    assert step_state(hub, nxt, "submit") == "waiting"
    hub.update_record(rana, ref, {"status": "cancelled", "cancel_reason": "Duplicate"})
    assert hub.engine.run(nxt["id"])["status"] != "running"
    assert not runs(hub, "plm-change", ref, "running")


def test_upgrading_plm_hands_a_change_under_review_on_to_the_new_version(hub, old_modules):
    for name in ("dms", "qms", "plm"):
        install(hub, name)
    for name in ("dms", "qms", "plm"):
        demo(hub, name)
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    co = hub.records.search("ChangeOrder", limit=None)[0]
    cref = f"ChangeOrder/{co['id']}"
    assert not runs(hub, "plm-change", cref)  # version 1: a draft is not walked
    hub.update_record(rana, cref, {"status": "review"})
    old = open_run(hub, "plm-change", cref)
    approve_all(hub, old_modules(hub, "dms"), amara)
    approve_all(hub, old_modules(hub, "qms"), amara)
    proposed = old_modules(hub, "plm")
    assert ("type", "ChangeOrder") in proposed and ("workflow", "plm-change") in proposed
    approve_all(hub, proposed, amara)
    _board(hub, old, "review", amara, "reject")
    assert hub.engine.run(old["id"])["status"] == "completed"
    new = open_run(hub, "plm-change", cref)  # version 1 left it rejected; version 2 holds it with its author
    assert new["version"] > old["version"] and step_state(hub, new, "submit") == "waiting"


# ------------------------------------------------------------------------------------------- wiki: WikiPage, Procedure
@pytest.fixture
def wiki(hub):
    def build(h):
        for name in ("dms", "wiki"):
            install(h, name)
        for m in h.pack.modules:
            m.module.demo(h)
    snapshot_onto(hub, "modwf:wiki", build, modules=["dms", "wiki"])
    return hub


def _named(hub, rtype, title):
    return next(p for p in hub.records.search(rtype, limit=None) if p["title"] == title)


def test_a_flagged_page_is_walked_by_its_check_and_each_flag_is_one_pass(wiki):
    hub = wiki
    rana, sam = M(hub, "rana"), M(hub, "sam")
    page = _named(hub, "WikiPage", "Booking rules")
    ref = f"WikiPage/{page['id']}"
    first = open_run(hub, "wiki-check", ref)
    _board(hub, first, "check", rana, "confirm")
    assert hub.records.read("WikiPage", page["id"])["status"] == "published"
    assert not runs(hub, "wiki-check", ref, "running")  # a page in use is no one's work
    hub.update_record(sam, ref, {"status": "needs-check"})  # flagged again: the next pass
    second = open_run(hub, "wiki-check", ref)
    assert second["ctx"]["run"]["previous"] == first["id"]
    for who in (sam, rana):  # while it is flagged, only its check archives it (or puts it back), even for an owner
        with pytest.raises(Denied):
            hub.update_record(who, ref, {"status": "archived"})
    _board(hub, second, "check", rana, "archive")
    assert hub.records.read("WikiPage", page["id"])["status"] == "archived"
    hub.update_record(sam, ref, {"status": "draft"})  # Restore: being written again is no one else's work
    assert not runs(hub, "wiki-check", ref, "running")


def test_a_sop_revision_is_walked_from_draft_to_in_use_and_its_periodic_review_never_moves_it(wiki):
    hub = wiki
    noor, amara, rana = M(hub, "noor"), M(hub, "dr-amara"), M(hub, "rana")
    sop = hub.create_record(noor, "Procedure", {"title": "Sharps", "number": "SOP-090", "owner": "noor",
                                                "body": "Use the yellow bin."})
    ref = f"Procedure/{sop['id']}"
    first = open_run(hub, "wiki-sop-release", ref)
    assert step_state(hub, first, "write") == "waiting"
    hub.update_record(noor, ref, {"change_summary": "First issue.", "status": "in-review"})
    _board(hub, first, "release", amara, "release")
    assert hub.records.read("Procedure", sop["id"])["status"] == "released"
    assert step_state(hub, first, "in-use") == "waiting"
    # the periodic review comes due: its companion asks for a revision, and the SOP's own run opens it
    review = hub.engine.start(rana, "wiki-sop-review", record=ref, via_automation=True)
    _board(hub, review, "review", noor, "revise", "The bins changed colour")
    assert hub.engine.run(review["id"])["status"] == "completed"
    assert hub.records.read("Procedure", sop["id"])["status"] == "draft"
    moved = hub.db.events.find_one({"action": "record.transitioned", "subject": ref, "data.to": "draft"})
    assert moved["run_id"] == first["id"]  # moved by its owner, never by the review
    assert hub.engine.run(first["id"])["status"] == "completed"
    second = open_run(hub, "wiki-sop-release", ref)
    assert second["ctx"]["run"]["previous"] == first["id"]
    hub.update_record(noor, ref, {"body": "Use the red bin.", "change_summary": "New bins."})
    hub.update_record(noor, ref, {"status": "in-review"})
    with pytest.raises(Denied):  # who wrote this revision does not release it
        hub.engine.claim(noor, item_for(hub, second, "release"))
    _board(hub, second, "release", amara, "release")
    rec = hub.records.read("Procedure", sop["id"])
    assert rec["status"] == "released" and rec["revision"] == 2 and rec["revise_asked"] is False
    hub.update_record(rana, ref, {"status": "retired"}, comment="Replaced by SOP-091")
    assert not runs(hub, "wiki-sop-release", ref, "running")


def test_upgrading_the_wiki_hands_a_sop_in_review_on_to_the_new_version(hub, old_modules):
    for name in ("dms", "wiki"):
        install(hub, name)
    for name in ("dms", "wiki"):
        demo(hub, name)
    amara = M(hub, "dr-amara")
    sop = _named(hub, "Procedure", "Cleaning the treatment rooms")
    ref = f"Procedure/{sop['id']}"
    old = open_run(hub, "wiki-sop-release", ref)
    approve_all(hub, old_modules(hub, "dms"), amara)
    proposed = old_modules(hub, "wiki")
    for key in (("type", "Procedure"), ("workflow", "wiki-sop-release"), ("type", "WikiPage"),
                ("workflow", "wiki-check"), ("workflow", "wiki-sop-review")):
        assert key in proposed
    approve_all(hub, proposed, amara)
    _board(hub, old, "release", amara, "release")
    assert hub.engine.run(old["id"])["version"] == old["version"]
    new = open_run(hub, "wiki-sop-release", ref)  # in use now, walked by the new version
    assert new["version"] > old["version"] and step_state(hub, new, "in-use") == "waiting"
    page = _named(hub, "WikiPage", "Booking rules")  # flagged under version 1: its check goes on as it was
    assert runs(hub, "wiki-check", f"WikiPage/{page['id']}", "running")[0]["version"] == 1


# ------------------------------------------------------------------------------------------- inventory: StockMove, StockItem
@pytest.fixture
def stock(hub):
    def build(h):
        install(h, "inventory")
        for m in h.pack.modules:
            m.module.demo(h)
    snapshot_onto(hub, "modwf:stock", build, modules=["inventory"])
    return hub


def _sku(hub, sku):
    return next(i for i in hub.records.search("StockItem", limit=None) if i["sku"] == sku)


def test_a_stock_move_is_walked_from_draft_and_a_rejected_one_is_revised_and_walked_again(stock):
    hub = stock
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    gloves = _sku(hub, "GL-M")
    m = hub.create_record(rana, "StockMove", {"kind": "count", "item": f"StockItem/{gloves['id']}", "quantity": 18,
                                              "reason": "shelf count"})
    ref = f"StockMove/{m['id']}"
    first = open_run(hub, "inventory-post", ref)
    assert step_state(hub, first, "submit") == "waiting"  # with its author until submitted
    hub.update_record(rana, ref, {"status": "submitted"})
    with pytest.raises((Denied, Invalid)):  # sent for sign-off: its author does not take it back
        hub.update_record(rana, ref, {"status": "draft"})
    with pytest.raises(Denied):
        hub.engine.claim(rana, item_for(hub, first, "sign-off"))
    _board(hub, first, "sign-off", amara, "reject", "count it again")
    assert hub.records.read("StockMove", m["id"])["status"] == "rejected"
    assert hub.engine.run(first["id"])["status"] == "completed"
    second = open_run(hub, "inventory-post", ref)  # back with its author: the next pass
    assert second["ctx"]["run"]["previous"] == first["id"] and step_state(hub, second, "submit") == "waiting"
    hub.update_record(rana, ref, {"status": "draft"})  # Revise: a move the open step waits on
    hub.update_record(rana, ref, {"quantity": 19})
    hub.update_record(rana, ref, {"status": "submitted"})
    with pytest.raises(Denied):  # who wrote it never signs it off, on any pass
        hub.engine.claim(rana, item_for(hub, second, "sign-off"))
    _board(hub, second, "sign-off", amara, "post")
    assert hub.records.read("StockMove", m["id"])["status"] == "posted" and _sku(hub, "GL-M")["on_hand"] == 19
    assert not runs(hub, "inventory-post", ref, "running")  # posted: no one's work


def test_a_stock_item_reorder_cycle_is_one_run_and_a_delivery_restocks_it_through_the_wait(stock):
    hub = stock
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    syr = _sku(hub, "SY-3")
    ref = f"StockItem/{syr['id']}"
    first = open_run(hub, "inventory-reorder", ref)
    assert step_state(hub, first, "watch") == "waiting"
    with pytest.raises((Denied, Invalid)):  # low and on order are its reorder work's, never typed in
        hub.update_record(rana, ref, {"status": "low"})
    issue = hub.create_record(rana, "StockMove", {"kind": "issue", "item": ref, "quantity": 2})
    hub.update_record(rana, f"StockMove/{issue['id']}", {"status": "submitted"})  # 5 -> 3: at its reorder point
    assert _sku(hub, "SY-3")["status"] == "low"
    assert open_run(hub, "inventory-reorder", ref)["id"] == first["id"]  # the same run, now at its order
    i = item_for(hub, first, "order")
    hub.engine.claim(amara, i)
    hub.engine.complete(amara, i, {"quantity": 10, "order_ref": "PO-1"})
    assert _sku(hub, "SY-3")["status"] == "on-order" and step_state(hub, first, "delivered") == "waiting"
    rcv = hub.create_record(rana, "StockMove", {"kind": "receive", "item": ref, "quantity": 10})
    hub.update_record(rana, f"StockMove/{rcv['id']}", {"status": "submitted"})
    item = _sku(hub, "SY-3")
    assert item["on_hand"] == 13 and item["status"] == "stocked"
    post = runs(hub, "inventory-post", f"StockMove/{rcv['id']}")[0]
    moved = hub.db.events.find_one({"action": "record.transitioned", "subject": ref, "data.to": "stocked"})
    assert moved["run_id"] == post["id"]  # the delivery's workflow moved it, through the wait the cycle held
    assert hub.engine.run(first["id"])["status"] == "completed"
    second = open_run(hub, "inventory-reorder", ref)  # restocked: the next cycle watches it
    assert second["ctx"]["run"]["previous"] == first["id"] and step_state(hub, second, "watch") == "waiting"
    hub.update_record(rana, ref, {"status": "discontinued", "discontinue_reason": "No longer used"})
    assert not runs(hub, "inventory-reorder", ref, "running")  # no longer kept: no one's work


def test_an_order_task_is_withdrawn_when_its_item_is_no_longer_low(stock):
    hub = stock
    rana = M(hub, "rana")
    ref = f"StockItem/{_sku(hub, 'SY-3')['id']}"
    first = open_run(hub, "inventory-reorder", ref)
    issue = hub.create_record(rana, "StockMove", {"kind": "issue", "item": ref, "quantity": 2})
    hub.update_record(rana, f"StockMove/{issue['id']}", {"status": "submitted"})
    order = item_for(hub, first, "order")
    rcv = hub.create_record(rana, "StockMove", {"kind": "receive", "item": ref, "quantity": 10})
    hub.update_record(rana, f"StockMove/{rcv['id']}", {"status": "submitted"})  # a delivery posted before any order
    assert _sku(hub, "SY-3")["status"] == "stocked"
    assert step_state(hub, first, "order") == "skipped"
    assert hub.engine.item(order)["status"] not in ("open", "claimed")  # no stale order left on anyone's list
    assert hub.db.events.find_one({"action": "step.withdrawn", "run_id": first["id"], "data.step": "order"})
    assert hub.engine.run(first["id"])["status"] == "completed"
    assert step_state(hub, open_run(hub, "inventory-reorder", ref), "watch") == "waiting"


def test_upgrading_inventory_hands_work_under_way_on_and_picks_up_items_on_their_next_write(hub, old_modules):
    install(hub, "inventory")
    demo(hub, "inventory")
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    syr, gloves = _sku(hub, "SY-3"), _sku(hub, "GL-M")
    sref, gref = f"StockItem/{syr['id']}", f"StockItem/{gloves['id']}"
    w = hub.create_record(rana, "StockMove", {"kind": "write-off", "item": gref, "quantity": 2, "reason": "wet"})
    wref = f"StockMove/{w['id']}"
    hub.update_record(rana, wref, {"status": "submitted"})
    old_post = open_run(hub, "inventory-post", wref)  # version 1, started by its event
    issue = hub.create_record(rana, "StockMove", {"kind": "issue", "item": sref, "quantity": 2})
    hub.update_record(rana, f"StockMove/{issue['id']}", {"status": "submitted"})
    old_reorder = open_run(hub, "inventory-reorder", sref)
    assert not runs(hub, "inventory-reorder", gref)  # version 1: a stocked item is no one's work
    proposed = old_modules(hub, "inventory")
    for key in (("type", "StockMove"), ("type", "StockItem"), ("workflow", "inventory-post"),
                ("workflow", "inventory-reorder")):
        assert key in proposed
    approve_all(hub, proposed, amara)
    _board(hub, old_post, "sign-off", amara, "post")  # the run under way keeps its version
    assert hub.records.read("StockMove", w["id"])["status"] == "posted"
    assert hub.engine.run(old_post["id"])["status"] == "completed" and not runs(hub, "inventory-post", wref, "running")
    i = item_for(hub, old_reorder, "order")
    hub.engine.claim(amara, i)
    hub.engine.complete(amara, i, {"quantity": 10, "order_ref": "PO-2"})
    assert hub.engine.run(old_reorder["id"])["status"] == "completed"  # version 1 ended at on order
    new = open_run(hub, "inventory-reorder", sref)  # handed on: version 2 waits for the delivery
    assert new["version"] > old_reorder["version"] and step_state(hub, new, "delivered") == "waiting"
    # an item filed before is picked up by its workflow on its next write, once
    hub.update_record(rana, gref, {"supplier": "Another supplier"})
    picked = open_run(hub, "inventory-reorder", gref)
    assert step_state(hub, picked, "watch") == "waiting"
    hub.update_record(rana, gref, {"supplier": "A third supplier"})
    assert len(runs(hub, "inventory-reorder", gref)) == 1


# ------------------------------------------------------------------------------------------- crm: Interaction, Person
def _interaction(hub, who, **more):
    from practicehub.hub import member_ref
    hub.authz.write([("role:relationship-manager", "assignee", member_ref(who["id"]))])
    if not hub.records.search("Organization", limit=1):
        demo(hub, "crm")
    org = hub.records.search("Organization", limit=1)[0]
    it = hub.create_record(who, "Interaction", {"with": f"Organization/{org['id']}", "kind": "call",
                                                "at": f"{hub.engine.now().date().isoformat()}T09:30:00",
                                                "summary": "Delivery dates.", **more})
    return f"Interaction/{it['id']}"


def test_an_interaction_is_walked_by_its_follow_up_and_the_daily_sweep_never_moves_it(hub):
    install(hub, "crm")
    rana = M(hub, "rana")
    ref = _interaction(hub, rana)
    first = open_run(hub, "crm-follow-up", ref)  # logging it starts its follow-up, which notes who logged it
    rec = hub.records.read(*ref.split("/", 1))
    assert rec["logged_by"] == "rana" and rec["status"] == "logged" and step_state(hub, first, "due") == "waiting"
    assert not hub.db.workflows.find_one({"workflow_id": "crm-interaction-logged"})  # merged into its owner
    hub.update_record(rana, ref, {"follow_up": hub.engine.now().date().isoformat()})
    hub.engine.tick(hub.engine.now())  # the date came: the sweep marks it, and the waiting follow-up takes it
    rec = hub.records.read(*ref.split("/", 1))
    assert rec["follow_up_asked"] == rec["follow_up"] and rec["status"] == "follow-up"
    sweep = next(r for r in hub.engine.runs() if r["workflow_id"] == "crm-follow-up-due" and r["record"] == ref)
    moved = hub.db.events.find_one({"action": "record.transitioned", "subject": ref, "data.to": "follow-up"})
    assert moved["run_id"] == first["id"] and moved["run_id"] != sweep["id"]  # moved by its owner, never the sweep
    with pytest.raises((Denied, Invalid)):  # being followed up: its workflow says when it is done
        hub.update_record(rana, ref, {"status": "logged"})
    # cancelled part way, it is as Not needed: back to logged, and the next run waits for the next date
    hub.engine.cancel(rana, first["id"], "Wrong person")
    second = open_run(hub, "crm-follow-up", ref)
    assert second["ctx"]["run"]["previous"] == first["id"] and step_state(hub, second, "due") == "waiting"
    assert hub.records.read(*ref.split("/", 1))["status"] == "logged"
    hub.engine.tick(hub.engine.now())  # the same date does not come back
    assert hub.records.read(*ref.split("/", 1))["status"] == "logged"
    # one with no date yet is followed up now by a person: a move its waiting step takes
    now = _interaction(hub, rana)
    waiting = open_run(hub, "crm-follow-up", now)
    hub.update_record(rana, now, {"status": "follow-up"})
    assert step_state(hub, waiting, "due") == "done" and step_state(hub, waiting, "asked") == "skipped"
    assert open_run(hub, "crm-follow-up", now)["id"] == waiting["id"]


def test_upgrading_crm_retires_the_logged_note_and_picks_up_interactions_filed_before(hub, old_modules):
    install(hub, "crm")
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    ref = _interaction(hub, rana)
    rec = hub.records.read(*ref.split("/", 1))
    assert rec["logged_by"] == "rana" and "status" not in rec  # version 1: no lifecycle, no one's work
    proposed = old_modules(hub, "crm")
    for key in (("type", "Interaction"), ("workflow", "crm-follow-up")):
        assert key in proposed
    approve_all(hub, proposed, amara)
    assert not runs(hub, "crm-follow-up", ref, "running")
    hub.update_record(rana, ref, {"summary": "Delivery dates, and a new price list."})  # its next write
    rec = hub.records.read(*ref.split("/", 1))
    assert rec["status"] == "logged"  # filed before its type had a lifecycle: in its first state
    picked = open_run(hub, "crm-follow-up", ref)
    assert step_state(hub, picked, "due") == "waiting"
    hub.update_record(rana, ref, {"summary": "Delivery dates and prices."})
    assert len(runs(hub, "crm-follow-up", ref)) == 1  # picked up once
