"""Define explicit user-initiated clipboard reads and writes."""

from __future__ import annotations

from abc import ABC, abstractmethod


class ClipboardPort(ABC):
    @abstractmethod
    async def get_text(self) -> str | None:
        """Read clipboard text. Called only from explicit user actions;
        never polled in the background."""

    @abstractmethod
    async def set_text(self, value: str) -> None: ...
