"""ChangesScreen over the synthetic gateway: list, file pager, scope switch, base choice, copy."""

from __future__ import annotations

import flet as ft
import pytest

import relaydeck.client.screens.changes.screen as screen_module
from relaydeck.client.design_system.theme import MIN_TOUCH
from relaydeck.client.ports.transport import TransportError
from relaydeck.client.screens.changes.parts.diff_view import ROW_HEIGHT
from relaydeck.git.parser import DiffParser
from tests.support.git_patches import two_hunk_patch
from tests.support.host_harness import HostApp, button_label, run
from tests.support.ui.focus import interactive_controls
from tests.ui.git.gateway import FileSpec, Gateway
from tests.ui.git.harness import built_rows, click, files, render


def test_copying_a_line_from_a_later_byte_limited_fragment_uses_its_identity() -> None:
    lines = [f"synthetic row {i:02d} " + "x" * 12_000 for i in range(30)]
    patch = (
        "\n".join(
            [
                "diff --git a/long.txt b/long.txt",
                "new file mode 100644",
                "--- /dev/null",
                "+++ b/long.txt",
                "@@ -0,0 +1,30 @@",
                *("+" + line for line in lines),
            ]
        )
        + "\n"
    ).encode()
    pages = DiffParser().parse(patch, file_id="file", snapshot="snapshot")
    assert len(pages) == 2
    expected = pages[1].hunks[0].lines[2].text
    gateway = Gateway([FileSpec("long.txt", patch, status="A")])
    copied: list[str] = []

    async def scenario() -> None:
        app = await render(gateway, copied=copied)
        await app.wait_for("changes-files")
        await click(app, "changes-file-0")
        await app.wait_for("diff-more")
        await click(app, "diff-more")
        await app.wait_for("diff-copy")
        await click(app, "diff-copy")
        await click(app, "changes-copy-hunk-0-lines")
        await app.wait_for("changes-copy-line-1-0-2")
        assert app.find("changes-copy-line-0-0-2") is not app.find("changes-copy-line-1-0-2")
        await click(app, "changes-copy-line-1-0-2")
        assert copied == [expected] and expected != lines[2]

    run(scenario())


def test_retry_after_the_first_commits_page_fails_requests_and_displays_it() -> None:
    gateway = Gateway(files())
    gateway.failures["graph"] = TransportError("unavailable", "synthetic failure", True, None)

    async def scenario() -> None:
        app = await render(gateway)
        await app.wait_for("changes-files")
        await click(app, "changes-scope-branch")
        await app.wait_for("changes-view-graph")
        await click(app, "changes-view-graph")
        await app.wait_for("graph-retry")
        failed = gateway.count("graph")
        del gateway.failures["graph"]
        await click(app, "graph-retry")
        await app.wait_for("changes-graph")
        assert gateway.count("graph") == failed + 1
        await click(app, "changes-view-graph")
        assert gateway.count("graph") == failed + 1

    run(scenario())


def audit_touch_targets(app: HostApp) -> None:
    for key, control in interactive_controls(app.page):
        for dimension in ("height", "width"):
            declared = getattr(control, dimension, None)
            if isinstance(declared, int | float):
                assert declared >= MIN_TOUCH, f"{key} {dimension}={declared}"
        if isinstance(control, ft.Button | ft.TextButton):
            assert (control.height or 0) >= MIN_TOUCH, f"{key} button height {control.height!r}"


def test_lists_files_then_pages_through_diffs() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app = await render(gateway)
        await app.wait_for("changes-files")
        assert len(built_rows(app)) == 2
        await click(app, "changes-file-0")
        await app.wait_for("diff-rows")
        assert ("diff", "rev-1:0", None, False) in gateway.calls
        await click(app, "changes-next")
        await app.settle()
        assert ("diff", "rev-1:1", None, False) in gateway.calls
        await click(app, "changes-files-back")
        await app.wait_for("changes-files")

    run(scenario())


