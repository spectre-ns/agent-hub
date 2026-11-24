"""Inventory: parts, the lots that hold them, where they are kept, and the ledger of stock moves (after InvenTree).

A lot (``StockItem``: one quantity of one part in one place) is never written by a person. Only ``inventory.post``
changes one, in the step that posts a stock move, and it writes on the move what it changed (``entries``: each lot's
quantity, status and place before and after). So a lot's quantity is always what its posted moves left it, and the
part's totals are summed from its lots in that same step.

What needs a signature first is the inventory policy in force (``inventory.policy``): basic (a corner store: nothing
is signed), tracked (a small clinic: adjustments, write-offs and releases are signed) or regulated (ISO 13485). What is
tracked (lots, serials, expiry) is each part's own choice. With no policy signed, the module's own basic policy holds.

A move is checked before anyone signs it (``inventory.check`` writes the plan onto it), and posted after
(``inventory.post`` plans it again and refuses when the stock changed since: a signature covers only what was checked).
A refusal is a result, not an error: the move goes back to its author with the reason in words."""

from __future__ import annotations

from datetime import date

LEVELS = ("basic", "tracked", "regulated")
SIGNOFF = ("none", "self", "second")
FEFO = ("off", "suggest", "enforce")
# Each level's switches. A policy may set a switch stricter than its level, never looser (FLOORS): a tracked clinic
# run by one person signs its own changes (``self``); a regulated one never does.
PRESETS = {
    "basic": {"signoff": "none", "part_approval": False, "po_required": False, "approved_suppliers_only": False,
              "inspection": False, "nc_required": False, "fefo": "off", "count_tolerance_pct": None,
              "over_receipt_pct": None, "default_tracking": "none"},
    "tracked": {"signoff": "second", "part_approval": False, "po_required": False, "approved_suppliers_only": False,
                "inspection": False, "nc_required": False, "fefo": "suggest", "count_tolerance_pct": 2,
                "over_receipt_pct": 5, "default_tracking": "lot"},
    "regulated": {"signoff": "second", "part_approval": True, "po_required": True, "approved_suppliers_only": True,
                  "inspection": True, "nc_required": True, "fefo": "enforce", "count_tolerance_pct": 0,
                  "over_receipt_pct": 0, "default_tracking": "lot"},
}
FLOORS = {**PRESETS, "tracked": {**PRESETS["tracked"], "signoff": "self"}}
SWITCHES = tuple(PRESETS["basic"])
SWITCH_WORDS = {"signoff": "sign-off", "part_approval": "part approval", "po_required": "a purchase order for every "
                "receipt", "approved_suppliers_only": "approved suppliers only", "inspection": "receiving inspection",
                "nc_required": "a nonconformance for held stock", "fefo": "first expiry, first out",
                "count_tolerance_pct": "the count tolerance", "over_receipt_pct": "the over-receipt tolerance"}

KINDS = ("receive", "issue", "transfer", "count", "adjust", "hold", "release", "scrap", "return")
LIVE = ("available", "quarantined", "inspection")  # a lot with stock in it
GONE = {"depleted", "scrapped", "returned"}
HOLDING = {"quarantine", "reject"}                 # locations that hold stock nobody may use


class Refused(Exception):
    """A move the ledger will not post; the message says why, in words, to the move's author."""


# ------------------------------------------------------------------ the policy in force
def effective(rec: dict | None) -> dict:
    """A policy record's switches: its level's, with what it sets itself over them."""
    rec = rec or {}
    level = rec.get("level") if rec.get("level") in LEVELS else "basic"
    out = {**PRESETS[level], "level": level}
    out.update({k: rec[k] for k in SWITCHES if rec.get(k) not in (None, "")})
    return out


def _looser(k: str, a, b) -> bool:
    """Whether switch ``k`` set to ``a`` asks less than ``b``."""
    if k == "signoff":
        return SIGNOFF.index(a) < SIGNOFF.index(b)
    if k == "fefo":
        return FEFO.index(a) < FEFO.index(b)
    if k in ("count_tolerance_pct", "over_receipt_pct"):
        return b is not None and (a is None or float(a) > float(b))
    if k == "default_tracking":
        return False
    return bool(b) and not a


