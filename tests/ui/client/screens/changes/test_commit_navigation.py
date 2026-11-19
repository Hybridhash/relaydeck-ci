"""Back from an open commit (or one of its files) returns one level, not to the Console."""

from __future__ import annotations

from typing import Any

from relaydeck.client.models.routing import format_route
from tests.support.host_harness import run
from tests.ui.git.gateway import KEY, Gateway
from tests.ui.git.harness import click, files, mount, present, row_key, until


async def to_graph(gateway: Gateway, **kwargs: Any) -> tuple[Any, dict[str, Any]]:
    app, control = await mount(gateway, **kwargs)
    await app.wait_for("changes-files")
    await click(app, "changes-view-graph")
    await app.wait_for(row_key(gateway, 0))
    return app, control


def section(control: dict[str, Any]) -> str:
    return str(control["route"]()[2])


def test_browser_back_from_a_commit_returns_to_the_graph_then_to_the_console() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app, control = await to_graph(gateway, width=390, height=844)
        await click(app, "graph-mode-branch")
        await app.wait_for("changes-more-graph")
        await click(app, row_key(gateway, 1))
        await app.wait_for("commit-file-0")
        assert section(control) == "commit"
        assert [e[2] for e in control["history"]] == ["", "changes", "commit"]
        control["back"]()
        await until(lambda: present(app, "changes-graph"))
        assert not present(app, "commit-pane") and section(control) == "changes"
        # Same chip and selection after the step back.
        assert app.find("graph-mode-branch").data["selected"]
        assert app.find(row_key(gateway, 1)).data["selected"]
        control["back"]()
        await app.wait_for("console")

    run(scenario())


def test_browser_back_from_a_file_of_a_commit_returns_to_its_files_then_to_the_graph() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app, control = await to_graph(gateway, width=390, height=844)
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-file-0")
        await click(app, "commit-file-0")
        await app.wait_for("diff-rows")
        assert section(control) == "commit-file"
        control["back"]()
        await app.wait_for("commit-file-0")
        assert not present(app, "diff-rows") and present(app, "commit-pane")
        assert section(control) == "commit"
        control["back"]()
        await until(lambda: present(app, "changes-graph"))
        assert section(control) == "changes"

    run(scenario())


def urls(control: dict[str, Any]) -> list[str]:
    """The browser's history as the sections it holds, oldest first."""
    return [str(entry[2]) for entry in control["history"]]


def test_the_buttons_on_the_screens_push_nothing_and_the_next_browser_back_goes_up_once() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app, control = await to_graph(gateway, width=390, height=844)
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-file-0")
        await click(app, "commit-file-0")
        await app.wait_for("diff-rows")
        assert urls(control) == ["", "changes", "commit", "commit-file"]
        await click(app, "changes-files-back")  # the file's own Back: up to the commit's files
        await app.wait_for("commit-file-0")
        assert urls(control) == ["", "changes", "commit", "commit-file"]  # nothing was pushed
        control["back"]()  # one browser Back from the commit's files: the graph
        await until(lambda: present(app, "changes-graph"))
        assert not present(app, "commit-pane") and app.find(row_key(gateway, 0)).data["selected"]
        assert urls(control) == ["", "changes", "commit"]
        control["back"]()  # and one more: the Console
        await app.wait_for("console")
        assert urls(control)[:2] == ["", "changes"]

    run(scenario())


def test_a_commit_closed_with_its_own_button_returns_to_the_console_on_one_browser_back() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app, control = await to_graph(gateway, width=390, height=844)
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-file-0")
        await click(app, "commit-back")
        await until(lambda: present(app, "changes-graph"))
        assert urls(control) == ["", "changes", "commit"]  # no entry added by the button
        control["back"]()
        await app.wait_for("console")

    run(scenario())


def test_back_buttons_and_browser_back_mixed_at_both_levels_always_go_up_one_level() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app, control = await to_graph(gateway, width=390, height=844)
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-file-0")
        await click(app, "commit-file-0")
        await app.wait_for("diff-rows")
        await click(app, "changes-files-back")  # file -> files (button)
        await app.wait_for("commit-file-0")
        await click(app, "commit-back")  # files -> graph (button)
        await until(lambda: present(app, "changes-graph"))
        assert urls(control) == ["", "changes", "commit", "commit-file"]
        # Going in again after the buttons: the entries already there serve the new levels.
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-file-0")
        await click(app, "commit-file-0")
        await app.wait_for("diff-rows")
        assert urls(control) == ["", "changes", "commit", "commit-file"]
        control["back"]()  # file -> files
        await app.wait_for("commit-file-0")
        assert not present(app, "diff-rows") and present(app, "commit-pane")
        control["back"]()  # files -> graph
        await until(lambda: present(app, "changes-graph"))
        assert not present(app, "commit-pane")
        control["back"]()  # graph -> Console
        await app.wait_for("console")

    run(scenario())


