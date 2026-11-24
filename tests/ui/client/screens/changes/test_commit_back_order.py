"""Browser Back and the screens' own Back buttons, in every order, go up one level at a time."""

from __future__ import annotations

from typing import Any

from tests.support.host_harness import run
from tests.ui.git.gateway import Gateway
from tests.ui.git.harness import click, files, mount, present, row_key, until


async def to_graph(gateway: Gateway, **kwargs: Any) -> tuple[Any, dict[str, Any]]:
    app, control = await mount(gateway, **kwargs)
    await app.wait_for("changes-files")
    await click(app, "changes-view-graph")
    await app.wait_for(row_key(gateway, 0))
    return app, control


async def open_commit(app: Any, gateway: Gateway) -> None:
    await click(app, row_key(gateway, 0))
    await app.wait_for("commit-file-0")


async def open_file(app: Any) -> None:
    await click(app, "commit-file-0")
    await app.wait_for("diff-rows")


async def at_file(app: Any) -> None:
    await until(lambda: present(app, "diff-rows"))


async def at_commit(app: Any) -> None:
    await until(lambda: present(app, "commit-pane") and not present(app, "diff-rows"))


async def at_graph(app: Any) -> None:
    await until(lambda: present(app, "changes-graph") and not present(app, "commit-pane"))


async def top_back_from_file(app: Any) -> None:
    await click(app, "changes-files-back")
    await at_commit(app)


async def top_back_from_commit(app: Any) -> None:
    await click(app, "commit-back")
    await at_graph(app)


def test_two_browser_backs_from_a_file_go_to_the_commit_then_the_graph() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app, control = await to_graph(gateway, width=390, height=844)
        await open_commit(app, gateway)
        await open_file(app)
        control["back"]()
        await at_commit(app)
        control["back"]()
        await at_graph(app)

    run(scenario())


def test_a_file_opened_again_after_the_top_back_goes_back_to_the_commit() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app, control = await to_graph(gateway, width=390, height=844)
        await open_commit(app, gateway)
        await open_file(app)
        await top_back_from_file(app)
        await open_file(app)
        control["back"]()
        await at_commit(app)
        control["back"]()
        await at_graph(app)

    run(scenario())


def test_browser_back_after_the_top_back_from_a_file_goes_to_the_graph() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app, control = await to_graph(gateway, width=390, height=844)
        await open_commit(app, gateway)
        await open_file(app)
        await top_back_from_file(app)
        control["back"]()
        await at_graph(app)
        control["back"]()
        await app.wait_for("console")

    run(scenario())


def test_both_top_backs_then_in_again_and_one_browser_back_returns_to_the_commit() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app, control = await to_graph(gateway, width=390, height=844)
        await open_commit(app, gateway)
        await open_file(app)
        await top_back_from_file(app)
        await top_back_from_commit(app)
        await open_commit(app, gateway)
        await open_file(app)
        control["back"]()
        await at_commit(app)
        control["back"]()
        await at_graph(app)

    run(scenario())


def test_beside_the_graph_opening_a_commit_and_its_file_adds_no_history_and_back_leaves() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app, control = await to_graph(gateway, width=1376, height=1000, wide=True)
        await click(app, row_key(gateway, 0))
        await app.wait_for("diff-rows")
        await app.settle(20)
        assert [e[2] for e in control["history"]] == ["", "changes"]
        control["back"]()
        await app.wait_for("console")

    run(scenario())
