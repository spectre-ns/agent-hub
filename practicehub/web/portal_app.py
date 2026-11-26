"""The portal's pages (``practicehub portal serve``): a process of its own, apart from the staff hub, meant to face the
internet while the hub stays inside.

It holds no database, no hub secret and no staff session: only the address of the hub's private listener and a
token of the practice's ``[portal] member`` with scope ``portal``, which opens the hub's ``/api/portal/`` and nothing
else. Each page asks the hub (``portal_api.py``); codes, sessions, counts, bookings and the track stay there. Its own
cookies (the session the hub gave, opaque here), the hub's stylesheet, no scripts: every page works without
JavaScript. What it says is the pack's ``[portal]``, as the hub gives it."""

from __future__ import annotations

import html
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from http import cookies

from .assets import CSS
from .server import CLIENT_HEADER, FORBIDDEN_PORTS, client_address, make_handler
from .. import decimals

e = html.escape
SESSION = "php_session"
SIGNIN = "php_signin"
MAX_FORM = 64 * 1024        # a portal form is small: anything bigger is refused before it is read
TIMEOUT = 30                # seconds a connection may sit idle, and a call to the hub may take
CONFIG_SECONDS = 60         # how long the hub's words are kept before they are asked for again
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]

PORTAL_CSS = """
.portal{max-width:640px;margin:0 auto;padding:16px;box-sizing:border-box}
.portal *{box-sizing:border-box}
.portal header form.inline .linkish{min-height:44px}
.portal header{display:flex;flex-wrap:wrap;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid var(--line,#ddd)}
.portal header .spacer{flex:1}
.portal footer{margin-top:32px;padding-top:12px;border-top:1px solid var(--line,#ddd);font-size:.9em}
.portal .slots{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:6px;margin:6px 0 14px}
.portal .slots label{display:flex;gap:6px;align-items:center;padding:10px 8px;border:1px solid var(--line,#ccc);
  border-radius:6px;min-height:44px}
.portal .advice{font-size:1.2em;font-weight:600;padding:16px;border-radius:8px;border:2px solid currentColor;
  margin:16px 0}
.portal .advice.alarm{color:var(--danger-fg);background:var(--danger-bg)}
.portal .advice.calm{font-size:1em;font-weight:400;border-width:1px}
.portal .advice h2{margin:0 0 8px;font-size:1.25em;color:inherit}
.portal .advice p{margin:6px 0}
.portal .dev{border:2px dashed var(--warning-fg);color:var(--warning-fg);padding:10px;border-radius:6px;margin:12px 0}
.portal form.inline{display:inline}
.portal fieldset{border:0;padding:0;margin:0 0 12px;min-width:0}
.portal label,.portal legend{font-size:16px;color:var(--fg)}
.portal label.q,.portal legend.q{display:block;margin:16px 0 4px;font-weight:600;font-size:18px}
.portal .req{font-weight:400;color:var(--subtle)}
.portal .help{display:block;margin:2px 0 6px;font-size:16px;color:var(--subtle)}
.portal .err{display:block;color:var(--danger-fg);font-weight:600;margin:2px 0 4px}
.portal input[type=text],.portal textarea,.portal select{width:100%;max-width:100%;min-height:44px;font-size:16px}
.portal .dob{display:flex;gap:8px}
.portal .dob>div{flex:1}
.portal .dob>div.month{flex:2}
.portal .btn{min-height:44px}
.portal .linkish{background:none;border:0;padding:8px 4px;min-height:44px;color:var(--link,var(--brand));
  text-decoration:underline;font:inherit;font-size:16px;cursor:pointer}
.portal .hero{padding:20px;border-radius:10px;border:2px solid var(--success-fg);background:var(--success-bg);
  color:var(--fg);margin:16px 0}
.portal .hero .big{font-size:1.5em;font-weight:700;margin:0 0 6px}
.portal .hero p{margin:6px 0}
.portal .bookings{list-style:none;padding:0;margin:0;display:grid;gap:10px}
.portal .booking{border:1px solid var(--border,var(--line));border-radius:8px;padding:12px 14px;background:var(--surface,transparent)}
.portal .booking .when{font-size:1.1em;font-weight:700;margin:0 0 4px}
.portal .booking p{margin:4px 0}
.portal .booking .why{color:var(--subtle)}
.portal .hi{margin:0 0 8px;font-size:1.1em}
.portal .days{display:grid;grid-template-columns:repeat(auto-fill,minmax(128px,1fr));gap:6px;margin:6px 0 12px}
.portal .days .btn{text-align:left;display:flex;flex-direction:column;align-items:flex-start;padding:8px 10px;height:auto;white-space:normal;line-height:1.3}
.portal .days .dname{font-weight:inherit}
.portal .days .dcount{font-size:14px;color:var(--subtle);font-weight:400}
.portal .days .btn.on{outline:3px solid var(--focus,var(--brand));outline-offset:1px;font-weight:700}
.portal h4{margin:10px 0 4px;font-size:1em}
.portal .slot-none{display:flex;gap:6px;align-items:center;min-height:44px}
.portal .enter-default{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}
.portal .limit{display:block;padding:8px 12px;margin:8px 0;border-radius:6px;background:var(--warning-bg);color:var(--fg)}
"""


# ------------------------------------------------------------------------------------------ talking to the hub
class HubSaid(Exception):
    """The hub answered with an error: its status and what it said (``says``, ``problems``, ``slots``)."""

    def __init__(self, status: int, data: dict):
        super().__init__(data.get("says") or data.get("error") or f"the hub answered {status}")
        self.status, self.data = status, data


def _answer(status: int, payload: bytes) -> dict:
    try:
        data = json.loads(payload or b"{}")
    except ValueError:
        data = {"error": "not JSON"}
    if status != 200:
        raise HubSaid(status, data if isinstance(data, dict) else {})
    return data