def policy_problems(rec: dict) -> list[str]:
    """Why a policy cannot come into force: a switch looser than its level allows."""
    level = rec.get("level")
    if level not in LEVELS:
        return [f"say which level it is: {', '.join(LEVELS)}"]
    pol, floor = effective(rec), FLOORS[level]
    out = []
    for k in SWITCHES:
        if _looser(k, pol[k], floor[k]):
            out.append(f"{level} needs {SWITCH_WORDS[k]} at least {_show(floor[k])}; this policy says {_show(pol[k])}")
    return out


def loosens(new: dict, old: dict) -> bool:
    return any(_looser(k, new[k], old[k]) for k in SWITCHES)


def _show(v) -> str:
    if v is True:
        return "on"
    if v is False:
        return "off"
    return "none" if v is None else (f"{v:g}%" if isinstance(v, (int, float)) else str(v))


def policy_in_force(hub) -> dict:
    """The switches in force: the signed policy (while it is being revised, its last signed version), else basic.
    ``ref`` says which policy, empty for the module's own."""
    for rec in hub.records.search("InventoryPolicy", limit=None):
        ref = f"InventoryPolicy/{rec['id']}"
        if rec.get("status") == "in-force":
            return {**effective(rec), "ref": ref}
        if rec.get("status") in ("draft", "in-review"):
            prior = _last_in_force(hub, ref)
            if prior:
                return {**effective(prior), "ref": ref}
    return {**effective(None), "ref": ""}


def _last_in_force(hub, ref: str) -> dict | None:
    return next((v for v in _versions(hub, ref) if v.get("status") == "in-force"), None)


# ------------------------------------------------------------------ records the ledger reads
def _num(x) -> float | int:
    v = round(float(x or 0), 6)
    return int(v) if v.is_integer() else v


def _read(hub, ref, rtype: str, what: str) -> dict:
    ref = str(ref or "")
    if not ref.startswith(rtype + "/"):
        raise Refused(f"say which {what}")
    rec = hub.records.read(rtype, ref.split("/", 1)[1])
    if rec is None:
        raise Refused(f"there is no {what} {ref}")
    return rec


def _label(hub, entries: list[dict]) -> str:
    """What a move moves, in a few words for its title: the part's code and its lot or serial numbers."""
    def of(e):  # (part code, lot number, serial) of one ledger line
        if e.get("lot"):
            rec = _location(hub, e["lot"]) or {}
            return rec.get("part_code") or "", rec.get("lot") or "", rec.get("serial") or ""
        return (_location(hub, e["part"]) or {}).get("code") or "", e.get("lot_number") or "", e.get("serial") or ""
    code, number, _ = of(entries[0])
    serials = [serial for _, _, serial in map(of, entries) if serial]
    if serials:
        more = f" and {len(serials) - 3} more" if len(serials) > 3 else ""
        return f"{code} serial{'s' if len(serials) > 1 else ''} {', '.join(serials[:3])}{more}".strip()
    return f"{code} lot {number}".strip() if number else code


def _name(rec: dict) -> str:
    t = rec.get("resourceType")
    if t == "Part":
        return f"{rec.get('code', '')} {rec.get('name', '')}".strip()
    if t == "StockItem":
        tag = rec.get("serial") and f"serial {rec['serial']}" or rec.get("lot") and f"lot {rec['lot']}" or "untracked"
        return f"the {tag} stock"
    return str(rec.get("name") or rec.get("id"))


def part_in_use(hub, part: dict) -> dict | None:
    """The part as it may be used now: active, or its last active version while it is revised. None when it may not
    be stocked (a draft never approved, or obsolete)."""
    if part.get("status") == "active":
        return part
    if part.get("status") in ("draft", "in-review"):
        last = next((v for v in _versions(hub, f"Part/{part['id']}") if v.get("status") == "active"), None)
        return {**last, "id": part["id"]} if last else None
    return None


