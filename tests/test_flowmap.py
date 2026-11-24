"""The flow map's geometry (practicehub/web/flowmap.py): what must hold of every picture it lays out.

No hub and no database: the layout is names in, numbers out.
"""

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from practicehub.web.flowmap import layout, path_d

W, H, GX, GY, PAD = 136, 34, 28, 60, 16


@pytest.fixture(autouse=True)
def _database():
    """In place of conftest's: these tests are given no database, and ask for none."""
    yield


def _both_ways(names):
    return [(a, b) for a in names for b in names if a != b]


VISIT_NODES = ["start", "requested", "reading", "callback", "not-reached", "planned", "arrived", "triaged",
               "in-progress", "onleave", "drafting", "editing", "signing", "finished", "undocumented", "cancelled",
               "noshow", "entered-in-error"]
VISIT_EDGES = [
    ("start", "reading"), ("start", "planned"), ("start", "arrived"), ("start", "in-progress"),
    ("start", "requested"), ("start", "callback"), ("reading", "requested"), ("callback", "requested"),
    ("callback", "finished"), ("callback", "not-reached"), ("not-reached", "finished"),
    ("not-reached", "cancelled"), ("requested", "planned"), ("planned", "arrived"), ("planned", "in-progress"),
    ("planned", "cancelled"), ("planned", "noshow"), ("planned", "entered-in-error"), ("arrived", "triaged"),
    ("arrived", "in-progress"), ("triaged", "in-progress"), ("arrived", "cancelled"), ("in-progress", "onleave"),
    ("onleave", "in-progress"), ("in-progress", "drafting"), ("drafting", "editing"), ("editing", "signing"),
    ("editing", "drafting"), ("signing", "finished"), ("signing", "editing"), ("in-progress", "undocumented")]
VISIT_LAST = ["cancelled", "noshow", "entered-in-error", "finished", "undocumented"]

