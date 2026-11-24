"""Owned subscription reads: one pending read per stream, without idle polling."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any


class SubscriptionReads:
    def __init__(self) -> None:
        self.tasks: dict[Any, asyncio.Task[tuple[str, dict[str, Any]]]] = {}

    async def wait(self, subscriptions: Iterable[Any], timeout: float | None) -> bool:
        subscriptions = tuple(subscriptions)
        for sub in tuple(self.tasks):
            if sub not in subscriptions:
                task = self.tasks.pop(sub)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        for sub in subscriptions:
            if sub not in self.tasks:
                self.tasks[sub] = asyncio.create_task(sub.__anext__())
        if not self.tasks:
            return False
        ready, _ = await asyncio.wait(
            self.tasks.values(), timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        return bool(ready)

    async def cancel_pending(self) -> None:
        pending = [sub for sub, task in self.tasks.items() if not task.done()]
        tasks = [self.tasks.pop(sub) for sub in pending]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        tasks, self.tasks = tuple(self.tasks.values()), {}
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
