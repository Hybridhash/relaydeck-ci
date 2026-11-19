"""Control-tree snapshots of the terminal screens on the phone layout.

Agent (compose and direct-input states, tools open) and ordinary shell,
rendered twice per scenario over the scripted terminal sessions the
terminal UI tests use. The direct-typing clock is pinned, as the keyboard
tests pin it.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

import relaydeck.client.widgets.terminal.direct_typing as direct_typing
from tests.support.host_harness import run
from tests.support.terminal.direct_typing import _direct, open_compose
from tests.support.ui.keyboard_hit_region import keyboard_hit_region_expected
from tests.support.ui.snapshot import snapshot_json, snapshot_scenario

GOLDEN = Path(__file__).parents[3] / "snapshots" / "golden"


@pytest.fixture(autouse=True)
def _fixed_typing_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(direct_typing, "_typing_now", lambda: 1000.0)


def _snap(name: str, make: Callable[[], Awaitable[Any]]) -> None:
    if name not in {
        "terminal-agent-direct-390",
        "terminal-agent-compose-390",
        "terminal-agent-tools-390",
        "terminal-agent-direct-768",
    }:
        run(snapshot_scenario(make, GOLDEN, name))
        return

    async def scenario() -> None:
        first = snapshot_json(await make())
        second = snapshot_json(await make())
        assert first == second, f"snapshot {name} is not identical across two renders"
        expected = json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))
        assert json.loads(first) == keyboard_hit_region_expected(expected)

    run(scenario())


def test_agent_direct_input_snapshot() -> None:
    async def make() -> Any:
        app, _session, _socket, _focuses = await _direct(False)
        await app.settle()
        return app

    _snap("terminal-agent-direct-390", make)


def test_agent_compose_snapshot() -> None:
    async def make() -> Any:
        app, _session, _socket, _focuses = await _direct(False)
        await open_compose(app)
        await app.settle()
        return app

    _snap("terminal-agent-compose-390", make)


def test_agent_tools_open_snapshot() -> None:
    async def make() -> Any:
        app, _session, _socket, _focuses = await _direct(False)
        await app.set_props_and_fire("terminal-tools-button", {}, "click")
        await app.settle()
        return app

    _snap("terminal-agent-tools-390", make)


def test_agent_direct_input_tablet_snapshot() -> None:
    async def make() -> Any:
        app, _session, _socket, _focuses = await _direct(False, "medium")
        await app.settle()
        return app

    _snap("terminal-agent-direct-768", make)


def test_shell_terminal_snapshot() -> None:
    async def make() -> Any:
        app, _session, _socket, _focuses = await _direct(True)
        await app.settle()
        return app

    _snap("terminal-shell-390", make)
