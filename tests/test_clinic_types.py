"""The clinic's create forms: plain inputs for what a person types, and a new record lands where its lifecycle starts."""

import urllib.parse
import threading
from http.server import ThreadingHTTPServer

import pytest

from practicehub import schema
from practicehub.hub import Hub
from practicehub.web.server import App, make_handler
from test_web import call, token

WHEN = "2026-11-20T09:30"

# type -> (who creates it, what the create screen is sent; "{patient}" is the patient's reference)
FORMS = {
    "AllergyIntolerance": ("dr-amara", {"patient": "{patient}", "code.text": "Penicillin", "type": "allergy",
                                        "criticality": "high", "reaction.0.manifestation.0.text": "Hives",
                                        "clinicalStatus.text": "active", "verificationStatus.text": "confirmed"}),
    "Encounter": ("noor", {"subject": "{patient}", "participant.0.individual": "Practitioner/dr-amara",
                           "type.0.text": "minor", "class.code": "AMB", "plannedStartDate": WHEN,
                           "reasonCode.0.text": "Sore throat"}),
    "Observation": ("dr-amara", {"subject": "{patient}", "code.text": "HbA1c", "effectiveDateTime": WHEN,
                                 "valueQuantity.value": "6.6", "valueQuantity.unit": "%",
                                 "referenceRange.0.text": "4.0-6.0 %"}),
    "MedicationRequest": ("dr-amara", {"subject": "{patient}", "medicationCodeableConcept.text": "Ramipril 5 mg",
                                       "dosageInstruction.0.text": "1 capsule by mouth once daily",
                                       "dispenseRequest.quantity.value": "90", "intent": "order"}),
    "Condition": ("dr-amara", {"subject": "{patient}", "code.text": "Essential hypertension",
                               "clinicalStatus.text": "active", "category.0.text": "problem-list-item"}),
    "Composition": ("dr-amara", {"subject": "{patient}", "title": "Phone call", "author": "Practitioner/dr-amara",
                                 "encounter": "{visit}",
                                 "section.0.text": "Called about the result."}),
    "ServiceRequest": ("dr-amara", {"subject": "{patient}", "code.text": "Cardiology consult", "intent": "order",
                                    "priority": "routine", "reasonCode.0.text": "Palpitations"}),
    "Immunization": ("noor", {"patient": "{patient}", "vaccineCode.text": "Influenza vaccine", "status": "completed",
                              "occurrenceDateTime": WHEN}),
}

# what each one's words land as in the record
LANDS = {
    "AllergyIntolerance": [("code.text", "Penicillin"), ("reaction.0.manifestation.0.text", "Hives")],
    "Encounter": [("type.0.text", "minor"), ("plannedStartDate", WHEN),
                  ("participant.0.individual", "Practitioner/dr-amara")],
    "Observation": [("code.text", "HbA1c"), ("valueQuantity.value", 6.6), ("valueQuantity.unit", "%")],
    "MedicationRequest": [("medicationCodeableConcept.text", "Ramipril 5 mg"), ("dispenseRequest.quantity.value", 90)],
    "Condition": [("code.text", "Essential hypertension"), ("clinicalStatus.text", "active")],
    "Composition": [("author.0", "Practitioner/dr-amara"), ("section.0.text", "Called about the result.")],
    "ServiceRequest": [("code.text", "Cardiology consult")],
    "Immunization": [("vaccineCode.text", "Influenza vaccine")],
}

# where a new one starts (the types without a lifecycle say their status on the form, or keep none)
STARTS = {"Encounter": "planned", "Observation": "registered",
          "MedicationRequest": "draft", "Composition": "preliminary", "ServiceRequest": "draft",
          "Immunization": "completed"}


# what owns a record of each type: it exists only inside its patient or its visit
OWNED = {"AllergyIntolerance": "Patient", "Condition": "Patient", "Immunization": "Patient",
         "MedicationStatement": "Patient", "Coverage": "Patient", "RelatedPerson": "Patient",
         "QuestionnaireResponse": "Patient", "Composition": "Encounter", "ChargeItem": "Encounter"}


@pytest.fixture
def clinic(monkeypatch):
    """The clinic pack with its team and no demo data: one patient, and a server."""
    monkeypatch.setenv("PRACTICEHUB_SECRET", "test-secret")
    monkeypatch.delenv("PRACTICEHUB_ALLOW_EXTERNAL_MODELS", raising=False)
    hub = Hub(None, pack="general-practice")
    hub.install("general-practice", demo=False)
    pat = hub.records.create("Patient", {"name": [{"given": ["Ellen"], "family": "MacLeod"}],
                                         "birthDate": "1958-03-14"})
    visit = hub.records.create("Encounter", {"status": "finished", "subject": {"reference": f"Patient/{pat['id']}"}})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(hub, dev=False)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield hub, f"http://127.0.0.1:{httpd.server_port}", f"Patient/{pat['id']}", f"Encounter/{visit['id']}"
    httpd.shutdown()


