"""Quality. Its workflow links a CAPA to document control by itself (``for_each``); no code is needed for that."""


def send_documents_for_review(engine, run, step, params, actor):
    """Kept for runs that started on version 1 of qms-capa; the workflow now does this itself with ``for_each``.

    Move every approved document the CAPA affects to in-review. Each move starts the dms module's review
    workflow (its trigger is ``record.transitioned:Document``): the modules meet at the record, not in code."""
    hub = engine.hub
    ref = params.get("capa") or run["record"]
    capa = hub.records.read(*ref.split("/", 1))
    if not capa:
        raise ValueError(f"there is no {ref}")
    sent, skipped = [], []
    for d in capa.get("documents") or []:
        dref = d.get("reference") if isinstance(d, dict) else d
        doc = hub.records.read(*str(dref).split("/", 1)) if dref and "/" in str(dref) else None
        if not doc:
            raise ValueError(f"the CAPA points at {dref}, which does not exist")
        if doc.get("status") != "approved":
            skipped.append(dref)
            continue
        hub.update_record(actor, dref, {"status": "in-review", "revision": int(doc.get("revision") or 0) + 1},
                          run_id=run["id"])
        sent.append(dref)
    return {"sent": sent, "skipped": skipped}


ACTIONS = {"qms.send-documents-for-review": send_documents_for_review}


def summarize(rec: dict) -> str:
    if rec.get("resourceType") != "CAPA":
        return ""
    return f"{rec.get('kind', '').capitalize()} action: {rec.get('title', '')}"


def demo(hub) -> None:
    from practicehub.hub import member_ref

    owners = [m for m in hub.members("person") if hub.is_policy_owner(m)]
    hub.authz.write([("role:quality-manager", "assignee", member_ref(m["id"])) for m in owners])
