"""A commit whose pages stop coming: what stays on screen and what the reader is offered."""

from __future__ import annotations

from typing import Any

import flet as ft
import pytest

from relaydeck.client.features.ask_agent.model import build_prompt
from relaydeck.client.ports.transport import TransportError
from tests.support.git_patches import added_patch
from tests.support.host_harness import run
from tests.ui.git.gateway import FileSpec, Gateway
from tests.ui.git.harness import click, files, graph, present, row_key, until

GONE = TransportError("stale_revision", "gone", False, {"reason": "stale_snapshot"})
EXPIRED = TransportError("forbidden", "expired", False, {"reason": "expired"})
BUSY = TransportError("rate_limited", "busy", True, {})
ORIGINAL = "cid:all:r:0"


def texts(app: Any) -> list[str]:
    values: list[str] = []
    stack: list[Any] = [app.page]
    seen: set[int] = set()
    while stack:
        control = stack.pop()
        if control is None or id(control) in seen:
            continue
        seen.add(id(control))
        if isinstance(control, ft.Text) and control.value:
            values.append(control.value)
        for attr in ("controls", "content", "_b", "views"):
            value = getattr(control, attr, None)
            if value is not None:
                stack.extend(value if isinstance(value, list | tuple) else [value])
    return values


def said(app: Any, fragment: str) -> bool:
    return any(fragment in value for value in texts(app))


def big_commit() -> Gateway:
    big = FileSpec("big.py", added_patch("big.py", 1200), status="A")
    return Gateway(files(), commit_page=1, commit_files={0: [big]})


async def paged_diff(gateway: Gateway) -> Any:
    app = await graph(gateway)
    await click(app, row_key(gateway, 0))
    await app.wait_for("commit-file-0")
    await click(app, "commit-file-0")
    await app.wait_for("diff-load")
    await click(app, "diff-load")
    await app.wait_for("diff-more")
    return app


def test_the_file_list_continuation_continues_with_the_original_id_and_cursor() -> None:
    gateway = Gateway(files(), commit_page=1)

    async def scenario() -> None:
        app = await graph(gateway)
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-more-files")
        await click(app, "commit-more-files")
        await app.wait_for("commit-file-1")
        reads = [c for c in gateway.calls if c[0] == "commit_changes"]
        assert [c[1:] for c in reads] == [(ORIGINAL, None), (ORIGINAL, "1")]

    run(scenario())


def test_an_expired_file_list_cursor_keeps_the_files_and_offers_the_graph_not_the_cursor() -> None:
    gateway = Gateway(files(), commit_page=1)

    async def scenario() -> None:
        app = await graph(gateway)
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-more-files")
        gateway.failures["commit_changes"] = GONE
        await click(app, "commit-more-files")
        await app.wait_for("commit-refresh-graph")
        assert said(app, "no longer available") and present(app, "commit-file-0")
        assert not present(app, "commit-more-files")  # a dead cursor is not offered again
        calls = gateway.count("commit_changes")
        before = gateway.count("graph")
        await click(app, "commit-refresh-graph")
        await until(lambda: gateway.count("graph") > before)
        await until(lambda: not present(app, "commit-pane"))
        assert gateway.count("commit_changes") == calls

    run(scenario())


def test_a_stale_diff_continuation_keeps_the_rows_and_offers_the_graph() -> None:
    gateway = big_commit()

    async def scenario() -> None:
        app = await paged_diff(gateway)
        gateway.failures["diff"] = GONE
        await click(app, "diff-more")
        await app.wait_for("commit-refresh-graph")
        assert said(app, "no longer available") and present(app, "diff-rows")
        assert not present(app, "diff-more")
        diffs = gateway.count("diff")
        await app.settle()
        assert gateway.count("diff") == diffs

    run(scenario())


def test_a_busy_file_list_continuation_says_so_and_retry_loads_the_page() -> None:
    gateway = Gateway(files(), commit_page=1)

    async def scenario() -> None:
        app = await graph(gateway)
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-more-files")
        gateway.failures["commit_changes"] = BUSY
        await click(app, "commit-more-files")
        await until(lambda: said(app, "The Mac is busy"))
        assert present(app, "commit-file-0") and not present(app, "commit-refresh-graph")
        assert app.find("commit-more-files").content == "Retry"
        del gateway.failures["commit_changes"]
        await click(app, "commit-more-files")
        await app.wait_for("commit-file-1")
        assert not said(app, "The Mac is busy")

    run(scenario())


