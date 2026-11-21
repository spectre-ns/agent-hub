"""Document control: what a document says about itself in a list, and demo data."""


def summarize(rec: dict) -> str:
    if rec.get("resourceType") != "Document":
        return ""
    num = f"{rec['number']} " if rec.get("number") else ""
    return f"{num}{rec.get('title', '')}".strip()


def demo(hub) -> None:
    """Demo only: the practice's policy owners hold the module's roles, and there is one approved procedure."""
    from practicehub.hub import member_ref

    owners = [m for m in hub.members("person") if hub.is_policy_owner(m)]
    if not owners:
        return
    hub.authz.write([(f"role:{r}", "assignee", member_ref(m["id"])) for m in owners
                     for r in ("document-controller", "document-author")])
    installer = {"id": "module:dms", "kind": "automation", "name": "Document control demo", "status": "active"}
    hub.create_record(installer, "Document", {
        "title": "Handling a critical lab result", "number": "SOP-014", "revision": 3, "status": "approved",
        "owner": owners[0]["id"], "effective": "2026-09-01",
        "body": "Call the patient the same day. If the physician is away, the covering physician decides."})
