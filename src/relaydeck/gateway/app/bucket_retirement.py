"""Application-owned, expiry-driven retirement of live token budgets."""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import Callable
from contextlib import suppress

from relaydeck.gateway.http.rate_limits import RateLimiter


class BucketRetirement:
    def __init__(self, now_ms: Callable[[], int]) -> None:
        self._now_ms = now_ms
        self._budgets: dict[int, tuple[weakref.ReferenceType[RateLimiter], int | None]] = {}
        self._changed = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._active = False

    def register(self, budget: RateLimiter) -> None:
        key = id(budget)
        current = self._budgets.get(key)
        if current is not None and current[0]() is budget:
            return

        def forgotten(reference: weakref.ReferenceType[RateLimiter]) -> None:
            entry = self._budgets.get(key)
            if entry is not None and entry[0] is reference:
                self._budgets.pop(key)
                self._wake()

        token = budget.subscribe(self._wake) if self._active else None
        self._budgets[key] = (weakref.ref(budget, forgotten), token)
        self._wake()

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        for key, (reference, token) in tuple(self._budgets.items()):
            budget = reference()
            if budget is not None and token is None:
                self._budgets[key] = (reference, budget.subscribe(self._wake))
        self._wake()

    async def stop(self) -> None:
        self._active = False
        task = self._task
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            self._task = None
        for key, (reference, token) in tuple(self._budgets.items()):
            budget = reference()
            if budget is not None and token is not None:
                budget.unsubscribe(token)
            self._budgets[key] = (reference, None)
        self._changed.clear()

    def _wake(self) -> None:
        self._changed.set()
        if self._active and self._task is None:
            if any(
                (budget := ref()) is not None and budget.next_expiry_ms is not None
                for ref, _token in self._budgets.values()
            ):
                self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            while self._active:
                self._changed.clear()
                delays = []
                for reference, _token in tuple(self._budgets.values()):
                    budget = reference()
                    if budget is None:
                        continue
                    now = self._now_ms()
                    budget.retire(now)
                    expiry = budget.next_expiry_ms
                    if expiry is not None:
                        delays.append(max(0.001, (expiry - now) / 1000))
                budget = None
                # No retained expiring keys means no background wakeups.
                if not delays:
                    return
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout=min(delays))
                except TimeoutError:
                    pass
        finally:
            self._task = None
