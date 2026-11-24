"""Store namespaced credentials through the native secure-storage adapter. Every operation
raises when storage is unavailable, and callers must report that failure without falling back
to plaintext.
"""

from __future__ import annotations

from flet_secure_storage.secure_storage import SecureStorage
from flet_secure_storage.types import AndroidOptions

from relaydeck.contracts.storage import CredentialStore

KEY_PREFIX = "relaydeck."


class SecureStorageUnavailable(RuntimeError):
    """Secure storage is missing or unusable on this device/browser."""


class FletSecureCredentialStore(CredentialStore):
    def __init__(
        self,
        service: SecureStorage,
        android: AndroidOptions | None = None,
    ) -> None:
        self._service = service
        self._android = android or AndroidOptions()

    def _key(self, key: str) -> str:
        if not key or not key.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"bad credential key {key!r}")
        return f"{KEY_PREFIX}{key}"

    async def available(self) -> bool:
        try:
            result = await self._service.get_availability()
        except Exception:  # noqa: BLE001 - any failure means unavailable
            return False
        return result is True

    async def _require_available(self) -> None:
        if not await self.available():
            raise SecureStorageUnavailable("secure storage unavailable")

    async def get(self, key: str) -> str | None:
        await self._require_available()
        value: str | None = await self._service.get(self._key(key), android=self._android)
        return value

    async def set(self, key: str, value: str) -> None:
        await self._require_available()
        await self._service.set(self._key(key), value, android=self._android)

    async def delete(self, key: str) -> None:
        await self._require_available()
        await self._service.remove(self._key(key), android=self._android)

    async def clear_account(self) -> None:
        await self._require_available()
        owned = await self._service.get_all(android=self._android)
        for stored in owned:
            if stored.startswith(KEY_PREFIX):
                await self._service.remove(stored, android=self._android)
