"""Token budgets with expiry only after complete replenishment."""

from __future__ import annotations

import heapq
import math
from collections.abc import Callable
from dataclasses import dataclass, field

MAX_COMMANDS_PER_MINUTE = 20
MAX_INPUT_BYTES_PER_SECOND = 100 * 1024


@dataclass
class RateLimiter:
    """Token bucket per key. All time comes from the caller's clock."""

    capacity: float
    refill_per_ms: float
    _buckets: dict[str, tuple[float, int]] = field(default_factory=dict)
    _expires: dict[str, int] = field(default_factory=dict)
    _deadlines: list[tuple[int, str]] = field(default_factory=list)
    _listeners: dict[int, Callable[[], None]] = field(default_factory=dict)
    _serial: int = 0

    def check(self, key: str, now_ms: int, cost: float = 1.0) -> bool:
        self.retire(now_ms)
        tokens, updated = self._buckets.get(key, (self.capacity, now_ms))
        tokens = min(self.capacity, tokens + (now_ms - updated) * self.refill_per_ms)
        allowed = tokens >= cost
        if allowed:
            tokens -= cost
        self._buckets[key] = (tokens, now_ms)
        if self.refill_per_ms > 0:
            deadline = now_ms + max(0, math.ceil((self.capacity - tokens) / self.refill_per_ms))
            self._expires[key] = deadline
            heapq.heappush(self._deadlines, (deadline, key))
            self._compact()
        self._notify()
        return allowed

    def retry_after_s(self, key: str, now_ms: int, cost: float = 1.0) -> int | None:
        if self.refill_per_ms <= 0 or cost > self.capacity:
            return None
        tokens, updated = self._buckets.get(key, (self.capacity, now_ms))
        tokens = min(self.capacity, tokens + (now_ms - updated) * self.refill_per_ms)
        return max(0, math.ceil((cost - tokens) / self.refill_per_ms / 1000))

    @property
    def next_expiry_ms(self) -> int | None:
        while self._deadlines and self._expires.get(self._deadlines[0][1]) != self._deadlines[0][0]:
            heapq.heappop(self._deadlines)
        return self._deadlines[0][0] if self._deadlines else None

    def retire(self, now_ms: int) -> int:
        retired = 0
        while (expiry := self.next_expiry_ms) is not None and expiry <= now_ms:
            deadline, key = heapq.heappop(self._deadlines)
            if self._expires.get(key) != deadline:
                continue
            self._expires.pop(key)
            self._buckets.pop(key, None)
            retired += 1
        if not self._buckets:
            # Clear backing tables too: deleting keys alone retains their allocation.
            self._buckets.clear()
            self._expires.clear()
            self._deadlines.clear()
        if retired:
            self._notify()
        return retired

    def subscribe(self, callback: Callable[[], None]) -> int:
        self._serial += 1
        self._listeners[self._serial] = callback
        return self._serial

    def unsubscribe(self, token: int) -> None:
        self._listeners.pop(token, None)

    def _notify(self) -> None:
        for callback in tuple(self._listeners.values()):
            callback()

    def _compact(self) -> None:
        if len(self._deadlines) > 2 * len(self._expires) + 64:
            self._deadlines = [(deadline, key) for key, deadline in self._expires.items()]
            heapq.heapify(self._deadlines)


def command_limiter() -> RateLimiter:
    return RateLimiter(
        capacity=MAX_COMMANDS_PER_MINUTE, refill_per_ms=MAX_COMMANDS_PER_MINUTE / 60_000
    )


def input_limiter() -> RateLimiter:
    return RateLimiter(
        capacity=MAX_INPUT_BYTES_PER_SECOND, refill_per_ms=MAX_INPUT_BYTES_PER_SECOND / 1000
    )


def retry_details(limiter: object, key: str, now_ms: int) -> dict[str, int]:
    if isinstance(limiter, RateLimiter):
        delay = limiter.retry_after_s(key, now_ms)
        if delay is not None and delay > 0:
            return {"retry_after_s": delay}
    return {}