def test_the_graph_builds_no_ordinary_detail_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    real = screen_module.display_rows

    def counting(pages: object, first: int = 0) -> object:
        calls.append(first)
        return real(pages, first)  # type: ignore[arg-type]

    monkeypatch.setattr(screen_module, "display_rows", counting)
    gateway = Gateway(files())

    async def scenario() -> None:
        app = await render(gateway)
        await app.wait_for("changes-files")
        await click(app, "changes-file-0")
        await app.wait_for("diff-rows")  # phone detail does flatten its rows
        assert calls
        calls.clear()
        await click(app, "changes-files-back")
        await app.wait_for("changes-files")
        calls.clear()
        await click(app, "changes-scope-branch")
        await app.wait_for("changes-view-graph")
        await click(app, "changes-view-graph")
        await app.wait_for("changes-graph")
        await app.settle()
        assert calls == []

    run(scenario())


def test_branch_scope_without_a_base_offers_the_picker() -> None:
    gateway = Gateway(files(), base=None)

    async def scenario() -> None:
        app = await render(gateway)
        await app.wait_for("changes-files")
        assert not _present(app, "changes-scope-branch")
        await click(app, "changes-choose-base-tab")
        await app.wait_for("changes-base-0")
        await click(app, "changes-base-0")
        await app.wait_for("changes-files")
        assert ("set_base", "id:origin/main") in gateway.calls

    run(scenario())


def test_wide_layout_shows_list_and_diff_side_by_side() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app = await render(gateway, wide=True)
        await app.wait_for("changes-files")
        assert app.find("changes-search") is not None
        await click(app, "changes-file-1")
        await app.wait_for("diff-rows")
        assert app.find("changes-files") is not None

    run(scenario())


def test_code_rows_are_not_tappable() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app = await render(gateway)
        await app.wait_for("changes-files")
        await click(app, "changes-file-0")
        await app.wait_for("diff-rows")
        built = built_rows(app, "diff-rows")
        assert built and all(row.height == ROW_HEIGHT for row in built)
        assert all(row.on_click is None for row in built)

    run(scenario())


def test_a_hunk_copies_as_a_diff_or_as_code_and_a_part_of_a_hunk_says_so() -> None:
    gateway = Gateway(files())
    copied: list[str] = []

    async def scenario() -> None:
        app = await render(gateway, copied=copied)
        await app.wait_for("changes-files")
        await click(app, "changes-file-0")
        await app.wait_for("diff-copy")
        await click(app, "diff-copy")
        await app.wait_for("changes-copy-sheet")
        assert button_label(app.find("changes-copy-hunk-0-diff")) == "Copy diff"
        await click(app, "changes-copy-hunk-0-diff")
        assert copied == ["@@ -1,3 +1,3 @@\n first\n-second old\n+second new\n third"]
        assert not _present(app, "changes-copy-sheet")

        await click(app, "diff-copy")
        await app.wait_for("changes-copy-sheet")
        assert button_label(app.find("changes-copy-hunk-0-code")) == "Copy code"
        await click(app, "changes-copy-hunk-0-code")
        assert copied[-1] == "first\nsecond new\nthird"

        await click(app, "diff-copy")
        await app.wait_for("changes-copy-sheet")
        await click(app, "changes-copy-hunk-1-lines")
        await app.wait_for("changes-copy-line-0-1-1")
        await click(app, "changes-copy-line-0-1-1")
        assert copied[-1] == "twenty-one old"

        await click(app, "changes-files-back")
        await click(app, "changes-file-1")
        await app.wait_for("diff-copy")
        await click(app, "diff-copy")
        await app.wait_for("changes-copy-sheet")
        # 500 of the hunk's 600 lines are held: the row says so and copies only those
        assert "Part of a hunk" in app.find("changes-copy-hunk-0").content.controls[1].value
        await click(app, "changes-copy-hunk-0-code")
        held = copied[-1].split("\n")
        assert len(held) == 500 and held[0] == "line 00000" and held[-1] == "line 00499"

        await click(app, "diff-more")  # page 1 replaces page 0: a hunk with no start
        await app.wait_for("diff-earlier")
        await click(app, "diff-copy")
        await app.wait_for("changes-copy-sheet")
        await click(app, "changes-copy-hunk-0-diff")
        rest = copied[-1].split("\n")
        assert len(rest) == 101 and rest[1] == "+line 00500"

    run(scenario())


