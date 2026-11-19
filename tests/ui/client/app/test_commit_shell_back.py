"""Back after a reload left a deeper URL behind, with the real root and its route bridge."""

from __future__ import annotations

from relaydeck.client.app.navigation import RouteBridge
from tests.support.host_harness import run
from tests.support.ui.retained_root import _render
from tests.ui.git.harness import Transport, click, present, row_key

KEY = "home-mac:term-1"


def test_back_to_the_url_a_reload_left_closes_the_commit_instead_of_staying_in_it() -> None:
    async def scenario() -> None:
        transport, pushed = Transport(), list[str]()
        bridge = RouteBridge(route=f"/agents/{KEY}/commit-file", width=390, height=844)
        app, _, _, pool, _ = await _render(bridge=bridge, transport=transport, go=pushed.append)
        try:
            await app.wait_for("changes-graph")
            await app.wait_for(row_key(transport.git, 0))
            assert bridge.state.section == "changes"  # the screen corrected itself
            await click(app, row_key(transport.git, 0))
            await app.wait_for("commit-file-0")
            assert pushed == [f"/agents/{KEY}/commit.1"]  # one entry, numbered, no identity
            # The browser's Back: the entry the reload left, which names a deeper section.
            bridge.set_route(f"/agents/{KEY}/commit-file")
            await app.settle(20)
            assert present(app, "changes-graph") and not present(app, "commit-pane")
        finally:
            await pool.shutdown()

    run(scenario())


def test_the_numbers_the_root_pushes_keep_counting_up() -> None:
    async def scenario() -> None:
        transport, pushed = Transport(), list[str]()
        bridge = RouteBridge(route=f"/agents/{KEY}/changes", width=390, height=844)
        app, _, _, pool, _ = await _render(bridge=bridge, transport=transport, go=pushed.append)
        try:
            await app.wait_for("changes-view-graph")
            await click(app, "changes-view-graph")
            await app.wait_for(row_key(transport.git, 0))
            await click(app, row_key(transport.git, 0))
            await app.wait_for("commit-file-0")
            await click(app, "commit-file-0")
            await app.wait_for("diff-rows")
            assert pushed == [f"/agents/{KEY}/commit.1", f"/agents/{KEY}/commit-file.2"]
        finally:
            await pool.shutdown()

    run(scenario())


def test_reopening_a_commit_after_back_to_the_reload_entry_still_pushes_an_entry() -> None:
    async def scenario() -> None:
        transport, pushed = Transport(), list[str]()
        bridge = RouteBridge(route=f"/agents/{KEY}/commit-file", width=390, height=844)
        app, _, _, pool, _ = await _render(bridge=bridge, transport=transport, go=pushed.append)
        try:
            await app.wait_for("changes-graph")
            await app.wait_for(row_key(transport.git, 0))
            await click(app, row_key(transport.git, 0))
            await app.wait_for("commit-file-0")
            bridge.set_route(f"/agents/{KEY}/commit-file")  # Back to the entry the reload left
            await app.settle(20)
            assert present(app, "changes-graph") and not present(app, "commit-pane")
            assert bridge.state.section == "changes"  # the level shown, not the URL's
            await click(app, row_key(transport.git, 1))
            await app.wait_for("commit-file-0")
            assert pushed == [f"/agents/{KEY}/commit.1", f"/agents/{KEY}/commit.2"]
            bridge.set_route(f"/agents/{KEY}/commit-file")  # Back from the second commit
            await app.settle(20)
            assert present(app, "changes-graph") and not present(app, "commit-pane")
            bridge.set_route("/agents")  # and from the Graph, the Console
            await app.settle(20)
            assert not present(app, "changes-graph") and bridge.state.selection == ""
        finally:
            await pool.shutdown()

    run(scenario())
