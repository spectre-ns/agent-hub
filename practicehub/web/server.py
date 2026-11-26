"""The hub's web server: a JSON API (agents, integrations) and plain HTML pages (people). Standard library only.

Everyone signs in as a member. The API takes ``Authorization: Bearer ph_...``; the pages take the same token in a
cookie. In dev mode (``--dev``) ``/login?as=<member>`` signs in without a token, for trying the practice out.

Every request acts as the signed-in member and goes through the same hub calls; the server adds no permissions.
"""

from __future__ import annotations

import dataclasses
import difflib
import html
import json
import re
import threading
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import tomllib

from .. import blocks as blockmod
from .. import expr
from .. import export as exportmod
from .. import schema
from .. import workflows as wfmod
from ..backends import engines
from ..sandbox import ALLOWED_IMPORTS, TIMEOUT as SANDBOX_TIMEOUT
from ..hub import Denied, Duplicate, HubError, Invalid, NotFound, member_ref
from ..store import Transient

CLIENT_HEADER = "x-practicehub-client"  # the address a request came from, as the listener saw it (it overwrites any)


def client_address(peer: str, headers: dict) -> str:
    """Where a request came from, for counting failed sign-ins per address: the peer, or, when the peer is this
    machine or a private network (a reverse proxy in front of the hub), the address that proxy added last to
    X-Forwarded-For. A client cannot choose it otherwise."""
    import ipaddress
    try:
        near = ipaddress.ip_address(peer)
    except ValueError:
        return str(peer)[:64]
    fwd = [x.strip() for x in str(headers.get("x-forwarded-for", "")).split(",") if x.strip()]
    if fwd and (near.is_loopback or near.is_private):
        try:
            return str(ipaddress.ip_address(fwd[-1]))
        except ValueError:
            pass
    return str(near)
from ..visibility import Viewer, workflow_classes as visibility_classes
from .holder import HolderWords, holder
from .analytics_pages import AnalyticsPages
from .assets import CSS, ICONS, JS
from .bpmn_pages import BpmnPages
from .dashboard_pages import DashboardPages
from .definitions_pages import DefinitionPages
from .structure_pages import StructurePages
from .calendar_pages import CalendarPages
from .work_panel import WorkPanel
from .workflow_editor import condition_words, editor_toml, is_old_form, source_of
from .mcp_pages import McpPages
from .passes import PassPages
from .office_pages import OfficePages
from .live_pages import LivePages
from .record_work import RecordWork
from .records_pages import Download, RecordPages, a_an
from .journey_pages import JourneyPages

FORBIDDEN_PORTS = {8080, 9980, 11434}
MAX_BODY = 26 * 1024 * 1024  # the largest file (Hub.MAX_FILE) and the form around it
e = html.escape


SHOWN = 200  # rows a list shows; past this, the store is searched, never a list cut short


class _NoType:
    part_of = None


KEY_PATH = r"[A-Za-z][A-Za-z0-9]{0,7}-[0-9]{1,9}"  # a key in an address: /T1-12


class Redirect(Exception):
    def __init__(self, to: str, cookie: str | list[str] | None = None):
        self.to, self.cookie = to, cookie


class Unauthenticated(HubError):
    pass


class Rendered(Exception):
    """A page shown with an error status: a form re-rendered with what went wrong and what was typed, kept."""
    def __init__(self, code: int, page: str, cookie: str | None = None, headers: dict | None = None):
        self.code, self.page, self.cookie, self.headers = code, page, cookie, headers or {}


def _json(obj) -> bytes:
    def default(o):
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)
        return str(o)
    return json.dumps(obj, default=default, indent=1).encode()


