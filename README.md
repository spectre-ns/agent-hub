# Practice Hub

A platform for running an organization's work, where **AI agents are members of the team**. Agents hold roles, take
work from the same inbox as people, have an accountable sponsor and a kill switch, and leave the same signed audit
trail. The organization (the hub calls it a **practice**) writes down how it works as **workflows** and changes them
through change control.

The core knows nothing about any one line of work. A **pack** makes a hub a particular kind of practice, and
**modules** add capabilities to any of them. The packs in `packs/` are for unrelated kinds of business and run on the
same core, and `tests/packs/accounting` is a small one to copy.

New here? Start with **[Getting started](docs/getting-started.md)**, then **[Customizing](docs/customizing.md)**.

How it is built:

| Idea | Here |
|---|---|
| Procedures are configuration | **Workflows**: TOML a manager can read; a pack ships the first versions |
| Agents hold roles like people | **Members**: `person`, `agent` or `service`; workflows name roles, never people |
| Checks are deterministic | **Eligibility** (one function, `Engine.eligibility`), field validation, pack guards. No model in any guard |
| Decisions are e-signatures | **Gate steps**: every decision is signed with the step's meaning ("I approve this request") |
| The audit trail is hash-chained | **The track**: a hash-chained, HMAC-signed event log; every event names the event that caused it |

## Getting started

You need Python 3.12 or later, and a MongoDB replica set to keep the data in. One node will do, and Docker runs one:

```sh
git clone https://github.com/spectre-ns/agent-hub.git && cd agent-hub
python3 -m venv .venv && . .venv/bin/activate
pip install -e .                                # the hub is the standard library, pymongo and pypdf

docker run -d --name practicehub-mongo -p 127.0.0.1:27017:27017 mongo:7.0 --replSet rs0 --bind_ip_all
docker exec practicehub-mongo mongosh --quiet --eval \
  'rs.initiate({_id: "rs0", members: [{_id: 0, host: "localhost:27017"}]})'

practicehub init --demo          # a demo practice, in ./state, with demo records and open work
practicehub agents run --once    # the agents work their inboxes, on a local model through Ollama
practicehub serve --dev --agents # http://127.0.0.1:8200/  (dev: sign in as anyone, local only;
                                 # --agents: agents take new work as soon as it appears)
practicehub verify               # check the track's hash chain and signatures
```

`PRACTICEHUB_DATABASE` is the MongoDB URI (by default
`mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true`), and `deploy/docker-compose.yml` runs the hub and
its MongoDB together. **[docs/getting-started.md](docs/getting-started.md)** walks through each step, the other demo
practices, giving an agent a real model, and running it for real.

The sign-in page lists the demo's members. Sign in as **Rana** or **Dr. Amara Okafor**, its two policy owners, to
change settings and to approve each other's changes; as Dr. Amara to edit and sign what an agent drafted; or as
**Jo** to see what a member with narrower access sees.

Run an agent as a separate process, with its own token, through the API:

```sh
practicehub token <agent> --as <its sponsor> > agent.token      # the sponsor issues it
practicehub agents run --agent <agent> --url http://127.0.0.1:8200 --token-file agent.token
```

Outside dev mode, people sign in with their member id and a passphrase of their own. A member admin issues a
one-time setup code (Team page, or `practicehub member passphrase-link <member> --as <member admin>`), and the
person sets their passphrase at `/setup`. Someone already signed in sets or changes theirs under **Your
passphrase**. The first member admin issues their own code from the command line: `--as` is themselves.

A delivery connector, such as an email or SMS gateway, is a `service` member with a token. A member admin grants it
channels with `practicehub member channel <service> <channel> --as <member admin>`. It lists queued messages with
`GET /api/outbox`, claims one with `POST /api/outbox/<id>/claim`, and reports it with `.../sent` or with
`.../failed {"reason": ...}`. Run `practicehub tick`, or `serve`, which ticks on its own. It escalates a message
still undelivered past its channel's deadline (`[practice.outbox.<channel>]` in pack.toml).

Tests: `pip install -e '.[dev]' && scripts/test-mongo.sh && python -m pytest -q` (the script starts the tests' MongoDB
in Docker).

## Customizing

The core knows nothing about any one business. What makes a hub your practice and not another
is a set of definitions in TOML: record types, workflows, views, skills, roles and grants. There are four ways to
change them, from least effort to most:

| You want to | Do this |
|---|---|
| Change how a running practice works | Use the pages under **Settings**: workflows, record types, views, blocks, skills, the team and its agents. Nothing to write |
| Run your own practice on a pack that ships | Write an **overlay**: a `pack.toml` that names its `base`, your practice's name and people, and the modules you run |
| Add a capability any practice can install | Write a **module** in `modules/<id>/`: types, views, workflows and roles |
| Set up a different kind of business | Write a **pack** in `packs/<kind>/`: the subject, roles, data classes, workflows, skills and a small `pack.py` |

An overlay is a few lines:

```toml
# packs/northside/pack.toml
base = "general-practice"            # the pack it builds on: any pack in packs/
modules = ["wiki", "crm"]

[practice]
id = "northside"
name = "Northside"
timezone = "America/Toronto"
```

