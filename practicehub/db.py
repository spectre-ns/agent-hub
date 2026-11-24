"""The hub's database: MongoDB (see ``store``). This module is the statement of what is stored: ``SHAPES`` names every
collection, how its ``_id`` is built and the fields of its documents; ``INDEXES`` names its indexes. A field or a
collection that is not written down here fails the tests (``tests/conftest.py``, ``stored_as_written_down``): add it
here when you add it, and take it out when nothing writes it any more.

The same in every collection:

* ``_id`` is the document's key, built as its collection says below. The fields it is built from (``id``, ``seq``,
  ``workflow_id`` and ``version``...) are kept beside it, so code reads ``doc["id"]``.
* A value with parts is stored with its parts: ``members.profile``, ``runs.ctx``, ``runs.steps``, ``items.output``,
  ``events.data``, ``records.data``, ``record_versions.data``, ``settings.value``.
* A flag is 0 or 1 (``tokens.revoked``, ``sessions.revoked``, ``portal_sessions.revoked``, ``items.persons_only``,
  ``outbox.escalated``). ``items.escalated`` is not a flag: it is how many times the item has gone up. An optional
  field with nothing in it is ``None``.
* A time is an ISO 8601 string in UTC (``store.utcnow``), named ``at`` or ``..._at``. The live editing collections
  (``live_*``) keep seconds since the epoch instead, since they only compare and subtract them.
* Numbers in order come from ``store.next_seq(name)``: ``outbox.id`` ("outbox") and ``items.n`` ("items").
  ``events.seq`` does not: it is the head's plus one and is the event's ``_id``, so two writers cannot both append
  (``track.py``).

What the lists below cannot say:

``runs.steps`` is ``{step id: {"state": pending | active | waiting | done | skipped | failed, ...}}`` and ``runs.ctx``
what each step produced, with ``ctx.run`` (the run's own: record, subject, started_by, parent, previous, origin...)
and ``ctx.input``. ``runs.status`` is running, completed or cancelled. ``runs.refs`` is every record the run carries;
``runs.waits_on`` the record its waits can see ("*": any), ``runs.resting`` that nothing is to be done about it;
``runs.item`` its one work item (None: its workflow asks nothing of anyone); ``runs.at`` where its record is, for a
workflow state the record's own field has no value for; ``runs.picks`` the people chosen for work not started yet.
``items.status`` is open, claimed, held, waiting, done or cancelled (folded: see ``Engine.fold_run_items``);
``items.kind`` task, gate, wait, notice or hold. ``items.n`` is the item's number in order of creation, shown as
its key. ``outbox.status`` is queued, sending, sent, delivered, failed, refused or withdrawn.
``records.words`` is every word of the record's values, folded (a search matches the start of one, by index);
``records.search_parts`` the same per data class, for a type with classed fields (the record without any of them as
``c = "_"``, then with only one class's), so a reader's search looks only where they may read; ``ix`` stamps what
they were built from (``Hub._search_stamp``).
``record_versions.refs`` is every "Type/id" string in that version's data: the access report finds what was ever
about a subject by it.
``signin_failures`` is keyed by a member's id, and by the portal's own keys ("portal client <address>",
"portal id <hash>"). ``setup_codes.used_at`` is the time a code was used, or "superseded <time>".
Files (attachments) are in GridFS (``store.put_file``; the collections ``files.files`` and ``files.chunks``), named
by their SHA-256, with metadata size contentType data_class added_by at; ``Hub.add_file`` adds one and
``Hub.read_file`` reads one through a record that names it.
"""

from __future__ import annotations

import os

from .store import Store, utcnow  # noqa: F401  (utcnow is imported from here across the hub)

