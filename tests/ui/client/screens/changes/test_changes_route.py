"""The Changes route through the production wiring: polling, large diffs, states, Viewed.

A synthetic gateway answers the real routes' shapes, so the repository adapter, the refresh
scheduler, the feed and the screen run exactly as in the app.
"""

from __future__ import annotations

import asyncio
import re
import time
from types import SimpleNamespace
from typing import Any

import pytest

import relaydeck.client.screens.changes.parts.diff_view as diff_view
from relaydeck.client.ports.transport import TransportError
from relaydeck.client.screens.changes.parts.diff_view import ROW_HEIGHT
from tests.support.git_patches import added_patch, two_hunk_patch
from tests.support.host_harness import run
from tests.ui.git.gateway import KEY, FileSpec, Gateway
from tests.ui.git.harness import (
    built_rows,
    click,
    message_text,
    mount,
    present,
    render,
    scope_is,
    small_files,
    tick,
    until,
)


def test_polling_never_moves_the_reader_and_a_new_revision_says_updated() -> None:
    gateway = Gateway(small_files())

    async def scenario() -> None:
        app, _ = await mount(gateway)
        await app.wait_for("changes-files")
        await click(app, "changes-scope-branch")
        await app.wait_for("changes-files")
        await click(app, "changes-file-0")
        await app.wait_for("diff-rows")
        listed = gateway.count("changes")
        seen = gateway.count("summaries")

        await until(lambda: gateway.count("summaries") >= seen + 2)  # two polls, same worktree
        assert present(app, "diff-rows")
        assert not present(app, "changes-updated") and gateway.count("changes") == listed

        gateway.edit()  # the agent changed something: new revision, new snapshot and file ids
        await app.wait_for("changes-updated")
        assert present(app, "diff-rows")
        assert gateway.count("changes") == listed  # the reader's list was not reloaded

        await click(app, "changes-updated")
        await until(lambda: ("diff", "rev-2+origin/main:0", None, False) in gateway.calls)
        await app.wait_for("diff-rows")  # the same file, found again in the new snapshot
        assert not present(app, "changes-updated")
        await click(app, "changes-files-back")
        await app.wait_for("changes-files")
        assert scope_is(app, "branch")

    run(scenario())


def test_one_clipboard_service_serves_every_visit_to_the_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import flet as ft

    built: list[object] = []
    real = ft.Clipboard

    class Counting(real):  # type: ignore[valid-type, misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            built.append(self)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(ft, "Clipboard", Counting)
    gateway = Gateway(small_files())

    async def scenario() -> None:
        app, control = await mount(gateway)
        await app.wait_for("changes-files")
        for _ in range(2):  # Console -> Changes -> Console -> Changes
            control["back"]()
            await app.wait_for("console")
            control["forward_again"]()
            await app.wait_for("changes-files")
        assert len(built) == 1

    run(scenario())


def test_the_screen_shows_when_the_list_was_taken() -> None:
    gateway = Gateway(small_files())

    async def scenario() -> None:
        app, _ = await mount(gateway)
        await app.wait_for("changes-files")
        assert re.search(r"(^| · )as of 1[2-4] s ago$", app.find("changes-asof").value)

    run(scenario())


def test_load_diff_replaces_the_gate_and_pages_a_large_file() -> None:
    gateway = Gateway([FileSpec("big.py", added_patch("big.py", 1200), status="A")])

    async def scenario() -> None:
        app, _ = await mount(gateway)
        await app.wait_for("changes-files")
        await click(app, "changes-file-0")
        await app.wait_for("diff-load")
        assert not present(app, "diff-rows")

        await click(app, "diff-load")
        await app.wait_for("diff-rows")
        first = app.find("diff-rows")
        assert ("diff", "rev-1:0", None, True) in gateway.calls
        assert first.data["rows"] > 0 and present(app, "diff-more")
        assert not present(app, "diff-earlier") and not present(app, "diff-load")

        await click(app, "diff-more")
        await until(lambda: ("diff", "rev-1:0", "pg:1", True) in gateway.calls)
        await app.wait_for("diff-earlier")  # consent travels with every page
        assert app.find("diff-rows").data["rows"] <= 501

        await click(app, "diff-more")
        await until(lambda: ("diff", "rev-1:0", "pg:2", True) in gateway.calls)
        await until(lambda: not present(app, "diff-more"))
        assert app.find("diff-rows").data["rows"] > 0

        await click(app, "diff-earlier")
        await until(lambda: ("diff", "rev-1:0", "pg:1", True) in gateway.calls[3:])

    run(scenario())