def _versions(hub, ref: str) -> list[dict]:
    """A record's versions, newest first (what it said each time it was written)."""
    rtype, rid = ref.split("/", 1)
    from practicehub.store import DESC
    return [r.get("data") or {} for r in hub.db.record_versions.find({"type": rtype, "id": rid},
                                                                    sort=[("version", DESC)])]


def lots_of(hub, part_ref: str) -> list[dict]:
    out = []
    for row in hub.db.links.find({"to_ref": part_ref, "rel": "lot-of"}):
        rec = hub.records.read(*row["from_ref"].split("/", 1))
        if rec is not None:
            out.append(rec)
    return out


def expired(lot: dict, today: str) -> bool:
    return bool(lot.get("expiry")) and str(lot["expiry"]) < today


def _place(hub, ref) -> dict:
    loc = _read(hub, ref, "Location", "location")
    if loc.get("structural"):
        raise Refused(f"{loc.get('name')} groups other locations and holds no stock: choose one inside it")
    return loc


def _purpose(loc: dict | None) -> str:
    return (loc or {}).get("purpose") or "storage"


# ------------------------------------------------------------------ the plan
def plan(hub, move: dict, pol: dict, today: str) -> dict:
    """What posting ``move`` would do, or Refused. ``entries``: one per lot it touches, each with its quantity,
    status, place and count baseline before and after (``lot`` empty for a lot it makes). ``signoff``: why it needs a
    signature first, in words ("" when it posts at once)."""
    kind = move.get("kind")
    if kind not in KINDS:
        raise Refused(f"say what kind of move it is: {', '.join(KINDS)}")
    q = move.get("quantity")
    signoff, note = "", ""
    if kind == "receive":
        return _plan_receive(hub, move, pol, today)
    lot = _read(hub, move.get("lot"), "StockItem", "stock (the lot or serial)")
    ref = f"StockItem/{lot['id']}"
    part = _read(hub, lot.get("part"), "Part", "part")
    st, have, here = lot.get("status"), float(lot.get("quantity") or 0), lot.get("location") or ""
    base = float(lot.get("count_baseline") if lot.get("count_baseline") is not None else have)
    name = _name(lot)
    if st in GONE:
        raise Refused(f"{name} of {_name(part)} is {st}: nothing of it is left to move")
    whole = q in (None, "") or float(q) == have
    if kind != "count" and kind != "adjust":
        if q not in (None, "") and float(q) <= 0:
            raise Refused("the quantity must be more than nothing")
        if q not in (None, "") and float(q) > have:
            raise Refused(f"only {_num(have)} of {_name(part)} is in {name}; this moves {_num(q)}")
    take = have if q in (None, "") else float(q)
    entry = {"lot": ref, "part": lot["part"], "quantity": [_num(have), _num(have)], "status": [st, st],
             "location": [here, here], "baseline": [_num(base), _num(base)]}
    entries = [entry]

    def split(status: str, where: str) -> None:
        if lot.get("serial"):
            raise Refused(f"{name} is one serial number: it moves whole")
        share = _num(take * base / have) if have else 0
        entry["quantity"][1] = _num(have - take)
        entry["baseline"][1] = _num(base - share)
        entries.append({"lot": "", "part": lot["part"], "parent": ref, "lot_number": lot.get("lot") or "",
                        "supplier_lot": lot.get("supplier_lot") or "", "expiry": lot.get("expiry") or "",
                        "part_revision": lot.get("part_revision"), "quantity": [0, _num(take)], "status": ["", status],
                        "location": ["", where], "baseline": [0, share]})

    if kind == "issue":
        if st != "available":
            raise Refused(f"{name} is {st}: only available stock is issued")
        if expired(lot, today):
            raise Refused(f"{name} expired on {lot['expiry']}: expired stock is not issued; hold it or write it off")
        first = _earlier(hub, lot, today)
        if first and pol["fefo"] == "enforce":
            raise Refused(f"{_name(first)} of {_name(part)} expires first ({first['expiry']}): issue that first")
        if first and pol["fefo"] == "suggest":
            note = f"{_name(first)} expires first ({first['expiry']})"
        entry["quantity"][1] = _num(have - take)
        entry["status"][1] = "depleted" if have - take <= 0 else st
    elif kind == "transfer":
        to = _place(hub, move.get("location"))
        to_ref = f"Location/{to['id']}"
        if to_ref == here:
            raise Refused(f"{name} is already in {to.get('name')}")
        if st == "quarantined" and _purpose(to) not in HOLDING:
            raise Refused(f"{name} is held: it leaves quarantine only by a signed release")
        if st != "quarantined" and _purpose(to) in HOLDING:
            raise Refused(f"{to.get('name')} keeps held stock: hold {name} instead (a hold may name where it goes)")
        if st == "available" and expired(lot, today):
            raise Refused(f"{name} expired on {lot['expiry']}: expired stock is not moved to storage; hold it")
        if whole:
            entry["location"][1] = to_ref
        else:
            split(st, to_ref)
    elif kind in ("count", "adjust"):
        if q in (None, "") or float(q) < 0:
            raise Refused("say how many there are (0 or more)")
        after = float(q)
        if lot.get("serial") and after not in (0, 1):
            raise Refused(f"{name} is one serial number: it counts 1 or 0")
        entry["quantity"][1] = _num(after)
        if after <= 0 and st == "available":
            entry["status"][1] = "depleted"
        if kind == "adjust":
            entry["baseline"][1] = _num(after)
            signoff = "an adjustment changes stock without a count"
        else:
            tol = pol["count_tolerance_pct"]
            drift = abs(after - base) / base * 100 if base else (0 if after == 0 else 100)
            if pol["signoff"] != "none" and tol is not None and drift > float(tol) + 1e-9:
                since = "" if base == have else \
                    f" (the counts since it was last set to {_num(base)} share one tolerance)"
                signoff = (f"counted {_num(after)} against {_num(base)}: {drift:.1f}% off{since}, beyond the "
                           f"{_num(tol)}% a count may change without a signature")
                entry["baseline"][1] = _num(after)  # a signed count is the new baseline
    elif kind == "hold":
        if st != "available":
            raise Refused(f"{name} is {st}: only available stock is held")
        where = here
        if move.get("location"):
            to = _place(hub, move.get("location"))
            if _purpose(to) not in HOLDING:
                raise Refused(f"{to.get('name')} is not a quarantine location: leave it empty to hold the stock where "
                              "it is")
            where = f"Location/{to['id']}"
        if whole:
            entry["status"][1], entry["location"][1] = "quarantined", where
        else:
            split("quarantined", where)
    elif kind == "release":
        if st != "quarantined":
            raise Refused(f"{name} is {st}: only held stock is released")
        if expired(lot, today):
            raise Refused(f"{name} expired on {lot['expiry']}: expired stock is not released; write it off")
        if not whole:
            raise Refused(f"{name} is released whole; hold the rest apart first")
        where = here
        if move.get("location"):
            to = _place(hub, move.get("location"))
            if _purpose(to) in HOLDING:
                raise Refused(f"{to.get('name')} keeps held stock: release {name} into storage")
            where = f"Location/{to['id']}"
        elif _purpose(_location(hub, here)) in HOLDING:
            raise Refused(f"{name} is in a quarantine location: say where it goes once released")
        entry["status"][1], entry["location"][1] = "available", where
        signoff = "a release puts held stock back into use"
    elif kind in ("scrap", "return"):
        gone = "scrapped" if kind == "scrap" else "returned"
        entry["quantity"][1] = _num(have - take)
        if have - take <= 0:
            entry["status"][1] = gone
        signoff = "a write-off takes stock away without an issue" if kind == "scrap" else \
            "a return sends stock back to its supplier"
    if pol["signoff"] == "none":
        signoff = ""
    return {"kind": kind, "entries": entries, "signoff": signoff, "note": note}


