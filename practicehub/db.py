"""The hub's database: MongoDB (see ``store``). This module names its collections, the shape of their documents and
their indexes, and opens the database for a hub.

Conventions, the same in every collection:

* Each document keeps the fields its row had as plain fields, and ``_id`` is its key, built as below. The natural
  fields (``id``, ``seq``, ``workflow_id`` + ``version``...) are kept beside ``_id`` so code reads ``doc["id"]``.
* What was a JSON column is a nested value now: ``members.profile``, ``runs.ctx``, ``runs.steps``, ``items.output``,
  ``events.data``, ``records.data``, ``record_versions.data``, ``settings.value``.
* Flags that were 0/1 stay 0/1 (``tokens.revoked``, ``sessions.revoked``, ``items.persons_only``, ``items.escalated``,
  ``outbox.escalated``); missing optional fields are ``None``.
* Numbers that were AUTOINCREMENT come from ``store.next_seq(name)``: ``events.seq`` ("events"), ``outbox.id``
  ("outbox").

Collection          _id                                    fields
settings            key                                    value
members             id                                     id kind name title email sponsor status profile{} created_at
roles               id                                     id name description module
tokens              token_hash                             token_hash member label created_at revoked issued_by
tuples              "object#relation@subject"              object relation subject
records             "Type/id"                              type id subject data{} version updated_at search
                                                           words[] search_parts[{c, w[]}]|None ix
workflow_versions   "workflow_id@version"                  workflow_id version status source author note created_at
                                                           approved_by approved_at module
block_versions      "block_id@version"                     block_id ... (as workflow_versions)
type_versions       "type_id@version"                      type_id ...
view_versions       "view_id@version"                      view_id ...
skill_versions      "skill_id@version"                     skill_id ...
agent_profile_versions "agent_id@version"                  agent_id version status profile{} source author note
                                                           created_at approved_by approved_at based_on
data_classes        id                                     id name description module
record_versions     "Type/id@version"                      type id version data{} refs[] actor run_id at
office_drafts       file id (signed, opaque)              ref field base working name editors[] lock modified
links               "from_ref|rel|to_ref"                  from_ref rel to_ref
titles              "Type/id"                              type key (the title folded), for types with formatted text
mention_names       "Type/id|key"                          from_ref type key: a title named by [[...]] in that record
runs                id                                     id workflow_id version title record status ctx{} steps{}
                                                           started_by root_event created_at updated_at
items               id                                     id n run_id step_id kind title role assignee persons_only
                                                           status claimed_by output{}|None note cause created_at
                                                           updated_at due_at escalated priority escalate_next
events              seq                                    seq at actor actor_kind action subject run_id cause data{}
                                                           prev hash signature mac
outbox              id                                     id at channel recipient subject body run_id sent_by status
                                                           classes claimed_by claimed_at done_at done_by note escalated
passphrases         member                                 member hash set_at set_by
setup_codes         code_hash                              code_hash member issued_by created_at expires_at used_at
sessions            session_hash                           session_hash member created_at expires_at revoked
signin_failures     member                                 member count first_at locked_until (the portal's
                                                           keys too: "portal client <address>", "portal id <hash>")
portal_codes        hash of the sign-in's token            record code_hash identifier outbox attempts created_at
                                                           expires_at used_at (``portal``: one-time codes, hashed)
portal_sessions     hash of the session's token            record created_at expires_at revoked form
counters            sequence name                          n

``records.words`` is every word of the record's values, folded (a search matches the start of one, by index);
``records.search_parts`` the same per data class, for a type with classed fields (the record without any of them as
``c = "_"``, then with only one class's), so a reader's search looks only where they may read; ``ix`` stamps what
they were built from (``Hub._search_stamp``).
``record_versions.refs`` is every "Type/id" string in that version's data: the access report finds what was ever
about a subject by it.
``items.n`` is the item's number in order of creation (``store.next_seq("items")``), shown as the item's key.
Files (attachments) are in GridFS (``store.put_file``), named by their SHA-256, with metadata size contentType
data_class added_by at; ``Hub.add_file`` adds one and ``Hub.read_file`` reads one through a record that names it.
"""

from __future__ import annotations

import os

from .store import Store, utcnow  # noqa: F401  (utcnow is imported from here across the hub)

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
    "runs": ["status", "record", "workflow_id", "refs"],
    "items": ["status", "run_id", ("assignee", "status"), ("role", "status"), "n"],
    "events": [{"fields": ["hash"], "unique": True}, "run_id", ("subject", "action"), ("action", "seq"), "actor",
               ("action", "data.refs"), ("data.refs_sha256", "actor"), ("data.subject", "actor")],
    "outbox": ["status", "channel"],
    "sessions": ["member"],
    "setup_codes": ["member"],
    "portal_codes": ["expires_at"],
    "portal_sessions": ["record"],
}


def open_database(uri: str | None = None, database: str | None = None) -> Store:
    """The hub's database: ``uri`` (else ``PRACTICEHUB_DATABASE``, else a local replica set), with its indexes."""
    store = Store(uri, database or os.environ.get("PRACTICEHUB_DATABASE_NAME") or None)
    store.ensure_indexes(INDEXES)
    return store


Database = Store  # the type the rest of the hub annotates with
