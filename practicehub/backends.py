"""The automation engines under the hub.

The hub is the part people and agents see and are governed by: the board, the workflow editor, eligibility, gates
and signatures, and the track. What actually executes a block can be swapped. A backend takes a block and its
checked inputs and returns outputs, which the hub then checks against the block's ``output`` fields, like any answer.

* ``local``: built in. Runs python blocks in the sandbox. Always available.
* ``webhook``: any engine or service that accepts a JSON POST and answers with JSON (a Kestra webhook trigger, an
  Activepieces flow, an in-house service). ``PRACTICEHUB_ENGINE_WEBHOOK_URL`` (+ ``_TOKEN``).
* ``kestra``: starts a Kestra flow execution and waits for its outputs. ``KESTRA_URL`` (+ ``KESTRA_TENANT``,
  ``KESTRA_TOKEN`` or ``KESTRA_USER``/``KESTRA_PASSWORD``). Written against Kestra's REST API; not yet run against a
  live server.

To try the webhook path on one machine, ``scripts/dev-engine.py`` is an engine that answers with a local model.

To add an engine, write a class with ``name``, ``configured()``, ``describe()`` and ``run(block, inputs, meta)``,
add it to ``backend_for`` and ``ENGINES``. An engine sees only the block's declared inputs, never the whole run: the
inputs are what the approver saw. Data residency applies: point engines only at servers the practice controls.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request

from . import sandbox

ENGINES = ("webhook", "kestra")


class EngineError(Exception):
    """The engine could not run the block. The message is for a person."""


class LocalPython:
    name = "local"
    label = "Built-in Python sandbox"

    def configured(self) -> bool:
        return True

    def describe(self) -> str:
        return (f"Runs python blocks in a separate interpreter: {sandbox.TIMEOUT:g}s wall clock, {sandbox.CPU_SECONDS}s "
                f"CPU, {sandbox.MEMORY // 2**20} MB, no files, network or processes.")

    def run(self, block, inputs: dict, meta: dict) -> dict:
        try:
            return sandbox.run_code(block.code, inputs)
        except sandbox.SandboxError as exc:
            raise EngineError(str(exc)) from None


def _timeout() -> float:
    return float(os.environ.get("PRACTICEHUB_ENGINE_TIMEOUT", "60"))


def _send(req: urllib.request.Request, what: str) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            data = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise EngineError(f"{what} answered {exc.code}: {exc.read().decode(errors='replace')[:300]}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EngineError(f"cannot reach {what}: {exc}") from None
    except json.JSONDecodeError:
        raise EngineError(f"{what} did not answer with JSON") from None
    if not isinstance(data, dict):
        raise EngineError(f"{what}'s answer is not an object")
    return data


_KESTRA_ID = re.compile(r"[A-Za-z0-9._-]+")


def kestra_target(target: str) -> tuple[str, str]:
    """``namespace/flow-id`` split and checked, so neither part can steer the URL; raises EngineError."""
    parts = str(target or "").split("/")
    if len(parts) != 2 or not all(_KESTRA_ID.fullmatch(p) and ".." not in p and p.strip(".") for p in parts):
        raise EngineError("a Kestra block's target is namespace/flow-id: letters, digits, '.', '_' and '-', "
                          "with one '/' between them")
    return parts[0], parts[1]


class Webhook:
    """POST {block, version, target, input, run, step} -> {"output": {...}} (or the outputs themselves)."""

    name = "webhook"
    label = "Webhook (any engine that speaks JSON)"

    def __init__(self, url: str | None = None, token: str | None = None):
        self.url = url if url is not None else os.environ.get("PRACTICEHUB_ENGINE_WEBHOOK_URL", "")
        self.token = token if token is not None else os.environ.get("PRACTICEHUB_ENGINE_WEBHOOK_TOKEN", "")

    def configured(self) -> bool:
        return bool(self.url)

    def describe(self) -> str:
        if not self.url:
            return "Not set up: set PRACTICEHUB_ENGINE_WEBHOOK_URL."
        return f"Posts to {urllib.parse.urlsplit(self.url).netloc}."

    def run(self, block, inputs: dict, meta: dict) -> dict:
        if not self.url:
            raise EngineError("the webhook engine is not set up (PRACTICEHUB_ENGINE_WEBHOOK_URL)")
        body = {"block": block.id, "version": meta.get("version"), "target": block.target, "input": inputs,
                "run": meta.get("run"), "step": meta.get("step")}
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = _send(urllib.request.Request(self.url, data=json.dumps(body, default=str).encode(), method="POST",
                                            headers=headers), "the webhook engine")
        out = data.get("output", data)
        if not isinstance(out, dict):
            raise EngineError("the webhook engine's answer is not an object")
        return {"output": out, "log": str(data.get("log", ""))[:2000] if "output" in data else "",
                "ref": str(data.get("ref", ""))}


class Kestra:
    """Start ``namespace/flow-id`` with the block's inputs and wait for the execution's outputs."""

    name = "kestra"
    label = "Kestra"

    def __init__(self, url: str | None = None):
        self.url = (url if url is not None else os.environ.get("KESTRA_URL", "")).rstrip("/")
        self.tenant = os.environ.get("KESTRA_TENANT", "")
        self.token = os.environ.get("KESTRA_TOKEN", "")
        self.user, self.password = os.environ.get("KESTRA_USER", ""), os.environ.get("KESTRA_PASSWORD", "")

    def configured(self) -> bool:
        return bool(self.url)

    def describe(self) -> str:
        if not self.url:
            return "Not set up: set KESTRA_URL."
        return f"Starts flows on {urllib.parse.urlsplit(self.url).netloc} and waits for their outputs."

    def _auth(self) -> dict:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        if self.user:
            raw = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
            return {"Authorization": f"Basic {raw}"}
        return {}

    def run(self, block, inputs: dict, meta: dict) -> dict:
        if not self.url:
            raise EngineError("the Kestra engine is not set up (KESTRA_URL)")
        ns, flow = kestra_target(block.target)
        if self.tenant and not (_KESTRA_ID.fullmatch(self.tenant) and ".." not in self.tenant):
            raise EngineError("KESTRA_TENANT may hold only letters, digits, '.', '_' and '-'")
        tenant = f"/{self.tenant}" if self.tenant else ""
        url = (f"{self.url}/api/v1{tenant}/executions/{ns}/{flow}"
               f"?wait=true&labels=practicehub-run:{urllib.parse.quote(str(meta.get('run') or ''))}")
        boundary = "ph" + secrets.token_hex(12)
        parts = []
        for k, v in inputs.items():  # Kestra takes flow inputs as multipart form fields
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(k)):
                raise EngineError(f"the input name {k!r} cannot be sent to Kestra")
            text = v if isinstance(v, str) else json.dumps(v, default=str)
            if boundary in text:
                raise EngineError(f"the input {k!r} cannot be sent to Kestra")
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{text}\r\n')
        body = ("".join(parts) + f"--{boundary}--\r\n").encode()
        ex = _send(urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}", **self._auth()}), "Kestra")
        state = ex.get("state")
        state = str((state.get("current") if isinstance(state, dict) else "") or "").upper()
        if state != "SUCCESS":
            raise EngineError(f"the Kestra execution {ex.get('id', '')} ended {state or 'without a state'}")
        out = ex.get("outputs") or {}
        if not isinstance(out, dict):
            raise EngineError("the Kestra flow's outputs are not an object")
        return {"output": out, "log": "", "ref": str(ex.get("id", ""))}


def backend_for(block):
    if block.runtime == "python":
        return LocalPython()
    if block.engine == "webhook":
        return Webhook()
    if block.engine == "kestra":
        return Kestra()
    raise EngineError(f"there is no engine {block.engine!r}")


def engines() -> list:
    """Every engine, for the settings page."""
    return [LocalPython(), Webhook(), Kestra()]
