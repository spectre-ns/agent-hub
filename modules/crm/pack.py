"""Relationships (CRM). Business data only: nothing here reads or writes health information. The consent rule for
commercial messages is the hub's (``Hub.consent_problem``), not this module's: the module only declares where a
contact's consent is kept (``[consent]`` in types/Person.toml)."""

from practicehub import schema


def summarize(rec: dict) -> str:
    t = rec.get("resourceType")
    if t == "Organization":
        kind = schema.first(rec, "type.0.text") or "organisation"
        return f"{rec.get('name', '')} ({kind}, {rec.get('crm_status', '')})"
    if t == "Person":
        return f"Contact: {schema.first(rec, 'name.0.text') or ''}" + (
            f", {rec['job_title']}" if rec.get("job_title") else "")
    if t == "Interaction":
        due = f"; follow up {rec['follow_up']}" if rec.get("follow_up") else ""
        return f"{(rec.get('kind') or '').capitalize()} {str(rec.get('at') or '')[:10]}: {rec.get('summary', '')}{due}"
    if t == "Enquiry":
        return f"Enquiry: {rec.get('title', '')} ({rec.get('status', '')})"
    return ""


def _read(hub, ref):
    ref = schema.ref_of(ref) if ref else None
    if not ref or "/" not in ref:
        return None, None
    rtype, rid = ref.split("/", 1)
    return ref, hub.records.read(rtype, rid)


def follow_up_who(engine, run, step, params, actor):
    """Who follows an interaction up (the owner of what it was with, else who logged it) and whom they write to (the
    person spoken with, else the contact it was with). An organisation has no consent of its own, so a commercial
    message to one is refused by the hub."""
    hub = engine.hub
    _, it = _read(hub, run["record"])
    if it is None:
        raise ValueError("crm.follow-up-who: the interaction is gone")
    with_ref, with_rec = _read(hub, it.get("with"))
    contact_ref, contact = _read(hub, it.get("contact"))
    if contact is None and with_rec is not None and with_rec.get("resourceType") == "Person":
        contact_ref, contact = with_ref, with_rec
    owner = (with_rec or {}).get("account_owner") or (contact or {}).get("account_owner") or it.get("logged_by") or ""
    return {"owner": owner, "to": contact_ref or with_ref or "",
            "to_name": hub.title(contact) if contact else (hub.title(with_rec) if with_rec else ""),
            "with": hub.title(with_rec) if with_rec else "", "kind": it.get("kind", ""),
            "at": str(it.get("at") or "")[:10], "summary": it.get("summary", "")}


ACTIONS = {"crm.follow-up-who": follow_up_who}


def standin_follow_up(ctx):
    """The stand-in model's draft: a short note from the interaction's summary. A person edits and sends it."""
    who = (ctx.get("context") or {}).get("who") or {}
    if not who.get("summary"):
        return {"answer": {}, "escalate": True, "reason": "no interaction summary to follow up"}
    name = (who.get("to_name") or "").split(" · ")[0] or "there"
    return {"answer": {"subject": f"Following up on our {who.get('kind') or 'conversation'} of {who.get('at')}",
                       "body": f"Hello {name},\n\nThank you for your time on {who.get('at')}. As discussed: "
                               f"{who['summary']}\n\nPlease let me know if anything has changed.\n"},
            "reason": "drafted from the interaction's summary"}


STANDINS = {"crm-follow-up-draft": standin_follow_up}


