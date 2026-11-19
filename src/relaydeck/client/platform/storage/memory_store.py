"""In-memory command identity storage."""

from __future__ import annotations

from relaydeck.contracts.storage import CredentialStore


class MemoryCommandStore(CredentialStore):
    """Server-session/headless fallback; native always supplies secure storage."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def available(self) -> bool:
        return True

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str) -> None:
        self.data[key] = value

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)

    async def clear_account(self) -> None:
        self.data.clear()
