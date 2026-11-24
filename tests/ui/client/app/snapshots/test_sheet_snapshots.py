"""Control-tree snapshots of the creation sheets over the Console.

Rendered through the shipped root with the stub controller/transport the
navigation tests use, twice per scenario.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from tests.support.host_harness import run
from tests.support.ui.navigation import _render_root, _state
from tests.support.ui.snapshot import snapshot_scenario

GOLDEN = Path(__file__).parents[3] / "snapshots" / "golden"


def _snap(name: str, make: Callable[[], Awaitable[Any]]) -> None:
    run(snapshot_scenario(make, GOLDEN, name))


def test_start_agent_sheet_snapshot() -> None:
    async def make() -> Any:
        app, _controller, _transport, _bridge, _pushed = await _render_root(_state())
        await app.set_props_and_fire("console-new-agent", {}, "click")
        await app.wait_for("start-agent-sheet")
        await app.settle()
        return app

    _snap("sheet-start-agent-1280", make)


def test_new_terminal_sheet_snapshot() -> None:
    async def make() -> Any:
        app, _controller, _transport, _bridge, _pushed = await _render_root(_state())
        await app.set_props_and_fire("console-rail-terminals", {}, "click")
        await app.set_props_and_fire("new-terminal-open", {}, "click")
        await app.wait_for("new-terminal-sheet")
        await app.settle()
        return app

    _snap("sheet-new-terminal-1280", make)