def _location(hub, ref) -> dict | None:
    """The record ``ref`` names (a location, a lot, a part), or None."""
    return hub.records.read(*str(ref).split("/", 1)) if ref and "/" in str(ref) else None


def _earlier(hub, lot: dict, today: str) -> dict | None:
    """An available, unexpired lot of the same part that expires before this one: the one to issue first."""
    if not lot.get("expiry"):
        return None
    best = None
    for other in lots_of(hub, lot["part"]):
        if other["id"] == lot["id"] or other.get("status") != "available" or not other.get("expiry"):
            continue
        if expired(other, today) or float(other.get("quantity") or 0) <= 0:
            continue
        if str(other["expiry"]) < str(lot["expiry"]) and (best is None or other["expiry"] < best["expiry"]):
            best = other
    return best


def _plan_receive(hub, move: dict, pol: dict, today: str) -> dict:
    part = _read(hub, move.get("part"), "Part", "part")
    use = part_in_use(hub, part)
    if use is None:
        raise Refused(f"{_name(part)} is {part.get('status')}: only a part in use is received")
    q = move.get("quantity")
    if q in (None, "") or float(q) <= 0:
        raise Refused("the quantity must be more than nothing")
    q = float(q)
    to = _place(hub, move.get("location"))
    if _purpose(to) in HOLDING:
        raise Refused(f"{to.get('name')} keeps held stock: receive into storage or receiving, then hold it")
    tracking = use.get("tracking") or "none"
    serials = [s.strip() for s in str(move.get("serials") or "").replace(",", "\n").splitlines() if s.strip()]
    lot_number = str(move.get("lot_number") or "").strip()
    if tracking == "serial":
        if not q.is_integer() or len(serials) != int(q):
            raise Refused(f"{_name(part)} is tracked by serial number: give one for each of the {_num(q)} received "
                          f"({len(serials)} given)")
        if len(set(serials)) != len(serials):
            raise Refused("a serial number is given twice")
        taken = sorted({lot.get("serial") for lot in lots_of(hub, f"Part/{part['id']}")} & set(serials))
        if taken:
            raise Refused(f"{_name(part)} already has serial number {', '.join(taken)}")
    elif serials:
        raise Refused(f"{_name(part)} is not tracked by serial number: leave the serial numbers empty")
    expiry = str(move.get("expiry") or "")
    if use.get("shelf_life_days") and not expiry:
        raise Refused(f"{_name(part)} has a shelf life: give the expiry date on the label")
    if expiry and expiry < today:
        raise Refused(f"it expired on {expiry}: expired stock is not received into use")
    status = "available"
    one = {"part": f"Part/{part['id']}", "parent": "", "lot_number": lot_number,
           "supplier_lot": str(move.get("supplier_lot") or ""), "expiry": expiry,
           "part_revision": use.get("revision"), "status": ["", status],
           "location": ["", f"Location/{to['id']}"], "lot": ""}
    if tracking == "serial":
        entries = [{**one, "serial": s, "quantity": [0, 1], "baseline": [0, 1]} for s in serials]
    else:
        entries = [{**one, "quantity": [0, _num(q)], "baseline": [0, _num(q)]}]
    return {"kind": "receive", "entries": entries, "signoff": "", "note": ""}


