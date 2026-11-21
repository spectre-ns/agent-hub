"""Product lifecycle. Its workflow does the work (``for_each`` over a change order's products and documents); the
module meets document control and quality at the records, with no code between them."""


def summarize(rec: dict) -> str:
    if rec.get("resourceType") == "Product":
        return f"{rec.get('number', '')} {rec.get('name', '')}, revision {rec.get('revision') or 0} ({rec.get('status', '')})"
    if rec.get("resourceType") == "ChangeOrder":
        return f"Change order: {rec.get('title', '')} ({rec.get('status', '')})"
    return ""


def demo(hub) -> None:
    """Demo only: policy owners hold the module's roles; a product line, a kit made of two parts with a specification
    under document control, and a change order to it, still a draft."""
    from practicehub.hub import member_ref

    owners = [m for m in hub.members("person") if hub.is_policy_owner(m)]
    if not owners:
        return
    hub.authz.write([(f"role:{r}", "assignee", member_ref(m["id"])) for m in owners
                     for r in ("product-owner", "change-board")])
    me = {"id": "module:plm", "kind": "automation", "name": "Product lifecycle demo", "status": "active"}
    spec = hub.create_record(me, "Document", {
        "title": "Dressing kit: contents and packing", "number": "SPEC-101", "revision": 1, "status": "approved",
        "owner": owners[0]["id"], "effective": "2026-10-01",
        "body": "## Contents\n\n| Part | Quantity |\n| --- | --- |\n| Sterile gauze 10x10 | 4 |\n| Saline 30 mL | 1 |"})
    line = hub.create_record(me, "Product", {"number": "WC", "name": "Wound care", "kind": "product-line",
                                             "status": "design", "owner": owners[0]["id"]})
    parts = [hub.create_record(me, "Product", {"number": n, "name": t, "kind": "supply", "status": "design",
                                               "revision": 1, "family": f"Product/{line['id']}",
                                               "supplier": "Approved supplier list"})
             for n, t in (("WC-201", "Sterile gauze 10x10"), ("WC-202", "Saline 30 mL"))]
    kit = hub.create_record(me, "Product", {
        "number": "WC-100", "name": "Dressing kit", "kind": "kit", "status": "design", "revision": 1,
        "family": f"Product/{line['id']}", "owner": owners[0]["id"],
        "components": [f"Product/{p['id']}" for p in parts], "specs": [f"Document/{spec['id']}"],
        "description": "A single-use kit for a simple dressing change. Packed to [[Document/" + spec["id"] + "]]."})
    for p in (line, kit, *parts):  # the demo starts with them released, as a change order would have left them
        hub.update_record(me, f"Product/{p['id']}", {"status": "released"})
    hub.create_record(me, "ChangeOrder", {
        "title": "Larger gauze in the dressing kit", "raised": "2026-11-15", "owner": owners[0]["id"],
        "reason": "Clinicians cut two pads to cover larger wounds. Use **15x15** gauze.",
        "products": [f"Product/{kit['id']}"], "documents": [f"Document/{spec['id']}"]})
