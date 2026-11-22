"""The Hub: one practice's members, permissions, records, workflows and track, in one object.

Everything a person does in the web shell and everything an agent does over the API goes through these methods, with
the acting member passed in. There is no back door for agents: they call the same API with their own credentials.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import blocks as blockmod
from . import markup
from . import schema
from . import visibility
from . import pack as packmod
from . import workflows as wfmod
from .authz import Authorizer, LocalAuthorizer
from .db import Database, open_database, utcnow
from .store import ASC, DESC
from .catalog import CatalogMixin
from .credentials import CredentialsMixin
from .outbox import OutboxMixin
from .records import LocalRecordStore, RecordStore
from .track import Track, hub_secret

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
APPROVAL_WORKFLOW = "approval-notice"  # the home of a notice that a change waits for a policy owner's approval
APPROVAL_SOURCE = (f'id = "{APPROVAL_WORKFLOW}"\ntitle = "Waiting for a policy owner"\n'
                   'summary = "A change someone asked for waits for a second policy owner to approve it."\n'
                   '[[steps]]\nid = "look"\ntitle = "Approve or decline it"\nkind = "wait"\nuntil = "false"\n')


class HubError(Exception):
    status = 400


class Denied(HubError):
    status = 403


class NotFound(HubError):
    status = 404


class Invalid(HubError):
    status = 422

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = problems or [message]


class Conflict(Invalid):
    """The thing changed since the caller read it."""
    status = 409


class Duplicate(Conflict):
    """Another open run of the workflow already has the value its ``trigger.one_open_per`` names. ``run`` is that run;
    ``allowed``: the trigger lets the starter confirm and start another anyway (``allow_duplicate``)."""

    def __init__(self, message: str, run: dict, allowed: bool):
        super().__init__(message)
        self.run = run
        self.allowed = allowed


EVERYONE = "everyone"  # a role every person holds, and nobody assigns: [grants.everyone] gives it read access only


def member_ref(mid: str) -> str:
    return f"member:{mid}"



def hmac_ok(track, kept: dict) -> bool:
    """Whether a kept verify result carries this hub's seal (it was not written into the settings by hand)."""
    import hmac as _hmac
    from .track import canonical
    try:
        want = track.seal("verified:" + canonical({k: x for k, x in kept.items() if k != "mac"}))
        return _hmac.compare_digest(str(kept.get("mac", "")), want) and bool(kept.get("head"))
    except (TypeError, ValueError):
        return False


_SHA256 = re.compile(r"[0-9a-f]{64}")


def _files_in(value) -> list[dict]:
    """Every attachment in a record that names a file in the hub's file store: ``{blob: <sha256>, ...}``."""
    out, stack = [], [value]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            if isinstance(v.get("blob"), str) and _SHA256.fullmatch(v["blob"]):
                out.append(v)
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)
    return out


def _refs_in(value) -> list[str]:
    """Every "Type/id" string anywhere in a record: what the access report finds a record by, whoever it is about."""
    out: set[str] = set()
    stack = [value]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)
        elif isinstance(v, str) and "/" in v and len(v) <= 200:
            out.add(v)
    return sorted(out)


def _counted(got: dict) -> dict:
    """A batch's result for the track: a list by its length (each record it names has an event of its own)."""
    return {k: len(v) if isinstance(v, (list, tuple)) else v for k, v in got.items()}


def _merge_upgrade_results(so_far: dict, got: dict) -> dict:
    """What an upgrade's batches did, together: numbers (and lists, by their length) add up, a yes stays yes."""
    out = dict(so_far)
    for k, v in _counted(got).items():
        old = out.get(k)
        if isinstance(v, bool) or isinstance(old, bool):
            out[k] = bool(old) or bool(v)
        elif isinstance(v, (int, float)) and isinstance(old, (int, float)):
            out[k] = old + v
        else:
            out[k] = v
    return out


def _how(data: dict) -> str:
    """How a record was seen, for an access report: 'export (csv)' when it went out in a file."""
    how = data.get("how", "")
    return f"{how} ({data['format']})" if how == "export" and data.get("format") else how


