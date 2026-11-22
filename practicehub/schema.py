"""Record types: the object model, as data.

A record type is a TOML document, versioned and approved like a workflow (change control ``kind = "type"``). A pack
or module ships the first versions in ``types/*.toml``; a practice changes them from the UI (``/types``). Every
record the hub writes is checked against its type, whoever writes it: a person's form, an automation, an agent's
answer that becomes a record, an engine calling the API.

    id = "Observation"            # the record type (FHIR resource names, or a module's own: Document, CAPA, Part)
    label = "Result"
    plural = "Results"
    data_class = "clinical"       # who may read and write it (grants are per data class)
    subject = false               # true: work is about records of this type (Patient, Client, Matter)
    strict = true                 # refuse top-level keys no field declares
    date = "effectiveDateTime"    # the path that dates a record (charts and lists sort by it, newest first)
    flag = "interpretation.0.text"  # a path whose value is shown as a flag (abnormal, high, overdue...)
    title = "{name}[ (born {birthDate})]"   # how a record is named: field (or base field) values; [...] is optional
    parent = "parent"             # a reference field to the same type: its records form a tree (pages under pages, folders)
    part_of = "subject"           # a reference field (or base field) naming the one record that owns it: an allergy is
                                  #   part of its patient, a visit note part of its visit. It is reached only through its
                                  #   owner (no list or create of its own). A record that merely refers to another (a
                                  #   visit to its patient) links to it instead: ``rel`` on the reference field.
    [navigator]                   # with a tree: a second navigation column, its tops (spaces) and the open one's tree
    tops = "Spaces"               #   what the records at the top of the tree are called
    hide = ["archived"]           #   lifecycle states left out of it (a record in one still shows when it is open)
    standard = "fhir-r4"          # its records are resources of a standard (FHIR R4): exported as such (practicehub/export.py)
    layout = "page"               # "record" (the default) or "page": its formatted text is the page, read first, with
                                  #   the other fields and the controls in a bar above it, and edited in place
    alert = false                 # show active records of this type in a banner on the subject's page
    active = "record.status != 'inactive'"   # which records are current (a condition over ``record``)
    order = 10                    # where it sits on a subject's page

    [[field]]
    name = "status"               # the key in the record, unless ``path`` says where (``code.text``, ``name.0.family``)
    type = "choice"               # string text markdown number boolean date datetime choice member reference attachment json object list
    choices = ["preliminary", "final", "amended"]
    required = true

    [[field]]
    name = "subject"
    type = "reference"            # {"reference": "Patient/<id>"} or "Patient/<id>"
    target = ["Patient"]
    rel = "subject"               # the link written for it; ``subject`` makes it what the record is about
    # fact = true                 # shown among a subject's headline facts
    # match = true                # with the name, how a second registration of the same subject is noticed
    # data_class = "clinical"     # a field of its own data class: only who may read that class sees it (on every
                                  #   page, list, export and history), only who may write it sets it. The type's class
                                  #   still says who sees the record exists, lists it and creates it.

    [lifecycle]                   # optional: the states a record moves through
    field = "status"
    initial = "preliminary"
    [[lifecycle.transition]]
    from = ["preliminary"]
    to = "final"
    by = "workflow"               # "anyone" who may write the class (default), or only a "workflow" step

A path may use ``*`` for every item of a list (``participant.*.actor``).

Base types. The core ships abstract record types (``practicehub/base/*.toml``: ``Subject``, ``Booking``) that say what
the core and its modules need from a record, and nothing about any practice. A pack's type extends one:

    id = "Appointment"
    extends = "Booking"           # the core's calendar, free slots and booking.book now handle it
    [base]                        # which of its fields holds each of the base type's fields
    reason = "description"        # its own field (same name: nothing to say; absent: the base field is inherited)
    subject = "patient"           # a reference to a type that extends the base's target (Patient extends Subject)
    with = "participants"         # references to member types give the members' ids
    cancelled = { when = "record.status in ['cancelled', 'noshow']" }   # a condition, for a true/false field
    # name = ["given", "family"]  # several fields: their words, in order (read only)
    # contact = false             # an optional base field it does not keep

The core reads such a record only through ``project`` (the base fields, as the base declares them) and writes one
through ``compose``. That is the contract between core and pack (``contract.py`` exports it as Avro schemas).
"""

from __future__ import annotations

import difflib
import re
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import expr
from .workflows import DefinitionError

TYPE_ID = re.compile(r"[A-Za-z][A-Za-z0-9\-]*")
FIELD_TYPES = {"string", "text", "markdown", "number", "boolean", "date", "datetime", "choice", "member", "reference",
               "attachment", "json", "object", "list"}
RESERVED = {"resourceType", "id", "meta"}
BY = ("anyone", "workflow")
OPS = ("create", "edit", "view")   # what a screen is shown for (a transition names its own screen)
SCREEN_ID = re.compile(r"[a-z][a-z0-9-]*")
BASE_DIR = Path(__file__).parent / "base"
ROOT = "Record"  # every record type is a Record
FIELD_PATH = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)*")  # keys only: what a store can range over


class SchemaError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class RecordType:
    id: str
    label: str
    plural: str
    data_class: str
    fields: list[dict] = field(default_factory=list)
    subject: bool = False
    strict: bool = True
    date: str | None = None
    flag: str | None = None
    alert: bool = False
    active: str | None = None
    order: int = 100
    lifecycle: dict | None = None
    summary: str = ""
    source: str = ""
    signatures: list[dict] = field(default_factory=list)  # [{meaning, roles, states}]: who signs it, and for what
    abstract: bool = False   # a core base type: records are never of it, types extend it
    extends: str | None = None   # the core base type it extends
    base_map: dict = field(default_factory=dict)   # base field -> {"fields": [own field names]} or {"when": expr}
    title: str | None = None  # how a record of it is named: "{name}", "{start}[ {reason}]"
    screens: dict = field(default_factory=dict)  # id -> {"id", "on": [ops], "tabs": [{"label", "fields": [names]}]}
    parent: str | None = None  # the path of a reference to its own type: its records form a tree
    part_of: dict | None = None  # {"field", "path", "target"}: the reference to the one record that owns it
    navigator: dict | None = None  # {"tops": words, "hide": [states]}: a second navigation column for the tree
    layout: str = "record"   # "page": its formatted text is the page, with its fields and controls in a bar above
    standard: str | None = None  # "fhir-r4": its records are FHIR R4 resources (exported as FHIR); None: its own shape

    def screen(self, op: str) -> list[dict] | None:
        """The tabs of the screen shown to create, edit or view a record (None: no screen says, so every field)."""
        sc = next((s for s in self.screens.values() if op in s["on"]), None)
        return sc["tabs"] if sc else None

    def is_a(self, base: str) -> bool:
        """Whether its records may stand where ``base`` is asked for (itself, the base it extends, or any record)."""
        return base in ("*", ROOT) or base == self.id or base == self.extends

    def signature(self, meaning: str) -> dict | None:
        return next((g for g in self.signatures if g["meaning"] == meaning), None)

    def field(self, name: str) -> dict | None:
        return next((f for f in self.fields if f["name"] == name), None)

    def field_class(self, f: dict) -> str:
        """The data class that guards a field's value: its own, else the type's."""
        return f.get("data_class") or self.data_class

    @property
    def classed_fields(self) -> list[dict]:
        """The fields whose own data class differs from the type's: those some readers of the record do not see."""
        return [f for f in self.fields if f.get("data_class") and f["data_class"] != self.data_class]

    @property
    def references(self) -> list[dict]:
        return [f for f in self.fields if f["type"] == "reference"]

    def top_keys(self) -> set[str]:
        return {f["path"].split(".")[0] for f in self.fields} | RESERVED

    def states(self) -> list[str]:
        return list((self.lifecycle or {}).get("states") or [])

    def transition(self, old, new) -> dict | None:
        for t in (self.lifecycle or {}).get("transition", []):
            if new == t["to"] and (old in t["from"] or "*" in t["from"]):
                return t
        return None


