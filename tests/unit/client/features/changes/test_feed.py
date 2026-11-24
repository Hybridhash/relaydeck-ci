"""The summary feed: what a failed poll shows and tells the scheduler, and the viewer check."""

from __future__ import annotations

import asyncio
from typing import Any

from relaydeck.client.features.changes.hooks import _Feed, unavailable_summary
from relaydeck.client.features.changes.model import FailedPoll
from relaydeck.client.features.changes.refresh import Outcome
from relaydeck.client.features.changes.source import ChangesFailure
from relaydeck.contracts.git import BranchCounts, WorkingCounts, WorktreeSummary
from relaydeck.domain.models import scope_key

KEY = scope_key("h1", "t1")
OTHER = scope_key("h1", "t2")


def summary(agent: str = "t1", **over: Any) -> WorktreeSummary:
    fields: dict[str, Any] = {
        "agent_id": agent,
        "worktree_id": f"wt-{agent}",
        "state": "ok",
        "detached": False,
        "working": WorkingCounts(files=0, staged=0, unstaged=0, untracked=0, conflicts=0),
        "branch_scope": BranchCounts(),
        "shared": False,
        "revision": "rev-1",
        "as_of_ms": 1,
        "partial": False,
    }
    return WorktreeSummary(**{**fields, **over})


class Source:
    def __init__(self) -> None:
        self.replies: list[Any] = []
        self.asked: list[tuple[str, list[str]]] = []

    async def summaries(self, host_id: str, agent_ids: list[str]) -> list[WorktreeSummary]:
        self.asked.append((host_id, list(agent_ids)))
        reply = self.replies.pop(0) if self.replies else [summary(a) for a in agent_ids]
        if isinstance(reply, Exception):
            raise reply
        return reply


def feed(source: Source) -> tuple[_Feed, list[int]]:
    renders: list[int] = []
    return _Feed(source, lambda: renders.append(1)), renders  # type: ignore[arg-type]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_summaries_are_stored_by_scoped_key_and_the_screen_is_redrawn_once() -> None:
    source = Source()
    shown, renders = feed(source)
    outcome = run(shown._fetch((KEY, OTHER)))
    assert outcome == Outcome() and set(shown.summaries) == {KEY, OTHER}
    assert source.asked == [("h1", ["t1", "t2"])] and renders == [1]
    run(shown._fetch((KEY,)))
    assert renders == [1]  # nothing changed: no redraw


def test_a_failed_poll_shows_unavailable_and_hands_the_retry_window_to_the_scheduler() -> None:
    source = Source()
    source.replies = [ChangesFailure("busy", 7.0)]
    shown, renders = feed(source)
    outcome = run(shown._fetch((KEY,)))
    assert outcome == Outcome("busy", 7.0)
    assert shown.summaries[KEY] == unavailable_summary("t1") and renders == [1]
    assert shown.summaries[KEY].state == "unavailable" and shown.summaries[KEY].revision == ""


def test_a_failed_poll_keeps_what_was_already_known() -> None:
    source = Source()
    shown, _ = feed(source)
    run(shown._fetch((KEY,)))
    known = shown.summaries[KEY]
    source.replies = [ChangesFailure("host_offline")]
    run(shown._fetch((KEY, OTHER)))
    assert shown.summaries[KEY] is known and shown.summaries[OTHER].state == "unavailable"


def test_an_unexpected_error_is_a_failed_poll_not_the_end_of_the_feed() -> None:
    source = Source()
    source.replies = [RuntimeError("boom")]
    shown, _ = feed(source)
    assert run(shown._fetch((KEY,))) == Outcome("error")
    assert shown.summaries[KEY].state == "unavailable"


def test_a_later_reply_replaces_the_unavailable_stand_in() -> None:
    source = Source()
    source.replies = [ChangesFailure("unavailable")]
    shown, _ = feed(source)
    run(shown._fetch((KEY,)))
    run(shown._fetch_one(KEY))
    assert shown.summaries[KEY].state == "ok" and shown.summaries[KEY].revision == "rev-1"


def test_the_scheduler_reads_the_agents_live_status_from_the_feed() -> None:
    shown, _ = feed(Source())
    shown.status = {KEY: "working", OTHER: "idle"}
    assert shown.scheduler._working(KEY) and not shown.scheduler._working(OTHER)
    assert not shown.scheduler._working("unknown")


def test_the_viewer_check_asks_for_that_agent_alone() -> None:
    source = Source()
    shown, _ = feed(source)
    run(shown.scheduler._check_revision(OTHER))
    assert source.asked == [("h1", ["t2"])]


def test_a_calculating_entry_then_a_failed_poll_becomes_unavailable_keeping_its_worktree() -> None:
    source = Source()
    source.replies = [[summary("t1", state="calculating")], ChangesFailure("unavailable")]
    shown, renders = feed(source)
    run(shown._fetch((KEY,)))
    assert shown.summaries[KEY].state == "calculating"
    run(shown._fetch((KEY,)))
    standin = shown.summaries[KEY]
    assert (
        standin.state == "unavailable" and standin.revision == "" and standin.worktree_id == "wt-t1"
    )
    assert KEY in shown.stale


