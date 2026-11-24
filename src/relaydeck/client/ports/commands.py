"""Command errors shared by the client interfaces."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from relaydeck.client.models.commands.wording import (
    EARLIER_UNCONFIRMED_REQUEST,
    earlier_unconfirmed,
)
from relaydeck.client.ports.transport import TransportError


class RecoveryError(TransportError):
    def __init__(self, detail: str) -> None:
        super().__init__("command_recovery", detail, False)


class UnconfirmedEarlier(RecoveryError):
    """A new send is refused because an earlier one to the same target is not confirmed. `check`
    re-reads the earlier outcome; the transport that submits supplies it.
    """

    def __init__(
        self,
        kind: str,
        check: Callable[[], Awaitable[None]] | None = None,
        standing: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(
            earlier_unconfirmed("") if kind == "agent.prompt" else EARLIER_UNCONFIRMED_REQUEST
        )
        self.kind = kind
        self.check = check
        self.standing = standing


class CommandOutcomeUnknown(RuntimeError):
    """No terminal outcome was proven. Keep the draft and reconcile only."""


class CommandRecoveryUnavailable(CommandOutcomeUnknown):
    """Resolution cannot prove this outcome; stop the current reconciliation attempt."""


class CommandRefused(RuntimeError):
    def __init__(self, code: str, record: dict[str, Any] | None = None) -> None:
        self.code = code
        self.record = record
        self.message = f"Command refused ({code})."
        if record is not None and record.get("kind") == "agent.prompt" and code == "expired":
            self.message = "Prompt was not sent. Your draft is kept; you can send it again."
        elif (
            record is not None
            and record.get("kind") == "agent.prompt"
            and code == "agent_not_ready"
        ):
            self.message = (
                "Agent is not ready. Your draft is kept; send it when the agent is ready."
            )
        super().__init__(self.message)


class CommandStatusSource(Protocol):
    """Read a previously submitted command without submitting another."""

    async def command_status(self, host_id: str, command_id: str) -> dict[str, Any] | None: ...
