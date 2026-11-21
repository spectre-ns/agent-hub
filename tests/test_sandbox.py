"""The python-block sandbox and the external engines' input checks."""

import io
import json
import types
from pathlib import Path

import pytest

from practicehub import backends, blocks, sandbox

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def secret(tmp_path):
    f = tmp_path / "secret.txt"
    f.write_text("TOP-SECRET-42")
    return str(f)


@pytest.fixture
def unchecked(monkeypatch):
    """Skip the static check, to show the child refuses on its own."""
    monkeypatch.setattr(sandbox, "check_code", lambda code: [])


def _refused(code, secret, inputs=None):
    try:
        result = sandbox.run_code(code, inputs or {})
    except sandbox.SandboxError as exc:
        assert "TOP-SECRET" not in str(exc)
        return str(exc)
    assert "TOP-SECRET" not in json.dumps(result)
    return None


ESCAPES = {
    # the reported escape: flip the hook's switch through an allowed module, then read a file
    "calendar.sys": "import calendar\ndef run(input):\n    m = calendar.sys.modules['__main__']\n    m.armed[0] = False\n"
                    "    return {'x': calendar.sys.modules['io'].open(PATH).read()}",
    "attrgetter": "import operator, collections\ndef run(input):\n    s = operator.attrgetter('_sys')(collections)\n"
                  "    return {'x': s.modules['io'].open(PATH).read()}",
    "uuid": "import uuid\ndef run(input):\n    return {'x': uuid.os.listdir('/')}",
    "collections._sys": "import collections\ndef run(input):\n    s = collections._sys\n"
                        "    m = s.modules['__main__']\n    m.__dict__.clear()\n"
                        "    return {'x': s.modules['io'].open(PATH).read()}",
    "statistics.sys io": "import statistics\ndef run(input):\n    return {'x': statistics.sys.modules['io'].open(PATH).read()}",
    "_io.FileIO": "import statistics\ndef run(input):\n    return {'x': statistics.sys.modules['_io'].FileIO(PATH).read()}",
    "re.enum.sys os": "import re\ndef run(input):\n    o = re.enum.sys.modules['os']\n"
                      "    return {'x': o.read(o.open(PATH, 0), 100)}",
    "json chain": "import json\ndef run(input):\n    return {'x': json.codecs.sys.modules['io'].open(PATH).read()}",
    "builtins.__import__ os": "def run(input):\n    b = __builtins__\n    imp = b['__import__'] if isinstance(b, dict) else b.__import__\n"
                              "    return {'x': imp('os').listdir('/')}",
    "importlib": "import statistics\ndef run(input):\n    il = statistics.sys.modules['_frozen_importlib']\n"
                 "    return {'x': str(il.__import__('socket'))}",
    "import socket": "def run(input):\n    import socket\n    return {'x': str(socket.socket())}",
    "import ctypes": "def run(input):\n    import ctypes\n    return {}",
    "open": "def run(input):\n    return {'x': open(PATH).read()}",
    "subprocess": "import statistics\ndef run(input):\n    return {'x': statistics.sys.modules['os'].system('cat ' + PATH)}",
    "fork": "import statistics\ndef run(input):\n    return {'x': statistics.sys.modules['os'].fork()}",
    "exec new code": "def run(input):\n    exec('import os')\n    return {}",
    "frames": "import statistics\ndef run(input):\n    return {'x': str(statistics.sys._getframe(1).f_globals)}",
    "swap the hook": "import statistics\ndef run(input):\n    m = statistics.sys.modules['__main__']\n"
                     "    m.hook.__code__ = (lambda e, a: None).__code__\n"
                     "    return {'x': statistics.sys.modules['io'].open(PATH).read()}",
    "formatter": "import string\ndef run(input):\n    s = string.Formatter().get_field('0.sys', [string.re.enum], {})[0]\n"
                 "    return {'x': s.modules['io'].open(PATH).read()}",
}


@pytest.mark.parametrize("name", sorted(ESCAPES))
def test_escapes_are_refused_by_the_static_check(name, secret):
    code = ESCAPES[name].replace("PATH", repr(secret))
    assert sandbox.check_code(code), name
    assert _refused(code, secret)


@pytest.mark.parametrize("name", sorted(ESCAPES))
def test_escapes_are_refused_by_the_child_itself(name, secret, unchecked):
    code = ESCAPES[name].replace("PATH", repr(secret))
    assert _refused(code, secret), f"{name} ran"


def test_code_run_at_shutdown_cannot_read_files(secret, unchecked):
    # CPython clears audit hooks at shutdown; a __del__ must never get to run then
    code = ("import statistics\nclass K:\n    def __del__(self):\n        o = statistics.sys.modules['os']\n"
            "        o.write(1, statistics.sys.modules['io'].open(PATH).read().encode())\n"
            "def run(input):\n    o = statistics.sys.modules['os']\n    o._exit = lambda n: None\n"
            "    statistics.sys.modules['__main__'].keep = K()\n    return {'ok': 1}\n").replace("PATH", repr(secret))
    assert sandbox.run_code(code, {})["output"] == {"ok": 1}


def test_dropped_modules():
    for m in ("calendar", "uuid", "operator", "os", "sys", "importlib", "io"):
        assert m not in sandbox.ALLOWED_IMPORTS
        assert sandbox.check_code(f"import {m}\ndef run(input):\n    return {{}}")
    assert sandbox.check_code("from collections import abc\ndef run(input):\n    return {}")
    assert sandbox.check_code("import statistics\ndef run(input):\n    f = statistics\n    return {}")
    assert sandbox.check_code("import json.decoder\ndef run(input):\n    return {}")


