"""Pending command vocabulary and bounds."""

MAX_RECORDS = 128

MAX_BYTES = 131072

KINDS = {"agent.prompt", "agent.rename", "agent.start", "shell.create", "tab.create"}

OUTCOMES = {"pending", "accepted", "dispatched", "unknown", "succeeded", "failed", "rejected"}

FINAL = {"succeeded", "failed", "rejected"}

IN_FLIGHT = {"pending", "accepted", "dispatched"}

DISMISSED = "dismissed"  # outcome listeners only; never stored

RELEASED, DISMISSABLE, WAITING = "released", "dismissable", "waiting"

REVIEW_GRACE_MS = 15_000  # an ordinary send confirms well within this; only a stuck one is shown

IDENTITIES = {"workspace_id", "tab_id", "pane_id", "agent_id"}
