"""Where the boxes and arrows of a small flow go: a record's states and the moves between them, top to bottom.

Geometry only, and nothing of this hub: names in, numbers out, the same numbers every time (the author's order of
the nodes breaks every tie; nothing is random and no set is walked). The caller draws the boxes, the arrows
(``path_d`` turns a route into an SVG path) and the words.

The method is the usual layered one, kept small:

1. **Ranks.** The start nodes are the top row. A walk from them (depth first, in the author's order) finds the moves
   that lead back to where the walk already is; those count as pointing the other way, for ranking only, so that no
   loop is left. A node's row is then the longest way to it from a node nothing leads to. The endings (``last``) go
   together on one row below everything else.
2. **Stand-ins.** A move that passes over rows gets a stand-in point in each row it passes. A stand-in takes a place
   in its row like a narrow node, so the move runs down a real gap between the boxes, not across them.
3. **Order.** Each row is put in the order of the average place of what it is joined to in the row above, then the
   row below, a few times over; the order with the fewest crossings seen is kept.
4. **Places.** Each row is laid out left to right and centred. The arrows that meet one side of a box are spread
   along it in the order of where their other ends are, so they do not stack on one point.
5. **Routes.** A move going down leaves the bottom of its box and enters the top of the next; one going up (a way
   back) leaves the top and enters the bottom. Between two rows a route is a short stub straight out of the box, one
   straight line across the gap, and a short stub straight in; through a row it is the upright line at its stand-in.
   Nothing but those upright lines is ever level with the boxes, which is why no arrow crosses a box, and two lines
   across one gap never start from the same point, which is why no two arrows lie on top of each other. A move
   between two nodes of one row (two endings, say) dips into the gap below the row and comes back up.
"""

from __future__ import annotations

import math

Point = tuple[float, float]


