"""An open commit file takes the commit pane, like the Working diff column, at every size, and
Back finds the file list, and a file opened again its diff, where the reader left them."""

from __future__ import annotations

from typing import Any

import flet as ft
import pytest
from flet.components.component import Component

import relaydeck.client.screens.changes.parts.diff_view as diff_view
from relaydeck.client.design_system import tokens as t
from relaydeck.client.design_system.lists import virtual_list
from relaydeck.client.screens.changes.parts.commit_view import HEADER_ROOM, SUBJECT_WIDE
from relaydeck.client.screens.changes.parts.diff_view import ROW_HEIGHT
from relaydeck.client.screens.changes.parts.file_list import FILE_ROW
from tests.support.flet_layout import _children, expand_violations
from tests.support.git_patches import added_patch, two_hunk_patch
from tests.support.host_harness import run
from tests.support.ui.virtual import leading_space
from tests.ui.git.gateway import FileSpec, Gateway
from tests.ui.git.harness import click, files, graph, present, row_key, scroll_to, until

SIZES = [
    pytest.param(1900, 920, True, id="web-desktop"),
    pytest.param(1376, 1032, True, id="tablet-landscape"),
    pytest.param(1032, 1376, False, id="tablet-portrait"),
    pytest.param(390, 844, False, id="phone"),
]
MIN_ROWS = 8  # diff rows the open file must have room for, whatever the commit says above it
BOTH = [SIZES[0], SIZES[3]]  # beside the graph, and as a screen of its own
READ_ROW = 99  # a line of a long file, well below its first screen
LISTED = 40  # a file well outside the list's first window


def asks(app: Any) -> list[Any]:
    """Every Ask button on the page (the sheets key theirs `changes-ask-...`)."""
    found: list[Any] = []

    def walk(control: Any) -> None:
        if str(getattr(control, "key", "")).startswith("changes-ask-"):
            found.append(control)
        body = getattr(control, "_b", None) if isinstance(control, Component) else None
        for kid in [body] if body is not None else [c for _, c in _children(control)]:
            walk(kid)

    walk(app.page)
    return found


def ancestors(app: Any, key: str) -> list[Any]:
    """The controls from the page down to the one with `key`, components stepped through."""
    path: list[Any] = []

    def walk(control: Any, trail: list[Any]) -> bool:
        if getattr(control, "key", None) == key:
            path.extend([*trail, control])
            return True
        body = getattr(control, "_b", None) if isinstance(control, Component) else None
        kids = [body] if body is not None else [c for _, c in _children(control)]
        return any(walk(kid, [*trail, control]) for kid in kids)

    assert walk(app.page, [])
    return path


def spent_outside_the_rows(app: Any) -> float:
    """Height the pane gives to controls that are not the diff rows: the explicit heights of the
    rows' siblings up the pane, with their padding. Text without a set height counts nothing, so
    the number is a floor; the pane's fixed room is proved by its structure, not by this sum."""
    chain = ancestors(app, "diff-scroll")
    pane = next(i for i, c in enumerate(chain) if getattr(c, "key", None) == "commit-pane")
    total = 0.0
    for parent, child in zip(chain[pane:], chain[pane + 1 :], strict=False):
        for sibling in (c for _, c in _children(parent)):
            if sibling is child or isinstance(sibling, Component):
                continue
            total += float(getattr(sibling, "height", None) or 0)
            padding = getattr(sibling, "padding", None)
            if padding is not None:
                total += float(getattr(padding, "top", 0) or 0) + float(
                    getattr(padding, "bottom", 0) or 0
                )
            inner = getattr(sibling, "content", None)
            if inner is not None and getattr(sibling, "height", None) is None:
                heights = [float(getattr(c, "height", None) or 0) for _, c in _children(inner)] + [
                    float(getattr(inner, "height", None) or 0)
                ]
                total += max(heights)
    return total


