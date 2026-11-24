"""Every Changes state keeps Flutter's flex rule: nothing expands outside a Row or Column."""

from __future__ import annotations

from typing import Any

from relaydeck.client.models.changes_summary import changes_row
from relaydeck.client.widgets.sessions.cards import AgentCard
from tests.support.flet_layout import expand_violations
from tests.support.host_harness import HostApp, render_component, run
from tests.ui.git.gateway import Gateway
from tests.ui.git.harness import click, files, render


def no_violations(app: HostApp) -> None:
    assert expand_violations(app.page) == []


def test_list_branch_commits_and_diff_on_the_phone() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app = await render(gateway)
        await app.wait_for("changes-files")
        no_violations(app)
        await click(app, "changes-file-0")
        await app.wait_for("diff-rows")
        no_violations(app)
        await click(app, "changes-files-back")
        await app.wait_for("changes-files")
        await click(app, "changes-scope-branch")
        await app.settle()
        no_violations(app)
        await click(app, "changes-view-graph")
        await app.settle()
        no_violations(app)
        await click(app, "changes-change-base")
        await app.wait_for("changes-base-picker")
        no_violations(app)

    run(scenario())


def test_the_wide_layout() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app = await render(gateway, wide=True)
        await app.wait_for("changes-files")
        await click(app, "changes-file-0")
        await app.wait_for("diff-rows")
        no_violations(app)

    run(scenario())


def test_the_agent_card_with_its_changes_strip() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        card: Any = await render_component(
            AgentCard,
            "h1:t1",
            "agent",
            "working",
            "tool-a · demo",
            "",
            "",
            False,
            lambda _key: None,
            agent="tool-a",
            changes=changes_row(gateway.summary()),
            on_changes=lambda _key: None,
        )
        no_violations(card)

    run(scenario())
