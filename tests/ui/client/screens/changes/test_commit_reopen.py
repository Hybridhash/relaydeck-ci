"""A commit whose id expired is opened again only by the graph refresh that was asked for it."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

import relaydeck.client.features.changes.model as m
from relaydeck.client.features.changes.commit_model import open_commit
from relaydeck.client.features.changes.repository import ChangesRepository
from relaydeck.client.ports.transport import TransportError
from relaydeck.client.screens.changes.controller import _Runner
from tests.support.commit_graph import REVISION, graph_model
from tests.support.host_harness import run
from tests.ui.git.gateway import Gateway
from tests.ui.git.harness import click, files, graph, present, row_key, until

EXPIRED = TransportError("forbidden", "expired", False, {"reason": "expired"})
LOST = "This commit is no longer in the first page of the graph. Find it below or refresh."


class HeldGraph(Gateway):
    """Answers the graph refreshes only when told to, so the reader can move meanwhile."""

    started: asyncio.Event
    release: asyncio.Event

    async def git_graph(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if self.count("graph"):
            self.started.set()
            await self.release.wait()
        return await super().git_graph(*args, **kwargs)


def held(**kwargs: Any) -> HeldGraph:
    gateway = HeldGraph(files(), **kwargs)
    gateway.started, gateway.release = asyncio.Event(), asyncio.Event()
    return gateway


async def expired_runner(gateway: HeldGraph) -> _Runner:
    """A runner whose open commit has just failed with an expired id; the refresh is held."""
    gateway.calls.append(("graph", None, "all"))  # the first graph was read
    gateway.fail_once["commit_changes"] = EXPIRED
    runner = _Runner(ChangesRepository(gateway), "host", lambda: None)  # type: ignore[arg-type]
    # The model the screen would have after reading this gateway's Branch list.
    model = replace(graph_model(), base_name="origin/main", base_revision="b" * 40)
    runner.model, request = open_commit(model, "all", REVISION, f"{1:040x}", 1)
    assert request is not None
    await runner._fetch(request)
    await asyncio.wait_for(gateway.started.wait(), 2)
    return runner


async def finish(runner: _Runner, gateway: HeldGraph) -> None:
    gateway.release.set()
    while runner.tasks:
        await asyncio.gather(*tuple(runner.tasks))


def runner_scenario(
    change: Callable[[_Runner], None], expect_open: bool, *, lost: bool = False
) -> None:
    async def scenario() -> None:
        gateway = held()
        runner = await expired_runner(gateway)
        try:
            change(runner)
            await finish(runner, gateway)
            assert (runner.model.commit is not None) is expect_open
            assert runner.model.commit_lost is lost
            assert gateway.count("commit_changes") == (2 if expect_open else 1)
        finally:
            runner.stop()

    asyncio.run(scenario())


def test_the_matching_refresh_reopens_the_commit_while_the_reader_stays_on_the_graph() -> None:
    runner_scenario(lambda runner: None, expect_open=True)


def test_leaving_the_graph_before_the_refresh_answers_cancels_the_reopen() -> None:
    def leave(runner: _Runner) -> None:
        runner.apply(m.set_view(runner.model, "files"))

    runner_scenario(leave, expect_open=False)


def test_changing_the_chip_before_the_refresh_answers_cancels_the_reopen() -> None:
    runner_scenario(lambda r: r.apply(m.set_graph_mode(r.model, "branch")), expect_open=False)


def test_changing_the_scope_before_the_refresh_answers_cancels_the_reopen() -> None:
    runner_scenario(lambda r: r.apply(m.set_scope(r.model, "working")), expect_open=False)


def test_changing_the_base_before_the_refresh_answers_cancels_the_reopen() -> None:
    runner_scenario(lambda r: r.apply(replace(r.model, base_name="other")), expect_open=False)


def test_a_newer_refresh_supersedes_the_one_a_reopen_waits_for() -> None:
    def again(runner: _Runner) -> None:
        runner.reload()

    runner_scenario(again, expect_open=False)


def test_a_response_to_another_request_does_not_consume_the_intent() -> None:
    async def scenario() -> None:
        gateway = held()
        runner = await expired_runner(gateway)
        try:
            runner.commits.graph_arrived(runner.model.graph_load.token + 99)
            await finish(runner, gateway)
            assert runner.model.commit is not None and gateway.count("commit_changes") == 2
        finally:
            runner.stop()

    asyncio.run(scenario())


def rendered(scenario: Callable[[Any, HeldGraph], Awaitable[None]], **kwargs: Any) -> None:
    gateway = held()

    async def body() -> None:
        app = await graph(gateway, **kwargs)
        await scenario(app, gateway)

    run(body())


def test_leaving_for_branch_files_during_the_refresh_keeps_that_view_intact() -> None:
    async def scenario(app: Any, gateway: HeldGraph) -> None:
        gateway.fail_once["commit_changes"] = EXPIRED
        await click(app, row_key(gateway, 0))
        await asyncio.wait_for(gateway.started.wait(), 2)
        await click(app, "changes-scope-branch")
        gateway.release.set()
        await app.settle(30)
        assert gateway.count("commit_changes") == 1
        assert present(app, "changes-scope-branch") and present(app, "changes-tabs")
        assert not present(app, "commit-pane") and not present(app, "commit-back")

    rendered(scenario)


def test_a_commit_beyond_the_first_page_is_reported_not_dropped_on_a_wide_screen() -> None:
    async def scenario(app: Any, gateway: HeldGraph) -> None:
        gateway.release.set()
        await click(app, "changes-more-graph")
        await app.wait_for(row_key(gateway, 3))
        gateway.fail_once["commit_changes"] = EXPIRED
        await click(app, row_key(gateway, 3))
        await app.wait_for("commit-lost")
        assert app.find("commit-lost").content.controls[0].value == LOST
        assert not present(app, "commit-pane") and present(app, "changes-graph")
        before = gateway.count("graph")
        await click(app, row_key(gateway, 0))  # another choice replaces the notice
        await app.wait_for("commit-pane")
        assert not present(app, "commit-lost") and gateway.count("graph") == before

    rendered(scenario, width=1376, height=1000, wide=True)


def test_a_commit_beyond_the_first_page_is_reported_above_the_graph_on_a_phone() -> None:
    async def scenario(app: Any, gateway: HeldGraph) -> None:
        gateway.release.set()
        await click(app, "changes-more-graph")
        await app.wait_for(row_key(gateway, 3))
        gateway.fail_once["commit_changes"] = EXPIRED
        await click(app, row_key(gateway, 3))
        await app.wait_for("commit-lost")
        assert present(app, "changes-graph") and not present(app, "commit-pane")
        await click(app, "commit-lost-refresh")
        await until(lambda: gateway.count("graph") == 4)
        await until(lambda: not present(app, "commit-lost"))

    rendered(scenario)


def test_the_notice_goes_when_the_chip_changes() -> None:
    async def scenario(app: Any, gateway: HeldGraph) -> None:
        gateway.release.set()
        await click(app, "changes-more-graph")
        await app.wait_for(row_key(gateway, 3))
        gateway.fail_once["commit_changes"] = EXPIRED
        await click(app, row_key(gateway, 3))
        await app.wait_for("commit-lost")
        await click(app, "graph-mode-branch")
        await until(lambda: not present(app, "commit-lost"))

    rendered(scenario)