# Every key each table of a record type may hold: anything else is a typo, said so rather than quietly ignored.
KEYS = {
    "": {"id", "label", "plural", "data_class", "subject", "strict", "date", "flag", "alert", "active", "order",
         "field", "lifecycle", "summary", "signature", "abstract", "extends", "base", "title", "screen", "parent",
         "part_of",
         "navigator", "layout", "standard"},
    "field": {"name", "path", "type", "label", "required", "help", "target", "rel", "choices", "max_length", "min",
              "max", "list", "show", "advanced", "with", "fact", "match", "by", "live", "data_class"},
    "lifecycle": {"field", "states", "initial", "transition", "locked", "abandoned", "in_use"},
    "transition": {"from", "to", "by", "label", "screen", "fields", "require", "comment"},
    "signature": {"meaning", "roles", "states", "moves_to", "readers"},
    "screen": {"id", "on", "tab", "fields", "label"},
    "tab": {"label", "fields"},
    "navigator": {"tops", "hide"},
}
FIELD_BOOLS = ("required", "list", "show", "advanced", "fact", "match", "live")


def _unknown(where: str, table: dict, kind: str) -> list[str]:
    """``where: unknown key "requried" (did you mean "required"?)`` for each key the table may not hold."""
    out = []
    for k in table:
        if k not in KEYS[kind]:
            near = difflib.get_close_matches(str(k), sorted(KEYS[kind]), n=1)
            out.append(f'{where}: unknown key "{k}"' + (f' (did you mean "{near[0]}"?)' if near else ""))
    return out


