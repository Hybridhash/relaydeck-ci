"""Persist and restore composer drafts through secure storage, reporting capability failures
without plaintext fallback.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from relaydeck.contracts.storage import CredentialStore

#: Prefix (adapter-safe form) for pause-persisted draft keys.
DRAFT_KEY_PREFIX = "draft-"

#: Capability failure surfaced when secure storage cannot take the draft.
OUTAGE_MESSAGE = (
    "Secure storage unavailable: draft held in memory only (capability failure; never plaintext)."
)


def draft_key(agent_id: str) -> str:
    """Secure-storage key for one agent surface's pause-persisted draft."""
    return f"{DRAFT_KEY_PREFIX}{agent_id}"


@dataclass(frozen=True)
class PausePersistReport:
    """What `pause_persist` did with one draft snapshot."""

    key: str
    persisted: bool
    held_snapshot: dict[str, Any] | None
    capability_error: str | None


async def pause_persist(
    store: CredentialStore, key: str, snapshot: dict[str, Any]
) -> PausePersistReport:
    """Persist a draft snapshot only through secure storage. On failure, return an in-memory
    held snapshot and capability error without writing a plaintext fallback.
    """
    try:
        if not await store.available():
            return PausePersistReport(key, False, dict(snapshot), OUTAGE_MESSAGE)
        await store.set(key, json.dumps(snapshot))
    except Exception as exc:
        return PausePersistReport(key, False, dict(snapshot), f"{OUTAGE_MESSAGE} ({exc})")
    return PausePersistReport(key, True, None, None)


@dataclass(frozen=True)
class ResumeRestoreReport:
    """What `resume_restore` found under one draft key."""

    key: str
    snapshot: dict[str, Any] | None
    found: bool
    capability_error: str | None


async def resume_restore(store: CredentialStore, key: str) -> ResumeRestoreReport:
    """Read one pause-persisted snapshot back for `import_draft`. A missing key reports
    `found=False` with no error (nothing was persisted).
    """
    try:
        if not await store.available():
            return ResumeRestoreReport(key, None, False, OUTAGE_MESSAGE)
        raw = await store.get(key)
    except Exception as exc:
        return ResumeRestoreReport(key, None, False, f"{OUTAGE_MESSAGE} ({exc})")
    if raw is None:
        return ResumeRestoreReport(key, None, False, None)
    try:
        doc = json.loads(raw)
    except ValueError:
        return ResumeRestoreReport(key, None, False, "stored draft snapshot is corrupt")
    if not isinstance(doc, dict):
        return ResumeRestoreReport(key, None, False, "stored draft snapshot is corrupt")
    return ResumeRestoreReport(key, doc, True, None)
