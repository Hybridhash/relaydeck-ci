"""The console card's changes row: states, reserved height, label, tap routing and rebuilds."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import flet as ft
import pytest

from relaydeck.client.design_system import tokens as T
from relaydeck.client.models.changes_summary import changes_row
from relaydeck.client.models.inventory_selectors import AgentRow
from relaydeck.client.screens.console.parts.lists import AgentsList
from relaydeck.client.widgets.sessions.cards import AgentCard
from relaydeck.contracts.git import BranchCounts, WorkingCounts, WorktreeSummary
from relaydeck.domain.models import AgentStatus
from tests.support.host_harness import HostApp, render_component, run

KEY = "home-mac:term-1"


def summary(**over: Any) -> WorktreeSummary:
    counts = {"files": 0, "staged": 0, "unstaged": 0, "untracked": 0, "conflicts": 0}
    working = WorkingCounts(
        **{**counts, **over.pop("working", {})},
        additions=over.pop("additions", 0),
        deletions=over.pop("deletions", 0),
    )
    return WorktreeSummary(
        **{
            "agent_id": "term-1",
            "worktree_id": "wt",
            "state": "ok",
            "branch": "feature",
            "detached": False,
            "short_oid": "abc1234",
            "base_name": "origin/main",
            "working": working,
            "branch_scope": BranchCounts(commits=over.pop("commits", None)),
            "shared": False,
            "revision": "rev-1",
            "as_of_ms": 1,
            "partial": False,
            **over,
        }
    )


DIRTY = {"files": 3, "unstaged": 2, "untracked": 2}

STATES: dict[str, tuple[WorktreeSummary | None, str, str]] = {
    # name: (summary, branch text, figure text)
    "none": (None, "", ""),
    "calculating": (summary(state="calculating", branch=None), "", "Calculating…"),
    "no_repo": (summary(state="no_repo", branch=None), "", "No repository"),
    "folder_gone": (summary(state="folder_gone"), "", "Changes unavailable"),
    "unavailable": (summary(state="unavailable"), "", "Changes unavailable"),
    "unsupported": (summary(state="unsupported_config"), "", "Changes unavailable"),
    "git_missing": (summary(state="git_unavailable"), "", "Changes unavailable"),
    "clean": (summary(), "feature", "Clean"),
    "clean_ahead": (summary(commits=3), "feature", "Clean"),
    "clean_one_ahead": (summary(commits=1), "feature", "Clean"),
    "dirty": (
        summary(working=DIRTY, additions=12, deletions=3),
        "feature",
        "+12 −3",
    ),
    "dirty_no_untracked": (
        summary(working={"files": 1, "staged": 1}, additions=4, deletions=0),
        "feature",
        "+4 −0",
    ),
    "net_zero": (summary(working={"files": 1, "staged": 1}), "feature", "+0 −0"),
    "unknown_counts": (
        summary(working=DIRTY, additions=None, deletions=None),
        "feature",
        "3 files",
    ),
    "conflict": (
        summary(working={"files": 1, "conflicts": 1}),
        "feature",
        "+0 −0 · 1 unmerged",
    ),
    "detached": (
        summary(detached=True, branch=None, working=DIRTY, additions=1, deletions=1),
        "abc1234",
        "+1 −1",
    ),
    "partial": (
        summary(working=DIRTY, additions=5, deletions=2, partial=True),
        "feature",
        "+5 −2",
    ),
    "shared": (summary(shared=True), "feature", "Clean"),
}


async def card(changes: WorktreeSummary | None, **over: Any) -> HostApp:
    props: dict[str, Any] = {
        "agent_id": KEY,
        "name": "agent",
        "status": "working",
        "subtitle": "tool-a · 2m ago",
        "meta_left": "",
        "meta_right": "",
        "selected": False,
        "on_select": lambda _key: None,
        "agent": "tool-a",
        "changes": changes_row(changes),
        "on_changes": lambda _key: None,
        **over,
    }
    return await render_component(AgentCard, **props)


def present(app: HostApp, key: str) -> bool:
    try:
        app.find(key)
    except KeyError:
        return False
    return True


def spans_text(app: HostApp, key: str) -> str:
    return "".join(span.text for span in app.find(key).spans) if present(app, key) else ""


def row_texts(app: HostApp) -> tuple[str, str]:
    """(branch, figure) as shown; empty when the row has nothing to say."""
    prefix = f"agent-changes-{KEY}"
    branch = app.find(f"{prefix}-branch").value if present(app, f"{prefix}-branch") else ""
    return branch, spans_text(app, f"{prefix}-figure")


def dirty_dot(app: HostApp) -> bool:
    return present(app, f"agent-changes-{KEY}-dirty")


def row_height(app: HostApp) -> float:
    key = f"agent-changes-{KEY}"
    return app.find(key if present(app, key) else f"{key}-reserved").height


def column_size(app: HostApp) -> int:
    return len(app.find(f"agent-card-{KEY}").content.content.controls)


@pytest.mark.parametrize("name", STATES)
def test_each_state_shows_its_text(name: str) -> None:
    async def scenario() -> None:
        changes, branch, figure = STATES[name]
        app = await card(changes)
        if changes is None:
            assert present(app, f"agent-changes-{KEY}-reserved")
            assert not present(app, f"agent-changes-{KEY}")
            return
        assert row_texts(app) == (branch, figure)

    run(scenario())


def test_calculating_is_never_shown_as_clean() -> None:
    async def scenario() -> None:
        for name in ("none", "calculating"):
            app = await card(STATES[name][0])
            assert "Clean" not in "".join(row_texts(app))
        assert row_texts(await card(STATES["calculating"][0]))[1] == "Calculating…"

    run(scenario())


def test_the_row_has_the_same_reserved_height_and_the_card_the_same_shape_in_every_state() -> None:
    async def scenario() -> None:
        heights, sizes = set(), set()
        for name in STATES:
            app = await card(STATES[name][0])
            heights.add(row_height(app))
            sizes.add(column_size(app))
        assert heights == {T.MIN_TOUCH} and len(sizes) == 1

    run(scenario())


def test_a_card_without_the_changes_callback_has_no_row() -> None:
    async def scenario() -> None:
        plain = await card(STATES["dirty"][0], on_changes=None)
        wired = await card(STATES["dirty"][0])
        assert present(wired, f"agent-changes-{KEY}")
        assert not present(plain, f"agent-changes-{KEY}")
        assert not present(plain, f"agent-changes-{KEY}-reserved")

    run(scenario())


def test_the_dirty_dot_shows_only_when_something_is_uncommitted_and_has_words_in_the_label() -> (
    None
):
    async def scenario() -> None:
        dirty, clean = await card(STATES["dirty"][0]), await card(STATES["clean"][0])
        assert dirty_dot(dirty) and not dirty_dot(clean)
        assert "uncommitted changes" in dirty.find(f"agent-changes-{KEY}").tooltip
        assert "no uncommitted changes" in clean.find(f"agent-changes-{KEY}").tooltip
        for name in ("dirty_no_untracked", "unknown_counts", "conflict", "detached"):
            assert dirty_dot(await card(STATES[name][0]))

    run(scenario())


def test_the_share_blocks_split_added_and_removed_lines() -> None:
    async def scenario() -> None:
        app = await card(STATES["dirty"][0])
        blocks = app.find(f"agent-changes-{KEY}-blocks").controls
        colours = [block.bgcolor for block in blocks]
        assert colours == [T.DIFF_ADD_SIGN] * 4 + [T.DIFF_DELETE_SIGN]
        assert not present(await card(STATES["clean"][0]), f"agent-changes-{KEY}-blocks")
        assert present(app, f"agent-changes-{KEY}-open")

    run(scenario())


def test_the_accessible_label_carries_the_full_text() -> None:
    async def scenario() -> None:
        app = await card(STATES["dirty"][0])
        label = app.find(f"agent-changes-{KEY}").tooltip
        for part in (
            "branch feature",
            "uncommitted changes",
            "12 lines added",
            "3 lines removed",
            "2 untracked files",
            "Open changes",
        ):
            assert part in label, (part, label)
        ahead = (await card(STATES["clean_ahead"][0])).find(f"agent-changes-{KEY}").tooltip
        assert "3 commits ahead of origin/main" in ahead
        detached = (await card(STATES["detached"][0])).find(f"agent-changes-{KEY}").tooltip
        assert "detached at abc1234" in detached
        shared = (await card(STATES["shared"][0])).find(f"agent-changes-{KEY}").tooltip
        assert "shared checkout" in shared
        assert (
            "calculating"
            in (await card(STATES["calculating"][0])).find(f"agent-changes-{KEY}").tooltip
        )

    run(scenario())


def test_every_figure_carries_a_sign_and_colour_is_only_an_addition() -> None:
    async def scenario() -> None:
        app = await card(STATES["dirty"][0])
        spans = app.find(f"agent-changes-{KEY}-figure").spans
        assert (spans[0].text, spans[0].style.color) == ("+12", T.DIFF_ADD_SIGN)
        assert (spans[1].text, spans[1].style.color) == (" −3", T.DIFF_DELETE_SIGN)
        blocked = await card(STATES["dirty"][0], status="blocked")
        colours = {s.style.color for s in blocked.find(f"agent-changes-{KEY}-figure").spans}
        assert colours == {T.BLOCKED_SMALL_TEXT}  # readable on the orange card

    run(scenario())


def test_the_row_is_a_48dp_button_and_taps_go_to_on_changes_not_the_terminal() -> None:
    async def scenario() -> None:
        opened: list[str] = []
        selected: list[str] = []
        app = await card(
            STATES["dirty"][0],
            on_changes=opened.append,
            on_select=selected.append,
        )
        button = app.find(f"agent-changes-{KEY}")
        assert isinstance(button, ft.TextButton) and button.height == T.MIN_TOUCH
        await app.set_props_and_fire(f"agent-changes-{KEY}", {}, "click")
        assert (opened, selected) == ([KEY], [])
        await app.set_props_and_fire(f"agent-card-{KEY}", {}, "click")
        assert (opened, selected) == ([KEY], [KEY])

    run(scenario())


def test_an_empty_row_is_not_tappable() -> None:
    async def scenario() -> None:
        opened: list[str] = []
        app = await card(None, on_changes=opened.append)
        reserved = app.find(f"agent-changes-{KEY}-reserved")
        assert not isinstance(reserved, ft.TextButton) and reserved.height == T.MIN_TOUCH
        assert opened == []

    run(scenario())


# -- the list: wiring and the card cache ---------------------------------------------------------


def agent_row(changes: WorktreeSummary | None = None) -> AgentRow:
    return AgentRow(
        key=KEY,
        title="web",
        subtitle="tool-a · web · 2 min ago",
        status=AgentStatus.WORKING,
        focused=False,
        host_id="home-mac",
        workspace_id="w1",
        agent="tool-a",
        changes=changes,
    )


def test_the_list_passes_each_rows_summary_and_the_tap_to_its_card() -> None:
    async def scenario() -> None:
        opened: list[str] = []

        @ft.component
        def Host() -> Any:
            return AgentsList(
                rows=(agent_row(STATES["dirty"][0]),),
                selected="",
                on_select=lambda key: None,
                on_start_agent=lambda: None,
                on_changes=opened.append,
            )

        app = await render_component(Host)
        assert row_texts(app) == ("feature", "+12 −3")
        await app.set_props_and_fire(f"agent-changes-{KEY}", {}, "click")
        assert opened == [KEY]

    run(scenario())


@pytest.mark.parametrize("dark", [True, False])
def test_the_list_shows_no_row_until_it_is_given_the_callback(dark: bool) -> None:
    async def scenario() -> None:
        @ft.component
        def Host() -> Any:
            return AgentsList(
                rows=(agent_row(STATES["dirty"][0]),),
                selected="",
                on_select=lambda key: None,
                on_start_agent=lambda: None,
                dark=dark,
            )

        app = await render_component(Host)
        assert not present(app, f"agent-changes-{KEY}")

    run(scenario())


def test_a_card_is_rebuilt_only_when_the_revision_or_state_changes() -> None:
    setter: list[Any] = []

    async def scenario() -> None:
        @ft.component
        def Host() -> Any:
            row, set_row = ft.use_state(agent_row(summary(revision="rev-1", as_of_ms=1)))
            setter[:] = [set_row]
            return AgentsList(
                rows=(row,),
                selected="",
                on_select=lambda key: None,
                on_start_agent=lambda: None,
                on_changes=lambda key: None,
            )

        app = await render_component(Host)
        first = app.find(f"agent-{KEY}")
        base = summary(revision="rev-1", as_of_ms=1)

        setter[0](agent_row(replace(base, as_of_ms=99)))  # a refresh with nothing new
        await app.settle()
        assert app.find(f"agent-{KEY}") is first

        setter[0](agent_row(replace(base, as_of_ms=100, partial=False)))
        await app.settle()
        assert app.find(f"agent-{KEY}") is first

        setter[0](
            agent_row(
                summary(revision="rev-2", working=DIRTY, additions=1, deletions=1, as_of_ms=101)
            )
        )
        await app.settle()
        changed = app.find(f"agent-{KEY}")
        assert changed is not first
        assert row_texts(app) == ("feature", "+1 −1")

        setter[0](agent_row(replace(base, revision="rev-2", state="calculating")))
        await app.settle()
        assert app.find(f"agent-{KEY}") is not changed
        assert row_texts(app)[1] == "Calculating…"

    run(scenario())
