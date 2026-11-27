# Writing workflows

A workflow is how the practice does one piece of work, written as TOML. It is about one **record** (a renewal
request, a lab result, a job), and it is written as that record's **states** (where the record stops, and the work
done there) and the **edges** between them (the ways on, and the automations that run on the way), the way an issue
tracker writes a workflow. Work is done by **roles**, never by named people. Whether a person or an agent does it
depends on who holds the role and on the rules below.

> A list of `[[steps]]` with `after` is not a way to write a workflow, and is refused wherever one is read. Write states
> and edges.
> The hub still compiles a workflow to steps internally (`practicehub/states.py`, `compile_states`), so runs, their
> history, analytics and the BPMN export read as before.

```toml
id = "prescription-renewal"          # lower-case letters, digits and dashes
title = "Prescription renewal"
record = "MedicationRequest"         # the record a run walks (required: see "The record" below)
summary = """A patient asks for a renewal..."""

[trigger]
starters = ["front-desk", "patient-portal"]   # roles that may start it by hand or through the API
event = "renewal.requested"                   # and/or: an event that starts it (POST /api/events)

[new_record]                         # optional: started on what the record is about (a patient), the run makes it
status = "draft"
medication_text = "{{input.medication}}"

[[input]]                            # what the starter gives; same field types as work fields
name = "medication"
type = "string"
required = true

[start]
to = "requested"

[[state]]
id = "requested"
label = "Requested"
tone = "active"
[[state.work]]
id = "decide"
title = "Decide on the renewal"
kind = "gate"
role = "physician"
signer = "person"
meaning = "I have reviewed this renewal and I am responsible for the decision"

[[edge]]
id = "renew"
from = "requested"
to = "renewed"

[[edge]]
id = "decline"
from = "requested"
to = "declined"
[[edge.do]]
id = "tell-declined"
action = "message.send"
[edge.do.params]
to = "{{run.subject}}"
body = "Your renewal was declined: {{decide.comment}}"

[[state]]
id = "renewed"
tone = "done"

[[state]]
id = "declined"
tone = "done"
```

### One open run at a time

```toml
[trigger]
one_open_per = "record"           # or "input.<name>": one open run per value
allow_duplicate = true            # optional: the starter may confirm "Start another anyway"
```

With `one_open_per`, starting the workflow by hand (the start form, a calendar slot, the API) while another open run
of it has the same value (the same record, or the same value of that input) is refused in the same transaction that
would start it, so pressing Start twice, or two people taking the same call, starts one. The refusal names the open
item: its key with a link, who holds it and the work it is at (to whoever may see it). Only with `allow_duplicate =
true` does the form offer **Start another anyway** (the API: `"allow_duplicate": true`); otherwise there is just the
link. An event or a schedule always starts its run: a request that came in is never dropped. An empty value is never
a duplicate. Forms also post once: a second press while one is being sent does nothing.

### Cancelling a run

Whoever started a run, or a policy owner, may cancel it. Two settings change what a cancel does:

```toml
cancel_by = ["physician"]            # once work for this role is claimed, only this role or a policy owner cancels

[[on_cancel]]                        # run in the transaction that cancels the run
id = "tell"
title = "Tell the patient and the physician"
action = "message.send"
when = "cancel.reason != ''"         # optional
[on_cancel.params]
to = "{{run.subject}}"
body = "Your renewal was cancelled: {{cancel.reason}}"
```

- `cancel_by` lists roles. When someone claims work for one of them, cancelling passes to that role. From then on,
  only someone who holds it, or a policy owner, may cancel the run. Before anyone has claimed such work, the usual
  rule applies. `Engine.cancel_refusal(actor, run)` gives the reason in words, or None, so a page can leave out a
  Cancel button that would be refused.
