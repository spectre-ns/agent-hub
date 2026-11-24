"""The usability review: one open run per patient (and the button pressed twice), a filter that tells the truth,
pages in sentences rather than code, plain words for the jargon, Done that takes the work, and a board that says
what its marks mean."""

import html
import re

import pytest

from conftest import M, item_for
from practicehub import expr
from practicehub.hub import Duplicate
from practicehub.models import Standin
from practicehub.web.assets import JS
from practicehub.workflows import DefinitionError, parse
from test_web import call, server, token  # noqa: F401

CALLBACK = '''id = "callback"
title = "Call back"
record = "Patient"
[trigger]
starters = ["front-desk"]
one_open_per = "input.phone"
allow_duplicate = true
[[input]]
name = "phone"
label = "Phone number"
type = "string"
[start]
to = "to-call"
[[state]]
id = "to-call"
[[state.work]]
id = "call"
title = "Call them back"
kind = "task"
role = "front-desk"
[[state.work.fields]]
name = "note"
label = "What was said"
type = "text"
[[edge]]
id = "called"
from = "to-call"
to = "called-back"
[[state]]
id = "called-back"
'''


def _pat(hub):
    return "Patient/" + hub.records.search("Patient", limit=1)[0]["id"]


def _install(hub, src):
    wid = src.split('"')[1]
    p = hub.propose_workflow(M(hub, "jo"), src, "usability review")
    hub.approve_workflow(M(hub, "rana"), wid, p["version"])
    return wid


