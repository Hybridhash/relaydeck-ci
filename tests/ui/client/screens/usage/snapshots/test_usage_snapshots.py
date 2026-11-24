"""Control-tree snapshots of the Usage panel: loading, data and empty.

The document and the panel clock are fixed to one synthetic instant, so the
goldens do not move with the calendar.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import flet as ft
import pytest

import relaydeck.client.features.usage.hooks as hooks_usage
from relaydeck.client.screens.usage.screen import UsagePanel
from tests.support.host_harness import render_component, run
from tests.support.ui.snapshot import snapshot_scenario

GOLDEN = Path(__file__).parents[4] / "snapshots" / "golden"

NOW_MS = int(datetime(2026, 1, 15, 12, 0, tzinfo=UTC).timestamp() * 1000)
DAY = "2026-01-15"
_COUNTERS = {
    "input_uncached": 0,
    "cache_read": 0,
    "cache_write_short": 0,
    "cache_write_long": 0,
    "cache_write_unknown": 0,
    "output": 0,
    "reasoning": 0,
}


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hooks_usage, "now_ms", lambda: NOW_MS)
    monkeypatch.setattr("relaydeck.client.features.usage.ui.now_ms", lambda: NOW_MS)


def _row(tokens: int, *, space: str | None = None) -> dict[str, Any]:
    scope = "workspace" if space else "overall"
    return {
        "day": DAY,
        "workspace_id": space,
        "kind": "kind-a",
        "model": "model-a",
        "counters": {**_COUNTERS, "input_uncached": tokens},
        "sessions": 1,
        "cost_micro_usd": tokens,
        "scope": scope,
        "price_snapshot": "2026-01-01",
        "priced_cost_micro_usd": tokens,
        "priced_tokens": tokens,
        "unpriced_tokens": 0,
        "hour": None,
    }


def _doc(*, empty: bool = False) -> dict[str, Any]:
    rows = [] if empty else [_row(2_000_000, space="space-0"), _row(2_000_000)]
    return {
        "schema_version": 1,
        "host_id": "host-a",
        "generated_ms": NOW_MS,
        "timezone": "UTC",
        "price_table_date": "2026-01-01",
        "next_cursor": None,
        "rows": rows,
        "limits": [
            {
                "kind": "kind-a",
                "quota_key": "q1",
                "observed_ms": NOW_MS - 60_000,
                "status": "fresh",
                "windows": [
                    {
                        "window_key": "primary",
                        "used_percent": 39.0,
                        "window_minutes": 10080,
                        "resets_at_ms": NOW_MS + 3 * 86_400_000,
                        "status": "fresh",
                    }
                ],
            }
        ],
        "coverage": [],
        "snapshot_labels": [{"date": "2026-01-01", "label": "Standard-tier estimate"}],
        "kinds": [
            {
                "kind": "kind-a",
                "display_name": "Alpha CLI",
                "tokens_reported": True,
                "limits_setup": None,
                "sources": [],
            }
        ],
        "workspaces": [{"workspace_id": "space-0", "label": "Space 0", "last_used_day": DAY}],
        "capacity": "ok",
    }


def _panel(fetch: Any, *, dark: bool, width: float) -> Callable[[], Awaitable[Any]]:
    async def make() -> Any:
        @ft.component
        def Screen() -> Any:
            return ft.Column(
                controls=[
                    UsagePanel(
                        fetch=fetch,
                        identity="acct-a",
                        host_id="host-a",
                        active=True,
                        dark=dark,
                        wide=width >= 1024,
                        width=width,
                    )
                ]
            )

        app = await render_component(Screen)
        await app.settle()
        return app

    return make


def _snap(name: str, make: Callable[[], Awaitable[Any]]) -> None:
    run(snapshot_scenario(make, GOLDEN, name))


def test_usage_loading_snapshot() -> None:
    async def never(_query: Any) -> Any:
        await asyncio.Event().wait()

    _snap("usage-loading-390-dark", _panel(never, dark=True, width=390))


def test_usage_data_dark_snapshot() -> None:
    async def fetch(_query: Any) -> Any:
        return _doc()

    _snap("usage-data-390-dark", _panel(fetch, dark=True, width=390))


def test_usage_data_light_wide_snapshot() -> None:
    async def fetch(_query: Any) -> Any:
        return _doc()

    _snap("usage-data-1440-light", _panel(fetch, dark=False, width=1440))


def test_usage_empty_snapshot() -> None:
    async def fetch(_query: Any) -> Any:
        return _doc(empty=True)

    _snap("usage-empty-390-dark", _panel(fetch, dark=True, width=390))
