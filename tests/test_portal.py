"""The booking portal for the people a practice serves (``practicehub/portal.py``, ``practicehub/web/portal_app.py``):
its ``[portal]`` checked at pack load, sign-in by details on file and a one-time code, slots, booking through workflows,
the person's own view, and that the core names nothing of a pack's."""

import copy
import dataclasses
import html
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import M
from practicehub import portal as portal_mod
from practicehub.hub import Denied, Invalid
from practicehub.portal import AlreadyOpen, Clash, Portal, check_config

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


# ------------------------------------------------------------------------------------------- [portal] is checked
def _pack(hub):
    return dataclasses.replace(hub.pack, portal=copy.deepcopy(hub.pack.portal))


def test_the_clinic_portal_is_valid(hub):
    assert check_config(hub.pack) == []
    p = Portal(hub)
    assert set(p.kinds()) == {"new-concern", "forms"}
    assert p.kind("new-concern")["workflow"] == "appointment-request"
    assert p.kind("forms")["workflow"] == "portal-booking"


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
    (lambda c: c["code"].update(minutes=600), "minutes"),
    (lambda c: c["kinds"]["forms"].update(cancel_workflow="appointment-request"), "cancel_workflow"),
])
def test_a_bad_portal_is_refused_with_a_clear_problem(hub, change, says):
    pack = _pack(hub)
    change(pack.portal)
    problems = check_config(pack)
    assert problems and any(says in p for p in problems), problems


def test_a_bad_portal_stops_the_pack_loading(tmp_path):
    import shutil
    from practicehub.pack import load as load_pack
    dst = tmp_path / "general-practice"
    shutil.copytree(ROOT / "packs" / "general-practice", dst)
    toml = (dst / "pack.toml").read_text()
    (dst / "pack.toml").write_text(toml.replace('roles = ["physician"]', 'roles = ["astronaut"]', 1))
    with pytest.raises(ValueError, match=r"\[portal\].*astronaut"):
        load_pack(dst)


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
    assert got["dev_code"] not in hub.db.outbox.get(rows[0]["id"])["body"], "a used code is taken out of the outbox"
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


def test_a_code_expires(hub):
    p = Portal(hub, dev=True)
    person(hub)
    got = p.begin_sign_in(BORN, CARD, client="10.0.1.1")
    cid = portal_mod._sha(got["challenge"])
    hub.db.portal_codes.set(cid, {"expires_at": portal_mod._stamp(datetime.now(timezone.utc) - timedelta(seconds=1))})
    with pytest.raises(Invalid):
        p.finish_sign_in(got["challenge"], got["dev_code"], client="10.0.1.1")
    p.begin_sign_in(BORN, CARD, client="10.0.1.2")  # the sweep takes the expired code out of the outbox
    assert got["dev_code"] not in hub.db.outbox.get(code_rows(hub)[0]["id"])["body"]


def test_a_code_is_used_once(hub):
    p = Portal(hub, dev=True)
    person(hub)
    got = p.begin_sign_in(BORN, CARD, client="10.0.1.3")
    p.finish_sign_in(got["challenge"], got["dev_code"], client="10.0.1.3")
    with pytest.raises(Invalid):
        p.finish_sign_in(got["challenge"], got["dev_code"], client="10.0.1.3")


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


def test_booking_forms_books_through_its_workflow(hub):
    p, ref, token = signed_in(hub)
    slot = p.free_slots("forms")[0]
    got = p.book(ref, "forms", slot["value"], {"reason": "a school form"})
    run = hub.engine.run(got["run"])
    assert run["workflow_id"] == "portal-booking" and run["record"] == ref
    assert got["when"] and not got["failed"]
    assert slot["value"] not in {s["value"] for s in p.free_slots("forms", limit=10000)}, "the time is taken"
    mine = p.upcoming(ref)
    assert len(mine) == 1 and mine[0]["kind"] == p.kind("forms")["label"]
    assert "school form" not in str(mine), "the reason stays with the practice"
    rt = hub.record_type(mine[0]["ref"].split("/")[0])
    assert mine[0]["state"] == rt.state_label("planned"), "the state is the type's lifecycle's word"


