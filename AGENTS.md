# Working on Practice Hub

Notes for anyone, human or agent, changing this code.

## Ground rules

- **Never touch the live hub-erp** (localhost:8080, containers `hub-erp*`). Never bind ports 8080, 9980 or 11434.
  Using Ollama on 11434 as a client is fine. Practice Hub serves on 8200.
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
| Sponsors, kill switch, tokens, change control | `practicehub/hub.py` |
| Hash chain and signatures | `practicehub/track.py` |
| Collections, document shapes, indexes; transactions and files | `practicehub/db.py`, `practicehub/store.py` |
| Answer validation | `practicehub/fields.py`, plus the pack's `GUARDS` |
| Blocks: parsing, who may propose (agents: prompt blocks only), workflow checks, version pinning | `practicehub/blocks.py`, `Hub.propose_block`, `Hub._block_problems`, `Engine.start` |
| What Python block code may do | `practicehub/sandbox.py` (static check and the child's audit hook) |
| Which engine runs a block, and what it is sent | `practicehub/backends.py` |
| Consent to commercial messages (CASL: express, implied for 2 years, withdrawn), checked at queue and at delivery | `Hub.consent_problem` in `practicehub/outbox.py`, a type's `[consent]` (`practicehub/schema.py`) |

If you change one of these, add or update a test in `tests/test_rules.py` that states the rule.

## Checks before you finish

```sh
scripts/test-mongo.sh   # a one-node MongoDB replica set on 127.0.0.1:27118; every test gets its own database
.venv/bin/python -m pytest -q
scripts/test-parallel.sh     # or: the suite in one shard per core, each with a MongoDB of its own (removed afterwards); one run at a time per machine
python3 -m practicehub.cli --state /tmp/ph init --demo --force && \
  python3 -m practicehub.cli --state /tmp/ph agents run --once && \
  python3 -m practicehub.cli --state /tmp/ph verify
```

Commit only when asked. End commit messages with:
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
