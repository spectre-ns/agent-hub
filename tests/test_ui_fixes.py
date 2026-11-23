"""The record's lifecycle on the page: the diagram lights only the moves from where it is, and the side panel makes
only the forward move loud."""

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


def test_the_diagram_lights_only_the_moves_that_leave_the_current_state(hub):
    app = App(hub, dev=False)
    rt = hub.record_type("Encounter")
    W, H = 136, 34
    for st in ("planned", "arrived", "triaged"):
        svg = app.lifecycle_svg(rt, st)
        node = re.search(r'<g class="lc-node [^"]*current[^"]*"><title>[^<]*</title><rect x="([\d.]+)" y="([\d.]+)"', svg)
        fx, fy = float(node.group(1)), float(node.group(2))
        mine = [t for t in rt.lifecycle["transition"] if t["by"] != "workflow" and t["to"] != st
                and (st in t["from"] or "*" in t["from"])]
        paths = _here_paths(svg)
        assert len(paths) == len(mine), st  # one lit arrow per move a person may make from here, no more
        for d in paths:
            x1, y1 = (float(v) for v in re.match(r"M([\d.]+),([\d.]+)", d).groups())
            tab = re.match(r"M[\d.]+,[\d.]+ L[\d.]+,[\d.]+$", d)  # the "any state" tab's short arrow
            assert tab or (fx <= x1 <= fx + W and y1 in (fy + H, fy + H / 2)), (st, d)
        assert ' auto here"' not in svg and "lc-edge auto here" not in svg  # a workflow's move is never "from here"


def test_only_the_forward_move_is_primary(hub):
    app = App(hub, dev=False)
    lc = hub.record_type("Encounter").lifecycle

    def ahead(st):
        moves = [t for t in lc["transition"] if (st in t["from"] or "*" in t["from"]) and t["to"] != st
                 and t["by"] != "workflow"]
        return app.forward_move(lc, st, moves)["to"]

    assert ahead("planned") == "arrived"  # not the no-show, not cancelled, not entered in error
    assert ahead("arrived") == "triaged"
    # finishing a visit in progress is the note workflow's move, never a button; the pause is all a person may do
    assert ahead("in-progress") == "onleave"


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
    side = page.split('class="side-actions"', 1)[1].split("</div>", 1)[0]
    assert re.search(r'<button class="primary"[^>]*>Check in</button>', side)
    assert re.search(r'<button class="secondary"[^>]*>Mark no-show…</button>', side)
    assert len(re.findall(r'class="(?:btn )?primary"', side)) == 1


def test_a_reply_that_stays_reloads_however_the_dialog_closes():
    listener = JS.split('dlg.addEventListener("close"', 1)[1].split("});", 1)[0]
    assert "if (stayed) { stayed = false; location.reload(); }" in listener
    stay = JS.split('indexOf("data-modal-stay")', 1)[1].split("return;", 1)[0]
    assert "advice.tabIndex = -1; advice.focus();" in stay
