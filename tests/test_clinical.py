"""Clinical checks for the general-practice pack: record types, summaries, lab review, renewal, notes, facts."""
from pathlib import Path

import pytest

from practicehub import schema
from tests.conftest import M, item_for, quiet, run_all_local, run_of

def read(hub, ref):
    rtype, rid = ref.split("/")
    return hub.records.read(rtype, rid)


PACK = Path(__file__).resolve().parent.parent / "packs" / "general-practice"


@pytest.fixture(scope="module")
def types():
    return {p.stem: schema.parse(p.read_text()) for p in sorted((PACK / "types").glob("*.toml"))}


def test_every_type_parses_and_matches_its_file(types):
    assert len(types) == 18  # Appointment became the Visit (pack version 2); a patient's Consent directive
    for name, rt in types.items():
        assert rt.id == name
    assert [rt.id for rt in types.values() if rt.subject] == ["Patient"]


def test_demo_records_validate(worked, types):
    for name, rt in types.items():
        for rec in worked.records.search(name):
            assert schema.validate(rt, rec) == [], (name, rec)


def test_patient_with_junk_key_is_rejected(types):
    ok = {"name": [{"family": "Test", "given": ["A"]}], "birthDate": "1980-01-01"}
    assert schema.validate(types["Patient"], ok) == []
    assert schema.validate(types["Patient"], {**ok, "junk": "x"})


def test_allergy_summary_is_readable(hub):
    (allergy,) = hub.records.search("AllergyIntolerance")
    text = hub.pack.summarize(allergy)
    assert "Penicillin" in text and "Hives" in text and "high" in text
    assert "{" not in text and "coding" not in text


def test_a_result_summary_never_hides_one_value_behind_another(hub):
    """A lab can send a number and a text that says something else; the summary shows both and says they differ."""
    obs = {"resourceType": "Observation", "id": "x", "status": "final", "code": {"text": "Potassium"},
           "valueQuantity": {"value": 7.1, "unit": "mmol/L"}, "valueString": "haemolysed sample, repeat"}
    text = hub.pack.summarize(obs)
    assert "7.1" in text and "haemolysed" in text and "differ" in text
    assert "differ" not in hub.pack.summarize({**obs, "valueString": ""})


def test_abnormal_result_cannot_be_filed_as_normal(worked):
    run = run_of(worked, "lab-result-review")
    with pytest.raises(AssertionError):
        item_for(worked, run, "review")          # the file-normal gate is not offered
    amara = M(worked, "dr-amara")
    worked.engine.decide(amara, item_for(worked, run, "review-flagged"), "no-action-needed", "recheck in 3 months")
    run_all_local(worked, log=quiet)
    bodies = [r["body"] for r in worked.db.outbox.find({"channel": "patient-portal"}, sort="id")]
    result_msgs = [b for b in bodies if "result" in b]
    assert result_msgs and not any("normal" in b.lower() for b in result_msgs)
    ob = read(worked, worked.engine.run(run["id"])["ctx"]["input"]["observation"])
    assert ob["status"] == "final"
    assert any(e.get("valueReference") == {"reference": "Practitioner/dr-amara"} for e in ob["extension"])


def test_renewal_has_authored_on_and_dispense_request(worked):
    run = run_of(worked, "prescription-renewal")
    worked.engine.decide(M(worked, "dr-amara"), item_for(worked, run, "decide"), "renew", "")
    run_all_local(worked, log=quiet)
    orders = worked.records.search("MedicationRequest")
    new = [o for o in orders if o.get("priorPrescription")]
    assert len(new) == 1
    assert new[0]["authoredOn"] and new[0]["dispenseRequest"]["quantity"]["value"]
    prior = read(worked, new[0]["priorPrescription"]["reference"])
    assert prior["status"] == "completed"
    fax = worked.db.outbox.find_one({"channel": "fax"}, sort="id")["body"]
    assert "Ellen MacLeod" in fax and "1958-03-14" in fax and "1234 567 890" in fax


