"""The core is practice-agnostic: an accounting practice runs on it with no medical code."""

from pathlib import Path

import pytest

from conftest import quiet, snapshot_hub
from practicehub.agents.runner import run_all_local
from practicehub.hub import Denied

PACK = str(Path(__file__).parent / "packs" / "accounting")


@pytest.fixture
def firm(monkeypatch):
    monkeypatch.setenv("PRACTICEHUB_SECRET", "test-secret")
    return snapshot_hub("accounting", PACK, lambda h: h.install(PACK))


def test_an_accounting_practice_files_a_return(firm):
    lee, kim = firm.member("lee"), firm.member("kim")
    client = firm.create_record(kim, "Client", {"name": [{"text": "Avery Boudreau"}]})
    for kind, amount in (("T4", 52000), ("T5", 310.5)):
        firm.create_record(lee, "TaxSlip", {"client": {"reference": f"Client/{client['id']}"}, "kind": kind,
                                            "amount": amount})
    run = firm.engine.start(kim, "personal-return", record=f"Client/{client['id']}", inputs={"year": 2026})
    run_all_local(firm, log=quiet)
    r = firm.engine.run(run["id"])
    assert r["steps"]["prepare"]["by"] == "prep-agent" and r["ctx"]["prepare"]["income"] == 52310.5
    # the agent's sponsor was told about its medium-risk work
    assert any(i["kind"] == "notice" for i in firm.engine.inbox(lee))
    # reception may not read tax data, so it neither sees the chart's slips nor the agent's draft
    with pytest.raises(Denied):
        firm.read_record(kim, f"TaxSlip/{firm.records.search('TaxSlip')[0]['id']}")
    review = firm.db.items.find_one({"run_id": run["id"], "step_id": "review"}, sort="n")["id"]
    firm.engine.complete(lee, review, {"income": 52310.5, "summary": "checked"})
    sign = firm.db.items.find_one({"run_id": run["id"], "step_id": "sign"}, sort="n")["id"]
    assert not firm.engine.eligibility(firm.member("prep-agent"), firm.engine.item(sign))[0]
    firm.engine.decide(lee, sign, "file", "")
    assert firm.records.search("Invoice")[0]["subject"]["reference"] == f"Client/{client['id']}"
    assert firm.chart(lee, f"Client/{client['id']}")["classes"].keys() >= {"tax", "billing"}
    assert firm.track.verify()["ok"]


def test_its_web_pages_say_clients(firm):
    from practicehub.web.server import App
    app = App(firm, dev=True)
    kim = {"cookie": "ph_as=kim"}
    code, _, body = app.handle("POST", "/clients", {**kim, "content-type": "application/x-www-form-urlencoded"},
                               b"name.0.text=Avery+Boudreau&email=a%40example.com&intake=1")
    assert code == 303
    code, _, body = app.handle("GET", "/clients", kim, b"")
    assert code == 200 and b"Avery Boudreau" in body and b"Register a client" in body
    assert b"atient" not in body  # no patients anywhere
    assert any(ev["action"] == "event.received" and ev["data"].get("type") == "client.signed-up"
               for ev in firm.track.list(limit=500))  # the pack's own registration event


# --------------------------------------------------------------------------------- the core's base types
def test_a_firm_books_a_meeting_through_the_core(firm):
    """Meeting extends the core's Booking: the core's calendar and booking.book keep it, knowing nothing of firms."""
    from practicehub.engine import act_booking_book, bookings
    kim = firm.member("kim")
    client = firm.create_record(kim, "Client", {"name": [{"text": "Avery Boudreau"}], "email": "a@example.com"})
    ref = f"Client/{client['id']}"
    assert firm.title(client) == "Avery Boudreau · a@example.com" and firm.subject_types() == {"Client"}
    assert firm.project(client) == {"name": "Avery Boudreau", "identifier": None, "contact": "a@example.com"}
    run = {"id": None, "record": ref}
    made = act_booking_book(firm.engine, run, None, {"slot": "2027-04-06T10:00|lee", "reason": "T1 review"}, kim)
    meeting = firm.records.read(*made["reference"].split("/", 1))
    assert meeting["client"] == {"reference": ref} and meeting["staff"] == ["lee"] and meeting["reason"] == "T1 review"
    [b] = bookings(firm, since="2027-04-06")
    assert b["type"] == "Meeting" and b["with"] == ["lee"] and b["subject"] == ref and not b["cancelled"]
    with pytest.raises(ValueError, match="taken in the meantime"):
        act_booking_book(firm.engine, run, None, {"slot": "2027-04-06T10:00|lee"}, kim)
    firm.update_record(kim, made["reference"], {"called_off": True})
    assert bookings(firm, since="2027-04-06") == []  # its own true/false field is the Booking's cancelled
    act_booking_book(firm.engine, run, None, {"slot": "2027-04-06T10:00|lee"}, kim)  # the time is free again


