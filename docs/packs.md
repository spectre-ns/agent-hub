# Packs: setting up another kind of practice

A pack is a directory that makes a hub a particular kind of practice: a family clinic, a dental office, a law firm,
an accountancy. `packs/general-practice` is the full example. `tests/packs/accounting` is a minimal one, about 300
lines in all.

[Customizing Practice Hub](customizing.md) says when to write a pack, and when an overlay (your own practice on a
pack that ships, `base = "<pack>"`) or a module is enough.

```
pack.toml          the practice, its subject, roles, data classes and grants, seed members, calendar
workflows/*.toml   the first version of each workflow (the practice changes them through change control)
skills/*.toml      what agents know how to do: instructions, and the shape of the answer
blocks/*.toml      the first version of each block: python, prompt or engine steps workflows can reuse
types/*.toml       the first version of each record type: fields, lifecycle, screens, signatures, key
pack.py            optional Python: summaries, registration, actions, providers, guards, stand-in answers
```

Install a pack with `practicehub init --pack <name or path>`. Packs are looked up in `packs/`, or in
`$PRACTICEHUB_PACKS`.

## pack.toml

```toml
[practice]
id = "lighthouse"
kind = "accounting"
name = "Lighthouse Accounting"
jurisdiction = "Nova Scotia, Canada"
data_residency = "Canada"
escalation_role = "partner"          # who gets items no one else can take

[subject]                            # who or what the work is about
type = "Client"                      # the record type; a run's `record` points at one
label = "Client"
plural = "Clients"
path = "clients"                     # the web pages: /clients, /clients/<id>
registered_event = "client.signed-up"  # emitted when someone registers one (a workflow can listen for it)
fields = [                           # the registration form; the first two make the name
    { name = "given", label = "Given name", required = true },
    { name = "family", label = "Family name", required = true },
    { name = "email", label = "Email" },
]

[[roles]]
id = "preparer"
name = "Return preparer"

[[data_classes]]                     # permissions are per data class, never per record type
id = "tax"
name = "Tax documents and returns"
record_types = ["TaxSlip", "TaxReturn"]
# restricted = false                 # only for data anyone may see (opening hours): giving an agent or service a role
                                     # that reaches a restricted class waits for a second policy owner

[grants.preparer]
read = ["profile", "tax"]
# write = [...], policy_owner = true (approves workflow changes), member_admin = true (manages members)

[[members]]
id = "lee"
name = "Lee Marsh, CPA"
roles = ["partner"]
owner = true

[[members]]
id = "prep-agent"
kind = "agent"
name = "Preparation agent"
sponsor = "lee"                      # required: an active person
roles = ["preparer"]
profile = { model = "litellm/firm-local", max_risk = "medium", skills = ["return-prep"] }
```

An agent that names no `model` is given the hub's default when it is installed: `PRACTICEHUB_DEFAULT_MODEL`, else
`ollama/qwen3:14b`, a local model through Ollama (`OLLAMA_URL`). It is written into the agent's profile, so a later
change of the default moves no agent. `model = "standin"` answers from the pack's `STANDINS`, with no model.

**Design the data classes first.** They decide what each agent can ever see. Give every agent the narrowest grants
that let it do its job. A scheduling agent needs demographics and appointments, nothing else.

## Record types

`types/*.toml` holds the first version of each record type: its fields, lifecycle, screens and signatures. The whole
language is in the docstring of `practicehub/schema.py`. A type's records are keyed in it:

```toml
id = "T1Return"
label = "T1 return"
key = "T1"            # its records are T1-1, T1-2...: one to eight capital letters and digits, starting with a letter
```

The key is given when a record is made, counted per type, and never changes. Work on the record shows it. A type
that sets no key takes one from its label (`Engagement`: `ENG`). Once records carry a key, the type keeps it: a
change, another type taking it (even after this one is retired), or the practice's code as a key is refused. The
key opens the record (`/T1-12`, the search box, `GET /api/keys/T1-12`, the MCP tool `find_by_key`).

A type whose records need a number of their own, in a series with no gaps (a document number), says so:

```toml
[number]
field = "number"                # a string field, not required: the hub writes it, and nobody else does
pattern = "INV-{year}-{seq:04}" # {seq} once ({seq:04}: at least four digits); {year}, {month}; letters, digits, - / . _
date = "issued_on"              # the date it reads (default: the type's date); needed when the pattern names one
when = ["sent"]                 # given by the move into one of these states (default: when the record is made)
```

The number is the next in its series (each year or month the pattern names starts again at 1), taken in the write
that numbers the record, so a write that fails gives it back. It never changes, a record numbered by its date keeps
that date, and a numbered record is never converted into another type: no number is skipped or given twice. A series
goes on after the highest number its records already carry. Numbers are unique within the type, and find the record
as its key does (not in the address). Only the hub's own record store numbers records.

### Sign-off that holds

A state can be **final**: once a record is in it, nothing changes it, not a person, its workflow, a pack upgrade or a
signature's move, and no move leaves it (`from = ["*"]` does not match it). A final state is locked too. A record in
one is corrected by another record of its type that reverses it:

```toml
reverses = "reversal_of"        # a reference field to this same type, not required
[lifecycle]
final = ["posted"]
```

The reversing record reverses one that is final, that no other record reverses, and which one it reverses never
changes. The record page offers "Reverse it": the new record's form holds a copy of the final one's values. What the
reversal changes (a sign, say) is the pack's to say, in its form or its workflow; the core copies.

A reference can **pin** what it refers to: it keeps the record's version and hash as they were when it was given.

```toml
[[field]]
name = "counted"
type = "reference"
target = ["Slip"]
list = true
pin = true                      # one field (list = true for several), not a path through a list
```

A later write keeps the pin whatever it says; only "Use version M" on the record page (`Hub.repin`) moves it on. A
signature on the record covers what it pins, and the parts its type **covers** (`covers = ["Line"]`, part types whose
`part_of` points here): a change to either leaves the signature not current. A pinned record is covered where it
was in its lifecycle too, so moving it (a document sent back to be read again) is such a change. The record is not
signed while a pin is behind. While the record is locked people do not add or change the parts it covers (its
workflow still does); while it is final no one does. A type with no pins and no `covers` is signed exactly as before.

A **closed period** keeps everything dated in it as it was. One type says what closes it, another closes it:

```toml
# on the dated type
[period]
closed_by = "PeriodClose"       # a type with [closing]
date = "posted_on"              # a required date or datetime (default: the type's date)
scope = "book"                  # optional: a reference; a close reaches only records with its own

# on the closing type
[closing]
through = "through"             # its required date: the last day it closes
states = ["closed"]             # it closes while in these
scope = "book"                  # names the same type as the period's scope
```

While a close is in one of its closing states, nothing dated on or before its `through` is written, moved into the
period or out of it, by anyone. A closing state is locked, nothing changes a close in it, and every transition out
of one carries `sign`: only a signed move reopens a period.

### Exact numbers

A `decimal` field keeps an exact number with a set number of places, as a `number` field keeps a float:

```toml
[[field]]
name = "amount"
type = "decimal"
scale = 2                       # places, a whole number from 0 to 10 (required)
unit = "CAD"                    # optional, at most 20 characters, shown beside the value (also on a number field)
min = 0                         # bounds are exact too
```

A value is rounded half up (away from zero) to its places when it is written, whoever writes it: 12.345 is 12.35,
-2.345 is -2.35, "1,234.5" is 1234.50, and -0.00 is 0.00. True or false, NaN, infinity, words and more than 15
digits are refused, saying how to write one. It is stored exact (MongoDB's Decimal128), so it reads back with its
places, 12.30 and not 12.3; writing the same number again (12.3 over 12.30) changes nothing, so a signature over it
stays current. JSON (the API, MCP, exports, a backup) carries it as its exact text, `"12.30"`; FHIR, whose decimal is
a JSON number, gets a number. A condition compares it as a number (a stored 0.10 is 0.1), and Python block code is
given it as a `Decimal`. There is no currency key: a pack that keeps one says so with a field and a check.

### Tables

A `table` field holds rows (line items), each the same few columns:

```toml
[[field]]
name = "lines"
type = "table"
min_rows = 1                    # optional
max_rows = 200                  # optional, at most 1000 (the default)
[[field.column]]                # a field without a path: string, text, number, decimal, boolean, date, datetime,
name = "item"                   # choice, member or reference; never another table
type = "reference"
target = ["Item"]
[[field.column]]
name = "amount"
type = "decimal"
scale = 2
required = true                 # in every row
```

Every row is checked against its columns ("Lines, row 2: What is required"); a cell that is no column is refused, and
decimal cells are rounded in every row. A column is read at `lines.*.amount`, so a reference column links its record
(by the column's name) as a reference field does. The form shows a grid, a row left blank being dropped; the record
page shows a table; a list, CSV or spreadsheet shows "2 rows" in its cell.

### Checks

A type's `[[check]]` is a rule every write of its records keeps, whoever writes (a person, the API, MCP, a workflow,
the hub); a pack upgrade does not run it, as it does not run `unique`:

```toml
[[check]]
when = "count(record.lines.*) > 0"                    # optional: the check applies while this holds
must = "record.amount == sum(record.lines.*.amount)"
message = "the amount is the sum of its lines"         # the refusal, in the pack's words
```

A check reads the record's fields by name (`record.<field>`, wherever its path puts it) and `today`. A move can
have checks of its own, kept by whoever makes the move, a person's step that would make it included (the step is not
finished):

