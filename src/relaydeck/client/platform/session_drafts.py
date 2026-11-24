"""Page-owned draft memory when persistence is deliberately not configured."""

from __future__ import annotations

from relaydeck.contracts.storage import CredentialStore


class SessionDrafts(CredentialStore):
    """RAM only; never substituted for a configured secure adapter that failed."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    async def available(self) -> bool:
        return True

    async def get(self, key: str) -> str | None:
        return self._values.get(key)

    async def set(self, key: str, value: str) -> None:
        self._values[key] = value

    async def delete(self, key: str) -> None:
        self._values.pop(key, None)

    async def clear_account(self) -> None:
        self._values.clear()


def session_drafts(configured: CredentialStore | None) -> CredentialStore:
    return configured if configured is not None else SessionDrafts()
