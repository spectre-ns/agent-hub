# Working on Practice Hub

Notes for anyone, human or agent, changing this code.

## Ground rules

- **Never bind ports 8080, 9980 or 11434**, and leave whatever else runs on the machine alone: other services use
  those ports. Using Ollama on 11434 as a client is fine. Practice Hub serves on 8200.
- **No secrets in git or in output.** `state/`, `.env`, `deploy/.env`, `.secret` and the generated Keycloak realm are
  gitignored. Secrets come from the environment.
- **Model output is data, never code.** There is no `eval`, `exec` or template engine over model output. Conditions
  use `practicehub/expr.py`.
- **The runtime is the standard library, pymongo and pypdf.** pypdf reads a PDF's words, in `practicehub/filetext.py`
  only. The database is MongoDB only (a replica set, for transactions; files in GridFS); there is no SQLite, not even
  for tests. pytest is for tests only.
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
| Which files' words the hub reads (a PDF's text layer, office and text files; never a picture), and which field they fill (`from_file`, not over the writer's own words) | `filetext.text_of` in `practicehub/filetext.py`; `Hub._fill_from_files` in `practicehub/hub.py` |
| Where a picture goes: only to the model the agent's profile names as `vision_model` (a second policy owner approves it), fetched as the agent; any other model is told it is not shown one | `Engine._file_context` in `practicehub/engine.py`; `AgentRunner._pictures` in `practicehub/agents/runner.py`; `ModelGateway.answer` in `practicehub/models.py`; `Hub.propose_agent_profile` |
| Who brings an agent into a thread (only a person: an agent's `@` names only people), who may be mentioned where (grants and the subject's directive), and how an agent answers (in the thread, or says why it cannot) | `Mentions.resolve`, `Mentions.may_see` and `Mentions.reply` in `practicehub/mentions.py`; `AgentRunner.answer_mention` in `practicehub/agents/runner.py` |
| What an agent may do in a chat (only what both it and the person may: the types, moves and workflows both have, the records neither is kept from, the fields both may read and write; always as itself, under its own grants and profile), who reads a chat (its person only), and what the track keeps of it (ids, never what was said) | `Chat.may_reach` and `Offer` in `practicehub/chat.py` |
| Who reads a queued message (data classes, and a subject's directive on the file it is about) | `Hub.may_read_message`, `Hub.message_restricted` in `practicehub/outbox.py` |
| A record in a locked state: people change nothing but its state, and its own workflow is not started on it again | the `locked` check in `Hub.update_record`; `Engine.start_over_refusal` in `practicehub/engine.py` |
| An event nothing started on is not dropped | `Engine.emit`, `Engine._unhandled` in `practicehub/engine.py` |
| Blocks: parsing, who may propose (agents: prompt blocks only), workflow checks, version pinning | `practicehub/blocks.py`, `Hub.propose_block`, `Hub._block_problems`, `Engine.start` |
| What Python block code may do | `practicehub/sandbox.py` (static check and the child's audit hook) |
| Which engine runs a block, and what it is sent | `practicehub/backends.py` |
| Which model an agent answers with (the one its profile names; a new agent is given the hub's default, a local Ollama model) | `ModelGateway.answer`, `default_model` in `practicehub/models.py`, `Hub._create_member` |
| Consent to commercial messages (CASL: express, implied for 2 years, withdrawn), checked at queue and at delivery | `Hub.consent_problem` in `practicehub/outbox.py`, a type's `[consent]` (`practicehub/schema.py`) |
| A record's key (its type's, given once, never reused by another type; a family of types shares one) and who is told what a key names | `LocalRecordStore.create` in `practicehub/records.py`, `CatalogMixin._key_problems` in `practicehub/catalog.py`, `Hub.find_key` |
| A type under another: it inherits everything and only adds or narrows; no loops, at most eight deep; a parent's change that breaks a type under it is refused; an abstract type has no records of its own | `schema._merge`, `schema._narrows`, `CatalogMixin.type_parent`, `CatalogMixin._descendant_problems` in `practicehub/catalog.py`; `Hub._creatable` in `practicehub/hub.py` |
| Naming a type takes in its family (references, lists and search, views, workflow triggers and `for_each`, grants, directives, MCP and exports); a ref naming a type above a record's own finds it | `CatalogMixin.family`, `CatalogMixin.family_rx` in `practicehub/catalog.py`; `Hub.search_records`, `Hub.resolve_ref` in `practicehub/hub.py`; `Engine.on_record`; `Pipeline` in `practicehub/views_query.py`; `practicehub/restrictions.py`; `practicehub/mcp.py` |
| A type's own number (`[number]`): given once by the hub, in a series with no gaps, never written by hand | `Hub._check_number_kept`, `Hub._take_number` in `practicehub/hub.py` |
| A family shares its key and counter, its number's series, each unique key (across the family of the type that set it) and its ids; a record moved within its family keeps its key and number; an abstract type's records change only by moving under it | `Hub._take_number`, `Hub._check_unique`, `Hub._check_id_free`, `Hub.convert_record`, the abstract check in `Hub.update_record`; `LocalRecordStore.create` (`key`); the `(number, number_of)` index in `practicehub/db.py` |
| A record in a final state: nothing changes it, no move leaves it; it is corrected by one record that reverses it | `Hub._sealed_write`, `Hub._check_reversal` in `practicehub/hub.py`; `schema.leaves`, `catalog.lifecycle_fit` |
| What a signature covers (its pins and the parts its type covers), nothing signed (a signature or a signed move) while a pin is behind, and the parts locked with their record | `Hub._record_digest`, `Hub._pinned`, `Hub.refuse_pins_behind`, `Hub._check_part_lock` in `practicehub/hub.py` |
| A closed period: nothing dated in it is written; only a signed move reopens it | `Hub._check_period`, `Hub.closed_through`, `Hub._sealed_write` in `practicehub/hub.py` |
| Exact numbers: a decimal is rounded half up to its places, stored exact, and carried as its exact text; a model is asked for one as a JSON number (it fills that; a string with a pattern it leaves empty), read back exactly | `practicehub/decimals.py`, `schema.exact`; `fields.json_schema`; the codec in `Store.__init__` (`practicehub/store.py`) |
| A table's rows, each checked against its columns | `schema._check_value` (the table branch), `RecordType.cells` in `practicehub/schema.py` |
| A type's checks (`[[check]]`): kept by every write, whoever writes | `Hub._check_record` in `practicehub/hub.py`, `schema._check_checks` |
| A move's checks (over the record and its parts), kept by whoever moves it, a step included; a step whose answer the record's own field rules would refuse (its form or skill need not state them) is not finished | `Hub.move_check_refusal`, the move checks in `Hub.update_record`; `Engine._refused_move` (`schema.field_problems`); `moves.move_refusal` |
| A view's numbers: worked out in the database over the records the reader may read; a value withheld from them is not added up, a path hidden from them shows "needs access" | `records_numbers`, `Pipeline`, `_rights` in `practicehub/views_query.py`; `masks_for` in `practicehub/restrictions.py` |
| The sign-offs a view lists: a person's signatures, on records the reader may read and not withheld from them | `signature_rows` in `practicehub/views.py` |
| A move's stamp: who made it and when, written by that move alone (a workflow's move: whoever did the step) and covered by a signature that makes it | `Hub.stamped`, `Hub._check_stamps_kept` in `practicehub/hub.py`; `schema._stamp_fields` |
| `unique` over a reference compares the record it names, however it is written | `Hub._check_unique` in `practicehub/hub.py` |
| A portal kind about a person's own record: only one of theirs its condition offers them now, checked again on sending | `Portal.about_choices`, `Portal.book` in `practicehub/portal.py` |
| A step done by a move is refused to whoever wrote the record before the move (`not_by = ["record.editors"]`); the fields that move writes do not make its mover an editor | `Hub.revision_authors` in `practicehub/hub.py`; `Engine.eligibility`; `_step_by_move` in `practicehub/moves.py` |

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
