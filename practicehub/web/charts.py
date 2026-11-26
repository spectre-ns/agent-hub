"""A view's line chart, drawn on the server as SVG: a value over months, quarters or years, a line per series.

It needs no script. Each column of the plot has a hover target, wider than the marks, whose tooltip names every
series' value there; the numbers are also in a table below the chart (``<details>``), so nothing is read from
colour alone. Series take the categorical colours in a fixed order (``--series-1`` to ``--series-8`` in
``assets.CSS``, stepped separately for the dark theme); text is in the page's text colours, never a series'.
"""

from __future__ import annotations

import html
import math

from .. import decimals

W, H = 720, 240
TOP, BOTTOM, LEFT = 16, 28, 56
END = 150          # room on the right for the end labels
MAX_X_LABELS = 6
GAP = 14           # end labels closer than this would collide: the legend names the lines instead


def e(s) -> str:
    return html.escape(str(s), quote=True)


def _f(v) -> float | None:
    n = decimals.number(v)
    return None if n is None else float(n)


def _ticks(lo: float, hi: float) -> list[float]:
    """Three to six round values from at or below ``lo`` to at or above ``hi``; zero among them when in range."""
    if lo == hi:
        lo, hi = (lo - 1, hi + 1) if lo else (0.0, 1.0)
    raw = (hi - lo) / 4
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    v, out = math.floor(lo / step) * step, []
    while True:  # from the round value at or below lo up to the first at or above hi
        out.append(round(v, 10))
        if v >= hi - step * 1e-9 or len(out) >= 12:
            return out
        v += step


def _tick_text(v: float, ticks: list[float]) -> str:
    if all(float(t).is_integer() for t in ticks):
        return format(int(v), ",")
    return format(v, ",.2f").rstrip("0").rstrip(".")


def _short(s: str, n: int = 20) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def line_chart(line: dict, title: str) -> str:
    """``line`` as ``views_query.finish`` makes it: ``x`` (labels), ``series`` (``name``, ``values``, ``texts``),
    ``unit`` and ``measure``."""
    xs, series = line.get("x") or [], line.get("series") or []
    if not xs or not series:
        return "<p class='muted'>Nothing yet.</p>"
    vals = [_f(v) for s in series for v in s["values"]]
    have = [v for v in vals if v is not None]
    if not have:
        return "<p class='muted'>Nothing to draw: no value in any of these.</p>"
    ticks = _ticks(min(0.0, *have), max(0.0, *have))
    lo, hi = ticks[0], ticks[-1]
    pw, ph = W - LEFT - END, H - TOP - BOTTOM
    n = len(xs)

    def X(i):
        return LEFT + (pw * i / (n - 1) if n > 1 else pw / 2)

    def Y(v):
        return TOP + ph * (hi - v) / (hi - lo)

    out = []
    for t in ticks:  # hairline grid; the zero line stronger
        y = Y(t)
        out.append(f'<line class="{"vl-zero" if t == 0 else "vl-grid"}" x1="{LEFT}" x2="{LEFT + pw}" '
                   f'y1="{y:.1f}" y2="{y:.1f}"/>'
                   f'<text class="vl-tick" x="{LEFT - 8}" y="{y + 4:.1f}" text-anchor="end">'
                   f'{e(_tick_text(t, ticks))}</text>')
    step = max(1, math.ceil(n / MAX_X_LABELS))
    shown = [i for i in range(0, n, step)]
    if shown[-1] != n - 1:
        if n - 1 - shown[-1] < step:
            shown.pop()
        shown.append(n - 1)
    for i in shown:
        anchor = "middle" if 0 < i < n - 1 or n == 1 else ("start" if i == 0 else "end")
        out.append(f'<text class="vl-tick" x="{X(i):.1f}" y="{H - 8}" text-anchor="{anchor}">{e(xs[i])}</text>')
    ends = []
    for k, s in enumerate(series):
        cls = f"vl-s{k % 8 + 1}"
        pts = [(X(i), Y(v)) if v is not None else None for i, v in enumerate(_f(x) for x in s["values"])]
        runs, cur = [], []
        for p in pts:  # a missing value breaks the line, it is not drawn as zero
            if p is None:
                if cur:
                    runs.append(cur)
                cur = []
            else:
                cur.append(p)
        if cur:
            runs.append(cur)
        for r in runs:
            if len(r) == 1:
                out.append(f'<circle class="vl-dot {cls}" cx="{r[0][0]:.1f}" cy="{r[0][1]:.1f}" r="4"/>')
            else:
                d = " ".join(f"{'M' if j == 0 else 'L'}{x:.1f},{y:.1f}" for j, (x, y) in enumerate(r))
                out.append(f'<path class="vl-line {cls}" d="{d}"/>')
        last = next((i for i in range(n - 1, -1, -1) if pts[i] is not None), None)
        if last is not None:
            x, y = pts[last]
            out.append(f'<circle class="vl-end {cls}" cx="{x:.1f}" cy="{y:.1f}" r="4"/>')
            ends.append((y, s["name"], s["texts"][last], x))
    one = len(series) == 1
    ends.sort()
    apart = all(b[0] - a[0] >= GAP for a, b in zip(ends, ends[1:]))
    if one or (len(series) <= 4 and apart):  # direct labels, at each line's end
        for y, name, text, x in ends:
            label = text if one else f"{_short(name)} {text}"
            out.append(f'<text class="vl-label" x="{x + 8:.1f}" y="{y + 4:.1f}">{e(label)}</text>')
    for i in range(n):  # a hover target per column, wider than the marks: every series' value there
        left = X(i) - (pw / (n - 1) / 2 if n > 1 else pw / 2)
        width = pw / (n - 1) if n > 1 else pw
        tip = f"{xs[i]}: " + "; ".join(
            (s["texts"][i] if one else f"{s['name']} {s['texts'][i]}") for s in series)
        out.append(f'<rect class="vl-hit" x="{max(left, LEFT - 8):.1f}" y="{TOP}" width="{width:.1f}" '
                   f'height="{ph}"><title>{e(tip)}</title></rect>')
    label = f"{title}: {line.get('measure', '')}, {xs[0]} to {xs[-1]}" if n > 1 else f"{title}: {xs[0]}"
    svg = (f'<svg class="line-chart" viewBox="0 0 {W} {H}" role="img" aria-label="{e(label)}" '
           f'preserveAspectRatio="xMinYMin meet">{"".join(out)}</svg>')
    legend = "" if one else "<ul class='vl-legend'>" + "".join(
        f"<li><span class='vl-key vl-s{k % 8 + 1}'></span>{e(s['name'])}</li>" for k, s in enumerate(series)) + "</ul>"
    unit = f" ({line['unit']})" if line.get("unit") else ""
    heads = "".join(f"<th scope='col'>{e(s['name'] if not one else line.get('measure', ''))}{e(unit)}</th>"
                    for s in series)
    body = "".join(f"<tr><th scope='row'>{e(x)}</th>" + "".join(f"<td class='num'>{e(s['texts'][i])}</td>"
                                                                 for s in series) + "</tr>"
                   for i, x in enumerate(xs))
    table = (f"<details class='vl-data'><summary>The numbers</summary><div class='tablewrap'><table><thead><tr>"
             f"<th scope='col'></th>{heads}</tr></thead><tbody>{body}</tbody></table></div></details>")
    return f"<figure class='vl'>{legend}{svg}</figure>{table}"
