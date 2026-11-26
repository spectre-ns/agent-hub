"""Practice packs: what makes a hub a general practice, a dental office, a law firm or any other practice.

A pack is a directory:

    pack.toml          practice defaults, version, roles, data classes and grants, events, calendar, seed members,
                       and ``modules = [...]``: overlay modules installed with it
    types/*.toml       record types: the object model (``practicehub/schema.py``)
    workflows/*.toml   the starting workflows (a practice changes them through change control)
    skills/*.toml      what agents know how to do: instructions and the shape of their answer
    blocks/*.toml      reusable, parameterised steps: python code, injected prompts, external engine calls
    views/*.toml       screens and dashboards (``practicehub/views.py``)
    pack.py            optional: deterministic actions, context providers, stand-in answers, summaries, and
                       ``UPGRADES``: what a new version does to the records an older one left (``Hub.run_pack_upgrades``)

A **custom overlay** is a practice pack that names another as its ``base = "<pack>"``: one client's practice built on a
shared pack. It gets the base's code and every definition, and replaces what it has itself: a ``[practice]`` key at a
time, any other top-level key (``modules``, ``roles``, ``members``...) whole, and a type, workflow, skill, block or view
by its id. It brings no pack.py of its own (the code that runs is the base's, reviewed with it), and a base is not
itself an overlay. So two clinics on general-practice can choose different modules.

A **module** (``modules/<id>/module.toml``: document control, quality, product lifecycle...) has the same layout with
``[module]`` in place of ``[practice]``. It adds roles, data classes, grants, events, types, workflows, blocks, skills
and views to whatever practice it is installed in; its workflow, block and view ids start with ``<module id>-``.

Everything here is a first version: once installed, the practice owns it and changes it through change control.
``practicehub pack plan`` shows how a newer pack differs from what the practice runs, and ``pack upgrade`` turns the
differences into proposals for a policy owner to approve.

The core knows nothing about medicine; everything clinical is in the general-practice pack.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .fields import check_spec, normalize

PACKS_DIR = Path(os.environ.get("PRACTICEHUB_PACKS") or Path(__file__).resolve().parent.parent / "packs")
MODULES_DIR = Path(os.environ.get("PRACTICEHUB_MODULES") or Path(__file__).resolve().parent.parent / "modules")


@dataclass
class Skill:
    id: str
    title: str
    instructions: str
    output: list[dict]
    source: str = ""


@dataclass
class Pack:
    id: str
    dir: Path
    practice: dict
    roles: list[dict]
    data_classes: list[dict]
    grants: dict
    members: list[dict]
    calendar: dict
    skills: dict[str, Skill]
    workflow_sources: dict[str, str]
    record_labels: dict[str, str]
    module: object | None = None
    subject: dict = field(default_factory=dict)
    actions: dict = field(default_factory=dict)
    providers: dict = field(default_factory=dict)
    standins: dict = field(default_factory=dict)
    guards: dict = field(default_factory=dict)
    upgrades: dict = field(default_factory=dict)   # id -> fn(hub, actor): None until it can run, then what it did
    block_sources: dict = field(default_factory=dict)
    type_sources: dict = field(default_factory=dict)
    view_sources: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    version: str = "0"
    kind: str = "practice"           # or "module"
    modules: list = field(default_factory=list)   # overlay Packs (kind = "module") installed with this one
    module_sha256: str = ""          # of pack.py: written to the track at install and upgrade
    digest: str = ""                 # of every file in the module: what a second policy owner reviews
    skills_fn: object = None         # set by the hub: the skills the practice runs now (under change control)
    types_fn: object = None          # set by the hub: the record types the practice has now
    requires: list = field(default_factory=list)  # modules this one builds on (a CAPA points at a controlled Document)
    extensions: dict = field(default_factory=dict)  # (kind, id) -> TOML that adds to another part's type or view
    portal: dict = field(default_factory=dict)      # [portal]: the online booking portal (``practicehub/portal.py``)

    @property
    def name(self) -> str:
        return str(self.dir.relative_to(PACKS_DIR)) if self.dir.is_relative_to(PACKS_DIR) else str(self.dir)

    def all_parts(self) -> list["Pack"]:
        return [self, *self.modules]

    # --- lookups, across the practice and its modules
    def role_ids(self) -> set[str]:
        return {r["id"] for p in self.all_parts() for r in p.roles}

    def all_data_classes(self) -> list[dict]:
        return [dc for p in self.all_parts() for dc in p.data_classes]

    def class_of(self, rtype: str) -> str | None:
        for dc in self.all_data_classes():
            if rtype in dc.get("record_types", []):
                return dc["id"]
        return None

    def record_types(self) -> list[str]:
        return [t for dc in self.all_data_classes() for t in dc.get("record_types", [])]

    def all_skills(self) -> dict:
        return {k: v for p in self.all_parts() for k, v in p.skills.items()}

    def member_types(self) -> set[str]:
        """Record types whose references name a member of the practice (``Practitioner/<member id>``)."""
        return set(self.practice.get("member_types", ["Member"])) | {"Member"}

    # --- the subject: who or what the practice's work is about (a patient, a client, a matter)
    @property
    def subject_type(self) -> str:
        return self.subject["type"]

    def facts(self, rec: dict, today=None) -> dict | None:
        """The headline facts on a subject's page, when the pack's code says (``facts``); else None, and the core
        shows the type's ``fact = true`` fields. ``today`` (the practice's date) goes to a ``facts`` that takes it."""
        fn = getattr(self.module, "facts", None) if self.module else None
        if fn:
            try:
                import inspect
                takes = "today" in inspect.signature(fn).parameters
                return fn(rec, today=today) if takes else fn(rec)
            except Exception:  # cosmetic, like summaries
                pass
        return None

    def summarize(self, rec: dict) -> str | None:
        """A record's name, when a module's or the practice's code gives one (``summarize``); else None, and the
        core names it from its type's ``title``."""
        for part in [*self.modules, self]:  # a module summarizes its own types; the practice the rest
            fn = getattr(part.module, "summarize", None) if part.module else None
            if fn:
                try:
                    text = fn(rec)
                    if text:
                        return text
                except Exception:  # a summary is cosmetic; never let it break a page
                    pass
        return None

    def check_workflow(self, wf) -> list[str]:
        """Pack-level checks of a parsed workflow (roles, skills, actions, data classes exist)."""
        from .engine import CORE_ACTIONS, CORE_PROVIDERS

        problems = []
        roles = self.role_ids()
        classes = {dc["id"] for dc in self.all_data_classes()}
        skills = self.skills_fn() if self.skills_fn else self.all_skills()
        types = self.types_fn() if self.types_fn else set(self.record_types())
        if wf.record and wf.record not in types:
            problems.append(f"record: {wf.record!r} is not a record type of this practice")
        for r in wf.starters:
            if r not in roles:
                problems.append(f"trigger.starters: there is no role {r!r}")
        if getattr(wf, "deadline_escalate_to", None) and wf.deadline_escalate_to not in roles:
            problems.append(f"deadline: there is no role {wf.deadline_escalate_to!r} (roles: {', '.join(sorted(roles))})")
        for r in getattr(wf, "cancel_by", None) or []:
            if r not in roles:
                problems.append(f"cancel_by: there is no role {r!r} (roles: {', '.join(sorted(roles))})")
        for s in getattr(wf, "on_cancel", None) or []:
            if s.action and s.action not in CORE_ACTIONS and s.action not in self.actions:
                known = sorted(set(CORE_ACTIONS) | set(self.actions))
                problems.append(f"on_cancel {s.id}: there is no action {s.action!r} (actions: {', '.join(known)})")
        for s in wf.steps:
            if s.role and s.role not in roles:
                problems.append(f"step {s.id}: there is no role {s.role!r} (roles: {', '.join(sorted(roles))})")
            for r in (s.escalate_to, s.people_role):
                if r and r not in roles:
                    problems.append(f"step {s.id}: there is no role {r!r} (roles: {', '.join(sorted(roles))})")
            if s.skill and s.skill not in skills:
                problems.append(f"step {s.id}: there is no skill {s.skill!r}")
            if s.action and s.action not in CORE_ACTIONS and s.action not in self.actions:
                known = sorted(set(CORE_ACTIONS) | set(self.actions))
                problems.append(f"step {s.id}: there is no action {s.action!r} (actions: {', '.join(known)})")
            for c in s.context:
                if c.startswith("record:"):
                    if c[7:] not in classes:
                        problems.append(f"step {s.id}: context {c!r}: there is no data class {c[7:]!r}")
                elif c.startswith("provider:"):
                    name = c[9:]
                    if name not in CORE_PROVIDERS and name not in self.providers:
                        problems.append(f"step {s.id}: context {c!r}: there is no provider {name!r}")
        return problems