# ------------------------------------------------------------------ posting
def _same(planned: list[dict], checked: list[dict]) -> bool:
    """Whether the stock is as it was when the move was checked: each lot's quantity, status and place before, and
    what the move does to them."""
    keys = ("lot", "quantity", "status", "location", "baseline")
    return [{k: e.get(k) for k in keys} for e in planned] == [{k: e.get(k) for k in keys} for e in (checked or [])]


def post(hub, actor: dict, run_id: str, ref: str, move: dict, p: dict, pol: dict, authority: str = "") -> list[str]:
    """Write the plan: the lots, the move's ledger lines (with its place in the ledger) and the part's totals.
    Returns the lots written."""
    lots, parts, today = [], set(), _today(hub)
    for e in p["entries"]:
        values = {"quantity": e["quantity"][1], "status": e["status"][1], "location": e["location"][1],
                  "count_baseline": e["baseline"][1]}
        parts.add(e["part"])
        if e["lot"]:
            hub.update_record(actor, e["lot"], values, run_id=run_id)
        else:
            part = _read(hub, e["part"], "Part", "part")
            tracking = (part_in_use(hub, part) or part).get("tracking") or "none"
            number = e.get("lot_number") or ("" if tracking == "none" or e.get("serial") else
                                              f"LOT-{hub.db.next_seq('inventory.lot'):05d}")
            made = hub.create_record(actor, "StockItem", {
                **values, "part": e["part"], "part_code": part.get("code") or "", "lot": number,
                "serial": e.get("serial") or "",
                "supplier_lot": e.get("supplier_lot") or "", "expiry": e.get("expiry") or None,
                "part_revision": e.get("part_revision"), "parent": e.get("parent") or None,
                "received": move.get("moved") or today}, run_id=run_id, start_workflow=False)
            e["lot"], e["lot_number"] = f"StockItem/{made['id']}", number
        lots.append(e["lot"])
    first = p["entries"][0]
    hub.update_record(actor, ref, {
        "entries": p["entries"], "lot": move.get("lot") or first["lot"], "stock": _label(hub, p["entries"]),
        "before_quantity": first["quantity"][0], "after_quantity": first["quantity"][1],
        "policy": pol.get("ref") or f"module default ({pol['level']})", "level": pol["level"],
        **({"authority": authority} if authority else {}), "reject_reason": "",
        "posted_seq": hub.db.next_seq("inventory.posted")}, run_id=run_id)
    for part in sorted(parts):
        hub.update_record(actor, part, totals(hub, part, today), run_id=run_id)
    return lots


