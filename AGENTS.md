# Working on Practice Hub

Notes for anyone, human or agent, changing this code.

## Ground rules

- **Never bind ports 8080, 9980 or 11434**, and leave whatever else runs on the machine alone: other services use
  those ports. Using Ollama on 11434 as a client is fine. Practice Hub serves on 8200.
- **No secrets in git or in output.** `state/`, `.env`, `deploy/.env`, `.secret` and the generated Keycloak realm are
  gitignored. Secrets come from the environment.
- **Model output is data, never code.** There is no `eval`, `exec` or template engine over model output. Conditions
  use `practicehub/expr.py`.
- **The runtime is the standard library and pymongo.** The database is MongoDB only (a replica set, for
  transactions; files in GridFS); there is no SQLite, not even for tests. pytest is for tests only.
- **The core knows nothing about any one practice.** Anything medical belongs in `packs/general-practice`.
  `tests/test_other_practice.py` fails if the core starts assuming patients.

## Where the rules live

| Rule | Code |
|---|---|
| Who may take an item (agents, sponsors, signing, criteria, risk, skills, data classes, separation of duties) | `Engine.eligibility` in `practicehub/engine.py`, the one place to read |
| Risk tiers (medium: sponsor notice; high: held for sponsor approval) | `Engine._finish`, `Engine._hold`, `Engine._decide_hold` |
| What a member sees of runs and the track | `practicehub/visibility.py` |
| What a subject withheld from whom (consent directive, lockbox), and the emergency override | `practicehub/restrictions.py` (base type `practicehub/base/Restriction.toml`) |
| Sponsors, kill switch, tokens, change control | `practicehub/hub.py` |
| Hash chain and signatures | `practicehub/track.py` |
| Collections, document shapes, indexes; transactions and files | `practicehub/db.py`, `practicehub/store.py` |
| Answer validation | `practicehub/fields.py`, plus the pack's `GUARDS`; a guard whose code is not running fails closed (`Hub.code_not_running`, checked in `Engine._complete`) |
| What an agent may write outside its work (nothing, unless its profile has `direct_writes`) | `Hub.writes_directly`, `Hub.require_writer` in `practicehub/hub.py` |
| Which files a record may name, and who reads a file | `Hub._check_files_named`, `Hub.read_file` in `practicehub/hub.py` |
| Who reads a queued message (data classes, and a subject's directive on the file it is about) | `Hub.may_read_message`, `Hub.message_restricted` in `practicehub/outbox.py` |
| A record in a locked state: people change nothing but its state, and its own workflow is not started on it again | the `locked` check in `Hub.update_record`; `Engine.start_over_refusal` in `practicehub/engine.py` |
| An event nothing started on is not dropped | `Engine.emit`, `Engine._unhandled` in `practicehub/engine.py` |
| Blocks: parsing, who may propose (agents: prompt blocks only), workflow checks, version pinning | `practicehub/blocks.py`, `Hub.propose_block`, `Hub._block_problems`, `Engine.start` |
| What Python block code may do | `practicehub/sandbox.py` (static check and the child's audit hook) |
| Which engine runs a block, and what it is sent | `practicehub/backends.py` |
| Which model an agent answers with (the one its profile names; a new agent is given the hub's default, a local Ollama model) | `ModelGateway.answer`, `default_model` in `practicehub/models.py`, `Hub._create_member` |
| Consent to commercial messages (CASL: express, implied for 2 years, withdrawn), checked at queue and at delivery | `Hub.consent_problem` in `practicehub/outbox.py`, a type's `[consent]` (`practicehub/schema.py`) |
| A record's key (its type's, given once, never reused by another type) and who is told what a key names | `LocalRecordStore.create` in `practicehub/records.py`, `CatalogMixin._key_problems` in `practicehub/catalog.py`, `Hub.find_key` |
| A type's own number (`[number]`): given once by the hub, in a series with no gaps, never written by hand | `Hub._check_number_kept`, `Hub._take_number` in `practicehub/hub.py` |

If you change one of these, add or update a test in `tests/test_rules.py` that states the rule (the rules the
2026-11-24 review panel found broken are stated in `tests/test_panel_fixes.py`).

## Checks before you finish

```sh
scripts/test-mongo.sh   # a one-node MongoDB replica set on 127.0.0.1:27118; every test gets its own database
.venv/bin/python -m pytest -q
scripts/test-parallel.sh     # or: the suite in one shard per core, all on one shared test MongoDB (ph-test-mongo-shared, kept running); runs queue for slots, half the cores by default (PH_TEST_SLOTS)
python3 -m practicehub.cli --state /tmp/ph init --demo --force && \
  python3 -m practicehub.cli --state /tmp/ph agents run --once && \
  python3 -m practicehub.cli --state /tmp/ph verify
```

Commit only when asked. End commit messages with:
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