def _strings(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def default_label(name: str) -> str:
    """A label from a field name: ``serviceProvider`` and ``service_provider`` are both "Service provider"."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z][a-z])", " ", name).replace("_", " ").strip().capitalize()


def _spec(raw, i: int) -> tuple[dict | None, list[str]]:
    if not isinstance(raw, dict):
        return None, [f"field {i + 1}: a field is a table ([[field]] with name = ..., type = ...)"]
    name = str(raw.get("name", ""))
    where = f"field {name or i + 1}"
    problems = _unknown(where, raw, "field")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        problems.append(f"{where}: a name is letters, digits and underscores")
    typ = raw.get("type", "string")
    if typ not in FIELD_TYPES:
        problems.append(f"{where}: unknown type {typ!r} (one of {', '.join(sorted(FIELD_TYPES))})")
    for k in FIELD_BOOLS:
        if k in raw and not isinstance(raw[k], bool):
            problems.append(f"{where}: {k} is true or false, not {raw[k]!r}")
    for k in ("label", "help", "rel", "data_class"):
        if k in raw and not isinstance(raw[k], str):
            problems.append(f"{where}: {k} is text, not {raw[k]!r}")
    ml = raw.get("max_length")
    if ml is not None and (isinstance(ml, bool) or not isinstance(ml, int) or ml < 1):
        problems.append(f"{where}: max_length is a whole number of characters, not {ml!r}")
    for k in ("min", "max"):
        v = raw.get(k)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            problems.append(f"{where}: {k} is a number, not {v!r}")
        elif typ != "number":
            problems.append(f"{where}: {k} is for a number field")
    if isinstance(raw.get("min"), (int, float)) and isinstance(raw.get("max"), (int, float)) \
            and raw["min"] > raw["max"]:
        problems.append(f"{where}: min {raw['min']} is above max {raw['max']}")
    choices = raw.get("choices")
    if choices is not None:
        if not _strings(choices):
            problems.append(f"{where}: choices is a list of words, e.g. [\"draft\", \"final\"]")
        elif len(set(choices)) != len(choices):
            problems.append(f"{where}: choices lists {next(c for c in choices if choices.count(c) > 1)!r} twice")
    if typ == "choice" and not choices:
        problems.append(f"{where}: a choice needs choices")
    target = raw.get("target")
    if target is not None and not (isinstance(target, str) or _strings(target)):
        problems.append(f"{where}: target is a record type, or a list of them")
    elif typ == "reference" and not target:
        problems.append(f"{where}: a reference needs a target: the record types it may point at")
    elif target and typ != "reference":
        problems.append(f"{where}: target is for a reference field (this one is {typ})")
    if "with" in raw and not isinstance(raw["with"], dict):
        problems.append(f"{where}: with is a table of sibling keys and values, e.g. {{ system = \"phone\" }}")
    if raw.get("live") is True and typ != "markdown":
        problems.append(f"{where}: live is for a markdown field (several people write it at once)")
    by = raw.get("by", "anyone")
    if by not in BY:
        problems.append(f"{where}: by is 'anyone' or 'workflow' (only a workflow step changes it, like stock on hand)")
    elif by == "workflow" and raw.get("required") is True:
        problems.append(f"{where}: a field only a workflow sets cannot be required: a person creating the record "
                        f"could never give it, so every new record would fail")
    path = raw.get("path") or name
    if not isinstance(path, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.([A-Za-z0-9_]+|\*))*", path):
        problems.append(f"{where}: path {path!r} is dotted keys and list positions, e.g. name.0.family")
    if problems:
        return None, problems
    target = target or []
    f = {"name": name, "path": path, "type": typ, "label": raw.get("label") or default_label(name),
         "required": raw.get("required", False), "help": raw.get("help", ""),
         "target": [target] if isinstance(target, str) else list(target), "rel": raw.get("rel") or None,
         "choices": list(choices or []), "max_length": ml, "min": raw.get("min"), "max": raw.get("max"),
         "list": raw.get("list", False), "show": raw.get("show", True),
         "advanced": raw.get("advanced", False), "with": dict(raw.get("with") or {}),
         "fact": raw.get("fact", False), "match": raw.get("match", False), "by": by,
         "live": raw.get("live", False), "data_class": raw.get("data_class") or None}
    if typ == "reference" and not f["rel"]:
        f["rel"] = name
    return f, []


def parse(source: str, catalog=None, *, base_ok: bool = False) -> RecordType:
    """Parse and check a record type. ``catalog`` (the hub) checks the data class and reference targets exist.
    ``base_ok``: the core's own base types (``abstract = true``), which keep no records and need no data class.
    Every problem is a DefinitionError, whatever shape the document has."""
    try:
        doc = tomllib.loads(source)
    except tomllib.TOMLDecodeError as exc:
        raise DefinitionError([f"The record type is not valid TOML: {exc}"]) from None
    try:
        return _parse(doc, source, catalog, base_ok)
    except DefinitionError:
        raise
    except (TypeError, ValueError, AttributeError, KeyError, IndexError) as exc:  # a shape no check below foresaw
        raise DefinitionError([f"the record type is not shaped as expected ({type(exc).__name__}: {exc})"]) from None


def _tables(doc: dict, key: str, problems: list, what: str) -> list[dict]:
    """``[[key]]``: a list of tables (anything else is a problem, and left out)."""
    v = doc.get(key)
    if v is None:
        return []
    if not isinstance(v, list):
        problems.append(f"{what}: a list of tables ([[{what}]])")
        return []
    bad = [x for x in v if not isinstance(x, dict)]
    if bad:
        problems.append(f"{what}: each one is a table ([[{what}]]), not {bad[0]!r}")
    return [x for x in v if isinstance(x, dict)]


def _resolves(path: str, fields: list[dict]) -> bool:
    """Whether a dotted path is a declared field's, inside one (an object or list it holds), or around some."""
    return any(path == f["path"] or path.startswith(f["path"] + ".") or f["path"].startswith(path + ".")
               for f in fields)


def _parse(doc: dict, source: str, catalog, base_ok: bool) -> RecordType:
    problems: list[str] = _unknown("top level", doc, "")
    tid = str(doc.get("id", ""))
    if not TYPE_ID.fullmatch(tid) or tid.lower() == "new":
        problems.append("id: give the record type an id of letters, digits and dashes, starting with a letter")
    for k in ("subject", "strict", "alert", "abstract"):
        if k in doc and not isinstance(doc[k], bool):
            problems.append(f"{k}: true or false, not {doc[k]!r}")
    for k in ("label", "plural", "summary", "data_class", "title", "active", "date", "flag", "parent", "extends",
              "part_of"):
        if k in doc and not isinstance(doc[k], str):
            problems.append(f"{k}: text, not {doc[k]!r}")
            doc.pop(k)
    order = doc.get("order", 100)
    if isinstance(order, bool) or not isinstance(order, int):
        problems.append(f"order: a whole number (where it sits on a subject's page), not {order!r}")
        order = 100
    abstract = doc.get("abstract") is True
    if abstract and not base_ok:
        problems.append("abstract: only the core's base types are abstract; a pack's type extends one (extends = ...)")
    if not doc.get("data_class") and not abstract:
        problems.append("data_class: say which data class the records belong to (permissions are per class)")
    fields, names, paths = [], set(), set()
    for i, raw in enumerate(_tables(doc, "field", problems, "field")):
        f, ps = _spec(raw, i)
        problems += ps
        if f:
            if f["name"] in names:
                problems.append(f"field {f['name']}: two fields share this name")
            if f["path"] in paths:
                problems.append(f"field {f['name']}: two fields share the path {f['path']}")
            names.add(f["name"])
            paths.add(f["path"])
            fields.append(f)
    ext, base_map, base = doc.get("extends"), {}, None
    spec = doc.get("base")
    if spec is not None and not isinstance(spec, dict):
        problems.append("base: a table of base field = the field that holds it")
        spec = None
    if ext is not None:
        base = bases().get(str(ext))
        if base is None or base_ok:
            problems.append(f"extends: {ext!r} is not one of the core's base types ({', '.join(sorted(bases()))})")
            base = None
        else:
            fields, base_map, ps = _inherit(base, fields, spec or {})
            problems += ps
    elif spec:
        problems.append("base: says which fields hold a base type's fields; say which type it extends")
    seen: dict = {}
    for f in fields:  # a form with two "Status" boxes: nobody can tell which is which
        other = seen.setdefault(f["label"].strip().lower(), f["name"])
        if other != f["name"]:
            problems.append(f"field {f['name']}: {other} has the same label {f['label']!r}; give one a label of its own")
    if any(f["path"].split(".")[0] in RESERVED for f in fields):
        problems.append("field: resourceType, id and meta are kept by the hub")
    active = doc.get("active")
    if active:
        try:
            expr.check(active)
        except expr.ExprError as exc:
            problems.append(f"active: {exc}")
    lc = doc.get("lifecycle")
    if lc is not None and not isinstance(lc, dict):
        problems.append("lifecycle: a table ([lifecycle]) with its field, initial state and transitions")
        lc = None
    if lc is not None:
        problems += _unknown("lifecycle", lc, "lifecycle")
        lf = lc.get("field", "status")
        if not isinstance(lf, str):
            problems.append(f"lifecycle.field: the name of the field that holds the state, not {lf!r}")
            lf = "status"
        fd = next((f for f in fields if f["name"] == lf), None)
        if not fd:
            problems.append(f"lifecycle.field: there is no field {lf!r}")
        elif fd["by"] == "workflow":
            problems.append(f"lifecycle.field: {lf} says by = \"workflow\", which would stop every move, even those "
                            f"the transitions give to anyone; say by on each transition instead")
        states = lc.get("states") or (fd or {}).get("choices") or []
        if not _strings(states):
            problems.append("lifecycle.states: a list of words")
            states = []
        states = list(states)
        if not states:
            problems.append("lifecycle: list its states (or make its field a choice)")
        initial = lc.get("initial")
        if initial is None:
            problems.append("lifecycle.initial: say which state a new record starts in")
        elif initial not in states:
            problems.append(f"lifecycle.initial: {initial!r} is not one of its states")
        trans = []
        for t in _tables(lc, "transition", problems, "lifecycle.transition"):
            frm = t.get("from", ["*"])
            frm = [frm] if isinstance(frm, str) else frm
            to = t.get("to")
            where = f"lifecycle.transition to {to!r}"
            problems += _unknown(where, t, "transition")
            if not _strings(frm) or not isinstance(to, str):
                problems.append(f"{where}: from is a state or a list of them, and to is one state")
                continue
            by = t.get("by", "anyone")
            for s in frm + [to]:
                if s != "*" and s not in states:
                    problems.append(f"lifecycle.transition: {s!r} is not one of its states")
            if by not in BY:
                problems.append(f"{where}: by is one of {', '.join(BY)}")
            req = t.get("require", []) or []
            req = [req] if isinstance(req, str) else req
            tf = t.get("fields", []) or []
            if not _strings(req) or not _strings(tf):
                problems.append(f"{where}: require and fields are lists of field names")
                req, tf = [], []
            if not isinstance(t.get("comment", False), bool):
                problems.append(f"{where}: comment is true or false")
            if t.get("screen") is not None and not isinstance(t["screen"], str):
                problems.append(f"{where}: screen is a screen id")
            trans.append({"from": frm, "to": to, "by": by, "label": str(t.get("label") or f"Move to {to}"),
                          "screen": t.get("screen") if isinstance(t.get("screen"), str) else None,
                          "fields": list(tf), "require": list(req), "comment": t.get("comment") is True})
        locked = lc.get("locked") or []  # states in which people change nothing but the state itself
        if not _strings(locked):
            problems.append("lifecycle.locked: a list of its states")
            locked = []
        for st in locked:
            if st not in states:
                problems.append(f"lifecycle.locked: {st!r} is not one of its states")
        # Where a record goes when the work that was moving it is cancelled: a state that only workflows leave
        # would otherwise strand the record for good.
        abandoned = lc.get("abandoned") or {}
        if not isinstance(abandoned, dict):
            problems.append("lifecycle.abandoned: a table of state = the state it goes to")
            abandoned = {}
        abandoned = dict(abandoned)
        for st, to in abandoned.items():
            if st not in states or to not in states:
                problems.append(f"lifecycle.abandoned: {st!r} = {to!r}: both must be its states")
            elif not any(st in t["from"] or "*" in t["from"] for t in trans if t["to"] == to):
                problems.append(f"lifecycle.abandoned: there is no move from {st!r} to {to!r}")
        for st in states:
            exits = [t for t in trans if (st in t["from"] or "*" in t["from"]) and t["to"] != st]
            if exits and all(t["by"] == "workflow" for t in exits) and st not in abandoned:
                problems.append(f"lifecycle.abandoned: only workflows move a record out of {st!r}; say where it goes "
                                f"if that work is cancelled (abandoned = {{ {st!r} = ... }})".replace("'", '"'))
        in_use = lc.get("in_use")  # the state people work from; while revised, the last such version stays shown
        if in_use is not None and in_use not in states:
            problems.append(f"lifecycle.in_use: {in_use!r} is not one of its states")
        revising: list = []
        if in_use in states:  # states a revision passes through: those with a way back to in_use (not 'obsolete')
            reach, grew = {in_use}, True
            while grew:
                grew = False
                for t in trans:
                    for f in (states if "*" in t["from"] else t["from"]):
                        if t["to"] in reach and f not in reach:
                            reach.add(f)
                            grew = True
            revising = [x for x in states if x in reach and x != in_use]
        lc = {"field": lf, "path": (fd or {}).get("path", lf), "states": states, "initial": initial,
              "transition": trans, "locked": locked, "abandoned": abandoned, "in_use": in_use, "revising": revising}
    for key in ("date", "flag"):
        p = doc.get(key)
        if p and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.([A-Za-z0-9_]+))*", str(p)):
            problems.append(f"{key}: {p!r} is a dotted path into the record")
        elif p and not _resolves(p, fields):
            problems.append(f"{key}: {p!r} is not the path of any of its fields")
    sigs = []
    for g in _tables(doc, "signature", problems, "signature"):
        meaning = str(g.get("meaning", "")).strip()
        problems += _unknown(f"signature {meaning or len(sigs) + 1!r}", g, "signature")
        if not meaning:
            problems.append("signature: say what signing means, e.g. 'Approved for release'")
            continue
        states = g.get("states", []) or []
        states = [states] if isinstance(states, str) else states
        roles = g.get("roles", []) or []
        roles = [roles] if isinstance(roles, str) else roles
        if not _strings(states) or not _strings(roles):
            problems.append(f"signature {meaning!r}: states and roles are lists of words")
            continue
        for st in states:
            if lc is not None and st not in lc["states"]:
                problems.append(f"signature {meaning!r}: {st!r} is not one of its states")
        sigs.append({"meaning": meaning, "roles": list(roles), "states": list(states), "moves_to": g.get("moves_to"),
                     "readers": g.get("readers") is True})  # readers: everyone who reads it is asked to sign it
        if "readers" in g and not isinstance(g["readers"], bool):
            problems.append(f"signature {meaning!r}: readers is true or false")
        if g.get("moves_to") and (lc is None or g["moves_to"] not in lc["states"]):
            problems.append(f"signature {meaning!r}: moves_to {g['moves_to']!r} is not one of its states")
        if g.get("moves_to") and not g.get("roles"):
            problems.append(f"signature {meaning!r}: a signature that moves the record says which roles may sign it")
    screens = _screens(doc, fields, base_map, lc, problems)
    label = str(doc.get("label") or tid)
    rt = RecordType(id=tid, label=label, plural=str(doc.get("plural") or label + "s"),
                    data_class=str(doc.get("data_class", "")), fields=fields, subject=doc.get("subject") is True,
                    strict=doc.get("strict", True) is not False, date=doc.get("date"), flag=doc.get("flag"),
                    alert=doc.get("alert") is True, active=active, order=order,
                    lifecycle=lc, summary=str(doc.get("summary", "")), source=source, signatures=sigs,
                    abstract=abstract, extends=base.id if base else None, base_map=base_map,
                    title=doc.get("title"), screens=screens)
    if base is not None:
        rt.date = rt.date or base.date
        rt.subject = doc.get("subject", base.id == "Subject") is True
    if rt.title:
        problems += [f"title: {p}" for p in _check_title(rt.title)]
        known = {f["name"] for f in fields if "*" not in f["path"]} | set(base_map)
        for n in TITLE_PART.findall(rt.title):
            if n not in known:
                near = difflib.get_close_matches(n, sorted(known), n=1)
                problems.append(f"title: {{{n}}} is not one of its fields" + (f" (did you mean {{{near[0]}}}?)"
                                                                           if near else ""))
    if doc.get("parent"):
        pf = rt.field(str(doc["parent"]))
        if not pf or pf["type"] != "reference" or pf.get("list") or "*" in pf["path"] or tid not in pf["target"]:
            problems.append(f"parent: {doc['parent']!r} is not a field that refers to one other {tid}")
        else:
            rt.parent = pf["path"]
    if doc.get("part_of"):
        name = str(doc["part_of"])
        own = ((base_map.get(name) or {}).get("fields") or [name])[0] if name in base_map else name
        pf = rt.field(own)
        if not pf or pf["type"] != "reference" or pf.get("list") or "*" in pf["path"]:
            problems.append(f"part_of: {name!r} is not a field that refers to one record (the one that owns it)")
        elif not pf.get("required"):
            problems.append(f"part_of: {name!r} must be required: a {rt.label.lower()} never exists without its owner")
        elif tid in pf["target"]:
            problems.append("part_of: a record is not part of its own kind; for a tree, say parent")
        else:
            rt.part_of = {"field": pf["name"], "path": pf["path"], "target": list(pf["target"])}
    problems += _check_field_classes(rt)
    nav = doc.get("navigator")
    if nav is not None:
        hide = nav.get("hide", []) if isinstance(nav, dict) else None
        states = set((rt.lifecycle or {}).get("states", [])) or set(
            next((f.get("choices", []) for f in rt.fields if rt.lifecycle and f["path"] == rt.lifecycle["path"]), []))
        if not isinstance(nav, dict):
            problems.append("navigator: a table of tops (words) and hide (states)")
        elif set(nav) - KEYS["navigator"]:
            problems += _unknown("navigator", nav, "navigator")
        elif not doc.get("parent"):
            problems.append("navigator: only a type whose records form a tree (parent) has one")
        elif not isinstance(hide, list) or any(not isinstance(h, str) or (states and h not in states) for h in hide):
            problems.append(f"navigator.hide: states of its lifecycle ({', '.join(sorted(states)) or 'none'})")
        else:
            rt.navigator = {"tops": str(nav.get("tops") or rt.plural)[:40], "hide": list(hide)}
    layout = doc.get("layout", "record")
    if layout not in ("record", "page"):
        problems.append(f"layout: 'record' or 'page', not {layout!r}")
    elif layout == "page" and not any(f["type"] == "markdown" for f in rt.fields):
        problems.append("layout: a page is formatted text; give the type a field of type markdown")
    else:
        rt.layout = layout
    std = doc.get("standard")
    if std is not None:
        problems += _check_standard(rt, std)
        if std in STANDARDS:
            rt.standard = std
    if catalog is not None:
        try:
            problems += [p for p in catalog.check_type(rt) if p not in problems]
        except Exception:  # noqa: BLE001 - the type's own problems say why
            pass
    if problems:
        raise DefinitionError(problems)
    return rt


