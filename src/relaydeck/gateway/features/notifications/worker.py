"""One gateway-lifespan owner of the synchronous durable outbox pump."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

from relaydeck.gateway.features.notifications.ports import OutboxStore
from relaydeck.gateway.features.notifications.service import ProviderPort, pump_outbox

# Empty-outbox waits double up to this multiple of the worker interval.
IDLE_WAIT_FACTOR = 32
# While rows are pending, re-check at least this often even when the next
# attempt is far out, so clock changes are picked up promptly.
PENDING_RECHECK_MAX_S = 1.0


class PushWorker:
    def __init__(
        self,
        store: OutboxStore,
        provider: ProviderPort,
        *,
        interval: float = 1.0,
        authorized: Callable[[dict[str, Any]], bool] = lambda row: True,
    ) -> None:
        if not 0.01 <= interval <= 60:
            raise ValueError("Invalid push interval")
        self.store = store
        self.provider = provider
        self.authorized = authorized
        self.interval = interval
        self._stop = threading.Event()
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.task: asyncio.Task[None] | None = None
        self.report: dict[str, int] = {}
        self.error = ""

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._run())

    def wake(self) -> None:
        """Ask for a prompt pump pass after new work was enqueued."""
        loop = self._loop
        if loop is None or loop.is_closed():
            self._wake.set()
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._wake.set()
        else:
            loop.call_soon_threadsafe(self._wake.set)

    def _pump_once(self) -> tuple[dict[str, int], int | None, int]:
        report = pump_outbox(
            self.store,
            self.provider,
            limit=10,
            stop=self._stop.is_set,
            authorized=self.authorized,
        )
        return report, self.store.next_due_ms(), self.store.now()

    async def _run(self) -> None:
        self._loop = asyncio.get_running_loop()
        idle_passes = 0
        try:
            while not self._stop.is_set():
                # Clear before pumping: a wake during the pump then makes
                # the wait below return at once instead of being lost.
                self._wake.clear()
                next_due_ms: int | None = None
                store_now_ms = 0
                try:
                    self.report, next_due_ms, store_now_ms = await asyncio.to_thread(
                        self._pump_once
                    )
                    self.error = ""
                    worked = any(self.report.values())
                except Exception:  # noqa: BLE001 - bounded recovery, no storage/provider content
                    self.error = "push_store_unavailable"
                    worked = True  # Retry at the base interval after a pump failure.
                if self._stop.is_set():
                    break
                if worked or next_due_ms is not None:
                    idle_passes = 0
                if next_due_ms is not None:
                    wait = min(
                        max(0.0, (next_due_ms - store_now_ms) / 1000),
                        max(PENDING_RECHECK_MAX_S, self.interval),
                    )
                else:
                    wait = min(
                        self.interval * IDLE_WAIT_FACTOR,
                        self.interval * (2 ** min(idle_passes, 5)),
                    )
                    if not worked:
                        idle_passes += 1
                try:
                    async with asyncio.timeout(wait):
                        await self._wake.wait()
                except TimeoutError:
                    pass
        finally:
            self._loop = None

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self.task is not None:
            # Do not cancel to_thread while its provider/store are still in use.
            await self.task
        close: Any = getattr(self.provider, "close", None)
        if callable(close):
            close()