def layout(nodes: list[str], edges: list[tuple[str, str]], start: list[str], *, last: list[str] = (),
           w: int = 136, h: int = 34, gx: int = 28, gy: int = 60, pad: int = 16) -> dict:
    """The places of ``nodes`` (boxes ``w`` by ``h``, ``gx`` apart in a row, rows ``gy`` apart, ``pad`` around) and
    the routes of ``edges`` (pairs ``(a, b)``; repeats, loops on one node and unknown names are left out).

    Returns ``{"width", "height", "rank": {node: row}, "pos": {node: (x, y)}`` (the box's top left corner),
    ``"routes": {(a, b): [(x, y), ...]}`` (a line of points from the border of ``a`` to the border of ``b``),
    ``"labels": {(a, b): (x, y)}}`` (a point on the route, in the gap next to ``a``, to centre a few words on).
    """
    names = list(dict.fromkeys(nodes))
    known = set(names)
    pairs = list(dict.fromkeys((a, b) for a, b in edges if a != b and a in known and b in known))
    if not names:
        return {"width": float(2 * pad), "height": float(2 * pad), "rank": {}, "pos": {}, "routes": {}, "labels": {}}
    rank = _ranks(names, pairs, start, last)
    rows: list[list] = [[] for _ in range(max(rank.values()) + 1)]
    for n in names:
        rows[rank[n]].append(n)
    chains: dict = {}                             # a move between rows: upper node, its stand-ins, lower node
    level: list[list] = [[] for _ in rows]        # the moves between two nodes of one row, by row
    for a, b in pairs:
        if rank[a] == rank[b]:
            level[rank[a]].append((a, b))
            continue
        lo, hi = (a, b) if rank[a] < rank[b] else (b, a)
        mid = [(a, b, r) for r in range(rank[lo] + 1, rank[hi])]  # a stand-in is a tuple, so never a node's name
        for d in mid:
            rows[d[2]].append(d)
        chains[(a, b)] = [lo, *mid, hi]

    def spread(row: list) -> tuple[dict, float]:
        """The centre of each item of a row laid left to right from 0, and how wide the row is. A box is ``w`` wide
        and a stand-in has no width; ``gx`` is kept clear beside a box, half of it between two stand-ins."""
        xs, x, before = {}, 0.0, None
        for it in row:
            if before is not None:
                x += gx if isinstance(it, str) or isinstance(before, str) else gx / 2
            wide = w if isinstance(it, str) else 0
            xs[it] = x + wide / 2
            x += wide
            before = it
        return xs, x

    rows = _order(rows, chains, spread)
    width = float(max(spread(row)[1] for row in rows) + 2 * pad)
    cx = {}
    for row in rows:
        xs, span = spread(row)
        cx.update({it: x + (width - span) / 2 for it, x in xs.items()})
    top = lambda r: pad + r * (h + gy)
    pos = {n: (cx[n] - w / 2, float(top(rank[n]))) for n in names}

    # where each move meets its two boxes: the moves on one side of a box (0 its top, 1 its bottom), in the order of
    # where each comes from or goes to next, spread evenly along the side and kept off its corners
    sides: dict = {}
    for k, (a, b) in enumerate(pairs):
        ch = chains.get((a, b))
        if ch:
            sides.setdefault((ch[0], 1), []).append((cx[ch[1]], k, (a, b)))
            sides.setdefault((ch[-1], 0), []).append((cx[ch[-2]], k, (a, b)))
        else:                                     # along a row; a two-way pair nests, one inside the other
            sides.setdefault((a, 1), []).append((cx[b], k if cx[b] < cx[a] else -k, (a, b)))
            sides.setdefault((b, 1), []).append((cx[a], k if cx[a] < cx[b] else -k, (a, b)))
    inset = min(10, w / 4)
    port: dict = {}                               # (node, move) -> the x where they meet, and its place on the side
    for (n, _), ends in sides.items():
        for i, (_, _, move) in enumerate(sorted(ends)):
            port[(n, move)] = (pos[n][0] + inset + (w - 2 * inset) * (i + 0.5) / len(ends), i)

    stub = gy / 5
    routes: dict = {}
    labels: dict = {}
    for (a, b), ch in chains.items():
        marks = [(port[(ch[0], (a, b))][0], top(rank[ch[0]]) + h)]       # out of a row, into the next, out, into...
        for d in ch[1:-1]:
            marks += [(cx[d], top(d[2])), (cx[d], top(d[2]) + h)]
        marks.append((port[(ch[-1], (a, b))][0], top(rank[ch[-1]])))
        line = []
        for p, q in zip(marks[::2], marks[1::2]):                        # one gap: stub, straight across, stub
            line += [p, (p[0], p[1] + stub), (q[0], q[1] - stub), q]
        if rank[a] > rank[b]:
            line.reverse()
        # the words go on the line across the first gap; neighbours on a side take turns at two heights
        far = 0.45 if port[(a, (a, b))][1] % 2 else 0.15
        labels[(a, b)] = (line[1][0] + (line[2][0] - line[1][0]) * far, line[1][1] + (line[2][1] - line[1][1]) * far)
        routes[(a, b)] = _tidy(line)
    for r, moves in enumerate(level):
        foot = top(r) + h
        reach = lambda move: abs(port[(move[0], move)][0] - port[(move[1], move)][0])
        for i, (a, b) in enumerate(sorted(moves, key=reach)):            # each at its own depth, the longest lowest
            y = foot + stub + (gy - 2 * stub) * (i + 1) / (len(moves) + 1)
            xa, xb = port[(a, (a, b))][0], port[(b, (a, b))][0]
            routes[(a, b)] = [(xa, float(foot)), (xa, y), (xb, y), (xb, float(foot))]
            labels[(a, b)] = (xa + (xb - xa) / 3, y)
    height = max([top(len(rows) - 1) + h] + [y for line in routes.values() for _, y in line]) + pad
    return {"width": width, "height": float(height), "rank": rank, "pos": pos,
            "routes": {move: routes[move] for move in pairs}, "labels": {move: labels[move] for move in pairs}}


def path_d(points: list[tuple[float, float]], *, r: float = 8) -> str:
    """An SVG path ``d`` through the points, each corner rounded: the line stops ``r`` short of the corner (or half
    the shorter of the two lines that meet there, if that is less) and curves to the same distance past it. Points in
    one line, and a point repeated, are passed straight through. Numbers have at most one decimal."""
    pts = [(float(x), float(y)) for x, y in points]
    if not pts:
        return ""
    out = [f"M{_num(pts[0][0])},{_num(pts[0][1])}"]
    for before, (x, y), after in zip(pts, pts[1:], pts[2:]):
        d1 = math.hypot(x - before[0], y - before[1])
        d2 = math.hypot(after[0] - x, after[1] - y)
        cut = min(r, d1 / 2, d2 / 2)
        if cut <= 0 or abs(_turn(before, (x, y), after)) <= 1e-9 * d1 * d2:
            out.append(f"L{_num(x)},{_num(y)}")
            continue
        ax, ay = x + (before[0] - x) * cut / d1, y + (before[1] - y) * cut / d1
        bx, by = x + (after[0] - x) * cut / d2, y + (after[1] - y) * cut / d2
        out.append(f"L{_num(ax)},{_num(ay)} Q{_num(x)},{_num(y)} {_num(bx)},{_num(by)}")
    if len(pts) > 1:
        out.append(f"L{_num(pts[-1][0])},{_num(pts[-1][1])}")
    return " ".join(out)


