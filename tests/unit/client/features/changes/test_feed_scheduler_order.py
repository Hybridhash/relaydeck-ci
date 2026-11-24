"""A stop immediately followed by a start still leaves the feed polling."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from relaydeck.client.features.changes.hooks import _Feed


def test_a_source_flap_waits_out_the_stop_before_starting_again() -> None:
    async def scenario() -> None:
        feed = _Feed(SimpleNamespace(), lambda: None)  # type: ignore[arg-type]
        feed.scheduler.start()
        assert feed.scheduler.running
        feed.stop_scheduler()  # the stop is only scheduled at this point
        await asyncio.create_task(feed.start_scheduler())
        assert feed.scheduler.running
        await feed.scheduler.stop()
        assert not feed.scheduler.running

    asyncio.run(scenario())
