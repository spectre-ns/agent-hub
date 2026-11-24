"""Quality. Its workflow links a CAPA to document control by itself (``for_each``); no code is needed for that."""


def summarize(rec: dict) -> str:
    if rec.get("resourceType") != "CAPA":
        return ""
    return f"{rec.get('kind', '').capitalize()} action: {rec.get('title', '')}"


def demo(hub) -> None:
    from practicehub.hub import member_ref

    owners = [m for m in hub.members("person") if hub.is_policy_owner(m)]
    hub.authz.write([("role:quality-manager", "assignee", member_ref(m["id"])) for m in owners])
