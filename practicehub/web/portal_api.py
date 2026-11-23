"""The hub's side of the online portal: ``/api/portal/<op>``, JSON in and out, for the portal's own process only.

The portal's pages (``portal_app.py``) run apart from the hub, on the internet side, and hold no database and no secret
of the hub's: only a token of the ``[portal] member`` service with scope ``portal``, which opens this and nothing
else (and no other token opens this). Everything a person does there comes here: the codes, the sessions, the counts,
the bookings (through workflows) and the track are the hub's, in ``practicehub/portal.py``.

Every operation is a POST. A person's session is the opaque string the hub gave at sign-in; the portal keeps it in
the person's cookie and sends it back. ``client`` is the person's address as the portal saw it (it is counted, never
trusted for anything else). A form that changes something carries the session's ``form`` (against cross-site
posts) and its own ``submit`` (the same form sent twice gets one answer: ``done``)."""

from __future__ import annotations

import json
import secrets

from ..hub import Denied, HubError, Invalid, NotFound
from ..portal import Clash, Portal, config

PREFIX = "/api/portal/"


def _json(status: int, data: dict) -> tuple[int, dict, bytes]:
    return status, {"Content-Type": "application/json"}, json.dumps(data, default=str).encode()


class Unauthenticated(HubError):
    pass


def _portal(app) -> Portal:
    """The hub's portal for its pack as loaded now (a pack upgraded or modules changed: made again)."""
    key = (id(app.hub.pack), app.dev)
    got = getattr(app, "_portal", None)
    if got is None or got[0] != key:
        got = (key, Portal(app.hub, dev=app.dev))
        app._portal = got
    return got[1]


def handle(app, method: str, path: str, headers: dict, body: bytes) -> tuple[int, dict, bytes]:
    hub = app.hub
    if method != "POST":
        return _json(405, {"error": "POST only"})
    auth = str(headers.get("authorization") or "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    me = hub.authenticate(token, scope="portal") if token else None
    cfg = config(hub.pack)
    if me is None or not cfg or me["id"] != cfg.get("member"):
        return _json(401, {"error": "a portal token of the practice's [portal] member"})
    try:
        p = _portal(app)
    except ValueError as exc:
        print(f"portal closed: {exc}")
        return _json(503, {"error": "closed", "says": "Online booking is closed for now. Call the practice."})
    why = p.closed()
    if why:
        print(f"portal closed: {why}")
        return _json(503, {"error": "closed", "says": p.say("closed")})
    try:
        data = json.loads(body or b"{}")
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        return _json(400, {"error": "a JSON object"})
    op = path[len(PREFIX):].strip("/")
    fn = OPS.get(op)
    if fn is None:
        return _json(404, {"error": f"no portal operation {op!r}"})
    try:
        return _json(200, fn(p, data) or {})
    except Clash as exc:
        return _json(409, {"error": "clash", "says": p.say("taken"), "slots": exc.slots})
    except Unauthenticated as exc:
        return _json(401, {"error": "signed_out", "says": str(exc)})
    except Invalid as exc:
        return _json(422, {"error": "invalid", "says": str(exc), "problems": list(exc.problems or [])})
    except Denied as exc:
        return _json(403, {"error": "denied", "says": str(exc)})
    except NotFound as exc:
        return _json(404, {"error": "not_found", "says": str(exc)})


def _s(data: dict, key: str) -> str:
    v = data.get(key)
    return str(v)[:500] if v is not None else ""


def _session(p: Portal, data: dict, *, form: bool = False) -> dict:
    row = p.session(_s(data, "session"))
    if row is None:
        raise Unauthenticated("Your session has ended. Sign in again.")
    if form and not secrets.compare_digest(str(row.get("form") or ""), _s(data, "form")):
        raise Invalid("This form has expired. Open the page again.")
    return row


def op_config(p: Portal, data: dict) -> dict:
    return p.public()


def op_sign_in(p: Portal, data: dict) -> dict:
    return p.begin_sign_in(_s(data, "born"), _s(data, "identifier"), client=_s(data, "client") or None)


def op_code(p: Portal, data: dict) -> dict:
    return {"session": p.finish_sign_in(_s(data, "challenge"), _s(data, "code"), client=_s(data, "client") or None)}


def op_session(p: Portal, data: dict) -> dict:
    row = _session(p, data)
    return {"form": row["form"], "expires_at": row["expires_at"]}


def op_sign_out(p: Portal, data: dict) -> dict:
    p.end_session(_s(data, "session"))
    return {}


def op_me(p: Portal, data: dict) -> dict:
    row = _session(p, data)
    return {"form": row["form"], "upcoming": p.upcoming(row["record"]), "requests": p.open_requests(row["record"]),
            "limits": p.limits(row["record"])}


def op_slots(p: Portal, data: dict) -> dict:
    row = _session(p, data)
    kid = _s(data, "kind")
    return {"slots": p.free_slots(kid), "limit": p.limits(row["record"]).get(kid)}


def op_book(p: Portal, data: dict) -> dict:
    row = _session(p, data, form=True)
    answers = data.get("answers") if isinstance(data.get("answers"), dict) else {}
    answers = {str(k)[:64]: str(v)[:4000] for k, v in answers.items()}
    kid, slot = _s(data, "kind"), _s(data, "slot") or None
    p.kind(kid)
    return {"done": p.once(row["record"], _s(data, "submit"), "book",
                           lambda: p.book(row["record"], kid, slot, answers))}


def op_cancel(p: Portal, data: dict) -> dict:
    row = _session(p, data, form=True)
    ref = _s(data, "booking")

    def go():
        b = p.cancel(row["record"], ref)
        return {"when": b["when"], "kind": b["kind"]}
    return {"done": p.once(row["record"], _s(data, "submit"), "cancel", go)}


def op_done(p: Portal, data: dict) -> dict:
    person = None
    if data.get("session"):
        person = _session(p, data)["record"]
    return p.answer(person, _s(data, "id"))


def op_register(p: Portal, data: dict) -> dict:
    values = data.get("values") if isinstance(data.get("values"), dict) else {}
    values = {str(k)[:64]: str(v)[:200] for k, v in values.items()}
    client = _s(data, "client") or None

    def go():
        p.register(values, client=client)
        return {"requested": True}
    return {"done": p.once(None, _s(data, "submit"), "register", go)}


OPS = {"config": op_config, "sign-in": op_sign_in, "code": op_code, "session": op_session, "sign-out": op_sign_out,
       "me": op_me, "slots": op_slots, "book": op_book, "cancel": op_cancel, "done": op_done,
       "register": op_register}