- `[[on_cancel]]` actions are written like an automation's `action` and `params`. Their placeholders can also read
  `cancel.reason` and `cancel.by` (the canceller's name). They run inside the cancel. If one fails, the cancel is
  refused ("it cannot be cancelled now: 'Tell the patient' did not go through (...)") and nothing of it is kept, so
  nobody is left untold.
- The run's record goes where its type's `abandoned` says for the state it is in (back to a draft, closed, or it
  stays where it is), unless other work on it is still open. See "What the type says, and what its workflow says".

## States and edges

`packs/general-practice/workflows/lab-result-review.toml` and `patient-follow-up.toml` are worked examples.

- **`[start]`** is the edge a record enters by: `to` (the state it enters), an optional `label`, and automations of
  its own (`[[start.do]]`). A run that starts on its record again (a corrected result) enters by it too.
- **A state** (`[[state]]`) is where the record waits. It takes `id`, `label`, `tone` (`active`, `attention`,
  `done`...), `help` and `work`. Its id is a value of the record type's state field; its label and tone are what the
  record page shows.
- **Its work** (`[[state.work]]`) is what is done while the record is there. It is either **a person's** (`task` or
  `gate`: an agent may do a task) or **automated** (`automation` or `wait`), never both on one state. Several pieces
  of work may each have their own `when`: the ones that hold are done (a critical result's review is urgent, any
  other is not). Work takes any key a piece of work can have (see [Work and automations](#work-and-automations)),
  except `after`, `outcomes` and `state`, which the workflow works out from its states and edges.
- **An edge** (`[[edge]]`) is a way out of a state: `id`, `label`, `from`, `to`, `when`, `help` and `do`. **People
  choose the edge, never the state it leads to.**
  - From **a person's state** with several edges out, the work's outcomes are the edge ids, in order, shown as one
    button each, labelled with the edge's `label`. Later work reads the choice as `<work>.outcome` (the edge id). An
    edge's `when` decides whether it is offered at all: "File as normal" only when the lab, the sort and the range
    check all call a result normal. With one edge out, the record takes it once the work is done. A gate decides
    between edges, so a state with a gate has more than one edge out.
  - From **an automated state**, the record leaves by itself, by the first edge whose `when` holds once the work is
    done. Only the last edge may go without a `when`, as the otherwise. There an edge's `when` routes the record.
    The choice is made once, before any edge moves the record (by a step `<state>--route`, whose output `edge` the
    edges' steps read), so an edge whose automations change what the conditions read never sends the record down a
    second edge.
- **Edge automations** (`[[edge.do]]`, any automation) run as the record moves along the edge. Each waits only for
  the from-state's work and for an earlier automation on the same edge whose output it reads (`{{x.y}}`, or `x.y` in
  its `when`), so one that fails holds up only what needs it. The record moves once they have all run. An automation
  may have its own `when`: it runs only on this edge, and only then.
- **A diversion** is an edge automation with its own `to` and `when`: when it runs, the record lands in that state
  instead of the edge's `to` (where the last diversion that ran sent it). To always send it there, make it the
  edge's own `to`.
- **A workflow that only acts** (a sweep that must never move its record) has a `[start]` with automations
  (`[[start.do]]`) and no `to`, and no states or edges: the run ends once they are done.
- **A state with no edge out** is where the record ends. Every state with work needs an edge out, and an edge in.
- **A follow-up that outlives the decision** (calling the patient, booking a visit) is a record of its own, started
  by an edge's `workflow.start`, so the source record can close. Do not keep a record open only because something it
  asked for is still being done.

```toml
[start]
to = "checking"
label = "Filed by the lab"

[[state]]
id = "checking"                      # automated: the range check, then out by the first edge whose when holds
label = "Checking"
[[state.work]]
id = "check"
kind = "automation"
block = "lab-range-check"

[[edge]]
id = "critical"
from = "checking"
to = "received"
when = "check.band == 'critical'"
[[edge.do]]                          # runs on the way: the physicians are paged
id = "page"
action = "message.send"
[edge.do.params]
channel = "page"
to = "role:physician"
body = "Critical result for {{run.subject}}. Review now."

[[edge]]
id = "to-sort"                       # the otherwise: no when
from = "checking"
to = "sorting"

[[state]]
id = "sorting"
label = "To sort"
[[state.work]]
id = "sort"
kind = "task"
role = "clinical-assistant"
skill = "lab-triage"

[[edge]]
id = "sorted"
from = "sorting"
to = "received"

[[state]]
id = "received"
label = "In review"
tone = "attention"
[[state.work]]
id = "review"
kind = "gate"
role = "physician"
signer = "person"
meaning = "I have reviewed this result"

[[edge]]
id = "file-normal"                   # a button only while this holds
from = "received"
to = "closed"
when = "sort.category == 'normal' and check.band == 'normal'"

[[edge]]
id = "call-patient"
from = "received"
to = "closed"
[[edge.do]]                          # the call is a follow-up of its own: the result closes
id = "follow-up"
action = "workflow.start"
[edge.do.params]
workflow = "patient-follow-up"
record = "{{run.subject}}"
[edge.do.params.input]
what = "Call about the result"
because_of = "{{run.record}}"

[[state]]
id = "closed"
label = "Closed"
tone = "done"
```

### The record

A workflow is about a record (`record = "<Type>"`). The states form moves that record:

- **A type with a lifecycle**: each state id is a value of its state field. The type lists the values the field
  takes, the moves people make **by hand** (corrections, putting a job on hold) and where a record goes when its
  work is cancelled (`abandoned`). The workflow's edges become the type's moves "by workflow"
  (`practicehub/catalog.py`, `_with_workflow_moves`), each from the state it leaves to every state it can land in,
  so nobody makes those moves by hand while the workflow walks the record. The workflow's state labels and tones
  are the type's. See "What the type says, and what its workflow says" below.
- **Where the state field is a fixed standard vocabulary** (a FHIR status) that cannot say every stop, the type
  gets a separate stage field as its lifecycle field (Observation's `review`, Task's `stage`), and edge automations
  set the standard status with `record.update`.
- **A type with no lifecycle** (no `[lifecycle]`, a patient, say) may still be walked: the run keeps where the
  record is (`run["at"]`) instead of moving it. So does a state the record's field has no value for (a stop only the
  run knows, such as a final sign-off that moves nothing): the field stays where it was. Prefer a value: then the
  record itself says where it is. Every pack and module the hub ships lists each of its workflows' states on the
  type.
- **A workflow about no record cannot be written as states and edges**: the moves along its edges need a record
  ("state moves the run's record; the workflow is about none"). A scheduled workflow names its records with
  `trigger.for_each` (one run per record).

### What the type says, and what its workflow says

A record type that names its own workflow (`workflow = "<id>"` in the type) and that workflow each say their part
once. The hub checks the pair (`catalog.lifecycle_fit`) when either is proposed or approved, against the other as
it is in force, and when a pack or module installs both.

| The type says | The workflow says |
|---|---|
| the values its state field takes (`choices`), every state of the workflow among them | its states, each with its `label` and `tone` |
| the moves people make by hand (`[[lifecycle.transition]]`), and what each asks, requires or signs | the moves it makes: its edges |
| where a record goes when its work is cancelled (`abandoned`) | |
| words and a tone for a value the workflow has no state for (`lifecycle.labels`, `tones`) | |

```toml
[lifecycle]                              # in the record type
field = "status"
initial = "draft"
abandoned = { in-review = "draft", sent = "sent" }   # cancelled in review: back to a draft; once sent: it stays
[[lifecycle.transition]]                 # a move by hand
from = ["approved"]
to = "draft"
label = "Revise"
```

- **`abandoned`** (state = the state it goes to) is itself the move a record makes when the run about it is
  cancelled: no transition is written for it, and a `by = "workflow"` row that only repeats it is refused. A state
  named as its own target stays where it is. A state that only the workflow moves a record out of, counting the
  type's moves and the workflow's edges together, must be listed, so a cancelled run never strands a record where
  nobody can move it.
- **A state is named once.** The field that holds the state takes no `labels`, and the type gives no label or tone
  to a state its workflow has.
- **A move is written once.** The type does not repeat a move one of its workflow's edges makes. A transition
  `by = "workflow"` is still the type's to write for a move no edge of its own workflow makes (another workflow's
  run retiring the policy it replaces, a signature's `moves_to`), for a move from a value the workflow has no state
  for, and where the type attaches `asks`, `requires` or `sign` to the move: an edge that makes the same move then
  goes through that transition.

**The order to change them in.** Add to the type first, then to the workflow: a new state's value goes on the
field (without it the run alone keeps that stop), and a state that only the workflow will leave gets its `abandoned`
entry, before the workflow version that uses them is approved. Remove from the workflow first, then from the type:
a value goes once no state of the workflow in force is it. A label, a tone or a `by = "workflow"` row that the
workflow is about to say itself comes off the type first.

### How it runs

The workflow is compiled to ordinary steps (`practicehub/states.py`): each piece of work is a step with its id; each
edge automation is a step after the from-state's work, run when that edge was chosen; each edge ends with a step that
moves the record (its id is `<edge>--to`, which no author writes). So placeholders read work and automations by their
ids (`{{decide.by}}`, `{{review.outcome}}`, `{{check.band}}`), and the run's history, analytics and BPMN export read
as any workflow's. Keep ids stable: other workflows, blocks and tests refer to them.

Workflows are written only this way, and every version is read by the same rules, whenever it was approved. A list of
`[[steps]]` is refused wherever a workflow is read. A version in force that a newer rule refuses is not read: it is
left out of the workflows and starts nothing, its page and its runs say why, and the clock goes on for everyone
else's work. The way back is to approve a version that passes; proposing the refused text again is refused too.

### A module adding to another module's workflow

A module may add to a workflow another module installed (`extends/workflows/<id>.toml` in the module), but never
change or remove what that workflow says:

- new states (`[[state]]` with an `id`) and new edges (`[[edge]]` with an `id`);
- work on an existing state: `[[state]]` with `state = "<id>"` and its `[[state.work]]`;
- automations on an existing edge: `[[edge]]` with `edge = "<id>"` and its `[[edge.do]]`.

```toml
# modules/dms-notify/extends/workflows/dms-review.toml
[[edge]]
edge = "approve"            # the approve edge dms-review has
[[edge.do]]
id = "tell-owner"
action = "message.send"
after = ["publish"]         # once the edge's own publish has run
[edge.do.params]
to = "{{record.owner}}"
subject = "Reviewed"
body = "Your document was reviewed."

[[edge]]                    # a new edge, from a state the workflow has to one the module adds
id = "hold"
from = "in-review"
to = "on-hold"
```

The additions are merged into the workflow's authored text (between `# >>> added by the <module> module` and
`# <<< <module>`) before it is compiled, so the result compiles and runs like any workflow; taking the module out
takes them out again, and the practice's own later versions of the workflow keep them on. Anything else is refused
when the module is installed: keys of the workflow's own (`title`, `[start]`, `[[steps]]`), a state or edge id the
workflow already has, work or an automation id it already has, and anything on `state = ...` or `edge = ...` but its
work or automations (a label, an edge's `from`, `to` or `when`). A practice changes or removes what another part owns
through change control, not by a module.

### What the record page shows of a move

A move is one edge taken in one run (`Hub.record_activity`, `practicehub/activity.py`). The record's page shows what
each move ran and started, in the edge's own words, so write automation titles a person can read ("Page the
physicians", "Ask a nurse to call the patient"):

- **The work section** has a block for the latest move (a person's, with any moves that followed on from it by
  themselves, each as "Then, “<edge label>”"): every automation on the edge that was taken, with its outcome
  (done, did not go through, waiting on the one that did not, or a message's status: waiting to be sent, sent, not
  delivered), and each record it started with its state and who holds its work now. Automations on edges not
  taken, and those skipped by their own `when`, are not listed. The block stays on a closed record while what it
  started is open, refreshes itself while anything is still going on (`GET /records/<Type>/<id>/activity`, with a
  Refresh link without JavaScript), and folds to one line once settled.
- **History** tells the record's whole journey, nested (`Hub.record_journey`, `practicehub/journey.py`). Across
  the top are the stages it has been in, in order. Under that is one block per stage: when it got there and how
  long it stayed; the work done there, each line marked by who did it (a person, an agent, a service, or
  automation: the legend's marks); and each move out of it (the edge's label, or the type's own words for a move
  made by hand) with its automations nested under it. On the rail, each stage carries the mark of whoever put the
  record there. Open work says who it now waits on, and who passed it up or gave it back, and why. A move by hand
  that the workflow then followed carries what followed ("Then, by automation"). Automations of the taken edge that were not needed, the other ways out that were not taken, and work
  not needed in a stage each fold to one line; nothing is listed of a state the record never reached. A move that
  did not go through stays in the stage it was tried from, with what went wrong and each retry. The section
  refreshes itself while a job is in the queue or under way, an agent has work open, or a message is on its way
  (`GET /records/<Type>/<id>/journey`, answered `304` until something changes; a Refresh link without JavaScript).
- **View workflow** opens the type's states and moves as a map (`practicehub/web/flowmap.py` places it: no arrow
  crosses a box). Drawn over it is the way this record came: its moves numbered in order and named, the states it
  has been in solid and the rest faint, where it is now ringed, and the moves a person can make from here in the
  accent colour. Where the workflow starts a record is one Start, and an ending many states can reach (cancelled,
  entered in error) is said on each of them in small words, not drawn from each. Folded below: every move in
  words, and the workflow stage by stage with the stages it has been in and the edges it took marked.
