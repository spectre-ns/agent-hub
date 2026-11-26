"""The record's own page is where its work is done, as an issue is in a tracker: its state beside the title, the
moves the member may make (a move that asks for something on its transition screen), the step it is at, cancelling
it, and its steps told in its history. A run's page is only for work about no record; every link to work about a
record opens the record."""

import html
import json
import re
import urllib.parse

from conftest import M, follow_up_run, item_for, visit_run
from test_rules import _jobs
from test_web import _ellen, addr, call, follow, passphrase, server, token  # noqa: F401 - server is a fixture


def _job(hub, title="Fix the autoclave"):
    _jobs(hub, walked=False)
    rec = hub.create_record(M(hub, "dr-amara"), "Job", {"title": title})
    return f"Job/{rec['id']}"


def test_a_move_that_asks_opens_its_screen_with_the_fields_the_required_marked_and_what_was_typed_kept(hub, server):
    ref = _job(hub)
    amara = token(hub, "dr-amara")
    url = f"/records/{ref}"
    code, _ = call(server, "POST", f"{url}/move", amara, form={"to": "doing"})  # asks nothing: made at once
    assert code == 303 and hub.records.read(*ref.split("/"))["status"] == "doing"
    page = call(server, "GET", url, amara)[1]
    status = page.split('class="rec-status"', 1)[1].split("</div>", 1)[0]
    assert "Doing" in status  # its state, beside the title
    moves = page.split('class="rw-moves"', 1)[1].split("</div>", 1)[0]
    assert f'href="{url}/move?to=done" data-modal>Finish…</a>' in moves  # it asks: its screen first
    code, screen = call(server, "GET", f"{url}/move?to=done", amara)
    assert code == 200 and 'name="outcome"' in screen and 'name="hours"' in screen
    outcome = screen.split('name="outcome"', 1)[0].rsplit("<label", 1)[1]
    assert "Outcome" in outcome and ("required" in screen.split('name="outcome"', 1)[1].split(">", 1)[0]
                                     or "*" in outcome)
    # refused (the required one left empty): the same screen, saying why, with what was typed kept, nothing moved
    code, again = call(server, "POST", f"{url}/move", amara, form={"to": "done", "hours": "3", "_screen": "1"})
    assert code == 422 and 'role="alert"' in again and "Outcome" in again and 'value="3"' in again
    assert hub.records.read(*ref.split("/"))["status"] == "doing"
    code, _ = call(server, "POST", f"{url}/move", amara, form={"to": "done", "outcome": "New seal", "hours": "3",
                                                                "_screen": "1"})
    rec = hub.records.read(*ref.split("/"))
    assert code == 303 and rec["status"] == "done" and rec["outcome"] == "New seal"


def test_a_signed_move_says_what_is_signed_and_takes_the_signers_confirmation(hub, server):
    ref = _job(hub)
    amara = M(hub, "dr-amara")
    hub.move_record(amara, ref, "doing")
    hub.move_record(amara, ref, "done", {"outcome": "Done"})
    tok, words = token(hub, "dr-amara"), passphrase(hub, "dr-amara")
    url = f"/records/{ref}"
    screen = call(server, "GET", f"{url}/move?to=approved", tok)[1]
    assert "“Approved”" in screen and 'name="_sign_ok"' in screen and 'name="_seen"' in screen
    seen = re.search(r'name="_seen" value="([^"]+)"', screen).group(1)
    code, page = call(server, "POST", f"{url}/move", tok, form={"to": "approved", "_screen": "1", "_seen": seen,
                                                                 "_comment": "Checked the log"})
    assert code == 422 and "tick that you have read it" in page and "Checked the log" in page  # the comment kept
    code, page = call(server, "POST", f"{url}/move", tok, form={"to": "approved", "_screen": "1", "_seen": seen,
                                                                 "_sign_ok": "1", "passphrase": "wrong words",
                                                                 "_comment": "Checked the log"})
    assert code in (403, 422) and 'role="alert"' in page and "Checked the log" in page
    assert hub.records.read(*ref.split("/"))["status"] == "done"
    code, _ = call(server, "POST", f"{url}/move", tok, form={"to": "approved", "_screen": "1", "_seen": seen,
                                                              "_sign_ok": "1", "passphrase": words})
    assert code == 303 and hub.records.read(*ref.split("/"))["status"] == "approved"
    assert [s["meaning"] for s in hub.signatures(amara, ref)] == ["Approved"]
    hist = call(server, "GET", f"{url}?tab=history", tok)[1]
    assert "signed 'Approved' by Dr. Amara Okafor" in html.unescape(hist)


