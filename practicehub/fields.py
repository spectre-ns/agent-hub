"""Field schemas: what a step's output looks like, for a person's form and for an agent's structured answer alike.

A step's output is validated against its fields whoever produced it. Model output is data: parsed, coerced and
checked here, never executed.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from .expr import lookup

TYPES = {"text", "string", "number", "boolean", "choice", "list", "date", "datetime", "member", "reference"}
REF = re.compile(r"[A-Za-z][A-Za-z0-9_]*/[A-Za-z0-9_.-]+")


class FieldError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def normalize(spec: dict) -> dict:
    f = {"name": spec["name"], "label": spec.get("label") or spec["name"].replace("_", " ").capitalize(),
         "type": spec.get("type", "string"), "required": bool(spec.get("required", False)),
         "help": spec.get("help", "")}
    for k in ("choices", "choices_from", "default", "max_length", "min", "max", "target", "of_record"):
        if k in spec:
            f[k] = spec[k]
    return f


def check_spec(spec: dict) -> list[str]:
    problems = []
    if not spec.get("name") or not re.fullmatch(r"[a-z][a-z0-9_]*", str(spec.get("name"))):
        problems.append(f"field {spec.get('name')!r}: a name is lower_case letters, digits and underscores")
    if spec.get("type", "string") not in TYPES:
        problems.append(f"field {spec.get('name')!r}: unknown type {spec.get('type')!r} (one of {', '.join(sorted(TYPES))})")
    if spec.get("type") == "choice" and not (spec.get("choices") or spec.get("choices_from")):
        problems.append(f"field {spec.get('name')!r}: a choice needs choices or choices_from")
    return problems


def choices_for(field: dict, ctx: dict) -> list | None:
    if field.get("choices"):
        return list(field["choices"])
    if field.get("choices_from"):
        found = lookup(ctx, field["choices_from"]) or []
        return [c["value"] if isinstance(c, dict) and "value" in c else c for c in found]
    return None


def coerce(fields: list[dict], raw: dict, ctx: dict | None = None) -> dict:
    """Coerce and validate ``raw`` against ``fields``. Raises FieldError listing every problem."""
    ctx = ctx or {}
    out, problems = {}, []
    for f in fields:
        name, typ = f["name"], f["type"]
        val = raw.get(name, f.get("default"))
        if val in (None, "", []) and typ != "boolean":
            if f.get("required"):
                problems.append(f"{f['label']} is required")
            out[name] = [] if typ == "list" else None
            continue
        try:
            if typ in ("text", "string", "member"):
                val = str(val).strip()
                if typ == "string" and "\n" in val:
                    val = " ".join(val.split())
                limit = f.get("max_length") or (20000 if typ == "text" else 500)
                if len(val) > limit:
                    problems.append(f"{f['label']} is longer than {limit} characters")
            elif typ == "number":
                val = float(val)
                if val.is_integer():
                    val = int(val)
                if "min" in f and val < f["min"]:
                    problems.append(f"{f['label']} is below {f['min']}")
                if "max" in f and val > f["max"]:
                    problems.append(f"{f['label']} is above {f['max']}")
            elif typ == "boolean":
                val = val if isinstance(val, bool) else str(val).lower() in ("1", "true", "yes", "on")
            elif typ == "list":
                if isinstance(val, str):
                    val = [v.strip() for v in re.split(r"[\n,]", val) if v.strip()]
                val = [str(v).strip() for v in val if str(v).strip()]
            elif typ == "date":
                val = date.fromisoformat(str(val)[:10]).isoformat()
            elif typ == "datetime":
                val = datetime.fromisoformat(str(val).replace("Z", "+00:00")).isoformat()
            elif typ == "choice":
                val = str(val)
            elif typ == "reference":  # Type/id; that it exists, is a target and is about the run's record: Engine
                val = str(val).strip()
                if not REF.fullmatch(val):
                    problems.append(f"{f['label']}: {val[:40]!r} is not a record (Type/id)")
                    continue
            allowed = choices_for(f, ctx)
            if allowed is not None:
                vals = val if isinstance(val, list) else [val]
                bad = [v for v in vals if v not in [str(a) for a in allowed]]
                if bad:
                    problems.append(f"{f['label']}: {', '.join(map(str, bad))} is not one of the allowed values")
        except (TypeError, ValueError):
            problems.append(f"{f['label']}: {str(val)[:40]!r} is not a valid {typ}")
            continue
        out[name] = val
    if problems:
        raise FieldError(problems)
    return out


def json_schema(fields: list[dict], ctx: dict | None = None) -> dict:
    """A JSON Schema for the fields, given to models that support structured output."""
    props, required = {}, []
    for f in fields:
        typ = f["type"]
        if typ == "number":
            p = {"type": "number"}
        elif typ == "boolean":
            p = {"type": "boolean"}
        elif typ == "list":
            p = {"type": "array", "items": {"type": "string"}}
        else:
            p = {"type": "string"}
        allowed = choices_for(f, ctx or {})
        if allowed is not None:
            if typ == "list":
                p["items"] = {"type": "string", "enum": [str(a) for a in allowed]}
            else:
                p["enum"] = [str(a) for a in allowed]
        if f.get("help"):
            p["description"] = f["help"]
        props[f["name"]] = p
        if f.get("required"):
            required.append(f["name"])
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}