def _ranks(names: list[str], pairs: list[tuple[str, str]], start: list[str], last: list[str]) -> dict[str, int]:
    """The row of each node: 0 for the start nodes, the last row for the endings, the longest way down for the rest."""
    place = {n: i for i, n in enumerate(names)}
    first = [n for n in dict.fromkeys(start) if n in place]
    ends = [n for n in dict.fromkeys(last) if n in place and n not in first]
    # a move into a start node or to or from an ending says nothing about rows: those rows are fixed
    out: dict[str, list[str]] = {n: [] for n in names}
    for a, b in pairs:
        if b not in first and a not in ends and b not in ends:
            out[a].append(b)
    for n in names:
        out[n].sort(key=place.get)
    down = []                                     # every move left, pointing down: one that leads back is turned
    state: dict[str, int] = {}                    # 1: the walk is on it, 2: the walk has left it
    for root in first + names:
        if root in state:
            continue
        state[root] = 1
        walk = [(root, iter(out[root]))]
        while walk:
            n, rest = walk[-1]
            for m in rest:
                if state.get(m) == 1:
                    down.append((m, n))
                    continue
                down.append((n, m))
                if m not in state:
                    state[m] = 1
                    walk.append((m, iter(out[m])))
                    break
            else:
                state[n] = 2
                walk.pop()
    rank = {n: 0 for n in names}
    for _ in names:                               # no loop is left, so this settles within one pass per node
        moved = False
        for a, b in down:
            if rank[b] < rank[a] + 1:
                rank[b], moved = rank[a] + 1, True
        if not moved:
            break
    below = max((rank[n] for n in names if n not in ends), default=-1) + 1
    for n in ends:
        rank[n] = below
    return rank


def _order(rows: list[list], chains: dict, spread) -> list[list]:
    """The rows, each in the order that crosses the fewest lines: a few sweeps down and up, each putting a row in
    the order of the average place of its neighbours in the row before (first order where that says nothing)."""
    above: dict = {it: [] for row in rows for it in row}
    below: dict = {it: [] for row in rows for it in row}
    for ch in chains.values():
        for u, v in zip(ch, ch[1:]):
            below[u].append(v)
            above[v].append(u)
    first = {it: i for row in rows for i, it in enumerate(row)}
    rows = [list(row) for row in rows]
    cx: dict = {}

    def settle(row: list) -> None:
        xs, span = spread(row)
        cx.update({it: x - span / 2 for it, x in xs.items()})

    def crossings() -> int:
        total = 0
        for upper, lower in zip(rows, rows[1:]):
            at = {it: i for i, it in enumerate(lower)}
            ends = [at[v] for u in upper for v in sorted(below[u], key=at.get)]
            total += sum(1 for i, a in enumerate(ends) for b in ends[i + 1:] if b < a)
        return total

    for row in rows:
        settle(row)
    best = (crossings(), [list(row) for row in rows])
    for turn in range(8):
        near, order = (above, range(1, len(rows))) if turn % 2 == 0 else (below, range(len(rows) - 2, -1, -1))
        for r in order:
            want = {it: sum(cx[o] for o in near[it]) / len(near[it]) if near[it] else cx[it] for it in rows[r]}
            rows[r].sort(key=lambda it: (want[it], first[it]))
            settle(rows[r])
        found = crossings()
        if found < best[0]:
            best = (found, [list(row) for row in rows])
    return best[1]


def _turn(a: Point, b: Point, c: Point) -> float:
    """Zero when the way from ``a`` through ``b`` to ``c`` does not turn."""
    return (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])


def _tidy(line: list[Point]) -> list[Point]:
    """The line without the points it passes straight through (a stub and the upright line it runs into)."""
    out: list[Point] = []
    for p in line:
        p = (float(p[0]), float(p[1]))
        if out and p == out[-1]:
            continue
        while len(out) > 1 and abs(_turn(out[-2], out[-1], p)) < 1e-9:
            out.pop()
        out.append(p)
    return out


def _num(v: float) -> str:
    """A number with at most one decimal, and no ``-0``."""
    text = f"{round(v, 1) + 0.0:.1f}"
    return text[:-2] if text.endswith(".0") else text