def test_a_run_about_a_record_opens_the_record_and_one_about_none_keeps_its_page(hub, server):
    run = visit_run(hub)
    amara = token(hub, "dr-amara")
    code, _ = call(server, "GET", f"/runs/{run['id']}", amara)
    assert code == 303
    code, page = follow(server, f"/runs/{run['id']}", amara)
    assert code == 200 and 'id="work"' in page
    from practicehub.web.server import App
    app = App(hub, dev=False)
    assert app.run_href(run) == f"{app.record_href(run['record'])}#work"
    assert app.run_href({"id": "run-x"}) == "/runs/run-x"
    hub.db.runs.update({"_id": run["id"]}, {"$set": {"record": None}})  # work about no record
    code, page = call(server, "GET", f"/runs/{run['id']}", amara)
    assert code == 200 and "run-head" in page


def test_no_page_links_work_about_a_record_to_the_run(hub, server):
    run = visit_run(hub)
    pid = run["subject"].split("/")[1]
    for who in ("dr-amara", "jo", "rana"):
        tok = token(hub, who)
        for path in ("/", "/board", f"/patients/{pid}", f"/records/{run['record']}", "/schedule",
                     "/search?q=" + urllib.parse.quote("Priya")):
            code, page = call(server, "GET", path, tok)
            if code != 200:
                continue
            assert not re.search(r'href="/runs/run-', page), (who, path)
    api = call(server, "GET", f"/api/runs/{run['id']}", token(hub, "dr-amara"))[1]
    assert f'"href": "{addr(hub, run["record"])}#work"' in api.replace('":"', '": "')


def test_the_history_tells_the_steps_and_folds_what_the_member_may_not_read(hub, server):
    run = visit_run(hub)
    url = f"/records/{run['record']}?tab=history"
    page = call(server, "GET", url, token(hub, "dr-amara"))[1]
    journey = html.unescape(page.split('<section id="journey"', 1)[1].split("</section>", 1)[0])
    assert 'class="jr-strip"' in journey and journey.count('<li class="jr-stage') >= 3  # nested by stage
    assert "<b>End the visit</b>" in journey and "done" in journey
    assert 'class="jr-move jr-edge jr-done"' in journey  # a move along its workflow's edge, with what it ran
    jo = call(server, "GET", url, token(hub, "jo"))[1]
    shown = jo.split('<section id="journey"', 1)[1].split("</section>", 1)[0]
    assert "not shown to you" in shown and "Draft the note" not in shown  # a clinical step, folded to one line
    assert shown.count('<li class="jr-stage') == journey.count('<li class="jr-stage')  # where it went is its own


def test_work_is_cancelled_from_the_record_and_a_refusal_says_why_there(hub, server):
    run = hub.engine.start(M(hub, "jo"), "appointment-request", record=_ellen(hub),
                           inputs={"reason": "a form for my employer", "kind": "forms", "urgency": "routine"})
    run = hub.engine.run(run["id"])
    back = addr(hub, run['record']) + "#work"
    page = call(server, "GET", back.split("#")[0], token(hub, "jo"))[1]
    cancel = page.split('class="cancel-work"', 1)[1].split("</details>", 1)[0]
    assert f'action="/runs/{run["id"]}/cancel"' in cancel and f'name="_back" value="{back}"' in cancel
    refuser = next((m for m in ("sam", "noor", "dr-liam")
                    if hub.engine.cancel_refusal(M(hub, m), run)), None)
    assert refuser
    if refuser:  # someone the work is not theirs to cancel: the record's page again, saying why
        code, page = call(server, "POST", f"/runs/{run['id']}/cancel", token(hub, refuser),
                          form={"reason": "not needed", "_back": back})
        assert code == 403 and "you may not cancel it" in page and 'id="work"' in page
        assert hub.engine.run(run["id"])["status"] == "running"
    code, _ = call(server, "POST", f"/runs/{run['id']}/cancel", token(hub, "jo"),
                   form={"reason": "booked by phone", "_back": back})
    assert code == 303 and hub.engine.run(run["id"])["status"] == "cancelled"


def test_a_step_is_done_on_the_record_and_its_verbs_return_there(hub, server):
    run = hub.engine.start(M(hub, "jo"), "appointment-request", record=_ellen(hub),
                           inputs={"reason": "a form for my employer", "kind": "forms", "urgency": "routine"})
    run = hub.engine.run(run["id"])
    iid = item_for(hub, run, "propose")
    back = addr(hub, run['record']) + "#work"
    page = call(server, "GET", back.split("#")[0], token(hub, "jo"))[1]
    step = page.split('class="rw-step"', 1)[1]
    assert f'action="/items/{iid}/claim"' in step and f'name="_back" value="{back}"' in step
    code, _ = call(server, "POST", f"/items/{iid}/claim", token(hub, "jo"), form={"_back": back})
    assert code == 303 and hub.engine.item(iid)["status"] == "claimed"


