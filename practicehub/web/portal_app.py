"""The portal's pages (``practicehub portal serve``): a small app of its own, apart from the staff hub, meant to face
the internet while the hub stays inside. Its own sessions and cookies (never the staff sign-in), the hub's stylesheet,
no scripts. What it does is ``practicehub/portal.py``'s; what it says is the pack's ``[portal]``."""

from __future__ import annotations

import html
import urllib.parse
from http import cookies

from ..hub import Denied, Invalid, NotFound
from ..portal import AlreadyOpen, Clash, Portal
from .assets import CSS
from .server import CLIENT_HEADER, FORBIDDEN_PORTS, make_handler

e = html.escape
SESSION = "php_session"
SIGNIN = "php_signin"
MAX_FORM = 64 * 1024

PORTAL_CSS = """
.portal{max-width:640px;margin:0 auto;padding:16px}
.portal header{display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid var(--line,#ddd)}
.portal header .spacer{flex:1}
.portal footer{margin-top:32px;padding-top:12px;border-top:1px solid var(--line,#ddd);font-size:.9em}
.portal .slots{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:6px;margin:6px 0 14px}
.portal .slots label{display:flex;gap:6px;align-items:center;padding:6px 8px;border:1px solid var(--line,#ccc);
  border-radius:6px}
.portal .advice{font-size:1.25em;font-weight:600;padding:16px;border-radius:8px;border:2px solid currentColor;
  margin:16px 0}
.portal .advice.alarm{color:#9b1c1c;background:#fdecec}
.portal .dev{border:2px dashed #b45309;color:#b45309;padding:10px;border-radius:6px;margin:12px 0}
.portal form.inline{display:inline}
.portal fieldset{border:0;padding:0;margin:0 0 12px}
.portal label.q{display:block;margin:10px 0 4px;font-weight:600}
.portal input[type=text],.portal input[type=date],.portal textarea,.portal select{width:100%;max-width:100%;
  box-sizing:border-box}
"""


class Done(Exception):
    """A response other than a page: (status, headers)."""

    def __init__(self, status: int, headers: dict):
        self.status, self.headers = status, headers


class _Page(Exception):
    """A page shown under an error status (a form again, with what went wrong)."""

    def __init__(self, status: int, page: str):
        self.status, self.page = status, page