SKILL_ID = re.compile(r"[a-z][a-z0-9\-]*")


def parse_skill(source: str) -> Skill:
    """A skill: what an agent is told to do, and the shape of its answer (``kind = "skill"`` under change control)."""
    from .workflows import DefinitionError

    try:
        s = tomllib.loads(source)
    except tomllib.TOMLDecodeError as exc:
        raise DefinitionError([f"The skill is not valid TOML: {exc}"]) from None
    problems = []
    sid = str(s.get("id", ""))
    if not SKILL_ID.fullmatch(sid) or sid == "new":
        problems.append("id: give the skill an id of lower-case letters, digits and dashes")
    if not str(s.get("instructions", "")).strip():
        problems.append("instructions: say what the agent is to do")
    problems += [q for spec in s.get("output", []) for q in check_spec(spec)]
    if problems:
        raise DefinitionError(problems)
    return Skill(id=sid, title=str(s.get("title", sid)), instructions=str(s.get("instructions", "")).strip(),
                 output=[normalize(f) for f in s.get("output", [])], source=source)


def _load_module(pack_dir: Path, pack_id: str, code: bytes | None = None):
    """Run a pack's pack.py. ``code``: the bytes that were hashed, so what runs is exactly what was checked."""
    path = pack_dir / "pack.py"
    if code is None:
        if not path.exists():
            return None
        code = path.read_bytes()
    spec = importlib.util.spec_from_file_location(f"practicehub_pack_{pack_id.replace('-', '_')}", path)
    mod = importlib.util.module_from_spec(spec)
    exec(compile(code, str(path), "exec"), mod.__dict__)
    return mod


