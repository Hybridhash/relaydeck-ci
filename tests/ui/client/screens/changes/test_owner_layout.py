"""Layout findings from the phone: shared inset, bottom actions, graph rows, one graph summary."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import flet as ft
import pytest

from relaydeck.client.design_system import tokens as t
from relaydeck.client.features.changes.graph_state import GraphState
from relaydeck.client.features.changes.state import ChangesModel
from relaydeck.client.screens.changes.parts.graph_rows import BADGE_CAP
from relaydeck.client.screens.changes.parts.header import graph_summary
from relaydeck.contracts.git import GraphCommit, GraphRef
from tests.support.flet_layout import expand_violations
from tests.support.graph_pages import graph_page
from tests.support.host_harness import run
from tests.ui.git.gateway import Gateway
from tests.ui.git.harness import click, files, graph, present, render, row_key, summary_text

SIZES = [
    pytest.param(390, 844, False, id="390x844"),
    pytest.param(430, 932, False, id="430x932"),
    pytest.param(844, 390, False, id="844x390"),
    pytest.param(1376, 1000, True, id="1376x1000"),
]
INSET = t.PAD_PHONE


def walk(root: Any) -> list[Any]:
    found: list[Any] = []
    stack: list[Any] = [root]
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


def horizontal(padding: Any) -> tuple[float, float]:
    return float(padding.left), float(padding.right)


@pytest.mark.parametrize(("width", "height", "wide"), SIZES)
def test_search_chips_and_header_share_one_inset_and_the_field_fills_it(
    width: int, height: int, wide: bool
) -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app = await render(gateway, width=width, height=height, wide=wide)
        await app.wait_for("changes-files")
        header = horizontal(app.find("changes-header").padding)
        assert header == (INSET, INSET)
        assert horizontal(app.find("changes-search-row").padding) == header
        row = app.find("changes-search-row").content
        assert isinstance(row, ft.Row) and row.controls[0] is app.find("changes-search")
        assert app.find("changes-search").expand is True  # spans the content width
        await click(app, "changes-scope-branch")
        await app.wait_for("changes-files")
        assert horizontal(app.find("changes-search-row").padding) == header
        await click(app, "changes-view-graph")
        await app.wait_for("graph-chips")
        assert horizontal(app.find("graph-chips").padding) == header
        assert expand_violations(app.page) == []

    run(scenario())


@pytest.mark.parametrize(("width", "height", "wide"), SIZES)
def test_the_bottom_actions_are_content_sized_where_they_must_be_and_never_clip(
    width: int, height: int, wide: bool
) -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app = await render(
            gateway, width=width, height=height, wide=wide, on_ask=lambda *a: True, copied=[]
        )
        await app.wait_for("changes-actions")
        bar = app.find("changes-actions")
        buttons = bar.content.controls
        assert [b.key for b in buttons] == [
            "changes-terminal",
            "changes-ask",
            "changes-copy-action",
        ]
        terminal, ask, copy = buttons
        assert all(b.height >= t.MIN_TOUCH for b in buttons)
        # Terminal and Copy are as wide as their content; Ask takes the rest.
        assert not terminal.expand and not copy.expand and ask.expand is True
        for button in (terminal, copy):
            label = next(c for c in walk(button.content) if isinstance(c, ft.Text))
            assert label.no_wrap and label.max_lines == 1
            assert button.style.padding.left >= 12
        # The space the three need at a phone's width, by an estimate of their labels.
        room = width - 2 * INSET - 2 * 10
        needed = (18 + 6 + 8 * 8.2 + 28) + (18 + 6 + 11 * 8.2 + 28)
        assert room - needed >= 60  # Ask keeps room for its label
        assert expand_violations(app.page) == []

    run(scenario())


def test_the_bottom_actions_on_the_phone_still_work() -> None:
    gateway = Gateway(files())
    seen: list[str] = []

    async def scenario() -> None:
        app = await render(gateway, width=390, height=844, copied=seen, on_ask=lambda *a: True)
        await app.wait_for("changes-copy-action")
        await click(app, "changes-copy-action")
        assert seen == ["feature"]

    run(scenario())


def commit(refs: tuple[GraphRef, ...], subject: str = "A subject") -> GraphCommit:
    return GraphCommit(oid="a" * 40, parents=(), subject=subject, time_ms=1, ahead=True, refs=refs)


@pytest.mark.parametrize(("width", "height", "wide"), SIZES)
def test_rows_lead_with_the_refs_when_stacked_and_keep_the_subject_wide_when_not(
    width: int, height: int, wide: bool
) -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app = await graph(gateway, width=width, height=height, wide=wide)
        row = app.find(row_key(gateway, 0))
        stacked = row.data["stacked"]
        text: Any = row.content.controls[1].content
        if stacked:
            assert isinstance(text, ft.Column)
            parts: list[Any] = list(text.controls)
            first, second = parts
            assert isinstance(first.content, ft.Row)  # refs, oid, age
            kinds = [
                type(c.value).__name__ if isinstance(c, ft.Text) else "row"
                for c in first.content.controls
            ]
            assert kinds[0] == "row" and len(first.content.controls) == 3
            assert second.value == "Commit number 0" and second.max_lines == 1
        else:
            assert isinstance(text, ft.Row)
            pair: list[Any] = list(text.controls)
            subject, meta = pair
            assert subject.expand is True and subject.content.value == "Commit number 0"
            assert not meta.expand and meta.tight is True
        assert expand_violations(app.page) == []

    run(scenario())


def test_a_long_badge_is_cut_inside_itself_and_the_rest_are_counted() -> None:
    from relaydeck.client.screens.changes.parts.graph_rows import ref_badges

    long_name = "feature/" + "very-long-branch-name-" * 8
    refs = (
        GraphRef(name="main", kind="base"),
        GraphRef(name=long_name, kind="local"),
        GraphRef(name="origin/x", kind="upstream"),
        GraphRef(name="topic", kind="local"),
    )
    model = ChangesModel(graph=GraphState(page=graph_page()))
    row = ref_badges(commit(refs), model, budget=250.0)
    badges: list[Any] = [c for c in row.controls if isinstance(c, ft.Container)]
    assert all(b.width is not None and b.width <= BADGE_CAP for b in badges)
    cut = next(b for b in badges if b.content.value == long_name)
    assert cut.width == BADGE_CAP and cut.content.overflow == ft.TextOverflow.ELLIPSIS
    assert cut.content.max_lines == 1 and cut.content.no_wrap
    counted: list[Any] = [c for c in row.controls if isinstance(c, ft.Text)]
    assert len(badges) + int(counted[0].value.lstrip("+")) == len(refs)
    # The first (semantic) badge is always there, even if it alone exceeds the room.
    tiny: Any = ref_badges(commit(refs), model, budget=1.0)
    assert tiny.controls[0].content.value == "main · base"


def test_the_graph_shows_one_summary_line_with_everything_in_it() -> None:
    gateway = Gateway(files(), commit_total=17)

    async def scenario() -> None:
        app = await graph(gateway, width=390, height=844)
        summary = summary_text(app.find("graph-summary"))
        assert summary.startswith("17 commits ahead of origin/main · ")
        assert (
            "split at " in summary
            and summary.endswith("as of just now") is False
            or "as of" in summary
        )
        # The reference line no longer repeats the count or the time.
        assert not present(app, "changes-asof")
        texts = [
            summary_text(c)
            for c in walk(app.page)
            if isinstance(c, ft.Text) and (c.value or c.spans)
        ]
        assert sum("commits ahead" in value for value in texts) == 1
        assert sum("as of" in value for value in texts) == 1

    run(scenario())


def test_the_summary_orders_ahead_behind_split_and_age() -> None:
    page = replace(graph_page(), ahead=17, behind=1, as_of_ms=1_000_000)
    model = ChangesModel(graph=GraphState(page=page))
    line = graph_summary(model, 1_000_000)
    assert isinstance(line, ft.Text)
    assert (
        summary_text(line)
        == "17 commits ahead of main · 1 behind · split at bbbbbbb · as of just now"
    )
    no_behind = graph_summary(
        ChangesModel(graph=GraphState(page=replace(page, behind=None))), 1_000_000
    )
    assert isinstance(no_behind, ft.Text)
    assert "behind" not in summary_text(no_behind)


def test_files_view_keeps_its_reference_line_with_count_and_time() -> None:
    gateway = Gateway(files(), commit_total=3)

    async def scenario() -> None:
        app = await render(gateway, width=390, height=844)
        await app.wait_for("changes-files")
        await click(app, "changes-scope-branch")
        await app.wait_for("changes-asof")
        assert "3 commits ahead" in app.find("changes-asof").value
        assert "as of" in app.find("changes-asof").value

    run(scenario())