def test_a_visit_is_under_its_patient_in_the_crumbs_of_its_page_and_its_move_screen(hub, server):
    run = visit_run(hub)
    pid = run["subject"].split("/")[1]
    amara = token(hub, "dr-amara")
    page = call(server, "GET", f"/records/{run['record']}", amara)[1]
    crumbs = page.split('class="crumbs"', 1)[1].split("</nav>", 1)[0] if 'class="crumbs"' in page else \
        page.split("aria-label=\"Breadcrumb\"", 1)[1].split("</nav>", 1)[0]
    assert f'href="/patients/{pid}"' in crumbs and "Records" not in crumbs
    screen = call(server, "GET", f"/records/{run['record']}/move?to=entered-in-error", amara)[1]
    crumbs = screen.split('class="crumbs"', 1)[1].split("</nav>", 1)[0] if 'class="crumbs"' in screen else \
        screen.split("aria-label=\"Breadcrumb\"", 1)[1].split("</nav>", 1)[0]
    assert f'href="/patients/{pid}"' in crumbs and f'href="/records/{run["record"]}"' in crumbs


def test_visits_are_listed_in_time_order_with_a_today_filter(hub, server):
    tok = token(hub, "jo")
    page = call(server, "GET", "/records/Encounter", tok)[1]
    assert 'href="/records/Encounter?when=today"' in page and "All, latest first" in page
    code, today = call(server, "GET", "/records/Encounter?when=today", tok)
    assert code == 200 and 'aria-current=page>Today' in today
    day = hub.engine.now().date()
    shown = re.findall(r"<time datetime=\"(\d{4}-\d\d-\d\d)", today.split("<table", 1)[-1])
    assert all(d == day.isoformat() for d in shown)


def test_a_patients_lab_results_and_prescriptions_are_sections_of_their_chart(hub, server):
    pid = _ellen(hub).split("/")[1]
    page = call(server, "GET", f"/patients/{pid}", token(hub, "dr-amara"))[1]
    # lab results and prescriptions are parts of the patient (Patient > Lab results > Lab result)
    for t in ("Observation", "MedicationRequest"):
        assert page.count(f'id="part-{t}"') == 1 and f'id="link-{t}"' not in page
    own = page.split('class="subnav owner-rail"', 1)[1].split("</nav>", 1)[0].split('<div class="sec">Linked here', 1)[0]
    assert 'href="#part-Observation"' in own and 'href="#part-MedicationRequest"' in own


def test_a_lab_result_is_under_its_patient_in_its_crumbs(hub, server):
    pid = _ellen(hub).split("/")[1]
    lab = next(o for o in hub.records.search("Observation", limit=None)
               if (o.get("subject") or {}).get("reference") == f"Patient/{pid}")
    page = call(server, "GET", f"/records/Observation/{lab['id']}", token(hub, "dr-amara"))[1]
    crumbs = page.split('class="crumbs"', 1)[1].split("</div>", 1)[0]
    ptitle = hub.short_title(hub.records.read("Patient", pid))
    assert crumbs.index(ptitle) < crumbs.index("Lab results") < crumbs.index(hub.short_title(lab)), crumbs


def test_on_the_record_a_decision_leads_its_step_before_the_request_it_is_about(worked, server):
    hub = worked
    run = next(r for r in hub.engine.runs(limit=1000) if r["workflow_id"] == "prescription-renewal"
               and r["steps"].get("decide", {}).get("state") == "active")
    page = call(server, "GET", f"/records/{run['record']}", token(hub, "dr-amara"))[1]
    step = page.split('class="rw-step"', 1)[1]
    assert "<h2>Decide</h2>" in step and "<h2>The request</h2>" in step
    assert step.index("<h2>Decide</h2>") < step.index("<h2>The request</h2>") < step.index("What this step reads")
    # the request's own status is where its renewal is, said in the workflow's words and tone
    assert hub.records.read(*run["record"].split("/", 1))["status"] == "deciding" and run.get("at") is None
    head = page.split('class="rw-step"', 1)[0]
    assert '<span class="loz moved">To decide</span>' in head and ">Draft</span>" not in head


def test_a_long_parts_section_shows_the_latest_fifty_and_lists_every_one(hub, server):
    pid = _ellen(hub).split("/")[1]
    ref = f"Patient/{pid}"
    amara = M(hub, "dr-amara")
    have = [o for o in hub.records.search("Observation", limit=None) if (o.get("subject") or {}).get("reference") == ref]
    tmpl = {k: v for k, v in have[0].items() if k not in ("id", "meta", "review")}
    for _ in range(51 - len(have)):
        hub.create_record(amara, "Observation", {**tmpl, "status": "final", "subject": {"reference": ref}})
    tok = token(hub, "dr-amara")
    page = call(server, "GET", f"/patients/{pid}", tok)[1]
    q = urllib.parse.quote(ref, safe="")
    section = page.split('id="part-Observation"', 1)[1].split("</section>", 1)[0]
    assert f'href="/records/Observation?linked={q}">Show all ' in section
    code, all_ = call(server, "GET", f"/records/Observation?linked={q}", tok)
    assert code == 200 and all_.count('<tr><td class="key">') >= 51  # a row each, its key first


