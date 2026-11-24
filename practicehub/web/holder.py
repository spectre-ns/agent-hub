"""Who holds a piece of work: one answer, said the same way on every page.

A piece of work is held by the member who took it (``claimed_by``), else by the member it was given to
(``assignee``); with neither, it waits for anyone in its role. Finished work is held by nobody: it was done by the
member who finished it. The item page, the board, Your work and the words of a refusal all ask here, so they never
disagree about whose it is.
"""
from __future__ import annotations

from html import escape as e

OPEN = ("open", "claimed", "held", "waiting")


def holder(item: dict) -> dict:
    """``{"member": id | None, "how": "taken" | "given" | "done" | None, "role": role id | None}``.

    ``how`` is "taken" when the member took it, "given" when it was given to them and nobody took it yet, and "done"
    for finished work (``member`` is then who finished it). With no member, the work waits for its ``role``."""
    role = item.get("role") or None
    if item.get("status") in ("done", "cancelled"):
        who = item.get("done_by") or item.get("claimed_by")
        return {"member": who, "how": "done" if who else None, "role": role}
    if item.get("claimed_by"):
        return {"member": item["claimed_by"], "how": "taken", "role": role}
    if item.get("assignee"):
        return {"member": item["assignee"], "how": "given", "role": role}
    return {"member": None, "how": None, "role": role}


class HolderWords:
    """Mixed into the App: the holder in words, for pages (HTML) and for refusals (plain text)."""

    def _holder_role(self, role: str | None) -> str:
        if not role:
            return ""
        try:
            return self.hub.role(role).get("name") or role
        except Exception:  # noqa: BLE001 - a role since removed
            return role

    def holder_text(self, item: dict) -> str:
        """Plain words: "Noor Haddad", "Noor Haddad (given to them)", "anyone in Nurse", or "nobody yet"."""
        h = holder(item)
        if h["member"]:
            name = self.member_name(h["member"]) or h["member"]
            return name + (" (given to them, not yet taken)" if h["how"] == "given" else "")
        if h["role"]:
            return f"anyone in {self._holder_role(h['role'])}"
        return "nobody yet"

    def holder_html(self, item: dict, *, avatar: bool = True) -> str:
        """For a details list or a table cell: the avatar and name, or what the work waits for."""
        h = holder(item)
        if h["member"]:
            how = {"given": ' <span class="muted">given, not yet taken</span>',
                   "done": ' <span class="muted">finished it</span>'}.get(h["how"], "")
            return (self.who(h["member"]) if avatar else e(self.member_name(h["member"]) or h["member"])) + how
        if h["role"]:
            return f'<span class="muted">Waiting for</span> {e(self._holder_role(h["role"]))}'
        return '<span class="muted">Nobody yet</span>'

    def holder_refusal(self, me: dict, item: dict, why: str) -> str:
        """A refusal's words that name who holds the work, when it is someone else: "it is assigned to someone
        else" says nothing a person can act on; "it is held by Noor Haddad" does."""
        h = holder(item)
        if h["member"] and h["member"] != me.get("id") and h["how"] != "done":
            name = self.member_name(h["member"]) or h["member"]
            if name not in why:
                why = f"{why.rstrip('.')}: it is held by {name}" + (" (given to them, not yet taken)"
                                                                     if h["how"] == "given" else "")
        return why

    def holder_avatar(self, item: dict) -> str:
        """A board tile's avatar: the holder's, or an empty one that says which role it waits for."""
        h = holder(item)
        if h["member"]:
            return self._av(h["member"])
        words = f"Waiting for {self._holder_role(h['role'])}" if h["role"] else "Nobody has taken this yet"
        return f'<span class="av empty" role="img" title="{e(words)}" aria-label="{e(words)}">?</span>'