```sh
practicehub init --pack northside --demo
```

Whichever way you choose, a change is a proposal that a second policy owner approves, and the rules below are not
configurable: a pack decides what is high risk and who signs, never whether those rules apply.

**[docs/customizing.md](docs/customizing.md)** is the guide: each level with a worked example, how to set up agents
(roles, skills, risk limit, model, sponsor), connecting other systems, and upgrading what is installed.

## The rules

These rules are enforced in code and each one has a test (`tests/test_rules.py`).

- **Every agent has a sponsor**, an active person who is accountable for it. If the sponsor is switched off, their
  agents are switched off too.
- **Kill switch.** The sponsor or a member admin can switch an agent off. Its tokens stop working at once and its
  runner stops. Only a member admin can switch it back on.
- **No puppets.** An agent's or service's token is issued only by its sponsor. Giving an agent or service a role that
  reads or writes restricted data (every data class, unless the pack marks it `restricted = false`), or raising an
  agent's `max_risk`, waits for a second policy owner, who is neither the one who asked nor the member. Lowering
  `max_risk`, taking skills away (nothing added) or taking a role away applies at once, as a version on the track.
- **An agent changes records through its work.** An agent's skills, its `max_risk`, its sponsor's hold and who else
  must check its work are all checked on the work it is given, and the workflow writes what it answers. Its roles'
  write grants alone do not let it create, change, move or attach to records by the API or an MCP tool: that takes
  `direct_writes` in its profile, off unless a second policy owner approved it (taking it away applies at once).
- **Two policy owners.** Changes are approved by a second policy owner, so the hub refuses whatever leaves fewer than
  two active ones: switching one off, or taking away their role. If the practice is already down to one (someone
  left in a hurry), whoever holds the server and its secret adds one: `practicehub owners recover --grant <member>`.
  The track records that the server holder did it.
- **Risk tiers.** A step's risk must be within the agent's `max_risk`.
  - Low-risk work goes ahead.
  - Medium-risk work goes ahead, and the sponsor gets a notice.
  - High-risk work is *held*: nothing happens until the sponsor approves the agent's answer. If they reject it, the
    item goes to people with the sponsor's reason.
- **People sign; agents never do.** A gate with `signer = "person"` is never decided by an agent, even an agent
  holding the role the gate names. A gate with `signer = "any"` may be decided by an agent only while its
  `agent_criteria` holds. The engine (not the caller) makes an agent's decision one of two things, and neither is a
  signature, whichever way the agent calls (the API, the agent runner or MCP):
  - a *proposal*, when the step is high risk or the agent calls over MCP: it waits for the sponsor, whose approval is
    their own signature on that decision (and on the record it is about);
  - an *attestation*, for a routine decision the workflow allows (a low-risk item within the criteria): the agent's
    own action on the track (`gate.decided` with `data.attested`, no signature), shown as "decided by <agent> (an
    agent)". It never appears among a record's signatures.
- **A person signs with their passphrase.** Each signature, on a decision, an approval or a record, asks again for
  the signer's passphrase. A passphrase is stored only as a salted scrypt hash and is never in the track. After five
  failed attempts in 15 minutes (signing in, signing, or a setup code) from one address, that address is locked out
  for 15 minutes; the member is locked only after 20 failures on them from several places (or five when signing,
  where there is no address), and is then sent a notice. Each failure is on the track. Tokens are for agents, services and API calls. A member admin issues setup codes,
  but never one for themselves.
- **The outbox is delivered, or someone is told.** A connector delivers only on the channels it has been granted,
  and claims only messages whose every data class it reads. Each claim, send, failure and hand delivery is on the
  track. A message about a file that a subject's directive keeps from someone is kept from them too, on the Outbox
  page and everywhere else it is listed: neither its words nor its subject line are shown. Whoever it is addressed
  to by name reads it, and the connector granted its channel still carries it. A message still queued past its channel's deadline goes to the escalation role. On an urgent channel (a
  page), and after any failure, a person is also given the work of delivering it. A person who reads
  the message marks it delivered by hand, saying how.
- **Separation of duties.** `not_by` stops whoever did an earlier step from also doing this one. An agent that
  prepares something is not the one who submits it.
- **Data minimisation.** Records belong to data classes, and roles are granted read or write access per class. An
  agent whose role reads two classes reads those and nothing else, and its work-item context holds no data of any
  other class.
- **Workflows carry the data they read.**
  - Data copied into a run keeps its data class, so a member without access to that class sees the run's audit
    trail but not its content.
  - A step that takes an earlier step's whole output needs access to what that step read.
- **Audit causality.**
  - Every event names the event that caused it, so `/track/<seq>` shows the chain back to its root: a message
    from another system, a portal request, a schedule.
  - Decisions are signed, and the chain is tamper-evident.
