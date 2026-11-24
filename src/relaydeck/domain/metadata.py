"""Shared identity and activity facts for bounded inventory deltas."""

import hashlib

from relaydeck.contracts.canonical import canonical_json

AGENT_FIELDS = (
    "terminal_id",
    "workspace_id",
    "tab_id",
    "pane_id",
    "name",
    "agent",
    "title",
    "status",
    "focused",
    "revision",
    "state_change_seq",
    "interactive_ready",
    "launch_pending",
)


def activity_id(epoch: str, event_id: str, terminal_id: str) -> str:
    return hashlib.sha256(canonical_json([epoch, event_id, terminal_id]).encode()).hexdigest()
