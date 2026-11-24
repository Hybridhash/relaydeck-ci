"""UpdateNotice operations supplied by the application."""

from __future__ import annotations

from typing import Any, Protocol


class UpdateNoticeSource(Protocol):
    async def updates(self) -> dict[str, Any]: ...
