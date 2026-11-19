"""Opening a commit from the graph, at each artboard size and on the wide split."""

from __future__ import annotations

from typing import Any

import pytest

from relaydeck.client.features.ask_agent.model import build_prompt
from relaydeck.client.features.changes.review_state import ReviewStateStore
from relaydeck.client.ports.transport import TransportError
from tests.support.flet_layout import expand_violations
from tests.support.git_patches import added_patch
from tests.support.host_harness import run
from tests.ui.git.gateway import EMPTY_TREE, FileSpec, Gateway
from tests.ui.git.harness import click, files, graph, mount, present, press, render, row_key, until

SIZES = [
    pytest.param(390, 844, False, id="phone-portrait"),
    pytest.param(844, 390, False, id="phone-landscape"),
    pytest.param(1032, 1376, False, id="tablet-portrait"),
    pytest.param(1376, 1000, True, id="tablet-landscape"),
    pytest.param(1024, 768, True, id="tablet-landscape-small"),
]


def text(app: Any, key: str) -> str:
    return str(app.find(key).value)


@pytest.mark.parametrize(("width", "height", "wide"), SIZES)
def test_a_commit_opens_with_its_details_files_and_diff(
    width: int, height: int, wide: bool
) -> None:
    gateway = Gateway(files())
    copied: list[str] = []

    async def scenario() -> None:
        app = await graph(gateway, width=width, height=height, wide=wide, copied=copied)
        if wide:
            await app.wait_for("commit-none")
        await click(app, row_key(gateway, 0))
        if wide:
            # The first file is opened for the reader, under a one-line commit header.
            await app.wait_for("diff-rows")
            assert text(app, "commit-subject") == "Commit number 0"
            assert present(app, "diff-ask") and present(app, "commit-ask")
            assert expand_violations(app.page) == []
            await click(app, "changes-files-back")
        await app.wait_for("commit-subject")
        assert text(app, "commit-subject") == "Commit number 0"
        assert text(app, "commit-parents") == f"parent {1:040x}"[:15]
        assert any(c[0] == "commit_changes" and c[1] == "cid:all:r:0" for c in gateway.calls)
        for key in ("commit-ask", "commit-copy"):
            assert app.find(key).height >= 48
        await app.wait_for("commit-file-1")
        assert "2 files changed" in text(app, "commit-summary")
        if wide:
            assert present(app, "changes-graph") and app.find(row_key(gateway, 0)).data["selected"]
        else:
            assert not present(app, "changes-graph") and not present(app, "changes-tabs")
            await click(app, "commit-file-0")
            await app.wait_for("diff-rows")
            assert present(app, "diff-ask")  # a narrow screen can ask about the whole file
            await click(app, "changes-files-back")
            await app.wait_for("commit-file-0")
        assert not present(app, "changes-viewed") and not present(app, "diff-ask")
        assert expand_violations(app.page) == []
        await click(app, "commit-copy")
        assert copied == [f"{0:040x}"]
        if not wide:
            await click(app, "commit-back")
            await app.wait_for("changes-graph")
            assert app.find(row_key(gateway, 0)).data["selected"]
            assert not present(app, "commit-pane")

    run(scenario())


def test_the_split_row_is_a_commit_with_no_parent_and_the_empty_tree() -> None:
    gateway = Gateway(files(), commit_total=2, graph_page_size=10)

    async def scenario() -> None:
        app = await graph(gateway)
        await click(app, row_key(gateway, 2))
        await app.wait_for("commit-parents")
        assert text(app, "commit-parents") == "root commit"
        await app.wait_for("commit-file-0")
        assert any(c[0] == "commit_changes" and c[1].endswith(":2") for c in gateway.calls)
        assert EMPTY_TREE in str(await gateway.git_commit_changes("h1", "wt-stable", "cid:all:r:2"))

    run(scenario())


def test_reading_a_commit_never_touches_viewed_marks_and_v_does_nothing() -> None:
    gateway = Gateway(files())
    store = ReviewStateStore()

    async def scenario() -> None:
        app = await graph(gateway, width=1376, height=1000, wide=True, reviews=store)
        await click(app, row_key(gateway, 0))
        await app.wait_for("diff-rows")
        await until(lambda: any(c[0] == "diff" for c in gateway.calls))
        first = [c for c in gateway.calls if c[0] == "diff"][-1][1]
        await press(app, "j")
        await until(lambda: [c for c in gateway.calls if c[0] == "diff"][-1][1] != first)
        count = gateway.count("diff")
        await press(app, "k")
        await until(lambda: gateway.count("diff") > count)
        await press(app, "v")
        await app.settle()
        assert len(store) == 0 and not present(app, "changes-viewed")

    run(scenario())


