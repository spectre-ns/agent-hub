# Plan: what a move set going (automation activity on the record page)

Status: planned 2026-11-23, not started. Written for whoever implements it; read `AGENTS.md` first.

Line numbers below are from the working tree on 2026-11-23, which had the states-and-edges work uncommitted
(`practicehub/states.py`, `engine.py`, `workflows.py`, `catalog.py`, the general-practice workflows). Build on that
work as you find it; find code by name if the lines have moved.

## Why

When a record moves along an edge, the edge's automations run and may start other work (a follow-up Task, an
appointment request, a message). The record page hides almost all of it:

- History drops every edge automation that succeeds (`moves.py:334`, `_run_rows`), and lists a "skipped" line for
  each automation on edges the record did not take.
- A record started by `workflow.start` (the lab result's follow-up call) is not in "Started from this…"; only
  `record.child` spawns are (`Engine.children_of`, `engine.py:1139`).
- A closed record with a follow-up still open shows nothing about it at the top of its page.
- Nothing on the record page refreshes.

Goal: on the record's own page, show per move what it ran and what it started, each with its status, and keep that
current while anything is still open.

## Decided with the owner

| Question | Decision |
|---|---|
| Where | Both: a block in the work section for the latest move, and a list under every move in History |
| Live | The block updates on its own while anything is in progress; a Refresh link without JavaScript |
| Detail | Every automation on the edge the record took, in words with its outcome; none from edges not taken |
| Depth | One level: each started record with its title, state and holder, linking to its own page |

The owner's sketch of the block:

```
Lab result LR-204          [ In review ]

 Its work
 +--------------------------------------------+
 | After "Needs a call" (Dr. Lee, 2:14 pm)     |
 |  v Paged the physicians                     |
 |  v Started Task T-88 "Call the patient"     |
 |      -> To do, with Nursing                 |
 |  ~ Sending the patient a message...         |
 +--------------------------------------------+
 [ Close ]

 History
  2:14  Moved from In review to Actioned
          > ran 3 automations, started 1 record
  2:10  Created (Checking)
```

Lines use the automation's authored title and a status word ("Page the physicians: done"), not a derived past tense.

## What is true today (found by reading the code)

- **Automations run synchronously**, inside the request that makes the move and inside one savepoint each
  (`Engine._advance`, `engine.py:1271-1410`). By the time the page comes back each is `done` or `failed`. There is
  no "running" state to show. What is live afterwards: the work they started, messages waiting to be sent (outbox),
  agent or people's work at the record's new state, and stuck steps being retried.
- **An edge compiles to steps** (`states.py:246-296`): each `[[edge.do]]` becomes an automation step with
  `on_edge = <edge id>`, and the edge ends with an arrive step `ARRIVE.format(edge=...)` that has `state = <to>`.
  `Workflow.shape = {start, states[], edges[{id, label, from, to, when, do, steps, lands, route?, ends_pass}]}`.
  A person's one-way-on task also carries `on_edge` and `state` (`states.py:233-236`); it is a state's work, not an
  edge automation.
- **Nothing records a move as such.** The edge id is only on the workflow definition's `Step.on_edge`. But within
  one run each step runs once and each state is entered once, so `(run id, edge id)` names a move.
- **Run step states** (`run["steps"][sid]`): `pending`, `done` (`by`, `moved`), `failed` (`error`), `skipped`,
  `waiting`, `active`. No timestamps; times come from track events `action.ran` / `step.failed` (`data.step`),
  indexed by `run_id`. A failed automation leaves its dependents `pending`, including the arrive step, so the
  record does not move; the run stays `running`.
- **Step outputs** (`run["ctx"][sid]`): `workflow.start` → `{run_id}`; `record.child` → `{id, reference, run_id?}`;
  `record.create` → `{id, reference}`; `record.update` → `{reference}`; `message.send` →
  `{message_id, to[, message_ids]}`; a `for_each` automation → `{"each": [...], "passed_over": [...]}`.
- **Outbox** rows (`db.outbox`) have `status`: `queued`, `sending`, `sent`, `delivered`, `failed`.
  `message.send` counts as done once queued.
- **Spawn links.** `workflow.start` passes `parent=run["id"]` (`engine.py:233`), stored as `ctx.run.parent` and in
  `run.started` data. `record.child` also writes a `record.child` event on the parent. `record.create` on a type
  that names a workflow starts it with no parent at all (`Hub._start_record_workflow`, `hub.py:3752`). Nothing says
  which step or edge made the spawn.
- **Web layer.** Standard library `http.server`, pages are f-strings escaped with `html.escape` (`e`), one CSS and
  one JS string in `practicehub/web/assets.py`. The record page: `page_record` (`records_pages.py:1436`), the work
  section `RecordWork.record_work` (`record_work.py:46-158`), History `_history_panel` (`records_pages.py:1952`)
  over `Hub.record_timeline` (`moves.py:209`). The only partial refresh in the app is the board's fetch-and-redraw
  with a hidden `aria-live` region (`assets.py`, `boards()`, about line 2285).
- **Background work**: a 60 s clock thread (`engine.tick`, `outbox_tick`) and, with `serve --agents`, an agent
  thread (`cli.py:174-219`). There is no event bus or change stream.

## The model: a move is (run, edge)

New module `practicehub/activity.py`, a Hub mixin beside `moves.py`, with one public function:

`Hub.record_activity(viewer, ref) -> list[move]`, oldest first, over runs with `record == ref` of any status.

```
{"run", "edge", "label", "to", "seq", "at", "who",
 "after_person": bool,          # False: this move followed on from the one before (an automated state's edge)
 "ran": [{"step", "title",
          "state": "done" | "failed" | "held",     # held: pending behind a failed one
          "at", "error", "hidden": bool,
          "started": [{"ref", "run", "readable": bool}],
          "message": "queued" | "sending" | "sent" | "delivered" | "failed" | None}],
 "settled": bool}               # nothing failed or held, no message unsent, every started run finished
```

Rules:

- The edge's automations are `shape["edges"][i]["steps"]` plus its arrive step, read from the run's **pinned**
  workflow version (`hub.workflow(run["workflow_id"], run["version"])`, or `Viewer.workflow_of_run`).
- An edge was taken when any of its steps is `done` or `failed`.
- Leave out automations on a taken edge that were `skipped` (their own `when` was false), and the arrive step
  itself (it is the move, not something the move ran) unless it failed.
- `at` / `who` / `seq`: from the event that set the edge going (the decision or completion that chose it, or
  `run.started` for the start edge); fall back to the first of the edge's own events.
- What an automation started, from its output: `run_id` → that run and its `record`; `reference` → that record and
  the run walking it (`db.runs.find({"record": ref})`); `message_id(s)` → the outbox row's status (the worst of
  several). Look inside `each` for a `for_each` automation. `record.update`'s `reference` is not a started record.
