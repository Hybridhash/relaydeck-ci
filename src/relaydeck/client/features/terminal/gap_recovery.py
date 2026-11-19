"""Retry state and timing for terminal gap recovery."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

WATCH_RETRY_DELAYS_S = (1.0, 2.0, 4.0, 8.0, 16.0)

GAP_STABLE_S = 30.0

HOST_OFFLINE_WAIT_S = 60.0

DRIVE_DEFERRED = "deferred"

DRIVE_EXHAUSTED = "exhausted"

DRIVE_REACQUIRE = "reacquire"


@dataclass
class GapRecovery:
    detail: str = ""
    revision: int = 0
    consumed: int = -1
    attempt: int = 0
    foreground_attempts: int = 0
    visibility_epoch: int | None = None
    stale_retry_at: float | None = None
    stable_since: float | None = None
    gap_at: float = 0.0
    gap_ms: float = 0.0
    host_online: bool = False
    waiting: bool = False
    exhausted: bool = False
    delay: float = 0.0
    wake: asyncio.Event = field(default_factory=asyncio.Event)

    def reset(self) -> None:
        self.cancel_wait()
        self.revision += 1
        self.detail = ""
        self.attempt = self.foreground_attempts = 0
        self.stale_retry_at = self.stable_since = None
        self.exhausted = False

    def cancel_wait(self) -> None:
        self.wake.set()

    def renew_foreground(self, epoch: int) -> None:
        if self.visibility_epoch is not None and epoch != self.visibility_epoch:
            self.foreground_attempts = 0
        self.visibility_epoch = epoch

    def rearm(self) -> None:
        """A failed admission keeps its detail: a host still away restarts the host wait, a
        host that came back or a generic gap moves on to the next backoff delay."""
        self.revision += 1
        if self.detail == "host_offline" and not self.host_online:
            self.gap_at, self.gap_ms = time.monotonic(), time.time() * 1000
        elif self.detail != "stale_revision" and self.attempt >= len(WATCH_RETRY_DELAYS_S):
            self.exhausted = True

    def record(self, detail: str) -> None:
        self.detail = detail
        self.revision += 1
        self.gap_at, self.gap_ms = time.monotonic(), time.time() * 1000
        self.stable_since = None
        if detail == "host_offline":
            self.host_online = False
        self.wake.set()

    def output(self) -> None:
        self.detail = ""
        now = time.monotonic()
        if self.stable_since is None:
            self.stable_since = now
        elif now - self.stable_since >= GAP_STABLE_S:
            self.attempt = self.foreground_attempts = 0
            self.stale_retry_at = None
            self.exhausted = False

    def set_host_online(self, online: bool) -> None:
        self.host_online = online
        if online:
            self.wake.set()

    async def wait(self, current: Callable[[], bool]) -> bool:
        if not current() or self.exhausted:
            return False
        if not self.detail:
            return True
        if self.waiting or self.consumed == self.revision:
            return False
        revision, detail = self.revision, self.detail
        now = time.monotonic()
        host_wait = detail == "host_offline" and not self.host_online
        if detail == "stale_revision":
            if self.stale_retry_at is not None and now - self.stale_retry_at < 10:
                self.exhausted = True
                return False
            self.delay = 0.0
        elif host_wait:
            self.delay = max(0.0, self.gap_at + HOST_OFFLINE_WAIT_S - now)
        elif self.attempt < len(WATCH_RETRY_DELAYS_S):
            self.delay = WATCH_RETRY_DELAYS_S[self.attempt]
        else:
            self.exhausted = True
            return False
        deadline = now + self.delay
        self.waiting = True
        try:
            while current() and revision == self.revision:
                self.wake.clear()
                if host_wait and self.host_online:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    await asyncio.wait_for(self.wake.wait(), remaining)
                except TimeoutError:
                    break
            if not current() or revision != self.revision:
                return False
            if detail == "stale_revision":
                self.stale_retry_at = time.monotonic()
            elif not host_wait:
                self.attempt += 1
            self.consumed = revision
            return True
        finally:
            self.waiting = False
