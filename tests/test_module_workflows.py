"""Each module's records are walked by their own workflow: the record type names it (``workflow = {id, when}``),
making a record starts it, and the record's lifecycle state is the run's position. One run per pass of the
lifecycle, one item per run, reassigned at each step; a lifecycle that loops (a revision, a CAPA reopened, a change
order sent back) is walked by the next run, which knows who did what before. Workflows that are not a lifecycle (a
scheduled sweep, a reaction) start on their schedule or event and never move a state another workflow owns."""

import re
import shutil

import pytest

from conftest import M, item_for, snapshot_onto
from practicehub.hub import Denied, Invalid
from test_web import call, server, token  # noqa: F401 - server is a fixture: the pages a signer reads


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


def test_a_document_in_force_is_proposed_obsolete_with_its_reason_and_nothing_else_changes(docs):
    """An approved document is locked: it is not edited. A person still moves it on, and the move writes what its
    dialog asks for (the reason it is withdrawn) and nothing else."""
    hub = docs
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    d = hub.create_record(rana, "Document", {"title": "Sharps", "body": "Use the yellow bin."})
    ref = f"Document/{d['id']}"
    hub.update_record(rana, ref, {"status": "in-review"})
    i = item_for(hub, open_run(hub, "dms-review", ref), "approve")
    hub.engine.claim(amara, i)
    hub.engine.decide(amara, i, "approve")
    assert hub.records.read("Document", d["id"])["status"] == "approved"
    with pytest.raises(Invalid, match="is not edited"):
        hub.update_record(rana, ref, {"body": "Use any bin."})
    with pytest.raises(Invalid, match="is not edited"):  # the move does not carry an edit with it
        hub.update_record(rana, ref, {"status": "retiring", "obsolete_reason": "Replaced", "body": "Use any bin."})
    with pytest.raises(Invalid):  # and its reason is still required: spaces say nothing
        hub.move_record(rana, ref, "retiring", {"obsolete_reason": "   "})
    hub.move_record(rana, ref, "retiring", {"obsolete_reason": "Replaced by the sharps procedure"})
    rec = hub.records.read("Document", d["id"])
    assert rec["status"] == "retiring" and rec["obsolete_reason"] == "Replaced by the sharps procedure"
    assert rec["body"] == "Use the yellow bin."


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
    assert hub.engine.run(run["id"])["status"] == "completed"  # that pass ends; the next one decides it
    nxt = open_run(hub, "dms-review", ref)
    assert nxt["ctx"]["run"]["previous"] == run["id"]
    g = item_for(hub, nxt, "retire")
    assert hub.engine.item(g)["role"] == "document-controller"
    hub.engine.claim(amara, g)
    hub.engine.decide(amara, g, "obsolete")
    assert hub.records.read("Document", d["id"])["status"] == "obsolete"
    assert hub.engine.run(nxt["id"])["status"] == "completed"
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
    assert "Pass 3" in page and "Say where the bins are" in page and "the pass before ended" in page  # how pass 2 ended
    sign = page.split("What you sign")[1]
    assert "Use the yellow bin by the sink." in sign  # the record, open, above Decide
    assert "What changed since the version in use" in sign and "Use a bin." in sign  # against the approved one
    run_page = call(server, "GET", f"/records/{ref}", amara)[1]  # the run's page is the record's
    assert "pass 3" in run_page.split('class="rec-status"', 1)[1].split("</div>", 1)[0]
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


# ------------------------------------------------------------------------------------------- dms: upgraded, rolled back
@pytest.fixture
def earlier_dms(hub, tmp_path, monkeypatch):
    """The hub with an earlier version of document control installed, made from the files shipped today: its Document
    type does not name its workflow yet, and that workflow starts itself when a document is made
    (``record.created:Document``), as modules did before types named their workflows. Calling it puts today's files
    in the modules directory, has them reviewed and upgrades to them: what that proposed, ``{(kind, id): version}``."""
    from practicehub import pack as packmod
    shipped, d = packmod.MODULES_DIR / "dms", tmp_path / "modules" / "dms"

    def copy():
        shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(shipped, d, ignore=shutil.ignore_patterns("__pycache__"))

    def change(name, pattern, to):
        text, n = re.subn(pattern, to, (d / name).read_text(), flags=re.M)
        assert n == 1, f"{name}: {pattern!r} is there {n} times, not once"
        (d / name).write_text(text)

    copy()
    change("types/Document.toml", r"^workflow = .*\n", "")
    change("workflows/dms-review.toml", r'^record = "Document"\n',
           'record = "Document"\n\n[trigger]\nevent = "record.created:Document"\n')
    change("module.toml", r'^version = "(\d+)"$', lambda m: f'version = "{int(m.group(1)) - 1}"')
    monkeypatch.setattr(packmod, "MODULES_DIR", d.parent)
    install(hub, "dms")
    demo(hub, "dms")  # the policy owners hold its roles

    def upgrade():
        copy()
        hub.approve_module_code(M(hub, "dr-amara"), "dms")
        out = hub.upgrade_module(M(hub, "rana"), "dms")
        return {(p["kind"], p["id"]): p["version"] for p in out["proposed"]}
    return upgrade