- Visibility: `visibility.Viewer.step_visible(wf, sid)` for each automation (hidden ones set `hidden` and carry no
  title, error or started records); `hub.may_read(viewer, rtype)` for each started record (`readable`).
- A cascade is a move with `after_person` True and the moves after it with `after_person` False. "The latest move"
  on the page means the latest cascade.
- A workflow with no `shape` gives an empty list (the page then behaves as it does today).

## Engine: say where a spawn came from

So a started record can name the move that made it, and so `record.create` spawns are linked at all.

- `Engine.start(..., origin: dict | None = None)` (`engine.py:625`): store as `ctx.run.origin = {"step", "edge"}`
  next to `parent` (`engine.py:711`) and add it to `run.started` data (`engine.py:731`).
- `act_workflow_start` (`engine.py:233`): pass `origin={"step": step.id, "edge": step.on_edge}`.
- `make_child_record` / `_start_child_run` (`engine.py:1079`, `1115`): thread the step through and pass the same.
- `record.create` (`act_record_create`, `engine.py:157`): have `Hub.create_record` → `_start_record_workflow`
  (`hub.py:3752`) pass `parent` and `origin` when the record is made by a step.
- New `Engine.started_by(run) -> {"ref", "label"} | None`: the parent run's record and the edge's label, from
  `ctx.run.parent` + `ctx.run.origin`. For runs from before this change (no origin): the record only.

No new collection and no new index. The only change to stored shapes is the added `origin` key.

## Record page

### 1. The block in the work section (`practicehub/web/record_work.py`)