def test_a_decision_on_the_record_is_a_button_per_outcome_each_opening_its_own_dialog(hub, server):
    """On the record's page each outcome of a decision is a button, like a move. Each opens a dialog for that outcome
    alone: the sentence it signs, what it sets going (booking a visit books one; filing it normal tells the patient),
    a comment and the signature. Deciding from it moves the work on and comes back to the record."""
    from tests.test_clinic_records import LAB, _pat
    run = hub.engine.start(M(hub, "lab-feed"), "lab-result-review", record=_pat(hub),
                           inputs={**LAB, "value": "131", "flag": "abnormal"})
    hub.engine.complete(M(hub, "clinical-admin-agent"), item_for(hub, run, "sort"),
                        {"category": "abnormal", "rationale": "low"})
    ref = hub.engine.run(run["id"])["record"]
    tok = token(hub, "dr-amara")
    page = call(server, "GET", f"/records/{ref}", tok)[1]
    step = page.split('class="rw-step"', 1)[1]
    assert 'name="outcome"' not in step  # no radios on the record: a button each
    iid = item_for(hub, run, "review")
    assert f'href="/items/{iid}/decide?outcome=file-normal' not in step  # an edge not offered: it is abnormal
    for o in ("no-action-needed", "call-patient", "book-visit"):
        assert f'href="/items/{iid}/decide?outcome={o}' in step
    code, book = call(server, "GET", f"/items/{iid}/decide?outcome=book-visit", tok)
    assert code == 200 and "data-modal-body" in book and "Book a visit" in book and "Tell the patient" not in book
    assert "I have reviewed this result" in book and 'name="outcome" value="book-visit"' in book
    code, fine = call(server, "GET", f"/items/{iid}/decide?outcome=no-action-needed", tok)
    assert "Tell the patient their result was reviewed" in fine and "Book a visit" not in fine
    assert call(server, "GET", f"/items/{iid}/decide?outcome=launch", tok)[0] == 422
    seen = re.search(r'name="seen" value="([0-9a-f]+)"', book).group(1)
    form = {"outcome": "book-visit", "comment": "talk it over", "seen": seen, "_dialog": "1",
            "_back": f"/records/{ref}#work"}
    code, again = call(server, "POST", f"/items/{iid}/decide", tok, form=form)  # signing asks for the passphrase
    assert code == 403 and "data-modal-body" in again and ">talk it over</textarea>" in again
    assert 'name="outcome" value="book-visit"' in again
    words = passphrase(hub, "dr-amara")
    code, _ = call(server, "POST", f"/items/{iid}/decide", tok, form={**form, "passphrase": words})
    assert code in (200, 303) and hub.engine.item(iid)["status"] == "done"


def test_a_call_to_the_patient_ends_with_a_button_per_outcome_each_opening_its_dialog(hub, server):
    """A task leads its step as a decision does: on the record's page, a button for each way it can end (a call:
    the patient reached, or not), each opening a dialog with the step's fields and what that ending sets going; no
    answer form trails under what the step reads. Not reached goes to a physician; refused, the dialog comes back
    with what was typed."""
    from tests.test_clinic_records import LAB, _pat
    run = hub.engine.start(M(hub, "lab-feed"), "lab-result-review", record=_pat(hub),
                           inputs={**LAB, "value": "131", "flag": "abnormal"})
    hub.engine.complete(M(hub, "clinical-admin-agent"), item_for(hub, run, "sort"),
                        {"category": "abnormal", "rationale": "low"})
    amara = M(hub, "dr-amara")
    flagged = item_for(hub, run, "review")
    hub.engine.decide(amara, flagged, "call-patient", "", seen=hub.engine.shown_digest(amara, flagged))
    run = follow_up_run(hub, run)  # the call is the follow-up's: a record of its own
    ref = run["record"]
    iid = item_for(hub, run, "call")
    tok = token(hub, "noor")
    page = call(server, "GET", f"/records/{ref}", tok)[1]
    step = page.split('class="rw-step"', 1)[1]
    assert "Your answer" not in page and 'name="notes"' not in step  # the fields are in the dialogs
    buttons = step.split('class="rw-moves"', 1)[1].split("</div>", 1)[0]
    assert f'href="/items/{iid}/answer?outcome=patient-reached' in buttons and ">Patient reached…<" in buttons
    assert f'href="/items/{iid}/answer?outcome=could-not-reach' in buttons and ">Could not reach…<" in buttons
    assert step.index('class="rw-moves"') < step.index("What this step reads")  # the buttons lead the step
    code, dlg = call(server, "GET", f"/items/{iid}/answer?outcome=could-not-reach", tok)
    assert code == 200 and "data-modal-body" in dlg and 'name="notes"' in dlg
    assert 'name="outcome" value="could-not-reach"' in dlg and "could not be reached" in dlg  # what follows
    assert call(server, "GET", f"/items/{iid}/answer?outcome=gone-fishing", tok)[0] == 422
    form = {"outcome": "could-not-reach", "_dialog": "1", "_back": f"/records/{ref}#work"}
    code, again = call(server, "POST", f"/items/{iid}/complete", tok, form=form)  # notes are required
    assert code == 422 and "data-modal-body" in again and 'name="outcome" value="could-not-reach"' in again
    # who does the work it sets going is chosen in the dialog, one person each (here: the physician's follow-up)
    needs = hub.engine.assignments_needed(hub.engine.run(run["id"]), hub.engine.step_of(hub.engine.item(iid))[2],
                                          "could-not-reach")
    assert needs and all(f'name="assign:{n["key"]}"' in dlg for n in needs)
    form = {**form, **{f"assign:{n['key']}": n["people"][0]["id"] for n in needs}}
    code, _ = call(server, "POST", f"/items/{iid}/complete", tok, form={**form, "notes": "no answer, voicemail"})
    assert code == 303 and hub.engine.item(iid)["output"]["outcome"] == "could-not-reach"
    assert hub.engine.run(run["id"])["steps"]["not-reached"]["state"] == "active"


