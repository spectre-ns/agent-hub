"""A record's History tab: its journey, nested (``Hub.record_journey``, practicehub/journey.py).

Across the top, the stages the record has been in, in order. Under it, one block per stage: when it got there and
how long it stayed; the work done there, each line marked by who did it (a person, an agent, or the workflow by
itself); and each move out of it, with what that move ran nested under it. What ran and was not needed, and the ways
out that were not taken, fold to one line each; what the reader may not see is one quiet line saying so.

The section fetches itself again while something is going on that no one here is doing (``data-live``: a job in the
queue or under way, an agent's work, a message on its way); the page's script swaps it in. Without the script, a
Refresh link.
"""
from __future__ import annotations

import html

from ..hub import Invalid

e = html.escape


def span_words(seconds) -> str:
    """How long, in a few words: "41 s", "11 min", "2 h 5 min", "3 days"."""
    if seconds is None:
        return ""
    s = int(seconds)
    if s < 1:
        return "a moment"
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    if s < 86400:
        h, m = s // 3600, s % 3600 // 60
        return f"{h} h {m} min" if m else f"{h} h"
    d = s // 86400
    return f"{d} day{'' if d == 1 else 's'}"


def _n(k: int, one: str, many: str) -> str:
    return f"{k} {one if k == 1 else many}"