def test_the_routes_carry_no_commit_identity() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app, control = await to_graph(gateway, width=390, height=844)
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-file-0")
        await click(app, "commit-file-0")
        await app.wait_for("diff-rows")
        for entry in control["history"]:
            url = format_route(*entry)
            assert f"{0:040x}"[:12] not in url and "cid" not in url and "Commit number" not in url
            assert "file" not in url.replace("commit-file", "")
        assert format_route("agents", KEY, "commit-file") == f"/agents/{KEY}/commit-file"

    run(scenario())


def test_a_reload_on_a_commit_route_opens_the_graph_not_working() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        for level in ("commit", "commit-file"):
            app, control = await mount(gateway, width=390, height=844, section=level)
            await app.wait_for("changes-graph")
            assert app.find("changes-view-graph").data["selected"]
            assert not app.find("changes-scope-working").data["selected"]
            assert not present(app, "commit-pane") and not present(app, "changes-search")
            assert any(c[0] == "graph" for c in gateway.calls)
            await until(lambda c=control: section(c) == "changes")  # type: ignore[misc]

    run(scenario())


def test_a_forward_step_to_a_commit_that_is_not_open_falls_back_to_the_graph() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app, control = await to_graph(gateway, width=390, height=844)
        control["forward"]("agents", KEY, "commit")
        await until(lambda: section(control) == "changes")
        assert present(app, "changes-graph") and not present(app, "commit-pane")
        assert urls(control) == ["", "changes", "commit"]  # replaced in the app, never pushed again

    run(scenario())


def test_beside_the_graph_a_commit_is_not_a_screen_level() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app, control = await to_graph(gateway, width=1376, height=1000, wide=True)
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-pane")
        await app.settle(20)
        assert section(control) == "changes" and len(control["history"]) == 2

    run(scenario())


def test_narrowing_with_a_commit_open_pushes_its_level_and_back_returns_to_the_graph() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app, control = await to_graph(gateway, width=1376, height=1000, wide=True)
        await click(app, row_key(gateway, 0))
        await app.wait_for("diff-rows")
        control["set_wide"](False)
        control["set_viewport"]((390, 844))
        await until(lambda: section(control) == "commit-file")
        assert [e[2] for e in control["history"]] == ["", "changes", "commit-file"]
        control["back"]()  # one entry was pushed for the jump, so one step returns to the graph
        await until(lambda: present(app, "changes-graph"))
        assert not present(app, "commit-pane") and section(control) == "changes"

    run(scenario())


def test_back_to_the_entry_a_reload_left_behind_is_a_back_not_a_forward() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app, control = await mount(gateway, width=390, height=844, section="commit-file")
        await app.wait_for("changes-graph")
        await until(lambda: section(control) == "changes")
        assert urls(control) == ["", "commit-file"]  # the URL still names the old section
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-file-0")
        assert urls(control) == ["", "commit-file", "commit"]
        control["back"]()  # to the entry the reload left, whose section is deeper than the commit's
        await until(lambda: present(app, "changes-graph"))
        assert not present(app, "commit-pane")
        control["back"]()
        await app.wait_for("console")

    run(scenario())


def test_navigating_again_after_a_forward_step_was_replaced_still_goes_back_one_level() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app, control = await to_graph(gateway, width=390, height=844)
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-file-0")
        control["back"]()  # the commit closes
        await until(lambda: present(app, "changes-graph"))
        control["forward_again"]()  # Forward to the commit's entry: nothing is open to go to
        await app.settle(10)
        assert present(app, "changes-graph") and not present(app, "commit-pane")
        await click(app, row_key(gateway, 1))  # navigate again
        await app.wait_for("commit-file-0")
        assert urls(control)[-1] == "commit"
        steps = [entry[3] for entry in control["history"]]
        assert steps == [0, 0, 1, 2]  # the new entry is numbered past the one stepped over
        control["back"]()  # one Back: the graph, not the leftover commit entry
        await until(lambda: present(app, "changes-graph"))
        assert not present(app, "commit-pane")

    run(scenario())


def test_entry_numbers_only_count_and_name_nothing() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app, control = await to_graph(gateway, width=390, height=844)
        await click(app, row_key(gateway, 0))
        await app.wait_for("commit-file-0")
        await click(app, "commit-file-0")
        await app.wait_for("diff-rows")
        pushed = [entry for entry in control["history"] if entry[2] in ("commit", "commit-file")]
        assert [e[3] for e in pushed] == [1, 2]  # a counter, nothing else
        assert [format_route(*e) for e in pushed] == [
            f"/agents/{KEY}/commit.1",
            f"/agents/{KEY}/commit-file.2",
        ]

    run(scenario())