# collection: (how its _id is built, its fields)
SHAPES = {
    "settings": ("the key", "value"),
    "counters": ("the sequence's name", "n"),
    "locks": ('"writer": the lease of the one writer (store.py)', "owner until host pid"),
    # members, roles and what they may do
    "members": ("id", "id kind name title email sponsor status profile prefs created_at"),
    "roles": ("id", "id name description module"),
    "data_classes": ("id", "id name description module"),
    "tuples": ('"object#relation@subject"', "object relation subject"),
    "tokens": ("token_hash", "token_hash member label created_at revoked issued_by scope servers client"),
    "passphrases": ("member", "member hash set_at set_by issued_by"),
    "setup_codes": ("code_hash", "code_hash member issued_by created_at expires_at used_at"),
    "sessions": ("session_hash", "session_hash member created_at expires_at revoked"),
    "signin_failures": ("member, or the portal's key", "member count first_at locked_until"),
    # definitions under change control: one document per version
    "workflow_versions": ('"workflow_id@version"', "workflow_id version status source author note created_at "
                                                   "approved_by approved_at module"),
    "block_versions": ('"block_id@version"', "block_id version status source author note created_at approved_by "
                                             "approved_at module"),
    "type_versions": ('"type_id@version"', "type_id version status source author note created_at approved_by "
                                           "approved_at module"),
    "view_versions": ('"view_id@version"', "view_id version status source author note created_at approved_by "
                                           "approved_at module"),
    "skill_versions": ('"skill_id@version"', "skill_id version status source author note created_at approved_by "
                                             "approved_at module"),
    "agent_profile_versions": ('"agent_id@version"', "agent_id version status profile source author note created_at "
                                                     "approved_by approved_at based_on reduction"),
    # records
    "records": ('"Type/id"', "type id subject data version updated_at search words search_parts ix"),
    "record_versions": ('"Type/id@version"', "type id version data refs actor run_id at comment authors"),
    "links": ('"from_ref|rel|to_ref"', "from_ref rel to_ref hand"),
    "titles": ('"Type/id"', "type key"),                 # the title folded, for types with formatted text
    "mention_names": ('"Type/id|key"', "from_ref type key"),   # a title named by [[...]] in that record
    "mentions": ("id", "id ref who member kind by anchor field file quote around at item run answered answered_at"),
    "office_drafts": ("the file's id (signed, opaque)", "ref field origin base working name contentType modified "
                                                        "editors lock joined updated_at"),
    "live_sessions": ('"Type/id#field"', "ref field base_version base_text base_seq text seq members contributors "
                                         "pv started last_op checkpoints saved_at saved_reason outside_at ended"),
    "live_ops": ('"session@seq"', "session seq cid op by at outside"),
    "live_ended": ("the session's", "reason at text seq"),
    "live_comments": ("id", "id ref field file quote before after body by agent at mentions replies resolved "
                                "resolved_at resolved_by"),
    # work
    "runs": ("id", "id workflow_id version title record subject status ctx steps started_by root_event item refs "
                   "waits_on resting picks at deadline_at deadline_missed_at created_at updated_at "
                   "fold_failed relink_attempts relink_error relink_next_at relink_skipped"),
    "items": ("id", "id n run_id step_id kind title role assignee persons_only status claimed_by output note cause "
                    "created_at updated_at due_at escalated priority escalate_next escalates repeats "
                    "step_title history parent_id children wave step_started_at waiting resting folded_into"),
    "events": ("seq", "seq at actor actor_kind action subject run_id cause data prev hash signature mac"),
    "outbox": ("id", "id at channel recipient subject body run_id sent_by status classes purpose claimed_by "
                     "claimed_at done_at done_by note escalated"),
    # the portal
    "portal_codes": ("a hash of the sign-in's token", "record code_hash identifier outbox attempts created_at "
                                                      "expires_at used_at"),
    "portal_sessions": ("a hash of the session's token", "record created_at expires_at revoked form"),
    "portal_counts": ('"key|seconds|window"', "key n expires_at"),
    "portal_submits": ("a hash of the person and the form's token", "done record what state result created_at "
                                                                    "expires_at"),
}


def fields(collection: str) -> frozenset:
    """The fields a document of ``collection`` may have (``_id`` among them)."""
    return frozenset(SHAPES[collection][1].split()) | {"_id"}


INDEXES = {
    "members": ["kind", "sponsor"],
    "tokens": ["member"],
    "tuples": ["subject", "object"],
    "records": [("type", "updated_at"), ("subject", "updated_at"), "updated_at", ("type", "ix"), "words",
                ("search_parts.c", "search_parts.w")],
    "office_drafts": [("ref", "field", "base"), ("ref", "working")],
    "workflow_versions": [("workflow_id", "status"), "status"],
    "block_versions": [("block_id", "status"), "status"],
    "type_versions": [("type_id", "status"), "status"],
    "view_versions": [("view_id", "status"), "status"],
    "skill_versions": [("skill_id", "status"), "status"],
    "agent_profile_versions": [("agent_id", "status")],
    "record_versions": [("type", "id", "version"), "refs"],
    "links": ["to_ref", "from_ref"],
    "titles": [("type", "key")],
    "mention_names": [("type", "key"), "from_ref"],
    "runs": ["status", "record", "workflow_id", "refs", "waits_on"],
    "items": ["status", "run_id", ("assignee", "status"), ("role", "status"), "n"],
    "events": [{"fields": ["hash"], "unique": True}, "run_id", ("subject", "action"), ("action", "seq"), "actor",
               ("action", "data.refs"), ("data.refs_sha256", "actor"), ("data.subject", "actor")],
    "outbox": ["status", "channel"],
    "sessions": ["member"],
    "setup_codes": ["member"],
    "portal_codes": ["expires_at"],
    "portal_sessions": ["record", "expires_at"],
    "portal_counts": ["expires_at"],
    "portal_submits": ["done", "expires_at"],
}


def open_database(uri: str | None = None, database: str | None = None) -> Store:
    """The hub's database: ``uri`` (else ``PRACTICEHUB_DATABASE``, else a local replica set), with its indexes."""
    store = Store(uri, database or os.environ.get("PRACTICEHUB_DATABASE_NAME") or None)
    store.ensure_indexes(INDEXES)
    return store


Database = Store  # the type the rest of the hub annotates with