def _document(hub, title, by="rana"):
    d = hub.create_record(M(hub, by), "Document", {"title": title, "body": "B.", "owner": by})
    return f"Document/{d['id']}"


def _versions(hub, wid, ref):
    return [r["version"] for r in runs(hub, wid, ref)]


def test_an_upgrade_hands_documents_over_type_first_and_work_under_way_keeps_its_version(hub, earlier_dms):
    """Rule: an upgrade after which a record type names its workflow proposes both, and they are approved in order:
    the workflow version that stops starting itself only after the type that names it (``Hub._check_hand_over``).
    Between the two approvals the earlier version goes on starting itself, once per new record. When the workflow is
    approved, a record nothing walks (its earlier run ended) is picked up where it is, once (``Hub.walk_unwalked``),
    and a run under way keeps the version it started on: the version in force walks the record's next pass."""
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    ref, ended = _document(hub, "Cover rota"), _document(hub, "Sharps")
    old = open_run(hub, "dms-review", ref)  # the earlier version: started by its own event when a document is made
    was = open_run(hub, "dms-review", ended)
    hub.engine.cancel(rana, was["id"], "not written yet")  # nothing walks this one when the upgrade comes
    proposed = earlier_dms()
    assert set(proposed) == {("type", "Document"), ("workflow", "dms-review")}  # proposed: not in force yet
    new = proposed[("workflow", "dms-review")]
    assert not hub.record_type("Document").workflow and hub.workflow("dms-review")[1]["version"] == old["version"]
    with pytest.raises(Invalid) as no:  # it would stop starting itself before its type starts it
        hub.approve_workflow(amara, "dms-review", new)
    assert "record type first" in str(no.value)
    hub.approve(amara, "type", "Document", proposed[("type", "Document")])
    # the type names the workflow, whose new version is not approved yet: the earlier one goes on starting itself
    between = _document(hub, "Interim")
    assert _versions(hub, "dms-review", between) == [old["version"]]
    assert not runs(hub, "dms-review", ended, "running")
    hub.approve_workflow(amara, "dms-review", new)
    # the document nothing walked is picked up where it is, at the approval and once; work under way is left alone
    picked = open_run(hub, "dms-review", ended)
    assert picked["version"] == new and picked["ctx"]["run"]["previous"] == was["id"]
    assert step_state(hub, picked, "revise") == "active"
    assert hub.walk_unwalked() == {}
    assert _versions(hub, "dms-review", ref) == _versions(hub, "dms-review", between) == [old["version"]]
    assert _versions(hub, "dms-review", _document(hub, "Rota")) == [new]  # a new one: started by its type, once
    # the run under way keeps its version, and finishes its review as it began
    hub.update_record(rana, ref, {"status": "in-review"})
    i = item_for(hub, old, "approve")
    hub.engine.claim(amara, i)
    hub.engine.decide(amara, i, "approve")
    assert hub.records.read(*ref.split("/"))["status"] == "approved"
    assert open_run(hub, "dms-review", ref)["id"] == old["id"] and step_state(hub, old, "in-force") == "waiting"
    assert hub.engine.run(old["id"])["version"] == old["version"]
    # revised: the pass the earlier version walked ends there, and the version in force walks the next one
    hub.update_record(rana, ref, {"status": "draft"})
    assert hub.engine.run(old["id"])["status"] == "completed"
    nxt = open_run(hub, "dms-review", ref)
    assert nxt["version"] == new and nxt["ctx"]["run"]["previous"] == old["id"]


