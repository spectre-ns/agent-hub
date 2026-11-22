"""The practice's catalog: record types (the object model), views and skills, beside workflows and blocks.

Every definition is TOML under one change control (``Hub.propose`` / ``Hub.approve``): anyone with standing proposes,
a different person who is a policy owner approves, and the approval is signed with the source's SHA-256. A pack or
module installs the first versions; after that the practice owns them and changes them from the UI.

The hub is the catalog every definition is checked against: a record type's data class and reference targets must
exist, a view's record types, workflows and roles must exist, and a workflow's skills are the skills the practice runs
now, not the ones its pack shipped.
"""

from __future__ import annotations

import difflib
import hashlib
import tomllib

from . import schema
from . import views as viewmod
from .pack import parse_skill
from .store import DESC
from .workflows import DefinitionError


def _plain(v) -> str:
    """Words compared as people mean them: case, accents and spacing aside."""
    import unicodedata
    text = unicodedata.normalize("NFKD", schema.words(v)).casefold()
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())

# kind -> (collection, id field, noun). Workflows and blocks are in the same table of kinds (Hub.KINDS).
DEFINITIONS = {
    "type": ("type_versions", "type_id", "record type"),
    "view": ("view_versions", "view_id", "view"),
    "skill": ("skill_versions", "skill_id", "skill"),
}


