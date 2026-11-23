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
