"""Run a Python block's code: a separate, short-lived, locked-down interpreter.

A Python block is code a person wrote and another person approved and signed (agents may not author it). It defines
``run(input) -> dict``, a pure function from its inputs to its outputs. Anything with an effect (a message, a record)
is done afterwards by an ordinary automation step reading those outputs, so it is checked and audited like any other.

The real control is people: code is written by a person, reviewed in the diff, approved by a different policy owner
and signed, and every run records the code's SHA-256. What this module adds is defence in depth, in three layers,
and only the last one is a security boundary in the strict sense:

* a static check when the code is proposed (``check_code``): it must define ``run``, may import only
  ALLOWED_IMPORTS, may not touch private or dunder attributes, reach modules through other modules, or name builtins
  like ``open``, ``eval`` or ``getattr``. This is a lint that catches mistakes and the obvious; Python is too dynamic
  for a static check to be a wall.
* a Python-level guard in the child: every allowed module is imported, and the input read, before an audit hook is
  installed. The hook has no state reachable from Python, so once installed it refuses, for good: opening any file,
  importing anything not already loaded or allowed, compiling or executing new code, the ``os``/``socket``/
  ``subprocess`` families, frames and tracing, and every other audited event not on a short list of harmless ones.
  Audit hooks are not designed as a sandbox (some C functions raise no event), so this narrows, it does not seal.
* the OS boundary: the child runs as ``python -I -S`` with an empty environment and an empty temporary directory,
  under resource limits (CPU seconds, address space, no file writes, no new files or processes), and, where the host
  allows unprivileged user namespaces, inside ``unshare --user --net``: no network at all and no privileges. Run as
  root, the child drops to ``nobody``.

For production, run python blocks on an isolated engine (gVisor, nsjail or a Firecracker VM around this sandbox, or
an external engine block) rather than relying on this process alone. ``os_boundary()`` says what this host provides.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import types

from . import decimals

# Modules a block may import. None of them may be reached *through* another module in block code (``re.enum``,
# ``statistics.sys``): check_code refuses attributes of a module that are themselves modules.
ALLOWED_IMPORTS = ("math", "statistics", "datetime", "decimal", "fractions", "re", "json", "itertools", "functools",
                   "collections", "string", "textwrap", "zoneinfo")
FORBIDDEN_NAMES = {"open", "eval", "exec", "compile", "__import__", "globals", "locals", "vars", "getattr", "setattr",
                   "delattr", "breakpoint", "memoryview", "help", "exit", "quit", "__builtins__"}
# Attribute or imported names that look things up by string (string.Formatter().get_field("0.sys", ...)).
FORBIDDEN_ATTRS = {"Formatter", "get_field", "get_value", "vformat", "format_field", "convert_field"}
TIMEOUT = 5.0         # seconds of wall-clock time
CPU_SECONDS = 2
MEMORY = 512 * 2**20  # bytes of address space
MAX_IO = 2**20        # bytes in and out
NOBODY = 65534


class SandboxError(Exception):
    """The code failed, was refused, or ran too long. The message is for a person."""


def _module(name: str):
    if name.split(".")[0] not in ALLOWED_IMPORTS or "." in name:
        return None
    try:
        return __import__(name)
    except ImportError:
        return None


def check_code(code: str) -> list[str]:
    """Problems with a block's code, in words. An empty list means it may be proposed."""
    if not code.strip():
        return ["code: write a function `def run(input):` that returns a dict"]
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"code: line {exc.lineno}: {exc.msg}"]
    problems = []
    if not any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body):
        problems.append("code: define a top-level function `def run(input):` that returns a dict")
    allowed = ", ".join(ALLOWED_IMPORTS)
    modules: dict[str, types.ModuleType] = {}   # local name -> the module it is bound to
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mod = _module(a.name)
                if mod is None:
                    problems.append(f"code: line {node.lineno}: `import {a.name}` is not allowed (allowed: {allowed})")
                else:
                    modules[a.asname or a.name] = mod
    attr_values = set()
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, ast.ImportFrom):
            mod = None if node.level else _module(node.module or "")
            if mod is None:
                problems.append(f"code: line {line}: `from {node.module} import ...` is not allowed "
                                f"(allowed: {allowed})")
                continue
            for a in node.names:
                if a.name == "*" or a.name.startswith("_") or a.name in FORBIDDEN_ATTRS \
                        or isinstance(getattr(mod, a.name, None), types.ModuleType):
                    problems.append(f"code: line {line}: `from {node.module} import {a.name}` is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                problems.append(f"code: line {line}: `.{node.attr}`: private and dunder attributes are not allowed")
            elif node.attr in FORBIDDEN_ATTRS:
                problems.append(f"code: line {line}: `.{node.attr}` is not allowed in a block")
            elif isinstance(node.value, ast.Name) and node.value.id in modules:
                attr_values.add(id(node.value))
                if isinstance(getattr(modules[node.value.id], node.attr, None), types.ModuleType):
                    problems.append(f"code: line {line}: `{node.value.id}.{node.attr}`: reaching a module through "
                                    f"another module is not allowed; import what you need")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            problems.append(f"code: line {line}: `global` and `nonlocal` are not allowed; return what you compute")
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            line = node.lineno
            if node.id in FORBIDDEN_NAMES or node.id.startswith("__"):
                problems.append(f"code: line {line}: `{node.id}` is not allowed in a block")
            elif node.id in modules and id(node) not in attr_values:
                problems.append(f"code: line {line}: a module (`{node.id}`) may only be used as `{node.id}.name`")
    return problems


# The child interpreter. Everything it needs is loaded before the audit hook goes in; the hook itself uses only
# literal constants (no globals, no closures, no defaults), so nothing the block can reach changes what it allows.
# The rest runs inside main(), whose locals the block cannot reach, and ends with os._exit so that no Python code
# (a __del__, say) runs at interpreter shutdown, when CPython clears audit hooks.
_CHILD = r"""
import sys, json, io, os, resource
def lim(kind, n):
    try:
        resource.setrlimit(kind, (n, n))
    except (ValueError, OSError):
        pass
lim(resource.RLIMIT_CPU, %(cpu)d)
lim(resource.RLIMIT_AS, %(mem)d)
lim(resource.RLIMIT_CORE, 0)

def hook(event, args):
    if event == "import":
        if args[0].partition(".")[0] in %(allowed)r:
            return
        raise RuntimeError("import of %%s is not allowed in a block" %% (args[0],))
    if event == "exec":
        if args[0].co_filename == "<block>":
            return
        raise RuntimeError("running new code is not allowed in a block")
    if event in ("builtins.id", "re.compile", "time.sleep", "sys._getframemodulename", "object.__delattr__"):
        return
    if event == "object.__setattr__" and args[1] not in ("__code__", "__defaults__", "__kwdefaults__"):
        return
    raise RuntimeError("%%s is not allowed in a block" %% (event,))

def main():
    import decimal
    def exact_in(d):                               # {"$decimal": "12.30"}: an exact number (decimals.tag)
        if len(d) == 1 and isinstance(d.get("$decimal"), str):
            return decimal.Decimal(d["$decimal"])
        return d
    def exact_out(o):
        if isinstance(o, decimal.Decimal) and o.is_finite():
            return {"$decimal": format(o, "f")}
        return str(o)
    req = json.loads(sys.stdin.read(), object_hook=exact_in)
    for m in %(allowed)r:
        __import__(m)
    for m in ("_strptime", "heapq", "copy", "weakref", "unicodedata", "warnings", "_statistics", "locale"):
        try:                                       # what allowed modules import lazily, on first use
            __import__(m)
        except ImportError:
            pass
    import datetime, zoneinfo, warnings
    warnings.simplefilter("ignore")                # showing a warning reads source files
    datetime.datetime.strptime("2000-01-02", "%%Y-%%m-%%d")
    zones = []                                     # zoneinfo reads files: load the ones the block may ask for
    for key in req.get("zones", ()):
        try:
            zones.append(zoneinfo.ZoneInfo(key))
        except Exception:
            pass
    write, exit_, dumps = os.write, os._exit, json.dumps
    try:
        code = compile(req["code"], "<block>", "exec")
    except BaseException as exc:
        code = None
        error = "%%s: %%s" %% (type(exc).__name__, exc)
    lim(resource.RLIMIT_FSIZE, 0)
    lim(resource.RLIMIT_NPROC, 0)
    lim(resource.RLIMIT_NOFILE, 3)
    log = io.StringIO()
    sys.stdout = sys.stderr = log
    ns = {"__name__": "block"}
    sys.addaudithook(hook)
    try:
        if code is None:
            raise SyntaxError(error)
        exec(code, ns)
        result = ns["run"](req["input"])
        if not isinstance(result, dict):
            raise TypeError("run() returned %%s, not a dict" %% type(result).__name__)
        payload = {"ok": True, "output": result}
    except BaseException as exc:
        try:
            payload = {"ok": False, "error": ("%%s: %%s" %% (type(exc).__name__, exc)).rstrip(": ")}
        except BaseException:
            payload = {"ok": False, "error": "the block failed"}
    try:
        payload["log"] = log.getvalue()[-2000:]
        try:
            text = dumps(payload, default=exact_out)
        except (TypeError, ValueError) as exc:
            text = dumps({"ok": False, "error": "the output is not JSON: %%s" %% exc, "log": payload["log"]})
        data = text.encode()
        while data:
            data = data[write(1, data):]
    finally:
        exit_(0)

main()
"""

_boundary: dict | None = None
_boundary_lock = threading.Lock()


def _as_nobody() -> dict:
    return {"user": NOBODY, "group": NOBODY, "extra_groups": []} if os.geteuid() == 0 else {}


def os_boundary() -> dict:
    """What this host gives the child beyond resource limits: ``{"prefix": [...], "network": bool, "user": str}``.
    Detected once and cached. ``network`` is False when the child runs with no network."""
    global _boundary
    with _boundary_lock:
        if _boundary is None:
            prefix: list[str] = []
            unshare = shutil.which("unshare")
            if unshare:
                cmd = [unshare, "--user", "--net", sys.executable, "-I", "-S", "-c", "pass"]
                try:
                    ok = subprocess.run(cmd, capture_output=True, timeout=10, env={}, cwd=tempfile.gettempdir(),
                                        **_as_nobody()).returncode == 0
                except (OSError, subprocess.SubprocessError):
                    ok = False
                if ok:
                    prefix = [unshare, "--user", "--net"]
            _boundary = {"prefix": prefix, "network": not prefix,
                         "user": "nobody" if os.geteuid() == 0 else "the server's user"}
        return _boundary


_ZONE = re.compile(r"^(UTC|[A-Z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+){1,2})$")


def _zones(code: str, inputs) -> list[str]:
    """Strings in the code or the inputs that look like time-zone keys, so the child can load them in advance."""
    found = {"UTC"}
    try:
        found |= {n.value for n in ast.walk(ast.parse(code)) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    except SyntaxError:
        pass
    stack = [inputs]
    while stack and len(found) < 200:
        v = stack.pop()
        if isinstance(v, str):
            found.add(v)
        elif isinstance(v, dict):
            stack.extend(v.values())
        elif isinstance(v, (list, tuple)):
            stack.extend(v)
    return sorted(k for k in found if len(k) < 64 and _ZONE.match(k))[:32]


def run_code(code: str, inputs: dict, *, timeout: float = TIMEOUT) -> dict:
    """Run ``run(inputs)`` in a sandboxed interpreter. Returns ``{"output": dict, "log": str}`` or raises
    SandboxError."""
    problems = check_code(code)
    if problems:
        raise SandboxError("; ".join(problems))
    request = json.dumps({"code": code, "input": decimals.tag(inputs), "zones": _zones(code, inputs)}, default=str)
    if len(request) > MAX_IO:
        raise SandboxError("the block's input is too large")
    child = _CHILD % {"cpu": CPU_SECONDS, "mem": MEMORY, "allowed": ALLOWED_IMPORTS}
    boundary = os_boundary()
    with tempfile.TemporaryDirectory(prefix="ph-block-") as tmp:
        os.chmod(tmp, 0o555)
        try:
            proc = subprocess.run([*boundary["prefix"], sys.executable, "-I", "-S", "-B", "-c", child],
                                  input=request, capture_output=True, text=True, timeout=timeout, cwd=tmp, env={},
                                  close_fds=True, **_as_nobody())
        except subprocess.TimeoutExpired:
            raise SandboxError(f"the block ran longer than {timeout:g} seconds and was stopped") from None
        finally:
            os.chmod(tmp, 0o700)
    if len(proc.stdout) > MAX_IO:
        raise SandboxError("the block's output is too large")
    try:
        payload = json.loads(proc.stdout)
        if not isinstance(payload, dict):
            raise ValueError
    except ValueError:
        why = "it used more than its CPU or memory allowance" if proc.returncode and proc.returncode < 0 else \
            (proc.stderr.strip().splitlines() or ["no output"])[-1][:300]
        raise SandboxError(f"the block stopped without an answer: {why}") from None
    if not payload.get("ok"):
        raise SandboxError(str(payload.get("error") or "the block failed"))
    if not isinstance(payload.get("output"), dict):
        raise SandboxError("the block's answer is not an object")
    return {"output": decimals.untag(payload["output"]), "log": str(payload.get("log", ""))}