# ================================================================================================== the app
class App(CalendarPages, RecordWork, WorkPanel, RecordPages, JourneyPages, DashboardPages, DefinitionPages, StructurePages, OfficePages, LivePages, McpPages,
          AnalyticsPages, BpmnPages, HolderWords, PassPages):
    def __init__(self, hub, *, dev: bool = False, oidc=None):
        self.hub, self.dev, self.oidc = hub, dev, oidc
        self._local = threading.local()  # per request (one thread each): who is reading
        # who the work is about, from the pack: patients in a clinic, clients in a firm
        self.subj = hub.pack.subject if hub.pack else {"type": "Subject", "label": "Subject", "plural": "Subjects",
                                                       "path": "subjects", "registered_event": "",
                                                       "register_label": ""}
        sp = re.escape(self.subj["path"])
        self.routes = []
        R = self.route
        # ---- API
        R("GET", r"/api/me", self.api_me)
        R("GET", r"/api/inbox", self.api_inbox)
        R("GET", r"/api/queue", self.api_queue)
        R("GET", r"/api/items/(?P<iid>[\w-]+)/context", self.api_context)
        R("POST", r"/api/items/(?P<iid>[\w-]+)/(?P<verb>claim|complete|decide|release|ack|take-over)", self.api_item_verb)
        R("GET", r"/api/runs", self.api_runs)
        R("POST", r"/api/runs", self.api_start)
        R("GET", r"/api/runs/(?P<rid>[\w-]+)", self.api_run)
        R("POST", r"/api/runs/(?P<rid>[\w-]+)/cancel", self.api_cancel)
        R("POST", r"/api/events", self.api_emit)
        R("GET", r"/api/events", self.api_track)
        R("GET", r"/api/events/(?P<seq>\d+)/chain", self.api_chain)
        R("GET", r"/api/track/verify", self.api_verify)
        # FHIR-style operations: a subject's whole file (Patient/<id>/$everything) and a bulk export (Type/$export)
        R("GET", r"/api/records/(?P<rtype>[A-Za-z]+)/(?P<rid>[\w.-]+)/(?:\$|%24)everything", self.api_everything)
        R("GET", r"/api/records/(?P<rtype>[A-Za-z]+)/(?:\$|%24)export", self.api_export_list)
        R("GET", r"/api/records/(?P<rtype>[A-Za-z]+)/(?P<rid>[\w.-]+)", self.api_record)
        R("POST", r"/api/records/(?P<rtype>[A-Za-z]+)", self.api_create_record)
        R("PATCH", r"/api/records/(?P<rtype>[A-Za-z]+)/(?P<rid>[\w.-]+)", self.api_update_record)
        R("GET", r"/api/records/(?P<rtype>[A-Za-z]+)/(?P<rid>[\w.-]+)/links", self.api_hand_links)
        R("POST", r"/api/records/(?P<rtype>[A-Za-z]+)/(?P<rid>[\w.-]+)/(?P<verb>link|unlink)", self.api_link)
        R("GET", r"/api/chart/(?P<rtype>[A-Za-z]+)/(?P<rid>[\w.-]+)", self.api_chart)
        R("GET", r"/api/workflows", self.api_workflows)
        R("POST", r"/api/workflows", self.api_propose)
        R("POST", r"/api/workflows/check", self.api_check_workflow)
        R("POST", r"/api/workflows/(?P<wid>[\w-]+)/(?P<ver>\d+)/approve", self.api_approve)
        R("GET", r"/api/blocks", self.api_blocks)
        R("POST", r"/api/blocks", self.api_propose_block)
        R("POST", r"/api/blocks/check", self.api_check_block)
        R("POST", r"/api/blocks/(?P<bid>[\w-]+)/(?P<ver>\d+)/approve", self.api_approve_block)
        R("GET", r"/api/members", self.api_members)
        R("POST", r"/api/members/(?P<mid>[\w-]+)/status", self.api_member_status)
        R("POST", r"/api/members/(?P<mid>[\w-]+)/tokens", self.api_member_token)
        R("POST", r"/api/members", self.api_member_add)
        R("GET", r"/api/members/(?P<mid>[\w-]+)/dependents", self.api_member_dependents)
        R("GET", r"/api/members/(?P<mid>[\w-]+)/profile-versions", self.api_member_profiles)
        R("POST", r"/api/members/(?P<mid>[\w-]+)/(?P<verb>roles|approve-roles|withdraw-roles|sponsor|profile|"
          r"approve-profile|withdraw-profile|switch-off-request|approve-switch-off|withdraw-switch-off|revoke)",
          self.api_member_change)
        R("GET", r"/api/outbox", self.api_outbox)
        R("POST", r"/api/outbox/(?P<oid>\d+)/(?P<verb>claim|sent|failed)", self.api_outbox_verb)
        # ---- pages
        R("GET", r"/login", self.page_login, auth=False)
        R("POST", r"/login", self.post_login, auth=False)
        R("GET", r"/logout", self.page_logout, auth=False)
        R("GET", r"/setup", self.page_setup, auth=False)
        R("POST", r"/setup", self.post_setup, auth=False)
        R("GET", r"/me/passphrase", self.page_passphrase)
        R("GET", r"/me/settings", self.page_settings)
        R("POST", r"/me/settings", self.post_settings)
        R("POST", r"/me/passphrase", self.post_passphrase)
        R("GET", r"/", self.page_inbox)
        R("GET", r"/board", self.page_board)
        R("POST", r"/board/move", self.post_board_move)
        R("GET", r"/items/(?P<iid>[\w-]+)", self.page_item_or_its_record)
        R("GET", r"/items/(?P<iid>[\w-]+)/decide", self.page_decide)
        R("GET", r"/items/(?P<iid>[\w-]+)/answer", self.page_answer)
        R("POST", r"/items/(?P<iid>[\w-]+)/(?P<verb>claim|complete|decide|release|ack|take-over)", self.post_item)
        R("POST", r"/notices/ack", self.post_notices_ack)
        R("GET", rf"/{sp}", self.page_subjects)
        R("POST", rf"/{sp}", self.post_subject)
        R("GET", rf"/{sp}/(?P<pid>[\w.-]+)", self.page_subject)
        R("GET", rf"/{sp}/(?P<pid>[\w.-]+)/export", self.page_export_subject)
        R("GET", r"/runs", self.page_runs)
        R("GET", r"/start", self.page_start)
        R("POST", r"/start", self.post_start_any)
        R("GET", r"/runs/(?P<rid>[\w-]+)", self.page_run)
        R("POST", r"/runs/(?P<rid>[\w-]+)/cancel", self.post_cancel)
        R("POST", r"/runs/(?P<rid>[\w-]+)/steps/(?P<sid>[\w-]+)/retry", self.post_retry)
        R("GET", r"/workflows", self.page_workflows)
        R("GET", r"/workflows/new", self.page_workflow_new)
        R("POST", r"/workflows/propose", self.post_propose)
        R("GET", r"/workflows/(?P<wid>[\w-]+)", self.page_workflow)
        R("GET", r"/workflows/(?P<wid>[\w-]+)/edit", self.page_workflow_edit)
        R("POST", r"/workflows/(?P<wid>[\w-]+)/(?P<ver>\d+)/(?P<verb>approve|withdraw)", self.post_workflow_verb)
        R("GET", r"/blocks", self.page_blocks)
        R("GET", r"/blocks/new", self.page_block_new)
        R("POST", r"/blocks/propose", self.post_block_propose)
        R("GET", r"/blocks/(?P<bid>[\w-]+)", self.page_block)
        R("GET", r"/blocks/(?P<bid>[\w-]+)/edit", self.page_block_edit)
        R("POST", r"/blocks/(?P<bid>[\w-]+)/(?P<ver>\d+)/(?P<verb>approve|withdraw)", self.post_block_verb)
        R("GET", r"/start/(?P<wid>[a-z][a-z0-9-]*)", self.page_start_one)
        R("GET", r"/team/new", self.page_team_new)
        R("GET", r"/settings", self.page_admin)
        R("GET", r"/settings/practice", self.page_practice)
        R("POST", r"/settings/practice", self.post_practice)
        R("GET", r"/team", self.page_team)
        R("GET", r"/team/(?P<mid>[\w-]+)", self.page_member)
        R("POST", r"/team", self.post_team_add)
        R("POST", r"/team/(?P<mid>[\w-]+)/(?P<verb>enable|disable|token|roles|approve-roles|withdraw-roles|sponsor|"
          r"profile|approve-profile|withdraw-profile|ask-disable|approve-disable|withdraw-disable|revoke|setup-code)",
          self.post_member)
        R("GET", r"/track", self.page_track)
        R("GET", r"/access", self.page_access)
        R("GET", r"/track/(?P<seq>\d+)", self.page_chain)
        R("GET", r"/outbox", self.page_outbox)
        R("POST", r"/outbox/(?P<oid>\d+)/delivered", self.post_outbox_delivered)
        self.calendar_routes(R)
        self.live_routes(R)
        self.work_routes(R)
        self.record_routes(R)
        self.dashboard_routes(R)
        self.structure_routes(R)  # before /define/<kinds>/...
        self.definition_routes(R)
        self.office_routes(R)
        self.mcp_routes(R)
        self.analytics_routes(R)
        self.bpmn_routes(R)
        R("GET", rf"/api/keys/(?P<key>{KEY_PATH})", self.api_key)
        R("GET", rf"/(?P<key>{KEY_PATH})", self.page_key)  # last: every page with a name of its own first

    def route(self, method, pattern, fn, auth=True):
        self.routes.append((method, re.compile(pattern + r"/?$"), fn, auth))

    # ------------------------------------------------------------------------------------------ dispatch
    def handle(self, method: str, raw_path: str, headers: dict, body: bytes) -> tuple[int, dict, bytes]:
        url = urllib.parse.urlsplit(raw_path)
        path, query = url.path, dict(urllib.parse.parse_qsl(url.query))
        is_api = path.startswith("/api/")
        req = {"method": method, "path": path, "query": query, "headers": headers, "body": body, "me": None}
        self._local.me = None
        self._local.activity = {}  # Hub.record_activity per (member, record), once a request (RecordWork.activity_of)
        try:
            if path not in self.STATIC:
                self.hub.sync_modules()  # a module the command line installed, upgraded or removed: loaded, or taken out
            if path.startswith("/wopi/"):  # Collabora, with an access token of its own (office_pages, wopi.py)
                return self.handle_wopi(method, raw_path, headers, body)
            if path.startswith("/mcp/"):  # MCP servers, with a member's token (mcp_pages, mcp.py)
                return self.handle_mcp(method, raw_path, headers, body)
            if path.startswith("/api/portal/"):  # the portal's own process, with its portal-scoped token
                from . import portal_api
                return portal_api.handle(self, method, path, headers, body)
            return self._dispatch(method, path, query, is_api, req, headers)
        except Transient:  # the database stayed busy past every retry: nothing of this transaction was saved
            msg = "The hub is busy with other work; try again in a moment."
            h = {"Retry-After": "2"}
            if is_api:
                return 503, {**h, "Content-Type": "application/json"}, _json({"error": msg})
            return 503, {**h, "Content-Type": "text/plain; charset=utf-8"}, msg.encode()
        except Exception:  # a bug is a 500 with no detail to the client; the traceback goes to stderr
            import traceback
            traceback.print_exc()
            msg = b"Something went wrong; it has been logged."
            if is_api:
                return 500, {"Content-Type": "application/json"}, _json({"error": "internal error"})
            return 500, {"Content-Type": "text/plain; charset=utf-8"}, msg

    STATIC = {"/static/app.css": ("text/css; charset=utf-8", CSS), "/static/app.js": ("text/javascript; charset=utf-8", JS)}

    def _dispatch(self, method, path, query, is_api, req, headers):
        if method == "GET" and path in self.STATIC:
            ctype, text = self.STATIC[path]
            return 200, {"Content-Type": ctype, "Cache-Control": "no-cache"}, text.encode()
        if not is_api:
            # a part is addressed under its owner (/patients/7f3a/lab-results/b147): routed by its own address, and
            # shown by the nested one; its own address, asked for in a browser, goes to the nested one
            flat = self.flat_path(path)
            if flat != path:
                req["shown"], req["path"], path = path, flat, flat
            elif method == "GET":
                m = re.fullmatch(r"/records/([A-Za-z]\w*)/([\w.-]+)()/?", path)  # its page; its files and dialogs serve here too
                if m and m.group(2) not in ("new", "export") and (self.hub.record_type(m.group(1)) or
                                                                   _NoType).part_of:
                    nested = self.record_href(f"{m.group(1)}/{m.group(2)}")
                    if not nested.startswith("/records/"):
                        q = urllib.parse.urlencode(query)
                        return 303, {"Location": nested + m.group(3) + (f"?{q}" if q else "")}, b""
        for m, rx, fn, auth in self.routes:
            match = rx.match(path)
            if not match or m != method:
                continue
            try:
                if auth:
                    req["me"] = self.authenticate(headers, query)
                    self._local.me = req["me"]
                result = fn(req, **match.groupdict())
                if isinstance(result, Download):  # a file, as it is
                    return 200, result[0], result[1]
                if is_api:
                    return 200, {"Content-Type": "application/json"}, _json(result)
                if isinstance(result, tuple):  # (headers, body): a download
                    return 200, result[0], result[1]
                return 200, {"Content-Type": "text/html; charset=utf-8"}, result.encode()
            except Redirect as r:
                h = {"Location": r.to}
                if r.cookie:
                    h["Set-Cookie"] = r.cookie
                return 303, h, b""
            except Rendered as r:
                h = {"Content-Type": "text/html; charset=utf-8"}
                if r.cookie:
                    h["Set-Cookie"] = r.cookie
                return r.code, {**h, **r.headers}, r.page.encode()
            except Unauthenticated as exc:
                if is_api:
                    return 401, {"Content-Type": "application/json"}, _json({"error": str(exc)})
                return 303, {"Location": "/login"}, b""
            except (Denied, Invalid, NotFound) as exc:
                code = 403 if isinstance(exc, Denied) else 422 if isinstance(exc, Invalid) else 404
                problems = getattr(exc, "problems", None) or []
                if is_api:
                    return code, {"Content-Type": "application/json"}, _json({"error": str(exc), "problems": problems})
                return code, {"Content-Type": "text/html; charset=utf-8"}, self.error_page(req, exc, problems).encode()
        code = 404 if not any(rx.match(path) for _, rx, _, _ in self.routes) else 405
        if is_api:
            return code, {"Content-Type": "application/json"}, _json({"error": "no such endpoint"})
        return code, {"Content-Type": "text/html; charset=utf-8"}, b"Not found"

    def authenticate(self, headers: dict, query: dict) -> dict:
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            tok = auth[7:].strip()
            m = self.hub.authenticate(tok)
            if not m and self.oidc and not tok.startswith("ph_"):
                m = self.oidc.authenticate(self.hub, tok)
            if not m:
                raise Unauthenticated("the token is not valid, or its member is not active")
            return m
        jar = cookies.SimpleCookie(headers.get("cookie", ""))
        if "ph_session" in jar:  # a person signed in with their passphrase
            m = self.hub.session_member(jar["ph_session"].value)
            if m:
                return m
        if self.dev and "ph_as" in jar:
            try:
                m = self.hub.member(jar["ph_as"].value)
            except NotFound:
                m = None
            if m and m["status"] == "active":
                return m
        raise Unauthenticated("sign in first")

    # ------------------------------------------------------------------------------------------ helpers
    @staticmethod
    def body_json(req) -> dict:
        try:
            data = json.loads(req["body"] or b"{}")
        except ValueError:
            raise Invalid("the request body is not JSON") from None
        if not isinstance(data, dict):
            raise Invalid("the request body is a JSON object")
        return data

    @staticmethod
    def form(req) -> dict:
        """Form fields; a repeated field becomes a list."""
        out: dict = {}
        for k, v in urllib.parse.parse_qsl((req["body"] or b"").decode(), keep_blank_values=True):
            out[k] = v if k not in out else (out[k] if isinstance(out[k], list) else [out[k]]) + [v]
        return out

    def _item_for(self, me, iid):
        """An item and its run, for a member who may see it: a notice only by whom (or whose role) it is for; a step
        only by who may see that step on its run's page; neither past a patient's directive. Others are told only
        that there is no such item (an item's title can name a patient and why)."""
        item = self.hub.engine.item(iid)
        run = self.hub.engine.run(item["run_id"])
        if not self.may_see_item(me, item, run):
            self.hub.track.append(me, "access.denied", f"item:{iid}", data={"relation": "reader", "what": "an item"})
            raise NotFound("this item is not yours to open")
        return item, run

    def page_item_or_its_record(self, req, iid):
        """An item's own address: open work on a record goes to the record's page, where it is done; anything else
        has the item's page."""
        home = self._item_home(req["me"], *self._item_for(req["me"], iid))
        if home:
            raise Redirect(home)
        return self.page_item(req, iid)

    def _item_home(self, me, item: dict, run: dict) -> str | None:
        """Where open work on a record is done: the record's own page, at its work, which shows the step, who has
        it and what it asks. None when the item keeps a page of its own: a notice or an approval, one part of work
        split between several people, work that is over, work about no record or about a record the member may not
        open, work about a file a directive keeps from them (the item's page says so), and work about a subject or
        a member itself, whose pages list their work and do not carry it."""
        from ..visibility import Viewer
        ref = run.get("record")
        if (item["kind"] not in ("task", "gate") or item["status"] not in ("open", "claimed", "waiting")
                or run.get("item") != item["id"] or not ref or "/" not in str(ref)):
            return None
        rtype = ref.split("/", 1)[0]
        if (rtype == self.subj["type"] or rtype in self.hub.member_types() or not self._may_read(me, rtype)
                or Viewer(self.hub, me).run_withheld(run)):
            return None
        return self.record_href(ref) + "#work"

    def _key_href(self, found: dict) -> str:
        return self.record_href(found["ref"]) if "ref" in found else f"/items/{found['item']}"

    def page_key(self, req, key):
        """A key's own address (/T1-12): the record or the work it names, for whoever may see it."""
        found = self.hub.find_key(req["me"], key)
        if not found:
            raise NotFound(f"nothing you may open has the key {key.upper()}")
        raise Redirect(self._key_href(found))

    def api_key(self, req, key):
        """``GET /api/keys/T1-12``: what the key names (``ref`` or ``item``), and its page."""
        found = self.hub.find_key(req["me"], key)
        if not found:
            raise NotFound(f"nothing you may open has the key {key.upper()}")
        return {**found, "href": self._key_href(found)}

    def may_see_item(self, me, item: dict, run: dict) -> bool:
        from ..visibility import may_see_item
        return may_see_item(self.hub, me, item, run)

    # ================================================================================================ API
    def api_me(self, req):
        me = req["me"]
        return {**me, "practice": self.hub.practice.get("name"), "pack": self.hub.pack.id if self.hub.pack else None}

    def api_inbox(self, req):
        return {"items": self.hub.engine.inbox(req["me"], include_ineligible=req["query"].get("all") == "1")}

    def api_queue(self, req):
        return {"items": self.hub.engine.queue(req["me"])}

    def api_context(self, req, iid):
        """What the item shows, and ``seen``: the hash a decision on it signs (send it back with the decision)."""
        ctx = self.hub.engine.context(req["me"], iid)
        return {**ctx, "seen": self.hub.engine.shown_digest(req["me"], iid)}

    def api_item_verb(self, req, iid, verb):
        me, eng, b = req["me"], self.hub.engine, self.body_json(req)
        if verb == "claim":
            return eng.claim(me, iid)
        if verb == "complete":
            return eng.complete(me, iid, b.get("output") or {}, assign=b.get("assign") or None)
        if verb == "decide":
            self._confirm_signer(req, b)
            return eng.decide(me, iid, b.get("outcome", ""), b.get("comment", ""), seen=b.get("seen") or "-",
                              assign=b.get("assign") or None)
        if verb == "release":
            return eng.release(me, iid, b.get("note", ""), escalate=bool(b.get("escalate")))
        if verb == "take-over":
            return eng.take_over(me, iid, b.get("note", ""), to=b.get("to") or None)
        return eng.acknowledge(me, iid)

    def api_runs(self, req):
        q = req["query"]
        runs = self.hub.engine.runs(record=q.get("record"), status=q.get("status"))
        return {"runs": [self._run_view(req["me"], r) for r in runs]}

    def _run_view(self, me, run):
        """A run as the member may see it: step outputs only for steps whose data the member may read."""
        viewer = Viewer(self.hub, me)
        wf = viewer.workflow_of_run(run["id"])
        steps = viewer.run_steps(wf, run) if wf else run["steps"]  # a step they may not see: not even which ran
        return {**{k: run[k] for k in ("id", "workflow_id", "version", "record", "status", "started_by",
                                       "created_at", "updated_at")}, "steps": steps, "title": self.hub.run_title(me, run),
                "href": self.run_href(run)}  # where it is worked on: its record's page, else its own

    def _note_run_view(self, me, run) -> None:
        """A run's page shows its record's name, the run's input and its steps' results: what a member sees there
        goes in the access report (PHIA) like opening the record itself."""
        if run.get("record"):
            self.hub.note_view(me, run["record"], how=f"run {run['id']}")

    def api_run(self, req, rid):
        run = self.hub.engine.run(rid)
        self._note_run_view(req["me"], run)
        return {**self._run_view(req["me"], run), "events": Viewer(self.hub, req["me"]).events(self.hub.track.list(run_id=rid, limit=500))}

    def api_start(self, req):
        b = self.body_json(req)
        run = self.hub.engine.start(req["me"], b.get("workflow", ""), record=b.get("record"), inputs=b.get("input"),
                                    allow_duplicate=b.get("allow_duplicate") is True)
        return self._run_view(req["me"], run)

    def api_cancel(self, req, rid):
        self.hub.engine.cancel(req["me"], rid, self.body_json(req).get("reason", ""))
        return {"ok": True}

    def api_emit(self, req):
        b = self.body_json(req)
        runs = self.hub.engine.emit(req["me"], b.get("type", ""), record=b.get("record"), data=b.get("data") or {})
        return {"started": [r["id"] for r in runs], "handled": bool(runs)}  # not handled: the escalation role is told

    def api_track(self, req):
        q = req["query"]
        evs = self.hub.track.list(run_id=q.get("run"), actor=q.get("actor"), limit=min(int(q.get("limit", 200)), 2000))
        return {"events": Viewer(self.hub, req["me"]).events(evs)}

    def api_chain(self, req, seq):
        if self.hub.track.get(int(seq)) and self.hub.track.get(int(seq))["action"] in Viewer.ACCESS_EVENTS \
                and not self.hub.is_policy_owner(req["me"]):
            raise NotFound(f"there is no event {seq}")
        return {"chain": Viewer(self.hub, req["me"]).events(self.hub.track.chain(int(seq)))}

    def api_verify(self, req):
        """The track's verify, for a policy owner only: it reads every event. It checks the events since the last
        verify and adds them to that kept result; ``?full=1`` checks the whole track again."""
        if not self.hub.is_policy_owner(req["me"]):
            raise Denied("the track is verified by a policy owner")
        return self.hub.verify(full=(req.get("query") or {}).get("full") in ("1", "true", "yes"))

    def api_record(self, req, rtype, rid):
        """A record as the hub keeps it; ``?format=fhir`` (or json, odt, print) exports it (a read with how=export)."""
        fmt = (req["query"].get("format") or "").lower()
        if fmt:
            x = exportmod.record(self.hub, req["me"], f"{rtype}/{rid}", fmt)
            return Download((x.headers(), x.body))
        return self.hub.read_record(req["me"], f"{rtype}/{rid}")

    def api_everything(self, req, rtype, rid):
        """A subject's whole file, as FHIR's ``$everything``: a Bundle (type collection) of what the caller may read;
        the records left out are counted in the X-Left-Out header. ``?format=`` json, odt or print instead."""
        x = exportmod.subject_file(self.hub, req["me"], f"{rtype}/{rid}", (req["query"].get("format") or "fhir").lower())
        return Download((x.headers(), x.body))

    def api_export_list(self, req, rtype):
        """A type's records: ``?format=`` ndjson (FHIR Bulk Data, the default for a FHIR type), csv or ods; ``_text``
        searches; ``all=1`` is every record, a bulk export a policy owner may make."""
        q = req["query"]
        rt = self.hub.record_type(rtype)
        fmt = (q.get("format") or q.get("_outputFormat") or ("ndjson" if exportmod.is_fhir(rt) else "csv")).lower()
        fmt = {"application/fhir+ndjson": "ndjson", "text/csv": "csv"}.get(fmt, fmt)
        x = exportmod.records_list(self.hub, req["me"], rtype, fmt, text=q.get("_text") or None, limit=SHOWN,
                                   every=q.get("all") in ("1", "true", "yes"))
        return Download((x.headers(), x.body))

    def api_update_record(self, req, rtype, rid):
        """Change a record. The ``If-Match`` header (or ``_version`` in the body) names the version the caller read;
        it is required, so one client never overwrites another's change unseen."""
        b = self.body_json(req)
        ver = (req.get("headers") or {}).get("if-match") or b.pop("_version", None)
        if not ver:
            raise Invalid("say which version you changed: the If-Match header, or _version in the body")
        return self.hub.update_record(req["me"], f"{rtype}/{rid}", b, expected_version=str(ver).strip('"W/'))

    def api_hand_links(self, req, rtype, rid):
        """The links a record's type allows by hand (``[[link]]``), and what each links to now."""
        rt = self.hub.record_type(rtype)
        if not rt:
            raise Invalid(f"there is no record type {rtype}")
        self.hub.read_record(req["me"], f"{rtype}/{rid}")
        have = self.hub.hand_links(req["me"], f"{rtype}/{rid}")
        return [{**{k: x[k] for k in ("rel", "label", "to", "many")}, "linked": have.get(x["rel"], [])}
                for x in rt.links]

    def api_link(self, req, rtype, rid, verb):
        """Link a record to another by a link its type allows (``{"rel": ..., "to": "Type/id"}``), or unlink it."""
        b = self.body_json(req)
        fn = self.hub.link if verb == "link" else self.hub.unlink
        return fn(req["me"], f"{rtype}/{rid}", str(b.get("rel") or ""), str(b.get("to") or ""))

    def api_create_record(self, req, rtype):
        return self.hub.create_record(req["me"], rtype, self.body_json(req))

    def api_chart(self, req, rtype, rid):
        ref = f"{rtype}/{rid}"
        return {"record": self.hub.read_record(req["me"], ref), **self.hub.chart(req["me"], ref)}

    def api_workflows(self, req):
        return {"workflows": [{"id": w["workflow"].id, "title": w["workflow"].title, "version": w["row"]["version"],
                               "summary": w["workflow"].summary, "starters": w["workflow"].starters,
                               "event": w["workflow"].event, "proposed": w["proposed"]} for w in self.hub.workflows()]}

    def api_propose(self, req):
        b = self.body_json(req)
        return self.hub.propose_workflow(req["me"], b.get("source", ""), b.get("note", ""))

    def api_approve(self, req, wid, ver):
        return self.hub.approve_workflow(req["me"], wid, int(ver), self.body_json(req).get("meaning", ""))

    def api_blocks(self, req):
        return {"blocks": [{"id": b["block"].id, "title": b["block"].title, "runtime": b["block"].runtime,
                            "version": b["row"]["version"], "proposed": b["proposed"],
                            "inputs": b["block"].inputs, "outputs": b["block"].outputs} for b in self.hub.blocks()]}

    def api_propose_block(self, req):
        b = self.body_json(req)
        return self.hub.propose_block(req["me"], b.get("source", ""), b.get("note", ""))

    def api_check_block(self, req):
        try:
            blockmod.parse(str(self.body_json(req).get("source", "")), self.hub.pack)
            return {"ok": True, "problems": []}
        except wfmod.DefinitionError as exc:
            return {"ok": False, "problems": exc.problems}

    def api_approve_block(self, req, bid, ver):
        return self.hub.approve_block(req["me"], bid, int(ver), self.body_json(req).get("meaning", ""))

    def api_members(self, req):
        warn = self.hub.member_admin_warning()
        return {"members": [{k: m[k] for k in ("id", "kind", "name", "title", "sponsor", "status", "roles", "profile")}
                            for m in self.hub.members()], "warnings": [warn] if warn else []}

    def api_member_status(self, req, mid):
        """Switch a member on or off. Switching off a person who sponsors agents or services names what becomes of
        each: ``hand_over`` {agent: new sponsor} and ``stop`` [agent, ...] (or "all"); GET .../dependents lists them."""
        b = self.body_json(req)
        return self.hub.set_status(req["me"], mid, b.get("status", ""), b.get("reason", ""),
                                   hand_over=b.get("hand_over"), stop=b.get("stop"))

    def api_member_dependents(self, req, mid):
        return {"dependents": self.hub.dependents(mid), "switch_off_request": self.hub.off_request(mid)}

    def api_member_profiles(self, req, mid):
        return {"versions": [{k: v for k, v in r.items() if k != "_id"} for r in self.hub.agent_profile_versions(mid)]}

    def api_member_token(self, req, mid):
        return {"token": self.hub.issue_token(req["me"], mid, self.body_json(req).get("label", ""))}

    def api_member_add(self, req):
        return self.hub.add_member(req["me"], self.body_json(req))

    def api_member_change(self, req, mid, verb):
        b, me, hub = self.body_json(req), req["me"], self.hub
        if verb == "roles":
            return hub.set_roles(me, mid, list(b.get("roles", [])))
        if verb == "approve-roles":
            return hub.approve_roles(me, mid)
        if verb == "withdraw-roles":
            hub.withdraw_roles(me, mid)
            return {"withdrawn": True}
        if verb == "sponsor":
            return hub.set_sponsor(me, mid, str(b.get("sponsor", "")))
        if verb == "profile":  # proposed: in force once a second policy owner approves it
            return hub.propose_agent_profile(me, mid, {k: v for k, v in b.items() if k != "note"},
                                             str(b.get("note", "")))
        if verb == "approve-profile":
            return hub.approve_agent_profile(me, mid, int(b.get("version", 0)), str(b.get("meaning", "")))
        if verb == "withdraw-profile":
            hub.withdraw_agent_profile(me, mid, int(b.get("version", 0)), str(b.get("reason", "")))
            return {"withdrawn": True}
        if verb == "switch-off-request":
            return hub.request_switch_off(me, mid, str(b.get("reason", "")), hand_over=b.get("hand_over"),
                                          stop=b.get("stop"))
        if verb == "approve-switch-off":
            return hub.approve_switch_off(me, mid)
        if verb == "withdraw-switch-off":
            hub.withdraw_switch_off(me, mid)
            return {"withdrawn": True}
        hub.revoke_token(me, mid, str(b.get("credential", "")))
        return {"revoked": True}

    # ================================================================================================ pages
    def layout(self, req, title: str, body: str, *, crumbs=(), actions: str = "", subnav: str = "",
               heading: bool = True, above: str = "", status: str = "") -> str:
        """A page. ``status``: what sits beside its title (a record's state, who holds it, when it is due)."""
        me = req.get("me")
        p = self.hub.practice
        name = p.get("name", "Practice Hub")
        prefs = self.hub.preferences(me["id"]) if me else {}
        theme = f' data-theme="{e(prefs["theme"])}"' if prefs.get("theme") in ("light", "dark") else ""
        mode = f' data-editor="{e(prefs["editor"])}"' if prefs.get("editor") else ""
        head = (f'<!doctype html><html lang="en"{theme}{mode}><head><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(title)} · {e(name)}</title>'
                f'<link rel="stylesheet" href="/static/app.css"><script src="/static/app.js" defer></script></head><body>')
        crumb = ""
        if crumbs:
            crumb = ('<div class="crumbs">' + '<span class="sep">/</span>'.join(
                f'<a href="{e(h)}">{e(t)}</a>' if h else e(t) for h, t in crumbs) + "</div>")
        page = (f'{crumb}{above}<div class="pagehead"><h1{"" if heading else " class=visually-hidden"}>{e(title)}</h1>'
                f'{status}{f"<div class=actions>{actions}</div>" if actions else ""}</div>{body}')
        mark = f'<span class="mark">{e(self._code())}</span>'
        if not me:
            return (head + f'<header class="top"><span class="logo">{mark}{e(name)}</span></header>'
                    f'<main style="max-width:560px;margin:0 auto">{page}</main></body></html>')
        if self._is_admin(req, me):
            return head + self._admin_shell(req, me, mark, name, page)
        side = self._sidebar(req, me)
        top = (f'<a class="skip" href="#main">Skip to content</a><header class="top">'
               f'<button type="button" class="menu-toggle" aria-label="Menu" aria-expanded="false" aria-controls="side">'
               f'<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" '
               f'stroke-width="2"><path d="M4 6h16M4 12h16M4 18h16"/></svg></button>'
               + self._practice_menu(me, mark) +
               f'<form class="search" action="/{e(self.subj["path"])}" method="get"><input name="q" type="search" '
               f'placeholder="Search {e(self.subj["plural"].lower())}" aria-label="Search {e(self.subj["plural"].lower())}"></form>'
               f'<span class="spacer"></span>'
               + "".join(f'<a class="btn quick" href="/start/{e(wf.id)}" data-modal>{e(wf.title)}</a>'
                         for wf in self.quick_starts(me)[:3])
               + '<a class="btn create" href="/start" data-modal accesskey="c" aria-keyshortcuts="Alt+C">Start work</a>'
               + self._me_menu(me) + '</header>')
        shell = "shell has-subnav" if subnav else "shell"
        return (f'{head}{top}<div class="{shell}">{side}{subnav}<main id="main" tabindex="-1">{page}</main></div>'
                f'</body></html>')

    def _settings(self, me) -> list[tuple[str, list[tuple[str, str, str, str]]]]:
        """The practice's settings, grouped: how it works (set up) and what it did (audit), each link with an icon. The
        admin ribbon and the settings landing list them; the work sidebar keeps to the work."""
        owner = bool(me and self.hub.is_policy_owner(me))
        setup = ([("/settings/practice", "Practice", "its name, code, contact details and time zone", "settings")]
                 if owner else []) + [("/team", "Team", "who works here, their roles and sponsors", "team"),
                 ("/modules", "Modules", "what the practice has installed", "module"),
                 ("/workflows", "Workflows", "how work moves, step by step", "flow"),
                 ("/define/types", "Record types", "what the practice keeps records of", "records"),
                 ("/define/structure", "Record structure", "what is part of what, and the links between records",
                  "records"),
                 ("/define/views", "Views", "boards and tables of records", "board"),
                 ("/blocks", "Blocks", "reusable steps", "block"),
                 ("/define/skills", "Skills", "what agents know how to do", "skill"),
                 ("/settings/connections", "Connections (MCP)", "servers agents and tools connect to", "module")]
        audit = ([("/track", "Track", "everything that happened, signed and chained", "track"),
                  ("/access", "Access report", "who may read and write what", "access"),
                  ("/settings/analytics", "Process analytics", "where time goes in each workflow", "board")]
                 if owner else [])
        if me and (owner or self.hub.can(me, "reader", "data_class:correspondence")):
            audit.append(("/outbox", "Outbox", "messages waiting to go and those sent", "outbox"))
        # who does not own policy changes none of the rest at once (a policy owner installs and approves): they are
        # listed apart, marked view only, so each page does not have to say so; the team stays readable to all
        view = [] if owner else [x for x in setup if x[0] != "/team"]
        setup = setup if owner else [x for x in setup if x[0] == "/team"]
        return [g for g in (("Set up", setup), ("View only", view), ("Audit", audit)) if g[1]]

    SETTINGS_GROUPS = {"Set up": "How the practice works: its people, what it keeps and how work moves.",
                       "View only": "How the practice is set up. A policy owner installs and approves changes to "
                                    "these; you may read them, and propose a change where a page offers one.",
                       "Audit": "What the practice did, and who may see what."}
    # pages that open in the admin shell (the settings), not in the work layout
    ADMIN_PREFIXES = ("/settings", "/team", "/modules", "/workflows", "/define", "/blocks", "/track", "/access",
                      "/outbox")

    def _is_admin(self, req, me) -> bool:
        path = (req.get("path") or "/").rstrip("/") or "/"
        own = f"/team/{me['id']}"
        if path == own or path.startswith(own + "/"):  # a member's own profile is theirs, under the profile menu
            return False
        return any(path == p or path.startswith(p + "/") for p in self.ADMIN_PREFIXES)

    def _admin_shell(self, req, me, mark: str, name: str, page: str) -> str:
        """The settings, as a screen of their own: a slim bar with the way back to the work, and a ribbon, left, of
        every setup item (the closest one lit)."""
        path = (req.get("path") or "/").rstrip("/") or "/"
        groups = self._settings(me)
        hrefs = ["/settings"] + [h for _, links in groups for h, *_ in links]
        hits = [h for h in hrefs if path == h or path.startswith(h + "/")]
        best = max(hits, key=len) if hits else None

        def link(h, label, icon):
            on = h == best
            return (f'<a class="rib{" on" if on else ""}" href="{e(h)}"{" aria-current=page" if on else ""}>'
                    f'<svg viewBox="0 0 24 24" aria-hidden="true">{ICONS[icon]}</svg><span>{e(label)}</span></a>')

        ribbon = ('<nav class="ribbon" id="ribbon" aria-label="Settings">' + link("/settings", "Overview", "settings")
                  + "".join(f'<div class="rib-group" role="group" aria-labelledby="rib-{i}"><div class="rib-sec" '
                            f'id="rib-{i}">{e(g)}</div>' + "".join(link(h, w, ic) for h, w, _, ic in links) + "</div>"
                            for i, (g, links) in enumerate(groups)) + "</nav>")
        top = (f'<a class="skip" href="#main">Skip to content</a><header class="top admin-top">'
               f'<a class="logo" href="/settings">{mark}<span>{e(name)} settings</span></a><span class="spacer"></span>'
               f'<a class="btn subtle back-to-work" href="/"><span aria-hidden="true">&larr;</span> Back to work</a>'
               + self._me_menu(me) + "</header>")
        return (f'{top}<div class="admin-shell">{ribbon}<main id="main" tabindex="-1">{page}</main></div>'
                f'</body></html>')

    def _practice_menu(self, me, mark: str) -> str:
        """Top left: the practice, and the way to its settings (a screen of their own) and home."""
        p = self.hub.practice
        item = lambda href, words: f'<a role="menuitem" href="{e(href)}">{e(words)}</a>'
        return (f'<div class="avatar-menu practice-menu"><button type="button" aria-haspopup="menu" aria-expanded="false" '
                f'aria-controls="practice-menu" class="logo" aria-label="{e(p.get("name", "Practice Hub"))}: settings">'
                f'{mark}<span>{e(p.get("name", "Practice Hub"))}</span><svg class="chev" width="12" height="12" '
                f'viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.5">'
                f'<path d="M6 9l6 6 6-6"/></svg></button><div role="menu" id="practice-menu" aria-label="Practice" '
                f'hidden><div class="menu-head"><b>{e(p.get("name", "Practice Hub"))}</b>'
                f'<span>{e(p.get("kind", "").replace("-", " "))}</span></div>' + item("/settings", "Practice settings")
                + item("/", "Home") + '</div></div>')

    def page_admin(self, req):
        """The settings landing: each group in a line, and its items as cards."""
        me, p = req["me"], self.hub.practice
        body = []
        for g, links in self._settings(me):
            body.append(f'<section class="admin-group"><h2>{e(g)}</h2><p class="muted">{e(self.SETTINGS_GROUPS.get(g, ""))}'
                        f'</p><ul class="admin-cards">' + "".join(
                            f'<li><a href="{e(h)}"><svg viewBox="0 0 24 24" aria-hidden="true">{ICONS[ic]}</svg>'
                            f'<b>{e(w)}</b><span class="muted">{e(d)}</span></a></li>' for h, w, d, ic in links)
                        + "</ul></section>")
        facts = [("Name", p.get("name")), ("Kind", (p.get("kind") or "").replace("-", " ")),
                 ("Privacy law", p.get("privacy_law")), ("Data stays in", p.get("data_residency"))]
        rows = "".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in facts if v)
        if rows:
            edit = (' <a href="/settings/practice">Change the practice\'s settings</a>'
                    if self.hub.is_policy_owner(me) else "")
            body.append('<section class="admin-group"><h2>Practice</h2><p class="muted">Who the practice is comes from '
                        f'its pack.{edit}</p><table class="kv">{rows}</table></section>')
        return self.layout(req, "Settings", "".join(body))

    PRACTICE_FIELDS = (("name", "Name", "text", "what the practice is called, on every page and message"),
                       ("code", "Short code", "text", "2 to 6 capital letters: the mark top left and the start of every "
                                                       "work item's key, like HFM-12; empty uses the name's initials"),
                       ("phone", "Phone", "tel", ""), ("email", "Email", "email", "the practice's own address"),
                       ("address", "Address", "textarea", ""), ("website", "Website", "url", "starting https://"),
                       ("timezone", "Time zone", "text", "a name like America/Halifax: when the day starts and ends"))

    def page_practice(self, req, error: str = "", done: bool = False, kept: dict | None = None):
        """The practice's own settings, for its policy owners: its name and contact details, who hears when work is
        stuck, and the shortcuts on the work sidebar. Who the practice is (its law, where data stays) is shown, fixed."""
        me = req["me"]
        if not self.hub.is_policy_owner(me):
            raise Denied("only a policy owner changes the practice's settings")
        p = {**self.hub.practice, **(kept or {})}
        err = f'<div class="error" role="alert" tabindex="-1">{e(error)}</div>' if error else ""
        ok = '<div class="ok" role="status">The practice\'s settings are saved.</div>' if done else ""
        inputs = []
        for k, label, kind, help_ in self.PRACTICE_FIELDS:
            hid = f' aria-describedby="h-{k}"' if help_ else ""
            hint = f'<small class="help" id="h-{k}">{e(help_)}</small>' if help_ else ""
            v = p.get(k) or ""
            ctl = (f'<textarea name="{k}" rows="3"{hid}>{e(v)}</textarea>' if kind == "textarea" else
                   f'<input name="{k}" type="{kind}" value="{e(v)}"{hid}'
                   + (' required maxlength="120"' if k == "name" else "")
                   + (f' placeholder="{e(self._initials(p.get("name", "")))}" pattern="[A-Z][A-Z0-9]{{1,5}}"'
                      if k == "code" else "") + (' list="tz-list"' if k == "timezone" else "") + ">")
            inputs.append(f'<div class="field"><label>{e(label)}'
                          f'{" <span class=req aria-hidden=true>*</span>" if k == "name" else ""}{ctl}</label>{hint}</div>')
        import zoneinfo
        tzs = "".join(f'<option value="{e(z)}">' for z in sorted(zoneinfo.available_timezones()) if "/" in z)
        roles = "".join(f'<option value="{e(r["id"])}"{" selected" if r["id"] == p.get("escalation_role") else ""}>'
                        f'{e(r["name"])}</option>' for r in self.hub.roles() if r["id"] != "everyone")
        members = set(self.hub.member_types())
        types = sorted((t for t in self.hub.record_types().values() if not t.part_of and t.id not in members),
                       key=lambda t: t.plural.lower())
        chosen = p.get("shortcuts") or []
        chip = lambda v, words: (f'<label class="radio multi"><input type="checkbox" class="opt-input" name="shortcuts" '
                                 f'value="{e(v)}"{" checked" if v in chosen else ""}> <span>{e(words)}</span></label>')
        chips = "".join(chip(t.id, t.plural) for t in types)
        starts = "".join(chip("start:" + w.id, w.title or w.id) for w in (x["workflow"] for x in self.hub.workflows()))
        fixed = [("Kind", (p.get("kind") or "").replace("-", " ")), ("Jurisdiction", p.get("jurisdiction")),
                 ("Privacy law", p.get("privacy_law")), ("Data stays in", p.get("data_residency"))]
        rows = "".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in fixed if v)
        body = (f'{ok}{err}<form method="post" action="/settings/practice" class="card settings stack" style="max-width:760px">'
                f'<h2>The practice</h2>{"".join(inputs)}<datalist id="tz-list">{tzs}</datalist>'
                f'<h2>When work is stuck</h2><div class="field"><label>Who hears<select name="escalation_role" '
                f'aria-describedby="h-esc">{roles}</select></label><small class="help" id="h-esc">the role told when a '
                f'step fails, is overdue with no one to escalate to, or a message cannot be sent</small></div>'
                f'<h2>Shortcuts</h2><p class="muted">On the work sidebar and the top bar, for everyone who may use '
                f'them. Modules add their own.</p><fieldset class="choices options"><legend>Records</legend>{chips}'
                f'</fieldset>' + (f'<fieldset class="choices options"><legend>Start work</legend>{starts}</fieldset>'
                                  if starts else "")
                + '<input type="hidden" name="_shortcuts" value="1"><div class="filters"><button class="primary">'
                f'Save the practice\'s settings</button><a class="btn" href="/settings">Cancel</a></div></form>'
                f'<section class="admin-group"><h2>Who the practice is</h2><p class="muted">From its pack: these change '
                f'only when a policy owner reviews a pack upgrade and another puts it into force.</p>'
                f'<table class="kv">{rows}</table></section>')
        return self.layout(req, "Practice", body, crumbs=[("/settings", "Settings"), (None, "Practice")])

    def post_practice(self, req):
        f = self.form(req)
        changes = {k: f.get(k, "") for k, *_ in self.PRACTICE_FIELDS}
        changes["escalation_role"] = f.get("escalation_role", "")
        if f.get("_shortcuts"):
            got = f.get("shortcuts", [])
            changes["shortcuts"] = got if isinstance(got, list) else [got]
        try:
            self.hub.set_practice(req["me"], changes)
        except Invalid as exc:
            raise Rendered(422, self.page_practice(req, error=str(exc), kept=changes)) from None
        return self.page_practice(req, done=True)

    def _me_menu(self, me) -> str:
        """The signed-in member's menu, top right: their profile, their settings, signing out."""
        rnames = {r["id"]: r["name"] for r in self.hub.roles()}
        roles = ", ".join(rnames.get(r, r) for r in self.hub.roles_of(me["id"])) or me["kind"]
        item = lambda href, words: f'<a role="menuitem" href="{e(href)}">{e(words)}</a>'
        return (f'<div class="me avatar-menu"><button type="button" aria-haspopup="menu" aria-expanded="false" '
                f'aria-controls="me-menu" aria-label="{e(me["name"])}: your profile and settings">{self._av(me)}'
                f'<span class="name">{e(me["name"])}</span><svg class="chev" width="12" height="12" viewBox="0 0 24 24" '
                f'aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M6 9l6 6 6-6"/></svg>'
                f'</button><div role="menu" id="me-menu" aria-label="Your profile and settings" hidden>'
                f'<div class="menu-head"><b>{e(me["name"])}</b><span>{e(roles)}</span></div>'
                + item(f"/team/{me['id']}", "Your profile") + item("/me/settings", "My settings")
                + '<div role="separator"></div>' + item("/logout", "Sign out") + '</div></div>')

    def _nav_path(self, req, me) -> str:
        """The page the sidebar lights a link for: a part's page (an allergy, a visit's note) is lit as its owner's,
        up to the first owner that is not itself a part."""
        path = req["path"]
        for _ in range(4):  # a part of a part of a part, at most
            path = self.flat_path(path)
            m = re.fullmatch(r"/records/([A-Za-z][A-Za-z0-9_]*)/(new|[A-Za-z0-9_.-]+)(/.*)?", path)
            rt = self.hub.record_type(m.group(1)) if m else None
            if not (rt and rt.part_of and me and self._may_read(me, rt.id)):
                break
            if m.group(2) == "new":  # adding a part from its owner's page: the owner is in the query
                of = self._owner_field(rt)
                owner = str(req["query"].get(of["name"]) or req["query"].get(of["path"]) or "") if of else ""
            else:
                rec = self.hub.records.read(rt.id, m.group(2))
                owner = self.hub.owner_of(rec) if rec else None
            if not owner or not schema.REF_TEXT.fullmatch(owner):
                break
            path = self.record_href(owner)
        return path

    def _sidebar(self, req, me) -> str:
        path = self._nav_path(req, me)
        inbox = self.hub.engine.inbox(me) if me else []
        mine = [i for i in inbox if i["kind"] != "notice"]
        urgent = len(self._urgent(me, inbox)) if me else 0
        sp = f'/{self.subj["path"]}'
        hrefs = ["/", "/board", "/runs", sp, "/schedule", "/records"] + [f"/records/{t}" for t in self.shortcuts(me)] \
            + [f"/views/{v.id}" for v in (self.hub.views_for(me) if me else [])]
        hits = [h for h in hrefs if path == h or (h != "/" and path.startswith(h + "/"))]
        best = max(hits, key=len) if hits else None  # only the closest link is lit: a shortcut, not All records too

        def nav(h, label, icon, count=None, red=0):
            on = h == best
            n = f'<span class="count">{count}</span>' if count else ""
            if red:  # urgent work first, in red, said in words to a screen reader
                n = (f'<span class="count urgent" title="{red} urgent">{red}<span class="vh"> urgent</span>'
                     f'</span>') + n.replace('class="count"', 'class="count also"')
            cur = ' aria-current="page"' if on else ""
            return (f'<a class="nav{" on" if on else ""}" href="{h}"{cur}><svg viewBox="0 0 24 24" aria-hidden="true">'
                    f'{ICONS[icon]}</svg>{e(label)}{n}</a>')

        p = self.hub.practice
        parts = ['<aside class="side" id="side">',
                 '<nav aria-label="Main">', nav("/", "Your work", "inbox", len(mine), urgent), nav("/board", "Board", "board"), nav("/runs", "All work", "list"),
                 '<div class="sec">Practice</div>', nav(sp, self.subj["plural"], "people")]
        if me and self.hub.of_base("Booking"):  # everyone opens the calendar; what they may not read shows as Busy
            parts.append(nav("/schedule", "Schedule", "calendar"))
        for t in self.shortcuts(me):  # the record types the pack and its modules work in most
            parts.append(nav(f"/records/{t}", self.hub.record_type(t).plural, "records"))
        parts.append(nav("/records", "All records", "records"))
        views = self.hub.views_for(me) if me else []
        if views:
            parts.append('<div class="sec">Views</div>')
            parts += [nav(f"/views/{v.id}", v.title, "board") for v in views]
        parts += ['</nav>',  # set up and audit live in the practice menu, top left (and /settings)
                  f'<p class="legal">{e(p.get("privacy_law", ""))}{" · " if p.get("privacy_law") else ""}data stays in '
                  f'{e(p.get("data_residency", "the practice"))}</p></aside>']
        return "".join(parts)

    def _may_read(self, me, rtype) -> bool:
        try:
            return bool(self.hub.may_read(me, rtype))
        except HubError:
            return False

    def _code(self) -> str:
        """The practice's short code (``Hub.practice_code``)."""
        return self.hub.practice_code()

    @staticmethod
    def _initials(name: str) -> str:
        from ..hub import initials
        return initials(name)

    AV_COLORS = ("#2f6fd6", "#1f8a5b", "#b5482c", "#7b4fc9", "#b07d12", "#2a8a9a", "#c03d77", "#5b6b84")

    def _av(self, m) -> str:
        """An avatar: round for people, square for agents and services."""
        if m is None:
            return ('<span class="av empty" role="img" title="Nobody has taken this yet" '
                    'aria-label="Nobody has taken this yet">?</span>')
        if isinstance(m, str):
            if m.startswith(("workflow:", "pack:", "type:")):
                return '<span class="av automation" title="automation">⚙</span>'
            try:
                m = self.hub.member(m)
            except NotFound:
                return ('<span class="av" style="background:#5b6b84" role="img" title="Someone no longer in the '
                        'team" aria-label="Someone no longer in the team">?</span>')
        color = self.AV_COLORS[sum(map(ord, m["id"])) % len(self.AV_COLORS)]
        return (f'<span class="av {e(m["kind"])}" style="background:{color}" title="{e(m["name"])} ({e(m["kind"])})">'
                f'{e(self._initials(m["name"])[:2])}</span>')

    def _key(self, iid: str) -> str:
        """A work item's key: its record's (T1-12), or the practice's code and its number (``Hub.item_key``)."""
        return self.hub.item_key(iid)

    ITEM_STATUS = {"open": ("To do", ""), "claimed": ("In progress", "prog"), "held": ("Waiting for sponsor", "wait"),
                   "done": ("Done", "done"), "cancelled": ("Cancelled", ""), "waiting": ("Waiting", "wait")}
    STEP_STATE = {"pending": ("To come", ""), "active": ("Now", "prog"), "done": ("Done", "done"),
                  "skipped": ("Not needed", ""), "failed": ("Stopped", "bad"), "waiting": ("Now", "wait"),
                  "queued": ("In the queue", "wait"), "running": ("Under way", "prog"),
                  "if-needed": ("If needed", "")}
    RUN_STATUS = {"running": ("In progress", "prog"), "completed": ("Done", "done"), "cancelled": ("Cancelled", "")}

    @staticmethod
    def _loz(table: dict, status: str) -> str:
        label, cls = table.get(status, (status, ""))
        return f'<span class="loz {cls}">{e(label)}</span>'

    def _item_loz(self, item) -> str:
        if item["kind"] == "hold" and item["status"] == "open":
            return '<span class="loz wait">Awaiting approval</span>'
        return self._loz(self.ITEM_STATUS, item["status"])

    KI = {"task": ("T", "Task: something to do and fill in"), "gate": ("D", "Decision: a person decides and signs"),
          "hold": ("H", "Approval: a person checks an agent's high-risk work"),
          "notice": ("!", "Notice: something to read and acknowledge"),
          "automation": ("A", "Automation: the system does this step"),
          "wait": ("W", "Waiting: for a time or for something to happen")}

    @classmethod
    def _ki(cls, kind: str) -> str:
        letter, label = cls.KI.get(kind, ("?", "Other work"))
        return f'<span class="ki {e(kind)}" role="img" title="{e(label)}" aria-label="{e(label)}">{letter}</span>'

    def _board_legend(self) -> str:
        """What the marks on a card mean: the kind letters and the avatars."""
        kinds = "".join(f'<li><span class="ki {k}" aria-hidden="true">{letter}</span> {e(label)}</li>'
                        for k, (letter, label) in self.KI.items())
        return (f'<details class="board-legend"><summary>What the marks on a card mean</summary>'
                f'<ul class="plain">{kinds}<li><span class="av empty" aria-hidden="true">?</span> Nobody has taken '
                f'it yet: anyone it is for may</li><li><span class="badge person">a person must do this</span> No '
                f'agent may take it</li><li><span class="badge urgent">Urgent</span> Do it first</li></ul></details>')

    def member_name(self, mid):
        if not mid:
            return ""
        if str(mid).startswith("workflow:"):
            return "automation"
        try:
            return self.hub.member(mid)["name"]
        except NotFound:
            return mid

    def _type_label(self, rtype: str) -> str:
        """A record type as people call it (its label), never its id."""
        rt = self.hub.record_type(rtype) if rtype else None
        return rt.label if rt is not None else self.human(rtype)

    def _role_words(self, roles) -> str:
        """Role names for people to read, never their ids: one role, or a list joined with commas."""
        if not roles:
            return ""
        if isinstance(roles, str):
            roles = [roles]
        return ", ".join((self.hub.db.roles.get(r) or {}).get("name") or r for r in roles)

    def who(self, mid) -> str:
        if not mid:
            return '<span class="muted">Unassigned</span>'
        if str(mid).startswith(("workflow:", "pack:", "type:")):
            return f'<span class="who">{self._av(mid)}<span class="badge automation">automation</span></span>'
        try:
            m = self.hub.member(mid)
        except NotFound:
            return e(mid)
        tag = f' <span class="badge {m["kind"]}">{m["kind"]}</span>' if m["kind"] != "person" else ""
        return f'<span class="who">{self._av(m)}<a href="/team/{e(m["id"])}">{e(m["name"])}</a>{tag}</span>'

    def mention_text(self, text: str) -> str:
        """Text someone wrote (a comment, a reply), escaped, with each ``@id`` of a member shown as that member: avatar,
        name (a link to their page) and the agent badge. The stored text keeps the id; an ``@`` of nobody stays as written."""
        cache: dict = {}

        def one(m):
            mid = m.group(1)
            if mid not in cache:
                try:
                    cache[mid] = self.hub.member(mid)
                except NotFound:
                    cache[mid] = None
            mem = cache[mid]
            if mem is None:
                return m.group(0)
            tag = f' <span class="badge {e(mem["kind"])}">{e(mem["kind"])}</span>' if mem["kind"] != "person" else ""
            return (f'<span class="at who" data-member="{e(mid)}">{self._av(mem)}'
                    f'<a href="/team/{e(mid)}">{e(mem["name"])}</a>{tag}</span>')
        out = e(text or "").replace("\n", "<br>")
        return re.sub(r"(?<![\w@/&#;])@([a-z0-9][a-z0-9_.-]*[a-z0-9])", one, out)

    def _at(self, ts: str) -> str:
        """A time as pages show it: in the practice's time zone (``clock.shown``; the track keeps instants in UTC)."""
        from .. import clock
        return e(clock.shown(ts, self.hub.engine.zone()))

    @staticmethod
    def _urgency(it) -> str:
        """Urgent and overdue marks, and when it is due: shown wherever a piece of work is listed."""
        out = ""
        if it.get("priority") == "urgent" and it.get("kind") != "notice":
            out += ' <span class="badge urgent">Urgent</span>'
        due = it.get("due_at")
        if due and it.get("status") in ("open", "claimed", "held"):
            try:
                left = (datetime.fromisoformat(due.replace("Z", "+00:00")) - datetime.now(timezone.utc)).total_seconds()
            except ValueError:
                return out
            span = abs(left)
            text = (f"{int(span // 86400)}d" if span >= 86400 else f"{int(span // 3600)}h" if span >= 3600
                    else f"{max(1, int(span // 60))}m")
            out += (f' <span class="badge overdue" title="due {e(due)}">Overdue {text}</span>' if left < 0 else
                    f' <span class="badge due" title="due {e(due)}">Due in {text}</span>')
        return out

    # ------------------------------------------------------------------------------------------ sign in
    def page_login(self, req):
        q = req["query"]
        if self.dev and q.get("as"):
            nxt = str(q.get("next") or "/")
            nxt = nxt if re.fullmatch(r"/(?![/\\])[^\s\\]*", nxt) else "/"  # a page here, never another site
            raise Redirect(nxt, f"ph_as={urllib.parse.quote(q['as'])}; Path=/; HttpOnly; SameSite=Strict")
        dev = ""
        if self.dev:
            rows = "".join(f'<a class="tile" href="/login?as={e(m["id"])}"><div class="foot" style="gap:10px">'
                           f'{self._av(m)}<div><b>{e(m["name"])}</b> <span class="badge {m["kind"]}">{m["kind"]}</span>'
                           f'<div class="muted" style="font-size:12px">{e(self._role_words(m["roles"]))}</div></div></div></a>'
                           for m in self.hub.members() if m["status"] == "active" and m["kind"] != "service")
            dev = f'<h2>Dev mode: sign in as</h2><div>{rows}</div>'
        return self.layout(req, "Sign in", self._login_form() + dev)

    def _login_form(self, member: str = "", error: str = "") -> str:
        err = f'<div class="error" role="alert">{e(error)}</div>' if error else ""
        return (f'{err}<form method="post" action="/login" class="card">'
                f'<label>Your member id<input name="member" value="{e(member)}" autocomplete="username" required '
                f'autocapitalize="none" spellcheck="false"></label>'
                f'<label>Your passphrase<input name="passphrase" type="password" autocomplete="current-password" '
                f'required></label><button>Sign in</button></form>'
                f'<p class="muted">No passphrase yet, or forgotten it? {self._admins_who_help()} gives you a '
                f'one-time setup code: <a href="/setup">set your passphrase</a>. Agents, services and API clients use '
                f'access tokens.</p>')

    def _admins_who_help(self) -> str:
        """Who gives a setup code, by display name ('A member admin (Rana Haddad or Jo Tremblay)'), escaped."""
        names = [m["name"] for m in self.hub.member_admins()]
        if not names:
            return "A member admin"
        joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]
        return f"A member admin ({e(joined)})"

    # ------------------------------------------------------------------------------------------ your work
    def _item_rows(self, items, show_reason=False):
        if not items:
            return '<div class="empty-state">Nothing here.</div>'
        rows = []
        for it in items:
            note = f'<div class="note">{e(it["note"])}</div>' if it.get("note") else ""
            reason = f'<div class="note">Not for you: {e(it["reason"])}</div>' if show_reason and it.get("reason") else ""
            flag = (' <span class="badge person" title="No agent may take this">a person must do this</span>'
                    if it["persons_only"] and it["kind"] != "notice" else "")
            rows.append(f'<tr><td style="white-space:nowrap">{self._ki(it["kind"])} <span class="key">{self._key(it["id"])}'
                        f'</span></td><td><a href="/items/{it["id"]}">{e(it["title"])}</a>{self._urgency(it)}{flag}{note}{reason}</td>'
                        f'<td><a href="{e(self.run_href(it["run_id"]))}">{e(it.get("run_title", ""))}</a></td>'
                        f'<td>{e(self._role_words(it["role"]))}</td><td>{self.holder_html(it) if it["kind"] != "notice" else ""}</td>'
                        f'<td>{self._item_loz(it)}</td><td class="muted" style="white-space:nowrap">'
                        f'{self._at(it["created_at"])}</td></tr>')
        return ('<table class="list"><tr><th>Key</th><th>Work</th><th>About</th><th>Role</th><th>Held by</th>'
                f'<th>Status</th><th>Created</th></tr>{"".join(rows)}</table>')

    def _approvals_section(self, me) -> str:
        """Changes that wait for a second policy owner and that this member may approve: a role change, what an
        agent may do, a reviewed module. Empty for anyone who approves none."""
        waiting = self.hub.waiting_approvals(me)
        if not waiting:
            return ""
        rows = "".join(f'<tr><td><a href="{e(w["href"])}">{e(w["what"])}</a></td><td>{e(self.member_name(w["by"]))}</td>'
                       f'<td class="muted" style="white-space:nowrap">{self._at(w["at"])}</td></tr>' for w in waiting)
        return (f'<section class="waiting-approvals"><h2>Waiting for a second policy owner ({len(waiting)})</h2>'
                f'<p class="muted">Someone asked for these; you may approve or decline each one on its page.</p>'
                f'<table class="list"><tr><th>Change</th><th>Asked by</th><th>Asked</th></tr>{rows}</table></section>')

    # ------------------------------------------------------------------------------------------ urgent work
    @staticmethod
    def _overdue(it) -> bool:
        due = it.get("due_at")
        if not due or it.get("status") not in ("open", "claimed", "held"):
            return False
        try:
            return datetime.fromisoformat(due.replace("Z", "+00:00")) < datetime.now(timezone.utc)
        except ValueError:
            return False

    def _is_urgent(self, it) -> bool:
        """Urgent work still to do, or work past its time: what goes first, above everything else."""
        return (it.get("kind") not in ("notice", "hold") and it.get("status") in ("open", "claimed")
                and (it.get("priority") == "urgent" or self._overdue(it)))

    def _urgent(self, me, items: list | None = None) -> list[dict]:
        """This member's urgent work, one group per record (else per run): urgent or overdue work in their roles or
        hands, and the escalation notices sent to them about work still not done. A notice sent again and again
        about the same work counts up on its row; it is not a row of its own each time."""
        eng = self.hub.engine
        items = eng.inbox(me, include_ineligible=True) if items is None else items
        groups: dict = {}
        runs: dict = {}

        def run_of(rid):
            if rid not in runs:
                runs[rid] = eng.run(rid)
            return runs[rid]

        def group(it):
            run = run_of(it["run_id"])
            key = run.get("record") or run["id"]
            if key not in groups:
                groups[key] = {"key": key, "run": run, "title": self.hub.run_title(me, run), "work": {},
                               "notices": []}
            return groups[key]

        for it in items:
            if self._is_urgent(it):
                group(it)["work"][it["id"]] = it
        for n in items:
            if n["kind"] != "notice" or n["status"] not in ("open", "claimed") or not n.get("step_id"):
                continue
            late = self._late_work_of(me, n)
            if not late:
                continue  # a notice about work since done, or not about late work: an ordinary notice
            g = group(n)
            g["notices"].append(n)
            for w in late:
                g["work"].setdefault(w["id"], w)
        out = list(groups.values())
        for g in out:
            g["work"] = sorted(g["work"].values(), key=lambda w: (not self._overdue(w), w.get("due_at") or "~"))
        return sorted(out, key=lambda g: (not any(self._overdue(w) for w in g["work"]),
                                          min((w.get("due_at") or "~") for w in g["work"]) if g["work"] else "~"))

    def _record_urgent(self, me, ref: str, runs: list[dict]) -> list[dict]:
        """The urgent band of one record's page: urgent or overdue work about it that this member may see, whoever
        holds it, and the escalation notices about it sent to this member."""
        eng = self.hub.engine
        open_runs = {r["id"]: r for r in runs if r["status"] == "running"}
        if not open_runs:
            return []
        work = [eng.item(r["_id"]) for r in self.hub.db.items.find(
            {"run_id": {"$in": list(open_runs)}, "status": {"$in": ["open", "claimed"]},
             "kind": {"$in": ["task", "gate"]}}, fields=["_id"])]
        work = [w for w in work if self._is_urgent(w) and self.may_see_item(me, w, open_runs[w["run_id"]])]
        notices = [n for n in eng.inbox(me) if n["kind"] == "notice" and n["run_id"] in open_runs]
        return self._urgent(me, work + notices)

    def _late_work_of(self, me, notice) -> list[dict]:
        """The late work an escalation notice is about (open, at the notice's step of its run), that this member
        may see; none for any other notice."""
        if notice["kind"] != "notice" or not (notice.get("step_id") or notice.get("escalates")):
            return []
        eng = self.hub.engine
        run = eng.run(notice["run_id"])
        if notice.get("escalates"):  # the notice names the late work
            w = self.hub.db.items.get(notice["escalates"])
            late = [eng.item(notice["escalates"])] if w and w["status"] in ("open", "claimed") else []
            return [w for w in late if self.may_see_item(me, w, run)]
        late = [eng.item(r["_id"]) for r in self.hub.db.items.find(
            {"run_id": notice["run_id"], "step_id": notice["step_id"], "kind": {"$in": ["task", "gate"]},
             "status": {"$in": ["open", "claimed"]}, "escalated": {"$gt": 0}}, fields=["_id"])]
        return [w for w in late if self.may_see_item(me, w, run)]

    def _urgent_value(self, me, run) -> str:
        """What the work is about besides its record: the records its start named (a result, a referral), each
        named the way this member may see it."""
        vals = (run.get("ctx") or {}).get("input") or {}
        subject = run.get("subject") or run.get("record")
        named = ([run["record"]] if run.get("record") and run["record"] != subject else []) + [
            v for v in vals.values() if isinstance(v, str) and v not in (subject, run.get("record"))]
        refs = [self._ref_html(me, v) for v in named]
        return " · ".join(r for r in refs if r)

    def _urgent_band(self, me, groups: list[dict], *, here: str = "/", records=(), about: str | None = None) -> str:
        """The Urgent band: a row per record, with what it is about, how late, who holds it, and the way in; then
        records left waiting with no work on them (``records``: type, record, state words). On the page of the record
        ``about`` names, a row says the work alone, not that record's name again."""
        if not groups and not records:
            return ""
        eng = self.hub.engine
        rows = []
        for g in groups:
            value = self._urgent_value(me, g["run"])
            # overdue work in someone else's hands this member may take from the escalation notice: one click, as on
            # the notice's own page (the work opens, theirs)
            from_notice = {}
            for n in g["notices"]:
                x = eng.escalated_item(me, n)
                if x and eng.holder(x):
                    from_notice.setdefault(x["id"], n["id"])
            asks = []
            for w in g["work"]:
                if w["id"] in from_notice:
                    go = (f'<form method="post" action="/items/{e(from_notice[w["id"]])}/take-over" class="inline">'
                          f'<button class="danger">Take it over</button></form>')
                elif eng.may_take_over(me, w):
                    go = f'<a class="btn danger" href="/items/{e(w["id"])}#take-over">Take it over</a>'
                elif w["status"] == "open" and eng.eligibility(me, w)[0]:
                    go = f'<a class="btn primary" href="/items/{e(w["id"])}">Do it now</a>'
                else:
                    go = f'<a class="btn secondary" href="/items/{e(w["id"])}">Open</a>'
                asks.append(f'<div class="u-work"><span class="key">{self._key(w["id"])}</span> '
                            f'<a href="/items/{e(w["id"])}">{e(w.get("step_title") or w["title"])}</a>'
                            f'{self._urgency(w)} <span class="muted">·</span> {self.holder_html(w, avatar=False)}'
                            f'<span class="u-go">{go}</span></div>')
            count = sum(int(n.get("repeats") or 1) for n in g["notices"])  # one notice counts its repeats
            told = (f' <span class="badge overdue" title="Escalation notices sent to you about this">Escalated '
                    f'{count}×</span>' if count else "")
            ack = ""
            if g["notices"]:
                ids = "".join(f'<input type="hidden" name="id" value="{e(n["id"])}">' for n in g["notices"])
                ack = (f'<form method="post" action="/notices/ack" class="inline">{ids}<input type="hidden" '
                       f'name="_back" value="{e(here)}"><button class="subtle">Dismiss '
                       f'{"notice" if len(g["notices"]) == 1 else "the notices"}</button></form>')
            run = g["run"]
            title = (str(run.get("title") or "").split(": ", 1)[0] or g["title"]
                     if about and about in (run.get("subject"), run.get("record")) else g["title"])
            rows.append(f'<li class="u-row"><div class="u-head"><b>{e(title)}</b>{told}'
                        f'{f" <span class=u-value>{value}</span>" if value else ""}{ack}</div>{"".join(asks)}</li>')
        busy = {g["run"].get("record") for g in groups} | {v for g in groups for v in ((g["run"].get("ctx") or {}).get("input") or {}).values() if isinstance(v, str)}
        for rt, r, state in records:
            ref = f"{rt.id}/{r['id']}"
            if ref in busy:
                continue
            rows.append(f'<li class="u-row"><div class="u-head"><b>{e(rt.label)}</b> <span class="badge overdue">'
                        f'{e(state)}</span> <span class="u-value"><a href="{e(self.record_href(ref))}">'
                        f'{e(self.hub.title(r))}</a></span><span class="u-go"><a class="btn primary" '
                        f'href="{e(self.record_href(ref))}">Open</a></span></div></li>')
        return (f'<section class="urgent-band" aria-labelledby="urgent-h"><h2 id="urgent-h">Urgent '
                f'<span class="badge urgent">{len(rows)}</span></h2><ul class="plain">{"".join(rows)}</ul></section>')

    def post_notices_ack(self, req):
        """Dismiss several notices at once (an Urgent row's escalations): each one this member may acknowledge."""
        me, eng = req["me"], self.hub.engine
        f = self.form(req)
        ids = f.get("id") or []
        for nid in [ids] if isinstance(ids, str) else ids:
            try:
                eng.acknowledge(me, str(nid))
            except (Denied, Invalid, NotFound):
                continue  # acknowledged already, or not this member's: the others still go
        raise Redirect(self.safe_back(f.get("_back")) or "/")

    def page_inbox(self, req):
        me = req["me"]
        items = self.hub.engine.inbox(me, include_ineligible=True)
        mine = [i for i in items if i["eligible"]]
        notices = [i for i in mine if i["kind"] == "notice"]
        holds = [i for i in mine if i["kind"] == "hold"]
        doing = [i for i in mine if i["kind"] not in ("notice", "hold") and i["claimed_by"] == me["id"]]
        ready = [i for i in mine if i["kind"] not in ("notice", "hold") and i["claimed_by"] != me["id"]]
        other = [i for i in items if not i["eligible"]]
        urgent = self._urgent(me, items)
        folded = {n["id"] for g in urgent for n in g["notices"]}
        notices = [n for n in notices if n["id"] not in folded]
        parts = [self._urgent_band(me, urgent)]
        if holds:
            parts.append(f'<h2>Waiting for your approval ({len(holds)})</h2>'
                         f'<p class="muted">High-risk work by agents you sponsor. Nothing happens until you decide.</p>'
                         + self._item_rows(holds))
        parts.append(self._approvals_section(me))
        parts.append(f'<h2>In progress with you ({len(doing)})</h2>{self._item_rows(doing)}')
        parts.append(f'<h2>Ready for you ({len(ready)})</h2>{self._item_rows(ready)}')
        parts.append(f'<h2>Notices ({len(notices)})</h2>{self._item_rows(notices)}')
        if other:
            parts.append(f'<details class="section"><summary><b>Work in your roles that you cannot take ({len(other)})</b>'
                         f'</summary>{self._item_rows(other, True)}</details>')
        sponsored = [m for m in self.hub.members() if m["sponsor"] == me["id"]]
        if sponsored:
            rows = "".join(f'<tr><td>{self.who(m["id"])}</td><td>{e(self._role_words(m["roles"]))}</td>'
                           f'<td>max risk {e(m["profile"].get("max_risk", "low"))}</td>'
                           f'<td><span class="loz {"done" if m["status"] == "active" else "bad"}">{e(m["status"])}</span></td>'
                           f'</tr>' for m in sponsored)
            parts.append(f'<h2>Agents you sponsor</h2><p class="muted">You answer for what they do, and you can switch '
                         f'them off from their page.</p><table class="list">{rows}</table>')
        return self.layout(req, "Your work", "".join(parts),
                           actions=f'<a class="btn secondary" href="/board?who=mine">Your board</a>')

    # ------------------------------------------------------------------------------------------ board
    def _chip(self, query: dict, key: str, value: str, label: str) -> str:
        q = {**query, key: value}
        q = {k: v for k, v in q.items() if v}
        on = (query.get(key) or "") == value
        return f'<a class="chip{" on" if on else ""}" href="?{e(urllib.parse.urlencode(q))}">{e(label)}</a>'

    def page_board(self, req):
        me, eng, q = req["me"], self.hub.engine, req["query"]
        who, group = q.get("who", ""), q.get("group", "")
        items = [i for i in eng.queue(req["me"]) if i["kind"] != "notice"]
        wid = q.get("workflow", "") if group == "step" else ""
        flt = {"run_id": {"$in": [r["_id"] for r in self.hub.db.runs.find({"workflow_id": wid}, fields=["_id"],
                                                                           sort=[("created_at", -1)], limit=500)]}} \
            if wid else {}
        if wid:
            items = [i for i in items if i["run_id"] in flt["run_id"]["$in"]]
        done_rows = self.hub.db.items.find({**flt, "status": "done", "kind": {"$in": ["task", "gate"]}},
                                           sort=[("updated_at", -1)], limit=12, fields=["id"])
        # a run's item between people's steps (an automation running, a wait waiting): seen, in no one's to-do
        done_rows += self.hub.db.items.find({**flt, "status": "waiting", "resting": {"$ne": True}},
                                            sort=[("updated_at", -1)], limit=24,
                                            fields=["id"])
        runs: dict = {}

        def run_of(rid):
            if rid not in runs:
                runs[rid] = eng.run(rid)
            return runs[rid]

        for r in done_rows:
            it = eng.item(r["id"])
            run = run_of(it["run_id"])
            it.update(run_title=self.hub.run_title(req["me"], run), record=run["record"])
            items.append(it)
        # a card links to its item: show only those this member may open, and say how many others there are
        shown = [i for i in items if self.may_see_item(me, i, run_of(i["run_id"]))]
        kept_back, items = len(items) - len(shown), shown
        agents = [m for m in self.hub.members("agent") if m["status"] == "active"]
        agent_ok: dict = {}

        def agent_work(it):
            if it["claimed_by"]:
                try:
                    return self.hub.member(it["claimed_by"])["kind"] == "agent"
                except NotFound:
                    return False
            if it["id"] not in agent_ok:
                agent_ok[it["id"]] = it["kind"] != "hold" and any(eng.eligibility(a, it)[0] for a in agents)
            return agent_ok[it["id"]]

        if who == "mine":
            items = [i for i in items if i["claimed_by"] == me["id"] or (i["status"] == "open" and eng.eligibility(me, i)[0])]
        elif who == "agents":
            items = [i for i in items if agent_work(i)]
        elif who == "people":
            items = [i for i in items if not agent_work(i)]

        def column(it):
            if it["kind"] == "hold" or it["status"] == "waiting":
                return "wait"
            return {"open": "todo", "claimed": "prog", "done": "done"}.get(it["status"], "todo")

        cols = self.BOARD_COLS
        swf = None
        if wid:  # one workflow, a column per step: where each run's item is now
            try:
                swf = self.hub.workflow(wid)[0]
            except (NotFound, KeyError):
                swf = None
        if swf is not None:
            sids = [st.id for st in swf.steps if st.kind in ("task", "gate", "wait")]
            cols = [(f"step-{sid}", swf.step(sid).title, "") for sid in sids] + [("done", "Done recently", "done")]

            def column(it):  # noqa: F811 - the step board's columns
                if it["status"] == "done":
                    return "done"
                return f"step-{it['step_id']}" if it.get("step_id") in sids else f"step-{sids[0]}" if sids else ""
        back = self.here(req)

        def lane_of(it):
            if self._is_urgent(it):
                return "\x00urgent"  # the first lane
            if group == "workflow":
                return run_of(it["run_id"])["workflow_id"]
            if group == "role":
                return self._role_words(it["role"]) if it["role"] else "(no role)"
            return ""

        lanes: dict = {}
        for it in items:
            lanes.setdefault(lane_of(it), []).append(it)
        titles = {w["workflow"].id: w["workflow"].title for w in self.hub.workflows()} if group == "workflow" else {}
        out = []
        for lane in sorted(lanes, key=lambda k: (k != "\x00urgent", k)):
            its = lanes[lane]
            if lane == "\x00urgent":
                out.append(f'<div class="lane urgent">Urgent <span class="badge urgent">{len(its)}</span>'
                           f'<span class="muted">urgent, or past its time</span></div>')
            elif lane or "\x00urgent" in lanes:
                out.append(f'<div class="lane">{e(titles.get(lane, lane) or "Everything else")} '
                           f'<span class="badge">{len(its)}</span></div>')
            colhtml = []
            for cid, label, cls in cols:
                tiles = [self._tile(i, self._item_moves(me, i), back) for i in its if column(i) == cid]
                colhtml.append(f'<section class="col" data-col="{cid}" aria-label="{e(label)}"><header><span class="col-name">{e(label)}'
                               f'</span><span class="n">{len(tiles)}</span></header>{"".join(tiles)}</section>')
            out.append(f'<div class="board">{"".join(colhtml)}</div>')
        if not items:
            out.append('<div class="empty-state">No work matches these filters.</div>')
        if kept_back:
            out.append(f'<p class="muted">{kept_back} item{"s" if kept_back != 1 else ""} you may not open '
                       f'{"are" if kept_back != 1 else "is"} not shown.</p>')
        filters = ('<div class="filters"><span class="lbl">Show</span>'
                   + self._chip(q, "who", "", "Everyone") + self._chip(q, "who", "mine", "Only my work")
                   + self._chip(q, "who", "agents", "Agents") + self._chip(q, "who", "people", "People")
                   + '<span class="gap"></span><span class="lbl">Group by</span>'
                   + self._chip(q, "group", "", "None") + self._chip(q, "group", "workflow", "Workflow")
                   + self._chip(q, "group", "role", "Role") + "</div>")
        flows = [w["workflow"] for w in self.hub.workflows()]
        if flows:  # one workflow on a column per step
            opts = "".join(f'<option value="{e(w.id)}"{" selected" if w.id == wid else ""}>{e(w.title)}</option>'
                           for w in flows)
            filters += (f'<form method="get" action="/board" class="filters"><input type="hidden" name="group" '
                        f'value="step"><label>One workflow, by step <select name="workflow">{opts}</select></label>'
                        f'<button class="secondary">Show</button></form>')
        return self.layout(req, "Board", filters + self._board_legend() + "".join(out),
                           crumbs=[("/", "Practice"), ("", "Board")],
                           actions='<a class="btn secondary" href="/runs">All work</a>')

    BOARD_COLS = [("todo", "To do", ""), ("prog", "In progress", "prog"), ("wait", "Waiting", "wait"),
                  ("done", "Done recently", "done")]

    def _tile(self, it, moves: dict | None = None, back: str = "/board") -> str:
        flag = ('<span class="badge person" title="No agent may take this">a person must do this</span>'
                if it["persons_only"] else "")
        note = f'<div class="why">{e(it["note"])}</div>' if it.get("note") and it["status"] != "done" else ""
        urgent = " urgent" if it.get("priority") == "urgent" else ""
        now = it.get("waiting") if it["status"] == "waiting" else it.get("step_title")
        about = it.get("run_title", "") if it.get("run_title", "") != it["title"] else ""
        inner = (f'<div class="t">{e(it["title"])}{self._urgency(it)}</div>'
                 f'<div class="about">{e(" · ".join(x for x in (now, about) if x))}</div>{note}<div class="foot">{self._ki(it["kind"])}'
                 f'<span class="key">{self._key(it["id"])}</span>{flag}{self.holder_avatar(it)}</div>')
        return self.board_card(inner, f'/items/{it["id"]}', urgent, it["id"], it["title"], moves or {},
                               "/board/move", {"item": it["id"], "_back": back})

    def _item_moves(self, me, it) -> dict:
        """Where a work item's card may go on the board, by the item page's own buttons: Assign to me (to In
        progress), Give it back (to To do), and its form (to Done, which asks for what the step needs)."""
        if it["kind"] not in ("task", "gate") or it["status"] not in ("open", "claimed"):
            return {}
        if it["status"] == "claimed" and it["claimed_by"] != me["id"]:
            return {}
        if not self.hub.engine.eligibility(me, it)[0]:
            return {}
        screen = {"how": "screen", "href": f"/items/{it['id']}", "label": "Done…"}
        if it["status"] == "open":
            return {"prog": {"how": "post", "label": "In progress"}, "done": screen}
        return {"todo": {"how": "post", "label": "To do"}, "done": screen}

    @staticmethod
    def here(req) -> str:
        q = urllib.parse.urlencode(req["query"])
        return req.get("shown", req["path"]) + (f"?{q}" if q else "")

    @staticmethod
    def safe_back(v) -> str:
        """Where a board's form returns to: a path on this hub, never another site."""
        v = v if isinstance(v, str) else ""
        return v if v.startswith("/") and not v.startswith("//") and not any(c in v for c in "\\\r\n") else ""

    def board_card(self, inner: str, href: str, cls: str, key: str, title: str, moves: dict, action: str,
                   hidden: dict) -> str:
        """A card on a board. One that may move is dragged to a column it may go to (``data-moves``: each column,
        and whether it moves at once or asks first), or moved with its Move to… list, a plain form without the
        script. The server decides: the card stays where it was until the move is accepted."""
        card = f' data-card="{e(key)}"' if key else ""
        if not moves:
            return f'<a class="tile{cls}" href="{e(href)}"{card}>{inner}</a>'
        opts = "".join(f'<option value="{e(c)}">{e(m["label"])}</option>' for c, m in moves.items())
        fields = "".join(f'<input type="hidden" name="{e(k)}" value="{e(str(v))}">' for k, v in hidden.items())
        return (f'<div class="tile movable{cls}" data-card="{e(key)}" data-moves="{e(json.dumps(moves))}" '
                f'draggable="true"><a class="tile-link" href="{e(href)}" draggable="false">{inner}</a>'
                f'<form method="post" action="{e(action)}" class="move-to">{fields}'
                f'<select name="to" aria-label="Move {e(title)} to" required><option value="">Move to…</option>'
                f'{opts}</select><button class="secondary">Move</button></form></div>')

    def refused_move(self, req, what: str, exc: Exception, back: str):
        """A move the server would not make: why, in the dialog over the board (a page without the script)."""
        code = getattr(exc, "status", None) or (403 if isinstance(exc, Denied) else 422)
        body = (f'<div data-modal-body><div class="flash error" role="alert"><p>{e(what)} was not moved: {e(str(exc))}'
                f'</p></div><p><a class="btn secondary" href="{e(back)}" data-modal-close>Back to the board</a></p></div>')
        return Rendered(code, self.layout(req, "Not moved", body))

    def post_board_move(self, req):
        """A card moved on the work board: the same engine call as the item page's button, or its form."""
        me, eng = req["me"], self.hub.engine
        f = self.form(req)
        back = self.safe_back(f.get("_back")) or "/board"
        iid, to = str(f.get("item") or ""), f.get("to", "")
        item = eng.item(iid)
        try:
            if to == "done":
                ok, why = eng.eligibility(me, item)
                if not ok:
                    raise Denied(why)
                if item["kind"] not in ("task", "gate") or item["status"] not in ("open", "claimed"):
                    raise Invalid("only work still to do is finished")
                raise Redirect(f"/items/{iid}")  # what the step needs is asked on its page
            if to == "prog":
                if item["kind"] == "hold" or item["status"] != "open":
                    raise Invalid("only work still to do is assigned")
                eng.claim(me, iid)
            elif to == "todo":
                eng.release(me, iid, f.get("note", ""))
            else:
                raise Invalid({"wait": "an approval waits for its sponsor; work is not moved there"}.get(
                    to, "pick a column it may go to"))
        except (Denied, Invalid) as exc:
            if isinstance(exc, Denied):
                exc = Denied(self.holder_refusal(me, eng.item(iid), str(exc)))
            raise self.refused_move(req, item["title"], exc, back) from None
        raise Redirect(back)

    # ------------------------------------------------------------------------------------------ all work (runs)
    def page_runs(self, req):
        q = req["query"]
        status, wid, text = q.get("status") or "running", q.get("workflow", ""), q.get("q", "").lower()
        if status not in ("running", "completed", "cancelled", "any"):
            status = "running"
        # "any" is a value of its own: an empty one is dropped from the query and would read as the default
        runs = self.hub.engine.runs(status=None if status == "any" else status, limit=300)
        if wid:
            runs = [r for r in runs if r["workflow_id"] == wid]
        if text:
            runs = [r for r in runs if text in self.hub.run_title(req["me"], r).lower()]
        wfs = {w["workflow"].id: w["workflow"] for w in self.hub.workflows()}
        rows = []
        viewer = Viewer(self.hub, req["me"])
        for r in runs:
            wf = viewer.workflow_of_run(r["id"])
            shown = viewer.run_steps(wf, r) if wf else {}
            now = [wf.step(sid).title for sid, st in shown.items() if st["state"] == "active"]
            if wf and any(st["state"] == "active" for st in r["steps"].values()) and not now:
                now = ["a step you may not see"]
            wf = wf or wfs.get(r["workflow_id"])
            # Work is done on its record's page (as in Jira): a row opens the record, not the run's steps
            href = self.run_href(r)
            rows.append(f'<tr><td><a href="{e(href)}">{e(self.hub.run_title(req["me"], r))}</a></td>'
                        f'<td>{e(wf.title if wf else r["workflow_id"])} <span class="muted">v{r["version"]}</span></td>'
                        f'<td>{e(", ".join(now))}</td><td>{self._loz(self.RUN_STATUS, r["status"])}</td>'
                        f'<td>{self._av(r["started_by"])}</td><td class="muted" style="white-space:nowrap">'
                        f'{self._at(r["updated_at"])}</td></tr>')
        opts = "".join(f'<option value="{e(k)}"{" selected" if k == wid else ""}>{e(w.title)}</option>' for k, w in wfs.items())
        sopts = "".join(f'<option value="{v}"{" selected" if v == status else ""}>{t}</option>'
                        for v, t in (("running", "In progress"), ("completed", "Done"), ("cancelled", "Cancelled"), ("any", "Any status")))
        filt = (f'<form class="filters" method="get"><input name="q" value="{e(text)}" placeholder="Search work" '
                f'style="width:auto;margin:0;flex:1;min-width:180px"><select name="workflow" style="width:auto;margin:0">'
                f'<option value="">Every workflow</option>{opts}</select><select name="status" style="width:auto;margin:0">'
                f'{sopts}</select><button class="secondary" style="margin:0">Filter</button></form>')
        table = (f'<table class="list"><tr><th>Work</th><th>Workflow</th><th>Now at</th><th>Status</th><th>Started by</th>'
                 f'<th>Updated</th></tr>{"".join(rows)}</table>' if rows else
                 '<div class="empty-state">No work matches.</div>')
        return self.layout(req, "All work", filt + table, crumbs=[("/", "Practice"), ("", "All work")],
                           actions='<a class="btn" href="/start">Start work</a>')

    def page_start(self, req):
        me, hub = req["me"], self.hub
        q = req["query"]
        records: dict = {}  # per record type: the records this member may read, to start work about one

        def choices(rtype):
            if rtype not in records:
                rt = hub.record_type(rtype)
                records[rtype] = (rt, hub.records.search(rtype, limit=1) if rt and self._may_read(me, rtype) else [])
            return records[rtype]

        if q.get("workflow"):  # one workflow's start form (the dialog loads it; without the script it is a page)
            return self.page_start_one(req, q["workflow"])
        cards = []
        for w in hub.workflows():
            wf = w["workflow"]
            if not hub.can(me, "starter", f"workflow:{wf.id}"):
                continue
            if wf.record and not any(choices(t)[1] for t in hub.engine.start_on(wf)):
                continue
            cards.append(f'<li><a href="/start/{e(wf.id)}" data-modal><b>{e(wf.title)}</b>'
                         f'<span class="muted">{e(wf.summary.strip()[:160])}{"…" if len(wf.summary.strip()) > 160 else ""}'
                         f'</span></a></li>')
        create = self._create_pick(me)
        body = ("<div data-modal-body>"
                + (f'<h2 class="pick-head">Create a record</h2><p class="muted">Choose what you are adding; its form '
                   f'opens here.</p>{create}' if create else "")
                + (f'<h2 class="pick-head">Or start a workflow</h2><p class="muted">Each one runs the practice\'s '
                   f'approved workflow.</p><ul class="pick">{"".join(cards)}</ul>' if cards else "")
                + ("" if create or cards else '<div class="empty-state">There is nothing you may create or start.</div>')
                + "</div>")
        return self.layout(req, "Start work", body, crumbs=[("/", "Practice"), ("", "Start work")])

    def page_start_one(self, req, wid, kept: dict | None = None, error: Exception | None = None):
        me, hub, q = req["me"], self.hub, req["query"]
        wf = hub.workflow(wid)[0]
        if not hub.can(me, "starter", f"workflow:{wf.id}"):
            raise Denied(f"{me['name']} may not start {wf.title}")
        pick, about = "", ""
        if wf.record:
            want = q.get("record", "") or (kept or {}).get("_record", "")
            on = hub.engine.start_on(wf)
            fixed = (q.get("record") or (kept or {}).get("_fixed")) and want.split("/", 1)[0] in on \
                and self._may_read(me, want.split("/", 1)[0]) and hub.records.read(*want.split("/", 1))
            if fixed:  # opened from the record itself: it is what the work is about, shown, not asked
                about = want
                pick = (f'<input type="hidden" name="_record" value="{e(want)}"><input type="hidden" name="_fixed" '
                        f'value="1"><p class="about">About {self.record_link(want)}</p>')
            else:
                pick = self._input(self._record_field(wf), {"_record": {"reference": want}} if want else None, me)
        kept = kept or {}
        fields = "".join(self._active_ref_input(me, f, about, kept.get(f["name"]))
                         if f["type"] == "reference" and f.get("active") and about else
                         self._input(self._ref_input(f), {f["name"]: kept[f["name"]]} if kept.get(f["name"]) else None,
                                     me) if f["type"] == "reference" else
                         self._field(f, [{"value": c, "label": c} for c in f["choices"]] if f.get("choices") else None,
                                     kept.get(f["name"])) for f in wf.inputs if not f.get("hidden"))
        err = self.duplicate_notice(me, error) if isinstance(error, Duplicate) else \
            f'<div class="flash error" role="alert"><p>{e(str(error))}</p></div>' if error else ""
        anyway = ('<button class="secondary" name="_anyway" value="1">Start another anyway</button>'
                  if isinstance(error, Duplicate) and error.allowed else "")
        body = (f'<div data-modal-body>{err}<p class="muted">{e(self._brief(wf.summary))}</p>'
                f'<form method="post" action="/start" class="card stack"><input type="hidden" name="_workflow" '
                f'value="{e(wf.id)}">{self._req_note(pick + fields)}{pick}{fields}<div class="filters">'
                f'<button class="primary">Start</button>{anyway}'
                f'<a class="btn secondary" href="{e(self.record_href(about)) if about else "/start"}" data-modal-close>'
                f'Cancel</a>' + ("" if about else '<a class="btn subtle back" href="/start" data-modal>← Back</a>')
                + '</div></form></div>')
        return self.layout(req, wf.title, body, crumbs=[("/", "Practice"), ("/start", "Start work"), ("", wf.title)])

    @staticmethod
    def _brief(text: str, limit: int = 300) -> str:
        """A summary short enough to read above a form: whole sentences up to the limit, else whole words and "…"."""
        text = " ".join(text.split())
        if len(text) <= limit:
            return text
        cut = text[:limit]
        end = cut.rfind(". ")
        return cut[:end + 1] if end > limit // 3 else cut.rsplit(" ", 1)[0] + "…"

    def _record_field(self, wf) -> dict:
        on = self.hub.engine.start_on(wf)  # a workflow that makes its record is started on what it is about
        rt = self.hub.record_type(on[0]) if len(on) == 1 else None
        return {"path": "_record", "name": "_record", "label": rt.label if rt else " or ".join(on), "type": "reference",
                "target": on, "required": True}

    def _active_ref_input(self, me, f: dict, about: str, kept) -> str:
        """A start-form input that takes an active record (``active = true``), for work about a known record: a
        choice of that record's own active ones the member may read, newest first, each by its title (a booking by
        its time and members), the one under way (a booking started and not over) chosen."""
        hub = self.hub
        targets = f.get("target") or []
        opts, now = [], hub.engine.now().isoformat(timespec="minutes")
        for t in hub.concrete([targets] if isinstance(targets, str) else targets):
            rt = hub.record_type(t)
            if not rt or not self._may_read(me, t):
                continue
            for rec in hub.records.search(t, limit=None):
                if hub.subject_of(rec) != about or not schema.is_active(rt, rec):
                    continue
                label, key, under_way = self.hub.title(hub.redact(me, rec)), "", False
                if rt.is_a("Booking"):
                    seen = schema.project(rt, rec, hub.member_types())
                    if seen.get("cancelled"):
                        continue
                    key = str(seen.get("start") or "")
                    who = ", ".join(self.member_name(m) for m in seen.get("with") or [])
                    try:
                        when = datetime.fromisoformat(key[:16]).strftime("%a %b %d, %H:%M")
                    except ValueError:
                        when = key
                    label = " · ".join(x for x in (when, who) if x)
                    state = schema.first(rec, rt.lifecycle["path"]) if rt.lifecycle else None
                    under_way = bool(state and state != rt.lifecycle.get("initial")) or \
                        key[:16] <= now < str(seen.get("end") or "~")[:16]
                opts.append((key, f"{t}/{rec['id']}", label, under_way))
        opts.sort(key=lambda o: o[0], reverse=True)
        chosen = kept or next((o[1] for o in opts if o[3]), "")
        req = " required" if f.get("required") else ""
        none = "" if f.get("required") else '<option value="">None</option>'
        body = "".join(f'<option value="{e(o[1])}"{" selected" if o[1] == chosen else ""}>{e(o[2])}</option>'
                       for o in opts)
        helps = f'<small class="help">{e(f["help"])}</small>' if f.get("help") else ""
        return (f'<div class="field"><label>{e(f["label"])}<select name="{e(f["name"])}"{req}>{none}{body}</select>'
                f'</label>{helps}</div>')

    @staticmethod
    def _ref_input(f: dict) -> dict:
        """A start-form input naming a record, drawn with the record picker (type-ahead past 300 records)."""
        t = f.get("target") or []
        return {**f, "path": f["name"], "target": [t] if isinstance(t, str) else list(t)}

    def post_start_any(self, req):
        f = self._form_output(self.form(req))
        wid = f.get("_workflow", "")
        self.hub.workflow(wid)  # an unknown workflow is refused as it is, not as a form
        try:
            self._start_from(req, dict(f))
        except Invalid as exc:  # the start form again, saying why, with what was typed kept
            raise Rendered(409 if isinstance(exc, Duplicate) else 422,
                           self.page_start_one({**req, "query": {}}, wid, kept=f, error=exc)) from None

    def duplicate_notice(self, me, exc) -> str:
        """Why a start was refused as a duplicate (``trigger.one_open_per``): the open item by its key, with a link,
        who holds it and the step it is at (to a member who may see it), and whether another may be started."""
        run = exc.run
        wf = self.hub.workflow(run["workflow_id"], run.get("version"))[0]
        iid = run.get("item")
        item = self.hub.db.items.get(iid) if iid else None
        if item is None or not self.may_see_item(me, item, run):
            what = f"<b>{e(wf.title)} is already open for this.</b> " + (
                f"It is {e(self._key(iid))}; whoever holds it can tell you more." if item else "")
        else:
            role = item.get("role") or ""
            who = (f"held by {e(self.holder_text(item))}" if holder(item)["member"] else
                   f"waiting for {e(self.hub.role(role)['name'])}" if role and self.hub._role_known(role) else
                   "not held by anyone yet")
            at = ""
            if item.get("step_id"):
                try:
                    at = f', at “{e(wf.step(item["step_id"]).title)}”'
                except KeyError:
                    at = ""
            what = (f'<b>{e(wf.title)} is already open for this:</b> <a href="/items/{e(iid)}">'
                    f'{e(self._key(iid))}</a>, {who}{at}. Open it instead of starting another.')
        more = (" If this is a separate request, you can start another anyway." if exc.allowed else "")
        return f'<div class="flash warning" role="alert"><p>{what}{e(more)}</p></div>'

    def _start_from(self, req, f: dict):
        wid, rec = f.pop("_workflow", ""), f.pop("_record", "") or None
        f.pop("_fixed", None)
        anyway = f.pop("_anyway", "") == "1"
        wf, _ = self.hub.workflow(wid)
        for i in wf.inputs:  # a name typed in a picker becomes the record it names (refused if it fits two)
            if i["type"] == "reference" and f.get(i["name"]):
                f[i["name"]] = self._resolve_ref(self._ref_input(i), f[i["name"]])
        if wf.record and rec:
            rec = self._resolve_ref(self._record_field(wf), rec)
        on = self.hub.engine.start_on(wf)
        if wf.record and not (rec and rec.split("/", 1)[0] in on):
            label = self._record_field(wf)["label"]
            raise Invalid(f"choose {a_an(label.lower())} to start this about")
        run = self.hub.engine.start(req["me"], wid, record=rec, inputs=f, allow_duplicate=anyway)
        tell = self.tell_the_caller(run["id"], me=req["me"])
        booked = self.booked_confirmation(run["id"])
        if not tell and not booked:
            raise Redirect(self.run_href(run))
        # what the checks say to tell the caller is the page itself, not a link on a run page that may show nothing
        waiting = self.hub.db.items.find({"run_id": run["id"], "status": {"$in": ["open", "claimed"]}})
        roles = sorted({it.get("role") or "" for it in waiting} - {""})
        names = ", ".join(self.hub.role(r)["name"] for r in roles if self.hub._role_known(r))
        sent = (f'<div class="flash warning" role="status"><p><b>Sent on to {e(names)}.</b> Nothing is booked '
                f'yet.</p></div>' if names and not booked else "")
        body = (f'<div data-modal-body data-modal-stay>{booked}{tell}{sent}<div class="filters"><a class="btn primary" '
                f'href="{e(self.run_href(run))}">{e(self._open_words(run))}</a></div></div>')
        raise Rendered(200, self.layout(req, f"{wf.title}: started", body,
                                        crumbs=[("/start", "Start"), (None, wf.title)]))

    # ------------------------------------------------------------------------------------------ items
    def page_item(self, req, iid, kept: dict | None = None, error: Exception | None = None):
        me, eng = req["me"], self.hub.engine
        item, run = self._item_for(me, iid)
        wf, _ = self.hub.workflow(run["workflow_id"], run["version"])
        try:
            step = wf.step(item["step_id"])
        except KeyError:
            step = None
        crumbs = [("/board", "Board"), (self.run_href(run), self.hub.run_title(req["me"], run)), ("", self._key(iid))]
        dl = [("Status", self._item_loz(item) + self._urgency(item)), ("Held by", self.holder_html(item)),
              ("Role", e(self._role_words(item["role"]) or "—")),
              ("Type", f'{self._ki(item["kind"])} {e({"gate": "decision", "hold": "approval"}.get(item["kind"], item["kind"]))}')]
        if step and item["kind"] in ("task", "gate"):
            risk = f'<span class="badge risk-{e(step.risk)}">{e(step.risk)}</span>'
            dl += [("Step", e(step.title)), ("Agent risk", risk),
                   ("Agents", "may do this" if step.agents_may and not item["persons_only"] else "a person must do this")]
            if step.kind == "gate":
                dl.append(("Decided by", "a person, who signs" if step.signer == "person" else
                           "a person, who signs; or an agent within criteria, whose decision is its attestation, not a signature"))
        rec = ""
        if run["record"] and run["record"].startswith(self.subj["type"] + "/"):
            subj = self._may_read(me, self.subj["type"]) and self.hub.records.read(*run["record"].split("/", 1))
            if subj:  # who it is and how to reach them, not an id
                self.hub.note_view(me, run["record"], how=f"work item {iid}")
                rec = self.subject_link(run["record"], self.hub.title(subj))
                dl.append((self.subj["label"], rec))
                facts = self.hub.facts(subj)
                reach = next((v for k, v in facts.items() if v and any(w in str(k).lower() for w in ("contact", "phone"))
                              and "emergency" not in str(k).lower()), None)
                if reach:
                    dl.append(("Contact", e(str(reach))))
            else:
                rec = self.subject_link(run["record"])
                dl.append((self.subj["label"], rec))
        elif run["record"]:
            rt = self.hub.record_type(run["record"].split("/")[0])
            rec = (self._ref_html(me, run["record"])
                   or f'<a href="{e(self.record_href(run["record"]))}">{e(run["record"])}</a>')
            dl.append((rt.label if rt else "Record", rec))
        dl += [("Workflow", f'<a href="/workflows/{e(wf.id)}">{e(wf.title)}</a> v{run["version"]}'),
               ("Work", f'<a href="{e(self.run_href(run))}">{e(self.hub.run_title(req["me"], run))}</a>'),
               ("Created", self._at(item["created_at"])), ("Updated", self._at(item["updated_at"]))]
        details = ('<aside class="details"><h3>Details</h3><dl>'
                   + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in dl) + "</dl></aside>")
        note = f'<div class="note card">Note: {e(item["note"])}</div>' if item.get("note") else ""
        actions = ""
        main = [note, self._pass_strip(me, run), self._blocked_html(item)]
        if error is not None:
            problems = [p for p in (getattr(error, "problems", None) or []) if str(p) != str(error)]
            main.insert(0, f'<div class="error" role="alert"><p>That did not go through: {e(str(error))}</p>'
                           + ("<ul>" + "".join(f"<li>{e(str(p))}</li>" for p in problems) + "</ul>" if problems else "")
                           + "<p>Nothing was saved. What you typed is kept below.</p></div>")
        # a notice told to this member by name says only what is theirs to know (a review of an emergency access);
        # any other work about a file a directive keeps from them shows the directive's note instead
        withheld = (None if item["kind"] == "notice" and item.get("assignee") == me.get("id")
                    else Viewer(self.hub, me).run_withheld(run))
        if withheld:
            # what the directive keeps from this member: not the request, a draft, a result or a notice's content
            main.append(f'<div class="card withheld"><p><b>{e(withheld)}</b></p><p class="muted">What this is about '
                        f'is not shown to you. In an emergency, open the file with emergency access first (the '
                        f'privacy officer reviews it); otherwise leave it to someone the directive allows.</p>'
                        + (f'<p><a href="{e(self.record_href(run["record"]))}">Open the file</a></p>'
                           if run.get("record") else "") + '</div>')
            if item["kind"] == "notice" and item["status"] not in ("done", "cancelled"):
                actions = (f'<form method="post" action="/items/{iid}/ack" class="inline"><button>Acknowledge'
                           f'</button></form>')
        elif item["status"] == "waiting":
            main.append(f'<div class="card"><p><b>{e(item.get("waiting") or "Waiting for the workflow to go on")}'
                        f'</b></p><p class="muted">Nobody needs to do anything here yet; it comes back to someone '
                        f'when the next step is theirs.</p>'
                        f'{self._wait_records_html(me, run, self._open_wait(run, wf))}</div>')
        elif item["status"] in ("done", "cancelled"):
            out = item["output"] or {}
            ok, missing = (True, [])
            if item["kind"] != "notice":
                ok, missing = Viewer(self.hub, me).step_visible(wf, item["step_id"])
            shown = self._output_html(out, me) if ok else f'<p class="muted">Content needs access to: {e(", ".join(self._class_name(c) for c in missing))}</p>'
            main.append(f'<div class="section"><h2>Result</h2><div class="card">{shown}</div></div>')
        elif item["kind"] == "notice":
            actions = (f'<form method="post" action="/items/{iid}/ack" class="inline"><button>Acknowledge</button></form>')
            if eng.escalated_item(me, item):  # overdue work this member may do: taking it is the first thing to do
                actions = (f'<form method="post" action="/items/{iid}/take-over" class="inline"><button>Take it over'
                           f'</button></form> <form method="post" action="/items/{iid}/ack" class="inline">'
                           f'<button class="secondary">Acknowledge</button></form>')
            else:
                for w in self._late_work_of(me, item):  # an escalation: the way to the late work itself
                    go = ("Take it over" if eng.may_take_over(me, w) else "Open the work")
                    actions = (f'<a class="btn {"danger" if go == "Take it over" else "primary"}" '
                               f'href="/items/{e(w["id"])}{"#take-over" if go == "Take it over" else ""}">{go} '
                               f'({e(self._key(w["id"]))})</a> ' + actions)
            about = (item.get("output") or {}).get("about") or ""
            if about.startswith("outbox:"):
                main.append(self._message_card(me, about.split(":", 1)[1]))
            else:
                main.append(f'<div class="card">{self._output_html(item["output"], me) if item.get("output") else ""}'
                            f'<p class="muted">A notice: read it, then acknowledge.</p></div>')
        elif item["kind"] == "hold":
            main.append(self._hold_form(me, item, run, kept))
        else:
            main.append(self._work_form(me, item, iid, step, kept))
            actions = self._item_actions(me, item)
        main.append(self._item_family(me, item))
        main.append(self._step_history(me, wf, item))
        events = [ev for ev in self.hub.track.list(run_id=run["id"], limit=500) if ev["subject"] == f"item:{iid}"]
        main.append(f'<div class="section"><h2>Activity</h2>{self._activity(list(reversed(events)), me)}</div>')
        body = f'<div class="issue"><div data-modal-body>{"".join(main)}</div>{details}</div>'
        return self.layout(req, item["title"], body, crumbs=crumbs, actions=actions)

    def _item_actions(self, me, item, back: str = "") -> str:
        """What a member may do with a person's step besides its form: assign it to themselves, give it back, or take
        it over or reassign it. ``back``: where the forms return to (the record page the step is shown on)."""
        eng, iid = self.hub.engine, item["id"]
        hidden = f'<input type="hidden" name="_back" value="{e(back)}">' if back else ""
        actions = ""
        ok, _ = eng.eligibility(me, item)
        ok_free = eng.eligibility(me, {**item, "status": "open", "claimed_by": None, "assignee": None})[0]
        if ok and item["status"] == "open" and item["assignee"] != me["id"]:
            # only on a shared queue, where it tells the others it is taken; Done takes it anyway
            actions = (f'<form method="post" action="/items/{iid}/claim" class="inline">{hidden}<button class="secondary" '
                       f'aria-describedby="claim-hint-{iid}">Assign to me</button> <span class="muted" '
                       f'id="claim-hint-{iid}">Optional: tells the others you are on it. Finishing it assigns it to you '
                       f'anyway.</span></form>')
        elif ok:
            actions = (f'<form method="post" action="/items/{iid}/release" class="inline">{hidden}<label>Why you give it '
                       f'back <span class="muted">(optional)</span> <input name="note"></label> '
                       f'<button class="secondary">Give it back</button></form>')
        elif eng.may_reassign(me, item):
            held = self.member_name(eng.holder(item))
            people = "".join(f'<option value="{e(m["id"])}">{e(m["name"])}</option>'
                             for m in eng.reassign_options(item))
            mine = '<option value="">Me</option>' if eng.may_take_over(me, item) and ok_free else ""
            actions = (f'<form method="post" action="/items/{iid}/take-over" class="inline reassign" '
                       f'id="take-over">{hidden}'
                       f'<label>Move from {e(held)} to <select name="to">{mine}{people}'
                       f'<option value="queue">Anyone who can</option></select></label>'
                       f'<label>Why <span class="muted">(they will see it)</span> <input name="note" required></label>'
                       f'<button>{"Take over" if item["escalated"] else "Reassign"}</button></form>')
        return actions

    def _item_family(self, me, item) -> str:
        """The item a child closes back into, and the children an item waits on (each with its key and holder)."""
        out = []
        if item.get("parent_id"):
            out.append(f'<p>Part of <a href="/items/{e(item["parent_id"])}">{e(self._key(item["parent_id"]))}</a>'
                       f'</p>')
        kids = self.hub.db.items.find({"_id": {"$in": list(item.get("children") or [])}}, sort="n") \
            if item.get("children") else []
        if kids:
            rows = "".join(f'<li><a href="/items/{e(k["_id"])}">{e(self._key(k["_id"]))}</a> '
                           f'{e(k.get("step_title") or k["title"])} {self._loz(self.ITEM_STATUS, k["status"])} '
                           f'{self.holder_html(k, avatar=False)}</li>'
                           for k in kids)
            out.append(f'<div class="section"><h2>Parts</h2><ul class="plain">{rows}</ul></div>')
        return "".join(out)

    def _step_history(self, me, wf, item) -> str:
        """The steps this item has been through, in order: who held each, when it began and ended, and how."""
        hist = item.get("history") or []
        if not hist:
            return ""
        viewer = Viewer(self.hub, me)
        rows = []
        for h in hist:
            if h.get("step") and not viewer.step_visible(wf, h["step"])[0]:
                rows.append('<tr><td colspan="4" class="muted">A step you may not see</td></tr>')
                continue
            who = self.member_name(h.get("by")) or self.member_name(h.get("assignee")) or \
                self._role_words(h.get("role")) or ""
            how = (h.get("outcome") or ("in progress" if not h.get("done_at") else "done")).replace("_", " ")
            if not h.get("done_at") and not h.get("outcome") and h.get("step") == item.get("step_id") \
                    and item["status"] in ("open", "claimed"):  # the step now: whoever holds the item holds it
                held = holder(item)
                who = self.holder_text(item) if held["member"] else who
                how = "in progress" if held["how"] == "taken" else "waiting to be taken"
            extra = (" (ran out of time)" if h.get("timed_out") else "") + \
                (f" (escalated {h['escalated']}x)" if h.get("escalated") else "")
            rows.append(f'<tr><td>{e(h.get("title") or "")}</td><td>{e(who)}</td>'
                        f'<td>{self._at(h["started_at"]) if h.get("started_at") else ""}</td>'
                        f'<td>{e(how + extra)}</td></tr>')
        return (f'<div class="section"><h2>Steps so far</h2><table><thead><tr><th>Step</th><th>Who</th>'
                f'<th>Began</th><th>How it went</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')

    def _work_form(self, me, item, iid, step, kept: dict | None = None, *, back: str = "",
                   on_record: bool = False) -> str:
        """A person's step: what it reads and its form (a decision, or its answer). ``back``: the page its forms return
        to; ``on_record``: shown on the record's own page, which is what the step reads, so that stays folded."""
        eng = self.hub.engine
        kept = kept or {}
        hidden = f'<input type="hidden" name="_back" value="{e(back)}">' if back else ""
        ok, why = eng.eligibility(me, item)
        if not ok:
            return f'<div class="error">You cannot take this: {e(self.holder_refusal(me, item, why))}</div>'
        ctx = eng.context(me, iid)
        st = ctx["step"]
        parts = []
        run = self.hub.engine.run(item["run_id"])
        wf = self.hub.workflow(run["workflow_id"], run["version"])[0]
        asked = {k: v for k, v in ctx["context"].items() if k == "input" or k.startswith("input.")}
        if asked:  # what the person wrote, in their own words, before anything about it
            parts.append(f'<div class="section"><h2>The request</h2><div class="card request">'
                         f'{self._context(asked, wf, me)}</div></div>')
        rest = {k: v for k, v in ctx["context"].items() if k not in asked}
        if st.get("instructions"):
            parts.append(f'<div class="section"><h2>Description</h2><div class="card">{e(st["instructions"])}</div></div>')
        if st.get("needs"):  # requires_links: what to link first, on the record's page
            rec_url = self.record_href(run["record"]) + "#links"
            parts.append(f'<div class="flash info" role="note"><p>Before this is done, the record needs: '
                         f'{e("; ".join(st["needs"]))}. <a href="{e(rec_url)}">Link it on the record</a>.</p></div>')
        if ctx.get("skill") and not on_record:
            parts.append(f'<details class="card"><summary>How an agent is told to do this ({e(ctx["skill"]["title"])})'
                         f'</summary><pre class="wrap">{e(ctx["skill"]["instructions"])}</pre></details>')
        if rest:
            parts.append(f'<details class="section"{"" if on_record else " open"}><summary><b>What this step reads'
                         f'</b></summary><div class="card">{self._context(rest, wf, me)}</div></details>')
        if ctx.get("draft"):
            by = ctx["draft"].get("by")
            parts.append(f'<div class="section"><h2>Draft by {self.who(by)}</h2><div class="card">'
                         f'{self._values(ctx["draft"], me)}</div></div>')
        q = urllib.parse.quote(back, safe="") if back else ""
        tail = f'&amp;_back={e(q)}' if q else ""
        if st["kind"] == "gate":
            # on the record's page, the record is the page above: only what changed in it is said again
            parts.append(self._signed_changes(me, run, step) if on_record else self._signed_record_view(me, run, step))
            # the decision leads, on the record's page and the step's own: a button for each outcome, as a record's
            # moves are, each opening a dialog for that outcome alone (what it signs, what it sets going, a comment,
            # the signature)
            last = len(st["outcomes"]) - 1
            btns = "".join(
                f'<a class="btn {"primary" if i == 0 else "danger-subtle" if i == last and self._ends(o) else "secondary"}" '
                f'href="/items/{e(iid)}/decide?outcome={e(urllib.parse.quote(o, safe=""))}{tail}" '
                f'data-modal>{e(self.outcome_word(step, o))}…</a>'
                for i, o in enumerate(st["outcomes"]))
            parts.insert(0, f'<div class="section rw-decide"><h2>Decide</h2>'
                            f'<div class="rw-moves" role="group" aria-label="Your decision">{btns}</div>'
                            f'<p class="muted">Each opens what that decision signs and what follows it.</p></div>')
        else:
            # a task leads the same way: a button for each way it can end (its outcomes), or one Done, each opening
            # a dialog with the step's fields; an answer refused comes back in its dialog, as typed
            outs = list(st["outcomes"]) if step else []
            btns = "".join(
                f'<a class="btn {"primary" if i == 0 else "secondary"}" '
                f'href="/items/{e(iid)}/answer?outcome={e(urllib.parse.quote(o, safe=""))}{tail}" '
                f'data-modal>{e(self.outcome_word(step, o))}…</a>' for i, o in enumerate(outs)) or (
                f'<a class="btn primary" href="/items/{e(iid)}/answer?{tail[5:]}" data-modal>Done…</a>')
            said = ("Each opens what to note for it and what follows it." if outs
                    else "Opens what this step asks you to fill in.")
            parts.insert(0, f'<div class="section rw-decide"><h2>Do it</h2>'
                            f'<div class="rw-moves" role="group" aria-label="How it went">{btns}</div>'
                            f'<p class="muted">{said}</p></div>')
        return "".join(parts)

    def outcome_word(self, step, outcome: str) -> str:
        """An outcome in words: on a workflow written as states and edges, its edge's label."""
        return (getattr(step, "outcome_labels", None) or {}).get(outcome) or self.human(outcome)

    @staticmethod
    def _ends(outcome: str) -> bool:
        """An outcome that turns the request down, shown as the quiet red button (decline, reject, cancel)."""
        return any(w in outcome for w in ("decline", "reject", "refuse", "cancel", "deny"))

    def _assign_fields(self, run: dict, step, outcome: str, kept: dict) -> str:
        """Who does each piece of people's work this transition sets going (``Engine.assignments_needed``): one person
        each, chosen here and required. A role's group is who may be chosen, not who it goes to."""
        return self._assign_selects(self.hub.engine.assignments_needed(run, step, outcome), kept,
                                    here=run["workflow_id"])

    def _assign_selects(self, needed: list[dict], kept: dict, *, here: str = "") -> str:
        """A required choice of one person for each piece of work in ``needed`` (``Engine.assignable``)."""
        out = []
        for n in needed:
            name = f"assign:{n['key']}"
            chosen = str(kept.get(name) or "")
            opts = "".join(f'<option value="{e(m["id"])}"{" selected" if m["id"] == chosen else ""}>{e(m["name"])}</option>'
                           for m in sorted(n["people"], key=lambda m: m["name"]))
            where = ("" if here and n["key"].startswith(f"{here}/")  # in a record it starts: which
                     else f' <span class="muted">({e(n["workflow"])})</span>')
            out.append(f'<label>Who does “{e(n["title"])}”{where} *<select name="{e(name)}" required>'
                       f'<option value="">Choose a person…</option>{opts}</select>'
                       f'<small>It goes to this person, not to everyone who holds {e(self.human(n["role"]))}.</small></label>')
        return "".join(out)

    def page_decide(self, req, iid, *, kept: dict | None = None, error=None):
        """One outcome of a decision, as its own dialog: what is decided about, the sentence this decision signs for
        this member, what it sets going (``Engine.outcome_follows``), a comment, and the signature. Sent from the
        record page's decision buttons (``data-modal``); posts to the item's decide as the full form does."""
        me, eng = req["me"], self.hub.engine
        item = eng.item(iid)
        kept = kept or {}
        outcome = kept.get("outcome") or req["query"].get("outcome", "")
        back = self.safe_back(kept.get("_back") or req["query"].get("_back"))
        run = eng.run(item["run_id"])
        wf = self.hub.workflow(run["workflow_id"], run["version"])[0]
        step = wf.step(item["step_id"])
        if step.kind != "gate" or outcome not in eng.open_outcomes(run, step):
            raise Invalid(f"“{self.human(outcome)}” is not a decision of “{step.title}”")
        ok, why = eng.eligibility(me, item)
        if not ok:
            raise Denied(self.holder_refusal(me, item, why))
        if item.get("status") not in ("open", "claimed"):
            raise Invalid("this decision has already been made")
        label = self.outcome_word(step, outcome)
        meaning = eng.meaning_for(me, run, step, outcome)
        about = self.hub.run_title(me, run)
        follows = eng.outcome_follows(run, step, outcome)  # steps of the workflow they are deciding in: theirs to see
        then = ("".join(f"<li>{e(s.title)}</li>" for s in follows) if follows else "")
        hidden = f'<input type="hidden" name="_back" value="{e(back)}">' if back else ""
        cancel = back or f"/items/{iid}"
        err = (f'<div class="flash error" role="alert"><p>That did not go through: {e(str(error))}</p>'
               f'<p>Nothing was decided. What you typed is kept below.</p></div>' if error is not None else "")
        body = (f'<div data-modal-body>{err}<p><b>{e(step.title)}</b> <span class="muted">·</span> {e(about)}</p>'
                + (f'<div class="card"><p class="muted">What follows</p><ul class="plain then">{then}</ul></div>'
                   if then else '<p class="muted">Nothing else follows from it in this workflow.</p>')
                + f'<form method="post" action="/items/{e(iid)}/decide" class="card stack">{hidden}'
                f'<input type="hidden" name="outcome" value="{e(outcome)}"><input type="hidden" name="_dialog" value="1">'
                f'{self._assign_fields(run, step, outcome, kept)}<label>Comment<textarea name="comment" rows="3">{e(str(kept.get("comment", "")))}</textarea>'
                f'<small>Kept with your decision in the record\'s history.</small></label>'
                f'<p class="sign">Deciding is your electronic signature: <b>“{e(meaning)}”</b>, over exactly what '
                f'this step shows you. If it changes before you decide, you are asked to read it again.</p>'
                f'{self._sign_prompt(me)}<input type="hidden" name="seen" value="{e(eng.shown_digest(me, iid))}">'
                f'<div class="filters"><button class="primary">{e(label)}: sign and decide</button>'
                f'<a class="btn secondary" href="{e(cancel)}" data-modal-close>Cancel</a></div></form></div>')
        crumbs = [(cancel, about), (None, label)]
        return self.layout(req, label, body, crumbs=crumbs)

    def page_answer(self, req, iid, *, kept: dict | None = None, error=None):
        """A task's answer, as its own dialog: for one of its outcomes (``?outcome=``), what that sets going; the
        step's fields, with a draft or what was typed before filled in; Done. Sent from the step's buttons
        (``data-modal``) on the record's page or the step's own; posts to the item's complete."""
        me, eng = req["me"], self.hub.engine
        item = eng.item(iid)
        kept = kept or {}
        outcome = kept.get("outcome") or req["query"].get("outcome", "")
        back = self.safe_back(kept.get("_back") or req["query"].get("_back"))
        run = eng.run(item["run_id"])
        wf = self.hub.workflow(run["workflow_id"], run["version"])[0]
        step = wf.step(item["step_id"])
        if item["kind"] != "task":
            raise Invalid(f"“{step.title}” is not a task")
        if step.outcomes and outcome not in eng.open_outcomes(run, step):
            raise Invalid(f"“{self.human(outcome)}” is not a way “{step.title}” can end")
        ok, why = eng.eligibility(me, item)
        if not ok:
            raise Denied(self.holder_refusal(me, item, why))
        if item.get("status") not in ("open", "claimed"):
            raise Invalid("this step has already been done")
        ctx = eng.context(me, iid)
        prefill = dict(ctx.get("draft") or {})
        prefill.update(self._form_output(kept))
        fields = "".join(self._field(f, ctx["choices"].get(f["name"]), prefill.get(f["name"]))
                         for f in ctx["fields"] if f["name"] != "outcome" or not step.outcomes)
        label = self.outcome_word(step, outcome) if outcome else "Done"
        about = self.hub.run_title(me, run)
        follows = eng.outcome_follows(run, step, outcome) if outcome else []
        then = (f'<div class="card"><p class="muted">What follows</p><ul class="plain then">'
                + "".join(f"<li>{e(s.title)}</li>" for s in follows) + "</ul></div>" if follows else "")
        hidden = (f'<input type="hidden" name="_back" value="{e(back)}">' if back else "") + \
            (f'<input type="hidden" name="outcome" value="{e(outcome)}">' if outcome else "")
        cancel = back or f"/items/{iid}"
        err = ""
        if error is not None:
            problems = [p for p in (getattr(error, "problems", None) or []) if str(p) != str(error)]
            err = (f'<div class="flash error" role="alert"><p>That did not go through: {e(str(error))}</p>'
                   + ("<ul>" + "".join(f"<li>{e(str(p))}</li>" for p in problems) + "</ul>" if problems else "")
                   + '<p>Nothing was saved. What you typed is kept below.</p></div>')
        body = (f'<div data-modal-body>{err}<p><b>{e(step.title)}</b> <span class="muted">·</span> {e(about)}</p>'
                f'{then}<form method="post" action="/items/{e(iid)}/complete" class="card stack">{hidden}'
                f'<input type="hidden" name="_dialog" value="1">{fields}{self._assign_fields(run, step, outcome, kept)}'
                f'<div class="filters"><button class="primary">{e(label)}{": done" if outcome else ""}</button>'
                f'<a class="btn secondary" href="{e(cancel)}" data-modal-close>Cancel</a></div></form></div>')
        return self.layout(req, label, body, crumbs=[(cancel, about), (None, label)])

    def _signed_badge(self, ev) -> str:
        """A person's signature, or an agent's attested decision, which is never a signature."""
        if ev.get("signature"):
            return ' <span class="badge signed">signed</span>'
        att = (ev.get("data") or {}).get("attested")
        if att:
            return (f' <span class="badge">decided by {e(self.member_name(att.get("agent") or ev["actor"]))} (an agent)'
                    f', not a signature</span>')
        return ""

    EVENT_QUIET = {"signed", "attested", "refs", "refs_sha256", "hash", "mac", "signature", "version", "parent",
                   "blocks", "skills", "cause", "credential", "token", "jti", "token_id"}

    def _event_words(self, ev: dict, me: dict, memo: dict | None = None, raw: bool = True) -> str:
        """An event's data as plain lines, "Label: value", members by name, records by their title or kind, a step
        by its title, no ids; the raw data stays under Details for whoever needs it."""
        memo = {} if memo is None else memo
        data = {k: v for k, v in (ev.get("data") or {}).items() if k not in ("signed", "attested")}
        if not data:
            return ""
        wf = None
        if ev.get("run_id"):
            key = ("wf", ev["run_id"])
            if key not in memo:
                memo[key] = Viewer(self.hub, me).workflow_of_run(ev["run_id"])
            wf = memo[key]

        def member(v):
            if ("m", v) not in memo:
                memo[("m", v)] = self.hub.db.members.get(v) if self.SLUG_RE.fullmatch(v) else None
            return memo[("m", v)]

        def say(k, v, depth=0) -> str:
            if v is None or v == "" or v == [] or v == {}:
                return ""
            if isinstance(v, bool):
                return "Yes" if v else "No"
            if isinstance(v, (int, float)):
                return e(str(v))
            if isinstance(v, str):
                if k == "workflow":
                    try:
                        return e(self.hub.workflow(v)[0].title)
                    except HubError:
                        return e(self.human(v))
                if v in ("override", "break_glass"):
                    return "emergency access"
                if k == "when" and wf is not None:
                    return e(cond_words(wf, v).removeprefix("when "))
                if k in ("role", "escalate_to", "to_role") and self.hub._role_known(v):
                    return e(self.hub.role(v)["name"])
                if k in ("step", "step_id") and wf is not None:
                    try:
                        return e(wf.step(v).title)
                    except KeyError:
                        pass
                m = member(v)
                if m:
                    return e(m["name"])
                if v.startswith("workflow:"):
                    return "automation"
                ref = self._ref_html(me, v)
                if ref:
                    return ref
                if self.ID_RE.fullmatch(v):
                    return ""  # an internal id says nothing to a reader
                plain = " ".join(self.ID_RE.sub("", v).split())
                if plain != v and plain.lower() in ("", "run", "item", "event"):
                    return ""
                return e(plain if len(plain) <= 160 else plain[:160] + "…")
            if isinstance(v, list):
                parts = [x for x in (say(k, x, depth + 1) for x in v[:6]) if x]
                return ", ".join(parts) + (" …" if len(v) > 6 else "")
            if isinstance(v, dict) and depth < 1:
                parts = [f"{e(self.human(kk))} {x}" for kk, x in ((kk, say(kk, vv, depth + 1)) for kk, vv in v.items()
                                                                    if kk not in self.EVENT_QUIET) if x]
                return "; ".join(parts[:6]) + (" …" if len(parts) > 6 else "")
            return ""

        lines = []
        for k, v in data.items():
            if k in self.EVENT_QUIET:
                continue
            text = say(k, v)
            if text:
                lines.append(f'<li><span class="muted">{e(self.human(k))}:</span> {text}</li>')
        plain = f'<ul class="plain ev-lines">{"".join(lines[:10])}</ul>' if lines else ""
        if not raw:
            return plain
        return (plain + f'<details><summary class="meta">Details</summary><pre>{e(json.dumps(data, indent=1))}</pre>'
                        f'</details>')

    SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
    ID_RE = re.compile(r"\b(?:(?:run|item|ev|evt|out|tok|cred)[-_][A-Za-z0-9]{6,}|[0-9a-f]{10,})\b")

    def _event_on(self, me, subject: str) -> str:
        """What an event was about, in words: a record by its title (or its kind), a workflow by its title."""
        if not subject:
            return ""
        ref = self._ref_html(me, subject)
        if ref:
            return ref
        kind, _, rest = subject.partition(":")
        if kind == "workflow":
            try:
                return e(self.hub.workflow(rest.split("@")[0])[0].title)
            except HubError:
                return "a workflow"
        words = {"step": "a step", "item": "a work item", "member": "a member", "role": "a role", "block": "a block",
                 "type": "a record type", "view": "a view", "skill": "a skill", "module": "a module"}
        if kind in words and rest:
            m = self.hub.db.members.get(rest) if kind == "member" else None
            return e(m["name"]) if m else words[kind]
        return e(self.human(subject)) if not self.ID_RE.search(subject) else ""

    ACTION_WORDS = {
        "run.started": "started this work", "run.completed": "finished this work", "run.cancelled": "cancelled this work",
        "run.deadline_missed": "noted that this work missed its deadline", "action.ran": "did an automatic step",
        "step.activated": "started a step", "step.done": "finished a step", "step.skipped": "found a step not needed",
        "step.waiting": "is waiting", "step.failed": "could not do a step", "step.retried": "tried a step again",
        "step.queued": "put an automatic step in the queue", "step.started": "began an automatic step",
        "step.requeued": "put a step that was cut short back in the queue",
        "step.late": "got the answer to a step after it was over",
        "item.claimed": "took the work", "item.released": "gave the work back", "item.escalated": "passed the work up",
        "item.overdue": "noted the work is late", "item.taken_over": "took the work over", "item.held": "held the work "
        "for approval", "item.passed_on": "passed the work on", "item.refused": "refused the work",
        "hold.decided": "decided on the held work", "notice.acknowledged": "read a notice",
        "record.created": "created", "record.updated": "changed", "record.transitioned": "moved on",
        "record.signed": "signed", "record.attested": "attested", "record.viewed": "opened",
        "file.added": "added a file", "booking.overbooked": "overbooked a slot", "event.received": "received an event",
    }

    def _activity(self, events, me) -> str:
        """Events as an activity feed in words, redacted for this viewer. Events about steps this member may not see
        fold into one line each run of them; event numbers and raw data are for policy owners (who audit)."""
        rows, memo = [], {}
        audit = self.hub.is_policy_owner(me)
        hidden = 0

        def fold():
            if hidden:
                rows.append(f'<li class="muted"><span class="av empty" aria-hidden="true">·</span><div class="body">'
                            f'{hidden} thing{"s" if hidden != 1 else ""} happened in steps you may not see.</div></li>')

        for ev in Viewer(self.hub, me).events(events):
            data = {k: v for k, v in ev["data"].items() if k not in ("signed", "attested")}
            if data and set(data) <= {"hidden"}:  # all it would say is that it is hidden
                hidden += 1
                continue
            fold()
            hidden = 0
            signed = self._signed_badge(ev)
            cause = (f' · because of <a href="/track/{ev["cause"]}">#{ev["cause"]}</a>'
                     if ev["cause"] and audit else "")
            seq = f'<a href="/track/{ev["seq"]}">#{ev["seq"]}</a> · ' if audit else ""
            what = self.ACTION_WORDS.get(ev["action"], ev["action"].replace(".", " ").replace("_", " "))
            rows.append(f'<li>{self._av(ev["actor"])}<div class="body"><div><b>{e(self.member_name(ev["actor"]))}</b> '
                        f'{e(what)}{signed}</div><div class="meta">{seq}{self._at(ev["at"])}{cause}</div>'
                        + (self._event_words(ev, me, memo, raw=audit) if data else "") + "</div></li>")
        fold()
        return f'<ul class="activity">{"".join(rows)}</ul>' if rows else '<p class="muted">No activity yet.</p>'

    # ------------------------------------------------------------------------------------------ runs
    @staticmethod
    def _flow_json(wf, run=None, links=None, viewer=None) -> str:
        """The workflow as the flow diagram draws it. With a run and a ``viewer``: a step the member may not see
        is drawn as one, without its title, condition or state (which branch ran is what it would tell)."""
        hide = {s.id for s in wf.steps if viewer and run and viewer.step_state(wf, run, s.id) is None}
        alias = {sid: f"hidden-{i + 1}" for i, sid in enumerate(s.id for s in wf.steps if s.id in hide)}
        name = lambda sid: alias.get(sid, sid)
        data = {"steps": [{"id": name(s.id), "after": [name(a) for a in s.after],
                           **({"title": "A step you may not see", "kind": "task", "role": None, "action": None,
                               "when": None, "agents_may": False} if s.id in hide else
                              {"title": s.title, "kind": s.kind, "role": s.role, "action": s.action,
                               "when": None if viewer and run and viewer.step_state(wf, run, s.id) == "if-needed"
                               and not viewer.full(wf) else (cond_words(wf, s.when) or None),
                               "agents_may": s.agents_may and s.kind != "automation", "timing": step_timing(s)})}
                          for s in wf.steps]}
        if run:
            states = viewer.run_steps(wf, run) if viewer else run["steps"]
            data["states"] = {sid: st["state"] for sid, st in states.items()}
        if links:
            data["links"] = {k: v for k, v in links.items() if k not in hide}
        return json.dumps(data).replace("</", "<\\/")

    def _flow(self, wf, run=None, links=None, legend=True, viewer=None) -> str:
        fid = f"flow-{wf.id}-{run['id'] if run else 'def'}"
        leg = ('<div class="legend"><span><span class="ki task">T</span> something a person does</span><span>'
               '<span class="ki gate">D</span> a decision someone signs</span><span><span class="ki automation">A</span> '
               'done automatically</span><span><span class="badge agent">AI</span> an assistant may do it</span>'
               '<span>- - - only when needed</span></div>') if legend else ""
        return (f'<div class="flowwrap"><svg data-flow="{fid}"></svg></div>{leg}'
                f'<script type="application/json" id="{fid}">{self._flow_json(wf, run, links, viewer)}</script>')

    def _states_view(self, wf, marks: dict | None = None) -> str:
        """A workflow written as states and edges (practicehub/states.py), as its page shows it: where the record
        enters, then each state it stops in with the work done there, and each edge out of it: what the person
        chooses (or, on an automated state, when the record leaves that way), the automations that run on the way,
        and where it lands. ``marks`` (a record's own view of it): ``{"visited", "current", "taken"}``, the states
        that record has been in, the one it is in, and the edges it took, each said in a word beside its name."""
        shape = wf.shape
        marks = marks or {}
        tones = {st["id"]: st.get("tone") for st in shape["states"]}
        names = {st["id"]: st.get("label") or self.human(st["id"]) for st in shape["states"]}

        def loz(sid):
            return f'<span class="loz {self.TONE_CLASS.get(tones.get(sid) or "", "default")}">{e(names.get(sid, sid))}</span>'

        def step_line(sid):
            st = wf.step(sid)
            bits = []
            if st.kind != "automation":
                who = self._role_name(st.role) if st.role else ""
                bits.append(f"by {e(who)}" if who else "")
                if st.agents_may:
                    bits.append('<span class="badge agent">AI</span> an assistant may do it')
                if st.due:
                    bits.append(f"within {e(duration_text(st.due))}")
                if getattr(st, "priority", None) == "urgent":
                    bits.append('<span class="badge urgent">urgent</span>')
            when = (f' <span class="muted">only when</span> <code>{e(st.when)}</code>'
                    if st.when and st.kind != "automation" else "")
            kind = {"task": "task", "gate": "gate"}.get(st.kind, "automation")
            letter = {"task": "T", "gate": "D"}.get(st.kind, "A")
            return (f'<li><span class="ki {kind}">{letter}</span> {e(st.title)}'
                    f'{(" <span class=muted>· " + " · ".join(b for b in bits if b) + "</span>") if any(bits) else ""}'
                    f'{when}</li>')

        def edge_block(edge, automated=False):
            cond = ""
            if edge.get("when"):
                cond = (f'<div class="wf-cond"><span class="muted">{"when" if automated else "offered only when"}'
                        f'</span> <code>{e(edge["when"])}</code></div>')
            elif automated:
                cond = '<div class="wf-cond muted">otherwise</div>'
            dos = []
            for sid in edge.get("steps", []):
                st = wf.step(sid)
                line = f'<span class="ki automation">A</span> {e(st.title)}'
                own = next((a for a in edge["do"] if a.get("id") == sid), {})
                if own.get("when"):
                    line += f' <span class="muted">only when</span> <code>{e(own["when"])}</code>'
                if own.get("to"):
                    line += f' <span class="muted">and sends it to</span> {loz(own["to"])}'
                dos.append(f"<li>{line}</li>")
            lands = (f'{loz(edge["to"])}' if edge.get("to") else "")
            took = edge["id"] in (marks.get("taken") or ())
            return (f'<li class="wf-edge{" taken" if took else ""}"><div class="wf-edge-head">'
                    f'<span class="wf-edge-name">{e(edge["label"])}</span>'
                    f' <span class="wf-arrow" aria-label="goes to">→</span> {lands}'
                    f'{" <span class=wf-mark>taken</span>" if took else ""}</div>{cond}'
                    + (f'<ul class="wf-do">{"".join(dos)}</ul>' if dos else "") + "</li>")

        edges = {x["id"]: x for x in shape["edges"]}
        out = [f'<div class="wf-state wf-entry"><div class="wf-state-head"><span class="muted">Enters</span></div>'
               f'<ul class="wf-edges">{edge_block(shape["start"])}</ul></div>']
        for st in shape["states"]:
            work = "".join(step_line(w) for w in st["work"])
            if st.get("automated"):
                how = "Automated: it moves on by itself, by the first way out whose condition holds"
            elif len(st["out"]) > 1:
                how = "The person who does the work chooses the way out"
            elif st["out"]:
                how = "Once the work is done it moves on"
            else:
                how = "It ends here"
            ways = "".join(edge_block(edges[x], st.get("automated")) for x in st["out"])
            mark = ('<span class="wf-mark now">it is here</span>' if st["id"] == marks.get("current") else
                    '<span class="wf-mark">been here</span>' if st["id"] in (marks.get("visited") or ()) else "")
            out.append(f'<div class="wf-state{" visited" if mark else ""}" id="state-{e(st["id"])}">'
                       f'<div class="wf-state-head">{loz(st["id"])} {mark}'
                       f' <span class="muted">{e(how)}</span></div>'
                       + (f'<p class="muted">{e(st["help"])}</p>' if st.get("help") else "")
                       + (f'<ul class="wf-work">{work}</ul>' if work else "")
                       + (f'<ul class="wf-edges">{ways}</ul>' if ways else "") + "</div>")
        return f'<div class="wf-states">{"".join(out)}</div>'

    def page_run(self, req, rid):
        """Work about a record is worked on the record's page: there it goes, to its work (303). Work about no
        record (or a record of a type this member may not read) keeps this page: where it is and its steps."""
        me, hub = req["me"], self.hub
        run = hub.engine.run(rid)
        ref = run.get("record")
        if ref and "/" in ref and self._may_read(me, ref.split("/", 1)[0]):
            raise Redirect(self.run_href(run))
        self._note_run_view(me, run)
        wf, _ = hub.workflow(run["workflow_id"], run["version"])
        viewer = Viewer(hub, me)
        rows, links, unseen = [], {}, 0
        shown_steps = viewer.run_steps(wf, run)
        for s in wf.steps:
            if s.id not in shown_steps:  # not theirs to see: not its title, not whether it ran
                unseen += 1
                continue
            st = {**run["steps"][s.id], **shown_steps[s.id]} if shown_steps[s.id]["state"] != "if-needed" \
                else shown_steps[s.id]
            out = run["ctx"].get(s.id)
            if st.get("item"):
                links[s.id] = f'/items/{st["item"]}'
            detail = ""
            if st["state"] == "done" and isinstance(out, dict) and viewer.run_withheld(run):
                detail = f'<div class="muted">{e(viewer.run_withheld(run))}</div>'
            elif st["state"] == "done" and isinstance(out, dict):
                ok, missing = viewer.step_visible(wf, s.id)
                if ok:
                    shown = {k: v for k, v in out.items() if k not in ("by", "by_kind", "event", "signature")}
                    if out.get("by_kind") == "agent" and s.kind == "gate":
                        shown = {"decided by": f"{self.member_name(out['by'])} (an agent)", **shown}
                    detail = f"<details><summary class='muted'>result</summary>{self._values(shown)}</details>"
                else:
                    detail = f'<div class="muted">result needs access to: {e(", ".join(missing))}</div>'
            if st.get("error"):
                detail = f'<div class="error">{e(viewer.run_withheld(run) or st["error"])}</div>'
                if st["state"] == "failed" and run["status"] == "running" and hub.is_policy_owner(me):
                    detail += (f'<form method="post" action="/runs/{rid}/steps/{e(s.id)}/retry" class="inline">'
                               f'<input name="reason" placeholder="what was fixed" aria-label="What was fixed" '
                               f'required><button>Retry this step</button></form>')
            title = (f'<a href="/items/{e(st["item"])}">{e(s.title)}</a>' if st.get("item") else e(s.title))
            key = f'<span class="key">{self._key(st["item"])}</span>' if st.get("item") else ""
            cond = (f"only {cond_words(wf, s.when)}" if s.when and viewer.full(wf) else "")  # the lozenge says "if needed"
            meta = " · ".join(x for x in [self._role_words(s.role) if s.role else "", cond] if x)
            rows.append(f'<div class="row{" skipped" if st["state"] == "skipped" else ""}">{self._ki(s.kind)}{key}'
                        f'<div class="t">{title} <span class="muted">{e(meta)}</span>{detail}</div>'
                        f'{self._av(st["by"]) if st.get("by") else ""}{self._loz(self.STEP_STATE, st["state"])}</div>')
        if unseen:
            missing = viewer.missing(visibility_classes(wf))
            kinds = " and ".join(self._class_name(c).lower() for c in missing) or "restricted"
            rows.append(f'<div class="row muted"><div class="t">{unseen} more step{"s" if unseen != 1 else ""} '
                        f'{"are" if unseen != 1 else "is"} kept from your role: '
                        f'{"they show" if unseen != 1 else "it shows"} {e(kinds)} information.</div></div>')
        events = list(reversed(hub.track.list(run_id=rid, limit=500)))
        actions = ""
        if run["status"] == "running" and (run["started_by"] == me["id"] or hub.is_policy_owner(me)):
            actions = (f'<details class="cancel-work"><summary class="btn subtle">Cancel this work…</summary>'
                       f'<form method="post" action="/runs/{rid}/cancel" class="inline"><label>Why it is cancelled '
                       f'<input name="reason" required></label> <button class="danger">Cancel this work</button>'
                       f'</form></details>')
        dl = [("Status", self._loz(self.RUN_STATUS, run["status"])),
              ("Workflow", f'<a href="/workflows/{e(wf.id)}">{e(wf.title)}</a> v{run["version"]}')]
        if run.get("subject") and run["subject"] != run.get("record") and run["subject"].startswith(self.subj["type"] + "/"):
            dl.append((self.subj["label"], self._ref_html(me, run["subject"]) or e(self.subj["label"])))
        elif run.get("record") and run["record"].startswith(self.subj["type"] + "/"):
            dl.append((self.subj["label"], self._ref_html(me, run["record"]) or e(self.subj["label"])))
        dl += [("Started by", self.who(run["started_by"])), ("Created", self._at(run["created_at"])),
               ("Updated", self._at(run["updated_at"]))]
        if run.get("root_event") and hub.is_policy_owner(me):  # the audit trail: for who audits
            dl.append(("Caused by", f'<a href="/track/{run["root_event"]}">event #{run["root_event"]}</a>'))
        details = ('<aside class="details"><h3>Details</h3><dl>'
                   + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in dl) + "</dl></aside>")
        main = (self._pass_strip(me, run) + self._run_head(me, run, wf, shown_steps)
                + f'<div class="section"><h2>Workflow</h2>{self._flow(wf, run, links, viewer=viewer)}</div>'
                f'<div class="section"><h2>Steps</h2><div class="subtasks">{"".join(rows)}</div></div>'
                f'<div class="section"><h2>Activity</h2>{self._activity(events, me)}</div>')
        return self.layout(req, self._run_heading(me, run, wf), f'<div class="issue"><div>{main}</div>{details}</div>',
                           crumbs=[("/runs", "All work"), (f"/workflows/{wf.id}", wf.title)], actions=actions)

    def _run_heading(self, me, run, wf) -> str:
        """A run page's title: the work and whom it is about ("Appointment request: Ellen MacLeod (born …)") when this
        member may read who that is, else the work alone; never the record's summary, which the head shows once."""
        about = run.get("subject") or run.get("record")
        title = self.hub.run_title(me, {**run, "record": about}) if about else wf.title
        n = self._pass_number(run) if run.get("record") else 1  # a record walked more than once: which pass
        return f"{title}, pass {n}" if n > 1 or (run.get("record") and self._next_pass(run)) else title

    def _run_head(self, me, run, wf, shown_steps) -> str:
        """The top of a run's page, in words: the record it walks and its state, where the work is now (even at a step
        this member may not see, said as such), who holds it and how late, what is waiting beside it, what comes
        next."""
        hub, eng = self.hub, self.hub.engine
        viewer = Viewer(hub, me)
        lines = []
        if run.get("record") and "/" in run["record"]:
            rtype, rid = run["record"].split("/", 1)
            rt = hub.record_type(rtype)
            try:
                r = hub.records.read(rtype, rid)
            except HubError:
                r = None
            field = ((rt.lifecycle or {}).get("field") if rt else None)
            state = rt.state_label(r.get(field)) if r and field and self._may_read(me, rtype) else ""
            words = e(rt.label) if rt else e(rtype)
            lines.append(("About", f'<a href="{e(self.record_href(run["record"]))}">{words}</a>'
                          + (f' <span class="loz">{e(state)}</span>' if state else "")))
        if run["status"] != "running":
            lines.append(("Status", self._loz(self.RUN_STATUS, run["status"]) + self._pass_outcome_words(me, run)))
        item = eng.item(run["item"]) if run.get("item") and hub.db.items.get(run["item"]) else None
        at = None
        if run["status"] == "running" and item and item["status"] in ("open", "claimed", "held", "waiting"):
            at = item.get("step_id")
            step = next((s for s in wf.steps if s.id == at), None)
            if step and at in shown_steps and self.may_see_item(me, item, run):
                now = (f'<span class="key">{self._key(item["id"])}</span> '
                       f'<a href="/items/{e(item["id"])}"><b>{e(step.title)}</b></a>')
            else:
                missing = viewer.step_visible(wf, at)[1] if step else []
                kinds = " and ".join(self._class_name(c).lower() for c in missing)
                now = (f'<b>a {e(kinds)} step</b> <span class="muted">(kept from your role)</span>' if kinds
                       else '<b>a step you may not see</b>')
            lines.append(("Now", now + (self._urgency(item) if item["status"] != "waiting" else "")))
            if item["status"] == "waiting":
                lines.append(("Held by", f'<span class="muted">Nobody: it waits for</span> '
                                         f'{e(item.get("waiting") or "the workflow to go on")}'))
            else:
                lines.append(("Held by", self.holder_html(item)))
        if run["status"] == "running":
            beside = [s for s in wf.steps if s.id in shown_steps and s.id != at
                      and shown_steps[s.id]["state"] in ("waiting", "active", "queued", "running")]
            for s in beside:
                lines.append(("Also", f'{e(s.title)} {self._loz(self.STEP_STATE, shown_steps[s.id]["state"])}'))
            nxt = [s for s in wf.steps if s.id in shown_steps and shown_steps[s.id]["state"] == "pending"][:2]
            if nxt:
                lines.append(("Next", e(", then ".join(s.title for s in nxt))))
        if not lines:
            return ""
        rows = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in lines)
        extra = (self._blocked_html(item) if item else "") + (
            self._wait_records_html(me, run, next((s for s in wf.steps if s.id == at), None))
            if run["status"] == "running" else "")
        return f'<div class="card run-head"><dl class="run-now">{rows}</dl>{extra}</div>'

    # ------------------------------------------------------------------------------------------ workflows
    def page_workflows(self, req):
        rows = []
        for w in self.hub.workflows():
            wf = w["workflow"]
            agents = sum(1 for s in wf.steps if s.agents_may and s.kind != "automation")
            prop = f' <span class="badge proposed">{w["proposed"]} proposed</span>' if w["proposed"] else ""
            rows.append(f'<tr><td><a href="/workflows/{e(wf.id)}"><b>{e(wf.title)}</b></a>{prop}'
                        f'<div class="muted">{e(wf.summary[:160])}</div></td><td>v{w["row"]["version"]}</td>'
                        f'<td>{len(wf.steps)}</td><td>{agents}</td><td>{e(", ".join(wf.starters))}'
                        f'{"<div class=muted>on " + e(wf.event) + "</div>" if wf.event else ""}</td>'
                        f'<td><a href="/workflows/{e(wf.id)}/edit">Edit</a></td></tr>')
        skills = "".join(f'<details class="card"><summary><b>{e(s.title)}</b> <code>{e(s.id)}</code></summary>'
                         f'<pre class="wrap">{e(s.instructions)}</pre>{self._values({f["name"]: f["type"] for f in s.output})}'
                         f'</details>' for s in (self.hub.pack.skills.values() if self.hub.pack else []))
        body = (f'<p class="muted">How this practice works, written down. Anyone may propose a change; a policy owner who '
                f'did not write it approves it, and every approval is signed.</p><table class="list"><tr><th>Workflow</th>'
                f'<th>Version</th><th>Steps</th><th>Agent steps</th><th>Started by</th><th></th></tr>{"".join(rows)}</table>'
                f'<h2>Skills agents can be given</h2>{skills}')
        return self.layout(req, "Workflows", body, crumbs=[("/", "Practice"), ("", "Workflows")],
                           actions='<a class="btn" href="/workflows/new">Create workflow</a>' + self.bpmn_import_form())

    def _propose_form(self, source, note="", problems=None) -> str:
        probs = ("<div class='error'><ul>" + "".join(f"<li>{e(p)}</li>" for p in problems) + "</ul></div>") if problems else ""
        actions = ", ".join(sorted(self._actions()))
        return (f'{probs}<form method="post" action="/workflows/propose" class="card"><label>Workflow (TOML)'
                f'<textarea name="source" rows="28" class="code" spellcheck="false">{e(source)}</textarea></label>'
                f'<label>What changes, and why<input name="note" value="{e(note)}" required></label>'
                f'<small>Actions available to automations: {e(actions)}. Roles: '
                f'{e(", ".join(r["id"] for r in self.hub.pack.roles))}.</small><button>Propose</button></form>')

    def _actions(self):
        from ..engine import CORE_ACTIONS
        return set(CORE_ACTIONS) | set(self.hub.pack.actions if self.hub.pack else {})

    def _catalog(self) -> dict:
        from ..engine import CORE_PROVIDERS
        pack = self.hub.pack
        return {
            "roles": [{"id": r["id"], "name": r.get("name", r["id"])} for r in (pack.roles if pack else [])],
            "skills": {s.id: {"id": s.id, "title": s.title, "output": [f["name"] for f in s.output]}
                       for s in (pack.skills.values() if pack else [])},
            "actions": sorted(self._actions()),
            "providers": sorted(set(CORE_PROVIDERS) | set(pack.providers if pack else {})),
            "data_classes": [{"id": d["id"], "name": d.get("name", d["id"])} for d in (pack.data_classes if pack else [])],
            "subject": self.subj["type"], "subject_label": self.subj["label"],
            "blocks": {b["block"].id: {"id": b["block"].id, "title": b["block"].title, "runtime": b["block"].runtime,
                                       "kind": b["block"].step_kind, "capability": b["block"].capability,
                                       "active": b["row"]["version"] is not None,
                                       "inputs": [{k: f.get(k) for k in ("name", "type", "required", "default", "help",
                                                                          "choices")} for f in b["block"].inputs],
                                       "outputs": [f["name"] for f in b["block"].outputs]}
                       for b in self.hub.blocks()},
        }

    def page_workflow_new(self, req):
        return self.page_workflow_edit(req, None)

    def page_workflow_edit(self, req, wid, *, source=None, note="", problems=None):
        if source is None:
            if wid:
                versions = self.hub.versions(wid)
                if not versions:
                    raise NotFound(f"there is no workflow {wid!r}")
                try:
                    source = self.hub.workflow(wid)[1]["source"]
                except NotFound:
                    source = versions[0]["source"]
            else:
                source = NEW_WORKFLOW.replace('record = "SUBJECT"', f'record = "{self.subj["type"]}"')
        if req["query"].get("plain"):
            return self.layout(req, "Edit workflow", self._propose_form(source, note, problems))
        try:
            data = tomllib.loads(source)
        except tomllib.TOMLDecodeError:
            data = None
        old = is_old_form(data)  # a list of steps: the diagram edits states and edges, so it is never converted
        visual = data is not None and not old
        boot = json.dumps({"data": data if visual else None, "catalog": self._catalog(), "problems": problems or [],
                           "words": condition_words(data) if visual else {}, "old": old,
                           "locked_id": bool(wid)}, default=str).replace("</", "<\\/")
        title = "Create workflow"
        crumbs = [("/workflows", "Workflows")]
        name = data.get("title", wid) if isinstance(data, dict) else wid
        if wid:
            title = f"Edit: {name}"
            crumbs.append((f"/workflows/{wid}", name))
        old_note = ('<div class="section-msg warning" id="wf-old" role="note"><p><strong>This workflow is written in the old form, '
                    'as a list of steps.</strong> The diagram edits a workflow written as states and the ways out of '
                    'them, so it does not open this one, and it does not convert it for you. Edit it as text: '
                    '<a href="?plain=1">open the text editor</a>, or use the Text tab above.</p></div>') if old else ""
        body = (
            '<p class="muted">Lay out the states a record moves through, the work done in each, and the ways out. '
            'Your change is a proposal: a policy owner who did not write it approves it before any new run uses it.</p>'
            f'{old_note}<div id="wf-editor"><div class="ed-tabs" role="tablist">'
            f'<button type="button" data-tab="visual" class="{"on" if visual else ""}">Diagram</button>'
            f'<button type="button" data-tab="toml" class="{"" if visual else "on"}">Text</button></div>'
            f'<div id="wf-visual"{"" if visual else " hidden"}><div class="ed"><div>'
            '<div class="ed-tools"><button type="button" data-add="state">+ State</button>'
            '<span class="muted" style="font-size:12px">Select a state, its work, an automation or a way out to change '
            'it. Drag from a state\'s handle onto another state to add a way out.</span></div>'
            '<div class="flowwrap wf-canvas" id="wf-canvas"></div>'
            '<div class="legend"><span><span class="ki task">T</span> task</span><span><span class="ki gate">D</span> '
            'decision (signed)</span><span><span class="ki automation">A</span> automation: a stop of its own on the '
            'way</span><span><span class="badge agent">AI</span> an agent may do it</span><span>- - - only when its '
            'condition holds</span></div></div>'
            '<div class="ed-panel" id="wf-panel"></div></div></div>'
            f'<div id="wf-text"{" hidden" if visual else ""}><textarea id="wf-toml" class="code" rows="30" '
            f'spellcheck="false" aria-label="Workflow text">{"" if visual else e(source)}</textarea></div>'
            '<form method="post" action="/workflows/propose" id="wf-propose" class="ed-bar">'
            '<input type="hidden" name="source"><input type="hidden" name="model"><div class="status" id="wf-status"></div>'
            f'<input name="note" value="{e(note)}" placeholder="What changes, and why" required aria-label="What changes, and why">'
            '<button>Propose change</button></form></div>'
            f'<noscript><p>The diagram editor needs JavaScript. <a href="?plain=1">Edit the text instead.</a></p></noscript>'
            f'<script type="application/json" id="wf-boot">{boot}</script>')
        return self.layout(req, title, body, crumbs=crumbs)

    def post_propose(self, req):
        f = self.form(req)
        source = source_of(f)  # the text editor's text, or the visual editor's model written out
        try:
            out = self.hub.propose_workflow(req["me"], source, f.get("note", ""))
        except Invalid as exc:
            try:
                wid = tomllib.loads(source).get("id")
            except tomllib.TOMLDecodeError:
                wid = None
            wid = wid if isinstance(wid, str) and self.hub.versions(wid) else None
            return self.page_workflow_edit(req, wid, source=source, note=f.get("note", ""),
                                           problems=exc.problems)
        raise Redirect(f"/workflows/{out['workflow_id']}#v{out['version']}")

    def api_check_workflow(self, req):
        """Checks a workflow as it is edited: the text editor sends its ``source``, the visual editor its ``model``
        (the document, written out here as the text it would propose). The answer carries the text, the document
        read back from it, whether that is the old form (a list of steps), and its conditions in words."""
        body = self.body_json(req)
        if isinstance(body.get("model"), dict):
            source = editor_toml(body["model"])
        else:
            source = str(body.get("source", ""))
        try:
            data = tomllib.loads(source)
        except tomllib.TOMLDecodeError as exc:
            return {"ok": False, "problems": [f"The workflow is not valid TOML: {exc}"], "data": None,
                    "source": source}
        try:
            self.hub.check_workflow(source)
            problems = []
        except wfmod.DefinitionError as exc:
            problems = exc.problems
        return {"ok": not problems, "problems": problems, "data": data, "source": source,
                "old": is_old_form(data), "words": condition_words(data)}

    def _workflow_graph(self, source: str, fallback: str) -> str:
        """The workflow drawn as the editor draws it (states, the automations on the way, the ways out), read only;
        without the script, its states as a list."""
        data = tomllib.loads(source)
        boot = json.dumps({"data": data, "catalog": self._catalog(), "problems": [], "words": condition_words(data),
                           "old": False, "readonly": True}, default=str).replace("</", "<\\/")
        return ('<div id="wf-editor" class="wf-view"><div class="flowwrap wf-canvas" id="wf-canvas"></div>'
                '<div class="legend"><span><span class="ki task">T</span> task</span><span><span class="ki gate">D</span> '
                'decision (signed)</span><span><span class="ki automation">A</span> automation: a stop of its own on the '
                'way</span><span><span class="badge agent">AI</span> an agent may do it</span><span>- - - only when its '
                'condition holds</span></div></div>'
                f'<noscript>{fallback}</noscript><script type="application/json" id="wf-boot">{boot}</script>')

    def page_workflow(self, req, wid):
        me, hub = req["me"], self.hub
        versions = hub.versions(wid)
        if not versions:
            raise NotFound(f"there is no workflow {wid!r}")
        try:
            wf, active = hub.workflow(wid)
        except NotFound:
            wf, active = None, None
        main, dl = [], []
        if wf:
            if wf.shape:
                main.append(f'<p>{e(wf.summary)}</p><div class="section"><h2>Where it stops, and how it moves on</h2>'
                            f'{self._workflow_graph(active["source"], self._states_view(wf) + self._flow(wf))}</div>')
            else:
                main.append(f'<p>{e(wf.summary)}</p><div class="section"><h2>How it flows</h2>{self._flow(wf)}</div>')
            agents = [s.title for s in wf.steps if s.agents_may and s.kind != "automation"]
            dl = [("Active", f"v{active['version']}"), ("About", e(wf.record or "nothing in particular")),
                  ("Started by", e(", ".join(wf.starters) or "—")), ("On event", e(wf.event or "—")),
                  ("Steps", str(len(wf.steps))), ("Agents may do", e(", ".join(agents) or "nothing"))]
            if wf.schedule:
                dl.insert(4, ("On schedule", e(schedule_text(wf.schedule))))
            if getattr(wf, "deadline", None):
                names = {r["id"]: r["name"] for r in hub.roles()}
                who = wf.deadline_escalate_to or hub.practice.get("escalation_role")
                dl.append(("Deadline", e(f"done within {duration_text(wf.deadline)}"
                                         + (f"; if not, {names.get(who, who)} is told" if who else ""))))
        rows = []
        for v in versions:
            extra = ""
            if v["status"] == "proposed":
                impact = hub.agent_impact(wid, v["version"])
                diff = "\n".join(difflib.unified_diff((active["source"] if active else "").splitlines(),
                                                      v["source"].splitlines(), "active", f"v{v['version']}", lineterm=""))
                impact_html = ("<ul>" + "".join(f"<li>{e(n)}</li>" for n in impact) + "</ul>") if impact else \
                    "<p class='muted'>No change to what agents may do.</p>"
                approve = ""
                if me["kind"] == "person" and hub.is_policy_owner(me) and v["author"] != me["id"]:
                    approve = (f'<form method="post" action="/workflows/{e(wid)}/{v["version"]}/approve" class="inline">'
                               f'<button>Approve and sign</button></form>')
                withdraw = ""
                if v["author"] == me["id"] or hub.is_policy_owner(me):
                    withdraw = (f'<form method="post" action="/workflows/{e(wid)}/{v["version"]}/withdraw" class="inline">'
                                f'<label>Why <input name="reason"></label> <button class="secondary">'
                                f'{"Withdraw" if v["author"] == me["id"] else "Decline"}</button></form>')
                try:
                    pwf, _ = hub.workflow(wid, v["version"])
                    pflow = f'<h3>The proposed flow</h3>{self._flow(pwf, legend=False).replace(f"flow-{wid}-def", f"flow-{wid}-v{v["version"]}")}'
                except (NotFound, Invalid):  # a proposal today's rules no longer pass: its text below, no drawing
                    pflow = ""
                extra = (f'<div class="card"><h3>What changes for agents</h3>{impact_html}{pflow}<details><summary>'
                         f'Changes to the text</summary><pre class="diff">{self._diff(diff)}</pre></details>'
                         f'<div class="filters" style="margin-top:10px">{approve}{withdraw}</div></div>')
            rows.append(f'<tr id="v{v["version"]}"><td>v{v["version"]}</td><td><span class="badge {e(v["status"])}">'
                        f'{e(v["status"])}</span></td><td>{self.who(v["author"])}</td><td>{e(v["note"])}</td>'
                        f'<td>{self.who(v["approved_by"]) if v["approved_by"] else ""}</td></tr>'
                        + (f'<tr><td colspan="5">{extra}</td></tr>' if extra else ""))
        main.append(f'<div class="section"><h2>Versions</h2><table><tr><th></th><th>Status</th><th>Author</th><th>Note</th>'
                    f'<th>Approved by</th></tr>{"".join(rows)}</table></div>')
        details = ('<aside class="details"><h3>Details</h3><dl>'
                   + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in dl) + "</dl></aside>") if dl else "<div></div>"
        return self.layout(req, wf.title if wf else wid, f'<div class="issue"><div>{"".join(main)}</div>{details}</div>',
                           crumbs=[("/workflows", "Workflows")],
                           actions=f'<a class="btn" href="/workflows/{e(wid)}/edit">Edit workflow</a>'
                                   + (self.bpmn_download_link(wid) if wf else ""))

    def error_page(self, req, exc, problems) -> str:
        items = "".join(f"<li>{e(str(p))}</li>" for p in problems if str(p) != str(exc))
        back = self._back_link(req)
        body = (f'<div class="error" role="alert"><p>{e(str(exc))}</p>{"<ul>" + items + "</ul>" if items else ""}</div>'
                f'<p><a href="{e(back)}">{"Back to the page you were on" if back != "/" else "Back to your work"}</a></p>')
        return self.layout(req, "That did not work", body)

    @staticmethod
    def _back_link(req) -> str:
        """Where the error page links back to: the page a form was posted from, on this site only; else home."""
        if req.get("method") == "GET":
            return "/"
        headers = req.get("headers") or {}
        ref = urllib.parse.urlsplit(headers.get("referer", ""))
        same_site = (not ref.scheme and not ref.netloc) or (ref.scheme in ("http", "https")
                                                             and ref.netloc == headers.get("host", ""))
        if same_site and ref.path.startswith("/") and not ref.path.startswith("//") and "\\" not in ref.path:
            return ref.path + (f"?{ref.query}" if ref.query else "")
        return "/"

    # ------------------------------------------------------------------------------------------ sign in
    SESSION_COOKIE = "ph_session={}; Path=/; HttpOnly; SameSite=Strict"

    def post_login(self, req):
        """A person signs in with their member id and passphrase. A failure shows the form again, saying so; the
        attempt is counted against the member and on the track."""
        f = self.form(req)
        mid = str(f.get("member", "")).strip()[:64]
        try:
            m = self.hub.sign_in(mid, str(f.get("passphrase", "")), client=req["headers"].get(CLIENT_HEADER))
        except (Denied, Invalid) as exc:
            raise Rendered(403, self.layout(req, "Sign in", self._login_form(mid, str(exc)))) from None
        raise Redirect("/", self.SESSION_COOKIE.format(self.hub.start_session(m)))

    def page_logout(self, req):
        jar = cookies.SimpleCookie(req["headers"].get("cookie", ""))
        if "ph_session" in jar:
            self.hub.end_session(jar["ph_session"].value)
        # the demo's sign-in-as cookie too: else the next visit is signed in again as the same member
        raise Redirect("/login", ["ph_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict",
                                  "ph_as=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"])

    # ------------------------------------------------------------------------------------------ passphrases
    def _passphrase_fields(self, current: bool) -> str:
        cur = ('<label>Your current passphrase<input name="current" type="password" autocomplete="current-password" '
               'required></label>') if current else ""
        return (f'{cur}<label>New passphrase<input name="new" type="password" autocomplete="new-password" minlength="12" '
                f'required><small>At least 12 characters: a few words you will remember. It is yours alone: '
                f'every signature asks for it.</small></label>'
                f'<label>The new passphrase again<input name="again" type="password" autocomplete="new-password" '
                f'required></label>')

    def _setup_form(self, member: str = "", error: str = "") -> str:
        err = f'<div class="error" role="alert">{e(error)}</div>' if error else ""
        return (f'{err}<form method="post" action="/setup" class="card"><p>Set your passphrase with the one-time setup '
                f'code you were given. {self._admins_who_help()} gives you one.</p>'
                f'<label>Your member id<input name="member" value="{e(member)}" autocomplete="username" required '
                f'autocapitalize="none" spellcheck="false"></label>'
                f'<label>Setup code<input name="code" autocomplete="one-time-code" required autocapitalize="characters" '
                f'spellcheck="false" placeholder="XXXXX-XXXXX-XXXXX-XXXXX"></label>'
                f'{self._passphrase_fields(False)}<button>Set my passphrase</button></form>'
                f'<p><a href="/login">Sign in</a></p>')

    def page_setup(self, req):
        return self.layout(req, "Set your passphrase", self._setup_form(str(req["query"].get("member", ""))[:64]))

    def post_setup(self, req):
        f = self.form(req)
        mid = str(f.get("member", "")).strip()[:64]
        try:
            if f.get("new", "") != f.get("again", ""):
                raise Invalid("the two passphrases are not the same")
            m = self.hub.redeem_setup_code(mid, str(f.get("code", "")), str(f.get("new", "")),
                                           client=req["headers"].get(CLIENT_HEADER))
        except (Denied, Invalid) as exc:
            code = 403 if isinstance(exc, Denied) else 422
            raise Rendered(code, self.layout(req, "Set your passphrase", self._setup_form(mid, str(exc)))) from None
        raise Redirect("/", self.SESSION_COOKIE.format(self.hub.start_session(m)))

    SETTING_WORDS = {
        "theme": ("Appearance", {"system": "Match my device", "light": "Light", "dark": "Dark"}),
        "editor": ("Formatted text opens as", {"visual": "Formatted (what you see is what you get)",
                                               "markdown": "Markdown (the marks as typed)"}),
    }

    def page_settings(self, req, error: str = "", done: bool = False):
        me = req["me"]
        prefs = self.hub.preferences(me["id"])
        err = f'<div class="error" role="alert">{e(error)}</div>' if error else ""
        ok = '<div class="ok" role="status">Your settings are saved.</div>' if done else ""
        groups = ""
        for key, (legend, words) in self.SETTING_WORDS.items():
            opts = "".join(f'<label class="radio"><input type="radio" class="opt-input" name="{key}" value="{e(v)}"'
                           f'{" checked" if prefs[key] == v else ""}> {e(words.get(v, v))}</label>'
                           for v in self.hub.PREFERENCES[key])
            groups += f'<fieldset class="choices options"><legend>{e(legend)}</legend>{opts}</fieldset>'
        sign_in = (f'<h2>Signing in and signing</h2><p>Your passphrase is on <a href="/team/{e(me["id"])}#passphrase">'
                   f'your profile</a>: you sign in with it, and every electronic signature asks for it.</p>'
                   if me["kind"] == "person" else "")
        body = (f'{ok}{err}<p>These change how the hub looks and works for you only. '
                f'<a href="/team/{e(me["id"])}">Your profile</a> shows your roles and what you may do.</p>'
                f'<form method="post" action="/me/settings" class="card settings">{groups}'
                f'<button>Save settings</button></form>{sign_in}')
        return self.layout(req, "My settings", body, crumbs=[("/team/" + me["id"], me["name"]), (None, "My settings")])

    def post_settings(self, req):
        f = self.form(req)
        try:
            self.hub.set_preferences(req["me"], {k: str(f[k]) for k in self.hub.PREFERENCES if k in f})
        except Invalid as exc:
            raise Rendered(422, self.page_settings(req, error=str(exc))) from None
        return self.page_settings(req, done=True)

    def _own_passphrase(self, me, error: str = "", done: bool = False) -> str:
        has = self.hub.has_passphrase(me["id"])
        err = f'<div class="error" role="alert">{e(error)}</div>' if error else ""
        ok = '<div class="ok" role="status">Your passphrase is set. Use it to sign in and to sign.</div>' if done else ""
        intro = ("<p>You sign in with it, and every electronic signature asks for it again. Changing it signs you out "
                 "everywhere else.</p>" if has else
                 "<p>You have no passphrase yet. Set one: every electronic signature asks for it.</p>")
        return (f'<section id="passphrase" class="own-passphrase"><h2>Your passphrase</h2>{ok}{err}{intro}'
                f'<details{" open" if error or done or not has else ""}><summary>{"Change" if has else "Set"} your '
                f'passphrase</summary><form method="post" action="/me/passphrase" class="card">'
                f'{self._passphrase_fields(has)}<button>{"Change" if has else "Set"} my passphrase</button></form>'
                f'</details></section>')

    def page_passphrase(self, req, error: str = "", done: bool = False):
        """Your passphrase lives on your own profile."""
        me = req["me"]
        if me["kind"] != "person":
            raise Denied("agents and services use access tokens: they have no passphrase")
        if not error and not done and req["method"] == "GET":
            raise Redirect(f"/team/{me['id']}#passphrase")
        return self.page_member(req, me["id"], passphrase=(error, done))

    def post_passphrase(self, req):
        me, f = req["me"], self.form(req)
        try:
            if f.get("new", "") != f.get("again", ""):
                raise Invalid("the two passphrases are not the same")
            self.hub.set_passphrase(me, me["id"], str(f.get("new", "")), current=str(f.get("current", "")) or None)
        except (Denied, Invalid) as exc:
            code = 403 if isinstance(exc, Denied) else 422
            raise Rendered(code, self.page_passphrase(req, error=str(exc))) from None
        page = self.page_passphrase(req, done=True)
        jar = cookies.SimpleCookie(req["headers"].get("cookie", ""))
        if "ph_session" in jar:  # setting it ended every session: this one goes on as a new one
            raise Rendered(200, page, self.SESSION_COOKIE.format(self.hub.start_session(me)))
        return page

    # ------------------------------------------------------------------------------------------ inbox
    # ------------------------------------------------------------------------------------------ items
    def _hold_form(self, me, hold, run, kept: dict | None = None) -> str:
        """An agent's held high-risk answer, for its sponsor to approve or reject."""
        eng = self.hub.engine
        kept = kept or {}
        chk = lambda o: " checked" if kept.get("outcome") == o else ""  # noqa: E731
        held = eng.item(hold["output"]["holds"])
        ok, why = eng.eligibility(me, hold)
        wf, _ = self.hub.workflow(run["workflow_id"], run["version"])
        vis, missing = Viewer(self.hub, me).step_visible(wf, held["step_id"])
        answer = self._values(held["output"] or {}) if vis else (
            f'<p class="muted">Content needs access to: {e(", ".join(self._class_name(c) for c in missing))}</p>')
        parts = [f'<p>{self.who(held["claimed_by"])} answered <b>{e(wf.step(held["step_id"]).title)}</b>, a high-risk '
                 f'step. Nothing has happened yet: the answer waits for you.</p><h2>The answer</h2>{answer}']
        if not ok:
            return "".join(parts) + f'<div class="error">You cannot decide this: {e(why)}</div>'
        parts.append(f'<form method="post" action="/items/{e(hold["id"])}/decide" class="card">'
                     f'<fieldset class="choices options decide"><legend>Your decision'
                     f'<span class="req" aria-hidden="true"> *</span></legend>'
                     f'<label class="radio"><input type="radio" class="opt-input" name="outcome" value="approve" required'
                     f'{chk("approve")}> <b>Approve</b> <small>apply it</small></label>'
                     f'<label class="radio"><input type="radio" class="opt-input" name="outcome" value="reject" required'
                     f'{chk("reject")}> <b>Reject</b> <small>give the work to people</small></label></fieldset>'
                     f'<label>Comment<textarea name="comment" rows="2">{e(str(kept.get("comment", "")))}</textarea></label>'
                     f'<p class="sign">Approving is your electronic signature: <b>“I approve this agent\'s work”</b>, '
                     f'over the answer shown above.</p>{self._sign_prompt(me)}<input type="hidden" name="seen" '
                     f'value="{e(eng.shown_digest(me, hold["id"]))}"><button>Decide</button></form>')
        return "".join(parts)

    def _context_label(self, key: str, wf=None) -> str:
        """What a context entry is, in words: an input's label, a step's title, a data class's name."""
        if key.startswith("record:"):
            dc = key.split(":", 1)[1]
            names = {d["id"]: d.get("name") for d in (self.hub.pack.all_data_classes() if self.hub.pack else [])}
            return names.get(dc) or dc.capitalize() + " records"
        if key.startswith("provider:"):
            return key.split(":", 1)[1].replace("-", " ").capitalize()
        head, _, rest = key.partition(".")
        inputs = {i["name"]: i.get("label") or i["name"] for i in (wf.inputs if wf else [])}
        if head == "input":
            return inputs.get(rest, rest.replace("_", " ").capitalize()) if rest else "What was asked"
        titles = {s.id: s.title for s in (wf.steps if wf else [])}
        if head in titles:
            return titles[head] + (f": {rest.replace('_', ' ')}" if rest else "")
        return key

    def _context(self, data: dict, wf=None, me: dict | None = None) -> str:
        out = []
        for key, val in data.items():
            label = self._context_label(key, wf)
            if key.startswith("record:"):
                pack = self.hub.pack
                subj = pack.subject_type if pack else None
                lines = []
                for r in val:
                    head = e(self.hub.title(r))
                    facts = ""
                    if pack and r.get("resourceType") == subj and hasattr(pack, "facts"):
                        # who they are and how to reach them (and whom to call), in the open: an urgent task needs it
                        rows = "".join(f"<tr><th>{e(self.human(k))}</th><td>{self._fact_html(v)}</td></tr>"
                                       for k, v in self.hub.facts(r).items() if v not in (None, ""))
                        facts = f"<table class='kv'>{rows}</table>"
                    lines.append(f'<li>{facts and "<b>" + head + "</b>" + facts}'
                                 f'<details><summary>{e(self._type_label(r.get("resourceType", "")))}'
                                 f'{"" if facts else ": " + head}</summary>{self._plain_html(r)}</details></li>')
                out.append(f"<h3>{e(label)}</h3><ul class='plain'>{''.join(lines) or chr(10)}</ul>"
                           if lines else f"<h3>{e(label)}</h3><ul class='plain'><li class='muted'>none</li></ul>")
            elif key.startswith("provider:") and isinstance(val, list):
                out.append(f"<h3>{e(label)}</h3><p class='muted'>{len(val)} options, offered in the form below.</p>")
            else:
                out.append(f"<h3>{e(label)}</h3>" + self._values(val, me))
        return "".join(out) or "<p class='muted'>Nothing.</p>"

    def _fact_html(self, v) -> str:
        if isinstance(v, str) and v.startswith("member:"):
            return self.who(v.removeprefix("member:"))
        return e(str(v))

    @staticmethod
    def human(key) -> str:
        """A key or slug as words: found_in_chart, review-flagged, valueQuantity -> Found in chart, ..."""
        text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(key)).replace("_", " ").replace("-", " ").strip()
        return (text[:1].upper() + text[1:].lower()) if text and not text.isupper() else text

    def _choice_label(self, f: dict | None, v) -> str:
        """A choice in words: the field's label for it, else the code as words (new-concern -> New concern)."""
        if v is None:
            return ""
        words = schema.choice_words(f, v)
        return self.human(words) if words == str(v) and re.fullmatch(r"[a-z][a-z0-9_-]*", words) else words

    def _values(self, v, me: dict | None = None) -> str:
        if isinstance(v, dict):
            # who did it, named (not their id or their kind); a reference, linked by its title
            rows = "".join(f"<tr><th>{e(self.human(k))}</th><td>"
                           f"{self.who(x) if k == 'by' and isinstance(x, str) and self.hub.db.members.get(x) else self._values(x, me)}"
                           f"</td></tr>" for k, x in v.items() if k not in ("event", "by_kind"))
            return f"<table class='kv'>{rows}</table>"
        if isinstance(v, list):
            return ("<ul>" + "".join(f"<li>{self._values(x, me)}</li>" for x in v) + "</ul>"
                    if v else "<span class='muted'>none</span>")
        if v is None or v == "":
            return "<span class='muted'>—</span>"
        if isinstance(v, bool):
            return "Yes" if v else "No"
        if isinstance(v, str) and "\n" in v:
            return f"<pre class='wrap'>{e(v)}</pre>"
        if me is not None and isinstance(v, str):
            link = self._ref_html(me, v)
            if link:
                return link
        return e(str(v))

    REF_RE = re.compile(r"([A-Z][A-Za-z0-9]*)/([A-Za-z0-9][A-Za-z0-9_.-]*)")

    def _ref_html(self, me: dict, v: str) -> str | None:
        """A value that is a reference to a known record, as a link named the way the record is named: its title
        when this member may read its type, else its type's label. ``None`` when it is not a reference."""
        m = self.REF_RE.fullmatch(v)
        if not m:
            return None
        rtype, rid = m.groups()
        if rtype in self.hub.member_types():
            return self.who(rid) if self.hub.db.members.get(rid) else None
        rt = self.hub.record_type(rtype)
        if rt is None:
            return None
        text = rt.label
        if self._may_read(me, rtype):
            try:
                rec = self.hub.records.read(rtype, rid)
            except HubError:
                rec = None
            if rec:
                text = self.hub.title(rec) or text
        return f'<a href="{e(self.record_href(v))}">{e(text)}</a>'

    def _output_html(self, out: dict, me: dict) -> str:
        """A step's or a notice's output: its ``summary`` as a sentence first (the rest under Details), references
        as links named in words."""
        if isinstance(out, dict) and isinstance(out.get("summary"), str) and out["summary"].strip():
            rest = {k: v for k, v in out.items() if k != "summary"}
            more = (f'<details><summary>Details</summary>{self._values(rest, me)}</details>' if rest else "")
            return f'<p class="summary">{e(out["summary"])}</p>{more}'
        return self._values(out, me)

    def _field(self, f, choices, value) -> str:
        """A form control in its label; the help below it, tied to the control (``aria-describedby``)."""
        name, typ = f["name"], f["type"]
        val = value if value is not None else f.get("default")
        req = " required" if f.get("required") and typ != "boolean" else ""
        helps = [f["help"]] if f.get("help") else []
        slots = bool(choices) and typ == "choice" and all("day" in c and "with" in c for c in choices)
        cid, ids = self._control_id(name, bool(helps) or slots)
        if slots:
            return self._slot_picker(f, choices, val, cid, ids, req, helps)
        if choices is not None and typ != "member" and len(choices) <= self.CARDS:
            return self._option_cards(f, [(str(c["value"]), self.human(c["label"]) if str(c["label"]) == str(c["value"])
                                           else str(c["label"])) for c in choices],
                                      [] if val is None else [str(x) for x in val] if isinstance(val, list)
                                      else [str(val)], multi=typ == "list")
        if choices is not None or typ == "member":
            if typ == "member":
                choices = [{"value": m["id"], "label": m["name"]} for m in self.hub.members() if m["status"] == "active"]
            opts = "".join(f'<option value="{e(str(c["value"]))}"{" selected" if str(c["value"]) == str(val) else ""}>'
                           f'{e(self.human(c["label"]) if str(c["label"]) == str(c["value"]) else str(c["label"]))}'
                           f'</option>' for c in choices)
            ctl = f'<select name="{e(name)}"{ids}{req}><option value=""></option>{opts}</select>'
        elif typ == "text":
            ctl = f'<textarea name="{e(name)}"{ids} rows="5"{req}>{e(str(val or ""))}</textarea>'
        elif typ == "markdown":
            # a plain text box of Markdown that the script turns into a formatted (what you see) editor; it stays a
            # text box without the script. The start is the server's own safe rendering, never the browser's.
            hid = "md-" + re.sub(r"[^A-Za-z0-9_-]", "-", name)
            titles = "".join(f'<option value="{e(t)}">' for t in f.get("_titles", []))
            ctl = (f'<textarea name="{e(name)}" id="{hid}" class="markdown" rows="16" '
                   f'aria-describedby="{hid}-help"{req}>{e(str(val or ""))}</textarea>'
                   f'<template class="md-start" data-for="{hid}">{f.get("_start", "")}</template>'
                   + (f'<datalist id="{hid}-titles">{titles}</datalist>' if titles else ""))
            words = "".join(f"{e(h)} " for h in helps)
            return (f'<div class="field"><label>{self._label_words(f)}{ctl}</label>'
                    f'<small id="{hid}-help" class="help md-help">{words}Formatting: # heading, **bold**, *italic*, '
                    f'- list, | table |, [words](https://…). Link a page with [[Its title]], any record with '
                    f'[[Type/id]], a piece of work with [[work:id]].</small></div>')
        elif typ == "list":
            txt = "\n".join(map(str, val)) if isinstance(val, list) else str(val or "")
            ctl = f'<textarea name="{e(name)}"{ids} rows="3" placeholder="one per line"{req}>{e(txt)}</textarea>'
        elif typ == "boolean":  # a switch: the native checkbox, drawn as a toggle
            ctl = (f'<input type="hidden" name="{e(name)}" value="false"><input type="checkbox" class="switch" '
                   f'name="{e(name)}"{ids} value="true"{" checked" if val is True else ""}>')
            words = self._label_words(f)
            out = f'<label class="switch-row">{ctl}<span>{words}</span></label>'
            return (f'<div class="field">{out}<small id="{cid}-help" class="help">{e(helps[0])}</small></div>'
                    if helps else out)
        else:
            kind = {"number": "number", "date": "date", "datetime": "datetime-local"}.get(typ, "text")
            step = ' step="any"' if typ == "number" else ""
            ctl = (f'<input type="{kind}" name="{e(name)}"{ids} value="{e(str(val if val is not None else ""))}"'
                   f'{req}{step}>')
        return self._labelled(f, ctl, cid, helps)

    def _slot_picker(self, f, choices, val, cid, ids, req, helps) -> str:
        """Appointment slots: pick a day and whom it is with, then the time. The filters have no name, so only the
        slot is sent; without the script it is one list of every slot, grouped by day, and still works."""
        days = list(dict.fromkeys((c["day"], c["day_label"]) for c in choices))
        whom = list(dict.fromkeys((c["with"], c["with_name"]) for c in choices))
        chosen = next((c for c in choices if str(c["value"]) == str(val)), None)
        day_opts = "".join(f'<option value="{e(d)}"{" selected" if chosen and chosen["day"] == d else ""}>{e(lab)} '
                           f'({sum(1 for c in choices if c["day"] == d)} free)</option>' for d, lab in days)
        who_opts = "".join(f'<option value="{e(w)}"{" selected" if chosen and chosen["with"] == w else ""}>{e(n)}</option>'
                           for w, n in whom)
        groups = ""
        for d, lab in days:
            opts = "".join(f'<option value="{e(str(c["value"]))}" data-day="{e(d)}" data-with="{e(c["with"])}"'
                           f'{" selected" if chosen is c else ""}>{e(c["time"])} with {e(c["with_name"])}</option>'
                           for c in choices if c["day"] == d)
            groups += f'<optgroup label="{e(lab)}">{opts}</optgroup>'
        pick = (f'<div class="slot-filters"><label>Day<select data-slot-day><option value="">Any day</option>{day_opts}'
                f'</select></label><label>With<select data-slot-with><option value="">Anyone</option>{who_opts}'
                f'</select></label></div>')
        ctl = f'<select name="{e(f["name"])}"{ids}{req} data-slot-time><option value=""></option>{groups}</select>'
        count = (f'{len(choices)} free slots over {len(days)} days with {len(whom)} '
                 f'{"person" if len(whom) == 1 else "people"}')
        return (f'<fieldset class="slot-picker" data-slots><legend>{self._label_words(f)}</legend>{pick}'
                f'<label>Time{ctl}</label>'
                f'<small id="{cid}-help" class="help">{" · ".join(e(h) for h in [*helps, count])}</small></fieldset>')

    @staticmethod
    def _form_output(form: dict) -> dict:
        out = {}
        for k, v in form.items():
            out[k] = v[-1] if isinstance(v, list) else v  # a checked box sends "false" then "true"
        return out

    def _sign_prompt(self, me: dict | None = None) -> str:
        """Every electronic signature asks again for the signer's passphrase (in dev mode, sign-in has none)."""
        if self.dev and not (me and self.hub.has_passphrase(me["id"])):
            return ('<p class="muted">Dev mode: sign-in has no credentials, so none is asked for. In use, signing asks '
                    'for your passphrase again.</p>')
        if me and me.get("kind") == "person" and not self.hub.has_passphrase(me["id"]):
            return ('<div class="error">You have no passphrase yet, and signing asks for it: '
                    '<a href="/me/passphrase">set your passphrase</a>, then come back.</div>'
                    '<label>Your passphrase, to sign *<input name="passphrase" type="password" '
                    'autocomplete="current-password" required></label>')
        return ('<label>Your passphrase, to sign *<input name="passphrase" type="password" '
                'autocomplete="current-password" required></label>')

    def _confirm_signer(self, req, f) -> None:
        """A signature is made with the signer's own credential, given for it: on a page, their passphrase typed again;
        over the API, the call's ``Authorization: Bearer`` (sent with that request, never ambient like a cookie).
        Agents never sign: the engine records their decisions as proposals or attestations, whatever they send."""
        given = str(f.get("passphrase", "") or "")
        if (not given and req["path"].startswith("/api/")
                and req.get("headers", {}).get("authorization", "").lower().startswith("bearer ")):
            return  # an API call: this very request was authenticated by the signer's token (ph_ or the identity provider's)
        if self.dev and not given:
            return
        self.hub.reauthenticate(req["me"], given)

    def post_item(self, req, iid, verb):
        me, eng = req["me"], self.hub.engine
        f = self.form(req)
        item = eng.item(iid)
        back = self.safe_back(f.get("_back"))  # from the record's page (or the board): back to it
        if verb == "claim":
            try:
                eng.claim(me, iid)
            except Denied as exc:  # the refusal names who holds it
                raise Denied(self.holder_refusal(me, item, str(exc))) from None
            raise Redirect(back or f"/items/{iid}")
        if verb == "release":
            eng.release(me, iid, f.get("note", ""))
            raise Redirect(back or "/")
        if verb == "take-over":  # from an overdue notice, the work taken opens
            taken = eng.take_over(me, iid, f.get('note', ''), to=f.get('to') or None)
            raise Redirect(back or f"/items/{taken['id']}")
        if verb == "ack":
            eng.acknowledge(me, iid)
            raise Redirect("/")
        # who does the work it sets going: one person each, chosen in its dialog (``_assign_fields``)
        assign = {k[len("assign:"):]: (v[-1] if isinstance(v, list) else v) for k, v in f.items() if k.startswith("assign:")}
        try:
            if verb == "decide":
                self._confirm_signer(req, f)
                eng.decide(me, iid, f.get("outcome", ""), f.get("comment", ""), seen=f.get("seen") or "-",
                           assign=assign, assign_required=True)
            else:
                eng.complete(me, iid, {k: v for k, v in self._form_output(f).items()
                                       if k != "_dialog" and not k.startswith("assign:")},
                             assign=assign, assign_required=True)
        except (Denied, Invalid) as exc:  # the page again, saying what went wrong, with what was typed kept
            if isinstance(exc, Denied):
                exc = Denied(self.holder_refusal(me, eng.item(iid), str(exc)))
            kept = {k: v for k, v in f.items() if k not in ("passphrase", "credential", "seen", "_back")}
            code = 403 if isinstance(exc, Denied) else 422
            if verb == "decide" and f.get("outcome"):  # a decision is made in its outcome's dialog: that again, as typed
                raise Rendered(code, self.page_decide(req, iid, kept={**kept, "_back": back}, error=exc)) from None
            if verb == "complete" and f.get("_dialog"):  # an answer is given in its dialog: that again, as typed
                raise Rendered(code, self.page_answer(req, iid, kept={**kept, "_back": back}, error=exc)) from None
            again = self._record_again(req, back, {"iid": iid, "kept": kept, "error": exc}) if back else None
            raise Rendered(code, again or self.page_item(req, iid, kept=kept, error=exc)) from None
        raise Redirect(back or self.run_href(item["run_id"]))  # where it was done: back to it

    # ------------------------------------------------------------------------------------------ subjects
    def subject_link(self, ref: str, text: str | None = None) -> str:
        return f'<a href="/{e(self.subj["path"])}/{e(ref.split("/", 1)[-1])}">{e(text or ref)}</a>'

    def page_subjects(self, req):
        me, st, label = req["me"], self.subj["type"], self.subj["label"].lower()
        found = self.hub.find_key(me, req["query"].get("q", ""))  # the search box takes a key too: T1-12 opens it
        if found:
            raise Redirect(self._key_href(found))
        if not self.hub.may_read(me, st):
            raise Denied(f"{me['name']} may not read {label} records")
        q = req["query"].get("q", "").lower()
        subs = self.hub.search_records(me, st, limit=SHOWN + 1, text=q or None)  # in the store: none is missed
        more = len(subs) > SHOWN
        subs = subs[:SHOWN]
        self.hub.note_list(me, subs, how="search" if q else "list", what=f"records:{st}", text=q or None)
        heads = list(self.hub.facts(subs[0]).keys()) if subs else []
        rows = "".join(f'<tr><td>{self.subject_link(f"{st}/{p["id"]}", self.hub.title(p))}</td>'
                       + "".join(f"<td>{self._fact(v)}</td>" for v in self.hub.facts(p).values()) + "</tr>"
                       for p in subs)
        reg = self._register_form(me) if self.hub.may_write(me, st) else ""
        body = (f'<form class="inline"><input name="q" value="{e(q)}" placeholder="search"><button class="secondary">'
                f'Search</button></form><table><tr><th>{e(self.subj["label"])}</th>'
                + "".join(f"<th>{e(h.capitalize())}</th>" for h in heads) + f"</tr>{rows}</table>"
                + (f"<p class='muted'>The {SHOWN} most recently changed are shown. Search by a name, a key or "
                   f"another detail to find any other.</p>" if more else "")
                + ("<p class='muted'>No one matches. Check the spelling, or search by another detail.</p>"
                   if q and not subs else "") + reg)
        return self.layout(req, self.subj["plural"], body)

    def _fact(self, v) -> str:
        if isinstance(v, str) and v.startswith("member:"):
            return e(self.member_name(v[7:]))
        return e("" if v is None else str(v))

    def _register_form(self, me, rec: dict | None = None, form: dict | None = None, same: list | None = None) -> str:
        """The subject type's own create form (its type extends the core's Subject), with the intake box. ``same``:
        who this may already be; registering anyway is a choice made on purpose."""
        st, label = self.subj["type"], self.subj["label"].lower()
        rt = self.hub.record_type(st)
        if rt is None:
            return ""
        tabs = self._on_screen(rt, "create")  # the type's create screen, when it has one
        fields = self._tabbed(tabs, lambda f: self._input(f, rec or {}, me), f"register-{st.lower()}") if tabs is not None else \
            "".join(self._input(f, rec or {}, me) for f in self._editable(rt) if not f.get("advanced"))
        intake = (f'<label class="switch-row"><input type="checkbox" class="switch" name="intake" value="1"'
                  f'{" checked" if form is None or form.get("intake") else ""}> {e(self.subj["register_label"])}</label>'
                  if self.subj["registered_event"] else "")
        warn = ""
        if same:
            rows = "".join(f'<li>{self.subject_link(f"{st}/{r["id"]}", self.hub.title(r))}</li>' for r in same)
            warn = (f'<div class="warn" role="alert"><strong>This may be someone already registered.</strong>'
                    f'<ul>{rows}</ul><p>Open their file instead, or, if this is a different {e(label)}, say so:</p>'
                    f'<label class="switch-row"><input type="checkbox" class="switch" name="_different" value="1" required> This is a '
                    f'different {e(label)}: register them anyway</label></div>')
        return (f'<h2 id="register">Register {e(a_an(label))}</h2>{self._req_note(fields)}{warn}'
                f'<form method="post" action="/{e(self.subj["path"])}" class="card grid">{fields}{intake}{warn and "<input type=hidden name=_checked value=1>"}'
                f'<button>Register</button></form>')

    def post_subject(self, req):
        me, f, st = req["me"], self.form(req), self.subj["type"]
        rt = self.hub.record_type(st)
        if rt is None:
            raise Invalid(f"there is no record type {st}")
        rec = self._from_form(rt, f, partial=False, op="create")
        same = [] if f.get("_different") else self.hub.duplicates(st, rec)
        if same:
            body = self._register_form(me, rec, f, same)
            return self.layout(req, f"Register {a_an(self.subj['label'].lower())}", body)
        p = self.hub.create_record(me, st, rec)
        if f.get("intake") and self.subj["registered_event"]:
            self.hub.engine.emit(me, self.subj["registered_event"], record=f"{st}/{p['id']}")
        raise Redirect(f"/{self.subj['path']}/{p['id']}")

    def page_subject(self, req, pid):
        """A subject's page: their banner (facts and alerts), then what is part of them (allergies, problems), each
        kind a section with its own Add, then what links to them (visits, results), then the work about them."""
        me, st = req["me"], self.subj["type"]
        ref = f"{st}/{pid}"
        p = self.hub.read_record(me, ref)
        chart = self.hub.chart(me, ref)  # the view of the whole file, as the access report counts it
        sections, rail, hidden = self.owner_sections(me, ref)
        parts = [self.subject_banner(me, ref, heading=True), self.upcoming_section(me, ref), sections]
        if not sections:
            parts.append("<p class='muted'>Nothing recorded yet.</p>")
        if chart["hidden"] or hidden:
            parts.append('<p class="muted">Some records are kept from your role and are not shown.</p>')
        word = self.subj["label"].lower()
        tools = f'<a class="btn secondary" href="/{e(self.subj["path"])}/{e(pid)}/export" data-modal>Export this {e(word)}\'s file</a>'
        if self.hub.is_policy_owner(me):
            tools += (f'<a class="btn secondary" href="/access?subject={urllib.parse.quote(ref)}">'
                      f'Who opened or changed this file</a>')
        parts.insert(1, f'<div class="file-tools" role="group" aria-label="This file">{tools}</div>')
        runs = self.hub.engine.runs(record=ref)
        parts.append(f"<h2 id='work'>Work about this {e(self.subj['label'].lower())}</h2>" + (
            "<table>" + "".join(f'<tr><td><a href="{e(self.run_href(r))}">{e(self.hub.run_title(req["me"], r))}</a></td><td>{self._loz(self.RUN_STATUS, r["status"])}</td>'
                                f'<td class="muted">{self._at(r["created_at"])}</td></tr>' for r in runs)
            + "</table>" if runs else "<p class='muted'>None.</p>"))
        starts = self.start_buttons(me, st, ref)
        if starts:  # the work a clinician starts about this patient: one button each, its form in the dialog
            parts.insert(2, f"<div id='start-work' class='starts' role='group' aria-label='Start work'>{starts}</div>")
        extra = [("work", f"About this {self.subj['label'].lower()}", len(runs))]
        # below the chart, the full width: the work under way about them, as cards (finished work is in the list above)
        panel = self.work_panel(me, ref)
        # urgent work about them goes directly under the banner, at every width: never below the fold of the chart
        about = self.hub.engine.runs(mentions=ref, status="running", limit=200)  # its results' reviews too
        band = self._urgent_band(me, self._record_urgent(me, ref, about), here=f"/{self.subj['path']}/{pid}", about=ref,
                                 records=self.urgent_records(me, ref))
        body = parts[0] + band + "".join(parts[1:])
        if panel:
            body += f'<div class="wp-inline wp-below" role="region" aria-label="Open work">{panel}</div>'

        return self.layout(req, self.hub.short_title(p), body, heading=False,
                           crumbs=[(f"/{self.subj['path']}", self.subj["plural"]), (None, self.hub.short_title(p))],
                           subnav=self.owner_rail(self.hub.short_title(p), rail, extra, label=self.subj["label"]))

    # ------------------------------------------------------------------------------------------ runs
    def post_retry(self, req, rid, sid):
        f = self.form(req)
        self._run_verb(req, rid, f, lambda: self.hub.engine.retry(req["me"], rid, sid, f.get("reason", "")))

    def post_cancel(self, req, rid):
        """Cancel a piece of work, from its record's page (or its own, for work about no record); refused, the
        record's page says why (``Engine.cancel_refusal``)."""
        f = self.form(req)
        run = self.hub.engine.run(rid)
        why = self.hub.engine.cancel_refusal(req["me"], run) if run["status"] == "running" else None

        def cancel():
            if why:
                raise Denied(f"you may not cancel it: {why}")
            self.hub.engine.cancel(req["me"], rid, f.get("reason", ""))
        self._run_verb(req, rid, f, cancel)

    def _run_verb(self, req, rid, f, act):
        """Do ``act`` to a run, then back to where it was asked (the record's page, at its work). Refused, from a
        record's page: that page again, saying why at its work."""
        back = self.safe_back(f.get("_back")) or self.run_href(rid)
        try:
            act()
        except (Denied, Invalid) as exc:
            again = self._record_again(req, back, {"error": exc})
            if again is None:
                raise
            raise Rendered(403 if isinstance(exc, Denied) else 422, again) from None
        raise Redirect(back)

    def _record_again(self, req, back: str, work: dict) -> str | None:
        """The record page ``back`` names, drawn again with ``work`` (a refusal, what was typed) at its work; None
        for a page that is not a record's."""
        path = self.flat_path(back.split("#", 1)[0].split("?", 1)[0])
        m = re.fullmatch(r"/records/([A-Za-z][\w]*)/([\w.-]+)", path)
        if not m:
            return None
        return self.page_record({**req, "query": {}, "_work": work}, m.group(1), m.group(2))

    def _open_words(self, run) -> str:
        """The words of a link to where a piece of work is worked on: its record (Open the visit), else the work."""
        ref = run.get("record")
        rt = self.hub.record_type(ref.split("/", 1)[0]) if ref and "/" in ref else None
        return f"Open the {rt.label.lower()}" if rt else "See the request"

    def _events(self, events, me) -> str:
        rows, who, memo = [], {}, {}
        for ev in Viewer(self.hub, me).events(events):
            if ev["actor"] not in who:  # one look-up per member on the page, not one per event
                who[ev["actor"]] = self.who(ev["actor"])
            signed = self._signed_badge(ev)
            data = {k: v for k, v in ev["data"].items() if k not in ("signed", "attested")}
            rows.append(f'<tr><td><a href="/track/{ev["seq"]}">#{ev["seq"]}</a></td>'
                        f'<td class="muted">{self._at(ev["at"])}</td><td>{who[ev["actor"]]}</td>'
                        f'<td>{e(self.human(ev["action"].replace(".", " ")))}{signed}</td>'
                        f'<td>{self._event_on(me, ev["subject"])}</td>'
                        f'<td class="muted">{"#" + str(ev["cause"]) if ev["cause"] else ""}</td>'
                        f'<td>{self._event_words(ev, me, memo) if data else ""}</td></tr>')
        return ('<table><tr><th>#</th><th>at</th><th>who</th><th>what</th><th>on</th><th>because</th><th></th></tr>'
                + "".join(rows) + "</table>")

    # ------------------------------------------------------------------------------------------ workflows
    @staticmethod
    def _diff(diff: str) -> str:
        out = []
        for line in diff.splitlines():
            cls = "add" if line.startswith("+") and not line.startswith("+++") else \
                  "del" if line.startswith("-") and not line.startswith("---") else ""
            out.append(f'<span class="{cls}">{e(line)}</span>')
        return "\n".join(out)

    def post_workflow_verb(self, req, wid, ver, verb):
        if verb == "approve":
            self.hub.approve_workflow(req["me"], wid, int(ver))
        else:
            self.hub.withdraw_workflow(req["me"], wid, int(ver), self.form(req).get("reason", ""))
        raise Redirect(f"/workflows/{wid}")

    # ------------------------------------------------------------------------------------------ blocks
    RUNTIME_LABEL = {"python": "Python", "prompt": "Agent prompt", "external": "Engine"}

    def _rt(self, runtime: str) -> str:
        return f'<span class="badge rt-{e(runtime)}">{e(self.RUNTIME_LABEL.get(runtime, runtime))}</span>'

    @staticmethod
    def _io(fields) -> str:
        return ", ".join(f'{f["name"]}{"*" if f.get("required") else ""}' for f in fields) or "—"

    def page_blocks(self, req):
        rows = []
        for b in self.hub.blocks():
            blk, row = b["block"], b["row"]
            prop = f' <span class="badge proposed">{b["proposed"]} proposed</span>' if b["proposed"] else ""
            used = ", ".join(w.title for w in self.hub.block_users(blk.id)) or "—"
            where = {"python": "sandbox", "prompt": f"agents with {blk.capability}",
                     "external": f"{blk.engine}: {blk.target}"}[blk.runtime]
            rows.append(f'<tr><td><a href="/blocks/{e(blk.id)}"><b>{e(blk.title)}</b></a>{prop}'
                        f'<div class="muted">{e(blk.summary[:160])}</div></td><td>{self._rt(blk.runtime)}'
                        f'<div class="muted">{e(where)}</div></td>'
                        f'<td>{"v" + str(row["version"]) if row["version"] else "—"}</td>'
                        f'<td>{e(self._io(blk.inputs))}</td><td>{e(self._io(blk.outputs))}</td><td>{e(used)}</td></tr>')
        engine_rows = "".join(
            f'<tr><td><b>{e(x.label)}</b> <code>{e(x.name)}</code></td><td>'
            f'{'<span class="badge active">ready</span>' if x.configured() else '<span class="badge">not set up</span>'}'
            f'</td><td class="muted">{e(x.describe())}</td></tr>' for x in engines())
        body = (
            '<p class="muted">Blocks are steps the practice builds once and drops into any workflow, with parameters. '
            'Python blocks are deterministic code run in a sandbox; prompt blocks are instructions an agent carries out; '
            'engine blocks run on an automation engine under the hub. Every block is approved and signed like a '
            'workflow, and runs keep the version they started with.</p>'
            f'<table class="list"><tr><th>Block</th><th>Runs as</th><th>Version</th><th>Inputs</th><th>Outputs</th>'
            f'<th>Used by</th></tr>{"".join(rows) or "<tr><td colspan=6 class=muted>No blocks yet.</td></tr>"}</table>'
            '<h2>Engines</h2><p class="muted">What runs a block. The hub keeps the gates, eligibility and the track '
            'whichever engine does the work. Engines are set up by the administrator, in the environment.</p>'
            f'<table class="list"><tr><th>Engine</th><th>Status</th><th></th></tr>{engine_rows}</table>')
        new = "".join(f'<a class="btn{" secondary" if i else ""}" href="/blocks/new?runtime={r}">New {lbl.lower()} block</a> '
                      for i, (r, lbl) in enumerate(self.RUNTIME_LABEL.items()))
        return self.layout(req, "Blocks", body, crumbs=[("/", "Practice"), ("", "Blocks")], actions=new)

    def page_block_new(self, req):
        runtime = req["query"].get("runtime", "python")
        return self.page_block_edit(req, None, source=NEW_BLOCKS.get(runtime, NEW_BLOCKS["python"]))

    def page_block_edit(self, req, bid, *, source=None, note="", problems=None):
        if source is None:
            versions = self.hub.block_versions(bid)
            if not versions:
                raise NotFound(f"there is no block {bid!r}")
            try:
                source = self.hub.block(bid)[1]["source"]
            except NotFound:
                source = versions[0]["source"]
        me = req["me"]
        probs = ("<div class='error'><ul>" + "".join(f"<li>{e(p)}</li>" for p in problems) + "</ul></div>") if problems else ""
        agent_note = ("<div class='warn'>As an agent you may propose prompt blocks only. Python and engine blocks are "
                      "written by people.</div>") if me["kind"] != "person" else ""
        help_ = (
            '<details class="card"><summary>How to write a block</summary><ul>'
            '<li><code>[[input]]</code> fields are the parameters a workflow fills with <code>params</code>; '
            '<code>[[output]]</code> fields are what the block returns, checked like any answer. Write top-level keys '
            '(<code>code</code>, <code>prompt</code>, <code>engine</code>) before the first <code>[[input]]</code>.</li>'
            f'<li><b>Python</b>: <code>def run(input):</code> returns a dict. Imports allowed: '
            f'{e(", ".join(ALLOWED_IMPORTS))}. No files, network, processes, <code>open</code>, <code>eval</code> or '
            f'dunder attributes. It runs in its own process with {SANDBOX_TIMEOUT:g}s to finish.</li>'
            '<li><b>Prompt</b>: what an agent should do; <code>{{input.name}}</code> fills in an input. Values a '
            'workflow reads from the run stay data: the prompt points at them instead of quoting them. Give it '
            '<code>[[output]]</code> fields or <code>extends</code> an existing skill.</li>'
            '<li><b>Engine</b>: <code>engine = "webhook"</code> or <code>"kestra"</code> and a <code>target</code> '
            '(for Kestra, <code>namespace/flow-id</code>). The engine receives the inputs only.</li></ul></details>')
        title = f"Edit block: {bid}" if bid else "Create block"
        crumbs = [("/blocks", "Blocks")] + ([(f"/blocks/{bid}", bid)] if bid else [])
        body = (f'<p class="muted">Saving is a proposal: a policy owner who did not write it approves and signs it '
                f'before any workflow uses it.</p>{agent_note}{probs}{help_}'
                f'<form method="post" action="/blocks/propose" class="card" id="bk-form"><label>Block (TOML)'
                f'<textarea name="source" id="bk-source" rows="30" class="code" spellcheck="false">{e(source)}</textarea>'
                f'</label><div class="status" id="bk-status"></div>'
                f'<label>What changes, and why<input name="note" value="{e(note)}" required></label>'
                f'<button>Propose block</button></form>'
                '<script>(function(){const t=document.getElementById("bk-source"),s=document.getElementById("bk-status");'
                'let timer;async function check(){try{const r=await fetch("/api/blocks/check",{method:"POST",'
                'credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify({source:t.value})});'
                'const j=await r.json();s.textContent="";if(!j.problems||!j.problems.length){s.innerHTML=\'<span class="loz done">'
                'ready</span> The hub accepts this block.\';return}const ul=document.createElement("ul");ul.className="ed-problems";'
                'for(const p of j.problems){const li=document.createElement("li");li.textContent=p;ul.append(li)}'
                'const b=document.createElement("span");b.className="loz bad";b.textContent=j.problems.length+" problem"+'
                '(j.problems.length===1?"":"s");s.append(b,ul)}catch(e){s.textContent="Could not reach the hub to check."}}'
                't.addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(check,500)});check()})();</script>')
        return self.layout(req, title, body, crumbs=crumbs)

    def post_block_propose(self, req):
        f = self.form(req)
        try:
            out = self.hub.propose_block(req["me"], f.get("source", ""), f.get("note", ""))
        except Invalid as exc:
            try:
                bid = tomllib.loads(f.get("source", "")).get("id")
            except tomllib.TOMLDecodeError:
                bid = None
            bid = bid if isinstance(bid, str) and self.hub.block_versions(bid) else None
            return self.page_block_edit(req, bid, source=f.get("source", ""), note=f.get("note", ""),
                                        problems=exc.problems)
        raise Redirect(f"/blocks/{out['block_id']}#v{out['version']}")

    def _block_body(self, blk) -> str:
        io = (f'<div class="section"><h2>Inputs</h2>{self._fields_table(blk.inputs)}</div>'
              f'<div class="section"><h2>Outputs</h2>{self._fields_table(blk.outputs) if blk.outputs else "<p class=muted>The outputs of the skill " + e(blk.extends or "") + ".</p>"}</div>')
        if blk.runtime == "python":
            what = f'<h2>Code</h2><pre class="code">{e(blk.code.strip())}</pre><p class="muted">SHA-256 {e(blk.code_sha256)}</p>'
        elif blk.runtime == "prompt":
            what = f'<h2>Prompt given to the agent</h2><pre class="wrap">{e(blk.prompt)}</pre>'
        else:
            what = (f'<h2>Engine call</h2><p>Runs <code>{e(blk.target)}</code> on <b>{e(blk.engine)}</b>, which receives '
                    f'the inputs above and nothing else from the run.</p>')
        return f'{io}<div class="section">{what}</div>'

    def _fields_table(self, fields) -> str:
        if not fields:
            return "<p class='muted'>None.</p>"
        rows = "".join(f'<tr><td><code>{e(f["name"])}</code>{" *" if f.get("required") else ""}</td><td>{e(f["type"])}'
                       f'{" (" + e(", ".join(map(str, f.get("choices") or []))) + ")" if f.get("choices") else ""}</td>'
                       f'<td>{e(str(f["default"])) if f.get("default") is not None else ""}</td>'
                       f'<td class="muted">{e(f.get("help") or "")}</td></tr>' for f in fields)
        return f'<table><tr><th>Name</th><th>Type</th><th>Default</th><th></th></tr>{rows}</table>'

    def page_block(self, req, bid):
        me, hub = req["me"], self.hub
        versions = hub.block_versions(bid)
        if not versions:
            raise NotFound(f"there is no block {bid!r}")
        try:
            blk, active = hub.block(bid)
        except NotFound:
            blk, active = None, None
        main, dl = [], []
        if blk:
            main.append(f'<p>{e(blk.summary)}</p>{self._block_body(blk)}')
            where = {"python": "the built-in sandbox", "prompt": "an agent (a task step)",
                     "external": f"{blk.engine}"}[blk.runtime]
            dl = [("Active", f"v{active['version']}"), ("Runs as", self._rt(blk.runtime)), ("Runs on", e(where))]
            if blk.runtime == "prompt":
                dl.append(("Agents need", f"<code>{e(blk.capability)}</code> in their profile"))
            dl.append(("Used by", ", ".join(f'<a href="/workflows/{e(w.id)}">{e(w.title)}</a>'
                                            for w in hub.block_users(bid)) or "no workflow yet"))
        rows = []
        for v in versions:
            extra = ""
            if v["status"] == "proposed":
                try:
                    pblk, _ = hub.block(bid, v["version"])
                    impact = hub.block_impact(bid, v["version"])
                except wfmod.DefinitionError as exc:
                    pblk, impact = None, exc.problems
                warn = "".join(f'<div class="warn">{e(n)}</div>' for n in impact) or \
                    "<p class='muted'>Nothing changes for workflows or agents.</p>"
                diff = "\n".join(difflib.unified_diff((active["source"] if active else "").splitlines(),
                                                      v["source"].splitlines(), "active", f"v{v['version']}", lineterm=""))
                approve = ""
                if me["kind"] == "person" and hub.is_policy_owner(me) and v["author"] != me["id"]:
                    approve = (f'<form method="post" action="/blocks/{e(bid)}/{v["version"]}/approve" class="inline">'
                               f'<button>Approve and sign</button></form>')
                withdraw = ""
                if v["author"] == me["id"] or hub.is_policy_owner(me):
                    withdraw = (f'<form method="post" action="/blocks/{e(bid)}/{v["version"]}/withdraw" class="inline">'
                                f'<label>Why <input name="reason"></label> <button class="secondary">'
                                f'{"Withdraw" if v["author"] == me["id"] else "Decline"}</button></form>')
                extra = (f'<div class="card"><h3>Before you approve</h3>{warn}'
                         f'{self._block_body(pblk) if pblk and not blk else ""}<details{" open" if blk else ""}><summary>'
                         f'Changes to the text</summary><pre class="diff">{self._diff(diff)}</pre></details>'
                         f'<div class="filters" style="margin-top:10px">{approve}{withdraw}</div></div>')
            rows.append(f'<tr id="v{v["version"]}"><td>v{v["version"]}</td><td><span class="badge {e(v["status"])}">'
                        f'{e(v["status"])}</span></td><td>{self.who(v["author"])}</td><td>{e(v["note"])}</td>'
                        f'<td>{self.who(v["approved_by"]) if v["approved_by"] else ""}</td></tr>'
                        + (f'<tr><td colspan="5">{extra}</td></tr>' if extra else ""))
        main.append(f'<div class="section"><h2>Versions</h2><table><tr><th></th><th>Status</th><th>Author</th><th>Note</th>'
                    f'<th>Approved by</th></tr>{"".join(rows)}</table></div>')
        details = ('<aside class="details"><h3>Details</h3><dl>'
                   + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in dl) + "</dl></aside>") if dl else "<div></div>"
        return self.layout(req, blk.title if blk else bid, f'<div class="issue"><div>{"".join(main)}</div>{details}</div>',
                           crumbs=[("/blocks", "Blocks")], actions=f'<a class="btn" href="/blocks/{e(bid)}/edit">Edit block</a>')

    def post_block_verb(self, req, bid, ver, verb):
        if verb == "approve":
            self.hub.approve_block(req["me"], bid, int(ver))
        else:
            self.hub.withdraw_block(req["me"], bid, int(ver), self.form(req).get("reason", ""))
        raise Redirect(f"/blocks/{bid}")

    # ------------------------------------------------------------------------------------------ team
    def page_team(self, req):
        me, hub = req["me"], self.hub
        groups = {"person": "People", "agent": "Agents", "service": "Services"}
        parts = ["<p>Everyone who works here is a member: people, agents and services. Every agent and service has a "
                 "person as its sponsor, who answers for what it does and can switch it off.</p>"]
        for kind, title in groups.items():
            rows = []
            for m in hub.members(kind):
                prof = m["profile"]
                extra = (f'{e(prof.get("model", ""))} · max risk {e(prof.get("max_risk", "low"))} · '
                         f'{e(", ".join(prof.get("skills", [])))}') if kind == "agent" else e(m["title"])
                rows.append(f'<tr><td>{self.who(m["id"])}</td><td>{e(self._role_words(m["roles"]))}</td>'
                            f'<td>{self.who(m["sponsor"]) if m["sponsor"] else ""}</td>'
                            f'<td class="{"" if m["status"] == "active" else "off"}">{e(m["status"])}</td>'
                            f'<td class="muted">{extra}</td></tr>')
            parts.append(f'<h2>{title}</h2><table><tr><th>Member</th><th>Roles</th><th>Sponsor</th><th>Status</th><th></th>'
                         f'</tr>{"".join(rows)}</table>')
        def on_duty(r):
            if any(h["status"] == "active" for h in r["holders"]):
                return ""
            return '<div class="lozenge danger">nobody on duty</div>'
        lone = hub.member_admin_warning()
        if lone and (hub.can(me, "member_admin", "practice:main") or hub.is_policy_owner(me)):
            parts.insert(0, f'<div class="warn" role="status">{e(lone)}</div>')
        empty = hub.empty_roles()
        if empty:
            parts.insert(0, '<div class="error">Work is routed to roles that nobody switched on holds: '
                         + e(self._role_words(empty)) + ". It is raised with the escalation role or a policy owner "
                         "until someone holds the role.</div>")
        out_of_reach = getattr(hub, "sponsors_out_of_reach", None)
        if out_of_reach and (hub.can(me, "member_admin", "practice:main") or hub.is_policy_owner(me)):
            far = out_of_reach() or []
            if far:
                parts.insert(0, '<div class="warn" role="status"><p><b>Sponsors who no longer reach what their agent '
                             'or service does</b> (its credentials end until a member admin names a sponsor who '
                             'does):</p><ul>' + "".join(
                                 f'<li>{self.who(x["id"])}, sponsored by {self.who(x["sponsor"])}: '
                                 f'{e(", ".join(str(b) for b in x.get("beyond") or []))}</li>' for x in far)
                             + '</ul></div>')
        roles = "".join(f'<tr><td><b>{e(r["name"])}</b><div class="muted">{e(r["description"])}</div></td>'
                        f'<td>{", ".join(self.who(h["id"]) for h in r["holders"])}{on_duty(r)}</td></tr>'
                        for r in hub.roles())
        parts.append(f"<h2>Roles</h2><table>{roles}</table>")
        add = ""
        if hub.can(me, "member_admin", "practice:main"):
            add = '<a class="btn primary" href="/team/new" data-modal>Add people</a>'
        else:
            admins = [m for m in hub.members("person") if m["status"] == "active"
                      and hub.can(m, "member_admin", "practice:main")]
            parts.insert(0, '<p class="muted">To add someone, ask '
                         + (", ".join(self.who(m["id"]) for m in admins[:5]) or "a policy owner") + ".</p>")
        return self.layout(req, "Team", "".join(parts), actions=add)

    def page_team_new(self, req, kept: dict | None = None, error: Exception | None = None):
        if not self.hub.can(req["me"], "member_admin", "practice:main"):
            raise Denied(f"{req['me']['name']} may not add members")
        err = f'<div class="flash error" role="alert"><p>{e(str(error))}</p></div>' if error else ""
        return self.layout(req, "Add people", f'<div data-modal-body>{err}{self._add_member_form(kept or {}, req["me"])}</div>',
                           crumbs=[("/team", "Team"), (None, "Add people")])

    def _role_reach(self, roles: set[str], actor: dict | None) -> dict[str, str]:
        """What each role reaches that ``actor`` does not, in words ('reads visits and writes intake forms'), or what
        it reaches at all when ``actor`` is None. Only restricted data classes and emergency access count."""
        hub, rclasses = self.hub, self.hub.restricted_classes()
        found: dict[str, dict[str, list[str]]] = {}
        for obj, rel, sub in hub.authz.read():
            if not (sub.startswith("role:") and sub.endswith("#assignee")):
                continue
            role = sub.removeprefix("role:").removesuffix("#assignee")
            if role not in roles or (actor is not None and hub.can(actor, rel, obj)):
                continue
            if rel in ("reader", "writer") and obj.startswith("data_class:") and obj[11:] in rclasses:
                found.setdefault(role, {}).setdefault(self._class_name(obj[11:]), []).append(
                    "reads" if rel == "reader" else "writes")
            elif rel == "break_glass" and obj == "practice:main":
                found.setdefault(role, {}).setdefault("withheld files in an emergency", []).append("opens")
        out = {}
        for role, classes in found.items():
            parts = [f'{" and ".join(sorted(set(verbs), reverse=True))} {what}' for what, verbs in classes.items()]
            out[role] = ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1]
        return out

    def _role_reasons(self, me: dict | None, kind: str = "person", sponsor: dict | None = None) -> dict[str, str]:
        """Why each role the viewer may not give at once waits for a second policy owner, in words; and, for an agent
        or a service, the roles its sponsor does not reach (giving one would be refused). Roles not named are given
        at once."""
        hub = self.hub
        if me is None:
            return {r: "a second policy owner approves" for r in hub.privileged_roles()}
        priv, beyond = hub.privileged_roles(), hub.beyond_roles(me)
        data = hub.data_roles() if kind != "person" else set()
        out: dict[str, str] = {}
        mine = self._role_reach(beyond, me)
        for r in beyond:
            out[r] = f"a second policy owner approves: it {mine.get(r, 'reaches information')}, which you do not"
        for r in priv - beyond:
            out[r] = "a second policy owner approves: it approves changes or manages members"
        reach = self._role_reach(data - beyond - priv, None)
        for r in data - beyond - priv:
            out[r] = (f"a second policy owner approves: it {reach.get(r, 'reaches restricted information')}, "
                      f"for an {kind}")
        if sponsor is not None:
            sp_beyond = hub.beyond_roles(sponsor)
            theirs = self._role_reach(sp_beyond, sponsor)
            for r in sp_beyond:
                out[r] = (f"its sponsor does not reach this: it {theirs.get(r, 'reaches information')}, which "
                          f"{sponsor['name']} does not")
        return out

    def _role_boxes(self, have=(), pending=(), held: set[str] | None = None,
                    reasons: dict[str, str] | None = None) -> str:
        """The role checkboxes: the roles the viewer gives at once first, then those that wait, each saying why."""
        if reasons is None:
            reasons = {r: "a second policy owner approves"
                       for r in (self.hub.privileged_roles() if held is None else held)}
        roles = sorted(self.hub.roles(), key=lambda r: r["id"] in reasons)  # stable: the practice's order within
        waiting = any(r["id"] in reasons for r in roles)
        out = []
        for i, r in enumerate(roles):
            if waiting and r["id"] in reasons and (i == 0 or roles[i - 1]["id"] not in reasons):
                out.append('<p class="muted role-wait-head">These wait for a second policy owner:</p>')
            why = reasons.get(r["id"])
            out.append(
                f'<label class="radio multi"><input type="checkbox" class="opt-input" name="roles" value="{e(r["id"])}"'
                f'{" checked" if r["id"] in have or r["id"] in pending else ""}> {e(r["name"])}'
                f'{f" <small>{e(why)}</small>" if why else ""}'
                f'{" <span class=\"lozenge\">waiting for approval</span>" if r["id"] in pending and r["id"] not in have else ""}'
                f'</label>')
        return "".join(out)

    def _person_options(self, selected: str | None = None) -> str:
        return "".join(f'<option value="{e(m["id"])}"{" selected" if m["id"] == selected else ""}>{e(m["name"])}</option>'
                       for m in self.hub.members("person") if m["status"] == "active")

    def _add_member_form(self, kept: dict, me: dict | None = None) -> str:
        k = lambda n: e(str(kept.get(n, "") or ""))
        kinds = "".join(f'<label class="radio"><input type="radio" class="opt-input" name="kind" value="{v}"'
                        f'{" checked" if kept.get("kind", "person") == v else ""}> {t}</label>'
                        for v, t in (("person", "Person"), ("agent", "Agent"), ("service", "Service (a feed or connector)")))
        roles = kept.get("roles") or []
        cid, ids = self._control_id("id", True)
        sid, sids = self._control_id("sponsor", True)
        req = '<span class="req" aria-hidden="true"> *</span>'
        # the sponsor is for agents and services: the script shows it (and asks for it) only for those kinds
        return ('<form method="post" action="/team" class="card stack" id="add">'
                f'{self._req_note(req)}'
                f'<fieldset class="choices options"><legend>Kind</legend>{kinds}</fieldset>'
                f'<label>Name{req}<input name="name" required value="{k("name")}"></label>'
                f'<div class="field"><label>Short id<input name="id"{ids} pattern="[a-z0-9][a-z0-9-]*" '
                f'title="Lower-case letters, digits and dashes, starting with a letter or digit" '
                f'placeholder="made from the name" value="{k("id")}"></label><small id="{cid}-help" class="help">'
                f'Lower-case letters, digits and dashes; it cannot change. Left empty, it is made from the name.'
                f'</small></div>'
                f'<label>Title<input name="title" value="{k("title")}"></label>'
                f'<label>Email<input name="email" type="email" value="{k("email")}"></label>'
                f'<div class="field" data-kinds="agent service"><label>Sponsor<span class="kind-hint"> (agents and '
                f'services)</span><span class="req" aria-hidden="true" hidden> *</span><select name="sponsor"{sids}>'
                f'<option value=""></option>{self._person_options(kept.get("sponsor"))}</select></label>'
                f'<small id="{sid}-help" class="help">The person accountable for it.</small></div>'
                f'<fieldset class="choices options"><legend>Roles</legend>{self._role_boxes(roles, reasons=self._role_reasons(me))}</fieldset>'
                '<div class="filters"><button class="primary">Add</button>'
                '<a class="btn secondary" href="/team" data-modal-close>Cancel</a></div></form>')

    def post_team_add(self, req):
        f = self.form(req)
        roles = f.get("roles", [])
        roles = roles if isinstance(roles, list) else [roles] if roles else []
        m = {k: str(f.get(k, "")).strip() for k in ("kind", "id", "name", "title", "email")}
        if m["kind"] != "person":
            m["sponsor"] = str(f.get("sponsor", "")) or None
        try:
            if not m["id"]:  # made from the name: "Dr. Lee Chen" -> lee-chen, then lee-chen-2 if taken
                base = re.sub(r"[^a-z0-9]+", "-", re.sub(r"^(dr|mr|mrs|ms|mx)\.?\s+", "", m["name"].lower())).strip("-")[:56]
                base, n, m["id"] = base or m["kind"], 1, base or m["kind"]
                while self.hub.db.members.get(m["id"]):
                    n += 1
                    m["id"] = f"{base}-{n}"
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", m["id"]):
                raise Invalid("the short id is lower-case letters, digits and dashes")
            out = self.hub.add_member(req["me"], {**m, "roles": roles})
        except (Invalid, Denied) as exc:
            raise Rendered(422, self.page_team_new(req, kept={**m, "roles": roles,
                                                               "sponsor": f.get("sponsor")}, error=exc)) from None
        raise Redirect(f"/team/{out['id']}")

    def page_member(self, req, mid, new_token: str | None = None, empty: list | None = None,
                    setup: dict | None = None, passphrase: tuple = ("", False)):
        me, hub = req["me"], self.hub
        m = hub.member(mid)
        rnames = {r["id"]: r["name"] for r in hub.roles()}
        info = {"kind": m["kind"], "title": m["title"], "roles": ", ".join(rnames.get(r, r) for r in m["roles"]),
                "status": "Switched on" if m["status"] == "active" else "Switched off",
                "sponsor": self.member_name(m["sponsor"]) if m["sponsor"] else None}
        if m["kind"] == "agent":
            info.update(model=m["profile"].get("model"), max_risk=m["profile"].get("max_risk"),
                        skills=", ".join(m["profile"].get("skills", [])))
        actions = []
        can_admin = hub.can(me, "member_admin", "practice:main")
        off_form = ""
        if m["status"] == "active" and m["id"] != me["id"]:
            if can_admin or m["sponsor"] == me["id"]:
                off_form = self._switch_off_form(m, "disable", "Switch off", me=me)
            elif hub.is_policy_owner(me) and me["kind"] == "person" and not hub.off_request(mid):
                off_form = self._switch_off_form(m, "ask-disable", "Ask to switch off",
                                                 "Two policy owners together switch someone off: a second policy "
                                                 "owner approves.", me=me)
        if m["status"] == "disabled" and can_admin:
            actions.append(f'<form method="post" action="/team/{e(mid)}/enable" class="inline"><button>Switch on</button></form>')
        if m["status"] == "active" and (m["id"] == me["id"] if m["kind"] == "person" else m["sponsor"] == me["id"]):
            actions.append(f'<form method="post" action="/team/{e(mid)}/token" class="inline"><input name="label" '
                           f'placeholder="what the token is for"><button class="secondary">Issue a token</button></form>')
        token = ""
        if new_token:  # shown in this response only: never put in a URL, where history and logs keep it
            token = (f'<div class="card"><p>The new token, shown once (only its hash is stored). Copy it now:</p>'
                     f'<pre>{e(new_token)}</pre></div>')
        if setup:  # shown in this response only, like a new token; only its hash is stored
            token += (f'<div class="card"><p>A one-time setup code for {e(m["name"])}, shown once. Give it to them in '
                      f'person or by phone, not by email. It works once, until {self._at(setup["expires_at"])}, '
                      f'at <a href="/setup?member={e(urllib.parse.quote(mid))}">/setup</a>:</p>'
                      f'<pre>{e(setup["code"])}</pre><p class="muted">It is for {e(m["name"])} alone: once it is '
                      f'used, their other sessions end and their old passphrase stops working. That you issued it is '
                      f'on the track.</p></div>')
        if empty:
            names = {r["id"]: r["name"] for r in hub.roles()}
            token += ('<div class="error">Nobody switched on now holds: ' + ", ".join(e(names.get(r, r)) for r in empty)
                      + ". Work for these roles is raised with the escalation role or a policy owner until someone "
                      "holds them. Give the roles to someone below, or re-sponsor the agents that stopped.</div>")
        lone = hub.member_admin_warning()
        if (lone and m["status"] == "active" and (can_admin or hub.is_policy_owner(me))
                and mid in {a["id"] for a in hub.member_admins()}):
            token += f'<div class="warn" role="status">{e(lone)}</div>'
        actions += self._off_request_card(me, m) + self._member_admin_forms(me, m)
        sponsored = [x for x in hub.members() if x["sponsor"] == mid]
        sp = ("<h2>Sponsors</h2><ul class='plain'>" + "".join(f"<li>{self.who(x['id'])} · {e(x['status'])}</li>"
                                                              for x in sponsored) + "</ul>") if sponsored else ""
        events = hub.track.list(actor=mid, limit=50)
        own = ""
        if mid == me["id"] and m["kind"] == "person":  # your own profile holds your passphrase
            own = self._own_passphrase(me, *passphrase)
        body = (self._values(info) + own + token + "".join(actions) + off_form + sp + "<h2>Recent actions</h2>"
                + self._events(events, me))
        return self.layout(req, m["name"], body, actions=(f'<a class="btn" href="/me/settings">My settings</a>'
                                                          if mid == me["id"] else ""))

    def _switch_off_form(self, m, verb: str, label: str, note: str = "", me: dict | None = None) -> str:
        """The off switch. For a person who sponsors agents or services it lists each one, what stops with it, and
        asks what becomes of it: handed to a new sponsor, or stopped. There is no default: someone chooses."""
        hub, mid = self.hub, m["id"]
        deps = hub.dependents(mid)
        reason = (f'<label>Why{"" if deps else " (for the track)"}<input name="reason" placeholder="reason" '
                  f'required></label>')
        if not deps:
            return (f'<form method="post" action="/team/{e(mid)}/{verb}" class="inline">'
                    f'<input name="reason" placeholder="reason" required aria-label="Why"><button class="danger">'
                    f'{e(label)}</button></form>' + (f'<p class="muted">{e(note)}</p>' if note else ""))
        rnames = {r["id"]: r["name"] for r in hub.roles()}
        rows = []
        for d in deps:
            # only someone who reaches all it reaches may take it on: say who could, or what a successor needs
            people, _ = self._sponsor_candidates(hub.member(d["id"]), me, leaving=mid)
            opts = ('<option value="">Choose…</option>'
                    + "".join(f'<option value="hand:{e(p["id"])}">Hand over to {e(p["name"])}</option>' for p in people)
                    + '<option value="stop">Let it stop (its credentials end for good)</option>')
            needs = self._successor_roles(d)
            who_can = (f'Could take it: {", ".join(e(p["name"]) for p in people)}' if people else
                       "Nobody else reaches what it does, so it can only stop"
                       + (f": a successor first needs {e(needs)} (a second policy owner approves)" if needs else ""))
            stops = [f'{d["credentials"]} live credential{"s" if d["credentials"] != 1 else ""}' if d["credentials"]
                     else "", ("the only one switched on in " + ", ".join(e(rnames.get(r, r)) for r in d["only_holder_of"]))
                     if d["only_holder_of"] else ""]
            what = " · ".join(x for x in stops if x)
            rows.append(f'<tr><td>{self.who(d["id"])}<div class="muted">{e(d["kind"].capitalize())}'
                        f'{" · " + what if what else ""}</div><div class="muted">{who_can}.</div></td><td><label class="visually-hidden" for="fate-{e(d["id"])}">'
                        f'What becomes of {e(d["name"])}</label><select id="fate-{e(d["id"])}" name="fate:{e(d["id"])}" '
                        f'required>{opts}</select></td></tr>')
        return (f'<h2 id="switch-off">{e(label)}</h2><form method="post" action="/team/{e(mid)}/{verb}" class="card stack">'
                f'<p>{e(m["name"])} sponsors {len(deps)} agent{"s" if len(deps) != 1 else ""} or service'
                f'{"s" if len(deps) != 1 else ""} that would stop with them. Choose a new sponsor for each, or say '
                f'that it stops: nothing stops without someone choosing it.</p>'
                f'<div class="tablewrap"><table class="fates"><thead><tr><th scope="col">Depends on {e(m["name"])}</th>'
                f'<th scope="col">What becomes of it</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
                f'{reason}' + (f'<p class="muted">{e(note)}</p>' if note else "")
                + f'<div class="filters"><button class="danger">{e(label)}</button></div></form>')

    def _fates(self, f: dict) -> tuple[dict, list]:
        """The switch-off form's choices: fate:<agent> = hand:<person> | stop."""
        hand, stop = {}, []
        for k, v in f.items():
            if k.startswith("fate:") and isinstance(v, str):
                if v.startswith("hand:"):
                    hand[k[5:]] = v[5:]
                elif v == "stop":
                    stop.append(k[5:])
        return hand, stop

    def _off_request_card(self, me, m) -> list[str]:
        hub, mid = self.hub, m["id"]
        req = hub.off_request(mid)
        if not req or m["status"] != "active":
            return []
        what = [f'{self.member_name(a)} goes to {self.member_name(p)}' for a, p in req["hand_over"].items()]
        what += [f'{self.member_name(a)} stops' for a in req["stop"]]
        btns = ""
        if hub.is_policy_owner(me) and me["kind"] == "person" and me["id"] not in (req["by"], mid):
            btns += (f'<form method="post" action="/team/{e(mid)}/approve-disable" class="inline">'
                     f'<button class="danger">Approve: switch {e(m["name"])} off</button></form>')
        if me["id"] != mid and (me["id"] == req["by"] or hub.is_policy_owner(me)):  # never the one it is about
            btns += (f'<form method="post" action="/team/{e(mid)}/withdraw-disable" class="inline">'
                     f'<button class="secondary">{"Withdraw" if me["id"] == req["by"] else "Decline"}</button></form>')
        return [f'<div class="card"><p><b>Waiting for a second policy owner:</b> {self.member_name(req["by"])} asked '
                f'to switch {e(m["name"])} off: {e(req["reason"])}.' + (f' {e("; ".join(what))}.' if what else "")
                + f'</p>{btns}</div>']

    def _sponsor_candidates(self, m: dict, me: dict | None = None, leaving: str | None = None) -> tuple[list, list]:
        """Who may sponsor this agent or service: active people (not ``leaving``) who reach everything it reaches,
        and not the viewer naming themselves. Returns (those, [(person, why not)]) so a page can say why others
        are missing."""
        hub, able, missing = self.hub, [], []
        me = me or self.reader()
        for p in hub.members("person"):
            if p["status"] != "active" or p["id"] == leaving:
                continue
            if me and p["id"] == me["id"] and m.get("sponsor") != p["id"]:
                missing.append((p, "you may not name yourself; another member admin does"))
                continue
            beyond = hub.reach_beyond(p, m["id"])
            if beyond:
                missing.append((p, f"does not reach what it does ({', '.join(beyond)})"))
            else:
                able.append(p)
        return able, missing

    def _successor_roles(self, d: dict) -> str:
        """What someone needs to sponsor this agent or service, in words: what its roles reach of restricted
        information, and the roles of the practice that give each of it."""
        hub, rclasses = self.hub, self.hub.restricted_classes()
        needs: dict[tuple[str, str], set[str]] = {}
        for obj, rel, sub in hub.authz.read():
            if (sub.startswith("role:") and sub.endswith("#assignee") and rel in ("reader", "writer")
                    and obj.startswith("data_class:") and obj[11:] in rclasses):
                needs.setdefault((rel, obj[11:]), set()).add(sub.removeprefix("role:").removesuffix("#assignee"))
        mine = set(d.get("roles") or [])
        out = []
        for (rel, dc), roles in sorted(needs.items(), key=lambda kv: kv[0][1]):
            if roles & mine:
                givers = sorted(roles - mine) or sorted(roles)
                out.append(f'{"to read" if rel == "reader" else "to write"} {self._class_name(dc)} '
                           f'({" or ".join(self._role_words(r) for r in givers)})')
        return "; ".join(out)

    def _account_history(self, m: dict) -> str:
        """What a second policy owner weighs before approving roles: who holds the credential (how the passphrase
        was set, and with whose code), roles taken away lately, and the sessions open now. All from the track and the
        credentials store."""
        if m.get("kind") != "person":
            return ""
        hub, mid, lines = self.hub, m["id"], []
        row = hub.db.passphrases.get(mid)
        if not row:
            lines.append("No passphrase yet.")
        else:
            when = self._at(row.get("set_at", ""))
            if row.get("issued_by") and row["issued_by"] != mid:
                lines.append(f'Passphrase set {when} with a setup code issued by {self.who(row["issued_by"])}.')
            else:
                lines.append(f"Passphrase set {when}.")
        since = (datetime.now(timezone.utc) - timedelta(days=90)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        removed = [ev for ev in hub.db.events.find({"subject": member_ref(mid), "action": "member.roles_changed",
                                                    "at": {"$gte": since}}, sort=[("_id", -1)], limit=20)
                   if (ev.get("data") or {}).get("removed")]
        for ev in removed[:5]:
            lines.append(f'{e(self._role_words(ev["data"]["removed"]))} taken away {self._at(ev["at"])} by '
                         f'{self.who(ev["actor"])}.')
        if not removed:
            lines.append("No roles taken away in the last 90 days.")
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        n = hub.db.sessions.count({"member": mid, "revoked": 0, "expires_at": {"$gt": now}})
        lines.append(f'{n} open session{"s" if n != 1 else ""} now.' if n else "No open sessions now.")
        return ('<div class="account-history"><p class="muted">Their account:</p><ul class="plain">'
                + "".join(f"<li>{x}</li>" for x in lines) + "</ul></div>")

    def _profile_forms(self, me, m) -> list[str]:
        """An agent's profile: the version in force, a proposed one waiting for a second person, the history."""
        hub, mid, out = self.hub, m["id"], []
        admin = hub.can(me, "member_admin", "practice:main")
        prof = {"max_risk": "low", "skills": [], **m["profile"]}
        prop = hub.agent_profile_proposal(mid)
        if prop:
            new = prop["profile"]
            rows = []
            if new.get("model", "") != prof.get("model", ""):
                rows.append(("Model", e(prof.get("model", "") or "—"), e(new.get("model", "") or "—")))
            if new.get("max_risk") != prof.get("max_risk"):
                rows.append(("Highest risk", e(prof["max_risk"].capitalize()), e(new["max_risk"].capitalize())))
            if bool(new.get("direct_writes")) != bool(prof.get("direct_writes")):
                words = lambda on: "Yes: by hand, outside its work" if on else "No: only through its work"
                rows.append(("Changes records directly", words(prof.get("direct_writes")),
                             words(new.get("direct_writes"))))
            if sorted(new.get("skills", [])) != sorted(prof["skills"]):
                def skills(xs, other):  # the whole list, with what the change adds or takes away marked
                    return ", ".join(f'<b>{e(self.human(x))}</b>' if x not in other else e(self.human(x))
                                     for x in xs) or "none"
                rows.append(("Skills", skills(prof["skills"], new.get("skills", [])),
                             skills(new.get("skills", []), prof["skills"])))
            table = "".join(f'<tr><th scope="row">{k}</th><td>{a}</td><td>{b}</td></tr>' for k, a, b in rows)
            btns = ""
            if hub.is_policy_owner(me) and me["kind"] == "person" and me["id"] != prop["author"]:
                btns += (f'<form method="post" action="/team/{e(mid)}/approve-profile" class="inline"><input '
                         f'type="hidden" name="version" value="{prop["version"]}"><button>Approve version '
                         f'{prop["version"]}</button></form>')
            if me["id"] == prop["author"] or hub.is_policy_owner(me):
                btns += (f'<form method="post" action="/team/{e(mid)}/withdraw-profile" class="inline"><input '
                         f'type="hidden" name="version" value="{prop["version"]}"><button class="secondary">'
                         f'{"Withdraw" if me["id"] == prop["author"] else "Decline"}</button></form>')
            out.append(f'<div class="card" id="proposed-profile"><p><b>Waiting for a second policy owner:</b> '
                       f'{self.member_name(prop["author"])} proposed version {prop["version"]} of what {e(m["name"])} '
                       f'may do{": " + e(prop["note"]) if prop["note"] else ""}. Nothing changes until it is approved.'
                       f'</p><div class="tablewrap"><table><thead><tr><th scope="col"></th><th scope="col">In force'
                       f'</th><th scope="col">Proposed</th></tr></thead><tbody>{table}</tbody></table></div>'
                       f'<div class="actions">{btns}</div></div>')
        if admin or m["sponsor"] == me["id"]:
            risks = "".join(f'<label class="radio"><input type="radio" class="opt-input" name="max_risk" value="{r}"'
                            f'{" checked" if prof.get("max_risk", "low") == r else ""}> {r.capitalize()}</label>'
                            for r in ("low", "medium", "high"))
            skills = sorted(set(hub.pack.all_skills()) if hub.pack else set())
            boxes = "".join(f'<label class="radio multi"><input type="checkbox" class="opt-input" name="skills" value="{e(k)}"'
                            f'{" checked" if k in prof.get("skills", []) else ""}> {e(self.human(k))}</label>'
                            for k in skills)
            out.append(f'<h2>What it may do</h2><form method="post" action="/team/{e(mid)}/profile" class="card">'
                       f'<p class="muted">A change that adds (a higher risk, a skill, another model) is proposed, '
                       f'not made: it takes effect when a policy owner other than you approves it. Lowering the risk, '
                       f'taking skills away or turning direct changes off, and nothing else, takes effect at once. Every version is kept, on '
                       f'the track.</p>'
                       f'<label>Model<input name="model" value="{e(prof.get("model", ""))}"></label>'
                       f'<fieldset class="choices options"><legend>Highest risk it may take</legend>{risks}</fieldset>'
                       f'<fieldset class="choices options"><legend>Skills</legend>{boxes}</fieldset>'
                       f'<fieldset class="choices options"><legend>Records</legend><label class="radio multi">'
                       f'<input type="checkbox" class="opt-input" name="direct_writes" value="1"'
                       f'{" checked" if prof.get("direct_writes") else ""}> It may change records directly, '
                       f'outside the work it is given</label><p class="muted">Left off, the agent changes records '
                       f'only by answering the steps it is given: the workflow writes what it answers, within its '
                       f'skills, its highest risk and its sponsor\'s hold. Turned on, it may also create, change '
                       f'and move the records its roles may write, by the API or a tool, where none of those apply.'
                       f'</p></fieldset>'
                       f'<label>Why (for the reviewer)<input name="note" placeholder="what the change is for"></label>'
                       f'<button>Propose the change</button></form>')
        versions = hub.agent_profile_versions(mid)
        if versions:
            rows = "".join(
                f'<tr><td>v{v["version"]}</td><td><span class="badge {e(v["status"])}">{e(v["status"])}</span></td>'
                f'<td>{e(v["profile"].get("model", ""))} · {e(v["profile"].get("max_risk", "low"))} · '
                f'{e(", ".join(self.human(x) for x in v["profile"].get("skills", [])))}'
                f'{" · changes records directly" if v["profile"].get("direct_writes") else ""}</td>'
                f'<td>{self.who(v["author"]) if hub.db.members.get(v["author"]) else e(v["note"])}</td>'
                f'<td>{self.who(v["approved_by"]) if v["approved_by"] else ""}</td></tr>' for v in versions)
            out.append(f'<details><summary>Profile versions ({len(versions)})</summary><div class="tablewrap"><table>'
                       f'<thead><tr><th scope="col"></th><th scope="col">Status</th><th scope="col">Profile</th>'
                       f'<th scope="col">Proposed by</th><th scope="col">Approved by</th></tr></thead><tbody>{rows}'
                       f'</tbody></table></div></details>')
        return out

    def _member_admin_forms(self, me, m) -> list[str]:
        hub, mid, out = self.hub, m["id"], []
        admin = hub.can(me, "member_admin", "practice:main")
        owner_path = not admin and me["kind"] == "person" and hub.is_policy_owner(me)  # two policy owners together
        pending = hub.role_request(mid)
        if pending:
            names = {r["id"]: r["name"] for r in hub.roles()}
            asked = ", ".join(e(names.get(r, r)) for r in pending["roles"]) or "no roles"
            btns = ""
            if hub.is_policy_owner(me) and me["id"] not in {pending["by"], mid, *pending.get("asked_by", [])}:
                btns += (f'<form method="post" action="/team/{e(mid)}/approve-roles" class="inline">'
                         f'<button>Approve these roles</button></form>')
            if admin or hub.is_policy_owner(me):
                btns += (f'<form method="post" action="/team/{e(mid)}/withdraw-roles" class="inline">'
                         f'<button class="secondary">Withdraw the request</button></form>')
            out.append(f'<div class="card"><p><b>Waiting for a second policy owner:</b> {self.member_name(pending["by"])} '
                       f'asked that {e(m["name"])} hold {asked}.</p>{self._account_history(m)}{btns}</div>')
        if (admin or owner_path) and mid != me["id"] and m["status"] == "active":
            note = ('<p class="muted">You do not manage members, so any change you ask for waits for a second policy '
                    'owner: two policy owners together can give someone a role that manages members.</p>'
                    if owner_path else "")
            out.append(f'<h2>Roles</h2><form method="post" action="/team/{e(mid)}/roles" class="card">{note}'
                       f'<fieldset class="choices options"><legend>Roles {e(m["name"])} holds</legend>'
                       f'{self._role_boxes(m["roles"], pending["roles"] if pending else (), reasons=self._role_reasons(me, m["kind"], hub.db.members.get(m["sponsor"] or "") if m["kind"] != "person" else None))}</fieldset>'
                       f'<button>{"Ask for these roles" if owner_path else "Save roles"}</button></form>')
        owner = me.get("kind") == "person" and hub.is_policy_owner(me)
        if (admin or owner) and m["kind"] == "person" and mid != me["id"] and m["status"] == "active":
            has = hub.has_passphrase(mid)
            said = (f'<p>{e(m["name"])} {"has set a passphrase" if has else "has no passphrase yet"}. A one-time '
                    f'setup code lets them set {"a new" if has else "one"}{" (if they forgot it)" if has else ""}; '
                    f'an earlier unused code stops working.</p>')
            with hub.reach_memo():  # the very check the action makes, so the card never offers what is refused
                refusal = hub.setup_code_refusal(me, mid)
            if refusal and refusal["reach"]:  # whoever issues it can sign in as them: someone who reaches as far
                beyond = set(m["roles"]) & hub.beyond_roles(me)
                what = self._role_reach(beyond, me)
                words = "; ".join(f'as {self._role_words(r)} they {what[r]}' for r in sorted(beyond) if r in what)
                if refusal["beyond"]:
                    why = (f'{e(m["name"])} reaches information you do not'
                           f'{" (" + e(words) + ")" if words else ""}, so someone who does issues their setup code.')
                else:
                    why = (f'{e(m["name"])} approves changes or manages members, so a member admin issues their '
                           f'setup code.')
                if refusal["who"]:
                    who = ", ".join(e(n) for n in refusal["who"])
                    todo = f'Ask {who}: they may issue it.'
                else:
                    todo = (f'Nobody on the team may issue it, so the server holder (whoever runs the hub\'s '
                            f'computer) issues it there with <code>{e(hub.holder_setup_command(mid))}</code> and '
                            f'gives it to {e(m["name"])} in person or by phone.')
                out.append(f'<h2>Passphrase</h2><div class="card">{said}<p class="muted">{why}</p>'
                           f'<p>{todo}</p></div>')
            elif not refusal:
                out.append(f'<h2>Passphrase</h2><form method="post" action="/team/{e(mid)}/setup-code" class="card">'
                           f'{said}<p class="muted">The code is for {e(m["name"])} alone. Once they use it, their '
                           f'other sessions end and their old passphrase stops working. Issuing it goes on the track, '
                           f'with your name.</p><button class="secondary">Issue a setup code</button></form>')
        if admin and m["kind"] == "service":
            chans = hub.channels_of(m)
            out.append(f'<h2>Delivers on</h2><div class="card"><p>{e(", ".join(chans) or "No outbox channel")}. A '
                       f'member admin grants or revokes one with <code>practicehub member channel {e(mid)} '
                       f'&lt;channel&gt; [--revoke] --as &lt;member admin&gt;</code>.</p></div>')
        if admin and m["kind"] != "person":
            able, missing = self._sponsor_candidates(m, me)
            opts = "".join(f'<option value="{e(p["id"])}"{" selected" if p["id"] == m["sponsor"] else ""}>'
                           f'{e(p["name"])}</option>' for p in able)
            if m["sponsor"] and m["sponsor"] not in {p["id"] for p in able}:
                opts = (f'<option value="" selected disabled>{e(self.member_name(m["sponsor"]))} (no longer reaches '
                        f'as far)</option>' + opts)
            left_out = ("" if not missing else '<details><summary>Not offered ' f'({len(missing)})</summary>'
                        '<ul class="plain">' + "".join(f'<li>{e(p["name"])}: {e(why)}</li>' for p, why in missing)
                        + '</ul></details>')
            out.append(f'<h2>Sponsor</h2><form method="post" action="/team/{e(mid)}/sponsor" class="card">'
                       f'<label>The person accountable for {e(m["name"])}<select name="sponsor" required>'
                       f'{opts}</select></label>'
                       f'<small>Only people who reach everything {e(m["name"])} reaches may sponsor it. Their open '
                       f'approvals and notices about it move to the new sponsor.</small>{left_out}'
                       f'<button>Change the sponsor</button></form>')
        if m["kind"] == "agent":
            out += self._profile_forms(me, m)
        try:
            creds = hub.tokens(me, mid)
        except Denied:
            creds = []
        live = [c for c in creds if not c["revoked"]]
        if live:
            rows = "".join(f'<tr><td>{e(c["label"] or "—")}</td><td class="muted">{self._at(c["created_at"])}</td>'
                           f'<td>{self.who(c["issued_by"]) if c["issued_by"] else ""}</td><td><form method="post" '
                           f'action="/team/{e(mid)}/revoke" class="inline"><input type="hidden" name="credential" '
                           f'value="{e(c["id"])}"><button class="danger" data-confirm="Revoke this credential? Anything '
                           f'using it stops working.">Revoke</button></form></td></tr>' for c in live)
            out.append(f"<h2>Credentials</h2><div class='tablewrap'><table><thead><tr><th scope='col'>For</th>"
                       f"<th scope='col'>Issued</th><th scope='col'>By</th><th scope='col'></th></tr></thead>"
                       f"<tbody>{rows}</tbody></table></div>")
        return out

    def post_member(self, req, mid, verb):
        f, me, hub = self.form(req), req["me"], self.hub
        if verb == "token":
            tok = hub.issue_token(me, mid, f.get("label", ""))
            return self.page_member(req, mid, new_token=tok)
        if verb == "setup-code":
            return self.page_member(req, mid, setup=hub.issue_setup_code(me, mid))
        if verb in ("roles", "approve-roles", "withdraw-roles", "sponsor", "profile", "approve-profile",
                    "withdraw-profile", "ask-disable", "withdraw-disable", "revoke"):
            many = lambda k: f.get(k, []) if isinstance(f.get(k), list) else ([f[k]] if f.get(k) else [])
            if verb == "roles":
                hub.set_roles(me, mid, many("roles"))
            elif verb == "approve-roles":
                hub.approve_roles(me, mid)
            elif verb == "withdraw-roles":
                hub.withdraw_roles(me, mid)
            elif verb == "sponsor":
                hub.set_sponsor(me, mid, str(f.get("sponsor", "")))
            elif verb == "profile":
                hub.propose_agent_profile(me, mid, {"model": str(f.get("model", "")), "max_risk": f.get("max_risk"),
                                                    "skills": many("skills"),
                                                    "direct_writes": bool(f.get("direct_writes"))},
                                          str(f.get("note", "")))
            elif verb == "approve-profile":
                hub.approve_agent_profile(me, mid, int(f.get("version") or 0))
            elif verb == "withdraw-profile":
                hub.withdraw_agent_profile(me, mid, int(f.get("version") or 0), str(f.get("reason", "")))
            elif verb == "ask-disable":
                hand, stop = self._fates(f)
                hub.request_switch_off(me, mid, str(f.get("reason", "")), hand_over=hand, stop=stop)
            elif verb == "withdraw-disable":
                hub.withdraw_switch_off(me, mid)
            else:
                hub.revoke_token(me, mid, str(f.get("credential", "")))
            raise Redirect(f"/team/{mid}")
        if verb == "approve-disable":
            out = hub.approve_switch_off(me, mid)
        else:
            hand, stop = self._fates(f)
            out = hub.set_status(me, mid, "active" if verb == "enable" else "disabled", f.get("reason", ""),
                                 hand_over=hand, stop=stop)
        if out.get("roles_left_empty"):
            return self.page_member(req, mid, empty=out["roles_left_empty"])
        raise Redirect(f"/team/{mid}")

    # ------------------------------------------------------------------------------------------ track, outbox, schedule
    def page_track(self, req):
        if not self.hub.is_policy_owner(req["me"]):  # who opened what is itself personal health information
            raise Denied("the track is read by a policy owner (the privacy officer)")
        v = self.hub.verify(full=False)  # the events since the last verify; 'practicehub verify' checks them all
        checked = (f' This view checked the events after {v["from"]}; the ones before were checked earlier '
                   f'(<code>practicehub verify</code> checks them all).' if v.get("from") else "")
        banner = (f'<div class="ok">The track verifies: {v["events"]} events, each chained to the one before, every '
                  f'signature valid.{checked}</div>' if v["ok"] else
                  '<div class="error"><p>The track does not verify:</p><ul>'
                  + "".join(f"<li>{e(p)}</li>" for p in v["problems"]) + "</ul></div>")
        q = req["query"]
        actor = q.get("actor")
        flt: dict = {"actor": actor} if actor else {}
        before = int(q["before"]) if str(q.get("before") or "").isdigit() else None
        if before:
            flt["seq"] = {"$lt": before}
        page = self.hub.db.events.find(flt, sort=[("seq", -1)], limit=self.TRACK_PAGE + 1)  # one page, by index
        more, page = len(page) > self.TRACK_PAGE, page[:self.TRACK_PAGE]
        nav = []
        if before:
            nav.append(f'<a href="/track{"?actor=" + urllib.parse.quote(actor) if actor else ""}">Newest</a>')
        if more:
            nav.append(f'<a href="/track?{"actor=" + urllib.parse.quote(actor) + "&" if actor else ""}'
                       f'before={page[-1]["seq"]}">Older events</a>')
        nav_html = f'<p class="muted">{" · ".join(nav)}</p>' if nav else ""
        return self.layout(req, "Track", banner + "<p class='muted'>Every action, by whom, and what caused it. Open an "
                           "event to see its whole chain of causes. <a href='/access'>Who accessed a file</a></p>"
                           + self._events(page, req["me"]) + nav_html)

    TRACK_PAGE = 100  # events on one page of the track

    def page_access(self, req):
        """Who saw or changed a subject's file: the privacy officer's report, on screen or as CSV."""
        q, me = req["query"], req["me"]
        subject = q.get("subject", "")
        st = self.subj["type"]
        owner = self.hub.is_policy_owner(me)
        recent = self.hub.records.search(st, limit=SHOWN) if owner else []  # suggestions; any file is found by typing
        named = {f'{self.hub.title(r)} · {r["id"]}': f'{st}/{r["id"]}' for r in recent}
        typed = subject.strip()
        picked = ""
        if typed and "/" not in typed and owner:
            fld = {"label": self.subj["label"], "target": [st]}
            try:
                subject, picked = self._resolve_ref(fld, typed), typed
            except Invalid as exc:
                subject, picked, msg = "", typed, str(exc).split(": ", 1)[-1].capitalize() + "."
        elif subject:
            rec = owner and "/" in subject and self.hub.records.read(*subject.split("/", 1))
            picked = f"{self.hub.title(rec)} · {rec['id']}" if rec else subject
        opts = "".join(f'<option value="{e(n)}"></option>' for n in named)
        form = (f'<form method="get" action="/access" class="card"><label>{e(self.subj["label"])}'
                f'<input name="subject" value="{e(picked)}" list="subjects" required autocomplete="off" '
                f'placeholder="start typing a name"><small>Pick from the list, or type a name</small></label>'
                f'<datalist id="subjects">{opts}</datalist><button>Report</button></form>')
        if typed and not subject:
            form += f'<p class="flash error">{e(msg)}</p>'
            return self.layout(req, "Access report", form, crumbs=[("/track", "Track"), (None, "Access report")])
        if not subject:
            if not self.hub.is_policy_owner(me):
                raise Denied("the privacy officer (a policy owner) reports who accessed a file")
            return self.layout(req, "Access report", self._unreviewed_html() + form,
                               crumbs=[("/track", "Track"), (None, "Access report")])
        rows = self.hub.access_report(me, subject)
        if q.get("format") == "csv":
            import csv
            import io
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["when", "who", "name", "kind", "action", "record", "how", "event"])
            from ..export import csv_cell as cell  # a spreadsheet must not read a name as a formula
            for r in rows:
                w.writerow([cell(r[k]) for k in ("at", "who", "name", "kind", "action", "record", "how", "seq")])
            name = re.sub(r"[^\w.-]", "_", subject)
            return ({"Content-Type": "text/csv; charset=utf-8",
                     "Content-Disposition": f'attachment; filename="access-{name}.csv"'}, buf.getvalue().encode())
        what = {"record.viewed": "Opened", "exported": "Exported", "created": "Created", "updated": "Changed", "transitioned": "Moved on",
                "signed": "Signed"}
        trs = "".join(f'<tr><td class="muted">{self._at(r["at"])}</td><td>{self.who(r["who"])}</td>'
                      f'<td>{e(what.get(r["action"].split(".")[-1], what.get(r["action"], self.human(r["action"]))))}'
                      f'</td><td>{self.record_link(r["record"]) if r["record"] else ""}</td><td>{self._how(r["how"])}</td>'
                      f'<td><a href="/track/{r["seq"]}">#{r["seq"]}</a></td></tr>' for r in rows)
        table = ("<div class='tablewrap'><table><thead><tr><th scope='col'>When</th><th scope='col'>Who</th>"
                 "<th scope='col'>What</th><th scope='col'>Record</th><th scope='col'>How</th>"
                 f"<th scope='col'>Event</th></tr></thead><tbody>{trs}</tbody></table></div>" if rows
                 else "<p class='muted'>No one has opened or changed this file.</p>")
        dl = (f'<p><a class="btn secondary" href="/access?subject={urllib.parse.quote(subject)}&amp;format=csv">'
              f'Download CSV</a> <span class="muted">{len(rows)} events across every record in the file, people '
              f'and agents alike. Running this report is itself recorded.</span></p>')
        try:
            whose = self.hub.records.read(*subject.split("/", 1)) if "/" in subject else None
        except Exception:  # noqa: BLE001
            whose = None
        head = f"<h2>{e(self.hub.short_title(whose))}</h2>" if whose else ""
        return self.layout(req, "Access report", form + head + dl + table,
                           crumbs=[("/track", "Track"), (None, "Access report")])

    def _unreviewed_html(self, days: int = 90) -> str:
        """Emergency access that nobody could review in full (``access.unreviewed``: the file's directive keeps it
        from every reviewer): who opened which file and when, and who was told without the reason. For policy
        owners, at the top of the access report, so it is read and not only written."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z")
        evs = self.hub.db.events.find({"action": "access.unreviewed", "at": {"$gte": since}}, sort=[("_id", -1)],
                                      limit=50)
        if not evs:
            return ""
        rows = []
        for ev in evs:
            data = ev.get("data") or {}
            told = [t for t in data.get("told") or [] if t]
            subj = data.get("subject") or ""
            rows.append(f'<tr><td class="muted">{self._at(ev["at"])}</td><td>{self.who(ev["actor"])}</td>'
                        f'<td>{self.record_link(subj) if subj else ""}</td>'
                        f'<td>{", ".join(self.who(t) for t in told) + " (without the reason)" if told else "Nobody"}'
                        f'</td><td><a href="/track/{ev["seq"]}">#{ev["seq"]}</a></td></tr>')
        return (f'<div class="warn" role="status"><p><b>Emergency access nobody could review in full '
                f'({len(evs)} in the last {days} days).</b> The file\'s directive kept it from every reviewer, so '
                f'nobody was shown why it was opened. Ask the person who opened it; the server holder can read the '
                f'reason on the track.</p></div><div class="tablewrap"><table><thead><tr><th scope="col">When</th>'
                f'<th scope="col">Who opened it</th><th scope="col">File</th><th scope="col">Told</th>'
                f'<th scope="col">Event</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')

    def _how(self, how) -> str:
        """How a file was reached, in words: a work item by its title and a link, not its id."""
        m = re.fullmatch(r"work item (item-[\w-]+)", str(how or ""))
        if m:
            row = self.hub.db.items.find_one({"_id": m.group(1)}, fields=["title"])
            return (f'In the work item <a href="/items/{e(m.group(1))}">{e(row["title"] if row else m.group(1))}</a>')
        return e(self.human(how)) if how else ""

    def page_chain(self, req, seq):
        chain = self.hub.track.chain(int(seq))
        if chain and chain[-1]["action"] in Viewer.ACCESS_EVENTS and not self.hub.is_policy_owner(req["me"]):
            chain = []
        if not chain:
            raise NotFound(f"there is no event {seq}")
        last = chain[-1]
        sig = ""
        att = (last.get("data") or {}).get("attested")
        if att and not last.get("signature"):
            sig = (f'<div class="muted">Decided by {self.who(att.get("agent") or last["actor"])} (an agent) at '
                   f'{e(last["at"])}: “{e(str(att.get("meaning", "")))}”. This is the agent\'s attested decision, '
                   f'not a signature: agents never sign.</div>')
        if last.get("signature"):
            okay = self.hub.track.verify_signature(last)
            signed = last["data"].get("signed", {})
            sig = (f'<div class="{"ok" if okay else "error"}">Signed by {self.who(last["actor"])} at {e(last["at"])}: '
                   f'“{e(str(signed.get("meaning", "")))}” — signature {"valid" if okay else "INVALID"}</div>')
        return self.layout(req, f"Event #{seq} and what caused it",
                           sig + "<p class='muted'>From the first cause down to this event.</p>" + self._events(chain, req["me"]))

    def _class_name(self, cid: str) -> str:
        dcs = self.hub.pack.all_data_classes() if self.hub.pack else []
        return next((d.get("name") or cid for d in dcs if d["id"] == cid), cid).lower()

    def _may_open_outbox(self, me) -> bool:
        return self.hub.is_policy_owner(me) or self.hub.can(me, "reader", "data_class:correspondence")

    def _message_about(self, r) -> str | None:
        """Whose message it is (a patient's record), for the record of who saw their information."""
        return self.hub.message_about(r)

    def _message_kept(self, me, r) -> str | None:
        """The words shown in place of a message a subject's directive keeps from the reader: neither its subject
        line nor its body, both of which may quote the file."""
        if not self.hub.message_restricted(me, r):
            return None
        return f'<span class="muted">{e(self.hub.message_kept_note(r))}.</span>'

    OUTBOX_STATUS = {"queued": "Waiting for the connector", "sending": "Being sent", "sent": "Sent",
                     "failed": "Failed", "delivered": "Delivered by hand",
                     "withdrawn": "Withdrawn (a sign-in code, used or expired)"}

    def _message_status(self, r) -> str:
        cls = {"sent": "done", "delivered": "done", "withdrawn": "done", "failed": "overdue"}.get(r["status"], "due")
        out = f'<span class="badge {cls}">{e(self.OUTBOX_STATUS.get(r["status"], r["status"]))}</span>'
        if r["status"] in ("queued", "sending") and r.get("escalated"):
            out += ' <span class="badge overdue">Late</span>'
        by = r.get("done_by") or (r.get("claimed_by") if r["status"] == "sending" else None)
        if by:
            out += f'<div class="muted">{e(self.member_name(by))}{" · " + self._at(r["done_at"]) if r.get("done_at") else ""}</div>'
        return out

    def _deliver_form(self, me, r) -> str:
        if not self.hub.may_deliver_by_hand(me, r):
            return ""
        return (f'<form method="post" action="/outbox/{r["id"]}/delivered" class="inline deliver">'
                f'<input name="note" required maxlength="2000" placeholder="how: who you reached, and when" '
                f'aria-label="How it was delivered"><button class="secondary">Mark delivered by hand</button></form>')

    def _message_card(self, me, oid) -> str:
        """The message a notice is about, with the way to mark it delivered by hand (for whoever reads it)."""
        try:
            r = self.hub._message(oid)
        except NotFound:
            return '<div class="card"><p class="muted">The message is gone.</p></div>'
        readable = self.hub.may_read_message(me, r)
        if readable and self._message_about(r):
            self.hub.note_view(me, self._message_about(r), how=f"outbox message {r['id']}")
        kept = self._message_kept(me, r)
        words = (f'<p><b>{e(r["subject"])}</b></p><p>{e(r["body"])}</p>' if readable else
                 f'<p>The message is not shown. {kept}</p>' if kept else
                 '<p class="muted">The message is not shown: it holds information you do not read.</p>')
        why = f'<p class="muted">{e(r["note"])}</p>' if r.get("note") and not kept else ""
        return (f'<div class="card"><p>{e(r["channel"].capitalize())} message #{r["id"]} to {e(r["recipient"])}: '
                f'{self._message_status(r)}</p>{words}{why}{self._deliver_form(me, r)}'
                f'<p class="muted"><a href="/outbox#msg-{r["id"]}">In the outbox</a></p></div>')

    def page_outbox(self, req):
        me = req["me"]
        if not self._may_open_outbox(me):
            raise Denied(f"the outbox holds messages to {self.subj['plural'].lower()}: it is read by members who read "
                         f"correspondence")
        rows = self.hub.db.outbox.find({}, sort=[("id", -1)], limit=200)
        body = []
        for r in rows:
            classes = self.hub.message_classes_of(r)
            readable = self.hub.may_read_message(me, r)
            about = self._message_about(r)
            if readable and about:
                self.hub.note_view(me, about, how="outbox")
            kept = self._message_kept(me, r)
            words = (f'<b>{e(r["subject"])}</b><div>{e(r["body"])}</div>' if readable else
                     f'<div>Not shown. {kept}</div>' if kept else
                     f'<b>{e(r["subject"])}</b><div class="muted">Not shown: it holds '
                     f'{e(", ".join(self._class_name(c) for c in classes if not self.hub.can(me, "reader", f"data_class:{c}")))} '
                     f'information you do not read.</div>')
            note = (f'<div class="muted">{"Why" if r["status"] == "failed" else "Note"}: {e(r["note"])}</div>'
                    if r.get("note") and readable else "")
            body.append(f'<tr id="msg-{r["id"]}"><td class="muted">{self._at(r["at"])}</td>'
                        f'<td>{e(r["channel"])}</td><td>{e(r["recipient"])}</td><td>{words}</td>'
                        f'<td>{"<a href=" + e(self.run_href(r["run_id"])) + ">the work</a>" if r["run_id"] else ""}</td>'
                        f'<td>{self._message_status(r)}{note}{self._deliver_form(me, r)}</td></tr>')
        body = "".join(body)
        return self.layout(req, "Outbox", "<p class='muted'>Messages the practice has queued to send (portal, fax, "
                           "pages). A delivery connector sends each one and reports it sent or failed; one still waiting "
                           "past its channel's deadline is escalated, and a page is given to a person to phone. Whoever "
                           "reads a message may mark it delivered by hand, saying how.</p>"
                           f"<table><tr><th>When</th><th>Channel</th><th>To</th><th>Message</th><th></th>"
                           f"<th>Status</th></tr>{body}</table>")

    def post_outbox_delivered(self, req, oid):
        me = req["me"]
        if not self._may_open_outbox(me):
            raise Denied("the outbox is read by members who read correspondence")
        self.hub.outbox_delivered_by_hand(me, oid, str(self.form(req).get("note", "")))
        raise Redirect(f"/outbox#msg-{int(oid)}")

    # ---- the delivery connector's API
    def api_outbox(self, req):
        me = req["me"]
        if me["kind"] != "service":
            raise Denied("the outbox API is for delivery connectors (services); people use the outbox page")
        return {"channels": self.hub.channels_of(me), "messages": self.hub.outbox_pending(me)}

    def api_outbox_verb(self, req, oid, verb):
        me = req["me"]
        if me["kind"] != "service":
            raise Denied("the outbox API is for delivery connectors (services); people use the outbox page")
        d = self.body_json(req) if req["body"] else {}
        if verb == "claim":
            return self.hub.outbox_claim(me, oid)
        if verb == "sent":
            return self.hub.outbox_sent(me, oid, str(d.get("reference", "") or ""))
        return self.hub.outbox_failed(me, oid, str(d.get("reason", "") or ""))

NEW_BLOCKS = {
    "python": """id = "my-block"
title = "My block"
runtime = "python"
summary = "What it decides, in a sentence."

code = '''
def run(input):
    total = input["amount"] * (1 + input["rate"] / 100)
    return {"total": round(total, 2)}
'''

[[input]]
name = "amount"
type = "number"
required = true

[[input]]
name = "rate"
type = "number"
default = 15
help = "percent"

[[output]]
name = "total"
type = "number"
required = true
""",
    "prompt": """id = "my-prompt"
title = "My prompt"
runtime = "prompt"
summary = "What the agent does, in a sentence."

prompt = \"\"\"
Summarise {{input.text}} in a {{input.style}} way, for the person who reviews it next.
\"\"\"

[[input]]
name = "text"
type = "text"
required = true

[[input]]
name = "style"
type = "choice"
choices = ["short", "detailed"]
default = "short"

[[output]]
name = "summary"
type = "text"
required = true
""",
    "external": """id = "my-engine-step"
title = "My engine step"
runtime = "external"
engine = "webhook"
target = "my-flow"
summary = "What the engine does, in a sentence."

[[input]]
name = "reference"
type = "string"
required = true

[[output]]
name = "result"
type = "string"
required = true
""",
}

NEW_WORKFLOW = '''id = "my-workflow"
title = "My workflow"
record = "SUBJECT"
summary = """What this workflow is for, in a sentence or two."""

[trigger]
starters = ["front-desk"]

[[input]]
name = "reason"
type = "string"
required = true

[start]
to = "doing"

[[state]]
id = "doing"
label = "Being done"
[[state.work]]
id = "do-it"
title = "Do the work"
kind = "task"
role = "front-desk"
context = ["record:demographics", "input.reason"]
[[state.work.fields]]
name = "notes"
type = "text"
required = true

[[edge]]
id = "done"
from = "doing"
to = "approving"
label = "Done"

[[state]]
id = "approving"
label = "Waiting for approval"
[[state.work]]
id = "approve"
title = "Approve"
kind = "gate"
role = "practice-manager"
signer = "person"
meaning = "I approve this"

[[edge]]
id = "approved"
from = "approving"
to = "closed"
label = "Approve"

[[edge]]
id = "rejected"
from = "approving"
to = "doing"
label = "Send it back"

[[state]]
id = "closed"
label = "Closed"
'''



# ================================================================================================== server
def make_handler(app, only: str | None = None, *, max_body: int = MAX_BODY, timeout: float | None = 60,
                 client=client_address):
    """``only``: a path prefix this listener serves, and nothing else (the WOPI listener Collabora reaches).
    ``max_body``: the largest request, refused before it is read. ``timeout``: seconds a connection may sit idle (a
    slow client holds no thread for ever). ``client``: where a request came from, from its peer and headers."""
    class Handler(BaseHTTPRequestHandler):
        server_version = "PracticeHub"

        def setup(self):
            self.timeout = timeout
            super().setup()

        def _chunked(self) -> bytes | None:
            out = b""
            while True:
                line = self.rfile.readline(64)
                try:
                    size = int(line.split(b";", 1)[0].strip(), 16)
                except ValueError:
                    return None
                if size == 0:
                    while self.rfile.readline(1024) not in (b"\r\n", b"\n", b""):
                        pass
                    return out
                if len(out) + size > max_body:
                    return None
                out += self.rfile.read(size)
                self.rfile.readline(4)

        def _do(self):
            if only and not self.path.startswith(only):
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            chunked = "chunked" in (self.headers.get("Transfer-Encoding") or "").lower()
            body = self._chunked() if chunked else None
            if chunked and body is None:
                n = max_body + 1
            if n < 0 or n > max_body:  # refused before it is read: a request is never larger than a file and a form
                self.send_response(413 if n > 0 else 400)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                return
            if not chunked:
                body = self.rfile.read(n) if n else b""
            headers = {k.lower(): v for k, v in self.headers.items()}
            headers[CLIENT_HEADER] = client(self.client_address[0], headers)  # set here, never by the client
            status, h, payload = app.handle(self.command, self.path, headers, body)
            self.send_response(status)
            for k, v in h.items():
                for one in (v if isinstance(v, list) else [v]):  # Set-Cookie may be several: one header each
                    self.send_header(k, one)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_PATCH = _do

        def log_message(self, fmt, *args):  # no request lines with tokens or record ids in logs
            pass

    return Handler


def serve(hub, host: str = "127.0.0.1", port: int = 8200, *, dev: bool = False, oidc=None,
          wopi_hosts: tuple[str, ...] = ()) -> None:
    """``wopi_hosts``: more addresses to listen on, on the same port, for Collabora's WOPI calls only (a docker
    bridge address, say): nothing else, dev sign-in included, is served there."""
    if port in FORBIDDEN_PORTS:
        raise SystemExit(f"port {port} is reserved on this machine; pick another (default 8200)")
    app = App(hub, dev=dev, oidc=oidc)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    for extra in wopi_hosts:
        side = ThreadingHTTPServer((extra, port), make_handler(app, only="/wopi/"))
        threading.Thread(target=side.serve_forever, daemon=True).start()
        print(f"WOPI (Collabora) on http://{extra}:{port}/wopi/")
    from ..live import live_of
    live_of(hub).start_sweeper()  # saves idle live sessions and lets go of editors who closed their tab
    print(f"Practice Hub on http://{host}:{port}/" + (" (dev sign-in on)" if dev else ""))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


# ------------------------------------------------------------------------------------------ time, in words
_UNITS = {"m": "minute", "h": "hour", "d": "day", "w": "week"}
_DAY_NAMES = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday",
              "sat": "Saturday", "sun": "Sunday"}