def _inside(path: str, outer: str) -> bool:
    """Whether ``path`` lies within ``outer`` (``reasonCode.0.text`` within ``reasonCode``; ``*`` matches a position)."""
    a, b = path.split("."), outer.split(".")
    return len(a) > len(b) and all(x == y or "*" in (x, y) or (x.isdigit() and y.isdigit() and x == y)
                                   for x, y in zip(a, b))


def _check_field_classes(rt: RecordType) -> list[str]:
    """A field's own data class must make sense in the record: what says which record it is (its state, its owner,
    its parent, its base fields' subject) stays with the type's class, and a field inside another holds no less
    guarded data than the one around it (a reader would see it there)."""
    problems = []
    keep = {(rt.lifecycle or {}).get("path"), (rt.part_of or {}).get("path"), rt.parent}
    subj = (rt.base_map.get("subject") or {}).get("fields") or []
    for f in rt.classed_fields:
        if f["path"] in keep or f["name"] in subj or (f["rel"] == "subject" and f["type"] == "reference"):
            problems.append(f"field {f['name']}: it says which record this is (its state, owner, parent or "
                            f"subject): it keeps the type's data class")
    for f in rt.fields:
        for g in rt.fields:
            if f is not g and _inside(f["path"], g["path"]) and g.get("data_class") \
                    and rt.field_class(f) != rt.field_class(g):
                problems.append(f"field {f['name']}: it is inside {g['name']}, which is {rt.field_class(g)}: give it "
                                f"the same data class")
    return problems