- **Change control.** Anyone with standing may propose a workflow change, agents included. A *different* person who
  is a policy owner approves it, never an agent. Before approving, the approver sees the diff and what agents may now
  do.
  - A definition runs only as the track sealed it: its text must hash to what a sealed approval recorded, and the
    version marked active must be the one the track put in force. An approval written into the database without the
    hub's secret is ignored.
  - Retiring a workflow, block, skill or record type is a proposal too: a second policy owner approves it.
  - An event that no workflow takes is not dropped: the track records it (`event.unhandled`) and the escalation role
    gets an urgent notice. The same holds when the workflow that listens refuses to start on what the event carried
    (a payload its start form does not accept): the sender gets the refusal and the escalation role is told.
- **Verifying the track.** `practicehub verify` checks every event. The Track page (policy owners only, like
  `GET /api/track/verify`) checks the events since the last verify and builds on that kept, sealed result; the
  rows are read in chunks so a long track does not hold up the hub.
- **Model output is data.**
  - An agent's answer is validated against the step's fields and the pack's guards, exactly like a person's form.
  - A guard lives in its pack's or module's code, which runs only as reviewed. While that code waits for review
    (it changed on disk, or the module was left out), the steps it guards take no answer: a guard fails closed, as
    an action does.
  - Nothing a model returns is executed, and conditions use a small safe expression language, not `eval`.
  - An unsure agent escalates to people instead of guessing.
- **Blocks are approved code, prompts and engine calls.**
  - Only a person may write a Python or engine block; agents may propose prompt blocks.
  - A different policy owner approves and signs every block version, and the code's SHA-256 is in the track.
  - Python runs in a locked-down child process; a run keeps the block versions it started with.
  - Run data filled into a prompt stays data in the agent's context, never part of the instructions.
- **Data residency.**
  - Models run on the premises: Ollama, or vLLM behind LiteLLM. An agent added without a model is given a local
    Ollama model, written into its profile; any other model is a reviewed profile change.
  - External model APIs are refused unless the practice sets `PRACTICEHUB_ALLOW_EXTERNAL_MODELS=1`.
  - A pack says what its agents must never do (`agent_rules` in `[practice]`), and every agent of the practice is
    told so in its system prompt.

## The stack

The hub keeps everything in **MongoDB**: records, the track, runs, work, definitions, members, and files in GridFS. It
runs as a replica set, even of one node, because the hub writes in transactions (`PRACTICEHUB_DATABASE`, a MongoDB
URI; `scripts/test-mongo.sh` starts one in docker for the tests). The MongoDB server is under the SSPL, the one
exception to the permissive-licence rule, chosen for its transactions.

