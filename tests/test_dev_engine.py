"""The dev engine (``scripts/dev-engine.py``): a real engine for the hub's webhook path, run by a local model. Here it
runs for real, over HTTP, with the hub's own webhook backend calling it; only the model behind it is a stand-in (a
few lines that answer as Ollama does), so the suite needs no model."""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from practicehub import backends

_spec = importlib.util.spec_from_file_location("dev_engine",
                                               Path(__file__).resolve().parent.parent / "scripts" / "dev-engine.py")
dev_engine = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dev_engine)


@pytest.fixture(autouse=True)
def _database():
    """In place of conftest's: these tests are given no database, and ask for none."""
    yield


def _serve(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_port}"


@pytest.fixture
def model():
    """What stands in for Ollama: it keeps what it was asked and answers ``answer`` as the model's words."""
    seen = {"asked": [], "answer": {"erp_ref": "ACC-SINV-0042", "aside": "not asked for"}}

    class Ollama(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["asked"].append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            words = seen["answer"] if isinstance(seen["answer"], str) else json.dumps(seen["answer"])
            data = json.dumps({"message": {"role": "assistant", "content": words}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    httpd, url = _serve(Ollama)
    yield SimpleNamespace(url=url, **{"seen": seen})
    httpd.shutdown()


@pytest.fixture
def engine(model):
    lines = []
    made = []

    def start(token="", flows=None):
        handler = dev_engine.make_handler(flows or dev_engine.load_flows(None), "tiny:1b", model.url, token,
                                          log=lines.append)
        httpd, url = _serve(handler)
        made.append(httpd)
        return url + "/hook"
    yield SimpleNamespace(start=start, lines=lines)
    for httpd in made:
        httpd.shutdown()


BLOCK = SimpleNamespace(id="post-claim-to-erp", target="erpnext/claim")
META = {"run": "run-1", "step": "post", "version": 1}


def test_the_hub_s_webhook_engine_gets_a_block_s_outputs_from_the_model_through_the_dev_engine(model, engine):
    url = engine.start()
    out = backends.Webhook(url=url, token="").run(BLOCK, {"claim": "Claim/123", "amount": 80}, META)
    assert out["output"] == {"erp_ref": "ACC-SINV-0042"}  # what the flow's shape names, and nothing the model added
    assert out["ref"] == "dev-1" and "tiny:1b answered in" in out["log"]
    (path, asked), = model.seen["asked"]
    assert path == "/api/chat" and asked["model"] == "tiny:1b" and asked["stream"] is False
    assert asked["format"] == dev_engine.FLOWS["erpnext/claim"]["output"]  # the model is held to the flow's shape
    said = asked["messages"][0]["content"]
    assert '"claim": "Claim/123"' in said and '"amount": 80' in said  # the block's inputs, and only those
    assert "run-1" not in said and "post-claim-to-erp" not in said
    assert len(engine.lines) == 1 and "post-claim-to-erp (erpnext/claim) dev-1: 200" in engine.lines[0]


def test_a_target_with_no_flow_and_an_answer_that_is_not_json_fail_the_call_in_words(model, engine):
    url = engine.start()
    hook = backends.Webhook(url=url, token="")
    with pytest.raises(backends.EngineError, match="no flow for the target 'erp/unknown'"):
        hook.run(SimpleNamespace(id="other", target="erp/unknown"), {}, META)
    assert model.seen["asked"] == []  # nothing was sent to the model
    model.seen["answer"] = "I would rather chat"
    with pytest.raises(backends.EngineError, match="answered 502.*could not be read"):
        hook.run(BLOCK, {"claim": "Claim/1", "amount": 1}, META)


def test_the_dev_engine_wants_the_hub_s_token_when_one_is_set(model, engine):
    url = engine.start(token="engine-token")
    with pytest.raises(backends.EngineError, match="answered 401"):
        backends.Webhook(url=url, token="").run(BLOCK, {"claim": "Claim/1", "amount": 1}, META)
    out = backends.Webhook(url=url, token="engine-token").run(BLOCK, {"claim": "Claim/1", "amount": 1}, META)
    assert out["output"]["erp_ref"] == "ACC-SINV-0042"


def test_more_flows_come_from_a_file_and_a_port_kept_for_something_else_is_refused(tmp_path, model, engine):
    extra = tmp_path / "flows.json"
    extra.write_text(json.dumps({"lab/second-opinion": {
        "ask": "Say whether this value is inside its range.",
        "output": {"type": "object", "properties": {"inside": {"type": "boolean"}}, "required": ["inside"]}}}))
    flows = dev_engine.load_flows(str(extra))
    assert set(flows) == {"erpnext/claim", "lab/second-opinion"}
    model.seen["answer"] = {"inside": True}
    out = backends.Webhook(url=engine.start(flows=flows), token="").run(
        SimpleNamespace(id="second-opinion", target="lab/second-opinion"), {"value": 139, "range": "135-145"}, META)
    assert out["output"] == {"inside": True}
    extra.write_text(json.dumps({"x/y": {"ask": 3}}))
    with pytest.raises(SystemExit, match="needs \"ask\""):
        dev_engine.load_flows(str(extra))
    for port in (8080, 9980, 11434):
        with pytest.raises(SystemExit, match="kept for something else"):
            dev_engine.main(["--port", str(port)])
