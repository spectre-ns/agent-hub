"""The page editor: its title box and Publish button, and the markdown that goes through the visual view.

The visual view turns the page's HTML back into markdown in the browser (``MD.toMd`` in assets.JS). The round trip
is checked here with the server's own renderer on both ends: markdown -> server HTML -> the editor's markdown ->
server HTML again must read the same. That part runs the script in a headless Chrome and is skipped without one."""

import glob
import json
import os
import shutil
import subprocess
import tempfile

import pytest

from practicehub import markup
from practicehub.web import assets
from test_live_web import _live_cfg, _page, wiki  # noqa: F401 - the fixture
from test_web import call, server, token  # noqa: F401 - the server fixture


def _draft(hub, title="A new page", body="Words."):
    home = _page(hub, "Clinic handbook")
    return hub.create_record(hub.member("rana"), "WikiPage",
                             {"title": title, "parent": {"reference": f"WikiPage/{home['id']}"}, "body": body})


def test_the_editor_is_given_the_title_and_for_a_draft_the_publish_move(wiki):  # noqa: F811
    hub, srv = wiki
    d = _draft(hub)
    assert d["status"] == "draft"
    cfg = _live_cfg(call(srv, "GET", f"/records/WikiPage/{d['id']}", token(hub, "jo"))[1])
    assert cfg["title"] == "A new page" and cfg["publish"] == {"to": "published", "label": "Publish"}
    p = _page(hub, "On-call guide")  # published already: its button is Update
    cfg = _live_cfg(call(srv, "GET", f"/records/WikiPage/{p['id']}", token(hub, "jo"))[1])
    assert cfg["title"] == "On-call guide" and "publish" not in cfg


def test_the_title_is_saved_from_the_editor_and_kept_with_the_text_written_meanwhile(wiki):  # noqa: F811
    hub, srv = wiki
    d = _draft(hub, body="One.")
    jo = token(hub, "jo")
    base = f"/api/live/WikiPage/{d['id']}/body"
    assert json.loads(call(srv, "POST", base + "/join", jo, body={})[1])["mode"] == "edit"
    assert call(srv, "POST", base + "/ops", jo, body={"cid": "jo-t-000001", "base": 0, "op": [4, " Two."]})[0] == 200
    code, out = call(srv, "POST", f"/api/records/WikiPage/{d['id']}/title", jo, body={"title": "  Front   desk  "})
    assert code == 200 and json.loads(out)["title"] == "Front desk"
    call(srv, "POST", base + "/done", jo, body={})
    rec = hub.records.read("WikiPage", d["id"])
    assert rec["title"] == "Front desk" and rec["body"] == "One. Two."


def test_a_title_is_refused_when_empty_too_long_or_from_a_reader(wiki):  # noqa: F811
    hub, srv = wiki
    d = _draft(hub)
    url = f"/api/records/WikiPage/{d['id']}/title"
    assert call(srv, "POST", url, token(hub, "jo"), body={"title": "  "})[0] == 422
    assert call(srv, "POST", url, token(hub, "jo"), body={"title": "x" * 201})[0] == 422
    from practicehub.hub import member_ref
    hub.authz.delete([("role:wiki-editor", "assignee", member_ref("sam"))])
    assert call(srv, "POST", url, token(hub, "sam"), body={"title": "Mine"})[0] in (403, 422)
    assert hub.records.read("WikiPage", d["id"])["title"] == "A new page"


# ---------------------------------------------------------------- markdown through the visual view, in a browser

CORPUS = [
    "Plain words, **bold**, *italic*, ~~gone~~ and `code`.",
    "# Top\n\n## Second\n\n### Third\n\n#### Fourth\n\nText.",
    "- one\n- two\n  - nested\n- three\n\n1. first\n2. second",
    "- [ ] to do\n- [x] done\n- plain",
    "> [!note]\n> A note with **weight**.\n\n> [!warning]\n> Careful.\n\n> [!tip]\n> A tip.\n\n> A plain quote.",
    "| Task | Who |\n| --- | --- |\n| Backups | jo |\n| Fridge | noor |",
    "```\nssh nas\n  ls -la\n\nlast line\n```",
    "See [[Onboarding]] and [[WikiPage/abc|the page]] and [a link](https://example.org/x) and @dr-amara.",
    "Line one\nline two of the same paragraph.\n\n---\n\nAfter the rule.",
    "Intro.\n\n## Steps\n\n1. Open **the** door\n2. Check `fridge`\n\n- [ ] Log it\n\n> [!important]\n> Call @noor.\n\n| A | B |\n| --- | --- |\n| *x* | [y](https://e.org) |\n",
]


def _chrome():
    for c in [os.environ.get("PRACTICEHUB_TEST_CHROME"), shutil.which("chromium"), shutil.which("google-chrome"),
              shutil.which("chrome-headless-shell"),
              *sorted(glob.glob(os.path.expanduser(
                  "~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell")))]:
        if c and os.path.exists(c):
            return c
    return None


def _run_in_chrome(script: str) -> str:
    exe = _chrome()
    if not exe:
        pytest.skip("no headless Chrome here")
    page = ("<!doctype html><meta charset=utf-8><body><pre id=out></pre><script>" + assets.JS.replace("</script", "<\\/script")
            + "</script><script>" + script + "</script></body>")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "t.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(page)
        env = dict(os.environ)
        if os.environ.get("PRACTICEHUB_TEST_CHROME_LIBS"):
            env["LD_LIBRARY_PATH"] = os.environ["PRACTICEHUB_TEST_CHROME_LIBS"]
        try:
            out = subprocess.run([exe, "--headless", "--disable-gpu", "--no-sandbox", f"--user-data-dir={tmp}/u",
                                  "--dump-dom", "file://" + path], capture_output=True, text=True, timeout=60, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            pytest.skip(f"Chrome did not run: {exc}")
    if out.returncode != 0 or '<pre id="out">' not in out.stdout:
        pytest.skip("Chrome did not run: " + out.stderr[-300:])
    import html
    return html.unescape(out.stdout.split('<pre id="out">', 1)[1].split("</pre>", 1)[0])


def test_markdown_round_trips_through_the_visual_view():
    docs = [d for d in CORPUS if d]
    served = [markup.render(d) for d in docs]
    script = """
    const docs = %s, served = %s, out = [];
    docs.forEach((d, i) => {
      const box = document.createElement("div"); box.innerHTML = served[i];
      const blocks = PH.MD.blocks(d, {});
      out.push({
        md: PH.MD.toMd(box),                                        // what the editor writes from the server's HTML
        stable: PH.MD.toHtml(PH.MD.toMd((() => { const b = document.createElement("div");
          b.innerHTML = PH.MD.toHtml(d, {}); return b; })()), {}) === PH.MD.toHtml(d, {}),
        blocks: blocks.map((b) => b.html).join("") === PH.MD.toHtml(d, {}),
        spans: blocks.every((b, k) => b.from <= b.to && (k === 0 || blocks[k - 1].to <= b.from)),
      });
    });
    document.getElementById("out").textContent = JSON.stringify(out);
    """ % (json.dumps(docs), json.dumps(served))
    got = json.loads(_run_in_chrome(script))
    assert len(got) == len(docs)
    for d, html_, g in zip(docs, served, got):
        assert markup.render(g["md"]) == html_, (d, g["md"])  # server HTML -> markdown -> the same server HTML
        assert g["stable"] and g["blocks"] and g["spans"], d