def test_signed_note_keeps_follow_up(worked):
    run = run_of(worked, "visit-documentation")
    amara = M(worked, "dr-amara")
    item = item_for(worked, run, "edit")
    draft = worked.engine.context(amara, item)["draft"]
    assert draft.get("follow_up")
    fields = {k: draft.get(k) for k in ("subjective", "objective", "assessment", "plan", "follow_up")}
    worked.engine.complete(amara, item, {**fields, "diagnosis_codes": ["034"]})
    worked.engine.decide(amara, item_for(worked, run, "sign"), "sign", "")
    run_all_local(worked, log=quiet)
    (note,) = worked.records.search("Composition")
    sections = {s["title"]: s["text"] for s in note["section"]}
    assert sections["Follow-up"] == draft["follow_up"]
    booked = worked.engine.run(run["id"])["ctx"]["input"]["appointment"]  # the visit was booked: documenting finishes it
    visit = worked.records.read(*booked.split("/", 1))
    assert visit["status"] == "finished" and visit["plannedStartDate"]
    assert visit["period"]["start"] and visit["diagnosis"][0]["condition"][0]["concept"]["text"] == "ICD-9 034"
    assert visit["reasonCode"] == [{"text": "Sore throat"}]  # the booked reason is kept apart from what was billed
    assert note["encounter"]["reference"] == booked


def test_a_filed_note_carries_the_physicians_signature_and_is_locked(worked):
    from practicehub.hub import Denied, Invalid
    run = run_of(worked, "visit-documentation")
    amara = M(worked, "dr-amara")
    item = item_for(worked, run, "edit")
    draft = worked.engine.context(amara, item)["draft"]
    fields = {k: draft.get(k) for k in ("subjective", "objective", "assessment", "plan", "follow_up")}
    worked.engine.complete(amara, item, {**fields, "diagnosis_codes": ["034"]})
    worked.engine.decide(amara, item_for(worked, run, "sign"), "sign", "")
    run_all_local(worked, log=quiet)
    (note,) = worked.records.search("Composition")
    ref = f"Composition/{note['id']}"
    sigs = worked.signatures(amara, ref)
    assert len(sigs) == 1 and sigs[0]["by"] == "dr-amara" and sigs[0]["valid"] and sigs[0]["current"]
    assert note["attester"][0]["party"]["reference"] == "Practitioner/dr-amara"
    with pytest.raises((Denied, Invalid)):
        worked.update_record(amara, ref, {"title": "Changed after signing"})
    from practicehub.web.server import App
    page = App(worked, dev=False).page_record({"me": amara, "query": {}, "path": "/records/" + ref}, "Composition",
                                              note["id"])
    assert "Subjective" in page and "&quot;title&quot;" not in page and '"title"' not in page


def test_facts_include_age_address_and_emergency_contact(hub):
    pat = [p for p in hub.records.search("Patient") if p["name"][0]["family"] == "MacLeod"][0]
    facts = hub.pack.facts(pat)
    assert isinstance(facts["age"], int) and facts["age"] >= 68
    assert "Halifax" in facts["address"] and "Fiona" in facts["emergency contact"]


# ------------------------------------------------------------------------------- safety checks are code
def _pat(hub):
    return f"Patient/{hub.records.search('Patient')[0]['id']}"


def _outbox(hub, run):
    return [r["body"] for r in hub.db.outbox.find({"run_id": run["id"]}, sort="id")]


def test_a_red_flag_message_goes_straight_to_a_nurse(hub):
    run = hub.engine.start(M(hub, "jo"), "patient-question", record=_pat(hub),
                           inputs={"question": "I have had crushing chest pain since this morning, is that ok?"})
    run = hub.engine.run(run["id"])
    assert run["ctx"]["screen"]["red_flag"] is True and "chest pain" in run["ctx"]["screen"]["flags"]
    assert run["steps"]["draft"]["state"] == "skipped"  # no agent drafts a reply to an emergency
    assert any("911" in b for b in _outbox(hub, run))
    hub.engine.complete(M(hub, "noor"), item_for(hub, run, "urgent"),
                        {"reached": True, "action": "sent-to-emergency", "notes": "Called, advised 911."})
    assert hub.engine.run(run["id"])["status"] == "completed"