def test_another_row_replaces_the_pane_and_a_chip_change_closes_it() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app = await graph(gateway, width=1376, height=1000, wide=True)
        await click(app, row_key(gateway, 0))
        await until(
            lambda: present(app, "commit-subject") and text(app, "commit-subject").endswith("0")
        )
        await click(app, row_key(gateway, 1))
        await until(lambda: text(app, "commit-subject").endswith("1"))
        await app.wait_for("diff-rows")
        assert app.find(row_key(gateway, 1)).data["selected"]
        assert not app.find(row_key(gateway, 0)).data["selected"]
        await click(app, "graph-mode-branch")
        await app.wait_for("commit-none")
        assert not present(app, "commit-subject")

    run(scenario())


def test_ask_about_a_commit_its_file_and_its_hunk_each_send_one_prompt() -> None:
    gateway = Gateway(files())
    sent: list[tuple[Any, ...]] = []

    def ask(*args: Any) -> bool:
        sent.append(args)
        return True

    async def scenario() -> None:
        app = await graph(gateway, width=1376, height=1000, wide=True, on_ask=ask)
        await click(app, row_key(gateway, 0))
        await app.wait_for("diff-rows")
        assert not present(app, "changes-ask")  # the pane carries the question
        assert not app.find("commit-ask").disabled and not app.find("diff-ask").disabled
        await click(app, "commit-ask")
        await click(app, "changes-ask-send")
        assert len(sent) == 1
        scope, question, bookmark, _ = sent[0]
        assert scope.kind == "commit" and scope.commit == f"{0:040x}"
        assert scope.base == f"{1:040x}" and scope.head == scope.commit
        assert build_prompt(scope, question).startswith("About this commit")
        assert bookmark.view == "graph" and bookmark.commit is not None
        await click(app, "diff-ask")  # no hunk chosen: the file
        await click(app, "changes-ask-send")
        scope, question, bookmark, _ = sent[1]
        assert scope.kind == "file" and scope.path == "src/c0/a.py"
        assert scope.commit == f"{0:040x}" and scope.start is None
        assert build_prompt(scope, question).startswith("About src/c0/a.py (Commit: 00000000)")
        assert bookmark.commit is not None
        await click(app, "diff-hunk-0-1")  # the second hunk
        await click(app, "diff-ask")
        await click(app, "changes-ask-send")
        scope = sent[2][0]
        assert (scope.kind, scope.page, scope.hunk) == ("hunk", 0, 1)
        assert scope.commit == f"{0:040x}" and (scope.start, scope.end) == (20, 23)
        assert "lines 20–23 (Commit: 00000000)" in build_prompt(scope, "Why?")
        assert len(sent) == 3

    run(scenario())


@pytest.mark.parametrize(
    ("width", "height"),
    [pytest.param(390, 844, id="phone"), pytest.param(1032, 1376, id="tablet-portrait")],
)
def test_a_narrow_screen_picks_a_hunk_in_the_copy_sheet_and_asks_about_it(
    width: int, height: int
) -> None:
    gateway = Gateway(files())
    sent: list[tuple[Any, ...]] = []

    def ask(*args: Any) -> bool:
        sent.append(args)
        return True

    async def scenario() -> None:
        app = await graph(gateway, width=width, height=height, on_ask=ask, copied=[])
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-file-0")
        await click(app, "commit-file-0")
        await app.wait_for("diff-rows")
        # The header asks about the whole file; a hunk is chosen in the Copy sheet.
        assert present(app, "diff-ask") and not present(app, "diff-hunk-0-0")
        await click(app, "diff-copy")
        await click(app, "changes-ask-hunk-0-1-None")
        assert app.find("changes-ask-title").value == "Ask about this hunk"
        await click(app, "changes-ask-send")
        scope = sent[0][0]
        assert (scope.kind, scope.hunk, scope.commit) == ("hunk", 1, f"{0:040x}")
        assert scope.path == "src/c0/a.py"
        # The hunk stays selected, but the header of a diff without hunk selection asks the file.
        await click(app, "diff-ask")
        assert "file" in app.find("changes-ask-title").value
        await click(app, "changes-ask-send")
        assert sent[1][0].kind == "file" and sent[1][0].path == "src/c0/a.py"

    run(scenario())


def test_tapping_a_hunk_header_beside_the_graph_selects_it_and_a_file_change_clears_it() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app = await graph(gateway, width=1376, height=1000, wide=True)
        await click(app, row_key(gateway, 0))
        await app.wait_for("diff-rows")
        assert not app.find("diff-hunk-0-0").data["selected"]
        await click(app, "diff-hunk-0-0")
        assert app.find("diff-hunk-0-0").data["selected"]
        await click(app, "diff-hunk-0-1")
        assert app.find("diff-hunk-0-1").data["selected"]
        assert not app.find("diff-hunk-0-0").data["selected"]
        await click(app, "changes-next")
        await app.wait_for("diff-rows")
        assert not any(
            present(app, f"diff-hunk-0-{n}") and app.find(f"diff-hunk-0-{n}").data["selected"]
            for n in range(2)
        )

    run(scenario())


