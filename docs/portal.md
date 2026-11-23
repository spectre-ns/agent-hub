# The booking portal

The portal lets the people a practice serves book online. It is a process of its own, the only part meant to face
the internet. The staff hub stays inside.

The portal process has no database and none of the hub's secrets. It does not hold the MongoDB URI or
`PRACTICEHUB_SECRET`. It talks to the hub over a private link, acting as the `[portal] member` service (`portal` in
the clinic pack), with a token whose scope is `portal`. That token opens the hub's `/api/portal/` and nothing else,
and no other token opens `/api/portal/`. Everything the portal does is kept on the hub, in `practicehub/portal.py`:
the codes, the sessions, the counts, the answers to forms, the bookings and the track. The portal keeps the
session the hub gave in the person's cookie, without reading it.

```
browser ──TLS──> nginx ──> portal serve (127.0.0.1:8210) ──private link──> hub serve (127.0.0.1:8200) ──> MongoDB
                           pages only: no DB, no secret        /api/portal/ with the portal token
```

## Running it

### Next to a dev hub

Run these two steps on one machine:

```sh
practicehub serve --dev                                   # the hub, http://127.0.0.1:8200/
practicehub token portal --as jo --portal > portal.token  # once: the portal member's token (jo is its sponsor)
practicehub portal serve --dev --token-file portal.token  # the portal, http://127.0.0.1:8210/
```

A dev portal shows the sign-in code on screen, marked dev-only, because nothing sends it. It only listens on this
machine. It refuses any request that has an `X-Forwarded-For` header or a Host other than localhost, so it cannot be
put behind a proxy by mistake. It works only with a dev hub: the hub gives the code only when it is served with
`--dev` itself, and the portal refuses to start if the two disagree.

### For real

The hub's private listener should be reachable only from the portal's host, over a private network or a WireGuard
link. Set the token in `PRACTICEHUB_PORTAL_TOKEN`, or give its file with `--token-file`:

```sh
PRACTICEHUB_PORTAL_TOKEN=$(cat /etc/practicehub/portal.token) \
  practicehub portal serve --hub http://10.0.0.2:8200 --public-origin https://book.example.ca
```

The portal checks the hub at start. It refuses to start if the hub refuses its token or the portal is closed (see
below). It never binds 8080, 9980 or 11434.

Limits on the listener:

- a 64 KB body, refused before it is read;
- a 30 second socket timeout, so a slow client does not hold a thread;
- POSTs only from `public_origin`;
- Secure, HttpOnly, SameSite=Strict cookies;
- a CSP that allows no scripts and no framing.

Every page works without JavaScript.

### nginx in front

The proxy must overwrite `X-Forwarded-For` rather than append to it, because the address it sends is what the
portal counts.

```nginx
server {
    listen 443 ssl http2;
    server_name book.example.ca;
    ssl_certificate     /etc/letsencrypt/live/book.example.ca/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/book.example.ca/privkey.pem;
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    client_max_body_size 64k;

    location / {
        proxy_pass http://127.0.0.1:8210;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_read_timeout 30s;
    }
}
server {
    listen 80;
    server_name book.example.ca;
    return 301 https://$host$request_uri;
}
```

Set `[portal] public_origin = "https://book.example.ca"` in the pack, or pass `--public-origin`. The portal then
accepts only forms posted from that address.

## What it does, as whom

The portal acts as the `[portal] member`. In the clinic, that member holds `patient-portal`, which writes contact
details, intake, messages and visits, and reads no clinical record. It makes only three kinds of change:

- **A booking or a request** starts the kind's workflow about the person (`hub.engine.start`). In the clinic, both
  kinds start the practice's own `appointment-request`. It makes the visit (an Encounter) and screens the reason
  for red flags, as it does for a request from the desk.
- **A cancellation** moves the visit to `cancel_to` (`cancelled`) through `hub.update_record`. The hub allows the
  move only where the visit's lifecycle has it as a move made by people (`planned → cancelled`) and the workflow is
  waiting on it (`checked-in` waits until the visit is cancelled), so that run moves on in the same transaction. The
  portal never writes a status any other way.
