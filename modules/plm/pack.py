"""Product lifecycle. Its workflow does the work (``for_each`` over a change order's parts and documents); the module
meets inventory (the parts), document control and quality at the records, with no code between them."""


def _ref(v) -> str:
    """A reference as stored ("Part/7", or ``{"reference": "Part/7"}``), as its text."""
    return str(v.get("reference") or "") if isinstance(v, dict) else str(v or "")


def _change(hub, run, params) -> tuple[str, dict]:
    ref = str(params.get("change") or run["record"])
    rec = hub.records.read(*ref.split("/", 1)) if ref.startswith("ChangeOrder/") else None
    if rec is None:
        raise ValueError(f"there is no change order {ref}")
    return ref, rec


def _listed(rec: dict) -> list[str]:
    """The parts a change order names, each once, in its order."""
    return list(dict.fromkeys(r for r in (_ref(p) for p in rec.get("parts") or []) if r.startswith("Part/")))


def _held(hub, cref: str) -> list[str]:
    """The parts this change holds now."""
    return [r["from_ref"] for r in hub.db.links.find({"to_ref": cref, "rel": "held-by"}, fields=["from_ref"])]


def _restore(hub, actor, run_id: str, ref: str, cref: str) -> None:
    """Let a part go as it was before this change held it: its fields then (what the workflow writes, such as its
    stock and revision, stays as it is now) and its status then (in use, or a draft with its author). Edits made under
    the change are undone; they are kept in the part's history."""
    from practicehub.store import DESC
    rtype, rid = ref.split("/", 1)
    now = hub.records.read(rtype, rid)
    if now is None:
        return
    seen, before = False, None
    for v in hub.db.record_versions.find({"type": rtype, "id": rid}, sort=[("version", DESC)]):
        d = v.get("data") or {}
        if _ref(d.get("held_by")) == cref:
            seen = True
        elif seen:
            before = d
            break
    if before is None:  # nothing to go back to: only let it go
        hub.update_record(actor, ref, {"held_by": None}, run_id=run_id)
        return
    rt = hub.record_type(rtype)
    keep = {"resourceType", "id", "meta"} | {f["path"] for f in rt.fields
                                             if f.get("by") == "workflow" and "." not in f["path"]}
    data = {k: None for k in now if k not in keep and k not in before}
    data.update({k: v for k, v in before.items() if k not in keep})
    data["held_by"] = None
    hub.update_record(actor, ref, data, run_id=run_id)


def act_hold(engine, run, step, params, actor):
    """plm.hold: hold each part the change order names in draft for its next revision (stock and builds keep using the
    revision last released), and let go of any it held that it no longer names. Refused, holding nothing, when a part
    is held by another change, is in its own review, or is obsolete: one change at a time changes a part. ``ok``,
    ``problem``, ``held`` (the parts it took hold of now)."""
    hub = engine.hub
    cref, change = _change(hub, run, params)
    listed = _listed(change)
    parts = hub.records.read_many(listed)
    problems = []
    for ref in listed:
        p = parts.get(ref)
        if p is None:
            problems.append(f"{ref} no longer exists")
            continue
        name, holder = hub.title(p), _ref(p.get("held_by"))
        if holder and holder != cref:
            other = hub.records.read(*holder.split("/", 1)) if "/" in holder else None
            other_name = " ".join(x for x in (hub.key_of(holder), (other or {}).get("title")) if x) or holder
            problems.append(f"{name} is held by {other_name}: one change at a time changes a part")
        elif p.get("status") == "in-review":
            problems.append(f"{name} is in review on its own: approve it or send it back first")
        elif p.get("status") not in ("active", "draft"):
            problems.append(f"{name} is {p.get('status')}")
    if problems:
        return {"ok": False, "problem": "; ".join(problems), "held": []}
    for ref in _held(hub, cref):
        if ref not in listed:
            _restore(hub, actor, run["id"], ref, cref)
    held = []
    for ref in listed:
        if _ref(parts[ref].get("held_by")) != cref:
            hub.update_record(actor, ref, {"status": "draft", "held_by": cref}, run_id=run["id"])
            held.append(ref)
    return {"ok": True, "problem": "", "held": held}


