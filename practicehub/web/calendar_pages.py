"""The practice calendar: every record of a type that extends the core's ``Booking``, by day or week, one column per
bookable member (the pack's ``[calendar] provider_role``), between the practice's hours, in slots of its size.

Everyone may open it. A booking the member may not read (its type, or whom it is for) shows as Busy, nothing more. A
free slot opens the new-booking form in the dialog with the member and time filled in: it asks whom for and why.
A booking is moved (another time, another member) from its own page. The core's clash rule refuses a member booked
twice (``Hub.booking_clashes``); the form says so in the dialog. Nothing here knows what the practice books.

A booking type's ``[booking]`` says the rest, in the practice's words: the priorities the calendar marks; the roles
that may book over another booking (a squeeze-in), saying why, which the calendar then shows side by side; and the
workflow a member who may not give the reason books through (the form asks that workflow's inputs, gives it the slot,
and says what became of it: booked, or sent on to someone first).
"""

from __future__ import annotations

import html
import re
import urllib.parse
from datetime import date, datetime, timedelta

from .. import schema, visibility
from ..hub import Denied, Invalid, NotFound

e = html.escape
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class CalendarPages:
    def calendar_routes(self, R):
        R("GET", r"/schedule", self.page_schedule)
        R("GET", r"/schedule/new", self.page_booking_new)
        R("POST", r"/schedule/new", self.post_booking_new)
        R("GET", r"/records/(?P<rtype>[A-Za-z][\w]*)/(?P<rid>[\w.-]+)/reschedule", self.page_reschedule)
        R("POST", r"/records/(?P<rtype>[A-Za-z][\w]*)/(?P<rid>[\w.-]+)/reschedule", self.post_reschedule)

    # ------------------------------------------------------------------------------------------ what the calendar is
    def _cal(self) -> dict:
        return dict((self.hub.pack.calendar if self.hub.pack else None) or {})

    def _slot(self) -> int:
        return max(5, int(self._cal().get("slot_minutes", 15)))

    def _hours(self, day: date) -> tuple[datetime, datetime] | None:
        """When the practice books on this day (its ``hours``), or None when it is closed."""
        got = self._cal().get("hours", {}).get(DAYS[day.weekday()])
        if not got:
            return None
        start, end = (datetime.combine(day, datetime.strptime(str(t), "%H:%M").time()) for t in got[:2])
        return (start, end) if end > start else None

    def calendar_members(self) -> list[dict]:
        """The members whose time is booked: the holders of the calendar's ``provider_role``, people, active; with no
        such role, every active person. In the order the team lists them."""
        role = self._cal().get("provider_role")
        try:
            held = self.hub.role(role)["holders"] if role else self.hub.members("person")
        except NotFound:
            held = []
        return [m for m in held if m.get("kind") == "person" and m.get("status") == "active"]

    def booking_kinds(self, me, *, write: bool = False) -> list[str]:
        """The practice's booking types (those extending ``Booking``) this member may read, or write."""
        test = self._may_write if write else self._may_read
        return [t for t in self.hub.of_base("Booking") if test(me, t)]

    def _now(self) -> datetime:
        return self.hub.engine.now().replace(second=0, microsecond=0)

    # ------------------------------------------------------------------------------------------ one booking, as shown
    def _shown(self, me, b: dict) -> dict:
        """A booking as this member sees it: whom for and why only when they may read both its type and its subject's;
        else Busy (the time is held, nothing more)."""
        subj = b.get("subject") or ""
        sees = self._may_read(me, b["type"]) and (not subj or self._may_read(me, subj.split("/", 1)[0]))
        span = self.hub.booking_span(b, self._slot())
        out = {"ref": f'{b["type"]}/{b["record"]["id"]}', "start": span[0] if span else None,
               "end": span[1] if span else None, "with": b["with"], "cancelled": b["cancelled"], "sees": sees,
               "name": "Busy", "reason": "", "state": "", "kind": "", "priority": "", "mark": "", "over": ""}
        rt = self.hub.record_type(b["type"])
        marked = list(((rt.booking if rt else None) or {}).get("marked") or [])
        if b.get("priority") in marked:  # how soon is shown even as Busy: the time is the practice's to plan
            out["priority"] = str(b["priority"])
            out["mark"] = "high" if b["priority"] == marked[-1] else "raised"
        out["over"] = "yes" if b.get("overbooked") is not None and b.get("overbooked") != "" else ""
        if sees:
            rec = self.hub.redact(me, self.hub.records.read(*subj.split("/", 1))) if subj else None
            full = self.hub.title(rec) if rec else (subj or self.hub.record_type(b["type"]).label)
            out["name"] = re.split(r" \(| · ", full, maxsplit=1)[0].strip() or full  # its name part: the grid is narrow
            out["full"] = full
            out["reason"] = str(b.get("reason") or "")
            out["kind"] = str(b.get("kind") or "")
            out["overbooked"] = str(b.get("overbooked") or "")
            out["state"] = str(schema.first(b["record"], rt.lifecycle["path"]) or "") if rt and rt.lifecycle else ""
        return out

    @staticmethod
    def _lanes(items: list[dict]) -> None:
        """Bookings that overlap in one column (one booked over another) side by side: each gets ``lane`` of ``lanes``
        in its group of overlapping ones. A cancelled booking holds no time and takes no lane."""
        live = sorted((x for x in items if not x["cancelled"]), key=lambda x: (x["start"], x["end"]))
        group, ends, group_end = [], [], None
        for x in live + [None]:
            if x is None or (group_end is not None and x["start"] >= group_end):
                for y in group:
                    y["lanes"] = len(ends)
                group, ends, group_end = [], [], None
                if x is None:
                    break
            lane = next((i for i, end in enumerate(ends) if end <= x["start"]), len(ends))
            if lane == len(ends):
                ends.append(x["end"])
            else:
                ends[lane] = x["end"]
            x["lane"] = lane
            group.append(x)
            group_end = max(group_end, x["end"]) if group_end else x["end"]

    def _block(self, x: dict, *, row0: datetime | None = None, slot: int = 15) -> str:
        """A booking in a column (placed on the column's grid) or in the list."""
        when = f'{x["start"]:%H:%M}–{x["end"]:%H:%M}' if x["start"] else ""
        cls = ("cal-ev" + (" cancelled" if x["cancelled"] else "") + ("" if x["sees"] else " busy")
               + (f' marked-{x["mark"]}' if x.get("mark") and not x["cancelled"] else "")
               + (" over" if x.get("over") and not x["cancelled"] else ""))
        place = ""
        if row0 is not None and x["start"]:
            top = max(0, int((x["start"] - row0).total_seconds() // 60))
            span = max(1, -(-int((x["end"] - x["start"]).total_seconds() // 60) // slot))
            lanes = (f';--lane:{x["lane"]};--lanes:{x["lanes"]}' if x.get("lanes", 1) > 1 else "")
            place = f' style="grid-row:{top // slot + 1} / span {span}{lanes}"'
        what = x["reason"] or self.human(x["kind"]) if x.get("kind") or x["reason"] else ""
        mark = (f'<span class="loz cal-pri {e(x["mark"])}">{e(self.human(x["priority"]))}</span> '
                if x.get("mark") and not x["cancelled"] else "")
        text = (f'{mark}<span class="t">{e(when)}</span> <b>{e(x["name"])}</b>'
                + (f' <span class="why">{e(what)}</span>' if what else "")
                + (' <span class="visually-hidden">(booked over another booking)</span>'
                   if x.get("over") and not x["cancelled"] else "")
                + (' <span class="visually-hidden">(cancelled)</span>' if x["cancelled"] else "")
                + (f' <span class="st">{e(x["state"])}</span>' if x["state"] and not x["cancelled"] else ""))
        tip = (e(x.get("full") or x["name"]) + (f" · {e(what)}" if what else "")
               + (f" · {e(self.human(x['priority']))}" if x.get("mark") else "")
               + (f" · booked over another: {e(x['overbooked'])}" if x.get("overbooked") else ""))
        if not x["sees"]:
            return f'<div class="{cls}"{place} title="Booked">{text}</div>'
        return f'<a class="{cls}"{place} href="{e(self.record_href(x["ref"]))}" title="{tip}">{text}</a>'

    # ------------------------------------------------------------------------------------------ the calendar page
    def page_schedule(self, req):
        """The calendar, by day or week, for every member: one column per bookable member, free slots to book."""
        me, q = req["me"], req["query"]
        kinds = self.hub.of_base("Booking")
        if not kinds:
            raise NotFound("this practice keeps no calendar")
        now = self._now()
        today = now.date()
        try:
            day = date.fromisoformat(q.get("date") or today.isoformat())
        except ValueError:
            raise Invalid(f"{q.get('date')!r} is not a date (YYYY-MM-DD)") from None
        view = "week" if q.get("view") == "week" else "day"
        members = self.calendar_members()
        ids = [m["id"] for m in members]
        who = q.get("who") or "all"
        if who == "mine":
            cols = [m for m in members if m["id"] == me["id"]]
        elif who in ids:
            cols = [m for m in members if m["id"] == who]
        else:
            who, cols = "all", members
        days = [day] if view == "day" else [day - timedelta(days=day.weekday()) + timedelta(days=i) for i in range(7)]
        if view == "week":  # the week's open days, and any closed day with something booked
            open_days = [d for d in days if self._hours(d)]
        else:
            open_days = days
        from ..engine import bookings
        held = bookings(self.hub, active_only=False, since=days[0].isoformat(),
                        until=(days[-1] + timedelta(days=1)).isoformat(), member=me)
        shown = [self._shown(me, b) for b in held]
        shown = [x for x in shown if x["start"]]
        if view == "week":
            booked_days = {x["start"].date() for x in shown}
            open_days = [d for d in days if d in open_days or d in booked_days]
        writable = bool(self.booking_kinds(me, write=True))
        slot = self._slot()
        squeeze = next(((t, self.hub.record_type(t)) for t in self.booking_kinds(me, write=True)
                        if self.hub.may_overbook(me, self.hub.record_type(t))), None)

        step = timedelta(days=1 if view == "day" else 7)
        base = {"view": view, "who": who}
        href = lambda **kw: "/schedule?" + urllib.parse.urlencode({**base, **kw})
        title = (f"{day:%A %B} {day.day}, {day.year}" if view == "day" else
                 f"Week of {days[0]:%B} {days[0].day}, {days[0].year}")
        pick = "".join(f'<option value="{e(v)}"{" selected" if v == who else ""}>{e(lbl)}</option>' for v, lbl in
                       [("all", "Everyone"), *([("mine", "Mine")] if me["id"] in ids else []),
                        *((m["id"], m["name"]) for m in members)])
        bar = (f'<div class="cal-bar" role="group" aria-label="Calendar">'
               f'<div class="cal-nav"><a class="btn secondary" href="{e(href(date=today.isoformat()))}">Today</a>'
               f'<a class="btn subtle" href="{e(href(date=(day - step).isoformat()))}" aria-label="Previous {view}">'
               f'‹</a><a class="btn subtle" href="{e(href(date=(day + step).isoformat()))}" aria-label="Next {view}">›</a>'
               f'<h2 class="cal-title">{e(title)}</h2></div>'
               f'<div class="cal-views" role="group" aria-label="View">'
               + "".join(f'<a class="btn {"selected" if v == view else "subtle"}" href="{e(href(view=v, date=day.isoformat()))}"'
                         f'{" aria-current=true" if v == view else ""}>{lbl}</a>' for v, lbl in (("day", "Day"), ("week", "Week")))
               + f'</div><form method="get" action="/schedule" class="cal-who"><input type="hidden" name="view" '
                 f'value="{view}"><input type="hidden" name="date" value="{day.isoformat()}"><label>'
                 f'<span class="visually-hidden">Whose calendar</span><select name="who">{pick}</select></label>'
                 f'<button class="secondary">Show</button></form>'
               + (f'<a class="btn secondary cal-squeeze" href="/schedule/new?'
                  f'{e(urllib.parse.urlencode({"overbook": "1", "type": squeeze[0], "date": day.isoformat()}))}" '
                  f'data-modal>{e(squeeze[1].booking["overbook_label"])}</a>' if squeeze else "")
               + '</div>')
        if not cols:
            body = bar + ('<p class="muted">Nobody is booked on this calendar: the practice names no one whose time '
                          'it books.</p>')
            return self.layout(req, "Schedule", body, crumbs=[(None, "Schedule")])

        # the grid: a time column, then for each day each member's column
        windows = [self._hours(d) for d in open_days]
        opens = [w[0].time() for w in windows if w]
        closes = [w[1].time() for w in windows if w]
        for x in shown:  # a booking outside the hours widens the grid to show it
            if x["start"].date() in open_days and (x["with"] and any(c["id"] in x["with"] for c in cols)):
                opens.append(x["start"].time())
                closes.append(x["end"].time() if x["end"].date() == x["start"].date() else datetime.max.time())
        if not opens:
            grid = '<p class="muted cal-closed">Closed: nothing is booked.</p>'
            listing = grid
        else:
            first = datetime.combine(day, min(opens))
            first = first.replace(minute=first.minute - first.minute % slot)
            last = datetime.combine(day, max(closes))
            rows = max(1, -(-int((last - first).total_seconds() // 60) // slot))
            grid = self._grid(me, open_days, cols, shown, first.time(), rows, slot, now, writable)
            listing = self._listing(me, open_days, cols, shown, now, writable)
        hidden = sum(1 for x in shown if not x["sees"])
        legend = ('<p class="muted cal-legend">Busy: booked, for someone you may not read about.</p>' if hidden else "")
        if any(x.get("over") and not x["cancelled"] for x in shown):
            legend += ('<p class="muted cal-legend"><span class="cal-key over" aria-hidden="true"></span> Booked over '
                       'another booking: both are kept, side by side.</p>')
        body = (bar + f'<div class="cal-wrap">{grid}</div><div class="cal-list">{listing}</div>{legend}')
        return self.layout(req, "Schedule", body, crumbs=[(None, "Schedule")])

    def _free(self, member: str, start: datetime, end: datetime, shown: list[dict]) -> bool:
        return not any(member in x["with"] and not x["cancelled"] and x["start"] < end and start < x["end"]
                       for x in shown)

    def _grid(self, me, days, cols, shown, first, rows, slot, now, writable) -> str:
        """Day or week: one column per member per day, rows of slots; free slots are links to book them."""
        heads, colsh = [], []
        times = "".join(f'<div class="cal-time" style="grid-row:{i + 1}">'
                        f'{(datetime.combine(days[0], first) + timedelta(minutes=i * slot)):%H:%M}</div>'
                        if (i * slot) % 60 == 0 or i == 0 else "" for i in range(rows))
        for d in days:
            row0 = datetime.combine(d, first)
            hours = self._hours(d)
            today = d == now.date()
            heads.append(f'<div class="cal-day{" today" if today else ""}" style="grid-column:span {len(cols)}">'
                         f'{"<span class=visually-hidden>Today, </span>" if today else ""}'
                         f'<a href="/schedule?view=day&amp;date={d.isoformat()}">{d:%a} {d.day}</a></div>')
            for c in cols:
                cells = []
                for i in range(rows):
                    t = row0 + timedelta(minutes=i * slot)
                    inside = bool(hours and hours[0] <= t and t + timedelta(minutes=slot) <= hours[1])
                    if not inside:
                        cells.append(f'<div class="cal-off" style="grid-row:{i + 1}"></div>')
                    elif writable and t >= now and self._free(c["id"], t, t + timedelta(minutes=slot), shown):
                        q = urllib.parse.urlencode({"with": c["id"], "at": t.isoformat(timespec="minutes")})
                        cells.append(f'<a class="cal-slot" style="grid-row:{i + 1}" href="/schedule/new?{e(q)}" '
                                     f'data-modal><span class="visually-hidden">Book {e(c["name"])} on '
                                     f'{t:%a %b} {t.day} at </span>{t:%H:%M}</a>')
                    else:
                        cells.append(f'<div class="cal-cell" style="grid-row:{i + 1}"></div>')
                mine = sorted(({**x} for x in shown if c["id"] in x["with"] and x["start"].date() == d),
                              key=lambda x: (x["cancelled"], x["start"]))
                self._lanes(mine)
                evs = "".join(self._block(x, row0=row0, slot=slot) for x in mine)
                mark = ""
                if today and row0 <= now < row0 + timedelta(minutes=rows * slot):
                    mins = (now - row0).total_seconds() / 60
                    mark = (f'<div class="cal-now" style="grid-row:{int(mins // slot) + 1};--f:{(mins % slot) / slot:.2f}"'
                            f' aria-hidden="true"></div>')
                colsh.append(f'<div class="cal-col{" today" if today else ""}" style="--rows:{rows}" '
                             f'aria-label="{e(c["name"])}, {d:%A %B} {d.day}">{"".join(cells)}{evs}{mark}</div>')
        names = "".join(f'<div class="cal-who-h" title="{e(c["name"])}">{e(c["name"])}</div>'
                        for _ in days for c in cols)
        n = len(days) * len(cols)
        return (f'<div class="cal" style="--cols:{n}"><div class="cal-corner"></div>{"".join(heads)}'
                f'<div class="cal-corner"></div>{names}<div class="cal-times" style="--rows:{rows}">{times}</div>'
                f'{"".join(colsh)}</div>')

    def _listing(self, me, days, cols, shown, now, writable) -> str:
        """The same days as a list (what a phone shows): each day's bookings by time, then its next free slots."""
        out = []
        slot = self._slot()
        for d in days:
            items = sorted((x for x in shown if x["start"].date() == d and any(c["id"] in x["with"] for c in cols)),
                           key=lambda x: x["start"])
            lis = "".join(f'<li>{self._block(x)}<span class="muted who">'
                          f'{e(", ".join(self.member_name(m) for m in x["with"]))}</span></li>' for x in items)
            free = []
            hours = self._hours(d)
            if writable and hours:
                for c in cols:
                    t = hours[0]
                    while t + timedelta(minutes=slot) <= hours[1] and len([f for f in free if f[1] is c]) < 3:
                        if t >= now and self._free(c["id"], t, t + timedelta(minutes=slot), shown):
                            free.append((t, c))
                        t += timedelta(minutes=slot)
            slots = "".join(
                f'<a class="btn subtle" href="/schedule/new?{e(urllib.parse.urlencode({"with": c["id"], "at": t.isoformat(timespec="minutes")}))}" '
                f'data-modal>{t:%H:%M} {e(c["name"])}</a>' for t, c in sorted(free, key=lambda f: f[0]))
            today = " today" if d == now.date() else ""
            out.append(f'<section class="cal-listday{today}"><h3>{d:%A %B} {d.day}'
                       f'{" <span class=loz>Today</span>" if today else ""}</h3>'
                       + (f'<ul class="plain">{lis}</ul>' if lis else '<p class="muted">Nothing booked.</p>')
                       + (f'<p class="cal-free"><span class="muted">Free:</span> {slots}</p>' if slots else "")
                       + "</section>")
        return "".join(out)

    # ------------------------------------------------------------------------------------------ booking a slot
    def _kind_for(self, me, q: dict) -> str:
        kinds = self.booking_kinds(me, write=True)
        if not kinds:
            raise Denied(f"{me['name']} may not book")
        want = q.get("type") or ""
        if want:
            if want not in kinds:
                raise Invalid(f"{want} is not a booking {me['name']} may make")
            return want
        return kinds[0]

    def _may_give_reason(self, me, rt) -> bool:
        """Whether the booking form asks this member why: not when the reason is of a class they may not write."""
        held = (rt.base_map.get("reason") or {}).get("fields") or []
        fs = [rt.field(n) for n in held if rt.field(n)]
        return bool(fs) and all(visibility.may_field(self.hub, me, f, write=True) for f in fs)

    def _base_field(self, me, rt, base: str) -> dict | None:
        """The one field the type keeps a base field in, when this member may write it."""
        held = (rt.base_map.get(base) or {}).get("fields") or []
        f = rt.field(held[0]) if len(held) == 1 else None
        return f if f and visibility.may_field(self.hub, me, f, write=True) else None

    def _request(self, me, rt):
        """The workflow a member who may not give the reason books through (``[booking] request``), when there is one
        they may start: ``(workflow, the inputs the form asks)``; else None, and they book the time alone."""
        wid = (rt.booking or {}).get("request")
        if not wid or self._may_give_reason(me, rt):
            return None
        try:
            wf = self.hub.workflow(wid)[0]
        except (NotFound, KeyError):
            return None
        if not self.hub.can(me, "starter", f"workflow:{wf.id}"):
            return None
        want = rt.booking.get("request_inputs") or []
        return wf, [i for i in wf.inputs if i["name"] in want and i["type"] != "reference"]

    def _choice_field(self, f: dict, name: str, value) -> str:
        choices = [{"value": c, "label": c} for c in f.get("choices") or []] or None
        return self._field({**f, "name": name, "required": False}, choices, value or None)

    def _overbook_rows(self, me, rt, q: dict) -> str:
        """For a member who may book over another booking: the type's words for doing so, and for why."""
        if not self.hub.may_overbook(me, rt):
            return ""
        b = rt.booking
        on = str(q.get("overbook") or "") in ("1", "on", "true")
        return (f'<fieldset class="overbook"><legend class="visually-hidden">{e(b["overbook_label"])}</legend>'
                f'<label class="check"><input type="checkbox" name="overbook" value="1"{" checked" if on else ""}> '
                f'{e(b["overbook_label"])}</label>'
                f'<label>{e(b["overbook_reason"])} <input name="overbook_reason" maxlength="300" autocomplete="off" '
                f'value="{e(str(q.get("overbook_reason") or ""))}"></label>'
                f'<small class="help">Books it even when the time is taken: both bookings are kept, side by side, '
                f'and the calendar marks it.</small></fieldset>')

    def _subject_field(self, rt) -> dict | None:
        held = (rt.base_map.get("subject") or {}).get("fields") or []
        return rt.field(held[0]) if len(held) == 1 else None

    def page_booking_new(self, req, kept: dict | None = None, error: Exception | None = None):
        """Book a slot: the member and time come from the calendar; it asks whom for and why."""
        me, q = req["me"], {**req["query"], **(kept or {})}
        rtype = self._kind_for(me, q)
        rt = self.hub.record_type(rtype)
        members = self.calendar_members()
        who = q.get("with") or ""
        at = str(q.get("at") or "")[:16]
        sf = self._subject_field(rt)
        try:
            when = datetime.fromisoformat(at) if at else None
        except ValueError:
            when = None
        rows = []
        if who in {m["id"] for m in members} and when:
            rows.append(f'<input type="hidden" name="with" value="{e(who)}"><input type="hidden" name="at" '
                        f'value="{e(at)}"><p class="booking-when"><b>{e(self.member_name(who))}</b>, '
                        f'{when:%A %B} {when.day} at {when:%H:%M} ({self._slot()} min)</p>')
        else:  # opened without a slot (from a subject's page): which member, and when
            opts = "".join(f'<option value="{e(m["id"])}"{" selected" if m["id"] == who else ""}>{e(m["name"])}</option>'
                           for m in members)
            rows.append(f'<label>With <select name="with" required><option value=""></option>{opts}</select></label>'
                        f'<label>When <input type="datetime-local" name="at" value="{e(at)}" required '
                        f'step="{self._slot() * 60}"></label>')
        seed = {"resourceType": rtype}
        subj = str(q.get("subject") or "")
        if sf and subj:
            seed = schema.put(seed, sf["path"], {"reference": subj} if "/" in subj else subj)
        if sf:
            ctl = self._input({**sf, "required": True, "name": "subject"}, seed, me).replace(
                f'name="{e(sf["path"].removesuffix(".*"))}"', 'name="subject"', 1)
            rows.append(ctl)
        request = self._request(me, rt)
        asked = {i["name"] for i in request[1]} if request else set()
        kf = self._base_field(me, rt, "kind")
        if kf and "kind" not in asked:
            rows.append(self._choice_field(kf, "kind", q.get("kind")))
        if self._may_give_reason(me, rt):
            rows.append(f'<label>Reason <input name="reason" value="{e(str(q.get("reason") or ""))}" maxlength="200" '
                        f'autocomplete="off"></label>')
            pf = self._base_field(me, rt, "priority")
            if pf:
                rows.append(self._choice_field(pf, "priority", q.get("priority")))
        for i in request[1] if request else []:  # the request's own inputs: its screen reads the reason
            rows.append(self._field({**i, "name": f"req_{i['name']}"},
                                    [{"value": c, "label": c} for c in i["choices"]] if i.get("choices") else None,
                                    q.get(f"req_{i['name']}") or None))
        if request:
            rows.append(f'<p class="muted help">Booked through {e(request[0].title.lower())}: if the reason may be '
                        f'urgent it goes to the clinical team first, and is not booked until they have seen it.</p>')
        rows.append(self._overbook_rows(me, rt, q))
        if len(self.booking_kinds(me, write=True)) > 1 or q.get("type"):
            rows.append(f'<input type="hidden" name="type" value="{e(rtype)}">')
        lc = rt.lifecycle
        start = f' It starts as <b>{e(lc["initial"])}</b>.' if lc and lc.get("initial") else ""
        err = ""
        if error is not None:
            probs = getattr(error, "problems", None) or []
            err = (f'<div class="flash error" role="alert"><p>{e(str(error))}</p>'
                   + (f'<ul>{"".join(f"<li>{e(p)}</li>" for p in probs)}</ul>' if probs else "") + "</div>")
        body = (f'<div data-modal-body>{err}<form method="post" action="/schedule/new" class="card stack" id="book">'
                f'<p class="muted">{e(a_an_label(rt.label))}.{start}</p>{"".join(rows)}'
                f'<div class="filters"><button class="primary">Book</button>'
                f'<a class="btn secondary" href="/schedule" data-modal-close>Cancel</a></div></form></div>')
        return self.layout(req, f"New {rt.label.lower()}", body, crumbs=[("/schedule", "Schedule"), (None, "Book")])

    def post_booking_new(self, req):
        from .server import Rendered
        me = req["me"]
        f = self.form(req)
        f = {k: (v[0] if isinstance(v, list) else v) for k, v in f.items()}
        try:
            rtype = self._kind_for(me, f)
            rt = self.hub.record_type(rtype)
            who = f.get("with") or ""
            if who not in {m["id"] for m in self.calendar_members()}:
                raise Invalid("say whom the booking is with")
            try:
                start = datetime.fromisoformat(str(f.get("at") or "")[:16])
            except ValueError:
                raise Invalid("say when the booking is (a date and time)") from None
            sf = self._subject_field(rt)
            subject = None
            if sf:
                typed = str(f.get("subject") or "").strip()
                if not typed:
                    raise Invalid(f"say whom it is for ({sf['label'].lower()})")
                subject = self._resolve_ref(sf, typed)
                self.hub.read_record(me, subject)  # one the member may read: never booked blind
            minutes = self._slot()
            why = str(f.get("reason") or "").strip()
            extra = {k: str(f[k]) for k in ("kind", "priority") if f.get(k) and self._base_field(me, rt, k)}
            if "priority" in extra and not why:
                extra.pop("priority")
            rec = schema.compose(rt, {"start": start.isoformat(timespec="minutes"),
                                      "end": (start + timedelta(minutes=minutes)).isoformat(timespec="minutes"),
                                      **({"reason": why} if why else {}), "subject": subject,
                                      "with": [who], **extra}, self.hub.member_ref_type())
            over = str(f.get("overbook_reason") or "").strip() if f.get("overbook") else ""
            if f.get("overbook") and not over:
                raise Invalid(f"say {(rt.booking or {}).get('overbook_reason', 'why').lower()}")
            request = self._request(me, rt)
            if request and not why and subject and subject.split("/")[0] == request[0].record:
                return self._book_by_request(req, me, rt, request[0], rec, subject, start, who, f)
            self.hub.create_record(me, rtype, rec, overbook=over or None)
        except (Invalid, NotFound, Denied) as exc:
            if isinstance(exc, NotFound):
                exc = Invalid(str(exc))
            raise Rendered(422, self.page_booking_new({**req, "query": {}}, kept=f, error=exc)) from None
        raise self._redirect(f"/schedule?date={start.date().isoformat()}#cal")

    def _book_by_request(self, req, me, rt, wf, rec: dict, subject: str, start: datetime, who: str, f: dict):
        """Book through the type's request workflow: the time must be free now; the workflow is given the slot and
        what the member typed, and books it, or sends it on to someone first. The page says which."""
        clashes = self.hub.booking_clashes(rt, {"resourceType": rt.id, **rec})
        if clashes:
            raise Invalid(f"the {rt.label.lower()} clashes with another booking",
                          [self.hub._clash_text(c, w) for c, w in clashes])
        inputs = {i["name"]: f[f"req_{i['name']}"] for i in wf.inputs if f.get(f"req_{i['name']}") not in (None, "")}
        inputs["slot"] = f"{start.isoformat(timespec='minutes')}|{who}"
        run = self.hub.engine.start(me, wf.id, record=subject, inputs=inputs)
        if self._run_bookings(run["id"]):
            raise self._redirect(f"/schedule?date={start.date().isoformat()}#cal")
        waiting = [it for it in self.hub.db.items.find({"run_id": run["id"], "status": {"$in": ["open", "claimed"]}})]
        roles = sorted({it.get("role") or "" for it in waiting} - {""})
        names = ", ".join(self.hub.role(r)["name"] for r in roles if self.hub._role_known(r)) or "someone"
        body = (f'<div data-modal-body><div class="flash warning" role="status"><p><b>Not booked yet.</b> '
                f'{e(self.member_name(who))}, {start:%A %B} {start.day} at {start:%H:%M} is still free: the reason '
                f'went to {e(names)} first.</p></div><div class="card stack"><ul>'
                + "".join(f'<li>{e(it.get("title") or it.get("step_id") or "")}</li>' for it in waiting)
                + f'</ul><div class="filters"><a class="btn primary" href="/runs/{e(run["id"])}">See the request</a>'
                  f'<a class="btn secondary" href="/schedule?date={start.date().isoformat()}" data-modal-close>'
                  f'Back to the calendar</a></div></div></div>')
        return self.layout(req, "Not booked yet", body, crumbs=[("/schedule", "Schedule"), (None, "Not booked yet")])

    def _run_bookings(self, run_id: str) -> list[str]:
        """The bookings a run made (``booking.book`` steps that ran)."""
        ev = self.hub.db.events.find({"run_id": run_id, "action": "record.created"})
        return [x["subject"] for x in ev if self.hub.is_a((x.get("subject") or "/").split("/")[0], "Booking")]

    # ------------------------------------------------------------------------------------------ moving a booking
    def _booking(self, me, rtype, rid):
        rt = self.hub.record_type(rtype)
        if not rt or not rt.is_a("Booking"):
            raise NotFound(f"{rtype} is not a booking")
        rec = self.hub.read_record(me, f"{rtype}/{rid}")
        return rt, rec, schema.project(rt, rec, self.hub.member_types())

    def page_reschedule(self, req, rtype, rid, kept: dict | None = None, error: Exception | None = None):
        """Move a booking: another time, or another member (the one whose column it is in)."""
        me = req["me"]
        rt, rec, seen = self._booking(me, rtype, rid)
        if not self.hub.may_write(me, rtype):
            raise Denied(f"{me['name']} may not move {rt.plural.lower()}")
        if seen.get("cancelled") or not schema.is_active(rt, rec):
            raise Invalid(f"this {rt.label.lower()} no longer holds its time (cancelled or over): book a new one")
        span = self.hub.booking_span(seen, self._slot())
        k = kept or {}
        at = str(k.get("at") or (span[0].isoformat(timespec="minutes") if span else ""))[:16]
        held = list(seen.get("with") or [])
        members = self.calendar_members()
        cur = k.get("with") or (held[0] if held else "")
        opts = "".join(f'<option value="{e(m["id"])}"{" selected" if m["id"] == cur else ""}>{e(m["name"])}</option>'
                       for m in members)
        mins = int((span[1] - span[0]).total_seconds() // 60) if span else self._slot()
        err = ""
        if error is not None:
            probs = getattr(error, "problems", None) or []
            err = (f'<div class="flash error" role="alert"><p>{e(str(error))}</p>'
                   + (f'<ul>{"".join(f"<li>{e(p)}</li>" for p in probs)}</ul>' if probs else "") + "</div>")
        url = f"/records/{e(rtype)}/{e(rid)}/reschedule"
        body = (f'<div data-modal-body>{err}<form method="post" action="{url}" class="card stack" id="reschedule">'
                f'<p class="muted">{e(self.hub.title(rec))}: {mins} min'
                + (f', now {span[0]:%A %B} {span[0].day} at {span[0]:%H:%M}' if span else "") + '.</p>'
                f'<input type="hidden" name="_from" value="{e(held[0] if held else "")}">'
                f'<input type="hidden" name="_version" value="{e(str((rec.get("meta") or {}).get("versionId", "")))}">'
                f'<label>When <input type="datetime-local" name="at" value="{e(at)}" required '
                f'step="{self._slot() * 60}"></label>'
                + (f'<label>With <select name="with" required>{opts}</select></label>' if members and held else "")
                + self._overbook_rows(me, rt, k)
                + f'<div class="filters"><button class="primary">Move</button>'
                  f'<a class="btn secondary" href="{e(self.record_href(f"{rtype}/{rid}"))}" data-modal-close>Cancel</a>'
                  f'</div></form></div>')
        return self.layout(req, f"Move {a_an_label(rt.label).lower()}", body,
                           crumbs=[("/schedule", "Schedule"), (self.record_href(f"{rtype}/{rid}"), self.hub.title(rec)),
                                   (None, "Move")])

    def post_reschedule(self, req, rtype, rid):
        from .server import Rendered
        me = req["me"]
        f = {k: (v[0] if isinstance(v, list) else v) for k, v in self.form(req).items()}
        rt, rec, seen = self._booking(me, rtype, rid)
        try:
            try:
                start = datetime.fromisoformat(str(f.get("at") or "")[:16])
            except ValueError:
                raise Invalid("say when (a date and time)") from None
            span = self.hub.booking_span(seen, self._slot())
            length = (span[1] - span[0]) if span else timedelta(minutes=self._slot())
            values = {"start": start.isoformat(timespec="minutes")}
            if seen.get("end"):
                values["end"] = (start + length).isoformat(timespec="minutes")
            new = schema.compose(rt, values, self.hub.member_ref_type(), {k: v for k, v in rec.items() if k != "meta"})
            old, to = str(f.get("_from") or ""), str(f.get("with") or "")
            if to and old and to != old:
                if to not in {m["id"] for m in self.calendar_members()}:
                    raise Invalid(f"{to} is not on the calendar")
                new = self._swap_member(rt, new, old, to)
            over = str(f.get("overbook_reason") or "").strip() if f.get("overbook") else ""
            if f.get("overbook") and not over:
                raise Invalid(f"say {(rt.booking or {}).get('overbook_reason', 'why').lower()}")
            self.hub.update_record(me, f"{rtype}/{rid}", new, expected_version=f.get("_version") or None,
                                   overbook=over or None)
        except Denied as exc:
            raise Rendered(403, self.page_reschedule({**req, "query": {}}, rtype, rid, kept=f, error=exc)) from None
        except Invalid as exc:
            raise Rendered(409 if exc.__class__.__name__ == "Conflict" else 422,
                           self.page_reschedule({**req, "query": {}}, rtype, rid, kept=f, error=exc)) from None
        raise self._redirect(self.record_href(f"{rtype}/{rid}"))

    def _swap_member(self, rt, rec: dict, old: str, new: str) -> dict:
        """The booking held by ``new`` where it was held by ``old``, in the field the type keeps its members in."""
        held = (rt.base_map.get("with") or {}).get("fields") or []
        if not held:
            raise Invalid(f"a {rt.label.lower()} keeps no members to move it to")
        mref = self.hub.member_ref_type()
        top = {rt.field(n)["path"].split(".")[0] for n in held}

        def swap(v):
            if isinstance(v, str):
                return {f"{mref}/{old}": f"{mref}/{new}", f"member:{old}": f"member:{new}", old: new}.get(v, v)
            if isinstance(v, dict):
                return {k: swap(x) for k, x in v.items()}
            if isinstance(v, list):
                return [swap(x) for x in v]
            return v
        return {k: (swap(v) if k in top else v) for k, v in rec.items()}

    # ------------------------------------------------------------------------------------------ on a subject's page
    def upcoming_section(self, me, ref: str) -> str:
        """A subject's coming bookings, soonest first (the ones this member may read), with a button to book one."""
        kinds = self.hub.of_base("Booking")
        if not kinds:
            return ""
        from ..engine import bookings
        now = self._now()
        mine = [b for b in bookings(self.hub, since=now.date().isoformat(), member=me) if b.get("subject") == ref
                and self._may_read(me, b["type"]) and not b["cancelled"]]
        mine = [b for b in mine if (self.hub.booking_span(b, self._slot()) or (now,))[-1] >= now]
        mine.sort(key=lambda b: b["start"])
        book = (f'<a class="btn secondary" href="/schedule/new?subject={e(urllib.parse.quote(ref, safe=""))}" data-modal>'
                f'Book</a>' if self.booking_kinds(me, write=True) else "")
        if not mine and not book:
            return ""
        lis = "".join(
            f'<li><a href="{e(self.record_href(f"{b["type"]}/{b["record"]["id"]}"))}">'
            f'{e(datetime.fromisoformat(b["start"][:16]).strftime("%a %b %d, %H:%M"))}</a> '
            f'<span class="muted">{e(", ".join(self.member_name(m) for m in b["with"]))}'
            f'{" · " + e(str(b["reason"])) if b["reason"] else ""}</span></li>' for b in mine[:10])
        return (f'<section class="chart-section upcoming" id="upcoming" aria-labelledby="upcoming-h"><header>'
                f'<h2 id="upcoming-h">Upcoming <span class="count-badge">{len(mine)}</span></h2>'
                + ("" if mine else '<span class="muted none">Nothing booked</span>') + f'{book}</header>'
                + (f'<ul class="plain body">{lis}</ul>' if lis else "") + "</section>")


def a_an_label(label: str) -> str:
    from .records_pages import a_an
    words = a_an(label.lower())
    return words[0].upper() + words[1:]
