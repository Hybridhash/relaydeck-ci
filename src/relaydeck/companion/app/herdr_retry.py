"""When a Herdr source cannot be started: whether waiting can help, and for how long."""

from __future__ import annotations

TRANSIENT = "transient"
INCOMPATIBLE = "incompatible"

WAITING_ISSUE = "Herdr is not answering. The companion connects when it does."
CLEANUP_ISSUE = "Closing the previous Herdr connection is taking a while. The companion retries."
TOOL_ISSUE = (
    "No trusted Herdr command was found, so terminals cannot be opened. Install Herdr in "
    "~/.local/bin (or fix the permissions of its folder), then restart the companion."
)
INCOMPATIBLE_ISSUE = (
    "Herdr is offline or incompatible. Check the socket and protocol 22/version baseline."
)


def classify(error: BaseException) -> str:
    """A missing or refusing socket and a timeout are worth retrying. Anything Herdr did answer
    (a protocol mismatch, a malformed reply) will not change by itself, so it needs the owner."""
    if isinstance(error, TimeoutError | OSError):
        return TRANSIENT
    return INCOMPATIBLE