async def open_first_file(app: Any, gateway: Gateway, wide: bool) -> None:
    await click(app, row_key(gateway, 0))
    if wide:
        await app.wait_for("diff-rows")  # beside the graph the first file is opened for the reader
    else:
        await app.wait_for("commit-file-0")
        await click(app, "commit-file-0")
        await app.wait_for("diff-rows")


@pytest.mark.parametrize(("width", "height", "wide"), SIZES)
def test_the_open_file_takes_the_pane_with_the_working_diffs_header_and_sheets(
    width: int, height: int, wide: bool
) -> None:
    gateway = Gateway(files())
    copied: list[str] = []
    sent: list[tuple[Any, ...]] = []

    def ask(*args: Any) -> bool:
        sent.append(args)
        return True

    async def scenario() -> None:
        app = await graph(gateway, width=width, height=height, wide=wide, copied=copied, on_ask=ask)
        await open_first_file(app, gateway, wide)
        # Nothing competes with the diff: no details block, summary or file list is in the pane.
        for key in ("commit-details", "commit-summary", "commit-files", "commit-parents"):
            assert not present(app, key), key
        assert present(app, "diff-header") and present(app, "diff-copy")
        assert present(app, "diff-ask")  # the whole file can be asked about at every width
        assert present(app, "commit-ask") == wide  # the whole-commit Ask stays one tap away
        for key in ("changes-prev", "changes-next", "changes-picker-open", "changes-files-back"):
            assert present(app, key), key
        # The room is structural: every control from the pane down to the rows expands, and the
        # rows' siblings are the one-line commit header, the diff's own header and the pager.
        chain = ancestors(app, "diff-scroll")
        pane = next(i for i, c in enumerate(chain) if getattr(c, "key", None) == "commit-pane")
        assert chain[pane].expand
        for parent, child in zip(chain[pane:], chain[pane + 1 :], strict=False):
            assert getattr(child, "height", None) is None, type(child).__name__
            if isinstance(parent, ft.Row | ft.Column) and not isinstance(child, Component):
                assert child.expand, f"{type(child).__name__} in {type(parent).__name__}"
        if wide:
            assert app.find("commit-file-header").height == HEADER_ROOM
        assert spent_outside_the_rows(app) + 2 * t.MIN_TOUCH <= height - MIN_ROWS * ROW_HEIGHT
        assert expand_violations(app.page) == []
        # The Copy sheet offers Copy diff, Copy code, Ask and Lines per hunk; Lines asks per line.
        await click(app, "diff-copy")
        await app.wait_for("changes-copy-hunk-0")
        for action in ("diff", "code", "lines"):
            assert present(app, f"changes-copy-hunk-0-{action}"), action
        hunk_asks = asks(app)
        assert hunk_asks and not any(c.disabled for c in hunk_asks)
        await click(app, "changes-copy-hunk-0-lines")
        await app.wait_for("changes-copy-list")
        line_asks = asks(app)
        assert line_asks and not any(c.disabled for c in line_asks)
        await click(app, line_asks[0].key)
        await click(app, "changes-ask-send")
        assert sent[-1][0].kind == "line" and sent[-1][0].commit == f"{0:040x}"
        # Back restores the commit's details and files, with the open file still marked.
        await click(app, "changes-files-back")
        await app.wait_for("commit-file-1")
        assert not present(app, "diff-rows")
        for key in ("commit-details", "commit-summary", "commit-ask", "commit-copy"):
            assert present(app, key), key
        assert app.find("commit-files").expand
        if wide:  # the open file stays marked; a narrow screen's Back closes it, as before
            assert app.find("commit-file-0").bgcolor == t.CARD_SELECTED
            assert present(app, "changes-graph") and app.find(row_key(gateway, 0)).data["selected"]
            assert app.find("commit-subject").size == SUBJECT_WIDE
        await click(app, "commit-file-1")
        await app.wait_for("diff-rows")
        assert app.find("changes-next").disabled and not app.find("changes-prev").disabled
        assert not present(app, "commit-files")

    run(scenario())


