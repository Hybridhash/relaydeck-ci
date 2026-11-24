"""Capabilities used while coordinating a terminal control transition."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from relaydeck.client.features.terminal.controller import TerminalController


class TransitionSession(Protocol):
    _disclosure_armed: bool
    _takeover_flight: asyncio.Future[str] | None

    @property
    def controller(self) -> TerminalController | None: ...

    @property
    def phase(self) -> str: ...

    def _notify(self) -> None: ...

    def expect_control(self, expected: bool = True) -> None: ...

    async def _auto_take_control(self, token: object) -> str: ...

    async def _confirm_takeover(
        self, token: object, *, expected_control_revision: str | None = None
    ) -> str: ...
