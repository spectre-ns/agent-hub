# Writing workflows

A workflow is how the practice does one piece of work, written as TOML. Steps are done by **roles**, never by named
people. Whether a person or an agent does a step depends on who holds the role and on the rules below.

```toml
id = "prescription-renewal"          # lower-case letters, digits and dashes
title = "Prescription renewal"
record = "Patient"                   # what a run is about: the pack's subject type, or none
summary = """A patient asks for a renewal..."""

[trigger]
starters = ["front-desk", "patient-portal"]   # roles that may start it by hand or through the API
event = "renewal.requested"                   # and/or: an event that starts it (POST /api/events)

[[input]]                            # what the starter gives; same field types as step fields
name = "medication"
type = "string"
required = true
```

## Steps

```toml
[[steps]]
id = "prepare"
title = "Prepare the renewal"
kind = "task"                        # task | gate | automation
role = "clinical-assistant"
after = []                           # steps that must finish first (default: none, so it starts at once)
when = "input.urgency == 'routine'"  # optional: otherwise the step is skipped
risk = "medium"                      # low | medium | high (see "Risk" below)
skill = "renewal-prep"               # what an agent uses to do it; its output fields come from the skill
context = ["record:clinical", "record:visits", "input"]
```

### task

Someone holding the role fills in the step's `fields`, or the skill's output fields. A person sees a form and an agent
answers with JSON. Both answers are validated the same way.

- `fields`: the output, as a list of `[[steps.fields]]` tables. A task needs fields, or a skill that defines them.
- `review_of = "draft"`: the person sees an earlier step's output as a draft, prefilled into the form.
- `persons_only = true`: agents may not take it.
- `instructions`: shown to whoever does it.

### gate

Someone decides by choosing an outcome. **The decision is an electronic signature** with the step's `meaning`.

```toml
kind = "gate"
outcomes = ["renew", "decline", "see-first"]
meaning = "I have reviewed this renewal and I am responsible for the decision"
signer = "person"                    # person: never an agent | any: agents too, within agent_criteria
agent_criteria = "code.amount <= 80" # only with signer = "any": an agent may decide while this holds
not_by = ["code"]                    # whoever did these steps may not decide this one
```

### automation

A deterministic action. No model runs in an automation.

```toml
kind = "automation"
action = "record.create"             # record.create | record.update | message.send | workflow.start | context.set,
                                     # or an action the pack adds (appointment.book)
[steps.params]
type = "MedicationRequest"
[steps.params.data]
subject = { reference = "{{run.record}}" }
note = [{ text = "{{decide.comment}}" }]
```

`{{path}}` in params is filled from the run: `run.record`, `input.<name>`, `<step>.<field>`, `<step>.by` and
`<step>.outcome`. Values are substituted as data and never evaluated.

### wait

Does nothing until what it waits for comes; one of:

```toml
kind = "wait"
until = "record.status == 'approved'"   # checked whenever a record changes, and by the clock's tick
timeout = "2d"                          # optional, with until only: then it ends anyway, as timed out
# or
for = "3d"                              # a duration: 30m, 4h, 3d, 1w
# or
at = "{{input.appointment}} - 1d"       # one placeholder, optionally moved by + or - a duration
```

`at` takes a date (`2026-12-01`: its midnight), a wall time (`2026-12-01T09:30`) or an instant (`...Z`, or with an
offset). Dates and wall times are the practice's, in its time zone; durations are counted in real time, so a clock
change does not move them. A value that is not a date or a time fails the step and asks the escalation role.

The output is `waited_for`; a wait with `timeout` also has `timed_out` (true or false), so a later step can say
`when = "chase.timed_out"`. A timeout is on the track as `step.timed_out`.

### Timing: deadlines and schedules

```toml
[deadline]
within = "5d"                    # a run still open after this is late
escalate_to = "practice-manager" # told once (else the practice's escalation role)

[trigger]
schedule = "weekly mon,wed,fri 08:00"
```

A late run is flagged once, `run.deadline_missed` on the track, and keeps `deadline_at` and `deadline_missed_at` (a
run that finishes late before the tick sees it is marked too). Schedules: `hourly`, `every 15m` to `every 24h`
(stretches counted from midnight), `daily 07:00`, `weekdays 07:00`, `weekly mon 08:00` or `weekly mon,wed,fri 08:00`,
`monthly 1 09:00` (day 1 to 28) or `monthly last 17:00`. Each fires once per slot, by the practice's clock.

## Blocks: reusable, parameterised steps

