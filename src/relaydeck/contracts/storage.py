"""Secure key-value storage interface shared by the native app and the Mac side."""

from __future__ import annotations

from abc import ABC, abstractmethod


class CredentialStore(ABC):
    """Secure key-value storage. No plaintext fallback: when secure
    storage is unavailable the adapter raises instead of downgrading."""

    @abstractmethod
    async def available(self) -> bool: ...

    @abstractmethod
    async def get(self, key: str) -> str | None: ...

    @abstractmethod
    async def set(self, key: str, value: str) -> None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def clear_account(self) -> None:
        """Remove every key owned by this app (logout/revoke cleanup)."""
