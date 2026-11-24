"""Keeps change summaries fresh for the Console's visible cards and the open Changes screen."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from typing import Any

import flet as ft

from relaydeck.client.features.changes.model import FailedPoll
from relaydeck.client.features.changes.refresh import Outcome, RefreshScheduler
from relaydeck.client.features.changes.source import ChangesFailure, ChangesSource
from relaydeck.contracts.git import BranchCounts, WorkingCounts, WorktreeSummary
from relaydeck.domain.models import scope_key, unscope_key


def unavailable_summary(agent_id: str) -> FailedPoll:
    """What a card and the screen say about an agent whose summary could not be fetched."""
    return FailedPoll(
        agent_id=agent_id,
        worktree_id="",
        state="unavailable",
        detached=False,
        working=WorkingCounts(files=0, staged=0, unstaged=0, untracked=0, conflicts=0),
        branch_scope=BranchCounts(),
        shared=False,
        revision="",
        as_of_ms=0,
        partial=False,
    )


@dataclass(frozen=True, slots=True)
class ChangesFeed:
    summaries: dict[str, WorktreeSummary]
    stale: frozenset[str]  # agents whose latest poll failed (their summary, if any, is old)
    recheck: Callable[[str], None]  # ask for one agent's summary again now
    # When each agent was last found as its summary says. A check that finds nothing new moves
    # only this, never the summary, so nothing built from the summary is rebuilt.
    checked: Mapping[str, int] = field(default_factory=dict)


SETTLE_S = 2.0  # a calculating agent is asked again this soon
SETTLE_TRIES = 15
# Summaries of agents no longer on screen are kept for going back, but not without end.
KEEP_INACTIVE = 64
INACTIVE_TTL_S = 600.0


class _Feed:
    def __init__(self, source: ChangesSource | None, rerender: Callable[[], None]) -> None:
        self.source = source
        self.summaries: dict[str, WorktreeSummary] = {}
        self.status: dict[str, str] = {}
        self.stale: set[str] = set()
        self.checked: dict[str, int] = {}
        self._rerender = rerender
        self._settling = 0
        self._stopping: asyncio.Task[None] | None = None
        self._settle_timer: asyncio.TimerHandle | None = None
        self._seen: dict[str, float] = {}  # when each key was last on screen or answered
        self.active: frozenset[str] = frozenset()  # what is on screen now
        self.scheduler = RefreshScheduler(
            fetch_summaries=self._fetch,
            check_revision=self._fetch_one,
            agent_working=lambda key: self.status.get(key) == "working",
        )

    async def _fetch(self, keys: tuple[str, ...]) -> Outcome:
        """Ask for these agents; a failure is shown and the scheduler keeps its retry timing."""
        source = self.source
        if source is None:
            return Outcome()
        by_host: dict[str, list[str]] = {}
        for key in keys:
            split = unscope_key(key)
            if split is not None:
                by_host.setdefault(split[0], []).append(split[1])
        changed = False
        try:
            for host, agents in by_host.items():
                for summary in await source.summaries(host, agents):
                    key = scope_key(host, summary.agent_id)
                    known = self.summaries.get(key)
                    self.checked[key] = summary.as_of_ms
                    if known is not None and replace(known, as_of_ms=summary.as_of_ms) == summary:
                        # Found as it was: keep the object, so what it built stays as it is.
                        self._seen[key] = time.monotonic()
                        if key in self.stale:
                            self.stale.discard(key)
                            changed = True
                        continue
                    changed = True
                    self.summaries[key] = summary
                    self._seen[key] = time.monotonic()
                    if key in self.stale:
                        self.stale.discard(key)
                        changed = True
        except asyncio.CancelledError:
            raise
        except ChangesFailure as failure:
            self._show_unavailable(keys)
            return Outcome(failure.reason, failure.retry_after_s)
        except Exception:  # noqa: BLE001 - a failed poll is shown, never fatal
            self._show_unavailable(keys)
            return Outcome("error")
        self.prune(self.active)
        if changed:
            self._rerender()
        self._settle(keys)
        return Outcome()

    def _settle(self, keys: Sequence[str]) -> None:
        """While an agent is still calculating, ask again soon instead of at the next 30 s poll."""
        calculating = any(
            (summary := self.summaries.get(key)) is not None and summary.state == "calculating"
            for key in keys
        )
        if not calculating:
            self._settling = 0
            return
        if self._settling >= SETTLE_TRIES:
            return
        self._settling += 1
        if self._settle_timer is not None:
            self._settle_timer.cancel()
        self._settle_timer = asyncio.get_running_loop().call_later(
            SETTLE_S, self.scheduler.agent_status_changed
        )

    def stop_settling(self) -> None:
        if self._settle_timer is not None:
            self._settle_timer.cancel()
            self._settle_timer = None

    async def start_scheduler(self) -> None:
        """Start polling, waiting out a stop still in flight first: a source
        flap (stop, then start in the same tick) must not leave the feed with
        no scheduler running."""
        if self._stopping is not None:
            await self._stopping
            self._stopping = None
        if self.source is not None:
            self.scheduler.start()

    def stop_scheduler(self) -> None:
        self.stop_settling()
        if self.scheduler.running:
            self._stopping = asyncio.ensure_future(self.scheduler.stop())

    def prune(self, active: Collection[str], *, now: float | None = None) -> None:
        """Drop summaries nobody can see: agents that left the inventory or the screen long ago,
        and all but the most recent few of the rest. Whatever is on screen is always kept."""
        stamp = time.monotonic() if now is None else now
        for key in active:
            self._seen[key] = stamp
        inactive = sorted(
            (key for key in self.summaries if key not in active),
            key=lambda key: self._seen.get(key, 0.0),
            reverse=True,
        )
        keep = {
            key
            for key in inactive[:KEEP_INACTIVE]
            if stamp - self._seen.get(key, 0.0) <= INACTIVE_TTL_S
        }
        for key in inactive:
            if key not in keep:
                self.summaries.pop(key, None)
                self.checked.pop(key, None)
                self.stale.discard(key)
                self._seen.pop(key, None)
        for key in [key for key in self._seen if key not in self.summaries and key not in active]:
            del self._seen[key]
        self.stale.intersection_update(self.summaries.keys() | set(active))

    async def _fetch_one(self, key: str) -> Outcome:
        return await self._fetch((key,))

    def _show_unavailable(self, keys: Sequence[str]) -> None:
        """A failed poll is shown. An agent with nothing real yet (no entry, or one still
        calculating) becomes unavailable; real data is kept and marked stale."""
        changed = False
        for key in keys:
            split = unscope_key(key)
            if split is None:
                continue
            known = self.summaries.get(key)
            if known is None:
                self.summaries[key] = unavailable_summary(split[1])
                changed = True
            elif known.state == "calculating":
                values = {f.name: getattr(known, f.name) for f in fields(known)}
                self.summaries[key] = FailedPoll(
                    **{**values, "state": "unavailable", "revision": ""}
                )
                changed = True
            if key not in self.stale:
                self.stale.add(key)
                changed = True
        if changed:
            self._rerender()

    def recheck(self, key: str) -> None:
        self.scheduler.agent_status_changed(key)


def use_changes_feed(
    source: ChangesSource | None,
    visible: Sequence[str],
    statuses: Mapping[str, str],
    *,
    console: bool,
    foreground: bool,
    viewer: str | None = None,
    connected: bool | None = None,
) -> ChangesFeed:
    """Summaries keyed by scoped agent key.

    `visible` are the Console's cards; `viewer` is the agent whose Changes screen is open, which
    is checked on its own (faster while the agent works) and never mixed into the Console's call.
    """
    _tick, set_tick = ft.use_state(0)
    feed_ref: Any = ft.use_ref(None)
    if feed_ref.current is None:
        feed_ref.current = _Feed(source, lambda: set_tick(lambda value: value + 1))
    feed: _Feed = feed_ref.current
    feed.source = source
    feed.status = dict(statuses)
    keys = tuple(sorted(set(visible)))
    feed.active = frozenset({*keys, *([viewer] if viewer else [])})

    ft.use_effect(
        feed.start_scheduler, dependencies=[source is not None], cleanup=feed.stop_scheduler
    )

    def show() -> None:
        feed.prune(feed.active)
        if console and keys:
            feed.scheduler.show_console(keys)
        else:
            feed.scheduler.hide_console()

    ft.use_effect(show, dependencies=[console, keys, viewer])

    def watch() -> None:
        if viewer is None:
            return
        known = feed.summaries.get(viewer)
        # With nothing real to show yet the first check is not left to wait for its interval.
        feed.scheduler.open_changes(
            viewer, immediately=known is None or isinstance(known, FailedPoll)
        )

    ft.use_effect(
        watch, dependencies=[viewer, source is not None], cleanup=feed.scheduler.close_changes
    )
    ft.use_effect(
        lambda: feed.scheduler.agent_status_changed(),
        dependencies=[tuple(sorted(statuses.items()))],
    )

    def visibility() -> None:
        if foreground:
            feed.scheduler.focus()
        else:
            feed.scheduler.background()

    ft.use_effect(visibility, dependencies=[foreground])

    was_connected: Any = ft.use_ref(connected)

    def reconnect() -> None:
        # Coming back from a lost connection: both channels check at once.
        if connected and was_connected.current is False:
            feed.scheduler.reconnected()
        was_connected.current = connected

    ft.use_effect(reconnect, dependencies=[connected])
    return ChangesFeed(feed.summaries, frozenset(feed.stale), feed.recheck, feed.checked)
