"""Exact numbers.

A ``decimal`` field (``schema.py``) holds a ``Decimal`` with the field's places: 12.30 is kept as 12.30, never as the
float nearest to it. The database stores it as Decimal128 and gives it back as a ``Decimal`` with its places
(``CODECS``, set on the store), so a record reads back as it was written and its hash does not move. Out of the hub it
travels as exact text ("12.30"): JSON (``jsonable``), CSV, the sandbox (``tag``/``untag``). Only FHIR, whose
``valueDecimal`` is a JSON number, gets a number.

Rounding is half up, away from zero (12.345 is 12.35, -2.345 is -2.35). A value has at most ``DIGITS`` significant
digits. A float is read through its shortest text (0.1 is 0.1, not 0.1000000000000000055...); true and false are not
numbers; neither is NaN or infinity.
"""

from __future__ import annotations

import json
import re
import secrets
from decimal import ROUND_HALF_UP, Context, Decimal, DivisionByZero, InvalidOperation, Overflow

from bson.codec_options import TypeCodec, TypeRegistry
from bson.decimal128 import Decimal128

DIGITS = 15     # significant digits a value may have
MAX_SCALE = 10  # places a field may keep
CTX = Context(prec=34, rounding=ROUND_HALF_UP, traps=[InvalidOperation, DivisionByZero, Overflow])

_PLAIN = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)")
_GROUPED = re.compile(r"[+-]?\d{1,3}(,\d{3})+(\.\d*)?")  # 1,234.50: the commas are only grouping


def to_decimal(value, scale: int | None = None) -> Decimal:
    """``value`` as an exact number, rounded half up to ``scale`` places when one is given. Raises ValueError, saying
    why, for anything that is not a number written plainly (or with grouping commas)."""
    if isinstance(value, bool):
        raise ValueError("a number, not true or false")
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, Decimal128):
        d = value.to_decimal()
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, float):
        d = Decimal(repr(value)) if value == value and value not in (float("inf"), float("-inf")) else Decimal("NaN")
    elif isinstance(value, str):
        t = value.strip()
        if _GROUPED.fullmatch(t):
            t = t.replace(",", "")
        if not _PLAIN.fullmatch(t):
            raise ValueError(f"{value!r} is not a number (write it as 1234.50)")
        d = Decimal(t)
    else:
        raise ValueError("a number")
    if not d.is_finite():
        raise ValueError("a number (not NaN or infinity)")
    if scale is not None:
        try:
            d = d.quantize(Decimal(1).scaleb(-scale), context=CTX)
        except InvalidOperation:
            raise ValueError(f"at most {DIGITS} digits") from None
    if d.is_zero():
        d = d.copy_abs()  # -0.00 is 0.00
    if len(d.as_tuple().digits) > DIGITS and not d.is_zero():
        raise ValueError(f"at most {DIGITS} digits")
    return d


def number(value):
    """A number to compute with: an int stays an int; a float is read through its text; a Decimal stays. Anything
    else (true or false, text, nothing) is None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, Decimal)):
        return value
    if isinstance(value, Decimal128):
        return value.to_decimal()
    if isinstance(value, float):
        return Decimal(repr(value)) if value == value and abs(value) != float("inf") else None
    return None


def text(d: Decimal) -> str:
    """Its exact text, with its places, never in exponent form: 12.30, 100, 0.005."""
    return format(d, "f")


def example(scale: int) -> str:
    """How a value of a field with ``scale`` places is written: 1234.50."""
    return "1234" + ("." + "5" + "0" * (scale - 1) if scale else "")


def jsonable(o):
    """``default=`` for json.dumps: a Decimal goes out as its exact text. Anything else is not JSON."""
    if isinstance(o, Decimal):
        return text(o)
    if isinstance(o, Decimal128):
        return text(o.to_decimal())
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def number_json(obj, **kw) -> str:
    """JSON in which an exact number is a JSON number, written as its exact text (12.30, not "12.30"): for a reader
    whose numbers are numbers, such as a FHIR server (FHIR's decimal keeps its places as written)."""
    marks: dict[str, str] = {}
    salt = secrets.token_hex(8)  # no text a record holds can pass for a mark

    def mark(o):
        if isinstance(o, Decimal128):
            o = o.to_decimal()
        if isinstance(o, Decimal):
            k = f"\x00{salt}:{len(marks)}\x00"
            marks[json.dumps(k)] = text(o)
            return k
        return jsonable(o)
    out = json.dumps(obj, default=mark, **kw)
    for k, v in marks.items():
        out = out.replace(k, v)
    return out


TAG = "$decimal"


def tag(obj):
    """A copy of ``obj`` that plain JSON carries exactly: each Decimal becomes {"$decimal": "12.30"}."""
    if isinstance(obj, Decimal):
        return {TAG: text(obj)}
    if isinstance(obj, dict):
        return {k: tag(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [tag(v) for v in obj]
    return obj


def untag(obj):
    """``tag`` undone: each {"$decimal": "12.30"} is a Decimal again (a malformed one stays as it is)."""
    if isinstance(obj, dict):
        if set(obj) == {TAG} and isinstance(obj[TAG], str):
            try:
                return to_decimal(obj[TAG])
            except ValueError:
                return obj
        return {k: untag(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [untag(v) for v in obj]
    return obj


class DecimalCodec(TypeCodec):
    """Decimal to Decimal128 and back, places kept."""
    python_type = Decimal
    bson_type = Decimal128

    def transform_python(self, value):
        return Decimal128(value)

    def transform_bson(self, value):
        return value.to_decimal()


REGISTRY = TypeRegistry([DecimalCodec()])