def test_no_network_when_the_host_allows_namespaces(unchecked):
    if not sandbox.os_boundary()["prefix"]:
        pytest.skip("no unprivileged user namespaces here")
    assert sandbox.os_boundary()["network"] is False


def test_normal_blocks_still_work():
    code = """
import datetime, zoneinfo, statistics, re, json, collections, decimal, fractions, textwrap, string, math, functools
from collections import Counter
class Total:
    def __init__(self, n):
        self.n = n
@functools.lru_cache
def sq(x):
    return x * x
def run(input):
    d = datetime.datetime.strptime(input["day"], "%Y-%m-%d").replace(tzinfo=zoneinfo.ZoneInfo(input["tz"]))
    print("working")
    return {"day": d.isoformat(), "median": statistics.median([1, 2, 3]), "sub": re.sub("a", "b", "aa"),
            "top": Counter("aab").most_common(1)[0][0], "dec": str(decimal.Decimal("1.10") + 1),
            "frac": str(fractions.Fraction(1, 3)), "cap": string.capwords("a b"), "n": Total(3).n,
            "sq": sq(4), "sqrt": math.sqrt(16), "wrap": textwrap.fill("a b", 1), "j": json.loads("[1]")}
"""
    assert sandbox.check_code(code) == []
    res = sandbox.run_code(code, {"day": "2024-03-01", "tz": "Europe/London"})
    assert res["output"]["day"] == "2024-03-01T00:00:00+00:00"
    assert res["output"]["top"] == "a" and res["output"]["sq"] == 16 and res["log"] == "working\n"


def test_the_pack_block_runs_in_the_sandbox():
    block = blocks.parse((ROOT / "packs/general-practice/blocks/missed-appointment-fee.toml").read_text())
    run = lambda **kw: sandbox.run_code(block.code, kw)["output"]  # noqa: E731
    assert run(notice_hours=2, missed_this_year=1, fee=50) == {
        "charge": True, "amount": 50,
        "reason": "less than 24 hours' notice and 1 earlier missed appointment(s) this year"}
    assert run(notice_hours=30, missed_this_year=3, fee=50)["charge"] is False
    out = backends.LocalPython().run(block, {"notice_hours": 0, "missed_this_year": 0, "fee": 50}, {})
    assert out["output"]["charge"] is False


def test_failures_are_sandbox_errors():
    with pytest.raises(sandbox.SandboxError, match="not a dict"):
        sandbox.run_code("def run(input):\n    return [1]", {})
    with pytest.raises(sandbox.SandboxError, match="ZeroDivision"):
        sandbox.run_code("def run(input):\n    return {'x': 1 / 0}", {})


# --- engines ---------------------------------------------------------------------------------------------------

def _block(target="ops/fee", engine="kestra"):
    return types.SimpleNamespace(id="b", target=target, runtime="external", engine=engine, code="")


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def answer(monkeypatch):
    sent = []

    def set_answer(body):
        def urlopen(req, timeout=None):
            sent.append(req)
            return _Resp(json.dumps(body).encode())
        monkeypatch.setattr(backends.urllib.request, "urlopen", urlopen)
        return sent
    return set_answer


@pytest.mark.parametrize("target", ["ops", "a/b/c", "../x", "ops/..", "ops/.", "/flow", "ops/", "ops/fl ow",
                                    "ops/fl%2fow", "ops/flow?x=1", "ops/flow#x", "op\ns/flow", "ops\\x/flow", ""])
def test_kestra_targets_are_checked(target, answer):
    sent = answer({"state": {"current": "SUCCESS"}, "outputs": {}})
    with pytest.raises(backends.EngineError, match="namespace/flow-id"):
        backends.Kestra("http://kestra.local").run(_block(target), {}, {})
    assert not sent


def test_kestra_good_target(answer):
    sent = answer({"id": "e1", "state": {"current": "SUCCESS"}, "outputs": {"x": 1}})
    out = backends.Kestra("http://kestra.local").run(_block("company.team-1/my_flow.v2"), {"a": 1}, {"run": "r1"})
    assert out == {"output": {"x": 1}, "log": "", "ref": "e1"}
    assert sent[0].full_url.startswith("http://kestra.local/api/v1/executions/company.team-1/my_flow.v2?")


@pytest.mark.parametrize("body", [[1, 2], "SUCCESS", 3, None, {"state": "SUCCESS"}, {"state": {"current": "SUCCESS"},
                                                                                     "outputs": [1]}])
def test_kestra_odd_answers_are_engine_errors(body, answer):
    answer(body)
    with pytest.raises(backends.EngineError):
        backends.Kestra("http://kestra.local").run(_block(), {}, {})


@pytest.mark.parametrize("body", [[1, 2], "x", 3, None, {"output": [1]}])
def test_webhook_odd_answers_are_engine_errors(body, answer):
    answer(body)
    with pytest.raises(backends.EngineError):
        backends.Webhook("http://engine.local/hook").run(_block(engine="webhook"), {}, {})


def test_webhook_good_answer(answer):
    answer({"output": {"x": 1}, "log": "ok", "ref": "r"})
    assert backends.Webhook("http://engine.local/hook").run(_block(engine="webhook"), {}, {}) == {
        "output": {"x": 1}, "log": "ok", "ref": "r"}
