"""Wiki: what a page says about itself in a list, and demo pages."""


def summarize(rec: dict) -> str:
    if rec.get("resourceType") != "WikiPage":
        return ""
    from practicehub.markup import plain
    words = plain(rec.get("body") or "", 140)
    title = str(rec.get("title", ""))
    return f"{title}: {words}" if words else title


def demo(hub) -> None:
    """Demo only: every person writes in the wiki, the policy owners own it, and two spaces have a few pages."""
    from practicehub.hub import member_ref

    owners = [m for m in hub.members("person") if hub.is_policy_owner(m)]
    if not owners:
        return
    hub.authz.write([("role:wiki-owner", "assignee", member_ref(m["id"])) for m in owners]
                    + [("role:wiki-editor", "assignee", member_ref(m["id"])) for m in hub.members("person")
                       if m["status"] == "active"])
    me = {"id": "module:wiki", "kind": "automation", "name": "Wiki demo", "status": "active"}
    owner = owners[0]["id"]

    def page(title, body, parent=None, status="published", labels=()):
        data = {"title": title, "body": body.strip(), "status": status, "owner": owner, "labels": list(labels)}
        if parent:
            data["parent"] = {"reference": f"WikiPage/{parent['id']}"}
        return hub.create_record(me, "WikiPage", data)

    sop = next((r for r in hub.records.search("Document", limit=50) if r.get("number") == "SOP-014"), None) \
        if hub.record_type("Document") else None
    sop_link = f"[[Document/{sop['id']}|SOP-014, handling a critical lab result]]" if sop else "SOP-014"

    home = page("Clinic handbook", """
How this clinic works, for everyone who works here. Start with [[Onboarding]] if you are new.

## In this space

- [[Onboarding]]: your first week
- [[On-call guide]]: evenings, weekends and who to call
- [[Booking rules]]: how long each kind of visit takes
""", labels=["handbook"])
    page("Onboarding", """
Welcome. In your first week:

1. Get your account and sign in to the hub with your own name.
2. Read and sign the controlled documents you work under (**Records → Controlled documents**).
3. Shadow a colleague for one clinic.

## Who to ask

| About | Ask |
|---|---|
| Accounts and devices | the [[IT and systems]] page |
| Clinical questions | the physician on call: see the [[On-call guide]] |
""", home, labels=["onboarding", "new staff"])
    page("On-call guide", f"""
## Evenings and weekends

The physician on call carries the on-call phone from 17:00 to 08:00, and all weekend.

## A critical lab result

Follow {sop_link}: call the patient the same day. If the physician is away, the covering physician decides.

> Never leave a critical result in an inbox overnight.
""", home, labels=["on-call", "lab"])
    booking = page("Booking rules", """
| Visit | Length |
|---|---|
| Follow-up | 15 minutes |
| New patient | 30 minutes |
| Annual physical | 45 minutes |

Book a longer visit when the patient brings a list. ~~Double-booking~~ is not allowed.
""", home, labels=["booking"])
    hub.update_record(me, f"WikiPage/{booking['id']}", {"status": "needs-check"})  # a wiki owner checks it
    it = page("IT and systems", """
Accounts, devices and backups. The hub keeps every record in one database; see [[Backups]].
""", labels=["it"])
    page("Backups", """
A backup runs every night. To check one:

```
practicehub backup --to /backups/tonight
practicehub verify
```

Restores are done by IT only, and every restore is written to the track.
""", it, labels=["backup"])

    # how-tos the clinic writes together; one it wants under document control becomes a controlled document through
    # the wiki-sop module (a PDF of the page, filed as a draft for review)
    how = page("How we work", """
How the clinic does things day to day, written together. A page here is not controlled: when one has to be approved,
released and signed, a document author files it as a controlled document (**Make a controlled document** on its page,
with the wiki-sop module).
""", home, labels=["how-to"])
    page("Hand hygiene", """
## When

Clean your hands **before** touching a patient, **before** a clean or aseptic task, **after** contact with body
fluids, **after** touching a patient, and **after** touching their surroundings.

## How

1. Use alcohol hand rub unless your hands are visibly dirty.
2. Rub every surface of both hands for 20 to 30 seconds, until dry.
3. If your hands are visibly dirty, or after caring for a patient with diarrhoea, wash with soap and water for
   40 to 60 seconds, and dry with a single-use towel.

## Checklist for each room

- [x] Hand rub at the door and at the sink
- [x] Soap and paper towels stocked
- [ ] Poster at the sink

> [!note]
> Gloves are not a substitute for clean hands. Clean your hands before you put gloves on and after you take them off.
""", how, labels=["how-to", "infection control"])
    page("Cleaning the treatment rooms", """
## Between patients

1. Wipe the couch, the trolley top and anything the patient touched with a detergent wipe.
2. Change the couch roll.
3. Empty the sharps bin when it is three-quarters full.

## At the end of the day

| Surface | With | Who |
|---|---|---|
| Floors | mop and detergent | cleaner |
| Sinks and taps | detergent wipe | clinic nurse |
| Fridge (vaccines) | do not clean: report anything spilt | clinic nurse |
""", how, status="draft", labels=["how-to", "infection control"])
