"""The list area is built only while it is on screen."""

from __future__ import annotations

from typing import Any

import flet as ft
import pytest

import relaydeck.client.screens.changes.screen as screen
from tests.support.host_harness import run
from tests.ui.git.gateway import Gateway
from tests.ui.git.harness import click, files, graph, render, row_key


class Built:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.count = 0
        original = screen._list_area

        def counted(*args: Any, **kwargs: Any) -> ft.Control:
            self.count += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(screen, "_list_area", counted)


def test_an_open_diff_on_the_phone_does_not_build_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        built = Built(monkeypatch)
        app = await render(gateway, width=390, height=844, copied=[])
        await app.wait_for("changes-files")
        assert built.count >= 1  # the list is the body
        await click(app, "changes-file-0")
        await app.wait_for("diff-rows")
        settled = built.count
        await click(app, "diff-copy")  # renders that happen while the diff is the body
        await app.wait_for("changes-copy-sheet")
        await click(app, "changes-copy-close")
        await app.settle(10)
        assert built.count == settled
        await click(app, "changes-files-back")
        await app.wait_for("changes-files")
        assert built.count > settled  # and it is built again when it is shown

    run(scenario())


def test_a_pushed_commit_does_not_build_the_graph_list(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        built = Built(monkeypatch)
        app = await graph(gateway, width=390, height=844)
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-file-0")
        settled = built.count
        await click(app, "commit-file-0")
        await app.wait_for("diff-rows")
        await app.settle(10)
        assert built.count == settled

    run(scenario())


def test_beside_the_diff_on_a_wide_screen_the_list_is_still_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        built = Built(monkeypatch)
        app = await render(gateway, width=1376, height=1000, wide=True)
        await app.wait_for("changes-files")
        await click(app, "changes-file-0")
        await app.wait_for("diff-rows")
        before = built.count
        await click(app, "changes-file-1")
        await app.settle(10)
        assert built.count > before

    run(scenario())