def _present(app: HostApp, key: str) -> bool:
    try:
        app.find(key)
    except KeyError:
        return False
    return True


def test_every_control_meets_the_touch_target_size() -> None:
    gateway = Gateway([*files(), FileSpec("uv.lock", two_hunk_patch("uv.lock"), generated=True)])

    async def scenario() -> None:
        phone = await render(gateway, copied=[])
        await phone.wait_for("changes-files")
        audit_touch_targets(phone)
        await click(phone, "changes-scope-branch")
        await phone.wait_for("changes-view-graph")
        audit_touch_targets(phone)
        await click(phone, "changes-view-graph")
        await phone.wait_for("changes-graph")
        audit_touch_targets(phone)
        await click(phone, "changes-scope-branch")
        await phone.wait_for("changes-files")
        await click(phone, "changes-generated")
        await click(phone, "changes-file-0")
        await phone.wait_for("diff-rows")
        audit_touch_targets(phone)
        await click(phone, "diff-copy")
        await phone.wait_for("changes-copy-hunk-0-lines")
        audit_touch_targets(phone)
        await click(phone, "changes-copy-hunk-0-lines")
        await phone.wait_for("changes-copy-line-0-0-0")
        audit_touch_targets(phone)
        await click(phone, "changes-copy-close")
        await click(phone, "changes-picker-open")
        await phone.wait_for("changes-picker")
        audit_touch_targets(phone)
        await click(phone, "changes-picker-close")
        await click(phone, "changes-files-back")
        await phone.wait_for("changes-files")
        await click(phone, "changes-change-base")
        await phone.wait_for("changes-base-0")
        audit_touch_targets(phone)
        wide = await render(gateway, wide=True, copied=[])
        await wide.wait_for("changes-files")
        audit_touch_targets(wide)

    run(scenario())


def test_a_copy_click_that_arrives_after_the_file_changed_copies_nothing() -> None:
    gateway = Gateway(files())
    copied: list[str] = []

    async def scenario() -> None:
        app = await render(gateway, copied=copied)
        await app.wait_for("changes-files")
        await click(app, "changes-file-0")
        await app.wait_for("diff-copy")
        await click(app, "diff-copy")
        await app.wait_for("changes-copy-sheet")
        late_hunk = app.find("changes-copy-hunk-0-diff").on_click
        await click(app, "changes-copy-hunk-0-lines")
        await app.wait_for("changes-copy-line-0-0-1")
        late_line = app.find("changes-copy-line-0-0-1").on_click

        await click(app, "changes-files-back")
        await click(app, "changes-file-1")  # another file, drawn on the same coordinates
        await app.wait_for("diff-rows")
        assert not _present(app, "changes-copy-sheet")  # the sheet does not outlive its file

        late_hunk(None)
        late_line(None)
        assert copied == []

    run(scenario())


def test_the_copy_sheet_closes_when_the_snapshot_changes() -> None:
    gateway = Gateway(files())
    copied: list[str] = []

    async def scenario() -> None:
        app = await render(gateway, wide=True, copied=copied)
        await app.wait_for("changes-files")
        await click(app, "changes-file-0")
        await app.wait_for("diff-copy")
        await click(app, "diff-copy")
        await app.wait_for("changes-copy-sheet")
        late = app.find("changes-copy-hunk-0-diff").on_click
        gateway.edit()  # a refresh brings a new snapshot and new file ids
        await click(app, "changes-scope-branch")
        await app.wait_for("changes-files")
        assert not _present(app, "changes-copy-sheet")
        late(None)
        assert copied == []

    run(scenario())
