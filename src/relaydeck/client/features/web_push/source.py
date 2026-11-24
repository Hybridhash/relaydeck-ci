"""WebPush operations supplied by the application."""

from __future__ import annotations

from typing import Protocol


class WebPushSource(Protocol):
    async def web_push_state(self) -> dict[str, bool]: ...

    async def web_push_preferences(self) -> list[str]: ...

    async def set_web_push_preferences(self, categories: list[str]) -> list[str]: ...

    async def web_push_disable(self) -> None: ...