def totals(hub, part_ref: str, today: str) -> dict:
    """A part's stock, summed from its lots: on hand (every lot with stock in it), available (in use: not held, not
    waiting for inspection, not expired) and held."""
    on_hand = available = held = 0.0
    for lot in lots_of(hub, part_ref):
        q, st = float(lot.get("quantity") or 0), lot.get("status")
        if st in LIVE:
            on_hand += q
        if st == "quarantined":
            held += q
        if st == "available" and not expired(lot, today):
            available += q
    return {"on_hand": _num(on_hand), "available": _num(available), "quarantined": _num(held)}


def ledger_problems(hub, lot_ref: str) -> list[str]:
    """Where a lot's history does not add up: its posted moves, in order, must take it from nothing to what it holds
    now, each starting where the one before left it. Empty when it adds up."""
    lines = []
    for m in hub.records.search("StockMove", limit=None):
        if m.get("status") != "posted":
            continue
        for e in m.get("entries") or []:
            if e.get("lot") == lot_ref:
                lines.append((int(m.get("posted_seq") or 0), m["id"], e))
    lines.sort(key=lambda x: (x[0], x[1]))
    lot = hub.records.read(*lot_ref.split("/", 1)) or {}
    at, out = 0, []
    for _, mid, e in lines:
        if _num(e["quantity"][0]) != _num(at):
            out.append(f"StockMove/{mid} starts from {e['quantity'][0]}, not {_num(at)}")
        at = e["quantity"][1]
    if _num(at) != _num(lot.get("quantity")):
        out.append(f"its posted moves leave {_num(at)}; it holds {_num(lot.get('quantity'))}")
    return out


def _today(hub) -> str:
    eng = getattr(hub, "engine", None)
    return eng.now().date().isoformat() if eng else date.today().isoformat()


# ------------------------------------------------------------------ actions
def act_policy(engine, run, step, params, actor):
    """inventory.policy: the switches in force, for the run's conditions (``policy.signoff == 'second'``)."""
    return policy_in_force(engine.hub)


def act_check(engine, run, step, params, actor):
    """inventory.check: plan the move and write the plan onto it, before anyone signs. ``ok``; ``problem`` (why it
    is refused, also written on the move); ``signoff`` (why it needs a signature, "" when it posts at once);
    ``self`` and ``second`` (who may sign: its author too, or only someone else)."""
    hub = engine.hub
    ref = str(params.get("move") or run["record"])
    move = _read(hub, ref, "StockMove", "stock move")
    pol = policy_in_force(hub)
    try:
        p = plan(hub, move, pol, _today(hub))
    except Refused as r:
        hub.update_record(actor, ref, {"reject_reason": str(r)}, run_id=run["id"])
        return {"ok": False, "problem": str(r), "signoff": "", "self": False, "second": False, "note": ""}
    first = p["entries"][0]
    hub.update_record(actor, ref, {"entries": p["entries"], "stock": _label(hub, p["entries"]),
                                   "before_quantity": first["quantity"][0],
                                   "after_quantity": first["quantity"][1], "needs_signoff": p["signoff"],
                                   "reject_reason": ""}, run_id=run["id"])
    signed = bool(p["signoff"])
    return {"ok": True, "problem": "", "signoff": p["signoff"], "self": signed and pol["signoff"] == "self",
            "second": signed and pol["signoff"] == "second", "note": p["note"]}