class HubClient:
    """The hub's ``/api/portal/``, over the private link, with the portal's token."""

    def __init__(self, url: str, token: str, timeout: float = TIMEOUT):
        self.url, self.token, self.timeout = url.rstrip("/"), token, timeout

    def call(self, op: str, data: dict | None = None) -> dict:
        req = urllib.request.Request(f"{self.url}/api/portal/{op}", data=json.dumps(data or {}, default=decimals.jsonable).encode(),
                                     method="POST", headers={"Authorization": f"Bearer {self.token}",
                                                             "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return _answer(r.status, r.read())
        except urllib.error.HTTPError as exc:
            return _answer(exc.code, exc.read())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HubSaid(503, {"error": "unreachable", "says": f"the hub did not answer ({exc})"}) from None


class LocalClient:
    """The same, to a hub app in this process (tests, and a dev hub run in one process)."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token

    def call(self, op: str, data: dict | None = None) -> dict:
        status, _, payload = self.app.handle("POST", f"/api/portal/{op}", {
            "authorization": f"Bearer {self.token}", "content-type": "application/json"}, json.dumps(data or {}, default=decimals.jsonable).encode())
        return _answer(status, payload)


# ------------------------------------------------------------------------------------------ the pages
class Done(Exception):
    """A response other than a page: (status, headers)."""

    def __init__(self, status: int, headers: dict):
        self.status, self.headers = status, headers


class _Page(Exception):
    """A page shown under an error status (a form again, with what went wrong)."""

    def __init__(self, status: int, page: str):
        self.status, self.page = status, page


def _host(h: str) -> str:
    h = (h or "").strip().lower()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def _and(xs: list[str]) -> str:
    """'a', 'a and b', 'a, b and c'."""
    return xs[0] if len(xs) == 1 else f'{", ".join(xs[:-1])} and {xs[-1]}'


class PortalApp:
    """The portal's web pages. ``handle`` has the staff app's shape, so ``make_handler`` serves it."""

    def __init__(self, client, *, dev: bool = False, public_origin: str | None = None):
        self.client, self.dev = client, dev
        self._public_origin = public_origin
        self._cfg, self._cfg_at = None, 0.0

    # --- the hub's words
    @property
    def cfg(self) -> dict:
        if self._cfg is None or time.monotonic() - self._cfg_at > CONFIG_SECONDS:
            self._cfg, self._cfg_at = self.client.call("config"), time.monotonic()
        return self._cfg

    def say(self, key: str, **values) -> str:
        text = str(self.cfg["wording"].get(key) or "")
        for k, v in values.items():
            text = text.replace("{" + k + "}", str(v))
        return text

    def kind(self, kid: str) -> dict:
        for k in self.cfg["kinds"]:
            if k["id"] == kid:
                return k
        raise _Page(404, self.page(None, "Not found", '<p>There is no such page. <a href="/">Start again</a>.</p>'))

    @property
    def public_origin(self) -> str | None:
        return self._public_origin or (self._cfg or {}).get("public_origin") or None

    # --- plumbing
    def handle(self, method: str, raw_path: str, headers: dict, body: bytes) -> tuple[int, dict, bytes]:
        url = urllib.parse.urlsplit(raw_path)
        path = url.path.rstrip("/") or "/"
        req = {"method": method, "path": path, "query": dict(urllib.parse.parse_qsl(url.query)),
               "headers": headers, "body": body, "client": headers.get(CLIENT_HEADER), "cookies": {}, "me": None}
        jar = cookies.SimpleCookie()
        try:
            jar.load(headers.get("cookie", ""))
        except cookies.CookieError:
            pass
        req["cookies"] = {k: v.value for k, v in jar.items()}
        sec = {"Content-Security-Policy": "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; "
                                          "frame-ancestors 'none'; base-uri 'none'",
               # Referrers go to the portal's own pages and nowhere else. Not "no-referrer": under it a browser
               # tells a form's origin as "null", and every form posted from these pages would be refused below.
               "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin"}
        text = {"Content-Type": "text/plain; charset=utf-8", **sec}
        if self.dev and (headers.get("x-forwarded-for") or _host(headers.get("host", "")) not in LOOPBACK):
            # a dev portal shows codes on screen: it answers this machine only, never through a proxy
            return 403, text, b"Refused: a development portal answers only on this machine, with no proxy in front."
        try:
            if method == "GET" and path == "/static/portal.css":
                return 200, {"Content-Type": "text/css; charset=utf-8", **sec}, (CSS + PORTAL_CSS).encode()
            if method == "POST" and not self._same_origin(headers):
                return 403, text, b"Refused: not from this site."
            if method == "POST" and len(body or b"") > MAX_FORM:
                return 413, text, b"Too large."
            fn = self._route(method, path)
            if fn is None:
                return 404, {"Content-Type": "text/html; charset=utf-8", **sec}, self.page(
                    req, "Not found", '<p>There is no such page. <a href="/">Start again</a>.</p>').encode()
            page = fn(req)
            return 200, {"Content-Type": "text/html; charset=utf-8", **sec}, page.encode()
        except Done as d:
            return d.status, {**d.headers, **sec}, b""
        except _Page as p:
            return p.status, {"Content-Type": "text/html; charset=utf-8", **sec}, p.page.encode()
        except HubSaid as exc:
            if exc.status == 401 and exc.data.get("error") == "signed_out":
                return 303, {"Location": "/sign-in", "Set-Cookie": [self.cookie(SESSION, "", 0)], **sec}, b""
            if exc.status in (503, 401) or exc.status >= 500:
                closed = exc.data.get("error") == "closed"
                if not closed:
                    print(f"portal: the hub answered {exc.status}: {exc}")
                words = exc.data.get("says") if closed else "Online booking is not working just now. Call the practice."
                return 503, {"Content-Type": "text/html; charset=utf-8", "Retry-After": "60", **sec}, \
                    self.bare("Closed for now", f'<p class="warn">{e(words or "")}</p>').encode()
            title = "Not possible here" if exc.status == 403 else "Not found"
            safety = f'<p class="safety"><strong>{e(self.say("safety"))}</strong></p>' if exc.status == 403 else ""
            return exc.status, {"Content-Type": "text/html; charset=utf-8", **sec}, self.page(
                req, title, f'<p class="warn" role="alert">{e(str(exc))}</p>{safety}<p><a href="/">Start again</a></p>'
            ).encode()
        except Exception:  # a bug: no detail to the person; the traceback goes to stderr
            import traceback
            traceback.print_exc()
            return 500, text, b"Something went wrong. Call the practice."

    def _route(self, method, path):
        routes = {("GET", "/"): self.home, ("GET", "/sign-in"): self.sign_in_form, ("POST", "/sign-in"): self.sign_in,
                  ("GET", "/code"): self.code_form, ("POST", "/code"): self.code, ("GET", "/me"): self.me_page,
                  ("GET", "/cancel"): self.cancel_form, ("POST", "/cancel"): self.cancel,
                  ("POST", "/sign-out"): self.sign_out, ("GET", "/new"): self.new_form, ("POST", "/new"): self.new}
        if (method, path) in routes:
            return routes[(method, path)]
        parts = path.split("/")
        if len(parts) == 3 and parts[1] == "book":
            return (lambda req: self.book_form(req, parts[2])) if method == "GET" else \
                (lambda req: self.book(req, parts[2])) if method == "POST" else None
        if len(parts) == 3 and parts[1] == "done" and method == "GET":
            return lambda req: self.done_page(req, parts[2])
        return None

    def _same_origin(self, headers: dict) -> bool:
        """A form posted from another site is refused (the cookies are SameSite=Strict as well). Behind a proxy the
        address people use is ``public_origin``; otherwise the Host the request came with."""
        origin = headers.get("origin") or ""
        if not origin:
            return True  # no Origin (an old browser, a test): the cookies' SameSite still holds
        if origin == "null":
            return False
        if self.public_origin:
            return origin.rstrip("/") == self.public_origin.rstrip("/")
        return urllib.parse.urlsplit(origin).netloc == (headers.get("host") or "")

    @staticmethod
    def form(req) -> dict:
        return {k: v for k, v in urllib.parse.parse_qsl((req["body"] or b"").decode("utf-8", "replace"),
                                                        keep_blank_values=True)}

    def cookie(self, name: str, value: str, max_age: int | None = None) -> str:
        parts = [f"{name}={value}", "Path=/", "HttpOnly", "SameSite=Strict"]
        if not self.dev:
            parts.append("Secure")
        if max_age is not None:
            parts.append(f"Max-Age={max_age}")
        return "; ".join(parts)

    def redirect(self, to: str, *cookie_list: str):
        h = {"Location": to}
        if cookie_list:
            h["Set-Cookie"] = list(cookie_list)
        raise Done(303, h)

    def me(self, req) -> dict | None:
        """The session's ``form`` (and that it is live), from the hub; None when signed out."""
        if req.get("me") is None and req["cookies"].get(SESSION):
            try:
                req["me"] = self.client.call("session", {"session": req["cookies"][SESSION]})
            except HubSaid as exc:
                if exc.status != 401:
                    raise
                req["me"] = {}
        return req.get("me") or None

    def signed_in(self, req) -> dict:
        s = self.me(req)
        if not s:
            self.redirect("/sign-in")
        return s

    def call(self, req, op: str, **data) -> dict:
        """An operation for the signed-in person: their session goes with it."""
        return self.client.call(op, {"session": req["cookies"].get(SESSION, ""), **data})

    # --- the page
    def bare(self, title: str, body: str) -> str:
        return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(title)}</title>'
                f'<link rel="stylesheet" href="/static/portal.css"></head><body><div class="portal"><main>'
                f'<h1>{e(title)}</h1>{body}</main></div></body></html>')

    def page(self, req, title: str, body: str, head: str = "") -> str:
        cfg = self.cfg
        name = cfg.get("title") or ""
        s = self.me(req) if req else None
        out = ""
        if s:
            out = (f'<form class="inline" method="post" action="/sign-out"><input type="hidden" name="_form" '
                   f'value="{e(s["form"])}"><button class="linkish">Sign out</button></form>')
        dev = ('<p class="dev"><strong>Development portal.</strong> No messages go out from here; do not use it '
               'with real people.</p>') if self.dev else ""
        return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width, initial-scale=1">{head}<title>{e(title)} · '
                f'{e(name)}</title><link rel="stylesheet" href="/static/portal.css"></head><body><div class="portal">'
                f'<header><a href="/"><strong>{e(name)}</strong></a><span class="spacer"></span>{out}</header>'
                f'<main><h1>{e(title)}</h1>{dev}{body}</main>'
                f'<footer class="muted"><p>{e(cfg.get("privacy", ""))}</p>'
                f'{f"<p>{e(cfg["contact_line"])}</p>" if cfg.get("contact_line") else ""}</footer>'
                f'</div></body></html>')

    @staticmethod
    def _problems(problems) -> str:
        if not problems:
            return ""
        return ('<div class="warn" role="alert" id="problems"><p><strong>Error:</strong> please check what is '
                'marked below.</p><ul>' + "".join(f"<li>{e(str(p))}</li>" for p in problems) + "</ul></div>")

    @staticmethod
    def _about(label: str, problems) -> str | None:
        for p in problems or ():
            if str(p).startswith(f"{label}:"):
                return str(p)[len(label) + 1:].strip()
        return None

    @staticmethod
    def _label(fid: str, label: str, required: bool) -> str:
        return (f'<label class="q" for="{e(fid)}">{e(label)}'
                f'{" <span class=req>(required)</span>" if required else ""}</label>')

    def _dob(self, prefix: str, label: str, value: str = "", problem: str | None = None, help: str = "") -> str:
        """A date of birth as day, month and year (the browser fills it from ``bday-*``), never a date picker."""
        y, m, d = (value.split("-") + ["", "", ""])[:3] if value else ("", "", "")
        err = f'<span class="err" id="{prefix}-err">Error: {e(problem)}</span>' if problem else ""
        help_ = f'<span class="help" id="{prefix}-help">{e(help)}</span>' if help else ""
        inv = ' aria-invalid="true"' if problem else ""
        ids = " ".join(x for x in (f"{prefix}-help" if help else "", f"{prefix}-err" if problem else "") if x)
        desc = f' aria-describedby="{ids}"' if ids else ""
        months = '<option value="">Month</option>' + "".join(
            f'<option value="{i:02d}"{" selected" if m == f"{i:02d}" else ""}>{n}</option>'
            for i, n in enumerate(MONTHS, 1))
        return (f'<fieldset><legend class="q">{e(label)} <span class="req">(required)</span></legend>{help_}{err}'
                f'<div class="dob"><div><label for="{prefix}-day">Day</label>'
                f'<input id="{prefix}-day" type="text" name="{prefix}_day" inputmode="numeric" pattern="[0-9]*" '
                f'maxlength="2" autocomplete="bday-day" required value="{e(d.lstrip("0"))}"{inv}{desc}></div>'
                f'<div class="month"><label for="{prefix}-month">Month</label>'
                f'<select id="{prefix}-month" name="{prefix}_month" autocomplete="bday-month" required{inv}{desc}>'
                f'{months}</select></div>'
                f'<div><label for="{prefix}-year">Year</label>'
                f'<input id="{prefix}-year" type="text" name="{prefix}_year" inputmode="numeric" pattern="[0-9]*" '
                f'maxlength="4" autocomplete="bday-year" required value="{e(y)}"{inv}{desc}></div></div></fieldset>')

    @staticmethod
    def _dob_value(f: dict, prefix: str) -> str:
        try:
            return f"{int(f.get(prefix + '_year', '')):04d}-{int(f.get(prefix + '_month', '')):02d}-" \
                   f"{int(f.get(prefix + '_day', '')):02d}"
        except ValueError:
            return ""

    @staticmethod
    def dob_problem(f: dict, prefix: str) -> str | None:
        """What is wrong with a date of birth as typed (day 1 to 31, a month, a 4-digit year from 1900 to this
        year, a day that exists), in words; None when it is a real date."""
        from datetime import date as _date
        d, m, y = (str(f.get(f"{prefix}_{x}", "")).strip() for x in ("day", "month", "year"))
        if not (d or m or y):
            return "enter your date of birth"
        if not d.isdigit() or not 1 <= int(d) <= 31:
            return "the day is a number from 1 to 31"
        if not m.isdigit() or not 1 <= int(m) <= 12:
            return "choose the month"
        this_year = _date.today().year
        if not (y.isdigit() and len(y) == 4 and 1900 <= int(y) <= this_year):
            return f"the year has 4 digits, from 1900 to {this_year}, for example 1984"
        try:
            born = _date(int(y), int(m), int(d))
        except ValueError:
            return f"{MONTHS[int(m) - 1]} {y} has no day {int(d)}"
        if born > _date.today():
            return "the date of birth is in the future"
        return None

    # --- home
    def home(self, req) -> str:
        cfg = self.cfg
        kinds = "".join(f'<li><strong>{e(k["label"])}</strong>'
                        f'{f" <span class=muted>{e(k["description"])}</span>" if k.get("description") else ""}</li>'
                        for k in cfg["kinds"])
        new = '<p>New here? <a href="/new">Ask to register</a>.</p>' if cfg.get("register") else ""
        if self.me(req):
            act = '<p><a class="btn primary" href="/me">Your bookings</a></p>'
        else:
            act = '<p><a class="btn primary" href="/sign-in">Sign in to book</a></p>'
        return self.page(req, "Book online", f'<p>{e(cfg.get("welcome", ""))}</p><ul>{kinds}</ul>{act}{new}')

    # --- signing in
    def sign_in_form(self, req, problems=(), values=None) -> str:
        values = values or {}
        ident = self.cfg["identity"]
        mode = ident.get("identifier_inputmode") or "text"
        bad = dict(values.get("_bad") or {})
        digits = ident.get("digits")
        help_ = ident.get("identifier_help") or (f"{digits} digits, as on your card. Spaces are fine." if digits else "")
        id_err = bad.get("identifier")
        ids = " ".join(x for x in ("identifier-help" if help_ else "", "identifier-err" if id_err else "") if x)
        aria = (f' aria-describedby="{ids}"' if ids else "") + (' aria-invalid="true"' if id_err else "")
        return self.page(req, "Sign in", self._problems(problems) + (
            '<p>We send a code to the contact details we have for you.</p>'
            '<form method="post" action="/sign-in" class="card" novalidate>'
            + self._dob("born", ident["born_label"], values.get("born", ""), bad.get("born"),
                        ident.get("born_help") or "For example 3 February 1984.")
            + self._label("identifier", ident["identifier_label"], True)
            + (f'<span class="help" id="identifier-help">{e(help_)}</span>' if help_ else "")
            + (f'<span class="err" id="identifier-err">Error: {e(id_err)}</span>' if id_err else "")
            + f'<input id="identifier" type="text" name="identifier" required autocomplete="off" maxlength="40" '
              f'inputmode="{e(mode)}" value="{e(values.get("identifier", ""))}"{aria}>'
              '<p><button class="btn primary">Send me a code</button></p></form>'))

    def sign_in(self, req) -> str:
        f = self.form(req)
        born = self._dob_value(f, "born")
        ident = self.cfg["identity"]
        bad = {}
        got = self.dob_problem(f, "born")
        if got:
            bad["born"] = got
        card = re.sub(r"[\s-]", "", f.get("identifier", ""))
        digits = ident.get("digits")
        if not card:
            bad["identifier"] = f"enter your {ident['identifier_label'].lower()}"
        elif digits and (not card.isdigit() or len(card) != int(digits)):
            bad["identifier"] = f"it has {digits} digits; you typed {sum(c.isdigit() for c in card)}"
        if bad:
            names = {"born": ident["born_label"], "identifier": ident["identifier_label"]}
            raw = "-".join(f.get(f"born_{x}", "") for x in ("year", "month", "day"))
            raise _Page(422, self.sign_in_form(req, [f"{names[k]}: {v}" for k, v in bad.items()],
                                               {"born": raw if raw.strip("-") else "", "_bad": bad,
                                                "identifier": f.get("identifier", "")[:40]})) from None
        try:
            got = self.client.call("sign-in", {"born": born, "identifier": f.get("identifier", ""),
                                               "client": req["client"]})
        except HubSaid as exc:
            if exc.status != 403:
                raise
            raise _Page(429, self.sign_in_form(req, [str(exc)], {"born": born})) from None
        c = self.cookie(SIGNIN, got["challenge"], max_age=int(self.cfg["code_minutes"]) * 60)
        if self.dev and got.get("dev_code"):  # a dev hub only: the code is shown, marked so, since nothing sends it
            self.redirect(f"/code?dev={got['dev_code']}", c)
        self.redirect("/code", c)

    def code_form(self, req, problems=()) -> str:
        if not req["cookies"].get(SIGNIN):
            self.redirect("/sign-in")
        dev = req["query"].get("dev") if self.dev else None
        shown = (f'<p class="dev"><strong>Dev only:</strong> the code is {e(dev)}. On a real portal it is sent to '
                 f'the person and never shown here.</p>') if dev and dev.isdigit() else ""
        err = ' aria-invalid="true" aria-describedby="problems"' if problems else ""
        return self.page(req, "Enter your code", self._problems(problems) + (
            f'<p>{e(self.say("sent", minutes=self.cfg["code_minutes"]))}</p>{shown}'
            f'<form method="post" action="/code" class="card">{self._label("code", "Code", True)}'
            f'<input id="code" type="text" name="code" inputmode="numeric" autocomplete="one-time-code" required '
            f'maxlength="12"{err}><p><button class="btn primary">Sign in</button></p></form>'
            f'<p><a href="/sign-in">Ask for a new code</a></p>'))

    def code(self, req) -> str:
        f = self.form(req)
        try:
            got = self.client.call("code", {"challenge": req["cookies"].get(SIGNIN, ""), "code": f.get("code", ""),
                                            "client": req["client"]})
        except HubSaid as exc:
            if exc.status not in (403, 422):
                raise
            raise _Page(422, self.code_form(req, [str(exc)])) from None
        minutes = int(self.cfg.get("session_minutes", 30))
        self.redirect("/me", self.cookie(SESSION, got["session"], max_age=minutes * 60),
                      self.cookie(SIGNIN, "", max_age=0))

    def sign_out(self, req) -> str:
        s = self.me(req)
        if s and secrets.compare_digest(self.form(req).get("_form", ""), s["form"]):
            self.call(req, "sign-out", form=s["form"])
        self.redirect("/", self.cookie(SESSION, "", max_age=0))

    # --- their own page
    def me_page(self, req) -> str:
        got = self.call(req, "me")
        req["me"] = {"form": got["form"]}
        rows = []
        for b in got["upcoming"]:
            act = (f'<p><a class="btn" href="/cancel?{urllib.parse.urlencode({"booking": b["ref"]})}">'
                   f'Cancel this visit</a></p>') if b["can_cancel"] else \
                (f'<p class="why">{e(b["why_not"])}</p>' if b.get("why_not") else "")
            state = f' <span class="muted">({e(b["state"])})</span>' if b["state"] else ""
            rows.append(f'<li class="booking"><p class="when">{e(b["when"])}</p>'
                        f'<p>{e(b["kind"])}{" with " + e(", ".join(b["with"])) if b["with"] else ""}{state}</p>'
                        f'{f"<p>{e(b["next"])}</p>" if b.get("next") else ""}{act}</li>')
        upcoming = f'<ul class="bookings">{"".join(rows)}</ul>' if rows else '<p class="muted">Nothing booked.</p>'
        reqs = got["requests"]
        open_ = ('<h2>Open requests</h2><ul class="bookings">' + "".join(
            f'<li class="booking"><p class="when">{e(r["what"])}</p><p>Sent {e(r["since"])}.</p>'
            f'{f"<p>{e(r["next"])}</p>" if r.get("next") else ""}</li>' for r in reqs) + "</ul>") if reqs else ""
        limits = got.get("limits") or {}

        def full(k):
            lim = limits.get(k["id"]) or {}
            if lim and lim.get("open", 0) >= lim.get("max", 1):
                return f'<span class="limit">{e(self.say("at_limit", open=lim["open"]))}</span>'
            return ""
        kinds = "".join(f'<li><a href="/book/{e(k["id"])}">{e(k["label"])}</a>'
                        f'{f" <span class=muted>{e(k["description"])}</span>" if k.get("description") else ""}'
                        f'{full(k)}</li>' for k in self.cfg["kinds"])
        return self.page(req, "Your bookings", f'<h2>Coming up</h2>{upcoming}{open_}<h2>Book</h2><ul>{kinds}</ul>')

    def cancel_form(self, req) -> str:
        got = self.call(req, "me")
        req["me"] = {"form": got["form"]}
        ref = req["query"].get("booking", "")
        b = next((x for x in got["upcoming"] if x["ref"] == ref and x["can_cancel"]), None)
        if b is None:
            return self.page(req, "Not possible here", '<p class="warn">That visit cannot be cancelled online. Call '
                                                       'the practice.</p><p><a href="/me">Your bookings</a></p>')
        return self.page(req, "Cancel this visit?", (
            f'<p>{e(b["kind"])} on <strong>{e(b["when"])}</strong>'
            f'{" with " + e(", ".join(b["with"])) if b["with"] else ""}.</p>'
            f'<form method="post" action="/cancel"><input type="hidden" name="_form" value="{e(got["form"])}">'
            f'<input type="hidden" name="_submit" value="{secrets.token_urlsafe(16)}">'
            f'<input type="hidden" name="booking" value="{e(ref)}">'
            f'<p><button class="btn danger">Yes, cancel this visit</button> <a href="/me">No, keep it</a></p></form>'))

    def cancel(self, req) -> str:
        self.signed_in(req)
        f = self.form(req)
        got = self.call(req, "cancel", form=f.get("_form", ""), submit=f.get("_submit", ""),
                        booking=f.get("booking", ""))
        self.redirect(f"/done/{urllib.parse.quote(got['done'])}")

    # --- booking
    def _question(self, q: dict, value, problems) -> str:
        name, label = q["name"], q.get("label") or q["name"]
        fid = f"q-{name}"
        req = " required" if q.get("required") else ""
        problem = self._about(label, problems)
        ids = [x for x in (f"{fid}-help" if q.get("help") else "", f"{fid}-err" if problem else "") if x]
        aria = (f' aria-describedby="{" ".join(ids)}"' if ids else "") + (' aria-invalid="true"' if problem else "")
        help_ = f'<span class="help" id="{e(fid)}-help">{e(q["help"])}</span>' if q.get("help") else ""
        err = f'<span class="err" id="{e(fid)}-err">Error: {e(problem)}</span>' if problem else ""
        val = "" if value is None else str(value)
        if q.get("choices"):
            opts = "".join(f'<option value="{e(c)}"{" selected" if c == val else ""}>{e(words)}</option>'
                           for c, words in q["choices"])
            blank = f'<option value=""{" selected" if not val else ""}>Choose one</option>'
            field = f'<select id="{e(fid)}" name="{e(name)}"{req}{aria}>{blank}{opts}</select>'
        else:
            field = (f'<textarea id="{e(fid)}" name="{e(name)}" rows="3" maxlength="2000"{req}{aria}>'
                     f'{e(val)}</textarea>')
        return self._label(fid, label, bool(q.get("required"))) + help_ + err + field

    def _records(self, got: dict, chosen: str = "", problem: bool = False) -> str:
        """Which of the person's own records the kind is about: one radio each, chosen already when there is one."""
        rows = got.get("about") or []
        if not rows:
            return f'<p class="limit" role="status">{e(self.say("nothing_about"))}</p>'
        if not any(r["ref"] == chosen for r in rows):
            chosen = rows[0]["ref"] if len(rows) == 1 else ""
        inv = ' aria-invalid="true" aria-describedby="problems"' if problem else ""
        return (f'<fieldset{inv}><legend class="q">{e(self.say("about"))} <span class=req>(required)</span></legend>'
                + "".join(f'<label><input type="radio" name="_about" value="{e(r["ref"])}"'
                          f'{" checked" if r["ref"] == chosen else ""} required> {e(r["title"])}</label>'
                          for r in rows) + "</fieldset>")

    def _slots(self, k: dict, got: dict, chosen: str = "", problem: bool = False) -> str:
        """Day first: the working days with free time, as buttons that show that day (no script: the form comes back
        with what was written kept), then that day's times, under each person who gives them."""
        mode = k["slot"]
        if mode == "none":
            return ""
        days, day, slots = got.get("days") or [], got.get("day"), got.get("slots") or []
        if not days:
            grid = '<p class="muted">No times are free online just now. Call the practice.</p>'
        else:
            picker = "".join(
                f'<button class="btn day{" on" if d["date"] == day else ""}" name="_day" value="{e(d["date"])}" '
                f'formnovalidate aria-pressed="{"true" if d["date"] == day else "false"}">'
                + "".join(f'<span class="dname">{e(x)}</span>' for x in d["label"].split(", ", 1)) +
                f'<span class="dcount">{d["count"]} {"time" if d["count"] == 1 else "times"} free</span></button>'
                for d in days)
            by: dict[str, list[dict]] = {}
            for s in slots:
                by.setdefault(s["with"], []).append(s)
            when = next((d["label"] for d in days if d["date"] == day), "")
            grid = (f'<p class="help" id="days-help">Choose a day to see its times.</p>'
                    f'<div class="days" role="group" aria-label="Days with free times">{picker}</div>'
                    f'<h3>{e(when)}</h3>' + ("".join(
                        f'<h4>{e(who)}</h4><div class="slots">' + "".join(
                            f'<label><input type="radio" name="slot" value="{e(s["value"])}"'
                            f'{" checked" if s["value"] == chosen else ""}{" required" if mode == "required" else ""}>'
                            f' {e(s["time"])}</label>' for s in mine) + "</div>"
                        for who, mine in by.items()) or '<p class="muted">No times are free on this day now.</p>'))
        if mode == "optional":
            grid = (f'<label class="slot-none"><input type="radio" name="slot" value=""{" checked" if not chosen else ""}>'
                    f' No time now: the practice contacts you</label>{grid}')
        inv = ' aria-invalid="true" aria-describedby="problems"' if problem else ""
        return (f'<fieldset{inv}><legend class="q">Choose a time'
                f'{" <span class=req>(required)</span>" if mode == "required" else ""}</legend>{grid}</fieldset>')

    def book_form(self, req, kid: str, problems=(), values=None, slots=None) -> str:
        """Questions first, then the day and its times: what the person needs is asked before when."""
        self.signed_in(req)
        s = self.me(req)
        k = self.kind(kid)
        values = values or {}
        day = values.get("_day") or (values.get("slot") or "")[:10]
        got = self.call(req, "slots", kind=kid, **({"day": day} if day else {}))
        if slots is not None:
            got = {**got, "slots": [x for x in slots if x.get("date") == got.get("day")] or got.get("slots")}
        lim = got.get("limit") or {}
        full = bool(lim) and lim.get("open", 0) >= lim.get("max", 1)
        qs = "".join(self._question(q, values.get(q["name"]), problems) for q in k["questions"])
        desc = f'<p>{e(k["description"])}</p>' if k.get("description") else ""
        note = (f'<p class="limit" role="status">{e(self.say("at_limit", open=lim["open"]))}</p>'
                f'<p class="safety"><strong>{e(self.say("safety"))}</strong></p>') if full else ""
        rule = ""
        if k.get("cancel_before_hours") and not full and k["slot"] != "none":
            rule = (f'<p class="help">You can cancel online up to {int(k["cancel_before_hours"])} hours before. '
                    f'After that, call the practice.</p>')
        no_slot = any("time" in str(p).lower() for p in problems or ())
        chosen = values.get("slot", "") if (values.get("slot") or "")[:10] == got.get("day") else ""
        times = "" if full else self._slots(k, got, chosen, no_slot)
        about = "" if full or not k.get("about") else self._records(
            got, values.get("_about") or "", self.say("choose_about") in (problems or ()))
        if about and not got.get("about"):  # nothing of theirs it could be about: said so, and no form to send
            return self.page(req, k["label"], desc + about)
        button = "Send to the practice" if full else "Book" if k["mode"] == "book" else "Send my request"
        # The first submit button in a form is what Enter presses: the real one, not a day.
        default = (f'<button class="enter-default" name="_go" value="1" tabindex="-1" aria-hidden="true">'
                   f'{e(button)}</button>') if times else ""
        return self.page(req, k["label"], desc + note + self._problems(problems) + (
            f'<form method="post" action="/book/{e(kid)}" class="card">{default}'
            f'<input type="hidden" name="_form" value="{e(s["form"])}">'
            f'<input type="hidden" name="_submit" value="{e(values.get("_submit") or secrets.token_urlsafe(16))}">'
            f'{about}{qs}{times}{rule}'
            f'<p><button class="btn primary" name="_go" value="1">{e(button)}</button></p></form>'))

    def book(self, req, kid: str) -> str:
        self.signed_in(req)
        f = self.form(req)
        k = self.kind(kid)
        if f.get("_day") and not f.get("_go"):  # a day was chosen: the same form, that day's times, nothing sent
            return self.book_form(req, kid, (), {**f, "slot": ""})
        answers = {q["name"]: f[q["name"]] for q in k["questions"] if f.get(q["name"]) not in (None, "")}
        try:
            got = self.call(req, "book", form=f.get("_form", ""), submit=f.get("_submit", ""), kind=kid,
                            slot=f.get("slot") or None, answers=answers, about=f.get("_about") or None)
        except HubSaid as exc:
            if exc.status == 409:  # the time went: the form again, with that day's times free now, as a new form
                raise _Page(409, self.book_form(req, kid, [self.say("taken")],
                                                {**f, "_day": (f.get("slot") or "")[:10], "slot": "", "_submit": ""},
                                                exc.data.get("slots") or [])) from None
            if exc.status == 422:
                raise _Page(422, self.book_form(req, kid, exc.data.get("problems") or [str(exc)], f)) from None
            raise
        self.redirect(f"/done/{urllib.parse.quote(got['done'])}")

    def done_page(self, req, done: str) -> str:
        """What a form got, after the redirect: the same page however often it is sent or reloaded. A booking's
        always says what to do in an emergency, first, and loudly when the screen found something."""
        data = {"id": done}
        if req["cookies"].get(SESSION):
            data["session"] = req["cookies"][SESSION]
        got = self.client.call("done", data)
        if got["state"] != "done":
            return self.page(req, "Sending your request", (
                f'<div class="advice alarm" role="alert">{e(self.say("emergency"))}</div>'
                f'<p role="status">{e(self.say("working"))}</p><p><a href="/done/{e(done)}">Refresh</a></p>'),
                head='<meta http-equiv="refresh" content="2">')
        r = got.get("result") or {}
        if got["what"] == "cancel":
            return self.page(req, "Cancelled", f'<p class="ok" role="status">'
                                                     f'{e(self.say("cancelled", when=r.get("when", "")))}</p>'
                                                     f'<p><a class="btn primary" href="/me">Your bookings</a></p>')
        if got["what"] == "register":
            return self.page(req, "Request sent", f'<p class="ok" role="status">{e(self.say("registered"))}</p>')
        text = r.get("advice") or self.say("emergency")
        found = [str(x) for x in r.get("found") or []]
        closed = r.get("closed")
        if r.get("alarm"):
            # Loud: no green tick anywhere on this page, a heading of its own, what was found in plain words, and
            # when nobody is at the practice to read it.
            said = (f'<p>You wrote about {e(_and(found))}. That can be serious.</p>' if found else "")
            shut = ((f'<p>The practice is closed now. It opens {e(closed.rstrip("."))}. Do not wait for us.</p>'
                     if closed else "") + f'<p><strong>{e(self.say("safety"))}</strong></p>')
            sent = ("We have not booked anything. A nurse has what you wrote and will call you."
                    if r.get("over_limit") or not r.get("when") else "")
            parts = [f'<div class="advice alarm" role="alert"><h2>What to do now</h2>{said}'
                     f'<p>{e(text)}</p>{shut}</div>']
            if sent:
                parts.append(f'<p>{e(sent)}</p>')
            if r.get("when"):
                parts.append(f'<p>{e(self.say("booked", when=r["when"], **{"with": r.get("with") or ""}))}</p>')
            parts.append('<p><a class="btn" href="/me">Your bookings</a></p>')
            return self.page(req, "Your message needs attention today", "".join(parts))
        if r.get("when") and not r.get("over_limit"):
            hero = (f'<div class="hero" role="status"><p class="big">You are booked</p>'
                    f'<p class="when"><strong>{e(r["when"])}</strong>'
                    f'{" with " + e(r["with"]) if r.get("with") else ""}</p>'
                    f'<p>{e(self.say("visit_next"))}</p></div>')
            return self.page(req, "Booked", hero + f'<div class="advice calm">{e(text)}</div>'
                             '<p><a class="btn primary" href="/me">Your bookings</a></p>')
        parts = [f'<div class="advice" role="alert">{e(text)}</div>']
        if closed and not r.get("when"):
            parts.append(f'<p>The practice is closed now. Someone reads your request after it opens '
                         f'{e(closed.rstrip("."))}.</p>')
        if r.get("over_limit"):
            title = "Not booked: sent to the practice"
            parts.append(f'<p>{e(self.say("over_limit"))}</p>')
        elif r.get("when"):
            title = "Booked"
            parts.append(f'<p class="ok">{e(self.say("booked", when=r["when"], **{"with": r.get("with") or ""}))}</p>')
        elif r.get("failed"):
            title = "Not booked"
            parts.append(f'<p class="warn">{e(self.say("failed"))}</p>')
        else:
            title = "Request sent"
            parts.append(f'<p class="ok">{e(self.say("requested"))}</p>')
        parts.append('<p><a class="btn primary" href="/me">Your bookings</a></p>')
        return self.page(req, title, "".join(parts))

    # --- someone new
    def new_form(self, req, problems=(), values=None) -> str:
        if not self.cfg.get("register"):
            return self.page(req, "Register", "<p>New registrations are not taken online here. Call the practice."
                                              "</p>")
        values = values or {}
        rows = []
        auto = {"born": "", "identifier": "off", "contact": "tel"}
        for fd in self.cfg["register_fields"]:
            name, label = fd["name"], fd["label"]
            if fd["type"] == "date":
                rows.append(self._dob(f"r_{name}", label, values.get(name, ""), self._about(label, problems),
                                      fd.get("help") or "For example 3 February 1984."))
                continue
            problem = self._about(label, problems)
            fid = f"r-{name}"
            ac = auto.get(fd.get("role") or "") or {"given": "given-name", "family": "family-name"}.get(name, "off")
            mode = (self.cfg["identity"].get("identifier_inputmode") if fd.get("role") == "identifier" else
                    "tel" if fd.get("role") == "contact" else "text")
            err = f'<span class="err" id="{e(fid)}-err">Error: {e(problem)}</span>' if problem else ""
            help_ = f'<span class="help" id="{e(fid)}-help">{e(fd["help"])}</span>' if fd.get("help") else ""
            ids = " ".join(x for x in (f"{fid}-help" if help_ else "", f"{fid}-err" if problem else "") if x)
            aria = (f' aria-describedby="{e(ids)}"' if ids else "") + (' aria-invalid="true"' if problem else "")
            rows.append(self._label(fid, label, bool(fd.get("required"))) + help_ + err +
                        f'<input id="{e(fid)}" type="text" name="{e(name)}" maxlength="200" inputmode="{e(mode)}" '
                        f'autocomplete="{e(ac)}"{" required" if fd.get("required") else ""}{aria} '
                        f'value="{e(values.get(name, ""))}">')
        return self.page(req, "Register", self._problems(problems) + (
            '<p>Tell us who you are. This is a request to join the practice: someone there reads it and contacts '
            'you. You can sign in and book once they have taken you in.</p>'
            f'<form method="post" action="/new" class="card" novalidate>'
            f'<input type="hidden" name="_submit" value="{e(values.get("_submit") or secrets.token_urlsafe(16))}">'
            f'{"".join(rows)}<p><button class="btn primary">Send</button></p></form>'))

    def new(self, req) -> str:
        f = self.form(req)
        values = {}
        bad = []
        for fd in self.cfg["register_fields"]:
            values[fd["name"]] = self._dob_value(f, f"r_{fd['name']}") if fd["type"] == "date" else \
                f.get(fd["name"], "")
            if fd["type"] == "date":
                got = self.dob_problem(f, f"r_{fd['name']}")
                if got and (fd.get("required") or any(f.get(f"r_{fd['name']}_{x}") for x in ("day", "month", "year"))):
                    bad.append(f"{fd['label']}: {got}")
                    values[fd["name"]] = "-".join(f.get(f"r_{fd['name']}_{x}", "") for x in ("year", "month", "day"))
        if bad:
            raise _Page(422, self.new_form(req, bad, {**values, "_submit": f.get("_submit", "")}))
        try:
            got = self.client.call("register", {"values": values, "client": req["client"],
                                                "submit": f.get("_submit", "")})
        except HubSaid as exc:
            if exc.status not in (403, 422):
                raise
            raise _Page(422, self.new_form(req, exc.data.get("problems") or [str(exc)],
                                           {**values, "_submit": f.get("_submit", "")})) from None
        self.redirect(f"/done/{urllib.parse.quote(got['done'])}")