- **Started from this …** lists the records a move started (`workflow.start`, `record.create`) beside the
  sub-records `record.child` made, and the started record's page says "Started by “<edge label>” on <record>".

What a member may not see stays out: an automation they may not see folds to "a step you may not see", work they
may not see folds to a line with its data class and a count, and a started record of a type they may not read keeps
its kind, state and holder but not its title or a link. Which way a record left a state is what the work there
decided, so who may not see that work is told where the record went (that is the record's own history) but not the
edge's name, who chose it, or the ways out it was chosen over (`Viewer.edge_visible`).

## Work and automations

Every piece of work on a state, and every automation on an edge, takes these keys:

```toml
[[state.work]]
id = "prepare"
title = "Prepare the renewal"
kind = "task"                        # task | gate (a person's) | automation | wait (automated)
role = "clinical-assistant"
when = "input.urgency == 'routine'"  # optional: otherwise this work is not done here
risk = "medium"                      # low | medium | high (see "Risk" below)
skill = "renewal-prep"               # what an agent uses to do it; its output fields come from the skill
context = ["record:clinical", "record:visits", "input"]
instructions = "Check the last visit and the dose."
```

An edge automation (`[[edge.do]]`) is always `kind = "automation"` (it may be left out) and takes `action` or
`block`, `params`, `when`, `for_each`/`where`, and `to` for a diversion. A person's work never goes on an edge.

### One item walks the run

A run has **one work item**, with one key (like `HFM-12`) and one history, from start to finish. When a person's
work starts, the item moves to it: it is reassigned to that work's role (or `assign_to`, or the person a directive
names), shows the work's title, fields and edges, and its title stays the run's subject. Each piece of work the item
goes through is kept in its **history**, in order: the work, who it was for, when it began, was taken and was done,
by whom, which edge it took, and whether it timed out or was escalated. `due`, `escalate_to` and timers count from
when the item reached the current work.

