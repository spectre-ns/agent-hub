"""The record's own page is where its work is done, as an issue is in a tracker: its state beside the title, the
moves the member may make (a move that asks for something on its transition screen), the step it is at, cancelling
it, and its steps told in its history. A run's page is only for work about no record; every link to work about a
record opens the record."""

import html
import re
import urllib.parse

from conftest import M, item_for, visit_run
from test_rules import _jobs
from test_web import _ellen, call, follow, passphrase, server, token  # noqa: F401 - server is a fixture


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
    assert f'"href": "/records/{run["record"]}#work"' in api.replace('":"', '": "')


def test_the_history_tells_the_steps_and_folds_what_the_member_may_not_read(hub, server):
    run = visit_run(hub)
    url = f"/records/{run['record']}?tab=history"
    page = call(server, "GET", url, token(hub, "dr-amara"))[1]
    timeline = html.unescape(page.split('<ol class="timeline">', 1)[1].split("</ol>", 1)[0])
    assert "'End the visit'" in timeline or "End the visit" in timeline
    assert "started" in timeline and "done" in timeline
    jo = call(server, "GET", url, token(hub, "jo"))[1]
    shown = jo.split('<ol class="timeline">', 1)[1].split("</ol>", 1)[0]
    assert "not shown to you" in shown and "Draft the note" not in shown  # a clinical step, folded to one line


def test_work_is_cancelled_from_the_record_and_a_refusal_says_why_there(hub, server):
    run = hub.engine.start(M(hub, "jo"), "appointment-request", record=_ellen(hub),
                           inputs={"reason": "a form for my employer", "kind": "forms", "urgency": "routine"})
    run = hub.engine.run(run["id"])
    back = f"/records/{run['record']}#work"
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
    back = f"/records/{run['record']}#work"
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


def test_a_patients_results_and_prescriptions_are_sections_of_their_chart(hub, server):
    pid = _ellen(hub).split("/")[1]
    page = call(server, "GET", f"/patients/{pid}", token(hub, "dr-amara"))[1]
    assert '<h2 id="link-Observation-h">Results' in page and '<h2 id="link-MedicationRequest-h">Prescriptions' in page
    own = page.split('class="subnav owner-rail"', 1)[1].split("</nav>", 1)[0].split('<div class="sec">Linked here', 1)[0]
    assert 'href="#link-Observation"' in own and 'href="#link-MedicationRequest"' in own