def test_a_part_is_addressed_under_its_owner_layer_by_layer(hub, server):
    """A part's address is its owner's, then its kind (the pack's plural, as a slug) and its id, to any depth: a
    result is /patients/<id>/lab-results/<id>, a visit's note /patients/<id>/visits/<id>/notes/<id>. Its own
    address goes there; a part named under a record that does not own it is not found."""
    from tests.test_clinic_records import LAB, _pat
    run = hub.engine.start(M(hub, "lab-feed"), "lab-result-review", record=_pat(hub),
                           inputs={**LAB, "value": "131", "flag": "abnormal"})
    ref = hub.engine.run(run["id"])["record"]
    pid = hub.owner_of(hub.records.read(*ref.split("/", 1))).split("/", 1)[1]
    oid = ref.split("/", 1)[1]
    tok = token(hub, "dr-amara")
    nested = f"/patients/{pid}/lab-results/{oid}"
    import urllib.error
    import urllib.request
    from test_web import opener
    try:
        opener.open(urllib.request.Request(server + f"/records/{ref}", headers={"Authorization": f"Bearer {tok}"}))
        raise AssertionError("its own address is not where it is shown")
    except urllib.error.HTTPError as exc:
        assert exc.code == 303 and exc.headers["Location"] == nested
    code, page = call(server, "GET", nested, tok)
    assert code == 200
    crumbs = page.split('class="crumbs"', 1)[1].split("</nav>", 1)[0]
    assert f'href="/patients/{pid}"' in crumbs and "Lab results" in crumbs and "Records" not in crumbs
    # it is read inside its patient: the patient's sections beside it, its own kind marked; its actions nested too
    rail = page.split('class="subnav owner-rail"', 1)[1].split("</nav>", 1)[0]
    assert f'href="/patients/{pid}#part-Encounter"' in rail
    assert f'href="/patients/{pid}#part-Observation" aria-current="page"' in rail
    assert f'href="{nested}/export"' in page and f'/records/{ref}/export' not in page
    assert call(server, "GET", f"{nested}/export", tok)[0] == 200
    other = next(p["id"] for p in hub.records.search("Patient", limit=None) if p["id"] != pid)
    assert call(server, "GET", f"/patients/{other}/lab-results/{oid}", tok)[0] == 404  # not theirs
    assert call(server, "GET", f"/patients/{pid}/visits/{oid}", tok)[0] == 404