While an automation runs or a wait waits, the item says so ("waiting: Send the reminder", "waiting until 9 Mar
09:00") and is in nobody's to-do list. Work that runs side by side (several pieces on one state whose `when` all
hold), and a task done once per person (`for_each = "{{input.reviewers}}"`), are **child items**: each has its own
key, is listed on the run's item, and closes back into it (its work joins the history). The run's item says
"waiting on 1 of 2" until they are done. Notices are separate items, as before.

Separation of duties (`not_by`) and `meanings_if_did` read the item's history: who did a piece of work stays who did
it after a reassignment or a take-over.

### Work goes to a person, chosen where it is set going

A role says who **may** do a piece of work. It is not who the work goes to. Whoever makes the transition that sets
people's work going chooses the one person who gets it. That happens in the decision's dialog, in a task's
"Done…"/outcome dialog, or on a record move's screen. Each such piece of work adds a required "Who does …"
choice, which offers the active people who hold its role.

The work counted is:
- the work that follows in the same run;
- the work waiting in the first state of a run that an automation on the edge starts (`workflow.start`: "Call the
  patient" asks which nurse calls);
- for a move, the work waiting in the state the record moves into.

The page refuses the transition until each piece of work has a person. The person is kept on the run until that work
starts, even if an agent's step or a wait comes first, and is used once. A later pass asks again.

Some work is not asked about:
- work that names its person (`assign_to`);
- work done once per person (`for_each`);
- work an agent of its role may take;
- work whose role nobody holds (that is reported as before).

Over the API, `assign` (`{"<workflow>/<work>": "<member>"}`) on `complete`, `decide` and `move_record` is checked
the same way, but is optional.

### Moving the record on its page

People do the work on the record's own page, as in an issue tracker: the open work's edges are its buttons, and
choosing one moves the record along it. The record type's own transitions are the moves people make by hand, and
say what such a move needs:

```toml
[[lifecycle.transition]]           # in the record type
from = ["doing"]
to = "on-hold"
label = "Put on hold"
asks = ["reason"]                  # the move's screen asks for these, and they are written with the state, in one go
requires = ["reason"]              # these must hold something once it has moved; the type's field rules check them
# sign = "Approved"                # the mover signs with this meaning (one of the type's [[signature]] meanings)
```

A move that leaves a `requires` field empty is refused, naming the field ("to move a job to 'on-hold', fill in
Reason"). This applies to every move: by hand, by a workflow, or by a signature. A move with `sign` is made only by a
person's signature with that meaning. An agent never signs, and neither does a plain edit or a workflow.

`Hub.move_record(actor, ref, to, values, signature)` makes any move. It is refused, in words, unless the actor may
make that move now:

- the transition's `by` allows them (a move "by workflow", one of the workflow's edges, is the workflow's to make);
- the signature is a person's, by a role the signature allows;
- they hold the role of the open work of the record's own workflow;
- nobody else holds that work. If someone has claimed it or it is assigned to them, the refusal names that person.

A correction open from every state (`from = ["*"]`) stays anyone's who may write the record. A person's work, or a
wait, may list `moves = ["<state>", ...]`: the states a person may move the record to by hand while it is open
(starting the work, stepping out of a visit and back).

**A write that is not a move can start the work again.** A record type names its workflow with
`workflow = { id = "...", when = "...", again = "..." }`. Take a write that does not move the record, such as a lab
writing `status = "corrected"` on a result already reviewed. If `again` holds after that write and did not hold
before it, the type's workflow starts about the record again, entering by `[start]`. The last run becomes its
previous run. This happens only when no run of the workflow is walking the record. `when` does not apply here, and a
write that leaves `again` as it was starts nothing.

`Hub.transition_screen(actor, ref)` returns two lists for the record page:

- The moves the actor may make now. Each has its label, the fields it asks for (with their definitions and values),
  what it requires, the signature it asks for, and whether it is the forward move.
- The moves they may not make, each with the reason in words.

`Hub.record_timeline(viewer, ref)` tells the record's history in words, oldest first. It covers:

- the record's edits;
- its moves: who made each, when, the values it wrote and the signature it carried;
- its runs' work: started, done by whom, decided, skipped or waiting.

What the viewer may not read is folded, one line per hidden stretch ("3 clinical steps"). (`Hub.record_history` stays
the list of versions that pages and exports read.)

### task

Someone holding the role fills in the work's `fields`, or the skill's output fields. A person sees a form and an
agent answers with JSON. Both answers are validated the same way. With several edges out of its state, the task ends
with a button per edge (unsigned).

- `fields`: the output, as a list of `[[state.work.fields]]` tables. A task needs fields, or a skill that defines
  them.
- `review_of = "draft"`: the person sees earlier work's output as a draft, prefilled into the form.
- `persons_only = true`: agents may not take it.
- `people_role`: when an agent's work here goes back to people (its sponsor rejected it, or the agent gave it up),
  it goes to this role.
- `assign_to = "{{run.record.generalPractitioner}}"`: offered first to that member (still only if eligibility
  allows).
- `requires_links = ["report"]` (tasks and gates): the record must have these links before the work is done.
- `instructions`: shown to whoever does it.

### gate

Someone decides by choosing an edge out of the state. **The decision is an electronic signature** with the work's
`meaning`.

```toml
[[state.work]]
id = "decide"
kind = "gate"
role = "physician"
meaning = "I have reviewed this renewal and I am responsible for the decision"
meanings = { decline = "I decline this prescription renewal" }   # by edge id: what signing that edge says
signer = "person"                    # person: never an agent | any: agents too, within agent_criteria
agent_criteria = "code.amount <= 80" # only with signer = "any": an agent may decide while this holds
not_by = ["code"]                    # whoever did this earlier work may not decide (also "run", "record.editors")
meanings_if_did = { edit = "I am the author of this note and it is accurate" }
```

The gate's choices are its state's edges (`renew`, `decline`, `see-first`), so `meanings` is keyed by edge id.
`meanings_if_did` names earlier work: when the signer is the member who did it, by the item's history, the signature
says that meaning; anyone else signs the work's `meaning` (or the edge's, from `meanings`). The signer sees the
meaning that applies to them before they sign, and the track keeps the one they signed.

### automation

A deterministic action. No model runs in an automation. It is an automated state's work, or an edge's automation.

```toml
[[edge.do]]
id = "write"
action = "record.create"             # record.create | record.update | message.send | notice.send | workflow.start |
                                     # clock.now | context.set, or an action the pack adds (appointment.book)
[edge.do.params]
type = "MedicationRequest"
[edge.do.params.data]
subject = { reference = "{{run.subject}}" }
note = [{ text = "{{decide.comment}}" }]
```

`{{path}}` in params is filled from the run: `run.record`, `run.subject`, `input.<name>`, `<work>.<field>`,
`<work>.by`, `<work>.outcome` (the edge chosen) and `record` (the record as it is now). Values are substituted as data
and never evaluated.

An automation with `for_each = "{{record.documents}}"` runs its action once per item of the list, in order, all or
nothing; `{{each}}` is the item and `each_record` the record it points at. `where = "each_record.status ==
'approved'"` keeps only some items. Its output is `each` (one output per item that ran) and `passed_over`.

### wait

Automated work that does nothing until what it waits for comes; one of:

```toml
[[state.work]]
id = "chase"
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
change does not move them. A value that is not a date or a time fails the work and asks the escalation role.

With `for_each`, it waits until `until` holds for every item. Its `role`, if given, gets a notice saying what the
work waits for. The output is `waited_for`; a wait with `timeout` also has `timed_out` (true or false). The state's
edges route on it, and a timeout is on the track as `step.timed_out`:

```toml
[[edge]]
id = "gave-up"
from = "chasing"
to = "to-call"
when = "chase.timed_out"

[[edge]]
id = "came-in"
from = "chasing"
to = "to-file"
```

### Timing: due dates, deadlines and schedules

A task or gate may have `due = "2d"` (or `"4h"`, `"30m"`): when it passes, the item is flagged overdue, and with
`escalate_to = "<role>"` a notice goes to that role. `priority = "urgent"` puts the work at the top of every queue.

```toml
[deadline]
within = "5d"                    # a run still open after this is late
escalate_to = "practice-manager" # told once (else the practice's escalation role)

[trigger]
schedule = "weekly mon,wed,fri 08:00"
for_each = "Job"                 # one run per record of the workflow's record type
where = "record.status == 'open'"
```

A late run is flagged once, `run.deadline_missed` on the track, and keeps `deadline_at` and `deadline_missed_at` (a
run that finishes late before the tick sees it is marked too). Schedules: `hourly`, `every 15m` to `every 24h`
(stretches counted from midnight), `daily 07:00`, `weekdays 07:00`, `weekly mon 08:00` or `weekly mon,wed,fri 08:00`,
`monthly 1 09:00` (day 1 to 28) or `monthly last 17:00`. Each fires once per slot, by the practice's clock.

## Blocks: reusable, parameterised work

A block is work the practice builds once (on **Blocks** in the sidebar) and uses in any workflow. Work or an edge
automation names it with `block` and fills its inputs with `params`; the block's outputs become its output, so later
work reads `<id>.<output>` as usual.

```toml
[[edge.do]]
id = "assess"
block = "missed-appointment-fee"      # a python or engine block: an automation
[edge.do.params]
notice_hours = "{{input.notice_hours}}"
fee = 50

[[state.work]]
id = "draft"
kind = "task"
role = "clinical-assistant"
block = "patient-reply-draft"         # a prompt block: a task an agent carries out
context = ["input.question"]          # whatever the params read must be in what the work reads
[state.work.params]
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
- Work that uses a block takes no `action`, `skill` or `fields`: those come from the block. A failing block fails the
  work and sends a notice to the practice's escalation role, like any automation.
- **A python or external block is a job where the hub is served.** `practicehub serve` runs workers
  (`--job-workers`, 3 by default; the first takes only python blocks, so a quick screen never waits behind a slow
  engine). There a block automation is not run in the request that makes it ready: it is queued, a worker takes it
  (running), calls its engine with no transaction open, and writes the answer (done or failed) before the run goes
  on. The record's page shows it in the queue, under way and done as it happens. What reads its output waits for
  it; everything else goes on. A command or a test has no worker, and runs a block where it becomes ready
  (`--job-workers 0` does the same in a served hub). The portal always runs a block in place, since it answers the
  patient from what the screen found (`Engine.jobs_inline`).
- **To try an engine block on one machine**, `scripts/dev-engine.py` is a real engine for the webhook path: a small
  HTTP service that does a block's work with a local model through Ollama (`--model`, default `qwen3.5:4b`).
  Start it, then serve the hub with `PRACTICEHUB_ENGINE_WEBHOOK_URL=http://127.0.0.1:8215/hook`. A block's
  `target` names a flow there (what the model is asked, and the shape of its answer); the pack's
  `post-claim-to-erp` has one built in, and `--flows` adds more. It posts nothing anywhere: it is for seeing an
  engine block queued, under way and done with a call that really takes time, not a back office.
- A job cut short (its worker stopped, or it ran past twice its engine's time limit) fails saying so when it was
  on an engine: whether it finished there is not known, so it is never run again by itself; a policy owner checks
  the engine and retries the step. Python in the sandbox does nothing outside itself, so it goes back in the queue
  once. An answer that arrives after its work was stopped, or after the step was run again, changes nothing and is
  kept on the track (`step.late`).

## Context: what work reads

| Entry | Gives |
|---|---|
| `record:<class>` | the run's subject's records in that data class (e.g. `record:clinical`) |
| `input` / `input.<name>` | the run's inputs, or one of them |
| `<work>` | earlier work's (or an automation's) whole output |
| `<work>.<field>` | one field of it |
| `provider:<name>` | computed data: `free-slots`, `members`, or a provider the pack adds |
| `file:<field>` | the file in that attachment field of the run's record: its name, type, size and words (none for a scan or a photo), and whether it is a picture; never its bytes. It needs a `record:<class>` entry beside it, the run's record's class |

A step whose `file:` entry is a picture (a PNG, JPEG or WebP photo of a document) is answered by the model the
agent's profile names as `vision_model`, which is shown the picture (fetched as the agent: reading it is a view of
the record, on the track). An agent with no model for pictures is told there is a picture it cannot see; a skill
that needs what it shows escalates. Setting or changing an agent's `vision_model` is proposed and approved by a
second policy owner, as a change of its model is.

