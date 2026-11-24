#!/usr/bin/env python3
"""A real engine under the hub, for trying engine blocks on one machine: a small HTTP service that takes the hub's
webhook call and does the block's work with a local model through Ollama.

    scripts/dev-engine.py                                  # on http://127.0.0.1:8215/, model granite4.2:3b
    PRACTICEHUB_ENGINE_WEBHOOK_URL=http://127.0.0.1:8215/hook \\
      python3 -m practicehub.cli serve --dev --agents      # the hub posts its engine blocks here

The hub's webhook engine (practicehub/backends.py) posts ``{block, version, target, input, run, step}`` and checks
the answer against the block's outputs, like any answer. Here a block's ``target`` names a **flow**: what the model is
asked to do with the inputs, and the shape its answer must have. One flow is built in, for the block the
general-practice pack ships (``post-claim-to-erp``, target ``erpnext/claim``); ``--flows flows.json`` adds yours::

    {"lab/second-opinion": {"ask": "Say whether this value is inside its range.",
                            "output": {"type": "object", "properties": {"inside": {"type": "boolean"}},
                                       "required": ["inside"]}}}

What it is for: seeing an engine block queued, under way and done on a record's page with a call that really takes
time, and running the hub's engine path against something that is not a stub. What it is not: a back office. It
posts nothing anywhere; the model makes its answer up. The model's answer is data: it is read as JSON, cut down to
what the flow's shape names, and handed back for the hub to check. Nothing it says is run.

It listens on this machine only unless told otherwise, and it talks only to Ollama (``OLLAMA_URL``, default
http://localhost:11434): what a block is sent stays on the machine. If ``PRACTICEHUB_ENGINE_WEBHOOK_TOKEN`` is set,
a call must carry it (``Authorization: Bearer ...``), as the hub's does. The runtime is the standard library.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FLOWS = {
    "erpnext/claim": {
        "ask": "You are standing in for a back office. Post this claim and answer with the reference of the sales "
               "invoice you made for it, of the form ACC-SINV-<four digits>.",
        "output": {"type": "object", "properties": {"erp_ref": {"type": "string"}}, "required": ["erp_ref"]},
    },
}
KEPT_FOR_OTHERS = (8080, 9980, 11434)  # ports this project never binds (AGENTS.md)


class NoFlow(Exception):
    pass


def ask_model(url: str, model: str, flow: dict, inputs: dict, timeout: float = 55.0) -> dict:
    """What the model answers for one call of a flow: an object with the keys the flow's shape names, no others."""
    body = {"model": model, "stream": False, "format": flow["output"], "options": {"temperature": 0},
            "messages": [{"role": "user", "content": f"{flow['ask']}\nWhat you are given, as JSON:\n"
                                                      f"{json.dumps(inputs, default=str)}"}]}
    req = urllib.request.Request(f"{url.rstrip('/')}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        said = json.loads(resp.read())
    answer = json.loads((said.get("message") or {}).get("content") or "{}")
    if not isinstance(answer, dict):
        raise ValueError("the model did not answer with an object")
    names = (flow["output"].get("properties") or {})
    return {k: v for k, v in answer.items() if k in names}


def say(line: str) -> None:
    print(line, flush=True)  # each call is said as it ends, also into a file


def make_handler(flows: dict, model: str, ollama: str, token: str = "", log=say):
    count = [0]

    class Engine(BaseHTTPRequestHandler):
        def _answer(self, code: int, obj: dict) -> None:
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # what is here, for a person who opens it
            self._answer(200, {"engine": "practicehub dev engine", "model": model, "flows": sorted(flows)})

        def do_POST(self):
            if token and self.headers.get("Authorization") != f"Bearer {token}":
                return self._answer(401, {"error": "this engine wants the hub's token"})
            try:
                call = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if not isinstance(call, dict):
                    raise ValueError
            except ValueError:
                return self._answer(400, {"error": "the call is not a JSON object"})
            began = time.monotonic()
            count[0] += 1
            ref = f"dev-{count[0]}"
            try:
                flow = flows.get(str(call.get("target") or ""))
                if flow is None:
                    raise NoFlow(f"this engine has no flow for the target {call.get('target')!r} "
                                 f"(it has: {', '.join(sorted(flows)) or 'none'})")
                out = ask_model(ollama, model, flow, call.get("input") or {})
            except NoFlow as exc:
                code, body = 404, {"error": str(exc)}
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                code, body = 502, {"error": f"the model could not be reached at {ollama}: {exc}"}
            except ValueError as exc:
                code, body = 502, {"error": f"the model's answer could not be read: {exc}"}
            else:
                took = time.monotonic() - began
                code, body = 200, {"output": out, "ref": ref, "log": f"{model} answered in {took:.1f} s"}
            log(f"{time.strftime('%H:%M:%S')} {call.get('block')} ({call.get('target')}) {ref}: {code} "
                f"in {time.monotonic() - began:.1f} s")
            self._answer(code, body)

        def log_message(self, *args):  # one line per call, above, is enough
            pass

    return Engine


def load_flows(path: str | None) -> dict:
    flows = dict(FLOWS)
    if path:
        with open(path, encoding="utf-8") as fh:
            more = json.load(fh)
        for target, flow in more.items():
            if not isinstance(flow, dict) or not isinstance(flow.get("ask"), str) \
                    or not isinstance(flow.get("output"), dict):
                raise SystemExit(f"{path}: the flow for {target!r} needs \"ask\" (words) and \"output\" (a JSON schema)")
            flows[target] = flow
    return flows


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="A dev engine for the hub's engine blocks, run by a local model.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8215)
    ap.add_argument("--model", default=os.environ.get("PRACTICEHUB_DEV_ENGINE_MODEL", "granite4.2:3b"),
                    help="the Ollama model that answers (default granite4.2:3b)")
    ap.add_argument("--flows", help="a JSON file of more flows: {target: {\"ask\": ..., \"output\": {schema}}}")
    args = ap.parse_args(argv)
    if args.port in KEPT_FOR_OTHERS:
        raise SystemExit(f"port {args.port} is kept for something else on this machine; choose another")
    ollama = os.environ.get("OLLAMA_URL") or "http://localhost:11434"
    flows = load_flows(args.flows)
    handler = make_handler(flows, args.model, ollama, os.environ.get("PRACTICEHUB_ENGINE_WEBHOOK_TOKEN", ""))
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"dev engine on http://{args.host}:{args.port}/ ; {args.model} through {ollama}; "
          f"flows: {', '.join(sorted(flows))}\n"
          f"point the hub at it: PRACTICEHUB_ENGINE_WEBHOOK_URL=http://{args.host}:{args.port}/hook", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