def duration_text(d: str | None) -> str:
    """``3d`` as "3 days"."""
    m = wfmod.DURATION.fullmatch(str(d or "").strip())
    if not m:
        return str(d or "")
    n, unit = int(m.group(1)), _UNITS[m.group(2)]
    return f"{n} {unit}{'' if n == 1 else 's'}"


def schedule_text(sch: str) -> str:
    """A trigger.schedule as a person says it: "every Monday, Wednesday and Friday at 08:00"."""
    parts = str(sch).split()
    if parts == ["hourly"]:
        return "every hour"
    if parts[0] == "every" and len(parts) == 2:
        return f"every {duration_text(parts[1])}"
    at = parts[-1]
    if parts[0] == "daily":
        return f"every day at {at}"
    if parts[0] == "weekdays":
        return f"Monday to Friday at {at}"
    if parts[0] == "weekly" and len(parts) == 3:
        days = [_DAY_NAMES.get(d, d) for d in parts[1].split(",")]
        listed = days[0] if len(days) == 1 else ", ".join(days[:-1]) + " and " + days[-1]
        return f"every {listed} at {at}"
    if parts[0] == "monthly" and len(parts) == 3:
        day = "the last day" if parts[1] == "last" else f"day {parts[1]}"
        return f"on {day} of each month at {at}"
    return str(sch)


def cond_words(wf, src: str | None) -> str:
    """A step's condition as people read it ("when urgency is urgent"), never as code: inputs and step fields by
    their labels."""
    if not src:
        return ""
    names = {f"input.{i['name']}": i.get("label") for i in wf.inputs}
    for st in wf.steps:
        names.update({f"{st.id}.{f['name']}": f.get("label") for f in st.fields})

    def name(path):
        lab = names.get(path)
        return (lab[:1].lower() + lab[1:]) if lab and not lab[:2].isupper() else lab

    return "when " + expr.words(src, name)


def step_timing(s) -> str:
    """A step's time settings in a few words, for the diagram: how long a wait waits, when it gives up, when work
    is due."""
    out = []
    if getattr(s, "wait_for", None):
        out.append(f"waits {duration_text(s.wait_for)}")
    if getattr(s, "at", None):
        out.append("waits until a set time")
    if getattr(s, "timeout", None):
        out.append(f"gives up after {duration_text(s.timeout)}")
    if getattr(s, "due", None):
        out.append(f"due in {duration_text(s.due)}")
    return "; ".join(out)
