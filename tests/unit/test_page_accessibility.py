"""One page-owned read, current subscribers, and motion preference values."""

from __future__ import annotations

import asyncio

import pytest

from relaydeck.client.app.state.accessibility import AccessibilitySnapshot, PageAccessibility


@pytest.mark.parametrize(
    "flags",
    [
        {},
        {"reduce_motion": True},
        {"disable_animations": True},
        {"reduce_motion": True, "disable_animations": True},
    ],
)
def test_motion_is_the_union_of_the_two_native_flags(flags: dict[str, bool]) -> None:
    async def scenario() -> None:
        calls = 0

        async def read() -> dict[str, bool]:
            nonlocal calls
            calls += 1
            return flags | {"bold_text": True}

        owner = PageAccessibility(read)
        snapshots: list[AccessibilitySnapshot] = []
        stop = owner.subscribe(snapshots.append)
        owner.start()
        owner.start()
        await asyncio.sleep(0.01)
        assert calls == 1
        assert owner.snapshot.motion_reduced == any(flags.values())
        assert owner.snapshot.bold_text
        assert len(snapshots) == 1
        stop()
        owner.close()

    asyncio.run(scenario())


def test_replacing_resource_subscribers_does_not_repeat_the_page_read() -> None:
    async def scenario() -> None:
        gate: asyncio.Future[dict[str, bool]] = asyncio.get_running_loop().create_future()
        calls = 0

        async def read() -> dict[str, bool]:
            nonlocal calls
            calls += 1
            return await gate

        owner = PageAccessibility(read)
        old: list[AccessibilitySnapshot] = []
        new: list[AccessibilitySnapshot] = []
        retire = owner.subscribe(old.append)
        owner.start()
        await asyncio.sleep(0)
        retire()
        current = owner.subscribe(new.append)
        owner.start()
        gate.set_result({"reduce_motion": True})
        await asyncio.sleep(0.01)
        assert calls == 1 and old == []
        assert len(new) == 1 and new[0].motion_reduced
        current()
        owner.close()
        assert owner._task is None and not owner._listeners

    asyncio.run(scenario())


def test_two_pages_read_independently() -> None:
    async def scenario() -> None:
        calls = 0

        async def read() -> dict[str, bool]:
            nonlocal calls
            calls += 1
            return {}

        owners = [PageAccessibility(read), PageAccessibility(read)]
        for owner in owners:
            owner.start()
        await asyncio.sleep(0.01)
        assert calls == 2
        for owner in owners:
            owner.close()

    asyncio.run(scenario())


def test_page_retirement_discards_a_late_result_and_drops_subscribers() -> None:
    async def scenario() -> None:
        gate: asyncio.Future[dict[str, bool]] = asyncio.get_running_loop().create_future()

        async def read() -> dict[str, bool]:
            try:
                return await asyncio.shield(gate)
            except asyncio.CancelledError:
                return await gate

        owner = PageAccessibility(read)
        seen: list[AccessibilitySnapshot] = []
        owner.subscribe(seen.append)
        owner.start()
        await asyncio.sleep(0.01)
        owner.close()
        gate.set_result({"reduce_motion": True})
        await asyncio.sleep(0.01)
        assert seen == []
        assert not owner.snapshot.motion_reduced
        assert owner._task is None and not owner._listeners

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["unsupported", "timeout", "absent", "retired"])
def test_failed_reads_preserve_defaults_and_are_not_retried(mode: str) -> None:
    async def scenario() -> None:
        calls = 0

        async def read() -> dict[str, bool]:
            nonlocal calls
            calls += 1
            if mode == "timeout":
                await asyncio.sleep(10)
            if mode == "retired":
                return {"reduce_motion": True}
            raise RuntimeError("service unavailable")

        owner = PageAccessibility(
            None if mode == "absent" else read, timeout=0.001, current=lambda: mode != "retired"
        )
        owner.start()
        await asyncio.sleep(0.01)
        owner.start()
        assert calls == (0 if mode == "absent" else 1)
        assert not owner.snapshot.motion_reduced
        owner.close()
        assert owner._task is None and not owner._listeners

    asyncio.run(scenario())
