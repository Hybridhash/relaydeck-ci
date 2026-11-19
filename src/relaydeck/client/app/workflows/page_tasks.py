"""Connection-bound scheduling without constructing coroutines for detached pages."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)


def connection_for(page: Any) -> Any:
    try:
        return page.session.connection
    except (AttributeError, RuntimeError):
        return None


class PageTasks:
    def __init__(self, page: Any, lost: Callable[[], None]) -> None:
        self.page = page
        self.connection = connection_for(page)
        self.loop = asyncio.get_running_loop()
        self.lost = lost
        self.closed = False
        self.unsubscribe: Callable[[], None] | None = None
        self.tasks: set[asyncio.Task[Any]] = set()

    def live(self) -> bool:
        return (
            not self.closed
            and self.connection is not None
            and connection_for(self.page) is self.connection
            and not self.loop.is_closed()
            and self.loop.is_running()
        )

    def watch(self, session: Any, callback: Callable[[], Coroutine[Any, Any, Any]]) -> None:
        if self.live():
            self.unsubscribe = session.subscribe(lambda _: self.run(callback))

    def run(self, callback: Callable[[], Coroutine[Any, Any, Any]]) -> None:
        if self.closed:
            return
        if not self.live():
            self.lost()
            self.close()
            return
        try:
            self.loop.call_soon_threadsafe(self.start, callback)
        except RuntimeError:
            self.lost()
            self.close()

    def start(self, callback: Callable[[], Coroutine[Any, Any, Any]]) -> None:
        if self.closed:
            return
        if not self.live():
            self.lost()
            self.close()
            return
        task: asyncio.Task[Any] = self.loop.create_task(self.execute(callback))
        self.tasks.add(task)
        task.add_done_callback(self.finished)

    async def execute(self, callback: Callable[[], Coroutine[Any, Any, Any]]) -> None:
        if self.closed:
            return
        if not self.live():
            self.lost()
            self.close()
            return
        await callback()

    def finished(self, task: asyncio.Task[Any]) -> None:
        self.tasks.discard(task)
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                logger.warning("page_task_failed type=%s", type(exc).__name__)

    def close(self) -> None:
        self.closed = True
        if self.unsubscribe is not None:
            self.unsubscribe()
            self.unsubscribe = None
        # Already-claimed journal sends finish through their existing session sender.
