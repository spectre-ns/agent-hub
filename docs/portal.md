# The booking portal

The portal is a small web app of its own. It lets the people a practice serves book online, and it is the part
meant to face the internet. The staff hub stays inside.

```sh
practicehub portal serve --dev                       # http://127.0.0.1:8210/, codes shown on screen
practicehub portal serve --host 0.0.0.0 --port 8210  # behind a TLS proxy (its cookies are Secure)
```

It never binds 8080, 9980 or 11434. It shares the hub's database and stylesheet, but nothing else: it has its own
sessions and cookies (`php_session`, `php_signin`) and never accepts the staff sign-in.

## What it does, as whom

The portal acts as the pack's service member (`[portal] member`; in the clinic pack this is `portal`, holding
`patient-portal`). Every change it makes is a workflow that member starts:

- a booking or a request starts the kind's workflow;
- a cancellation starts the kind's cancel workflow;
- someone new starts the intake workflow.

It never writes a booking itself. The member reads no clinical records, so a person sees only these things about a
booking: when it is, with whom, what kind it is (in the portal's words), and its state (in the words of the type's
lifecycle).

The code makes and cancels bookings in exactly one place each: `Portal._book` and `Portal._cancel` in
`practicehub/portal.py`.

## Signing in (existing people only)

1. The person gives their date of birth and identifier (the subject type's `[base] identifier`; in the clinic,
   the health card). The answer is the same whether or not anyone matches. Only on a match with a contact on file
   is a code queued.
2. A one-time code (6 digits, 10 minutes by default) goes to the outbox, to the contact on file (`[portal.code]
   channel`). The code and the sign-in token are stored only as hashes. A code works once, and after 5 wrong tries
   it is spent. Once it is used or expires, the code is removed from the outbox message.
3. Failures are counted per address (5) and per identifier typed (10, from any address; this is hashed, and it
   locks whether or not anyone has that identifier). They use the same lockout as the staff sign-in.

Someone not on file uses **Register** (`/new`). Their details make a record and start the intake workflow
(counted per address). Registering online does not detect duplicates; the intake review catches them.

**No message provider is connected.** Codes wait on the outbox, so a real portal works only once a connector
delivers them, and that provider must keep the data in Canada. Until then, use `--dev` on a local hub only: it
shows the code on screen, marked dev-only.

## `[portal]` in the pack

It is checked when the pack loads, and a mistake stops the load with a clear problem
(`practicehub/portal.py: check_config`). Modules may add kinds under `[portal.kinds]`.

| Key | |
|---|---|
| `member` | the service member the portal acts as |
| `title`, `welcome`, `contact_line` | the portal's own words |
| `privacy` | required; shown on every page |
| `wording.*` | `booking sent no_match duplicate clash requested booked registered cancelled` |
| `identity.born` | the subject type's date-of-birth field |
| `code` | `channel`, `minutes` (2 to 30), `length` (6 to 10) |
| `register.fields`, `intake_workflow` | the form for someone new, and the workflow it starts |
| `session_minutes` | 5 to 120, default 30 |

Each `[portal.kinds.<id>]` takes:

- `label`, `description`
- `mode`: `book` (booked straight away) or `request` (someone at the practice arranges it)
- `workflow`: started about the person. The portal role must be one of its starters.
- `roles`, `minutes`, `horizon_days`, `max_slots`: whose free time is offered, how long, how far ahead
- `slot`: `required`, `optional` or `none`. The input the time goes to is `slot_input` (default `slot`).
- `inputs`: fixed inputs. `questions` are the workflow's inputs to ask, each given as a name or as `{input,
  label, help}`.
- `advice`: a path into the run's results that holds the say-now advice. It is shown prominently on the
  confirmation, and loudly when `alarm` (an `expr` condition) holds. If the alarm cannot be read, it is shown loudly.
- `booked`: the step whose output says `when` (required for `book`)
- `booking_kind`: the bookings' kind, so the portal can name them in the person's view
- `cancel_workflow`, `cancel_before_hours`: cancelling online, through that workflow, at least this long ahead

The clinic has two kinds:

- **A new concern** is a request through `appointment-request`. It is screened for red flags and shows the
  screen's advice. The workflow's `one_open_per` means a second request answers only "you already have an open
  request".
- **Forms or paperwork** is booked straight away through `portal-booking` and can be cancelled through
  `portal-cancel` up to 24 hours ahead.

The time a person picks is checked again when they submit. If it has been taken, the form comes back with fresh
times.

## Gaps

- `booking.book` books the calendar's slot length, not the kind's `minutes`. They are equal in the clinic pack.
- Until a code is used or expires, it is readable in the outbox by staff who may read correspondence.
