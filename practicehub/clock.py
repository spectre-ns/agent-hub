"""The practice's clock: "now" and "today" are the practice's own, in its time zone (``[practice] timezone``, which a
policy owner can change on the practice settings page), never the server's. A hub in a UTC container for a clinic in
Halifax still dates a note signed at 20:30 with today's date.

How times are kept:
  * system times (the track, work items' created/due/escalation times, sessions, the outbox) are instants, stored in
    UTC as ISO text ending in ``Z`` (``store.utcnow``); a page shows them as they are;
  * clinical and calendar times (a booking's start and end, a visit's period, a note's date, ``valid_until``, the
    ``today`` an expression or a view compares with) are the practice's local wall time, stored without an offset
    (``2026-11-23T09:30``, ``2026-11-23``), the way the practice reads them and the way existing data was written.
    The zone they are in is the practice's ``timezone``; a change of it is on the track (``practice.changed``).
    ``utc`` turns such a wall time into an instant when the two meet (a step's due time, the clock's tick).

Only the standard library: ``zoneinfo`` with the system's tz database (the container image installs ``tzdata``).
A practice whose zone is unknown, or a machine without tz data, fails when the hub opens, not later with wrong dates.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_ZONE = "UTC"  # a hub with no practice installed yet (a practice pack always names its zone)


class ZoneError(ValueError):
    """The practice's time zone cannot be used: an unknown name, or no tz data on this machine."""


def zone(name: str | None) -> ZoneInfo:
    """The time zone called ``name`` (``America/Halifax``). Refuses, loudly, a name the tz database does not know,
    and a machine with no tz database at all (a slim container without ``tzdata``)."""
    name = (name or DEFAULT_ZONE).strip()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        try:
            ZoneInfo("America/Halifax")
        except (ZoneInfoNotFoundError, ValueError):
            raise ZoneError(f"this machine has no time zone data, so the practice's time zone {name!r} cannot be "
                            "used: install the system's tzdata package (the container image does)") from exc
        raise ZoneError(f"the practice's time zone {name!r} is not a time zone name like America/Halifax") from exc


def local(t: datetime, tz: ZoneInfo) -> datetime:
    """``t`` as the practice's wall time, without an offset. A naive ``t`` already is one; an aware one (an
    instant, say from the track) is converted."""
    return t.astimezone(tz).replace(tzinfo=None) if t.tzinfo else t


def now(tz: ZoneInfo) -> datetime:
    """The practice's wall time now, without an offset (as its calendar and records keep times)."""
    return datetime.now(tz).replace(tzinfo=None)


def utc(t: datetime, tz: ZoneInfo) -> datetime:
    """``t`` as an aware UTC instant. A naive ``t`` is the practice's wall time (``fold=0``: in the hour the clocks
    go back, the first of the two)."""
    return (t if t.tzinfo else t.replace(tzinfo=tz)).astimezone(timezone.utc)


def stamp(t: datetime, tz: ZoneInfo) -> str:
    """``t`` as the track writes times: UTC, ISO, ending in ``Z``."""
    return utc(t, tz).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def shown(value, tz: ZoneInfo) -> str:
    """A time as a page shows it, ``YYYY-MM-DD HH:MM`` in the practice's zone: an instant (``...Z``, or with an
    offset) is converted; a wall time without an offset already is the practice's. Anything else as it is."""
    v = str(value or "")
    if len(v) < 16 or v[10:11] != "T":
        return v
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return v[:16].replace("T", " ")
    return local(t, tz).strftime("%Y-%m-%d %H:%M")