def test_a_new_concern_is_a_request_through_appointment_request(hub):
    p, ref, token = signed_in(hub)
    got = p.book(ref, "new-concern", None, {"reason": "a rash on my arm for a week"})
    run = hub.engine.run(got["run"])
    assert run["workflow_id"] == "appointment-request" and run["record"] == ref
    assert run["ctx"]["input"]["kind"] == "new-concern"
    assert got["mode"] == "request" and got["advice"], "the screen's say-now advice is shown"
    assert [r["what"] for r in p.open_requests(ref)] == [p.kind("new-concern")["label"]]


def test_a_red_flag_is_told_loudly(hub):
    p, ref, token = signed_in(hub)
    got = p.book(ref, "new-concern", None, {"reason": "crushing chest pain and I can't breathe"})
    assert got["alarm"] is True and got["advice"]


def test_a_taken_time_is_a_clash_with_fresh_times(hub):
    p, ref, token = signed_in(hub)
    slot = p.free_slots("forms")[0]["value"]
    other = person(hub, card="2222 333 444", born="1980-05-06")
    p.book(other, "forms", slot, {})
    with pytest.raises(Clash) as clash:
        p.book(ref, "forms", slot, {})
    assert clash.value.slots and slot not in {s["value"] for s in clash.value.slots}


def test_a_time_must_be_chosen_to_book(hub):
    p, ref, token = signed_in(hub)
    with pytest.raises(Invalid):
        p.book(ref, "forms", "", {})


def test_one_open_request_and_no_details_of_it(hub):
    p, ref, token = signed_in(hub)
    p.book(ref, "new-concern", None, {"reason": "my knee hurts"})
    with pytest.raises(AlreadyOpen):
        p.book(ref, "new-concern", None, {"reason": "and my elbow"})
    assert "open" in p.say("duplicate")


def test_a_booking_is_cancelled_through_its_workflow(hub):
    p, ref, token = signed_in(hub)
    slots = p.free_slots("forms", limit=10000)
    later = next(s for s in slots if datetime.fromisoformat(s["value"].split("|")[0]) - hub.engine.now()
                 > timedelta(hours=30))
    p.book(ref, "forms", later["value"], {})
    b = p.upcoming(ref)[0]
    assert b["can_cancel"]
    p.cancel(ref, b["ref"])
    assert p.upcoming(ref) == []
    rec = hub.records.read(*b["ref"].split("/", 1))
    assert rec["status"] == "cancelled"
    assert any(r["workflow_id"] == "portal-cancel" for r in hub.engine.runs(record=b["ref"]))
    assert later["value"] in {s["value"] for s in p.free_slots("forms", limit=10000)}, "the time is free again"


def test_cancel_only_ones_own_and_only_far_enough_ahead(hub):
    p, ref, token = signed_in(hub)
    soon = next(s for s in p.free_slots("forms", limit=10000)
                if datetime.fromisoformat(s["value"].split("|")[0]) - hub.engine.now() < timedelta(hours=24))
    p.book(ref, "forms", soon["value"], {})
    b = p.upcoming(ref)[0]
    assert not b["can_cancel"]
    with pytest.raises(Denied):
        p.cancel(ref, b["ref"])
    other = person(hub, card="2222 333 444", born="1980-05-06")
    with pytest.raises(Denied):
        p.cancel(other, b["ref"])


# ------------------------------------------------------------------------------------------- someone new
def test_someone_new_starts_the_intake(hub):
    p = Portal(hub)
    run = p.register({"given": "Bea", "family": "Moss", "birthDate": "2001-07-08", "health_card": "7777 888 999",
                      "phone": "902-555-0199"}, client="10.0.2.1")
    assert run["workflow_id"] == "new-patient-intake"
    rec = hub.records.read(*run["record"].split("/", 1))
    assert rec["birthDate"] == "2001-07-08" and rec["identifier"][0]["value"] == "7777 888 999"
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


# ------------------------------------------------------------------------------------------- the pages
class Browser:
    def __init__(self, app, client="10.0.3.1"):
        self.app, self.jar, self.client = app, {}, client

    def _cookies(self, h):
        for c in h.get("Set-Cookie", []) if isinstance(h.get("Set-Cookie"), list) else [h.get("Set-Cookie")] \
                if h.get("Set-Cookie") else []:
            name, _, rest = c.partition("=")
            value = rest.split(";")[0]
            if "Max-Age=0" in c:
                self.jar.pop(name, None)
            else:
                self.jar[name] = value

    def go(self, method, path, form=None, origin=None):
        headers = {"host": "portal.test", "x-practicehub-client": self.client,
                   "cookie": "; ".join(f"{k}={v}" for k, v in self.jar.items())}
        if origin:
            headers["origin"] = origin
        body = urllib.parse.urlencode(form or {}).encode()
        status, h, page = self.app.handle(method, path, headers, body)
        self._cookies(h)
        return status, h, page.decode()