A record type's `attachment` field holds a file: it is added from the record's page (or `POST /api/files/<Type>`), kept
in GridFS by its SHA-256 (25 MB at most), and read only through a record that names it, by someone who may read that
record. Each read is a view of the record on the track, and the file is served sandboxed, with no scripts. A record
names a file only for a writer who could already get at it: they added those bytes themselves, the record named it
before, or they may read it in a record that names it (a workflow's step carries files as the workflow says). So
knowing a file's hash is never a way to read it, and the hash is shown on the track only to those who read what
the file was added for.

Each part of the target stack is an adapter you switch on with
environment variables. The Medplum, OpenFGA and Keycloak adapters are written against those projects' APIs but have
not yet been run against live servers:

| Concern | Built in | In production |
|---|---|---|
| Records | MongoDB | An external record server, **Medplum** (`MEDPLUM_URL`); the hub checks data-class permissions before every call |
| Back office | The practice's own records | **ERPNext/Frappe** through an engine block (a webhook); no flow on the ERPNext side ships yet. The hub stays the system of record for work, not for accounts |
| Permissions | Local tuples, OpenFGA-shaped | **OpenFGA** (`practicehub openfga init`). The model has `agent → sponsor`, roles as usersets, data classes |
| Identity | Passphrases for people (scrypt; sessions and setup codes kept as hashes); API tokens (hashed) for agents, services and API clients | **Keycloak**: `practicehub keycloak realm` exports every member. Each agent is a confidential client with a service account, and tokens are exchanged (RFC 8693) for the hub's audience |
| Orchestration | The run engine; Python blocks in a sandbox | **Engine blocks** run on Kestra (`KESTRA_URL`) or any JSON webhook (`PRACTICEHUB_ENGINE_WEBHOOK_URL`: Activepieces, an in-house service); the Kestra adapter is not yet run against a live server. Engines can also call `POST /api/events` and `POST /api/runs` as a `service` member. Either way the hub keeps the gates, eligibility and the track. Where the hub is served, a block runs as a job outside the request that made it ready (queued, running, done), and the record's History shows it as it happens |
| Agent plumbing | `GET /api/inbox`, `/context`, `/complete` | **agentgateway** in front of the API. A2A Agent Cards per agent are *not built yet* (each agent's profile has what a card needs) |
| Models | Ollama (a new agent's default, `PRACTICEHUB_DEFAULT_MODEL`, else `ollama/qwen3:14b`); stand-in answers for tests and a demo's past | **LiteLLM** (`litellm/<model>`) in front of Ollama or vLLM, traced in **Langfuse** with the agent, run and item |

Everything else is Apache-2.0 or similarly permissive. Windmill and n8n are deliberately not used.

`deploy/docker-compose.yml` runs the hub with its MongoDB and, by profile, Keycloak, OpenFGA and LiteLLM. Copy `deploy/env.example`
to `deploy/.env` first. Ports 8080, 9980 and 11434 are never bound. The track's anchors live on their own volume
(`hub-anchors`), away from the database's, so that replacing the database shows.

The hub's clock is the practice's, not the server's: "now" and "today" (a record's date, a booking's period, the
calendar's today and free slots, `run.today`, due times) are in the practice's time zone (`[practice] timezone`, changeable on
the practice settings page), whatever zone the container runs in. Record and calendar times are stored as the
practice's wall time without an offset; the track and work items keep instants in UTC (`...Z`) and pages show them in
the practice's zone (`practicehub/clock.py`). The image installs `tzdata`; a hub with an unknown zone, or on a machine
without tz data, refuses to open.

### Kubernetes and Nebari

`deploy/helm/practicehub` is a Helm chart: the hub (one replica: one hub process per database), MongoDB as a one-node
replica set on a persistent volume, and the state and the track's anchors on volumes of their own. The secret comes
from an existing Kubernetes Secret. On first start the chart sets the practice up from its pack (`init --if-new`).
On Nebari, add it as a `helm_extensions` entry: `deploy/nebari/practicehub.nebari-config.yaml` has the entry and the
steps before it. Put the cluster in the region the practice's data must stay in.

### Backup and restore

```sh
practicehub backup /backups/2026-11-21          # every collection (one snapshot) and file, and the anchors; never the secret
practicehub restore /backups/2026-11-21 --force # stop the hub first; the current database is backed up beside the state
practicehub track restored --as rana --note "disk failed"
practicehub verify
```

Keep the secret (`PRACTICEHUB_SECRET`, or `state/.secret`) in your secret store, not with the backups. After a
restore, `verify` reports the anchored events the backup does not have. A policy owner, on the server that holds the
secret, records that loss with `track restored`: the track gets a signed `track.restored` event and the anchors a
sealed marker, and from then on verify holds the track to the anchors again. Nothing is dropped silently.

Searches ignore accents and case: "cote" and "helene" find "Hélène Côté". Records are stored as written, with a folded copy for searching. On
Medplum the hub matches every page of results itself.

### Office documents

Practice Hub can open office documents (ODF: .odt, .ods, .odp, .odg; and .docx, .xlsx, .pptx, plus .csv and .txt
where Collabora offers them) in **Collabora Online**, so several people edit one document at the same time. It is
optional and off until you point the hub at a Collabora server; nothing about it is bundled.

- Set `PRACTICEHUB_COLLABORA_URL` (Collabora as browsers reach it), `PRACTICEHUB_WOPI_BASE` (the hub as Collabora
  reaches it) and, if the hub must use another address, `PRACTICEHUB_COLLABORA_DISCOVERY_URL`. Collabora's
  `aliasgroup1` names the WOPI base, so it opens files from this hub only. `docker compose --profile office`
  runs one; for local work, `practicehub serve --wopi-host 172.17.0.1` also listens on the docker bridge, for
  `/wopi/` calls only.
- An office attachment gets **Open** and **Edit together**; a record with a free attachment field gets **New
  document** (a blank text document, spreadsheet or presentation). The editor is a full page with the record's
  title, a way back, and who has it open.
- The hub is the WOPI host: files stay in its database, and Collabora holds a copy only while it is open. Every call
  carries a 10-hour access token bound to the member, record, field and file, and is checked against the hub's own
  rules; WOPI proof keys are verified (RSA-SHA256, standard library) whenever Collabora's discovery publishes them.
- Autosaves are a working copy, not a version. Closing the document, or saving it, makes a new version of the record
  whose history names everyone who edited. A working copy left an hour (Collabora gone without a final save) becomes a
  version as its last editor. Collabora saves with the token of whoever changed the document last, so the names are
  the people whose changes were saved; someone who typed but whose change reached the hub in another person's save
  is not named.
- A released document (a locked lifecycle state) opens read-only; Revise it first. A signed file saved again is a new
  version, which is not signed, and the page says so.
- Licensing: Collabora Online is MPL-2.0. The `collabora/code` image is CODE, the development edition, with a welcome
  banner and limits on connections; for production, use a supported Collabora build or subscription. The practice
  decides.

## Crafting workflows for your practice

Open **Workflows → Create workflow**, or **Edit workflow** on an existing one. The editor draws the workflow as a
diagram:

- add tasks, decisions and automations;
- drop in a **block** (Python, an agent prompt or an engine step) and fill its parameters;
- select a step to set its role, risk, agent skill, signer, fields and what it reads;
- or switch to the TOML tab and edit the text directly.

The hub checks the workflow as you edit it and lists every problem, in words. Saving is a proposal: another policy
owner approves it, after seeing the diff and what agents may now do. Runs that have already started finish on the
version they started with.

Time is the practice's (its time zone), and the clock's tick (`practicehub tick`, or every minute while serving)
acts on it:

- a **wait** step waits until a condition holds (`until`), for a while (`for = "3d"`), or until a moment
  (`at = "{{input.appointment}} - 1d"`: one placeholder giving a date or a time, optionally moved by `+`/`-` a
  duration; a date alone means its midnight). A wait on `until` may give up: `timeout = "2d"` ends it as timed out,
  and later steps branch on it with `when = "chase.timed_out"` (or `not chase.timed_out`);
- a **deadline** for the whole run: `[deadline]` with `within = "5d"` and `escalate_to = "<role>"`. A run still open
  after it is flagged once (`run.deadline_missed` on the track, a notice to that role, else the practice's
  escalation role) and keeps `deadline_at` and `deadline_missed_at`, so late runs can be counted;
- a **schedule** (`trigger.schedule`): `hourly`, `every 15m` / `every 2h` (15 minutes to 24 hours), `daily 07:00`,
  `weekdays 07:00`, `weekly mon 08:00` / `weekly mon,wed,fri 08:00`, `monthly 1 09:00` (day 1 to 28) or
  `monthly last 17:00`. Each fires once per slot.

Each run is **one work item** that walks the workflow: one key and one step history from start to finish. It is
reassigned to each step's role as the run goes on, says what it waits on while an automation runs or a wait waits
(and is then in nobody's to-do), and gets child items for steps side by side or a task done once per person, each
closing back into it. The board can show one workflow with a column per step (**Group by: one workflow, by
step**).

Every record has a **key**, numbered in its type as a Jira issue is in its project: the 12th record of a type keyed
`REQ` is `REQ-12`, and each type counts on its own. The key is given when the record is made and never changes; the
record page shows it beside the title, and the records list in a column of its own. Work on a record shows the
record's key, so the record's work, its reviews and its reopening all read `REQ-12`. Work on no record (a notice
about your account, an approval) shows the practice's code and the item's number, such as `AC-40`.

A key finds what it names: `/REQ-12` opens the record (any case), the search box takes a key as it takes a name,
`GET /api/keys/REQ-12` says what the key names, and MCP's `find_by_key` does the same for agents. A record
converted into another type is still found by its old key. A key names nothing to a member who may not read what
has it: they are told no more than for a key nothing has.

A record type sets its key with `key = "REQ"`: one to eight capital letters and digits, starting with a letter. A
type that sets none takes one from its label (Request: `REQ`, Change order: `CO`). Once its records carry a key, a
type keeps it: a new label does not change it, no other type may take it, even after the type is retired, and no
type's key is ever the practice's code.

The diagram editor's step panel sets a wait's condition, duration, moment and timeout; the workflow panel its
schedule and deadline. The workflow's page says them in words.

To see where a workflow loses time, a policy owner opens **Settings → Process analytics** (`/settings/analytics`;
`/api/analytics?days=30` as JSON; `practicehub analytics --as <policy owner> --days 30` on the command line). For
the last 7, 30 or 90 days it shows, per workflow: runs started, finished, cancelled and still open; how long
finished runs took (median and 90th percentile); per step, the median time waiting to be taken and from taken to
done, how often it was overdue and escalated; the step where most time goes, in a sentence; the work open now by
age; and missed deadlines. It is counts and times only: no records, no names, no inputs, no item ids. It reads at
most 20,000 runs of the period and says so when there were more.

- **BPMN 2.0.** **Download as BPMN** on a workflow's page (or `practicehub workflow export-bpmn <id>`) gives a file
  Camunda Modeler, bpmn.io and other BPMN tools open. It draws the workflow as the steps it runs as: a lane per role,
  user tasks for tasks and decisions, service tasks for automations, catch events for waits, conditions on the flows,
  gateways where branches split or join, due times as timers, and a diagram. The workflow itself (its states and
  edges, and everything else it says) travels in a `ph:` extension, so a file exported and read back gives the same
  workflow. **Import BPMN** on the Workflows page (or `practicehub workflow import-bpmn <file>`, which prints the
  TOML, or proposes it with `--propose --as <member>`) reads such a file back and opens it in the editor. The title,
  the summary and how the workflow starts (a timer start is its schedule, a message start its event) come back as
  the BPMN tool left them. The steps do not: they come from the states and edges, so the import says when tasks were
  added or removed in the diagram, and names what else was drawn there that a workflow has no place for
  (sub-processes, message flows, more pools...). A diagram drawn in another tool carries no states and edges, so it
  is refused rather than guessed at: the refusal lists its tasks by lane, to help you write the workflow by hand
  ("States and edges" in `docs/workflows.md`). An import is never installed: you review it and propose it, and a
  policy owner who did not write it approves it. Files with a DOCTYPE or entities, or over 2 MB, are refused.
- **Blocks** in the sidebar lists the blocks and the engines under the hub; create or change one there.
- `docs/workflows.md` is the reference.
- `docs/packs.md` explains how to set up another kind of practice: its roles, data classes, subject (whoever or
  whatever its work is about), skills and starting workflows.

## Modules

A module adds record types, views, workflows and roles to any practice, without the core knowing what it is for. One
policy owner reviews a module's code and another installs it (`practicehub module review --as rana wiki`, then
`practicehub module install --as <another policy owner> wiki`, or from the Modules page). These ship in `modules/`:

- **dms**, document control: every controlled document (policies, SOPs, forms).
  - **Its text is its file:** an OpenDocument file (.odt, .ods, .odp) is written together in Collabora Online while the
    document is a draft, and opens read-only once it is sent for review (see Office documents). A PDF is kept as filed.
  - **Its status is its workflow's:** each revision is walked from draft to in review to approved by `dms-review`. A
    document controller who did not write it signs the approval, or returns it with the reason. Revising an approved
    document numbers the next revision; making it obsolete is a controller's signed decision.
  - **Periodic review:** a year after approval, `dms-periodic-review` asks its owner to sign that it is still right
    or to ask for a revision, which the document's own workflow opens. Staff sign that they have read it.
- **qms**, quality: corrective and preventive actions that send the documents they change back for review. It needs
  dms.
- **plm**, product lifecycle: products at revisions, what each is made of, and the controlled documents that
  specify it. It needs qms and dms.
  - **Change orders:** a released product changes only through one. The change board signs to approve it, and its
    author never does. The products and documents it names then open at their next revision, and the documents go
    through document control again. A product owner other than the approver signs the release.
  - **CAPAs:** a change order may answer a CAPA, which then lists it on its Linked tab.
- **wiki**: pages of formatted text in a tree. A page with nothing above it is a space's home page.
  - **Links:** a page links another by its title (`[[On-call guide]]`), any record as `[[Type/id]]`, and a piece of
    work as `[[work:<id>]]`. The record shows the page on its Linked tab, so a controlled document or a CAPA lists the
    pages that explain it.
  - **Spaces:** beside each page, a second column lists the spaces and the open space's page tree. Archived pages are
    left out of it, unless one is the page open.
  - **Who:** everyone reads the wiki; the wiki-editor role writes in it.
  - **Checks:** a page anyone flags as out of date waits for a wiki owner, who signs that it is current or archives
    it.
  - **Not controlled:** nothing in the wiki is approved or released. A page that must be becomes a controlled document
    through wiki-sop.
- **wiki-sop**: makes a wiki page a controlled document. A document author starts **Make a controlled document** on
  the page and gives it a number. A PDF of the page as it is then (standard library, base-14 fonts: Latin-1 text) is
  filed in document control as a new draft that links back to the page, and `dms-review` walks it from there. The
  page is not changed. It needs dms and wiki.

- **inventory** (after InvenTree): parts, the lots that hold them (stock items, with lot, serial and expiry), the
  storage locations they are kept in (a tree: site, room, cupboard or fridge), and the ledger of stock moves. It
  installs alone.
  - **The ledger:** a lot changes only when a stock move posts, and nobody writes a lot by hand. The move keeps each
    lot it changed, before and after, so a lot's quantity is always the sum of its posted moves.
  - **Check, sign, post:** submitting a move checks it first. One that is refused (more than is there, expired
    stock, a location that only groups others) goes back to its author with the reason. A signature covers only the
    stock it was checked against: if the lot changed in the meantime, the move goes back too.
  - **Control levels:** a signed inventory policy sets how much is signed. With none signed, the hub runs at basic.
    - **basic**: nothing is signed.
    - **tracked**: adjustments, write-offs, returns, releases and counts beyond tolerance are
      signed by an inventory manager other than the writer, or by the writer when the policy says so.
    - **regulated**: always a second person; parts are approved before use, and the first lot to expire
      is issued first.
    - **Changing it:** a switch may be set stricter than its level, never looser. A change that loosens a control
      the practice signs with a second person is signed by someone other than its author.
  - **Holds:** a hold is free and immediate. Holding part of a lot splits off a child lot with the same lot number,
    and only a release (signed, from tracked up) frees it.
  - **Counts:** a count's tolerance is measured against the lot's last signed baseline, so small counts cannot
    drain a lot unsigned.
  - **Parts:** each code is unique. When the policy asks for part approval, someone with the stock quality role,
    other than the writer, approves each revision.

Each module, and the practice pack, names its **shortcuts** (`shortcuts = ["Part", "StockMove"]` in
`[module]`, or in `[practice]`). These are the record types people work in most, listed in the sidebar and first in
Start work. An entry `"start:<workflow>"` puts that workflow on the top bar as a button.

These core rules came with inventory, and any module can use them:

- **Workflow-only fields:** a field with `by = "workflow"` is changed only by a workflow, never by a person, and forms
  leave it out.
- **Amounts:** `increment` and `decrement` in `record.update` take amounts, such as `revision = 1` or
  `count = "{{record.quantity}}"`.
- **One reference:** `for_each` also takes a single reference.
- **Unique keys** (`unique = ["code", ["part", "serial"]]` on a record type): no two records share a key. A key is one
  field, or several that are unique together, such as a serial within its part.
  - **When checked:** when a record is written with every field of the key filled in, whoever writes it: a person,
    a workflow or an import. A record that leaves one empty, such as an untracked part with no serial, shares
    nothing.
  - **Comparison:** text is compared the way a search compares it, so "GL-M" and " gl-m" are the same code.
  - **Older records:** on a change, the key is checked only if it changed. Two records that already shared a key
    before the type said unique can still be edited.

The wiki rests on three core features that any module can use:

- **Formatted text** (`type = "markdown"`): a safe subset of Markdown. Raw HTML is shown as text, and only http(s),
  mailto and in-hub links are followed.
  - **Editor:** it is edited as formatted text, what you see is what you get, with a toolbar, a page-link picker and
    Markdown shortcuts as you type. A Markdown view is one click away, and each member chooses which opens first.
  - **Paste:** a paste keeps only what Markdown can say.
  - **Without the script:** the field is a plain Markdown text box.
  - **Documents:** the dms module's Documents use it.
- **Trees** (`parent = "<field>"` on a record type): breadcrumbs, the records under one, and the `tree` widget for
  views. A record never sits under itself. With `[navigator]` (`tops`, and lifecycle states to `hide`), a tree type
  gets a second navigation column: the wiki's spaces and the plm module's product lines both use it.
- **Parts and links**: a record either is part of another or links to it.
  - **Part of** (`part_of = "<field>"`): the record exists only inside its owner. An order line is part of its
    order; a meeting's notes are part of the meeting. It is reached and added through its owner, has no list or
    create of its own, and never moves to another owner. The field must be a required reference to one record of another type.
  - **Link** (`rel` on a reference field): both records stand on their own. A meeting links to its client the way it
    links to its host, and the client's page shows its meetings from the other side.
- **Links written in the text**: they become links like a reference field's, including links to a page created after
  the text that named it.
- **Trees of several types:** a parent field may point at more than one type (`target = ["Folder", "Page"]`).
  The types are then one tree, with one navigator, and the page of each says what kind of record its children are.

### Writing together

A markdown field with `live = true` is edited by several people at once, on the page itself.

- **Who:** whoever may write the record edits it. The check runs on every change, so a grant taken away stops the next
  keystroke. Whoever may only read it sees "Jo is editing" and the text as it is typed. A locked state refuses edits.
- **Saving:** the record changes only at a checkpoint: when the last editor leaves, after two quiet minutes, or on
  Done. Each checkpoint is one version whose comment names its editors ("Edited by Jo and Noor"). All of them count
  as its authors, so a review that is "not by the record's editors" excludes each of them.
- **Changes made elsewhere:** a change saved some other way while people are editing is merged in, not lost. This
  covers a form, an import or a workflow.
- **Comments:** anyone who may read the page may comment on it. There is no separate grant, because a comment changes
  nothing the record says. A comment is made on words selected on the page, or on the whole page. It stays with its
  words while they are there; once they are gone, it shows as "on text that was removed". A thread is resolved by the
  person who started it, or by someone who may change the page. An `@id` in a comment records a mention. Every other
  record's page has comments too, below what it says: a plain record's, a part's under its owner, and a subject's own
  page. A comment there is on the record as a whole, and the form comes back to the page it was made on.
- **History:** any two versions can be compared word by word. "Restore" makes an old version's text the page's text
  again, as a new version.
- **Editor:**
  - A `/` menu offers headings, lists, checklists, tables, code, callouts and links to pages.
  - Markdown shortcuts work as you type: `[] ` starts a checklist item, a code fence closes itself, and Enter continues
    a list.
  - A paste keeps only what Markdown can say.
- **Markdown:** the text may have checklists (`- [ ] to do`, `- [x] done`) and callouts (`> [!note]`, `tip`,
  `warning`, `important`).

The merging is operational transformation (`practicehub/ot.py`, mirrored in the page script) around one ordered log per
field. The server keeps the order and transforms each change past those made since its sender last heard.

### Mentions: people and agents

Write `@` in a page or a comment and pick a member: people first, then agents (marked *agent*). The mention is kept as
that member's reference with the record, the passage and the thread (`practicehub/mentions.py`; it knows nothing of
wikis, so any record with text can use it). The member gets a notice in their inbox; an agent finds it in
`/api/inbox`, reads what it was asked with `GET /api/items/<item>/context` (the record, the passage and the thread,
asked again as that agent: only what its grants let it read), and answers with its own token:
`POST /api/mentions/<id>/reply {"body"}`, or `POST /api/mentions/<id>/suggest {"field", "find", "text", "body"}` to
propose a change. Its words show in the thread marked as an agent's; a suggestion changes nothing until a person who
may change the record accepts it (saved as their change, "Accepted a suggestion from …") or rejects it. An agent that
may not read the record is not offered in the list as one to pick, and a comment naming it is refused with why. Give an
agent the wiki's `wiki-reader` role to let it answer there. The track has `mention.created`, `mention.refused`,
`mention.answered` and `suggestion.proposed`/`accepted`/`rejected`. An agent never releases a SOP (the release is a
person's signature) and never signs.

The hub's own agents (`serve --agents`, `practicehub agent`) answer a mention the same way: from what they were given
there, with the profile's model, in the thread (`AgentRunner.answer_mention`). An agent that cannot answer (its model
escalates, fails, or its reply is refused twice) says so in the thread, and why, so a person takes it up there. Only a
person brings an agent into a thread: an `@` an agent writes tells the people it names but never another agent, so two
agents never answer each other forever. A member whom the subject's directive keeps the file from is not mentioned on
it, as one whose grants do not reach it is not; a directive made after the mention is respected when it is read.

### Talking to an agent

A chat is a conversation between one person and one agent, about no one record (`practicehub/chat.py`). A person
may talk with any active agent whose sponsor is active; each turn and each call asks again. The agent answers with
its profile's model and a small fixed set of tools: find a record by its key, search a type, read a record, describe
a type, make, change or move a record, and start a workflow. Each is the agent's own MCP tool, run as the agent: its
grants, its profile (`direct_writes`), the lifecycle's `by` and the moves only a person makes apply as they do over
MCP, and a workflow it starts gives its steps to whoever they name, under the usual risk tiers and reviews. No tool
signs or approves.

In a chat the agent reaches only what both it and the person may (`Chat.may_reach`): a type, move or workflow is
offered only when both have it, a record a subject's directive keeps from either is refused, a write sets only fields
both may write, and what comes back is redacted for both (a search hit only the agent's reading finds is dropped).
A chat is its person's: nobody else lists or reads it. It keeps the records it used (listed, read, created, updated,
moved or started), and the track has `chat.started`, `chat.asked`, each tool as `mcp.called` with how `chat` (caused
by the question it answers) and `chat.answered` or `chat.failed`; chat events carry ids, never what was said.
Nothing carries over from one conversation to another, and nothing said in one changes the agent. The model's tool
calls are data: an unknown tool or arguments that are not its own go back to the model as an error, and one answer
makes at most eight calls.

### Released documents

A lifecycle with `in_use = "<state>"` keeps a record's approved revision in use while a new one is written.

- **Readers:** someone who may only read the record reads the version in use, under a banner saying a new revision is
  being written.
- **Writers:** someone who may write it switches between the version in use and the draft.
- **Read and sign:** a `[[signature]]` with `readers = true` asks every reader to read and sign the version they read.
  Its page says whether you signed it, and tells writers who has.
- **Workflow dates:** a workflow template can count days, months and years (`{{run.today + 1y}}`,
  `{{record.effective + 30d}}`). A scheduled workflow's `where` can use `run.today`.
- **Controlled documents:** the dms module's Documents keep their approved revision in use while the next is written.

## MCP

Every installed module, the practice pack's own definitions, and each member's work are Model Context Protocol
servers (JSON-RPC 2.0, revision 2025-06-18 and later, negotiated at `initialize`), over Streamable HTTP at
`POST /mcp/<server>`:

| server     | tools |
|------------|-------|
| `core`     | `list_inbox`, `read_item`, `claim_item`, `complete_item`, `release_item`, `find_by_key`, and for agents `propose_decision` |
| `practice` | the pack's record types, workflows and views |
| `<module>` | each installed module's (`/mcp/dms`, `/mcp/qms`, ...) |

Tools are never written by hand. They are generated on every call from the approved, versioned definitions in force:
`search_<type>`, `read_<type>`, `create_<type>`, `update_<type>`, `move_<type>` (an enum of the lifecycle's moves,
with the fields each needs), `start_<workflow>` and `view_<view>`. Their input schemas come from the TOML fields
(required, choices as enums, references as `Type/id`). The list is filtered to what the calling member may do. A
definition change, proposed and then approved by a second person, is the only thing that changes it. Clients learn of
the change through `notifications/tools/list_changed`. `resources/list` gives the record types as JSON Schema and the
controlled documents in force that the member may read.

Authority stays in the hub. A call uses the same methods as the pages and the API: data classes, grants, lifecycle
`by`, required fields and eligibility. It is recorded on the track as `mcp.called` (how `mcp`, the server and the
tool), and it is the cause of whatever it changed. A refusal is a tool result with `isError` and the hub's reason.
Nothing signs: there is no signature tool, and an agent's `propose_decision` waits for its sponsor to approve and sign
it (the engine also refuses a signature from any agent, on any path). Moves that put a record into force are not offered to agents and are refused if called. Arguments are data,
checked against the schema and never evaluated.

The `Origin` header is checked: another site gets a 403 (allow more with `PRACTICEHUB_MCP_ORIGINS`). A member's token
is required as `Authorization: Bearer ph_...`, and cookies are not accepted (401). A server that is switched off is a
404. **Settings → Connections (MCP)** lists each server's URL and tool count, shows how to connect, and lets a policy
owner switch servers on and off, which is tracked. For clients that start a command:

    PRACTICEHUB_TOKEN=ph_... practicehub mcp dms --url https://hub.example.ca

## Layout

```
practicehub/          the core: hub, engine, workflows, blocks, sandbox, backends, track, authz, records, models, web, cli
  agents/runner.py    an agent's loop: inbox → context → model → answer (or escalate)
  integrations/       Keycloak; Collabora (discovery, proof keys, blank documents) and its WOPI host
packs/                practice packs (general-practice, accounting-office): roles, data classes, grants, members,
                      workflows, skills, blocks, stand-in answers; and an overlay on one of them (harbourview)
modules/              dms (document control), qms (quality), plm, wiki, wiki-sop, inventory, crm
tests/                rules, web (real HTTP), a practice of another kind
deploy/               docker compose, Helm chart, LiteLLM config, env example
docs/                 getting started, customizing, workflows, packs, the booking portal
```