def test_the_commit_header_asks_about_the_whole_commit_and_back_reads_nothing_again() -> None:
    gateway = Gateway(files())
    sent: list[tuple[Any, ...]] = []

    def ask(*args: Any) -> bool:
        sent.append(args)
        return True

    async def scenario() -> None:
        app = await graph(gateway, width=1900, height=920, wide=True, on_ask=ask)
        await open_first_file(app, gateway, True)
        assert str(app.find("commit-subject").value) == "Commit number 0"
        assert app.find("commit-subject").max_lines == 1
        await click(app, "commit-ask")
        await click(app, "changes-ask-send")
        assert sent[-1][0].kind == "commit" and sent[-1][0].commit == f"{0:040x}"
        reads = gateway.count("commit_changes"), gateway.count("diff")
        await click(app, "changes-files-back")
        await app.wait_for("commit-file-0")
        await click(app, "commit-file-0")
        await app.wait_for("diff-rows")
        assert (gateway.count("commit_changes"), gateway.count("diff")) == reads
        # Another commit opens at its first file again, whatever the reader did in this one.
        await click(app, "changes-files-back")
        await app.wait_for("commit-file-0")
        await click(app, row_key(gateway, 1))
        await app.wait_for("diff-rows")
        assert str(app.find("commit-subject").value) == "Commit number 1"

    run(scenario())


def test_a_long_file_list_fills_the_pane_and_its_footer_keeps_its_height() -> None:
    gateway = Gateway(files(), commit_page=1)

    async def scenario() -> None:
        app = await graph(gateway, width=1376, height=1000, wide=True)
        await open_first_file(app, gateway, True)
        await click(app, "changes-files-back")
        await app.wait_for("commit-more-files")
        assert app.find("commit-files").expand and app.find("commit-more-files").height == 48
        assert not present(app, "commit-files-box")
        assert isinstance(app.find("commit-pane"), ft.Column) and app.find("commit-pane").expand

    run(scenario())


def scroll_recorder(monkeypatch: pytest.MonkeyPatch, module: Any) -> list[float]:
    """The offsets `module`'s lists ask the client to show; the client takes each request."""
    moves: list[float] = []

    async def record(_listing: Any, offset: float) -> bool:
        moves.append(offset)
        return True

    monkeypatch.setattr(module, "scroll_list", record)
    return moves


async def back_to_the_list(app: Any) -> None:
    await click(app, "changes-files-back")
    await app.wait_for("commit-files")


async def open_from_the_list(app: Any, key: str) -> None:
    await click(app, key)
    await app.wait_for("diff-rows")


