"""Inventory operations supplied by the application."""

from __future__ import annotations

from typing import Any, Protocol


class InventorySource(Protocol):
    async def agents(
        self,
        host_id: str,
        limit: int = 50,
        page_token: str = "",
        *,
        project: str = "",
        status: str = "",
        query: str = "",
    ) -> dict[str, Any]: ...

    async def mark_agent_read(
        self, host_id: str, agent_id: str, host_epoch: str, activity_id: str
    ) -> dict[str, Any]: ...