STANDARDS = {"fhir-r4": "FHIR R4"}   # what ``standard`` may say: the standard its records are resources of
FHIR_NAME = re.compile(r"[A-Z][A-Za-z]+")       # a FHIR resource type: letters, capital first
FHIR_ELEMENT = re.compile(r"[a-z][A-Za-z0-9]*")  # a FHIR element: camelCase, small first


def _check_standard(rt: RecordType, std) -> list[str]:
    """A type that says its records are FHIR resources must be able to be one: a resource name, and no keys beyond
    its fields (strict). A field whose key is not a FHIR element name is exported as an extension."""
    if std not in STANDARDS:
        return [f"standard: {std!r} is not a standard the hub exports ({', '.join(sorted(STANDARDS))})"]
    out = []
    if rt.abstract:
        out.append("standard: a base type has no records of its own; the type that extends it says its standard")
    if not FHIR_NAME.fullmatch(rt.id):
        out.append(f"standard: {rt.id!r} is not a FHIR resource name (letters only, a capital first)")
    if not rt.strict:
        out.append("standard: a FHIR resource holds only FHIR's elements; say strict = true")
    return out  # a field of the practice's own (plan_code) goes out as a FHIR extension


def _screens(doc: dict, fields: list[dict], base_map: dict, lc: dict | None, problems: list) -> dict:
    """``[[screen]]``: which fields a form shows, in what tabs and order, for an operation (``on = ["create"]``) or a
    lifecycle move (``screen = "<id>"`` on the transition). The field's own settings (required, help, choices) stay
    with the field. Two screens of the same id are one: a module's ``extends/types/<Type>.toml`` adds a tab to the
    practice's screen that way. A screen may name a base field (``subject``) for the field that holds it."""
    byname = {f["name"]: f for f in fields}

    def resolve(n):
        if n in byname:
            return n
        m = base_map.get(n) or {}
        return m["fields"][0] if len(m.get("fields", [])) == 1 else None
    out: dict = {}
    for s in _tables(doc, "screen", problems, "screen"):
        sid = str(s.get("id") or "")
        problems += _unknown(f"screen {sid or len(out) + 1}", s, "screen")
        if not SCREEN_ID.fullmatch(sid):
            problems.append(f"screen: {sid!r} is not a screen id (lower case letters, digits and -)")
            continue
        sc = out.setdefault(sid, {"id": sid, "on": [], "tabs": []})
        on = s.get("on", [sid] if sid in OPS else [])
        on = [on] if isinstance(on, str) else on
        if not _strings(on):
            problems.append(f"screen {sid}: on is a list of {', '.join(OPS)}")
            on = []
        for op in on:
            if op not in OPS:
                problems.append(f"screen {sid}: on is one of {', '.join(OPS)}, not {op!r}")
            elif op not in sc["on"]:
                sc["on"].append(op)
        tabs = _tables(s, "tab", problems, f"screen {sid} tab")
        if "fields" in s:
            tabs.insert(0, {"label": s.get("label", ""), "fields": s["fields"]})
        for t in tabs:
            label, names = str(t.get("label") or ""), []
            problems += _unknown(f"screen {sid} tab {label!r}", t, "tab")
            tf = t.get("fields", []) or []
            if not _strings(tf):
                problems.append(f"screen {sid} tab {label!r}: fields is a list of field names")
                tf = []
            for n in tf:
                r = resolve(str(n))
                if r is None:
                    problems.append(f"screen {sid}: there is no field {n!r}")
                elif r not in names:
                    names.append(r)
            tab = next((x for x in sc["tabs"] if x["label"] == label), None)
            if tab is not None:
                tab["fields"] += [n for n in names if n not in tab["fields"]]
            else:
                sc["tabs"].append({"label": label, "fields": names})
    for op in OPS:
        owners = [s["id"] for s in out.values() if op in s["on"]]
        if len(owners) > 1:
            problems.append(f"screen: {' and '.join(owners)} are both shown to {op}; one screen per operation")
    create = next((s for s in out.values() if "create" in s["on"]), None)
    if create is not None:
        on_it = {n for t in create["tabs"] for n in t["fields"]}
        paths = [byname[n]["path"] for n in on_it]
        state = lc["field"] if lc else None
        for f in fields:  # a required field missing from the create screen would make every new record fail
            inside = any(q.startswith(f["path"] + ".") for q in paths)  # given and family fill the name
            if f["required"] and f["name"] not in on_it and f["name"] != state and not inside:
                problems.append(f"screen {create['id']}: {f['name']} is required, so the create screen shows it")
    for t in (lc or {}).get("transition", []):
        if t["screen"] is not None:
            sc = out.get(str(t["screen"]))
            if sc is None:
                problems.append(f"lifecycle.transition to {t['to']!r}: there is no screen {t['screen']!r}")
            else:
                t["fields"] = [n for tab in sc["tabs"] for n in tab["fields"]] + t["fields"]
        def ok(names):
            out = []
            for n in names:
                r = resolve(str(n))
                if r is None:
                    problems.append(f"lifecycle.transition to {t['to']!r}: there is no field {n!r}")
                elif r == lc["field"]:
                    problems.append(f"lifecycle.transition to {t['to']!r}: the move itself sets {r}")
                elif r not in out:
                    out.append(r)
            return out
        t["require"] = ok(t["require"])
        # what a move requires, its screen asks for
        asked = ok(t["fields"])
        t["fields"] = asked + [n for n in t["require"] if n not in asked]
        t["comment"] = bool(t["comment"])
    return out