def test_the_pages_walk_from_sign_in_to_a_booking(hub):
    from practicehub.web.portal_app import PortalApp
    app = PortalApp(hub, dev=True)
    person(hub)
    b = Browser(app)
    status, h, page = b.go("GET", "/")
    assert status == 200 and html.escape(app.portal.cfg["privacy"]) in page and "frame-ancestors 'none'" in \
        h["Content-Security-Policy"]
    assert b.go("GET", "/me")[1]["Location"] == "/sign-in"
    status, h, _ = b.go("POST", "/sign-in", {"born": BORN, "identifier": CARD}, origin="http://portal.test")
    assert status == 303 and "php_signin" in b.jar and "Secure" not in str(h["Set-Cookie"])
    code = urllib.parse.parse_qs(urllib.parse.urlsplit(h["Location"]).query)["dev"][0]
    status, _, page = b.go("GET", h["Location"])
    assert "Dev only" in page and code in page
    status, h, _ = b.go("POST", "/code", {"code": code})
    assert status == 303 and h["Location"] == "/me" and "php_session" in b.jar
    status, _, page = b.go("GET", "/me")
    assert status == 200 and "Nothing booked" in page
    form = re.search(r'name="_form" value="([^"]+)"', page).group(1)
    status, _, page = b.go("GET", "/book/forms")
    slot = re.search(r'name="slot" value="([^"]+)"', page).group(1)
    status, _, page = b.go("POST", "/book/forms", {"_form": form, "slot": slot, "reason": "a form"})
    assert status == 200 and "Thank you" in page
    status, _, page = b.go("POST", "/book/forms", {"_form": form, "slot": slot})
    assert status == 409 and html.escape(app.portal.say("clash")) in page
    status, _, page = b.go("POST", "/book/new-concern", {"_form": form, "reason": "chest pain, can't breathe"})
    assert status == 200 and 'class="advice alarm"' in page
    status, _, page = b.go("POST", "/book/new-concern", {"_form": form, "reason": "again"})
    assert status == 409 and html.escape(app.portal.say("duplicate")) in page
    assert b.go("POST", "/book/forms", {"_form": "forged", "slot": slot})[0] == 403
    assert b.go("POST", "/sign-out", {"_form": form}, origin="https://evil.example")[0] == 403
    assert b.go("POST", "/sign-out", {"_form": form})[0] == 303
    assert b.go("GET", "/me")[1]["Location"] == "/sign-in"


def test_a_wrong_code_shows_the_form_again(hub):
    from practicehub.web.portal_app import PortalApp
    app = PortalApp(hub)
    person(hub)
    b = Browser(app)
    status, h, _ = b.go("POST", "/sign-in", {"born": BORN, "identifier": CARD})
    assert h["Location"] == "/code", "a real portal never puts the code in the page"
    assert "Secure" in str(h["Set-Cookie"])
    status, _, page = b.go("POST", "/code", {"code": "000000"})
    assert status == 422 and html.escape(app.portal.say("no_match")) in page


def test_the_portal_does_not_take_the_staff_sign_in(hub):
    from practicehub.web.portal_app import PortalApp
    b = Browser(PortalApp(hub, dev=True))
    b.jar = {"ph_session": "anything", "ph_as": "dr-amara"}
    assert b.go("GET", "/me")[1]["Location"] == "/sign-in"


def test_portal_serve_refuses_the_reserved_ports(hub):
    from practicehub.web.portal_app import serve
    for port in (8080, 9980, 11434):
        with pytest.raises(SystemExit):
            serve(hub, "127.0.0.1", port)


# ------------------------------------------------------------------------------------------- the core stays general
def test_the_portal_core_names_nothing_of_a_pack():
    words = re.compile(r"\b(patients?|Patient|appointments?|Appointment|Encounter|Practitioner|clinic|physician|"
                       r"health.card|new-concern|appointment-request)\b")
    for f in (ROOT / "practicehub" / "portal.py", ROOT / "practicehub" / "web" / "portal_app.py"):
        found = words.findall(f.read_text())
        assert not found, f"{f.name} names {sorted(set(found))}"
