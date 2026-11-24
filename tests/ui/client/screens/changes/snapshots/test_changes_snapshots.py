"""Control-tree snapshots of the Changes screen: working, branch, graph, diff.

Rendered through the Changes route tests' synthetic gateway, twice per
scenario. Relative ages tick with the real clock and are scrubbed by the
snapshot helper; everything else in the world is fixed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from tests.support.host_harness import run
from tests.support.ui.snapshot import snapshot_scenario
from tests.ui.git.gateway import Gateway
from tests.ui.git.harness import click, render, small_files

GOLDEN = Path(__file__).parents[4] / "snapshots" / "golden"


def _snap(name: str, make: Callable[[], Awaitable[Any]]) -> None:
    run(snapshot_scenario(make, GOLDEN, name))


def test_changes_working_snapshot() -> None:
    async def make() -> Any:
        gateway = Gateway(small_files())
        app = await render(gateway)
        await app.wait_for("changes-files")
        await app.settle()
        return app

    _snap("changes-working-390", make)


def test_changes_file_diff_snapshot() -> None:
    async def make() -> Any:
        gateway = Gateway(small_files())
        app = await render(gateway)
        await app.wait_for("changes-files")
        await click(app, "changes-file-0")
        await app.wait_for("diff-rows")
        await app.settle()
        return app

    _snap("changes-diff-390", make)


def test_changes_wide_diff_snapshot() -> None:
    async def make() -> Any:
        gateway = Gateway(small_files())
        app = await render(gateway, wide=True)
        await app.wait_for("changes-files")
        await click(app, "changes-file-0")
        await app.wait_for("diff-rows")
        await app.settle()
        return app

    _snap("changes-diff-wide", make)


def test_changes_branch_snapshot() -> None:
    async def make() -> Any:
        gateway = Gateway(small_files())
        app = await render(gateway)
        await app.wait_for("changes-files")
        await click(app, "changes-scope-branch")
        await app.wait_for("changes-view-graph")
        await app.settle()
        return app

    _snap("changes-branch-390", make)


def test_changes_graph_snapshot() -> None:
    async def make() -> Any:
        gateway = Gateway(small_files())
        app = await render(gateway)
        await app.wait_for("changes-files")
        await click(app, "changes-scope-branch")
        await app.wait_for("changes-view-graph")
        await click(app, "changes-view-graph")
        await app.wait_for("changes-graph")
        await app.settle()
        return app

    _snap("changes-graph-390", make)