```toml
[[lifecycle.transition]]
from = ["in-review"]
to = "approved"
[[lifecycle.transition.check]]
must = "count(parts.Point.*, it.record.status != 'closed') == 0"
message = "every review point is closed"
[[lifecycle.transition.check]]
must = "count(parts.Point.*, 'I checked it' in it.signed) == count(parts.Point.*)"
message = "every review point is signed"
```

Only a move's check reads its record's parts, `parts.<part type>.*` (a type whose `part_of` points here), each with
its `record` and `signed` (the meanings of its current signatures). They are read for the hub, whatever the mover may
see; the mover is told only the message. A workflow's edge into the state keeps the same checks.

A move can also record who made it and when (`stamp`):

```toml
[[lifecycle.transition]]
from = ["in-review"]
to = "approved"
stamp = ["reviewed_by", "reviewed_on"]   # a member field, a date or a time
[[lifecycle.transition.check]]
must = "record.reviewed_by != record.prepared_by"
message = "whoever prepared it does not approve it"
```

The move sets each stamped field in its own write: a member field to whoever made the move (for a move a workflow's
step makes, whoever did the step; none, for an automation of its own), a date to the practice's today, a time to now.
A signature that makes the move covers what it stamps. Nothing else writes a stamped field, not a person, a workflow
or a tool (only an import that brings a record in as it was), so it always names who made the move; it is off every
form, it is not asked for, and it is one value (not a list, not the state). The move's checks read the record as
stamped, so a check can keep two moves apart, as above.

Conditions, here and everywhere, add, subtract, multiply and divide exactly (`0.1 + 0.2 == 0.3`), in brackets too;
anything over a missing value or text is null, as is dividing by zero. `sum`, `min`, `max` and `count` read every
item of a list (`sum(record.lines.*.amount)`), or a value or condition over each, `it` being the item
(`sum(record.lines.*, it.qty * it.price)`); a sum of nothing is 0, and min or max of nothing is null. A list read
any other way is refused. `round(x, 2)` rounds half up. A date is written in quotes (`'2025-01-31'`); `today + 30d`
still moves one.

## Views

A view (`views/<id>.toml`) is a page of widgets on a 12-column grid. Whoever opens it sees only what they may see.

```toml
id = "orders"
title = "Orders"
nav = true                    # in the sidebar
roles = ["manager"]           # whose sidebar; none: everyone's

[[widget]]
kind = "line"                 # count, table, list, board, chart, line, tree, links
type = "Order"
measure = "sum(total)"        # sum, avg, min or max of a number; none: how many
x = "placed_on:month"         # a date, by month, quarter or year
series = "customer.owner"     # a line for each (at most 8; the rest are Other)
where = "record.status != 'void'"
grid = { x = 0, y = 0, w = 12, h = 4 }
```

