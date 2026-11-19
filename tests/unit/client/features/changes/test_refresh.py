"""RefreshScheduler cadence, driven by a fake clock and a fake sleep."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable

from relaydeck.client.features.changes.refresh import (
    MAX_RETRY_AFTER,
    MAX_SUMMARY_AGENTS,
    Outcome,
    RefreshScheduler,
)

START = 1000.0


async def settle() -> None:
    for _ in range(30):
        await asyncio.sleep(0)


class FakeTime:
    def __init__(self) -> None:
        self.now = START
        self._sleepers: list[tuple[float, asyncio.Future[None]]] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        entry = (self.now + delay, asyncio.get_running_loop().create_future())
        self._sleepers.append(entry)
        try:
            await entry[1]
        finally:
            self._sleepers.remove(entry)

    async def advance(self, seconds: float) -> None:
        end = self.now + seconds
        await settle()
        while True:
            due = [e for e in self._sleepers if e[0] <= end and not e[1].done()]
            if not due:
                break
            when, future = min(due, key=lambda e: e[0])
            self.now = max(self.now, when)
            future.set_result(None)
            await settle()
        self.now = end
        await settle()


class Rig:
    def __init__(self) -> None:
        self.time = FakeTime()
        self.calls: list[tuple[float, str, object]] = []
        self.working: set[str] = set()
        self.summary_replies: deque[Outcome] = deque()
        self.revision_replies: deque[Outcome] = deque()
        self.hold: asyncio.Event | None = None
        self.active = 0
        self.peak = 0
        self.scheduler = RefreshScheduler(
            fetch_summaries=self._summaries,
            check_revision=self._revision,
            agent_working=lambda agent: agent in self.working,
            clock=self.time.clock,
            sleep=self.time.sleep,
        )

    async def _call(self, kind: str, arg: object, replies: deque[Outcome]) -> Outcome:
        self.calls.append((self.time.now - START, kind, arg))
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.hold is not None:
                await self.hold.wait()
            reply = replies.popleft() if replies else Outcome()
            if reply.code == "raise":
                raise RuntimeError("request failed")
            return reply
        finally:
            self.active -= 1

    async def _summaries(self, ids: tuple[str, ...]) -> Outcome:
        return await self._call("summaries", ids, self.summary_replies)

    async def _revision(self, agent: str) -> Outcome:
        return await self._call("revision", agent, self.revision_replies)

    def times(self, kind: str) -> list[float]:
        return [t for t, k, _ in self.calls if k == kind]


def scenario(body: Callable[[Rig], Awaitable[None]]) -> None:
    async def main() -> None:
        rig = Rig()
        rig.scheduler.start()
        try:
            await body(rig)
        finally:
            await rig.scheduler.stop()

    asyncio.run(main())


def test_nothing_is_requested_until_the_console_or_a_screen_is_shown() -> None:
    async def body(rig: Rig) -> None:
        await rig.time.advance(300)
        assert rig.calls == []

    scenario(body)


def test_showing_the_console_fetches_all_visible_agents_at_once_then_every_thirty_seconds() -> None:
    async def body(rig: Rig) -> None:
        rig.scheduler.show_console(["b", "a", "c", "a"])
        await settle()
        assert rig.calls == [(0.0, "summaries", ("a", "b", "c"))]
        await rig.time.advance(29.9)
        assert len(rig.calls) == 1
        await rig.time.advance(0.1)
        await rig.time.advance(30)
        assert rig.times("summaries") == [0.0, 30.0, 60.0]

    scenario(body)


def test_a_burst_of_status_changes_becomes_one_call_a_second_later() -> None:
    async def body(rig: Rig) -> None:
        rig.scheduler.show_console(["a", "b"])
        await settle()
        for _ in range(5):
            rig.scheduler.agent_status_changed("a")
        await settle()
        assert rig.times("summaries") == [0.0]
        await rig.time.advance(0.99)
        assert rig.times("summaries") == [0.0]
        await rig.time.advance(0.01)
        assert rig.times("summaries") == [0.0, 1.0]
        assert rig.calls[-1][2] == ("a", "b")

    scenario(body)


def test_a_status_change_after_a_quiet_second_is_fetched_at_once() -> None:
    async def body(rig: Rig) -> None:
        rig.scheduler.show_console(["a"])
        await rig.time.advance(5)
        rig.scheduler.agent_status_changed("a")
        await settle()
        assert rig.times("summaries") == [0.0, 5.0]

    scenario(body)


def test_never_more_than_one_summaries_call_per_second() -> None:
    async def body(rig: Rig) -> None:
        rig.scheduler.show_console(["a"])
        for _ in range(50):
            rig.scheduler.agent_status_changed("a")
            await rig.time.advance(0.2)
        times = rig.times("summaries")
        assert len(times) >= 9
        assert all(b - a >= 1.0 for a, b in zip(times, times[1:], strict=False))

    scenario(body)


def test_a_status_change_of_an_agent_nobody_watches_is_ignored() -> None:
    async def body(rig: Rig) -> None:
        rig.scheduler.show_console(["a"])
        await rig.time.advance(5)
        rig.scheduler.agent_status_changed("other")
        await rig.time.advance(5)
        assert rig.times("summaries") == [0.0]

    scenario(body)


def test_a_new_set_of_visible_agents_is_fetched_and_the_same_set_is_not() -> None:
    async def body(rig: Rig) -> None:
        rig.scheduler.show_console(["a"])
        await rig.time.advance(2)
        rig.scheduler.show_console(["a"])
        await rig.time.advance(2)
        assert rig.times("summaries") == [0.0]
        rig.scheduler.show_console(["a", "b"])
        await settle()
        assert rig.calls[-1] == (4.0, "summaries", ("a", "b"))

    scenario(body)


def test_more_than_the_server_limit_of_agents_is_split_into_sequential_calls() -> None:
    async def body(rig: Rig) -> None:
        ids = [f"agent-{i:04d}" for i in range(MAX_SUMMARY_AGENTS * 2 + 50)]
        rig.scheduler.show_console(ids)
        await settle()
        sizes = [len(arg) for _, _, arg in rig.calls]  # type: ignore[arg-type]
        assert sizes == [MAX_SUMMARY_AGENTS, MAX_SUMMARY_AGENTS, 50]
        assert rig.peak == 1

    scenario(body)


def test_hiding_the_console_stops_summaries_and_showing_it_again_fetches_at_once() -> None:
    async def body(rig: Rig) -> None:
        rig.scheduler.show_console(["a"])
        await rig.time.advance(1)
        rig.scheduler.hide_console()
        await rig.time.advance(120)
        assert rig.times("summaries") == [0.0]
        rig.scheduler.show_console(["a"])
        await settle()
        assert rig.times("summaries") == [0.0, 121.0]

    scenario(body)


def test_an_open_screen_checks_every_ten_seconds_while_its_agent_works() -> None:
    async def body(rig: Rig) -> None:
        rig.working.add("a")
        rig.scheduler.open_changes("a")
        await rig.time.advance(9.9)
        assert rig.calls == []
        await rig.time.advance(0.1)
        await rig.time.advance(20)
        assert rig.calls == [
            (10.0, "revision", "a"),
            (20.0, "revision", "a"),
            (30.0, "revision", "a"),
        ]

    scenario(body)


def test_an_open_screen_checks_every_sixty_seconds_when_the_agent_is_not_working() -> None:
    async def body(rig: Rig) -> None:
        rig.scheduler.open_changes("a")
        await rig.time.advance(59.9)
        assert rig.calls == []
        await rig.time.advance(0.1)
        await rig.time.advance(60)
        assert rig.times("revision") == [60.0, 120.0]

    scenario(body)


def test_a_status_change_of_the_open_agent_checks_once_and_switches_the_cadence() -> None:
    async def body(rig: Rig) -> None:
        rig.working.add("a")
        rig.scheduler.open_changes("a")
        await rig.time.advance(35)
        assert rig.times("revision") == [10.0, 20.0, 30.0]
        rig.working.discard("a")
        rig.scheduler.agent_status_changed("a")
        await rig.time.advance(80)
        assert rig.times("revision") == [10.0, 20.0, 30.0, 35.0, 95.0]

    scenario(body)


def test_opening_a_screen_does_not_check_at_once_and_closing_it_stops_checks() -> None:
    async def body(rig: Rig) -> None:
        rig.working.add("a")
        rig.scheduler.open_changes("a")
        await settle()
        assert rig.calls == []
        rig.scheduler.close_changes()
        await rig.time.advance(120)
        assert rig.calls == []

    scenario(body)


def test_nothing_is_scheduled_in_the_background_and_focus_checks_at_once() -> None:
    async def body(rig: Rig) -> None:
        rig.scheduler.show_console(["a"])
        rig.scheduler.open_changes("b")
        await settle()
        rig.scheduler.background()
        await rig.time.advance(300)
        assert rig.times("summaries") == [0.0] and rig.times("revision") == []
        rig.scheduler.focus()
        await settle()
        assert rig.times("summaries") == [0.0, 300.0]
        assert rig.times("revision") == [300.0]

    scenario(body)


def test_a_reconnect_checks_at_once_but_never_faster_than_the_minimum_gap() -> None:
    async def body(rig: Rig) -> None:
        rig.scheduler.show_console(["a"])
        rig.scheduler.open_changes("b")
        await rig.time.advance(10)
        rig.scheduler.reconnected()
        rig.scheduler.reconnected()
        await settle()
        assert rig.times("summaries") == [0.0, 10.0] and rig.times("revision") == [10.0]
        rig.scheduler.reconnected()
        await rig.time.advance(0.5)
        assert len(rig.calls) == 3
        await rig.time.advance(0.5)
        assert rig.times("summaries") == [0.0, 10.0, 11.0] and rig.times("revision") == [10.0, 11.0]

    scenario(body)


def test_retry_after_closes_the_channel_and_the_retry_comes_when_the_window_ends() -> None:
    async def body(rig: Rig) -> None:
        rig.summary_replies.append(Outcome("rate_limited", 7.0))
        rig.scheduler.show_console(["a"])
        await rig.time.advance(3)
        for _ in range(3):
            rig.scheduler.agent_status_changed("a")
            rig.scheduler.focus()
            rig.scheduler.reconnected()
        await rig.time.advance(3.9)
        assert rig.times("summaries") == [0.0]
        await rig.time.advance(0.1)
        assert rig.times("summaries") == [0.0, 7.0]
        await rig.time.advance(29.9)
        assert rig.times("summaries") == [0.0, 7.0]
        await rig.time.advance(0.1)
        assert rig.times("summaries") == [0.0, 7.0, 37.0]

    scenario(body)


def test_busy_without_retry_after_uses_a_default_and_a_huge_value_is_capped() -> None:
    async def body(rig: Rig) -> None:
        rig.summary_replies.append(Outcome("busy"))
        rig.scheduler.show_console(["a"])
        await rig.time.advance(4.9)
        assert rig.times("summaries") == [0.0]
        await rig.time.advance(0.1)
        assert rig.times("summaries") == [0.0, 5.0]
        rig.summary_replies.append(Outcome("busy", 10_000.0))
        rig.scheduler.agent_status_changed("a")
        await rig.time.advance(1.0)
        assert rig.times("summaries") == [0.0, 5.0, 6.0]  # the capped window starts at 6
        await rig.time.advance(MAX_RETRY_AFTER - 0.1)
        assert len(rig.times("summaries")) == 3
        await rig.time.advance(0.1)
        assert rig.times("summaries")[-1] == 6.0 + MAX_RETRY_AFTER

    scenario(body)


def test_a_closed_summaries_channel_does_not_hold_back_the_revision_checks() -> None:
    async def body(rig: Rig) -> None:
        rig.summary_replies.append(Outcome("rate_limited", 100.0))
        rig.working.add("b")
        rig.scheduler.show_console(["a"])
        rig.scheduler.open_changes("b")
        await rig.time.advance(35)
        assert rig.times("revision") == [10.0, 20.0, 30.0]
        assert rig.times("summaries") == [0.0]

    scenario(body)


def test_a_retry_after_survives_hiding_and_showing_the_console() -> None:
    async def body(rig: Rig) -> None:
        rig.summary_replies.append(Outcome("busy", 20.0))
        rig.scheduler.show_console(["a"])
        await rig.time.advance(1)
        rig.scheduler.hide_console()
        rig.scheduler.show_console(["a"])
        await rig.time.advance(18.9)
        assert rig.times("summaries") == [0.0]
        await rig.time.advance(0.1)
        assert rig.times("summaries") == [0.0, 20.0]

    scenario(body)


def test_requests_never_overlap_and_triggers_during_a_request_coalesce_into_one_more() -> None:
    async def body(rig: Rig) -> None:
        rig.hold = asyncio.Event()
        rig.scheduler.show_console(["a"])
        rig.scheduler.open_changes("b")
        await rig.time.advance(70)  # both channels are due while the first request is held
        for _ in range(5):
            rig.scheduler.agent_status_changed("a")
        await settle()
        assert rig.peak == 1 and len(rig.calls) == 1
        rig.hold.set()
        await settle()
        await rig.time.advance(2)
        assert rig.peak == 1
        assert rig.times("summaries").count(0.0) == 1
        assert len(rig.times("summaries")) == 2 and len(rig.times("revision")) >= 1

    scenario(body)


def test_a_failing_request_does_not_stop_the_loop_or_retry_in_a_tight_loop() -> None:
    async def body(rig: Rig) -> None:
        rig.summary_replies.append(Outcome("raise"))
        rig.scheduler.show_console(["a"])
        await rig.time.advance(29)
        assert rig.times("summaries") == [0.0]
        await rig.time.advance(1)
        assert rig.times("summaries") == [0.0, 30.0]
        assert rig.scheduler.running

    scenario(body)


def test_other_error_codes_wait_for_the_normal_period() -> None:
    async def body(rig: Rig) -> None:
        rig.revision_replies.append(Outcome("host_offline"))
        rig.working.add("a")
        rig.scheduler.open_changes("a")
        await rig.time.advance(19)
        assert rig.times("revision") == [10.0]

    scenario(body)


def test_stop_cancels_a_request_in_flight_and_nothing_runs_afterwards() -> None:
    async def main() -> None:
        rig = Rig()
        rig.hold = asyncio.Event()
        rig.scheduler.start()
        rig.scheduler.show_console(["a"])
        await settle()
        assert rig.active == 1 and rig.scheduler.running
        await rig.scheduler.stop()
        assert rig.active == 0 and not rig.scheduler.running
        await rig.time.advance(300)
        assert len(rig.calls) == 1
        await rig.scheduler.stop()  # a second stop is harmless

    asyncio.run(main())


def test_the_scheduler_can_be_started_again_after_a_stop() -> None:
    async def main() -> None:
        rig = Rig()
        rig.scheduler.start()
        rig.scheduler.start()
        rig.scheduler.show_console(["a"])
        await settle()
        await rig.scheduler.stop()
        rig.scheduler.start()
        await rig.time.advance(1)
        assert rig.scheduler.running
        await rig.scheduler.stop()
        await rig.scheduler.stop()
        assert len(rig.calls) >= 1

    asyncio.run(main())


def test_a_screen_with_nothing_real_to_show_is_checked_at_once() -> None:
    async def body(rig: Rig) -> None:
        rig.scheduler.open_changes("a", immediately=True)
        await rig.time.advance(0)
        assert rig.times("revision") == [0.0]
        await rig.time.advance(60)
        assert rig.times("revision") == [0.0, 60.0]  # then the idle cadence

    scenario(body)


def test_a_screen_with_data_waits_one_interval_for_its_first_check() -> None:
    async def body(rig: Rig) -> None:
        rig.scheduler.open_changes("a")
        await rig.time.advance(59)
        assert rig.times("revision") == []
        await rig.time.advance(1)
        assert rig.times("revision") == [60.0]

    scenario(body)
