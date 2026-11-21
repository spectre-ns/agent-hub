# Practice Hub

A practice-management platform for professional practices (a family clinic, a law firm, an accountancy) where **AI
agents are members of the practice**. Agents hold roles, take work from the same inbox as people, have an accountable
sponsor and a kill switch, and leave the same signed audit trail. The practice writes down how it works as
**workflows** and changes them through change control.

The first practice pack is a **general practice** (family medicine, Nova Scotia). The core knows nothing about
medicine. `tests/packs/accounting` is a small accounting practice that runs on the same core with no medical code.

It uses the ideas of hub-erp:

| hub-erp | here |
|---|---|
| Frames: SOPs as config | **Workflows**: TOML a practice manager can read; a pack ships the first versions |
| Cogs: agents hold roles like people | **Members**: `person`, `agent` or `service`; workflows name roles, never people |
| Guards: deterministic checks | **Eligibility** (one function, `Engine.eligibility`), field validation, pack guards. No model in any guard |
| Gates: decisions are e-signatures | **Gate steps**: every decision is signed with the step's meaning ("I approve this renewal") |
| Tracks: hash-chained audit | **The track**: a hash-chained, HMAC-signed event log; every event names the event that caused it |

## Quick start

Python 3.12+ and nothing else.

```sh
python3 -m practicehub.cli init --demo          # Harbourview Family Medicine, in ./state, with demo patients
python3 -m practicehub.cli agents run --once    # the agents work their inboxes
python3 -m practicehub.cli serve --dev --agents # http://127.0.0.1:8200/  (dev: sign in as anyone, local only;
                                                # --agents: agents take new work as soon as it appears)
python3 -m practicehub.cli verify               # check the track's hash chain and signatures
```

Sign in as **Dr. Amara Okafor** to edit and sign the scribe agent's draft note, as **Jo** (front desk) to see what a
person without clinical access sees, as **Rana** (practice manager) to approve a workflow change, or as **Sam** to
submit the claim that the billing agent coded.

Run an agent as a separate process, with its own token, through the API:

```sh
python3 -m practicehub.cli token scribe-agent --as dr-amara > scribe.token   # the sponsor issues it
python3 -m practicehub.cli agents run --agent scribe-agent --url http://127.0.0.1:8200 --token-file scribe.token
```

Outside dev mode, people sign in with their member id and a passphrase of their own. A member admin issues a
one-time setup code (Team page, or `practicehub member passphrase-link dr-liam --as rana`), and the person sets
their passphrase at `/setup`. Someone already signed in sets or changes theirs under **Your passphrase**. The first
member admin issues their own code from the command line: `--as` is themselves.

A delivery connector, such as a fax or pager gateway, is a `service` member with a token. A member admin grants it
channels with `practicehub member channel <service> page --as rana`. It lists queued messages with
`GET /api/outbox`, claims one with `POST /api/outbox/<id>/claim`, and reports it with `.../sent` or with
`.../failed {"reason": ...}`. Run `practicehub tick`, or `serve`, which ticks on its own. It escalates a message
still undelivered past its channel's deadline (`[practice.outbox.<channel>]` in pack.toml).

Tests: `python3 -m venv .venv && .venv/bin/pip install pytest && .venv/bin/python -m pytest -q`.

## The rules

These rules are enforced in code and each one has a test (`tests/test_rules.py`).

- **Every agent has a sponsor**, an active person who is accountable for it. If the sponsor is switched off, their
  agents are switched off too.
- **Kill switch.** The sponsor or a member admin can switch an agent off. Its tokens stop working at once and its
  runner stops. Only a member admin can switch it back on.
- **No puppets.** An agent's or service's token is issued only by its sponsor. Giving an agent or service a role that
  reads or writes restricted data (every data class, unless the pack marks it `restricted = false`), or raising an
  agent's `max_risk`, waits for a second policy owner, who is neither the one who asked nor the member. Lowering
  `max_risk` or taking a role away applies at once.
- **Two policy owners.** Changes are approved by a second policy owner, so the hub refuses whatever leaves fewer than
  two active ones: switching one off, or taking away their role. If the practice is already down to one (someone
  left in a hurry), whoever holds the server and its secret adds one: `practicehub owners recover --grant <member>`.
  The track records that the server holder did it.
- **Risk tiers.** A step's risk must be within the agent's `max_risk`.
  - Low-risk work goes ahead.
  - Medium-risk work goes ahead, and the sponsor gets a notice.
  - High-risk work is *held*: nothing happens until the sponsor approves the agent's answer. If they reject it, the
    item goes to people with the sponsor's reason.
- **Clinicians sign; agents never do.** A gate with `signer = "person"` is never decided by an agent, even an agent
  holding the physician role. A gate with `signer = "any"` may be decided by an agent only while its
  `agent_criteria` holds.