def test_rolling_back_an_approved_upgrade_proposes_the_earlier_versions_and_stops_the_newer_runs(hub, earlier_dms):
    """Rule: rolling back a module upgrade whose definitions were approved proposes again what was in force before
    them (``Hub.roll_back_module``), for another policy owner to approve as any change; nothing stops until then.
    Once the type in force no longer names the workflow, that workflow's open runs on the type's records are stopped
    (``Hub._release_runs``: ``run.stopped`` on the track), and the earlier workflow, approved again, starts itself
    as it did."""
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    proposed = earlier_dms()
    hub.approve(amara, "type", "Document", proposed[("type", "Document")])
    hub.approve_workflow(amara, "dms-review", proposed[("workflow", "dms-review")])
    ref = _document(hub, "Cover rota")
    walk = open_run(hub, "dms-review", ref)  # walked by the newer version, started by its type
    for who in (hub.engine.automation_actor(walk), M(hub, "sam")):  # a person who is a policy owner rolls back
        with pytest.raises(Denied):
            hub.roll_back_module(who, "dms")
    back = hub.roll_back_module(rana, "dms", "the new review is not ready")
    got = {(p["kind"], p["id"]): p["version"] for p in back["proposed"]}
    assert set(got) == {("type", "Document"), ("workflow", "dms-review")} and not back["withdrawn"]
    for (kind, did), version in got.items():  # the text in force before the upgrade, as a new version
        table, col = hub.KINDS[kind]
        assert hub.db[table].find_one({col: did, "version": version})["source"] == \
            hub.db[table].find_one({col: did, "version": 1})["source"]
    said = hub.db.events.find_one({"action": "module.rolled_back", "subject": "module:dms"})
    assert said["actor"] == "rana" and said["data"]["reason"] == "the new review is not ready"
    assert hub.engine.run(walk["id"])["status"] == "running"  # proposed only: nothing changes until it is approved
    with pytest.raises(Denied):  # whoever rolled it back does not also approve
        hub.approve(rana, "type", "Document", got[("type", "Document")])
    hub.approve(amara, "type", "Document", got[("type", "Document")])
    assert hub.engine.run(walk["id"])["status"] == "cancelled"  # its type no longer names it
    stopped = hub.db.events.find_one({"action": "run.stopped", "run_id": walk["id"]})
    assert stopped and "no longer names this workflow" in stopped["data"]["reason"]
    assert not [i for i in items_of(hub, walk) if i["status"] in ("open", "claimed", "waiting")]
    hub.approve_workflow(amara, "dms-review", got[("workflow", "dms-review")])
    assert not hub.record_type("Document").workflow  # its type starts nothing: its own event does, as before
    assert _versions(hub, "dms-review", _document(hub, "Interim")) == [got[("workflow", "dms-review")]]


def test_rolling_back_an_upgrade_not_yet_approved_withdraws_what_it_proposed(hub, earlier_dms):
    """Rule: rolling back a module upgrade none of whose definitions is approved yet withdraws what it proposed and
    proposes nothing: the versions in force stay in force, and work under way goes on. A module never upgraded has
    nothing to roll back."""
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    ref = _document(hub, "Cover rota")
    old = open_run(hub, "dms-review", ref)
    with pytest.raises(Invalid):
        hub.roll_back_module(rana, "dms")
    proposed = earlier_dms()
    back = hub.roll_back_module(rana, "dms")
    assert {(w["kind"], w["id"]): w["version"] for w in back["withdrawn"]} == proposed and not back["proposed"]
    for (kind, did), version in proposed.items():
        table, col = hub.KINDS[kind]
        assert hub.db[table].find_one({col: did, "version": version})["status"] == "withdrawn"
        assert not hub.db[table].find_one({col: did, "status": "proposed"})
    with pytest.raises(Invalid):  # withdrawn: no longer there to approve
        hub.approve(amara, "type", "Document", proposed[("type", "Document")])
    assert not hub.record_type("Document").workflow and hub.workflow("dms-review")[1]["version"] == old["version"]
    assert open_run(hub, "dms-review", ref)["id"] == old["id"]
    assert _versions(hub, "dms-review", _document(hub, "Interim")) == [old["version"]]  # started as it was