| Key | Means |
|---|---|
| `kind` | `count` a number; `table`, `list`; `board` a column per value; `chart` a bar per value; `line` a number over time; `tree` an outline of a type with a parent; `links` places to go |
| `source` | `records` (the default), `items`, `runs` or `signatures` |
| `type` | the record type (for records: required; for sign-offs: those on that type, else on every type the reader may read) |
| `where` | a condition over `record` (records) or `row` (the rest) |
| `columns`, `sort`, `descending`, `limit` | a table's or list's columns, its order, and at most how many rows (25, up to 1000) |
| `group_by` | a board's columns, a chart's bars |
| `measure` | `sum(path)`, `avg(path)`, `min(path)` or `max(path)` (min and max also of a date); on a number, a chart or a line |
| `x`, `series` | a line's date and what it draws a line for |
| `workflow`, `role`, `status` | for work items and runs |
| `links` | `[{ label, href }]` for `links` |
| `grid` | `{ x, y, w, h }`; no two widgets overlap |

An unknown key is refused, with the nearest key it may mean.

A path is a field's name or path. A computed widget may follow one reference to a field of the record it names
(`customer.owner`, when `customer` is one reference to one type), and may group a date by `:month`, `:quarter` or
`:year` (in the hub's time zone). A board groups by its records' own fields, unbucketed.

A number or a chart with a measure, a chart grouped by a date's month, quarter or year or by a field a reference
reaches, every line, and every chart of sign-offs are worked out in the database, not counted from the rows a table
would list: the numbers cover every record the reader may read, not the first thousand. A sum, minimum or maximum of a decimal is exact (three 0.10s are 0.30); an average is rounded to the
field's places. A table's column (`lines.*.amount`) is summed in each record, then over the records; its average is
refused (of the lines, or of the totals?). A line fills a month with nothing in it with 0 for a count or a sum, and
leaves it empty for the others. A value its subject withheld from the reader is not added up, and a path the reader
may not see (a hidden field, a type they may not read) shows "needs access to …" in place of a number. A hub whose
records are kept in another store shows a note in place of these widgets.

Sign-offs (`source = "signatures"`) list each signature on a record and each signed gate or hold decision: `at`,
`by` (the person), `meaning`, `record`, `type`, `title`, `version`, `comment`, and, in a table or list, `current`
(whether it still covers the record as it is now; not filtered or charted on). A chart or line of sign-offs counts
them (`group_by = "by"`, `x = "at:month"`). The newest 5000 are read, and a record withheld from the reader as a
whole is left out, with a note saying how many.

## Skills

```toml
id = "return-prep"
title = "Prepare a personal return"
instructions = "Total the client's slips into a draft return. Do not file anything."

[[output]]
name = "income"
type = "number"
required = true
```

The instructions go to the model with the step's context, marked as data. The output fields become a JSON schema
for structured output, and the answer is validated against them.

## pack.py

Everything is optional. These module-level names are picked up:

| Name | What it is |
|---|---|
| `summarize(rec) -> str` | a one-line label for a record |
| `register(form) -> dict` | the subject record built from the registration form |
| `facts(rec) -> dict` | the headline facts on a subject's page; a value `"member:<id>"` shows that member's name |
| `ACTIONS = {"name": fn(engine, run, step, params, actor)}` | deterministic actions for automation steps |
| `PROVIDERS = {"name": fn(engine, run, step, member)}` | computed context (`provider:<name>`) |
| `GUARDS = {"skill-id": fn(values, ctxview) -> [problems]}` | extra checks on any answer for that skill, from a person or an agent. `ctxview` is what the answerer was shown (`context`, `run`, `step`, `item`), and `by`: who answers (`id`, and `kind`, `person` or `agent`), for a rule that holds an agent only. A prompt block is guarded by `"block:<id>"`, or by its `extends` skill's guard |
| `STANDINS = {"skill-id": fn(ctx) -> {answer, escalate, reason}}` | deterministic answers, so tests and a demo's past need no model (`ModelGateway(hub, scripted=True)`). `"block:<id>"` answers a prompt block |
| `demo(hub)` | demo records and runs, for `init --demo` |

Guards are where a practice's hard rules live. A guard is code, and a pack's code runs only as reviewed: while a
changed `pack.py` waits for its review and upgrade, the hub runs none of it, and every step whose skill or block came
with that pack or module takes no answer until it does (its guard cannot run, so the answer is not taken unchecked).
In the general-practice pack:

- a claim's amount must match the fee schedule;
- lab triage never downgrades the lab's own abnormal flag;
- a draft note may not claim to be signed.