A block is a step the practice builds once (on **Blocks** in the sidebar) and uses in any workflow. A step names it
with `block` and fills its inputs with `params`; the block's outputs become the step's output, so later steps read
`<step>.<output>` as usual.

```toml
[[steps]]
id = "assess"
kind = "automation"
block = "missed-appointment-fee"      # a python or engine block: an automation step
[steps.params]
notice_hours = "{{input.notice_hours}}"
fee = 50

[[steps]]
id = "draft"
kind = "task"
role = "clinical-assistant"
block = "patient-reply-draft"         # a prompt block: a task step an agent carries out
context = ["input.question"]          # whatever the params read must be in what the step reads
[steps.params]
question = "{{input.question}}"
tone = "warm"
```

| Runtime | What it is | Who may write it |
|---|---|---|
| `python` | `def run(input) -> dict`, run in a sandbox: its own process, an empty environment, 5 s wall clock, 2 s CPU, 512 MB, no files, network or processes, a short list of standard modules. Its SHA-256 is in every `action.ran` event | people only |
| `prompt` | an injected prompt that becomes the agent's instructions; the answer is checked against the block's outputs. An agent takes it only if its profile lists the block (or the skill the block `extends`) | anyone, agents included |
| `external` | runs on an automation engine under the hub (`engine = "webhook"` or `"kestra"`, `target` = what to run there). The engine sees the inputs only | people only |

- **Plain params in a prompt block are written into the prompt; params read from the run are not.** The prompt says
  `[question: see block-input.question in the context]` and the value travels as data (`block-input.question`), so a
  patient's words can never become an instruction.
- A block is proposed and approved like a workflow: a different policy owner approves and signs it, after a warning
  about what it changes (new Python code, a new prompt, a new engine call) and which workflows use it.
- A run keeps the block versions that were active when it started (`run.blocks`).
- A step that uses a block takes no `action`, `skill` or `fields`: those come from the block. A failing block fails
  the step and sends a notice to the practice's escalation role, like any automation.

## Context: what a step reads

| Entry | Gives |
|---|---|
| `record:<class>` | the run's subject's records in that data class (e.g. `record:clinical`) |
| `input` / `input.<name>` | the run's inputs, or one of them |
| `<step>` | an earlier step's whole output |
| `<step>.<field>` | one field of it |
| `provider:<name>` | computed data: `free-slots`, `members`, or a provider the pack adds |

**Data classes travel with data.** A member may take a step only if they may read every class it reads:

- the classes in its `record:` entries;
- for a `<step>` entry or a `review_of`, the classes *that* step read. A draft note is clinical wherever it goes.

Naming a single field (`edit.diagnosis_codes`) passes on that field only. It is a deliberate disclosure, written in
the approved workflow, which is how billing gets diagnosis codes without the note.

On run pages and in the track, a member who cannot read a step's classes sees who did it, when and why, but not the
content.

## Conditions

`when` and `agent_criteria` use a small expression language. It has no `eval`.

```
intake.complete == true
billing.amount < 200 and billing.codes != []
review.outcome in ['approve', 'approve-with-changes']
not sign.outcome == 'decline'
```

Paths look up values in the run, and a missing path is `null`. The operators are `== != < <= > >= in and or not` and
parentheses.

## Fields

The types are `string`, `text`, `number`, `boolean`, `date`, `datetime`, `choice` (with `choices = [...]` or
`choices_from = "<context entry>"`), `list` and `member`. The options are `required`, `default`, `help`, `min`, `max`
and `max_length`.

## Risk

An agent may take a step only if the step's `risk` is within the agent's `max_risk`.

| risk | when an agent does it |
|---|---|
| low | it goes ahead |
| medium | it goes ahead, and the agent's sponsor gets a notice |
| high | it is **held**. The sponsor sees the answer and approves it (it is applied, and signed) or rejects it (the item goes to people with the reason) |

## Agents and escalation

An agent can take a step only when all of these hold:

- it holds the role;
- its sponsor is active;
- the step has a skill that is in the agent's profile;
- the step allows agents;
- the risk is within the agent's limit;
- the data-class rules allow it.

An agent that is unsure, or whose answer fails validation, gives the item back with a reason. The item is then for
people only.

## Change control

1. Propose a change: **Workflows → (workflow) → Edit workflow**, or `POST /api/workflows` with `{source, note}`.
   `POST /api/workflows/check` with `{source}` reports problems without proposing anything.
   The hub checks it against the pack's roles, skills, actions and data classes, and lists every problem.
2. A *different* person who is a policy owner approves it. The approver sees the diff and the **agent impact**: steps
   agents may now do that they could not before.
3. New runs use the new version. Runs already started finish on their own version.
