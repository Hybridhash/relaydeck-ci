"""Pure results of terminal transport recovery."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResumeOutcome:
    """Report verified same-engine recovery, incomplete continuity or an unattached no-op.
    Incomplete recovery requires the caller to reset as a watcher.
    """

    status: str
    reason: str
    resent_bytes: int = 0
