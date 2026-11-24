"""The companion started before Herdr connects when Herdr answers: one owned retry task, a failed
source closed before the next is made, transient failures only, nothing left running on stop."""

from __future__ import annotations

import asyncio
import gc
import weakref
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from relaydeck.companion.app import bindings, herdr_link, herdr_retry
from relaydeck.companion.app.runtime import CompanionRuntime
from relaydeck.companion.config import CompanionConfig, load_config
from relaydeck.connector.enrollment import ConnectorCreds, save_creds
from relaydeck.connector.herdr import HerdrError, HerdrProtocolError
from relaydeck.devtools.fakes.connector import FixtureAdapter
from relaydeck.gateway.features.access.hosts import HostStore
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)
from tests.support.companion.asgi_companion import AsgiServer, config_file

pytestmark = pytest.mark.usefixtures("hosted_web_bundle")


class World:
    """Herdr as the tests control it, and every source the companion made for it."""

    def __init__(self) -> None:
        self.up = False
        self.error: Callable[[], BaseException] = lambda: FileNotFoundError("synthetic socket")
        self.sources: list[Source] = []
        self.gate: asyncio.Event | None = None
        self.after_gate_up = True
        self.keep = True
        self.made = 0
        self.refs: list[weakref.ref[Source]] = []
        self.close_gate: asyncio.Event | None = None
        self.close_fails = False
        self.close_calls = 0
        self.close_entered = asyncio.Event()

    def factory(self) -> Source:
        source = Source(self)
        self.made += 1
        self.refs.append(weakref.ref(source))
        if self.keep:
            self.sources.append(source)
        return source


class Source(FixtureAdapter):
    def __init__(self, world: World) -> None:
        super().__init__()
        self.world = world
        self.closed = 0
        self.bootstraps = 0

    async def bootstrap(self) -> None:  # type: ignore[override]
        self.bootstraps += 1
        gate = self.world.gate
        if gate is not None:
            await gate.wait()
            if self.world.after_gate_up:
                return
        if not self.world.up:
            raise self.world.error()

    async def close(self) -> None:
        world = self.world
        world.close_calls += 1
        world.close_entered.set()
        if world.close_gate is not None:
            await world.close_gate.wait()
        if world.close_fails:
            raise RuntimeError("synthetic: the connection is still open")
        self.closed += 1


