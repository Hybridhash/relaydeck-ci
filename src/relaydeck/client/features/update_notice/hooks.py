"""One update notice for the Settings section and rail dot. Fetch after sign-in and on
foreground return at most once an hour.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import flet as ft

from relaydeck.client.features.update_notice.model import (
    UpdateNotice,
    after_error,
    fetch_due,
    parse,
)
from relaydeck.client.features.update_notice.source import UpdateNoticeSource
from relaydeck.client.platform.flet.events import spawn
from relaydeck.client.ports.transport import TransportError


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


def use_update_notice(
    transport: UpdateNoticeSource | None,
    foreground: bool,
    *,
    clock: Callable[[], int] = _now_ms,
) -> UpdateNotice | None:
    """The current notice, fetched once on mount (after sign-in) and again on a foreground
    return once the hourly interval has passed. A new transport (a new sign-in) fetches at
    once.
    """
    initial: tuple[int | None, UpdateNotice | None] = (None, None)
    scoped, set_scoped = ft.use_state(initial)
    book: Any = ft.use_ref(None)  # (transport id, last fetch ms)
    generation: Any = ft.use_ref(0)
    task: Any = ft.use_ref(None)
    available = callable(getattr(transport, "updates", None))
    identity = id(transport) if available else None

    def fence() -> None:
        # Transport replaced, capability lost, or unmounted: anything still in
        # flight from the old identity completes into a stale generation and
        # is discarded, never applied.
        generation.current += 1
        if task.current is not None:
            task.current.cancel()
        task.current = None

    ft.use_effect(lambda: None, dependencies=[identity], cleanup=fence)

    def start() -> None:
        if not (foreground and available) or transport is None:
            return
        now = clock()
        seen = book.current
        last = seen[1] if seen is not None and seen[0] == identity else None
        if not fetch_due(last, now):
            return
        book.current = (identity, now)
        mine = generation.current
        previous = scoped[1] if scoped[0] == identity else None

        async def fetch() -> None:
            try:
                parsed = parse(await transport.updates())
            except TransportError as exc:
                if generation.current == mine:
                    set_scoped((identity, after_error(previous, exc.code)))
                return
            except Exception:  # noqa: BLE001 - any other failure keeps the last value
                return
            if parsed is not None and generation.current == mine:
                set_scoped((identity, parsed))

        task.current = spawn(fetch(), label="update-notice-fetch")

    ft.use_effect(start, dependencies=[foreground, identity, available])
    return scoped[1] if scoped[0] == identity else None