def _subject(s: dict, kind: str = "practice") -> dict:
    """Who the practice's work is about. The pack says so (``[subject] type = "Patient"``, ``"Client"``): the core
    assumes no profession."""
    t = s.get("type")
    if not t and kind == "module":
        return {}  # a module adds to a practice; the practice says who its work is about
    if not t:
        raise ValueError("pack.toml: [subject] type is required (the type, extending the core's Subject, that "
                         "the practice's work is about)")
    label = s.get("label", t)
    return {"type": t, "label": label, "plural": s.get("plural", label + "s"),
            "path": s.get("path", (label + "s").lower().replace(" ", "-")),
            "registered_event": s.get("registered_event", f"{t.lower()}.registered"),
            "register_label": s.get("register_label", f"Start {label.lower()} intake")}


def _sources(d: Path) -> dict[str, str]:
    out = {}
    for p in sorted(d.glob("*.toml")) if d.is_dir() else []:
        src = p.read_text()
        out[str(tomllib.loads(src)["id"])] = src
    return out


class ModuleChanged(ValueError):
    """A module's pack.py is not the code that was installed: it is not run until someone upgrades to it."""


EXTENDS = {"types": "type", "views": "view", "workflows": "workflow"}


def _extensions(d: Path) -> dict:
    """``extends/types/<Type>.toml``, ``extends/views/<view>.toml`` and ``extends/workflows/<id>.toml``: fields,
    signatures, lifecycle moves, columns, widgets, or a workflow's states and edges (``[[state]]``, ``[[edge]]``; work
    on a state it has, ``[[state]] state = "<id>"`` with ``[[state.work]]``; automations on an edge it has,
    ``[[edge]] edge = "<id>"`` with ``[[edge.do]]``: ``practicehub/states.py``) a module adds to a definition another
    part of the practice owns. A practice changes or removes what others own through change control. Only additions:
    every value is a list of tables (``[[field]]``, ``[[lifecycle.transition]]``), appended to what the practice has."""
    out = {}
    for sub, kind in EXTENDS.items():
        for p in sorted((d / "extends" / sub).glob("*.toml")) if (d / "extends" / sub).is_dir() else []:
            src = p.read_text()
            doc = tomllib.loads(src)
            def additions(v):
                return isinstance(v, list) and all(isinstance(x, dict) for x in v)
            bad = [k for k, v in doc.items()
                   if not (additions(v) or (isinstance(v, dict) and v and all(additions(x) for x in v.values())))]
            if kind == "workflow":
                bad += [k for k in doc if k not in ("state", "edge") and k not in bad]
            if bad:
                raise ValueError(f"extends/{sub}/{p.name}: only adds lists of tables ([[field]], [[state]], "
                                 f"[[edge]], [[lifecycle.transition]]...), not {', '.join(bad)}")
            out[(kind, p.stem)] = src
    return out


