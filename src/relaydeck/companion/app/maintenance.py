"""Storage expiry work follows writes and deadlines; diagnostics stay infrequent."""

from __future__ import annotations

import asyncio
from typing import Any

from relaydeck.host.concurrency.deadlines import DeadlineWake
from relaydeck.host.persistence.contracts import StorageUnavailable

DIAGNOSTIC_INTERVAL_S = 30.0


async def maintain_storage(runtime: Any) -> None:
    store, stop = runtime.persistence, runtime.stop
    signal = DeadlineWake()
    signal.bind()
    notify = signal.wake
    store.on_change = notify
    loop = asyncio.get_running_loop()
    diagnostic = 0.0
    due: int | None = runtime.gateway.now_ms()
    dirty = True
    try:
        while not stop.is_set():
            signal.event.clear()
            try:
                now = runtime.gateway.now_ms()
                more = False
                if due is not None and due <= now:
                    report = await asyncio.to_thread(store.sweep, now)
                    more = report.more
                    dirty = True
                if loop.time() >= diagnostic:
                    runtime.persistence_status = await asyncio.to_thread(store.status)
                    diagnostic = loop.time() + DIAGNOSTIC_INTERVAL_S
                if stop.is_set():
                    break
                if dirty:
                    due = await asyncio.to_thread(store.next_expiry_ms)
                delay = max(0.001, diagnostic - loop.time())
                if more:
                    due = now + 100
                if due is not None:
                    delay = min(delay, max(0.001, (due - runtime.gateway.now_ms()) / 1000))
            except StorageUnavailable as exc:
                runtime.persistence_status = exc.status
                due = runtime.gateway.now_ms() + 30_000
                delay = 30.0
            await signal.wait(stop, delay)
            dirty = signal.event.is_set() and not more
    finally:
        if store.on_change is notify:
            store.on_change = None