def serve(hub_url: str, token: str, host: str = "127.0.0.1", port: int = 8210, *, dev: bool = False,
          public_origin: str | None = None) -> None:
    """The portal, on ``host:port``, a client of the hub at ``hub_url`` (its private listener)."""
    from http.server import ThreadingHTTPServer
    if port in FORBIDDEN_PORTS:
        raise SystemExit(f"port {port} is reserved on this machine; pick another (default 8210)")
    if dev and host not in LOOPBACK:
        raise SystemExit("a development portal shows sign-in codes on screen: it listens on this machine only "
                         "(--host 127.0.0.1)")
    client = HubClient(hub_url, token)
    try:
        cfg = client.call("config")
    except HubSaid as exc:
        raise SystemExit(f"the hub at {hub_url} refused the portal: {exc}") from None
    if cfg.get("dev") != dev:
        raise SystemExit("the portal and the hub disagree about dev: run both with --dev, or neither")
    app = PortalApp(client, dev=dev, public_origin=public_origin)
    httpd = ThreadingHTTPServer((host, port), make_handler(app, max_body=MAX_FORM, timeout=TIMEOUT,
                                                           client=client_address))
    print(f"Practice portal on http://{host}:{port}/, using the hub at {hub_url}"
          + (" (dev: codes are shown on screen)" if dev else ""))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
