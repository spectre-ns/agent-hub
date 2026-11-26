"""The booking portal for the people a practice serves (``practicehub/portal.py``, the hub's side;
``practicehub/web/portal_api.py``, what the portal's process may ask; ``practicehub/web/portal_app.py``, the pages):
its ``[portal]`` checked at pack load, sign-in by details on file and a one-time code, caps on sends, slots, booking
through workflows, the person's own view, and that the core names nothing of a pack's."""

import copy
import dataclasses
import html
import json
import re
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import M, item_for
from practicehub import portal as portal_mod
from practicehub.hub import Denied, Invalid
from practicehub.portal import Clash, Portal, check_config

ROOT = Path(__file__).resolve().parent.parent
CARD = "1234 567 890"
BORN = "1990-02-03"


def person(hub, card=CARD, born=BORN, phone="902-555-0100"):
    body = {"name": [{"given": ["Ada"], "family": "Lane"}], "birthDate": born,
            "identifier": [{"system": "https://novascotia.ca/health-card", "value": card}]}
    if phone:
        body["telecom"] = [{"system": "phone", "value": phone}]
    rec = hub.create_record(M(hub, "jo"), "Patient", body)
    return f"Patient/{rec['id']}"


def code_rows(hub):
    return list(hub.db.outbox.find({"purpose": "sign-in"}))


def signed_in(hub, p=None, client="10.0.0.9"):
    """A person on file, signed in: (portal, record, session token)."""
    p = p or Portal(hub, dev=True)
    ref = person(hub)
    got = p.begin_sign_in(BORN, CARD, client=client)
    return p, ref, p.finish_sign_in(got["challenge"], got["dev_code"], client=client)


def later_slot(p, kid="forms", hours=30):
    return next(s for s in p.free_slots(kid, limit=10000)
                if datetime.fromisoformat(s["value"].split("|")[0]) - p.hub.engine.now() > timedelta(hours=hours))


def portal_token(hub):
    return hub.issue_token(M(hub, "jo"), "portal", "portal process", scope="portal")


def portal_app(hub, dev=True):
    """The portal's pages, a client of this hub's API with the portal's own token (as its process is)."""
    from practicehub.web.portal_app import LocalClient, PortalApp
    from practicehub.web.server import App
    return PortalApp(LocalClient(App(hub, dev=dev), portal_token(hub)), dev=dev)


def api(hub, op, data=None, token=None, dev=True):
    from practicehub.web.server import App
    status, _, body = App(hub, dev=dev).handle("POST", f"/api/portal/{op}", {
        "authorization": f"Bearer {token or portal_token(hub)}"}, json.dumps(data or {}).encode())
    return status, json.loads(body)


# ------------------------------------------------------------------------------------------- [portal] is checked
def _pack(hub):
    return dataclasses.replace(hub.pack, portal=copy.deepcopy(hub.pack.portal))


def test_the_clinic_portal_is_valid(hub):
    assert check_config(hub.pack) == []
    p = Portal(hub)
    assert set(p.kinds()) == {"new-concern", "forms"}
    assert {k["workflow"] for k in p.kinds().values()} == {"appointment-request"}, \
        "every visit the portal asks for is the practice's own request workflow"
    assert p.closed() is None


@pytest.mark.parametrize("change,says", [
    (lambda c: c["kinds"]["forms"].update(roles=["astronaut"]), "astronaut"),
    (lambda c: c["kinds"]["forms"].update(workflow="no-such-flow"), "no-such-flow"),
    (lambda c: c["kinds"]["forms"].update(questions=["favourite_colour"]), "favourite_colour"),
    (lambda c: c["kinds"]["forms"].pop("booked"), "booked"),
    (lambda c: c["kinds"]["forms"].update(mode="maybe"), "mode"),
    (lambda c: c["kinds"]["forms"].update(minutes=7), "minutes"),
    (lambda c: c["kinds"]["new-concern"].update(alarm="screen.red_flag ==="), "alarm"),
    (lambda c: c.update(member="jo"), "service"),
    (lambda c: c.pop("privacy"), "privacy"),
    (lambda c: c.update(colour="teal"), "colour"),
    (lambda c: c["identity"].update(born="shoe_size"), "shoe_size"),
    (lambda c: c["identity"].update(inputmode="emoji"), "inputmode"),
    (lambda c: c["code"].update(minutes=600), "minutes"),
    (lambda c: c["kinds"]["forms"].update(cancel_to="no-such-state"), "cancel_to"),
    (lambda c: c["kinds"]["forms"].update(max_upcoming=0), "max_upcoming"),
    (lambda c: c.update(public_origin="portal.example.ca"), "public_origin"),
    (lambda c: c["register"].pop("pending"), "pending"),
    (lambda c: c["over_limit"].update(workflow="no-such-flow"), "no-such-flow"),
    (lambda c: c["over_limit"].update(input="nope"), "over_limit.input"),
    (lambda c: c["over_limit"].update(colour="red"), "over_limit.colour"),
])
def test_a_bad_portal_is_refused_with_a_clear_problem(hub, change, says):
    pack = _pack(hub)
    change(pack.portal)
    problems = check_config(pack)
    assert problems and any(says in p for p in problems), problems


@pytest.mark.parametrize("kind", ["new-concern", "forms"])
def test_a_question_in_a_persons_own_words_needs_a_screen(hub, kind):
    """Blocker 5: a free-text question goes to a workflow only through a step that screens it, the kind says when
    it is loud, and a kind booked at once books only on what the screen found."""
    pack = _pack(hub)
    pack.portal["kinds"][kind].pop("screen")
    assert any(f"kinds.{kind}.screen" in p for p in check_config(pack))
    pack = _pack(hub)
    pack.portal["kinds"][kind].pop("alarm", None)
    assert any(f"kinds.{kind}.alarm" in p for p in check_config(pack))


def test_a_bad_portal_stops_the_pack_loading(tmp_path):
    import shutil
    from practicehub.pack import load as load_pack
    dst = tmp_path / "general-practice"
    shutil.copytree(ROOT / "packs" / "general-practice", dst)
    toml = (dst / "pack.toml").read_text()
    (dst / "pack.toml").write_text(toml.replace('roles = ["physician"]', 'roles = ["astronaut"]', 1))
    with pytest.raises(ValueError, match=r"\[portal\].*astronaut"):
        load_pack(dst)


# ------------------------------------------------------------------------------------------- blocker 7: in force
def test_the_portal_is_closed_while_the_pack_has_a_problem(hub):
    hub.pack_problem = "the files differ from those put into force"
    assert "problem" in Portal(hub).closed()
    status, got = api(hub, "config")
    assert status == 503 and got["error"] == "closed" and "Call the practice" in got["says"]


def test_the_portal_is_closed_while_a_kinds_workflow_is_not_active(hub, monkeypatch):
    from practicehub.hub import NotFound
    real = hub.workflow

    def workflow(wid, *a, **kw):
        if wid == "appointment-request":
            raise NotFound("appointment-request is switched off")
        return real(wid, *a, **kw)
    monkeypatch.setattr(hub, "workflow", workflow)
    assert "not an active workflow" in Portal(hub).closed()
    assert api(hub, "config")[0] == 503


def test_the_portal_is_closed_when_the_workflow_in_force_no_longer_screens(hub, monkeypatch):
    wf, v = hub.workflow("appointment-request")
    steps = [dataclasses.replace(s, params={"question": "{{input.preferences}}"}) if s.id == "screen" else s
             for s in wf.steps]
    changed = dataclasses.replace(wf, steps=steps)
    real = hub.workflow
    monkeypatch.setattr(hub, "workflow", lambda wid, *a, **kw: (changed, v) if wid == "appointment-request"
                        else real(wid, *a, **kw))
    assert "does not read reason" in Portal(hub).closed()