def _fits(bf: dict, f: dict) -> bool:
    """Whether a pack's field can hold a base field's value (its contract type)."""
    bt, t = bf["type"], f["type"]
    if bt in ("string", "text"):
        return t not in ("attachment", "json")
    if bt in ("date", "datetime"):
        return t in ("date", "datetime")
    if bt == "member":
        return t in ("member", "reference")
    return bt == t


def _inherit(base: RecordType, fields: list[dict], spec: dict) -> tuple[list[dict], dict, list[str]]:
    """A pack type's fields with the base's merged in, and where each base field is held."""
    problems, out, bmap = [], list(fields), {}
    by_name = {f["name"]: f for f in fields}
    for k in spec:
        if not base.field(k):
            near = difflib.get_close_matches(str(k), [f["name"] for f in base.fields], n=1)
            problems.append(f"base.{k}: {base.id} has no field {k!r} (" + (f"did you mean {near[0]!r}? " if near else "")
                            + f"its fields: {', '.join(f['name'] for f in base.fields)})")
    for bf in base.fields:
        name, m = bf["name"], spec.get(bf["name"])
        where = f"base.{name}"
        if m is False:
            if bf["required"]:
                problems.append(f"{where}: {base.id} needs it; name the field that holds it")
            continue
        if isinstance(m, dict):
            if set(m) != {"when"} or bf["type"] != "boolean":
                problems.append(f"{where}: {{ when = ... }} is for a true/false base field: a condition over record")
                continue
            try:
                expr.check(str(m["when"]))
            except expr.ExprError as exc:
                problems.append(f"{where}: {exc}")
                continue
            bmap[name] = {"when": str(m["when"])}
            continue
        if m is None and name not in by_name:
            if any(f["path"].split(".")[0] == bf["path"].split(".")[0] for f in out):
                problems.append(f"{where}: the type keeps something else at {bf['path']!r}; name the field that "
                                f"holds {base.id}'s {name}, or say {name} = false")
                continue
            out.append(dict(bf))
            bmap[name] = {"fields": [name]}
            continue
        names = [name] if m is None else [m] if isinstance(m, str) else list(m) if isinstance(m, list) else None
        if not names or not all(isinstance(n, str) for n in names):
            problems.append(f"{where}: a field name, a list of them, {{ when = ... }} or false")
            continue
        bad = [n for n in names if n not in by_name]
        if bad:
            problems.append(f"{where}: there is no field {bad[0]!r}")
            continue
        if len(names) > 1 and bf["type"] not in ("string", "text"):
            problems.append(f"{where}: only a text base field is made of several fields")
            continue
        for n in names:
            if not _fits(bf, by_name[n]):
                problems.append(f"{where}: field {n} is {by_name[n]['type']}; {base.id}'s {name} is {bf['type']}")
        bmap[name] = {"fields": names}
    return out, bmap, problems


_BASES: dict | None = None


def bases() -> dict[str, RecordType]:
    """The core's base types, by id."""
    global _BASES
    if _BASES is None:
        out = {}
        for p in sorted(BASE_DIR.glob("*.toml")):
            rt = parse(p.read_text(), base_ok=True)
            out[rt.id] = rt
        _BASES = out
    return _BASES


def words(v) -> str:
    """A value as plain words: what a base text field holds, whatever shape the pack keeps it in."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (str, int, float)):
        return str(v)
    if isinstance(v, list):
        return " ".join(w for w in (words(x) for x in v) if w)
    if isinstance(v, dict):
        for k in ("text", "display", "value", "name"):
            if isinstance(v.get(k), (str, int, float)) and str(v[k]).strip():
                return str(v[k])
        return " ".join(w for k, x in v.items() if k not in ("system", "use", "id", "extension") for w in [words(x)] if w)
    return str(v)


def _contract(bf: dict, f: dict, item, member_types) -> object:
    bt = bf["type"]
    if bt in ("string", "text"):
        return words(item) or None
    if bt == "member":
        if f["type"] == "member":
            return str(item).removeprefix("member:") if item else None
        ref = ref_of(item)
        return ref.split("/", 1)[1] if ref and ref.split("/", 1)[0] in member_types else None
    if bt == "reference":
        return ref_of(item)
    return item


def project(rt: RecordType | None, rec: dict, member_types=()) -> dict:
    """The record as its base type sees it: ``{base field: value}`` in the base's own types (text is words, a member
    is a member id, a reference is ``Type/id``). The core and modules read a pack's records only this way."""
    base = bases().get(rt.extends) if rt is not None and rt.extends else None
    if base is None:
        return {}
    out = {}
    for bf in base.fields:
        m = rt.base_map.get(bf["name"])
        if m is None:
            continue
        if "when" in m:
            try:
                out[bf["name"]] = expr.holds(m["when"], {"record": rec})
            except expr.ExprError:
                out[bf["name"]] = False
            continue
        vals = []
        for n in m["fields"]:
            f = rt.field(n)
            for v in get(rec, f["path"]):
                for item in (v if isinstance(v, list) else [v]):
                    c = _contract(bf, f, item, member_types)
                    if c not in (None, ""):
                        vals.append(c)
        if bf["list"]:
            out[bf["name"]] = vals
        elif bf["type"] in ("string", "text") and len(m["fields"]) > 1:
            out[bf["name"]] = " ".join(map(str, vals)) or None
        else:
            out[bf["name"]] = vals[0] if vals else None
    return out


def compose(rt: RecordType, values: dict, member_ref: str, rec: dict | None = None) -> dict:
    """A record of ``rt`` from base-field values (the inverse of ``project``), on top of ``rec``. A member becomes a
    reference to ``member_ref``/id where the pack keeps references; a ``*`` path or a list adds an item."""
    base = bases().get(rt.extends or "")
    if base is None:
        raise ValueError(f"{rt.label} extends no base type")
    rec = dict(rec or {})
    for bf in base.fields:
        val = values.get(bf["name"])
        if val in (None, "", []):
            continue
        m = rt.base_map.get(bf["name"]) or {}
        if len(m.get("fields") or []) != 1:
            raise ValueError(f"{rt.label} keeps {base.id}'s {bf['name']} in no one field it can be written to")
        f = rt.field(m["fields"][0])
        for item in (val if bf["list"] and isinstance(val, list) else [val]):
            if f["type"] == "reference":
                ref = item if "/" in str(item) else f"{member_ref}/{item}"
                item = {"reference": str(ref)}
            rec = _place(rec, f, item)
    return rec


def _place(rec: dict, f: dict, value) -> dict:
    parts = f["path"].split(".")
    if "*" in parts:
        i = parts.index("*")
        lst = first(rec, ".".join(parts[:i]))
        new = set_in({}, parts[i + 1:], value, f["with"]) if parts[i + 1:] else value
        return set_in(rec, parts[:i], (lst if isinstance(lst, list) else []) + [new])
    if f.get("list"):
        cur = first(rec, f["path"])
        return set_in(rec, parts, (cur if isinstance(cur, list) else []) + [value], f["with"])
    return set_in(rec, locate(rec, f["path"], f["with"]).split("."), value, f["with"])


TITLE_PART = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _check_title(t: str) -> list[str]:
    depth = 0
    for ch in t:
        depth += {"[": 1, "]": -1}.get(ch, 0)
        if depth not in (0, 1):
            return ["optional parts [...] do not nest and close what they open"]
    return [] if depth == 0 else ["an optional part [... is not closed"]


