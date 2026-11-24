"""The Changes route: Branch polling, failed polls, reconnects, the wide tree, branch pages,
a reader far down a long list, and paging back and forth through a large file."""

from __future__ import annotations

import asyncio

import relaydeck.client.features.changes.refresh as refresh
from relaydeck.client.ports.transport import TransportError
from tests.support.git_patches import added_patch, two_hunk_patch
from tests.support.host_harness import run
from tests.ui.git.gateway import FileSpec, Gateway
from tests.ui.git.harness import (
    built_rows,
    click,
    message_text,
    mount,
    present,
    scope_is,
    scroll_to,
    small_files,
    until,
)


def test_an_unchanged_branch_view_never_says_updated_however_often_it_is_polled() -> None:
    gateway = Gateway(small_files())

    async def scenario() -> None:
        app, _ = await mount(gateway)
        await app.wait_for("changes-files")
        await click(app, "changes-scope-branch")
        await app.wait_for("changes-view-graph")
        seen = gateway.count("summaries")
        await until(lambda: gateway.count("summaries") >= seen + 4)
        assert scope_is(app, "branch") and not present(app, "changes-updated")
        gateway.edit()
        await app.wait_for("changes-updated")

    run(scenario())


def test_a_first_calculating_reply_then_failed_polls_becomes_unavailable_and_recovers() -> None:
    gateway = Gateway(small_files(), state="calculating")

    async def scenario() -> None:
        app, _ = await mount(gateway)
        await until(lambda: present(app, "changes-state") and "Calculating" in message_text(app))
        gateway.failures["summaries"] = TransportError("unavailable", "down", True, None)
        await until(lambda: "unavailable" in message_text(app))
        await app.wait_for("changes-retry")
        del gateway.failures["summaries"]
        gateway.state = "ok"
        await click(app, "changes-retry")
        await app.wait_for("changes-files", timeout=3.0)

    run(scenario())


def test_failed_polls_keep_the_list_and_say_so_until_one_succeeds() -> None:
    gateway = Gateway(small_files())

    async def scenario() -> None:
        app, _ = await mount(gateway)
        await app.wait_for("changes-files")
        gateway.failures["summaries"] = TransportError("unavailable", "down", True, None)
        await app.wait_for("changes-stale")
        assert present(app, "changes-files") and not present(app, "changes-updated")
        del gateway.failures["summaries"]
        await until(lambda: not present(app, "changes-stale"))

    run(scenario())


