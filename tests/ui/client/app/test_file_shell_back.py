"""Back from a file that fills a phone screen returns to its list, with the real root and route."""

from __future__ import annotations

from relaydeck.client.app.navigation import RouteBridge
from tests.support.host_harness import run
from tests.support.ui.retained_root import _render
from tests.ui.git.harness import Transport, click, present

KEY = "home-mac:term-1"


def test_back_from_a_phone_file_returns_to_the_list_and_then_leaves_changes() -> None:
    async def scenario() -> None:
        transport, pushed = Transport(), list[str]()
        bridge = RouteBridge(route=f"/agents/{KEY}/changes", width=390, height=844)
        app, _, _, pool, _ = await _render(bridge=bridge, transport=transport, go=pushed.append)
        try:
            await app.wait_for("changes-file-0")
            await click(app, "changes-file-0")
            await app.wait_for("diff-rows")
            assert pushed == [f"/agents/{KEY}/file.1"]  # one numbered entry for the file
            bridge.set_route(f"/agents/{KEY}/changes")  # the browser's Back
            await app.settle(20)
            assert present(app, "changes-files") and not present(app, "diff-rows")
            assert bridge.state.selection == KEY  # still in Changes, not the Console
            bridge.set_route("/agents")  # Back again leaves Changes
            await app.settle(20)
            assert not present(app, "changes-files") and bridge.state.selection == ""
        finally:
            await pool.shutdown()

    run(scenario())


def test_a_file_beside_the_list_adds_no_history_entry() -> None:
    async def scenario() -> None:
        transport, pushed = Transport(), list[str]()
        bridge = RouteBridge(route=f"/agents/{KEY}/changes", width=1280, height=800)
        app, _, _, pool, _ = await _render(bridge=bridge, transport=transport, go=pushed.append)
        try:
            await app.wait_for("changes-file-0")
            await click(app, "changes-file-0")
            await app.wait_for("diff-rows")
            assert pushed == []
        finally:
            await pool.shutdown()

    run(scenario())