def test_an_ordinary_message_is_drafted_and_a_declined_draft_reaches_a_nurse(hub):
    run = hub.engine.start(M(hub, "jo"), "patient-question", record=_pat(hub),
                           inputs={"question": "What are your hours on Saturday?"})
    assert hub.engine.run(run["id"])["ctx"]["screen"]["red_flag"] is False
    hub.engine.complete(M(hub, "clinical-admin-agent"), item_for(hub, run, "draft"),
                        {"reply": "We are open 9 to 12 on Saturdays.", "needs_clinician": False})
    hub.engine.decide(M(hub, "jo"), item_for(hub, run, "review"), "needs-a-clinician", "check with a nurse")
    hub.engine.complete(M(hub, "noor"), item_for(hub, run, "clinician"), {"answer": "Saturday 9 to 12. See you then."})
    run = hub.engine.run(run["id"])
    assert run["status"] == "completed" and "Saturday 9 to 12. See you then." in _outbox(hub, run)


def test_a_lab_value_out_of_range_is_never_filed_as_normal(hub):
    pat = _pat(hub)
    run = hub.engine.start(M(hub, "lab-feed"), "lab-result-review", record=pat, inputs={
        "test": "Potassium", "value": "6.8", "unit": "mmol/L", "range": "3.5-5.0", "flag": "normal"})
    run = hub.engine.run(run["id"])
    assert run["ctx"]["check"]["band"] == "critical"
    assert run["steps"]["page"]["state"] == "done"  # paged though the lab said normal
    # critical: straight to an urgent physician review, never waiting on an agent's sort
    assert run["steps"]["sort"]["state"] == "skipped"
    assert run["steps"]["review"]["state"] == "skipped" and run["steps"]["review-flagged"]["state"] == "skipped"
    item = hub.engine.item(item_for(hub, run, "review-critical"))
    assert item["priority"] == "urgent" and item["role"] == "physician"


def test_a_patient_nobody_could_reach_goes_to_a_physician(hub):
    pat = _pat(hub)
    run = hub.engine.start(M(hub, "jo"), "appointment-request", record=pat,
                           inputs={"reason": "I can barely breathe since this morning"})
    run = hub.engine.run(run["id"])
    assert run["steps"]["safety-net"]["state"] == "done"  # told to call 911, though nobody marked it urgent
    hub.engine.complete(M(hub, "noor"), item_for(hub, run, "nurse-call"),
                        {"reached": False, "advice": "no answer, left a voicemail"})
    run = hub.engine.run(run["id"])
    item = hub.engine.item(item_for(hub, run, "not-reached"))
    assert item["role"] == "physician" and item["priority"] == "urgent"


def test_the_range_check_reads_values_and_ranges(hub):
    from practicehub import sandbox
    blk, _ = hub.block("lab-range-check")
    band = lambda **i: sandbox.run_code(blk.code, {"unit": "", "range": "", **i})["output"]["band"]  # noqa: E731
    assert band(test="Sodium", value="139", unit="mmol/L", range="135 - 145") == "normal"
    assert band(test="Sodium", value="131", unit="mmol/L", range="135-145") == "abnormal"
    assert band(test="Sodium", value="118", unit="mmol/L", range="135-145") == "critical"
    assert band(test="eGFR", value=">90", range=">60") == "normal"
    assert band(test="TSH", value="pending", range="0.4-4.0") == "unknown"
    assert band(test="TSH", value="2.1") == "unknown"  # no range: not normal


