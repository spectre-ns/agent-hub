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

CORE_CONTRACT = "1.0"
NAMESPACE = "ca.practicehub.core"

# a base field's type, as Avro (datetimes and dates are ISO text: a booking keeps its local time as written)
_AVRO = {"string": "string", "text": "string", "markdown": "string", "number": "double", "boolean": "boolean", "member": "string",
         "reference": "string", "choice": "string", "date": "string", "datetime": "string"}
_LOGICAL = {"date": "iso-date", "datetime": "iso-datetime", "member": "member-id", "reference": "type-slash-id"}


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
    """A pack's type, as the contract sees it: the base it extends, and which of its own fields hold each base field.
    The record schema of its own fields is the pack's affair; this is what the core relies on."""
    mapping = {}
    for name, m in rt.base_map.items():
        mapping[name] = {"when": m["when"]} if "when" in m else {"fields": list(m["fields"])}
    return {"type": rt.id, "extends": f"{NAMESPACE}.{rt.extends}", "base": mapping}


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
        if not _is(value, t["type"]):
            return False
        lt = t.get("logicalType")
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
            bad.append(f"{f['name']}: {json.dumps(value[f['name']])[:60]} is not {json.dumps(f['type'])[:80]}")
    extra = set(value) - {f["name"] for f in sch["fields"]}
    bad += [f"{n}: not a field of {base}" for n in sorted(extra)]
    return bad
