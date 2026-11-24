"""Terminal controller and gateway operation interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol


class TerminalController(Protocol):
    def on_input_failure(self, generation: int, sequence: int, /) -> None: ...

    def next_input_identity(self) -> tuple[int, int]: ...

    async def send_paste_chunk(self, data: bytes) -> None: ...

    def paste_generation(self) -> int | None: ...


class ShellOpError(Exception):
    """Typed ownership-operation failure. `code` is the gateway envelope
    code (`lease_conflict`, `lease_expired`, `not_found`, ...); `details`
    carries the envelope details (owner/holder disclosure) verbatim."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = dict(details or {})


@dataclass(frozen=True)
class AttachmentInfo:
    attachment_id: str
    host_id: str
    terminal_id: str
    kind: str
    generation: int
    epoch: str
    owner_device_id: str
    owner_label: str
    takeover_authorized: bool

    @classmethod
    def from_wire(cls, doc: dict[str, Any]) -> AttachmentInfo:
        owner = doc.get("owner", {})
        if not isinstance(owner, dict):
            owner = {}
        return cls(
            attachment_id=str(doc.get("attachment_id", "")),
            host_id=str(doc.get("host_id", "")),
            terminal_id=str(doc.get("terminal_id", "")),
            kind=str(doc.get("kind", "")),
            generation=int(doc.get("generation", 0)),
            epoch=str(doc.get("epoch", "")),
            owner_device_id=str(owner.get("device_id", "")),
            owner_label=str(owner.get("label", "")),
            takeover_authorized=bool(doc.get("takeover_authorized", False)),
        )

    @property
    def owner_display(self) -> str:
        """Human-readable owner for the conflict banner: the
        disclosed label, falling back to the device id, never empty."""
        return self.owner_label or self.owner_device_id or "another device"


def owner_display_from_details(details: dict[str, Any]) -> str:
    """Owner label from a `lease_conflict` envelope (`details.owner` or
    the control-lease `details.holder`). Never empty, never cross-account
    (the gateway scrubs before disclosure)."""
    owner = details.get("owner")
    if isinstance(owner, dict):
        label = str(owner.get("label", "") or "")
        device = str(owner.get("device_id", "") or "")
        if label or device:
            return label or device
    holder = details.get("holder")
    if isinstance(holder, dict):
        device = str(holder.get("device_id", "") or "")
        if device:
            return device
    return "another device"


@dataclass(frozen=True)
class LeaseInfo:
    lease_id: str
    attachment_id: str
    host_id: str
    terminal_id: str
    mode: str
    generation: int
    epoch: str
    expires_at: str

    @classmethod
    def from_wire(cls, doc: dict[str, Any]) -> LeaseInfo:
        return cls(
            lease_id=str(doc.get("lease_id", "")),
            attachment_id=str(doc.get("attachment_id", "")),
            host_id=str(doc.get("host_id", "")),
            terminal_id=str(doc.get("terminal_id", "")),
            mode=str(doc.get("mode", "")),
            generation=int(doc.get("generation", 0)),
            epoch=str(doc.get("epoch", "")),
            expires_at=str(doc.get("expires_at", "")),
        )


class ShellGatewayOps(ABC):
    """Ownership operations one terminal detail needs. Production uses
    `HttpxShellGatewayOps`; headless UI tests use `ScriptedShellGatewayOps`."""

    @property
    def picture_api(self) -> Any:
        return None

    @abstractmethod
    async def attach(
        self,
        host_id: str,
        terminal_id: str,
        kind: str,
        takeover: bool,
        cols: int = 80,
        rows: int = 24,
        *,
        expected_control_revision: str | None = None,
    ) -> AttachmentInfo: ...

    @abstractmethod
    async def acquire_lease(
        self,
        attachment_id: str,
        mode: str,
        *,
        expected_generation: int | None = None,
    ) -> LeaseInfo: ...

    async def control_holder(self, host_id: str, terminal_id: str) -> dict[str, Any]:
        """Read-only control state ({"held", "mine"}). Implementations that
        cannot tell raise, and callers treat that as "ask first"."""
        raise ShellOpError("unsupported_capability", "control holder unknown")

    @abstractmethod
    async def terminal_ticket(self, lease_id: str) -> str: ...

    @abstractmethod
    async def release_lease(self, lease_id: str) -> None: ...

    @abstractmethod
    async def close_attachment(self, attachment_id: str) -> None: ...

    @abstractmethod
    async def close_terminal(self, command: dict[str, Any]) -> dict[str, Any]:
        """Submit the session.close command. One attempt; an
        unknown outcome is shown to the owner and never replayed."""


class KeyWriter(Protocol):
    """The live writer identity and exact-byte key path."""

    @property
    def lease_id(self) -> str: ...

    def key_generation(self) -> int | None: ...

    async def send_key_bytes(self, data: bytes) -> None: ...