**Data classes travel with data.** A member may take work only if they may read every class it reads:

- the classes in its `record:` entries;
- for a `<work>` entry or a `review_of`, the classes *that* work read. A draft note is clinical wherever it goes.

Naming a single field (`edit.diagnosis_codes`) passes on that field only. It is a deliberate disclosure, written in
the approved workflow, which is how billing gets diagnosis codes without the note.

On run pages and in the track, a member who cannot read a piece of work's classes sees who did it, when and why, but
not the content.

## Conditions

`when` (on work, edges and automations) and `agent_criteria` use a small expression language. It has no `eval`.

```
intake.complete == true
billing.amount < 200 and billing.codes != []
review.outcome in ['renew', 'renew-with-changes']
not sign.outcome == 'decline'
```

Paths look up values in the run, and a missing path is `null`. The operators are `== != < <= > >= in and or not` and
parentheses.

## Fields

The types are `string`, `text`, `number`, `boolean`, `date`, `datetime`, `choice` (with `choices = [...]` or
`choices_from = "<context entry>"`), `list` and `member`. The options are `required`, `default`, `help`, `min`, `max`
and `max_length`. A choice that decides where the record goes is better written as edges out of the state.

## Risk

An agent may take work only if its `risk` is within the agent's `max_risk`.

| risk | when an agent does it |
|---|---|
| low | it goes ahead |
| medium | it goes ahead, and the agent's sponsor gets a notice |
| high | it is **held**. The sponsor sees the answer and approves it (it is applied, and signed) or rejects it (the item goes to people with the reason) |

## Agents and escalation

An agent can take work only when all of these hold:

- it holds the role;
- its sponsor is active;
- the work has a skill that is in the agent's profile;
- the work allows agents;
- the risk is within the agent's limit;
- the data-class rules allow it.

Those rules bound the work an agent is given. An agent does not change records outside that work: its roles' write
grants let the workflow write what it answers, and nothing else, unless its profile has `direct_writes`, which a
second policy owner approves.

An agent that is unsure, or whose answer fails validation, gives the item back with a reason. The item is then for
people only.

## Change control

1. Propose a change: **Workflows → (workflow) → Edit workflow**, or `POST /api/workflows` with `{source, note}`.
   `POST /api/workflows/check` with `{source}` reports problems without proposing anything.
   The hub checks it against the pack's roles, skills, actions and data classes, and lists every problem.
2. A *different* person who is a policy owner approves it. The approver sees the diff and the **agent impact**: work
   agents may now do that they could not before.
3. New runs use the new version. Runs already started finish on their own version.