# name -> (nodes, edges, start, last)
GRAPHS = {
    "chain": (list("abcd"), [("a", "b"), ("b", "c"), ("c", "d")], ["a"], []),
    "diamond": (list("abcd"), [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d"), ("a", "d")], ["a"], []),
    "loop": (list("abcd"), [("a", "b"), ("b", "c"), ("c", "a"), ("c", "d")], ["a"], []),
    "two-way": (list("abc"), [("a", "b"), ("b", "a"), ("b", "c")], ["a"], []),
    "visit": (VISIT_NODES, VISIT_EDGES, ["start"], VISIT_LAST),
    "every-way": (list("pqrstu"), _both_ways("pqrstu"), ["p"], []),
    # two endings with moves between them, both ways: the only moves along a row
    "endings": (["a", "b", "x", "y", "z"],
                [("a", "b"), ("a", "x"), ("b", "y"), ("b", "z"), ("x", "y"), ("y", "x"), ("x", "z"), ("y", "a")],
                ["a"], ["x", "y", "z"]),
}
SIZES = {"usual": {}, "small": {"w": 60, "h": 20, "gx": 12, "gy": 30, "pad": 4}}


def _size(sizes):
    return {"w": W, "h": H, "gx": GX, "gy": GY, "pad": PAD, **sizes}


def _lay(name, **sizes):
    nodes, edges, start, last = GRAPHS[name]
    return layout(list(nodes), list(edges), list(start), last=list(last), **sizes)


@pytest.fixture(params=[(g, s) for g in GRAPHS for s in SIZES], ids=lambda p: f"{p[0]}-{p[1]}")
def drawn(request):
    """(the graph's name, its layout, the sizes it was laid out with), for each graph at each size."""
    name, sizes = request.param
    return name, _lay(name, **SIZES[sizes]), _size(SIZES[sizes])


# ------------------------------------------------------------------------------------------------- geometry
def _box(out, n, size, shrink=0.0):
    x, y = out["pos"][n]
    return x + shrink, y + shrink, x + size["w"] - shrink, y + size["h"] - shrink


def _segments(points):
    return list(zip(points, points[1:]))


def _crosses(p, q, box) -> bool:
    """Does the segment from ``p`` to ``q`` touch the rectangle ``box`` (x0, y0, x1, y1)? Liang and Barsky's
    clipping: the part of the segment inside each pair of sides, narrowed until nothing is left or it is known."""
    t0, t1 = 0.0, 1.0
    for from_, by, lo, hi in ((p[0], q[0] - p[0], box[0], box[2]), (p[1], q[1] - p[1], box[1], box[3])):
        if by == 0:
            if from_ < lo or from_ > hi:
                return False
            continue
        a, b = sorted(((lo - from_) / by, (hi - from_) / by))
        t0, t1 = max(t0, a), min(t1, b)
        if t0 > t1:
            return False
    return True


def _share(p, q, r, s, eps=1e-6) -> bool:
    """Do the segments ``p``-``q`` and ``r``-``s`` lie on one line and share more than a point of it?"""
    dx, dy = q[0] - p[0], q[1] - p[1]
    length = math.hypot(dx, dy)
    if length < eps or math.hypot(s[0] - r[0], s[1] - r[1]) < eps:
        return False
    off = [abs(dx * (t[1] - p[1]) - dy * (t[0] - p[0])) / length for t in (r, s)]
    if max(off) > eps:
        return False
    a, b = sorted((dx * (t[0] - p[0]) + dy * (t[1] - p[1])) / length for t in (r, s))
    return min(length, b) - max(0.0, a) > eps


def _on_border(pt, box, eps=1e-6) -> str:
    """The side of ``box`` the point lies on ("top", "bottom", "left", "right"), or "" if it is on none."""
    x, y = pt
    x0, y0, x1, y1 = box
    if x0 - eps <= x <= x1 + eps and abs(y - y0) <= eps:
        return "top"
    if x0 - eps <= x <= x1 + eps and abs(y - y1) <= eps:
        return "bottom"
    if y0 - eps <= y <= y1 + eps and abs(x - x0) <= eps:
        return "left"
    if y0 - eps <= y <= y1 + eps and abs(x - x1) <= eps:
        return "right"
    return ""


def _from_line(pt, p, q) -> float:
    """How far the point is from the segment ``p``-``q``."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    span = dx * dx + dy * dy
    t = 0.0 if span == 0 else max(0.0, min(1.0, ((pt[0] - p[0]) * dx + (pt[1] - p[1]) * dy) / span))
    return math.hypot(pt[0] - p[0] - t * dx, pt[1] - p[1] - t * dy)


def test_the_checks_themselves_tell_a_hit_from_a_miss():
    box = (10, 10, 30, 20)
    assert _crosses((0, 15), (40, 15), box) and _crosses((20, 0), (20, 40), box)      # straight through
    assert _crosses((0, 0), (40, 30), box) and _crosses((15, 12), (25, 18), box)      # aslant; wholly inside
    assert _crosses((0, 10), (40, 10), box)                                           # along a side counts
    assert not _crosses((0, 9), (40, 9), box) and not _crosses((31, 0), (31, 40), box)
    assert not _crosses((0, 0), (9, 30), box) and not _crosses((0, 25), (12, 21), box)
    assert not _crosses((0, 15), (9, 15), box)                                        # stops short of it
    assert _share((0, 0), (10, 0), (5, 0), (20, 0)) and _share((0, 0), (10, 10), (8, 8), (2, 2))
    assert not _share((0, 0), (10, 0), (10, 0), (20, 0))                              # end to end: one point
    assert not _share((0, 0), (10, 0), (0, 1), (10, 1)) and not _share((0, 0), (10, 0), (5, -5), (5, 5))
    assert _on_border((15, 10), box) == "top" and _on_border((15, 20), box) == "bottom"
    assert _on_border((15, 15), box) == "" and _on_border((40, 10), box) == ""


# ------------------------------------------------------------------------------- what holds of every picture
def test_boxes_do_not_overlap_and_keep_their_distance_in_a_row(drawn):
    _, out, size = drawn
    names = list(out["pos"])
    assert set(names) == set(out["rank"])
    for i, a in enumerate(names):
        ax, ay = out["pos"][a]
        assert ay == size["pad"] + out["rank"][a] * (size["h"] + size["gy"])
        for b in names[i + 1:]:
            bx, by = out["pos"][b]
            apart_x = max(ax, bx) - min(ax, bx) - size["w"]
            apart_y = max(ay, by) - min(ay, by) - size["h"]
            assert apart_x >= 0 or apart_y >= 0, f"{a} and {b} overlap"
            if out["rank"][a] == out["rank"][b]:
                assert apart_x >= size["gx"] - 1e-9, f"{a} and {b} are {apart_x} apart"


def test_no_route_passes_through_a_box(drawn):
    _, out, size = drawn
    for (a, b), points in out["routes"].items():
        for n in out["pos"]:
            inside = _box(out, n, size, shrink=0.5)
            for p, q in _segments(points):
                assert not _crosses(p, q, inside), f"{a} to {b} runs through {n}: {p} to {q}"


def test_routes_run_from_border_to_border_in_the_moves_own_direction(drawn):
    _, out, size = drawn
    for (a, b), points in out["routes"].items():
        assert len(points) >= 2
        leaves = _on_border(points[0], _box(out, a, size))
        enters = _on_border(points[-1], _box(out, b, size))
        ra, rb = out["rank"][a], out["rank"][b]
        want = ("bottom", "top") if ra < rb else ("top", "bottom") if ra > rb else ("bottom", "bottom")
        assert (leaves, enters) == want, f"{a} to {b}: leaves by {leaves or 'no side'}, enters by {enters or 'no side'}"
        # and at least 10 px in from the corners (less only where the box is too narrow for that)
        keep = min(10, size["w"] / 4) - 1e-9
        for (x, _), n in ((points[0], a), (points[-1], b)):
            assert out["pos"][n][0] + keep <= x <= out["pos"][n][0] + size["w"] - keep


def test_arrows_on_one_side_of_a_box_do_not_stack(drawn):
    _, out, _ = drawn
    met = {}
    for (a, b), points in out["routes"].items():
        for n, pt in ((a, points[0]), (b, points[-1])):
            met.setdefault((n, pt[1]), []).append(pt[0])
    for (n, _), xs in met.items():
        xs.sort()
        assert all(later - sooner > 1 for sooner, later in zip(xs, xs[1:])), f"arrows stack on {n}: {xs}"


def test_everything_lies_inside_the_picture(drawn):
    _, out, size = drawn
    width, height = out["width"], out["height"]
    assert isinstance(width, float) and isinstance(height, float)
    spots = [pt for points in out["routes"].values() for pt in points] + list(out["labels"].values())
    for n in out["pos"]:
        x0, y0, x1, y1 = _box(out, n, size)
        spots += [(x0, y0), (x1, y1)]
    for x, y in spots:
        assert 0 <= x <= width and 0 <= y <= height, f"({x}, {y}) is outside {width} by {height}"


def test_the_same_graph_is_laid_out_the_same_every_time(drawn):
    name, out, size = drawn
    nodes, edges, start, last = GRAPHS[name]
    again = layout([str(n) for n in nodes], [(str(a), str(b)) for a, b in edges], [str(s) for s in start],
                   last=tuple(str(n) for n in last), **size)
    assert again == out and repr(again) == repr(out)   # the same numbers, in the same order
    assert list(out["routes"]) == list(out["labels"]) == list(dict.fromkeys(edges))


def test_no_two_routes_share_a_stretch_of_line(drawn):
    """The two routes of a two-way pair above all, but it holds of any two: no arrow lies on another."""
    name, out, _ = drawn
    routes = list(out["routes"].items())
    pairs = [(a, b) for (a, b), _ in routes if (b, a) in out["routes"]]
    assert pairs or name in ("chain", "diamond", "loop")
    for a, b in pairs:
        assert out["routes"][(a, b)] != out["routes"][(b, a)][::-1]
    for i, (one, points) in enumerate(routes):
        for other, others in routes[i + 1:]:
            for p, q in _segments(points):
                for r, s in _segments(others):
                    assert not _share(p, q, r, s), f"{one} and {other} share {p}-{q} / {r}-{s}"


def test_labels_sit_on_their_routes_in_the_gap_by_the_source(drawn):
    _, out, size = drawn
    assert set(out["labels"]) == set(out["routes"])
    for (a, b), spot in out["labels"].items():
        for n in out["pos"]:
            x0, y0, x1, y1 = _box(out, n, size)
            assert not (x0 <= spot[0] <= x1 and y0 <= spot[1] <= y1), f"the label of {a} to {b} is in {n}"
        assert min(_from_line(spot, p, q) for p, q in _segments(out["routes"][(a, b)])) < 1e-6
        # level with no row of boxes, and in a gap that touches the source's row
        row, rest = divmod(spot[1] - size["pad"], size["h"] + size["gy"])
        assert rest > size["h"], f"the label of {a} to {b} is level with row {row}"
        assert row in (out["rank"][a], out["rank"][a] - 1)


# ---------------------------------------------------------------------------------------------------- ranks
def test_ranks_of_the_small_graphs():
    assert _lay("chain")["rank"] == {"a": 0, "b": 1, "c": 2, "d": 3}
    assert _lay("diamond")["rank"] == {"a": 0, "b": 1, "c": 1, "d": 2}
    assert _lay("loop")["rank"] == {"a": 0, "b": 1, "c": 2, "d": 3}       # c back to a is turned, for ranking only
    assert _lay("two-way")["rank"] == {"a": 0, "b": 1, "c": 2}
    assert _lay("every-way")["rank"] == dict(zip("pqrstu", range(6)))
    assert _lay("endings")["rank"] == {"a": 0, "b": 1, "x": 2, "y": 2, "z": 2}


def test_ranks_of_the_visit():
    out = _lay("visit")
    assert out["rank"] == {
        "start": 0, "reading": 1, "callback": 1, "requested": 2, "not-reached": 2, "planned": 3, "arrived": 4,
        "triaged": 5, "in-progress": 6, "onleave": 7, "drafting": 7, "editing": 8, "signing": 9,
        "finished": 10, "undocumented": 10, "cancelled": 10, "noshow": 10, "entered-in-error": 10}
    assert list(out["rank"]) == VISIT_NODES
    assert out["height"] == PAD + 11 * H + 10 * GY + PAD
    assert out["width"] >= 5 * W + 4 * GX + 2 * PAD


def test_start_nodes_are_the_top_row_whatever_leads_to_them():
    out = layout(["a", "b", "c"], [("a", "b"), ("b", "c"), ("c", "b")], ["b"])
    assert out["rank"] == {"a": 0, "b": 0, "c": 1}
    out = layout(["a", "b", "c"], [("a", "b"), ("b", "c")], ["a", "b"])
    assert out["rank"] == {"a": 0, "b": 0, "c": 1}


def test_a_node_nothing_reaches_still_has_a_row_and_a_place():
    out = layout(["a", "b", "alone"], [("a", "b")], ["a"])
    assert out["rank"]["alone"] == 0 and "alone" in out["pos"]
    out = layout(["a", "b"], [], [])
    assert out["rank"] == {"a": 0, "b": 0} and out["routes"] == {} and out["labels"] == {}
    assert out["pos"]["b"][0] - out["pos"]["a"][0] == W + GX


def test_endings_go_below_everything_else():
    out = layout(["a", "end", "b", "c"], [("a", "end"), ("a", "b"), ("b", "c")], ["a"], last=["end", "nobody"])
    assert out["rank"] == {"a": 0, "end": 3, "b": 1, "c": 2}
    assert layout(["x", "y"], [("x", "y")], [], last=["x", "y"])["rank"] == {"x": 0, "y": 0}
    assert layout(["x", "y"], [("x", "y")], ["x"], last=["x", "y"])["rank"] == {"x": 0, "y": 1}  # a start is a start


def test_repeats_loops_on_one_node_and_unknown_names_are_left_out():
    plain = layout(["a", "b"], [("a", "b")], ["a"])
    noisy = layout(["a", "b", "a"], [("a", "b"), ("a", "a"), ("a", "b"), ("a", "ghost"), ("ghost", "b"), ["a", "b"]],
                   ["a", "ghost", "a"], last=["ghost"])
    assert noisy == plain and list(plain["routes"]) == [("a", "b")]


def test_nothing_to_draw():
    out = layout([], [("a", "b")], ["a"])
    assert out == {"width": 2.0 * PAD, "height": 2.0 * PAD, "rank": {}, "pos": {}, "routes": {}, "labels": {}}
    one = layout(["a"], [], ["a"])
    assert one["pos"] == {"a": (PAD, PAD)} and (one["width"], one["height"]) == (W + 2 * PAD, H + 2 * PAD)


# --------------------------------------------------------------------------------------------------- routes
def test_a_chain_is_a_straight_column():
    out = _lay("chain")
    assert len({x for x, _ in out["pos"].values()}) == 1
    middle = out["pos"]["a"][0] + W / 2
    for (a, b), points in out["routes"].items():
        assert points == [(middle, out["pos"][a][1] + H), (middle, out["pos"][b][1])]
    assert out["width"] == W + 2 * PAD and out["height"] == 4 * H + 3 * GY + 2 * PAD


def test_a_long_move_runs_down_a_gap_between_the_boxes_it_passes():
    out = _lay("diamond")
    route = out["routes"][("a", "d")]
    band = (out["pos"]["b"][1], out["pos"]["b"][1] + H)       # the row of b and c, which a to d passes
    upright = [(p, q) for p, q in _segments(route) if p[1] <= band[0] and q[1] >= band[1]]
    assert len(upright) == 1 and upright[0][0][0] == upright[0][1][0]
    x = upright[0][0][0]
    for n in ("b", "c"):
        assert x <= out["pos"][n][0] - GX + 1e-9 or x >= out["pos"][n][0] + W + GX - 1e-9, f"too near {n}"


def test_a_way_back_leaves_by_the_top_and_arrives_from_below():
    out = _lay("loop")
    route = out["routes"][("c", "a")]
    assert route[0][1] == out["pos"]["c"][1] and route[-1][1] == out["pos"]["a"][1] + H
    assert all(q[1] <= p[1] for p, q in _segments(route))     # it never turns back down


def test_a_two_way_pair_is_two_arrows_side_by_side():
    out = _lay("two-way")
    down, up = out["routes"][("a", "b")], out["routes"][("b", "a")]
    assert down[0][1] == up[-1][1] == out["pos"]["a"][1] + H and down[-1][1] == up[0][1] == out["pos"]["b"][1]
    assert abs(down[0][0] - up[-1][0]) >= 10 and abs(down[-1][0] - up[0][0]) >= 10
    assert out["labels"][("a", "b")][1] < out["labels"][("b", "a")][1]   # each label by its own source


def test_a_move_along_a_row_dips_into_the_gap_below_it():
    out = _lay("endings")
    foot = out["pos"]["x"][1] + H
    depths = []
    for move in (("x", "y"), ("y", "x"), ("x", "z")):
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = out["routes"][move]
        assert y0 == y3 == foot and x0 == x1 and x2 == x3 and y1 == y2 and foot < y1 < foot + GY
        assert out["labels"][move][1] == y1
        depths.append(y1)
    assert len(set(depths)) == 3                              # each at a depth of its own
    (out_x, _), (_, deep), _, (back_x, _) = out["routes"][("y", "x")]
    (in_x, _), (_, shallow), _, (to_x, _) = out["routes"][("x", "y")]
    assert min(out_x, back_x) < min(in_x, to_x) < max(in_x, to_x) < max(out_x, back_x) and shallow < deep  # nested
    assert out["height"] >= max(depths) + PAD                 # and the picture is tall enough to hold them


def test_the_layout_does_not_depend_on_how_this_process_hashes_names():
    """A set walked anywhere in the layout would show here: each process below hashes strings its own way."""
    nodes, edges, start, last = GRAPHS["visit"]
    code = ("import json, sys; from practicehub.web.flowmap import layout; g = json.load(sys.stdin); "
            "print(repr(layout(g['nodes'], [tuple(e) for e in g['edges']], g['start'], last=g['last'])))")
    root = Path(__file__).resolve().parent.parent
    for seed in ("1", "2"):
        ran = subprocess.run([sys.executable, "-c", code], cwd=root, env={**os.environ, "PYTHONHASHSEED": seed},
                             input=json.dumps({"nodes": nodes, "edges": edges, "start": start, "last": last}),
                             capture_output=True, text=True, timeout=60)
        assert ran.returncode == 0, ran.stderr
        assert ran.stdout.strip() == repr(_lay("visit"))


# --------------------------------------------------------------------------------------------------- path_d
def test_path_d_of_two_points_is_one_line():
    assert path_d([(0, 0), (10, 20)]) == "M0,0 L10,20"
    assert path_d([(1.25, 2.0), (3.04, 4.96)]) == "M1.2,2 L3,5"   # at most one decimal, none where it is nought
    assert path_d([(5, 5)]) == "M5,5" and path_d([]) == ""


def test_path_d_rounds_a_corner():
    d = path_d([(0, 0), (0, 40), (40, 40)], r=8)
    assert d == "M0,0 L0,32 Q0,40 8,40 L40,40"
    assert path_d([(0, 0), (0, 10), (40, 10)], r=8) == "M0,0 L0,5 Q0,10 5,10 L40,10"   # half the shorter line
    assert path_d([(0, 0), (0, 40), (40, 40)], r=0) == "M0,0 L0,40 L40,40"


def test_path_d_passes_straight_through_points_in_one_line():
    d = path_d([(0, 0), (0, 10), (0, 30), (20, 30), (40, 30)])
    assert d.startswith("M") and d.count("Q") == 1 and "A" not in d and "nan" not in d.lower()
    assert path_d([(0, 0), (5, 5), (10, 10)]) == "M0,0 L5,5 L10,10"


def test_path_d_takes_a_point_twice_without_falling_over():
    for points in ([(0, 0), (0, 0)], [(0, 0), (0, 0), (10, 0)], [(0, 0), (10, 0), (10, 0), (10, 10)],
                   [(3, 3), (3, 3), (3, 3)]):
        d = path_d(points)
        assert d.startswith("M") and "nan" not in d.lower() and "inf" not in d.lower()


def test_path_d_draws_every_route(drawn):
    _, out, _ = drawn
    for points in out["routes"].values():
        d = path_d(points)
        assert d.startswith("M") and "nan" not in d.lower() and "inf" not in d.lower()
        first, final = d.split(" ")[0], d.split(" ")[-1]
        assert first == "M" + ",".join(f"{round(v, 1):g}" for v in points[0])
        assert final.startswith("L") and abs(float(final[1:].split(",")[1]) - points[-1][1]) < 0.06