def demo(hub) -> None:
    """Demo only: policy owners are relationship managers, with an agent that drafts follow-ups; a few organisations
    and contacts (with consent express, implied and lapsing, none, and withdrawn), interactions and enquiries."""
    from practicehub.hub import member_ref
    from practicehub.expr import shift_date

    owners = [m for m in hub.members("person") if hub.is_policy_owner(m)]
    if not owners:
        return
    me = {"id": "module:crm", "kind": "automation", "name": "Relationships demo", "status": "active"}
    lead = owners[0]["id"]
    hub.authz.write([(f"role:{r}", "assignee", member_ref(m["id"])) for m in owners
                     for r in ("relationship-manager", "follow-up-drafter")])
    try:
        hub.member("relationship-agent")
    except Exception:  # noqa: BLE001 - not there yet
        hub._create_member(me, {"id": "relationship-agent", "kind": "agent", "name": "Relationship agent",
                                "sponsor": lead, "roles": ["follow-up-drafter"],
                                "profile": {"model": "standin", "max_risk": "low",
                                            "skills": ["crm-follow-up-draft"]}})
    today = hub.engine.now().date().isoformat()

    def make(rtype, values):  # the record, from its fields' names (``phone`` goes to the telecom entry for phones)
        rt, rec = hub.record_type(rtype), {}
        for name, v in values.items():
            if v is not None:
                f = rt.field(name)
                rec = schema.set_in(rec, schema.locate(rec, f["path"], f["with"]).split("."), v, f["with"])
        return hub.create_record(me, rtype, rec)

    def ago(n, unit="d"):
        return shift_date(today, "-", n, unit)

    def org(name, kind, status, **kw):
        return f"Organization/{make('Organization', {'name': name, 'kind': kind, 'crm_status': status, 'account_owner': lead, **kw})['id']}"

    def person(name, at, title, **kw):
        return f"Person/{make('Person', {'name': name, 'works_at': at, 'job_title': title, 'account_owner': lead, **kw})['id']}"

    harbour = org("Harbour Community Credit Union", "partner", "active", phone="902-555-0140",
                  email="partners@harbourccu.example", city="Halifax", province="NS")
    league = org("South Shore Business League", "referrer", "active", phone="902-555-0177", city="Lunenburg",
                 province="NS")
    paper = org("Bluenose Office Supply", "supplier", "active", email="orders@bluenose.example", city="Dartmouth",
                province="NS")
    org("Maritime Mutual", "insurer", "prospect", city="Moncton", province="NB")
    org("North End Newcomers Centre", "community-agency", "active", city="Halifax", province="NS")
    ines = person("Ines Arsenault", harbour, "Member services manager", email="ines@harbourccu.example",
                  consent="express", consent_date=ago(200), consent_source="Signed partner form")
    raj = person("Raj Pillai", league, "Executive director", email="raj@ssbl.example",
                 consent="implied", consent_date=shift_date(ago(2, "y"), "+", 18, "d"),
                 consent_source="Sponsorship agreement")
    dana = person("Dana Comeau", harbour, "Branch lead", phone="902-555-0141", email="dana@harbourccu.example")
    person("Theo Wright", paper, "Account rep", email="theo@bluenose.example", consent="express",
           consent_date=ago(400), consent_source="Email reply", consent_withdrawn=ago(20))
    for with_, kind, when, contact, summary, follow in (
            (harbour, "meeting", ago(14), ines, "Annual partnership review. They will send next year's workshop "
                                               "dates; we offered two evening sessions.", ago(1)),
            (harbour, "call", ago(6), dana, "Asked about a referral card for new members.", today),
            (harbour, "email", ago(30), ines, "Sent the signed partner form back.", None),
            (league, "visit", ago(9), raj, "Spoke at the league breakfast; three members asked for a call.",
             shift_date(today, "+", 4, "d")),
            (paper, "call", ago(3), None, "Order of filing boxes delayed a week.", None)):
        make("Interaction", {"with": with_, "kind": kind, "at": f"{when}T10:00:00",
                                              "contact": contact, "summary": summary, "follow_up": follow,
                                              "logged_by": lead})
    for title, frm, status, value, extra in (
            ("Bookkeeping for a new café", raj, "new", 2400, {}),
            ("Payroll for a 12-person shop", league, "new", 3600, {}),
            ("Year-end for a family farm", ines, "contacted", 5200, {}),
            ("Workshop series for members", harbour, "qualified", 1500, {}),
            ("Monthly bookkeeping, machine shop", dana, "won", 4800, {}),
            ("Audit of a charity", league, "lost", 9000, {"lost_reason": "They need an auditor licensed in NB."})):
        rec = make("Enquiry", {"title": title, "from": frm, "received": ago(10), "owner": lead,
                                                "value": value, **extra})
        path = {"new": [], "contacted": ["contacted"], "qualified": ["contacted", "qualified"],
                "won": ["contacted", "qualified", "won"], "lost": ["contacted", "lost"]}[status]
        for st in path:
            hub.update_record(me, f"Enquiry/{rec['id']}", {"status": st})