def render_title(template: str, values: dict) -> str:
    """``{name}`` is a value's words; ``[ ... ]`` is left out when a value in it is empty."""
    def fill(seg: str, optional: bool) -> str:
        empty = False

        def one(m):
            nonlocal empty
            w = words(values.get(m.group(1)))
            if not w:
                empty = True
            return w
        out = TITLE_PART.sub(one, seg)
        return "" if optional and empty else out
    out, i = [], 0
    for m in re.finditer(r"\[([^\]]*)\]", template):
        out.append(fill(template[i:m.start()], False))
        out.append(fill(m.group(1), True))
        i = m.end()
    out.append(fill(template[i:], False))
    return "".join(out).strip()


def permissive(tid: str, data_class: str, label: str | None = None) -> RecordType:
    """A type with no declared fields, for a pack that names a record type but ships no schema for it."""
    src = f'id = "{tid}"\nlabel = "{label or tid}"\ndata_class = "{data_class}"\nstrict = false\n'
    return parse(src)


# --------------------------------------------------------------------------------------------------- paths
def get(rec, path: str):
    """The values at ``path`` (a list: ``*`` may match several)."""
    cur = [rec]
    for part in path.split("."):
        nxt = []
        for c in cur:
            if part == "*":
                if isinstance(c, list):
                    nxt += c
            elif isinstance(c, dict):
                if part in c:
                    nxt.append(c[part])
            elif isinstance(c, list) and part.isdigit():
                if int(part) < len(c):
                    nxt.append(c[int(part)])
        cur = nxt
    return cur


def first(rec, path: str | None):
    if not path:
        return None
    vals = get(rec, path)
    return vals[0] if vals else None


def locate(rec, path: str, siblings: dict | None = None) -> str:
    """Where a field's value is in this record. A list position beside ``with`` siblings (``telecom.0.value`` with
    ``system = phone``) means the entry that has those siblings, wherever it sits: the phone is the phone even when
    an email came first. With no such entry, the stated position if nothing else claims it, else a new entry."""
    parts = path.split(".")
    pos = next((i for i in range(len(parts) - 1, -1, -1) if parts[i].isdigit()), None)  # the innermost list
    if not siblings or pos is None:
        return path
    lst = rec
    for p in parts[:pos]:
        if p.isdigit():
            lst = lst[int(p)] if isinstance(lst, list) and int(p) < len(lst) else None
        else:
            lst = lst.get(p) if isinstance(lst, dict) else None
    if not isinstance(lst, list):
        return path
    for i, entry in enumerate(lst):
        if isinstance(entry, dict) and all(entry.get(k) == v for k, v in siblings.items()):
            return ".".join([*parts[:pos], str(i), *parts[pos + 1:]])
    i = int(parts[pos])
    entry = lst[i] if i < len(lst) else None
    if i < len(lst) and not (isinstance(entry, dict) and any(k in entry for k in siblings)):
        return path
    return ".".join([*parts[:pos], str(len(lst)), *parts[pos + 1:]])


def put(rec: dict, path: str, value) -> dict:
    """A copy of ``rec`` with ``value`` at a dotted path of plain keys (for a lifecycle's initial state)."""
    out = dict(rec)
    cur = out
    parts = path.split(".")
    for part in parts[:-1]:
        nxt = dict(cur.get(part) or {}) if isinstance(cur.get(part), dict) or cur.get(part) is None else None
        if nxt is None:
            raise ValueError(f"{path}: {part} is not an object")
        cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value
    return out


def set_in(value, parts: list[str], new, siblings: dict | None = None):
    """A copy of ``value`` with ``new`` at the path ``parts`` (keys and list positions), making the objects and
    lists on the way. ``siblings`` are set beside the new value where absent (a new phone gets ``system = phone``).
    A ``new`` of None removes the key."""
    import copy
    if not parts:
        return new
    head, rest = parts[0], parts[1:]
    if head.isdigit():
        out = copy.deepcopy(value) if isinstance(value, list) else []
        i = int(head)
        if new is None and i >= len(out):
            return out
        while len(out) <= i:
            out.append({} if rest and not rest[0].isdigit() else [] if rest else None)
        out[i] = set_in(out[i], rest, new, siblings)
        return out
    out = copy.deepcopy(value) if isinstance(value, dict) else {}
    if not rest:
        if new is None:
            out.pop(head, None)
        else:
            out[head] = new
            for k, v in (siblings or {}).items():
                out.setdefault(k, v)
        return out
    out[head] = set_in(out.get(head), rest, new, siblings)
    return out


REF_TEXT = re.compile(r"[A-Za-z][A-Za-z0-9_]*/[A-Za-z0-9_.-]+")


def ref_of(v) -> str | None:
    if isinstance(v, dict):
        v = v.get("reference")
    return v if isinstance(v, str) and "/" in v else None


# ----------------------------------------------------------------------------------------------- validation
def _check_value(f: dict, v, members=None, is_a=None) -> str | None:
    t, label = f["type"], f["label"]
    if t in ("string", "text", "markdown", "member"):
        if not isinstance(v, str):
            return f"{label}: expected text, got {type(v).__name__}"
        limit = f.get("max_length") or {"text": 20000, "markdown": 200000}.get(t, 2000)
        if len(v) > limit:
            return f"{label} is longer than {limit} characters"
        if t == "member" and members is not None and v.removeprefix("member:") not in members:
            return f"{label}: there is no member {v!r}"
    elif t == "number":
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return f"{label}: expected a number"
        if f.get("min") is not None and v < f["min"]:
            return f"{label} is below {f['min']}"
        if f.get("max") is not None and v > f["max"]:
            return f"{label} is above {f['max']}"
    elif t == "boolean":
        if not isinstance(v, bool):
            return f"{label}: expected true or false"
    elif t == "date":
        try:
            if not isinstance(v, str) or not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", v):
                raise ValueError
            if len(v) == 10:
                date.fromisoformat(v)
        except ValueError:
            return f"{label}: {str(v)[:30]!r} is not a date (YYYY-MM-DD)"
    elif t == "datetime":
        try:
            if not isinstance(v, str):
                raise ValueError
            datetime.fromisoformat(v.replace("Z", "+00:00")) if "T" in v else date.fromisoformat(v)
        except ValueError:
            return f"{label}: {str(v)[:30]!r} is not a date and time"
    elif t == "choice":
        if str(v) not in [str(c) for c in f["choices"]]:
            return f"{label}: {str(v)[:30]!r} is not one of {', '.join(map(str, f['choices']))}"
    elif t == "reference":
        ref = ref_of(v)
        if not ref:
            return f"{label}: expected a reference like {f['target'][0]}/<id>"
        t = ref.split("/")[0]
        if f["target"] and "*" not in f["target"] and t not in f["target"] and not (
                is_a and any(is_a(t, x) for x in f["target"])):
            return f"{label}: {ref} is not a {' or '.join(f['target'])}"
    elif t == "attachment":
        if not isinstance(v, dict) or not (v.get("blob") or v.get("url") or v.get("data")):
            return f"{label}: an attachment is {{blob: <sha256>, name, contentType}} or {{url}}"
    elif t == "object":
        if not isinstance(v, dict):
            return f"{label}: expected an object"
    elif t == "list":
        if not isinstance(v, list):
            return f"{label}: expected a list"
    return None