def test_a_referral_is_linked_by_hand_on_its_page_to_its_patients_records_and_unlinked_there(hub, server):
    """A type's ``[[link]]`` is a section of its page: Link opens a dialog listing what it may link to (the same
    patient's records), and each linked record has Unlink. The API does the same."""
    amara = token(hub, "dr-amara")
    pat = "Patient/" + next(iter(hub.records.search("Patient", limit=None)))["id"]
    ref = "ServiceRequest/" + hub.create_record(M(hub, "dr-amara"), "ServiceRequest", {
        "status": "draft", "intent": "order", "subject": {"reference": pat}, "code": {"text": "Cardiology"}},
        start_workflow=False)["id"]
    rep = "DiagnosticReport/" + hub.create_record(M(hub, "dr-amara"), "DiagnosticReport", {
        "status": "final", "code": {"text": "Echo"}, "subject": {"reference": pat}})["id"]
    url = addr(hub, ref)
    page = call(server, "GET", url, amara)[1]
    links = page.split('id="links"', 1)[1].split("</section>", 1)[0]
    assert "Specialist&#x27;s report" in links and "Sent with the letter" in links and "None linked yet" in links
    assert f'{url}/link?rel=report' in links
    code, dialog = call(server, "GET", f"{url}/link?rel=report", amara)
    assert code == 200 and f'value="{rep}"' in dialog                # the patient's report is offered
    other = [r for r in hub.records.search("DiagnosticReport", limit=None)
             if (r.get("subject") or {}).get("reference") != pat]
    assert all(f'value="DiagnosticReport/{r["id"]}"' not in dialog for r in other)  # another patient's is not
    code, page = call(server, "POST", f"{url}/link", amara, form={"rel": "nope", "to": rep})
    assert code == 422
    code, _ = call(server, "POST", f"{url}/link", amara, form={"rel": "report", "to": rep})
    assert code == 303 and hub.hand_links(M(hub, "dr-amara"), ref) == {"report": [rep]}
    links = call(server, "GET", url, amara)[1].split('id="links"', 1)[1].split("</section>", 1)[0]
    assert "Echo" in links and f'{url}/link?rel=report' not in links  # a single link, made: no second
    code, _ = call(server, "POST", f"{url}/unlink", amara, form={"rel": "report", "to": rep})
    assert code == 303 and hub.hand_links(M(hub, "dr-amara"), ref) == {}
    t, _, rid = ref.partition("/")
    assert call(server, "POST", f"/api/records/{t}/{rid}/link", amara, body={"rel": "report", "to": rep})[0] == 200
    got = json.loads(call(server, "GET", f"/api/records/{t}/{rid}/links", amara)[1])
    assert {x["rel"]: x["linked"] for x in got} == {"sent-with": [], "report": [rep]}


def test_the_record_structure_is_drawn_and_each_change_is_a_proposal_another_policy_owner_approves(hub, server):
    """/define/structure draws every type under the one it is part of, with its hand links as arcs; moving a type
    under another, adding a link and removing one each propose a version of that type, in force once approved."""
    amara, rana = token(hub, "dr-amara"), token(hub, "rana")
    code, page = call(server, "GET", "/define/structure", rana)
    assert code == 200 and 'class="st-svg"' in page
    assert 'data-type="ServiceRequest"' in page and 'data-from="ServiceRequest" data-to="DiagnosticReport"' in page
    panel = call(server, "GET", "/define/structure?type=ServiceRequest", rana)[1].split('id="st-panel"', 1)[1]
    assert "Specialist&#x27;s report" in panel and 'value="drop-link"' in panel
    # a type is never made part of its own part
    code, page = call(server, "POST", "/define/structure", rana, form={"type": "Patient", "op": "owner",
                                                                        "owner": "Encounter"})
    assert code == 422 and "its own part" in page
    code, _ = call(server, "POST", "/define/structure", rana, form={
        "type": "ServiceRequest", "op": "add-link", "label": "Follow-up visit", "to": "Encounter", "help": "x"})
    assert code == 303
    ver = hub.definition_versions("type", "ServiceRequest")[0]
    assert ver["status"] == "proposed" and "[[link]]" in ver["source"] and 'rel = "follow-up-visit"' in ver["source"]
    assert "# A referral to a specialist" in ver["source"]  # an added link keeps the text's comments
    assert hub.record_type("ServiceRequest").link("follow-up-visit") is None  # not before it is approved
    hub.approve(M(hub, "dr-amara"), "type", "ServiceRequest", ver["version"])
    x = hub.record_type("ServiceRequest").link("follow-up-visit")
    assert x["to"] == ["Encounter"] and x["many"] is False
    code, _ = call(server, "POST", "/define/structure", amara, form={
        "type": "ServiceRequest", "op": "drop-link", "rel": "follow-up-visit", "note": "not needed"})
    assert code == 303
    ver = hub.definition_versions("type", "ServiceRequest")[0]
    assert ver["status"] == "proposed" and "follow-up-visit" not in ver["source"]


def test_moving_a_type_under_another_proposes_a_required_reference_to_its_new_owner(hub, server):
    import tomllib
    rana = token(hub, "rana")
    code, page = call(server, "POST", "/define/structure", rana,
                      form={"type": "AllergyIntolerance", "op": "owner", "owner": "Encounter"})
    assert code == 303, page
    doc = tomllib.loads(hub.definition_versions("type", "AllergyIntolerance")[0]["source"])
    f = next(x for x in doc["field"] if x["name"] == doc["part_of"])
    assert f["type"] == "reference" and f["required"] and "Encounter" in str(f["target"])
    code, page = call(server, "POST", "/define/structure", rana,
                      form={"type": "Immunization", "op": "owner", "owner": ""})
    assert code == 303, page
    assert "part_of" not in tomllib.loads(hub.definition_versions("type", "Immunization")[0]["source"])