def test_registering_the_same_client_twice_is_noticed(firm):
    from practicehub.web.server import App
    app = App(firm, dev=True)
    kim = {"cookie": "ph_as=kim", "content-type": "application/x-www-form-urlencoded"}
    assert app.handle("POST", "/clients", kim, b"name.0.text=H%C3%A9l%C3%A8ne+C%C3%B4t%C3%A9")[0] == 303
    code, _, body = app.handle("POST", "/clients", kim, b"name.0.text=helene+cote")
    assert code == 200 and b"may be someone already registered" in body and len(firm.records.search("Client")) == 1
    assert app.handle("POST", "/clients", kim, b"name.0.text=helene+cote&_different=1")[0] == 303
    assert len(firm.records.search("Client")) == 2


def test_a_pack_type_keeps_the_base_contract_or_is_refused():
    from practicehub import schema
    from practicehub.workflows import DefinitionError
    head = 'id = "Visit"\ndata_class = "x"\nextends = "Booking"\n'
    for bad, says in ((head + '[base]\nstart = "when"\n[[field]]\nname = "when"\ntype = "number"\n', "start is datetime"),
                      (head + '[base]\nstart = false\n', "Booking needs it"),
                      (head + '[base]\ncolour = "x"\n', "has no field 'colour'"),
                      (head + '[base]\ncancelled = { when = "record.status ==" }\n', "base.cancelled"),
                      ('id = "Lot"\ndata_class = "x"\nextends = "Patient"\n', "not one of the core's base types"),
                      ('id = "Lot"\ndata_class = "x"\nabstract = true\n', "only the core's base types")):
        with pytest.raises(DefinitionError, match=says):
            schema.parse(bad)
    # the base fields it does not name are inherited, as the base declares them
    visit = schema.parse(head)
    assert visit.extends == "Booking" and visit.field("start")["type"] == "datetime" and visit.is_a("Booking")


def test_a_module_reference_to_subject_takes_the_practices_subjects(firm):
    """A module says target = ["Subject"] (a CAPA about whoever the work is about): any type extending it fits."""
    from practicehub import schema
    capa = schema.parse('id = "Finding"\ndata_class = "profile"\n[[field]]\nname = "about"\ntype = "reference"\n'
                        'target = ["Subject"]\n', catalog=firm)
    kim = firm.member("kim")
    client = firm.create_record(kim, "Client", {"name": [{"text": "Avery Boudreau"}]})
    slip = firm.create_record(firm.member("lee"), "TaxSlip", {"kind": "T4", "amount": 1})
    ok = {"about": {"reference": f"Client/{client['id']}"}}
    assert schema.validate(capa, ok, is_a=firm.is_a) == []
    assert schema.validate(capa, {"about": {"reference": f"TaxSlip/{slip['id']}"}}, is_a=firm.is_a)
    assert firm.concrete(["Subject", "TaxSlip"]) == ["Client", "TaxSlip"]


def test_a_practice_without_a_portal_has_none(firm):
    """``[portal]`` is a pack's choice: a firm that sets none loads, and has no portal to serve."""
    from practicehub.portal import Portal, check_config
    assert firm.pack.portal == {} and check_config(firm.pack) == []
    with pytest.raises(ValueError, match="no \\[portal\\]"):
        Portal(firm)


def test_the_core_names_no_pack_type():
    """The core reads a pack's records through its base types; its code names none of a pack's types. (Words in
    comments and docstrings may give examples; the Medplum store is a FHIR adapter, so it names FHIR's.)"""
    import ast
    from pathlib import Path
    names = {"Patient", "Appointment", "Practitioner", "Observation", "Encounter", "Client", "Meeting", "TaxSlip",
             "Visit", "patient", "patients", "appointment.book"}
    root = Path(__file__).parent.parent / "practicehub"
    found = []
    for p in root.rglob("*.py"):
        if p.name == "records.py":
            continue
        tree = ast.parse(p.read_text())
        docs = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef,
                ast.AsyncFunctionDef)) and n.body and isinstance(n.body[0], ast.Expr)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
                words = set(__import__("re").findall(r"[A-Za-z.]+", n.value))
                found += [f"{p.name}:{n.lineno} {w}" for w in words & names]
            elif isinstance(n, ast.Name) and n.id in names:
                found.append(f"{p.name}:{n.lineno} {n.id}")
    assert found == [], found