class CatalogMixin:
    """Mixed into Hub. Expects ``self.db``, ``self.pack``, ``self.track`` and the change-control helpers."""

    # ------------------------------------------------------------------ cache
    def _defs_reset(self) -> None:
        self._defs: dict = {}

    def _def_row(self, kind: str, did: str, version: int | None = None) -> dict | None:
        table, col, _ = DEFINITIONS[kind]
        if version is None:
            return self.db[table].find_one({col: did, "status": "active"})
        return self.db[table].find_one({col: did, "version": version})

    def _def_parse(self, kind: str, source: str, catalog=None):
        if kind == "type":
            return schema.parse(source, catalog)
        if kind == "view":
            return viewmod.parse(source, catalog)
        return parse_skill(source)

    def _def_get(self, kind: str, did: str, version: int | None = None):
        row = self._def_row(kind, did, version)
        if not row:
            return None
        key = (kind, did, row["version"], hashlib.sha256(row["source"].encode()).hexdigest())
        if key not in self._defs:
            if row["status"] in ("active", "retired"):
                self._require_sealed(kind, row)
            self._defs[key] = self._def_parse(kind, row["source"])
        return self._defs[key]

    def _def_ids(self, kind: str, statuses=("active",)) -> list[str]:
        table, col, _ = DEFINITIONS[kind]
        return self.db[table].distinct(col, {"status": {"$in": list(statuses)}})

    def _ever_defined(self, kind: str, did: str) -> bool:
        table, col, _ = DEFINITIONS[kind]
        return self.db[table].exists({col: did, "status": {"$in": ["active", "retired"]}})

    # ------------------------------------------------------------------ record types
    def record_type(self, tid: str, version: int | None = None) -> schema.RecordType | None:
        """The record type the practice uses now. A type a pack names in a data class but ships no schema for is
        permissive (any keys). A type that was retired is gone: its records can be read, not written. A type stored
        before a check that now refuses it is treated as absent (no page fails on it) and named on the record types
        page, until a fixed version is proposed and approved."""
        try:
            rt = self._def_get("type", tid, version)
        except DefinitionError as exc:
            self.__dict__.setdefault("broken_types", {})[tid] = str(exc)
            return None
        if rt is not None or version is not None:
            return rt
        if self._ever_defined("type", tid):
            return None
        dc = self.pack.class_of(tid) if self.pack else None
        if not dc:
            return None
        key = ("type", tid, "permissive", dc)
        if key not in self._defs:
            self._defs[key] = schema.permissive(tid, dc, (self.pack.record_labels or {}).get(tid))
        return self._defs[key]

    def record_type_for_reading(self, tid: str) -> schema.RecordType | None:
        """The type to read a record by: the active one, else the last version before it was retired."""
        rt = self.record_type(tid)
        if rt is not None:
            return rt
        table, col, _ = DEFINITIONS["type"]
        row = self.db[table].find_one({col: tid, "status": "retired"}, sort=[("version", DESC)], fields=["version"])
        return self._def_get("type", tid, row["version"]) if row else None

    def record_types(self) -> dict[str, schema.RecordType]:
        ids = set(self._def_ids("type")) | set(self.pack.record_types() if self.pack else [])
        out = {}
        for tid in sorted(ids):
            rt = self.record_type(tid)
            if rt is not None:
                out[tid] = rt
        return dict(sorted(out.items(), key=lambda kv: (kv[1].order, kv[1].label)))

    def type_ids(self) -> set[str]:
        return set(self.record_types())

    def subject_types(self) -> set[str]:
        """The types work is about: those that extend the core's Subject (or say ``subject = true``)."""
        out = {t for t, rt in self.record_types().items() if rt.subject or rt.extends == "Subject"}
        if self.pack and self.pack.subject_type in self.type_ids():
            out.add(self.pack.subject_type)
        return out

    def is_a(self, tid: str, base: str) -> bool:
        """Whether records of ``tid`` stand where ``base`` is asked for: itself, a core base type it extends, or
        any record (``Record``, ``*``). A module's reference to ``Subject`` takes whatever the practice's subjects are."""
        if tid == base or base in ("*", schema.ROOT):
            return True
        if base not in schema.bases():
            return False
        rt = self.record_type_for_reading(tid)
        return bool(rt and rt.is_a(base))

    def of_base(self, base: str) -> list[str]:
        """The practice's types that extend a core base type (or the type itself, when ``base`` is not one)."""
        if base not in schema.bases():
            return [base]
        return [t for t, rt in self.record_types().items() if rt.is_a(base)]

    def concrete(self, targets) -> list[str]:
        """Reference targets with each base type replaced by the practice's types that extend it."""
        out = []
        for t in targets:
            for c in self.of_base(t):
                if c not in out:
                    out.append(c)
        return out

    def project(self, rec: dict) -> dict:
        """A record as its core base type sees it (``schema.project``): what the core and modules read."""
        rt = self.record_type_for_reading(rec.get("resourceType") or "")
        return schema.project(rt, rec, self.member_types())

    def title(self, rec: dict | None) -> str:
        """What a record is called: its type's own ``title``, else the pack's ``summarize``, else its base type's
        title, else its type and id. Every page, picker and notice names records this way."""
        if not rec:
            return ""
        rtype = rec.get("resourceType") or ""
        rt = self.record_type_for_reading(rtype) if rtype else None
        values = None
        if rt is not None and rt.classed_fields:  # a title travels (notices, pickers, the track): never from those
            from .visibility import strip
            rec = strip(rec, rt.classed_fields)

        def vals():
            out = {f["name"]: schema.first(rec, f["path"]) for f in rt.fields if "*" not in f["path"]}
            out.update(schema.project(rt, rec, self.member_types()))
            return out
        if rt is not None and rt.title:
            values = vals()
            text = schema.render_title(rt.title, values)
            if text:
                return text
        text = self.pack.summarize(rec) if self.pack else None
        if text:
            return text
        base = schema.bases().get(rt.extends or "") if rt is not None else None
        if base is not None and base.title:
            text = schema.render_title(base.title, values or vals())
            if text:
                return text
        return f"{rt.label if rt else rtype} {rec.get('id', '')}".strip()

    def facts(self, rec: dict) -> dict:
        """A subject's headline facts: the pack's ``facts``, else its type's ``fact = true`` fields, else the
        identifier and contact its base type has."""
        rt = self.record_type_for_reading(rec.get("resourceType") or "")
        if rt is not None and rt.classed_fields:
            from .visibility import strip
            rec = strip(rec, rt.classed_fields)
        got = self.pack.facts(rec) if self.pack else None
        if got is not None:
            return got
        if rt is None:
            return {}
        marked = [f for f in rt.fields if f.get("fact")]
        if marked:
            return {f["label"].lower(): schema.words(schema.first(rec, f["path"])) or None for f in marked}
        base = schema.bases().get(rt.extends or "")
        seen = schema.project(rt, rec, self.member_types())
        return {bf["label"].lower(): seen.get(bf["name"]) for bf in (base.fields if base else [])
                if bf["name"] in ("identifier", "contact")}

    def duplicates(self, rtype: str, rec: dict) -> list[dict]:
        """Subjects that may be this one already: the same identifier, or the same name and the same values in
        every ``match = true`` field both have. Accents and case aside (Côté is Cote)."""
        rt = self.record_type(rtype)
        if rt is None or not rt.extends:
            return []
        mine = schema.project(rt, rec, self.member_types())
        name, ident = _plain(mine.get("name")), _plain(mine.get("identifier"))
        if not name and not ident:
            return []
        matchers = [f for f in rt.fields if f.get("match")]
        out = []
        # by identifier, a text search finds them; by name, every one of the type is read (accents are spelled many
        # ways, so no text search finds Côté from cote). Registration is rare; the type's records are not many.
        for word in ([ident] if ident else []) + ([None] if name else []):
            for other in self.records.search(rtype, limit=None, text=word):
                if other.get("id") == rec.get("id") or any(o.get("id") == other.get("id") for o in out):
                    continue
                theirs = schema.project(rt, other, self.member_types())
                if ident and _plain(theirs.get("identifier")) == ident:
                    out.append(other)
                    continue
                if name and _plain(theirs.get("name")) == name and all(
                        schema.first(rec, f["path"]) in (None, "") or schema.first(other, f["path"]) in (None, "")
                        or _plain(schema.words(schema.first(rec, f["path"])))
                        == _plain(schema.words(schema.first(other, f["path"]))) for f in matchers):
                    out.append(other)
        return out

    def member_ref_type(self) -> str:
        """The record type a reference to a member names (``Practitioner/<id>``)."""
        kinds = list(self.pack.practice.get("member_types", []) if self.pack else []) or ["Member"]
        return kinds[0]

    def member_types(self) -> set[str]:
        return self.pack.member_types() if self.pack else {"Member"}

    def subject_of(self, rec: dict) -> str | None:
        """What a record is about: itself, for a subject type; else its ``rel = "subject"`` reference."""
        rtype = rec.get("resourceType")
        rt = self.record_type(rtype) if rtype else None
        subjects = self.subject_types()
        if rtype in subjects:
            return f"{rtype}/{rec['id']}" if rec.get("id") else None
        if rt is not None:
            for rel, ref in schema.links(rt, rec):
                if rel == "subject" and ref.split("/")[0] in subjects:
                    return ref
        from .records import subject_of
        return subject_of(rec, frozenset(subjects))

    def data_classes(self) -> list[dict]:
        rows = {r["id"]: r for r in self.db.data_classes.find(sort="name")}
        for dc in (self.pack.all_data_classes() if self.pack else []):
            rows.setdefault(dc["id"], {"id": dc["id"], "name": dc.get("name", dc["id"]),
                                       "description": dc.get("description", ""), "module": ""})
        return list(rows.values())

    def data_class_ids(self) -> set[str]:
        return {d["id"] for d in self.data_classes()}

    def role_ids(self) -> set[str]:
        return set(self.db.roles.distinct("id")) | (self.pack.role_ids() if self.pack else set())

    def check_type(self, rt: schema.RecordType) -> list[str]:
        problems = []
        classes = self.data_class_ids()
        if rt.data_class and rt.data_class not in classes:
            problems.append(f"data_class: there is no data class {rt.data_class!r} "
                            f"(data classes: {', '.join(sorted(classes))})")
        for f in rt.fields:
            if f.get("data_class") and f["data_class"] not in classes:
                problems.append(f"field {f['name']}: there is no data class {f['data_class']!r} "
                                f"(data classes: {', '.join(sorted(classes))})")
        roles = self.role_ids()
        for g in rt.signatures:
            for r in g["roles"]:
                if r not in roles:
                    problems.append(f"signature {g['meaning']!r}: there is no role {r!r}")
        known = self.type_ids() | {rt.id} | self.member_types() | set(schema.bases()) | {schema.ROOT}
        for f in rt.references:
            for t in f["target"]:
                if t != "*" and t not in known:
                    problems.append(f"field {f['name']}: there is no record type {t!r} to point at")
        base = schema.bases().get(rt.extends or "")
        for bname, m in (rt.base_map.items() if base else ()):
            bf = base.field(bname)
            if bf["type"] != "reference":
                continue
            for n in m.get("fields") or []:
                for t in (rt.field(n) or {}).get("target", []):
                    if t != "*" and t in known and not any(
                            (rt.is_a(x) if t == rt.id else self.is_a(t, x)) for x in bf["target"]):
                        problems.append(f"base.{bname}: {base.id}'s {bname} is a {' or '.join(bf['target'])}; "
                                        f"{t} does not extend it")
        return problems

    # ------------------------------------------------------------------ views
    def check_view(self, v: viewmod.View) -> list[str]:
        problems = []
        types, roles = self.type_ids(), self.role_ids()
        flows = set(self.db.workflow_versions.distinct("workflow_id", {"status": {"$in": ["active", "proposed"]}}))
        if self.pack:
            flows |= {wid for p in self.pack.all_parts() for wid in p.workflow_sources}
        for r in v.roles:
            if r not in roles:
                problems.append(f"roles: there is no role {r!r}")
        for i, w in enumerate(v.widgets):
            where = f"widget {w['title'] or i + 1}"
            if w["source"] == "records" and w["type"] and w["type"] not in types:
                problems.append(f"{where}: there is no record type {w['type']!r}")
            elif w["source"] == "records" and w["type"]:
                rt = self.record_type(w["type"])
                names = {f["name"] for f in rt.fields} | rt.top_keys() if rt else set()
                for c in [*w["columns"], *([{"path": w["group_by"]}] if w["group_by"] else [])]:
                    if rt and c["path"].split(".")[0] not in names:
                        problems.append(f"{where}: {rt.label} has no field {c['path']!r} (its fields: "
                                        f"{', '.join(f['name'] for f in rt.fields)})")
            if w["workflow"] and w["workflow"] not in flows:
                problems.append(f"{where}: there is no workflow {w['workflow']!r}")
            if w["role"] and w["role"] not in roles:
                problems.append(f"{where}: there is no role {w['role']!r}")
        return problems

    def view(self, vid: str, version: int | None = None) -> viewmod.View | None:
        return self._def_get("view", vid, version)

    def views_for(self, member: dict) -> list[viewmod.View]:
        """The views a member sees in the sidebar (records inside them are still filtered by what they may read)."""
        mine = set(member.get("roles") or [])
        out = []
        for vid in self._def_ids("view"):
            v = self.view(vid)
            if v and v.nav and (not v.roles or mine & set(v.roles)):
                out.append(v)
        return out

    # ------------------------------------------------------------------ skills
    def skills_now(self) -> dict:
        """The skills the practice runs now: the active version of each (a pack's skill until it is changed)."""
        out = {}
        for sid in self._def_ids("skill"):
            sk = self._def_get("skill", sid)
            if sk:
                out[sid] = sk
        if self.pack:
            for sid, sk in self.pack.all_skills().items():
                if sid not in out and not self._ever_defined("skill", sid):
                    out[sid] = sk
        return out

    def skill(self, sid: str, version: int | None = None):
        if version is not None:
            return self._def_get("skill", sid, version) or self.skills_now().get(sid)
        return self.skills_now().get(sid)

    def skill_version(self, sid: str) -> int | None:
        row = self._def_row("skill", sid)
        return row["version"] if row else None

    # ------------------------------------------------------------------ change control for types, views and skills
    def definitions(self, kind: str) -> list[dict]:
        """Every definition of a kind with an active or proposed version, for the catalog screens."""
        table, col, _ = DEFINITIONS[kind]
        out = []
        for did in self._def_ids(kind, ("active", "proposed")):
            row = self._def_row(kind, did)
            if not row:
                row = self.db[table].find_one({col: did, "status": "proposed"}, sort=[("version", DESC)])
                row = {**row, "version": None}
            pending = self.db[table].count({col: did, "status": "proposed"})
            try:
                df, problem = self._def_parse(kind, row["source"]), None
            except DefinitionError as exc:  # stored before a check that now refuses it: listed, with why
                df, problem = None, str(exc)
            out.append({"id": did, "row": row, "proposed": pending, "definition": df, "problem": problem})
        return out

    def definition_versions(self, kind: str, did: str) -> list[dict]:
        table, col, _ = DEFINITIONS[kind]
        return self.db[table].find({col: did}, sort=[("version", DESC)])

    def check_definition(self, kind: str, source: str):
        """Parse a definition and check it against this practice; raises DefinitionError with every problem."""
        if kind == "workflow":
            return self.check_workflow(source)
        if kind == "block":
            from . import blocks as blockmod
            return blockmod.parse(source, self.pack)
        return self._def_parse(kind, source, self)

    def with_module_additions(self, kind: str, source: str) -> str:
        """The practice's text for a definition with what modules added to the one in force put back after it;
        Invalid if the text adds, changes, removes or redefines a module's additions (the practice owns its own text
        only: a module's lines change with the module)."""
        from .hub import Invalid
        try:
            did = tomllib.loads(schema.own_text(source)).get("id")
        except (tomllib.TOMLDecodeError, AttributeError):
            did = None
        row = self.db[DEFINITIONS[kind][0]].find_one({DEFINITIONS[kind][1]: did, "status": "active"}) \
            if isinstance(did, str) else None
        out, problems = schema.keep_module_additions(source, row["source"] if row else None)
        if problems:
            raise Invalid(f"the {DEFINITIONS[kind][2]} would change what a module added", problems)
        return out

    def propose(self, kind: str, actor: dict, source: str, note: str = "") -> dict:
        """Propose a version of a record type, view or skill. Agents may propose them (they are data, never code);
        only a person who is a policy owner, and not the author, approves."""
        from .hub import Denied, Invalid
        if kind not in DEFINITIONS:
            raise Invalid(f"there is no kind of definition {kind!r}")
        if actor.get("status") != "active":
            raise Denied("an inactive member cannot propose changes")
        source = self.with_module_additions(kind, source)
        try:
            d = self.check_definition(kind, source)
        except DefinitionError as exc:
            raise Invalid(f"the {DEFINITIONS[kind][2]} has problems", exc.problems) from None
        except (TypeError, ValueError, AttributeError, KeyError, IndexError) as exc:  # a shape the parser missed
            raise Invalid(f"the {DEFINITIONS[kind][2]} has problems",
                          [f"it is not shaped as expected ({type(exc).__name__}: {exc})"]) from None
        return self._propose(kind, actor, d.id, source, note)

    def approve(self, actor: dict, kind: str, did: str, version: int, meaning: str = "") -> dict:
        from .hub import Invalid
        row = self._approvable(kind, actor, did, version)
        try:
            d = self.check_definition(kind, row["source"])
        except DefinitionError as exc:
            raise Invalid(f"the {DEFINITIONS[kind][2]} no longer validates", exc.problems) from None
        noun = DEFINITIONS[kind][2]
        title = getattr(d, "label", None) or getattr(d, "title", None) or did
        meaning = meaning or f"I approve version {version} of the {title} {noun}"
        with self.db:
            self._activate(kind, actor, did, version, row, meaning)
            self._defs_reset()
            broken = self._workflows_broken_by(kind, did)
            if broken:
                raise Invalid(f"approving this {noun} would break workflows the practice runs", broken)
        if kind == "type":  # a pack upgrade that waited for this type (records of a merged type become this one)
            self._upgrades_after(actor)
        return {f"{kind}_id": did, "version": version}

    def withdraw(self, actor: dict, kind: str, did: str, version: int, reason: str = "") -> None:
        self._withdraw(kind, actor, did, version, reason)

    def impact(self, kind: str, did: str, version: int) -> list[str]:
        """What approving this version changes, for the approver to read."""
        row = self._def_row(kind, did, version)
        if not row:
            return []
        new = self._def_parse(kind, row["source"])
        old = self._def_get(kind, did)
        notes = []
        if kind == "type":
            if old is None:
                notes.append(f"A new record type, {new.label}, in the {new.data_class} data class.")
            else:
                if old.data_class != new.data_class:
                    notes.append(f"Its records move from the {old.data_class} to the {new.data_class} data class: "
                                 f"who may read and write them changes.")
                before, after = {f["name"]: f for f in old.fields}, {f["name"]: f for f in new.fields}
                for n in sorted(set(after) - set(before)):
                    notes.append(f"New field {after[n]['label']}" + (" (required: existing records without it can no"
                                                                     " longer be saved until it is filled)"
                                                                     if after[n]["required"] else "") + ".")
                for n in sorted(set(before) - set(after)):
                    notes.append(f"Field {before[n]['label']} is removed" +
                                 (": records that hold it can no longer be saved" if new.strict else "") + ".")
                for n in sorted(set(before) & set(after)):
                    if after[n]["required"] and not before[n]["required"]:
                        notes.append(f"{after[n]['label']} becomes required.")
                    if before[n]["type"] != after[n]["type"]:
                        notes.append(f"{after[n]['label']} changes from {before[n]['type']} to {after[n]['type']}.")
                if (old.lifecycle or {}) != (new.lifecycle or {}):
                    notes.append("Its lifecycle changes: check which moves only a workflow may make.")
            n = self.db.records.count({"type": did})
            if n:
                bad = sum(1 for r in self.records.search(did, limit=None) if schema.validate(new, r))
                notes.append(f"{n} {new.plural.lower()} exist" + (f"; {bad} of them would not pass this version"
                                                                   if bad else ", and all of them pass this version") + ".")
        elif kind == "skill":
            if old is None or old.instructions != new.instructions:
                notes.append("This version changes what agents are told to do. Agents whose profile lists "
                             f"{did!r} carry it out.")
            if old is not None and [f["name"] for f in old.output] != [f["name"] for f in new.output]:
                notes.append("The shape of the answer changes: steps that read it may need updating.")
        elif kind == "view":
            if old is None:
                notes.append(f"A new screen, {new.title}" + (f", for {', '.join(new.roles)}" if new.roles else
                                                             ", for everyone") + ".")
        if old is not None:
            diff = list(difflib.unified_diff(old.source.splitlines(), new.source.splitlines(), lineterm="", n=0))
            changed = sum(1 for d in diff if d[:1] in "+-" and d[:3] not in ("+++", "---"))
            notes.append(f"{changed} line{'s' if changed != 1 else ''} change.")
        return notes

    def _workflows_broken_by(self, kind: str, did: str) -> list[str]:
        """Active workflows that no longer validate after a change to a skill, record type or block they depend on."""
        if kind not in ("skill", "type", "block"):
            return []
        problems = []
        for w in self.workflows():
            wf = w["workflow"]
            uses = ((kind == "skill" and any(s.skill == did for s in wf.steps)) or
                    (kind == "type" and wf.record == did) or
                    (kind == "block" and any(s.block == did for s in wf.steps)))
            if not uses:
                continue
            try:
                self.check_workflow(w["row"]["source"], approving=True)
            except DefinitionError as exc:
                problems += [f"{wf.title}: {p}" for p in exc.problems]
        return problems

    # ------------------------------------------------------------------ retire and roll back, for every kind
    def retire(self, actor: dict, kind: str, did: str, reason: str = "") -> dict:
        """Propose to stop using a definition. Like any change it takes two people: another policy owner approves it
        (``approve_retirement``) and only then does it stop. Runs already under way finish on their pinned versions.
        A block or skill that an active workflow uses, or a record type a workflow is about, stays until those
        workflows change."""
        from .db import utcnow
        from .hub import Denied, Invalid
        if actor.get("kind") != "person":
            raise Denied("a definition is retired by a person, never an agent")
        self.require(actor, "policy_owner", "practice:main", f"retire {kind}s")
        row = self._retirable(kind, did)
        if self.retire_request(kind, did):
            raise Invalid(f"retiring the {kind} {did} is already proposed: another policy owner approves or withdraws it")
        req = {"version": row["version"], "by": actor["id"], "reason": reason, "at": utcnow()}
        with self.db:
            self.db.set_setting(f"retire_request:{kind}:{did}", req)
            self.track.append(actor, f"{kind}.retire_proposed", f"{kind}:{did}",
                              data={"version": row["version"], "reason": reason})
        return {"kind": kind, "id": did, "proposed": True, **req}

    def retire_request(self, kind: str, did: str) -> dict | None:
        """A proposal to retire this definition, waiting for a second policy owner."""
        return self.db.get_setting(f"retire_request:{kind}:{did}")

    def approve_retirement(self, actor: dict, kind: str, did: str) -> None:
        """A second policy owner, not the one who proposed it, retires the definition."""
        from .hub import Denied, Invalid
        if actor.get("kind") != "person":
            raise Denied("a definition is retired by a person, never an agent")
        self.require(actor, "policy_owner", "practice:main", f"approve retiring {kind}s")
        req = self.retire_request(kind, did)
        if not req:
            raise Invalid(f"nobody has proposed retiring the {kind} {did}")
        if actor["id"] == req["by"]:
            raise Denied("a retirement is approved by a policy owner other than the one who proposed it")
        row = self._retirable(kind, did)
        if row["version"] != req["version"]:
            raise Invalid(f"the {kind} {did} changed since its retirement was proposed (version {req['version']}, now "
                          f"{row['version']}): propose it again")
        with self.db:
            self.db.del_setting(f"retire_request:{kind}:{did}")
            self._retire_now(actor, kind, did, req["reason"], proposed_by=req["by"])

    def withdraw_retirement(self, actor: dict, kind: str, did: str) -> None:
        """The proposer, or another policy owner, takes back a proposal to retire."""
        from .hub import Invalid
        req = self.retire_request(kind, did)
        if not req:
            raise Invalid(f"nobody has proposed retiring the {kind} {did}")
        if actor["id"] != req["by"]:
            self.require(actor, "policy_owner", "practice:main", f"withdraw retiring {kind}s")
        with self.db:
            self.db.del_setting(f"retire_request:{kind}:{did}")
            self.track.append(actor, f"{kind}.retire_withdrawn", f"{kind}:{did}", data={"version": req["version"]})

    def _retirable(self, kind: str, did: str) -> dict:
        """The active row of a definition nothing active still uses."""
        from .hub import Invalid, NotFound
        table, col = self.KINDS[kind]
        row = self.db[table].find_one({col: did, "status": "active"})
        if not row:
            raise NotFound(f"there is no active {kind} {did!r}")
        users = []
        for w in self.workflows():
            wf = w["workflow"]
            if wf.id == did and kind == "workflow":
                continue
            if ((kind == "block" and any(s.block == did for s in wf.steps)) or
                    (kind == "skill" and any(s.skill == did for s in wf.steps)) or
                    (kind == "type" and wf.record == did)):
                users.append(wf.title)
        if users:
            raise Invalid(f"active workflows still use it: {', '.join(users)}")
        return row

    def _retire_now(self, actor: dict, kind: str, did: str, reason: str = "", proposed_by: str | None = None) -> None:
        """Retire at once: after a second policy owner approved it, or when a module that brought it is removed or
        upgraded (the module change itself was reviewed by two people)."""
        table, col = self.KINDS[kind]
        row = self._retirable(kind, did)
        with self.db:
            self.db[table].update({col: did, "status": "active"}, {"$set": {"status": "retired"}}, many=True)
            self.db.del_setting(f"retire_request:{kind}:{did}")
            if kind == "workflow":
                self.authz.delete(self.authz.read(obj=f"workflow:{did}", relation="starter"))
            self._defs_reset()
            self.track.append(actor, f"{kind}.retired", f"{kind}:{did}",
                              data={"version": row["version"], "reason": reason,
                                    **({"proposed_by": proposed_by} if proposed_by else {})},
                              signed={kind: did, "version": row["version"], "meaning": f"I retire the {kind} {did}"})

    def roll_back(self, actor: dict, kind: str, did: str, version: int, note: str = "") -> dict:
        """Propose an earlier version again, as a new version: going back is a change like any other."""
        from .hub import NotFound
        table, col = self.KINDS[kind]
        row = self.db[table].find_one({col: did, "version": version})
        if not row:
            raise NotFound(f"there is no {kind} {did!r} version {version}")
        note = note or f"Back to version {version}"
        if kind == "workflow":
            return self.propose_workflow(actor, row["source"], note)
        if kind == "block":
            return self.propose_block(actor, row["source"], note)
        return self.propose(kind, actor, row["source"], note)
