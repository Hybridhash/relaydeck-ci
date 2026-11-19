"""Server-health snapshots supplied by application composition."""

from __future__ import annotations

from typing import Any, Protocol


class ServerHealthSource(Protocol):
    async def metrics(self) -> dict[str, Any]: ...