class Hub(CatalogMixin, CredentialsMixin, OutboxMixin):
    def __init__(self, state_dir: Path | str | None, *, pack: str | packmod.Pack | None = None,
                 authorizer: Authorizer | None = None, records: RecordStore | None = None, models=None):
        self.state_dir = Path(state_dir) if state_dir else None
        # the practice's database (PRACTICEHUB_DATABASE); a hub with no state directory gets a scratch one
        self.db = open_database() if self.state_dir else open_database(database=f"ph_{secrets.token_hex(6)}")
        anchors = os.environ.get("PRACTICEHUB_ANCHORS") or (self.state_dir / "track.anchors" if self.state_dir else None)
        self.track = Track(self.db, hub_secret(self.state_dir), anchors)
        self.authz: Authorizer = authorizer or LocalAuthorizer(self.db)
        self.records: RecordStore = records or LocalRecordStore(self.db)
        pack_name = pack if pack is not None else self.db.get_setting("pack")
        self.pack: packmod.Pack | None = None
        if isinstance(pack_name, packmod.Pack):
            self.pack = pack_name
        self.module_problems: dict[str, str] = {}
        self.pack_problem: str | None = None
        # modules another process (the command line) installs, upgrades or removes are picked up by sync_modules
        self._modules_live = not isinstance(pack_name, packmod.Pack)
        self._modules_lock = threading.RLock()
        self._module_seq: dict[str, int | None] = {}  # the sealed install/upgrade event each loaded module came from
        self._modules_seen: tuple | None = None
        if pack_name and not isinstance(pack_name, packmod.Pack):
            self.pack = self._load_practice_pack(pack_name) if pack is None else packmod.load(pack_name)
        if self.pack and pack is None:  # modules added to the running practice, in the order they were installed
            if self._pack_in_force() is not None:  # a module the pack names but nobody installed is not used
                have = set(self.installed_modules())
                for m in [m for m in self.pack.modules if m.id not in have]:
                    self.pack.modules.remove(m)
                    self.module_problems[m.id] = "the practice pack names it, but it was never installed"
            self._load_installed_modules()
        if self.pack:
            self._modules_noted()
        self._defs_reset()
        self._sealed_ok: set = set()
        from .restrictions import Restrictions
        self.restrictions = Restrictions(self)  # a subject's limits on who sees what of their file
        self._subjects()
        if self.pack and pack is None:
            self._everyone_holds_everyone()
            self._search_ready(self.db.records.distinct("type"))  # records written before their words were kept
        from .models import ModelGateway
        self.models = models or ModelGateway(self)
        from .engine import Engine
        self.engine = Engine(self)
        self.engine.zone()  # an unknown time zone, or no tz data on this machine, fails here: never wrong dates later
        if self.db.runs.exists({"status": "running", "$or": [{"item": {"$exists": False}}, {"fold_failed": True}]}):
            self.engine.fold_run_items()  # open runs from before one item walked each run's workflow
        self.agents_wanted = threading.Event()  # set when new work appears; an in-process agent loop waits on it
        if self.pack and pack is None and self.pack.upgrades and self._pack_in_force() is not None:
            # an upgrade whose definitions were approved before this pack.py was running, or one a restart stopped
            # part way: the hub still opens if it fails, and the track says what did not happen
            self._upgrades_after(None)

    # ------------------------------------------------------------------ the practice pack, under change control
    PACK_CONFIG = ("practice", "subject", "calendar", "events", "record_labels", "roles", "data_classes", "grants",
                   "portal")

    @classmethod
    def _pack_config(cls, p: packmod.Pack) -> dict:
        return {k: getattr(p, k) for k in cls.PACK_CONFIG}

    def _pack_in_force(self) -> dict | None:
        """The practice pack as it was installed or last upgraded, from the signed track: digest and pack.py."""
        rows = self.track.trusted({"action": {"$in": ["practice.installed", "practice.upgraded", "practice.pinned"]}},
                                  sort=[("seq", DESC)], limit=1)
        return rows[0]["data"] if rows else None

    def _load_practice_pack(self, name: str) -> packmod.Pack:
        """The practice pack, as reviewed: its pack.py runs only if it is the code put into force, and its settings
        (subject, calendar, events, labels, the practice's own) are the ones put into force, whatever the files on
        disk say now. A change on disk waits for ``pack review`` and ``pack upgrade``, by two policy owners."""
        trusted = self._pack_in_force()
        if trusted is None:  # installed (it is in the settings) with no sealed record of what: fail closed
            p = packmod.load(name, run_code=False)
            self.pack_problem = ("there is no signed record of the practice pack put into force, so its code is not "
                                 "run: a policy owner reviews it and another upgrades to it (pack review / pack upgrade)")
            trusted = {}
        else:
            try:
                p = packmod.load(name, expect_sha256=trusted.get("pack_py_sha256"), run_modules=False)
            except ValueError as exc:
                p = packmod.load(name, run_code=False)  # its definitions only; its code is not run
                self.pack_problem = str(exc)
        cfg = self.db.get_setting("pack_config")
        if cfg is None:
            # Settings are pinned only from exactly the files that were signed into force; otherwise nothing is
            # pinned without a person (pack review / pack upgrade).
            if trusted and p.digest == trusted.get("digest") and not self.pack_problem:
                self.db.set_setting("pack_config", self._pack_config(p))
            elif not self.pack_problem:
                self.pack_problem = ("the practice pack's settings are not pinned and the files differ from those put "
                                     "into force: a policy owner reviews them and another upgrades")
        else:
            new_keys = [k for k in self.PACK_CONFIG if k not in cfg]
            if new_keys:  # settings put under change control by a newer hub
                disk = self._pack_config(p)
                if trusted and p.digest == trusted.get("digest") and not self.pack_problem:
                    pinned = {k: disk[k] for k in new_keys}  # exactly the files signed into force: as they are
                else:
                    # files that differ from those put into force (or no record of them): the new setting starts
                    # empty, and what the files say of it waits for review and upgrade like any other change
                    pinned = {k: type(disk[k])() if isinstance(disk[k], (dict, list)) else None for k in new_keys}
                cfg = {**cfg, **pinned}
                self.db.set_setting("pack_config", cfg)
            changed = sorted(k for k in self.PACK_CONFIG if k in cfg and cfg[k] != self._pack_config(p)[k])
            for k in self.PACK_CONFIG:
                if k in cfg:
                    setattr(p, k, cfg[k])
            last = trusted.get("digest")
            if last and p.digest != last and not self.pack_problem:
                self.pack_problem = ("the practice pack changed on disk since it was put into force"
                                     + (f" ({', '.join(changed)})" if changed else "")
                                     + ": the change is not used until a policy owner reviews it and another "
                                       "upgrades to it (practicehub pack review / pack upgrade)")
        if self.pack_problem:
            last = self.db.events.find_one({"action": "practice.refused"}, sort=[("seq", DESC)])
            if not last or last["data"].get("problem") != self.pack_problem:
                self.track.append({"id": "hub", "kind": "automation", "name": "Practice Hub"}, "practice.refused",
                                  f"practice:{p.practice.get('id', p.id)}", data={"problem": self.pack_problem})
        return p

    def pack_reviewers(self, digest: str) -> list[str]:
        rows = self.track.trusted({"action": "practice.code_approved", "data.digest": digest})
        return sorted({r["actor"] for r in rows})

    def review_pack(self, actor: dict) -> dict:
        """A policy owner signs that they reviewed the practice pack as it is on disk (its pack.py and settings)."""
        if actor.get("kind") != "person":
            raise Denied("the practice pack is reviewed by a person, never an agent")
        self.require(actor, "policy_owner", "practice:main", "review the practice pack")
        p = packmod.load(self.db.get_setting("pack"), run_code=False)
        data = {"pack": p.id, "version": p.version, "digest": p.digest, "pack_py_sha256": p.module_sha256}
        with self.db:
            self.track.append(actor, "practice.code_approved", f"practice:{p.practice.get('id', p.id)}", data=data,
                              signed={**data, "meaning": f"I have reviewed the practice pack {p.id}, version "
                                                         f"{p.version}: its code and its settings"})
        return data

    def upgrade_pack(self, actor: dict) -> dict:
        """Put the practice pack as it is on disk into force: its pack.py and its settings. Another policy owner
        must have reviewed exactly these files. Record types, workflows and the rest are versioned definitions: a new
        one arrives active; a changed one is proposed here, and goes live when a policy owner other than the one
        upgrading approves it (``proposed`` in the result)."""
        if actor.get("kind") != "person":
            raise Denied("the practice pack is upgraded by a person")
        self.require(actor, "policy_owner", "practice:main", "upgrade the practice pack")
        p = packmod.load(self.db.get_setting("pack"), run_code=False)
        if p.digest == (self._pack_in_force() or {}).get("digest"):
            raise Invalid("the practice pack on disk is the one in force")
        if not [r for r in self.pack_reviewers(p.digest) if r != actor["id"]]:
            raise Denied("the practice pack as it is on disk has not been reviewed by another policy owner")
        before = self.db.get_setting("pack_config") or {}
        cfg = self._pack_config(p)
        new_grants = {tuple(t) for t in self._grant_tuples(p)}
        # what access the practice's roles hold now, not what a pack object in memory (or on disk) says they should:
        # a grant the reviewed pack no longer gives is taken away whatever put it there
        roles = {f"role:{r}#assignee" for r in [EVERYONE, *(x["id"] for x in p.roles),
                                                 *(x["id"] for x in (self.pack.roles if self.pack else []))]}
        old_grants = {tuple(t) for t in self.authz.read() if t[2] in roles and (
            (t[0].startswith("data_class:") and t[1] in ("reader", "writer")) or
            (t[0] == "practice:main" and t[1] in ("policy_owner", "member_admin", "break_glass")))}
        new_roles = [r for r in p.roles if not self.db.roles.exists({"_id": r["id"]})]
        new_classes = [d for d in p.data_classes if not self.db.data_classes.exists({"_id": d["id"]})]
        # the definitions below are checked against the roles and data classes this pack brings, not the ones in
        # force before it (a new workflow may use a new data class); put back if the upgrade does not go through
        held = {k: getattr(self.pack, k) for k in self.PACK_CONFIG} if isinstance(self.pack, packmod.Pack) else None
        try:
            return self._upgrade_pack_in(actor, p, cfg, before, new_grants, old_grants, new_roles, new_classes, held)
        except BaseException:
            if held is not None:
                for k, v in held.items():
                    setattr(self.pack, k, v)
                self._defs_reset()
            raise

    def _upgrade_pack_in(self, actor, p, cfg, before, new_grants, old_grants, new_roles, new_classes, held) -> dict:
        with self.db:
            self.db.set_setting("pack_config", cfg)
            if held is not None:
                for k in self.PACK_CONFIG:
                    setattr(self.pack, k, cfg[k])
                self._defs_reset()
            # what the reviewed settings say about access takes effect now, not only on paper
            for r in new_roles:
                self.db.roles.insert({"_id": r["id"], "id": r["id"], "name": r["name"],
                                      "description": r.get("description", ""), "module": ""})
            for d in new_classes:
                self.db.data_classes.insert({"_id": d["id"], "id": d["id"], "name": d.get("name", d["id"]),
                                             "description": d.get("description", ""), "module": ""})
            added = sorted(new_grants - old_grants)
            if added:
                self.authz.write(added)
            removed = old_grants - new_grants
            others = {tuple(t) for x in self.installed_modules() for t in self._module_grants(x)}
            drop = sorted(t for t in removed if t not in others)
            if drop:
                self.authz.delete(drop)
            self.db.set_setting("practice", {**(self.db.get_setting("practice", {}) or {}), **p.practice})  # 
            # what policy owners changed on the settings page stays on top (practice_local)
            root = self.track.append(actor, "practice.upgraded", f"practice:{p.practice.get('id', p.id)}",
                              data={"pack": p.id, "version": p.version, "digest": p.digest,
                                    "pack_py_sha256": p.module_sha256,
                                    "changed": sorted(k for k in cfg if before.get(k) != cfg[k]),
                                    "roles_added": [r["id"] for r in new_roles],
                                    "data_classes_added": [d["id"] for d in new_classes],
                                    "grants_added": [list(t) for t in added], "grants_revoked": [list(t) for t in drop]},
                              signed={"pack": p.id, "version": p.version, "digest": p.digest,
                                      "meaning": f"I put the practice pack {p.id}, version {p.version}, into force"})
            # the pack's types, workflows, views, skills and blocks: a new one arrives active, as on install; a
            # changed one is proposed, for a policy owner other than the one upgrading to approve
            added_defs, proposed = [], []
            for kind, did, src in self._module_definitions(p):
                row = self._active_row(kind, did)
                if row is None and not self.db[self.KINDS[kind][0]].exists({self.KINDS[kind][1]: did}):
                    self._check_source(kind, did, src)
                    self._put_version(actor, kind, did, src, f"Added with the practice pack {p.version}", "", root,
                                      f"{kind}.installed")
                    if kind == "workflow":
                        self._sync_starters(self.check_workflow(src))
                    added_defs.append(f"{kind} {did}")
                elif row is not None and row["source"] != src and not self.db[self.KINDS[kind][0]].exists(
                        {self.KINDS[kind][1]: did, "status": "proposed", "source": src}):
                    proposed.append({"kind": kind, "id": did, **self._propose(
                        kind, actor, did, src, f"The practice pack {p.version} changes this", module=None)})
            self._defs_reset()
            reach = self.reach_sweep(actor, f"the practice pack {p.version} changed what roles reach")
        # what this process's pack.py can do now (the new one runs after the restart), in batches of their own: the
        # upgrade stands whether or not they finish here, and what they did not finish goes on at the next start
        self._upgrades_after(actor)
        return {"pack": p.id, "version": p.version, "restart": True, "added": added_defs, "proposed": proposed,
                **reach}

    UPGRADE_BATCH = 200       # records a pack upgrade converts in one transaction
    upgrade_state: dict = {}  # the running upgrade's own place, kept with each batch (run_pack_upgrades)

    def _upgrades_after(self, actor: dict | None) -> None:
        if self.db.in_transaction():
            return  # never inside another transaction: they run on their own, at the next call or the next start
        try:
            self.run_pack_upgrades(actor)
        except Exception as exc:  # noqa: BLE001 - what was approved stands; the upgrade goes on at the next start
            self.track.append({"id": "hub", "kind": "automation", "name": "Practice Hub"}, "practice.upgrade_failed",
                              f"practice:{(self.pack.practice or {}).get('id', self.pack.id)}",
                              data={"error": str(exc)[:500]})

    UPGRADE_PROGRESS = "pack_upgrade_progress"  # setting: {upgrade id: {"batches": n, "state": {...}, "done": {...}}}

    def run_pack_upgrades(self, actor: dict | None = None) -> list[dict]:
        """Run what the practice pack's ``UPGRADES`` (pack.py) do to records an older version left: each once, when it
        can (a function returns None while what it needs is not in force yet, such as a record type still proposed),
        written to the track. Called when a pack is upgraded, when a definition is approved, and when the hub opens,
        so the upgrade happens whichever of these comes last.

        An upgrade works in batches: a function that returns ``{"more": True, ...}`` did one batch and is called
        again. Each call is a transaction of its own (run again if another writer got in first), and with it the hub
        keeps where the upgrade is (``pack_upgrade_progress``: the batches done, what they did so far, and
        ``hub.upgrade_state``, a dict the function keeps its own place in), so an upgrade stopped part way (a restart, a
        failure) goes on from the last batch that was written, and never runs as one transaction of unbounded size.
        Every batch is an event on the track (``practice.upgrade_progress``); the last is ``practice.upgrade_applied``
        with what all the batches did. A function must leave every record whole after each batch: a record is
        converted, or not yet, never half."""
        ups = dict(self.pack.upgrades) if self.pack else {}
        if not ups:
            return []
        if self.db.in_transaction():
            raise RuntimeError("pack upgrades run in batches, each its own transaction: call this outside one")
        # the hub writes what the upgrade changes (the approver may not write every record it touches); the track says
        # whose approval or upgrade set it off
        by = (actor or {}).get("id")
        actor = {"id": "hub", "kind": "automation", "name": "Practice Hub"}
        practice = f"practice:{(self.pack.practice or {}).get('id', self.pack.id)}"
        out = []
        for uid, fn in ups.items():
            while True:
                def batch():
                    if uid in (self.db.get_setting("pack_upgrades_done") or []):
                        return "done"
                    progress = dict((self.db.get_setting(self.UPGRADE_PROGRESS) or {}).get(uid) or {})
                    self.upgrade_state = dict(progress.get("state") or {})
                    got = fn(self, actor)
                    if got is None:
                        return None
                    got = dict(got)
                    more = bool(got.pop("more", False))
                    so_far = _merge_upgrade_results(progress.get("done") or {}, got)
                    n = int(progress.get("batches") or 0) + 1
                    every = dict(self.db.get_setting(self.UPGRADE_PROGRESS) or {})
                    if more:
                        every[uid] = {"batches": n, "state": self.upgrade_state, "done": so_far}
                        self.db.set_setting(self.UPGRADE_PROGRESS, every)
                        self.track.append(actor, "practice.upgrade_progress", practice,
                                          data={"upgrade": uid, "batch": n, "pack_version": self.pack.version,
                                                "after": by, **_counted(got)})
                        return "more"
                    every.pop(uid, None)
                    self.db.set_setting(self.UPGRADE_PROGRESS, every)
                    self.db.set_setting("pack_upgrades_done", [*(self.db.get_setting("pack_upgrades_done") or []),
                                                               uid])
                    self.track.append(actor, "practice.upgrade_applied", practice,
                                      data={"upgrade": uid, "pack_version": self.pack.version, "after": by,
                                            "batches": n, **so_far})
                    out.append({"upgrade": uid, **so_far})
                    return "done"
                self._upgrading = True
                try:
                    step = self.db.atomic(batch)
                finally:
                    self._upgrading = False
                    self.upgrade_state = {}
                    self._defs_reset()
                if step != "more":
                    break
        return out

    def convert_record(self, actor: dict, ref: str, rtype: str, data: dict, *, into: str | None = None,
                       repoint: bool = True) -> dict:
        """Turn a record into one of another type (a pack upgrade that merges two types): a new record with the same
        id, or, with ``into``, the existing record of ``rtype`` it already stood for, rewritten as ``data``. The record
        written is checked as any other, every record that pointed at the old one points at it instead, and the old one
        is gone from the store (its versions stay, for the history). Nothing is left pointing nowhere: open workflow
        runs, work items and queued messages that name it name the new record (``repoint_work``; ``repoint=False``
        leaves that to a caller converting many records, which repoints them all at once)."""
        old_type, rid = ref.split("/", 1)
        rt = self._type(rtype)
        new_id = into.split("/", 1)[1] if into else rid
        new_ref = f"{rtype}/{new_id}"
        with self.db:
            before = self.records.read(old_type, rid)
            if not before:
                raise NotFound(f"there is no {ref}")
            there = self.records.read(rtype, new_id)
            if bool(there) != bool(into):
                raise Invalid(f"there is a {new_ref} already" if there else f"there is no {new_ref}")
            body = {k: v for k, v in data.items() if k not in ("resourceType", "meta")}
            body["id"] = new_id
            self._check_record(rt, {"resourceType": rtype, **body})
            rec = self.records.update(rtype, new_id, body) if into else self.records.create(rtype, body)
            self._write_version(rt, rec, actor, None)

            def swap(v):
                if isinstance(v, str):
                    return new_ref if v == ref else v
                if isinstance(v, dict):
                    return {k: swap(x) for k, x in v.items()}
                if isinstance(v, list):
                    return [swap(x) for x in v]
                return v
            for row in self.db.links.find({"to_ref": ref}):
                ft, fid = row["from_ref"].split("/", 1)
                other = self.records.read(ft, fid)
                if not other or ft == old_type and fid == rid:
                    continue
                fixed = swap({k: v for k, v in other.items() if k != "meta"})
                if fixed != {k: v for k, v in other.items() if k != "meta"}:
                    ort = self.record_type_for_reading(ft)
                    upd = self.records.update(ft, fid, fixed)
                    if ort is not None:
                        self._write_version(ort, upd, actor, None)
            self.db.records.delete({"_id": ref})
            self.db.links.delete({"from_ref": ref})
            self.db.titles.delete({"_id": ref})
            if repoint:
                self.repoint_work({ref: new_ref})
            self.track.append(actor, "record.converted", new_ref,
                              data={"from": ref, "subject": self.subject_of(rec), "summary": self.title(rec)})
        return rec

    REPOINT = (("runs", {"status": "running"}),
               ("items", {"status": {"$in": ["open", "claimed", "held"]}}),
               ("outbox", {"status": {"$in": ["queued", "sending"]}}))

    def repoint_work(self, refs: dict[str, str]) -> int:
        """Work still under way that names a record by a ref in ``refs`` (a run's record and context, an item's
        output, a queued message) names its new ref instead: after a conversion, a run goes on with the new record
        rather than failing on one that is gone. Finished work keeps what it named, as the track does."""
        if not refs:
            return 0

        def swap(v):
            if isinstance(v, str):
                return refs.get(v, v)
            if isinstance(v, dict):
                return {k: swap(x) for k, x in v.items()}
            if isinstance(v, list):
                return [swap(x) for x in v]
            return v
        n = 0
        with self.db:
            for coll, flt in self.REPOINT:
                for row in self.db[coll].find(flt):
                    fixed = swap(row)
                    if fixed != row:
                        self.db[coll].replace(fixed)
                        n += 1
        return n

    # ------------------------------------------------------------------ modules
    def installed_modules(self) -> list[str]:
        """Module ids in install order (an older practice stored directories: read those by their name)."""
        return [Path(m).name if ("/" in str(m) or "\\" in str(m)) else str(m)
                for m in self.db.get_setting("modules", []) or []]

    def _module_code_sha(self, mid: str) -> str | None:
        """The pack.py a policy owner installed or upgraded to last, from the signed track."""
        rows = self.track.trusted({"action": {"$in": ["module.installed", "module.upgraded"]},
                                   "subject": f"module:{mid}"}, sort=[("seq", DESC)], limit=1)
        return rows[0]["data"].get("pack_py_sha256", "") if rows else None

    def _load_installed_modules(self) -> None:
        """Load each installed module, only if its pack.py is the code that was signed in. A module that is missing
        or changed is left out (its definitions stay; its actions do not run) and named in ``module_problems``, for
        the modules page; the practice still starts."""
        for mid in self.installed_modules():
            self._load_one_module(mid)

    def _load_one_module(self, mid: str, pack: packmod.Pack | None = None) -> None:
        """Load one installed module into ``pack`` (the running practice by default; ``sync_modules`` passes a copy
        it puts in place when it is done). Only the files sealed by its newest install or upgrade run: pack.py by its
        hash, and every file (its definitions and any other code) by the digest a second policy owner reviewed."""
        pack = self.pack if pack is None else pack
        listed = next((i for i, x in enumerate(pack.modules) if x.id == mid), None)
        if listed is not None and pack.modules[listed].module is None and \
                not pack.modules[listed].module_sha256:
            return  # listed by the pack, read, and it has no code to run
        old = pack.modules[listed] if listed is not None else None
        try:
            sha = self._module_code_sha(mid)
            if sha is None:
                raise ValueError(f"the module {mid} has no signed install on the track: it is not run")
            m = packmod.load_module(mid, expect_sha256=sha, expect_digest=self._module_digest(mid) or None)
            if listed is not None:  # a module the pack lists was read without its code: run it now it is checked
                pack.modules[listed] = m
            try:
                packmod.add_module(pack, m)
            except Exception:
                if listed is not None:
                    pack.modules[listed] = old
                raise
        except Exception as exc:  # noqa: BLE001 - one broken module never stops the practice starting
            self.module_problems[mid] = str(exc)
            last = self.db.events.find_one({"action": "module.refused", "subject": f"module:{mid}"},
                                           sort=[("seq", DESC)])
            if not last or last["data"].get("problem") != str(exc):
                self.track.append({"id": "hub", "kind": "automation", "name": "Practice Hub"}, "module.refused",
                                  f"module:{mid}", data={"module": mid, "problem": str(exc)})

    MODULE_EVENTS = ("module.installed", "module.upgraded", "module.removed")

    def _module_event_seq(self, mid: str) -> int | None:
        """The newest sealed install, upgrade or removal of a module: what the loaded code must have come from."""
        rows = self.track.trusted({"action": {"$in": list(self.MODULE_EVENTS)}, "subject": f"module:{mid}"},
                                  sort=[("seq", DESC)], limit=1)
        return rows[0]["seq"] if rows else None

    def _modules_mark(self) -> tuple:
        """Two settings rows, read on every request: the installed modules and a mark every install, upgrade and
        removal changes (in whichever process it happens)."""
        return tuple(self.installed_modules()), self.db.get_setting("modules_rev")

    def _modules_changed(self) -> None:
        """Inside an install, upgrade or removal's transaction: other hubs on this database load the change."""
        self.db.set_setting("modules_rev", secrets.token_hex(8))

    def _modules_noted(self, *mids: str) -> None:
        """This process loaded (or took out) these modules as the track now says; with none, every installed one."""
        for mid in (mids or self.installed_modules()):
            if mid in self.installed_modules():
                self._module_seq[mid] = self._module_event_seq(mid)
            else:
                self._module_seq.pop(mid, None)
        if not mids:
            self._modules_seen = self._modules_mark()

    def sync_modules(self) -> bool:
        """Bring the running hub's modules in line with the database, when another process (``practicehub module
        install``, ``upgrade``, ``remove``) changed them: a removed module's code stops running, a new or upgraded
        one is loaded, and only if its pack.py is the code sealed into the track by its install or upgrade (whose
        review a second policy owner signed). Cheap when nothing changed: two settings rows. True if anything
        changed."""
        if not self.pack or not self._modules_live:
            return False
        mark = self._modules_mark()
        if mark == self._modules_seen:
            return False
        with self._modules_lock:
            mark = self._modules_mark()
            if mark == self._modules_seen:
                return False
            want = list(mark[0])
            changed = False
            # built off to the side and put in place with one assignment: a request in another thread reads the
            # old modules or the new ones, never a module half taken out
            new = packmod.forked(self.pack)
            for m in list(new.modules):
                if m.id not in want:
                    packmod.remove_module(new, m)
                    changed = True
            for mid in [x for x in self.module_problems if x not in want and x in self._module_seq]:
                self.module_problems.pop(mid, None)
            for mid in [x for x in self._module_seq if x not in want]:
                self._module_seq.pop(mid, None)
            for mid in want:
                seq = self._module_event_seq(mid)
                if mid in self._module_seq and self._module_seq[mid] == seq:
                    continue  # running the code its newest install or upgrade sealed (or refused that, already said)
                old = next((x for x in new.modules if x.id == mid), None)
                if old is not None:
                    packmod.remove_module(new, old)
                self.module_problems.pop(mid, None)
                self._load_one_module(mid, new)
                self._module_seq[mid] = seq
                changed = True
            self._modules_seen = mark
            if changed:
                self.pack = new
                self._defs_reset()
                self.track.append({"id": "hub", "kind": "automation", "name": "Practice Hub"}, "modules.reloaded",
                                  "practice:main", data={"modules": [m.id for m in self.pack.modules],
                                                         "not_running": sorted(self.module_problems)})
            return changed

    def module_status(self, mid: str, *, sync: bool = True) -> dict:
        """One module's state as every page shows it: installed or not, running or not, and what to do."""
        if sync:
            self.sync_modules()
        installed = mid in self.installed_modules()
        running = any(m.id == mid for m in self.pack.modules) if self.pack else False
        problem = self.module_problems.get(mid)
        if installed and running and not problem:
            return {"state": "running", "installed": True, "running": True, "why": "", "next": ""}
        if installed:
            return {"state": "not_running", "installed": True, "running": False,
                    "why": problem or "its code is not loaded in this hub",
                    "next": ("review and upgrade it on its modules page, or restart the hub after fixing the files"
                             if problem else "restart the hub to load it")}
        return {"state": "available", "installed": False, "running": False, "why": "", "next": ""}

    def module_requires(self, mid: str) -> list[str]:
        """What a module builds on, from its module.toml: the loaded one, else as it is on disk."""
        m = next((x for x in (self.pack.modules if self.pack else []) if x.id == mid), None)
        if m is None:
            try:
                m = packmod.load_module(mid, run_code=False)
            except (FileNotFoundError, ValueError):
                return []
        return list(m.requires)

    def _module_by_id(self, mid: str) -> packmod.Pack:
        m = next((x for x in self.pack.modules if x.id == mid), None)
        if m is None:
            raise NotFound(f"the module {mid} is not installed" + (f" ({self.module_problems[mid]})"
                                                                   if mid in self.module_problems else ""))
        return m

    def wake_agents(self) -> None:
        """New work: an agent loop running in this process (``serve --agents``) picks it up now, not at its next
        poll. Agents still take only what ``Engine.eligibility`` allows."""
        self.agents_wanted.set()

    # ------------------------------------------------------------------ setup
    def _subjects(self) -> None:
        """Wire the pack to this hub: its checks see the practice's current skills and types, and the record store
        learns what each record is about from the practice's record types."""
        if self.pack:
            self.pack.skills_fn = self.skills_now
            self.pack.types_fn = self.type_ids
        if hasattr(self.records, "subject_fn"):
            self.records.subject_fn = self.subject_of
        if hasattr(self.records, "parts_fn"):
            self.records.parts_fn = self._search_parts
        self._indexed_types: dict[str, str] = {}

    SEARCH_INDEX = "w1"  # bump when the words a record is found by are built differently: every record is redone

    def _search_stamp(self, rt) -> str:
        """What a record's search words were built from: the index's version and its type's classed fields."""
        import hashlib
        classed = sorted((f["path"], f["data_class"]) for f in (rt.classed_fields if rt is not None else []))
        return self.SEARCH_INDEX + ":" + (hashlib.sha1(json.dumps(classed).encode()).hexdigest()[:12] if classed else "")

    def _search_parts(self, rec: dict):
        """A record as each reader sees it, for its search words (``LocalRecordStore.parts_fn``)."""
        rt = self.record_type_for_reading(rec.get("resourceType") or "")
        classed = rt.classed_fields if rt is not None else []
        if not classed:
            return [], self._search_stamp(rt)
        parts = [("_", visibility.strip(rec, classed))]
        for dc in sorted({f["data_class"] for f in classed}):
            parts.append((dc, visibility.strip(rec, [f for f in classed if f["data_class"] != dc])))
        return parts, self._search_stamp(rt)

    def _search_ready(self, types) -> None:
        """Before a search: the records of these types are indexed by their type as it is now (a type whose fields'
        classes changed since has its records' words rewritten, once)."""
        if not hasattr(self.records, "reindex"):
            return
        for t in types:
            stamp = self._search_stamp(self.record_type_for_reading(t))
            if self._indexed_types.get(t) != stamp:
                self.records.reindex(t, stamp)
                self._indexed_types[t] = stamp

    def install(self, pack_name: str, *, demo: bool = False) -> None:
        """Install a practice pack into an empty hub: roles, data classes, grants, members, and the first version of
        every definition (record types, views, skills, blocks, workflows), its own and its modules'."""
        if self.db.get_setting("pack"):
            raise HubError("this hub already has a practice installed")
        self.pack = packmod.load(pack_name)
        self._defs_reset()
        self._subjects()
        p = self.pack
        installer = {"id": f"pack:{p.id}", "kind": "installer"}
        with self.db:
            self.db.set_setting("pack", str(p.dir))
            self.db.set_setting("practice", p.practice)
            self.db.set_setting("pack_config", self._pack_config(p))
            root = self.track.append(installer, "practice.installed", f"practice:{p.practice['id']}",
                                     data={"pack": p.id, "name": p.practice.get("name"), "version": p.version,
                                           "pack_py_sha256": p.module_sha256, "digest": p.digest,
                                           "modules": [m.id for m in p.modules]})
            self._install_part(installer, p, "", root)
            self.db.set_setting("modules", [m.id for m in p.modules])  # installed with the practice: they are its
            # a practice that starts on this version has nothing an older one left: its upgrades are done
            self.db.set_setting("pack_upgrades_done", list(p.upgrades))
            for m in p.modules:
                mroot = self.track.append(installer, "module.installed", f"module:{m.id}", cause=root,
                                          data={"module": m.id, "version": m.version,
                                                "pack_py_sha256": m.module_sha256, "digest": m.digest})
                self._install_part(installer, m, m.id, mroot)
            for m in sorted(p.members, key=lambda m: m.get("kind", "person") != "person"):
                self._create_member(installer, m, cause=root)
                if m.get("owner"):
                    self.authz.write([("practice:main", "owner", member_ref(m["id"]))])
            self._install_definitions(installer, p, "", root)
            for m in p.modules:
                self._install_definitions(installer, m, m.id, root)
            self._modules_changed()
        self._modules_noted()
        if demo and p.module is not None and hasattr(p.module, "demo"):
            p.module.demo(self)
        for m in p.modules:
            if demo and m.module is not None and hasattr(m.module, "demo"):
                m.module.demo(self)

    def approve_module_code(self, actor: dict, name: str) -> dict:
        """A policy owner signs that they reviewed a module as it is on disk: its code (pack.py runs in the hub, with
        the hub's powers) and its definitions, by one hash over every file. A module is installed or upgraded only by
        a second policy owner, after this review: no one person brings code or definitions into force alone."""
        if actor.get("kind") != "person":
            raise Denied("module code is reviewed by a person, never an agent")
        self.require(actor, "policy_owner", "practice:main", "review module code")
        if not wfmod.ID.fullmatch(name or ""):
            raise Invalid("name a module in the modules directory (letters, digits and dashes), not a path")
        try:
            m = packmod.load_module(name, run_code=False)
        except (FileNotFoundError, ValueError) as exc:
            raise Invalid(str(exc)) from None
        with self.db:
            data = {"module": m.id, "version": m.version, "digest": m.digest, "pack_py_sha256": m.module_sha256}
            seq = self.track.append(actor, "module.code_approved", f"module:{m.id}", data=data,
                                    signed={**data, "meaning": f"I have reviewed the {m.practice.get('name', m.id)} "
                                                               f"module, version {m.version}: its code and its "
                                                               f"definitions"})
            if m.digest != self._module_digest(m.id):
                name = m.practice.get("name", m.id)
                self._tell_approvers(actor, seq, set(self.module_code_reviewers(m.id, m.digest)),
                                     f"The {name} module waits for a second policy owner",
                                     f"{actor.get('name') or actor['id']} reviewed the {name} module, version "
                                     f"{m.version}. A policy owner who did not review it puts it into force (or "
                                     f"leaves it) on its page under Modules.", f"/modules/{m.id}")
        return {"module": m.id, "version": m.version, "digest": m.digest}

    def module_code_reviewers(self, mid: str, digest: str) -> list[str]:
        rows = self.track.trusted({"action": "module.code_approved", "subject": f"module:{mid}", "data.digest": digest})
        return sorted({r["actor"] for r in rows})

    def _module_digest(self, mid: str) -> str | None:
        """The module as last put into force, from sealed events only: an unsealed row is never a review."""
        rows = self.track.trusted({"action": {"$in": ["module.installed", "module.upgraded"]},
                                   "subject": f"module:{mid}"}, sort=[("seq", DESC)], limit=1)
        return rows[0]["data"].get("digest") if rows else None

    def _require_code_review(self, actor: dict, m: packmod.Pack, running: str | None) -> None:
        """A module as it is on disk (code and definitions) needs a review signed by a policy owner other than
        whoever puts it into force. The installer (setting up a new practice from its pack) is exempt."""
        if actor.get("kind") != "person" or m.digest == running:
            return
        if not [r for r in self.module_code_reviewers(m.id, m.digest) if r != actor["id"]]:
            raise Denied(f"the {m.id} module as it is on disk has not been reviewed by another policy owner: one of "
                         f"them reviews it first (Review on its modules page, or module review {m.id})")

    @staticmethod
    def _load_reviewed(name: str, read: packmod.Pack) -> packmod.Pack:
        """Run the module's code, only if the files are still the ones whose review was just checked."""
        try:
            m = packmod.load_module(name, expect_sha256=read.module_sha256)
        except (FileNotFoundError, ValueError) as exc:
            raise Invalid(str(exc)) from None
        if m.digest != read.digest:
            raise Invalid(f"the {name} module changed on disk while it was being installed: review it again")
        return m

    def install_module(self, actor: dict, name: str) -> dict:
        """Add a module (document control, quality, product lifecycle...) to a running practice. An owner or policy
        owner does it; its definitions arrive active, signed by the person who installed it."""
        if actor.get("kind") not in ("person", "installer"):
            raise Denied("a module is installed by a person")
        if actor.get("kind") == "person":
            self.require(actor, "policy_owner", "practice:main", "install modules")
        if not wfmod.ID.fullmatch(name or ""):
            raise Invalid("name a module in the modules directory (letters, digits and dashes), not a path")
        self.sync_modules()
        if name in self.installed_modules():
            raise Invalid(f"the module {name} is already installed: upgrade it instead")
        try:  # read it, check the review, and only then run the code that was reviewed
            m = packmod.load_module(name, run_code=False)
        except (FileNotFoundError, ValueError) as exc:
            raise Invalid(str(exc)) from None
        if any(x.id == m.id for x in self.pack.modules):
            raise Invalid(f"the module {m.id} is already installed")
        have = self.installed_modules()
        absent = [r for r in m.requires if r not in have]
        if absent:
            raise Invalid(f"the {m.id} module builds on {', '.join(absent)}: install "
                          f"{'it' if len(absent) == 1 else 'them'} first")
        stopped = [r for r in m.requires if self.module_status(r)["state"] != "running"]
        if stopped:
            raise Invalid(f"the {m.id} module builds on {', '.join(stopped)}, which "
                          f"{'is' if len(stopped) == 1 else 'are'} installed but not running here ("
                          + "; ".join(self.module_status(r)["why"] for r in stopped) + "): fix that first")
        self._require_code_review(actor, m, None)
        m = self._load_reviewed(name, m)
        try:
            packmod.add_module(self.pack, m)
        except ValueError as exc:
            raise Invalid(str(exc)) from None
        try:
            with self.db:
                mods = list(self.db.get_setting("modules", []) or [])
                self.db.set_setting("modules", mods + [m.id])
                root = self.track.append(actor, "module.installed", f"module:{m.id}",
                                         data={"module": m.id, "version": m.version, "pack_py_sha256": m.module_sha256,
                                               "digest": m.digest},
                                         signed={"module": m.id, "version": m.version,
                                                 "meaning": f"I install the {m.practice.get('name', m.id)} module",
                                                 "pack_py_sha256": m.module_sha256})
                self._install_part(actor, m, m.id, root)
                self._defs_reset()
                self._install_definitions(actor, m, m.id, root)
                self._install_extensions(actor, m, root)
                self._modules_changed()
                reach = self.reach_sweep(actor, f"the {m.id} module was installed")
        except Exception:
            packmod.remove_module(self.pack, m)
            self._defs_reset()
            raise
        self._modules_noted(m.id)
        return {"module": m.id, "version": m.version, **reach}

    EXT_BEGIN, EXT_END = "# >>> added by the {} module", "# <<< {}"

    def _extended_source(self, src: str, mid: str, fragment: str | None) -> str:
        """``src`` with module ``mid``'s additions replaced by ``fragment`` (or taken out, for None)."""
        import re as _re
        begin, end = self.EXT_BEGIN.format(mid), self.EXT_END.format(mid)
        src = _re.sub(rf"\n*{_re.escape(begin)}\n.*?\n{_re.escape(end)}\n?", "\n", src, flags=_re.S).rstrip() + "\n"
        return src if fragment is None else f"{src}\n{begin}\n{fragment.strip()}\n{end}\n"

    def _own_text(self, src: str, mid: str) -> str:
        """A definition's text without what modules other than ``mid`` added to it."""
        import re as _re
        for other in set(_re.findall(r"# >>> added by the ([a-z][a-z0-9-]*) module", src)) - {mid}:
            src = self._extended_source(src, other, None)
        return src.strip()

    def _with_others_additions(self, kind: str, did: str, src: str, mid: str) -> str:
        """A module's own text for a definition, with what other modules added to the one in force kept on: an upgrade
        of the owner never drops another module's fields, columns or steps."""
        import re as _re
        row = self._active_row(kind, did)
        if not row:
            return src
        for other, block in _re.findall(r"# >>> added by the ([a-z][a-z0-9-]*) module\n(.*?)\n# <<< \1", row["source"],
                                        flags=_re.S):
            if other != mid:
                src = self._extended_source(src, other, block)
        return src

    def _put_version(self, actor: dict, kind: str, did: str, source: str, note: str, module: str, cause: int,
                     action: str) -> int:
        """A new active version, in place of the active one, installed with a module (and signed by its root
        event)."""
        table, col = self.KINDS[kind]
        version = (self.db[table].max("version", {col: did}) or 0) + 1
        self.db[table].update({col: did, "status": "active"}, {"$set": {"status": "retired"}}, many=True)
        self.db[table].insert({"_id": f"{did}@{version}", col: did, "version": version, "status": "active",
                               "source": source, "author": actor["id"], "note": note, "created_at": utcnow(),
                               "approved_by": actor["id"], "approved_at": utcnow(), "module": module})
        self.track.append(actor, action, f"{kind}:{did}", cause=cause,
                          data={"version": version, "module": module, "note": note,
                                "source_sha256": hashlib.sha256(source.encode()).hexdigest()})
        self._defs_reset()
        return version

    def _active_row(self, kind: str, did: str) -> dict | None:
        table, col = self.KINDS[kind]
        return self.db[table].find_one({col: did, "status": "active"})

    def _owner_of(self, kind: str, did: str) -> str | None:
        """Who brought a definition in: a module's id, or '' for the practice. None if it was never defined."""
        table, col = self.KINDS[kind]
        row = self.db[table].find_one({col: did}, sort=[("version", ASC)])
        return None if row is None else (row["module"] or "")

    def _check_source(self, kind: str, did: str, source: str) -> None:
        try:
            self.check_definition(kind, source)
        except wfmod.DefinitionError as exc:
            raise Invalid(f"the {kind} {did} would have problems", exc.problems) from None

    def _install_extensions(self, actor: dict, m: packmod.Pack, root: int) -> None:
        """What a module adds to types and views it does not own: a new version of each, the practice's own text
        followed by the module's additions, marked so an upgrade or removal finds them again."""
        for (kind, did), fragment in m.extensions.items():
            row = self._active_row(kind, did)
            if not row:
                raise Invalid(f"the module {m.id} adds to the {kind} {did}, which this practice does not have")
            src = self._extended_source(row["source"], m.id, fragment)
            self._check_source(kind, did, src)
            self._put_version(actor, kind, did, src, f"Extended by the {m.id} module", m.id, root, f"{kind}.extended")
        broken = [p for kind, did in m.extensions for p in self._workflows_broken_by(kind, did)]
        if broken:
            raise Invalid(f"the module {m.id}'s additions would break workflows the practice runs", broken)

    def module_plan(self, name: str) -> dict:
        """How the module on disk differs from what the practice runs: new definitions, changed ones (proposed on
        upgrade, for a second policy owner), ones the practice changed itself, and whether its code changed."""
        m = packmod.load_module(name, run_code=False)
        plan = {"module": m.id, "version": m.version, "new": [], "changed": [], "kept": [], "same": [],
                "pending": [], "removed": [], "clashes": [],
                "code_changed": m.module_sha256 != (self._module_code_sha(m.id) or ""), "roles": [],
                "data_classes": [], "grants_added": [], "grants_removed": []}
        plan["reviewed_by"] = self.module_code_reviewers(m.id, m.digest)
        plan["needs_review"] = m.digest != self._module_digest(m.id)
        old_grants, new_grants = set(map(tuple, self._module_grants(m.id))), set(self._grant_tuples(m))
        plan["grants_added"] = sorted(map(list, new_grants - old_grants))
        plan["grants_removed"] = sorted(map(list, old_grants - new_grants))
        have_roles = set(self.db.roles.distinct("_id"))
        have_dcs = set(self.db.data_classes.distinct("_id"))
        plan["roles"] = [r["id"] for r in m.roles if r["id"] not in have_roles]
        plan["data_classes"] = [d["id"] for d in m.data_classes if d["id"] not in have_dcs]
        ships = set()
        for kind, did, src in self._module_definitions(m):
            table, col = self.KINDS[kind]
            ships.add((kind, did))
            owner = self._owner_of(kind, did)
            if (kind, did) not in m.extensions and owner not in (None, m.id):
                plan["clashes"].append(f"{kind} {did} belongs to {owner or 'the practice'}")
                continue
            active = self._active_row(kind, did)
            shipped = self.db[table].find_one({col: did, "module": m.id, "approved_by": {"$ne": None}},
                                              sort=[("version", DESC)])
            if self.db[table].exists({col: did, "status": "proposed", "source": src}):
                plan["pending"].append(f"{kind} {did}")  # already proposed, waiting for approval
            elif owner is None:
                plan["new"].append(f"{kind} {did}")  # never defined here: arrives active, as on install
            elif not active:
                plan["changed"].append(f"{kind} {did}")  # retired here: coming back is a change, proposed
            elif active["source"] == src:
                plan["same"].append(f"{kind} {did}")
            elif shipped and self._own_text(active["source"], m.id) != self._own_text(shipped["source"], m.id):
                plan["kept"].append(f"{kind} {did}")  # the practice changed it: the module's text is proposed
            else:
                plan["changed"].append(f"{kind} {did}")
        for kind, (table, col) in self.KINDS.items():  # what this version no longer ships
            for did in self.db[table].distinct(col, {"module": m.id}):
                if self._owner_of(kind, did) == m.id and (kind, did) not in ships and self._active_row(kind, did):
                    plan["removed"].append(f"{kind} {did}")
        return plan

    @staticmethod
    def _grant_tuples(p: packmod.Pack) -> list[tuple]:
        tuples = []
        for role, g in p.grants.items():
            if role == EVERYONE:  # every person may read these (controlled documents everyone must follow)
                tuples += [(f"data_class:{dc}", "reader", f"role:{EVERYONE}#assignee") for dc in g.get("read", [])]
                continue
            for dc in g.get("write", []):
                tuples.append((f"data_class:{dc}", "writer", f"role:{role}#assignee"))
            for dc in g.get("read", []):
                tuples.append((f"data_class:{dc}", "reader", f"role:{role}#assignee"))
            if g.get("policy_owner"):
                tuples.append(("practice:main", "policy_owner", f"role:{role}#assignee"))
            if g.get("member_admin"):
                tuples.append(("practice:main", "member_admin", f"role:{role}#assignee"))
            if g.get("break_glass"):  # may open what a subject's restriction withholds, with a reason (restrictions.py)
                tuples.append(("practice:main", "break_glass", f"role:{role}#assignee"))
        return tuples

    def _module_grants(self, mid: str) -> list[list]:
        """The grants a module holds now, from the track: what it installed, less what was taken back."""
        held: set = set()
        for r in self.db.events.find({"subject": f"module:{mid}",
                                      "action": {"$in": ["grants.installed", "grants.revoked"]}},
                                     sort=[("seq", ASC)]):
            ts = {tuple(t) for t in r["data"].get("tuples", [])}
            held = held | ts if r["action"] == "grants.installed" else held - ts
        return sorted(map(list, held))

    def _revoke_module_grants(self, actor: dict, mid: str, tuples, root: int) -> None:
        """Take back grants a module no longer gives, unless the practice or another installed module gives them."""
        others = set(self._grant_tuples(self.pack)) | {tuple(t) for x in self.installed_modules() if x != mid
                                                         for t in self._module_grants(x)}
        drop = [tuple(t) for t in tuples if tuple(t) not in others]
        if drop:
            self.authz.delete(drop)
        if tuples:
            self.track.append(actor, "grants.revoked", f"module:{mid}", cause=root,
                              data={"tuples": [list(t) for t in tuples]})

    def _module_definitions(self, m: packmod.Pack):
        """Every definition a module ships, with the source the practice would run: its own text, or for a type or
        view it extends, the practice's text with the module's additions."""
        for tid, src in m.type_sources.items():
            yield "type", tid, self._with_others_additions("type", tid, src, m.id)
        for sid, sk in m.skills.items():
            yield "skill", sid, sk.source
        for bid, src in m.block_sources.items():
            yield "block", bid, src
        for wid, src in m.workflow_sources.items():
            yield "workflow", wid, self._with_others_additions("workflow", wid, src, m.id)
        for vid, src in m.view_sources.items():
            yield "view", vid, self._with_others_additions("view", vid, src, m.id)
        for (kind, did), fragment in m.extensions.items():
            row = self._active_row(kind, did)
            if row:
                yield kind, did, self._extended_source(row["source"], m.id, fragment)

    def upgrade_module(self, actor: dict, name: str) -> dict:
        """Move an installed module to the version on disk. Its new code runs from now on (signed into the track by
        the person upgrading); new definitions arrive active, as on install; a definition the module changed is
        proposed, for another policy owner to approve, and so is one the practice had changed itself."""
        if actor.get("kind") != "person":
            raise Denied("a module is upgraded by a person")
        self.require(actor, "policy_owner", "practice:main", "upgrade modules")
        self.sync_modules()
        if name not in self.installed_modules():
            raise Invalid(f"the module {name} is not installed")
        try:  # read it, check the review, and only then run the code that was reviewed
            new = packmod.load_module(name, run_code=False)
        except (FileNotFoundError, ValueError) as exc:
            raise Invalid(str(exc)) from None
        old = next((x for x in self.pack.modules if x.id == new.id), None)
        plan = self.module_plan(name)
        if plan["clashes"]:
            raise Invalid(f"the {new.id} module would take over definitions it does not own", plan["clashes"])
        self._require_code_review(actor, new, self._module_digest(new.id))
        new = self._load_reviewed(name, new)
        if old is not None:
            packmod.remove_module(self.pack, old)
        try:
            packmod.add_module(self.pack, new)
        except ValueError as exc:
            if old is not None:
                packmod.add_module(self.pack, old)
            raise Invalid(str(exc)) from None
        proposed = []
        try:
            with self.db:
                root = self.track.append(actor, "module.upgraded", f"module:{new.id}",
                                         data={"module": new.id, "version": new.version,
                                               "pack_py_sha256": new.module_sha256, "digest": new.digest,
                                               "plan": plan},
                                         signed={"module": new.id, "version": new.version,
                                                 "meaning": f"I upgrade the {new.practice.get('name', new.id)} "
                                                            f"module to version {new.version}",
                                                 "pack_py_sha256": new.module_sha256})
                part = packmod.Pack(**{**new.__dict__, "roles": [r for r in new.roles if r["id"] in plan["roles"]],
                                       "data_classes": [d for d in new.data_classes
                                                        if d["id"] in plan["data_classes"]]})
                part.grants = {}
                self._install_part(actor, part, new.id, root)
                if plan["grants_added"]:
                    self.authz.write([tuple(t) for t in plan["grants_added"]])
                    self.track.append(actor, "grants.installed", f"module:{new.id}", cause=root,
                                      data={"tuples": plan["grants_added"]})
                if plan["grants_removed"]:
                    self._revoke_module_grants(actor, new.id, plan["grants_removed"], root)
                self._defs_reset()
                for label in plan["removed"]:
                    kind, did = label.split(" ", 1)
                    self._retire_now(actor, kind, did, f"The {new.id} module {new.version} no longer has it")
                for kind, did, src in self._module_definitions(new):
                    label = f"{kind} {did}"
                    if label in plan["new"]:
                        self._check_source(kind, did, src)
                        self._put_version(actor, kind, did, src, f"Installed with the {new.id} module {new.version}",
                                          new.id, root, f"{kind}.installed")
                        if kind == "workflow":
                            self._sync_starters(self.check_workflow(src))
                    elif label in plan["changed"] or label in plan["kept"]:
                        note = (f"The {new.id} module {new.version}" +
                                (" (the practice had changed this: compare before approving)"
                                 if label in plan["kept"] else ""))
                        proposed.append({"kind": kind, "id": did,
                                         **self._propose(kind, actor, did, src, note,
                                                         module=None if (kind, did) in new.extensions else new.id)})
                self.module_problems.pop(new.id, None)
                self._modules_changed()
                reach = self.reach_sweep(actor, f"the {new.id} module was upgraded")
        except Exception:
            packmod.remove_module(self.pack, new)
            if old is not None:
                packmod.add_module(self.pack, old)
            self._defs_reset()
            raise
        self._modules_noted(new.id)
        return {"module": new.id, "version": new.version, "proposed": proposed, "plan": plan, **reach}

    def uninstall_module(self, actor: dict, name: str, reason: str = "") -> dict:
        """Take a module out: its workflows, views, blocks, skills and types are retired (records stay, readable,
        with their history and signatures); what it added to other types and views is taken out of them. Roles and
        data classes stay, since members and records still carry them. Refused while another module builds on it or
        its work is under way."""
        if actor.get("kind") != "person":
            raise Denied("a module is removed by a person")
        self.require(actor, "policy_owner", "practice:main", "remove modules")
        self.sync_modules()
        if name not in self.installed_modules():
            raise Invalid(f"the module {name} is not installed")
        m = next((x for x in self.pack.modules if x.id == name), None)
        # every installed module, running here or not: one that builds on this one keeps it
        users = [x for x in self.installed_modules() if x != name and name in self.module_requires(x)]
        if users:
            raise Invalid(f"{', '.join(users)} build{'s' if len(users) == 1 else ''} on {name}: remove "
                          f"{'it' if len(users) == 1 else 'them'} first")
        owned = {}
        for kind, (table, col) in self.KINDS.items():
            owned[kind] = [r[col] for r in self.db[table].find({"module": name, "version": 1}, sort=[(col, ASC)])]
        running = [r for r in self.engine.runs(status="running", limit=10000) if r["workflow_id"] in owned["workflow"]]
        if running:
            raise Invalid(f"its work is under way ({len(running)} running): finish or cancel it first")
        with self.db:
            root = self.track.append(actor, "module.removed", f"module:{name}", data={"module": name, "reason": reason},
                                     signed={"module": name, "meaning": f"I remove the {name} module"})
            for kind in ("view", "workflow", "block", "skill", "type"):
                table, col = self.KINDS[kind]
                for did in owned[kind]:
                    if self.db[table].exists({col: did, "status": "active"}):
                        self._retire_now(actor, kind, did, f"The {name} module was removed")
                    for r in self.db[table].find({col: did, "status": "proposed"}, sort=[("version", ASC)]):
                        self._withdraw(kind, actor, did, r["version"], f"The {name} module was removed")
            self._revoke_module_grants(actor, name, self._module_grants(name), root)
            for kind in ("type", "view", "workflow"):  # what it added to others' types, views and workflows
                table, col = self.KINDS[kind]
                for r in self.db[table].find({"status": "active",
                                              "source": {"$regex": re.escape(self.EXT_BEGIN.format(name))}},
                                             sort=[(col, ASC)]):
                    src = self._extended_source(r["source"], name, None)
                    self._check_source(kind, r[col], src)
                    self._put_version(actor, kind, r[col], src, f"The {name} module's additions taken out", name,
                                      root, f"{kind}.unextended")
            self.db.set_setting("modules", [x for x in self.installed_modules() if x != name])
            self._modules_changed()
            self._defs_reset()
        if m is not None:
            with self._modules_lock:  # off to the side, then in place: other threads never see it half taken out
                new = packmod.forked(self.pack)
                packmod.remove_module(new, m)
                self.pack = new
        self.module_problems.pop(name, None)
        self._modules_noted(name)
        self._defs_reset()
        return {"module": name, "retired": owned}

    def _install_part(self, installer: dict, p: packmod.Pack, module: str, root: int) -> None:
        """Roles, data classes and grants of a practice or module."""
        for r in p.roles:
            had = self.db.roles.get(r["id"])
            if had and module and had["module"] == module:
                continue  # it came with this module before, which is being installed again
            if had:
                raise Invalid(f"the role {r['id']} already exists")
            self.db.roles.insert({"_id": r["id"], "id": r["id"], "name": r["name"],
                                  "description": r.get("description", ""), "module": module})
        for dc in p.data_classes:
            had = self.db.data_classes.get(dc["id"])
            if had and module and had["module"] == module:
                continue
            if had:
                raise Invalid(f"the data class {dc['id']} already exists")
            self.db.data_classes.insert({"_id": dc["id"], "id": dc["id"], "name": dc.get("name", dc["id"]),
                                         "description": dc.get("description", ""), "module": module})
        tuples = self._grant_tuples(p)
        self.authz.write(tuples)
        if tuples:
            self.track.append(installer, "grants.installed", f"module:{module}" if module else "practice:main",
                              cause=root, data={"tuples": [list(t) for t in tuples]})

    def _install_definitions(self, installer: dict, p: packmod.Pack, module: str, root: int) -> None:
        """Version 1 of each definition, active, in dependency order: types, skills, blocks, workflows, views."""
        def put(kind, did, src, title, extra=None):
            table, col = self.KINDS[kind]
            if self.db[table].exists({col: did}):
                if not (module and self._owner_of(kind, did) == module and not self._active_row(kind, did)):
                    raise Invalid(f"the {kind} {did} already exists in this practice")
                # this module had it before and was removed: it comes back as a new version
                self._put_version(installer, kind, did, src, f"Installed again with the {module} module", module,
                                  root, f"{kind}.installed")
                return
            self.db[table].insert({"_id": f"{did}@1", col: did, "version": 1, "status": "active", "source": src,
                                   "author": installer["id"],
                                   "note": "Installed" + (f" with the {module} module" if module else
                                                          " with the practice"),
                                   "created_at": utcnow(), "approved_by": installer["id"], "approved_at": utcnow(),
                                   "module": module})
            self.track.append(installer, f"{kind}.installed", f"{kind}:{did}", cause=root,
                              data={"version": 1, "title": title, "module": module,
                                    "source_sha256": hashlib.sha256(src.encode()).hexdigest(), **(extra or {})})

        def problems_of(kind, did, exc):
            return Invalid(f"the {kind} {did} in {p.id} has problems", exc.problems)

        for tid, src in p.type_sources.items():
            put("type", tid, src, tid)
        self._defs_reset()
        for tid in p.type_sources:  # check once every type is in: types may point at each other
            try:
                schema.parse(self._def_row("type", tid)["source"], self)
            except wfmod.DefinitionError as exc:
                raise problems_of("record type", tid, exc) from None
        for sid, sk in p.skills.items():
            put("skill", sid, sk.source, sk.title)
        self._defs_reset()
        for bid, src in p.block_sources.items():
            try:
                b = blockmod.parse(src, self.pack)
            except wfmod.DefinitionError as exc:
                raise problems_of("block", bid, exc) from None
            put("block", bid, src, b.title, {"runtime": b.runtime, "code_sha256": b.code_sha256})
        for wid, src in p.workflow_sources.items():
            try:
                wf = wfmod.parse(src, self.pack)
            except wfmod.DefinitionError as exc:
                raise problems_of("workflow", wid, exc) from None
            put("workflow", wid, src, wf.title)
            self._sync_starters(wf)
        for vid, src in p.view_sources.items():
            try:
                v = self._def_parse("view", src, self)
            except wfmod.DefinitionError as exc:
                raise problems_of("view", vid, exc) from None
            put("view", vid, src, v.title)
        self._defs_reset()

    @property
    def practice(self) -> dict:
        """The practice as its pack says, with what its policy owners changed since on the practice settings page."""
        return {**(self.db.get_setting("practice", {}) or {}), **(self.db.get_setting("practice_local", {}) or {})}

    # what a policy owner may change on the practice settings page; who the practice is (its id, kind, jurisdiction,
    # privacy law, where its data stays) comes from its pack, and changes only by a reviewed pack upgrade
    PRACTICE_EDITABLE = ("name", "code", "timezone", "phone", "email", "address", "website", "escalation_role",
                         "shortcuts")

    def set_practice(self, actor: dict, changes: dict) -> dict:
        """A policy owner changes the practice's own settings. Each change is tracked with what it was before."""
        self.require(actor, "policy_owner", "practice:main", "change the practice's settings")
        now, clean = self.practice, {}
        for k, v in changes.items():
            if k not in self.PRACTICE_EDITABLE:
                raise Invalid(f"{k!r} is not a practice setting that can be changed here")
            if k == "shortcuts":
                v = [str(x).strip() for x in (v or []) if str(x).strip()]
                for t in v:
                    if t.startswith("start:"):
                        try:
                            self.workflow(t[6:])
                        except HubError:
                            raise Invalid(f"shortcuts: there is no workflow {t[6:]!r}") from None
                    elif not self.record_type(t):
                        raise Invalid(f"shortcuts: there is no record type {t!r}")
            else:
                v = str(v or "").strip()
            if k == "name" and not v:
                raise Invalid("the practice needs a name")
            if k == "name" and len(v) > 120:
                raise Invalid("name: at most 120 characters")
            if k == "code" and v and not re.fullmatch(r"[A-Z][A-Z0-9]{1,5}", v):
                raise Invalid("code: 2 to 6 capital letters or digits, starting with a letter (it begins every work "
                              "item's key, like HFM-12)")
            if k == "timezone":  # it decides the practice's "now" and "today" (``clock``): never left empty
                if not v:
                    raise Invalid("timezone: the practice needs a time zone (a name like America/Halifax)")
                from .clock import ZoneError, zone
                try:
                    zone(v)
                except ZoneError as exc:
                    raise Invalid(f"timezone: {exc}") from None
            if k == "email" and v and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
                raise Invalid("email: that is not an email address")
            if k == "website" and v and not re.fullmatch(r"https://\S+", v):
                raise Invalid("website: give the full address, starting https://")
            if k == "escalation_role" and v not in {r["id"] for r in self.roles()}:
                raise Invalid(f"escalation_role: there is no role {v!r}")
            if now.get(k, "" if k != "shortcuts" else []) != v:
                clean[k] = v
        if clean:
            local = {**(self.db.get_setting("practice_local", {}) or {}), **clean}
            with self.db:
                self.db.set_setting("practice_local", local)
                self.engine._zone = None  # its time zone may have changed: read it afresh
                self.track.append(actor, "practice.changed", f"practice:{now.get('id', 'main')}",
                                  data={"changed": {k: {"from": now.get(k), "to": v} for k, v in clean.items()}})
        return self.practice

    # ------------------------------------------------------------------ members
    def member(self, mid: str) -> dict:
        row = self.db.members.get(mid)
        if not row:
            raise NotFound(f"there is no member {mid!r}")
        row["profile"] = dict(row.get("profile") or {})
        row["roles"] = self.roles_of(mid)
        return row

    # a member's own settings: how the hub looks and behaves for them. Each has a fixed set of values, the first
    # the default; nothing else is kept.
    PREFERENCES = {"theme": ("system", "light", "dark"), "editor": ("visual", "markdown")}

    def preferences(self, mid: str) -> dict:
        saved = (self.db.members.get(mid) or {}).get("prefs") or {}
        return {k: saved[k] if saved.get(k) in v else v[0] for k, v in self.PREFERENCES.items()}

    def set_preferences(self, actor: dict, prefs: dict) -> dict:
        """Change the actor's own settings (only their own: nobody sets another's)."""
        clean = {}
        for k, v in prefs.items():
            if k not in self.PREFERENCES:
                raise Invalid(f"there is no setting {k!r}")
            if v not in self.PREFERENCES[k]:
                raise Invalid(f"{k}: choose one of {', '.join(self.PREFERENCES[k])}")
            clean[k] = v
        if clean:
            with self.db:
                self.db.members.update({"_id": actor["id"]}, {"$set": {f"prefs.{k}": v for k, v in clean.items()}})
                self.track.append(actor, "member.preferences_set", f"member:{actor['id']}", data=clean)
        return self.preferences(actor["id"])

    def members(self, kind: str | None = None) -> list[dict]:
        rows = self.db.members.find({"kind": kind} if kind else {}, sort=[("kind", ASC), ("name", ASC)], fields=["id"])
        return [self.member(r["id"]) for r in rows]

    def roles_of(self, mid: str) -> list[str]:
        return sorted(o.split(":", 1)[1] for (o, r, _) in self.authz.read(subject=member_ref(mid)) if
                      o.startswith("role:") and r == "assignee" and o != f"role:{EVERYONE}")

    def _everyone_holds_everyone(self) -> None:
        """Every person holds the ``everyone`` role (hubs from before it existed get it on start)."""
        held = {s for (_, _, s) in self.authz.read(obj=f"role:{EVERYONE}", relation="assignee")}
        missing = [(f"role:{EVERYONE}", "assignee", member_ref(r["id"])) for r in
                   self.db.members.find({"kind": "person"}, fields=["id"]) if member_ref(r["id"]) not in held]
        if missing:
            self.authz.write(missing)

    def role(self, rid: str) -> dict:
        row = self.db.roles.get(rid)
        if not row:
            raise NotFound(f"there is no role {rid!r}")
        holders = [s.split(":", 1)[1] for (_, _, s) in self.authz.read(obj=f"role:{rid}", relation="assignee")]
        row["holders"] = [self.member(h) for h in holders]
        return row

    def run_title(self, member: dict | None, run: dict) -> str:
        """A run's title as this member may see it: the workflow's title, and who or what it is about only if they
        may read that record's data class (a name and birth date are themselves personal health information)."""
        base = str(run.get("title") or "").split(": ", 1)[0]
        ref = run.get("record") or ""
        if not member or not ref or not self.pack or "/" not in ref:
            return base
        rtype, _, rid = ref.partition("/")
        dc = self.class_or_none(rtype)
        if dc and not self.can(member, "reader", f"data_class:{dc}"):
            return base
        try:
            rec = self.records.read(rtype, rid)
        except Exception:  # noqa: BLE001
            rec = None
        return f"{base}: {self.short_title(rec)}" if rec else base

    def active_holders(self, rid: str, *, persons_only: bool = False) -> list[dict]:
        """Members who hold this role and are switched on (people only, for work only a person may do)."""
        out = []
        for (_, _, s) in self.authz.read(obj=f"role:{rid}", relation="assignee"):
            try:
                m = self.member(s.split(":", 1)[1])
            except NotFound:
                continue
            if m["status"] == "active" and (m["kind"] == "person" or not persons_only):
                out.append(m)
        return out

    def empty_roles(self) -> list[str]:
        """Roles some active workflow gives work to that nobody switched on holds."""
        used = {s.role for w in self.workflows() for s in w["workflow"].steps
                if getattr(s, "role", None) and s.kind in ("task", "gate")}
        return sorted(r for r in used if not self.active_holders(r))

    def roles(self) -> list[dict]:
        return [self.role(r["id"]) for r in self.db.roles.find({}, sort=[("name", ASC)], fields=["id"])]

    def can(self, member: dict, relation: str, obj: str) -> bool:
        if member.get("status") != "active":
            return False
        return self.authz.check(member_ref(member["id"]), relation, obj)

    def require(self, member: dict, relation: str, obj: str, what: str) -> None:
        if not self.can(member, relation, obj):
            self.track.append(member, "access.denied", obj, data={"relation": relation, "what": what})
            raise Denied(f"{member['name']} may not {what}")

    def is_policy_owner(self, member: dict) -> bool:
        return self.can(member, "policy_owner", "practice:main")

    def subject_word(self) -> str:
        """The practice's own word for whom its work is about, in lower case, for sentences (the pack's
        ``[subject] label``); "person" without a pack."""
        label = ((self.pack.subject or {}).get("label") if self.pack else None) or "person"
        return label if label[:2].isupper() else label.lower()

    def _create_member(self, actor: dict, m: dict, cause: int | None = None) -> dict:
        kind = m.get("kind", "person")
        if kind not in ("person", "agent", "service"):
            raise Invalid(f"a member is a person, an agent or a service, not {kind!r}")
        if not m.get("id") or not m.get("name"):
            raise Invalid("a member needs an id and a name")
        if self.db.members.exists({"_id": m["id"]}):
            raise Invalid(f"there is already a member {m['id']!r}")
        sponsor = m.get("sponsor")
        if kind != "person":
            if not sponsor:
                raise Invalid("an agent or service needs a sponsor: the person accountable for it")
            sp = self.member(sponsor)
            if sp["kind"] != "person" or sp["status"] != "active":
                raise Invalid("an agent's sponsor is an active person")
        profile = dict(m.get("profile", {}) or {})
        if kind == "agent":
            profile.setdefault("max_risk", "low")
            profile.setdefault("skills", [])
            if profile["max_risk"] not in RISK_ORDER:
                raise Invalid("max_risk is low, medium or high")
        roles = list(m.get("roles", []))
        known = set(self.db.roles.distinct("_id"))
        for r in roles:
            if r not in known:
                raise Invalid(f"there is no role {r!r}")
        with self.db:
            self.db.members.insert({"_id": m["id"], "id": m["id"], "kind": kind, "name": m["name"],
                                    "title": m.get("title", ""), "email": m.get("email", ""), "sponsor": sponsor,
                                    "status": "active", "profile": profile, "created_at": utcnow()})
            tuples = [(f"role:{r}", "assignee", member_ref(m["id"])) for r in roles]
            if kind == "person":
                tuples.append((f"role:{EVERYONE}", "assignee", member_ref(m["id"])))
            if sponsor:
                tuples.append((member_ref(m["id"]), "sponsor", member_ref(sponsor)))
            self.authz.write(tuples)
            self.track.append(actor, "member.added", member_ref(m["id"]), cause=cause,
                              data={"kind": kind, "name": m["name"], "roles": roles, "sponsor": sponsor,
                                    "profile": profile})
            if kind == "agent":  # its profile is a versioned record from the start: version 1 is what it was added with
                self._record_profile(actor, m["id"], profile, "added with the agent")
        return self.member(m["id"])

    def add_member(self, actor: dict, m: dict) -> dict:
        """A new member. A role that approves changes or manages members is only requested: another policy owner
        gives it (``approve_roles``)."""
        self.require(actor, "member_admin", "practice:main", "add members")
        if m.get("kind", "person") != "person" and m.get("sponsor") == actor["id"]:
            raise Denied(f"{actor['name']} may not add an agent or service sponsored by themselves: its sponsor issues "
                         "its credentials, so another person sponsors it")
        roles = list(m.get("roles", []) or [])
        held_back = self.held_back_roles(actor, m.get("kind", "person"))
        plain = [r for r in roles if r not in held_back]
        asked = dict(m.get("profile") or {}) if m.get("kind") == "agent" else {}
        if asked:  # a new agent may do nothing yet: the skills and risk it asks for are proposed, like any change
            asked = self._check_profile({"max_risk": "low", "skills": [], **asked})
            m = {**m, "profile": {**({"model": asked["model"]} if asked.get("model") else {}),
                                  "max_risk": "low", "skills": []}}
        with self.db:
            out = self._create_member(actor, {**m, "roles": plain})
            if len(plain) != len(roles):
                self.set_roles(actor, out["id"], roles)
            if asked and asked != self.member(out["id"])["profile"]:
                self.propose_agent_profile(actor, out["id"], asked, "what it was added to do")
        return self.member(out["id"])

    def _role_name(self, rid: str) -> str:
        return self.role(rid)["name"] if self._role_known(rid) else rid

    def _tell_approvers(self, actor: dict, cause: int, exclude: set, title: str, summary: str, href: str) -> list[str]:
        """A notice, on their work, to each active policy owner who may approve what is waiting: never the one who
        asked (``actor``) nor anyone in ``exclude`` (the member it is for, others who asked, reviewers)."""
        from .engine import _item_doc, _new_id
        who = [m for m in self.members("person") if m["status"] == "active" and m["id"] != actor.get("id")
               and m["id"] not in exclude and self.is_policy_owner(m)]
        if not who:
            return []
        run_id = _new_id("run")
        self.db.runs.insert({"_id": run_id, "id": run_id, "workflow_id": APPROVAL_WORKFLOW, "version": 0,
                             "title": title, "record": None, "status": "completed", "ctx": {"waiting": cause},
                             "steps": {"look": {"state": "skipped"}}, "started_by": actor["id"], "root_event": cause,
                             "created_at": utcnow(), "updated_at": utcnow()})
        items = []
        for m in who:
            item_id = _new_id("item")
            self.db.items.insert(_item_doc(
                self, id=item_id, run_id=run_id, step_id="look", kind="notice", role=None, assignee=m["id"],
                persons_only=1, status="open", cause=cause, title=title,
                output={"summary": summary, "where": href}))
            items.append(item_id)
        self.track.append(actor, "approval.owners_told", "practice:main", cause=cause,
                          data={"told": [m["id"] for m in who], "items": items})
        return items

    def waiting_approvals(self, actor: dict) -> list[dict]:
        """What waits for a second policy owner that ``actor`` may approve: role changes, agents' profiles and
        reviewed modules not yet in force. ``[{"what", "by", "at", "href"}]``, oldest first. Empty for anyone who is
        not a policy owner."""
        if actor.get("kind") != "person" or actor.get("status") != "active" or not self.is_policy_owner(actor):
            return []
        out = []
        for row in self.db.settings.find({"_id": {"$regex": "^role_request:"}}):
            req, mid = row["value"] or {}, row["_id"].removeprefix("role_request:")
            target = self.db.members.get(mid)
            if (not target or target["status"] != "active"
                    or actor["id"] in {req.get("by"), mid, *req.get("asked_by", [])}):
                continue
            roles = ", ".join(self._role_name(r) for r in req.get("roles") or []) or "no roles"
            out.append({"what": f"{target['name']} to hold {roles}", "by": req.get("by"), "at": req.get("at") or "",
                        "href": f"/team/{mid}"})
        for row in self.db.agent_profile_versions.find({"status": "proposed"}):
            target = self.db.members.get(row["agent_id"])
            if not target or row["author"] == actor["id"]:
                continue
            out.append({"what": f"Version {row['version']} of what {target['name']} may do", "by": row["author"],
                        "at": row["created_at"], "href": f"/team/{row['agent_id']}"})
        latest: dict = {}
        for ev in self.track.trusted({"action": "module.code_approved"}, sort=[("seq", ASC)]):
            latest[ev["data"]["module"]] = ev
        for mid, ev in latest.items():
            digest = ev["data"].get("digest")
            if digest == self._module_digest(mid) or actor["id"] in self.module_code_reviewers(mid, digest):
                continue
            out.append({"what": f"The {mid.replace('-', ' ')} module, version {ev['data'].get('version')}, to put into force",
                        "by": ev["actor"], "at": ev["at"], "href": f"/modules/{mid}"})
        return sorted(out, key=lambda x: x["at"])

    def privileged_roles(self) -> set[str]:
        """Roles whose holders approve changes or manage members."""
        return {t[2].removeprefix("role:").removesuffix("#assignee")
                for rel in ("policy_owner", "member_admin")
                for t in self.authz.read(obj="practice:main", relation=rel) if t[2].startswith("role:")}

    def role_request(self, mid: str) -> dict | None:
        return self.db.get_setting(f"role_request:{mid}")

    def restricted_classes(self) -> set[str]:
        """Data classes an agent or service is given access to only with a second policy owner's approval: every
        class, unless the pack marks it ``restricted = false`` (a practice's opening hours, say)."""
        open_ = {d["id"] for d in (self.pack.all_data_classes() if self.pack else []) if d.get("restricted") is False}
        return set(self.db.data_classes.distinct("_id")) - open_

    def data_roles(self) -> set[str]:
        """Roles that read or write a restricted data class. Giving one to an agent or a service is approved by a
        second policy owner, so that a member admin cannot give a puppet access they would not get themselves."""
        return {t[2].removeprefix("role:").removesuffix("#assignee")
                for dc in self.restricted_classes() for rel in ("reader", "writer")
                for t in self.authz.read(obj=f"data_class:{dc}", relation=rel) if t[2].startswith("role:")}

    _reach_local = threading.local()  # per thread: a page served in another thread never reads this one's

    @property
    def _reach_memo(self) -> dict | None:
        return getattr(self._reach_local, "memo", None)

    @_reach_memo.setter
    def _reach_memo(self, value: dict | None) -> None:
        self._reach_local.memo = value

    @contextmanager
    def reach_memo(self):
        """Within it, what roles reach (``beyond_roles``) is read once per actor: for a page or a sweep that asks
        about many members (every policy owner, every agent), not once per question."""
        if self._reach_memo is not None:
            yield
            return
        self._reach_memo = {}
        try:
            yield
        finally:
            self._reach_memo = None

    def _role_reach_grants(self) -> dict[tuple, set]:
        """The role grants that reach restricted data: (relation, object) -> the roles granted it. Read from those
        objects only (each restricted class, and the practice's break_glass), never the whole tuple store."""
        memo = self._reach_memo
        if memo is not None and "grants" in memo:
            return memo["grants"]
        out: dict[tuple, set] = {}
        objs = [(rel, f"data_class:{dc}") for dc in sorted(self.restricted_classes()) for rel in ("reader", "writer")]
        for rel, obj in [*objs, ("break_glass", "practice:main")]:
            for _, _, sub in self.authz.read(obj=obj, relation=rel):
                if sub.startswith("role:") and sub.endswith("#assignee"):
                    out.setdefault((rel, obj), set()).add(sub.removeprefix("role:").removesuffix("#assignee"))
        if memo is not None:
            memo["grants"] = out
        return out

    def beyond_roles(self, actor: dict) -> set[str]:
        """Roles that reach further than the actor does themselves: reading or writing a restricted data class they
        may not, or opening what a patient asked to withhold. Giving one to anyone waits for a second policy owner,
        so a member admin never reaches a patient's file through an account they hand out (a person's included)."""
        memo = self._reach_memo
        key = ("beyond", actor.get("id"), actor.get("status"))
        if memo is not None and key in memo:
            return set(memo[key])
        out = set()
        for (rel, obj), roles in self._role_reach_grants().items():
            if roles - out and not self.can(actor, rel, obj):
                out |= roles
        if memo is not None:
            memo[key] = set(out)
        return out

    def held_back_roles(self, actor: dict, kind: str = "person") -> set[str]:
        """Roles the actor's say alone does not give a member of this kind: a second policy owner approves them."""
        return (self.privileged_roles() | self.beyond_roles(actor)
                | (self.data_roles() if kind != "person" else set()))

    def reach_beyond(self, actor: dict, mid: str, roles: set | None = None) -> list[str]:
        """What the member reaches that the actor does not themselves, as names: their roles (or ``roles``, say a
        change waiting for approval too) beyond the actor's reach, and restricted data granted to them directly.
        Whoever issues a member's credential, or sponsors an agent, can act as it: they must reach as far."""
        target = self.member(mid)
        held = set(target["roles"]) if roles is None else set(roles)
        out = [self.role(r)["name"] if self._role_known(r) else r for r in sorted(held & self.beyond_roles(actor))]
        rclasses = self.restricted_classes()
        for obj, rel, _ in self.authz.read(subject=member_ref(mid)):
            if (rel in ("reader", "writer") and obj.startswith("data_class:")
                    and obj.removeprefix("data_class:") in rclasses and not self.can(actor, rel, obj)):
                out.append(f"{rel} of {obj.removeprefix('data_class:')}")
        # and a directive that lets the member see what it keeps from the actor: acting as the member would open
        # the file the subject kept from them
        if self.restrictions.allows_beyond(target, actor):
            out.append(f"a file whose directive keeps it from {actor.get('name') or actor['id']}")
        return list(dict.fromkeys(out))

    def sponsors_out_of_reach(self) -> list[dict]:
        """Switched-on agents and services whose sponsor does not reach what they do (``reach_beyond`` not empty):
        ``[{"id", "sponsor", "beyond": [...]}]``. A pack upgrade or a module that widens a role's grants, or a hub
        set up before the rule, can leave one; a member admin names a sponsor who reaches as far."""
        out = []
        with self.reach_memo():
            for m in self.members():
                if m["kind"] == "person" or m["status"] != "active":
                    continue
                sp = self.db.members.get(m.get("sponsor") or "")
                beyond = self.reach_beyond(sp, m["id"]) if sp else ["no sponsor"]
                if beyond:
                    out.append({"id": m["id"], "sponsor": m.get("sponsor") or "", "beyond": beyond})
        return out

    def reach_sweep(self, actor: dict | None, why: str) -> dict:
        """After grants widen (a pack upgrade, a module installed or upgraded) and when the hub starts: end the
        credentials whose issuer no longer reaches as far (``credentials_within_reach``), and put on the track the
        agents and services whose sponsor does not reach them, so nothing of it goes unreported."""
        actor = actor or {"id": "hub", "kind": "automation", "name": "Practice Hub"}
        with self.reach_memo():
            ended = self.credentials_within_reach(actor, why)
            out_of_reach = self.sponsors_out_of_reach()
        if ended or out_of_reach:
            self.track.append(actor, "access.reach_checked", "practice:main",
                              data={"why": why, "credentials_ended": ended, "sponsors_out_of_reach": out_of_reach})
        return {"credentials_ended": ended, "sponsors_out_of_reach": out_of_reach}

    def credentials_within_reach(self, actor: dict, why: str) -> list[str]:
        """End every credential whose issuer no longer reaches as far as its holder: after someone's roles are taken
        away, or a directive starts allowing a member it keeps from their issuer. An agent's or service's tokens end
        when its sponsor falls short (a member admin names a sponsor who reaches as far, who issues new ones); a
        person's passphrase set with a code from someone who falls short is cleared, with their tokens and sessions
        (a policy owner who reaches as far issues a new code). Returns the names of the members affected."""
        hit = []
        with self.reach_memo():
            self._credentials_sweep(actor, why, hit)
        return hit

    def _credentials_sweep(self, actor: dict, why: str, hit: list) -> None:
        for m in self.members():
            if m["status"] != "active":
                continue
            if m["kind"] != "person":
                sp = self.db.members.get(m.get("sponsor") or "")
                if (sp and self.db.tokens.find_one({"member": m["id"], "revoked": 0})
                        and self.reach_beyond(sp, m["id"])):
                    self._revoke_all(actor, m["id"], f"its sponsor no longer reaches what it does ({why})")
                    hit.append(m["name"])
                continue
            row = self.db.passphrases.get(m["id"]) or {}
            issuer = self.db.members.get(row.get("issued_by") or "")
            if issuer and issuer["id"] != m["id"] and self.reach_beyond(issuer, m["id"]):
                self._clear_passphrase(actor, m["id"], f"set with a setup code from someone who no longer reaches "
                                                      f"what they do ({why})")
                hit.append(m["name"])

    def _clear_passphrase(self, actor: dict, mid: str, why: str) -> None:
        self.db.passphrases.delete({"_id": mid})
        self._revoke_all(actor, mid, why)  # tokens made while it was in force too, and its sessions
        self.end_sessions(mid)
        self.track.append(actor, "credential.passphrase_cleared", member_ref(mid), data={"why": why})

    def _request_for(self, actor: dict, mid: str, **change) -> dict:
        """Put a change into the member's waiting role request, keeping everyone who asked: none of them approves
        it."""
        req = self.role_request(mid) or {}
        asked = list(dict.fromkeys([*req.get("asked_by", [req["by"]] if req.get("by") else []), actor["id"]]))
        req = {**req, **change, "by": actor["id"], "asked_by": asked, "at": utcnow()}
        if "roles" not in req:
            req["roles"] = sorted(self.member(mid)["roles"])
        self.db.set_setting(f"role_request:{mid}", req)
        return req

    def set_roles(self, actor: dict, mid: str, roles: list[str]) -> dict:
        """Change a member's roles. Taking roles away, or giving ordinary ones, applies now; giving a role that
        approves changes or manages members waits for a second policy owner (two people for every change).

        A policy owner who does not manage members may ask too: then the whole change waits for a second policy
        owner. Two policy owners together are the practice's way back when its only member admin is gone."""
        admin = self.can(actor, "member_admin", "practice:main")
        if not admin and not (actor.get("kind") == "person" and self.is_policy_owner(actor)):
            self.require(actor, "member_admin", "practice:main", "change roles")
        target = self.member(mid)
        if target["id"] == actor["id"]:
            raise Denied("nobody changes their own roles")
        known = set(self.db.roles.distinct("_id"))
        roles = list(dict.fromkeys(roles))
        bad = [r for r in roles if r not in known]
        if bad:
            raise Invalid(f"there is no role {', '.join(bad)}")
        before = set(target["roles"])
        wanted = set(roles)
        raised = (wanted - before) & self.privileged_roles()
        if raised and target["kind"] != "person":
            raise Invalid("only a person may hold a role that approves changes or manages members")
        # an agent or service reaching restricted data, or anyone reaching further than the one asking: a second
        # policy owner approves
        raised = (wanted - before) & self.held_back_roles(actor, target["kind"])
        if not admin:  # two policy owners together: all of it waits for the second
            raised = wanted ^ before
            if not raised:
                return target
        with self.db:
            if raised:
                req = self._request_for(actor, mid, roles=sorted(wanted))
                seq = self.track.append(actor, "member.roles_requested", member_ref(mid),
                                        data={"roles": sorted(wanted), "needs_approval": sorted(raised),
                                              **({} if admin else {"by_policy_owners": True})})
                self._tell_approvers(actor, seq, {mid, *req.get("asked_by", [])},
                                     f"Roles for {target['name']} wait for your approval",
                                     f"{actor.get('name') or actor['id']} asked that {target['name']} hold "
                                     f"{', '.join(self._role_name(r) for r in sorted(wanted)) or 'no roles'}. A "
                                     f"second policy owner approves it, or declines it, on {target['name']}'s page "
                                     f"in the team.", f"/team/{mid}")
                if not admin:
                    return self.member(mid)
                wanted -= raised  # the rest applies now
            else:
                self.db.del_setting(f"role_request:{mid}")
            self._apply_roles(actor, mid, before, wanted)
        return self.member(mid)

    def approve_roles(self, actor: dict, mid: str) -> dict:
        """A second policy owner gives the roles another member admin (or policy owner) asked for."""
        self.require(actor, "policy_owner", "practice:main", "approve a role change")
        req = self.role_request(mid)
        if not req:
            raise Invalid("there is no role change waiting for approval")
        if actor["id"] in {req["by"], mid, *req.get("asked_by", [])}:
            raise Denied("a role change is approved by a policy owner other than the one who asked, and not by the "
                         "member it is for")
        target = self.member(mid)
        if target["status"] != "active":
            raise Invalid(f"{target['name']} is switched off")
        with self.db:
            self.db.del_setting(f"role_request:{mid}")
            self._apply_roles(actor, mid, set(target["roles"]), set(req["roles"]), approved_from=req["by"])
        return self.member(mid)

    def withdraw_roles(self, actor: dict, mid: str) -> None:
        if not self.is_policy_owner(actor):
            self.require(actor, "member_admin", "practice:main", "withdraw a role change")
        with self.db:
            if self.role_request(mid):
                self.db.del_setting(f"role_request:{mid}")
                self.track.append(actor, "member.roles_withdrawn", member_ref(mid))

    def _apply_roles(self, actor: dict, mid: str, before: set, after: set, approved_from: str | None = None) -> None:
        if before == after:
            return
        with self.db:
            owners, admins = self.policy_owner_count(), len(self.member_admins())
            # every refusal before the first authz write: an external authorizer (OpenFGA) does not roll back with
            # the database, so a refusal after it would leave the roles granted there
            target = self.member(mid)
            if target["status"] == "active" and target["kind"] == "person":
                def lose(rel):
                    return self.can(target, rel, "practice:main") and not self._held_through(mid, rel, after)
                self._keep_a_policy_owner(owners, now=owners - (1 if lose("policy_owner") else 0))
                self._keep_a_member_admin(admins, now=admins - (1 if lose("member_admin") else 0))
            if after - before and target["kind"] != "person":
                self._sponsor_reaches(target, after - before)
            self.authz.delete([(f"role:{r}", "assignee", member_ref(mid)) for r in before - after])
            self.authz.write([(f"role:{r}", "assignee", member_ref(mid)) for r in after - before])
            self._keep_a_policy_owner(owners)
            self._keep_a_member_admin(admins)
            if after - before:
                self._credentials_within_reach(actor, mid, after - before)
            if before - after:  # whoever lost roles may no longer reach what they sponsor, or gave a code for
                self.credentials_within_reach(actor, f"{self.member(mid)['name']}'s roles changed")
            self.track.append(actor, "member.roles_changed", member_ref(mid),
                              data={"added": sorted(after - before), "removed": sorted(before - after),
                                    **({"asked_by": approved_from} if approved_from else {})})

    def _held_through(self, mid: str, rel: str, roles: set) -> bool:
        """Whether the member would still hold ``rel`` on the practice holding exactly ``roles``: granted to them
        directly, or through one of those roles (``owner`` implies both practice relations)."""
        sets = {member_ref(mid), *(f"role:{r}#assignee" for r in roles)}
        return any(t[2] in sets for r in (rel, "owner") for t in self.authz.read(obj="practice:main", relation=r))

    def _sponsor_reaches(self, target: dict, added: set) -> None:
        sp = self.db.members.get(target.get("sponsor") or "")
        if sp and sp["id"] != target["id"] and self.reach_beyond(sp, target["id"], added):
            raise Invalid(f"{target['name']}'s sponsor does not reach what those roles reach: name a sponsor who "
                          f"does first (whoever sponsors it issues its credentials and can act through it)")

    def _credentials_within_reach(self, actor: dict, mid: str, added: set) -> None:
        """A member given roles that reach further than whoever issued their credential: that credential ends.
        A person's passphrase set with a setup code from such an issuer is cleared (and unused codes from them
        superseded), so a policy owner who reaches as far issues a fresh one; an agent's sponsor must reach as far
        as the agent, so its tokens end and the approval is refused while its sponsor does not."""
        target = self.member(mid)

        def short(who):  # the same rule as at issue time: reach_beyond, direct grants and directives included
            if not who or who == mid:
                return False
            m = self.db.members.get(who)
            return bool(m) and bool(self.reach_beyond(m, mid, added))
        if target["kind"] != "person":
            self._sponsor_reaches(target, added)
            return
        for row in self.db.setup_codes.find({"member": mid, "used_at": None}):
            if short(row.get("issued_by")):
                self.db.setup_codes.set(row["code_hash"], {"used_at": "superseded " + utcnow()})
        row = self.db.passphrases.get(mid)
        if row and short(row.get("issued_by")):
            self._clear_passphrase(actor, mid, "set with a setup code from someone who does not reach the roles now "
                                               "given: a policy owner who does issues a new one")

    MIN_POLICY_OWNERS = 2  # every change is approved by a second person, so it takes two to approve anything

    def policy_owner_count(self) -> int:
        """Active people who are policy owners."""
        return sum(1 for m in self.members("person") if m["status"] == "active" and self.is_policy_owner(m))

    def _keep_a_policy_owner(self, before: int | None = None, *, now: int | None = None) -> None:
        """Changes are approved by a second policy owner, so a practice needs two. Refuse whatever takes it below two
        (or leaves it with none). With fewer than two, the server holder adds one: ``practicehub owners recover``.
        ``now``: the count a change would leave, checked before it is written."""
        now = self.policy_owner_count() if now is None else now
        if now == 0:
            raise Invalid("that would leave the practice with no active policy owner to approve changes")
        if before is not None and now < before and now < self.MIN_POLICY_OWNERS:
            raise Invalid(f"that would leave the practice with {now} active policy owner; changes need a second one "
                          f"to approve them. Give another person a policy owner's role first")

    def recover_policy_owner(self, mid: str, reason: str = "") -> dict:
        """The recovery path when fewer than two active policy owners are left (one left, was ill, or locked the
        other out): whoever holds the server and the track's secret makes an active person a policy owner. The
        track records that the server holder did it, not a member."""
        if not self.track.holds_secret():
            raise Denied("this server does not hold the secret that sealed the track: set PRACTICEHUB_SECRET to it")
        target = self.member(mid)
        if target["kind"] != "person" or target["status"] != "active":
            raise Invalid("a policy owner is an active person")
        if self.is_policy_owner(target):
            raise Invalid(f"{target['name']} is already a policy owner")
        if self.policy_owner_count() >= self.MIN_POLICY_OWNERS:
            raise Denied("the practice has two active policy owners: a member admin asks and a second policy owner "
                         "approves (practicehub member roles / approve-roles)")
        def granted(rel):
            return {t[2].removeprefix("role:").removesuffix("#assignee")
                    for t in self.authz.read(obj="practice:main", relation=rel) if t[2].startswith("role:")}
        owner_roles = granted("policy_owner")
        # the role that approves changes and does not also manage members, if the practice has one
        role = (sorted(owner_roles - granted("member_admin")) or sorted(owner_roles) or [None])[0]
        holder = {"id": "server-holder", "kind": "installer"}
        with self.db:
            if role:
                self.authz.write([(f"role:{role}", "assignee", member_ref(mid))])
            else:
                self.authz.write([("practice:main", "policy_owner", member_ref(mid))])
            self.db.del_setting(f"role_request:{mid}")
            self.track.append(holder, "member.owner_recovered", member_ref(mid),
                              data={"role": role, "reason": reason, "by": "the holder of the server and its secret",
                                    "policy_owners_before": self.policy_owner_count() - 1})
        return {**self.member(mid), "role": role or "policy_owner"}

    # ------------------------------------------------------------------ someone to manage members
    def member_admins(self) -> list[dict]:
        """Switched-on people who manage members (add people, change roles, switch someone off)."""
        return [m for m in self.members("person") if m["status"] == "active" and self.can(m, "member_admin", "practice:main")]

    def member_admin_warning(self) -> str | None:
        """What to tell a practice that has only one person (or nobody) who manages members."""
        admins = self.member_admins()
        if len(admins) == 1:
            return (f"{admins[0]['name']} is the only person who manages members. If they leave, another policy owner "
                    f"asks to switch them off, and a second one approves (or, with no second one, whoever holds the "
                    f"server: practicehub owners approve-switch-off); two policy owners together then give the role to "
                    f"someone else. Give a second person a role that manages members.")
        if not admins:
            return ("Nobody switched on manages members. Two policy owners together can give someone a role that "
                    "does: one asks, the other approves.")
        return None

    def _keep_a_member_admin(self, before: int, *, now: int | None = None) -> None:
        """Refuse whatever leaves the practice with nobody who manages members (it had someone before)."""
        if before and not (len(self.member_admins()) if now is None else now):
            raise Invalid("that would leave nobody who manages members (adds people, changes roles, switches someone "
                          "off): give another person a role that manages members first")

    # ------------------------------------------------------------------ the off switch
    def dependents(self, mid: str) -> list[dict]:
        """The switched-on agents and services a person sponsors. They stop with their sponsor, and their credentials
        end for good, unless each is handed to a new sponsor first: what the switch-off form shows and asks about."""
        target = self.member(mid)
        if target["kind"] != "person":
            return []
        rows = [self.member(r["id"]) for r in self.db.members.find({"sponsor": mid, "status": "active"},
                                                                   sort=[("name", ASC)], fields=["id"])]
        going = {mid} | {m["id"] for m in rows}
        out = []
        for m in rows:
            out.append({"id": m["id"], "name": m["name"], "kind": m["kind"], "roles": m["roles"],
                        "credentials": self.db.tokens.count({"member": m["id"], "revoked": 0}),
                        "only_holder_of": [r for r in m["roles"]
                                           if not [h for h in self.active_holders(r) if h["id"] not in going]]})
        return out

    def _dependents_plan(self, target: dict, hand_over: dict | None, stop) -> dict:
        """What becomes of each agent or service the target sponsors: handed to a new sponsor, or stopped, each one
        chosen. Nothing stops without someone saying so."""
        deps = self.dependents(target["id"])
        hand_over = {str(k): str(v) for k, v in (hand_over or {}).items() if v}
        ids = {d["id"] for d in deps}
        if stop is True or stop == "all":
            stop = [d["id"] for d in deps if d["id"] not in hand_over]
        stop = [str(x) for x in (stop or [])]
        problems = [f"{x} is not an agent or service {target['name']} sponsors" for x in [*hand_over, *stop]
                    if x not in ids]
        problems += [f"{x} is both handed over and stopped" for x in set(hand_over) & set(stop)]
        for aid, sp in hand_over.items():
            new = self.db.members.get(sp)
            if not new or new["kind"] != "person" or new["status"] != "active" or sp == target["id"]:
                problems.append(f"{aid}: a new sponsor is a switched-on person other than {target['name']}")
            elif aid in ids and self.reach_beyond(new, aid):
                problems.append(f"{aid}: {new['name']} does not reach what it does "
                                f"({', '.join(self.reach_beyond(new, aid))}): name a sponsor who does")
        undecided = [d for d in deps if d["id"] not in hand_over and d["id"] not in stop]
        for d in undecided:
            what = [f"{d['credentials']} live credential{'s' if d['credentials'] != 1 else ''} end for good"
                    if d["credentials"] else "", f"nobody else holds {', '.join(d['only_holder_of'])}"
                    if d["only_holder_of"] else ""]
            problems.append(f"{d['name']} stops" + (f" ({'; '.join(w for w in what if w)})" if any(what) else "")
                            + ": hand it to a new sponsor, or say that it may stop")
        if problems:
            names = ", ".join(d["name"] for d in deps)
            raise Invalid(f"{target['name']} sponsors {names}: choose a new sponsor for each, or say that it stops",
                          problems)
        return {"hand_over": hand_over, "stop": stop}

    def set_status(self, actor: dict, mid: str, status: str, reason: str = "", *, hand_over: dict | None = None,
                   stop=None) -> dict:
        """Disable (the off switch) or re-enable a member. An agent's sponsor may switch it off; turning anyone on,
        or off a person, takes a member admin (or two policy owners: ``request_switch_off``).

        A person who sponsors agents or services is switched off only once each of them is handed to a new sponsor
        (``hand_over``: agent -> person) or stops by choice (``stop``: their ids, or ``"all"``)."""
        target = self.member(mid)
        if status not in ("active", "disabled"):
            raise Invalid("status is active or disabled")
        is_sponsor = target["sponsor"] == actor["id"]
        if not (self.can(actor, "member_admin", "practice:main") or (is_sponsor and status == "disabled")):
            raise Denied(f"{actor['name']} may not {'disable' if status == 'disabled' else 'enable'} {target['name']}"
                         + (": two policy owners together may (one asks, the other approves)"
                            if status == "disabled" and self.is_policy_owner(actor) else ""))
        if status == "active" and target["kind"] != "person":
            sp = self.member(target["sponsor"])
            if sp["status"] != "active":
                raise Invalid(f"{target['name']}'s sponsor {sp['name']} is not active: give it a new sponsor first")
        plan = self._dependents_plan(target, hand_over, stop) if status == "disabled" else None
        return self._switch(actor, target, status, reason, plan)

    def _switch(self, actor: dict, target: dict, status: str, reason: str, plan: dict | None,
                asked_by: str | None = None, keep: bool = True) -> dict:
        mid = target["id"]
        # the roles held by whoever goes off: this member, and the agents that stop with them
        going = [target] + [self.member(a) for a in (plan or {}).get("stop", [])]
        had = [r for r in dict.fromkeys(r for m in going for r in m["roles"]) if self.active_holders(r)]
        with self.db:
            released = self._set_status(actor, target, status, reason, plan, asked_by, keep)
        out = self.member(mid)
        out["roles_left_empty"] = [r for r in had if not self.active_holders(r)] if status == "disabled" else []
        out["released"] = released or []
        out["stopped"] = list((plan or {}).get("stop") or [])
        out["handed_over"] = dict((plan or {}).get("hand_over") or {})
        return out

    def _set_status(self, actor: dict, target: dict, status: str, reason: str, plan: dict | None = None,
                    asked_by: str | None = None, keep: bool = True) -> list[str]:
        mid = target["id"]
        plan = plan or {"hand_over": {}, "stop": []}
        owners, admins = self.policy_owner_count(), len(self.member_admins())
        for aid, sp in plan["hand_over"].items():  # handed over while their sponsor is still on: they never stop
            self._move_sponsor(actor, aid, sp, f"handed over when {target['name']} was switched off")
        self.db.members.set(mid, {"status": status})
        if status == "disabled" and target["kind"] == "person" and keep:
            self._keep_a_policy_owner(owners)
            self._keep_a_member_admin(admins)
        data = {"reason": reason}
        if status == "disabled" and target["kind"] == "person" and (plan["hand_over"] or plan["stop"]):
            data.update(handed_over=plan["hand_over"], stopped=plan["stop"])
        if asked_by:
            data["asked_by"] = asked_by
        self.track.append(actor, "member.disabled" if status == "disabled" else "member.enabled", member_ref(mid),
                          data=data)
        released: list[str] = []
        if status == "disabled":
            # switching someone off ends their credentials for good: switching them on again does not revive them
            self._revoke_all(actor, mid, "switched off")
            if target["kind"] == "person":  # the agents chosen to stop with their sponsor stop until re-sponsored
                for agent in self.db.members.find({"sponsor": mid, "status": "active"}, fields=["id"]):
                    if agent["id"] not in plan["stop"]:  # nothing stops that nobody chose to stop
                        raise Invalid(f"{agent['id']} is sponsored by {target['name']}: hand it over or say it stops")
                    self.db.members.set(agent["id"], {"status": "disabled"})
                    self._revoke_all(actor, agent["id"], f"its sponsor {target['name']} was switched off and "
                                                         f"{actor['name']} chose that it stops")
                    self.track.append(actor, "member.disabled", member_ref(agent["id"]),
                                      data={"reason": f"its sponsor {target['name']} was disabled; "
                                                      f"{actor['name']} chose that it stops"})
                    released += self.engine.release_claims_of(agent["id"], actor, "its sponsor was disabled")
            released += self.engine.release_claims_of(mid, actor, reason or "disabled")
        return released

    # ------------------------------------------------------------------ two policy owners switch someone off
    def off_request(self, mid: str) -> dict | None:
        """A policy owner's request to switch a member off, waiting for a second policy owner."""
        return self.db.get_setting(f"off_request:{mid}")

    def request_switch_off(self, actor: dict, mid: str, reason: str, *, hand_over: dict | None = None,
                           stop=None) -> dict:
        """Two policy owners together switch a member off, for when no member admin can (the only one is away, or
        is the one leaving): one asks, with the reason and what becomes of their agents; another approves."""
        if actor.get("kind") != "person":
            raise Denied("a person asks to switch someone off")
        self.require(actor, "policy_owner", "practice:main", "ask to switch someone off")
        target = self.member(mid)
        if mid == actor["id"]:
            raise Denied("nobody asks to switch themselves off")
        if target["status"] != "active":
            raise Invalid(f"{target['name']} is already switched off")
        if not (reason or "").strip():
            raise Invalid("say why they are being switched off")
        plan = self._dependents_plan(target, hand_over, stop)
        self._not_handed_to_asker(plan, actor["id"])
        req = {"by": actor["id"], "reason": reason.strip(), **plan, "at": utcnow()}
        with self.db:
            self.db.set_setting(f"off_request:{mid}", req)
            self.track.append(actor, "member.off_requested", member_ref(mid),
                              data={"reason": req["reason"], "handed_over": plan["hand_over"], "stopped": plan["stop"]})
        return req

    def approve_switch_off(self, actor: dict, mid: str) -> dict:
        """A second policy owner, not the one who asked and not the member, switches them off as asked."""
        if actor.get("kind") != "person":
            raise Denied("a person approves switching someone off")
        self.require(actor, "policy_owner", "practice:main", "approve switching someone off")
        req = self.off_request(mid)
        if not req:
            raise Invalid("nobody has asked to switch them off")
        if actor["id"] in (req["by"], mid):
            raise Denied("switching someone off is approved by a policy owner other than the one who asked, and not "
                         "by the member it is for")
        target = self.member(mid)
        if target["status"] != "active":
            raise Invalid(f"{target['name']} is already switched off")
        plan = self._dependents_plan(target, req["hand_over"], req["stop"])  # their agents may have changed since
        self._not_handed_to_asker(plan, req["by"])
        with self.db:
            self.db.del_setting(f"off_request:{mid}")
            return self._switch(actor, target, "disabled", req["reason"], plan, asked_by=req["by"])

    def holder_approves_switch_off(self, mid: str) -> dict:
        """When a policy owner has asked to switch someone off and nobody else could approve it (the practice's
        other policy owner is the one leaving): whoever holds the server and the track's secret approves instead.
        The member goes off even if that leaves one policy owner or no member admin: the server holder then makes a
        second policy owner (``recover_policy_owner``), and two policy owners together give out the roles."""
        if not self.track.holds_secret():
            raise Denied("this server does not hold the secret that sealed the track: set PRACTICEHUB_SECRET to it")
        req = self.off_request(mid)
        if not req:
            raise Invalid("nobody has asked to switch them off: a policy owner asks first, with the reason")
        target = self.member(mid)
        if target["status"] != "active":
            raise Invalid(f"{target['name']} is already switched off")
        others = [m for m in self.members("person") if m["status"] == "active" and self.is_policy_owner(m)
                  and m["id"] not in (req["by"], mid)]
        if others:
            raise Denied(f"{', '.join(m['name'] for m in others)} can approve it: the server holder approves only "
                         "when no other policy owner can")
        asker = self.db.members.get(req["by"])
        if not asker or asker["status"] != "active" or not self.is_policy_owner(asker):
            raise Denied("whoever asked is no longer an active policy owner: a policy owner asks again")
        holder = {"id": "server-holder", "kind": "installer", "name": "the server holder"}
        plan = self._dependents_plan(target, req["hand_over"], req["stop"])
        self._not_handed_to_asker(plan, req["by"])
        with self.db:
            self.db.del_setting(f"off_request:{mid}")
            return self._switch(holder, target, "disabled", req["reason"], plan, asked_by=req["by"], keep=False)

    def _not_handed_to_asker(self, plan: dict, asker: str) -> None:
        """Whoever asks to switch someone off does not name themselves the new sponsor of that person's agents: the
        sponsor issues an agent's credentials and can act through it, so that takes someone else's say."""
        mine = sorted(aid for aid, sp in plan["hand_over"].items() if sp == asker)
        if mine:
            name = (self.db.members.get(asker) or {}).get("name") or asker
            raise Invalid(f"{name} asks to switch them off, so may not also take over {', '.join(mine)}: name "
                          f"another sponsor, or hand it over later through a member admin")

    def withdraw_switch_off(self, actor: dict, mid: str) -> None:
        req = self.off_request(mid)
        if not req:
            raise Invalid("nobody has asked to switch them off")
        if actor["id"] == mid:
            raise Invalid("the member a switch-off request is about does not withdraw it: the one who asked, or "
                          "another policy owner, does")
        if actor["id"] != req["by"]:
            self.require(actor, "policy_owner", "practice:main", "withdraw a request to switch someone off")
        with self.db:
            self.db.del_setting(f"off_request:{mid}")
            self.track.append(actor, "member.off_withdrawn", member_ref(mid))

    def set_sponsor(self, actor: dict, mid: str, sponsor: str) -> dict:
        self.require(actor, "member_admin", "practice:main", "change a sponsor")
        with self.db:
            self._move_sponsor(actor, mid, sponsor)
        return self.member(mid)

    def _move_sponsor(self, actor: dict, mid: str, sponsor: str, why: str = "") -> None:
        target, sp = self.member(mid), self.member(sponsor)
        if target["kind"] == "person":
            raise Invalid("people do not have sponsors")
        if sp["kind"] != "person" or sp["status"] != "active":
            raise Invalid("a sponsor is an active person")
        # whoever sponsors an agent issues its credentials, and so can act through it: nobody makes themselves one
        # (a member admin who could would reach everything the agent reads with no second person involved)
        if sponsor == actor["id"] and target["sponsor"] != sponsor:
            raise Denied(f"{actor['name']} may not make themselves {target['name']}'s sponsor: another member admin "
                         "names them, so no one gains what the agent may read on their own say")
        beyond = self.reach_beyond(sp, mid)
        if beyond:
            raise Denied(f"{sp['name']} does not reach what {target['name']} does ({', '.join(beyond)}): its sponsor "
                         "issues its credentials and can act through it, so name someone who reaches as far")
        if target["sponsor"]:
            self.authz.delete([(member_ref(mid), "sponsor", member_ref(target["sponsor"]))])
        self.authz.write([(member_ref(mid), "sponsor", member_ref(sponsor))])
        if target["sponsor"] != sponsor:  # the old sponsor's tokens for it end with the hand-over
            self._revoke_all(actor, mid, "its sponsor changed")
        self.db.members.set(mid, {"sponsor": sponsor})
        self.track.append(actor, "member.sponsor_changed", member_ref(mid),
                          data={"from": target["sponsor"], "to": sponsor, **({"reason": why} if why else {})})
        self.engine.move_sponsor_work(mid, sponsor, actor)

    # ------------------------------------------------------------------ an agent's profile: a controlled record
    # What an agent may do (its model, the skills and prompt blocks it takes, the highest risk) is a versioned record
    # under the same change control as a definition: proposed, approved by a second person who is a policy owner,
    # then in force. Nothing changes an agent's behaviour at once, and every version is kept.
    PROFILE_KEYS = ("model", "max_risk", "skills")

    def _check_profile(self, profile: dict) -> dict:
        extra = sorted(set(profile) - set(self.PROFILE_KEYS))
        if extra:
            raise Invalid(f"an agent's profile holds {', '.join(self.PROFILE_KEYS)}, not {', '.join(extra)}")
        out = {k: profile[k] for k in self.PROFILE_KEYS if k in profile}
        if out.get("max_risk") not in RISK_ORDER:
            raise Invalid("max_risk is low, medium or high")
        if "model" in out:
            out["model"] = str(out["model"] or "").strip()
            if not out["model"]:
                out.pop("model")
        skills = out.get("skills", [])
        if not isinstance(skills, list) or not all(isinstance(x, str) for x in skills):
            raise Invalid("skills is a list of skill or prompt block ids")
        out["skills"] = list(dict.fromkeys(skills))
        prompts = set(self.db.block_versions.distinct("block_id", {"status": "active"}))
        unknown = [x for x in out["skills"] if self.pack and x not in self.pack.skills and x not in prompts]
        if unknown:
            raise Invalid(f"there is no skill or prompt block {', '.join(unknown)}")
        return out

    @staticmethod
    def _profile_source(profile: dict) -> str:
        return json.dumps(profile, sort_keys=True, separators=(",", ":"))

    def _record_profile(self, actor: dict, mid: str, profile: dict, note: str) -> dict:
        """Put a profile in force without a second person: an agent's first version (added, or installed with a
        pack or module, which were reviewed themselves), or the one it had before profiles were versioned."""
        version = (self.db.agent_profile_versions.max("version", {"agent_id": mid}) or 0) + 1
        source = self._profile_source(profile)
        row = {"_id": f"{mid}@{version}", "agent_id": mid, "version": version, "status": "active",
               "profile": profile, "source": source, "author": actor["id"], "note": note, "created_at": utcnow(),
               "approved_by": None, "approved_at": None, "based_on": None}
        self.db.agent_profile_versions.insert(row)
        self.track.append(actor, "agent.profile_recorded", member_ref(mid),
                          data={"version": version, "note": note,
                                "source_sha256": hashlib.sha256(source.encode()).hexdigest()})
        return row

    def agent_profile_versions(self, mid: str) -> list[dict]:
        return self.db.agent_profile_versions.find({"agent_id": mid}, sort=[("version", DESC)])

    def agent_profile_proposal(self, mid: str) -> dict | None:
        return self.db.agent_profile_versions.find_one({"agent_id": mid, "status": "proposed"},
                                                       sort=[("version", DESC)])

    def _profile_in_force(self, actor: dict, target: dict) -> dict:
        row = self.db.agent_profile_versions.find_one({"agent_id": target["id"], "status": "active"})
        return row or self._record_profile(actor, target["id"], target["profile"],
                                           "the profile it had before profiles were versioned")

    def propose_agent_profile(self, actor: dict, mid: str, changes: dict, note: str = "") -> dict:
        """Propose a new version of an agent's profile (a member admin, or its sponsor). It is in force only once a
        policy owner other than whoever proposed it approves it. A newer proposal replaces one still waiting.
        A pure reduction (a lower ``max_risk`` and/or skills taken away, the model and everything else as it is)
        only ever lets the agent do less: it is in force at once, as a version on the track (``status`` active)."""
        target = self.member(mid)
        if target["kind"] != "agent":
            raise Invalid("only agents have a profile")
        if not (self.can(actor, "member_admin", "practice:main") or target["sponsor"] == actor["id"]):
            self.require(actor, "member_admin", "practice:main", "propose a change to an agent's profile")
        extra = sorted(set(changes) - set(self.PROFILE_KEYS))
        if extra:
            raise Invalid(f"an agent's profile holds {', '.join(self.PROFILE_KEYS)}, not {', '.join(extra)}")
        now = {"max_risk": "low", "skills": [], **target["profile"]}
        profile = self._check_profile({**now, **{k: v for k, v in changes.items() if v is not None}})
        current = self._check_profile(now)
        if profile == current:
            raise Invalid("that is the profile in force: there is nothing to propose")
        reduction = (profile.get("model") == current.get("model")
                     and RISK_ORDER[profile["max_risk"]] <= RISK_ORDER[current["max_risk"]]
                     and set(profile["skills"]) <= set(current["skills"]))
        with self.db:
            base = self._profile_in_force(actor, target)
            if reduction:
                return self._reduce_profile(actor, target, base, profile, note)
            for old in self.db.agent_profile_versions.find({"agent_id": mid, "status": "proposed"}):
                self.db.agent_profile_versions.set(old["_id"], {"status": "withdrawn"})
                self.track.append(actor, "agent.profile_withdrawn", member_ref(mid),
                                  data={"version": old["version"], "reason": "a newer proposal replaces it"})
            version = (self.db.agent_profile_versions.max("version", {"agent_id": mid}) or 0) + 1
            source = self._profile_source(profile)
            self.db.agent_profile_versions.insert({
                "_id": f"{mid}@{version}", "agent_id": mid, "version": version, "status": "proposed",
                "profile": profile, "source": source, "author": actor["id"], "note": note, "created_at": utcnow(),
                "approved_by": None, "approved_at": None, "based_on": base["version"]})
            seq = self.track.append(actor, "agent.profile_proposed", member_ref(mid),
                                    data={"version": version, "note": note, "before": base["profile"],
                                          "after": profile})
            self._tell_approvers(actor, seq, {mid}, f"A change to what {target['name']} may do waits for your approval",
                                 f"{actor.get('name') or actor['id']} proposed version {version} of "
                                 f"{target['name']}'s profile (its model, highest risk and skills). A policy owner "
                                 f"other than whoever proposed it approves it on {target['name']}'s page in the "
                                 f"team.", f"/team/{mid}")
        return {"agent_id": mid, "version": version, "profile": profile, "status": "proposed"}

    def _reduce_profile(self, actor: dict, target: dict, base: dict, profile: dict, note: str) -> dict:
        """Put a pure reduction of an agent's profile in force at once: a new active version, the last retired, and
        any proposal still waiting withdrawn (it was based on the version this replaces)."""
        mid = target["id"]
        for old in self.db.agent_profile_versions.find({"agent_id": mid, "status": "proposed"}):
            self.db.agent_profile_versions.set(old["_id"], {"status": "withdrawn"})
            self.track.append(actor, "agent.profile_withdrawn", member_ref(mid),
                              data={"version": old["version"], "reason": "the profile was reduced since"})
        self.db.agent_profile_versions.update({"agent_id": mid, "status": "active"}, {"$set": {"status": "retired"}},
                                              many=True)
        version = (self.db.agent_profile_versions.max("version", {"agent_id": mid}) or 0) + 1
        source = self._profile_source(profile)
        self.db.agent_profile_versions.insert({
            "_id": f"{mid}@{version}", "agent_id": mid, "version": version, "status": "active",
            "profile": profile, "source": source, "author": actor["id"], "note": note, "created_at": utcnow(),
            "approved_by": None, "approved_at": None, "based_on": base["version"], "reduction": True})
        self.db.members.set(mid, {"profile": profile})
        self.track.append(actor, "agent.profile_reduced", member_ref(mid),
                          data={"version": version, "note": note, "before": base["profile"], "after": profile,
                                "source_sha256": hashlib.sha256(source.encode()).hexdigest()})
        self.track.append(actor, "member.profile_changed", member_ref(mid),
                          data={"before": target["profile"], "after": profile, "version": version,
                                "reduction": True})
        return {"agent_id": mid, "version": version, "profile": profile, "status": "active"}

    def approve_agent_profile(self, actor: dict, mid: str, version: int, meaning: str = "") -> dict:
        """A policy owner who did not propose it puts a proposed profile in force, signing its text."""
        row = self.db.agent_profile_versions.find_one({"agent_id": mid, "version": int(version)})
        if not row or row["status"] != "proposed":
            raise Invalid("only a proposed profile can be approved")
        if actor.get("kind") != "person":
            raise Denied("an agent's profile is approved by a person, never an agent")
        self.require(actor, "policy_owner", "practice:main", "approve a change to an agent's profile")
        if row["author"] == actor["id"]:
            raise Denied("whoever proposed a change does not approve it")
        target = self.member(mid)
        base = self.db.agent_profile_versions.find_one({"agent_id": mid, "status": "active"})
        if base and row["based_on"] != base["version"]:
            raise Invalid(f"version {base['version']} came into force after this was proposed: propose it again")
        try:
            profile = self._check_profile(row["profile"])
        except Invalid as exc:
            raise Invalid(f"this profile no longer checks: {exc}") from None
        digest = hashlib.sha256(row["source"].encode()).hexdigest()
        meaning = meaning or f"I approve version {row['version']} of {target['name']}'s profile"
        with self.db:
            self.db.agent_profile_versions.update({"agent_id": mid, "status": "active"},
                                                  {"$set": {"status": "retired"}}, many=True)
            n = self.db.agent_profile_versions.update(
                {"agent_id": mid, "version": row["version"], "status": "proposed"},
                {"$set": {"status": "active", "approved_by": actor["id"], "approved_at": utcnow()}})
            if n != 1:
                raise Invalid(f"version {row['version']} is no longer proposed")
            self.db.members.set(mid, {"profile": profile})
            self.track.append(actor, "agent.profile_approved", member_ref(mid),
                              data={"version": row["version"], "source_sha256": digest, "asked_by": row["author"]},
                              signed={"agent": mid, "version": row["version"], "meaning": meaning,
                                      "source_sha256": digest})
            self.track.append(actor, "member.profile_changed", member_ref(mid),
                              data={"before": target["profile"], "after": profile, "asked_by": row["author"],
                                    "version": row["version"]})
        return self.member(mid)

    def withdraw_agent_profile(self, actor: dict, mid: str, version: int, reason: str = "") -> None:
        """Its author withdraws a proposed profile, or a policy owner declines it."""
        with self.db:
            row = self.db.agent_profile_versions.find_one({"agent_id": mid, "version": int(version)})
            if not row or row["status"] != "proposed":
                raise Invalid("only a proposed profile can be withdrawn or declined")
            if row["author"] != actor["id"] and not self.is_policy_owner(actor):
                raise Denied("only its author or a policy owner may withdraw a proposed profile")
            self.db.agent_profile_versions.set(row["_id"], {"status": "withdrawn"})
            self.track.append(actor, "agent.profile_withdrawn", member_ref(mid),
                              data={"version": row["version"], "reason": reason})

    # ------------------------------------------------------------------ credentials
    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def issue_token(self, actor: dict, mid: str, label: str = "", *, scope: str = "api", servers: list | None = None,
                    client: dict | None = None) -> str:
        """An API token for a member. A person's comes only to that person (whoever holds it can act and sign as
        them); an agent's or service's only from its sponsor, who answers for what is done with it (a member admin
        who could mint one could act through the agent as a puppet). ``scope="mcp"`` makes one that opens only the
        MCP servers named, for one named client (``practicehub.mcp.issue_token`` checks those first); an MCP token
        is refused everywhere else, and any other token by the MCP servers. ``scope="portal"`` is a service's token
        for the hub's ``/api/portal/`` only (the portal's own process holds it; it opens nothing else)."""
        if scope not in ("api", "mcp", "portal"):
            raise Invalid("a token is for the API, for MCP or for the portal")
        target = self.member(mid)
        if target["kind"] == "person":
            allowed = actor["id"] == mid or actor.get("kind") == "installer"
        else:
            allowed = target["sponsor"] == actor["id"] or actor.get("kind") == "installer"
        if allowed and target["kind"] != "person" and actor.get("kind") != "installer":
            beyond = self.reach_beyond(actor, mid)
            if beyond:
                raise Denied(f"{target['name']} reaches what {actor['name']} does not ({', '.join(beyond)}): a token "
                             "for it comes from a sponsor who reaches as far (a member admin names one)")
        if not allowed:
            raise Denied(f"{actor['name']} may not issue credentials for {target['name']}"
                         + (": a person's credentials are their own" if target["kind"] == "person" else
                            ": only its sponsor issues them"))
        if target["status"] != "active":
            raise Invalid(f"{target['name']} is switched off")
        if scope == "portal" and target["kind"] != "service":
            raise Invalid("a portal token is a service's (the practice's [portal] member)")
        token = "ph_" + secrets.token_urlsafe(32)
        th = self._hash_token(token)
        row = {"_id": th, "token_hash": th, "member": mid, "label": label, "created_at": utcnow(), "revoked": 0,
               "issued_by": actor["id"], "scope": scope}
        if scope == "mcp":
            row.update({"servers": list(servers or []), "client": dict(client or {})})
        with self.db:
            self.db.tokens.insert(row)
            self.track.append(actor, "credential.issued", member_ref(mid),
                              data={"label": label, "scope": scope, "credential": th[:12],
                                    **({"servers": row["servers"], "client": row["client"]} if scope == "mcp" else {})})
        return token

    def tokens(self, actor: dict, mid: str) -> list[dict]:
        """A member's credentials (never the secret): to the member, their sponsor and member admins."""
        target = self.member(mid)
        if not (actor["id"] == mid or target["sponsor"] == actor["id"]
                or self.can(actor, "member_admin", "practice:main")):
            raise Denied(f"{actor['name']} may not see {target['name']}'s credentials")
        return [{"id": r["token_hash"][:12], "label": r["label"], "created_at": r["created_at"],
                 "issued_by": r["issued_by"], "revoked": bool(r["revoked"]), "scope": r.get("scope", "api"),
                 "servers": r.get("servers", []), "client": r.get("client") or {}}
                for r in self.db.tokens.find({"member": mid}, sort=[("created_at", DESC)])]

    def revoke_token(self, actor: dict, mid: str, tid: str) -> None:
        """End one credential, without switching the member off: the member, their sponsor or a member admin."""
        target = self.member(mid)
        if not (actor["id"] == mid or target["sponsor"] == actor["id"]
                or self.can(actor, "member_admin", "practice:main")):
            raise Denied(f"{actor['name']} may not revoke {target['name']}'s credentials")
        if not re.fullmatch(r"[0-9a-f]{6,64}", tid or ""):
            raise Invalid("that is not a credential id")
        rows = self.db.tokens.find({"member": mid, "token_hash": {"$regex": "^" + re.escape(tid)}, "revoked": 0},
                                   fields=["token_hash"])
        if len(rows) != 1:
            raise Invalid("there is no such live credential")
        with self.db:
            self.db.tokens.set(rows[0]["token_hash"], {"revoked": 1})
            self.track.append(actor, "credential.revoked", member_ref(mid), data={"credential": tid[:12]})

    def _revoke_all(self, actor: dict, mid: str, why: str) -> None:
        n = self.db.tokens.update({"member": mid, "revoked": 0}, {"$set": {"revoked": 1}}, many=True)
        self.end_sessions(mid)  # and the pages' sign-in sessions (a passphrase stays, refused while switched off)
        if n:
            self.track.append(actor, "credential.revoked", member_ref(mid), data={"count": n, "reason": why})

    def authenticate(self, token: str, scope: str = "api") -> dict | None:
        """The active member a token is for, if it is live and for this use (an MCP token opens only MCP)."""
        got = self.credential(token)
        return got[0] if got and got[1].get("scope", "api") == scope else None

    def credential(self, token: str) -> tuple[dict, dict] | None:
        """(member, the credential's row) for a live token of an active member."""
        row = self.db.tokens.find_one({"_id": self._hash_token(token or ""), "revoked": 0})
        if not row:
            return None
        m = self.member(row["member"])
        return (m, {**row, "scope": row.get("scope", "api")}) if m["status"] == "active" else None

    # ------------------------------------------------------------------ records (permission-checked)
    def _type(self, rtype: str) -> schema.RecordType:
        rt = self.record_type(rtype)
        if rt is None:
            raise Invalid(f"{rtype} is not a record type of this practice")
        return rt

    def class_of(self, rtype: str) -> str:
        """The data class a record belongs to; a retired type's records keep theirs, so they can still be read."""
        rt = self.record_type_for_reading(rtype)
        if rt is None:
            raise Invalid(f"{rtype} is not a record type of this practice")
        return rt.data_class

    def class_or_none(self, rtype: str) -> str | None:
        """The data class of a record type, or None for a type this practice does not know."""
        rt = self.record_type_for_reading(rtype)
        return rt.data_class if rt else None

    def may_read(self, member: dict, rtype: str) -> bool:
        return self.can(member, "reader", f"data_class:{self.class_of(rtype)}")

    def may_write(self, member: dict, rtype: str) -> bool:
        return self.can(member, "writer", f"data_class:{self.class_of(rtype)}")

    def read_record(self, member: dict, ref: str, *, raw: bool = False) -> dict:
        """The record as the member may see it: without the fields of a data class they may not read (``raw``: the
        whole record, for what is computed over it and never shown, like a signature's hash)."""
        rtype, rid = ref.split("/", 1)
        self.require(member, "reader", f"data_class:{self.class_of(rtype)}", f"read {rtype} records")
        rec = self.records.read(rtype, rid)
        if not rec:
            raise NotFound(f"there is no {ref}")
        out = self.redact(member, rec)
        gone = visibility.withheld(out)
        self.note_view(member, ref, how="withheld" if gone and gone.get("whole") else "opened")
        return rec if raw else out

    # ------------------------------------------------------------------ restrictions (restrictions.py)
    def withheld(self, member: dict | None, subject: str) -> dict:
        """What restrictions on a subject's file withhold from the member, for a page to say so: ``{"note",
        "restrictions", "override" (their emergency access in force, or None), "may_override"}``; an empty dict
        when nothing in the file is restricted."""
        ds = self.restrictions.of_subject(subject)
        if not ds:
            return {}
        over = self.restrictions.override(member, subject)
        mine = [d["ref"] for d in ds if not self.restrictions._allowed(member, d)]
        return {"note": self.restrictions.note(subject), "restrictions": mine, "allowed": not mine,
                "override": over if mine else None, "may_override": bool(mine) and self.restrictions.may_override(member)}

    def break_glass(self, member: dict, ref: str, reason: str) -> dict:
        """Open, in an emergency and with a reason, what a restriction withholds of ``ref``'s file from the member:
        tracked as a view (``how = "override"``), reviewed by the privacy officer (``restrictions.py``)."""
        return self.restrictions.break_glass(member, ref, reason)

    def redact(self, member: dict | None, rec: dict | None) -> dict | None:
        """What the member may see of a record (visibility.redact): every record handed to a member goes through
        it, on every page, list, export, API and agent context."""
        return visibility.redact(self, member, rec)

    def redact_all(self, member: dict | None, recs) -> list[dict]:
        return [visibility.redact(self, member, r) for r in recs]

    def search_records(self, member: dict, rtype: str | None = None, *, text: str | None = None,
                       limit: int | None = 200, subject: str | None = None, field: str | None = None,
                       since: str | None = None, until: str | None = None, offset: int = 0) -> list[dict]:
        """Records as the member may see them (the store's search, then ``redact``), a page of them (``limit`` from
        ``offset``), counted in the database. A search by words matches only what the member may read of each
        record: the database looks for each word in the parts of the record whose data classes they read, so a
        hidden field's value never matches and nothing is read into the hub to be filtered. A range over a field
        they may not read is refused.

        The member must read the type's data class, as for :meth:`read_record` (a caller that forgets to check
        does not leak a class); across every type (no ``rtype``), records of types they may not read are left out.
        ``member`` None (a lookup that sees only what every reader does) and the hub's own automation are not
        checked."""
        checked = member is not None and not visibility._sees_all(member)
        if checked and rtype:
            dc = self.class_or_none(rtype)
            if dc:
                self.require(member, "reader", f"data_class:{dc}", f"read {rtype} records")
        if field and rtype and visibility.hides_path(self, member, rtype, field):
            raise Denied(f"{member.get('name', '')} may not search {rtype} records by {field}")
        if not text:
            recs = self.redact_all(member, self.records.search(rtype, subject=subject, limit=limit, field=field,
                                                               since=since, until=until, offset=offset))
            return self._readable_only(member, recs) if checked and not rtype else recs
        recs = self._search_words(member, rtype, text=text, limit=limit, subject=subject, field=field,
                                  since=since, until=until, offset=offset)
        if checked and not rtype:
            recs = self._readable_only(member, recs)
        if not self.restrictions.any():
            return recs
        # what a patient's directive withholds never matches: a record shown masked is matched against what is shown
        from .records import search_text, words_of
        words = words_of(text)
        return [r for r in recs if not visibility.withheld(r) or all(w in search_text(r) for w in words)]

    def _readable_only(self, member: dict, recs: list[dict]) -> list[dict]:
        seen: dict[str, bool] = {}

        def ok(r):
            t = r.get("resourceType") or ""
            if t not in seen:
                dc = self.class_or_none(t)
                seen[t] = not dc or self.can(member, "reader", f"data_class:{dc}")
            return seen[t]
        return [r for r in recs if ok(r)]

    def _search_words(self, member: dict, rtype: str | None, *, text: str, limit: int | None, subject: str | None,
                      field: str | None, since: str | None, until: str | None, offset: int) -> list[dict]:
        types = {rtype: self.record_type_for_reading(rtype)} if rtype else self.record_types()
        guarded = {t for t, rt in types.items() if visibility.hidden_fields(self, member, rt)}
        if guarded and hasattr(self.records, "reindex"):
            self._search_ready(guarded)
            classes = {f["data_class"] for rt in types.values() if rt is not None for f in rt.classed_fields}
            visible = {dc for dc in classes if member and self.can(member, "reader", f"data_class:{dc}")}
            plain = [t for t, rt in types.items() if rt is None or not rt.classed_fields]
            recs = self.records.search(rtype, subject=subject, limit=limit, text=text, field=field, since=since,
                                       until=until, offset=offset, visible=visible, plain=plain)
            return self.redact_all(member, recs)
        if guarded:  # a store that cannot look in parts (a FHIR server): match what the member sees, here
            from .records import search_text, words_of
            words = words_of(text)
            recs = self.redact_all(member, self.records.search(rtype, subject=subject, limit=None, text=text,
                                                               field=field, since=since, until=until))
            recs = [r for r in recs if all(w in search_text(r) for w in words)]
            return recs[offset:][:limit] if limit is not None else recs[offset:]
        if hasattr(self.records, "reindex"):
            self._search_ready(types)
            return self.redact_all(member, self.records.search(rtype, subject=subject, limit=limit, text=text,
                                                               field=field, since=since, until=until, offset=offset))
        return self.redact_all(member, self.records.search(rtype, subject=subject, limit=limit, text=text,
                                                           field=field, since=since, until=until))

    VIEW_WINDOW = timedelta(minutes=15)

    def note_view(self, member: dict, ref: str, *, how: str = "opened") -> None:
        """Write down that a member saw a record (PHIA: a record of who looked at personal health information).
        People, agents and services are recorded; the same member seeing the same record again within
        ``VIEW_WINDOW`` is one view. Workflow automations act on records, and the track records those actions."""
        if not ref or member.get("kind") not in ("person", "agent", "service"):
            return
        # one view per visit however the record was seen; reading one of its files is a view of its own
        last = self.db.events.find_one({"subject": ref, "action": "record.viewed", "actor": member["id"],
                                        "data.how": "file" if how == "file" else {"$ne": "file"}},
                                       sort=[("seq", DESC)])
        if last and last["at"] >= (datetime.now(timezone.utc) - self.VIEW_WINDOW).replace(microsecond=0).isoformat().replace("+00:00", "Z"):
            return
        self.track.append(member, "record.viewed", ref, data={"how": how})

    def note_list(self, member: dict | None, recs, *, how: str = "list", what: str = "",
                  text: str | None = None) -> int | None:
        """Write down that a member was shown a list of records (a list page, a search, a dashboard widget, an MCP
        search): one ``records.listed`` event naming every record shown that is about a subject (a patient's), so
        the access report for each of them names this list. A list is what its rows show (titles, a name and birth
        date); opening one is a ``record.viewed`` of its own, and an export records each record it holds. The same
        member shown the same records again within ``VIEW_WINDOW`` is one listing. Returns the event, if written."""
        if not member or member.get("kind") not in ("person", "agent", "service"):
            return None
        subjects = self.subject_types()
        refs = []
        for r in recs or []:
            if not isinstance(r, dict) or not r.get("id") or not r.get("resourceType"):
                continue
            ref = f"{r['resourceType']}/{r['id']}"
            if r["resourceType"] in subjects or self.subject_of(r):
                refs.append(ref)
        if not refs:
            return None
        refs = list(dict.fromkeys(refs))
        digest = hashlib.sha256("\n".join(sorted(refs)).encode()).hexdigest()
        since = (datetime.now(timezone.utc) - self.VIEW_WINDOW).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if self.db.events.find_one({"action": "records.listed", "actor": member["id"], "data.refs_sha256": digest,
                                    "data.how": how, "at": {"$gte": since}}, fields=["seq"]):
            return None
        return self.track.append(member, "records.listed", what or "records",
                                 data={"how": how, "count": len(refs), "refs": refs, "refs_sha256": digest,
                                       **({"search": text} if text else {})})

    # ------------------------------------------------------------------ files (attachments), in GridFS
    MAX_FILE = 25 * 1024 * 1024
    _CTYPE = re.compile(r"[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*")

    def add_file(self, member: dict, rtype: str, data: bytes, name: str = "", content_type: str = "") -> dict:
        """Keep a file for a record of ``rtype`` and return the attachment that names it, for the record's
        attachment field: ``{blob: <sha256>, name, contentType, size}``. Whoever may write that type's records
        may add one. Files are content-addressed: the same bytes added twice are one file."""
        rt = self._type(rtype)
        self.require(member, "writer", f"data_class:{rt.data_class}", f"attach files to {rtype} records")
        if not data:
            raise Invalid("the file is empty")
        if len(data) > self.MAX_FILE:
            raise Invalid(f"a file is at most {self.MAX_FILE // (1024 * 1024)} MB")
        name = re.sub(r"[\x00-\x1f\x7f]", "", (name or "").replace("\\", "/").rsplit("/", 1)[-1]).strip()[:200]
        ctype = (content_type or "").split(";", 1)[0].strip().lower()
        ctype = ctype if self._CTYPE.fullmatch(ctype) else "application/octet-stream"
        sha = hashlib.sha256(data).hexdigest()
        self.db.put_file(sha, data, {"size": len(data), "contentType": ctype, "data_class": rt.data_class,
                                     "added_by": member["id"], "at": utcnow()})
        with self.db:
            self.track.append(member, "file.added", f"file:{sha}",
                              data={"type": rtype, "size": len(data), "contentType": ctype})
        return {"blob": sha, "name": name or f"{sha[:12]}", "contentType": ctype, "size": len(data)}

    def read_file(self, member: dict, ref: str, blob: str) -> tuple[bytes, dict]:
        """A file a record names (now or in an earlier version), for a member who may read that record. Reading it
        is a view of the record, on the track like opening it."""
        rtype, _, rid = ref.partition("/")
        self.require(member, "reader", f"data_class:{self.class_of(rtype)}", f"read {rtype} records")
        if not _SHA256.fullmatch(blob or ""):
            raise NotFound("there is no such file")
        named = None
        masked = self.restrictions.mask(member, self.records.read(rtype, rid))
        for v in self.db.record_versions.find({"type": rtype, "id": rid}, sort=[("version", DESC)], fields=["data"]):
            # a file in what a restriction withholds from them is withheld too
            seen = self.restrictions.apply(member, v["data"]) if masked else v["data"]
            named = next((a for a in _files_in(seen) if a["blob"] == blob), None)
            if named:
                break
        got = self.db.get_file(blob) if named else None
        if got is None:
            raise NotFound(f"{ref} has no such file")
        data, meta = got
        self.note_view(member, ref, how="file")
        return data, {"name": named.get("name") or blob[:12], "contentType": meta.get("contentType")
                      or "application/octet-stream", "size": len(data)}

    def views_of(self, member: dict, ref: str, limit: int = 200) -> list[dict]:
        """Who saw a record, newest first. A policy owner (the privacy officer) reads it."""
        self.require(member, "policy_owner", "practice:main", "see who viewed a record")
        return [{k: r[k] for k in ("actor", "actor_kind", "at", "data")}
                for r in self.db.events.find({"subject": ref, "action": "record.viewed"}, sort=[("seq", DESC)],
                                             limit=limit)]

    ACCESS_ACTIONS = ("record.viewed", "records.exported", "record.created", "record.updated", "record.transitioned",
                      "record.signed")
    LISTED = "records.listed"  # a list or search that showed the record among others: found by data.refs

    def access_report(self, member: dict, subject: str) -> list[dict]:
        """Everyone (people, agents, services) who saw or changed anything in a subject's file, oldest first: what
        a custodian gives a patient who asks who looked at their information. A policy owner (the privacy officer)
        runs it, across every data class: it names records and who touched them, not what they say."""
        self.require(member, "policy_owner", "practice:main", "report who accessed a file")
        if "/" not in (subject or ""):
            raise Invalid("name the subject as Type/id")
        refs = {subject} | {f"{r['resourceType']}/{r['id']}" for r in self.records.search(subject=subject, limit=None)}
        # and every record that was ever about the subject, even if it is about someone else now
        refs |= {f"{r['type']}/{r['id']}" for r in self.db.record_versions.find({"refs": subject},
                                                                                fields=["type", "id"])}
        ordered = sorted(refs)
        rows = self.db.events.find({"$or": [{"subject": {"$in": ordered}, "action": {"$in": list(self.ACCESS_ACTIONS)}},
                                            {"action": self.LISTED, "data.refs": {"$in": ordered}}]},
                                   sort=[("seq", ASC)], fields=["seq", "at", "actor", "actor_kind", "action", "subject",
                                                                "data.how", "data.format", "data.refs", "data.search"])
        names = {m["id"]: m["name"] for m in self.db.members.find({}, fields=["id", "name"])}
        self.track.append(member, "access.reported", subject, data={"events": len(rows)})

        def record_of(r):
            if r["action"] != self.LISTED:
                return r["subject"]
            shown = [x for x in (r.get("data") or {}).get("refs", []) if x in refs]
            return subject if subject in shown else (shown[0] if shown else r["subject"])

        def how_of(r):
            d = r.get("data") or {}
            if r["action"] == self.LISTED:
                return f"in a list ({d.get('how', 'list')}" + (f", search {d['search']!r}" if d.get("search") else "") + ")"
            return _how(d)
        return [{"seq": r["seq"], "at": r["at"], "who": r["actor"], "name": names.get(r["actor"], r["actor"]),
                 "kind": r["actor_kind"], "action": r["action"].split(".", 1)[1], "record": record_of(r),
                 "how": how_of(r)} for r in rows]

    def record_history(self, member: dict, ref: str) -> list[dict]:
        rtype, rid = ref.split("/", 1)
        self.require(member, "reader", f"data_class:{self.class_of(rtype)}", f"read {rtype} records")
        self.note_view(member, ref, how="history")
        rows = self.db.record_versions.find({"type": rtype, "id": rid}, sort=[("version", DESC)])
        return [{**{k: v for k, v in r.items() if k not in ("_id", "refs")}, "data": self.redact(member, r["data"])}
                for r in rows]

    def revision_authors(self, ref: str) -> set[str]:
        """Who wrote what the record says now: whoever created it or changed a field other than its lifecycle state,
        since it was last in its lifecycle's ``in_use`` state (the approved revision others reviewed). Moving a
        record through its lifecycle does not make someone its author."""
        rtype, rid = ref.split("/", 1)
        rt = self.record_type(rtype)
        lc = rt.lifecycle if rt else None
        rows = self.db.record_versions.find({"type": rtype, "id": rid}, sort=[("version", ASC)])
        authors: set[str] = set()
        prev = None
        for r in rows:
            d = r["data"]
            state = schema.first(d, lc["path"]) if lc else None
            if lc and lc.get("in_use") and state == lc["in_use"] and r is not rows[-1]:
                authors, prev = set(), d  # an approved revision: later edits start a new one
                continue
            strip = (lambda x: {k: v for k, v in x.items() if not (lc and k == lc["path"].split(".")[0])
                                and k not in ("meta", "revision")})
            if prev is None or strip(d) != strip(prev):
                authors.update(r.get("authors") or [r["actor"]])  # a version several people wrote together names them
            prev = d
        return authors

    def in_use_version(self, member: dict, ref: str) -> dict | None:
        """While a record is being revised, the last version of it in its lifecycle's ``in_use`` state (the approved
        document staff keep working from). None when the record is in that state now, or never was."""
        rt = self.record_type(ref.split("/", 1)[0])
        lc = rt.lifecycle if rt else None
        if not lc or not lc.get("in_use"):
            return None
        rec = self.read_record(member, ref)
        if schema.first(rec, lc["path"]) not in lc.get("revising", []):
            return None  # in use now, or withdrawn (obsolete): nothing older is to be followed
        for v in self.record_history(member, ref):
            if schema.first(v["data"], lc["path"]) == lc["in_use"]:
                return v
        return None

    def linked(self, member: dict, ref: str) -> list[dict]:
        """Records that point at this one, that the member may read: (rel, ref)."""
        out = []
        for r in self.db.links.find({"to_ref": ref}, sort=[("from_ref", ASC), ("rel", ASC)]):
            t = r["from_ref"].split("/")[0]
            rt = self.record_type(t)
            if rt and self.can(member, "reader", f"data_class:{rt.data_class}"):
                out.append({"rel": r["rel"], "ref": r["from_ref"]})
        return out

    # ------------------------------------------------------------------ formatted text and trees of records
    def mention_ref(self, rt: schema.RecordType, target: str) -> str | None:
        """The record a ``[[target]]`` in a record of ``rt`` points at: ``Type/id`` as written, or the record of the
        same type titled ``target`` (case and accents aside). None when there is none: a link not yet made."""
        from .records import fold
        if target.startswith("work:"):
            return None
        if schema.REF_TEXT.fullmatch(target):
            t, _, rid = target.partition("/")
            return target if self.record_type(t) and self.records.read(t, rid) else None
        key = fold(target).strip()
        self._index_titles(rt)
        row = self.db.titles.find_one({"type": rt.id, "key": key}, sort=[("_id", 1)]) if key else None
        return row["_id"] if row else None

    def _index_titles(self, rt: schema.RecordType, skip: str | None = None) -> None:
        """Fill the title index, and the titles each record names, for a type whose records were written before it
        had one (once per type). ``skip``: the record being written now, which its own write indexes."""
        from .records import fold
        if self.db.get_setting(f"titles.indexed.{rt.id}"):
            return
        self.db.set_setting(f"titles.indexed.{rt.id}", True)
        for r in self.records.search(rt.id, limit=None):
            ref = f"{rt.id}/{r['id']}"
            if ref == skip:
                continue
            self.db.titles.replace({"_id": ref, "type": rt.id, "key": fold(self.title(r)).strip()})
            for key in {fold(t).strip() for t in self.mention_targets(rt, r)
                        if not t.startswith("work:") and not schema.REF_TEXT.fullmatch(t)} - {""}:
                self.db.mention_names.replace({"_id": f"{ref}|{key}", "from_ref": ref, "type": rt.id, "key": key})

    def mention_links(self, rt: schema.RecordType, rec: dict) -> list[tuple[str, str]]:
        """("mentions", Type/id) for every ``[[...]]`` in the record's formatted text that names a record."""
        out = set()
        for target in self.mention_targets(rt, rec):
            ref = self.mention_ref(rt, target)
            if ref and ref != f"{rt.id}/{rec.get('id')}":
                out.add(("mentions", ref))
        return sorted(out)

    @staticmethod
    def mention_targets(rt: schema.RecordType, rec: dict) -> list[str]:
        return [t for f in rt.fields if f["type"] == "markdown" for v in schema.get(rec, f["path"])
                if isinstance(v, str) for t, _ in markup.mentions(v)]

    def parent_of(self, rt: schema.RecordType, rec: dict) -> str | None:
        return schema.ref_of(schema.first(rec, rt.parent)) if rt.parent else None

    def tree_types(self, rtype: str) -> list[str]:
        """The types that share a tree with this one: it, then each type whose parent may be one of this type and
        which this type may sit under (a wiki's pages and its procedures). One tree, one navigator."""
        rt = self.record_type(rtype)
        if not rt or not rt.parent:
            return [rtype] if rt else []
        mine = set(rt.field(rt.parent)["target"])
        out = [rtype]
        for tid, other in sorted(self.record_types().items()):
            if tid != rtype and other.parent and tid in mine and rtype in other.field(other.parent)["target"]:
                out.append(tid)
        return out

    def _rt_of(self, rec: dict):
        return self.record_type(rec.get("resourceType", ""))

    def ancestors(self, member: dict, ref: str) -> list[dict]:
        """The records above this one in its tree, top first, that the member may read (``resourceType`` says which
        type each is)."""
        t, _, rid = ref.partition("/")
        rt = self.record_type(t)
        out, seen = [], {ref}
        rec = self.records.read(t, rid) if rt and rt.parent else None
        while rec:
            rrt = self._rt_of(rec)
            up = self.parent_of(rrt, rec) if rrt else None
            if not up or up in seen or len(out) > 50:
                break
            seen.add(up)
            rec = self.records.read(*up.split("/", 1))
            if rec and self.may_read(member, rec["resourceType"]):
                out.append(self.redact(member, rec))
            elif rec is None or not self.may_read(member, up.split("/", 1)[0]):
                break
        return list(reversed(out)) if self.may_read(member, t) else []

    def children(self, member: dict, ref: str) -> list[dict]:
        """The records directly under this one in its tree, by title, of any type in the tree that the member may
        read."""
        t = ref.split("/", 1)[0]
        rt = self.record_type(t)
        if not rt or not rt.parent or not self.may_read(member, t):
            return []
        kids = []
        for kt in self.tree_types(t):
            krt = self.record_type(kt)
            if not self.may_read(member, kt):
                continue
            rel = krt.field(krt.parent)["rel"] or krt.parent
            kids += [self.records.read(*r["from_ref"].split("/", 1)) for r in self.db.links.find(
                {"to_ref": ref, "rel": rel, "from_ref": {"$regex": f"^{re.escape(kt)}/"}})]
        return sorted((self.redact(member, k) for k in kids if k), key=lambda r: self.title(r).casefold())

    # ------------------------------------------------------------------ parts and links
    def part_types(self, rtype: str) -> list[schema.RecordType]:
        """The types whose records are parts of a record of ``rtype`` (``part_of`` naming it), by ``order``."""
        out = [rt for rt in self.record_types().values()
               if rt.part_of and not rt.abstract and rtype in self.concrete(rt.part_of["target"])]
        return sorted(out, key=lambda rt: (rt.order, rt.id))

    def owner_of(self, rec: dict) -> str | None:
        """The record that owns a part (``Type/id``), or None when its type is not part of another."""
        rt = self.record_type(rec.get("resourceType") or "")
        if not rt or not rt.part_of:
            return None
        return schema.ref_of(schema.first(rec, rt.part_of["path"]))

    def parts(self, member: dict, ref: str) -> tuple[dict[str, list[dict]], int]:
        """A record's parts, per part type (every part type of its type, even with none yet), newest first, that
        the member may read; and how many it may not."""
        out, hidden = {}, 0
        for rt in self.part_types(ref.split("/", 1)[0]):
            f = rt.field(rt.part_of["field"])
            rows = self.db.links.find({"to_ref": ref, "rel": f["rel"] if f else rt.part_of["field"],
                                       "from_ref": {"$regex": f"^{re.escape(rt.id)}/"}})
            if not self.may_read(member, rt.id):
                hidden += len(rows)
                continue
            recs = [r for r in (self.records.read(*x["from_ref"].split("/", 1)) for x in rows)
                    if r and schema.ref_of(schema.first(r, rt.part_of["path"])) == ref]
            shown = self.redact_all(member, recs)
            for r in shown:
                if not visibility.withheld(r) or not visibility.withheld(r).get("whole"):
                    self.note_view(member, f"{rt.id}/{r['id']}", how=f"part of {ref}")
            out[rt.id] = sorted(shown, key=lambda r: schema.record_date(rt, r), reverse=True)
        return out, hidden

    def tree(self, member: dict, rtype: str, where=None) -> list[tuple[int, dict]]:
        """Every record in a tree, as (depth, record) in reading order: each under its parent, each level by title.
        A record whose parent is missing or filtered out sits at the top. The tree holds every type that shares
        it (``tree_types``) that the member may read; ``resourceType`` says which each is."""
        rt = self.record_type(rtype)
        if not rt or not self.may_read(member, rtype):
            return []
        recs = [r for t in self.tree_types(rtype) if self.may_read(member, t)
                for r in self.redact_all(member, self.records.search(t, limit=None)) if where is None or where(r)]
        ids = {f"{r['resourceType']}/{r['id']}" for r in recs}
        under: dict = {}
        for r in recs:
            rrt = self._rt_of(r)
            up = self.parent_of(rrt, r) if rrt and rrt.parent else None
            under.setdefault(up if up in ids else None, []).append(r)
        out, seen = [], set()

        def walk(key, depth):
            for r in sorted(under.get(key, []), key=lambda x: self.title(x).casefold()):
                ref = f"{r['resourceType']}/{r['id']}"
                if ref in seen:
                    continue
                seen.add(ref)
                out.append((depth, r))
                walk(ref, depth + 1)
        walk(None, 0)
        for r in recs:  # a loop no write lets in, but never hidden if one is there
            if f"{r['resourceType']}/{r['id']}" not in seen:
                out.append((0, r))
        return out

    def navigator(self, member: dict, rtype: str, ref: str | None = None) -> dict | None:
        """A tree's second navigation column, for the member: its tops (spaces), and the tree of the top the open
        record is in. Records in their type's hidden states are left out, but the open one and those above it
        never. Opened from any type in the tree, it is the tree of the first type in it that has a navigator."""
        rt = self.record_type(rtype)
        if rt and not rt.navigator:
            rtype = next((t for t in self.tree_types(rtype) if self.record_type(t).navigator), rtype)
            rt = self.record_type(rtype)
        if not rt or not rt.navigator or not self.may_read(member, rtype):
            return None
        refof = lambda r: f"{r['resourceType']}/{r['id']}"
        above = self.ancestors(member, ref) if ref else []
        keep = {ref} | {refof(a) for a in above}

        def shown(r):
            if refof(r) in keep:
                return True
            trt = self._rt_of(r)
            if not trt or not trt.lifecycle:
                return True
            hide = set((trt.navigator or rt.navigator)["hide"])
            return schema.first(r, trt.lifecycle["path"]) not in hide
        rows = self.tree(member, rtype, where=shown)
        tops = [r for d, r in rows if d == 0]
        top = next((refof(a) for a in above), ref) if ref else None
        tree, inside = [], False
        for d, r in rows:
            if d == 0:
                inside = refof(r) == top
            if inside and d > 0:
                tree.append((d - 1, r))
        return {"label": rt.navigator["tops"], "type": rtype, "tops": tops, "top": top, "tree": tree, "open": keep}

    def _member_ids(self) -> set[str]:
        return set(self.db.members.distinct("_id"))

    def _role_problems(self, rt: schema.RecordType, rec: dict, before: dict | None) -> list[str]:
        """A field with ``roles`` names only members holding one of them. Checked for the members a write adds: a
        record kept from before (someone who has since left the role) still saves."""
        out, members = [], self.member_types()
        for f in rt.fields:
            if f["type"] != "reference" or not f.get("roles"):
                continue
            had = {schema.ref_of(v) for v in schema.get(before, f["path"])} if before else set()
            for v in schema.get(rec, f["path"]):
                ref = schema.ref_of(v)
                t, _, mid = (ref or "").partition("/")
                if not ref or ref in had or t not in members:
                    continue
                if not set(f["roles"]) & set(self.roles_of(mid)):
                    names = [self.role(r)["name"] for r in f["roles"] if self._role_known(r)] or f["roles"]
                    out.append(f"{f['label']}: only someone who is a {' or '.join(names).lower()} may be chosen")
        return out

    def _check_record(self, rt: schema.RecordType, rec: dict, before: dict | None = None) -> None:
        problems = schema.validate(rt, rec, members=self._member_ids(), is_a=self.is_a)
        problems += self._role_problems(rt, rec, before) if not problems else []
        for a in _files_in(rec):  # a file is added first (``add_file``); a record names only one the hub has
            if self.db.file_meta(a["blob"]) is None:
                problems.append(f"{rt.label}: no file {a['blob'][:12]}… was added to this hub")
        for rel, to in schema.links(rt, rec) if not problems else []:  # a reference points at a record that exists
            t, _, tid = to.partition("/")
            if self.record_type(t) and not self.records.read(t, tid):
                problems.append(f"{rt.label}: {rel} points at {to}, which does not exist")
        up = self.parent_of(rt, rec) if rt.parent and not problems else None
        me, hops = f"{rt.id}/{rec.get('id')}", 0
        while up and hops < 200:  # a record never sits under itself, however far down
            if up == me:
                problems.append(f"{rt.label}: it cannot sit under itself or under a {rt.label.lower()} below it")
                break
            above = self.records.read(*up.split("/", 1))
            up, hops = (self.parent_of(rt, above) if above else None), hops + 1
        if rt.is_a("Restriction") and not problems:
            problems += self.restrictions.problems(rt, rec)
        if problems:
            raise Invalid(f"the {rt.label.lower()} has problems", problems)

    # ------------------------------------------------------------------ the calendar: no member booked twice
    def may_overbook(self, member: dict, rt: schema.RecordType) -> bool:
        """Whether this member may book over another booking (an urgent squeeze-in): a person holding a role the
        booking type's ``[booking] overbook`` names. Nobody, when it names none."""
        roles = set((rt.booking or {}).get("overbook") or [])
        return bool(roles) and member.get("kind") == "person" and member.get("status", "active") == "active" \
            and bool(roles & set(self.roles_of(member["id"])))

    def _check_clashes(self, rt: schema.RecordType, rec: dict, member: dict, overbook: str | None) -> list:
        """The clashes this write may make: none, unless the member may book over another booking and says why
        (``overbook``). Called inside the write's transaction, so two people booking the same time are not both told
        it is free."""
        clashes = self.booking_clashes(rt, rec)
        if not clashes:
            return []
        texts = [self._clash_text(c, who) for c, who in clashes]
        if overbook and overbook.strip():
            if not self.may_overbook(member, rt):
                roles = [self.role(r)["name"] for r in (rt.booking or {}).get("overbook") or [] if self._role_known(r)]
                raise Denied(f"{member.get('name') or member.get('id')} may not book over another booking"
                             + (f" (only: {', '.join(roles)})" if roles else "") + ": " + "; ".join(texts))
            return clashes
        hint = (rt.booking or {}).get("overbook_label") if self.may_overbook(member, rt) else ""
        raise Invalid(f"the {rt.label.lower()} clashes with another booking"
                      + (f": to book it anyway, choose {hint!r} and say why" if hint else ""), texts)

    def _role_known(self, rid: str) -> bool:
        return self.db.roles.exists({"_id": rid})

    def _overbooked(self, rt: schema.RecordType, rec: dict, reason: str) -> dict:
        """The booking with why it was booked over another, and the priority the type gives such a booking."""
        b = rt.booking or {}
        values = {"overbooked": reason.strip()[:500]}
        if b.get("overbook_priority") and "priority" in rt.base_map:
            values["priority"] = b["overbook_priority"]
        return schema.compose(rt, values, self.member_ref_type(), rec)

    def _note_overbook(self, member: dict, rt: schema.RecordType, ref: str, clashes: list, reason: str, *,
                       cause: int | None, run_id: str | None) -> int:
        """On the track: who booked whom over which bookings, and why (shown to those who may read the reason)."""
        f = rt.field(((rt.base_map.get("overbooked") or {}).get("fields") or [""])[0])
        return self.track.append(member, "booking.overbooked", ref, cause=cause, run_id=run_id, data={
            "over": [f"{b['type']}/{b['record']['id']}" for b, _ in clashes],
            "with": sorted({who for _, who in clashes}), "reason": reason.strip()[:500],
            "classes": [rt.field_class(f)] if f else [rt.data_class]})

    def _held_time(self, rt: schema.RecordType, rec: dict) -> tuple:
        """What of a booking decides whom it holds and when: its time, its members, whether it still holds them."""
        seen = schema.project(rt, rec, self.member_types())
        return (seen.get("start"), seen.get("end"), sorted(seen.get("with") or []), bool(seen.get("cancelled")),
                schema.is_active(rt, rec))

    @staticmethod
    def booking_span(seen: dict, slot_minutes: int = 15) -> tuple[datetime, datetime] | None:
        """When a booking (as ``schema.project`` reads it through ``Booking``) holds its members: start to end, or one
        slot when it has no end. None when its start is not a time."""
        try:
            start = datetime.fromisoformat(str(seen.get("start") or "")[:16])
        except ValueError:
            return None
        try:
            end = datetime.fromisoformat(str(seen.get("end") or "")[:16])
        except ValueError:
            end = None
        if end is None or end <= start:
            end = start + timedelta(minutes=slot_minutes)
        return start, end

    def slot_minutes(self) -> int:
        return int(((self.pack.calendar if self.pack else None) or {}).get("slot_minutes", 15))

    def booking_clashes(self, rt: schema.RecordType, rec: dict) -> list[tuple[dict, str]]:
        """The other bookings that hold a member this one holds at an overlapping time, as ``(booking, member id)``.
        Only bookings that still hold time count: not cancelled, and active by their type (a finished visit filed
        afterwards clashes with nothing). Records carried over by a pack upgrade are taken as they were."""
        if not rt.is_a("Booking") or getattr(self, "_upgrading", False):
            return []
        seen = schema.project(rt, rec, self.member_types())
        if seen.get("cancelled") or not seen.get("with") or not schema.is_active(rt, rec):
            return []
        span = self.booking_span(seen, self.slot_minutes())
        if span is None:
            return []
        from .engine import bookings
        out = []
        for b in bookings(self, since=(span[0] - timedelta(days=1)).isoformat(timespec="minutes"),
                          until=span[1].isoformat(timespec="minutes")):
            if b["type"] == rt.id and b["record"].get("id") == rec.get("id"):
                continue
            if not schema.is_active(self.record_type(b["type"]), b["record"]):
                continue
            other = self.booking_span(b, self.slot_minutes())
            if other is None or not (other[0] < span[1] and span[0] < other[1]):
                continue
            for who in seen["with"]:
                if who in b["with"]:
                    out.append((b, who))
                    break
        return out

    def _clash_text(self, b: dict, who: str) -> str:
        """Which member is booked when: never whom for (the person booking may not read the other booking)."""
        try:
            name = self.member(who)["name"]
        except Exception:  # noqa: BLE001 - a member since removed is still named by id
            name = who
        start, end = self.booking_span(b, self.slot_minutes())
        return (f"{name} is already booked on {start.strftime('%a %b %d')} from {start.strftime('%H:%M')} to "
                f"{end.strftime('%H:%M')}: choose another time or another member")

    def _workflow_fields(self, rt, member: dict, run_id: str | None, before: dict, after: dict) -> None:
        """A field marked ``by = "workflow"`` (stock on hand) changes only in a workflow step: a person who could set it
        directly would make its history, the moves that explain it, untrue."""
        if self._by_workflow(member, run_id):
            return
        for f in rt.fields:
            if f.get("by") == "workflow" and schema.get(before, f["path"]) != schema.get(after, f["path"]):
                raise Denied(f"{f['label']} changes only through a workflow (it is not edited by hand)")

    def state_move_refusal(self, rt, ref: str, t: dict, after: dict, run_id: str | None) -> str | None:
        """Why a move of a record whose type names its workflow is refused, or None. That workflow alone moves its
        state: its own run, or a person (an import, the portal, any other path) only through a move the run is
        waiting on now (a wait whose ``until`` holds of the record as moved, or a state the open step lists in
        ``moves``). Another workflow never moves it. A correction open from every state (``from = ["*"]``, entered
        in error) stays anyone's who may write the record."""
        owner = (rt.workflow or {}).get("id")
        if not owner or "*" in t["from"]:
            return None
        label = rt.label.lower()
        if run_id:
            run = self.db.runs.get(run_id)
            if run and run["workflow_id"] == owner and run.get("record") == ref:
                return None
            if run and not self.state_move_waited_on(rt, ref, after, t["to"]):
                # another workflow (a CAPA revising the documents it names) moves it only through a move the owner's
                # open step waits on, as a person would
                return f"a {label}'s {rt.lifecycle.get('field')} is moved by its own workflow, not by another"
            if run:
                return None
        if self.state_move_waited_on(rt, ref, after, t["to"]):
            return None
        if not self.db.runs.find_one({"workflow_id": owner, "record": ref, "status": "running"}, fields=["id"]):
            return None  # its workflow is not walking it (filed before the type named one, or its run is over)
        return (f"a {label} is moved by its workflow; it is not waiting for "
                f"{rt.state_label(t['to']).lower()} now")

    def state_move_waited_on(self, rt, ref: str, after: dict, new: str) -> str | None:
        """Whether the open run of the type's workflow about ``ref`` is waiting on the record moving to ``new``: how
        ("until", a wait that then goes on; "moves", a state the open step lets people move it to), or None."""
        from . import expr
        owner = (rt.workflow or {}).get("id")
        field = (rt.lifecycle or {}).get("field")
        for run in self.db.runs.find({"workflow_id": owner, "record": ref, "status": "running"}):
            try:
                wf = self.workflow(run["workflow_id"], run["version"])[0]
            except NotFound:
                continue
            for s in wf.steps:
                st = (run["steps"].get(s.id) or {}).get("state")
                if st not in ("active", "waiting"):
                    continue
                if s.kind == "wait" and s.until and field and not s.for_each:
                    moved = {**after, field: new}
                    try:
                        if expr.holds(s.until, {**run["ctx"], "record": moved}):
                            return "until"
                    except Exception:  # noqa: BLE001 - a wait that cannot be read waits on nothing
                        pass
                if new in s.moves:
                    return "moves"
        return None

    def record_workflow(self, rt) -> str | None:
        """The workflow walking records of ``rt`` now: the one its type names, once that workflow's active version is
        about ``rt`` and does not start itself on ``rt``'s record events. Until then (a module upgrade whose new
        type is approved before its new workflow) the older version goes on starting itself, and nothing else
        starts it."""
        wid = (rt.workflow or {}).get("id")
        if not wid:
            return None
        try:
            wf = self.workflow(wid)[0]
        except NotFound:
            return None
        event = wf.event or ""
        if wf.record != rt.id or (event.startswith("record.") and event.endswith(f":{rt.id}")):
            return None
        return wid

    def _owner_follows_move(self, rt, ref: str, rec: dict, member: dict, run_id: str | None, t: dict,
                            cause: int) -> None:
        """The record moved: its own workflow's open run follows, in this same transaction, so the record's state and
        the run's step change together or not at all (never the savepoint that lets a run that cannot go on fail
        alone). A move its run made itself needs nothing more. Otherwise (a person, another workflow's run):

        - a correction open from every state (entered in error), or a move to a state that only a correction leaves
          (cancelled), that no wait of the run was waiting for, stops the open run (``run.stopped``): nothing is
          left to do (a run that waits for it, a visit's no-show, goes on to its own ending);
        - else the run takes the step the move finished (a wait's ``until``);
        - and when no run of its workflow is walking it now but the type's ``when`` holds of where it is (a record
          sent back and revised, or one filed before its type named a workflow), its workflow starts about it again:
          every pass of a lifecycle that loops is walked."""
        owner = rt.workflow["id"]
        if run_id:
            run = self.db.runs.get(run_id)
            if run and run["workflow_id"] == owner and run.get("record") == ref:
                return
        how = self.state_move_waited_on(rt, ref, rec, t["to"])
        closing = not any(t["to"] in x["from"] for x in rt.lifecycle["transition"] if "*" not in x["from"])
        runs = [r["id"] for r in self.db.runs.find({"workflow_id": owner, "record": ref, "status": "running"},
                                                   fields=["id"])]
        if runs and how != "until" and ("*" in t["from"] or closing):
            why = f"moved to {rt.state_label(t['to']).lower()}" + (" (a correction)" if "*" in t["from"] else "")
            for r in runs:
                self.engine.stop(member, r, why, cause)
            return
        for r in runs:
            self.engine.advance(r, cause)
        if getattr(self, "_upgrading", False) or self.record_workflow(rt) != owner \
                or schema.starts_workflow(rt, rec) != owner \
                or self.db.runs.find_one({"workflow_id": owner, "record": ref, "status": "running"}, fields=["id"]):
            return
        starter = member if member.get("kind") in ("person", "agent", "service") else \
            {"id": f"type:{rt.id}", "kind": "automation", "name": rt.label, "status": "active"}
        last = self.db.runs.find_one({"workflow_id": owner, "record": ref}, sort=[("created_at", DESC)],
                                     fields=["id"])
        self.engine.start(starter, owner, record=ref, cause=cause, via_automation=True, on_create=True,
                          previous=last["id"] if last else None)

    def _by_workflow(self, member: dict, run_id: str | None) -> bool:
        return member.get("kind") == "automation" or run_id is not None

    def _write_version(self, rt: schema.RecordType, rec: dict, member: dict, run_id: str | None, *,
                       comment: str | None = None, authors: list[str] | None = None) -> None:
        """A version of the record. ``comment``: what the change was, in words; ``authors``: everyone who wrote it,
        when several people wrote it together (a live editing session), besides the member who saved it."""
        ref = f"{rt.id}/{rec['id']}"
        version = (self.db.record_versions.max("version", {"type": rt.id, "id": rec["id"]}) or 0) + 1
        row = {"_id": f"{ref}@{version}", "type": rt.id, "id": rec["id"], "version": version, "data": rec,
               "refs": _refs_in(rec), "actor": member.get("id", ""), "run_id": run_id, "at": utcnow()}
        if comment and comment.strip():
            row["comment"] = comment.strip()[:500]
        if authors:
            row["authors"] = list(dict.fromkeys(authors))
        self.db.record_versions.insert(row)
        if any(f["type"] == "markdown" for f in rt.fields):
            self._retitle(rt, rec)
        self._relink(rt, rec)

    def _retitle(self, rt: schema.RecordType, rec: dict) -> None:
        """Keep the title index; when a record gets a new title (or is created), the records that name the old title
        or the new one link afresh."""
        from .records import fold
        ref = f"{rt.id}/{rec['id']}"
        self._index_titles(rt, skip=ref)
        key = fold(self.title(rec)).strip()
        row = self.db.titles.get(ref)
        before = row["key"] if row else None
        if before == key:
            return
        self.db.titles.replace({"_id": ref, "type": rt.id, "key": key})
        names = [k for k in (before, key) if k]
        for m in self.db.mention_names.find({"type": rt.id, "key": {"$in": names}}):
            if m["from_ref"] != ref:
                other = self.records.read(rt.id, m["from_ref"].split("/", 1)[1])
                if other:
                    self._relink(rt, other)

    def _relink(self, rt: schema.RecordType, rec: dict) -> None:
        """The record's links, from what it holds now: its references, and the ``[[...]]`` in its formatted text.
        The titles it names are kept too, so that a record given one of those titles later links back."""
        from .records import fold
        ref = f"{rt.id}/{rec['id']}"
        self.db.links.delete({"from_ref": ref})
        for rel, to in sorted(set(schema.links(rt, rec)) | set(self.mention_links(rt, rec))):
            self.db.links.insert({"_id": f"{ref}|{rel}|{to}", "from_ref": ref, "rel": rel, "to_ref": to})
        self.db.mention_names.delete({"from_ref": ref})
        for key in sorted({fold(t).strip() for t in self.mention_targets(rt, rec)
                           if not t.startswith("work:") and not schema.REF_TEXT.fullmatch(t)} - {""}):
            self.db.mention_names.insert({"_id": f"{ref}|{key}", "from_ref": ref, "type": rt.id, "key": key})

    def create_record(self, member: dict, rtype: str, data: dict, *, cause: int | None = None,
                      run_id: str | None = None, overbook: str | None = None, start_workflow: bool = True,
                      inputs: dict | None = None) -> dict:
        """``overbook``: for a booking, why it is booked over another one (a member the type lets do so); it is kept
        on the booking and on the track.

        A type that names its ``workflow`` has that workflow start about the new record, in the same step (whoever
        creates it, however: a form, the calendar, the API, another workflow), with ``inputs`` as its start form;
        its record's lifecycle state is then where that work is. ``start_workflow=False``: the run making it is that
        workflow's own."""
        rt = self._type(rtype)
        if member.get("kind") != "automation":
            self.require(member, "writer", f"data_class:{rt.data_class}", f"write {rtype} records")
        data = dict(data)
        lc = rt.lifecycle
        if lc:
            st = schema.first(data, lc["path"])
            if st is None and lc.get("initial"):
                data = schema.put(data, lc["path"], lc["initial"])
            elif st is not None and lc.get("initial") and st != lc["initial"] and member.get("kind") != "automation":
                # people start a record where its lifecycle starts; an automation (a workflow step, an import, a
                # module's demo) may write one in any of its states
                t = rt.transition(None, st)
                if t is None:
                    raise Invalid(f"a new {rt.label.lower()} starts as {lc['initial']!r}, not {st!r}")
                if t["by"] == "workflow" and not self._by_workflow(member, run_id):
                    raise Denied(f"only a workflow makes a {rt.label.lower()} {st!r}")
        self._workflow_fields(rt, member, run_id, {}, data)
        data = visibility.guard_write(self, member, rt, None, data)  # a field of a class they may not write
        over = overbook.strip() if overbook and overbook.strip() and rt.is_a("Booking") else None
        if over and self.may_overbook(member, rt):
            data = self._overbooked(rt, data, over)
        self._check_record(rt, {"resourceType": rtype, **data})
        with self.db:  # the clash check and the write are one step: two people booking one time are never both told yes
            clashes = self._check_clashes(rt, {"resourceType": rtype, **data}, member, over)
            rec = self.records.create(rtype, data)
            self._write_version(rt, rec, member, run_id)
            ev = self.track.append(member, "record.created", f"{rtype}/{rec['id']}", cause=cause, run_id=run_id,
                                   data={"subject": self.subject_of(rec),
                                         "summary": self.title(rec)})
            if clashes:
                ev = self._note_overbook(member, rt, f"{rtype}/{rec['id']}", clashes, over, cause=ev, run_id=run_id)
            self.engine.on_record("created", rt, rec, None, member, ev)
            if start_workflow and not getattr(self, "_upgrading", False):
                self._start_record_workflow(member, rt, rec, ev, inputs)
        if rt.is_a("Restriction"):
            self.restrictions.reset()  # in force at once, in this process
            self.credentials_within_reach(member, "a directive changed")
        return self.redact(member, rec)  # what they wrote, as they may read it

    def _start_record_workflow(self, member: dict, rt, rec: dict, cause: int, inputs: dict | None) -> None:
        """The workflow a new record's type names starts about it. Creating the record was the permission (no starter
        role is asked again); a workflow that is not installed yet (a pack still installing) is noted, not fatal."""
        wid = schema.starts_workflow(rt, rec)
        if not wid:
            return
        try:
            self.workflow(wid)
            if self.record_workflow(rt) != wid:
                return  # its active version still starts itself (a module upgrade half approved)
        except NotFound:
            self.track.append(member, "trigger.failed", f"{rt.id}/{rec['id']}", cause=cause,
                              data={"workflow": wid, "error": f"there is no workflow {wid!r}"})
            return
        starter = member if member.get("kind") in ("person", "agent", "service") else \
            {"id": f"type:{rt.id}", "kind": "automation", "name": rt.label, "status": "active"}
        self.engine.start(starter, wid, record=f"{rt.id}/{rec['id']}", inputs=inputs or {}, cause=cause,
                          via_automation=True, on_create=True)

    def update_record(self, member: dict, ref: str, data: dict, *, cause: int | None = None,
                      run_id: str | None = None, _signature: int | None = None,
                      expected_version: str | None = None, comment: str | None = None,
                      authors: list[str] | None = None, overbook: str | None = None) -> dict:
        """``comment``: why, kept on the version (and on the move, if the record moves). ``authors``: who wrote the
        change, when several people did (each must be a member who may write the record).

        ``_signature``: the move is made by a signature the record type allows (``moves_to``), which the signer's
        role was checked for; it may make moves reserved to workflows. ``expected_version``: the version the editor
        saw; when someone changed the record since, nothing is saved (their change is never silently overwritten).
        ``overbook``: a booking moved over another one, and why (as for ``create_record``)."""
        rtype, rid = ref.split("/", 1)
        rt = self._type(rtype)
        if member.get("kind") != "automation" and _signature is None:
            self.require(member, "writer", f"data_class:{rt.data_class}", f"write {rtype} records")
        with self.db:  # read, check and write as one step: a change made in between is never lost or skipped
            before = self.records.read(rtype, rid)
            if not before:
                raise NotFound(f"there is no {ref}")
            now = str((before.get("meta") or {}).get("versionId", ""))
            if expected_version is not None and str(expected_version) != now:
                last = self.db.record_versions.find_one({"type": rtype, "id": rid}, sort=[("version", DESC)])
                who = self.member(last["actor"])["name"] if last and self.db.members.exists(
                    {"_id": last["actor"]}) else "someone"
                raise Conflict(f"{who} changed this record while you were editing it (it is now version {now}; you "
                               f"started from version {expected_version}). Nothing was saved: open it again, then make "
                               f"your change.")
            after = {**before, **{k: v for k, v in data.items() if k not in ("resourceType", "id", "meta")}}
            after = {k: v for k, v in after.items() if v is not None}  # None clears a field
            self._workflow_fields(rt, member, run_id, before, after)
            after = visibility.guard_write(self, member, rt, before, after)  # untouched: kept; changed: refused
            if rt.part_of and schema.first(before, rt.part_of["path"]) != schema.first(after, rt.part_of["path"]):
                raise Invalid(f"a {rt.label.lower()} stays part of the record it was made in: "
                              f"make a new one there instead")
            moved = None
            if rt.lifecycle:
                old, new = schema.first(before, rt.lifecycle["path"]), schema.first(after, rt.lifecycle["path"])
                if old in rt.lifecycle.get("locked", []) and not self._by_workflow(member, run_id) and _signature is None:
                    strip = lambda r: {k: v for k, v in schema.put(r, rt.lifecycle["path"], None).items() if k != "meta"}
                    if strip(before) != strip(after):
                        moves = [t["label"] for t in rt.lifecycle["transition"] if old in t["from"] and t["by"] != "workflow"]
                        raise Invalid(f"a {rt.label.lower()} that is {old} is not edited: "
                                      + (f"{' or '.join(moves)} first" if moves else "its open work moves it on first"))
                if old != new:
                    t = rt.transition(old, new)
                    if t is None:
                        raise Invalid(f"a {rt.label.lower()} does not move from {old!r} to {new!r}")
                    if t["by"] == "workflow" and not self._by_workflow(member, run_id) and _signature is None:
                        raise Denied(f"only a workflow moves a {rt.label.lower()} to {new!r}")
                    why = self.state_move_refusal(rt, ref, t, after, run_id) if _signature is None else None
                    if why:
                        raise Denied(why)
                    # the move's own rule (a transition's require), whoever makes it: a person, a workflow, a signature
                    empty = [rt.field(n)["label"] for n in t.get("require", [])
                             if schema.first(after, rt.field(n)["path"]) in (None, "", [], {})]
                    if empty:
                        raise Invalid(f"to move a {rt.label.lower()} to {new!r}, fill in {', '.join(empty)}")
                    moved = (old, new)
            clashes, over = [], None
            if rt.is_a("Booking"):
                held, now_held = self._held_time(rt, before), self._held_time(rt, after)
                if held != now_held and not (held[4] and not held[3]) and now_held[:3] != held[:3] \
                        and not getattr(self, "_upgrading", False):
                    raise Invalid(f"a {rt.label.lower()} that no longer holds its time (cancelled or over) is not "
                                  f"moved: book a new one")
                over = overbook.strip() if overbook and overbook.strip() else None
                if over and self.may_overbook(member, rt):
                    after = self._overbooked(rt, after, over)
                    now_held = self._held_time(rt, after)
            self._check_record(rt, after, before)
            if rt.is_a("Booking") and held != now_held:  # only a change of time, members or state can make a clash
                clashes = self._check_clashes(rt, after, member, over)
            if authors:
                for a in authors:
                    if a != member.get("id") and not self.may_write(self.member(a), rtype):
                        raise Denied(f"{a} may not write {rtype} records")
            rec = self.records.update(rtype, rid, after)  # a store replaces the record: store what was validated
            note = comment.strip()[:500] if comment and comment.strip() else None
            self._write_version(rt, rec, member, run_id, comment=comment, authors=authors)
            changed = sorted(k for k in set(before) | set(rec) if k != "meta" and before.get(k) != rec.get(k))
            ev = self.track.append(member, "record.updated", ref, cause=cause, run_id=run_id,
                                   data={"changed": changed, **({"comment": note} if note and not moved else {})})
            if moved:
                ev = self.track.append(member, "record.transitioned", ref, cause=ev, run_id=run_id,
                                       data={"from": moved[0], "to": moved[1],
                                             **({"comment": comment.strip()} if comment and comment.strip() else {})})
            if clashes:
                ev = self._note_overbook(member, rt, ref, clashes, over, cause=ev, run_id=run_id)
            if moved and (rt.workflow or {}).get("id"):
                self._owner_follows_move(rt, ref, rec, member, run_id, rt.transition(*moved), ev)
            self.engine.on_record("transitioned" if moved else "updated", rt, rec, before, member, ev)
        if rt.is_a("Restriction"):
            self.restrictions.reset()
            self.credentials_within_reach(member, "a directive changed")
        return self.redact(member, rec)

    # ------------------------------------------------------------------ signatures on any record
    def _record_digest(self, rec: dict) -> str:
        """What a signature covers: the record's contents, without its bookkeeping (meta) or where it is in its
        lifecycle, so the move a signature makes (approved, closed) does not leave it covering nothing."""
        from .track import canonical
        rt = self.record_type_for_reading(rec.get("resourceType", "")) if rec.get("resourceType") else None
        if rt and rt.lifecycle:
            rec = schema.put(rec, rt.lifecycle["path"], None)
        return hashlib.sha256(canonical({k: v for k, v in rec.items() if k != "meta"}).encode()).hexdigest()

    def record_version(self, ref: str) -> int:
        rtype, rid = ref.split("/", 1)
        return self.db.record_versions.max("version", {"type": rtype, "id": rid}) or 0

    def record_digest(self, member: dict, ref: str) -> str:
        """The hash a signature on this record would cover, as this member reads it: a signing page or API client
        shows the record, then signs with ``seen`` set to this, so a change in between refuses the signature."""
        return self._record_digest(self.read_record(member, ref, raw=True))

    def sign_record(self, member: dict, ref: str, meaning: str, comment: str = "", *, seen: str | None = None) -> dict:
        """A person's electronic signature on a record: bound to its current version and the hash of its contents,
        so a later change shows the signature no longer covers what the record says. Agents never sign.

        A record type that lists ``[[signature]]`` meanings allows only those, by those roles, in those states; one
        that lists none lets anyone who may write the record sign with a meaning of their own. A signature with
        ``moves_to`` moves the record through its lifecycle, as the signer. ``seen`` is the ``record_digest`` of what
        the signer was shown; the pages and the API always pass it."""
        if member.get("kind") != "person":
            raise Denied("only a person signs: agents never sign")
        if member.get("status") != "active":
            raise Denied("an inactive member cannot sign")
        rtype, rid = ref.split("/", 1)
        rt = self._type(rtype)
        rec = self.read_record(member, ref)
        meaning = (meaning or "").strip()
        if not meaning:
            raise Invalid("say what your signature means")
        kind = rt.signature(meaning)
        if rt.signatures:
            if not kind:
                raise Invalid(f"a {rt.label.lower()} is signed as one of: "
                              + ", ".join(repr(g["meaning"]) for g in rt.signatures))
            if kind["roles"] and not set(kind["roles"]) & set(member["roles"]):
                self.track.append(member, "access.denied", ref, data={"what": f"sign {meaning}"})
                raise Denied(f"signing {meaning!r} needs the {' or '.join(kind['roles'])} role")
            if kind["states"] and rt.lifecycle:
                st = schema.first(rec, rt.lifecycle["path"])
                if st not in kind["states"]:
                    raise Invalid(f"a {rt.label.lower()} is signed {meaning!r} only while it is "
                                  f"{' or '.join(kind['states'])} (it is {st!r})")
        else:
            self.require(member, "writer", f"data_class:{rt.data_class}", f"sign {rtype} records")
        if any(s["by"] == member["id"] and s["meaning"] == meaning and s["current"]
               for s in self.signatures(member, ref)):
            raise Invalid(f"you have already signed this version as {meaning!r}")
        moves = bool(kind and kind.get("moves_to") and rt.lifecycle)
        with self.db:  # read, check and write as one step, so a change in between cannot slip under the signature
            rec = self.read_record(member, ref, raw=True)
            if seen is not None and seen != self._record_digest(rec):
                raise Invalid("the record changed since you read it: read it again, then sign what it says now")
            if moves:
                running = self.engine.runs(record=ref, status="running", limit=1)
                if running:
                    raise Invalid(f"this {rt.label.lower()} is in the work {running[0]['title']!r}: its decisions are "
                                  f"made there, and a signature here does not move it on")
                signed_rec = schema.put(rec, rt.lifecycle["path"], kind["moves_to"])
            else:
                signed_rec = rec
            version = self.record_version(ref) + (1 if moves else 0)
            digest = self._record_digest(signed_rec)  # what the record says once the signature has taken effect
            seq = self.track.append(member, "record.signed", ref, data={"comment": comment},
                                    signed={"record": ref, "version": version, "record_sha256": digest,
                                            "meaning": meaning})
            if moves:
                self.update_record(member, ref, signed_rec, cause=seq,
                                   _signature=seq)
        return {"event": seq, "version": version, "record_sha256": digest}

    def attest(self, actor: dict, ref: str, run: dict, step_id: str) -> int:
        """Record that ``ref``, as filed now, is what a signing gate earlier in the run signed: the physician signed
        the draft, and this is the note filed from it. The signature stays the signer's own event; this links it.
        An agent's decision is not a signature: it counts here only when a person signed their approval of it."""
        got = (run.get("ctx") or {}).get(step_id) or {}
        sig = got.get("signature") or got.get("event")
        ev = self.db.events.find_one({"_id": sig, "action": {"$in": ["gate.decided", "hold.decided"]},
                                      "run_id": run["id"]}) if sig else None
        if not ev or not ev["signature"]:
            raise Invalid(f"attest: the step {step_id} is not a signed decision in this run")
        rec = self.records.read(*ref.split("/", 1))
        rt_, rid_ = ref.split("/", 1)
        ver = {"v": self.db.record_versions.max("version", {"type": rt_, "id": rid_})}
        return self.track.append(actor, "record.attested", ref, run_id=run["id"], cause=ev["seq"],
                                 data={"gate_event": ev["seq"], "step": step_id,
                                       "record_sha256": self._record_digest(rec), "version": ver["v"] or 1})

    def signatures(self, member: dict, ref: str) -> list[dict]:
        """Every signature on a record, newest first, each marked current if it covers what the record says now."""
        rtype, rid = ref.split("/", 1)
        rec = self.read_record(member, ref, raw=True)
        now = self._record_digest(rec)
        out = []
        rows = self.db.events.find({"$or": [{"action": "record.signed", "subject": ref},
                                            {"action": {"$in": ["gate.decided", "hold.decided"]},
                                             "data.signed.record": ref}]},
                                   sort=[("seq", DESC)])
        for e in rows:
            d = e["data"]
            sg = d.get("signed", {})
            if sg.get("record") != ref or not e.get("signature"):  # an agent's attested decision is not a signature
                continue
            who = self.db.members.get(e["actor"])
            if not who or who.get("kind") != "person":
                continue
            gate = e["action"] in ("gate.decided", "hold.decided")
            current = bool(sg.get("record_sha256")) and sg.get("record_sha256") == now
            if gate and not current and sg.get("record_sha256") and sg.get("version"):
                # A decision's own run carries it out (publishes, dates it): it covers the record while every change
                # since it was made was made by that run.
                later = self.db.record_versions.find({"type": rtype, "id": rid, "version": {"$gt": sg["version"]}},
                                                     fields=["run_id"])
                current = bool(later) and all(v["run_id"] == sg.get("run") for v in later)
            out.append({"seq": e["seq"], "at": e["at"], "by": e["actor"], "name": who["name"] if who else e["actor"],
                        "meaning": sg.get("meaning"), "version": sg.get("version"), "comment": d.get("comment", ""),
                        "outcome": (sg.get("decision") or sg.get("outcome")) if gate else None,
                        "run": sg.get("run") if gate else None,
                        "current": current,
                        "valid": self.track.verify_signature({**e, "data": d})})
        for a in self.db.events.find({"action": "record.attested", "subject": ref}, sort=[("seq", DESC)]):
            d = a["data"]
            g = self.db.events.get(d.get("gate_event"))
            if not g or g["run_id"] != a["run_id"]:
                continue
            gd = g["data"]
            sg = gd.get("signed", {})
            who = self.db.members.get(g["actor"])
            current = d.get("record_sha256") == now
            if not current:  # changed since filing: covered only while every change was made by the signing run
                later = self.db.record_versions.find({"type": rtype, "id": rid,
                                                      "version": {"$gt": d.get("version", 1)}}, fields=["run_id"])
                current = bool(later) and all(v["run_id"] == a["run_id"] for v in later)
            out.append({"seq": g["seq"], "at": g["at"], "by": g["actor"], "name": who["name"] if who else g["actor"],
                        "meaning": sg.get("meaning"), "version": d.get("version"), "comment": gd.get("comment", ""),
                        "outcome": sg.get("outcome"), "run": a["run_id"], "current": current,
                        "attested": a["seq"], "valid": self.track.verify_signature({**g, "data": gd})})
        out.sort(key=lambda x: x["seq"], reverse=True)
        return out

    def chart(self, member: dict, subject: str) -> dict:
        """Every record about a subject the member may read, grouped by data class; what they may not read is
        counted, not shown. What a restriction withholds from them is there as a stub that says so (``withheld``
        counts them): the file is never shown as if it were complete."""
        out, hidden, withheld = {}, 0, 0
        self.note_view(member, subject, how="chart")
        for rec in self.records.search(subject=subject, limit=None):
            if rec.get("resourceType") == subject.split("/")[0] and f"{rec['resourceType']}/{rec['id']}" == subject:
                continue
            rt = self.record_type(rec["resourceType"])
            if rt is None:
                continue
            dc = rt.data_class
            if self.can(member, "reader", f"data_class:{dc}"):
                shown = self.redact(member, rec)
                out.setdefault(dc, []).append(shown)
                gone = visibility.withheld(shown)
                if gone and gone.get("whole"):
                    withheld += 1
                    continue
                self.note_view(member, f"{rec['resourceType']}/{rec['id']}", how=f"chart of {subject}")
            else:
                hidden += 1
        rest = self.withheld(member, subject)
        return {"classes": out, "hidden": hidden, "withheld": withheld,
                **({"withheld_note": rest["note"]} if rest and not rest["allowed"] and not rest["override"] else {})}

    # ------------------------------------------------------------------ workflows and change control
    def workflow(self, wid: str, version: int | None = None) -> tuple[wfmod.Workflow, dict]:
        from .engine import UNHANDLED_SOURCE, UNHANDLED_WORKFLOW
        if wid == UNHANDLED_WORKFLOW and version == 0:  # the home of a notice about an event no workflow took
            return wfmod.parse(UNHANDLED_SOURCE, None), {"workflow_id": wid, "version": 0, "status": "active",
                                                          "source": UNHANDLED_SOURCE, "author": "hub", "note": "",
                                                          "module": "", "approved_by": None, "approved_at": None}
        from .restrictions import OVERRIDE_SOURCE, OVERRIDE_WORKFLOW
        if wid == OVERRIDE_WORKFLOW and version == 0:  # the home of the privacy officer's notice of an emergency access
            return wfmod.parse(OVERRIDE_SOURCE, None), {"workflow_id": wid, "version": 0, "status": "active",
                                                         "source": OVERRIDE_SOURCE, "author": "hub", "note": "",
                                                         "module": "", "approved_by": None, "approved_at": None}
        from .credentials import ACCOUNT_SOURCE, ACCOUNT_WORKFLOW
        if wid == ACCOUNT_WORKFLOW and version == 0:  # the home of a notice to a member about their own account
            return wfmod.parse(ACCOUNT_SOURCE, None), {"workflow_id": wid, "version": 0, "status": "active",
                                                        "source": ACCOUNT_SOURCE, "author": "hub", "note": "",
                                                        "module": "", "approved_by": None, "approved_at": None}
        if wid == APPROVAL_WORKFLOW and version == 0:  # the home of a notice that a change waits for approval
            return wfmod.parse(APPROVAL_SOURCE, None), {"workflow_id": wid, "version": 0, "status": "active",
                                                         "source": APPROVAL_SOURCE, "author": "hub", "note": "",
                                                         "module": "", "approved_by": None, "approved_at": None}
        from .mentions import MENTION_SOURCE, MENTION_WORKFLOW
        if wid == MENTION_WORKFLOW and version == 0:  # the home of a notice that someone mentioned a member
            return wfmod.parse(MENTION_SOURCE, None), {"workflow_id": wid, "version": 0, "status": "active",
                                                        "source": MENTION_SOURCE, "author": "hub", "note": "",
                                                        "module": "", "approved_by": None, "approved_at": None}
        if version is None:
            row = self.db.workflow_versions.find_one({"workflow_id": wid, "status": "active"})
        else:
            row = self.db.workflow_versions.get(f"{wid}@{version}")
        if not row:
            raise NotFound(f"there is no workflow {wid!r}" + (f" version {version}" if version else ""))
        if row["status"] in ("active", "retired"):
            self._require_sealed("workflow", row)
        return wfmod.parse(row["source"], None, stored=True), row

    def workflows(self) -> list[dict]:
        rows = self.db.workflow_versions.find({"status": "active"}, sort=[("workflow_id", ASC)])
        out = []
        for r in rows:
            try:
                self._require_sealed("workflow", r)
            except Invalid:
                continue  # shown nowhere and started by nobody until restored; 'verify' names it
            try:
                wf = wfmod.parse(r["source"], None, stored=True)
            except wfmod.DefinitionError as exc:  # one unreadable version must not take every page down with it
                logging.getLogger(__name__).warning("workflow %s@%s is not readable: %s", r["workflow_id"],
                                                    r.get("version"), "; ".join(exc.problems))
                continue
            pending = self.db.workflow_versions.count({"workflow_id": r["workflow_id"], "status": "proposed"})
            out.append({"workflow": wf, "row": r, "proposed": pending})
        return out

    def versions(self, wid: str) -> list[dict]:
        return self.db.workflow_versions.find({"workflow_id": wid}, sort=[("version", DESC)])

    # Workflows and blocks share one change control: anyone with standing proposes, a different person who is a policy
    # owner approves, and the approval is signed with the source's hash. Runs use what was active when they started.
    KINDS = {"workflow": ("workflow_versions", "workflow_id"), "block": ("block_versions", "block_id"),
             "type": ("type_versions", "type_id"), "view": ("view_versions", "view_id"),
             "skill": ("skill_versions", "skill_id")}

    SEALS = ("approved", "installed", "extended", "unextended", "upgraded")

    def sealed_sha(self, kind: str, did: str, version: int) -> str | None:
        """The source hash the track recorded when this version was approved, installed or extended: the last such
        event for it that this hub wrote and sealed. An event written into the database without the hub's secret
        (a forged approval) is ignored. A row whose source does not hash to this was changed outside change control."""
        for e in self.track.trusted({"subject": f"{kind}:{did}", "action": {"$in": [f"{kind}.{a}" for a in self.SEALS]},
                                     "data.version": version}, sort=[("seq", DESC)]):
            d = e["data"] or {}
            if d.get("version") == version and d.get("source_sha256"):
                return d["source_sha256"]
        return None

    def sealed_active(self, kind: str, did: str) -> int | None:
        """The version the track says is in force: the newest sealed approval, installation or extension, unless a
        sealed retirement came after it. A row marked active in the database that the track does not put in force
        was switched on outside change control."""
        acts = [f"{kind}.{a}" for a in self.SEALS] + [f"{kind}.retired"]
        for e in self.track.trusted({"subject": f"{kind}:{did}", "action": {"$in": acts}}, sort=[("seq", DESC)]):
            if e["action"] == f"{kind}.retired":
                return None
            v = (e["data"] or {}).get("version")
            if v is not None:
                return v
        return None

    def _require_sealed(self, kind: str, row: dict) -> None:
        """Fail closed: a definition runs only as it was approved, and only the version the track put in force."""
        did = row[self.KINDS[kind][1]]
        digest = hashlib.sha256(row["source"].encode()).hexdigest()
        key = (kind, did, row["version"], row.get("status"), digest)
        if key in self._sealed_ok:
            return
        if self.sealed_sha(kind, did, row["version"]) != digest:
            raise Invalid(f"the {kind} {did} version {row['version']} is not as it was approved (its text does not "
                          f"match the track): it will not run. Run 'practicehub verify' and restore it.")
        if row.get("status") == "active" and self.sealed_active(kind, did) != row["version"]:
            raise Invalid(f"the {kind} {did} version {row['version']} is marked active, but the track did not put it "
                          f"in force: it will not run. Run 'practicehub verify' and restore it.")
        self._sealed_ok.add(key)

    def verify(self, *, full: bool = True) -> dict:
        """The track's chain, seals and signatures, and every definition's text against what the track sealed.

        ``full=False`` (the Track page) checks only the events since the last verify, from its sealed result kept in
        the settings, and adds what it finds to that result; ``practicehub verify`` checks everything. The result is
        kept, with when it was checked and from which event."""
        from .track import canonical
        last = self.db.get_setting("track_verified") if not full else None
        if last and not hmac_ok(self.track, last):
            last = None  # a result not sealed by this hub is not one to build on
        if last and not last["problems"]:
            newest = self.db.events.find_one({}, sort=[("seq", DESC)], fields=["seq", "hash"])
            if newest and newest["seq"] == last["head"]["seq"] and newest["hash"] == last["head"]["hash"]:
                # nothing since the last check: its sealed result stands, and nothing is recomputed or rewritten
                # (anchors are checked whenever there are new events, and by 'practicehub verify')
                v = {"ok": True, "events": last["events"], "problems": [], "head": last["head"],
                     "from": last["head"]["seq"], "at": last["at"], "full": False}
                defs = self.definitions_problems()
                return {**v, "ok": False, "problems": defs} if defs else v
        if last:
            v = self.track.verify(since=last["head"])
            v = {**v, "events": last["events"] + v["events"], "from": last["head"]["seq"],
                 "problems": list(last["problems"]) + [p for p in v["problems"] if p not in last["problems"]]}
            v["ok"] = not v["problems"]
        else:
            v = {**self.track.verify(), "from": 0}
        track_part = {"ok": v["ok"], "events": v["events"], "problems": v["problems"], "head": v["head"],
                      "at": utcnow(), "full": full or not last, "from": v["from"]}
        track_part["mac"] = self.track.seal("verified:" + canonical({k: x for k, x in track_part.items() if k != "mac"}))
        self.db.set_setting("track_verified", track_part)
        v["at"], v["full"] = track_part["at"], track_part["full"]
        defs = self.definitions_problems()
        return {**v, "ok": False, "problems": list(v.get("problems") or []) + defs} if defs else v

    def record_restore(self, actor: dict, note: str = "") -> int:
        """After the database was restored from a backup: a policy owner, on the server that holds the secret,
        records in the track that the events after the restored head were lost, and the anchors hold the track from
        here on. Without this, verify keeps reporting the anchored events the restore lost; nothing is dropped
        silently."""
        if actor.get("kind") != "person":
            raise Denied("a restore is recorded by a person")
        self.require(actor, "policy_owner", "practice:main", "record a restore")
        if not self.track.holds_secret():
            raise Denied("this server does not hold the secret that sealed the track: set PRACTICEHUB_SECRET to it")
        v = self.track.verify()
        other = [p for p in v["problems"] if "was anchored on" not in p]
        if other:
            raise Invalid("the restored track does not verify apart from its anchors, so it is not one to build on: "
                          + "; ".join(other[:5]))
        if v["ok"]:
            raise Invalid("nothing was lost: the track still holds every anchored event")
        lost = self.track.anchored_head()
        try:
            seq = self.track.record_restore(actor, lost, note)
        except ValueError as exc:
            raise Invalid(str(exc)) from None
        self._sealed_ok = set()
        self.db.del_setting("track_verified")
        return seq

    def definitions_problems(self) -> list[str]:
        """Every active or retired definition whose text no longer matches what the track sealed."""
        out = []
        for kind, (table, col) in self.KINDS.items():
            for row in self.db[table].find({"status": {"$in": ["active", "retired"]}}, sort=[(col, ASC), ("version", ASC)]):
                try:
                    self._require_sealed(kind, row)
                except Invalid as exc:
                    out.append(str(exc).split(":")[0] + ": its text does not match the track")
        return out

    def _propose(self, kind: str, actor: dict, did: str, source: str, note: str, module: str | None = None) -> dict:
        table, col = self.KINDS[kind]
        with self.db:
            version = (self.db[table].max("version", {col: did}) or 0) + 1
            self.db[table].insert({"_id": f"{did}@{version}", col: did, "version": version, "status": "proposed",
                                   "source": source, "author": actor["id"], "note": note, "created_at": utcnow(),
                                   "approved_by": None, "approved_at": None, "module": module or ""})
            self.track.append(actor, f"{kind}.proposed", f"{kind}:{did}", data={"version": version, "note": note})
        return {f"{kind}_id": did, "version": version}

    def _approvable(self, kind: str, actor: dict, did: str, version: int) -> dict:
        table, col = self.KINDS[kind]
        row = self.db[table].find_one({col: did, "version": version})
        if not row or row["status"] != "proposed":
            raise Invalid("only a proposed version can be approved")
        if actor["kind"] != "person":
            raise Denied(f"a {kind} change is approved by a person, never an agent")
        self.require(actor, "policy_owner", "practice:main", f"approve {kind} changes")
        if row["author"] == actor["id"]:
            raise Denied("whoever proposed a change does not approve it")
        # a second person means someone who could not have written it: not the author's sponsor (who answers for
        # the agent and may hold its credential), nor anyone who issued the author a credential still in use
        author = self.db.members.get(row["author"])
        if author and author["sponsor"] == actor["id"]:
            raise Denied("the sponsor of whoever proposed a change does not approve it: another policy owner does")
        if row["author"] != actor["id"] and self.db.tokens.exists(
                {"member": row["author"], "issued_by": actor["id"], "revoked": 0}):
            raise Denied("you issued a credential to whoever proposed this change, so another policy owner approves "
                         "it (or revoke that credential first)")
        return row

    def _activate(self, kind: str, actor: dict, did: str, version: int, row: dict, meaning: str, extra=None) -> None:
        table, col = self.KINDS[kind]
        digest = hashlib.sha256(row["source"].encode()).hexdigest()
        newer = self.db[table].find_one({col: did, "status": "active", "version": {"$gt": version}})
        if newer:  # approving it would quietly undo what was approved since
            raise Invalid(f"version {newer['version']} was approved after this was proposed: propose the change again "
                          f"from version {newer['version']}")
        self.db[table].update({col: did, "status": "active"}, {"$set": {"status": "retired"}}, many=True)
        n = self.db[table].update({col: did, "version": version, "status": "proposed"},
                                  {"$set": {"status": "active", "approved_by": actor["id"], "approved_at": utcnow()}})
        if n != 1:  # someone else approved, withdrew or declined it a moment ago
            raise Invalid(f"version {version} is no longer proposed")
        self.track.append(actor, f"{kind}.approved", f"{kind}:{did}",
                          data={"version": version, "source_sha256": digest, **(extra or {})},
                          signed={kind: did, "version": version, "meaning": meaning, "source_sha256": digest})

    def _withdraw(self, kind: str, actor: dict, did: str, version: int, reason: str) -> None:
        table, col = self.KINDS[kind]
        with self.db:
            row = self.db[table].find_one({col: did, "version": version})
            if not row or row["status"] != "proposed":
                raise Invalid("only a proposed version can be withdrawn or declined")
            if row["author"] != actor["id"] and not self.is_policy_owner(actor):
                raise Denied("only its author or a policy owner may withdraw a proposal")
            self.db[table].update({col: did, "version": version, "status": "proposed"},
                                  {"$set": {"status": "withdrawn"}})
            self.track.append(actor, f"{kind}.withdrawn", f"{kind}:{did}", data={"version": version, "reason": reason})

    def check_workflow(self, source: str, *, approving: bool = False, stored: bool = False) -> wfmod.Workflow:
        """Parse a workflow and check it against this practice: the pack's roles, skills and actions, and the blocks
        it uses. Raises DefinitionError with every problem. ``stored``: a version already approved, checked by the
        rules it was approved under (see ``workflows.parse``)."""
        problems: list[str] = []
        try:
            wf = wfmod.parse(source, self.pack, stored=stored)
        except wfmod.DefinitionError as exc:
            problems = exc.problems
            try:
                wf = wfmod.parse(source, None, stored=stored)
            except wfmod.DefinitionError:
                raise exc from None
        problems += [p for p in self._block_problems(wf, approving=approving) if p not in problems]
        problems += [p for p in self._state_problems(wf) if p not in problems]
        if problems:
            raise wfmod.DefinitionError(problems)
        return wf

    def _state_problems(self, wf: wfmod.Workflow) -> list[str]:
        """A step's ``state`` is a state of its record's lifecycle; a ``[new_record]`` state is one too."""
        rt = self.record_type(wf.record) if wf.record else None
        if rt is None:
            return []
        states = rt.states()
        if not states:
            return [f"step {s.id}: state: a {wf.record} has no lifecycle to move" for s in wf.steps if s.state]
        problems = [f"step {s.id}: state {s.state!r} is not a state of a {wf.record} ({', '.join(states)})"
                    for s in wf.steps if s.state and s.state not in states]
        field = (rt.lifecycle or {}).get("field")
        made = (wf.new_record or {}).get(field) if field else None
        if isinstance(made, str) and "{{" not in made and made not in states:
            problems.append(f"new_record: {field} {made!r} is not a state of a {wf.record} ({', '.join(states)})")
        return problems

    def _block_problems(self, wf: wfmod.Workflow, *, approving: bool) -> list[str]:
        problems = []
        ids = {s.id for s in wf.steps}
        for s in wf.steps:
            if not s.block:
                continue
            where = f"step {s.id}"
            try:
                b, _ = self.block(s.block)
            except NotFound:
                pending = self.db.block_versions.find_one({"block_id": s.block, "status": "proposed"},
                                                          sort=[("version", DESC)])
                if approving or not pending:
                    known = ", ".join(self.db.block_versions.distinct("block_id", {"status": "active"})) or "none yet"
                    problems.append(f"{where}: there is no approved block {s.block!r} (blocks: {known})")
                    continue
                b = blockmod.parse(pending["source"], None)
            if b.step_kind != s.kind:
                problems.append(f"{where}: the {b.runtime} block {b.id!r} is used by a {b.step_kind} step, not a {s.kind}")
            names = {f["name"] for f in b.inputs}
            for k in s.params:
                if k not in names:
                    problems.append(f"{where}: the block {b.id!r} has no input {k!r} "
                                    f"(inputs: {', '.join(sorted(names)) or 'none'})")
            for f in b.inputs:
                if f["required"] and f["name"] not in s.params and f.get("default") is None:
                    problems.append(f"{where}: give the block's input {f['name']!r} a value in params")
            if b.runtime == "prompt":
                if s.fields:
                    problems.append(f"{where}: a prompt block step takes its fields from the block; remove fields")
                # whatever the prompt is filled from is shown to the agent, so it must be in what the step reads:
                # the eligibility rules (data classes, earlier steps' data) then cover it
                covered = set(s.context)
                for value in s.params.values():
                    for m in wfmod.TEMPLATE.finditer(str(value)):
                        path = m.group(1)
                        head = path.split(".")[0]
                        if head in ("run",) or path in covered or any(path.startswith(c + ".") for c in covered):
                            continue
                        if head == "record":
                            problems.append(f"{where}: params read {{{{{path}}}}}: a prompt block is filled from the "
                                            f"run, not the live record; have an earlier step read it, so the rules "
                                            f"about who may read the record apply")
                        elif head in ids or head == "input":
                            problems.append(f"{where}: params read {{{{{path}}}}}; add {path!r} (or {head!r}) to the "
                                            f"step's context, so the rules about who may read it apply")
        return problems

    def propose_workflow(self, actor: dict, source: str, note: str = "") -> dict:
        """Anyone in the practice, a person or an agent, may propose a workflow change. It takes effect only when a
        policy owner who did not write it approves it."""
        if actor.get("status") != "active":
            raise Denied("an inactive member cannot propose changes")
        try:
            wf = self.check_workflow(source)
        except wfmod.DefinitionError as exc:
            raise Invalid("the workflow has problems", exc.problems) from None
        return self._propose("workflow", actor, wf.id, source, note)

    def approve_workflow(self, actor: dict, wid: str, version: int, meaning: str = "") -> dict:
        row = self._approvable("workflow", actor, wid, version)
        try:
            wf = self.check_workflow(row["source"], approving=True)
        except wfmod.DefinitionError as exc:
            raise Invalid("the workflow no longer validates", exc.problems) from None
        meaning = meaning or f"I approve version {version} of the {wf.title} workflow"
        with self.db:
            self._activate("workflow", actor, wid, version, row, meaning)
            self._sync_starters(wf)
        return {"workflow_id": wid, "version": version}

    def withdraw_workflow(self, actor: dict, wid: str, version: int, reason: str = "") -> None:
        self._withdraw("workflow", actor, wid, version, reason)

    # ------------------------------------------------------------------ blocks
    def block(self, bid: str, version: int | None = None) -> tuple[blockmod.Block, dict]:
        if version is None:
            row = self.db.block_versions.find_one({"block_id": bid, "status": "active"})
        else:
            row = self.db.block_versions.get(f"{bid}@{version}")
        if not row:
            raise NotFound(f"there is no block {bid!r}" + (f" version {version}" if version else ""))
        if row["status"] in ("active", "retired"):
            self._require_sealed("block", row)
        return blockmod.parse(row["source"], None), row

    def blocks(self) -> list[dict]:
        """Every block with an active or proposed version."""
        out = []
        ids = self.db.block_versions.distinct("block_id", {"status": {"$in": ["active", "proposed"]}})
        for bid in ids:
            try:
                b, row = self.block(bid)
            except NotFound:
                row = self.db.block_versions.find_one({"block_id": bid, "status": "proposed"}, sort=[("version", DESC)])
                b, row = blockmod.parse(row["source"], None), {**row, "version": None}
            pending = self.db.block_versions.count({"block_id": bid, "status": "proposed"})
            out.append({"block": b, "row": row, "proposed": pending})
        return out

    def block_versions(self, bid: str) -> list[dict]:
        return self.db.block_versions.find({"block_id": bid}, sort=[("version", DESC)])

    def propose_block(self, actor: dict, source: str, note: str = "") -> dict:
        """A prompt block may be proposed by anyone, agents included. A block that runs code or calls another engine
        is written by a person: model output is data, never code."""
        if actor.get("status") != "active":
            raise Denied("an inactive member cannot propose changes")
        try:
            b = blockmod.parse(source, self.pack)
        except wfmod.DefinitionError as exc:
            raise Invalid("the block has problems", exc.problems) from None
        if actor.get("kind") != "person" and b.runtime != "prompt":
            raise Denied(f"only a person may propose a {b.runtime} block: agents never write code or engine calls")
        return self._propose("block", actor, b.id, source, note)

    def approve_block(self, actor: dict, bid: str, version: int, meaning: str = "") -> dict:
        row = self._approvable("block", actor, bid, version)
        try:
            b = blockmod.parse(row["source"], self.pack)
        except wfmod.DefinitionError as exc:
            raise Invalid("the block no longer validates", exc.problems) from None
        what = {"python": "its Python code", "prompt": "its prompt", "external": f"its call to {b.engine}"}[b.runtime]
        meaning = meaning or f"I approve version {version} of the {b.title} block, including {what}"
        with self.db:
            self._activate("block", actor, bid, version, row, meaning,
                           extra={"runtime": b.runtime, "code_sha256": b.code_sha256} if b.code else {"runtime": b.runtime})
        return {"block_id": bid, "version": version}

    def withdraw_block(self, actor: dict, bid: str, version: int, reason: str = "") -> None:
        self._withdraw("block", actor, bid, version, reason)

    def block_users(self, bid: str) -> list[wfmod.Workflow]:
        """Active workflows that use a block (new runs of them pick up a newly approved version)."""
        return [w["workflow"] for w in self.workflows() if any(s.block == bid for s in w["workflow"].steps)]

    def block_impact(self, bid: str, version: int) -> list[str]:
        """What approving this block version changes, for the approver to read."""
        new, _ = self.block(bid, version)
        try:
            old, _ = self.block(bid)
        except NotFound:
            old = None
        notes = []
        if new.runtime == "python" and (not old or old.code != new.code):
            notes.append("This version changes Python code. It will run on this practice's data in the sandbox: "
                         "read every line.")
        if new.runtime == "prompt" and (not old or old.prompt != new.prompt):
            notes.append(f"This version changes what agents are told to do. Agents whose profile lists "
                         f"{new.capability!r} carry it out.")
        if new.runtime == "external" and (not old or (old.engine, old.target) != (new.engine, new.target)):
            notes.append(f"This version sends its inputs to {new.engine} ({new.target}). Check that engine keeps data "
                         f"where the practice must.")
        if old and [f["name"] for f in old.inputs] != [f["name"] for f in new.inputs]:
            notes.append("Its inputs change: workflows that use it may need new params.")
        users = self.block_users(bid)
        if users:
            notes.append("New runs of these workflows will use it: " + ", ".join(w.title for w in users) + ".")
        return notes

    def _sync_starters(self, wf: wfmod.Workflow) -> None:
        obj = f"workflow:{wf.id}"
        self.authz.delete(self.authz.read(obj=obj, relation="starter"))
        self.authz.write([(obj, "starter", f"role:{r}#assignee") for r in wf.starters])

    def agent_impact(self, wid: str, version: int) -> list[str]:
        """What a proposed version changes about what agents may do, for the approver to read."""
        new, _ = self.workflow(wid, version)
        try:
            old, _ = self.workflow(wid)
        except NotFound:
            old = None
        notes = []
        old_steps = {s.id: s for s in old.steps} if old else {}
        for s in new.steps:
            o = old_steps.get(s.id)
            if s.block and (not o or o.block != s.block or o.params != s.params):
                try:
                    b, row = self.block(s.block)
                    what = {"python": "Python code", "prompt": "an injected prompt for agents",
                            "external": f"a call to {b.engine}"}[b.runtime]
                    notes.append(f"'{s.title}' uses the block '{b.title}' (v{row['version']}, {what}) "
                                 f"with params {json.dumps(s.params)}.")
                except NotFound:
                    notes.append(f"'{s.title}' uses the block {s.block!r}, which is not approved yet.")
            if s.kind == "automation":
                if s.id not in old_steps and not s.block:
                    notes.append(f"New automation '{s.title}' runs {s.action}.")
                continue
            agent_now = s.agents_may and (s.kind != "gate" or bool(s.agent_criteria))
            agent_before = bool(o) and o.agents_may and (o.kind != "gate" or bool(o.agent_criteria))
            if agent_now and not agent_before:
                notes.append(f"Agents holding {s.role} may now do '{s.title}' (risk {s.risk}).")
            if o and agent_now and RISK_ORDER[s.risk] < RISK_ORDER[o.risk]:
                notes.append(f"'{s.title}' is now rated {s.risk} risk (was {o.risk}): more agents may take it.")
            if o and s.agent_criteria != o.agent_criteria and s.agent_criteria:
                notes.append(f"'{s.title}': agents may decide when {s.agent_criteria} (was {o.agent_criteria or 'never'}).")
            if o and s.role != o.role:
                notes.append(f"'{s.title}' moves from {o.role} to {s.role}.")
            if o and set(s.context) - set(o.context):
                notes.append(f"'{s.title}' now reads {', '.join(sorted(set(s.context) - set(o.context)))}.")
        if old and set(new.starters) != set(old.starters):
            notes.append(f"Who may start it: {', '.join(new.starters) or 'nobody by hand'} "
                         f"(was {', '.join(old.starters) or 'nobody by hand'}).")
        return notes
