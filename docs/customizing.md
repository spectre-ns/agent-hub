# Customizing Practice Hub

The core knows nothing about any one business. What makes a hub a clinic, an accounting office or your own practice
is a set of **definitions**: TOML that says what you keep records of, how work moves, who may do what, and what
agents know how to do. You customize Practice Hub by changing definitions, not by changing the core.

If you have not run the demo yet, start with [Getting started](getting-started.md).

## Pick your level

There are four ways to customize, from least effort to most. Start at the top and go down only when you need to.

| You want to | Do this | You write |
|---|---|---|
| Change how a running practice works | [Use the settings pages](#1-change-a-running-practice-in-the-browser) | nothing: it is done in the browser |
| Run your own practice on a pack that ships | [Write an overlay](#2-your-own-practice-on-a-shipped-pack-an-overlay) | one `pack.toml`, a few lines |
| Add a capability any practice can install | [Write a module](#5-a-capability-any-practice-can-install-a-module) | a `module.toml`, types, views, workflows |
| Set up a different kind of business | [Write a pack](#6-another-kind-of-business-a-pack) | a `pack.toml`, types, workflows, skills, a small `pack.py` |

Two things hold at every level:

- **A change is a proposal.** One policy owner proposes it and a different one approves it, after seeing the diff.
  That is true in the browser, on the command line and for files on disk. The demo clinic has two policy owners,
  Rana and Dr. Amara, so you can try both sides.
- **The rules are not configurable.** Every agent has a sponsor, agents never sign, high-risk agent work is held for
  its sponsor, and every event is on the signed track. A pack decides *what* is high risk and *who* signs. It cannot
  switch the rule off. The README lists the rules.

## 1. Change a running practice in the browser

Everything a practice manager changes day to day is under **Settings**. Nothing here needs a file or a restart.

| Page | Path | What you change there |
|---|---|---|
| Practice | `/settings/practice` | the practice's name, contact details and time zone |
| Team | `/team` | people, agents and services: their roles, sponsors, and an agent's model, skills and risk limit |
| Modules | `/modules` | which modules are installed |
| Workflows | `/workflows` | how work moves: draw it in the diagram editor, or edit its TOML |
| Record types | `/define/types` | what you keep records of: fields, lifecycle states, signature meanings |
| Record structure | `/define/structure` | what is part of what, and the links between records |
| Views | `/define/views` | boards, tables and dashboards |
| Blocks | `/blocks` | reusable steps: Python, an agent prompt, or a call to an outside engine |
| Skills | `/define/skills` | what agents know how to do: instructions, and the shape of the answer |
| Connections (MCP) | `/settings/connections` | which MCP servers are switched on, and tokens for clients |

To try it on the demo: sign in as Rana, open **Workflows**, choose a workflow and **Edit workflow**. Change a step's
role or add a task, and save. Then sign in as Dr. Amara and approve it. Runs that had already started finish on the
version they started with.

The editors check a definition as you type and list every problem in words. [Writing workflows](workflows.md) is the
reference for what a workflow can say.

## 2. Your own practice on a shipped pack: an overlay

A **pack** makes a hub a kind of practice. Three ship in `packs/`:

| Pack | What it is |
|---|---|
| `general-practice` | a family medicine clinic: visits, lab results, renewals, referrals, claims |
| `accounting-office` | an accounting office: personal tax, bookkeeping, corporate year-end, GST/HST, payroll |
| `harbourview` | an overlay on `general-practice`: one clinic's choice of modules |

An **overlay** is a pack that names another as its base. It is how one clinic or one office gets its own name,
people and modules without copying the shared pack. This is a complete overlay:

```toml
# packs/northside/pack.toml
base = "general-practice"

modules = ["wiki", "crm"]            # the modules this practice runs, installed with it

[practice]
id = "northside"
name = "Northside Family Practice"
timezone = "America/Toronto"
```

Set it up like any pack:

```sh
practicehub init --pack northside --demo
```

What an overlay may say:

- **`[practice]` keys replace the base's one at a time.** Name what differs and leave the rest.
- **Any other top-level key replaces the base's whole.** If you write `[[members]]`, you write every member; the
  same goes for `[[roles]]`, `[[data_classes]]` and `[grants.*]`.
- **A definition replaces the base's by its id.** Put `workflows/lab-result.toml`, `skills/intake-review.toml`, a
  type, a block or a view in the overlay's directory, and it is used in place of the base's file with that id. A
  file with a new id is added.
- **A type of its own can be a kind of one of the base's.** `types/Farmer.toml` with `extends = "Individual"` adds a
  kind of individual: it has every field, check and lifecycle of `Individual` (and of `Client` above it), adds its
  own fields, and is filed, listed, searched and worked on wherever an individual is. See
  [A kind of another type](#a-kind-of-another-type).
- **An overlay brings no `pack.py`.** The code that runs is the base's, reviewed with it.
- **A base is not itself an overlay.** Build on the pack your base is built on.

Packs are looked up in `packs/`, or in the directory `PRACTICEHUB_PACKS` names. `--pack` also takes a path, so your
overlay can live in a repository of its own.

Useful `[practice]` settings:

| Key | What it does |
|---|---|
| `name`, `timezone`, `jurisdiction`, `data_residency` | who the practice is, and whose clock "today" follows |
| `escalation_role` | who gets work nobody else can take, and events no workflow took |
| `shortcuts` | record types listed first in the sidebar; `"start:<workflow>"` puts a workflow on the top bar |
| `agent_rules` | sentences said to every agent of the practice in its system prompt, such as "You never give tax advice" |
| `[practice.outbox.<channel>]` | how long a queued message may wait before someone is asked to deliver it by hand |

## 3. Agents: who they are and what they may do

An agent is a member of the practice, like a person. What it can do is the sum of five things, and each one is a
place to customize:

| Setting | Where | What it decides |
|---|---|---|
| **Roles** | the Team page, or `[[members]]` in the pack | which work it is offered, and through the roles' grants, which data classes it reads and writes |
| **Skills** | its profile | which tasks it knows how to do; a task names the skill it needs |
| **Risk limit** (`max_risk`) | its profile | the riskiest step it may take: `low`, `medium` or `high` |
| **Model** | its profile | what answers: `ollama/<model>` (a new agent is given `PRACTICEHUB_DEFAULT_MODEL`, else `ollama/qwen3:14b`), `litellm/<model>`, `anthropic/<model>`, or `standin` for the pack's fixed answers |
| **Sponsor** | the Team page | the person accountable for it, who issues its tokens and can switch it off |

Add an agent and give it a profile:

```sh
practicehub member add filing-agent "Filing agent" --kind agent --sponsor dr-amara --as rana
practicehub member profile filing-agent --model ollama/qwen3:14b --max-risk low --skill intake-review --as rana
practicehub member approve-profile filing-agent 2 --as dr-amara
```

The hub holds you to these while you do it:

- The sponsor is someone other than whoever adds the agent, and must themselves reach whatever the agent's roles
  reach.
- A role that reads or writes restricted data waits for a second policy owner (`practicehub member approve-roles`).
- Taking a role, a skill or risk away applies at once.

**Design the data classes first.** Permissions are per data class, never per record type, so the classes decide what
each agent can ever see. Give an agent the narrowest roles that let it do its job.

### Teach an agent something: a skill

A skill is instructions and the shape of the answer. Create one on the **Skills** page, or as a file in a pack or
module:

```toml
# skills/return-prep.toml
id = "return-prep"
title = "Prepare a personal return"
instructions = "Total the client's slips into a draft return. Do not file anything."

[[output]]
name = "income"
type = "number"
required = true
```

The answer is checked against the output fields exactly like a form a person fills in. A task in a workflow asks for
the skill with `skill = "return-prep"`, and any member with the role and the skill may take it: a person or an agent.

For checks a schema cannot express, a pack's `pack.py` adds a **guard** for the skill: a Python function that
returns the problems with an answer, whoever gave it. In the clinic pack, a guard refuses a claim whose amount does
not match the fee schedule.

### Mentioning an agent

Write `@` and an agent's name in a comment on any record, or in a page's text, and the agent answers in that thread
with its profile's model. It reads only what its grants let it: the record, redacted for it, the thread and the
words quoted. When it cannot answer (its model fails, or it escalates), it says so in the thread, so a person sees
it. Only a person brings an agent into a thread: an agent's own `@` names wake only people, so two agents never
answer each other in a loop. Nobody can mention an agent on a record it may not read, or one a subject's directive
keeps from it.

### Talking to an agent

Every page a person works in has a chat button bottom right (Alt+K). It opens a conversation with any active agent
whose sponsor is active. The conversation is about no one record: ask "what is still missing for this return?" and
the agent finds, searches and reads the records it needs, and can make, change or move one, or start a workflow, all
as itself.

What it may do is what **both** of you may do. It is offered a type, a move or a workflow only when both of you have
it, it reaches a record only when neither of you is kept from it, it writes only fields both of you may write, and
what comes back is redacted for both. Its own limits still hold: an agent without `direct_writes` makes and changes
nothing directly (it may still start a workflow, whose steps go under the usual risk tiers and reviews), and no chat
tool signs or approves.

Each call it makes shows in the conversation as one line ("Searched clients for Kevin", "Read CLI-3"), with what came
back under it, and the panel lists every record the conversation used. The track has each call, caused by your
message, but never what either of you said. A conversation is yours: nobody else lists or reads it, and nothing in it
changes the agent. To change how an agent behaves, change its profile or skills, which are reviewed and approved.

## 4. Records, workflows and views

These are the three definitions you will change most. Each can be edited in the browser or shipped as a file.

### A record type

```toml
# types/Visitor.toml
id = "Visitor"
label = "Visitor"
plural = "Visitors"
data_class = "visitors"              # who may read and write it
title = "{name}"

[[field]]
name = "name"
label = "Name"
required = true

[[field]]
name = "host"
label = "Here to see"
type = "member"

[[field]]
name = "status"
label = "Status"
type = "choice"
choices = ["expected", "in", "out"]

[lifecycle]
field = "status"
initial = "expected"
locked = ["out"]                     # in a locked state, people change nothing but the state

[[lifecycle.transition]]
from = ["expected"]
to = "in"
label = "Sign in"

[[lifecycle.transition]]
from = ["in"]
to = "out"
label = "Sign out"
```

Field types are `string`, `text`, `markdown`, `number`, `boolean`, `date`, `datetime`, `choice`, `member`,
`reference`, `attachment`, `list`, `object` and `json`. A type can extend one of the core's base types
(`extends = "Subject"` for whoever the practice serves, `extends = "Booking"` for something on the calendar), so the
core's pages and every module work with it.

### A kind of another type

`extends` can also name a type of the hub: a module's, the pack's or your own. The type is then a kind of that one:

```toml
# types/Farmer.toml, in an overlay on the accounting office
id = "Farmer"
extends = "Individual"            # itself a kind of Client
label = "Farmer"

[[field]]
name = "farm_name"
label = "Farm"

[[field]]
name = "sin"                      # Individual's field, narrowed: only what changes
required = true
```

- It has everything of the types above it, and only adds fields, checks and links, or narrows a field (required,
  fewer choices, tighter bounds). Its data class, lifecycle, `[base]` and numbering are theirs.
- Wherever a type is named, its kinds count: a reference to an `Individual` takes a farmer, the list of individuals
  shows farmers (with a Type column), a workflow about individuals runs on a farmer, a grant on individuals covers
  farmers, and a view of individuals counts them.
- A kind with no `key` of its own shares the type's key and counter: farmers and other clients are CLI-1, CLI-2...
- `abstract = true` says only the kinds under a type have records: "New client" then asks which kind.
- In the builder (Record types, then New or Propose a change) the same is **A kind of** and **Only its kinds have
  records**.

### A workflow

A workflow is about one record. It is written as that record's **states** (where it stops, and the work done there)
and the **edges** between them (the ways on, and the automations that run on the way):

```toml
# workflows/personal-return.toml
id = "personal-return"
title = "Personal tax return"
record = "Client"

[trigger]
starters = ["partner", "reception"]

[start]
to = "preparing"

[[state]]
id = "preparing"
label = "Preparing"
[[state.work]]
id = "prepare"
title = "Prepare the return"
kind = "task"
role = "preparer"                    # an agent or a person holding the role
skill = "return-prep"
risk = "medium"
context = ["record:tax", "input"]    # what the work reads: nothing else reaches the agent

[[edge]]
id = "prepared"
from = "preparing"
to = "signing"

[[state]]
id = "signing"
label = "To sign"
[[state.work]]
id = "sign"
title = "Sign and file"
kind = "gate"
role = "partner"
signer = "person"                    # never an agent
meaning = "I have reviewed this return and it is complete and accurate"

[[edge]]                             # a decision chooses between edges, so a gate's state has at least two
id = "file"
label = "File"
from = "signing"
to = "filed"

[[edge]]
id = "return-to-preparer"
label = "Return to the preparer"
from = "signing"
to = "returned"

[[state]]
id = "filed"
tone = "done"

[[state]]
id = "returned"
tone = "ended"
```

The levers that decide how much an agent does are on the work itself: `role`, `skill`, `risk`, `signer`,
`persons_only`, `review_of` (a second pair of eyes on earlier work) and `not_by` (separation of duties).
[Writing workflows](workflows.md) covers these, plus waits, schedules, deadlines, conditions and blocks.

### A view

```toml
# views/visitors-board.toml
id = "visitors-board"
title = "Visitors"
roles = ["visitors-desk"]

[[widget]]
kind = "board"
title = "Visitors by status"
type = "Visitor"
group_by = "status"
columns = ["name", "host"]
```

Widget kinds are `count`, `table`, `list`, `board`, `chart`, `tree` and `links`.

## 5. A capability any practice can install: a module

A module adds record types, views, workflows, skills and roles to whatever practice installs it. Document control,
quality, inventory, the wiki and the CRM in `modules/` are all modules. Write one when the capability is not
specific to one kind of business.

```
modules/visitors/
  module.toml        what it is, its roles, data classes and grants
  types/*.toml       record types
  views/*.toml       views
  workflows/*.toml   workflows
  skills/*.toml      skills
  blocks/*.toml      blocks
  pack.py            optional Python: actions, guards, stand-in answers
```

```toml
# modules/visitors/module.toml
[module]
requires_core = "1.0"
id = "visitors"
name = "Visitor log"
version = "1"
summary = "Who came to the office, and whom they came to see."
requires = []                        # modules this one builds on
shortcuts = ["Visitor"]              # listed in the sidebar

[[roles]]
id = "visitors-desk"
name = "Visitor desk"
description = "Signs visitors in and out."

[[data_classes]]
id = "visitors"
name = "Visitor log"
description = "Names of visitors and whom they came to see."

[grants.visitors-desk]
read = ["visitors"]
write = ["visitors"]
```

With the `Visitor` type and the board from section 4 beside it, that is a whole module. A module's workflow, block
and view ids start with its own id (`visitors-board`).

Install it the way any module is installed. One policy owner reviews what is on disk, and another installs it:

```sh
practicehub module plan visitors                   # what installing it would add
practicehub module review --as rana visitors
practicehub module install --as dr-amara visitors
```

A running hub picks it up on its next request. To have a practice start with it, name it in the pack or overlay:
`modules = ["visitors"]`.

Modules are looked up in `modules/`, or in the directory `PRACTICEHUB_MODULES` names.

A module can also add to a workflow another module owns, with `extends/workflows/<id>.toml`: new states and edges,
more work on a state, more automations on an edge. It can never change or remove what is there. See "A module adding
to another module's workflow" in [Writing workflows](workflows.md).

## 6. Another kind of business: a pack

Write a pack when no shipped pack fits: a law firm, a dental office, a repair shop. A pack is the same layout as a
module, with `pack.toml` in place of `module.toml`. It also names the **subject**, the record type for whoever or
whatever the work is about: a patient, a client, a matter, a job.

Two packs to read first:

- `tests/packs/accounting` is the smallest one that works: about 300 lines, with one workflow, one agent and one
  skill. Copy it to start.
- `packs/accounting-office` is a full one.

```sh
practicehub init --pack tests/packs/accounting     # a pack by its path
```

[Packs](packs.md) is the reference for `pack.toml` and for the optional `pack.py`, which is where a practice's
Python lives:

| In `pack.py` | What it is for |
|---|---|
| `ACTIONS` | deterministic automations a workflow calls by name |
| `PROVIDERS` | computed context for a piece of work |
| `GUARDS` | hard rules on an answer, from a person or an agent |
| `STANDINS` | fixed answers, so demos and tests need no model |
| `summarize`, `facts`, `register`, `demo` | labels, headline facts, registration and demo records |

Keep `pack.py` small. Most of a practice is TOML, which a manager can read and change under change control. Code
runs only as reviewed: while a changed `pack.py` waits for its review, the steps it guards take no answer.

One test protects the split: `tests/test_other_practice.py` fails if the core starts assuming patients. If your pack
needs something the core cannot do, add it to the core as something any practice could use.

## 7. Connect it to other systems

| To | Use |
|---|---|
| Start work from another system | `POST /api/events` or `POST /api/runs`, as a `service` member with a token; a workflow's `[trigger] event` listens |
| Run a step on an automation engine | an **engine block**, on Kestra (`KESTRA_URL`) or any JSON webhook (`PRACTICEHUB_ENGINE_WEBHOOK_URL`); the hub keeps the gates and the track |
| Try an engine block on one machine | `scripts/dev-engine.py`, a small webhook engine that answers with a local Ollama model |
| Deliver messages (email, fax, a pager) | a connector: a `service` member granted a channel, working the outbox through `/api/outbox` |
| Let an agent or a tool use the hub | MCP: every module and the practice's own definitions are MCP servers at `/mcp/<server>`, generated from the definitions in force |
| Run an agent as its own process | `practicehub agents run --agent <id> --url <hub> --token-file <file>`, with a token its sponsor issued |
| Let the public book online | the booking portal: `[portal]` in the pack, and [The booking portal](portal.md) |

The README's "The stack" and "MCP" sections cover each of these.

## 8. Change what is already installed

Once a pack or module is installed, the practice owns its definitions. The files on disk are first versions, and the
practice's own later versions win. To ship a newer pack or module to a hub that is running:

```sh
practicehub pack status                            # is the pack on disk the one in force?
practicehub pack review --as rana                  # one policy owner signs that they reviewed it
practicehub pack upgrade --as dr-amara             # another puts it in force

practicehub module plan dms                        # how the module on disk differs from what the practice runs
practicehub module review --as rana dms
practicehub module upgrade --as dr-amara dms
```

New definitions arrive, and changed ones become proposals for a policy owner to approve. Raise `version` in
`pack.toml` or `module.toml` when you change one, and say what changed.

## A checklist for a new practice

1. Run the demo and find the shipped pack closest to your work.
2. Write an overlay on it with your name, time zone, people and modules.
3. Design the data classes, then give each role the narrowest grants that work.
4. Change the workflows in the browser until they match how you work. Set `risk` and `signer` on each step.
5. Add agents one at a time: a sponsor, a role, a skill and a low risk limit. Try each one's work with the
   `standin` model and the pack's stand-in answers first.
6. Let it answer with a model (a new agent's default is a local one through Ollama) once its work reads right with
   stand-ins.
7. Run `practicehub verify`, and keep a copy of the track's head off the machine.