# ------------------------------------------------------------------------------------------- identity
def test_right_details_and_the_code_sign_in(hub):
    p = Portal(hub, dev=True)
    ref = person(hub)
    got = p.begin_sign_in(BORN, " 1234-567-890 ", client="10.0.0.1")
    assert got["dev_code"] and got["challenge"].startswith("ppc_")
    rows = code_rows(hub)
    assert len(rows) == 1 and rows[0]["recipient"] == ref and rows[0]["channel"] == "sms"
    assert got["dev_code"] in rows[0]["body"]
    stored = hub.db.portal_codes.get(portal_mod._sha(got["challenge"]))
    assert got["dev_code"] not in str(stored) and got["challenge"] not in str(stored), "only hashes are kept"
    token = p.finish_sign_in(got["challenge"], got["dev_code"], client="10.0.0.1")
    assert p.session(token)["record"] == ref
    used = hub.db.outbox.get(rows[0]["id"])
    assert got["dev_code"] not in used["body"], "a used code is taken out of the outbox"
    assert used["status"] == "withdrawn", "and its message is never sent later"
    p.end_session(token)
    assert p.session(token) is None


def test_the_code_is_shown_only_on_a_dev_portal(hub):
    person(hub)
    got = Portal(hub).begin_sign_in(BORN, CARD, client="10.0.0.1")
    assert got["dev_code"] is None and code_rows(hub)


@pytest.mark.parametrize("born,card", [("1990-02-04", CARD), (BORN, "9999 999 999"), ("", CARD), (BORN, "")])
def test_wrong_details_get_the_same_answer_and_send_nothing(hub, born, card):
    p = Portal(hub)
    person(hub)
    right = p.begin_sign_in(BORN, CARD, client="10.0.0.2")
    wrong = p.begin_sign_in(born, card, client="10.0.0.3")
    assert set(wrong) == set(right) and wrong["dev_code"] is None and wrong["challenge"].startswith("ppc_")
    assert len(code_rows(hub)) == 1, "no message for details that match no one"
    with pytest.raises(Invalid) as no:
        p.finish_sign_in(wrong["challenge"], "000000", client="10.0.0.3")
    assert str(no.value) == p.say("no_match")


def test_no_record_at_all_answers_as_a_record_does(hub):
    p = Portal(hub, dev=False)
    nobody = p.begin_sign_in("1901-01-01", "0000 000 000", client="10.0.0.4")
    person(hub)
    somebody = p.begin_sign_in(BORN, CARD, client="10.0.0.5")
    assert {k: type(v) for k, v in nobody.items()} == {k: type(v) for k, v in somebody.items()}
    assert len(nobody["challenge"]) == len(somebody["challenge"])


def test_a_wrong_code_is_refused_and_attempts_lock(hub):
    p = Portal(hub, dev=True)
    person(hub)
    got = p.begin_sign_in(BORN, CARD, client="10.0.0.6")
    for _ in range(portal_mod.CODE_ATTEMPTS):
        with pytest.raises((Invalid, Denied)):
            p.finish_sign_in(got["challenge"], "1", client="10.0.0.6")
    with pytest.raises((Invalid, Denied)):  # the right code, too late: the attempts are spent (and the address locked)
        p.finish_sign_in(got["challenge"], got["dev_code"], client="10.0.0.6")


def test_an_address_is_locked_after_repeated_wrong_details(hub):
    p = Portal(hub)
    person(hub)
    for i in range(portal_mod.CLIENT_LIMIT):
        p.begin_sign_in("1950-01-01", f"5555 000 00{i}", client="10.0.0.7")
    with pytest.raises(Denied, match="Too many"):
        p.begin_sign_in(BORN, CARD, client="10.0.0.7")
    p.begin_sign_in(BORN, CARD, client="10.0.0.8")  # another address is not locked


def test_an_ipv6_address_is_counted_by_its_64(hub):
    """Blocker 2: one household or one attacker has a whole /64; each address in it is the same address here."""
    assert portal_mod.client_key("2001:db8:1:2::1") == portal_mod.client_key("2001:db8:1:2:ffff::9")
    assert portal_mod.client_key("2001:db8:1:2::1") != portal_mod.client_key("2001:db8:1:3::1")
    p = Portal(hub)
    for i in range(portal_mod.CLIENT_LIMIT):
        p.begin_sign_in("1950-01-01", f"5555 000 00{i}", client=f"2001:db8:1:2::{i + 1:x}")
    with pytest.raises(Denied, match="Too many"):
        p.begin_sign_in("1950-01-01", "5555 000 777", client="2001:db8:1:2:abcd::1")


def test_one_identifier_is_locked_from_every_address(hub):
    p = Portal(hub)
    person(hub)
    for i in range(portal_mod.PERSON_LIMIT):
        p.begin_sign_in("1950-01-01", CARD, client=f"10.1.0.{i}")
    with pytest.raises(Denied, match="Too many"):
        p.begin_sign_in(BORN, CARD, client="10.2.0.1")
    with pytest.raises(Denied, match="Too many"):  # and the lock is the same for an identifier no one has
        for i in range(portal_mod.PERSON_LIMIT + 1):
            p.begin_sign_in("1950-01-01", "4444 444 444", client=f"10.3.0.{i}")


def test_every_begin_counts_against_the_identifier(hub):
    """Blocker 2: right details count too, so asking for code after code for one person locks like guessing."""
    p = Portal(hub)
    person(hub)
    key = f"portal id {portal_mod._sha(portal_mod.norm_identifier(CARD))[:32]}"
    p.begin_sign_in(BORN, CARD, client="10.4.0.1")
    p.begin_sign_in(BORN, CARD, client="10.4.0.2")
    assert hub.db.signin_failures.get(key)["count"] == 2


def test_sends_to_one_record_are_capped_with_the_same_answer(hub):
    """Blocker 2: three codes a quarter hour to one record (ten a day); past that the answer is the same and
    nothing goes out, and the sends held back are one track event with their count."""
    p = Portal(hub, dev=True)
    ref = person(hub)
    answers = [p.begin_sign_in(BORN, CARD, client=f"10.5.0.{i}") for i in range(6)]
    assert len(code_rows(hub)) == 3
    assert all(set(a) == set(answers[0]) for a in answers)
    assert [bool(a["dev_code"]) for a in answers] == [True] * 3 + [False] * 3
    held = list(hub.db.events.find({"action": "portal.code_withheld", "subject": ref}))
    assert len(held) == 1 and held[0]["data"]["cap"] == 3
    assert len(list(hub.db.events.find({"action": "portal.code_sent", "subject": ref}))) == 3


def test_a_ceiling_on_everyones_sign_ins_in_a_minute(hub):
    pack = _pack(hub)
    pack.portal["per_minute"] = 3
    hub.pack = pack
    p = Portal(hub)
    for i in range(3):
        p.begin_sign_in("1950-01-01", f"6666 000 00{i}", client=f"10.6.{i}.1")
    with pytest.raises(Denied) as busy:
        p.begin_sign_in("1950-01-01", "6666 000 999", client="10.6.9.1")
    assert str(busy.value) == p.say("busy")
    with pytest.raises(Denied):
        p.register({"given": "X"}, client="10.6.9.2")


def test_a_code_expires(hub):
    p = Portal(hub, dev=True)
    person(hub)
    got = p.begin_sign_in(BORN, CARD, client="10.0.1.1")
    cid = portal_mod._sha(got["challenge"])
    hub.db.portal_codes.set(cid, {"expires_at": portal_mod._stamp(datetime.now(timezone.utc) - timedelta(seconds=1))})
    with pytest.raises(Invalid):
        p.finish_sign_in(got["challenge"], got["dev_code"], client="10.0.1.1")
    msg = hub.db.outbox.get(code_rows(hub)[0]["id"])
    assert got["dev_code"] not in msg["body"] and msg["status"] == "withdrawn"