@pytest.mark.parametrize(("width", "height", "wide"), BOTH)
def test_back_and_the_same_file_again_opens_at_the_rows_the_reader_was_reading(
    width: int, height: int, wide: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    moves = scroll_recorder(monkeypatch, diff_view)
    gateway = Gateway([FileSpec("pkg/long.py", added_patch("pkg/long.py", 400), status="A")])
    gateway.commit_files[0] = gateway.files
    # Row 0 is the hunk's header: a tap target beside the graph, a code line's height elsewhere.
    read = float((t.MIN_TOUCH if wide else ROW_HEIGHT) + (READ_ROW - 1) * ROW_HEIGHT)

    async def scenario() -> None:
        app = await graph(gateway, width=width, height=height, wide=wide)
        await open_first_file(app, gateway, wide)
        scroll_to(app, "diff-rows", read)  # the runner notes the row; nothing is redrawn
        await app.settle()
        moves.clear()
        reads = gateway.count("diff")
        await back_to_the_list(app)
        await open_from_the_list(app, "commit-file-0")
        await until(lambda: read in moves)  # the list is asked to show the row again
        assert present(app, f"diff-row-{READ_ROW}")  # and the rows there are the ones built
        if wide:  # beside the graph the window stays resident: nothing is read again
            assert gateway.count("diff") == reads

    run(scenario())


@pytest.mark.parametrize(("width", "height", "wide"), BOTH)
def test_back_returns_to_the_file_list_where_the_reader_left_it(
    width: int, height: int, wide: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    moves = scroll_recorder(monkeypatch, virtual_list)

    def regular(numbers: range) -> list[FileSpec]:
        return [FileSpec(f"pkg/{n:03}.py", two_hunk_patch(f"pkg/{n:03}.py")) for n in numbers]

    lockfiles = [FileSpec(n, two_hunk_patch(n), generated=True) for n in ("uv.lock", "a.lock")]
    # Three pages: the generated group sits at row 50 until the second page's files push it down.
    specs = regular(range(50)) + lockfiles + regular(range(50, 100)) + regular(range(100, 150))
    gateway = Gateway(files(), commit_page=52, commit_files={0: specs})
    at = float(LISTED * FILE_ROW)

    async def show_more_files_from_the_pager(app: Any) -> None:
        pages = gateway.count("commit_changes")
        await click(app, "changes-picker-open")
        await app.wait_for("changes-picker-more")
        await click(app, "changes-picker-more")
        await until(lambda: gateway.count("commit_changes") > pages)
        await click(app, "changes-picker-close")
        await app.settle()

    async def scenario() -> None:
        app = await graph(gateway, width=width, height=height, wide=wide)
        await open_first_file(app, gateway, wide)
        await back_to_the_list(app)
        assert leading_space(app, "commit-files") == 0
        scroll_to(app, "commit-files", at)
        await until(lambda: present(app, f"commit-file-{LISTED}"))
        moves.clear()
        # Back finds the list where it was: the row the reader opened is built and marked, and
        # the list is asked to show it, with the rows above it left unbuilt.
        await open_from_the_list(app, f"commit-file-{LISTED}")
        await back_to_the_list(app)
        await until(lambda: at in moves)
        assert present(app, f"commit-file-{LISTED}") and not present(app, "commit-file-0")
        assert leading_space(app, "commit-files") > 0
        if wide:
            assert app.find(f"commit-file-{LISTED}").bgcolor == t.CARD_SELECTED
        # Opening the generated group changes only the rows after it: the place is kept.
        await click(app, "changes-generated")
        moves.clear()
        await open_from_the_list(app, f"commit-file-{LISTED + 1}")
        await back_to_the_list(app)
        await until(lambda: at in moves)
        assert present(app, f"commit-file-{LISTED}") and present(app, "commit-file-50")
        # More files arriving under the list while a file is open leave the regular rows the
        # reader was among where they were: the place is kept.
        moves.clear()
        await open_from_the_list(app, f"commit-file-{LISTED + 1}")
        await show_more_files_from_the_pager(app)
        await back_to_the_list(app)
        await until(lambda: at in moves)
        assert present(app, f"commit-file-{LISTED}") and not present(app, "commit-file-0")
        # A reader at the generated group, which more regular files push down, starts over at
        # the top rather than at rows that are no longer theirs.
        group = 2 * gateway.commit_page - len(lockfiles)  # after two pages of files
        scroll_to(app, "commit-files", float(group * FILE_ROW))
        await until(lambda: present(app, "commit-file-50"))  # the first lockfile, just below
        moves.clear()
        await open_from_the_list(app, "commit-file-50")
        await show_more_files_from_the_pager(app)
        await back_to_the_list(app)
        await app.settle()
        assert present(app, "commit-file-0") and not present(app, "commit-file-50")
        assert leading_space(app, "commit-files") == 0 and not moves
        # Another commit's list starts at the top too.
        scroll_to(app, "commit-files", at)
        await until(lambda: present(app, f"commit-file-{LISTED}"))
        await open_from_the_list(app, f"commit-file-{LISTED}")
        if wide:
            await click(app, row_key(gateway, 1))
            await app.wait_for("diff-rows")
            await until(lambda: str(app.find("commit-subject").value) == "Commit number 1")
            await back_to_the_list(app)
        else:
            await back_to_the_list(app)
            await click(app, "commit-back")
            await app.wait_for(row_key(gateway, 1))
            await click(app, row_key(gateway, 1))
            await app.wait_for("commit-files")
        assert present(app, "commit-file-0") and leading_space(app, "commit-files") == 0

    run(scenario())