def test_every_clinic_type_says_what_it_is():
    from pathlib import Path
    for p in sorted((Path(__file__).parent.parent / "packs/general-practice/types").glob("*.toml")):
        rt = schema.parse(p.read_text())
        assert rt.summary, f"{rt.id} has no summary"
        labels = [f["label"] for f in rt.fields]
        assert len(labels) == len(set(labels)), f"{rt.id}: two fields share a label"
        owner = OWNED.get(rt.id)
        assert (rt.part_of or {}).get("target") == ([owner] if owner else None), f"{rt.id}: part_of"
        if not owner:  # a record that is not part of another links to its patient
            assert all(f["rel"] for f in rt.fields if f["type"] == "reference"), rt.id
        if rt.lifecycle:
            lc = rt.lifecycle
            assert lc["initial"] in lc["states"], f"{rt.id}: no initial state"
            assert any(lc["initial"] in t["from"] for t in lc["transition"]), f"{rt.id}: nothing leaves the start"


# The core's forms edit a reference only at the top of a record (subject, author.*), not one inside a list
# (participant.0.actor): records_pages._editable leaves those out. Until it edits them, the pickers below are missing.
CORE_GAP = "core: a form does not edit a reference inside a list (records_pages._editable)"


def _nested_ref(f) -> bool:
    return f["type"] == "reference" and "." in f["path"].removesuffix(".*")


@pytest.mark.parametrize("rtype", sorted(FORMS))
def test_a_create_form_is_plain_inputs_and_starts_the_record(clinic, rtype):
    hub, base, patient, visit = clinic
    who, sent = FORMS[rtype]
    rt = hub.record_type(rtype)
    own = ""
    if rt.part_of:  # a part is added from its owner's page, which names the owner (without it: "Which patient?")
        own = "?" + urllib.parse.urlencode({rt.field(rt.part_of["field"])["path"]: patient})
    code, page = call(base, "GET", f"/records/{rtype}/new{own}", token(hub, who))
    assert code == 200, page
    form = page.split('id="new"', 1)[1].split("</form>", 1)[0]
    assert 'class="mono"' not in form and "<small>JSON</small>" not in form  # nothing typed as JSON
    shown = [n for t in rt.screen("create") for n in t["fields"]]
    nested = {rt.field(n)["path"] for n in shown if _nested_ref(rt.field(n))}
    sent = {k: v for k, v in sent.items() if k not in nested}  # no picker for these yet (CORE_GAP)
    for name in shown:
        f = rt.field(name)
        assert not f["advanced"] and f["type"] not in ("object", "json"), f"{rtype}.{name}"
        if f["path"] not in nested:
            assert f'name="{f["path"].removesuffix(".*")}"' in form, f"{rtype}: {name} is not on the form"
    for key in sent:
        assert f'name="{key}"' in form, f"{rtype}: {key} is not on the form"
    if rt.lifecycle:
        assert f"It starts as <b>{rt.lifecycle['initial']}</b>" in form

    data = {k: v.replace("{patient}", patient).replace("{visit}", visit) for k, v in sent.items()}
    code, body = call(base, "POST", f"/records/{rtype}", token(hub, who), form=data)
    assert code in (200, 303), body
    recs = [r for r in hub.records.search(rtype) if f"{rtype}/{r['id']}" != visit]
    assert len(recs) == 1, body
    rec = recs[0]
    assert patient in str(rec)
    for path, want in LANDS[rtype]:
        if any(path.startswith(q) for q in nested):
            continue
        assert schema.first(rec, path) == want, f"{rtype}.{path}: {schema.first(rec, path)!r}"
    if rtype in STARTS:
        assert rec["status"] == STARTS[rtype]


NESTED = [("Encounter", "practitioner"),
          ("Immunization", "given_by"), ("MedicationRequest", "priorPrescription")]


@pytest.mark.xfail(strict=True, reason=CORE_GAP)
@pytest.mark.parametrize("rtype,name", NESTED)
def test_a_reference_inside_a_list_is_a_picker(clinic, rtype, name):
    hub, base, patient, visit = clinic
    who = FORMS[rtype][0]
    f = hub.record_type(rtype).field(name)
    assert _nested_ref(f)
    form = call(base, "GET", f"/records/{rtype}/new", token(hub, who))[1]
    assert f'<select name="{f["path"]}"' in form or f'<input name="{f["path"]}"' in form


def test_a_person_starts_a_record_where_its_lifecycle_starts(clinic):
    hub, base, patient, visit = clinic
    from practicehub.hub import Invalid
    with pytest.raises(Invalid, match="starts as 'registered'"):
        hub.create_record(hub.member("dr-amara"), "Observation",
                          {"status": "amended", "subject": {"reference": patient}, "code": {"text": "HbA1c"}})


def test_a_nurse_checks_a_patient_in(clinic):
    hub, base, patient, visit = clinic
    noor = hub.member("noor")
    visit = hub.create_record(noor, "Encounter", {"subject": {"reference": patient}, "plannedStartDate": WHEN})
    ref = f"Encounter/{visit['id']}"
    for st in ("arrived", "triaged", "in-progress", "finished"):
        hub.update_record(noor, ref, {"status": st})
    assert hub.records.read("Encounter", visit["id"])["status"] == "finished"
