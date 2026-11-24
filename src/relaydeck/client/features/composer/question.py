"""Conservative occupancy and full-payload installation for contextual questions."""

from __future__ import annotations

from typing import Any


def unresolved(store: Any) -> bool:
    return (
        store.submission in ("sending", "unknown")
        or store.pending_blocked is not None
        or any(message.state.kind in ("sending", "unknown") for message in store.messages)
    )


def occupied(store: Any) -> bool:
    return bool(store.draft or store.markers or store.pictures or unresolved(store))


def install(store: Any, text: str, *, replace: bool = False) -> bool:
    if unresolved(store) or occupied(store) and not replace:
        return False
    store.import_draft({"draft": text, "markers": [], "pictures": [], "prompt_only": True})
    return True
