"""Rename operations supplied by the application."""

from __future__ import annotations

from typing import Any, Protocol


class RenameSource(Protocol):
    async def submit_command(self, command: dict[str, Any]) -> dict[str, Any]: ...

    async def command_status(self, host_id: str, command_id: str) -> dict[str, Any] | None: ...
