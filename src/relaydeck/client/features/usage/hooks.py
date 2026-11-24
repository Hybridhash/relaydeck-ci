"""Usage feed lifecycle. `use_usage_feed` keys data by account/transport identity, host, query
(days and hour), and space.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Hashable
from typing import Any

import flet as ft

from relaydeck.client.features.usage.api import (
    StaleUsageRequest,
    UsageFetch,
    UsageFetchError,
    UsageHttpError,
    UsageQuery,
    fetch_usage,
)
from relaydeck.client.features.usage.loop import (
    STATUS_UNAVAILABLE,
    FeedState,
    RefreshGate,
    UsageFeed,
    run_usage_loop,
)


def now_ms() -> int:
    return int(time.time() * 1000)


def use_usage_feed(
    *,
    fetch: UsageFetch | None,
    identity: Hashable,
    host_id: str,
    days: int,
    space_id: str,
    active: bool,
    hour: bool = False,
) -> tuple[FeedState, Callable[[], None], bool]:
    """The one refresh loop for a view. Returns (feed state, request a manual refresh,
    refreshing)."""
    feed_ref: Any = ft.use_ref(None)
    task_ref: Any = ft.use_ref(None)
    gate_ref: Any = ft.use_ref(None)
    manual_ref: Any = ft.use_ref(False)
    _tick, set_tick = ft.use_state(0)
    nonce, set_nonce = ft.use_state(0)
    if feed_ref.current is None:
        feed_ref.current = UsageFeed()
    if gate_ref.current is None:
        gate_ref.current = RefreshGate()
    feed: UsageFeed = feed_ref.current
    gate: RefreshGate = gate_ref.current
    scope = (identity, host_id)
    key = (identity, host_id, days, space_id, hour)

    def start() -> None:
        manual = bool(manual_ref.current)
        manual_ref.current = False
        if not active or fetch is None or not host_id:
            if manual:
                gate.finish()  # a request that can no longer run releases the refresh gate
            return
        feed.set_key(key, scope)
        if manual:
            feed.clear_page_too_large()  # one manual try; an oversize answer stops it again
        query = UsageQuery(host_id=host_id, days=days, space_id=space_id or None, hour=hour)

        async def refresh() -> None:
            nonlocal manual
            force = manual
            manual = False
            generation = feed.begin()
            try:
                page = await fetch_usage(
                    fetch,
                    query,
                    is_current=lambda: feed.generation == generation,
                    previous=None if force else feed.state.page,
                )
            except StaleUsageRequest:
                return
            except UsageHttpError as error:
                feed.fail(generation, error.code, disabled=error.code == "usage_disabled")
            except UsageFetchError as error:
                feed.fail(generation, error.code)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a fixed code only; nothing from the error is shown
                feed.fail(generation, "usage_unavailable")
            else:
                feed.accept(generation, page, now_ms())
            finally:
                if force:
                    gate.finish()
            set_tick(lambda value: value + 1)

        task_ref.current = asyncio.create_task(
            run_usage_loop(
                active=lambda: active and not feed.auto_stopped(),
                refresh=refresh,
                delay=feed.retry_delay,
            )
        )

    def stop() -> None:
        feed.stop()
        task = task_ref.current
        if task is not None and not task.done():
            task.cancel()
        task_ref.current = None

    ft.use_effect(start, dependencies=[key, active, fetch is not None, nonce], cleanup=stop)

    def request_refresh() -> None:
        if active and fetch is not None and host_id and gate.try_start():
            manual_ref.current = True
            set_nonce(lambda value: value + 1)

    if fetch is None or not host_id:
        return FeedState(status=STATUS_UNAVAILABLE), request_refresh, False
    return feed.visible(key, active, scope), request_refresh, gate.running