- **A person signs with their passphrase.** Each signature, on a decision, an approval or a record, asks again for
  the signer's passphrase. A passphrase is stored only as a salted scrypt hash and is never in the track. After five
  failed attempts in 15 minutes (signing in, signing, or a setup code), that member is locked out for 15 minutes.
  Each failure is on the track. Tokens are for agents, services and API calls. A member admin issues setup codes,
  but never one for themselves.
- **The outbox is delivered, or someone is told.** A connector delivers only on the channels it has been granted,
  and claims only messages whose every data class it reads. Each claim, send, failure and hand delivery is on the
  track. A message still queued past its channel's deadline goes to the escalation role. On an urgent channel (a
  page), and after any failure, a person is also given the delivery work ("Phone the physician"). A person who reads
  the message marks it delivered by hand, saying how.
- **Separation of duties.** `not_by` stops whoever did an earlier step from also doing this one. The billing agent
  codes a visit, so a person submits the claim.
- **Data minimisation.** Records belong to data classes, and roles are granted read or write access per class. The
  scheduling agent reads demographics and appointments, never a clinical note, and its work-item context holds no
  clinical data.
- **Workflows carry the data they read.**
  - A draft note copied into a run is clinical data, so a member without clinical access sees the run's audit trail
    but not its content.
  - A step that takes an earlier step's whole output needs access to what that step read.
- **Audit causality.**
  - Every event names the event that caused it, so `/track/<seq>` shows the chain back to its root: a lab result,
    a portal request, a visit ending.
  - Decisions are signed, and the chain is tamper-evident.
- **Change control.** Anyone with standing may propose a workflow change, agents included. A *different* person who
  is a policy owner approves it, never an agent. Before approving, the approver sees the diff and what agents may now
  do.
  - A definition runs only as the track sealed it: its text must hash to what a sealed approval recorded, and the
    version marked active must be the one the track put in force. An approval written into the database without the
    hub's secret is ignored.
  - Retiring a workflow, block, skill or record type is a proposal too: a second policy owner approves it.
  - An event that no workflow takes is not dropped: the track records it (`event.unhandled`) and the escalation role
    gets an urgent notice.
- **Verifying the track.** `practicehub verify` checks every event. The Track page (policy owners only, like
  `GET /api/track/verify`) checks the events since the last verify and builds on that kept, sealed result; the
  rows are read in chunks so a long track does not hold up the hub.
- **Model output is data.**
  - An agent's answer is validated against the step's fields and the pack's guards, exactly like a person's form.
  - Nothing a model returns is executed, and conditions use a small safe expression language, not `eval`.
  - An unsure agent escalates to people instead of guessing.
- **Blocks are approved code, prompts and engine calls.**
  - Only a person may write a Python or engine block; agents may propose prompt blocks.
  - A different policy owner approves and signs every block version, and the code's SHA-256 is in the track.
  - Python runs in a locked-down child process; a run keeps the block versions it started with.
  - Run data filled into a prompt stays data in the agent's context, never part of the instructions.
- **PHIA and data residency.**
  - Models run on the premises: Ollama, or vLLM behind LiteLLM.
  - External model APIs are refused unless the practice sets `PRACTICEHUB_ALLOW_EXTERNAL_MODELS=1`.
  - Agents in the general-practice pack do administrative work only: drafting, sorting, preparing and coding. They
    never diagnose or prescribe.

## The stack

The hub keeps everything in **MongoDB**: records, the track, runs, work, definitions, members, and files in GridFS. It
runs as a replica set, even of one node, because the hub writes in transactions (`PRACTICEHUB_DATABASE`, a MongoDB
URI; `scripts/test-mongo.sh` starts one in docker for the tests). The MongoDB server is under the SSPL, the one
exception to the permissive-licence rule, chosen for its transactions.

A record type's `attachment` field holds a file: it is added from the record's page (or `POST /api/files/<Type>`), kept
in GridFS by its SHA-256 (25 MB at most), and read only through a record that names it, by someone who may read that
record. Each read is a view of the record on the track, and the file is served sandboxed, with no scripts.

Each part of the target stack is an adapter you switch on with
environment variables. The Medplum, OpenFGA and Keycloak adapters are written against those projects' APIs but have
not yet been run against live servers:

