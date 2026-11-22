"""Membership and permissions as relationship tuples, the OpenFGA way.

The authorization model (``AUTHZ_MODEL_DSL``) is the one the hub writes to OpenFGA when OpenFGA is configured. The
``LocalAuthorizer`` evaluates the same model over tuples in SQLite, so a hub without OpenFGA behaves identically and
the two can be swapped without changing a single tuple.

People and agents are both ``member:<id>`` principals. Nothing in the model distinguishes them: an agent gets exactly
the authority its roles give it, and the rules that do treat agents differently (who may sign, risk tiers) are guards
in the engine, written down where a reviewer can read them.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Protocol

from .db import Database

AUTHZ_MODEL_DSL = """\
model
  schema 1.1

type member
  relations
    define sponsor: [member]

type role
  relations
    define assignee: [member]

type practice
  relations
    define owner: [member]
    define policy_owner: [member, role#assignee] or owner
    define member_admin: [member, role#assignee] or owner
    define break_glass: [member, role#assignee]

type data_class
  relations
    define writer: [member, role#assignee]
    define reader: [member, role#assignee] or writer

type workflow
  relations
    define starter: [member, role#assignee]

type channel
  relations
    define sender: [member]
"""

# relation -> relations on the same object that imply it ("or writer")
IMPLIED = {
    ("practice", "policy_owner"): ["owner"],
    ("practice", "member_admin"): ["owner"],
    ("data_class", "reader"): ["writer"],
}


class Authorizer(Protocol):
    def write(self, tuples: list[tuple[str, str, str]]) -> None: ...
    def delete(self, tuples: list[tuple[str, str, str]]) -> None: ...
    def check(self, user: str, relation: str, obj: str) -> bool: ...
    def read(self, *, obj: str | None = None, relation: str | None = None, subject: str | None = None) -> list[tuple[str, str, str]]: ...


class LocalAuthorizer:
    """Evaluates AUTHZ_MODEL_DSL over the hub's tuples table."""

    def __init__(self, db: Database):
        self.db = db

    def write(self, tuples):
        with self.db:
            for obj, rel, sub in tuples:
                self.db.tuples.update({"_id": f"{obj}#{rel}@{sub}"},
                                      {"$setOnInsert": {"object": obj, "relation": rel, "subject": sub}}, upsert=True)

    def delete(self, tuples):
        with self.db:
            for obj, rel, sub in tuples:
                self.db.tuples.delete({"_id": f"{obj}#{rel}@{sub}"})

    def read(self, *, obj=None, relation=None, subject=None):
        flt = {col: val for col, val in (("object", obj), ("relation", relation), ("subject", subject))
               if val is not None}
        return [(r["object"], r["relation"], r["subject"])
                for r in self.db.tuples.find(flt, sort=["object", "relation", "subject"],
                                             fields=["object", "relation", "subject"])]

    def check(self, user: str, relation: str, obj: str, _depth: int = 0) -> bool:
        if _depth > 8:
            return False
        for (_, _, sub) in self.read(obj=obj, relation=relation):
            if sub == user:
                return True
            if "#" in sub:  # userset, e.g. role:physician#assignee
                set_obj, set_rel = sub.split("#", 1)
                if self.check(user, set_rel, set_obj, _depth + 1):
                    return True
        otype = obj.split(":", 1)[0]
        return any(self.check(user, implied, obj, _depth + 1) for implied in IMPLIED.get((otype, relation), []))


class OpenFGAAuthorizer:
    """The same interface against an OpenFGA server (HTTP API, no SDK).

    ``store_id`` and ``model_id`` are created by ``practicehub openfga init`` and kept in the hub's settings."""

    def __init__(self, url: str, store_id: str, model_id: str, token: str | None = None):
        self.url, self.store_id, self.model_id, self.token = url.rstrip("/"), store_id, model_id, token

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(self.url + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {self.token}"} if self.token else {})})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read() or b"{}")

    @staticmethod
    def _key(t):
        return {"object": t[0], "relation": t[1], "user": t[2]}

    def write(self, tuples):
        for i in range(0, len(tuples), 100):  # OpenFGA takes up to 100 writes per call
            chunk = [t for t in tuples[i:i + 100] if not self._exists(t)]
            if chunk:
                self._call("POST", f"/stores/{self.store_id}/write",
                           {"authorization_model_id": self.model_id,
                            "writes": {"tuple_keys": [self._key(t) for t in chunk]}})

    def _exists(self, t) -> bool:
        return bool(self.read(obj=t[0], relation=t[1], subject=t[2]))

    def delete(self, tuples):
        for t in tuples:
            if self._exists(t):
                self._call("POST", f"/stores/{self.store_id}/write",
                           {"authorization_model_id": self.model_id, "deletes": {"tuple_keys": [self._key(t)]}})

    def read(self, *, obj=None, relation=None, subject=None):
        # OpenFGA's read filters by object (optionally relation and user); without an object it returns every tuple,
        # which we filter here. The hub's tuple count is small (roles, data classes, workflows).
        key = {}
        if obj is not None:
            key = {"object": obj, **({"relation": relation} if relation else {}), **({"user": subject} if subject else {})}
        out, token = [], ""
        while True:
            body = {"page_size": 100, **({"tuple_key": key} if key else {}),
                    **({"continuation_token": token} if token else {})}
            res = self._call("POST", f"/stores/{self.store_id}/read", body)
            for t in res.get("tuples", []):
                k = t["key"]
                if (relation is None or k["relation"] == relation) and (subject is None or k["user"] == subject):
                    out.append((k["object"], k["relation"], k["user"]))
            token = res.get("continuation_token") or ""
            if not token:
                return sorted(out)

    def check(self, user, relation, obj):
        res = self._call("POST", f"/stores/{self.store_id}/check",
                         {"authorization_model_id": self.model_id,
                          "tuple_key": {"user": user, "relation": relation, "object": obj}})
        return bool(res.get("allowed"))


def model_json() -> dict:
    """AUTHZ_MODEL_DSL as the JSON OpenFGA's write-authorization-model API takes (no DSL parser needed)."""
    def this():
        return {"this": {}}

    def union(*children):
        return {"union": {"child": list(children)}}

    def computed(rel):
        return {"computedUserset": {"relation": rel}}

    def direct(*types):
        out = []
        for t in types:
            if "#" in t:
                typ, rel = t.split("#")
                out.append({"type": typ, "relation": rel})
            else:
                out.append({"type": t})
        return {"directly_related_user_types": out}

    return {
        "schema_version": "1.1",
        "type_definitions": [
            {"type": "member", "relations": {"sponsor": this()}, "metadata": {"relations": {"sponsor": direct("member")}}},
            {"type": "role", "relations": {"assignee": this()}, "metadata": {"relations": {"assignee": direct("member")}}},
            {"type": "practice",
             "relations": {"owner": this(), "policy_owner": union(this(), computed("owner")),
                           "member_admin": union(this(), computed("owner")), "break_glass": this()},
             "metadata": {"relations": {"owner": direct("member"), "policy_owner": direct("member", "role#assignee"),
                                        "member_admin": direct("member", "role#assignee"),
                                        "break_glass": direct("member", "role#assignee")}}},
            {"type": "data_class",
             "relations": {"writer": this(), "reader": union(this(), computed("writer"))},
             "metadata": {"relations": {"writer": direct("member", "role#assignee"),
                                        "reader": direct("member", "role#assignee")}}},
            {"type": "workflow", "relations": {"starter": this()},
             "metadata": {"relations": {"starter": direct("member", "role#assignee")}}},
            {"type": "channel", "relations": {"sender": this()},
             "metadata": {"relations": {"sender": direct("member")}}},
        ],
    }