@pytest.mark.parametrize("test,value,unit,rng,want", [
    ("K", "7.1", "mEq/L", "3.5-5.0", "critical"),
    ("Potassium", "4.2", "mEq/L", "3.5-5.0", "normal"),
    ("Na", "118", "mEq/L", "", "critical"),
    ("Glucose", "40", "mg/dL", "70-99", "critical"),
    ("Glucose", "95", "mg/dL", "70-99", "normal"),
    ("Glucose", "600", "mg/dL", "", "critical"),
    ("Glucose", "5.5", "", "3.9-6.1", "normal"),       # no unit, but the range says mmol/L
    ("Glucose", "45", "", "70-99", "critical"),         # no unit, the range says mg/dL
    ("Hb", "6.2", "g/dL", "12-16", "critical"),
    ("Haemoglobin", "135", "g/L", "120-160", "normal"),
    ("Hemoglobin", "6.2", "", "", "critical"),           # no unit, no range: critical in some unit is critical
    ("Calcium", "14.5", "mg/dL", "8.5-10.5", "critical"),
    ("Calcium", "9.4", "mg/dL", "8.5-10.5", "normal"),
    ("Platelets", "15", "K/uL", "150-400", "critical"),
    ("Platelets", "12,000", "/uL", "150,000-400,000", "critical"),
    ("Platelets", "250", "x10^9/L", "150-400", "normal"),
    ("WBC", "1,200", "/mm3", "", "abnormal"),          # unread range would be unknown; below: thousands read
    ("hs-Troponin I", "250", "ng/L", "<26", "critical"),
    ("Troponin T", "0.5", "ng/mL", "<0.014", "critical"),
    ("Troponin I", "12", "ng/L", "<26", "normal"),
    ("CA 125", "40", "U/mL", "<35", "abnormal"),       # not calcium
    ("INR", "6.1", "", "2-3", "critical"),
    ("Hemoglobin A1c", "6.5", "%", "4.0-5.6", "abnormal"),   # not haemoglobin: 6.5 is no critical anaemia
    ("HbA1c", "48", "mmol/mol", "20-42", "abnormal"),
    ("MCH", "25", "pg", "27-33", "abnormal"),
    ("Mean corpuscular hemoglobin concentration", "300", "g/L", "320-360", "abnormal"),
    ("Glucose, urine", "28", "mmol/L", "<0.8", "abnormal"),  # a blood limit means nothing for urine
    ("Potassium, urine 24h", "120", "mmol/d", "25-125", "normal"),
    ("White cells, urine", "60", "/hpf", "0-5", "abnormal"),
    ("Calcium, ionized", "1.20", "mmol/L", "1.15-1.30", "normal"),  # not below the total calcium limit 1.75
    ("Ionized calcium", "0.7", "mmol/L", "1.15-1.30", "critical"),
    ("Troponin I", ">100", "ng/L", "<26", "critical"),        # over the limit, as the analyser writes it
    ("Troponin I", "<5", "ng/L", "<26", "normal"),
])
def test_critical_limits_hold_in_any_unit(test, value, unit, rng, want):
    from practicehub import sandbox
    import tomllib
    code = tomllib.loads((PACK / "blocks" / "lab-range-check.toml").read_text())["code"]
    out = sandbox.run_code(code, {"test": test, "value": value, "unit": unit, "range": rng})["output"]
    if want == "abnormal" and not rng:
        assert out["band"] == "unknown", out
    else:
        assert out["band"] == want, (test, value, unit, out)


def _screen():
    import tomllib
    ns = {}
    exec(tomllib.loads((PACK / "blocks" / "patient-message-screen.toml").read_text())["code"], ns)  # the pack's own code
    return ns["run"]


@pytest.mark.parametrize("message", [
    "my chest feels heavy since lunch", "tight chest and my left arm is numb", "chest pressure on and off",
    "I took 30 of my pills", "I took a bottle of tylenol", "i just want to die", "thinking about ending things",
    "no point in living anymore", "I'm not suicidal but I want to die", "his lips are turning blue",
    "my 6 week old has a temperature of 38.2", "fever of 38.5 in my 3 week old baby",
    "my throat feels tight after the shrimp", "no, i can't breathe", "I don't have chest pain but I can't breathe",
    "no fever but chest pain", "temperature is 40.2 since yesterday",
    "my dad is having trouble speaking", "her face looks funny on one side", "it's hard to talk all of a sudden",
    "I can't feel my face", "mom is not making sense this morning",
])
def test_the_red_flag_screen_catches(message):
    assert _screen()({"question": message})["red_flag"] is True, message