def act_post(engine, run, step, params, actor):
    """inventory.post: plan the move again and write it, the only way a lot changes. Refused when the stock changed
    since the move was checked (a signature covers only what was checked), or when it no longer may move. ``ok``,
    ``problem``, ``lots`` (the lots it wrote). ``authority``: the record whose signed step posts it (a goods receipt,
    a nonconformance), kept on the move."""
    hub = engine.hub
    ref = str(params.get("move") or run["record"])
    move = _read(hub, ref, "StockMove", "stock move")
    pol = policy_in_force(hub)
    try:
        p = plan(hub, move, pol, _today(hub))
        if not _same(p["entries"], move.get("entries")) or p["signoff"] != (move.get("needs_signoff") or ""):
            raise Refused("the stock changed after this move was checked, so what was signed no longer holds: "
                          "revise it and submit it again")
    except Refused as r:
        hub.update_record(actor, ref, {"reject_reason": str(r)}, run_id=run["id"])
        return {"ok": False, "problem": str(r), "lots": []}
    lots = post(hub, actor, run["id"], ref, move, p, pol, str(params.get("authority") or ""))
    return {"ok": True, "problem": "", "lots": lots}


def act_policy_check(engine, run, step, params, actor):
    """inventory.policy-check: whether the policy being signed may come into force (``ok``, ``problem``) and whether
    someone other than its author must sign it (``second``: it loosens a control, and the policy in force or this one
    asks for a second person)."""
    hub = engine.hub
    ref = str(params.get("policy") or run["record"])
    rec = _read(hub, ref, "InventoryPolicy", "inventory policy")
    problems = policy_problems(rec)
    if problems:
        return {"ok": False, "problem": "; ".join(problems), "second": False}
    new, old = effective(rec), policy_in_force(hub)
    second = loosens(new, old) and "second" in (new["signoff"], old["signoff"])
    return {"ok": True, "problem": "", "second": second}


def act_policy_in_force(engine, run, step, params, actor):
    """inventory.policy-in-force: the policy just signed is the one in force; any other is retired."""
    hub = engine.hub
    ref = str(params.get("policy") or run["record"])
    retired = []
    for rec in hub.records.search("InventoryPolicy", limit=None):
        other = f"InventoryPolicy/{rec['id']}"
        if other != ref and rec.get("status") == "in-force":
            hub.update_record(actor, other, {"status": "retired"}, run_id=run["id"])
            retired.append(other)
    return {"retired": retired}


def act_expire(engine, run, step, params, actor):
    """inventory.expire: hold a lot that is past its expiry, where it stands, by a posted hold move (a hold is free
    and immediate at every level). Nothing when it is no longer available or not expired (held or issued since the
    clock chose it). ``move``: the hold it posted, or ""."""
    hub = engine.hub
    ref = str(params.get("lot") or run["record"])
    lot, today = _read(hub, ref, "StockItem", "stock (the lot or serial)"), _today(hub)
    if lot.get("status") != "available" or not expired(lot, today):
        return {"move": ""}
    pol = policy_in_force(hub)
    move = {"kind": "hold", "lot": ref, "part": lot.get("part"), "moved": today,
            "reason": f"expired on {lot['expiry']}: held by the daily expiry check"}
    p = plan(hub, move, pol, today)
    made = hub.create_record(actor, "StockMove", {**move, "status": "posted", "needs_signoff": ""}, run_id=run["id"],
                             start_workflow=False)
    mref = f"StockMove/{made['id']}"
    post(hub, actor, run["id"], mref, move, p, pol)
    return {"move": mref}


