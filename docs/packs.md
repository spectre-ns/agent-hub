# Packs: setting up another kind of practice

A pack is a directory that makes a hub a particular kind of practice: a family clinic, a dental office, a law firm,
an accountancy. `packs/general-practice` is the full example. `tests/packs/accounting` is a minimal one, about 150
lines in all.

```
pack.toml          the practice, its subject, roles, data classes and grants, seed members, calendar
workflows/*.toml   the first version of each workflow (the practice changes them through change control)
skills/*.toml      what agents know how to do: instructions, and the shape of the answer
blocks/*.toml      the first version of each block: python, prompt or engine steps workflows can reuse
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

**Design the data classes first.** They decide what each agent can ever see. Give every agent the narrowest grants
that let it do its job. A scheduling agent needs demographics and appointments, nothing else.

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
| `GUARDS = {"skill-id": fn(values, ctxview) -> [problems]}` | extra checks on any answer for that skill, from a person or an agent. A prompt block is guarded by `"block:<id>"`, or by its `extends` skill's guard |
| `STANDINS = {"skill-id": fn(ctx) -> {answer, escalate, reason}}` | deterministic answers, so demos and tests need no model. `"block:<id>"` answers a prompt block |
| `demo(hub)` | demo records and runs, for `init --demo` |

Guards are where a practice's hard rules live. A guard is code, and a pack's code runs only as reviewed: while a
changed `pack.py` waits for its review and upgrade, the hub runs none of it, and every step whose skill or block came
with that pack or module takes no answer until it does (its guard cannot run, so the answer is not taken unchecked).
In the general-practice pack:

- a claim's amount must match the fee schedule;
- lab triage never downgrades the lab's own abnormal flag;
- a draft note may not claim to be signed.
