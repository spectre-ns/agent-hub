"""Inventory. Its workflows do the work: posting a stock move changes the item's on hand, and an item at its reorder
point asks a purchaser to order more. No code between the module and the records."""


def summarize(rec: dict) -> str:
    t = rec.get("resourceType")
    if t == "StockItem":
        return (f"{rec.get('sku', '')} {rec.get('name', '')}: {rec.get('on_hand') or 0} {rec.get('unit') or ''} on hand "
                f"({rec.get('status', '')})")
    if t == "StockMove":
        return f"Stock move: {rec.get('kind', '')} {rec.get('quantity', '')} ({rec.get('status', '')})"
    if t == "Location":
        return f"Storage location: {rec.get('name', '')}"
    return ""


def demo(hub) -> None:
    """Demo only: policy owners hold the module's roles; a clinic with a supply room and a vaccine fridge, and a few
    items with their opening counts."""
    from practicehub.hub import member_ref

    owners = [m for m in hub.members("person") if hub.is_policy_owner(m)]
    if not owners:
        return
    hub.authz.write([(f"role:{r}", "assignee", member_ref(m["id"])) for m in owners
                     for r in ("stock-keeper", "purchaser", "inventory-manager")])
    me = {"id": "module:inventory", "kind": "automation", "name": "Inventory demo", "status": "active"}
    site = hub.create_record(me, "Location", {"name": "Main clinic", "kind": "site"})
    room = hub.create_record(me, "Location", {"name": "Supply room", "kind": "room", "within": f"Location/{site['id']}"})
    fridge = hub.create_record(me, "Location", {"name": "Vaccine fridge", "kind": "fridge", "cold_chain": True,
                                                "within": f"Location/{site['id']}"})
    for sku, name, cat, unit, where, on_hand, at, qty in (
            ("GL-M", "Nitrile gloves, medium", "clinical-supply", "box of 100", room, 24, 6, 20),
            ("GZ-10", "Sterile gauze 10x10", "clinical-supply", "pack of 10", room, 40, 10, 50),
            ("SY-3", "Syringe 3 mL", "clinical-supply", "box of 100", room, 5, 4, 10),
            ("VX-FLU", "Influenza vaccine", "vaccine", "dose", fridge, 30, 15, 40)):
        hub.create_record(me, "StockItem", {
            "sku": sku, "name": name, "category": cat, "unit": unit, "location": f"Location/{where['id']}",
            "on_hand": on_hand, "reorder_point": at, "reorder_quantity": qty, "supplier": "Approved supplier list"})