| Concern | Built in | In production |
|---|---|---|
| Records | MongoDB, FHIR R4 shaped | **Medplum** (`MEDPLUM_URL`); the hub checks data-class permissions before every call |
| Back office | ChargeItem and Claim records | **ERPNext/Frappe** through an engine block (`post-claim-to-erp`, a webhook); no flow on the ERPNext side ships yet. The hub stays the system of record for work, not for accounts |
| Permissions | Local tuples, OpenFGA-shaped | **OpenFGA** (`practicehub openfga init`). The model has `agent → sponsor`, roles as usersets, data classes |
| Identity | Passphrases for people (scrypt; sessions and setup codes kept as hashes); API tokens (hashed) for agents, services and API clients | **Keycloak**: `practicehub keycloak realm` exports every member. Each agent is a confidential client with a service account, and tokens are exchanged (RFC 8693) for the hub's audience |
| Orchestration | The run engine; Python blocks in a sandbox | **Engine blocks** run on Kestra (`KESTRA_URL`) or any JSON webhook (`PRACTICEHUB_ENGINE_WEBHOOK_URL`: Activepieces, an in-house service); the Kestra adapter is not yet run against a live server. Engines can also call `POST /api/events` and `POST /api/runs` as a `service` member. Either way the hub keeps the gates, eligibility and the track |
| Agent plumbing | `GET /api/inbox`, `/context`, `/complete` | **agentgateway** in front of the API. A2A Agent Cards per agent are *not built yet* (each agent's profile has what a card needs) |
| Models | Stand-in answers, Ollama | **LiteLLM** (`litellm/<model>`) in front of Ollama or vLLM, traced in **Langfuse** with the agent, run and item |

Everything else is Apache-2.0 or similarly permissive. Windmill and n8n are deliberately not used.

`deploy/docker-compose.yml` runs the hub with its MongoDB and, by profile, Keycloak, OpenFGA and LiteLLM. Copy `deploy/env.example`
to `deploy/.env` first. Ports 8080, 9980 and 11434 are never bound. The track's anchors live on their own volume
(`hub-anchors`), away from the database's, so that replacing the database shows.

### Kubernetes and Nebari

`deploy/helm/practicehub` is a Helm chart: the hub (one replica: one hub process per database), MongoDB as a one-node
replica set on a persistent volume, and the state and the track's anchors on volumes of their own. The secret comes
from an existing Kubernetes Secret. On first start the chart sets the practice up from its pack (`init --if-new`).
On Nebari, add it as a `helm_extensions` entry: `deploy/nebari/practicehub.nebari-config.yaml` has the entry and the
steps before it. Put the cluster in a Canadian region (PHIA).

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

- **Blocks** in the sidebar lists the blocks and the engines under the hub; create or change one there.
- `docs/workflows.md` is the reference.
- `docs/packs.md` explains how to set up another kind of practice: its roles, data classes, subject (Patient, Client,
  Matter...), skills and starting workflows.

## Modules

A module adds record types, views, workflows and roles to any practice, without the core knowing what it is for. One
policy owner reviews a module's code and another installs it (`practicehub module review --as rana wiki`, then
`practicehub module install --as dr-amara wiki`, or from the Modules page). Five ship in `modules/`:

- **dms**, document control: controlled documents, a signed review before one is used, and read-and-understood
  signatures.
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

- **inventory**: stock items, the storage locations that hold them (a tree: site, room, cupboard or fridge), and
  stock moves.
  - **On hand:** only a posted stock move changes it (`by = "workflow"` on the field), so every count has a history.
  - **Receive and issue:** these post at once. An issue for more than is on hand is rejected, never taken below zero.
  - **Adjust and count:** an inventory manager signs these off first, and never the person who wrote the move.
  - **Reorder:** an item that falls to its reorder point is marked low, and a purchaser is asked to order it. It
    shows as on order until a delivery is received.

Each module, and the practice pack, names its **shortcuts** (`shortcuts = ["StockItem", "StockMove"]` in
`[module]`, or in `[practice]`). These are the record types people work in most, listed in the sidebar and first in
Start work. An entry `"start:<workflow>"` puts that workflow on the top bar as a button: the clinic pack's
appointment request is one.

Inventory rests on three core rules:

- **Workflow-only fields:** a field with `by = "workflow"` is changed only by a workflow, never by a person, and forms
  leave it out.
- **Amounts:** `increment` and `decrement` in `record.update` take amounts, such as
  `on_hand = "{{record.quantity}}"`.
- **One reference:** `for_each` also takes a single reference.

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
  - **Part of** (`part_of = "<field>"`): the record exists only inside its owner. An allergy is part of its patient;
    a visit note is part of its visit. It is reached and added through its owner, has no list or create of its own,
    and never moves to another owner. The field must be a required reference to one record of another type.
  - **Link** (`rel` on a reference field): both records stand on their own. A visit links to its patient the way it
    links to its practitioner, and the patient's page shows its visits from the other side.
- **Links written in the text**: they become links like a reference field's, including links to a page created after
  the text that named it.

## Layout

```
practicehub/          the core: hub, engine, workflows, blocks, sandbox, backends, track, authz, records, models, web, cli
  agents/runner.py    an agent's loop: inbox → context → model → answer (or escalate)
  integrations/       Keycloak; Collabora (discovery, proof keys, blank documents) and its WOPI host
packs/general-practice/  roles, data classes, grants, members, 8 workflows, 8 skills, 3 blocks, stand-in answers
modules/              dms (document control), qms (quality), wiki
tests/                rules, web (real HTTP), another practice (accounting)
deploy/               docker compose, LiteLLM config, env example
docs/                 workflows and packs
```