def test_every_record_the_core_reads_keeps_the_contract(firm, hub):
    """What the core reads of a pack's record (its projection) fits the base type's Avro schema, in both packs."""
    from practicehub import contract
    from practicehub.engine import act_booking_book
    kim = firm.member("kim")
    c = firm.create_record(kim, "Client", {"name": [{"text": "Avery Boudreau"}], "email": "a@example.com"})
    act_booking_book(firm.engine, {"id": None, "record": f"Client/{c['id']}"}, None,
                     {"slot": "2027-04-06T10:00|lee"}, kim)
    seen = 0
    for h in (firm, hub):
        for base in ("Subject", "Booking"):
            for t in h.of_base(base):
                for rec in h.records.search(t, limit=None):
                    assert contract.conforms(base, h.project(rec)) == [], (t, rec.get("id"))
                    seen += 1
    assert seen >= 3
    out = contract.export([firm.record_type("Meeting")])
    assert out["version"] == contract.CORE_CONTRACT and {t["name"] for t in out["types"]} == {"Subject", "Booking", "Restriction"}
    assert out["extensions"] == [{"type": "Meeting", "extends": "ca.practicehub.core.Booking",
                                  "base": {"start": {"fields": ["start"]}, "end": {"fields": ["end"]},
                                           "reason": {"fields": ["reason"]}, "subject": {"fields": ["client"]},
                                           "with": {"fields": ["staff"]}, "cancelled": {"fields": ["called_off"]},
                                           "kind": {"fields": ["kind"]}, "priority": {"fields": ["priority"]},
                                           "overbooked": {"fields": ["overbooked"]}}}]


def test_a_pack_for_another_contract_is_refused(tmp_path):
    import shutil
    from practicehub.pack import load
    d = tmp_path / "acct"
    shutil.copytree(PACK, d)
    toml = (d / "pack.toml").read_text()
    (d / "pack.toml").write_text(toml.replace('requires_core = "1"', 'requires_core = "2"'))
    with pytest.raises(ValueError, match="data contract 2; this core keeps 1"):
        load(str(d))
    (d / "pack.toml").write_text(toml.replace('requires_core = "1"', 'requires_core = "1.9"'))
    with pytest.raises(ValueError, match="needs the core's data contract 1.9"):
        load(str(d))


def test_a_reference_to_subject_is_picked_by_the_clients_title(firm):
    """A field written against the core's Subject offers the firm's clients, and takes one typed as shown."""
    from practicehub import schema
    from practicehub.web.server import App
    app = App(firm, dev=True)
    kim = firm.member("kim")
    c = firm.create_record(kim, "Client", {"name": [{"text": "Avery Boudreau"}], "email": "a@example.com"})
    h = {"cookie": "ph_as=kim", "content-type": "application/x-www-form-urlencoded"}
    code, _, page = app.handle("GET", "/records/Complaint/new", h, b"")
    assert code == 200 and b"Avery Boudreau \xc2\xb7 a@example.com" in page
    body = "about=Avery+Boudreau+%C2%B7+a%40example.com&what=Late+filing".encode()
    code, hdrs, _ = app.handle("POST", "/records/Complaint", h, body)
    assert code == 303, code
    [made] = firm.records.search("Complaint")
    assert schema.ref_of(made["about"]) == f"Client/{c['id']}" and firm.subject_of(made) == f"Client/{c['id']}"


def test_a_record_named_by_its_id_is_said_in_the_log_once_for_its_type(hub, caplog, monkeypatch):
    """A record's last-resort name is its type and id: on a screen that says something is wrong (a type with no
    title, pack code that is not running), so the log says it too, with why, and not once a page."""
    import logging
    rec = {"resourceType": "NoSuchType", "id": "abc123"}
    monkeypatch.setattr(hub.pack, "summarize", lambda rec: None)  # as when the pack's code names nothing for it
    with caplog.at_level(logging.WARNING, logger="practicehub.catalog"):
        assert hub.title(rec) == "NoSuchType abc123"
        assert hub.title({**rec, "id": "def456"}) == "NoSuchType def456"
    said = [r.getMessage() for r in caplog.records if "named by its type and id" in r.getMessage()]
    assert len(said) == 1 and said[0].startswith("NoSuchType/abc123 is named by its type and id: ")