ACTIONS = {"inventory.policy": act_policy, "inventory.check": act_check, "inventory.post": act_post,
           "inventory.policy-check": act_policy_check, "inventory.policy-in-force": act_policy_in_force,
           "inventory.expire": act_expire}


def summarize(rec: dict) -> str:
    t = rec.get("resourceType")
    if t == "Part":
        return (f"{rec.get('code', '')} {rec.get('name', '')}: {_num(rec.get('available'))} {rec.get('unit') or ''} "
                f"available, {_num(rec.get('on_hand'))} on hand ({rec.get('status', '')})")
    if t == "StockItem":
        return (f"{_name(rec)}: {_num(rec.get('quantity'))} ({rec.get('status', '')})"
                + (f", expires {rec['expiry']}" if rec.get("expiry") else ""))
    if t == "StockMove":
        return f"Stock move: {rec.get('kind', '')} {rec.get('quantity', '')} ({rec.get('status', '')})"
    if t == "Location":
        return f"Storage location: {rec.get('name', '')}"
    if t == "InventoryPolicy":
        return f"Inventory policy: {rec.get('level', '')} ({rec.get('status', '')})"
    return ""


def demo(hub) -> None:
    """Demo only: policy owners hold the module's roles; a clinic with a supply room, a vaccine fridge and a quarantine
    shelf, a few parts, and their opening stock received by posted moves (under the module's own basic policy, so
    nothing waits for a signature)."""
    from practicehub.hub import member_ref

    owners = [m for m in hub.members("person") if hub.is_policy_owner(m)]
    if not owners:
        return
    hub.authz.write([(f"role:{r}", "assignee", member_ref(m["id"])) for m in owners
                     for r in ("stock-keeper", "purchaser", "inventory-manager", "stock-quality")])
    me = {"id": "module:inventory", "kind": "automation", "name": "Inventory demo", "status": "active"}
    site = hub.create_record(me, "Location", {"name": "Main clinic", "kind": "site", "structural": True})
    inside = {"within": f"Location/{site['id']}"}
    room = hub.create_record(me, "Location", {"name": "Supply room", "kind": "room", "purpose": "storage", **inside})
    fridge = hub.create_record(me, "Location", {"name": "Vaccine fridge", "kind": "fridge", "purpose": "storage",
                                                "cold_chain": True, **inside})
    hub.create_record(me, "Location", {"name": "Quarantine shelf", "kind": "shelf", "purpose": "quarantine", **inside})
    year = int(_today(hub)[:4])
    for code, name, cat, unit, tracking, life, where, at, qty, lots in (
            ("GL-M", "Nitrile gloves, medium", "clinical-supply", "box of 100", "none", None, room, 6, 20,
             [(24, "", None)]),
            ("GZ-10", "Sterile gauze 10x10", "clinical-supply", "pack of 10", "lot", 1095, room, 10, 50,
             [(40, "GZ2291", f"{year + 2}-03-31")]),
            ("SY-3", "Syringe 3 mL", "clinical-supply", "box of 100", "none", None, room, 4, 10, [(5, "", None)]),
            ("VX-FLU", "Influenza vaccine", "vaccine", "dose", "lot", 365, fridge, 15, 40,
             [(10, "FL4410", f"{year}-12-31"), (20, "FL5102", f"{year + 1}-05-31")])):
        part = hub.create_record(me, "Part", {k: v for k, v in {
            "code": code, "name": name, "category": cat, "unit": unit, "tracking": tracking,
            "shelf_life_days": life, "minimum_stock": at, "reorder_quantity": qty,
            "default_location": f"Location/{where['id']}"}.items() if v is not None})
        for quantity, lot, expiry in lots:
            move = hub.create_record(me, "StockMove", {k: v for k, v in {
                "kind": "receive", "part": f"Part/{part['id']}", "quantity": quantity,
                "location": f"Location/{where['id']}", "lot_number": lot, "expiry": expiry,
                "reason": "opening stock"}.items() if v})
            hub.update_record(me, f"StockMove/{move['id']}", {"status": "submitted"})
