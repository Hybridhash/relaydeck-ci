"""How a failed request for the open commit is told to the reader."""

from __future__ import annotations

from typing import Literal

# The graph this commit was opened from can no longer vouch for it, or the retained comparison and
# its cursors are gone: only reading the graph again helps.
GONE = frozenset(
    {"stale_snapshot", "stale_revision", "expired", "malformed", "mismatch", "not_found"}
)
BUSY = frozenset({"busy", "rate_limited"})
GONE_TEXT = "This commit is no longer available. Refresh the graph."
BUSY_TEXT = "The Mac is busy. Try again in a moment."

Kind = Literal["gone", "busy", "other"]


def kind(reason: str | None) -> Kind:
    if reason in GONE:
        return "gone"
    return "busy" if reason in BUSY else "other"
