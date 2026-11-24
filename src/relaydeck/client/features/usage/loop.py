"""Usage refresh lifecycle. Pure state plus one async loop; no Flet."""

from __future__ import annotations

import asyncio
import sys
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, fields, is_dataclass, replace

from relaydeck.client.features.usage.model import UsagePage

INTERVAL_SECONDS = 5.0
# A busy server, a transport timeout or a refusal without a page yet is not a verdict: the screen
# keeps loading and retries with a backoff (1 s, 2 s, 4 s, ... up to RETRY_CAP_SECONDS) until
# UNAVAILABLE_AFTER_SECONDS have passed since the first failure of the current query.
RETRY_CAP_SECONDS = 8.0
UNAVAILABLE_AFTER_SECONDS = 30.0
# Recently loaded pages are kept in memory per query identity, for the current account and host
# only: switching back to a period shows its page at once, marked "updating…" while it refreshes.
CACHE_ENTRIES = 4
CACHE_MAX_AGE_SECONDS = 300.0
CACHE_MAX_BYTES = 32 * 1024 * 1024
UNCHANGED_MAX_SECONDS = 60.0
PAGE_TOO_LARGE = "page_too_large"  # a page too big to retain here (not the server report cap)


def page_bytes(value: object, limit: int | None = None) -> int:
    """Bounded retained-object accounting, only when a new immutable page arrives."""
    seen: set[int] = set()
    pending = [value]
    total = 0
    ceiling = CACHE_MAX_BYTES if limit is None else limit
    while pending and total <= ceiling:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        # Include allocation rounding and per-object overhead beyond the
        # shallow header. Admission deliberately errs toward eviction.
        total += ((sys.getsizeof(item) + 15) // 16) * 16 + 16
        if is_dataclass(item) and not isinstance(item, type):
            pending.extend(getattr(item, field.name) for field in fields(item))
            storage = getattr(item, "__dict__", None)
            if storage is not None:
                pending.append(storage)
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, tuple | list | set | frozenset):
            pending.extend(item)
    return total


TRANSIENT_CODES = frozenset({"usage_busy", "usage_unavailable", "host_offline", "usage_timeout"})

STATUS_IDLE = "idle"
STATUS_LOADING = "loading"
STATUS_READY = "ready"
STATUS_ERROR = "error"  # a failure after a good page: the page stays, with the error
STATUS_UNAVAILABLE = "unavailable"  # no good page yet and the report cannot be produced
STATUS_DISABLED = "disabled"  # the host has usage turned off


@dataclass(frozen=True)
class FeedState:
    status: str = STATUS_IDLE
    page: UsagePage | None = None
    observed_ms: int | None = None
    error: str | None = None
    # The identity-scoped inventory (known spaces and providers) of an over-capacity report. It is
    # not usage: its totals are unknown, but the choices it names stay available.
    inventory: UsagePage | None = None