def validate(rt: RecordType, rec: dict, *, partial_ok: bool = False, members=None, is_a=None) -> list[str]:
    """Every problem with ``rec`` as a record of type ``rt``. ``is_a(type, target)``: whether a type stands for a
    reference's target (a ``Subject`` target takes a Patient when Patient extends Subject)."""
    problems = []
    if rt.strict:
        extra = sorted(k for k in rec if k not in rt.top_keys())
        if extra:
            problems.append(f"{rt.label}: {', '.join(extra)} {'is not a field' if len(extra) == 1 else 'are not fields'}"
                            f" of this record type")
    for f in rt.fields:
        vals = get(rec, f["path"])
        vals = [v for v in vals if v not in (None, "", [], {})]
        if not vals:
            if f["required"] and not partial_ok:
                problems.append(f"{rt.label}: {f['label']} is required")
            continue
        for v in vals:
            items = v if (f.get("list") and isinstance(v, list)) else [v]
            for item in items:
                p = _check_value(f, item, members, is_a)
                if p:
                    problems.append(f"{rt.label}: {p}")
                    break
    if rt.lifecycle:
        st = first(rec, rt.lifecycle["path"])
        if st is not None and st not in rt.lifecycle["states"]:
            problems.append(f"{rt.label}: {st!r} is not one of its states ({', '.join(rt.lifecycle['states'])})")
    return problems


def links(rt: RecordType, rec: dict) -> list[tuple[str, str]]:
    """(rel, Type/id) for every reference the record holds."""
    out = []
    for f in rt.references:
        for v in get(rec, f["path"]):
            for item in (v if isinstance(v, list) else [v]):
                ref = ref_of(item)
                if ref:
                    out.append((f["rel"], ref))
    return out


FORM_SIMPLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.\*)?")  # a field a form can edit: a top-level key, or a list of them
# or one plain value inside a structure, by keys and list positions (name.0.given.0, telecom.0.value)
FORM_NESTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.([0-9]+|[A-Za-z_][A-Za-z0-9_]*))+")
SCALAR = ("string", "text", "markdown", "date", "datetime", "choice", "number", "boolean")


def form_fields(rt: RecordType) -> list[dict]:
    """The fields a person edits in a form: plain values at a path a form can write, not the lifecycle's state, not
    a workflow's, not files, not a list whose items are a field of their own."""
    return [f for f in rt.fields if (FORM_SIMPLE.fullmatch(f["path"]) or (FORM_NESTED.fullmatch(f["path"])
                                                                         and f["type"] in (*SCALAR, "reference")))
            and f.get("show", True) is not False and f.get("by") != "workflow"
            and f["type"] != "attachment" and not (rt.lifecycle and f["path"] == rt.lifecycle["path"])
            and not (f["type"] == "list" and any(g["path"] == f["path"] + ".*" for g in rt.fields))]


def list_columns(rt: RecordType) -> list[dict]:
    """The fields a list of a type's records shows beside its title and status: for a part, its owner first; then a
    few short values. A list's export has the same columns."""
    heads = [f for f in form_fields(rt) if f["type"] in ("string", "choice", "date", "member", "number")][:3]
    owner = rt.field(rt.part_of["field"]) if rt.part_of else None
    if owner:
        heads = [owner] + [f for f in heads if f is not owner][:2]
    return heads


def record_date(rt: RecordType | None, rec: dict) -> str:
    v = first(rec, rt.date) if rt else None
    if isinstance(v, dict):  # a period
        v = v.get("start") or v.get("end")
    return str(v or (rec.get("meta") or {}).get("lastUpdated", ""))


def is_active(rt: RecordType | None, rec: dict) -> bool:
    if not rt or not rt.active:
        return True
    try:
        return expr.holds(rt.active, {"record": rec})
    except expr.ExprError:
        return True


# ------------------------------------------------------------------------------------------- module additions
# What a module adds to a type (or view, or workflow) another part of the practice owns sits in the text between
# these lines (``hub._extended_source`` writes them). The practice's own text is what lies outside them; a practice's
# change never adds, alters or removes what is inside, and the module's lines are put back after its text on save.
ADDED = re.compile(r"# >>> added by the ([a-z][a-z0-9-]*) module\n(.*?)\n# <<< \1(?:\n|$)", re.S)
ADDED_LINE = re.compile(r"^[ \t]*# (>>>|<<<)", re.M)


def module_additions(source: str) -> list[tuple[str, str]]:
    """``[(module id, the text it added)]``, in the order they stand."""
    return [(m, t.strip()) for m, t in ADDED.findall(source or "")]


def own_text(source: str) -> str:
    """A definition's text without what modules added to it: what the practice owns and edits."""
    out = ADDED.sub("", source or "")
    return re.sub(r"\n{3,}", "\n\n", out).strip() + "\n"


def with_additions(own: str, additions: list[tuple[str, str]]) -> str:
    """The practice's text followed by each module's additions, marked as ``hub._extended_source`` marks them."""
    src = (own or "").rstrip() + "\n"
    for mid, text in additions:
        src += f"\n# >>> added by the {mid} module\n{text.strip()}\n# <<< {mid}\n"
    return src


def _added_names(text: str) -> dict[str, set]:
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    pick = lambda key, k: {str(x.get(k)) for x in doc.get(key, []) if isinstance(x, dict) and x.get(k)}  # noqa: E731
    return {"field": pick("field", "name"), "signature": pick("signature", "meaning"),
            "column": pick("column", "field"), "widget": pick("widget", "id")}


def keep_module_additions(source: str, in_force: str | None) -> tuple[str, list[str]]:
    """A practice's proposed text, with what modules added to the version in force put back after it.

    The practice owns only its own text: a proposal may leave the modules' marked lines out (they are re-applied) or
    carry them exactly as they are in force; one that adds, changes or removes them, or that defines in its own text
    a field (signature, column, widget) a module added, is refused. Returns (the text to propose, problems)."""
    sent, have = module_additions(source), module_additions(in_force or "")
    own, problems = own_text(source), []
    for n, line in enumerate(own.splitlines(), 1):
        if ADDED_LINE.match(line):
            problems.append(f"line {n}: {line.strip()!r} marks where a module's additions start or end; the practice's "
                            f"text does not carry it (leave the module's lines out: they are kept as they are)")
    held = dict(have)
    for mid, text in sent:
        if mid not in held:
            problems.append(f"the {mid} module: the text carries additions marked as the {mid} module's, which the "
                            f"version in force does not have; only the module adds them (install or upgrade it)")
        elif text != held[mid]:
            problems.append(f"the {mid} module: what it added cannot be changed here; it changes with the module "
                            f"(upgrade it), and the practice's own fields go outside its marked lines")
    if sent:
        for mid in held:
            if mid not in dict(sent):
                problems.append(f"the {mid} module: the text leaves out what the {mid} module added; a module's "
                                f"additions go only when the module is removed")
    mine = _added_names(own)
    for mid, text in have:
        for what, names in _added_names(text).items():
            for nm in sorted(names & mine.get(what, set())):
                problems.append(f"{what} {nm}: added by the {mid} module, so the practice's text does not define it "
                                f"again (to change it, change the module)")
    if have:
        return with_additions(own, have), problems
    return (own if sent else source), problems