def _called_back(hub):
    """A lab result a physician sent on by "Call the patient": (its ref, the run that walked it)."""
    from tests.test_clinic_records import LAB, _pat
    run = hub.engine.start(M(hub, "lab-feed"), "lab-result-review", record=_pat(hub),
                           inputs={**LAB, "value": "131", "flag": "abnormal"})
    hub.engine.complete(M(hub, "clinical-admin-agent"), item_for(hub, run, "sort"),
                        {"category": "abnormal", "rationale": "low"})
    amara = M(hub, "dr-amara")
    iid = item_for(hub, run, "review")
    hub.engine.decide(amara, iid, "call-patient", "", seen=hub.engine.shown_digest(amara, iid))
    return hub.engine.run(run["id"])["record"], run


def test_the_history_tells_a_move_once_with_what_it_ran_and_nothing_of_the_edges_not_taken(hub, server):
    """History tells a move once, with what the move ran and started nested under it. The automations of edges the
    record did not take are not lines ("skipped"); an automated state's own work keeps its line. The page nests
    the same history by stage (``Hub.record_journey``): the move out of a stage, then its automations."""
    ref, _ = _called_back(hub)
    rows = hub.record_timeline(M(hub, "dr-amara"), ref)
    (moved,) = [r for r in rows if r["text"].startswith("Moved from in review to closed")]
    titles = [a["title"] for a in moved["ran"] if not a.get("hidden")]
    assert "Ask a nurse to call the patient" in titles and "Note who reviewed the result, and when" in titles
    assert "Ask a nurse to call the patient now (critical result)" not in titles  # its own when did not hold
    assert all(a["state"] == "done" for a in moved["ran"])
    assert sum(len(a.get("started") or []) for a in moved["ran"]) == 1  # the follow-up Task
    said = " | ".join(r["text"] for r in rows)
    assert "'Check the value against its range' done" in said  # the checking state's own automation
    for gone in ("Tell the patient it is normal", "Tell the patient their result was reviewed", "Ask for a visit",
                 "Move to Closed", "Page the physicians"):
        assert gone not in said, gone  # edges not taken, and the move itself, are no lines of their own
    assert sum(1 for r in rows if r.get("ran")) == 1  # the other moves ran nothing
    page = call(server, "GET", f"/records/{ref}?tab=history", token(hub, "dr-amara"))[1]
    journey = html.unescape(page.split('<section id="journey"', 1)[1].split("</section>", 1)[0])
    # the move out of "In review": the edge the physician chose, where it went, and what it ran under it
    review = journey.split('<li class="jr-stage', )[-2]  # the stage before the one it is in now
    assert ">In review<" in review.split("</summary>", 1)[0]
    move = review.split('<div class="jr-move jr-edge jr-done">', 1)[1]
    assert "<b>Call the patient</b>" in move.split("</div>", 1)[0] and ">Closed<" in move.split("</div>", 1)[0]
    listed = move.split('<ul class="plain ra-list">', 1)[1].split("</details>", 1)[0]
    assert "Ask a nurse to call the patient" in listed and "Note who reviewed the result, and when" in listed
    assert 'class="plain ra-started"' in listed  # the follow-up it started, as it is now
    assert "other ways out not taken" in move and "not needed" in move
    for gone in ("Tell the patient it is normal", "Ask for a visit", "Page the physicians"):
        assert gone not in journey, gone  # automations of the edges not taken are told nowhere
    assert "skipped" not in journey and not re.search(r'href="/runs/run-', journey)


def test_a_move_summary_counts_what_ran_what_did_not_go_through_and_what_it_started():
    from practicehub.web.records_pages import ran_summary
    ran = [{"state": "done", "started": [{"ref": "Task/1"}]}, {"state": "done"}, {"state": "failed"},
           {"state": "held"}, {"state": "done", "hidden": True, "started": [{"ref": "Task/2"}]}]
    assert ran_summary(ran) == ("3 automations ran, 1 automation did not go through, 1 automation waiting, "
                                "1 record started")  # a hidden one counts, but names no record it started
    assert ran_summary([{"state": "done"}]) == "1 automation ran"


def _block(page: str) -> str:
    """The work section's block for the latest move (``rw-activity``), as the page has it."""
    return page.split('<section id="activity"', 1)[1].split("</section>", 1)[0]