class PortalApp:
    """The portal's web pages. ``handle`` has the staff app's shape, so ``make_handler`` serves it."""

    def __init__(self, hub, *, dev: bool = False):
        self.hub, self.dev = hub, dev
        self.portal = Portal(hub, dev=dev)

    # ------------------------------------------------------------------------------------------ plumbing
    def handle(self, method: str, raw_path: str, headers: dict, body: bytes) -> tuple[int, dict, bytes]:
        url = urllib.parse.urlsplit(raw_path)
        path = url.path.rstrip("/") or "/"
        req = {"method": method, "path": path, "query": dict(urllib.parse.parse_qsl(url.query)),
               "headers": headers, "body": body, "client": headers.get(CLIENT_HEADER), "cookies": {}}
        jar = cookies.SimpleCookie()
        try:
            jar.load(headers.get("cookie", ""))
        except cookies.CookieError:
            pass
        req["cookies"] = {k: v.value for k, v in jar.items()}
        sec = {"Content-Security-Policy": "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; "
                                          "frame-ancestors 'none'; base-uri 'none'",
               "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"}
        try:
            if method == "GET" and path == "/static/portal.css":
                return 200, {"Content-Type": "text/css; charset=utf-8", **sec}, (CSS + PORTAL_CSS).encode()
            if method == "POST" and not self._same_origin(headers):
                return 403, {"Content-Type": "text/plain; charset=utf-8", **sec}, b"Refused: not from this site."
            if method == "POST" and len(body or b"") > MAX_FORM:
                return 413, {"Content-Type": "text/plain; charset=utf-8", **sec}, b"Too large."
            fn = self._route(method, path)
            if fn is None:
                return 404, {"Content-Type": "text/html; charset=utf-8", **sec}, self.page(
                    req, "Not found", "<p>There is no such page. <a href=\"/\">Start again</a>.</p>").encode()
            page = fn(req)
            return 200, {"Content-Type": "text/html; charset=utf-8", **sec}, page.encode()
        except Done as d:
            return d.status, {**d.headers, **sec}, b""
        except _Page as p:
            return p.status, {"Content-Type": "text/html; charset=utf-8", **sec}, p.page.encode()
        except (Denied, NotFound) as exc:
            code = 403 if isinstance(exc, Denied) else 404
            return code, {"Content-Type": "text/html; charset=utf-8", **sec}, self.page(
                req, "Not possible here", f'<p class="warn">{e(str(exc))}</p><p><a href="/">Start again</a></p>'
            ).encode()
        except Exception:  # a bug: no detail to the person; the traceback goes to stderr
            import traceback
            traceback.print_exc()
            return 500, {"Content-Type": "text/plain; charset=utf-8", **sec}, b"Something went wrong. Call the practice."

    def _route(self, method, path):
        routes = {("GET", "/"): self.home, ("GET", "/sign-in"): self.sign_in_form, ("POST", "/sign-in"): self.sign_in,
                  ("GET", "/code"): self.code_form, ("POST", "/code"): self.code, ("GET", "/me"): self.me_page,
                  ("POST", "/cancel"): self.cancel, ("POST", "/sign-out"): self.sign_out,
                  ("GET", "/new"): self.new_form, ("POST", "/new"): self.new}
        if (method, path) in routes:
            return routes[(method, path)]
        if path.startswith("/book/") and path.count("/") == 2:
            kid = path.split("/")[2]
            return (lambda req: self.book_form(req, kid)) if method == "GET" else \
                (lambda req: self.book(req, kid)) if method == "POST" else None
        return None

    @staticmethod
    def _same_origin(headers: dict) -> bool:
        """A form posted from another site is refused (the cookies are SameSite=Strict as well)."""
        origin = headers.get("origin") or ""
        if not origin or origin == "null":
            return not origin  # no Origin (an old browser, a test): the cookies' SameSite still holds
        host = headers.get("host") or ""
        return urllib.parse.urlsplit(origin).netloc == host

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

    def session(self, req) -> dict | None:
        return self.portal.session(req["cookies"].get(SESSION))

    def signed_in(self, req) -> dict:
        s = self.session(req)
        if not s:
            self.redirect("/sign-in")
        return s

    def check_form(self, req, s: dict, f: dict) -> None:
        if f.get("_form") != s.get("form"):
            raise Denied("This form has expired. Open the page again.")

    # ------------------------------------------------------------------------------------------ the page
    def page(self, req, title: str, body: str) -> str:
        cfg = self.portal.cfg
        name = cfg.get("title") or self.hub.practice.get("name", "")
        s = self.session(req)
        out = ""
        if s:
            out = (f'<form class="inline" method="post" action="/sign-out"><input type="hidden" name="_form" '
                   f'value="{e(s["form"])}"><button class="btn">Sign out</button></form>')
        dev = ('<p class="dev"><strong>Development portal.</strong> No messages go out from here; do not use it '
               'with real people.</p>') if self.dev else ""
        return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(title)} · {e(name)}'
                f'</title><link rel="stylesheet" href="/static/portal.css"></head><body><div class="portal">'
                f'<header><a href="/"><strong>{e(name)}</strong></a><span class="spacer"></span>{out}</header>'
                f'<main><h1>{e(title)}</h1>{dev}{body}</main>'
                f'<footer class="muted"><p>{e(cfg.get("privacy", ""))}</p>'
                f'{f"<p>{e(cfg["contact_line"])}</p>" if cfg.get("contact_line") else ""}</footer>'
                f'</div></body></html>')

    def _problems(self, problems) -> str:
        if not problems:
            return ""
        return '<div class="warn" role="alert"><ul>' + "".join(f"<li>{e(str(p))}</li>" for p in problems) + \
            "</ul></div>"

    # ------------------------------------------------------------------------------------------ home
    def home(self, req) -> str:
        cfg = self.portal.cfg
        kinds = "".join(f'<li><strong>{e(k["label"])}</strong>'
                        f'{f" <span class=muted>{e(k["description"])}</span>" if k.get("description") else ""}</li>'
                        for k in self.portal.kinds().values())
        new = ('<p>New here? <a href="/new">Ask to register</a>.</p>'
               if cfg.get("intake_workflow") and cfg.get("register") else "")
        if self.session(req):
            act = '<p><a class="btn" href="/me">Your bookings</a></p>'
        else:
            act = '<p><a class="btn" href="/sign-in">Sign in to book</a></p>'
        return self.page(req, "Book online", f'<p>{e(cfg.get("welcome", ""))}</p><ul>{kinds}</ul>{act}{new}')

    # ------------------------------------------------------------------------------------------ signing in
    def sign_in_form(self, req, problems=(), values=None) -> str:
        values = values or {}
        rt = self.portal._rt()
        born = rt.field(self.portal.cfg["identity"]["born"])
        ident_f = rt.field((rt.base_map.get("identifier") or {}).get("fields", [""])[0])
        born_label = (self.portal.cfg["identity"].get("born_label") or (born or {}).get("label") or "Date of birth")
        id_label = (self.portal.cfg["identity"].get("identifier_label") or (ident_f or {}).get("label")
                    or "Identifier")
        return self.page(req, "Sign in", self._problems(problems) + (
            f'<p>We send a code to the contact details we have for you.</p>'
            f'<form method="post" action="/sign-in" class="card">'
            f'<label class="q" for="born">{e(born_label)}</label>'
            f'<input id="born" type="date" name="born" required value="{e(values.get("born", ""))}">'
            f'<label class="q" for="identifier">{e(id_label)}</label>'
            f'<input id="identifier" type="text" name="identifier" required autocomplete="off" maxlength="40">'
            f'<p><button class="btn">Send me a code</button></p></form>'))

    def sign_in(self, req) -> str:
        f = self.form(req)
        got = self.portal.begin_sign_in(f.get("born", ""), f.get("identifier", ""), client=req["client"])
        minutes = self.portal.code_minutes()
        c = self.cookie(SIGNIN, got["challenge"], max_age=minutes * 60)
        if got.get("dev_code"):  # a dev hub only: the code is shown, marked so, since nothing delivers it
            self.redirect(f"/code?dev={got['dev_code']}", c)
        self.redirect("/code", c)

    def code_form(self, req, problems=()) -> str:
        if not req["cookies"].get(SIGNIN):
            self.redirect("/sign-in")
        dev = req["query"].get("dev") if self.dev else None
        shown = (f'<p class="dev"><strong>Dev only:</strong> the code is {e(dev)}. On a real portal it is sent to '
                 f'the person and never shown here.</p>') if dev and dev.isdigit() else ""
        return self.page(req, "Enter your code", self._problems(problems) + (
            f'<p>{e(self.portal.say("sent", minutes=self.portal.code_minutes()))}</p>{shown}'
            f'<form method="post" action="/code" class="card"><label class="q" for="code">Code</label>'
            f'<input id="code" type="text" name="code" inputmode="numeric" autocomplete="one-time-code" required '
            f'maxlength="12"><p><button class="btn">Sign in</button></p></form>'
            f'<p><a href="/sign-in">Ask for a new code</a></p>'))

    def code(self, req) -> str:
        f = self.form(req)
        try:
            raw = self.portal.finish_sign_in(req["cookies"].get(SIGNIN, ""), f.get("code", ""), client=req["client"])
        except Invalid as exc:
            raise _Page(422, self.code_form(req, [str(exc)])) from None
        minutes = int(self.portal.cfg.get("session_minutes", 30))
        self.redirect("/me", self.cookie(SESSION, raw, max_age=minutes * 60), self.cookie(SIGNIN, "", max_age=0))

    def sign_out(self, req) -> str:
        s = self.signed_in(req)
        self.check_form(req, s, self.form(req))
        self.portal.end_session(req["cookies"].get(SESSION))
        self.redirect("/", self.cookie(SESSION, "", max_age=0))

    # ------------------------------------------------------------------------------------------ their own page
    def me_page(self, req, note: str = "") -> str:
        s = self.signed_in(req)
        person = s["record"]
        rows = []
        for b in self.portal.upcoming(person):
            cancel = ""
            if b["can_cancel"]:
                cancel = (f'<form class="inline" method="post" action="/cancel"><input type="hidden" name="_form" '
                          f'value="{e(s["form"])}"><input type="hidden" name="booking" value="{e(b["ref"])}">'
                          f'<button class="btn">Cancel</button></form>')
            state = f' <span class="muted">({e(b["state"])})</span>' if b["state"] else ""
            rows.append(f'<li><strong>{e(b["when"])}</strong>: {e(b["kind"])}'
                        f'{" with " + e(", ".join(b["with"])) if b["with"] else ""}{state} {cancel}</li>')
        upcoming = f'<ul>{"".join(rows)}</ul>' if rows else '<p class="muted">Nothing booked.</p>'
        reqs = self.portal.open_requests(person)
        open_ = ("<h2>Open requests</h2><ul>" + "".join(
            f'<li>{e(r["what"])} <span class="muted">(since {e(r["since"])})</span></li>' for r in reqs) + "</ul>"
                 ) if reqs else ""
        kinds = "".join(f'<li><a href="/book/{e(k["id"])}">{e(k["label"])}</a>'
                        f'{f" <span class=muted>{e(k["description"])}</span>" if k.get("description") else ""}</li>'
                        for k in self.portal.kinds().values())
        msg = f'<p class="ok" role="status">{e(note)}</p>' if note else ""
        return self.page(req, "Your bookings", f'{msg}<h2>Coming up</h2>{upcoming}{open_}<h2>Book</h2><ul>{kinds}</ul>')

    def cancel(self, req) -> str:
        s = self.signed_in(req)
        f = self.form(req)
        self.check_form(req, s, f)
        b = self.portal.cancel(s["record"], f.get("booking", ""))
        return self.me_page(req, self.portal.say("cancelled", when=b["when"]))

    # ------------------------------------------------------------------------------------------ booking
    def _question(self, q: dict, value) -> str:
        name, label = q["name"], q.get("label") or q["name"]
        req = " required" if q.get("required") else ""
        help_ = f'<span class="muted help">{e(q["help"])}</span>' if q.get("help") and q.get("label") else ""
        val = value if value is not None else q.get("default") or ""
        if q.get("type") == "choice":
            opts = "".join(f'<option value="{e(str(c))}"{" selected" if str(c) == str(val) else ""}>'
                           f'{e(str(c).replace("-", " ").capitalize())}</option>' for c in q.get("choices") or [])
            blank = "" if q.get("required") else '<option value=""></option>'
            field = f'<select id="q-{e(name)}" name="{e(name)}"{req}>{blank}{opts}</select>'
        else:
            field = (f'<textarea id="q-{e(name)}" name="{e(name)}" rows="3" maxlength="2000"{req}>'
                     f'{e(str(val))}</textarea>')
        return f'<label class="q" for="q-{e(name)}">{e(label)}</label>{field}{help_}'

    def _slots(self, k: dict, slots: list[dict], chosen: str = "") -> str:
        mode = self.portal.slot_mode(k)
        if mode == "none":
            return ""
        days: dict[str, list[dict]] = {}
        for s in slots:
            days.setdefault(s["day"], []).append(s)
        if not days:
            grid = '<p class="muted">No times are free online just now.</p>'
        else:
            grid = "".join(
                f'<h3>{e(day)}</h3><div class="slots">' + "".join(
                    f'<label><input type="radio" name="slot" value="{e(s["value"])}"'
                    f'{" checked" if s["value"] == chosen else ""}{" required" if mode == "required" else ""}> '
                    f'{e(s["time"])} {e(s["with"])}</label>' for s in day_slots) + "</div>"
                for day, day_slots in days.items())
        if mode == "optional":
            grid = (f'<label><input type="radio" name="slot" value=""{" checked" if not chosen else ""}> '
                    f'No time now: tell us what suits you below</label>{grid}')
        return f'<fieldset><legend><strong>Choose a time</strong></legend>{grid}</fieldset>'

    def book_form(self, req, kid: str, problems=(), values=None, slots=None) -> str:
        s = self.signed_in(req)
        k = self.portal.kind(kid)
        values = values or {}
        slots = self.portal.free_slots(kid) if slots is None else slots
        qs = "".join(self._question(q, values.get(q["name"])) for q in self.portal.questions(k))
        desc = f'<p>{e(k["description"])}</p>' if k.get("description") else ""
        return self.page(req, k["label"], desc + self._problems(problems) + (
            f'<form method="post" action="/book/{e(kid)}" class="card">'
            f'<input type="hidden" name="_form" value="{e(s["form"])}">'
            f'{self._slots(k, slots, values.get("slot", ""))}{qs}'
            f'<p><button class="btn">{"Book" if k["mode"] == "book" else "Send my request"}</button></p></form>'))

    def book(self, req, kid: str) -> str:
        s = self.signed_in(req)
        f = self.form(req)
        self.check_form(req, s, f)
        k = self.portal.kind(kid)
        answers = {q["name"]: f[q["name"]] for q in self.portal.questions(k) if f.get(q["name"]) not in (None, "")}
        try:
            got = self.portal.book(s["record"], kid, f.get("slot"), answers)
        except Clash as c:
            raise _Page(409, self.book_form(req, kid, [self.portal.say("clash")], {**f, "slot": ""}, c.slots)) \
                from None
        except AlreadyOpen:
            raise _Page(409, self.page(req, k["label"], f'<p class="warn" role="alert">{e(self.portal.say("duplicate"))}'
                                                        f'</p><p><a href="/me">Your bookings</a></p>')) from None
        except Invalid as exc:
            raise _Page(422, self.book_form(req, kid, getattr(exc, "problems", None) or [str(exc)], f)) from None
        return self.confirmation(req, k, got)

    def confirmation(self, req, k: dict, got: dict) -> str:
        """What the person is told: the workflow's say-now advice first, and loud when its alarm holds."""
        parts = []
        if got.get("advice") or got.get("alarm"):
            text = got.get("advice") or self.portal.cfg.get("welcome") or ""
            parts.append(f'<div class="advice{" alarm" if got.get("alarm") else ""}" role="alert">{e(text)}</div>')
        if got.get("when"):
            parts.append(f'<p class="ok">{e(self.portal.say("booked", when=got["when"], **{"with": got["with"] or ""}))}'
                         f'</p>')
        elif got.get("failed"):
            parts.append('<p class="warn">We could not book that time. The practice has been told and will contact '
                         'you.</p>')
        else:
            parts.append(f'<p class="ok">{e(self.portal.say("requested"))}</p>')
        parts.append('<p><a class="btn" href="/me">Your bookings</a></p>')
        return self.page(req, "Thank you", "".join(parts))

    # ------------------------------------------------------------------------------------------ someone new
    def new_form(self, req, problems=(), values=None) -> str:
        if not (self.portal.cfg.get("intake_workflow") and self.portal.cfg.get("register")):
            return self.page(req, "Register", "<p>New registrations are not taken online here. Call the practice."
                                              "</p>")
        values = values or {}
        rows = []
        for fd in self.portal.register_fields():
            typ = "date" if fd["type"] == "date" else "text"
            req_ = " required" if fd.get("required") else ""
            rows.append(f'<label class="q" for="r-{e(fd["name"])}">{e(fd.get("label") or fd["name"])}</label>'
                        f'<input id="r-{e(fd["name"])}" type="{typ}" name="{e(fd["name"])}" maxlength="200"{req_} '
                        f'value="{e(values.get(fd["name"], ""))}">')
        return self.page(req, "Register", self._problems(problems) + (
            '<p>Tell us who you are. Someone at the practice reads it and contacts you.</p>'
            f'<form method="post" action="/new" class="card">{"".join(rows)}'
            f'<p><button class="btn">Send</button></p></form>'))

    def new(self, req) -> str:
        f = self.form(req)
        try:
            self.portal.register(f, client=req["client"])
        except Invalid as exc:
            raise _Page(422, self.new_form(req, getattr(exc, "problems", None) or [str(exc)], f)) from None
        return self.page(req, "Thank you", f'<p class="ok">{e(self.portal.say("registered"))}</p>')


def serve(hub, host: str = "127.0.0.1", port: int = 8210, *, dev: bool = False) -> None:
    from http.server import ThreadingHTTPServer
    if port in FORBIDDEN_PORTS:
        raise SystemExit(f"port {port} is reserved on this machine; pick another (default 8210)")
    app = PortalApp(hub, dev=dev)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    print(f"Practice portal on http://{host}:{port}/" + (" (dev: codes are shown on screen)" if dev else ""))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
