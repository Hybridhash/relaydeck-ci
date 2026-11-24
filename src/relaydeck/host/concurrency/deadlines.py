"""Thread-safe producer wakes and cancellable nearest-deadline waits."""

from __future__ import annotations

import asyncio


class DeadlineWake:
    def __init__(self) -> None:
        self.event = asyncio.Event()
        self.loop: asyncio.AbstractEventLoop | None = None

    def bind(self) -> None:
        self.loop = asyncio.get_running_loop()

    def wake(self) -> None:
        loop = self.loop
        if loop is None:
            self.event.set()
        else:
            try:
                loop.call_soon_threadsafe(self.event.set)
            except RuntimeError:
                pass  # the owning loop has shut down

    async def wait(self, stop: asyncio.Event | None, delay: float | None) -> None:
        tasks = [asyncio.create_task(self.event.wait())]
        if stop is not None:
            tasks.append(asyncio.create_task(stop.wait()))
        try:
            await asyncio.wait(
                tasks,
                timeout=max(0.001, delay) if delay is not None else None,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
