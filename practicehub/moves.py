"""Moving a record on its own page: a lifecycle transition that checks what it needs, does the step it is for, and the
record's history in words.

A record type's lifecycle transitions say what a move needs (``asks``, ``requires``, ``sign``; see schema.py). One
call makes any move (``Hub.move_record``): whoever may make it, with the values its screen asked for and, for a signed
move, the mover's signature, written with the state in one transaction. While the type's workflow walks the record, the
open person's step of that run decides who may make the moves it governs (its role, and whoever holds it), and a step
with ``state`` is done by the move to that state: its form takes the transition's ``asks`` by field name, so doing the
transition is doing the step, and finishing the step on its item makes the same move through the same transition.
``Hub.transition_screen`` lists the moves a member may make now and why the others are not theirs to make;
``Hub.record_timeline`` tells the record's history in words, oldest first.
"""
from __future__ import annotations

import re

from . import schema, visibility
from .store import ASC


class MovesMixin:
    # ------------------------------------------------------------------ the open step and who may move
    def open_step(self, rt, ref: str):
        """The open person's step (a task or a decision) of the run of the type's own workflow walking ``ref``:
        (run, workflow, step, item), or None."""
        owner = (rt.workflow or {}).get("id")
        if not owner:
            return None
        for run in self.db.runs.find({"workflow_id": owner, "record": ref, "status": "running"}):
            try:
                wf = self.workflow(run["workflow_id"], run["version"])[0]
            except Exception:  # noqa: BLE001 - a run whose workflow cannot be read holds no step
                continue
            for s in wf.steps:
                if s.kind not in ("task", "gate") or s.for_each \
                        or (run["steps"].get(s.id) or {}).get("state") != "active":
                    continue
                item = self.db.items.find_one({"run_id": run["id"], "step_id": s.id, "kind": {"$ne": "notice"},
                                               "status": {"$in": ["open", "claimed"]}})
                if item:
                    return run, wf, s, item
        return None

    @staticmethod
    def step_makes_move(step, to: str) -> bool:
        """A person's step is done by the move its ``state`` names: the transition is the step."""
        return step is not None and step.kind == "task" and bool(step.state) and step.state == to

    def _holder_name(self, item: dict) -> str | None:
        who = item.get("claimed_by") if item.get("status") == "claimed" else item.get("assignee")
        if not who:
            return None
        row = self.db.members.get(who) or {}
        return row.get("name") or "someone else"

    def move_refusal(self, actor: dict, rt, ref: str, rec: dict, t: dict, opened=None) -> str | None:
        """Why ``actor`` may not make the move ``t`` of the record ``rec`` now, in words, or None. The transition's
        ``by``, a signature only a person gives, writing the record, the open step's role and its holder, and the
        workflow walking the record (``state_move_refusal``)."""
        label, to = rt.label.lower(), t["to"]
        where = rt.state_label(to).lower()
        if actor.get("status") != "active":
            return f"{actor.get('name') or 'this member'} is not active"
        if t["by"] == "workflow":
            return f"only its workflow moves a {label} to {where}"
        if t.get("sign"):
            if actor.get("kind") != "person":
                return f"moving it to {where} is signed {t['sign']!r}, and only a person signs (an agent never does)"
            kind = rt.signature(t["sign"])
            if kind and kind["roles"] and not set(kind["roles"]) & set(actor.get("roles") or []):
                return f"signing {t['sign']!r} needs the {' or '.join(kind['roles'])} role"
        elif not self.may_write(actor, rt.id):
            return f"moving a {label} needs writing {rt.data_class} records"
        hidden = visibility.hidden_keys(self, actor, rt.id, write=True)
        locked = [rt.field(n)["label"] for n in t["fields"] if rt.field(n)
                  and rt.field(n)["path"].split(".")[0] in hidden and n in t["require"]]
        if locked:
            return f"it needs {', '.join(locked)}, which you may not fill in"
        opened = self.open_step(rt, ref) if opened is None else opened
        if opened and "*" not in t["from"]:
            run, wf, step, item = opened
            if to == step.state or to in step.moves:
                holder = self._holder_name(item)
                held_by = item.get("claimed_by") if item.get("status") == "claimed" else item.get("assignee")
                if holder and held_by != actor.get("id"):
                    return f"{holder} holds '{step.title}'"
                if not held_by and item.get("role") and item["role"] not in (actor.get("roles") or []):
                    return f"'{step.title}' is for the {item['role']} role"
                if self.step_makes_move(step, to):
                    ok, why = self.engine.eligibility(actor, item)
                    if not ok:
                        return why
        return self.state_move_refusal(rt, ref, t, rec, None)

    # ------------------------------------------------------------------ the move
    def move_record(self, actor: dict, ref: str, to: str, values: dict | None = None, signature=None, *,
                    comment: str | None = None, expected_version: str | None = None) -> dict:
        """Move the record to ``to`` through its lifecycle transition, writing ``values`` (the fields the move asks
        for) and the state in one transaction; refused, in words, unless ``actor`` may make that move now
        (``move_refusal``) and what it requires is filled in. ``signature``: for a transition with ``sign``, the
        mover's signature: its meaning (a string) or ``{"meaning", "comment", "seen"}`` (``seen``: the
        ``record_digest`` the mover read). A move that is what the open step of the record's own workflow is for
        (its ``state``) does that step, with the step's fields taken from the move's values."""
        from .hub import Denied, Invalid, NotFound
        rtype, _, rid = ref.partition("/")
        rt = self._type(rtype)
        if not rt.lifecycle:
            raise Invalid(f"a {rt.label.lower()} has no states to move through")
        self.require(actor, "reader", f"data_class:{rt.data_class}", f"read {rtype} records")
        values = dict(values or {})
        with self.db:
            rec = self.records.read(rtype, rid)
            if not rec:
                raise NotFound(f"there is no {ref}")
            old = schema.first(rec, rt.lifecycle["path"])
            t = rt.transition(old, to)
            if t is None or old == to:
                raise Invalid(f"a {rt.label.lower()} does not move from {rt.state_label(old).lower() or 'nowhere'} "
                              f"to {rt.state_label(to).lower()}")
            extra = [k for k in values if k not in t["fields"]]
            if extra:
                asked = ", ".join(rt.field(n)["label"] for n in t["fields"]) or "nothing"
                raise Invalid(f"moving it to {rt.state_label(to).lower()} asks for {asked}, not "
                              + ", ".join(str(rt.field(k)["label"] if rt.field(k) else k) for k in extra))
            after = dict(rec)
            for n, v in values.items():
                after = schema.put(after, rt.field(n)["path"], v)
            after = schema.put(after, rt.lifecycle["path"], to)
            why = self.move_refusal(actor, rt, ref, after, t)
            if why:
                self.track.append(actor, "access.denied", ref, data={"what": f"move to {to}", "why": why})
                raise Denied(why)
            sig = signature if isinstance(signature, dict) else {"meaning": signature} if signature else None
            seq = None
            if t.get("sign"):
                if not sig or (sig.get("meaning") or t["sign"]) != t["sign"]:
                    raise Invalid(f"moving it to {rt.state_label(to).lower()} is signed {t['sign']!r}: sign it "
                                  f"with that meaning")
                if sig.get("seen") is not None and sig["seen"] != self._record_digest(rec):
                    raise Invalid("the record changed since you read it: read it again, then sign what it says now")
                signed_rec = {k: v for k, v in after.items() if v is not None}
                seq = self.track.append(actor, "record.signed", ref, data={"comment": sig.get("comment") or ""},
                                        signed={"record": ref, "version": self.record_version(ref) + 1,
                                                "record_sha256": self._record_digest(signed_rec),
                                                "meaning": t["sign"]})
            elif sig:
                raise Invalid(f"moving it to {rt.state_label(to).lower()} is not signed")
            return self.update_record(actor, ref, after, _signature=seq, comment=comment,
                                      expected_version=expected_version)

    def _step_by_move(self, rt, ref: str, rec: dict, member: dict, t: dict, cause: int) -> bool:
        """The record moved to the state its open step makes (``state``): that move is the step, done by whoever
        moved it, with the step's fields taken from the record's fields the move asks for (by name). True if a step
        was done."""
        opened = self.open_step(rt, ref)
        if not opened or not self.step_makes_move(opened[2], t["to"]):
            return False
        run, wf, step, item = opened
        names = {f["name"] for f in self.engine.fields_for(step, run)}
        answer = {}
        for n in t["fields"]:
            f = rt.field(n)
            if n in names and f:
                v = schema.first(rec, f["path"])
                if v not in (None, "", [], {}):
                    answer[n] = v
        run["steps"][step.id]["moved"] = True  # the record is where the step puts it: nothing more to move
        self.db.runs.set(run["id"], {"steps": run["steps"]})
        self.engine.complete(member, item["id"], answer)
        return True

    # ------------------------------------------------------------------ the transition screen
    def transition_screen(self, actor: dict, ref: str) -> dict:
        """The moves ``actor`` may make of the record now, each with its label, the fields it asks for (their
        definitions and what they hold now), what it requires, the signature it asks for and whether it is the
        forward move (the one the open step makes, else the first way on that is not a correction); and the moves
        out of where it is that they may not make, each with why in words."""
        rtype, _, rid = ref.partition("/")
        rt = self._type(rtype)
        rec = self.read_record(actor, ref)
        if not rt.lifecycle:
            return {"state": None, "state_label": "", "may": [], "may_not": []}
        raw = self.records.read(rtype, rid)
        cur = schema.first(raw, rt.lifecycle["path"])
        opened = self.open_step(rt, ref) or False
        exits = [t for t in rt.lifecycle["transition"] if (cur in t["from"] or "*" in t["from"]) and t["to"] != cur]
        made = opened[2].state if opened and opened[2].kind == "task" and opened[2].state else None
        forward = made or next((t["to"] for t in exits if "*" not in t["from"] and t["by"] != "workflow"), None)
        may, may_not = [], []
        for t in exits:
            entry = {"to": t["to"], "label": t["label"], "state_label": rt.state_label(t["to"])}
            why = self.move_refusal(actor, rt, ref, schema.put(dict(raw), rt.lifecycle["path"], t["to"]), t,
                                    opened)
            if why:
                may_not.append({**entry, "why": why})
                continue
            asks = []
            for n in t["fields"]:
                f = rt.field(n)
                if f and visibility.may_field(self, actor, f, write=True):
                    asks.append({**{k: v for k, v in f.items() if k not in ("path",)}, "value": schema.first(rec, f["path"]),
                                 "required": f.get("required") is True or n in t["require"]})
            may.append({**entry, "asks": asks, "requires": [rt.field(n)["label"] for n in t["require"]],
                        "sign": t.get("sign"), "comment": t.get("comment", False), "forward": t["to"] == forward,
                        **({"does": opened[2].title} if opened and self.step_makes_move(opened[2], t["to"]) else {})})
        return {"state": cur, "state_label": rt.state_label(cur), "may": may, "may_not": may_not}

    # ------------------------------------------------------------------ the record's history in words
    def record_timeline(self, viewer: dict, ref: str) -> list[dict]:
        """The record's history as one list in words, oldest first: its edits (which fields changed), its moves (who,
        when, the values the move wrote, the signature it carried) and the steps of the runs about it (started, done
        by whom, decided, skipped, waiting). What the viewer may not read is folded, one line per hidden stretch
        ("3 clinical steps"). Each entry is ``{"at", "who", "text"}`` (``hidden``: the count it folds)."""
        from .export import _item_words
        rtype, _, rid = ref.partition("/")
        rt = self._type(rtype)
        self.require(viewer, "reader", f"data_class:{self.class_of(rtype)}", f"read {rtype} records")
        view = visibility.Viewer(self, viewer)
        gone = visibility.hidden_keys(self, viewer, rtype)
        label = rt.label.lower()
        names: dict = {}

        def who(mid):
            if mid not in names:
                row = self.db.members.get(mid) if mid else None
                names[mid] = (row or {}).get("name") or ("the hub" if not mid or ":" in str(mid) else "someone")
            return names[mid]

        def words(f, v) -> str:
            text = _item_words(self, viewer, f, v)
            return re.sub(r" \([A-Z][A-Za-z]*/[^)]+\)", "", text)

        lc = rt.lifecycle
        top = lambda f: f["path"].split(".")[0]
        fields = [f for f in rt.fields if "*" not in f["path"] and not (lc and f["path"] == lc["path"])]
        versions = self.db.record_versions.find({"type": rtype, "id": rid}, sort=[("version", ASC)])
        events = self.db.events.find({"subject": ref, "action": {"$in": ["record.created", "record.updated",
                                                                          "record.transitioned", "record.signed"]}},
                                     sort=[("seq", ASC)])
        writes = [e for e in events if e["action"] in ("record.created", "record.updated")]
        # each version is written by the event of its write; versions written without one (seeded) come first
        lead = len(versions) - len(writes)
        at_seq = {e["seq"]: v for e, v in zip(writes[max(0, -lead):], versions[max(0, lead):])}
        moves = {e["cause"]: e for e in events if e["action"] == "record.transitioned"}
        signed = {(e["data"].get("signed") or {}).get("version"): e for e in events if e["action"] == "record.signed"
                  and e.get("signature")}
        rows: list[tuple[int, dict]] = []
        prev = None
        for v in versions[:max(0, lead)]:
            rows.append((-1, {"at": v["at"], "who": who(v["actor"]), "text": f"{rt.label} filed"}))
            prev = v["data"]
        for e in writes:
            v = at_seq.get(e["seq"])
            if v is None:
                continue
            data = v["data"]
            mv = moves.get(e["seq"])
            changed = [f for f in fields if prev is not None
                       and schema.first(data, f["path"]) != schema.first(prev, f["path"])]
            seen_ = [f for f in changed if top(f) not in gone]
            if prev is None:
                text, hide = f"{rt.label} created", None
                if lc:
                    text += f" ({rt.state_label(schema.first(data, lc['path'])).lower()})"
            elif mv:
                text = (f"Moved from {rt.state_label(mv['data'].get('from')).lower()} to "
                        f"{rt.state_label(mv['data'].get('to')).lower()}")
                said = [f"{f['label']}: {words(f, schema.first(data, f['path']))}" for f in seen_
                        if schema.first(data, f["path"]) not in (None, "", [], {})]
                if said:
                    text += ", with " + "; ".join(said)
                if len(seen_) < len(changed):
                    text += " (and fields you may not read)"
                sg = signed.pop(v["version"], None)
                if sg:
                    text += f"; signed {sg['data']['signed'].get('meaning')!r} by {who(sg['actor'])}"
                hide = None
            elif changed and not seen_:
                text, hide = "", self._classes_of(rt, changed)
            elif changed:
                text, hide = "Changed " + ", ".join(f["label"] for f in seen_), None
                if len(seen_) < len(changed):
                    text += " and fields you may not read"
            else:
                text, hide = "Saved with no change to what it says", None
            if v.get("comment") and hide is None:
                text += f": {v['comment']}"
            row = {"at": v["at"], "who": who(v["actor"]), "text": text}
            if hide:
                row = {"at": v["at"], "who": "", "text": "", "fold": ("change", hide)}
            rows.append((e["seq"], row))
            prev = data
        for e in signed.values():  # a signature that moved nothing
            rows.append((e["seq"], {"at": e["at"], "who": who(e["actor"]),
                                    "text": f"Signed {(e['data'].get('signed') or {}).get('meaning')!r} by "
                                            f"{who(e['actor'])}"}))
        rows += self._run_rows(viewer, view, ref, who)
        rows.sort(key=lambda r: r[0])
        return _fold([r for _, r in rows])

    def _classes_of(self, rt, fields) -> str:
        classes = sorted({f.get("data_class") or rt.data_class for f in fields})
        return " and ".join(classes)

    def _run_rows(self, viewer, view, ref: str, who) -> list[tuple[int, dict]]:
        runs = {r["id"]: r for r in self.db.runs.find({"record": ref})}
        if not runs:
            return []
        acts = ["run.started", "run.completed", "run.cancelled", "run.stopped", "step.activated", "step.done",
                "step.skipped", "step.waiting", "step.failed", "item.completed", "gate.decided", "action.ran"]
        out = []
        for e in self.db.events.find({"run_id": {"$in": list(runs)}, "action": {"$in": acts}}, sort=[("seq", ASC)]):
            run = runs[e["run_id"]]
            wf = view.workflow_of_run(run["id"])
            title = str(run.get("title") or "").split(": ", 1)[0] or "the work"
            act = e["action"]
            if act.startswith("run."):
                done = {"run.started": "started", "run.completed": "finished", "run.cancelled": "cancelled",
                        "run.stopped": "stopped"}[act]
                why = (e.get("data") or {}).get("reason") or (e.get("data") or {}).get("why")
                out.append((e["seq"], {"at": e["at"], "who": who(e["actor"]) if act != "run.completed" else "",
                                       "text": f"'{title}' {done}" + (f": {why}" if why and isinstance(why, str)
                                                                       else "")}))
                continue
            sid = (e.get("data") or {}).get("step")
            step = None
            if wf is not None and sid:
                try:
                    step = wf.step(sid)
                except KeyError:
                    step = None
            if step is None:
                continue
            if act == "action.ran" and not step.state:
                continue  # an automation's inner workings: its move shows as the record's own
            ok, missing = view.step_visible(wf, sid)
            if not ok:
                out.append((e["seq"], {"at": e["at"], "who": "", "text": "",
                                       "fold": ("step", " and ".join(missing))}))
                continue
            name = f"'{step.title}'"
            data = e.get("data") or {}
            if act == "step.activated":
                text = f"{name} started" + (f", for the {self.role_name(step.role)}" if step.role else "")
                actor = ""
            elif act == "step.waiting":
                text, actor = f"{name} waiting", ""
            elif act == "step.skipped":
                text, actor = f"{name} skipped", ""
            elif act == "step.failed":
                text, actor = f"{name} did not go through", ""
            elif act == "gate.decided":
                outcome = (data.get("output") or {}).get("outcome")
                text = f"{name} decided" + (f": {schema.state_words(outcome).lower()}" if outcome else "")
                text += " (signed)" if e.get("signature") else (" (an agent's decision)" if e.get("actor_kind") == "agent"
                                                                 else "")
                actor = who(e["actor"])
            elif act == "item.completed":
                text, actor = f"{name} done", who(e["actor"])
            elif act == "action.ran":
                text, actor = f"{name} done", ""
            else:  # step.done: a wait that ended
                text, actor = f"{name} done", ""
            out.append((e["seq"], {"at": e["at"], "who": actor, "text": text + (f" by {actor}" if actor and
                                                                                act in ("item.completed", "gate.decided")
                                                                                else "")}))
        return out

    def role_name(self, rid: str) -> str:
        try:
            return f"{self.role(rid)['name'].lower()} role"
        except Exception:  # noqa: BLE001
            return f"{rid} role"


def _fold(rows: list[dict]) -> list[dict]:
    """One line per stretch of what the viewer may not read: "3 clinical steps", "2 changes to clinical fields"."""
    out: list[dict] = []
    for r in rows:
        fold = r.pop("fold", None)
        if not fold:
            out.append(r)
            continue
        last = out[-1] if out else None
        if last is not None and last.get("_fold") == fold:
            last["hidden"] += 1
        else:
            last = {"at": r["at"], "who": "", "text": "", "hidden": 1, "_fold": fold}
            out.append(last)
        kind, cls = fold
        n = last["hidden"]
        last["text"] = (f"{n} {cls} step{'s' if n != 1 else ''}" if kind == "step"
                        else f"{n} change{'s' if n != 1 else ''} to {cls} fields")
    for r in out:
        r.pop("_fold", None)
    return out
