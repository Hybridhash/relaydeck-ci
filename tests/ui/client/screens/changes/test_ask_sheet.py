"""Synthetic question entry points and frozen reader return contexts."""

from __future__ import annotations

from typing import Any

import pytest

from relaydeck.client.features.ask_agent.model import build_prompt
from tests.support.flet_layout import expand_violations
from tests.support.host_harness import run
from tests.ui.git.gateway import Gateway
from tests.ui.git.harness import click, files, present, render


@pytest.mark.parametrize("wide", [False, True])
def test_whole_section_presets_and_one_shot(wide: bool) -> None:
    sent: list[tuple[Any, ...]] = []

    def ask(*args: Any) -> bool:
        sent.append(args)
        return True

    async def scenario() -> None:
        app = await render(Gateway(files()), wide=wide, on_ask=ask)
        await app.wait_for("changes-files")
        await click(app, "changes-ask")
        await app.wait_for("changes-ask-sheet")
        assert app.find("changes-ask-question").value.startswith("What does")
        await click(app, "changes-ask-preset-1")
        assert app.find("changes-ask-question").value == "Any risk?"
        assert expand_violations(app.page) == []
        callback = app.find("changes-ask-send").on_click
        await click(app, "changes-ask-send")
        assert len(sent) == 1 and not present(app, "changes-ask-sheet")
        assert "working tree vs aaaaaaaa" in build_prompt(sent[0][0], sent[0][1])
        assert callback is not None
        callback(None)
        assert len(sent) == 1

    run(scenario())


def test_a_phone_file_can_be_asked_about_as_a_whole() -> None:
    sent: list[tuple[Any, ...]] = []

    def ask(*args: Any) -> bool:
        sent.append(args)
        return True

    async def scenario() -> None:
        gateway = Gateway(files())
        app = await render(gateway, wide=False, on_ask=ask, copied=[])
        await app.wait_for("changes-file-0")
        await click(app, "changes-file-0")
        await app.wait_for("diff-ask")
        assert app.find("diff-ask").height >= 48
        # A hunk asked about from the Copy sheet, then cancelled, stays selected for a wider
        # layout; the phone header still asks about the whole file.
        await click(app, "diff-copy")
        await click(app, "changes-ask-hunk-0-1-None")
        assert app.find("changes-ask-title").value == "Ask about this hunk"
        await click(app, "changes-ask-cancel")
        await click(app, "diff-ask")
        assert "file" in app.find("changes-ask-title").value
        await click(app, "changes-ask-preset-1")
        await click(app, "changes-ask-send")
        assert len(sent) == 1 and sent[0][0].kind == "file"
        assert expand_violations(app.page) == []

    run(scenario())


def test_wide_hunk_selection_and_same_snapshot_return() -> None:
    sent: list[tuple[Any, ...]] = []

    def ask(*args: Any) -> bool:
        sent.append(args)
        return True

    async def scenario() -> None:
        gateway = Gateway(files())
        app = await render(gateway, wide=True, on_ask=ask, copied=[])
        await app.wait_for("changes-file-0")
        await click(app, "changes-file-0")
        await app.wait_for("diff-ask")
        await click(app, "diff-ask")
        assert "file" in app.find("changes-ask-title").value
        await click(app, "changes-ask-cancel")
        assert app.find("diff-hunk-0-1").height >= 48
        await click(app, "diff-hunk-0-1")
        await click(app, "diff-ask")
        assert "hunk" in app.find("changes-ask-title").value
        await click(app, "changes-ask-send")
        scope, _, bookmark, _ = sent[0]
        assert scope.kind == "hunk" and bookmark.hunk == (0, 1)
        restored = await render(gateway, wide=True, on_ask=ask, bookmark=bookmark)
        await restored.wait_for("diff-ask")
        await click(restored, "diff-ask")
        assert "hunk" in restored.find("changes-ask-title").value
        gateway.edit()
        refreshed = await render(gateway, wide=True, on_ask=ask, bookmark=bookmark)
        await refreshed.wait_for("diff-ask")
        assert present(refreshed, "changes-reading-notice")
        await click(refreshed, "diff-ask")
        assert "file" in refreshed.find("changes-ask-title").value

    run(scenario())


def test_phone_line_hunk_and_partial_disabled() -> None:
    sent: list[tuple[Any, ...]] = []

    def ask(*args: Any) -> bool:
        sent.append(args)
        return True

    async def scenario() -> None:
        app = await render(Gateway(files()), on_ask=ask, copied=[])
        await app.wait_for("changes-file-0")
        await click(app, "changes-file-0")
        await app.wait_for("diff-copy")
        await click(app, "diff-copy")
        assert app.find("changes-ask-hunk-0-0-None").disabled is False
        await click(app, "changes-copy-hunk-0-lines")
        await click(app, "changes-ask-line-0-0-1")
        assert not present(app, "changes-copy-sheet")
        assert "line" in app.find("changes-ask-title").value
        await click(app, "changes-ask-send")
        assert sent[0][0].old
        await click(app, "changes-files-back")
        await click(app, "changes-file-1")
        await app.wait_for("diff-copy")
        await click(app, "diff-copy")
        assert app.find("changes-ask-hunk-0-0-None").disabled
        assert app.find("changes-ask-hunk-0-0-None").tooltip
        await click(app, "changes-copy-hunk-0-lines")
        assert not app.find("changes-ask-line-0-0-0").disabled

    run(scenario())


@pytest.mark.parametrize("status", ["idle", "working", "blocked", "unknown", "exited"])
def test_sheet_rules(status: str) -> None:
    async def scenario() -> None:
        app = await render(Gateway(files()), on_ask=lambda *args: True, ask_status=status)
        await app.wait_for("changes-ask")
        disabled = status not in ("idle", "working")
        assert app.find("changes-ask").disabled == disabled
        if not disabled:
            await click(app, "changes-ask")
            if status == "working":
                assert app.find("changes-ask-busy").value.endswith(" is busy")
                assert app.find("changes-ask-send").content == "Send anyway"

    run(scenario())


def test_question_presets_and_reopening_replace_pending_native_text() -> None:
    async def scenario() -> None:
        app = await render(Gateway(files()), on_ask=lambda *args: True)
        await app.wait_for("changes-files")
        await click(app, "changes-ask")
        await click(app, "changes-ask-preset-1")
        app.session.apply_patch(app.find("changes-ask-question")._i, {"value": "pending edit"})
        await click(app, "changes-ask-preset-1")
        assert app.find("changes-ask-question").value == "Any risk?"
        app.session.apply_patch(app.find("changes-ask-question")._i, {"value": "pending edit"})
        await click(app, "changes-ask-cancel")
        await click(app, "changes-ask")
        assert app.find("changes-ask-question").value.startswith("What does this change do")

    run(scenario())