FORM = 'id = "Form"\nlabel = "Form"\ndata_class = "documents"\n[[field]]\nname = "title"\nrequired = true\n'
FORM_CHECK = ('id = "dms-form-check"\ntitle = "Check a form"\nrecord = "Form"\n[trigger]\n'
              'starters = ["document-controller"]\n[start]\nto = "checking"\n[[state]]\nid = "checking"\n'
              '[[state.work]]\nid = "check"\nkind = "task"\nrole = "document-controller"\n'
              '[[state.work.fields]]\nname = "note"\ntype = "text"\n'
              '[[edge]]\nid = "checked"\nfrom = "checking"\nto = "checked"\n[[state]]\nid = "checked"\n')


def test_an_upgrade_retires_a_type_the_module_no_longer_ships_and_the_workflow_about_it(hub, tmp_path, monkeypatch):
    """Rule: what a module no longer ships is retired by the upgrade itself, not proposed: a record type too, after
    the workflows about it (a type an active workflow is about is never retired)."""
    from practicehub import pack as packmod
    from practicehub.hub import NotFound
    mods = tmp_path / "modules"
    shutil.copytree(packmod.MODULES_DIR / "dms", mods / "dms", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(packmod, "MODULES_DIR", mods)
    gone = {"types/Form.toml": FORM, "workflows/dms-form-check.toml": FORM_CHECK}
    for name, source in gone.items():  # the earlier version: today's, with a type and a workflow about it besides
        (mods / "dms" / name).write_text(source)
    install(hub, "dms")
    assert hub.record_type("Form") and hub.workflow("dms-form-check")
    for name in gone:  # today's module no longer ships them
        (mods / "dms" / name).unlink()
    assert sorted(hub.module_plan("dms")["removed"]) == ["type Form", "workflow dms-form-check"]
    hub.approve_module_code(M(hub, "dr-amara"), "dms")
    out = hub.upgrade_module(M(hub, "rana"), "dms")
    assert not out["proposed"] and hub.record_type("Form") is None
    with pytest.raises(NotFound):
        hub.workflow("dms-form-check")


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
    assert rec["status"] == "further-action" and rec["not_effective_because"] == "Still missed on weekends"
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
    d1, d2 = (f"Document/{hub.create_record(rana, 'Document', {'title': t, 'body': 'B.'})['id']}"
              for t in ("Callbacks", "Weekend cover"))
    _approve_docs(hub, [d1, d2], amara)  # two documents in force, whatever the demo holds
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
    assert step_state(hub, second, "revise") == "waiting"  # rejected: revised or cancelled by its author
    hub.update_record(rana, cref, {"status": "draft"})  # Revise
    assert step_state(hub, second, "revise") == "done" and step_state(hub, second, "submit") == "waiting"
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


# ------------------------------------------------------------------------------------------- wiki: WikiPage; wiki-sop
@pytest.fixture
def wiki(hub):
    def build(h):
        for name in ("dms", "wiki", "wiki-sop"):
            install(h, name)
        for m in h.pack.modules:
            m.module.demo(h)
    snapshot_onto(hub, "modwf:wiki", build, modules=["dms", "wiki", "wiki-sop"])
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


def test_a_wiki_page_made_a_controlled_document_is_a_pdf_of_the_page_filed_as_a_draft(wiki):
    hub = wiki
    rana, amara, sam = M(hub, "rana"), M(hub, "dr-amara"), M(hub, "sam")
    page = _named(hub, "WikiPage", "Hand hygiene")
    pref = f"WikiPage/{page['id']}"
    made = runs(hub, "wiki-sop-file", pref)  # the demo made it SOP-021
    assert len(made) == 1 and made[0]["status"] == "completed"
    doc = next(d for d in hub.records.search("Document", limit=None) if d.get("number") == "SOP-021")
    dref = f"Document/{doc['id']}"
    assert doc["status"] == "draft" and doc["title"] == "Hand hygiene"
    assert doc["file"]["contentType"] == "application/pdf"
    data, _ = hub.read_file(amara, dref, doc["file"]["blob"])
    assert data.startswith(b"%PDF-") and b"Hand hygiene" in data and b"Clean your hands" in data
    assert {"rel": "mentions", "ref": dref} in hub.linked(amara, pref)  # the page names the document made from it
    assert hub.records.read("WikiPage", page["id"])["status"] == "published"  # the page is not changed
    # document control's own workflow walks it from there: sent for review, approved by a controller
    first = open_run(hub, "dms-review", dref)
    hub.update_record(M(hub, doc["owner"]), dref, {"status": "in-review"})
    approver = amara if doc["owner"] != "dr-amara" else rana
    _board(hub, first, "approve", approver, "approve")
    assert hub.records.read("Document", doc["id"])["status"] == "approved"
    # a number in use is not given twice; only a document author makes one
    run = hub.engine.start(rana, "wiki-sop-file", record=pref, inputs={"number": "sop-021"})
    assert hub.engine.run(run["id"])["status"] != "completed"
    assert sum(1 for d in hub.records.search("Document", limit=None) if d.get("number") == "SOP-021") == 1
    hub.engine.cancel(rana, run["id"], "wrong number")
    with pytest.raises(Denied):
        hub.engine.start(sam, "wiki-sop-file", record=pref, inputs={"number": "SOP-099"})


def test_a_controlled_documents_periodic_review_confirms_it_or_asks_for_a_revision_and_never_moves_it(wiki):
    from datetime import datetime, timedelta
    hub = wiki
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    d = hub.create_record(rana, "Document", {"title": "Sharps", "number": "SOP-090", "owner": "rana",
                                             "body": "Use the yellow bin."})
    ref = f"Document/{d['id']}"
    first = open_run(hub, "dms-review", ref)
    hub.update_record(rana, ref, {"change_summary": "First issue.", "status": "in-review"})
    _board(hub, first, "approve", amara, "approve")
    rec = hub.records.read("Document", d["id"])
    today = hub.engine.now().date()
    assert rec["status"] == "approved" and rec["review_by"] == today.replace(year=today.year + 1).isoformat()
    assert step_state(hub, first, "in-force") == "waiting"
    # a year and a day on, the clock asks its owner to review it: they confirm it
    due = datetime.fromisoformat(rec["review_by"]) + timedelta(days=1, hours=8)
    hub.engine.tick(due)
    review = open_run(hub, "dms-periodic-review", ref)
    assert hub.records.read("Document", d["id"])["review_asked"] == rec["review_by"]
    _board(hub, review, "review", rana, "confirm")
    assert hub.engine.run(review["id"])["status"] == "completed"
    assert hub.records.read("Document", d["id"])["status"] == "approved"
    # the next one asks for a revision: the document's own run opens it, numbered; the review moves nothing
    rec = hub.records.read("Document", d["id"])
    rec = {k: x for k, x in rec.items() if k != "meta"}
    hub.records.update("Document", d["id"], {**rec, "review_by": (today - timedelta(days=2)).isoformat()})
    hub.engine.tick(due + timedelta(days=1))
    review = open_run(hub, "dms-periodic-review", ref)
    _board(hub, review, "review", rana, "revise", "The bins changed colour")
    rec = hub.records.read("Document", d["id"])
    assert rec["status"] == "draft" and rec["revise_asked"] is False
    moved = hub.db.events.find_one({"action": "record.transitioned", "subject": ref, "data.to": "draft"})
    assert moved["run_id"] == first["id"]  # moved by its own run, never by the review
    second = open_run(hub, "dms-review", ref)
    assert second["ctx"]["run"]["previous"] == first["id"]


# ------------------------------------------------------------------------------------------- inventory: StockMove, Part
@pytest.fixture
def stock(hub):
    def build(h):
        install(h, "inventory")
        for m in h.pack.modules:
            m.module.demo(h)
    snapshot_onto(hub, "modwf:stock", build, modules=["inventory"])
    return hub


def _stock_lot(hub, code):
    part = next(p for p in hub.records.search("Part", limit=None) if p["code"] == code)
    return next(x for x in hub.records.search("StockItem", limit=None) if x["part"] == f"Part/{part['id']}")


def _sign_policy(hub, who, level):
    rec = hub.create_record(who, "InventoryPolicy", {"level": level})
    ref = f"InventoryPolicy/{rec['id']}"
    hub.update_record(who, ref, {"status": "in-review"})
    _board(hub, open_run(hub, "inventory-policy", ref), "sign-own", who, "in-force")
    assert hub.records.read("InventoryPolicy", rec["id"])["status"] == "in-force"
    assert not runs(hub, "inventory-policy", ref, "running")   # in force: no one's work until it is revised


def test_a_stock_move_is_walked_from_draft_and_a_rejected_one_is_revised_and_walked_again(stock):
    hub = stock
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    _sign_policy(hub, rana, "tracked")
    gloves = _stock_lot(hub, "GL-M")
    m = hub.create_record(rana, "StockMove", {"kind": "count", "lot": f"StockItem/{gloves['id']}", "quantity": 18,
                                              "reason": "shelf count"})
    ref = f"StockMove/{m['id']}"
    first = open_run(hub, "inventory-post", ref)
    assert step_state(hub, first, "submit") == "waiting"  # with its author until submitted
    hub.update_record(rana, ref, {"status": "submitted"})
    assert step_state(hub, first, "sign-own") == "skipped"   # the policy asks for a second person
    with pytest.raises((Denied, Invalid)):  # sent for sign-off: its author does not take it back
        hub.update_record(rana, ref, {"status": "draft"})
    with pytest.raises(Denied):
        hub.engine.claim(rana, item_for(hub, first, "sign-other"))
    _board(hub, first, "sign-other", amara, "reject", "count it again")
    assert hub.records.read("StockMove", m["id"])["status"] == "rejected"
    assert hub.engine.run(first["id"])["status"] == "completed"
    second = open_run(hub, "inventory-post", ref)  # back with its author: the next pass
    assert second["ctx"]["run"]["previous"] == first["id"] and step_state(hub, second, "revise") == "waiting"
    hub.update_record(rana, ref, {"status": "draft"})  # Revise: a move the rejected state's wait takes
    assert step_state(hub, second, "submit") == "waiting"  # back in draft, in the same run
    hub.update_record(rana, ref, {"quantity": 19})
    hub.update_record(rana, ref, {"status": "submitted"})
    with pytest.raises(Denied):  # who wrote it never signs it off, on any pass
        hub.engine.claim(rana, item_for(hub, second, "sign-other"))
    _board(hub, second, "sign-other", amara, "post")
    assert hub.records.read("StockMove", m["id"])["status"] == "posted"
    assert hub.records.read("StockItem", gloves["id"])["quantity"] == 19
    assert not runs(hub, "inventory-post", ref, "running")  # posted: no one's work


def test_a_stock_move_posted_at_once_is_walked_by_one_run(stock):
    hub = stock
    rana = M(hub, "rana")
    lot = _stock_lot(hub, "SY-3")
    rcv = hub.create_record(rana, "StockMove", {"kind": "issue", "lot": f"StockItem/{lot['id']}", "quantity": 2})
    hub.update_record(rana, f"StockMove/{rcv['id']}", {"status": "submitted"})
    assert hub.records.read("StockMove", rcv["id"])["status"] == "posted"
    run = runs(hub, "inventory-post", f"StockMove/{rcv['id']}")
    assert len(run) == 1 and step_state(hub, run[0], "sign-own") == step_state(hub, run[0], "sign-other") == "skipped"


def test_a_part_is_walked_into_use_and_a_revision_is_walked_again(stock):
    hub = stock
    rana, amara = M(hub, "rana"), M(hub, "dr-amara")
    p = hub.create_record(rana, "Part", {"code": "MK-1", "name": "Face masks"})
    ref = f"Part/{p['id']}"
    assert hub.records.read("Part", p["id"])["status"] == "active"   # basic: in use at once, by its own run
    assert not runs(hub, "inventory-part", ref, "running")
    _sign_policy(hub, rana, "regulated")
    hub.update_record(rana, ref, {"status": "draft"}, comment="new supplier's mask")   # Revise
    first = open_run(hub, "inventory-part", ref)
    assert step_state(hub, first, "submit") == "waiting" and step_state(hub, first, "open") == "skipped"
    hub.update_record(rana, ref, {"name": "Face masks, type IIR", "status": "in-review"})
    with pytest.raises((Denied, Invalid)):   # in review: locked
        hub.update_record(rana, ref, {"name": "Masks"})
    _board(hub, first, "approve", amara, "send-back", "say which standard")
    got = hub.records.read("Part", p["id"])
    assert got["status"] == "draft" and got["return_reason"] == "say which standard"
    second = open_run(hub, "inventory-part", ref)
    assert second["ctx"]["run"]["previous"] == first["id"]
    hub.update_record(rana, ref, {"notes": "EN 14683 type IIR", "status": "in-review"})
    _board(hub, second, "approve", amara, "approve")
    got = hub.records.read("Part", p["id"])
    assert (got["status"], got["revision"]) == ("active", 1) and not runs(hub, "inventory-part", ref, "running")


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
    assert step_state(hub, waiting, "due") == "done" and step_state(hub, waiting, "taken") == "done"
    assert open_run(hub, "crm-follow-up", now)["id"] == waiting["id"]