def _merged_ext(layers: list[Path]) -> dict:
    return {k: v for d in layers for k, v in _extensions(d).items()}


def find(name_or_path: str, kind: str = "practice") -> Path:
    p = Path(name_or_path)
    marker = "pack.toml" if kind == "practice" else "module.toml"
    for d in (p, (PACKS_DIR if kind == "practice" else MODULES_DIR) / name_or_path):
        if (d / marker).exists():
            return d.resolve()
    raise FileNotFoundError(f"no {kind} {'pack' if kind == 'practice' else ''} at {name_or_path}".replace("  ", " "))


def load_module(name_or_path: str, expect_sha256: str | None = None, *, run_code: bool = True,
                expect_digest: str | None = None) -> Pack:
    """An overlay module (document control, quality, product lifecycle...): a pack with ``[module]`` instead of
    ``[practice]``. ``expect_sha256``: the pack.py that was installed; other code is refused before it runs.
    ``expect_digest``: every file as it was reviewed and installed (code and definitions); a module whose files differ
    is refused before its code runs. ``run_code=False`` reads its definitions only (to list or plan it), without
    running its pack.py."""
    if expect_digest:
        pack_dir = find(name_or_path, "module")
        now = digest(pack_dir)
        if now != expect_digest:
            load(name_or_path, kind="module", run_code=False)  # a file that does not read is named as such first
            raise ModuleChanged(f"the files of the {pack_dir.name} module are not the ones that were reviewed and "
                                f"installed (it was {expect_digest[:12]}, it is {now[:12]} now): it is not run until a "
                                f"policy owner reviews and upgrades it")
    m = load(name_or_path, kind="module", expect_sha256=expect_sha256, run_code=run_code)
    if expect_digest and m.digest != expect_digest:  # changed between the check and the read
        raise ModuleChanged(f"the files of the {m.id} module changed while it was being loaded: it is not run until "
                            f"a policy owner reviews and upgrades it")
    return m


def _layers(pack_dir: Path, kind: str) -> list[Path]:
    """The directories a pack is read from, base first: a custom overlay is ``[base, overlay]``."""
    if kind != "practice":
        return [pack_dir]
    base = tomllib.loads((pack_dir / "pack.toml").read_text()).get("base")
    if not base:
        return [pack_dir]
    if not isinstance(base, str):
        raise ValueError(f"the practice {pack_dir.name}: base names one pack")
    base_dir = find(base)
    if base_dir == pack_dir:
        raise ValueError(f"the practice {pack_dir.name}: base names itself")
    if tomllib.loads((base_dir / "pack.toml").read_text()).get("base"):
        raise ValueError(f"the practice {pack_dir.name}: its base {base} is a custom overlay itself; build on the "
                         f"pack it is built on")
    if (pack_dir / "pack.py").exists():
        raise ValueError(f"the practice {pack_dir.name}: a custom overlay brings no pack.py; the code that runs is "
                         f"its base's ({base})")
    return [base_dir, pack_dir]


def _overlaid(docs: list[dict]) -> dict:
    """The base's pack.toml with the overlay's on top: [practice] key by key, any other key whole."""
    out = dict(docs[0])
    for d in docs[1:]:
        for k, v in d.items():
            if k == "practice":
                out["practice"] = {**out.get("practice", {}), **v}
            elif k != "base":
                out[k] = v
    out.pop("base", None)
    return out


