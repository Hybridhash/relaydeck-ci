"""When the Changes feature asks the Mac for news. Pure scheduling: the clock, the sleep and
both requests are injected, so the cadence is testable with a fake clock.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

SUMMARIES_PERIOD = 30.0
SUMMARIES_MIN_GAP = 1.0
MAX_SUMMARY_AGENTS = 200
REVISION_WORKING = 10.0
REVISION_IDLE = 60.0
REVISION_MIN_GAP = 1.0
# Used when a throttling reply has no Retry-After; the cap stops a bad value silencing a channel.
DEFAULT_RETRY_AFTER = {"busy": 5.0, "rate_limited": 2.0}
THROTTLED = frozenset(DEFAULT_RETRY_AFTER)
MAX_RETRY_AFTER = 300.0


@dataclass(frozen=True, slots=True)
class Outcome:
    """How a request ended. `code` is None on success, else the reply's code or reason."""

    code: str | None = None
    retry_after: float | None = None


FetchSummaries = Callable[[tuple[str, ...]], Awaitable[Outcome]]
CheckRevision = Callable[[str], Awaitable[Outcome]]


class _Channel:
    def __init__(self) -> None:
        self.last_start: float | None = None
        self.blocked_until = 0.0
        self.dirty = False

    def restart(self) -> None:
        """Forget the schedule but not a Retry-After window."""
        self.last_start, self.dirty = None, False


class RefreshScheduler:
    def __init__(
        self,
        *,
        fetch_summaries: FetchSummaries,
        check_revision: CheckRevision,
        agent_working: Callable[[str], bool],
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        foreground: bool = True,
    ) -> None:
        self._fetch_summaries = fetch_summaries
        self._check_revision = check_revision
        self._working = agent_working
        self._clock = clock
        self._sleep = sleep
        self._foreground = foreground
        self._agents: frozenset[str] = frozenset()
        self._screen: str | None = None
        self._summaries = _Channel()
        self._revision = _Channel()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    # -- lifecycle -----------------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Begin scheduling on the running loop; a second call does nothing."""
        if not self.running:
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        """Cancel the task, including a request in flight, and wait for it to end."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # -- what the app tells the scheduler --------------------------------------------------------

    def show_console(self, agent_ids: Iterable[str]) -> None:
        """The Console shows these agents; a different set is fetched at once."""
        ids = frozenset(agent_ids)
        if ids != self._agents:
            self._agents = ids
            self._summaries.dirty = bool(ids)
            if not ids:
                self._summaries.restart()
            self._wake.set()

    def hide_console(self) -> None:
        self.show_console(())

    def open_changes(self, agent_id: str, *, immediately: bool = False) -> None:
        """A Changes or diff screen is open for this agent.

        Its first check is one interval away, unless the screen has nothing real to show yet.
        """
        if agent_id != self._screen:
            self._screen = agent_id
            self._revision.restart()
            if immediately:
                self._revision.dirty = True
            else:
                self._revision.last_start = self._clock()
            self._wake.set()

    def close_changes(self) -> None:
        self._screen = None
        self._revision.restart()
        self._wake.set()

    def agent_status_changed(self, agent_id: str | None = None) -> None:
        """An agent started or stopped working. With no id, any visible agent may have changed."""
        if self._agents and (agent_id is None or agent_id in self._agents):
            self._summaries.dirty = True
        if self._screen is not None and agent_id in (None, self._screen):
            self._revision.dirty = True
        self._wake.set()

    def focus(self) -> None:
        self._foreground = True
        self._mark_both()

    def background(self) -> None:
        self._foreground = False
        self._wake.set()

    def reconnected(self) -> None:
        self._mark_both()

    def _mark_both(self) -> None:
        self._summaries.dirty = bool(self._agents)
        self._revision.dirty = self._screen is not None
        self._wake.set()

    # -- the loop --------------------------------------------------------------------------------

    def _summaries_due(self) -> float | None:
        if not (self._foreground and self._agents):
            return None
        channel = self._summaries
        if channel.last_start is None:
            when = float("-inf")
        elif channel.dirty:
            when = channel.last_start + SUMMARIES_MIN_GAP
        else:
            when = channel.last_start + SUMMARIES_PERIOD
        return max(when, channel.blocked_until)

    def _revision_due(self) -> float | None:
        if not (self._foreground and self._screen is not None):
            return None
        channel = self._revision
        start = float("-inf") if channel.last_start is None else channel.last_start
        if channel.dirty:
            when = start + REVISION_MIN_GAP
        else:
            when = start + (REVISION_WORKING if self._working(self._screen) else REVISION_IDLE)
        return max(when, channel.blocked_until)

    async def _run(self) -> None:
        while True:
            self._wake.clear()
            now = self._clock()
            due = [
                (when, name)
                for name, when in (
                    ("summaries", self._summaries_due()),
                    ("revision", self._revision_due()),
                )
                if when is not None
            ]
            if not due:
                await self._wait(None)
            elif min(due)[0] <= now:
                await self._fire(min(due)[1])
            else:
                await self._wait(min(due)[0] - now)

    async def _wait(self, delay: float | None) -> None:
        """Sleep until `delay` passes or the app changes something, whichever is first."""
        waiting: set[asyncio.Future[Any]] = {asyncio.ensure_future(self._wake.wait())}
        if delay is not None:
            waiting.add(asyncio.ensure_future(self._sleep(delay)))
        try:
            await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in waiting:
                task.cancel()

    async def _fire(self, name: str) -> None:
        channel = self._summaries if name == "summaries" else self._revision
        channel.last_start = self._clock()
        channel.dirty = False
        screen = self._screen
        if name == "summaries":
            outcome = await self._fetch_all()
        else:
            outcome = (
                Outcome() if screen is None else await self._guard(self._check_revision(screen))
            )
        if outcome.code in THROTTLED:
            wait = (
                DEFAULT_RETRY_AFTER[outcome.code]
                if outcome.retry_after is None
                else outcome.retry_after
            )
            channel.blocked_until = self._clock() + min(max(wait, 0.0), MAX_RETRY_AFTER)
            channel.dirty = True

    async def _fetch_all(self) -> Outcome:
        ids = sorted(self._agents)
        outcome = Outcome()
        for start in range(0, len(ids), MAX_SUMMARY_AGENTS):
            outcome = await self._guard(
                self._fetch_summaries(tuple(ids[start : start + MAX_SUMMARY_AGENTS]))
            )
            if outcome.code in THROTTLED:
                break
        return outcome

    @staticmethod
    async def _guard(call: Awaitable[Outcome]) -> Outcome:
        """A failing request is a failed poll, not the end of the loop."""
        try:
            return await call
        except Exception:
            return Outcome("error")