@pytest.mark.parametrize("message", [
    "What are your hours on Saturday?", "need a refill of my blood pressure pills",
    "can you renew my pills? I took 2 tablets this morning", "my 2 year old has a fever of 38",
])
def test_the_red_flag_screen_lets_ordinary_messages_through(message):
    r = _screen()({"question": message})
    assert r["red_flag"] is False and r["unsure"] is False, message


def test_a_denied_warning_sign_goes_to_a_nurse_not_an_agent(hub):
    run = hub.engine.start(M(hub, "jo"), "patient-question", record=_pat(hub),
                           inputs={"question": "No chest pain, just a cough for two weeks. Can I come in?"})
    run = hub.engine.run(run["id"])
    assert run["ctx"]["screen"]["red_flag"] is False and run["ctx"]["screen"]["unsure"] is True
    assert run["steps"]["draft"]["state"] == "skipped" and run["steps"]["clinician"]["state"] == "active"
    sent = _outbox(hub, run)
    assert any("not read around the clock" in b for b in sent)  # every patient hears what to do in an emergency
    assert not any("call you as soon as possible" in b for b in sent)
    assert hub.engine.item(item_for(hub, run, "clinician"))["due_at"]  # a nurse answers within hours
    hub.engine.complete(M(hub, "noor"), item_for(hub, run, "clinician"), {"answer": "Yes, we have booked you Tuesday."})
    assert hub.engine.run(run["id"])["status"] == "completed"


@pytest.mark.parametrize("message", [
    "My chest has been hurting since last night and it goes into my jaw",
    "there's pressure in my chest when I climb stairs", "I have chest discomfort",
    "my heart is racing and I feel like I will pass out", "I took my mom's pills, maybe 30 of them",
    "I don't see the point anymore. Can you refill my sertraline?", "it would be easier if I wasn't around",
    "my husband's speech is garbled and his face looks lopsided", "I can't catch my breath walking to the car",
    "I'm wheezing and my puffer isn't helping", "my tongue feels thick and my throat is itchy after amoxicillin",
    "my 2 month old feels warm and is floppy", "I'm 34 weeks and baby hasn't moved since yesterday",
    "I'm pregnant and spotting", "my stool is black", "there's blood in the toilet after I poop",
    "there's a lot of blood after my tooth extraction and it won't stop",
    "my neck is stiff and I have a rash that doesn't fade with a glass", "my sugar is 1.9 and I'm shaky",
    "my daughter swallowed a button battery", "my son drank some bleach",
    "I fell and hit my head, I'm on warfarin, and I've been vomiting", "my testicle suddenly really hurts",
    "I can't feel my legs and I've lost control of my bladder", "My husband hit me again and I'm scared to go home",
    "I keep hearing voices telling me to hurt my baby", "I've never had chest pain like this before",
    "Without warning I got crushing chest pain", "I've never had a seizure before but I just had one",
    "no one is home and I can't breathe", "No fever but my 3 week old is very sleepy and won't feed",
])
def test_the_screen_catches_what_a_physician_tried(message):
    assert _screen()({"question": message})["red_flag"] is True, message


def test_every_patient_message_gets_the_emergency_advice():
    for q in ("Is the clinic open on Saturday?", "crushing chest pain"):
        assert "911" in _screen()({"question": q})["advice"]


@pytest.mark.parametrize("message", [
    "I keep thinking about killing myself", "I have a plan to hang myself", "I want to jump off the bridge",
    "nobody would miss me", "I can't go on like this", "I've been cutting my arms again", "my husband isn't breathing",
    "I can barely breathe", "I'm out of breath just sitting here", "it hurts to breathe", "my lips are swollen",
    "my face is swollen after the antibiotic", "the baby looks blue", "my son had a fit",
    "she's not waking up properly", "dad is confused and very drowsy", "my left side is weak and I can't talk properly",
    "suddenly I can't see out of my left eye", "my 3-week-old feels hot", "my heart is going 180",
    "I feel like I'm dying"])
