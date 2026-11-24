"""Scripted command-status reads for mounted recovery controls."""

from __future__ import annotations

from typing import Any

from relaydeck.client.ports.commands import CommandStatusSource


class StatusSource(CommandStatusSource):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.records: dict[tuple[str, str], dict[str, Any]] = {}

    async def command_status(self, host_id: str, command_id: str) -> dict[str, Any] | None:
        self.calls.append((host_id, command_id))
        return self.records.get((host_id, command_id))