def test_a_busy_diff_continuation_says_so_keeps_the_rows_and_can_be_tried_again() -> None:
    gateway = big_commit()

    async def scenario() -> None:
        app = await paged_diff(gateway)
        rows = len(app.find("diff-rows").controls)
        gateway.failures["diff"] = BUSY
        await click(app, "diff-more")
        await until(lambda: said(app, "The Mac is busy"))
        assert present(app, "diff-more") and not present(app, "commit-refresh-graph")
        assert len(app.find("diff-rows").controls) == rows
        del gateway.failures["diff"]
        await click(app, "diff-more")
        await until(lambda: not said(app, "The Mac is busy"))
        assert gateway.count("diff") >= 4 and present(app, "diff-rows")

    run(scenario())


def test_an_expired_id_refreshes_the_graph_and_opens_the_same_commit() -> None:
    gateway = Gateway(files())
    gateway.fail_once["commit_changes"] = EXPIRED

    async def scenario() -> None:
        app = await graph(gateway, width=1376, height=1000, wide=True)
        await click(app, row_key(gateway, 0))
        await app.wait_for("diff-rows")  # beside the graph the commit opens at its first file
        assert gateway.count("graph") == 2 and gateway.count("commit_changes") == 2
        assert str(app.find("commit-subject").value) == "Commit number 0"
        assert app.find(row_key(gateway, 0)).data["selected"]

    run(scenario())


def test_an_id_that_expires_again_is_refreshed_once_and_then_explained() -> None:
    gateway = Gateway(files())
    gateway.failures["commit_changes"] = EXPIRED

    async def scenario() -> None:
        app = await graph(gateway)
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-refresh-graph")
        await app.settle(30)
        assert gateway.count("graph") == 2 and gateway.count("commit_changes") == 2
        assert said(app, "no longer available")

    run(scenario())


@pytest.mark.parametrize("merge", [False, True])
def test_a_merge_lists_its_parents_with_a_plus(merge: bool) -> None:
    gateway = Gateway(files(), merge_at=0 if merge else None)

    async def scenario() -> None:
        app = await graph(gateway)
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-parents")
        short = [f"{n:040x}"[:8] for n in ((1, 2) if merge else (1,))]
        assert app.find("commit-parents").value == "parent " + " + ".join(short)

    run(scenario())


def test_the_file_list_fills_the_pane_and_show_more_keeps_its_height() -> None:
    gateway = Gateway(files(), commit_page=1)

    async def scenario() -> None:
        app = await graph(gateway, width=1376, height=1000, wide=True)
        await click(app, row_key(gateway, 0))
        await app.wait_for("diff-rows")
        await click(app, "changes-files-back")
        await app.wait_for("commit-more-files")
        assert app.find("commit-files").expand and not present(app, "commit-files-box")
        assert app.find("commit-more-files").height == 48

    run(scenario())


def test_hunks_and_lines_can_be_copied_and_asked_about_while_a_commit_is_open() -> None:
    gateway = Gateway(files())
    copied: list[str] = []
    sent: list[tuple[Any, ...]] = []

    def ask(*args: Any) -> bool:
        sent.append(args)
        return True

    async def scenario() -> None:
        app = await graph(gateway, copied=copied, on_ask=ask)
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-file-0")
        await click(app, "commit-file-0")
        await app.wait_for("diff-rows")
        await click(app, "diff-copy")
        await app.wait_for("changes-copy-hunk-0")
        assert asks(app) and not any(c.disabled for c in asks(app))
        await click(app, "changes-copy-hunk-0-code")
        assert len(copied) == 1 and copied[0]
        assert not present(app, "changes-copy-sheet")
        await click(app, "diff-copy")
        await click(app, "changes-copy-hunk-0-lines")
        line = next(
            c.key
            for c in app.find("changes-copy-list").controls
            if str(getattr(c, "key", "")).startswith("changes-copy-line-0-0-")
        )
        assert asks(app)
        await click(app, line)
        assert len(copied) == 2 and copied[1] != copied[0]
        await click(app, "diff-copy")
        await click(app, asks(app)[0].key)  # a hunk of the commit's file
        await click(app, "changes-ask-send")
        scope = sent[-1][0]
        assert scope.kind == "hunk" and scope.path == "src/c0/a.py"
        assert scope.commit == f"{0:040x}" and scope.start is not None
        prompt = build_prompt(scope, "Why?")
        assert prompt.startswith(f"About src/c0/a.py, lines {scope.start}")
        assert prompt.endswith(f"(Commit: {scope.commit[:8]}): Why?")

    run(scenario())


def asks(app: Any) -> list[Any]:
    return [c for c in walk(app) if str(getattr(c, "key", "")).startswith("changes-ask-")]


def walk(app: Any) -> list[Any]:
    found: list[Any] = []
    stack: list[Any] = [app.page]
    seen: set[int] = set()
    while stack:
        control = stack.pop()
        if control is None or id(control) in seen:
            continue
        seen.add(id(control))
        found.append(control)
        for attr in ("controls", "content", "_b", "views"):
            value = getattr(control, attr, None)
            if value is not None:
                stack.extend(value if isinstance(value, list | tuple) else [value])
    return found