def test_tick_sweeps_spent_codes_and_ended_sessions(hub):
    from practicehub.cli import portal_sweep
    p, ref, token = signed_in(hub)
    hub.db.portal_sessions.set(portal_mod._sha(token), {"expires_at": "2000-01-01T00:00:00+00:00"})
    got = p.begin_sign_in(BORN, CARD, client="10.0.1.9")
    hub.db.portal_codes.set(portal_mod._sha(got["challenge"]), {"expires_at": "2000-01-01T00:00:00+00:00"})
    swept = portal_sweep(hub)
    assert swept["codes"] == 1 and swept["sessions"] == 1
    assert all(r["status"] == "withdrawn" for r in code_rows(hub))


def test_a_code_is_used_once(hub):
    p = Portal(hub, dev=True)
    person(hub)
    got = p.begin_sign_in(BORN, CARD, client="10.0.1.3")
    p.finish_sign_in(got["challenge"], got["dev_code"], client="10.0.1.3")
    with pytest.raises(Invalid):
        p.finish_sign_in(got["challenge"], got["dev_code"], client="10.0.1.3")


def test_a_code_tried_at_once_opens_one_session_and_spends_its_attempts_exactly(hub):
    """Blocker 3: the check is atomic. The right code sent many times at once opens one session; wrong codes sent
    at once never get more tries than the code allows."""
    p = Portal(hub, dev=True)
    person(hub)
    got = p.begin_sign_in(BORN, CARD, client="10.0.1.5")
    wins, barrier = [], threading.Barrier(8)

    def right(i):
        barrier.wait()
        try:
            wins.append(p.finish_sign_in(got["challenge"], got["dev_code"], client=f"10.0.7.{i}"))
        except (Invalid, Denied):
            pass
    threads = [threading.Thread(target=right, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(wins) == 1 and hub.db.portal_sessions.count({}) == 1

    person(hub, card="3333 444 555", born="1970-01-01")  # another sign-in, its own identifier's count
    other = p.begin_sign_in("1970-01-01", "3333 444 555", client="10.0.1.6")
    barrier = threading.Barrier(8)

    def wrong(i):
        barrier.wait()
        try:
            p.finish_sign_in(other["challenge"], "000001", client=f"10.0.8.{i}")
        except (Invalid, Denied):
            pass
    threads = [threading.Thread(target=wrong, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert hub.db.portal_codes.get(portal_mod._sha(other["challenge"]))["attempts"] == portal_mod.CODE_ATTEMPTS
    with pytest.raises(Invalid):
        p.finish_sign_in(other["challenge"], other["dev_code"], client="10.0.8.99")


def test_no_contact_on_file_sends_nothing_and_answers_the_same(hub):
    p = Portal(hub, dev=True)
    person(hub, phone=None)
    got = p.begin_sign_in(BORN, CARD, client="10.0.1.4")
    assert got["dev_code"] is None and not code_rows(hub)


# ------------------------------------------------------------------------------------------- slots and booking
def test_free_slots_are_the_kinds_roles_within_its_horizon(hub):
    p = Portal(hub)
    slots = p.free_slots("forms")
    assert slots
    physicians = {m["id"] for m in hub.role("physician")["holders"]}
    now = hub.engine.now()
    for s in slots:
        when, who = s["value"].split("|")
        t = datetime.fromisoformat(when)
        assert who in physicians and now < t <= now + timedelta(days=22)
        assert re.fullmatch(r"\d{1,2}:\d\d [ap]\.m\.", s["time"]) and re.match(r"[A-Z][a-z]+day, ", s["day"])


def _run_of(hub, got):
    return hub.engine.run(got["run"])


def test_booking_forms_starts_the_practices_request_with_the_time_chosen(hub):
    p, ref, token = signed_in(hub)
    slot = later_slot(p)
    got = p.book(ref, "forms", slot["value"], {"reason": "a school form"})
    run = _run_of(hub, got)
    assert run["workflow_id"] == "appointment-request" and run["subject"] == ref
    assert run["started_by"] == p.member["id"]
    assert run["ctx"]["input"]["kind"] == "forms" and run["ctx"]["input"]["slot"] == slot["value"]
    assert got["advice"], "the screen's say-now advice is always on the page"
    if got["when"]:  # screened routine: booked at once, by the workflow's own step
        assert slot["value"] not in {s["value"] for s in p.free_slots("forms", limit=10000)}, "the time is taken"
        mine = p.upcoming(ref)
        assert len(mine) == 1 and mine[0]["kind"] == p.kind("forms")["label"]
        assert "school form" not in str(mine), "the reason stays with the practice"
    else:  # not plainly paperwork: a person reads it first
        assert got["requested"] and not got["failed"]


def test_a_red_flag_on_forms_is_a_request_to_a_nurse_never_a_booking(hub):
    """Blocker 5: "Forms" is screened too; what the screen flags (or is unsure of) goes to a nurse, not the
    calendar, and is told loudly."""
    p, ref, token = signed_in(hub)
    slot = later_slot(p)
    got = p.book(ref, "forms", slot["value"], {"reason": "a sick note, I have crushing chest pain and can't breathe"})
    assert got["alarm"] is True and not got["when"] and got["requested"]
    run = _run_of(hub, got)
    assert run["steps"].get("book-chosen", {}).get("state") != "done"
    assert run["steps"]["nurse-call"]["state"] not in ("skipped", None)
    assert slot["value"] in {s["value"] for s in p.free_slots("forms", limit=10000)}, "no time was taken"


def test_a_new_concern_is_a_request_through_appointment_request(hub):
    p, ref, token = signed_in(hub)
    got = p.book(ref, "new-concern", None, {"reason": "a rash on my arm for a week", "urgency": "routine"})
    run = _run_of(hub, got)
    assert run["workflow_id"] == "appointment-request" and run["subject"] == ref
    assert run["ctx"]["input"]["kind"] == "new-concern"
    assert got["mode"] == "request" and got["advice"], "the screen's say-now advice is shown"
    assert [r["what"] for r in p.open_requests(ref)] == [p.kind("new-concern")["label"]]


def test_a_red_flag_is_told_loudly(hub):
    p, ref, token = signed_in(hub)
    got = p.book(ref, "new-concern", None, {"reason": "crushing chest pain and I can't breathe", "urgency": "urgent"})
    assert got["alarm"] is True and got["advice"]


def test_a_taken_time_is_a_clash_with_fresh_times(hub):
    p, ref, token = signed_in(hub)
    other = person(hub, card="2222 333 444", born="1980-05-06")
    _, taken = _booked_forms(hub, p, other)
    slot = taken["value"]
    with pytest.raises(Clash) as clash:
        p.book(ref, "forms", slot, {"reason": "a form"})
    assert clash.value.slots and slot not in {s["value"] for s in clash.value.slots}


def test_a_time_must_be_chosen_to_book(hub):
    p, ref, token = signed_in(hub)
    with pytest.raises(Invalid):
        p.book(ref, "forms", "", {"reason": "a form"})


def test_the_same_request_twice_is_the_one_request(hub):
    """A double tap that reaches the workflow twice (a resend, a second tab) is the open request itself: the
    workflow's one_open_same names it and the portal answers with it."""
    p, ref, token = signed_in(hub)
    one = p.book(ref, "new-concern", None, {"reason": "my knee hurts", "urgency": "routine"})
    two = p.book(ref, "new-concern", None, {"reason": "my knee hurts", "urgency": "routine"})
    assert one["run"] == two["run"]
    three = p.book(ref, "new-concern", None, {"reason": "and now my elbow", "urgency": "routine"})
    assert three["run"] != one["run"], "another concern is another request, screened on its own"


def test_a_kind_allows_only_so_many_open(hub):
    """Blocker 4: max_upcoming per kind: past it, the person is told to call."""
    p, ref, token = signed_in(hub)
    for reason in ("my knee", "my elbow"):
        p.book(ref, "new-concern", None, {"reason": reason, "urgency": "routine"})
    assert p.limits(ref)["new-concern"] == {"open": 2, "max": 2}, "told up front, before they write"
    before = hub.db.runs.count({"workflow_id": "appointment-request"})
    with pytest.raises(Denied) as many:
        p.book(ref, "new-concern", None, {"reason": "I need a form filled in for work", "urgency": "routine"})
    assert str(many.value) == p.say("too_many"), "plainly paperwork: refused, to call"
    got = p.book(ref, "new-concern", None, {"reason": "my shoulder", "urgency": "routine"})
    assert got["over_limit"] and got["requested"] and not got["when"] and not got["alarm"]
    run = _run_of(hub, got)
    assert run["workflow_id"] == "patient-question" and run["ctx"]["input"]["question"] == "my shoulder", \
        "not routine: a person reads it, though nothing more is booked"
    assert hub.db.runs.count({"workflow_id": "appointment-request"}) == before


def test_a_red_flag_past_the_limit_is_screened_and_reaches_a_nurse(hub):
    """Round 7 blocker: a patient at the limit who then writes about chest pain is screened before the limit is
    applied: the advice is loud, nothing is booked, and the words go to a nurse."""
    p, ref, token = signed_in(hub)
    for reason in ("my knee", "my elbow"):
        p.book(ref, "new-concern", None, {"reason": reason, "urgency": "routine"})
    got = p.book(ref, "new-concern", None, {"reason": "Since an hour ago my chest feels tight and my left arm is numb",
                                            "urgency": "soon"})
    assert got["alarm"] is True and got["advice"] and "chest pain" in got["found"] and got["over_limit"]
    run = _run_of(hub, got)
    assert run["workflow_id"] == "patient-question"
    assert run["ctx"]["screen"]["red_flag"] is True
    assert run["steps"]["urgent"]["state"] not in ("skipped", None), "a nurse is to call now"


def test_the_limit_is_said_on_the_form_and_the_refusal_says_what_to_do_in_an_emergency(hub):
    app = portal_app(hub)
    p = Portal(hub, dev=True)
    ref = person(hub)
    for reason in ("my knee", "my elbow"):
        p.book(ref, "new-concern", None, {"reason": reason, "urgency": "routine"})
    b = Browser(app)
    _sign_in(b)
    page = b.go("GET", "/me")[2]
    assert html.escape(app.say("at_limit", open=2)) in page
    page = b.go("GET", "/book/new-concern")[2]
    assert html.escape(app.say("at_limit", open=2)) in page and 'name="slot"' not in page
    assert html.escape(app.say("safety")) in page
    status, h, page = b.go("POST", "/book/new-concern", {"_form": _field(page, "_form"),
                                                         "_submit": _field(page, "_submit"),
                                                         "reason": "a form for my work", "urgency": "routine"})
    assert status == 403 and html.escape(app.say("safety")) in page and "811" in page


def _booked_forms(hub, p, ref):
    """A forms visit booked at a time more than a day ahead (the screen's routine path; the triage nurse's when the
    screen sends it to triage)."""
    slot = later_slot(p)
    got = p.book(ref, "forms", slot["value"], {"reason": "a form for my employer"})
    if not got["when"]:
        run = _run_of(hub, got)
        if run["steps"].get("triage", {}).get("state") in ("open", "ready", "claimed"):
            iid = item_for(hub, run, "triage")
            nurse = hub.role("nurse")["holders"][0]
            hub.engine.complete(hub.member(nurse["id"]), iid, {"decision": "book-a-slot", "notes": ""})
    mine = p.upcoming(ref)
    assert mine, "booked"
    return mine[0], slot


def test_a_booking_is_cancelled_by_a_move_its_workflow_allows(hub):
    p, ref, token = signed_in(hub)
    b, slot = _booked_forms(hub, p, ref)
    assert b["can_cancel"]
    got = p.cancel(ref, b["ref"])
    assert got["when"] == b["when"] and p.upcoming(ref) == []
    rec = hub.records.read(*b["ref"].split("/", 1))
    assert rec["status"] == "cancelled"
    ev = hub.db.events.find_one({"action": "record.transitioned", "data.ref": b["ref"]}) or \
        hub.db.events.find_one({"action": {"$regex": "^record\\."}, "actor": p.member["id"], "data.to": "cancelled"})
    assert ev is None or ev["actor"] == p.member["id"]
    assert slot["value"] in {s["value"] for s in p.free_slots("forms", limit=10000)}, "the time is free again"


def test_cancel_only_ones_own_and_only_far_enough_ahead(hub, monkeypatch):
    p, ref, token = signed_in(hub)
    b, _ = _booked_forms(hub, p, ref)
    other = person(hub, card="2222 333 444", born="1980-05-06")
    with pytest.raises(Denied):
        p.cancel(other, b["ref"])
    k = p.kind("forms")
    monkeypatch.setitem(k, "cancel_before_hours", 24 * 400)
    monkeypatch.setattr(p, "kind", lambda kid: k)
    monkeypatch.setattr(p, "kinds", lambda: {"forms": k, "new-concern": Portal(hub).kind("new-concern")})
    assert not p.upcoming(ref)[0]["can_cancel"]
    with pytest.raises(Denied):
        p.cancel(ref, b["ref"])


# ------------------------------------------------------------------------------------------- someone new
def _intake_accepts(hub, run):
    iid = item_for(hub, run, "review")
    hub.engine.complete(M(hub, "intake-agent"), iid, {"complete": True, "missing": "", "urgency": "routine",
                                                       "summary": "complete", "flags": []})
    run = hub.engine.run(run["id"])
    hub.engine.decide(M(hub, "jo"), item_for(hub, run, "accept"), "accept")


def test_someone_new_is_a_request_until_the_intake_accepts_them(hub):
    """Blocker 4: a registration is a request. The record is not active, so nobody signs in as it or books for it
    until a person at the practice accepts them through new-patient-intake."""
    p = Portal(hub, dev=True)
    got = p.register({"given": "Bea", "family": "Moss", "birthDate": "2001-07-08", "health_card": "7777 888 999",
                      "phone": "902-555-0199"}, client="10.0.2.1")
    run = got if "workflow_id" in got else hub.engine.run(got["run"])
    assert run["workflow_id"] == "new-patient-intake"
    ref = run["record"] if run["record"].startswith("Patient/") else run["subject"]
    rec = hub.records.read(*ref.split("/", 1))
    assert rec["birthDate"] == "2001-07-08" and rec["identifier"][0]["value"] == "7777 888 999"
    assert rec["active"] is False
    first = p.begin_sign_in("2001-07-08", "7777 888 999", client="10.0.2.5")
    assert first["dev_code"] is None and not code_rows(hub), "a request to join cannot sign in"
    _intake_accepts(hub, run)
    assert hub.records.read(*ref.split("/", 1))["active"] is True
    then = p.begin_sign_in("2001-07-08", "7777 888 999", client="10.0.2.6")
    assert then["dev_code"], "accepted, they may sign in"
    with pytest.raises(Invalid):
        p.register({"given": "Bea"}, client="10.0.2.1")


def test_registering_is_counted_per_address(hub):
    p = Portal(hub)
    for _ in range(portal_mod.REGISTER_LIMIT):
        try:
            p.register({"given": "X"}, client="10.0.2.2")
        except Invalid:
            pass
    with pytest.raises(Denied):
        p.register({"given": "X"}, client="10.0.2.2")


# ------------------------------------------------------------------------------------------- blocker 1: no Hub here
def test_the_hub_answers_only_the_portal_members_portal_token(hub):
    assert api(hub, "config")[0] == 200
    staff = hub.issue_token(M(hub, "jo"), "jo", "api")
    assert api(hub, "config", token=staff)[0] == 401, "a staff API token does not open the portal's API"
    other = hub.issue_token(M(hub, "jo"), "lab-feed", "x", scope="portal") if \
        hub.member("lab-feed")["sponsor"] == "jo" else None
    if other:
        assert api(hub, "config", token=other)[0] == 401, "only the [portal] member's"
    with pytest.raises(Invalid):
        hub.issue_token(M(hub, "jo"), "jo", "x", scope="portal")  # a person never holds a portal token
    from practicehub.web.server import App
    tok = portal_token(hub)
    status, _, _ = App(hub).handle("GET", "/api/me", {"authorization": f"Bearer {tok}"}, b"")
    assert status in (401, 403), "the portal's token opens nothing else of the hub's"


def test_the_portal_process_holds_no_database_and_no_secret():
    """The pages import nothing that opens the database or reads the hub's secret, and serve() takes an address
    and a token, not a Hub."""
    import ast
    import inspect
    from practicehub.web import portal_app
    tree = ast.parse(Path(portal_app.__file__).read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
        {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not mods & {"..hub", "..db", "..store", "..portal", "pymongo", "practicehub.hub", "practicehub.db"}, mods
    src = Path(portal_app.__file__).read_text()
    assert "PRACTICEHUB_SECRET" not in src and "PRACTICEHUB_DATABASE" not in src and "MongoClient" not in src
    assert list(inspect.signature(portal_app.serve).parameters)[:2] == ["hub_url", "token"]


def test_portal_serve_refuses_the_reserved_ports_and_a_dev_portal_off_this_machine():
    from practicehub.web.portal_app import serve
    for port in (8080, 9980, 11434):
        with pytest.raises(SystemExit):
            serve("http://127.0.0.1:1", "t", "127.0.0.1", port)
    with pytest.raises(SystemExit, match="this machine"):
        serve("http://127.0.0.1:1", "t", "0.0.0.0", 8210, dev=True)
    with pytest.raises(SystemExit, match="refused|did not answer"):  # no hub there: it says so and does not start
        serve("http://127.0.0.1:1", "t", "127.0.0.1", 8219)


def test_the_portal_talks_to_a_served_hub_over_http(hub):
    from http.server import ThreadingHTTPServer
    from practicehub.web.portal_app import HubClient
    from practicehub.web.server import App, make_handler
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(hub, dev=False)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        c = HubClient(f"http://127.0.0.1:{httpd.server_address[1]}", portal_token(hub))
        cfg = c.call("config")
        assert cfg["kinds"] and cfg["privacy"] and cfg["dev"] is False
        assert "member" not in cfg and "uri" not in json.dumps(cfg).lower()
    finally:
        httpd.shutdown()


# ------------------------------------------------------------------------------------------- the pages
class Browser:
    def __init__(self, app, client="10.0.3.1", host="127.0.0.1:8210"):
        self.app, self.jar, self.client, self.host = app, {}, client, host

    def _cookies(self, h):
        got = h.get("Set-Cookie")
        for c in (got if isinstance(got, list) else [got] if got else []):
            name, _, rest = c.partition("=")
            value = rest.split(";")[0]
            if "Max-Age=0" in c:
                self.jar.pop(name, None)
            else:
                self.jar[name] = value

    def go(self, method, path, form=None, origin=None, headers=None):
        hd = {"host": self.host, "x-practicehub-client": self.client,
              "cookie": "; ".join(f"{k}={v}" for k, v in self.jar.items()), **(headers or {})}
        if origin:
            hd["origin"] = origin
        body = urllib.parse.urlencode(form or {}).encode()
        status, h, page = self.app.handle(method, path, hd, body)
        self._cookies(h)
        return status, h, page.decode()


def _dob(born=BORN, prefix="born"):
    y, m, d = born.split("-")
    return {f"{prefix}_year": y, f"{prefix}_month": m, f"{prefix}_day": str(int(d))}


def _sign_in(b):
    status, h, _ = b.go("POST", "/sign-in", {**_dob(), "identifier": CARD}, origin="http://127.0.0.1:8210")
    assert status == 303 and "php_signin" in b.jar
    code = urllib.parse.parse_qs(urllib.parse.urlsplit(h["Location"]).query)["dev"][0]
    status, h, _ = b.go("POST", "/code", {"code": code})
    assert status == 303 and h["Location"] == "/me" and "php_session" in b.jar
    return code


def _field(page, name):
    return re.search(rf'name="{name}" value="([^"]+)"', page).group(1)


def test_a_form_from_the_portal_s_own_page_is_taken_as_a_browser_sends_it_and_one_from_elsewhere_is_refused(hub):
    """A browser says where a form was posted from (``Origin``); the portal takes its own pages' forms and refuses
    any other site's. Under ``Referrer-Policy: no-referrer`` a browser says ``Origin: null`` for every form, the
    portal's own too, so the pages send ``same-origin``: the referrer goes to the portal itself and to no one else."""
    app = portal_app(hub)
    person(hub)
    b = Browser(app)
    status, h, _ = b.go("GET", "/sign-in")
    assert status == 200 and h["Referrer-Policy"] == "same-origin"
    form = {**_dob(), "identifier": CARD}
    for origin in ("null", "https://elsewhere.example", f"http://{b.host}.elsewhere.example"):
        status, h, page = b.go("POST", "/sign-in", form, origin=origin)
        assert status == 403 and page == "Refused: not from this site." and h["Referrer-Policy"] == "same-origin"
    status, h, _ = b.go("POST", "/sign-in", form, origin=f"http://{b.host}")  # what a browser sends from its page
    assert status == 303 and "php_signin" in b.jar


def test_the_pages_walk_from_sign_in_to_a_booking(hub):
    app = portal_app(hub)
    person(hub)
    b = Browser(app)
    status, h, page = b.go("GET", "/")
    assert status == 200 and html.escape(app.cfg["privacy"]) in page and "frame-ancestors 'none'" in \
        h["Content-Security-Policy"]
    assert "<script" not in page and "script-src" not in h["Content-Security-Policy"], "no JavaScript anywhere"
    assert b.go("GET", "/me")[1]["Location"] == "/sign-in"
    status, _, page = b.go("GET", "/sign-in")
    assert 'autocomplete="bday-day"' in page and 'autocomplete="bday-month"' in page and \
        'autocomplete="bday-year"' in page and 'type="date"' not in page
    assert 'inputmode="numeric"' in page.split('id="identifier"')[1].split(">")[0], "a health card: digits"
    assert "btn primary" in page and "(required)" in page
    code = _sign_in(b)
    assert code
    status, _, page = b.go("GET", "/me")
    assert status == 200 and "Nothing booked" in page
    status, _, page = b.go("GET", "/book/new-concern")
    assert '<option value="" selected>Choose one</option>' in page, "a required choice starts blank"
    assert "In the next few days" in page and ">soon<" not in page, "choices in the person's words"
    assert "A nurse reads it." in page and 'aria-describedby="q-reason-help"' in page
    assert "days, times or physician the patient prefers" not in page, "the workflow's staff help is never shown"
    form, submit = _field(page, "_form"), _field(page, "_submit")
    status, h, page = b.go("POST", "/book/new-concern", {"_form": form, "_submit": submit, "reason": "a rash"})
    assert status == 422 and 'aria-invalid="true"' in page, "a required choice left blank is said at the field"
    before = hub.db.runs.count({"workflow_id": "appointment-request"})
    status, h, _ = b.go("POST", "/book/new-concern", {"_form": form, "_submit": submit, "reason": "a rash",
                                                      "urgency": "routine"})
    assert status == 303 and h["Location"].startswith("/done/")
    again = b.go("POST", "/book/new-concern", {"_form": form, "_submit": submit, "reason": "a rash",
                                                "urgency": "routine"})
    assert again[0] == 303 and again[1]["Location"] == h["Location"], "the same form twice: the same answer"
    assert hub.db.runs.count({"workflow_id": "appointment-request"}) == before + 1
    status, _, page = b.go("GET", h["Location"])
    assert status == 200 and "<title>Request sent" in page and "Thank you" not in page
    assert 'class="advice' in page, "the say-now advice is always on the page"
    assert b.go("GET", h["Location"])[2] == page, "a reload is the same page"
    status, _, page = b.go("GET", "/book/new-concern")
    status, h, _ = b.go("POST", "/book/new-concern", {"_form": _field(page, "_form"), "_submit": _field(page, "_submit"),
                                                      "reason": "crushing chest pain, can't breathe",
                                                      "urgency": "urgent"})
    page = b.go("GET", h["Location"])[2]
    assert 'class="advice alarm"' in page
    assert 'class="ok"' not in page and app.say("requested") not in page, "no green 'we have it' over an alarm"
    assert "<title>Your message needs attention today" in page and "<h2>What to do now</h2>" in page and "chest pain" in page
    assert html.escape(app.say("safety")) in page
    status, _, page = b.go("POST", "/book/forms", {"_form": "forged", "_submit": "x" * 20, "slot": ""})
    assert status == 422 and "expired" in page
    assert b.go("POST", "/sign-out", {"_form": form}, origin="https://evil.example")[0] == 403
    assert b.go("POST", "/sign-out", {"_form": form})[0] == 303
    assert b.go("GET", "/me")[1]["Location"] == "/sign-in"


def test_the_done_page_belongs_to_whoever_sent_the_form(hub):
    app = portal_app(hub)
    person(hub)
    b = Browser(app)
    _sign_in(b)
    page = b.go("GET", "/book/new-concern")[2]
    h = b.go("POST", "/book/new-concern", {"_form": _field(page, "_form"), "_submit": _field(page, "_submit"),
                                           "reason": "a cough", "urgency": "routine"})[1]
    stranger = Browser(app, client="10.0.3.2")
    assert stranger.go("GET", h["Location"])[0] == 404


def test_cancel_has_a_confirm_page_and_lands_on_its_done_page(hub):
    app = portal_app(hub)
    p = Portal(hub, dev=True)
    ref = person(hub)
    _booked_forms(hub, p, ref)
    b = Browser(app)
    _sign_in(b)
    page = b.go("GET", "/me")[2]
    assert "Cancel this visit" in page and re.search(r"[A-Z][a-z]+day, [A-Z][a-z]+ \d+", page), "dates in words"
    link = html.unescape(re.search(r'href="(/cancel\?[^"]+)"', page).group(1))
    status, _, page = b.go("GET", link)
    assert status == 200 and "Yes, cancel this visit" in page and p.upcoming(ref), "nothing is cancelled by a GET"
    data = {"_form": _field(page, "_form"), "_submit": _field(page, "_submit"), "booking": _field(page, "booking")}
    status, h, _ = b.go("POST", "/cancel", data)
    assert status == 303 and h["Location"].startswith("/done/") and not p.upcoming(ref)
    assert b.go("POST", "/cancel", data)[1]["Location"] == h["Location"], "a second tap: the same page"
    page = b.go("GET", h["Location"])[2]
    assert "<title>Cancelled" in page


def test_a_wrong_code_shows_the_form_again(hub):
    app = portal_app(hub, dev=False)
    person(hub)
    b = Browser(app)
    status, h, _ = b.go("POST", "/sign-in", {**_dob(), "identifier": CARD})
    assert h["Location"] == "/code", "a real portal never puts the code in the page"
    assert "Secure" in str(h["Set-Cookie"])
    status, _, page = b.go("POST", "/code", {"code": "000000"})
    assert status == 422 and html.escape(app.say("no_match")) in page and 'aria-invalid="true"' in page


def test_the_portal_does_not_take_the_staff_sign_in(hub):
    b = Browser(portal_app(hub))
    b.jar = {"ph_session": "anything", "ph_as": "dr-amara"}
    assert b.go("GET", "/me")[1]["Location"] == "/sign-in"


def test_a_dev_portal_answers_this_machine_only(hub):
    app = portal_app(hub)
    assert Browser(app).go("GET", "/")[0] == 200
    assert Browser(app, host="book.example.ca").go("GET", "/")[0] == 403
    assert Browser(app).go("GET", "/", headers={"x-forwarded-for": "203.0.113.9"})[0] == 403


def test_a_large_form_is_refused_before_it_is_read(hub):
    from practicehub.web.portal_app import MAX_FORM
    status, _, _ = portal_app(hub).handle("POST", "/sign-in", {"host": "127.0.0.1"}, b"x" * (MAX_FORM + 1))
    assert status == 413


def test_the_listener_refuses_a_large_body_by_its_length_and_times_out_an_idle_client(hub):
    import socket
    from http.server import ThreadingHTTPServer
    from practicehub.web.server import make_handler
    app = portal_app(hub)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, max_body=1024, timeout=0.5))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        port = httpd.server_address[1]
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.sendall(b"POST /sign-in HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 999999\r\n\r\n")
            assert b" 413 " in s.recv(200)
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.sendall(b"GET / HTTP/1.1\r\n")  # and never finishes the request
            s.settimeout(5)
            assert s.recv(200) == b"", "the server closed the idle connection (a slow client holds no thread)"
    finally:
        httpd.shutdown()


def test_registering_lands_on_a_request_page(hub):
    app = portal_app(hub)
    b = Browser(app)
    page = b.go("GET", "/new")[2]
    assert 'autocomplete="bday-year"' in page and "request to join" in page
    form = {"_submit": _field(page, "_submit"), "given": "Bea", "family": "Moss", **_dob("2001-07-08", "r_birthDate"),
            "health_card": "7777 888 999", "phone": "902-555-0199"}
    before = hub.db.runs.count({"workflow_id": "new-patient-intake"})
    status, h, _ = b.go("POST", "/new", form)
    assert status == 303 and h["Location"].startswith("/done/")
    assert b.go("POST", "/new", form)[1]["Location"] == h["Location"]
    assert hub.db.runs.count({"workflow_id": "new-patient-intake"}) == before + 1, "one request, however often sent"
    page = b.go("GET", h["Location"])[2]
    assert "<title>Request sent" in page


# ------------------------------------------------------------------------------------------- the core stays general
def test_the_portal_core_names_nothing_of_a_pack():
    words = re.compile(r"\b(patients?|Patient|appointments?|Appointment|Encounter|Practitioner|clinic|physician|"
                       r"health.card|new-concern|appointment-request)\b")
    for f in (ROOT / "practicehub" / "portal.py", ROOT / "practicehub" / "web" / "portal_app.py",
              ROOT / "practicehub" / "web" / "portal_api.py"):
        found = words.findall(f.read_text())
        assert not found, f"{f.name} names {sorted(set(found))}"




# ------------------------------------------------------------------------------------------- the registration
def _register(hub, given="Bea", card="7777 888 999"):
    got = Portal(hub, dev=True).register({"given": given, "family": "Moss", "birthDate": "2001-07-08",
                                          "health_card": card, "phone": "902-555-0199"}, client="10.0.2.1")
    return got if "workflow_id" in got else hub.engine.run(got["run"])


def _moves(hub, ref):
    return [e["data"]["to"] for e in hub.db.events.find({"subject": ref, "action": "record.transitioned"}, sort="seq")]


def test_a_registration_is_a_record_of_its_own_walked_until_the_person_is_taken_in(hub):
    """The intake walks the registration (an intake form about the person), never the person's own record: checked,
    then accepted; the person is its subject and is made active only by the acceptance. One run, one item."""
    run = _register(hub)
    ref, pat = run["record"], run["subject"]
    assert ref.startswith("QuestionnaireResponse/") and pat.startswith("Patient/")
    form = hub.records.read(*ref.split("/", 1))
    assert form["registration"] == "received" and form["questionnaire"] == "registration"
    assert form["subject"]["reference"] == pat
    with pytest.raises((Denied, Invalid)):  # its intake moves it, nobody by hand
        hub.update_record(M(hub, "jo"), ref, {**form, "registration": "accepted"})
    _intake_accepts(hub, run)
    assert hub.records.read(*ref.split("/", 1))["registration"] == "accepted"
    assert hub.records.read(*pat.split("/", 1))["active"] is True
    assert _moves(hub, ref) == ["checked", "accepted"]
    assert len([i for i in hub.db.items.find({"run_id": run["id"]}) if i.get("parent_id") is None]) == 1
    booked = hub.db.runs.find_one({"workflow_id": "appointment-request", "subject": pat})
    assert booked and booked["record"].startswith("Encounter/"), "the first visit is booked for the person"


@pytest.mark.parametrize("outcome,state", [("decline", "declined"), ("waitlist", "waitlisted")])
def test_a_registration_not_accepted_says_so_and_the_person_stays_unable_to_sign_in(hub, outcome, state):
    run = _register(hub)
    hub.engine.complete(M(hub, "intake-agent"), item_for(hub, run, "review"), {
        "complete": True, "missing": "", "urgency": "routine", "summary": "complete", "flags": []})
    hub.engine.decide(M(hub, "jo"), item_for(hub, hub.engine.run(run["id"]), "accept"), outcome, "roster full")
    assert hub.records.read(*run["record"].split("/", 1))["registration"] == state
    assert hub.records.read(*run["subject"].split("/", 1))["active"] is False
    assert hub.engine.run(run["id"])["status"] == "completed"
    assert Portal(hub, dev=True).begin_sign_in("2001-07-08", "7777 888 999", client="10.0.2.7")["dev_code"] is None


def test_the_portal_knows_when_the_practice_is_closed(hub):
    """Round 7 blocker: an alarm page says when nobody is there to read it."""
    p = Portal(hub)
    hours = (hub.pack.calendar or {}).get("hours") or {}
    assert hours.get("mon") and not hours.get("sat")
    saturday = datetime(2026, 11, 21, 11, 0)
    assert p.opens(saturday) == datetime(2026, 11, 23, *map(int, hours["mon"][0].split(":")))
    assert p.opens(datetime(2026, 11, 23, 10, 0)) is None, "open now"
    assert p.opens(datetime(2026, 11, 23, 7, 30)).date() == datetime(2026, 11, 23).date()


def test_booking_is_day_first_with_each_days_times_under_its_physician(hub):
    """Round 7 blocker: the form listed only the first max_slots times (about a day), so other days never showed
    and a visit booked online was never far enough off to cancel online."""
    p = Portal(hub)
    days = p.free_days("forms")
    assert len(days) >= 10, "every working day of the horizon with free time"
    assert all(d["count"] > 0 for d in days) and [d["date"] for d in days] == sorted(d["date"] for d in days)
    later = days[-1]["date"]
    slots = p.free_slots("forms", day=later)
    assert slots and {s["date"] for s in slots} == {later}
    assert len(slots) <= int(p.kind("forms").get("max_slots", 60)), "the cap is per day"
    assert p.slot_free("forms", slots[0]["value"])
    assert p.free_slots("forms", day="1999-01-01") == [] and p.free_slots("forms", day="nonsense") == []

    app = portal_app(hub)
    person(hub)
    b = Browser(app)
    _sign_in(b)
    page = b.go("GET", "/book/forms")[2]
    assert page.index('name="reason"') < page.index('name="_day"'), "what it is about, before when"
    assert page.index('class="enter-default"') < page.index('name="_day"'), "Enter books, it does not pick a day"
    assert "You can cancel online up to 24 hours before" in page
    status, _, page = b.go("POST", "/book/forms", {"_form": _field(page, "_form"), "_submit": _field(page, "_submit"),
                                                   "reason": "a sick note for work", "_day": later})
    assert status == 200 and "a sick note for work" in page, "what they wrote is kept"
    assert f'value="{later}" formnovalidate aria-pressed="true"' in page
    values = [v for v in re.findall(r'name="slot" value="([^"]*)"', page) if v]
    assert values and all(v.startswith(later) for v in values)
    assert "<h4>" in page, "the times are under each physician"
    status, h, _ = b.go("POST", "/book/forms", {"_form": _field(page, "_form"), "_submit": _field(page, "_submit"),
                                                "reason": "a sick note for work", "slot": values[0], "_go": "1"})
    assert status == 303
    page = b.go("GET", h["Location"])[2]
    assert "<title>Booked" in page
    page = b.go("GET", "/me")[2]
    assert "/cancel?" in page, "booked days ahead: it can be cancelled online"



# ------------------------------------------------------------------------------------------- round 7: portal minor
@pytest.mark.parametrize("day,month,year,says", [
    ("32", "01", "1990", "1 to 31"),
    ("31", "02", "1990", "February 1990 has no day 31"),
    ("3", "02", "90", "4 digits"),
    ("3", "02", "1850", "4 digits"),
    ("3", "", "1990", "month"),
])
def test_sign_in_checks_the_date_as_typed_and_keeps_it(hub, day, month, year, says):
    app = portal_app(hub)
    b = Browser(app)
    status, _, page = b.go("POST", "/sign-in", {"born_day": day, "born_month": month, "born_year": year,
                                                "identifier": CARD})
    assert status == 422 and says in html.unescape(page) and "Error:" in page
    assert f'value="{year}"' in page, "what was typed is kept"
    assert 'aria-invalid="true"' in page


def test_sign_in_counts_the_cards_digits_and_says_how_many(hub):
    app = portal_app(hub)
    b = Browser(app)
    page = b.go("GET", "/sign-in")[2]
    assert "10 digits" in page and 'id="identifier-help"' in page and "For example" in page
    status, _, page = b.go("POST", "/sign-in", {**_dob(), "identifier": "1234 567"})
    assert status == 422 and "you typed 7" in page and 'value="1234 567"' in page


def test_registering_asks_for_a_mobile_with_why_and_a_first_name(hub):
    app = portal_app(hub)
    page = Browser(app).go("GET", "/new")[2]
    assert ">First name" in page and ">Last name" in page
    assert "We text your sign-in code to this number." in page
    assert re.search(r'for="r-phone">Mobile phone <span class=req>\(required\)', page)
    b = Browser(app)
    page = b.go("GET", "/new")[2]
    status, _, page = b.go("POST", "/new", {"_submit": _field(page, "_submit"), "given": "Bea", "family": "Moss",
                                            **_dob("2001-07-08", "r_birthDate"), "health_card": "7777 888 999"})
    assert status == 422 and "Mobile phone: required" in page
    bad = check_config(_pack_with(hub, lambda c: c["register"]["fields"].append({"field": "phone", "colour": 1})))
    assert any("colour" in x for x in bad)


def _pack_with(hub, change):
    pack = _pack(hub)
    change(pack.portal)
    return pack


def test_bookings_are_cards_with_the_next_step_and_why_not_cancel(hub):
    p, ref, token = signed_in(hub)
    soon = next(s for s in p.free_slots("forms", limit=10000))
    p.book(ref, "forms", soon["value"], {"reason": "a sick note for work"})
    later = later_slot(p, hours=48)
    p.book(ref, "forms", later["value"], {"reason": "a form for my insurer"})
    ups = p.upcoming(ref)
    first, second = ups[0], ups[-1]
    assert not first["can_cancel"] and "24 hours" in first["why_not"]
    assert second["can_cancel"] and second["why_not"] is None and second["next"]
    app = portal_app(hub)
    b = Browser(app)
    _sign_in(b)
    page = b.go("GET", "/me")[2]
    assert page.count('class="booking"') >= 2 and "Less than 24 hours away" in page
    assert '<button class="linkish">Sign out</button>' in page


def test_an_open_requests_day_is_the_practices_not_utcs(hub):
    p, ref, token = signed_in(hub)
    p.book(ref, "new-concern", None, {"reason": "my knee", "urgency": "routine"})
    run = next(r for r in p._runs(ref, p.kind("new-concern")))
    # 02:30 UTC on the 24th is the evening of the 23rd in Halifax
    hub.db.runs.update({"_id": run["_id"]}, {"$set": {"created_at": "2026-11-24T02:30:00Z"}})
    assert p.open_requests(ref)[0]["since"] == "Monday, November 23"


def test_a_booking_is_the_done_pages_hero(hub):
    app = portal_app(hub)
    person(hub)
    b = Browser(app)
    _sign_in(b)
    p = Portal(hub, dev=True)
    later = later_slot(p, hours=48)
    page = b.go("GET", f"/book/forms")[2]
    page = b.go("POST", "/book/forms", {"_form": _field(page, "_form"), "_submit": _field(page, "_submit"),
                                        "reason": "a sick note for work", "_day": later["date"]})[2]
    status, h, _ = b.go("POST", "/book/forms", {"_form": _field(page, "_form"), "_submit": _field(page, "_submit"),
                                                "reason": "a sick note for work", "slot": later["value"], "_go": "1"})
    page = b.go("GET", h["Location"])[2]
    assert page.index('class="hero"') < page.index('class="advice calm"'), "the booking first, then the advice"
    assert html.escape(later["day"]) in page


def test_the_portals_colours_follow_the_theme():
    from practicehub.web import portal_app as pa
    assert "#9b1c1c" not in pa.PORTAL_CSS and "#fdecec" not in pa.PORTAL_CSS and "#b45309" not in pa.PORTAL_CSS
    assert ".portal .err{display:block;color:var(--danger-fg)" in pa.PORTAL_CSS
    assert "font-size:18px" in pa.PORTAL_CSS and ".portal label,.portal legend{font-size:16px" in pa.PORTAL_CSS


# ------------------------------------------------------------------------------------- a kind about one's own record
def _about(hub, when="record.clinical_status == 'active'"):
    """new-concern, about one of the person's own active conditions: the request's because_of names it."""
    pack = _pack(hub)
    pack.portal["kinds"]["new-concern"]["about"] = {"type": "Condition", "when": when, "input": "because_of"}
    hub.pack = pack
    return Portal(hub, dev=True)


def _condition(hub, ref, problem, status="active"):
    rec = hub.create_record(M(hub, "dr-amara"), "Condition", {
        "clinicalStatus": {"text": status}, "code": {"text": problem}, "subject": {"reference": ref}})
    return f"Condition/{rec['id']}"


@pytest.mark.parametrize("about,says", [
    ("Condition", "new-concern.about: a table"),
    ({"type": "Condition", "input": "because_of", "colour": "red"}, "new-concern.about.colour: not a setting"),
    ({"type": "Nothing", "input": "because_of"}, "new-concern.about.type: there is no record type 'Nothing'"),
    ({"type": "Patient", "input": "because_of"}, "new-concern.about.type: a Patient is not about a Patient"),
    ({"type": "Condition", "when": "record.clinical_status ==", "input": "because_of"}, "new-concern.about.when: "),
    ({"type": "Condition", "when": "input.kind == 'forms'", "input": "because_of"},
     "new-concern.about.when: reads input.kind; it reads the record (record.<field>) and today"),
    ({"type": "Condition", "input": "reason"}, "new-concern.about.input: the reference input of the kind's workflow"),
    ({"type": "Condition", "input": "nope"}, "new-concern.about.input: the reference input of the kind's workflow"),
    ({"type": "AllergyIntolerance", "input": "because_of"},
     "new-concern.about.input: because_of takes a Observation or Condition or MedicationRequest, not a "
     "AllergyIntolerance"),
])
def test_a_kind_about_a_record_names_a_type_about_the_person_a_condition_and_a_reference_input(hub, about, says):
    pack = _pack(hub)
    pack.portal["kinds"]["new-concern"]["about"] = about
    problems = check_config(pack)
    assert any(says in p for p in problems), problems


def test_a_kind_about_a_record_offers_only_the_persons_own_that_its_condition_holds_of(hub):
    """A kind with ``about`` lists the person's own records of its type that its ``when`` holds of now (never
    another person's), and the one chosen is checked again on sending: one not offered is refused, and with none
    offered nothing is sent. The choice reaches the workflow as the input ``about`` names."""
    p = _about(hub)
    me = person(hub)
    other = person(hub, card="2222 333 444", born="1980-05-06")
    asthma = _condition(hub, me, "Asthma")
    _condition(hub, me, "Old sprain", status="resolved")
    theirs = _condition(hub, other, "Gout")
    kind = p.kind("new-concern")
    assert [k["about"] for k in p.public()["kinds"] if k["id"] == "new-concern"] == [True]
    assert [c["ref"] for c in p.about_choices(me, kind)] == [asthma]
    answers = {"reason": "my breathing is worse", "urgency": "routine"}
    for wrong in (None, theirs, "Condition/nothing"):
        with pytest.raises(Invalid) as exc:
            p.book(me, "new-concern", None, answers, wrong)
        assert p.say("choose_about") in exc.value.problems, exc.value.problems
    got = p.book(me, "new-concern", None, answers, asthma)
    run = _run_of(hub, got)
    assert run["ctx"]["input"]["because_of"] == asthma and run["subject"] == me
    lone = person(hub, card="3333 444 555", born="1970-07-08")
    with pytest.raises(Invalid) as exc:
        p.book(lone, "new-concern", None, answers, asthma)
    assert p.say("nothing_about") in exc.value.problems, exc.value.problems


def test_the_book_form_asks_which_record_it_is_about_and_sends_the_choice(hub):
    """The book form shows the person's own records the kind is about as one choice each (chosen already when there
    is one); with none it says so and has nothing to send."""
    _about(hub)
    app = portal_app(hub)
    asthma = _condition(hub, person(hub), "Asthma")
    b = Browser(app)
    _sign_in(b)
    page = b.go("GET", "/book/new-concern")[2]
    assert html.escape(app.say("about")) in page
    assert f'name="_about" value="{asthma}" checked required' in page
    status, h, _ = b.go("POST", "/book/new-concern", {"_form": _field(page, "_form"), "_submit": _field(page, "_submit"),
                                                      "reason": "my breathing is worse", "urgency": "routine",
                                                      "_about": asthma})
    assert status == 303 and h["Location"].startswith("/done/")
    run = hub.db.runs.find_one({"workflow_id": "appointment-request", "ctx.input.because_of": asthma})
    assert run is not None
    hub.update_record(M(hub, "dr-amara"), asthma, {"clinicalStatus": {"text": "resolved"}})
    page = b.go("GET", "/book/new-concern")[2]
    assert html.escape(app.say("nothing_about")) in page and 'name="_submit"' not in page
