"""Idle limiter cleanup preserves caller budgets and has a finite application lifetime."""

from __future__ import annotations

import asyncio
import gc
import random
import time
import weakref
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from relaydeck.devtools.fakes.gateway import FakeBackend
from relaydeck.gateway.app.bootstrap import create_app
from relaydeck.gateway.app.bucket_retirement import BucketRetirement
from relaydeck.gateway.app.state import GatewayState
from relaydeck.gateway.http.rate_limits import RateLimiter
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)


async def _eventually(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0.005)


def test_only_fully_replenished_keys_retire() -> None:
    budget = RateLimiter(2, 1 / 1000)
    assert budget.check("active", 0, 2)
    assert budget.check("idle", 0, 1)
    assert budget.retire(1000) == 1
    assert set(budget._buckets) == {"active"}
    assert not budget.check("active", 1000, 2)
    assert budget.retire(1999) == 0
    assert budget.check("active", 2000, 2)
    assert not budget.check("active", 2000)


def test_non_refilling_budget_never_forgets_consumption() -> None:
    budget = RateLimiter(1, 0)
    assert budget.check("caller", 0)
    assert budget.retire(365 * 24 * 3600 * 1000) == 0
    assert not budget.check("caller", 365 * 24 * 3600 * 1000)
    assert budget.next_expiry_ms is None


def test_fractional_refill_never_retires_early() -> None:
    budget = RateLimiter(1, 1 / 3)
    assert budget.check("caller", 0)
    assert budget.next_expiry_ms == 3
    assert budget.retire(2) == 0
    assert not budget.check("caller", 2)
    assert budget.check("caller", 3)


def test_retirement_matches_unpruned_budget_decisions() -> None:
    budget = RateLimiter(100, 0.1)
    unpruned: dict[str, tuple[float, int]] = {}
    rng = random.Random(17)  # noqa: S311 - deterministic budget trace, not secret material
    now = 0
    for _ in range(10000):
        now += rng.randrange(6)
        key, cost = str(rng.randrange(30)), rng.choice([0, 1, 5, 20, 200])
        tokens, updated = unpruned.get(key, (100.0, now))
        tokens = min(100, tokens + (now - updated) * 0.1)
        allowed = tokens >= cost
        unpruned[key] = (tokens - cost if allowed else tokens, now)
        assert budget.check(key, now, cost) is allowed


def test_repeated_hot_key_does_not_accumulate_deadlines() -> None:
    budget = RateLimiter(100, 0.1)
    for _ in range(10000):
        budget.check("same", 0, 0.001)
    assert len(budget._deadlines) <= 2 * len(budget._expires) + 64
    budget.retire(10000)
    assert budget._buckets == budget._expires == {}
    assert budget._deadlines == []


def test_idle_owner_exits_restarts_and_unsubscribes_on_stop() -> None:
    async def scenario() -> None:
        owner = BucketRetirement(lambda: int(time.monotonic() * 1000))
        budget = RateLimiter(1, 0.05)
        owner.register(budget)
        owner.start()
        assert owner._task is None
        assert budget.check("first", int(time.monotonic() * 1000))
        first = owner._task
        assert first is not None
        await _eventually(lambda: owner._task is None)
        assert budget._buckets == {}
        assert owner._task is None
        assert budget.check("next", int(time.monotonic() * 1000))
        assert owner._task is not None and owner._task is not first
        await owner.stop()
        assert owner._task is None
        assert not budget._listeners
        owner.start()
        assert owner._task is not None
        await _eventually(lambda: owner._task is None)
        assert budget._buckets == {}
        assert owner._task is None
        await owner.stop()

    asyncio.run(scenario())


def test_owner_does_not_keep_discarded_router_budget_alive() -> None:
    async def scenario() -> None:
        owner = BucketRetirement(lambda: 0)
        owner.start()
        budget = RateLimiter(1, 0.001)
        owner.register(budget)
        assert budget.check("caller", 0)
        await asyncio.sleep(0)
        reference = weakref.ref(budget)
        del budget
        gc.collect()
        assert reference() is None
        await _eventually(lambda: owner._task is None)
        assert not owner._budgets
        assert owner._task is None
        await owner.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize("hosted", [False, True])
def test_lifespan_retires_input_and_command_keys_without_another_request(hosted: bool) -> None:
    state = GatewayState(backend=FakeBackend(now_ms=lambda: 1))
    state.now_ms = lambda: int(time.monotonic() * 1000)
    state.commands = RateLimiter(1, 0.05)
    state.input_bytes = RateLimiter(1, 0.05)
    app = create_app(state)

    @app.get("/v1/idle-budget-probe")
    async def probe() -> dict[str, int]:
        assert state.commands.check("account", state.now_ms())
        assert state.input_bytes.check("lease", state.now_ms())
        await _eventually(lambda: not state.commands._buckets and not state.input_bytes._buckets)
        return {"commands": len(state.commands._buckets), "input": len(state.input_bytes._buckets)}

    if hosted:
        from relaydeck.companion.app.hosted_web.serve import WebSessionStore, build_web_app

        origin = "https://console.example"
        app, _ = build_web_app(
            gateway_state=state,
            gateway_app=app,
            flet_main=lambda page: None,
            public_origin=origin,
            cookie_secret=b"x" * 32,
            store=WebSessionStore(origin, b"x" * 32),
        )
    try:
        with TestClient(app) as client:
            assert client.get("/v1/idle-budget-probe").json() == {"commands": 0, "input": 0}
        assert not state.commands._listeners
        assert not state.input_bytes._listeners
    finally:
        state.push.close()


def test_every_git_read_budget_is_registered_for_idle_retirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[RateLimiter] = []
    original = BucketRetirement.register

    def register(owner: BucketRetirement, budget: RateLimiter) -> None:
        seen.append(budget)
        original(owner, budget)

    monkeypatch.setattr(BucketRetirement, "register", register)
    state = GatewayState(backend=FakeBackend(now_ms=lambda: 1))
    state.now_ms = lambda: int(time.monotonic() * 1000)
    app = create_app(state)
    assert len(seen) == 6
    assert {id(b) for b in seen} >= {id(state.commands), id(state.input_bytes)}
    readers = [b for b in seen if b is not state.commands and b is not state.input_bytes]
    assert sorted(b.capacity for b in readers) == [2, 5, 5, 10]

    @app.get("/v1/read-budget-probe")
    async def probe() -> dict[str, int]:
        for budget in readers:
            budget.refill_per_ms = 1000
            assert budget.check("caller", state.now_ms(), budget.capacity)
        await _eventually(lambda: not any(b._buckets for b in readers))
        return {"retained": sum(len(b._buckets) for b in readers)}

    try:
        with TestClient(app) as client:
            assert client.get("/v1/read-budget-probe").json() == {"retained": 0}
        assert not any(b._listeners for b in seen)
    finally:
        state.push.close()
