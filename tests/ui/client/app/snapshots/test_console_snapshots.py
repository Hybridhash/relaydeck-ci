"""Control-tree snapshots of the Console and its shell destinations.

Every scenario renders twice through the host harness with the synthetic
inventory worlds the other Console tests use; the two trees must be
identical and must match the golden. Regenerate with
``RELAYDECK_UPDATE_SNAPSHOTS=1``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from relaydeck.domain.models import AppState, Selection
from tests.support.host_harness import run
from tests.support.ui.navigation import _render_shell, _state
from tests.support.ui.snapshot import snapshot_scenario
from tests.support.ui.spaces_filter import _spaces_state

GOLDEN = Path(__file__).parents[3] / "snapshots" / "golden"


def _snap(name: str, render: Callable[[], Awaitable[Any]]) -> None:
    run(snapshot_scenario(render, GOLDEN, name))


# Phone (320, the smallest width), tablet and desktop; dark and light.
_CONSOLE_MATRIX = [
    (320, True),
    (320, False),
    (768, True),
    (1440, True),
    (1440, False),
]


@pytest.mark.parametrize(("width", "dark"), _CONSOLE_MATRIX)
def test_console_agents_snapshot(width: int, dark: bool) -> None:
    async def render() -> Any:
        return await _render_shell(_state(), viewport_width=width, is_wide=width >= 1100, dark=dark)

    _snap(f"console-agents-{width}-{'dark' if dark else 'light'}", render)


def test_console_empty_snapshot() -> None:
    async def render() -> Any:
        return await _render_shell(AppState(), viewport_width=320, is_wide=False, dark=True)

    _snap("console-empty-320-dark", render)


def test_console_several_hosts_snapshot() -> None:
    async def render() -> Any:
        return await _render_shell(_spaces_state(), viewport_width=390, is_wide=False, dark=True)

    _snap("console-hosts-390-dark", render)


def test_console_agent_detail_snapshot() -> None:
    async def render() -> Any:
        selected = _state(selection=Selection(host_id="home-mac", session_key="home-mac:term-1"))
        return await _render_shell(selected, viewport_width=1440, is_wide=True, dark=True)

    _snap("console-agent-detail-1440-dark", render)


def test_terminals_destination_snapshot() -> None:
    async def render() -> Any:
        return await _render_shell(
            _state(), destination="terminals", viewport_width=1440, is_wide=True, dark=True
        )

    _snap("terminals-1440-dark", render)