@pytest.fixture
def quick(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A private home, no real update check, and retries a few milliseconds apart."""
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))

    def no_updates(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("synthetic: no update service in this test")

    monkeypatch.setattr(bindings, "build_updates", no_updates)
    monkeypatch.setattr(herdr_link, "backoff_ms", lambda attempts, **kw: 5.0)


def enrolled(tmp_path: Path) -> tuple[CompanionConfig, str]:
    config = load_config(config_file(tmp_path, web_ui=False))
    config.state_dir.mkdir(mode=0o700)
    token = HostStore(path=config.state_dir / "hosts.json").enroll("home-mac", account="owner")
    save_creds(
        ConnectorCreds(config.advertised_origin, config.host_id, token),
        str(config.connector_credentials),
    )
    return config, token


async def until(check: Callable[[], bool], seconds: float = 5.0) -> None:
    async with asyncio.timeout(seconds):
        while not check():
            await asyncio.sleep(0.005)


def make(config: CompanionConfig, world: World) -> CompanionRuntime:
    return CompanionRuntime(
        config, connect_herdr=True, server_factory=AsgiServer, source_factory=world.factory
    )


def run(scenario: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(scenario())


def test_herdr_that_answers_late_is_connected_once_and_every_failed_source_is_closed(
    tmp_path: Path, quick: None
) -> None:
    config, _token = enrolled(tmp_path)
    world = World()

    async def scenario() -> None:
        runtime = make(config, world)
        try:
            await runtime.start(asyncio.Event())
            assert (
                runtime.herdr.link_task is None and runtime.herdr.issue == herdr_retry.WAITING_ISSUE
            )
            first_retry = runtime.herdr.retry_task
            assert first_retry is not None
            await until(lambda: len(world.sources) >= 3)
            world.up = True
            await until(lambda: runtime.herdr.link_task is not None)
            await until(lambda: first_retry.done())
            assert runtime.herdr.issue == "Waiting for authenticated connector synchronization."
            assert runtime.report()["connector_ready"] is False  # not claimed before real sync
            *failed, live = world.sources
            assert failed and all(source.closed == 1 for source in failed)
            assert live.closed == 0 and runtime.herdr.source is live
        finally:
            await runtime.close()
        assert world.sources[-1].closed == 1
        assert all(source.closed == 1 for source in world.sources)

    run(scenario)


def test_a_source_that_fails_half_way_is_closed_before_the_next_one_is_made(
    tmp_path: Path, quick: None
) -> None:
    config, _token = enrolled(tmp_path)
    world = World()
    seen: list[tuple[int, int]] = []
    make_source = world.factory

    def watching() -> Source:
        before = sum(source.closed for source in world.sources)
        seen.append((len(world.sources), before))
        return make_source()

    async def scenario() -> None:
        runtime = CompanionRuntime(
            config, connect_herdr=True, server_factory=AsgiServer, source_factory=watching
        )
        try:
            await runtime.start(asyncio.Event())
            await until(lambda: len(world.sources) >= 4)
        finally:
            await runtime.close()
        # When source n was made, every earlier one had already been closed.
        assert all(closed == made for made, closed in seen)

    run(scenario)


def test_a_protocol_mismatch_is_action_needed_and_is_never_retried(
    tmp_path: Path, quick: None
) -> None:
    config, _token = enrolled(tmp_path)
    world = World()
    world.error = lambda: HerdrProtocolError("protocol_mismatch", "synthetic")

    async def scenario() -> None:
        runtime = make(config, world)
        try:
            await runtime.start(asyncio.Event())
            assert runtime.herdr.retry_task is None and runtime.herdr.link_task is None
            assert runtime.herdr.issue == herdr_retry.INCOMPATIBLE_ISSUE
            await asyncio.sleep(0.1)
            assert len(world.sources) == 1 and world.sources[0].closed == 1
        finally:
            await runtime.close()

    run(scenario)


@pytest.mark.parametrize(
    "error",
    [lambda: HerdrError("bad_envelope", "synthetic"), lambda: RuntimeError("synthetic")],
    ids=["herdr-error", "runtime-error"],
)
def test_an_answer_that_will_not_change_by_waiting_is_not_retried(
    tmp_path: Path, quick: None, error: Callable[[], BaseException]
) -> None:
    config, _token = enrolled(tmp_path)
    world = World()
    world.error = error

    async def scenario() -> None:
        runtime = make(config, world)
        try:
            await runtime.start(asyncio.Event())
            assert runtime.herdr.retry_task is None
            await asyncio.sleep(0.05)
            assert len(world.sources) == 1
        finally:
            await runtime.close()

    run(scenario)


@pytest.mark.parametrize("error", [TimeoutError, ConnectionRefusedError, FileNotFoundError])
def test_a_missing_socket_a_refusal_and_a_timeout_are_all_waited_out(
    tmp_path: Path, quick: None, error: type[BaseException]
) -> None:
    config, _token = enrolled(tmp_path)
    world = World()
    world.error = lambda: error("synthetic")

    async def scenario() -> None:
        runtime = make(config, world)
        try:
            await runtime.start(asyncio.Event())
            assert runtime.herdr.retry_task is not None
            await until(lambda: len(world.sources) >= 2)
            world.up = True
            await until(lambda: runtime.herdr.link_task is not None)
        finally:
            await runtime.close()

    run(scenario)


def test_a_revoked_enrollment_ends_the_wait_and_never_links(tmp_path: Path, quick: None) -> None:
    config, _token = enrolled(tmp_path)
    world = World()

    async def scenario() -> None:
        runtime = make(config, world)
        try:
            await runtime.start(asyncio.Event())
            retry = runtime.herdr.retry_task
            assert retry is not None
            assert runtime.gateway.hosts.revoke(config.host_id)
            await until(retry.done)
            assert "enrollment expired" in runtime.herdr.issue
            world.up = True
            await asyncio.sleep(0.05)
            assert runtime.herdr.link_task is None
            assert all(source.closed == 1 for source in world.sources)
        finally:
            await runtime.close()

    run(scenario)


def test_repeated_wakeups_keep_one_retry_and_one_link(tmp_path: Path, quick: None) -> None:
    config, _token = enrolled(tmp_path)
    world = World()

    async def scenario() -> None:
        runtime = make(config, world)
        try:
            await runtime.start(asyncio.Event())
            retry = runtime.herdr.retry_task
            assert retry is not None
            for _ in range(5):
                await runtime.herdr._connect()
                assert runtime.herdr.retry_task is retry
            world.up = True
            await until(lambda: runtime.herdr.link_task is not None)
            link = runtime.herdr.link_task
            for _ in range(5):
                await runtime.herdr._connect()
            assert runtime.herdr.link_task is link
            made = len(world.sources)
            await asyncio.sleep(0.05)
            assert len(world.sources) == made
        finally:
            await runtime.close()

    run(scenario)


def test_stop_cancels_and_awaits_a_start_that_is_still_in_progress(
    tmp_path: Path, quick: None
) -> None:
    config, _token = enrolled(tmp_path)
    world = World()

    async def scenario() -> None:
        runtime = make(config, world)
        await runtime.start(asyncio.Event())
        retry = runtime.herdr.retry_task
        assert retry is not None
        world.gate = asyncio.Event()
        await until(lambda: world.sources[-1].bootstraps == 1 and len(world.sources) >= 2)
        await until(lambda: world.sources[-1].bootstraps == 1)
        await runtime.close()
        assert retry.done()
        assert runtime.herdr.link_task is None
        assert all(source.closed >= 1 for source in world.sources)

    run(scenario)


def test_a_start_that_succeeds_while_the_companion_stops_creates_no_link(
    tmp_path: Path, quick: None
) -> None:
    config, _token = enrolled(tmp_path)
    world = World()

    async def scenario() -> None:
        stop = asyncio.Event()
        runtime = make(config, world)
        await runtime.start(stop)
        retry = runtime.herdr.retry_task
        assert retry is not None
        world.gate = asyncio.Event()
        await until(lambda: world.sources[-1].bootstraps == 1 and world.sources[-1].closed == 0)
        stop.set()  # the stop arrives, then the bootstrap finishes successfully
        world.gate.set()
        await until(retry.done)
        assert runtime.herdr.link_task is None
        await runtime.close()
        assert all(source.closed >= 1 for source in world.sources)

    run(scenario)


def test_enrollment_failures_behave_as_before(tmp_path: Path, quick: None) -> None:
    config = load_config(config_file(tmp_path, web_ui=False))
    config.state_dir.mkdir(mode=0o700)
    world = World()

    async def scenario() -> None:
        runtime = make(config, world)
        try:
            await runtime.start(asyncio.Event())
            assert "Enroll this Mac's connector" in runtime.herdr.issue
            assert runtime.herdr.retry_task is None and world.sources == []
        finally:
            await runtime.close()

    run(scenario)


def test_classification_is_the_documented_set() -> None:
    assert herdr_retry.classify(FileNotFoundError()) == herdr_retry.TRANSIENT
    assert herdr_retry.classify(ConnectionRefusedError()) == herdr_retry.TRANSIENT
    assert herdr_retry.classify(TimeoutError()) == herdr_retry.TRANSIENT
    assert herdr_retry.classify(HerdrProtocolError("protocol_mismatch", "x")) == (
        herdr_retry.INCOMPATIBLE
    )
    assert herdr_retry.classify(HerdrError("bad_envelope", "x")) == herdr_retry.INCOMPATIBLE
    assert herdr_retry.classify(RuntimeError()) == herdr_retry.INCOMPATIBLE


def test_stop_during_a_failed_sources_close_finishes_that_close_before_returning(
    tmp_path: Path, quick: None
) -> None:
    config, _token = enrolled(tmp_path)
    world = World()
    world.close_gate = asyncio.Event()

    async def scenario() -> None:
        runtime = make(config, world)
        starting = asyncio.create_task(runtime.start(asyncio.Event()))
        try:
            await asyncio.wait_for(world.close_entered.wait(), 5)
            asyncio.get_running_loop().call_later(0.05, world.close_gate.set)  # type: ignore[union-attr]
            await runtime.close()
            assert world.sources[0].closed == 1  # shutdown waited for the close it did not start
            assert runtime.herdr.retry_task is None
        finally:
            starting.cancel()
            await asyncio.gather(starting, return_exceptions=True)

    run(scenario)


def test_a_source_whose_close_failed_is_not_replaced_until_a_close_succeeds(
    tmp_path: Path, quick: None
) -> None:
    config, _token = enrolled(tmp_path)
    world = World()
    world.close_fails = True

    async def scenario() -> None:
        runtime = make(config, world)
        try:
            await runtime.start(asyncio.Event())
            assert runtime.herdr.issue == herdr_retry.CLEANUP_ISSUE
            await until(lambda: world.close_calls >= 4)
            assert (
                world.made == 1 and runtime.herdr.link_task is None
            )  # the same close, tried again
            world.close_fails = False
            world.up = True
            await until(lambda: runtime.herdr.link_task is not None)
            first = world.sources[0]
            assert (
                first.closed == 1 and world.made == 2 and runtime.herdr.source is world.sources[1]
            )
        finally:
            await runtime.close()

    run(scenario)


def test_forty_failed_attempts_leave_no_source_alive(tmp_path: Path, quick: None) -> None:
    config, _token = enrolled(tmp_path)
    world = World()
    world.keep = False

    async def scenario() -> None:
        runtime = make(config, world)
        for _ in range(40):
            assert await runtime.herdr._attach(None) == herdr_retry.TRANSIENT
        gc.collect()
        alive = [ref for ref in world.refs if ref() is not None]
        assert world.made == 40 and len(alive) <= 1  # only the current one is held
        await runtime.close()

    run(scenario)


def test_a_protocol_failure_with_a_failing_close_keeps_closing_then_reports_it_and_stops(
    tmp_path: Path, quick: None
) -> None:
    config, _token = enrolled(tmp_path)
    world = World()
    world.error = lambda: HerdrProtocolError("protocol_mismatch", "synthetic")
    world.close_fails = True

    async def scenario() -> None:
        runtime = make(config, world)
        try:
            await runtime.start(asyncio.Event())
            retry = runtime.herdr.retry_task
            assert retry is not None and runtime.herdr.issue == herdr_retry.CLEANUP_ISSUE
            await until(lambda: world.close_calls >= 4)  # the close is not abandoned
            assert world.made == 1 and runtime.herdr.link_task is None
            world.close_fails = False
            await until(lambda: retry.done())
            assert runtime.herdr.issue == herdr_retry.INCOMPATIBLE_ISSUE  # the original reason
            assert world.sources[0].closed == 1
            assert world.made == 1 and runtime.herdr.link_task is None  # no source for this failure
            world.up = True  # Herdr "recovering" later changes nothing: the owner must act
            await asyncio.sleep(0.05)
            assert world.made == 1
        finally:
            await runtime.close()

    run(scenario)


def test_a_transient_failure_with_a_failing_close_keeps_the_waiting_reason_once_closed(
    tmp_path: Path, quick: None
) -> None:
    config, _token = enrolled(tmp_path)
    world = World()
    world.close_fails = True

    async def scenario() -> None:
        runtime = make(config, world)
        try:
            await runtime.start(asyncio.Event())
            assert runtime.herdr.issue == herdr_retry.CLEANUP_ISSUE
            await until(lambda: world.close_calls >= 3)
            world.close_fails = False
            await until(lambda: world.made >= 2)  # closed, then Herdr is still simply not there
            assert runtime.herdr.issue in (herdr_retry.WAITING_ISSUE, herdr_retry.CLEANUP_ISSUE)
            assert world.sources[0].closed == 1
        finally:
            await runtime.close()

    run(scenario)