def digest(pack_dir: Path) -> str:
    """One hash over every file of a pack (its code and its definitions), so a review covers all of it; a custom
    overlay's covers its base's files too, so a change to either is reviewed."""
    layers = _layers(pack_dir, "practice") if (pack_dir / "pack.toml").exists() else [pack_dir]
    if len(layers) > 1:
        h = hashlib.sha256()
        for d in layers:
            h.update(_files_digest(d).encode() + b"\0")
        return h.hexdigest()
    return _files_digest(pack_dir)


def _files_digest(pack_dir: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(pack_dir.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            h.update(p.relative_to(pack_dir).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def load(name_or_path: str, kind: str = "practice", expect_sha256: str | None = None, *, run_modules: bool | None = None,
         run_code: bool = True) -> Pack:
    pack_dir = find(name_or_path, kind)
    try:
        return _load(pack_dir, kind, expect_sha256, run_code, run_modules)
    except (KeyError, TypeError, AttributeError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        # a file without an id, or not TOML at all: named, so the practice can say which module is broken
        raise ValueError(f"the {kind} {pack_dir.name} has a file that does not read: {type(exc).__name__} {exc}") \
            from None


def _load(pack_dir: Path, kind: str, expect_sha256: str | None, run_code: bool,
          run_modules: bool | None = None) -> Pack:
    layers = _layers(pack_dir, kind)
    code_dir = layers[0]  # a custom overlay runs its base's code
    code = (code_dir / "pack.py").read_bytes() if (code_dir / "pack.py").exists() else None
    mod_sha = hashlib.sha256(code).hexdigest() if code is not None else ""
    if expect_sha256 is not None and mod_sha != expect_sha256:
        raise ModuleChanged(f"{pack_dir.name}/pack.py is not the code that was installed (it was "
                            f"{expect_sha256[:12] or 'absent'}, it is {mod_sha[:12] or 'absent'} now): it is not run "
                            f"until a policy owner upgrades the module")
    doc = _overlaid([tomllib.loads((d / ("pack.toml" if kind == "practice" else "module.toml")).read_text())
                     for d in layers])
    if kind == "module":
        doc["practice"] = doc.pop("module", {})
    skills = {}
    for p in [p for d in layers for p in sorted((d / "skills").glob("*.toml"))]:
        try:
            sk = parse_skill(p.read_text())
        except ValueError as exc:
            raise ValueError(f"skill {p.name}: {exc}") from None
        skills[sk.id] = sk
    workflows = {}
    for p in [p for d in layers for p in sorted((d / "workflows").glob("*.toml"))]:
        workflows[tomllib.loads(p.read_text())["id"]] = p.read_text()
    blocks = {}
    for p in [p for d in layers for p in sorted((d / "blocks").glob("*.toml"))]:
        blocks[tomllib.loads(p.read_text())["id"]] = p.read_text()
    pack_id = doc["practice"].get("id") if kind == "module" else doc["practice"].get("kind", code_dir.name)
    pack_id = pack_id or pack_dir.name
    from .contract import check_requires
    check_requires(f"the {kind} {pack_dir.name}", doc["practice"].get("requires_core"))
    if kind == "practice":  # its "now" and "today" (``clock``): a pack that does not say would date notes in UTC
        from .clock import zone
        if not str(doc["practice"].get("timezone") or "").strip():
            raise ValueError(f"the practice {pack_dir.name}: [practice] timezone: say the practice's time zone "
                             "(a name like America/Halifax)")
        zone(doc["practice"]["timezone"])
    mod = _load_module(code_dir, pack_id, code) if run_code else None
    pack = Pack(id=pack_id, dir=pack_dir, practice=doc["practice"], roles=doc.get("roles", []),
                data_classes=doc.get("data_classes", []), grants=doc.get("grants", {}), members=doc.get("members", []),
                calendar=doc.get("calendar", {}), skills=skills, workflow_sources=workflows,
                record_labels=doc.get("record_labels", {}), module=mod, subject=_subject(doc.get("subject", {}), kind),
                block_sources=blocks, type_sources={k: v for d in layers for k, v in _sources(d / "types").items()},
                view_sources={k: v for d in layers for k, v in _sources(d / "views").items()},
                events=list(doc.get("events", [])), version=str(doc.get("version") or doc["practice"].get("version", "0")),
                kind=kind, module_sha256=mod_sha, digest=digest(pack_dir), requires=list(doc["practice"].get("requires", []) or []),
                extensions=_merged_ext(layers), portal=dict(doc.get("portal") or {}))
    if any(r.get("id") == "everyone" for r in pack.roles):
        raise ValueError("roles: 'everyone' is reserved: [grants.everyone] gives every member read access")
    if set(pack.grants.get("everyone", {})) - {"read"}:
        raise ValueError("grants.everyone: every member may be given read access only")
    if kind == "module":
        if pack_id != pack_dir.name:
            raise ValueError(f"the module in {pack_dir.name}/ calls itself {pack_id}: a module's id is its directory's "
                             f"name, so the practice finds it again when it starts")
        prefix = pack_id + "-"
        for what, ids in (("workflow", workflows), ("block", blocks), ("view", pack.view_sources)):
            bad = [i for i in ids if not i.startswith(prefix)]
            if bad:
                raise ValueError(f"module {pack_id}: {what} ids start with {prefix!r}: {', '.join(bad)}")
    else:
        # ``run_modules=False``: the modules it lists are read, not run; the hub runs each one's code only if it is
        # the code that was signed in
        pack.modules = [load_module(m, run_code=run_code if run_modules is None else run_modules and run_code)
                        for m in doc.get("modules", [])]
    if mod is not None:
        pack.actions = dict(getattr(mod, "ACTIONS", {}))
        pack.providers = dict(getattr(mod, "PROVIDERS", {}))
        pack.standins = dict(getattr(mod, "STANDINS", {}))
        pack.guards = dict(getattr(mod, "GUARDS", {}))
        pack.upgrades = dict(getattr(mod, "UPGRADES", {}))
    for m in pack.modules:
        add_module(pack, m)
    if kind == "practice":  # [portal] names roles, workflows and members of the practice and its modules: checked
        from .portal import check_config  # once they are all read, with every problem named at once
        problems = check_config(pack)
        if problems:
            raise ValueError(f"the practice {pack_dir.name}: [portal]: " + "; ".join(problems))
    return pack


def add_module(pack: Pack, m: Pack) -> None:
    """Merge an overlay module's code (actions, providers, guards, stand-ins) into the practice. Its definitions stay
    in the module; lookups span every part. A module may not replace what the practice or another module defines."""
    have = {x.id for x in pack.modules if x is not m}
    missing = [r for r in m.requires if r not in have]
    if missing:
        raise ValueError(f"module {m.id} builds on {', '.join(missing)}: install {'it' if len(missing) == 1 else 'them'}"
                         f" first")
    # check everything before changing anything, so a refused module leaves the practice as it was
    for what in ("actions", "providers", "standins", "guards"):
        mine, theirs = getattr(pack, what), getattr(m, what)
        clash = sorted(k for k in set(mine) & set(theirs) if mine[k] is not theirs[k])
        if clash:
            raise ValueError(f"module {m.id}: {what} {', '.join(clash)} already exist in this practice")
    ids = {r["id"] for p in pack.all_parts() if p is not m for r in p.roles}
    clash = sorted(ids & {r["id"] for r in m.roles})
    if clash:
        raise ValueError(f"module {m.id}: roles {', '.join(clash)} already exist in this practice")
    if m not in pack.modules:
        pack.modules.append(m)
    for what in ("actions", "providers", "standins", "guards"):
        getattr(pack, what).update(getattr(m, what))


def forked(pack: Pack) -> Pack:
    """A copy of the practice whose module list and code tables are its own: changed off to the side and then put in
    place with one assignment, so a request running in another thread sees the old modules or the new, never half."""
    import copy
    new = copy.copy(pack)
    new.modules = list(pack.modules)
    for what in ("actions", "providers", "standins", "guards"):
        setattr(new, what, dict(getattr(pack, what)))
    return new


def remove_module(pack: Pack, m: Pack) -> None:
    """Undo ``add_module`` (an install that failed part way, or a module taken out)."""
    if m in pack.modules:
        pack.modules.remove(m)
    for what in ("actions", "providers", "standins", "guards"):
        mine = getattr(pack, what)
        for k, v in getattr(m, what).items():
            if mine.get(k) is v:
                del mine[k]