- **Someone new** gets a record with `[portal.register] pending` values (`active = false`), and the intake workflow
  starts about it.

The code makes and cancels bookings in exactly one place each: `Portal._book` and `Portal._cancel` in
`practicehub/portal.py`.

What a person sees of a booking is when it is, with whom, the kind in the portal's words, and the state in the
lifecycle's words. They never see why.

## Signing in (people on file only)

1. The person gives their date of birth (day, month and year, which the browser fills from `bday-*`) and their
   identifier (the subject type's `[base] identifier`; in the clinic, the health card).
   - The answer is the same whether or not anyone matches, and whether or not a code went out.
   - Only an active record is found. A registration not yet accepted is not.
2. A one-time code (6 digits, 10 minutes by default) goes to the outbox, addressed to the contact on file
   (`[portal.code] channel`).
   - The code and the sign-in token are kept only as hashes.
   - Each try spends one of the code's 5 attempts in a single atomic update, guarded on the code being unused, with
     attempts left and not expired. Only then is the code compared.
   - A right code is marked used by a conditional update, so tries made at once open one session.
   - Once the code is used or expires, its words are removed from the outbox message and the message is
     `withdrawn`, a final status, so a connector attached later never sends it.
3. Counting. Lockouts use the same mechanism as the staff sign-in.
   - Failures are counted per address (5), and IPv6 is counted by its /64.
   - Every attempt counts against the identifier typed (10, from any address), right or wrong. The count is hashed,
     and it locks whether or not anyone has that identifier.
   - Codes sent to one record are capped at 3 a quarter hour and 10 a day. Past the cap, the person gets the same
     answer and nothing is sent. The withheld sends become one `portal.code_withheld` event per window, with its
     count.
   - All sign-ins and registrations together are capped at `[portal] per_minute` (default 60). Past that, everyone
     is told to try again in a minute.

`practicehub tick`, and the clock in `practicehub serve`, sweep spent codes (their messages withdrawn), ended
sessions, old counts and old form answers.

**No message provider is connected.** Codes wait on the outbox, so a real portal works only once a connector
delivers them, and that provider must keep the data in Canada.

## Someone new

**Register** (`/new`) is a request to join, not a sign-up.

- The record is made with `[portal.register] pending` (`active = false` in the clinic), so nobody can sign in as it
  or book for it.
- `new-patient-intake` starts about it. Someone at the front desk accepts the patient (`accept`), and only then does
  the `activate` step set `active = true`.
- Registration is counted per address and against the per-minute ceiling.
- Duplicates are not detected online; the intake review catches them.

## Forms: one answer, after a redirect

Every form that changes something (book, cancel, register) carries its own token. The hub runs it at most once and
keeps the answer. The portal then redirects to `/done/<id>`, which:

- is the same page however often the form is sent or reloaded, and only for the person who sent it;
- while the hub is still working, shows the emergency line and refreshes itself (a meta refresh, no script);
- for a booking or request, always shows the say-now advice first: the screen's advice, or the emergency line when
  there is none. It shows the advice loudly when the kind's `alarm` holds;
- has a title that says what happened: **Booked**, **Request sent**, **Not booked** (with what happens next) or
  **Cancelled**.

If the same request reaches the workflow twice, for example from a second tab, the workflow's `one_open_same`
(the same reason within `one_open_within`) refuses the second start. The hub answers with the open request's own
outcome (`Duplicate.run`).

Cancelling is a link, **Cancel this visit**, to a confirm page. Nothing is cancelled by a GET.

## When the portal is closed

The hub's `/api/portal/` answers 503, and the portal shows "Online booking is closed for now. Call the practice."
(`wording.closed`), in any of these cases:

- **The practice pack has a problem** (`pack_problem`). For example, `pack.toml` changed on disk and the change has
  not yet been reviewed and upgraded.
- **A workflow the portal starts is not an active definition of the hub's**, or the definition in force no longer
  screens what the kind asks.

`[portal]` is one of the pack's pinned settings. A change to it on an existing hub waits for `practicehub pack
review` and `pack upgrade` by two policy owners, like any other pack setting. A hub that pinned its settings before
`[portal]` was a setting takes it from disk only if the files are exactly the ones put into force. Otherwise it
starts with no portal until the upgrade.

## `[portal]` in the pack

It is checked when the pack loads, and a mistake stops the load with a clear problem
(`practicehub/portal.py: check_config`). Modules may add kinds under `[portal.kinds]`.

| Key | |
|---|---|
| `member` | the service member the portal acts as |
| `title`, `welcome`, `contact_line` | the portal's own words |
| `privacy` | required; shown on every page |
| `public_origin` | the https address people use (behind a proxy) |
| `per_minute` | sign-ins and registrations from everyone in a minute (default 60) |
| `wording.*` | `booking sent no_match duplicate clash requested booked registered cancelled emergency working too_many at_limit over_limit safety busy failed closed` |
| `over_limit.workflow`, `over_limit.input` | what takes a person's own words once they are at a kind's limit (below) |
| `identity.born`, `born_label`, `identifier_label`, `inputmode` | the date-of-birth field; the labels; `numeric` for a number card |
| `code` | `channel`, `minutes` (2 to 30), `length` (6 to 10) |
| `register.fields`, `register.pending`, `intake_workflow` | the form for someone new, the values that keep them a request, and the workflow that takes them in |
| `session_minutes` | 5 to 120, default 30 |

Each `[portal.kinds.<id>]` takes these keys.

**What the kind is and what it starts:**

- `label`, `description`.
- `mode`: `book` (booked once the screen allows) or `request` (someone at the practice arranges it).
- `workflow`: started about the person. The portal's role must be one of its starters. Its record type is the
  subject type, or it makes its own record about the person (`[new_record]`).
- `inputs`: fixed inputs.

**Its times:**

- `roles`, `minutes`, `horizon_days`, `max_slots`: whose free time is offered, how long a visit is, and how far
  ahead.
- `slot`: `required`, `optional` or `none`. The input the time goes to is `slot_input` (default `slot`).

**What it asks:**

- `questions`: the workflow's inputs to ask, each `{input, label, help, choices}`.
- Help comes only from here; the workflow's help is written for staff.
- `choices` gives each choice in the person's words. A required choice starts on a blank "Choose one".

**Screening:**

- `screen`: the workflow's step that screens the person's own words. It is required when any question is free
  text. The step's params must read each such input, and a kind with `mode = "book"` must name in `booked` a step
  that runs after the screen and depends on it.
- `advice`: a path into the run's results that holds the say-now advice.
- `alarm`: an `expr` condition for showing the advice loudly. If it cannot be read, the advice is shown loudly.
- `booked`: the step whose output says `when`.

**Its bookings:**

- `booking_kind`: the bookings' kind, so the person's view can name them.
- `max_upcoming`: how many open bookings or requests of this kind a person may have (default 2). Their page and
  the form say so up front (`at_limit`), and the form then offers no times. What they write is still screened, with
  the kind's `screen`, before the limit is applied: anything not plainly routine starts `[portal.over_limit]`'s
  workflow with their words in its `input` (the clinic's `patient-question`, which screens them again and sends a red
  flag to a nurse), and the page shows the screen's advice, loudly on a red flag. Plainly routine words, or no
  `over_limit`, are refused (`too_many`). Every refusal page says what to do in an emergency (`safety`).
- `cancel_to`, `cancel_before_hours`: cancelling online moves the visit to this state, at least this many hours
  ahead. It must be a state the record type's lifecycle reaches by a move people make.

The clinic has two kinds. Both go through `appointment-request`:

- **A new concern** is a request. Its reason is screened, and the screen's advice is on the page.
- **Forms or paperwork** asks for a time. It is booked in that time at once (`book-chosen`) only when the screen
  finds the reason plainly routine. Anything flagged, unclear or not plainly paperwork goes to a nurse, the time is
  not taken, and the person is told the request was sent. A booked forms visit can be cancelled online up to 24 hours
  ahead.

The time a person picks is checked again when they submit. If it has been taken, the form comes back with fresh
times.

## Gaps

- `booking.book` books the calendar's slot length, not the kind's `minutes`. They are equal in the clinic pack.
- Until a code is used or expires, staff who may read correspondence can read it in the outbox.