def test_real_data_is_kept_on_a_failed_poll_and_marked_stale_until_a_poll_succeeds() -> None:
    source = Source()
    shown, _ = feed(source)
    run(shown._fetch((KEY,)))
    known = shown.summaries[KEY]
    source.replies = [ChangesFailure("host_offline")]
    run(shown._fetch((KEY,)))
    assert shown.summaries[KEY] is known and shown.stale == {KEY}
    run(shown._fetch((KEY,)))
    assert shown.stale == set()


def test_recheck_marks_the_agents_channels_due() -> None:
    shown, _ = feed(Source())
    shown.scheduler.open_changes(KEY)
    shown.recheck(KEY)
    assert shown.scheduler._revision.dirty


def test_a_state_the_server_reports_is_kept_as_it_is_and_only_a_failed_poll_is_marked() -> None:
    source = Source()
    reported = summary("t1", state="no_repo", worktree_id="", revision="", as_of_ms=0)
    source.replies = [[reported], ChangesFailure("unavailable")]
    shown, _ = feed(source)
    run(shown._fetch((KEY,)))
    assert shown.summaries[KEY] is not None and not isinstance(shown.summaries[KEY], FailedPoll)
    assert shown.summaries[KEY].state == "no_repo" and shown.stale == set()
    run(shown._fetch((KEY, OTHER)))  # now the poll fails: the reported state is kept, stale
    assert shown.summaries[KEY].state == "no_repo" and KEY in shown.stale
    assert isinstance(shown.summaries[OTHER], FailedPoll)


def test_visiting_many_batches_of_agents_keeps_a_bounded_number_of_summaries() -> None:
    from relaydeck.client.features.changes.hooks import KEEP_INACTIVE

    source = Source()
    shown, _ = feed(source)
    for batch in range(100):
        keys = tuple(scope_key("h1", f"a{batch}-{n}") for n in range(50))
        shown.active = frozenset(keys)
        run(shown._fetch(keys))
        assert len(shown.summaries) <= 50 + KEEP_INACTIVE
        assert set(keys) <= set(shown.summaries)  # whatever is on screen stays
    assert len(shown._seen) <= len(shown.summaries) + 50


def test_summaries_not_seen_for_a_long_time_are_dropped_and_visible_ones_never() -> None:
    from relaydeck.client.features.changes.hooks import INACTIVE_TTL_S

    source = Source()
    shown, _ = feed(source)
    run(shown._fetch((KEY, OTHER)))
    shown.active = frozenset({KEY})
    shown.prune(shown.active, now=shown._seen[KEY] + INACTIVE_TTL_S + 1)
    assert KEY in shown.summaries and OTHER not in shown.summaries
    shown.stale.add(OTHER)
    shown.prune(shown.active)
    assert OTHER not in shown.stale  # a dropped agent leaves no mark behind


def test_the_removed_agents_summary_goes_once_it_is_no_longer_on_screen() -> None:
    source = Source()
    shown, _ = feed(source)
    run(shown._fetch((KEY, OTHER)))
    shown.active = frozenset({KEY})  # OTHER left the inventory
    shown.prune(shown.active, now=shown._seen[OTHER] + 10_000)
    assert set(shown.summaries) == {KEY}


def test_stopping_cancels_a_pending_settle_check() -> None:
    async def scenario() -> None:
        source = Source()
        source.replies = [[summary("t1", state="calculating")]]
        shown, _ = feed(source)
        await shown._fetch((KEY,))
        assert shown._settle_timer is not None and not shown._settle_timer.cancelled()
        timer = shown._settle_timer
        shown.stop_settling()
        assert timer.cancelled() and shown._settle_timer is None
        await shown._fetch((KEY,))  # a later check schedules at most one timer at a time
        again = shown._settle_timer
        await shown._fetch((KEY,))
        assert again is None or again.cancelled() or shown._settle_timer is not again

    asyncio.run(scenario())


def test_a_check_that_finds_nothing_new_moves_only_the_check_time() -> None:
    source = Source()
    shown, renders = feed(source)
    run(shown._fetch((KEY,)))
    first = shown.summaries[KEY]
    source.replies = [
        [summary(as_of_ms=11_000)],
        [summary(as_of_ms=21_000)],
        [summary(as_of_ms=31_000)],
    ]
    for _ in range(3):
        run(shown._fetch((KEY,)))
    assert shown.summaries[KEY] is first  # the same object: nothing built from it is rebuilt
    assert shown.checked[KEY] == 31_000
    assert renders == [1]  # only the first arrival redrew
    source.replies = [[summary(as_of_ms=41_000, revision="rev-2")]]
    run(shown._fetch((KEY,)))
    assert shown.summaries[KEY] is not first and renders == [1, 1]
    assert shown.checked[KEY] == 41_000