def test_coming_back_from_a_lost_connection_checks_at_once(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(refresh, "REVISION_WORKING", 30.0)
    monkeypatch.setattr(refresh, "REVISION_IDLE", 30.0)
    gateway = Gateway(small_files())

    async def scenario() -> None:
        app, control = await mount(gateway, connected=False)
        await app.wait_for("changes-files")
        await until(lambda: gateway.count("summaries") >= 1)
        await asyncio.sleep(0.3)
        quiet = gateway.count("summaries")
        control["set_connected"](True)
        await until(lambda: gateway.count("summaries") > quiet, timeout=3.0)

    run(scenario())


def test_the_wide_layout_shows_a_searchable_directory_tree() -> None:
    files = [
        FileSpec("src/app/a.py", two_hunk_patch("a")),
        FileSpec("src/app/b.py", two_hunk_patch("b")),
        FileSpec("src/lib/deep/c.py", two_hunk_patch("c")),
        FileSpec("README.md", two_hunk_patch("r")),
    ]
    gateway = Gateway(files)

    async def scenario() -> None:
        app, _ = await mount(gateway, wide=True)
        await app.wait_for("changes-files")
        rows = built_rows(app)
        # src, src/app, a, b, src/lib/deep (joined), c, README
        assert [r.key for r in rows] == [
            "changes-dir-0",
            "changes-dir-1",
            "changes-file-0",
            "changes-file-1",
            "changes-dir-4",
            "changes-file-2",
            "changes-file-3",
        ]
        await click(app, "changes-dir-1")  # fold src/app
        assert [r.key for r in built_rows(app)][:3] == [
            "changes-dir-0",
            "changes-dir-1",
            "changes-dir-2",
        ]
        await click(app, "changes-dir-0")  # fold src
        assert [r.key for r in built_rows(app)] == [
            "changes-dir-0",
            "changes-file-3",
        ]
        await app.set_props_and_fire("changes-search", {"value": "c.py"}, "change", "c.py")
        found = built_rows(app)  # a search opens every folder
        assert [r.key for r in found] == ["changes-dir-0", "changes-file-2"]
        await click(app, "changes-file-2")
        await until(lambda: ("diff", "rev-1:2", None, False) in gateway.calls)

    run(scenario())


def test_branches_beyond_the_first_page_can_be_shown_and_chosen() -> None:
    gateway = Gateway(small_files())
    gateway.base_names = ["origin/main", "origin/develop", "origin/third"]
    gateway.base_page = 2

    async def scenario() -> None:
        app, _ = await mount(gateway)
        await app.wait_for("changes-files")
        await click(app, "changes-scope-branch")
        await app.wait_for("changes-change-base")
        await click(app, "changes-change-base")
        await app.wait_for("changes-more-bases")
        assert not present(app, "changes-base-2")
        await click(app, "changes-more-bases")
        await app.wait_for("changes-base-2")
        assert not present(app, "changes-more-bases")
        assert ("bases", "2") in gateway.calls
        await click(app, "changes-base-2")
        await until(lambda: ("set_base", "id:origin/third") in gateway.calls)
        await until(lambda: not present(app, "changes-base-picker"))

    run(scenario())


def test_a_refresh_finds_a_file_that_was_far_down_a_long_list() -> None:
    files = [FileSpec(f"pkg/f{n:03d}.py", two_hunk_patch("f")) for n in range(150)]
    gateway = Gateway(files)

    async def scenario() -> None:
        app, _ = await mount(gateway)
        await app.wait_for("changes-more-files")
        await click(app, "changes-more-files")
        await until(lambda: app.find("changes-files").data["rows"] == 150)
        scroll_to(app, "changes-files", 56.0 * 118)
        await app.wait_for("changes-file-120")
        await click(app, "changes-file-120")
        await app.wait_for("diff-rows")
        gateway.edit()
        await app.wait_for("changes-updated")
        await click(app, "changes-updated")
        await until(lambda: ("diff", "rev-2:120", None, False) in gateway.calls)
        await app.wait_for("diff-rows")  # not dropped back to the list
        assert ("changes", "wt-stable", "working", "100") in gateway.calls[-6:]

    run(scenario())


def test_paging_forward_back_and_forward_again_through_a_large_file() -> None:
    gateway = Gateway([FileSpec("big.py", added_patch("big.py", 1200), status="A")])

    async def scenario() -> None:
        app, _ = await mount(gateway)
        await app.wait_for("changes-files")
        await click(app, "changes-file-0")
        await app.wait_for("diff-load")
        await click(app, "diff-load")
        await app.wait_for("diff-more")
        await click(app, "diff-more")  # page 1
        await app.wait_for("diff-earlier")
        await click(app, "diff-earlier")  # back to page 0
        await until(lambda: not present(app, "diff-earlier"))
        await click(app, "diff-more")  # forward again: the page after the one held
        await until(lambda: gateway.calls.count(("diff", "rev-1:0", "pg:1", True)) == 2)
        assert ("diff", "rev-1:0", "pg:2", True) not in gateway.calls

        await app.wait_for("diff-earlier")
        await click(app, "diff-more")  # the last page
        await until(lambda: ("diff", "rev-1:0", "pg:2", True) in gateway.calls)
        await until(lambda: not present(app, "diff-more"))
        await click(app, "diff-earlier")  # back from the end: forward stays available
        await app.wait_for("diff-more")
        await click(app, "diff-more")
        await until(lambda: gateway.calls.count(("diff", "rev-1:0", "pg:2", True)) == 2)

    run(scenario())


def test_a_base_changed_elsewhere_offers_the_update_and_keeps_the_header_until_refresh() -> None:
    gateway = Gateway(small_files())

    async def scenario() -> None:
        app, _ = await mount(gateway)
        await app.wait_for("changes-files")
        await click(app, "changes-scope-branch")
        await app.wait_for("changes-change-base")
        assert "origin/main" in app.find("changes-base").content.value
        gateway.base = "origin/develop"  # the worktree and HEAD did not move
        await app.wait_for("changes-updated")
        assert "origin/main" in app.find("changes-base").content.value
        assert "develop" not in app.find("changes-base").content.value
        await click(app, "changes-updated")
        await until(lambda: "origin/develop" in app.find("changes-base").content.value)

    run(scenario())


def test_a_state_the_service_reports_later_replaces_a_loaded_list_and_recovers() -> None:
    gateway = Gateway(small_files())

    async def scenario() -> None:
        app, _ = await mount(gateway)
        await app.wait_for("changes-files")
        gateway.state = "unsupported_config"  # reported with no revision, as the service does
        await until(
            lambda: present(app, "changes-state") and "could run programs" in message_text(app)
        )
        gateway.state = "ok"
        await app.wait_for("changes-files", timeout=3.0)

    run(scenario())
