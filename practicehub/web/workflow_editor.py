"""The workflow editor's model, written out as the TOML a workflow is authored in.

The visual editor (``editor`` in ``assets.py``) edits a workflow as the document ``tomllib`` reads from its text:
``[start]``, ``[[state]]`` with its ``[[state.work]]``, and ``[[edge]]`` with its ``[[edge.do]]`` (see
``practicehub/states.py``). It sends that document here, and the hub writes the text: the same text is checked as it
is edited, shown in the text tab, and proposed. Keys the editor has no control for are kept as they were read.

A workflow in the old form (a list of ``[[steps]]``) is not opened in the editor: it says so and offers the text.
"""

from __future__ import annotations

import json

from .. import expr
from ..bpmn import _key, _value

HEAD = ("id", "title", "record", "summary")
DROP_FALSE = {"persons_only", "required"}  # false is what leaving them out says
CONDITIONS = ("when", "until", "agent_criteria")


def is_old_form(data) -> bool:
    """Whether a workflow's document is written in the old form, as a list of steps, rather than states and edges."""
    return isinstance(data, dict) and "steps" in data and not ("state" in data or "start" in data or "edge" in data)


def _clean(v):
    """What the editor's model says, without what an empty control leaves behind (blank text, empty lists)."""
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if str(k).startswith("_"):
                continue
            x = _clean(x)
            if x is None or (isinstance(x, (str, list, dict)) and not x) or (x is False and k in DROP_FALSE):
                continue
            out[str(k)] = x
        return out
    if isinstance(v, list):
        return [_clean(x) for x in v if x is not None]
    return v


def _aot(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def _table(path: list[str], t: dict, lines: list[str]) -> None:
    for k, v in t.items():
        if not isinstance(v, dict) and not _aot(v):
            lines.append(f"{_key(k)} = {_value(v, inline=False)}")
    for k, v in t.items():
        if isinstance(v, dict):
            lines.extend(["", f"[{'.'.join(_key(p) for p in path + [k])}]"])
            _table(path + [k], v, lines)
    for k, v in t.items():
        if _aot(v):
            for item in v:
                lines.extend(["", f"[[{'.'.join(_key(p) for p in path + [k])}]]"])
                _table(path + [k], item, lines)


def editor_toml(model: dict) -> str:
    """The workflow's text from the editor's model: its own keys, its tables (``[trigger]``, ``[start]``...), what the
    starter gives, then each state followed by the edges out of it."""
    if not isinstance(model, dict):
        raise ValueError("the workflow is a table of its keys")
    doc = _clean(model)
    lines: list[str] = []
    head = {k: doc[k] for k in HEAD if k in doc}
    rest = {k: v for k, v in doc.items() if k not in HEAD}
    _table([], {**head, **{k: v for k, v in rest.items() if not isinstance(v, dict) and not _aot(v)}}, lines)
    _table([], {k: v for k, v in rest.items() if isinstance(v, dict)}, lines)
    states = doc.get("state") if _aot(doc.get("state")) else []
    edges = doc.get("edge") if _aot(doc.get("edge")) else []
    for k, v in rest.items():
        if _aot(v) and k not in ("state", "edge"):
            _table([], {k: v}, lines)
    placed: set[int] = set()
    for s in states:
        _table([], {"state": [s]}, lines)
        out = [e for i, e in enumerate(edges) if i not in placed and e.get("from") == s.get("id")]
        placed |= {i for i, e in enumerate(edges) if e.get("from") == s.get("id")}
        if out:
            _table([], {"edge": out}, lines)
    rest_edges = [e for i, e in enumerate(edges) if i not in placed]
    if rest_edges:
        _table([], {"edge": rest_edges}, lines)
    return "\n".join(lines).strip() + "\n"


def source_of(form: dict) -> str:
    """The text a proposal sends: the text editor's, else the visual editor's model written out."""
    source = str(form.get("source") or "")
    if source.strip() or not form.get("model"):
        return source
    try:
        return editor_toml(json.loads(form["model"]))
    except (ValueError, TypeError):
        return ""


def condition_words(data) -> dict:
    """Each condition the workflow says, in words, for the editor to show beside it (``expr.words``)."""
    out: dict = {}

    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                if k in CONDITIONS and isinstance(x, str) and x.strip():
                    out[x] = expr.words(x)
                else:
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(data)
    return out
