"""Wide touch hunk headers expose an unobscured selected surface."""

from __future__ import annotations

from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as t
from tests.support.host_harness import run
from tests.ui.git.gateway import Gateway
from tests.ui.git.harness import click, files, render


def test_hunk_tap_selects_visible_header_and_question_scope() -> None:
    sent: list[tuple[Any, ...]] = []

    def ask(*args: Any) -> bool:
        sent.append(args)
        return True

    async def scenario() -> None:
        app = await render(Gateway(files()), wide=True, on_ask=ask)
        app.page.width, app.page.height = 1376, 1000
        app.page.platform = ft.PagePlatform.IOS
        await app.wait_for("changes-file-0")
        await click(app, "changes-file-0")
        await app.wait_for("diff-hunk-0-1")
        await click(app, "diff-hunk-0-1")
        header = app.find("diff-hunk-0-1")
        assert header.data["selected"] and header.bgcolor == t.CARD_SELECTED
        assert header.height >= t.MIN_TOUCH
        assert not isinstance(header.content, ft.Container) or header.content.bgcolor in (
            None,
            t.CARD_SELECTED,
        )
        assert header.ink
        await click(app, "diff-ask")
        assert "hunk" in app.find("changes-ask-title").value
        await click(app, "changes-ask-send")
        assert sent[0][0].kind == "hunk"

    run(scenario())
