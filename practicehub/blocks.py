"""Blocks: reusable, parameterised steps a practice builds once and drops into any workflow.

A block is a TOML document with typed ``input`` and ``output`` fields and one of three runtimes:

* ``python``: deterministic code, ``def run(input) -> dict``, run in the sandbox (``practicehub/sandbox.py``). Only a
  person may propose one, and a different policy owner approves and signs it, like a workflow change.
* ``prompt``: an injected prompt that an agent carries out. The prompt is filled from the block's inputs and
  becomes the agent's instructions; the answer is validated against the block's outputs like any answer. An agent
  may take it only when the block (or the skill it ``extends``) is in the agent's profile.
* ``external``: the work runs on an automation engine underneath the hub (``engine = "webhook"`` or
  ``"kestra"``, see ``practicehub/backends.py``). The hub keeps the gates, the eligibility rules and the track.

A workflow step uses a block with ``block = "<id>"`` and fills its inputs with ``params``, which may read the run
(``"{{intake.reason}}"``). Blocks are versioned and approved like workflows; a run uses the block versions that were
active when it started.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass, field

from .fields import check_spec, normalize
from .sandbox import check_code
from .workflows import DefinitionError, TEMPLATE

RUNTIMES = ("python", "prompt", "external")
ID = re.compile(r"[a-z][a-z0-9\-]*")


@dataclass
class Block:
    id: str
    title: str
    runtime: str
    summary: str = ""
    inputs: list[dict] = field(default_factory=list)
    outputs: list[dict] = field(default_factory=list)
    code: str = ""
    prompt: str = ""
    extends: str | None = None   # prompt blocks: the skill whose eligibility, instructions and guards they build on
    engine: str | None = None    # external blocks: which backend runs them
    target: str = ""             # external blocks: what to run there (a flow id, a webhook route)
    source: str = ""

    @property
    def step_kind(self) -> str:
        """The kind of workflow step that uses this block."""
        return "task" if self.runtime == "prompt" else "automation"

    @property
    def capability(self) -> str:
        """What an agent's profile must list to take a prompt block's work: the skill it extends, else the block."""
        return self.extends or self.id

    @property
    def code_sha256(self) -> str:
        return hashlib.sha256(self.code.encode()).hexdigest() if self.code else ""


def known_skills(catalog) -> dict:
    """The skills a catalog (the hub, or a pack before it is installed) knows."""
    if getattr(catalog, "skills_fn", None):
        return catalog.skills_fn()
    if hasattr(catalog, "all_skills"):
        return catalog.all_skills()
    return getattr(catalog, "skills", {}) or {}


def parse(source: str, catalog=None) -> Block:
    """Parse and check a block. ``catalog`` (a Pack) checks the skill a prompt block extends."""
    try:
        doc = tomllib.loads(source)
    except tomllib.TOMLDecodeError as exc:
        raise DefinitionError([f"The block is not valid TOML: {exc}"]) from None
    problems: list[str] = []
    bid = str(doc.get("id", ""))
    if not ID.fullmatch(bid) or bid == "new":
        problems.append("id: give the block an id of lower-case letters, digits and dashes (not 'new')")
    if not doc.get("title"):
        problems.append("title: give the block a title")
    runtime = doc.get("runtime", "")
    if runtime not in RUNTIMES:
        problems.append(f"runtime: one of {', '.join(RUNTIMES)}")
    io = {}
    for key in ("input", "output"):
        io[key] = []
        for spec in doc.get(key, []) or []:
            stray = [k for k in ("code", "prompt", "runtime", "engine", "target", "extends") if k in spec]
            if stray:
                problems.append(f"{key} {spec.get('name')!r}: {', '.join(stray)} ended up inside this field; in TOML, "
                                f"write top-level keys before the first [[input]] or [[output]]")
            problems += [f"{key}: {p}" for p in check_spec(spec)]
            if spec.get("name"):
                io[key].append(normalize(spec))
    b = Block(id=bid, title=str(doc.get("title", "")), runtime=runtime, summary=str(doc.get("summary", "")).strip(),
              inputs=io["input"], outputs=io["output"], code=str(doc.get("code", "")),
              prompt=str(doc.get("prompt", "")).strip(), extends=doc.get("extends"), engine=doc.get("engine"),
              target=str(doc.get("target", "")), source=source)
    names = {f["name"] for f in b.inputs}
    if runtime == "python":
        problems += check_code(b.code)
        if not b.outputs:
            problems.append("output: say what the code returns, so its answer can be checked")
    elif b.code:
        problems.append("code: only a python block has code")
    if runtime == "prompt":
        if not b.prompt:
            problems.append("prompt: write what the agent should do; {{input.<name>}} fills in an input")
        for m in TEMPLATE.finditer(b.prompt):
            path = m.group(1)
            if not path.startswith("input.") or path.split(".")[1] not in names:
                problems.append(f"prompt: {{{{{path}}}}} is not one of the block's inputs "
                                f"({', '.join('input.' + n for n in sorted(names)) or 'it has none'})")
        if not b.outputs and not b.extends:
            problems.append("output: say what the agent answers with, or extend a skill that does")
        if catalog is not None and b.extends and b.extends not in known_skills(catalog):
            problems.append(f"extends: there is no skill {b.extends!r}")
    elif b.extends:
        problems.append("extends: only a prompt block extends a skill")
    if runtime == "external":
        from .backends import ENGINES
        if b.engine not in ENGINES:
            problems.append(f"engine: one of {', '.join(ENGINES)}")
        if not b.target:
            problems.append("target: say what to run on the engine (for Kestra, namespace/flow-id)")
        if not b.outputs:
            problems.append("output: say what the engine returns, so its answer can be checked")
    if problems:
        raise DefinitionError(problems)
    return b


def render_prompt(block: Block, inputs: dict, literal: set[str]) -> str:
    """The prompt with its inputs filled in. Only inputs the workflow author wrote as plain values (``literal``) are
    written into the instructions; anything read from the run (a patient's words, a record) stays data: the prompt
    points at it, and the agent's context carries it inside <context>, where it is never an instruction."""
    from .workflows import _text

    def fill(m):
        name = m.group(1).split(".", 1)[1]
        return _text(inputs.get(name)) if name in literal else f"[{name}: see block-input.{name} in the context]"

    return TEMPLATE.sub(fill, block.prompt)