def act_release(engine, run, step, params, actor):
    """plm.release: put each part this change holds back in use at its next revision; one it holds but no longer
    names goes back as it was. ``released``: the parts put in use."""
    hub = engine.hub
    cref, change = _change(hub, run, params)
    listed = _listed(change)
    released = []
    for ref in _held(hub, cref):
        if ref not in listed:
            _restore(hub, actor, run["id"], ref, cref)
            continue
        p = hub.records.read(*ref.split("/", 1))
        hub.update_record(actor, ref, {"status": "active", "revision": int(p.get("revision") or 0) + 1,
                                       "held_by": None}, run_id=run["id"])
        released.append(ref)
    return {"released": released}


def act_unhold(engine, run, step, params, actor):
    """plm.unhold: a cancelled change lets go of every part it holds, each as it was before the change held it.
    ``restored``: those parts."""
    hub = engine.hub
    cref, _ = _change(hub, run, params)
    held = _held(hub, cref)
    for ref in held:
        _restore(hub, actor, run["id"], ref, cref)
    return {"restored": held}


ACTIONS = {"plm.hold": act_hold, "plm.release": act_release, "plm.unhold": act_unhold}


def summarize(rec: dict) -> str:
    if rec.get("resourceType") == "ChangeOrder":
        return f"Change order: {rec.get('title', '')} ({rec.get('status', '')})"
    return ""


def demo(hub) -> None:
    """Demo only: policy owners hold the module's roles; a wound care kit (a part whose bill of materials names two
    others) with a specification under document control, and a change order to it, still a draft."""
    from practicehub.hub import member_ref

    owners = [m for m in hub.members("person") if hub.is_policy_owner(m)]
    if not owners:
        return
    hub.authz.write([(f"role:{r}", "assignee", member_ref(m["id"])) for m in owners
                     for r in ("product-owner", "change-board")])
    me = {"id": "module:plm", "kind": "automation", "name": "Product lifecycle demo", "status": "active"}
    spec = hub.create_record(me, "Document", {
        "title": "Wound care kit: contents and packing", "number": "SPEC-101", "revision": 1, "status": "approved",
        "owner": owners[0]["id"], "effective": "2026-10-01",
        "body": "## Contents\n\n| Part | Quantity |\n| --- | --- |\n| WC-201 Sterile gauze 10x10 | 2 |\n"
                "| WC-202 Saline 30 mL | 1 |"})
    parts = [hub.create_record(me, "Part", {"code": c, "name": t, "category": "component", "unit": "each",
                                            "tracking": "lot", "owner": owners[0]["id"]})
             for c, t in (("WC-201", "Sterile gauze 10x10"), ("WC-202", "Saline 30 mL"))]
    kit = hub.create_record(me, "Part", {
        "code": "WC-100", "name": "Wound care kit", "category": "assembly", "unit": "kit", "tracking": "lot",
        "owner": owners[0]["id"], "specs": [f"Document/{spec['id']}"],
        "bom": [{"part": f"Part/{parts[0]['id']}", "quantity": 2, "designators": "left pouch"},
                {"part": f"Part/{parts[1]['id']}", "quantity": 1}],
        "description": "A single-use kit for a simple dressing change. Packed to [[Document/" + spec["id"] + "]]."})
    hub.create_record(me, "ChangeOrder", {
        "title": "Larger gauze in the wound care kit", "raised": "2026-11-15", "owner": owners[0]["id"],
        "reason": "Clinicians cut two pads to cover larger wounds. Use **15x15** gauze.",
        "parts": [f"Part/{kit['id']}"], "documents": [f"Document/{spec['id']}"]})