- New `RecordWork._activity_html(me, ref, url)` renders the latest cascade:
  `<section id="activity" class="rw-activity" aria-labelledby="rw-activity-h">`, heading
  "After “<edge label>”" with who and when, then one line per automation: its title and, in words,
  **done**, **did not go through** (with the error, for those who may see it), or **waiting on “<the failed
  one>”**; a message's status ("waiting to be sent", "sent", "not delivered"); and each started record as
  `_family_link` + `_state_loz` + `_child_work` (reuse `work_panel.py:168-194`: Done, Stopped, Stuck, or who holds
  it and when it is due). A started record the member may not read keeps its kind, state and holder, with no title
  and no link, as `started_from` does. Hidden automations fold to one line ("2 steps you may not see").
- A second edge in the cascade gets a sub-heading ("Then, “<label>”").
- `<details>`: open while the move is not settled or was made in the last ten minutes; after that a one-line
  summary ("After “Needs a call”: 3 automations ran, 1 record started"). A move that ran nothing and started
  nothing shows no block.
- Place it in `record_work()` between the stuck flashes and `rw-moves` (`record_work.py:146-150`). Build it from
  runs of any status, so a closed record still shows its open follow-up; `body` is the section whenever there is a
  block (today it is an empty `<span id="work">` once the run has finished).
- The existing Stuck flash keeps the retry form; the block only says what did not go through.
- No new buttons. The page keeps its one primary move.
- Share the list rendering with History through one helper, `_ran_list_html(me, ran)`.

### 2. History (`practicehub/moves.py`, `practicehub/web/records_pages.py`)

- `_run_rows` (`moves.py:305`): events of an automation step with `on_edge` no longer make rows of their own. That
  removes the "skipped" lines for edges not taken and the "'Move to X' done" line beside "Moved from … to …".
  A person's task that carries `on_edge` keeps its rows.
- Each move from `record_activity` becomes one row at its seq. Merge it into the record's own "Moved from X to Y"
  row when that row has the same run and to-state; otherwise (a workflow state the record's field has no value
  for, `engine.py:1170-1175`) it is its own row: "Moved on by “<edge label>”". The row carries `ran`.
- An automated state's own work (`action.ran` on a step with `at_state`, no `state`) gets a row:
  "“Check the value against its range” done".
- `_history_panel` (`records_pages.py:1952`): under a row with `ran`, a `<details class="tl-ran">` whose summary is
  "3 automations ran, 1 record started" and whose body is `_ran_list_html`, the started records shown as they are
  now. `_fold` and the "not shown to you" lines are unchanged.

### 3. Started from / Made for (`practicehub/web/work_panel.py`)

- `started_from` (`:144`): list every record a move of this record started, `engine.children_of(ref)` plus the
  started records in `record_activity`, deduplicated by ref. The follow-up Task then appears on the lab result.
- `family_panel` (`:136`): when `engine.started_by(run)` answers for the run walking this record,
  "Started by “Needs a call” on <link>" (checked with `hub.may_read`). The existing "Made for" stays for
  `record.child`.

## Keeping it current

- New route `GET /records/<T>/<I>/activity` (register with the others at `records_pages.py:95-120`): the block
  alone, with an `ETag` of its content and `304` on a matching `If-None-Match`. Same read check as the page.
- The section carries `data-live="<that url>"` only while the move is not settled, and `data-at` (the record's
  version and the `updated_at` of the run walking it).
- `assets.py`: a new `activity()` wired on DOMContentLoaded beside `boards()`. While `[data-live]` is on the page
  and the tab is visible, fetch every 4 s (every 15 s after two minutes), swap the section when the ETag changes,
  and say what changed in a visually hidden `aria-live="polite"` region. Stop when the section comes back without
  `data-live`.
- When `data-at` in the answer differs from the page's (the record itself moved on: an agent finished its step, a
  wait ended), do not redraw the page's forms. Show `role="status"`: "This has moved on since you opened it. Show
  it as it is now" (a link to the page), so nothing typed is lost.
- Without JavaScript: a "Refresh" link in the block while it is live. No meta refresh on a page with forms.
- `RECWORK_CSS` (`assets.py:4835`): `.rw-activity`, `.tl-ran`. State words keep their meaning without colour; any
  mark is `aria-hidden`.

## Rules the work must keep

- `AGENTS.md`: standard library and pymongo only; no template engine; the core knows nothing medical (wording
  comes from the record type and the workflow); never bind 8080, 9980 or 11434; commit only when asked.
- Everything shown goes through `visibility.Viewer` and `hub.may_read`. What a member may not see is folded or
  unnamed, never titled. A change to a visibility rule needs a test in `tests/test_rules.py`.
