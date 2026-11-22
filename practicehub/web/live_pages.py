"""Pages and calls for writing a record's text together: the live editor on the page, comments, and versions.

Generic: a type opts in with ``live = true`` on a markdown field (see README, "Writing together"). Nothing here knows
any module's types. The editor's script is ``assets.LIVE_JS``; the merging is ``practicehub.live``.

Permissions are the hub's: joining, every change, and every comment are checked as the signed-in member. Anyone who
may read a record may comment on it (no grant of its own: a comment changes nothing the record says); a thread is
resolved by whoever started it or by someone who may change the record.
"""

from __future__ import annotations

import html
import json
import urllib.parse

from .. import schema
from ..hub import Denied, Invalid, NotFound
from ..live import live_fields, live_of, names, word_diff
from .. import markup

e = html.escape
T = r"(?P<rtype>[A-Za-z][\w]*)"
I = r"(?P<rid>[\w.-]+)"
F = r"(?P<field>[A-Za-z_][\w.]*)"


class LivePages:
    def live_routes(self, R) -> None:
        base = rf"/api/live/{T}/{I}/{F}"
        R("POST", base + r"/join", self.api_live_join)
        R("GET", base + r"/state", self.api_live_state)
        R("POST", base + r"/ops", self.api_live_ops)
        R("GET", base + r"/poll", self.api_live_poll)
        R("POST", base + r"/caret", self.api_live_caret)
        R("POST", base + r"/leave", self.api_live_leave)
        R("POST", base + r"/done", self.api_live_done)
        R("POST", rf"/records/{T}/{I}/comments", self.post_comment)
        R("POST", rf"/records/{T}/{I}/comments/(?P<cid>c-[0-9a-f]+)/(?P<verb>reply|resolve|reopen)",
          self.post_comment_verb)
        R("POST", rf"/records/{T}/{I}/comments/(?P<cid>c-[0-9a-f]+)/suggestions/(?P<sid>r-[0-9a-f]+)/"
                  r"(?P<verb>accept|reject)", self.post_suggestion)
        R("GET", r"/api/mentions/candidates", self.api_mention_candidates)
        R("GET", r"/api/mentions/(?P<mid>mn-[0-9a-f]+)", self.api_mention)
        R("POST", r"/api/mentions/(?P<mid>mn-[0-9a-f]+)/(?P<verb>reply|suggest)", self.api_mention_verb)
        R("GET", rf"/records/{T}/{I}/compare", self.page_compare)
        R("POST", rf"/records/{T}/{I}/restore", self.post_restore)

    @property
    def live(self):
        return live_of(self.hub)

    # ------------------------------------------------------------------ the editor's calls (JSON)
    def api_live_join(self, req, rtype, rid, field):
        return self.live.join(req["me"], f"{rtype}/{rid}", field)

    def api_live_state(self, req, rtype, rid, field):
        return self.live.watch(req["me"], f"{rtype}/{rid}", field)

    def api_live_ops(self, req, rtype, rid, field):
        b = self.body_json(req)
        try:
            base = int(b.get("base", -1))
        except (TypeError, ValueError):
            raise Invalid("base is the sequence number the change was made on") from None
        return self.live.submit(req["me"], f"{rtype}/{rid}", field, str(b.get("cid", "")), base, b.get("op"),
                                caret=b.get("caret"))

    def api_live_poll(self, req, rtype, rid, field):
        q = req["query"]
        try:
            since, pv = int(q.get("since", 0)), int(q.get("pv", -1))
            wait = min(float(q.get("wait", 25)), 25.0)
        except ValueError:
            raise Invalid("since, pv and wait are numbers") from None
        return self.live.poll(req["me"], f"{rtype}/{rid}", field, since, pv=pv, wait=wait)

    def api_live_caret(self, req, rtype, rid, field):
        b = self.body_json(req)
        try:
            at, end, seq = int(b.get("at", 0)), int(b.get("end", b.get("at", 0))), int(b.get("seq", 0))
        except (TypeError, ValueError):
            raise Invalid("at, end and seq are numbers") from None
        return self.live.caret(req["me"], f"{rtype}/{rid}", field, at, end, seq)

    def api_live_leave(self, req, rtype, rid, field):
        return self.live.leave(req["me"], f"{rtype}/{rid}", field)

    def api_live_done(self, req, rtype, rid, field):
        return self.live.done(req["me"], f"{rtype}/{rid}", field)

    # ------------------------------------------------------------------ on the record's page
    def live_parts(self, req, rt, rec, ref, st, tab, may_write, locked) -> dict:
        """What a page-layout record adds to its page: the live editor on its text, a banner for a record under
        release control, read-and-sign, and the comments. Empty for a type that has none of these."""
        me, hub = req["me"], self.hub
        lc = rt.lifecycle
        out = {"rec": rec, "banner": "", "side": "", "after": "", "article": "", "edit": None}
        lf = live_fields(rt)
        banner = []
        # a record being revised: who may only read it reads the version in use; who writes it may look at either
        used = hub.in_use_version(me, ref) if lc and lc.get("in_use") and tab == "details" else None
        show = req["query"].get("show")
        if used:
            reading_used = show == "in-use" or (not may_write and show != "current")
            when = str(used["at"])[:10]
            toggle = ""
            if may_write:
                toggle = ('<div class="seg" role="group" aria-label="Which version">'
                          f'<a href="?show=in-use"{" aria-current=true" if reading_used else ""}>'
                          f'{e(lc["in_use"].capitalize())}</a>'
                          f'<a href="?show=current"{"" if reading_used else " aria-current=true"}>'
                          f'{e(str(st).capitalize())}</a></div>')
            if reading_used:
                out["rec"] = {**used["data"], "meta": rec.get("meta")}
                banner.append(f'<p><b>A new revision is being written</b> ({e(str(st))}). This is the '
                              f'{e(lc["in_use"])} version, in use since {e(when)}: work from it until the new one is '
                              f'{e(lc["in_use"])}.</p>')
            else:
                banner.append(f'<p>You are reading the <b>{e(str(st))}</b>. Everyone else works from the '
                              f'{e(lc["in_use"])} version of {e(when)} until this one is {e(lc["in_use"])}.</p>')
            banner.append(toggle)
            out["reading_used"] = reading_used
        # who is editing it now
        everyone = self.live.editing(ref) if lf else []
        now = [m for m in everyone if m["id"] != me["id"]]
        live_now = ""
        if lf and tab == "details":
            ppl = names([m["name"] for m in now])
            live_now = (f'<p class="live-now" data-live-now aria-live="polite"{"" if now else " hidden"}>'
                        + (f'{self._live_faces(now)} <b>{e(ppl)}</b> {"is" if len(now) == 1 else "are"} editing this '
                           f'page now.' if now else "") + '</p>')
        # read and sign: a signature the type asks of its readers, in the state the record is in
        sign = self._read_and_sign(me, rt, rec, ref, st) if tab == "details" and not out.get("reading_used") else ""
        if banner or sign:
            out["banner"] = (f'<section class="page-banner" aria-label="About this version">{"".join(banner)}'
                             f'{sign}</section>')
        out["banner"] += live_now
        if not lf or tab != "details" or out.get("reading_used"):
            return out
        f = lf[0]
        why = None
        if not may_write:
            why = "You may read this page and comment on it, but not change it."
        elif locked:
            why = f"It is {st}: it is not edited."
        url = f"/records/{e(rt.id)}/{e(rec['id'])}"
        out["article"] = (f' data-live="{e(json.dumps({"ref": ref, "field": f["path"], "may": why is None, "active": bool(everyone), "me": me["id"], "url": f"/records/{rt.id}/{rec["id"]}",
                                                         "colors": [self.AV_COLORS[i % len(self.AV_COLORS)] for i in range(8)]}))}"')
        if why is None:
            out["edit"] = (f'<a class="btn primary" href="{url}?edit=1#edit" data-live-edit accesskey="e" '
                           f'aria-keyshortcuts="Alt+E">Edit</a>')
            titles = sorted({hub.title(r) for r in hub.records.search(rt.id, limit=300)})
            out["after"] = ('<datalist id="live-titles">' + "".join(f'<option value="{e(t)}">' for t in titles)
                            + "</datalist>")
        out["side"], bottom = self._comments_panel(me, rt, rec, ref)
        out["after"] = bottom + out["after"]
        return out

    def _live_faces(self, people) -> str:
        return "".join(self._av(m["id"]) for m in people)

    def _read_and_sign(self, me, rt, rec, ref, st) -> str:
        asks = [g for g in rt.signatures if g.get("readers") and (not g["states"] or st in g["states"])
                and not g.get("moves_to")]
        if not asks or me.get("kind") != "person":
            return ""
        sigs = self.hub.signatures(me, ref)
        out = []
        for g in asks:
            here = [s for s in sigs if s["meaning"] == g["meaning"] and s["current"] and s["valid"]]
            mine = next((s for s in here if s["by"] == me["id"]), None)
            may = not g["roles"] or set(g["roles"]) & set(me["roles"])
            if mine:
                out.append(f'<p class="signed"><span class="loz success">signed</span> You signed “{e(g["meaning"])}” '
                           f'for this version, {e(mine["at"][:10])}.</p>')
            elif may:
                out.append(f'<form method="post" action="/records/{e(rt.id)}/{e(rec["id"])}/sign" class="read-sign">'
                           f'<input type="hidden" name="meaning" value="{e(g["meaning"])}">'
                           f'<input type="hidden" name="seen" value="{self.hub._record_digest(rec)}">'
                           f'<p>Read it, then sign: “{e(g["meaning"])}”.</p>{self._sign_prompt(me)}'
                           f'<button class="primary">Read and sign</button></form>')
            if self.hub.may_write(me, rt.id) and here:
                who = names([self._member_name(s["by"]) for s in here][:12])
                out.append(f'<p class="muted">Signed for this version by {len(here)}: {e(who)}'
                           + ("…" if len(here) > 12 else "") + '.</p>')
        return "".join(out)

    def _kind(self, mid) -> str:
        try:
            return self.hub.member(mid)["kind"]
        except NotFound:
            return ""

    def _member_name(self, mid) -> str:
        try:
            return self.hub.member(mid)["name"]
        except NotFound:
            return str(mid)

    # ------------------------------------------------------------------ comments
    def _comments_panel(self, me, rt, rec, ref) -> str:
        cs = self.live.comments(me, ref, file="")  # threads on a file are shown beside that file
        plain = " ".join(markup.plain(v, 10 ** 7) for f in rt.fields if f["type"] == "markdown"
                         for v in schema.get(rec, f["path"]) if isinstance(v, str))
        url = f"/records/{e(rt.id)}/{e(rec['id'])}"
        may_write = self.hub.may_write(me, rt.id)

        def thread(c):
            return self._thread_html(me, c, ref=ref, url=url, may_write=may_write, plain=plain)
        opened = [c for c in cs if not c["resolved"]]
        done = [c for c in cs if c["resolved"]]
        on_text = [c for c in opened if c["quote"]]
        on_page = [c for c in opened if not c["quote"]]
        side = (f'<aside class="comments-side" aria-labelledby="c-side-h"><h2 id="c-side-h">Comments '
                f'<span class="muted">{len(on_text)}</span></h2>'
                + (f'<ul class="plain threads">{"".join(thread(c) for c in on_text)}</ul>' if on_text else
                   '<p class="muted">Select words on the page, then Comment, to comment on them.</p>')
                + (f'<details><summary>Resolved ({len(done)})</summary><ul class="plain threads">'
                   f'{"".join(thread(c) for c in done)}</ul></details>' if done else "") + '</aside>')
        new = (f'<form method="post" action="{url}/comments" class="stack c-new" id="c-new">'
               f'<input type="hidden" name="quote" value=""><input type="hidden" name="before" value="">'
               f'<input type="hidden" name="after" value="">'
               f'<p class="c-on muted" hidden></p>'
               f'<label>Comment on the page<textarea name="body" rows="3" required data-at="{e(ref)}" '
               f'aria-describedby="c-new-help"></textarea></label><small id="c-new-help">Write @ to mention a '
               f'person or an agent: they are told in their inbox, and an agent answers here. Anyone who may read '
               f'this page may comment.</small>'
               f'<div class="filters"><button class="primary">Comment</button>'
               f'<button type="button" class="secondary" data-c-cancel hidden>Comment on the whole page instead</button>'
               f'</div></form>')
        bottom = (f'<section class="page-comments" aria-labelledby="c-page-h"><h2 id="c-page-h">Comments on the '
                  f'page</h2>' + (f'<ul class="plain threads">{"".join(thread(c) for c in on_page)}</ul>' if on_page
                                  else "") + new + '</section>')
        return side, bottom

    def _agent_mark(self, x) -> str:
        # who() marks an agent by name already; an agent's words that its member row no longer says are marked here
        return "" if not x.get("agent") or self._kind(x["by"]) == "agent" else ' <span class="badge agent">agent</span>'

    def _suggestion_html(self, me, c, r, *, url, may_write, back="") -> str:
        s = r.get("suggestion")
        if not s:
            return ""
        nxt = f'<input type="hidden" name="next" value="{e(back)}">' if back else ""
        change = f'<p class="diff c-change"><del>{e(s["find"])}</del> <ins>{e(s["text"])}</ins></p>'
        by_hand = bool(s.get("file"))
        if s["status"] != "open":
            said = ("Made in the file by" if by_hand and s["status"] == "accepted" else
                    f'{s["status"].capitalize()} by')
            return (f'<div class="c-sug">{change}<p class="muted">{e(said)} '
                    f'{e(self._member_name(s.get("by")))}</p></div>')
        act = ""
        if me["kind"] == "person" and may_write:
            base = f'{url}/comments/{e(c["id"])}/suggestions/{e(r["id"])}'
            yes = "I made this change" if by_hand else "Accept"
            act = (f'<div class="filters"><form method="post" action="{base}/accept" class="inline">{nxt}<button '
                   f'class="primary">{yes}</button></form><form method="post" action="{base}/reject" '
                   f'class="inline">{nxt}<button class="secondary">Reject</button></form></div>')
        if by_hand:
            head = (f'<p class="muted">Suggested change to {e(s["file"])}. The hub does not edit the file: a person '
                    f'who may change it makes this change in the document, then marks it made.</p>')
        else:
            head = '<p class="muted">Suggested change to the text, waiting for a person to accept it:</p>'
        return f'<div class="c-sug" aria-label="Suggested change">{head}{change}{act}</div>'

    def _thread_html(self, me, c, *, ref, url, may_write, plain="", back="") -> str:
        """One comment thread: its quote, the comment, replies (an agent's marked), suggestions and the forms.
        ``back``: where its forms return to (the record page when empty)."""
        nxt = f'<input type="hidden" name="next" value="{e(back)}">' if back else ""
        gone = c["quote"] and not c.get("file") and not self.live.anchored(c, plain)
        quote = ""
        if c["quote"]:
            quote = (f'<blockquote class="c-quote{" gone" if gone else ""}" data-quote="{e(c["quote"])}" '
                     f'data-before="{e(c.get("before") or "")}" data-after="{e(c.get("after") or "")}">'
                     f'{e(c["quote"])}</blockquote>'
                     + ('<p class="muted c-gone">On text that was removed.</p>' if gone else ""))
        replies = "".join(f'<li{" class=by-agent" if r.get("agent") else ""}><p class="c-by">{self.who(r["by"])}'
                          f'{self._agent_mark(r)} <span class="muted">{e(r["at"][:16].replace("T", " "))}'
                          f'</span></p><p>{self._comment_text(r["body"])}</p>'
                          f'{self._suggestion_html(me, c, r, url=url, may_write=may_write, back=back)}</li>'
                          for r in c["replies"])
        may_resolve = c["by"] == me["id"] or may_write
        verb = "reopen" if c["resolved"] else "resolve"
        act = (f'<form method="post" action="{url}/comments/{e(c["id"])}/{verb}" class="inline">{nxt}'
               f'<button class="subtle">{"Reopen" if c["resolved"] else "Resolve"}</button></form>'
               if may_resolve else "")
        return (f'<li class="thread{" resolved" if c["resolved"] else ""}{" by-agent" if c.get("agent") else ""}" '
                f'id="{e(c["id"])}" data-comment="{e(c["id"])}">'
                f'{quote}<p class="c-by">{self.who(c["by"])}{self._agent_mark(c)} <span class="muted">'
                f'{e(c["at"][:16].replace("T", " "))}</span>'
                f'{" <span class=loz>resolved</span>" if c["resolved"] else ""}</p>'
                f'<p>{self._comment_text(c["body"])}</p>'
                + (f'<ul class="plain replies">{replies}</ul>' if replies else "")
                + f'<details class="c-reply"><summary>Reply</summary><form method="post" '
                  f'action="{url}/comments/{e(c["id"])}/reply" class="stack">{nxt}<label>Reply<textarea name="body" '
                  f'rows="2" required data-at="{e(ref)}"></textarea></label><button>Reply</button></form></details>'
                  f'{act}</li>')

    def _comment_text(self, body: str) -> str:
        out = e(body).replace("\n", "<br>")
        return __import__("re").sub(r"(?<![\w@])@([a-z0-9][a-z0-9_.-]*[a-z0-9])",
                                    lambda m: f'<span class="at">@{m.group(1)}</span>', out)

    def _back(self, rtype, rid, anchor="", req=None) -> None:
        """Back to the record page, or to ``next`` when it is a page of the same record (the office editor)."""
        base = f"/records/{rtype}/{rid}"
        nxt = str(self.form(req).get("next") or "") if req is not None else ""
        if not (nxt.startswith(base + "/") and __import__("re").fullmatch(r"/[\w./%=?&-]*", nxt)):
            nxt = base
        raise self._redirect(nxt + (f"#{anchor}" if anchor else ""))

    def post_comment(self, req, rtype, rid):
        f = self.form(req)
        c = self.live.comment(req["me"], f"{rtype}/{rid}", str(f.get("body", "")), quote=str(f.get("quote", "")),
                              before=str(f.get("before", "")), after=str(f.get("after", "")),
                              field=str(f.get("field", "")), file=str(f.get("file", "")))
        self._back(rtype, rid, c["id"], req)

    def post_suggestion(self, req, rtype, rid, cid, sid, verb):
        self.live.decide_suggestion(req["me"], f"{rtype}/{rid}", cid, sid, verb == "accept")
        self._back(rtype, rid, cid, req)

    # ------------------------------------------------------------------ mentions (people and agents)
    @property
    def mentions(self):
        from ..mentions import mentions_of
        return mentions_of(self.hub)

    def api_mention_candidates(self, req):
        """Whom the signed-in member may mention on ``ref``, for the @ picker (agents marked, and who may not read
        it said, so nobody is mentioned who would be refused)."""
        q = req["query"]
        ref = str(q.get("ref") or "")
        if "/" not in ref:
            raise Invalid("say which record: ref=Type/id")
        return {"members": self.mentions.candidates(req["me"], ref, str(q.get("q") or ""))[:50]}

    def api_mention(self, req, mid):
        """What a mentioned member is given: the record, the passage, and the thread (only what they may read)."""
        return self.mentions.context(req["me"], mid)

    def api_mention_verb(self, req, mid, verb):
        """The mentioned member answers in the thread, or (``suggest``) proposes a change a person accepts."""
        me, b = req["me"], self.body_json(req)
        row = self.mentions.get(mid)
        if row["member"] != me["id"]:
            raise Denied("this mention is for someone else")
        ref, anchor = row["ref"], row["anchor"]
        if not anchor.startswith("c-"):  # mentioned in the text itself: the answer starts a thread on that passage
            line = next((ln for ln in row["quote"].splitlines() if f"@{me['id']}" in ln), row["quote"])[:500]
            c = self.live.comment(me, ref, str(b.get("body") or "") or "Suggested a change.", quote=line,
                                  field=row["field"])
            self.mentions.answered(me, ref, anchor)
            anchor = c["id"]
            if verb == "reply":
                return c
        if verb == "suggest":
            return self.live.suggest(me, ref, anchor, str(b.get("field") or row["field"] or ""),
                                     str(b.get("find") or ""), str(b.get("text") or ""), str(b.get("body") or ""))
        return self.live.reply(me, ref, anchor, str(b.get("body") or ""))

    def post_comment_verb(self, req, rtype, rid, cid, verb):
        ref = f"{rtype}/{rid}"
        if verb == "reply":
            self.live.reply(req["me"], ref, cid, str(self.form(req).get("body", "")))
        else:
            self.live.resolve(req["me"], ref, cid, resolved=verb == "resolve")
        self._back(rtype, rid, cid, req)

    # ------------------------------------------------------------------ versions
    def history_extra(self, v: dict) -> str:
        """What a version row adds: its comment (\"Edited by Jo and Noor\", \"Restored version 3\")."""
        return e(v.get("comment") or "")

    def page_compare(self, req, rtype, rid):
        """Two versions of a record side by side, as one text: what was taken out, and what was put in."""
        me, hub = req["me"], self.hub
        ref = f"{rtype}/{rid}"
        rt = hub.record_type(rtype)
        if not rt:
            raise NotFound(f"there is no record type {rtype}")
        versions = hub.record_history(me, ref)
        if not versions:
            raise NotFound(f"{ref} has no versions")
        by_n = {v["version"]: v for v in versions}
        q = req["query"]
        try:
            b = int(q.get("b") or versions[0]["version"])
            a = int(q.get("a") or (versions[1]["version"] if len(versions) > 1 else b))
        except ValueError:
            raise Invalid("a and b are version numbers") from None
        if a not in by_n or b not in by_n:
            raise NotFound("there is no such version")
        va, vb = by_n[a], by_n[b]
        url = f"/records/{e(rtype)}/{e(rid)}"

        def pick(name, cur):
            return (f'<label>{"From" if name == "a" else "To"} <select name="{name}">' + "".join(
                f'<option value="{v["version"]}"{" selected" if v["version"] == cur else ""}>v{v["version"]} · '
                f'{e(v["at"][:16].replace("T", " "))} · {e(self._member_name(v["actor"]) if v["actor"] else "")}'
                f'{(" · " + e(v["comment"])) if v.get("comment") else ""}</option>' for v in versions)
                + "</select></label>")
        out = [f'<form method="get" class="filters" action="{url}/compare">{pick("a", a)}{pick("b", b)}'
               f'<button>Compare</button></form>']
        any_change = False
        for f in rt.fields:
            if f.get("show", True) is False:
                continue
            x = schema.get(va["data"], schema.locate(va["data"], f["path"], f.get("with")))
            y = schema.get(vb["data"], schema.locate(vb["data"], f["path"], f.get("with")))
            if x == y:
                continue
            any_change = True
            sx = "\n".join(str(v) if not isinstance(v, (dict, list)) else json.dumps(v) for v in x)
            sy = "\n".join(str(v) if not isinstance(v, (dict, list)) else json.dumps(v) for v in y)
            runs = "".join(f"<del>{e(t)}</del>" if k == "del" else f"<ins>{e(t)}</ins>" if k == "ins" else e(t)
                           for k, t in word_diff(sx, sy))
            out.append(f'<section class="diff"><h2>{e(f["label"])}</h2><div class="diff-text">{runs}</div></section>')
        if not any_change:
            out.append("<p class='muted'>These two versions say the same.</p>")
        out.insert(0, '<p class="muted diff-key">Taken out: <del>like this</del>. Put in: <ins>like this</ins>.</p>')
        if hub.may_write(me, rtype) and a != versions[0]["version"]:
            out.append(f'<form method="post" action="{url}/restore" class="card stack"><input type="hidden" '
                       f'name="version" value="{a}"><p>Make version {a} the page’s text again. It is saved as a new '
                       f'version; nothing in between is lost.</p><button class="secondary" '
                       f'data-confirm="Restore version {a}?">Restore version {a}</button></form>')
        title = hub.title(hub.read_record(me, ref))
        return self.layout(req, f"Compare versions of {title}", "".join(out),
                           crumbs=[("/records", "Records"), (f"/records/{rtype}", rt.plural),
                                   (f"/records/{rtype}/{rid}", title), (None, "Compare versions")])

    def post_restore(self, req, rtype, rid):
        f = self.form(req)
        try:
            v = int(f.get("version", ""))
        except ValueError:
            raise Invalid("choose a version to restore") from None
        self.live.restore(req["me"], f"{rtype}/{rid}", v)
        raise self._redirect(f"/records/{rtype}/{rid}?tab=history")
