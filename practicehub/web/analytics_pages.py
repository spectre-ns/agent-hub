"""Process analytics (``/settings/analytics``, and ``/api/analytics`` as JSON): for policy owners, the same gate as
the Track and the Access report. Counts and times only (``practicehub.analytics``): no record, no title of what the
work is about, no item or run ids."""

from __future__ import annotations

import html

from .. import analytics
from ..hub import Denied

e = html.escape

STYLE = ("<style>.pa-neck{border-left:4px solid var(--brand);padding:8px 12px;margin:0 0 12px;background:var(--sunken);"
         "border-radius:3px}.pa-facts{display:flex;flex-wrap:wrap;gap:8px 24px;margin:0 0 12px;padding:0;"
         "list-style:none}.pa-facts b{font-size:20px;display:block}.pa-cell{display:flex;align-items:center;gap:6px;"
         "white-space:nowrap}.pa-cell .bar{min-width:2px;max-width:80px}td.num,th.num{text-align:right;"
         "white-space:nowrap}.pa-flow h2{margin:0 0 8px}.pa-flow h3{font-size:14px;margin:12px 0 4px}</style>")


class AnalyticsPages:
    def analytics_routes(self, R):
        R("GET", r"/settings/analytics", self.page_analytics)
        R("GET", r"/api/analytics", self.api_analytics)

    def _analytics(self, req) -> dict:
        me = req["me"]
        if not analytics.may_see(self.hub, me):
            raise Denied("process analytics are read by a policy owner")
        days = str(req["query"].get("days") or "")
        return analytics.report(self.hub, me, days=int(days) if days.isdigit() else analytics.DEFAULT_DAYS)

    def api_analytics(self, req):
        return self._analytics(req)

    @staticmethod
    def _bar_cell(secs, top) -> str:
        if secs is None:
            return '<span class="muted">–</span>'
        width = max(2, round(80 * secs / top)) if top else 2
        return (f'<span class="pa-cell"><span class="bar" style="width:{width}px" aria-hidden="true"></span>'
                f'{e(analytics.duration(secs))}</span>')

    def _flow_html(self, w: dict) -> str:
        facts = [("Started", w["started"]), ("Finished", w["finished"]), ("Cancelled", w["cancelled"]),
                 ("Still open", w["open"]),
                 ("Time to finish (median)", analytics.duration(w["cycle_median"])),
                 ("Time to finish (90% within)", analytics.duration(w["cycle_p90"]))]
        if w["with_deadline"]:
            facts.append(("Deadlines missed", f'{w["deadline_missed"]} of {w["with_deadline"]}'))
        out = [f'<section class="card pa-flow"><h2>{e(w["title"])}</h2><ul class="pa-facts">'
               + "".join(f'<li><b>{e(str(v))}</b><span class="muted">{e(k)}</span></li>' for k, v in facts)
               + "</ul>", f'<p class="pa-neck">{e(analytics.bottleneck_words(w["bottleneck"]))}</p>']
        if w["steps"]:
            top = max([s["wait_median"] or 0 for s in w["steps"]] + [s["work_median"] or 0 for s in w["steps"]])
            rows = "".join(
                f'<tr><td>{e(s["title"])}</td><td class="num">{s["count"]}</td>'
                f'<td>{self._bar_cell(s["wait_median"], top)}</td><td>{self._bar_cell(s["work_median"], top)}</td>'
                f'<td class="num">{s["overdue"]} ({s["overdue_rate"]:.0%})</td>'
                f'<td class="num">{s["escalations"]}</td></tr>' for s in w["steps"])
            out.append('<h3>Steps</h3><div class="tablewrap"><table><thead><tr><th>Step</th><th class="num">Times</th>'
                       '<th>Waiting to be taken (median)</th><th>Taken to done (median)</th>'
                       '<th class="num">Overdue</th><th class="num">Escalations</th></tr></thead>'
                       f'<tbody>{rows}</tbody></table></div>')
        if w["open_now"]:
            most = max(a["count"] for a in w["open_by_age"]) or 1
            rows = "".join(
                f'<tr><td>{e(a["age"])}</td><td><span class="pa-cell"><span class="bar" aria-hidden="true" '
                f'style="width:{max(2, round(80 * a["count"] / most)) if a["count"] else 0}px"></span>{a["count"]}'
                f'</span></td></tr>' for a in w["open_by_age"])
            out.append(f'<h3>Open now, by age ({w["open_now"]})</h3><div class="tablewrap"><table><thead><tr>'
                       f'<th>Waiting for</th><th>Items</th></tr></thead><tbody>{rows}</tbody></table></div>')
        return "".join(out) + "</section>"

    def page_analytics(self, req):
        rep = self._analytics(req)
        tabs = '<nav class="tabs" aria-label="Period">' + "".join(
            f'<a href="/settings/analytics?days={d}"{" aria-current=page" if d == rep["days"] else ""}>'
            f'Last {d} days</a>' for d in analytics.PERIODS) + "</nav>"
        notes = []
        if rep["capped"]:
            notes.append(f'Only the newest {rep["max_runs"]:,} runs of this period are counted; older ones are left '
                         f'out.')
        if rep["open_capped"]:
            notes.append(f'Only the newest {rep["max_open"]:,} open items are counted in the ages.')
        intro = ('<p class="muted">How work moves through each workflow: counts and times only, never who the work is '
                 f'about. Runs started in the last {rep["days"]} days ({rep["runs"]:,}); open work is as it is now.</p>')
        note = "".join(f'<p class="flash" role="status">{e(n)}</p>' for n in notes)
        flows = "".join(self._flow_html(w) for w in rep["workflows"]) or \
            '<p class="muted">No work was started in this period.</p>'
        return self.layout(req, "Process analytics", STYLE + tabs + intro + note + flows,
                           crumbs=[("/settings", "Settings"), (None, "Process analytics")])