@pytest.mark.parametrize(
    ("state", "words"),
    [
        ("no_repo", "not a git repository"),
        ("folder_gone", "no longer exists"),
        ("unavailable", "unavailable"),
        ("unsupported_config", "could run programs"),
        ("git_unavailable", "Git 2.45"),
    ],
)
def test_every_summary_state_shows_its_message(state: str, words: str) -> None:
    gateway = Gateway(small_files(), state=state)

    async def scenario() -> None:
        app, _ = await mount(gateway)
        await app.wait_for("changes-state")
        await until(lambda: words in message_text(app))
        assert not present(app, "changes-files")

    run(scenario())


def test_a_failed_poll_is_visible_and_waits_out_its_retry_window() -> None:
    gateway = Gateway(small_files())
    gateway.failures["summaries"] = TransportError(
        "unavailable", "busy", True, {"reason": "busy", "retry_after_s": 0.6}
    )

    async def scenario() -> None:
        app, _ = await mount(gateway)
        await app.wait_for("changes-state")
        await until(lambda: "unavailable" in message_text(app))  # never "Calculating" forever
        assert gateway.count("summaries") == 1
        await asyncio.sleep(0.3)
        assert gateway.count("summaries") == 1  # nothing inside the window
        del gateway.failures["summaries"]
        await app.wait_for("changes-files", timeout=3.0)  # asked again once it ended

    run(scenario())


def test_viewed_survives_leaving_and_reopening_but_not_new_content_or_another_account() -> None:
    gateway = Gateway(small_files())

    async def scenario() -> None:
        app, control = await mount(gateway)
        await app.wait_for("changes-files")
        await click(app, "changes-file-0")
        await app.wait_for("changes-viewed")
        assert app.find("changes-viewed").value is False
        await tick(app, "changes-viewed", True)
        assert app.find("changes-viewed").value is True

        control["navigate"]("agents")  # leave the route
        await app.wait_for("console")
        control["navigate"]("agents", KEY, section="changes")
        await app.wait_for("changes-files")
        await click(app, "changes-file-0")
        await app.wait_for("changes-viewed")
        assert app.find("changes-viewed").value is True

        control["set_account"]("account-b")  # another account starts with no marks
        await app.settle()
        control["navigate"]("agents")
        await app.wait_for("console")
        control["navigate"]("agents", KEY, section="changes")
        await app.wait_for("changes-files")
        await click(app, "changes-file-0")
        await app.wait_for("changes-viewed")
        assert app.find("changes-viewed").value is False

    run(scenario())


def test_new_content_clears_viewed_after_a_refresh() -> None:
    gateway = Gateway(small_files())

    async def scenario() -> None:
        app, _ = await mount(gateway)
        await app.wait_for("changes-files")
        await click(app, "changes-file-0")
        await app.wait_for("changes-viewed")
        await tick(app, "changes-viewed", True)
        gateway.edit()
        await app.wait_for("changes-updated")
        await click(app, "changes-updated")
        await until(lambda: ("diff", "rev-2:0", None, False) in gateway.calls)
        await app.wait_for("diff-rows")
        assert app.find("changes-viewed").value is False

    run(scenario())


def test_positions_follow_the_reader_across_files_rotation_and_earlier_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    moves: list[float] = []

    async def record(_listing: Any, offset: float) -> None:
        moves.append(offset)

    monkeypatch.setattr(diff_view, "scroll_list", record)
    gateway = Gateway(
        [
            FileSpec("long.py", added_patch("long.py", 900), status="A"),
            FileSpec("other.py", two_hunk_patch("other.py")),
        ]
    )

    async def scenario() -> None:
        app, control = await mount(gateway)
        await app.wait_for("changes-files")
        await click(app, "changes-file-0")
        await app.wait_for("diff-rows")
        await click(app, "diff-more")  # page 1 only is resident now
        await until(lambda: ("diff", "rev-1:0", "pg:1", False) in gateway.calls)
        await app.wait_for("diff-earlier")

        read = 100 * ROW_HEIGHT
        app.find("diff-rows").on_scroll(SimpleNamespace(pixels=float(read)))
        await click(app, "changes-next")
        await app.wait_for("diff-rows")
        await click(app, "changes-prev")
        await app.wait_for("diff-earlier")  # back at the page the reader had reached
        assert gateway.calls.count(("diff", "rev-1:0", "pg:1", False)) == 2
        assert gateway.calls.count(("diff", "rev-1:0", None, False)) == 1  # page 0 is not refetched
        await until(lambda: float(read) in moves)  # and at the row they were reading

        moves.clear()
        control["set_wide"](True)  # rotation: another layout, another view instance
        await until(lambda: float(read) in moves)

        await click(app, "diff-earlier")
        await until(lambda: -1.0 in moves)  # the earlier page opens at its end

    run(scenario())


