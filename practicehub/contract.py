"""The data contract between the core and the packs: the core's base types, as Avro schemas.

A pack depends on the core, never the other way. The core reads a pack's records only through a base type
(``schema.project``) and writes them only through one (``schema.compose``); this module states that shape as Avro
record schemas (Apache Avro 1.11 JSON form), versioned with ``CORE_CONTRACT``:

    practicehub contract export                  the core's base types
    practicehub contract export --pack <dir>     and the pack's types, each naming the base it extends

A pack says which contract it was written for (``requires_core = "1"`` in ``[practice]``, or ``[module]``); a core
with another major version refuses to load it rather than read its records wrongly. Within a major version the
contract only grows: a new optional base field (with a default) is a minor change.

Nothing here needs an Avro library: the schemas are JSON, and ``conforms`` checks a projected value against one, so
a test (or another system reading the export) holds the core to it. Standard library only.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime

from . import schema
from . import decimals

CORE_CONTRACT = "1.2"  # 1.1: a decimal field (exact text) and a table field (an array of rows); 1.2: a type under
# another of the pack's (its extends names that type, its core the base at the root) and abstract types
NAMESPACE = "ca.practicehub.core"

# a base field's type, as Avro (datetimes and dates are ISO text: a booking keeps its local time as written)
_AVRO = {"string": "string", "text": "string", "markdown": "string", "number": "double", "boolean": "boolean", "member": "string",
         "reference": "string", "choice": "string", "date": "string", "datetime": "string", "decimal": "string"}
# a decimal travels as its exact text ("1234.50", ``ph.scale`` places), never as a double that could round it
_LOGICAL = {"date": "iso-date", "datetime": "iso-datetime", "member": "member-id", "reference": "type-slash-id",
            "decimal": "decimal-text"}


def major(version: str) -> str:
    return str(version).split(".", 1)[0]


def check_requires(pack_name: str, wanted: str | None) -> None:
    """A pack written for another major version of the contract is refused (an absent ``requires_core`` is 1)."""
    if wanted is None or str(wanted).strip() == "":
        return
    if not re.fullmatch(r"\d+(\.\d+)?", str(wanted).strip()):
        raise ValueError(f"{pack_name}: requires_core = {wanted!r} is not a contract version (e.g. \"1\" or \"1.0\")")
    if major(wanted) != major(CORE_CONTRACT):
        raise ValueError(f"{pack_name} was written for the core's data contract {wanted}; this core keeps "
                         f"{CORE_CONTRACT}: upgrade the pack (or the core) so they agree")
    if "." in str(wanted) and tuple(map(int, str(wanted).split("."))) > tuple(map(int, CORE_CONTRACT.split("."))):
        raise ValueError(f"{pack_name} needs the core's data contract {wanted}; this core keeps {CORE_CONTRACT}")


def _field(bf: dict) -> dict:
    t: object = {"type": _AVRO.get(bf["type"], "string")}
    if bf["type"] in _LOGICAL:
        t["logicalType"] = _LOGICAL[bf["type"]]
    if bf["type"] == "decimal":
        t["ph.scale"] = bf["scale"]
    if bf["type"] == "table":  # its rows, each a record of its columns
        t = {"type": "array", "items": {"type": "record", "name": f"{bf['name']}_row",
                                        "fields": [_field(c) for c in bf["columns"]]}}
    if bf["type"] == "reference" and bf.get("target"):
        t["ph.targets"] = list(bf["target"])
    if bf.get("list"):
        t = {"type": "array", "items": t}
        out = {"name": bf["name"], "type": t, "default": []}
    elif bf.get("required"):
        out = {"name": bf["name"], "type": t}
    else:
        out = {"name": bf["name"], "type": ["null", t], "default": None}
    if bf.get("label"):
        out["doc"] = bf["label"] + (f": {bf['help']}" if bf.get("help") else "")
    return out


def avro(rt: schema.RecordType) -> dict:
    """A base type as an Avro record schema."""
    out = {"type": "record", "name": rt.id, "namespace": NAMESPACE, "fields": [_field(bf) for bf in rt.fields],
           "ph.contract": CORE_CONTRACT}
    if rt.summary:
        out["doc"] = rt.summary
    return out


def pack_type(rt: schema.RecordType) -> dict:
    """A pack's type, as the contract sees it: what it extends (a core base type, or another of the pack's types,
    with the core base at the root of its chain as ``core``), which of its own fields hold each base field, and
    whether it is abstract (no records of its own). The record schema of its own fields is the pack's affair; this
    is what the core relies on."""
    mapping = {}
    for name, m in rt.base_map.items():
        mapping[name] = {"when": m["when"]} if "when" in m else {"fields": list(m["fields"])}
    direct = rt.extends in schema.bases()
    out = {"type": rt.id, "extends": f"{NAMESPACE}.{rt.extends}" if direct else rt.extends}
    if not direct and rt.base:
        out["core"] = f"{NAMESPACE}.{rt.base}"
    out["base"] = mapping
    if rt.abstract:
        out["abstract"] = True
    return out


def export(types=None) -> dict:
    """The contract: every base type, and (given a pack's record types) how each one that extends a base holds it."""
    out = {"protocol": "PracticeHubCore", "namespace": NAMESPACE, "version": CORE_CONTRACT,
           "types": [avro(b) for _, b in sorted(schema.bases().items())]}
    if types:
        out["extensions"] = [pack_type(rt) for rt in sorted(types, key=lambda r: r.id) if rt.extends]
    return out


def _is(value, t) -> bool:
    if isinstance(t, list):
        return any(_is(value, x) for x in t)
    if t == "null":
        return value is None
    if isinstance(t, dict):
        if t["type"] == "array":
            return isinstance(value, list) and all(_is(v, t["items"]) for v in value)
        if t["type"] == "record":
            return isinstance(value, dict) and all(
                _is(value.get(f["name"]), f["type"]) for f in t["fields"]) and set(value) <= {f["name"] for f in t["fields"]}
        lt = t.get("logicalType")
        if lt == "decimal-text":  # exact text, or the Decimal a record holds
            v = decimals.text(value) if isinstance(value, decimals.Decimal) else value
            return isinstance(v, str) and bool(re.fullmatch(r"-?\d+(\.\d+)?", v)) and \
                (len(v.partition(".")[2]) == t.get("ph.scale", len(v.partition(".")[2])))
        if not _is(value, t["type"]):
            return False
        try:
            if lt == "iso-date":
                date.fromisoformat(value)
            elif lt == "iso-datetime":
                datetime.fromisoformat(value)
        except ValueError:
            return False
        if lt == "type-slash-id" and not re.fullmatch(r"[A-Za-z][\w]*/[\w.-]+", value):
            return False
        return True
    return {"string": lambda v: isinstance(v, str), "boolean": lambda v: isinstance(v, bool),
            "double": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)}[t](value)


def conforms(base: str, value: dict) -> list[str]:
    """What is wrong with ``value`` (a record projected onto ``base``) against the base's schema; [] when it fits.
    A field the record type leaves out of its ``[base]`` (``false``) is absent, which an optional field allows."""
    sch = avro(schema.bases()[base])
    bad = []
    for f in sch["fields"]:
        if f["name"] not in value:
            if "default" not in f:
                bad.append(f"{f['name']}: required, absent")
            continue
        if not _is(value[f["name"]], f["type"]):
            bad.append(f"{f['name']}: {json.dumps(value[f['name']], default=decimals.jsonable)[:60]} is not {json.dumps(f['type'], default=decimals.jsonable)[:80]}")
    extra = set(value) - {f["name"] for f in sch["fields"]}
    bad += [f"{n}: not a field of {base}" for n in sorted(extra)]
    return bad