def test_the_screen_flags_what_the_physician_found_it_missed(message):
    assert _screen()({"question": message})["red_flag"], message


MISSED_LAST_ROUND = [
    "I'm going to take all my pills tonight", "I have a rope ready", "I've written goodbye letters to my kids",
    "Life isn't worth living", "I took 8 of my oxycodone", "I drank a bottle of vodka and took some pills",
    "crushing feeling behind my breastbone", "it feels like an elephant is sitting on me",
    "I'm sweating and my jaw aches and I feel sick", "I can't get enough air", "I can only say a few words at a time",
    "my baby is breathing really fast and sucking in under her ribs", "mum's face dropped on one side",
    "my wife suddenly can't find her words", "I can't move my right arm", "my husband isn't responding",
    "my wife can't stop shaking and is burning up", "my stomach pain is unbearable",
    "my child swallowed some of my blood pressure pills", "my kid got into the dishwasher pods",
    "I'm afraid I'll hurt my baby", "I feel unsafe at home", "my sugar reading says HI",
    "I haven't had chest pain like this before", "the pills are not helping my chest pain",
    "Isn't crushing chest pain normal after a cold?", "No longer able to breathe properly"]


@pytest.mark.parametrize("text", MISSED_LAST_ROUND)
def test_more_ways_of_saying_an_emergency_are_red_flags(text):
    assert _screen()({"question": text})["red_flag"], text


@pytest.mark.parametrize("text,routine", [
    ("Can I move my appointment to Friday?", True), ("Please renew my metformin prescription", True),
    ("What are your hours on Saturday?", True), ("My knee hurts when I run", False),
    ("my dad seems a bit off today, can you call?", False), ("I have no chest pain but I cough", False)])
def test_only_plain_paperwork_may_get_an_agents_draft(text, routine):
    assert _screen()({"question": text})["routine"] is routine


MISSED_AT_THE_DESK = [
    "I took 20 tylenol", "took a lot of advil", "I think I overdosed", "overdose", "my chest is killing me",
    "SOB and CP", "cp", "sob since last night", "angina again", "numb face", "my face feels numb",
    "stabbing pain in my belly", "heavy period soaking a pad an hour", "ectopic", "worried about a miscarriage",
    "SI", "having SI again", "suicidal", "feeling suicidal lately", "douleur thoracique",
    "j'ai mal à la poitrine", "j'ai du mal à respirer", "je saigne beaucoup"]


@pytest.mark.parametrize("text", MISSED_AT_THE_DESK)
def test_the_screen_flags_what_a_desk_booking_let_through(text):
    assert _screen()({"question": text})["red_flag"], text


@pytest.mark.parametrize("text,routine", [
    ("a sick note form for work", True), ("can you fax my records to the pharmacy", True),
    ("I want to move my appt", True), ("please copy my results", True), ("my insurance form", True),
    ("Si vous pouvez m'appeler demain", False), ("took 10 minutes to log in to the portal", False),
    ("I sobbed when I heard", False), ("my copay for the visit", False)])
def test_short_words_flag_only_as_words(text, routine):
    r = _screen()({"question": text})
    assert r["red_flag"] is False and r["unsure"] is False and r["routine"] is routine, (text, r)


def _visits_at(hub, at):
    return [v for v in hub.records.search("Encounter", limit=None) if v.get("plannedStartDate") == at]