def test_commits_tab_picker_search_generated_group_and_changing_the_base() -> None:
    files = [
        FileSpec("pkg/a.py", two_hunk_patch("pkg/a.py")),
        FileSpec("pkg/b.py", two_hunk_patch("pkg/b.py")),
        FileSpec("uv.lock", two_hunk_patch("uv.lock"), generated=True),
    ]
    gateway = Gateway(files)

    async def scenario() -> None:
        app, _ = await mount(gateway)
        await app.wait_for("changes-files")
        assert len(built_rows(app)) == 3  # a, b and the group row
        assert scope_is(app, "working") and not present(app, "changes-graph")
        await click(app, "changes-generated")
        assert len(built_rows(app)) == 4

        await app.set_props_and_fire("changes-search", {"value": "b.py"}, "change", "b.py")
        assert len(built_rows(app)) == 1

        await click(app, "changes-scope-branch")
        await app.wait_for("changes-view-graph")
        await click(app, "changes-view-graph")
        await app.wait_for("changes-graph")
        assert app.find("changes-graph").data["rows"] == 2
        await click(app, "changes-more-graph")
        await until(lambda: app.find("changes-graph").data["rows"] == 4)
        await click(app, "changes-scope-branch")
        await app.wait_for("changes-files")

        await click(app, "changes-change-base")
        await app.wait_for("changes-base-1")
        await click(app, "changes-base-1")
        await until(lambda: ("set_base", "id:origin/develop") in gateway.calls)
        await app.wait_for("changes-files")
        assert scope_is(app, "branch")

        await click(app, "changes-file-1")  # the search above still shows only b.py
        await app.wait_for("changes-picker-open")
        await click(app, "changes-picker-open")
        await app.wait_for("changes-picker-search")
        await app.set_props_and_fire("changes-picker-search", {"value": "a.py"}, "change", "a.py")
        picked = built_rows(app, "changes-picker-list")
        assert len(picked) == 1
        await click(app, picked[0].key)
        await until(lambda: ("diff", "rev-1+origin/develop:0", None, False) in gateway.calls)

    run(scenario())


def test_a_busy_first_list_is_asked_again_when_its_window_ends() -> None:
    gateway = Gateway(small_files())
    gateway.failures["changes"] = TransportError(
        "unavailable", "busy", True, {"reason": "busy", "retry_after_s": 0.4}
    )

    async def scenario() -> None:
        app, _ = await mount(gateway)
        await until(lambda: present(app, "changes-state") and "busy" in message_text(app))
        await asyncio.sleep(0.15)
        assert gateway.count("changes") == 1  # nothing inside the window
        del gateway.failures["changes"]
        await app.wait_for("changes-files", timeout=3.0)
        assert gateway.count("changes") == 2

    run(scenario())


def test_the_header_shows_when_the_agent_was_last_checked_not_only_when_the_list_was_taken() -> (
    None
):
    gateway = Gateway(small_files())

    async def scenario() -> None:
        taken = await render(gateway)
        await taken.wait_for("changes-files")
        assert re.search(r"as of 1[2-4] s ago$", taken.find("changes-asof").value)
        now = int(time.time() * 1000)
        checked = await render(gateway, checked=lambda: now - 2_000)
        await checked.wait_for("changes-files")
        assert checked.find("changes-asof").value.endswith("as of just now")
        older = await render(gateway, checked=lambda: gateway.clock_ms - 60_000)
        await older.wait_for("changes-files")
        assert re.search(r"as of 1[2-4] s ago$", older.find("changes-asof").value)

    run(scenario())