- Plain words, as `tests/test_usability_review.py` enforces: no ids (`run-…`, `item-…`), no "run", no "input.",
  no "==". Use the page's own words: "work", "steps", "did not go through", "a step you may not see".
- Never link `/runs/<id>` for work about a record; use `run_href` / `record_href`
  (`test_no_page_links_work_about_a_record_to_the_run`, `tests/test_record_page.py:90`).
- Keep the class and id hooks tests slice by: `rec-status`, `rw-moves`, `rw-step`, `ol.timeline`, `cancel-work`,
  `id="work"`, `data-modal-body`.
- Every flow works without JavaScript.
- Tests use MongoDB only (`scripts/test-mongo.sh`, replica set on 127.0.0.1:27118; it is tmpfs, so after a reboot
  start it again).

## Files

| File | Change |
|---|---|
| `practicehub/activity.py` (new) | `record_activity`, the move model |
| `practicehub/hub.py` | mix it in; `create_record` / `_start_record_workflow` pass parent and origin |
| `practicehub/engine.py` | `start(origin=)`, `act_workflow_start`, `make_child_record` / `_start_child_run`, `started_by` |
| `practicehub/moves.py` | `_run_rows`: moves as rows with `ran`; automated-state work |
| `practicehub/web/record_work.py` | `_activity_html`, `_ran_list_html`, placement in `record_work` |
| `practicehub/web/records_pages.py` | the `/activity` route; `_history_panel` nested list |
| `practicehub/web/work_panel.py` | `started_from`, `family_panel` |
| `practicehub/web/assets.py` | `activity()` script, CSS |
| `docs/workflows.md` | a short section: what the record page shows of a move |

## Order of work

1. `activity.py` and the engine origin, with model tests (no UI).
2. History rows and the nested list.
3. The block in the work section; `started_from` / `family_panel`.
4. The `/activity` route and the script.

Each step leaves the suite green. Steps 1 to 3 are useful without 4.

## Tests

- `tests/test_activity.py` (new), on the demo hub with `lab-result-review`
  (`packs/general-practice/workflows/lab-result-review.toml`; helpers in `tests/conftest.py`: `run_of`,
  `item_for`, `follow_up_run`):
  - "needs a call": the move lists that edge's automations only, each done; the follow-up Task is in `started`
    with its run; the other outcomes' automations are absent; the Task's run has `ctx.run.origin`, and
    `started_by` names the lab result and the edge's label.
  - a critical result: one cascade of two moves (filed → checking, then "Critical"); the page's message with its
    outbox status, changing after `outbox_tick`.
  - an automation made to fail: `failed` with its error, what waits on it `held`, `settled` False; after `retry`,
    done and settled.
  - a member without the clinical data class: hidden automations folded, the started record unnamed.
- `tests/test_record_page.py`: the block is inside `id="work"` with `class="rw-activity"`, also on a closed
  result with its Task open; `data-live` only while unsettled; History's move row holds `class="tl-ran"`, and
  there is no "skipped" line for an edge not taken; the Task's page says "Started by"; no `href="/runs/run-`
  (extend the existing test to the fragment).
- `tests/test_web.py`: `GET …/activity` gives the fragment, `304` with the ETag, and refuses as the page does.
- `tests/test_usability_review.py`: the block and the nested list pass the `_text()` rules.
- `tests/test_rules.py`: a started record of a type the member may not read is never named or linked.

## Verification

```sh
scripts/test-mongo.sh && .venv/bin/python -m pytest -q
python3 -m practicehub.cli --state /tmp/ph init --demo --force
python3 -m practicehub.cli --state /tmp/ph serve --agents      # port 8200
```

In the browser as a physician: open a lab result in review and choose "Needs a call". The page returns with the
block listing the edge's automations and the Task "to do" with nursing; History's "Moved from in review to closed"
row opens to the same list. In a second window complete the Task as a nurse: the first window's block changes to
Done within a few seconds and stops polling. File a critical result and watch the page's message go from "waiting
to be sent" to "sent". Then the closing checks in `AGENTS.md` (`agents run --once`, `verify`).

## Not in this plan

- Running slow external blocks out of band, with a real "running" state.
- Passing `cause` into `workflow.start` so the track's chain is unbroken across a spawn.
- Authored past-tense wording per automation ("Paged the physicians").
- The board, the workflow page and analytics.