class UsageFeed:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._key: Hashable | None = None
        self._generation = 0
        self._clock = clock
        self._failures = 0  # consecutive failures of the current query
        self._unchanged = 0
        self._sizes: dict[Hashable, int] = {}
        self._first_failure: float | None = None
        self._scope: Hashable | None = None
        self._too_large_key: Hashable | None = None
        self._cache: OrderedDict[Hashable, tuple[UsagePage, int, float]] = OrderedDict()
        self.state = FeedState()

    @property
    def generation(self) -> int:
        return self._generation

    def _cached(self, key: Hashable) -> FeedState | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        page, observed_ms, stored = entry
        if self._clock() - stored > CACHE_MAX_AGE_SECONDS:
            del self._cache[key]
            self._sizes.pop(key, None)
            return None
        return FeedState(status=STATUS_LOADING, page=page, observed_ms=observed_ms)

    def set_key(self, key: Hashable, scope: Hashable | None = None) -> int:
        """Start a new request generation when identity, query or filters change. Scope changes
        clear cached pages; a query can reuse its own recent page while refreshing.
        """
        if key != self._key:
            self._too_large_key = None  # a rejection never carries to another query
            if scope != self._scope:
                self._cache.clear()
                self._sizes.clear()
                self._scope = scope
            self._key = key
            self._generation += 1
            self._failures = 0
            self._unchanged = 0
            self._first_failure = None
            self.state = self._cached(key) or FeedState(status=STATUS_LOADING)
        return self._generation

    def begin(self) -> int:
        """A refresh starts under the current generation."""
        if self.state.status != STATUS_READY:
            self.state = FeedState(
                status=STATUS_LOADING,
                page=self.state.page,
                observed_ms=self.state.observed_ms,
            )
        return self._generation

    def accept(self, generation: int, page: UsagePage, observed_ms: int) -> bool:
        if generation != self._generation:
            return False
        self._failures = 0
        self._first_failure = None
        self._unchanged = min(4, self._unchanged + 1) if self.state.page is page else 0
        if page.capacity != "ok":
            # Over the server's report cap: the report has no usable totals, so it is unavailable
            # and
            # never zero usage. Its spaces and providers are kept as the inventory for the picker
            # choices.
            self.state = FeedState(
                status=STATUS_UNAVAILABLE, error="capacity_exceeded", inventory=page
            )
            return True
        self.state = FeedState(status=STATUS_READY, page=page, observed_ms=observed_ms, error=None)
        self._too_large_key = None
        if self._key is not None:
            size = (
                self._sizes.get(self._key)
                if self._cache.get(self._key, (None,))[0] is page
                else None
            )
            size = page_bytes(page) if size is None else size
            if size > CACHE_MAX_BYTES:
                self._cache.clear()
                self._sizes.clear()
                self._too_large_key = self._key
                self.state = FeedState(status=STATUS_UNAVAILABLE, error=PAGE_TOO_LARGE)
                return True
            self._cache[self._key] = (page, observed_ms, self._clock())
            self._sizes[self._key] = size
            self._cache.move_to_end(self._key)
            while len(self._cache) > CACHE_ENTRIES or sum(self._sizes.values()) > CACHE_MAX_BYTES:
                key, _ = self._cache.popitem(last=False)
                self._sizes.pop(key, None)
        return True

    def fail(self, generation: int, code: str, *, disabled: bool = False) -> bool:
        if generation != self._generation:
            return False
        now = self._clock()
        self._failures += 1
        self._unchanged = 0
        if self._first_failure is None:
            self._first_failure = now
        if disabled:
            self.state = FeedState(status=STATUS_DISABLED, page=None, observed_ms=None, error=code)
        elif self.state.page is None:
            if code in TRANSIENT_CODES and now - self._first_failure < UNAVAILABLE_AFTER_SECONDS:
                # Still loading: a busy server or a slow link is retried, not reported.
                self.state = FeedState(status=STATUS_LOADING, error=code)
            else:
                self.state = FeedState(status=STATUS_UNAVAILABLE, error=code)
        else:
            self.state = FeedState(
                status=STATUS_ERROR,
                page=self.state.page,
                observed_ms=self.state.observed_ms,
                error=code,
            )
        return True

    def auto_stopped(self) -> bool:
        """Automatic refresh is stopped for this query after an oversize page,
        until a manual refresh or a different query clears the stop."""
        return self._too_large_key is not None and self._too_large_key == self._key

    def clear_page_too_large(self) -> None:
        """A manual refresh clears the oversize stop exactly once; a repeated
        oversize result sets it again."""
        self._too_large_key = None

    def retry_delay(self) -> float | None:
        """Seconds before the next request: the normal interval, or the backoff while the first
        load of this query keeps failing transiently. None ends the loop (oversize stop)."""
        if self.auto_stopped():
            return None
        if self._failures and self.state.page is None and self.state.status == STATUS_LOADING:
            return min(RETRY_CAP_SECONDS, float(2 ** min(self._failures - 1, 4)))
        return min(UNCHANGED_MAX_SECONDS, INTERVAL_SECONDS * float(2**self._unchanged))

    def stop(self) -> None:
        """Polling stops (hidden, backgrounded or unmounted). In-flight results are obsolete, so
        they are dropped.
        """
        self._generation += 1

    def visible(self, key: Hashable, active: bool, scope: Hashable | None = None) -> FeedState:
        """What one render may show. Polling eligibility and display are separate: the same
        context's last page is shown, aged, while inactive or offline.
        """
        if key != self._key:
            # Only the same account and host may show a cached page for another query.
            if scope is not None and scope == self._scope:
                cached = self._cached(key)
                if cached is not None:
                    return cached if active else replace(cached, status=STATUS_IDLE)
            return FeedState(status=STATUS_LOADING if active else STATUS_IDLE)
        return self.state


class RefreshGate:
    """Manual refresh: at most one request in flight. A press while one runs is refused, not
    queued."""

    def __init__(self) -> None:
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def try_start(self) -> bool:
        if self._running:
            return False
        self._running = True
        return True

    def finish(self) -> None:
        self._running = False


async def run_usage_loop(
    *,
    active: Callable[[], bool],
    refresh: Callable[[], Awaitable[None]],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    interval: float = INTERVAL_SECONDS,
    delay: Callable[[], float | None] | None = None,
) -> None:
    """Refresh while active. Each refresh is awaited before the wait starts, so refreshes never
    overlap.
    """
    while active():
        await refresh()
        if not active():
            break
        wait = delay() if delay is not None else interval
        if wait is None:
            break
        await sleep(wait)
