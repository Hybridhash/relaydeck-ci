"""Define notification permission, token and opaque tap-reference delivery."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class PushTap:
    """Opaque notification tap reference. Contains no transcript, path,
    command, secret, or decision."""

    notification_id: str
    event_ref: str
    category: str


class PushPermission(Enum):
    GRANTED = "granted"
    DENIED = "denied"
    UNKNOWN = "unknown"


class NotificationPort(ABC):
    @abstractmethod
    async def permission(self) -> PushPermission: ...

    @abstractmethod
    async def request_permission(self) -> PushPermission:
        """Ask once; denial leaves the app usable."""

    @abstractmethod
    async def token(self) -> str | None:
        """Current provider token, or None when unavailable/denied."""

    @abstractmethod
    async def revoke(self) -> None:
        """Delete the local provider token; server logout revokes its subscription."""

    @abstractmethod
    def on_token_refresh(self, listener: Callable[[str], None]) -> Callable[[], None]: ...

    @abstractmethod
    def on_tap(self, listener: Callable[[PushTap], None]) -> Callable[[], None]:
        """Foreground/background tap delivery. Tapping never executes a
        command; the app resolves the opaque ref after login."""

    @abstractmethod
    def on_app_link(self, listener: Callable[[str], None]) -> Callable[[], None]: ...

    @abstractmethod
    async def initial_tap(self) -> PushTap | None: ...