def test_a_closed_result_shows_what_its_last_move_set_going_and_keeps_it_live_while_the_follow_up_is_open(
        hub, server):
    """The block in the work section: after "Call the patient", every automation on that edge with its outcome and
    the follow-up Task as it is now (its link, its state, who holds it), and nothing of the edges not taken. The
    result is closed, yet the section is there while the Task is open, live (``data-live``) with a Refresh link;
    once the Task is finished it is settled: no ``data-live``, no Refresh."""
    from test_web import addr
    ref, run = _called_back(hub)
    fu = follow_up_run(hub, run)
    tok = token(hub, "dr-amara")
    page = call(server, "GET", f"/records/{ref}", tok)[1]
    assert page.index('id="work"') < page.index('<section id="activity" class="rw-activity"')
    block = _block(page)
    said = html.unescape(block)
    assert "After “Call the patient”" in said
    for line in ("Note who reviewed the result, and when", "File the review on the result",
                 "Ask a nurse to call the patient"):
        assert f"<b>{line}</b>: " in said, line
    assert block.count('<span class="ra-word">done</span>') == 3
    assert "did not go through" not in said and "waiting on" not in said
    for gone in ("now (critical result)", "Tell the patient", "Ask for a visit", "Page the physicians"):
        assert gone not in said, gone  # skipped by its own when, or on an edge not taken
    assert f'href="{addr(hub, fu["record"])}"' in block  # the Task, linked to its own page
    assert "1 record started" in said
    assert 'data-live="/records/' + ref + '/activity"' in block and ">Refresh</a>" in block
    assert "<details class=\"ra-fold\" open>" in block  # still going on: open
    assert not re.search(r'href="/runs/', block)
    hub.db.runs.update({"_id": fu["id"]}, {"$set": {"status": "completed"}})  # the call made and the Task done
    block = _block(call(server, "GET", f"/records/{ref}", tok)[1])
    assert "data-live" not in block and "Refresh" not in block
    assert ">Done<" in block and "Ask a nurse to call the patient" in html.unescape(block)


def test_a_record_started_by_a_move_names_that_move_and_is_listed_on_the_record_that_made_it(hub, server):
    """``workflow.start`` spawns are family: the lab result lists the follow-up Task under "Started from this…"
    (beside ``record.child`` sub-records), and the Task says "Started by “Call the patient” on <the result>", the
    result linked. Neither page links work about a record to a run's own page."""
    from test_web import addr
    ref, run = _called_back(hub)
    fu = follow_up_run(hub, run)
    tok = token(hub, "noor")
    lab = call(server, "GET", f"/records/{ref}", tok)[1]
    kids = lab.split('class="children started-from"', 1)[1].split("</section>", 1)[0]
    assert f'href="{addr(hub, fu["record"])}"' in kids
    code, task = call(server, "GET", f"/records/{fu['record']}", tok)
    assert code == 200
    by = html.unescape(task.split('class="wp-parent wp-started-by"', 1)[1].split("</p>", 1)[0])
    assert "Started by" in by and "“Call the patient”" in by and f'href="{addr(hub, ref)}"' in by
    for page in (lab, task, call(server, "GET", f"/records/{ref}/activity", tok)[1]):
        assert not re.search(r'href="/runs/run-', page)


def test_a_move_that_ran_nothing_shows_no_block(hub, server):
    """A result still in its sort has moved only by the lab's filing, which ran nothing on its edges: no block, and
    the work section is as it was."""
    from tests.test_clinic_records import LAB, _pat
    run = hub.engine.start(M(hub, "lab-feed"), "lab-result-review", record=_pat(hub),
                           inputs={**LAB, "value": "131", "flag": "abnormal"})
    ref = hub.engine.run(run["id"])["record"]
    page = call(server, "GET", f"/records/{ref}", token(hub, "dr-amara"))[1]
    assert 'class="rw-activity"' not in page


def test_open_work_s_own_address_goes_to_its_record_s_page_and_work_that_is_over_keeps_its_own(hub, server):
    from test_web import opener
    import urllib.error
    import urllib.request

    def get(path, who):
        req = urllib.request.Request(server + path, headers={"Authorization": f"Bearer {token(hub, who)}"})
        try:
            with opener.open(req) as r:
                return r.status, ""
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Location", "")

    run = hub.engine.start(M(hub, "jo"), "appointment-request", record=_ellen(hub),
                           inputs={"reason": "a form for my employer", "kind": "forms", "urgency": "routine"})
    run = hub.engine.run(run["id"])
    iid = item_for(hub, run, "propose")
    # the step, who has it and what it asks are on the record's page: one place for the work, not two
    assert get(f"/items/{iid}", "jo") == (303, addr(hub, run["record"]) + "#work")
    assert get(f"/items/{iid}/answer", "jo")[0] == 200  # what the step asks for still opens by the item
    hub.engine.cancel(M(hub, "jo"), run["id"], "asked for by mistake")
    assert get(f"/items/{iid}", "jo")[0] == 200  # over: what was done with it is told on its own page