class JourneyPages:
    STEP_WORDS = {"queued": "in the queue", "running": "under way", "done": "done",
                  "failed": "did not go through", "held": "waiting"}

    def journey_url(self, ref: str) -> str:
        """Where the History section alone is fetched from: the record's flat address (a part's too)."""
        return f"/records/{ref}/journey"

    def page_record_journey(self, req, rtype, rid):
        """The History section alone, for the page's script to fetch while it is live: read as the page is, with an
        ETag that changes only when the journey could have (``Hub.record_journey_token``), so a page that asks again
        and again is answered 304 without anything being built."""
        from .server import Rendered
        me, hub = req["me"], self.hub
        ref = f"{rtype}/{rid}"
        rt = hub.record_type(rtype)
        if not rt or rtype in hub.member_types():
            raise Invalid(f"there is no record type {rtype}")
        hub.read_record(me, ref)  # refuses as the page does
        tag = '"' + hub.record_journey_token(me, ref) + '"'
        asked = (req["headers"].get("if-none-match") or "").strip()
        if asked and tag in [t.strip().removeprefix("W/") for t in asked.split(",")]:
            raise Rendered(304, "", headers={"ETag": tag})
        url = e(self.record_href(ref)) if rt.part_of else f"/records/{e(rtype)}/{e(rid)}"
        body = self._journey_html(me, rt, ref, url)
        return ({"Content-Type": "text/html; charset=utf-8", "ETag": tag}, body.encode())

    # ------------------------------------------------------------------------------------------ the section
    def _journey_html(self, me, rt, ref: str, url: str) -> str:
        j = self.hub.record_journey(me, ref)
        stages = j["stages"]
        named = [st for st in stages if st["kind"] != "none"]
        strip = ""
        if len(named) > 1:
            strip = ('<ol class="jr-strip" aria-label="The stages it has been in, in order">' + "".join(
                f'<li{" class=now" if st["now"] else ""}><a href="#jr-{e(st["key"])}">{self._stage_loz(rt, st)}</a></li>'
                for st in named) + "</ol>")
        body = "".join(self._stage_html(me, rt, st) for st in stages)
        if not any(st["lines"] or st["moves"] for st in stages) and len(stages) < 2 and not stages[0]["entered"]:
            body = ""
        live = ""
        if j["live"]:
            live = (f'<p class="jr-live"><span class="muted small">Some of this is still going on.</span> '
                    f'<a href="{url}?tab=history#journey">Refresh</a></p>')
        # the keys are the marks the lines carry: the avatar's shape for who did it, the gear for what no one did
        key = lambda kind, word: f'<span class="jr-k"><span class="av {kind}" aria-hidden="true"></span> {word}</span>'
        legend = ('<p class="jr-legend muted small">' + key("person", "a person") + key("agent", "an agent")
                  + key("service", "a service")
                  + '<span class="jr-k"><span class="jr-mark auto" aria-hidden="true">⚙\ufe0e</span> automation</span>'
                    '</p>') if body else ""
        rec = self.hub.records.read(*ref.split("/", 1)) or {}
        # the token says when to fetch again; the version, when the page around the section has gone stale
        attrs = f' data-at="{e(j["token"])}" data-version="{e(str((rec.get("meta") or {}).get("versionId", "")))}"'
        if j["live"]:
            attrs += f' data-live="{e(self.journey_url(ref))}"'
        if j["busy"]:
            attrs += " data-busy"
        inner = (strip + legend + f'<ol class="timeline jr-stages">{body}</ol>' + live) if body \
            else "<p class='muted'>Nothing yet.</p>"
        return f'<section id="journey" class="jr" aria-label="How it got here"{attrs}>{inner}</section>'

    def _clock(self, ts, day=None) -> str:
        """A time inside a stage: the hour alone on the day the stage began (its heading says the day), else whole."""
        whole = self._at(ts)
        if day and whole[:10] == day and len(whole) > 11:
            return f'<time title="{whole}">{whole[11:]}</time>'
        return whole

    def _stage_loz(self, rt, st: dict) -> str:
        tone = self.TONE_CLASS.get(st["tone"] or "", None) or self._state_tone(st["state"], rt)
        return f'<span class="loz {tone}">{e(st["label"])}</span>'

    def _by(self, by: dict | None) -> str:
        """Who did it, marked by what they are: a person or an agent by their avatar and name (an agent's says so)."""
        if not by or by.get("kind") == "automatic" or not by.get("id"):
            return ""
        return self.who(by["id"])

    def _stage_html(self, me, rt, st: dict) -> str:
        head = self._stage_loz(rt, st) if st["kind"] != "none" else '<span class="muted">Before its first stage</span>'
        when = f'<span class="jr-when muted small">{self._at(st["at"])}</span>' if st["at"] else ""
        if st["now"]:
            took = '<span class="jr-took muted small">where it is now</span>'
        elif st["seconds"] is not None:
            took = f'<span class="jr-took muted small">for {e(span_words(st["seconds"]))}</span>'
        else:
            took = ""
        ent = st.get("entered")
        how = ""
        if ent:
            words = {"created": "created", "filed": "filed", "started": "started"}.get(ent["how"], "created")
            how = f'<span class="jr-how muted small">{e(words)}'
            if ent.get("by") and ent["by"]["kind"] != "automatic":
                how += f' by {e(ent["by"]["name"])}'
            if ent.get("from"):
                how += f' from {self.record_link(ent["from"])}' if self.hub.may_read(me, ent["from"].split("/", 1)[0]) \
                    else " from another record"
                if ent.get("label"):
                    how += f' (“{e(ent["label"])}”)'
            how += "</span>"
        # on the rail, in place of a dot: who or what put it in this stage, by the legend's marks
        by, dot = st.get("by"), ""
        if by and by.get("kind") == "automatic":
            dot = ('<span class="jr-dot auto" title="Moved here by automation" aria-hidden="true">'
                   '<span class="jr-mark auto">⚙\ufe0e</span></span>')
        elif by and by.get("id"):
            dot = (f'<span class="jr-dot" title="{e("Made" if ent else "Moved here")} by {e(by["name"])}" '
                   f'aria-hidden="true">{self._av(by["id"])}</span>')
        day = self._at(st["at"])[:10] if st["at"] else None
        lines = "".join(self._line_html(me, ln, day) for ln in st["lines"])
        moves = "".join(self._move_html(me, rt, m, day) for m in st["moves"])
        inner = (f'<ul class="plain jr-lines">{lines}</ul>' if lines else "") + moves
        if not inner:
            inner = '<p class="muted small jr-none">Nothing was done here.</p>' if not st["now"] else ""
        return (f'<li class="jr-stage{" now" if st["now"] else ""}{" by" if dot else ""}" id="jr-{e(st["key"])}">'
                f'<details open data-key="{e(st["key"])}"><summary>{dot}{head} {when} {took} {how}</summary>'
                f'{inner}</details></li>')

    # ------------------------------------------------------------------------------------------ lines
    def _line_html(self, me, ln: dict, day=None) -> str:
        kind = ln["kind"]
        at = lambda ts: f' <span class="jr-at muted small">{self._clock(ts, day)}</span>' if ts else ""
        if kind == "fold":
            n = ln["count"]
            if ln["what"] == "not-needed":
                items = "".join(f"<li>{e(t)}</li>" for t in ln["titles"])
                return (f'<li class="jr-line"><details class="jr-fold"><summary>{e(_n(n, "step", "steps"))} not '
                        f'needed here</summary><ul>{items}</ul></details></li>')
            words = (f'{_n(n, ln["classes"] + " step", ln["classes"] + " steps")}' if ln["what"] == "step"
                     else f'{_n(n, "change", "changes")} to {ln["classes"]} fields')
            return f'<li class="jr-line tl-hidden muted">{e(words)}, not shown to you</li>'
        if kind == "edit":
            if ln["changed"]:
                text = "Changed " + ", ".join(ln["changed"]) + (" and fields you may not read" if ln["unread"] else "")
            elif ln["unread"]:
                text = "Changed fields you may not read"
            else:
                text = "Saved with no change to what it says"
            if ln.get("comment"):
                text += f": {ln['comment']}"
            return (f'<li class="jr-line jr-edit"><span class="jr-mark" aria-hidden="true">✎</span> {e(text)} '
                    f'<span class="jr-who">{self._by(ln["by"])}</span>{at(ln["at"])}</li>')
        if kind == "signature":
            return (f'<li class="jr-line jr-edit"><span class="jr-mark" aria-hidden="true">✎</span> '
                    f'Signed {e(repr(ln["meaning"]))} by {e((ln["by"] or {}).get("name") or "someone")}'
                    f'{at(ln["at"])}</li>')
        if kind == "note":
            title = f'“{e(ln["title"])}”'
            said = {"began": "began" + (" again" if ln.get("again") else ""), "finished": "finished",
                    "cancelled": "was cancelled", "stopped": "was stopped"}[ln["what"]]
            text = f"{title} {said}"
            if ln.get("why"):
                text += f": {e(ln['why'])}"
            return (f'<li class="jr-line jr-note muted"><span class="jr-mark" aria-hidden="true">·</span> {text} '
                    f'<span class="jr-who">{self._by(ln.get("by"))}</span>{at(ln["at"])}</li>')
        return self._work_html(ln, day)

    SPIN = '<span class="jr-spin" aria-hidden="true"></span> '  # turning while something is being done right now

    def _work_html(self, ln: dict, day=None) -> str:
        status, what, by = ln["status"], ln["what"], ln.get("by")
        title = f'<b>{e(ln["title"])}</b>'
        took = f', after {e(span_words(ln["seconds"]))}' if ln.get("seconds") else ""
        cls, said, who = "person", "", self._by(by)
        if what == "automatic":
            cls = "auto"
        elif by and by.get("kind") == "agent":
            cls = "agent"
        if what == "wait":
            cls = "auto"
            said = {"done": f'over{took}', "timed-out": f'ended without it{took}', "waiting": "waiting",
                    "failed": "did not go through", "cancelled": "stopped with its work"}.get(status, status)
            took = ""
        elif status == "done":
            said = "decided" if what == "decision" else "done"
            if ln.get("via_move"):
                said += " by making the move below"
            if ln.get("chose"):
                said += f': “{e(ln["chose"])}”'
            if ln.get("signed"):
                said += ", signed"
            if ln.get("approved_by"):
                said += f', approved by {e(ln["approved_by"])}'
        elif status == "failed":
            said = '<span class="ra-word bad">did not go through</span>'
            if ln.get("error"):
                said += f' <span class="muted small">{e(str(ln["error"])[:200])}</span>'
        elif status in ("queued", "running"):
            said = (self.SPIN if status == "running" else "") + self.STEP_WORDS[status]
        elif status == "open":
            # who it is waiting for: the one it is assigned to, else whoever of the role takes it
            to = ln.get("assigned")
            said = "to do" + (f', for {e(to["name"])}' if to else f', for the {e(ln["for"])}' if ln.get("for") else "")
            said += "" if to else ", no one has taken it"
        elif status == "taken":
            said = self.SPIN + "being worked on" if cls == "agent" else "to do, taken"
        elif status == "held":
            said = "waiting for approval"
        elif status == "withdrawn":
            said = "no longer needed"
        elif status == "cancelled":
            said = "not done: its work was stopped"
        if status in ("open", "taken", "held") and ln.get("since"):
            said += f', open since {e(ln["since"])}'
        mark = {"auto": '<span class="jr-mark auto" aria-hidden="true">⚙\ufe0e</span>'}.get(cls, "")
        if what == "wait":
            mark = '<span class="jr-mark auto" aria-hidden="true">◷</span>'
        tail = f'{took}' if status == "done" else ""
        when = ln.get("done_at") or ln.get("at")
        at = f' <span class="jr-at muted small">{self._clock(when, day)}{e(tail)}</span>' if when else ""
        going = " jr-going" if status in ("queued", "running", "taken") and cls != "person" else ""
        passed, p = "", ln.get("passed")
        if p:  # an agent that could not do it, or someone who gave it back: said under the work it left open
            did = "passed it up to a person" if p["up"] else "gave it back"
            why = f': “{e(p["note"][:300])}”' if p.get("note") else ""
            passed = (f'<span class="jr-passed muted small">{self._by(p["by"])} {did}'
                      f' at {self._clock(p["at"], day)}{why}</span>')
        return (f'<li class="jr-line jr-work jr-{cls} jr-{e(status)}{going}">{mark}{title} '
                f'<span class="jr-said">{said}</span> <span class="jr-who">{who}</span>{at}{passed}</li>')

    # ------------------------------------------------------------------------------------------ moves
    def _move_html(self, me, rt, m: dict, day=None, nested: bool = False) -> str:
        how, status = m["how"], m["status"]
        to = (f'<span class="jr-arrow" aria-label="to">→</span> '
              f'<span class="loz {self._state_tone(m["to"], rt)}">{e(m.get("to_label") or "")}</span>') \
            if m.get("to_label") else ""
        if how == "hand":
            name = f'<b>{e(m["label"])}</b>' if m.get("label") else "<b>Moved</b>"
            tag = '<span class="jr-tag">by hand</span>'
        elif how == "auto":
            name, tag = "<b>Moved</b>", '<span class="jr-tag">automation</span>'
        else:
            name = f'<b>{e(m["label"])}</b>' if m.get("label") else "<b>Moved on</b>"
            tag = ""
        said = {"going": f'<span class="jr-word going">{self.SPIN}under way</span>',
                "failed": '<span class="ra-word bad">did not go through</span>',
                "fell-back": '<span class="jr-word">went there instead</span>'}.get(status, "")
        who = self._by(m.get("by"))
        at = f'<span class="jr-at muted small">{self._clock(m["at"], day)}</span>' if m.get("at") else ""
        extra, wrote_fold = [], ""
        values = m.get("values") or []
        if len(values) > 2:  # a move that wrote a lot: one line to open
            items = "".join(f"<li>{e(label)}: {e(str(v))}</li>" for label, v in values)
            wrote_fold = (f'<details class="jr-fold"><summary>{len(values)} fields filled in'
                          f'{" (and fields you may not read)" if m.get("unread") else ""}</summary>'
                          f'<ul>{items}</ul></details>')
        elif values:
            extra.append("with " + "; ".join(f"{e(label)}: {e(str(v))}" for label, v in values)
                         + (" (and fields you may not read)" if m.get("unread") else ""))
        elif m.get("unread"):
            extra.append("with fields you may not read")
        if m.get("signed"):
            extra.append(f'signed {e(repr(m["signed"]["meaning"]))} by '
                         f'{e((m["signed"]["by"] or {}).get("name") or "someone")}')
        if m.get("comment"):
            extra.append(e(m["comment"]))
        wrote = f'<p class="jr-wrote muted small">{"; ".join(extra)}</p>' if extra else ""
        ran = [{"state": s["status"], "title": s["title"], "error": s.get("error"), "message": s.get("message"),
                "started": s.get("started") or [], "hidden": False, "retried": s.get("retried") or [],
                "ms": s.get("ms"), "since": s.get("since")} for s in m.get("steps") or []]
        ran += [{"hidden": True}] * int((m.get("hidden") or {}).get("count") or 0)
        steps = self._ran_list_html(me, ran)
        folds = ""
        nn = m.get("not_needed")
        if nn:
            folds += (f'<details class="jr-fold"><summary>{e(_n(nn["count"], "step", "steps"))} not needed</summary>'
                      f'<ul>{"".join(f"<li>{e(t)}</li>" for t in nn["titles"])}</ul></details>')
        if m.get("not_taken"):
            k = len(m["not_taken"])
            folds += (f'<details class="jr-fold"><summary>{e(_n(k, "other way out", "other ways out"))} not taken'
                      f'</summary><ul>{"".join(f"<li>{e(t)}</li>" for t in m["not_taken"])}</ul></details>')
        then = "".join(self._move_html(me, rt, t, day, nested=True) for t in m.get("then") or [])
        if then:
            then = f'<div class="jr-then"><p class="muted small">Then, by automation:</p>{then}</div>'
        return (f'<div class="jr-move jr-{e(how)} jr-{e(status)}{" nested" if nested else ""}">'
                f'<div class="jr-move-head"><span class="jr-rail" aria-hidden="true">↓</span> {name} {to} {tag} '
                f'{said} <span class="jr-who">{who}</span> {at}</div>{wrote}{steps}{wrote_fold}{folds}{then}</div>')