def test_a_desk_booking_is_booked_at_once_only_for_a_plainly_routine_reason(hub):
    """From a calendar slot, a reason the screen does not flag but that is not plainly paperwork is read by a nurse
    first: an ordinary slot is fine (the desk's slot is booked), or sooner (the scheduler proposes one)."""
    jo, noor = M(hub, "jo"), M(hub, "noor")
    pat = _pat(hub)
    run = hub.engine.start(jo, "appointment-request", record=pat, inputs={
        "reason": "my knee hurts when I run", "kind": "new-concern", "slot": "2027-03-09T09:00|dr-liam"})
    assert not _visits_at(hub, "2027-03-09T09:00")  # not booked before a nurse read it
    triage = hub.engine.item(item_for(hub, run, "triage"))
    assert triage["role"] == "nurse" and hub.engine.run(run["id"])["steps"]["book-chosen"]["state"] == "pending"
    hub.engine.complete(noor, triage["id"], {"decision": "book-a-slot"})
    (v,) = _visits_at(hub, "2027-03-09T09:00")
    assert v["subject"]["reference"] == pat
    run = hub.engine.run(run["id"])
    assert run["steps"]["propose"]["state"] == "skipped" and run["steps"]["confirm-chosen"]["state"] == "done"
    # sooner: the desk's slot is not booked, the scheduler proposes another
    run = hub.engine.start(jo, "appointment-request", record=pat, inputs={
        "reason": "a rash on my arm", "kind": "new-concern", "slot": "2027-03-09T10:00|dr-liam"})
    hub.engine.complete(noor, item_for(hub, run, "triage"), {"decision": "book-it-soon"})
    run = hub.engine.run(run["id"])
    assert run["steps"]["book-chosen"]["state"] == "skipped" and run["steps"]["propose"]["state"] == "active"
    assert not _visits_at(hub, "2027-03-09T10:00")


def test_a_screen_that_fails_still_reaches_a_nurse(hub, monkeypatch):
    """Fail closed: if the red-flag screen cannot run, nothing is booked and a nurse calls the patient back."""
    real = type(hub.engine)._run_block

    def broken(self, run, step, params):
        if step.id == "screen":
            raise RuntimeError("the screen ran out of time")
        return real(self, run, step, params)
    monkeypatch.setattr(type(hub.engine), "_run_block", broken)
    for inputs in ({"reason": "a form for work", "kind": "forms", "slot": "2027-03-09T11:00|dr-liam"},
                   {"reason": "a form for work"}):
        run = hub.engine.run(hub.engine.start(M(hub, "jo"), "appointment-request", record=_pat(hub),
                                              inputs=inputs)["id"])
        assert run["steps"]["screen"]["state"] == "failed"
        nurse = hub.engine.item(item_for(hub, run, "nurse-call"))
        assert nurse["role"] == "nurse" and nurse["priority"] == "urgent"
        assert run["steps"]["book-chosen"]["state"] == "pending" and not _visits_at(hub, "2027-03-09T11:00")
    # a screen that runs: no nurse call for plain paperwork
    monkeypatch.setattr(type(hub.engine), "_run_block", real)
    run = hub.engine.run(hub.engine.start(M(hub, "jo"), "appointment-request", record=_pat(hub),
                                          inputs={"reason": "a form for work"})["id"])
    assert run["steps"]["nurse-call"]["state"] == "skipped"


def test_the_request_form_does_not_ask_for_the_machine_slot(hub):
    from practicehub.web.server import App
    wf = hub.workflow("appointment-request")[0]
    assert next(i for i in wf.inputs if i["name"] == "slot").get("hidden") is True
    urgency = next(i for i in wf.inputs if i["name"] == "urgency")
    page = App(hub, dev=False)._field(urgency, [{"value": c, "label": c} for c in urgency["choices"]], None)
    assert "<span>None</span>" not in page and 'value="routine" required checked' in page


def test_a_clean_routine_booking_from_a_slot_sends_no_emergency_message(hub):
    """The safety-net message goes only when the desk picked no slot or the screen flags or is unsure: its condition
    is a step setting, not a message parameter (it once sat under params and so went to every patient)."""
    run = hub.engine.run(hub.engine.start(M(hub, "jo"), "appointment-request", record=_pat(hub), inputs={
        "reason": "a form for work", "kind": "forms", "slot": "2027-03-09T11:00|dr-liam"})["id"])
    assert run["steps"]["safety-net"]["state"] == "skipped"
    run = hub.engine.run(hub.engine.start(M(hub, "jo"), "appointment-request", record=_pat(hub),
                                          inputs={"reason": "a form for work"})["id"])
    assert run["steps"]["safety-net"]["state"] == "done"