def _text(page: str) -> str:
    """What a reader sees: the page without its scripts and tags."""
    page = re.sub(r"<script.*?</script>|<style.*?</style>|<details>.*?</details>", "", page, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", page))


# ---- 1. no duplicates

def test_one_open_request_per_patient(hub):
    jo, pats = M(hub, "jo"), hub.records.search("Patient")
    first = hub.engine.start(jo, "appointment-request", record=f"Patient/{pats[0]['id']}",
                             inputs={"reason": "check-up", "urgency": "routine"})
    with pytest.raises(Duplicate) as exc:
        hub.engine.start(jo, "appointment-request", record=f"Patient/{pats[0]['id']}",
                         inputs={"reason": "check-up", "urgency": "routine"})
    assert exc.value.run["id"] == first["id"] and not exc.value.allowed
    with pytest.raises(Duplicate):  # not allowed by the workflow: confirming changes nothing
        hub.engine.start(jo, "appointment-request", record=f"Patient/{pats[0]['id']}",
                         inputs={"reason": "check-up", "urgency": "routine"}, allow_duplicate=True)
    hub.engine.start(jo, "appointment-request", record=f"Patient/{pats[1]['id']}",
                     inputs={"reason": "check-up", "urgency": "routine"})  # another patient: fine
    # one open request: the patient's booked visits walk the same workflow, and are not requests
    assert len(hub.db.runs.find({"workflow_id": "appointment-request", "status": "running",
                                 "ctx.run.start_state": "requested", "subject": f"Patient/{pats[0]['id']}"})) == 1


def test_a_second_press_names_the_open_item(hub, server):  # noqa: F811
    pat = hub.records.search("Patient")[0]
    jo = token(hub, "jo")
    form = {"_workflow": "appointment-request", "_record": f"Patient/{pat['id']}", "reason": "check-up",
            "urgency": "routine", "kind": "check-up"}
    code, _ = call(server, "POST", "/start", jo, form=form)
    assert code in (200, 303)  # started: the run, or a page saying so
    asked = {"workflow_id": "appointment-request", "ctx.run.start_state": "requested", "subject": f"Patient/{pat['id']}"}
    run = hub.db.runs.find_one(asked)
    code, page = call(server, "POST", "/start", jo, form=form)
    assert code == 409
    assert f'href="/items/{run["item"]}"' in page and "is already open for this" in page
    assert "Start another anyway" not in page  # the workflow does not allow another
    assert len(hub.db.runs.find(asked)) == 1


def test_start_another_anyway_only_where_the_workflow_allows(hub, server):  # noqa: F811
    _install(hub, CALLBACK)
    jo = token(hub, "jo")
    form = {"_workflow": "callback", "_record": _pat(hub), "phone": "902 555 0101"}
    assert call(server, "POST", "/start", jo, form=form)[0] == 303
    code, page = call(server, "POST", "/start", jo, form=form)
    assert code == 409 and "Start another anyway" in page and "Call them back" in page
    assert call(server, "POST", "/start", jo, form={**form, "_anyway": "1"})[0] == 303
    assert len(hub.db.runs.find({"workflow_id": "callback"})) == 2
    assert call(server, "POST", "/start", jo, form={**form, "phone": "902 555 0199"})[0] == 303


def test_an_event_is_never_refused_as_a_duplicate(hub):
    _install(hub, CALLBACK)
    jo = M(hub, "jo")
    hub.engine.start(jo, "callback", record=_pat(hub), inputs={"phone": "1"})
    hub.engine.start(jo, "callback", record=_pat(hub), inputs={"phone": "1"}, via_automation=True)
    assert len(hub.db.runs.find({"workflow_id": "callback"})) == 2


def test_one_open_per_is_checked():
    for bad, says in (('one_open_per = "patient"\n', "\"subject\" or \"input."),
                      ('one_open_per = "input.nope"\n', "'nope' is not one of"),
                      ('one_open_per = "record"\n', "needs a record"),
                      ('allow_duplicate = true\n', "needs one_open_per")):
        with pytest.raises(DefinitionError) as exc:
            src = CALLBACK.replace('one_open_per = "input.phone"\nallow_duplicate = true\n', bad)
            parse(src.replace('record = "Patient"\n', "") if "record" in bad else src)
        assert any(says in p for p in exc.value.problems), (bad, exc.value.problems)
    wf = parse(CALLBACK)
    assert wf.one_open_per == "input.phone" and wf.allow_duplicate


def test_a_form_is_sent_once():
    assert "function freeForm" in JS and "dataset.sending" in JS and "aria-busy" in JS


# ---- 2. the status filter

def test_any_status_shows_every_run_and_stays_chosen(hub, server):  # noqa: F811
    jo, pats = M(hub, "jo"), hub.records.search("Patient")
    gone = hub.engine.start(jo, "appointment-request", record=f"Patient/{pats[0]['id']}",
                            inputs={"reason": "check-up", "urgency": "routine"})
    hub.engine.cancel(M(hub, "rana"), gone["id"], "booked by phone")
    live = hub.engine.start(jo, "appointment-request", record=f"Patient/{pats[1]['id']}",
                            inputs={"reason": "check-up", "urgency": "routine"})
    rana = token(hub, "rana")
    # a row opens the record the work walks (as in Jira), not the run's steps
    gone_at, live_at = (hub.engine.run(r["id"])["record"].partition("/")[2] for r in (gone, live))
    _, page = call(server, "GET", "/runs?status=any", rana)
    assert '<option value="any" selected' in page
    assert gone_at in page and live_at in page and f'/runs/{live["id"]}' not in page
    _, page = call(server, "GET", "/runs", rana)
    assert gone_at not in page and live_at in page  # the default: in progress


# ---- 3. sentences, not code

def test_conditions_read_as_sentences():
    assert expr.words("input.flag == 'critical'") == "flag is critical"
    assert expr.words("not (input.flag == 'critical' or check.band == 'critical')") == \
        "neither flag is critical nor band is critical"
    assert expr.words("review.outcome in ['approve', 'approve-with-changes']") == \
        "outcome is one of approve or approve with changes"
    assert expr.words("((") == "a condition is met"


def test_run_and_track_pages_speak_plainly(worked, server):  # noqa: F811
    hub = worked
    rana, amara = token(hub, "rana"), token(hub, "dr-amara")
    run = next(r for r in hub.engine.runs(limit=50) if r["workflow_id"] == "lab-result-review")
    _, page = call(server, "GET", f"/records/{run['record']}?tab=history", amara)  # its steps, in its history
    seen = _text(page)
    assert "input." not in seen and "==" not in seen and "'Review the result" in seen
    _, page = call(server, "GET", "/track", rana)
    assert 'class="plain ev-lines"' in page and "<summary class=\"meta\">Details</summary>" in page
    lines = " ".join(re.findall(r'<ul class="plain ev-lines">(.*?)</ul>', page))
    assert not re.search(r"\b(run|item)-[0-9a-f]{6,}", _text(lines))


def test_start_work_has_no_implementation_language(hub, server):  # noqa: F811
    _, page = call(server, "GET", "/start", token(hub, "jo"))
    assert "Python" not in _text(page) and "block" not in _text(page).lower().split()


# ---- 4. jargon

def test_plain_words_for_who_may_do_a_step(hub):
    out = Standin({})("m", "", "", {}, {"step": {"id": "draft", "kind": "task"}})
    assert out["escalate"] and out["reason"] == "No agent can do this step; a person must"
    assert hub.subject_word() == hub.pack.subject["label"].lower()


def test_board_and_item_say_a_person_must(worked, server):  # noqa: F811
    hub = worked
    noor = token(hub, "noor")
    _, page = call(server, "GET", "/board", noor)
    assert "people only" not in page and "stand-in" not in page


# ---- 5. Done takes the work

def test_done_takes_the_item_on_the_record(hub, server):  # noqa: F811
    _install(hub, CALLBACK)
    jo = M(hub, "jo")
    run = hub.engine.start(jo, "callback", record=_pat(hub), inputs={"phone": "902 555 0101"})
    iid = item_for(hub, run, "call")
    _, page = call(server, "GET", f"/items/{iid}", token(hub, "jo"))
    assert "Assign to me" in page and "Finishing it assigns it to you anyway" in page
    hub.engine.complete(jo, iid, {"note": "left a message"})
    claimed = hub.track.list(run_id=run["id"], limit=50)
    assert any(ev["action"] == "item.claimed" and ev["actor"] == "jo" for ev in claimed)


# ---- 6 and 7. the board

def test_the_board_explains_its_marks(hub, server):  # noqa: F811
    _, page = call(server, "GET", "/board", token(hub, "jo"))
    assert "What the marks on a card mean" in page
    for said in ("Decision: a person decides and signs", "Approval: a person checks an agent", "Notice:",
                 "Nobody has taken it yet"):
        assert html.escape(said, quote=True) in page or said in page, said


def test_the_board_by_step(hub, server):  # noqa: F811
    pat = hub.records.search("Patient")[0]
    hub.engine.start(M(hub, "jo"), "appointment-request", record=f"Patient/{pat['id']}",
                     inputs={"reason": "check-up", "urgency": "routine"})
    wf = hub.workflow("appointment-request")[0]
    code, page = call(server, "GET", "/board?group=step&workflow=appointment-request", token(hub, "jo"))
    assert code == 200
    shown = [s.title for s in wf.steps if s.kind in ("task", "gate", "wait")]
    assert shown and all(f'aria-label="{html.escape(t)}"' in page for t in shown)
    assert '<option value="appointment-request" selected' in page


def test_what_a_move_ran_reads_plainly_in_history(hub, server):  # noqa: F811
    """The list under a move in History (``tl-ran``) speaks as the page does: no ids, no conditions."""
    from tests.test_clinic_records import LAB, _pat as _patient
    run = hub.engine.start(M(hub, "lab-feed"), "lab-result-review", record=_patient(hub),
                           inputs={**LAB, "value": "131", "flag": "abnormal"})
    hub.engine.complete(M(hub, "clinical-admin-agent"), item_for(hub, run, "sort"),
                        {"category": "abnormal", "rationale": "low"})
    amara = M(hub, "dr-amara")
    iid = item_for(hub, run, "review")
    hub.engine.decide(amara, iid, "call-patient", "", seen=hub.engine.shown_digest(amara, iid))
    ref = hub.engine.run(run["id"])["record"]
    _, page = call(server, "GET", f"/records/{ref}?tab=history", token(hub, "dr-amara"))
    nested = re.findall(r'<details class="tl-ran">.*?</details>', page, flags=re.S)
    assert nested
    seen = _text(" ".join(nested))
    assert "automations ran" in seen and "Ask a nurse to call the patient" in seen
    assert not re.search(r"\b(run|item)-[0-9a-f]{6,}", seen)
    assert "input." not in seen and "==" not in seen and " run " not in f" {seen.lower()} "


def test_the_block_of_what_a_move_set_going_reads_plainly(hub, server):  # noqa: F811
    """The work section's block (``rw-activity``) speaks as the page does: the edge's and the automations' own
    words and the outcome in words; no ids, no conditions, no "run"."""
    from tests.test_clinic_records import LAB, _pat as _patient
    run = hub.engine.start(M(hub, "lab-feed"), "lab-result-review", record=_patient(hub),
                           inputs={**LAB, "value": "131", "flag": "abnormal"})
    hub.engine.complete(M(hub, "clinical-admin-agent"), item_for(hub, run, "sort"),
                        {"category": "abnormal", "rationale": "low"})
    amara = M(hub, "dr-amara")
    iid = item_for(hub, run, "review")
    hub.engine.decide(amara, iid, "call-patient", "", seen=hub.engine.shown_digest(amara, iid))
    ref = hub.engine.run(run["id"])["record"]
    for who in ("dr-amara", "noor"):
        _, page = call(server, "GET", f"/records/{ref}", token(hub, who))
        block = page.split('<section id="activity"', 1)[1].split("</section>", 1)[0]
        seen = _text("<div " + block + "</div>")
        assert "After “Call the patient”" in seen and "Ask a nurse to call the patient" in seen and "done" in seen
        assert not re.search(r"\b(run|item)-[0-9a-f]{6,}", seen)
        assert "input." not in seen and "==" not in seen and " run " not in f" {seen.lower()} "
        assert "automation ran" in seen or "automations ran" in seen
