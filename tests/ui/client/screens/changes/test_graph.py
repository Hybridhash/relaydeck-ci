"""Graph rendering and branch actions at each approved artboard size."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import flet as ft
import pytest

import relaydeck.client.screens.changes.controller as controller
import relaydeck.client.screens.changes.parts.graph_rows as graph_rows
from relaydeck.client.design_system import tokens as t
from relaydeck.client.features.ask_agent.model import build_prompt
from relaydeck.client.features.changes.graph_state import GraphState
from relaydeck.client.features.changes.state import ChangesModel
from relaydeck.client.screens.changes.parts.header import graph_summary
from tests.support.flet_layout import expand_violations
from tests.support.graph_pages import graph_page
from tests.support.host_harness import run
from tests.ui.git.gateway import Gateway
from tests.ui.git.harness import click, files, present, render, summary_text, until


@pytest.mark.parametrize(
    ("width", "height", "wide", "stacked"),
    [
        (390, 844, False, True),
        (844, 390, False, False),
        (1032, 1376, False, False),
        (1376, 1000, True, True),
    ],
)
def test_graph_artboards_paging_and_branch_ask(
    width: int,
    height: int,
    wide: bool,
    stacked: bool,
) -> None:
    sent: list[tuple[Any, ...]] = []
    gateway = Gateway(files(), commit_total=201, graph_page_size=100)

    def ask(*args: Any) -> bool:
        sent.append(args)
        return True

    async def scenario() -> None:
        app = await render(gateway, width=width, height=height, wide=wide, on_ask=ask)
        await app.wait_for("changes-files")
        await click(app, "changes-view-graph")
        revision = gateway.snapshot("branch") + ":graph"
        row_key = f"graph-row-{revision}-{0:040x}"
        await app.wait_for(row_key)
        assert app.page.width == width and app.page.height == height
        assert app.find(row_key).data["stacked"] is stacked and app.find(row_key).height >= 48
        assert app.find("changes-graph").data["rows"] == 100
        assert "201 commits ahead" in summary_text(app.find("graph-summary"))
        assert not present(app, "changes-totals") and not app.find("changes-ask").disabled
        assert any(c[:3] == ("changes", "wt-stable", "branch") for c in gateway.calls)
        await click(app, "changes-more-graph")
        await until(lambda: app.find("changes-graph").data["rows"] == 200)
        await click(app, "changes-ask")
        await click(app, "changes-ask-send")
        scope, question, bookmark, _ = sent[0]
        assert scope.section == "branch" and scope.kind == "branch"
        assert build_prompt(scope, question).startswith("About this branch")
        assert bookmark.view == "graph" and bookmark.graph is not None
        assert len(bookmark.graph.commits) == 200 and len(sent) == 1
        assert expand_violations(app.page) == []
        await click(app, "graph-mode-branch")
        await until(lambda: any(c[0] == "graph" and c[-1] == "branch" for c in gateway.calls))
        await until(lambda: app.find("graph-mode-branch").data["selected"])
        assert app.find("graph-mode-branch").style.bgcolor == t.PRIMARY_FILL
        assert not app.find("graph-mode-all").data["selected"]
        assert expand_violations(app.page) == []

    run(scenario())


def test_graph_bookmark_keeps_chip_all_pages_selection_and_anchor() -> None:
    gateway = Gateway(files(), issue_commit_ids=False)  # a row with no id is only selected
    sent: list[tuple[Any, ...]] = []

    def ask(*args: Any) -> bool:
        sent.append(args)
        return True

    async def scenario() -> None:
        app = await render(gateway, on_ask=ask)
        await app.wait_for("changes-files")
        await click(app, "changes-view-graph")
        await click(app, "graph-mode-branch")
        await app.wait_for("changes-more-graph")
        await click(app, "changes-more-graph")
        await until(lambda: app.find("changes-graph").data["rows"] == 4)
        revision = gateway.snapshot("branch") + ":graph"
        key = f"graph-row-{revision}-{3:040x}"
        await click(app, key)
        app.find("changes-graph").on_scroll(SimpleNamespace(pixels=3 * 60 + 7))
        await click(app, "changes-ask")
        await click(app, "changes-ask-send")
        bookmark = sent[0][2]
        assert bookmark.graph.mode == "branch" and bookmark.graph.selected == f"{3:040x}"
        assert bookmark.graph.anchor.oid == f"{3:040x}" and bookmark.graph.anchor.offset == 7
        before = gateway.count("graph")
        restored = await render(gateway, on_ask=ask, bookmark=bookmark)
        await restored.wait_for(key)
        assert restored.find(key).data["selected"]
        assert restored.find("changes-graph").data["rows"] == 4
        assert gateway.count("graph") == before

    run(scenario())


def test_chip_change_invalidates_even_an_old_question_callback() -> None:
    sent: list[tuple[Any, ...]] = []

    def ask(*args: Any) -> bool:
        sent.append(args)
        return True

    async def scenario() -> None:
        gateway = Gateway(files())
        app = await render(gateway, on_ask=ask)
        await app.wait_for("changes-files")
        await click(app, "changes-view-graph")
        await app.wait_for("changes-more-graph")
        await click(app, "changes-ask")
        old_send = app.find("changes-ask-send").on_click
        await click(app, "graph-mode-branch")
        assert app.find("changes-ask-send").disabled
        assert old_send is not None
        old_send(None)
        await app.settle()
        assert not sent

    run(scenario())


def test_graph_blocks_review_keys_and_keeps_working_viewed_marks() -> None:
    async def scenario() -> None:
        gateway = Gateway(files())
        app = await render(gateway, wide=True)
        await app.wait_for("changes-file-0")
        await click(app, "changes-file-0")
        await app.wait_for("changes-viewed")
        await app.set_props_and_fire("changes-viewed", {"value": True}, "change", "true")
        await click(app, "changes-view-graph")
        await app.wait_for("changes-more-graph")
        before = gateway.count("diff")
        for key in "jkv":
            await app.session.dispatch_event(app.page._i, "keyboard_event", {"key": key})
        await app.settle()
        assert gateway.count("diff") == before and not present(app, "changes-viewed")
        await click(app, "changes-scope-working")
        await app.wait_for("changes-file-0")
        await click(app, "changes-file-0")
        await app.wait_for("changes-viewed")
        assert app.find("changes-viewed").value is True

    run(scenario())


def test_base_change_invalidates_open_question_and_reload_uses_new_base() -> None:
    sent: list[tuple[Any, ...]] = []

    def ask(*args: Any) -> bool:
        sent.append(args)
        return True

    async def scenario() -> None:
        gateway = Gateway(files())
        app = await render(gateway, on_ask=ask)
        await app.wait_for("changes-files")
        await click(app, "changes-view-graph")
        await app.wait_for("changes-more-graph")
        await click(app, "changes-ask")
        old = app.find("changes-ask-send").on_click
        await click(app, "changes-change-base")
        await app.wait_for("changes-base-1")
        await click(app, "changes-base-1")
        await until(lambda: "origin/develop" in summary_text(app.find("graph-summary")))
        assert old is not None
        old(None)
        await app.settle()
        assert not sent and app.find("changes-ask-send").disabled

    run(scenario())


def test_foreground_ref_change_offers_refresh_without_replacing_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(controller, "GRAPH_CHECK_INTERVAL_S", 0.02)

    async def scenario() -> None:
        gateway = Gateway(files())
        app = await render(gateway)
        await app.wait_for("changes-files")
        await click(app, "changes-view-graph")
        revision = gateway.snapshot("branch") + ":graph"
        old_key = f"graph-row-{revision}-{0:040x}"
        await app.wait_for(old_key)
        gateway.graph_revision = "-moved"
        await app.wait_for("changes-updated")
        assert present(app, old_key)
        await click(app, "changes-updated")
        await app.wait_for(f"graph-row-{revision}-moved-{0:040x}")
        assert not present(app, old_key)

    run(scenario())


def test_ref_limit_keeps_the_graph_and_discloses_omitted_branches() -> None:
    async def scenario() -> None:
        gateway = Gateway(files(), graph_partial=True)
        app = await render(gateway)
        await app.wait_for("changes-files")
        await click(app, "changes-view-graph")
        await app.wait_for("graph-ref-limit")
        assert app.find("graph-ref-limit").value == "Some branches are not shown"
        assert present(app, "changes-more-graph")

    run(scenario())


def test_unchanged_foreground_checks_do_not_render_resident_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(controller, "GRAPH_CHECK_INTERVAL_S", 0.02)
    original = graph_rows.build_row
    rendered = 0

    def row(*args: Any, **kwargs: Any) -> ft.Control:
        nonlocal rendered
        rendered += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(graph_rows, "build_row", row)

    async def scenario() -> None:
        gateway = Gateway(files())
        app = await render(gateway)
        await app.wait_for("changes-files")
        await click(app, "changes-view-graph")
        await app.wait_for("changes-more-graph")
        before = rendered
        calls = gateway.count("graph")
        await until(lambda: gateway.count("graph") >= calls + 3)
        await app.settle()
        assert rendered == before and not app.find("changes-more-graph").disabled

    run(scenario())


def test_paging_remains_available_while_a_foreground_check_is_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(controller, "GRAPH_CHECK_INTERVAL_S", 0.02)
    original = Gateway.git_graph

    async def scenario() -> None:
        started, release = asyncio.Event(), asyncio.Event()
        blocking = False

        async def delayed(
            gateway: Gateway,
            host: str,
            worktree: str,
            *,
            mode: str = "all",
            cursor: str | None = None,
        ) -> dict[str, Any]:
            if blocking and cursor is None:
                started.set()
                await release.wait()
            return await original(gateway, host, worktree, mode=mode, cursor=cursor)

        monkeypatch.setattr(Gateway, "git_graph", delayed)
        gateway = Gateway(files())
        app = await render(gateway)
        try:
            await app.wait_for("changes-files")
            await click(app, "changes-view-graph")
            await app.wait_for("changes-more-graph")
            blocking = True
            await asyncio.wait_for(started.wait(), 1)
            assert not app.find("changes-more-graph").disabled
            await click(app, "changes-more-graph")
            await until(lambda: app.find("changes-graph").data["rows"] == 4)
            release.set()
            await app.settle()
            assert app.find("changes-graph").data["rows"] == 4
        finally:
            release.set()

    run(scenario())


def test_graph_summary_uses_singular_commit() -> None:
    page = replace(graph_page(), ahead=1)
    model = ChangesModel(graph=GraphState(page=page))
    summary = graph_summary(model, 1_000_000)
    assert isinstance(summary, ft.Text)
    assert summary_text(summary).startswith("1 commit ahead of main")