def test_ask_then_terminal_then_back_returns_to_the_same_commit_and_file() -> None:
    gateway = Gateway(files())
    sent: list[tuple[Any, ...]] = []

    def ask(*args: Any) -> bool:
        sent.append(args)
        return True

    async def scenario() -> None:
        app = await graph(gateway, width=390, height=844, on_ask=ask)
        await click(app, row_key(gateway, 1))
        await app.wait_for("commit-file-1")
        await click(app, "commit-file-1")
        await app.wait_for("diff-rows")
        await click(app, "changes-files-back")
        await click(app, "commit-ask")
        await click(app, "changes-ask-send")
        bookmark = sent[0][2]
        before = gateway.count("commit_changes")
        restored = await render(gateway, on_ask=ask, bookmark=bookmark)
        await restored.wait_for("commit-file-1")
        assert text(restored, "commit-subject") == "Commit number 1"
        assert gateway.count("commit_changes") == before  # nothing is read twice
        await click(restored, "commit-back")
        await restored.wait_for("changes-graph")
        assert restored.find(row_key(gateway, 1)).data["selected"]

    run(scenario())


def test_a_commit_that_can_no_longer_be_opened_says_so_and_the_graph_can_be_read_again() -> None:
    gateway = Gateway(files())
    gateway.failures["commit_changes"] = TransportError(
        "stale_revision", "gone", False, {"reason": "stale_snapshot"}
    )

    async def scenario() -> None:
        app = await graph(gateway, width=1376, height=1000, wide=True)
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-refresh-graph")
        assert "no longer available" in str(app.find("commit-state").content.controls[0].value)
        before = gateway.count("graph")
        await click(app, "commit-refresh-graph")
        await until(lambda: gateway.count("graph") > before)
        await app.wait_for("commit-none")

    run(scenario())


def test_a_busy_mac_offers_retry_that_loads_the_commit() -> None:
    gateway = Gateway(files())
    gateway.failures["commit_changes"] = TransportError("rate_limited", "busy", True, {})

    async def scenario() -> None:
        app = await graph(gateway, width=390, height=844)
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-retry")
        del gateway.failures["commit_changes"]
        await click(app, "commit-retry")
        await app.wait_for("commit-file-0")

    run(scenario())


def test_a_long_file_list_pages_in_and_a_large_diff_waits_for_load_diff() -> None:
    big = FileSpec("src/big.py", added_patch("src/big.py", 1200), status="A")
    gateway = Gateway(
        files(),
        commit_page=1,
        commit_files={0: [big, FileSpec("src/b.py", added_patch("src/b.py", 3), status="A")]},
    )

    async def scenario() -> None:
        app = await graph(gateway, width=390, height=844)
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-more-files")
        await click(app, "commit-more-files")
        await app.wait_for("commit-file-1")
        assert not present(app, "commit-more-files")
        await click(app, "commit-file-0")
        await app.wait_for("diff-load")
        await click(app, "diff-load")
        await app.wait_for("diff-rows")
        assert any(c[0] == "diff" and c[3] is True for c in gateway.calls)

    run(scenario())


def test_an_empty_commit_is_a_valid_empty_list() -> None:
    gateway = Gateway(files(), commit_files={0: []})

    async def scenario() -> None:
        app = await graph(gateway, width=390, height=844)
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-empty")

    run(scenario())


def test_rotating_between_layouts_keeps_the_commit_open_and_reads_nothing_again() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app, control = await mount(gateway, width=390, height=844)
        await app.wait_for("changes-files")
        await click(app, "changes-view-graph")
        await app.wait_for(row_key(gateway, 0))
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-file-0")
        reads = gateway.count("commit_changes")
        assert present(app, "commit-pane") and not present(app, "changes-graph")
        control["set_viewport"]((1376, 1000))
        control["set_wide"](True)
        await app.wait_for("changes-graph")
        await app.wait_for("diff-rows")
        assert present(app, "commit-pane") and app.find(row_key(gateway, 0)).data["selected"]
        control["set_viewport"]((390, 844))
        control["set_wide"](False)
        await until(lambda: not present(app, "changes-graph"))
        # The wide layout chose a file, so the narrow one continues in that file's diff.
        assert present(app, "diff-rows") and present(app, "changes-files-back")
        assert gateway.count("commit_changes") == reads

    run(scenario())
