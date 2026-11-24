"""The record's lifecycle on the page: the diagram lights only the moves from where it is, and the side panel makes
only the forward move loud."""

import html
import re
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from conftest import M
from practicehub.web.assets import JS
from practicehub.web.server import App, make_handler


def _here_paths(svg):
    return re.findall(r'<path class="lc-edge[^"]*\bhere\b[^"]*" d="([^"]+)"', svg)


def _boxes(svg):
    """Each state's box on the map: ``{title: (x, y, w, h)}``."""
    out = {}
    for title, x, y, w, h in re.findall(r'<g class="lc-node [^"]*"><title>([^<]*)</title><rect x="([\d.]+)" '
                                        r'y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg):
        out[html.unescape(title).split(";")[0].replace(" (where it is now)", "")] = tuple(map(float, (x, y, w, h)))
    return out


def test_the_map_lights_only_the_moves_that_leave_the_current_state(hub):
    app = App(hub, dev=False)
    rt = hub.record_type("Encounter")
    g = app.lifecycle_graph(rt)
    for st in ("planned", "arrived", "triaged"):
        svg = app.lifecycle_svg(rt, st)
        node = re.search(r'<g class="lc-node [^"]*current"><title>[^<]*</title><rect x="([\d.]+)" y="([\d.]+)" '
                         r'width="([\d.]+)" height="([\d.]+)"', svg)
        fx, fy, w, h = (float(v) for v in node.groups())
        # one lit arrow per state a person can move it to from here, among the moves the map draws as arrows
        mine = {b for (a, b), moves in g["edges"].items() if a == st and any(t["by"] != "workflow" for t in moves)}
        paths = _here_paths(svg)
        assert paths and len(paths) <= len(mine), st
        for d in paths:  # each leaves the current state's own box, by its top or its bottom
            x1, y1 = (float(v) for v in re.match(r"M([\d.]+),([\d.]+)", d).groups())
            assert fx <= x1 <= fx + w and y1 in (fy, fy + h), (st, d)
        assert "lc-edge auto here" not in svg  # a move only its workflow makes is never "from here"


def test_the_map_has_one_start_and_no_arrow_from_every_state_into_where_a_workflow_starts(hub):
    """A workflow may start a visit in several states. That is one Start with one arrow, not an arrow from every
    state into each of them (which is how the type's moves say it, and what made the old picture a thicket)."""
    app = App(hub, dev=False)
    rt = hub.record_type("Encounter")
    entries = [t for t in rt.lifecycle["transition"] if t.get("entry")]
    assert sum(len(t["from"]) for t in entries) > 20  # the thicket: what the type's moves hold
    svg = app.lifecycle_svg(rt, "planned")
    assert svg.count('class="lc-edge begin') == 1 and svg.count(">Start</text>") == 1
    assert len(re.findall(r'<path class="lc-edge', svg)) < 30
    assert "from any state" in svg  # a move from anywhere is said on its target, not drawn from everywhere
    assert "or: Cancelled" in svg  # an ending many states share is said on each, not an arrow from each
    table = app._lifecycle_table(rt, "planned")
    assert table.count("<tr") < 45  # and the table of moves leaves the entry out too


def test_every_installed_type_s_map_draws_with_no_box_on_another(hub):
    import xml.etree.ElementTree as ET
    app = App(hub, dev=False)
    drawn = 0
    for rt in hub.record_types().values():
        lc = rt.lifecycle
        if not lc or not lc.get("states"):
            continue
        for st in lc["states"]:
            svg = app.lifecycle_svg(rt, st)
            picture = svg.split("</svg>", 1)[0].split("<svg", 1)[1]
            ET.fromstring("<svg" + picture + "</svg>")  # well formed
            assert len(re.findall(r'<g class="lc-node [^"]*current"', svg)) == 1, (rt.id, st)
            boxes = list(_boxes(svg).values())
            for n, (x, y, w, h) in enumerate(boxes):
                for (x2, y2, w2, h2) in boxes[n + 1:]:
                    assert x + w <= x2 or x2 + w2 <= x or y + h <= y2 or y2 + h2 <= y, (rt.id, st)
            drawn += 1
    assert drawn > 20


def test_the_map_draws_the_way_a_record_came_over_its_workflow(hub):
    """The record's own journey on the map: its moves numbered in order and named, where it began, the states it has
    not been in faint, where it is now ringed."""
    from test_journey import _visit
    run, ref = _visit(hub)
    app = App(hub, dev=False)
    rt = hub.record_type("Encounter")
    amara = M(hub, "dr-amara")
    journey = hub.record_journey(amara, ref)
    svg = app.lifecycle_svg(rt, "onleave", journey)
    assert re.findall(r'<text class="lc-num"[^>]*>([^<]+)</text>', svg) == ["1", "2", "3", "4", "5", "6"]
    names = [html.unescape(n) for n in re.findall(r'<g class="lc-tag route">.*?<text class="lc-name"[^>]*>([^<]+)<',
                                                    svg)]
    assert names[0] == "Asked for"  # where it began: its workflow's entry, by its own name
    assert names[1].startswith("An ordinary slot is fine") and names[2].startswith("Booked")
    assert names[3:] == ["Check in", "Mark triaged", "Start the visit", "Patient stepped out"]
    assert "⚙" in names[2]  # a move that ran automations says how many
    assert svg.count('class="lc-edge route') + svg.count(' route"') >= 6 and "lc-edge begin route" in svg
    faint = re.findall(r'<g class="lc-node [^"]* dim"><title>([^<;]+)', svg)
    assert "Note being drafted" in faint and "No-show" in faint and "Booked" not in faint
    assert re.search(r'<g class="lc-node [a-z]+ current"><title>Patient stepped out \(where it is now\)', svg)
    assert 'aria-label="How visit records move:' in svg and "it went Being read, then Requested" in svg
    # the dialog: the map, the moves in words, and its workflow stage by stage with the way it came marked
    page = app.page_record_workflow({"me": amara, "query": {}, "path": f"/records/{ref}/workflow", "headers": {}},
                                    *ref.split("/", 1))
    page = page if isinstance(page, str) else str(page)
    assert "the way it came is numbered" in page and "Its workflow, stage by stage" in page
    assert '<span class="wf-mark now">it is here</span>' not in page  # stepped out is the type's state, not a stage
    assert page.count('<span class=wf-mark>taken</span>') >= 2 and '<span class="wf-mark">been here</span>' in page
    jo = hub.record_journey(M(hub, "jo"), ref)  # who may not see the workflow's steps still sees where it went
    assert re.findall(r'<text class="lc-num"[^>]*>([^<]+)</text>', app.lifecycle_svg(rt, "onleave", jo))[-1] == "6"


def test_only_the_forward_move_is_primary(hub):
    app = App(hub, dev=False)
    lc = hub.record_type("Encounter").lifecycle

    def ahead(st):
        moves = [t for t in lc["transition"] if (st in t["from"] or "*" in t["from"]) and t["to"] != st
                 and t["by"] != "workflow"]
        t = app.forward_move(lc, st, moves)
        return t and t["to"]

    assert ahead("planned") == "arrived"  # not the no-show, not cancelled, not entered in error
    assert ahead("arrived") == "triaged"
    # finishing a visit in progress is the note workflow's move, never a button; the patient stepping out (and back)
    # is a side move, never the main button: nothing is primary then
    assert ahead("in-progress") is None and ahead("onleave") is None
    labels = {(t["from"][0], t["to"]): t["label"] for t in lc["transition"]}
    assert labels[("in-progress", "onleave")] == "Patient stepped out"
    assert labels[("planned", "cancelled")] == "Cancel the visit" and labels[("planned", "noshow")] == "Mark no-show"
    assert hub.record_type("Encounter").state_label("onleave") == "Patient stepped out"


@pytest.fixture
def server(hub):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(hub, dev=False)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_the_side_panel_shows_one_primary_move(hub, server):
    from datetime import datetime, timedelta
    v = next(v for v in hub.records.search("Encounter", limit=None) if v.get("status") == "planned")
    # at the visit's time: checking in and a no-show are both open then (neither before)
    hub.engine._now = datetime.fromisoformat(v["plannedStartDate"][:16]) + timedelta(minutes=5)
    tok = hub.issue_token(M(hub, "jo"), "jo", "test")
    req = urllib.request.Request(f"{server}/records/Encounter/{v['id']}", headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req) as r:
        page = r.read().decode()
    side = page.split('class="rw-moves"', 1)[1].split("</div>", 1)[0]  # the moves, under the record's title
    assert re.search(r'<button class="primary"[^>]*>Check in</button>', side)
    assert re.search(r'(<button class="secondary"[^>]*>|<a class="btn secondary"[^>]*>)Mark no-show…</(button|a)>', side)
    assert len(re.findall(r'class="(?:btn )?primary"', side)) == 1


def test_a_reply_that_stays_reloads_however_the_dialog_closes():
    listener = JS.split('dlg.addEventListener("close"', 1)[1].split("});", 1)[0]
    assert "if (stayed) { stayed = false; location.reload(); }" in listener
    stay = JS.split('indexOf("data-modal-stay")', 1)[1].split("return;", 1)[0]
    assert "advice.tabIndex = -1; advice.focus();" in stay
