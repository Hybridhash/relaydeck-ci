"""ChangesModel: large files, reading position, commits and summaries."""

from __future__ import annotations

from dataclasses import fields, replace

import pytest

import relaydeck.client.features.changes.model as m
from relaydeck.client.features.changes.model import ChangesModel, ChangesPage
from relaydeck.contracts.git import BranchCounts, FileDiff, WorktreeSummary
from tests.support.changes_model import diff_page, entry, loaded, stream, summary
from tests.support.graph_pages import graph_commit as commit
from tests.support.graph_pages import graph_page


def gate_page(file_id: str = "a") -> FileDiff:
    return FileDiff(file_id=file_id, snapshot="snap-1", render="load_required", hunks=())


def opened(file_id: str = "a") -> ChangesModel:
    """File `file_id` selected with its first (gate) page requested but not yet received."""
    model, request = m.request_diff(m.select_file(loaded(file_id), file_id))
    assert request is not None and request.page == 0 and not request.load
    return model


def gated() -> ChangesModel:
    model = opened()
    return m.receive_diff(model, model.diff_load.token, gate_page(), 0)


def test_load_diff_replaces_the_gate_page_and_asks_for_the_real_one() -> None:
    model = gated()
    assert m.resident_rows(model) == 0 and m.request_diff(model)[1] is None  # complete: the gate
    model, request = m.load_diff(model)
    assert request is not None and (request.page, request.cursor, request.load) == (0, None, True)
    assert model.diff is not None and model.diff.pages == () and model.diff.loaded


def test_load_consent_rides_every_later_page_of_the_file() -> None:
    model, request = m.load_diff(gated())
    assert request is not None
    model = m.receive_diff(model, request.token, diff_page("a", 3, "c1"), 0)
    model, request = m.request_diff(model)
    assert request is not None and request.load and request.cursor == "c1"
    model = m.receive_diff(model, request.token, diff_page("a", 3, "c2", start=4), 1)
    assert model.diff is not None and model.diff.loaded and model.diff.last_page == 1


def test_load_consent_is_forgotten_with_the_file_and_with_the_snapshot() -> None:
    model, request = m.load_diff(gated())
    assert request is not None
    model = m.receive_diff(model, request.token, diff_page("a", 3), 0)
    other = m.select_file(loaded("a", "b"), "b")  # another file starts without it
    _, first = m.request_diff(other)
    assert first is not None and not first.load
    assert m.begin_refresh(model).diff is None  # a new snapshot asks again


def test_load_diff_does_nothing_without_a_gate() -> None:
    started = opened()
    model = m.receive_diff(started, started.diff_load.token, diff_page("a", 3), 0)
    assert m.load_diff(model) == (model, None)
    assert m.load_diff(loaded("a")) == (loaded("a"), None)


def streamed(pages: int = 3, rows: int = 500) -> ChangesModel:
    return stream(pages, rows)


def test_the_cursor_chain_travels_with_the_reading_position() -> None:
    model = m.set_anchor(streamed(3, 500), m.Anchor("a", 2, 0, 0, 40))  # only page 2 is resident
    kept = m.anchor_for(model, "a")
    assert kept is not None and kept.cursors == (None, "c1", "c2")


def test_a_file_reopens_at_the_page_the_reader_left_using_the_kept_cursors() -> None:
    left = m.set_anchor(streamed(3, 500), m.Anchor("a", 2, 0, 0, 40))
    away = m.select_file(replace(loaded("a", "b"), anchors=left.anchors), "b")
    back, request = m.request_diff(m.select_file(away, "a"))
    assert request is not None and (request.page, request.cursor) == (2, "c2")
    back = m.receive_diff(back, request.token, diff_page("a", 500, None, start=1001), 2)
    assert back.diff is not None and [i for i, _ in back.diff.pages] == [2]
    assert m.earlier_page(back) == 1  # the pages before are one tap away
    back, earlier = m.request_diff(back, page=1)
    assert earlier is not None and earlier.cursor == "c1"


def test_a_file_without_a_position_opens_at_its_first_page() -> None:
    model, request = m.request_diff(m.select_file(loaded("a"), "a"))
    assert request is not None and (request.page, request.cursor) == (0, None)
    assert m.earlier_page(model) is None


def test_reading_on_past_a_dropped_page_continues_at_the_start_of_the_new_one() -> None:
    model = m.set_anchor(streamed(1, 500), m.Anchor("a", 0, 0, 0, 499))
    model, request = m.request_diff(replace(model, diff=_continued(model)))
    assert request is not None and request.page == 1
    model = m.receive_diff(model, request.token, diff_page("a", 500, None, start=501), 1)
    kept = m.anchor_for(model, "a")
    assert kept is not None and (kept.page, kept.row) == (1, -1)  # the new page's first row


def _continued(model: ChangesModel) -> m.DiffWindow:
    """The window of a two-page file whose first page has been received."""
    window = model.diff
    assert window is not None
    return replace(window, cursors=(None, "c1"), end=None)


def test_reading_back_past_a_dropped_page_continues_at_the_end_of_the_earlier_one() -> None:
    model = m.set_anchor(streamed(2, 500), m.Anchor("a", 1, 0, 0, 3))
    model, request = m.request_diff(model, page=0)
    assert request is not None
    model = m.receive_diff(model, request.token, diff_page("a", 500, "c1"), 0)
    edge = m.anchor_for(model, "a")
    assert edge is not None and (edge.page, edge.row) == (0, 499)


def test_a_refresh_finds_the_readers_file_again_by_path() -> None:
    model = m.select_file(loaded("a", "b"), "b")
    refreshed = m.begin_refresh(model)
    assert refreshed.selected_file == "b" and refreshed.files == ()
    refreshed, request = m.request_files(refreshed)
    assert request is not None
    renamed = ChangesPage(
        snapshot="snap-2", files=tuple(replace(entry(i), file_id=f"new-{i}") for i in ("a", "b"))
    )
    after = m.receive_files(refreshed, request.token, renamed)
    assert after.selected_file == "new-b" and after.selected_path == "dir/b.txt"
    gone = ChangesPage(snapshot="snap-2", files=(replace(entry("a"), file_id="new-a"),))
    refreshed, request = m.request_files(m.begin_refresh(model))
    assert request is not None
    assert m.receive_files(refreshed, request.token, gone).selected_file is None


def test_a_summary_does_not_rewrite_the_time_or_partial_mark_of_the_list_on_screen() -> None:
    model = loaded("a", as_of_ms=111, partial=False)
    updated = m.apply_summary(model, summary(as_of_ms=999, partial=True, revision="snap-2"))
    assert (updated.as_of_ms, updated.partial) == (111, False) and updated.updated_available
    fresh = m.apply_summary(m.select(ChangesModel(), "agent-1", "wt-1"), summary(as_of_ms=999))
    assert fresh.as_of_ms == 999


def test_a_summary_without_a_worktree_has_no_worktree_id() -> None:
    model = m.apply_summary(
        m.select(ChangesModel(), "agent-1", None), summary(state="no_repo", worktree_id="")
    )
    assert model.worktree_id is None and model.condition == "no_repo"


def test_a_first_list_that_fails_for_an_unknown_reason_is_a_state_not_calculating() -> None:
    model, request = m.request_files(m.select(ChangesModel(), "agent-1", "wt-1"))
    assert request is not None
    failed = m.fail(model, "files", request.token, "internal")
    assert failed.condition == "unavailable" and failed.files_load.error == "internal"


def test_graph_load_in_pages_and_only_in_branch_scope() -> None:
    working = loaded("a")
    assert m.set_view(working, "graph") == (working, None)
    model = loaded("a", scope="branch")
    model, request = m.set_view(model, "graph")
    assert request is not None and request.slot == "graph" and model.view == "graph"
    model = m.receive_graph(model, request.token, graph_page((commit(1), commit(2)), next="c1"))
    model, more = m.request_graph(model, more=True)
    assert more is not None and more.cursor == "c1"
    model = m.receive_graph(model, more.token, graph_page((commit(3),)), more=True)
    assert [c.subject for c in model.graph.commits] == ["c1", "c2", "c3"]
    assert m.request_graph(model, more=True)[1] is None
    back, again = m.set_view(model, "files")
    assert back.view == "files" and again is None
    assert m.set_view(back, "graph")[1] is None  # already loaded


def test_a_late_commit_page_is_dropped_and_a_refresh_forgets_the_graph() -> None:
    model, request = m.set_view(loaded("a", scope="branch"), "graph")
    assert request is not None
    refreshed = m.begin_refresh(model)
    assert m.receive_graph(refreshed, request.token, graph_page((commit(1),))) is refreshed
    assert refreshed.graph.commits == () and refreshed.view == "graph"


def test_a_scope_change_returns_to_the_file_list() -> None:
    model, _ = m.set_view(loaded("a", scope="branch"), "graph")
    assert m.set_scope(model, "working").view == "files"


# -- the update baseline, a file far down the list, paging both ways ----------------------------


def branch_summary(branch_revision: str | None, **over: object) -> WorktreeSummary:
    return summary(branch_scope=BranchCounts(revision=branch_revision), **over)


def test_a_branch_list_is_compared_with_the_branch_revision_it_was_loaded_under() -> None:
    model = loaded("a", scope="branch")  # baseline "brev-1"; the Working revision is no concern
    for revision in ("rev-1", "rev-2", "rev-3"):  # the worktree moves, the comparison does not
        model = m.apply_summary(model, branch_summary("brev-1", revision=revision))
    assert not model.updated_available and model.revision_baseline == "brev-1"
    moved = m.apply_summary(model, branch_summary("brev-2"))
    assert moved.updated_available and moved.revision_seen == "brev-2"


def test_a_working_list_ignores_the_branch_revision() -> None:
    model = loaded("a")
    model = m.apply_summary(model, branch_summary("brev-9"))
    assert not model.updated_available
    assert m.apply_summary(model, branch_summary("brev-9", revision="rev-2")).updated_available


def test_a_base_change_alone_raises_updated_and_leaves_the_header_on_the_loaded_snapshot() -> None:
    model = loaded("a", scope="branch", branch="feature", base_name="origin/main")
    after = m.apply_summary(model, branch_summary("brev-2", base_name="origin/develop"))
    assert (
        after.updated_available and after.base_name == "origin/main" and after.branch == "feature"
    )
    refreshed = m.begin_refresh(after)
    assert refreshed.base_name == "origin/main" and refreshed.revision_baseline is None


def test_a_base_that_disappears_raises_updated_without_blanking_the_list() -> None:
    model = loaded("a", scope="branch")
    after = m.apply_summary(model, branch_summary(None, base_name=None))
    assert after.updated_available and after.condition == "ok" and after.files == model.files


def test_a_scope_change_uses_the_revision_of_the_new_scope() -> None:
    model = m.apply_summary(loaded("a"), branch_summary("brev-4", revision="rev-4"))
    assert model.revision_seen == "rev-4"
    assert m.set_scope(model, "branch").revision_seen == "brev-4"


def test_a_summary_after_the_list_becomes_the_baseline_and_a_later_one_moves_from_it() -> None:
    fresh = m.select(ChangesModel(), "agent-1", "wt-1")
    fresh, request = m.request_files(fresh)
    assert request is not None
    model = m.receive_files(fresh, request.token, ChangesPage(snapshot="s", files=(entry("a"),)))
    assert model.revision_baseline is None
    model = m.apply_summary(model, summary(revision="rev-5"))
    assert model.revision_baseline == "rev-5" and not model.updated_available
    assert m.apply_summary(model, summary(revision="rev-6")).updated_available


def test_a_refresh_takes_a_new_baseline_and_a_scope_change_remembers_the_revision() -> None:
    model = m.apply_summary(loaded("a"), summary(revision="rev-2"))
    refreshed = m.begin_refresh(model)
    assert refreshed.revision_baseline is None and not refreshed.updated_available
    refreshed, request = m.request_files(refreshed)
    assert request is not None
    again = m.receive_files(refreshed, request.token, ChangesPage(snapshot="s2", files=()))
    assert again.revision_baseline == "rev-2" and not again.updated_available
    assert m.set_scope(model, "branch").working_seen == "rev-2"


def failed_poll(worktree_id: str = "wt-1") -> m.FailedPoll:
    values = {f.name: getattr(summary(), f.name) for f in fields(summary())}
    return m.FailedPoll(
        **{**values, "state": "unavailable", "revision": "", "worktree_id": worktree_id}
    )


def service_state(state: str, worktree_id: str = "") -> WorktreeSummary:
    """A summary the way the service reports a state: no revision, no counts."""
    return summary(
        state=state,
        worktree_id=worktree_id,
        revision="",
        as_of_ms=0,
        branch=None,
        short_oid=None,
        base_name=None,
    )


def test_a_failed_poll_changes_nothing_on_screen_and_a_blank_screen_says_unavailable() -> None:
    shown = loaded("a")
    assert m.apply_summary(shown, failed_poll()) is shown
    waiting = m.apply_summary(m.select(ChangesModel(), "agent-1", "wt-1"), failed_poll())
    assert waiting.condition == "unavailable" and waiting.worktree_id == "wt-1"
    nothing = m.apply_summary(m.select(ChangesModel(), "agent-1", None), failed_poll(""))
    assert nothing.condition == "unavailable"


@pytest.mark.parametrize(
    "state", ["no_repo", "folder_gone", "unsupported_config", "git_unavailable", "unavailable"]
)
def test_a_state_the_server_reports_keeps_its_own_condition_without_a_revision(state: str) -> None:
    blank = m.apply_summary(m.select(ChangesModel(), "agent-1", None), service_state(state))
    assert blank.condition == state and blank.worktree_id is None
    assert blank.revision_seen is None  # an empty revision is not a revision
    waiting = m.apply_summary(
        m.select(ChangesModel(), "agent-1", "wt-1"), service_state(state, "wt-1")
    )
    assert waiting.condition == state
    again = m.apply_summary(blank, summary())  # the folder is readable again
    assert again.condition == "ok" and again.worktree_id == "wt-1"


def test_a_service_calculating_reply_is_calculating_not_a_failure() -> None:
    model = m.apply_summary(m.select(ChangesModel(), "agent-1", None), service_state("calculating"))
    assert model.condition == "calculating"


def page(*names: str, snapshot: str = "snap-1", next: str | None = None) -> ChangesPage:
    files = tuple(replace(entry(n), path=f"dir/{n}.txt") for n in names)
    return ChangesPage(snapshot=snapshot, files=files, next=next)


def refreshing(selected: str) -> ChangesModel:
    model = m.select_file(loaded("a", "far"), "far") if selected == "far" else loaded("a")
    return m.begin_refresh(model)


def test_a_file_beyond_the_first_page_is_looked_for_as_pages_arrive() -> None:
    model, request = m.request_files(refreshing("far"))
    assert request is not None
    model = m.receive_files(model, request.token, page("a", "b", next="c1", snapshot="snap-2"))
    assert (
        m.reselecting(model)
        and model.selected_file is None
        and model.selected_path == "dir/far.txt"
    )
    model, request = m.request_files(model, more=True)
    assert request is not None and request.cursor == "c1"
    model = m.receive_files(
        model, request.token, page("c", next="c2", snapshot="snap-2"), more=True
    )
    assert m.reselecting(model)
    model, request = m.request_files(model, more=True)
    assert request is not None
    model = m.receive_files(
        model, request.token, page("far", next=None, snapshot="snap-2"), more=True
    )
    assert not m.reselecting(model) and model.selected_file == "far"


def test_the_search_for_a_missing_file_ends_with_the_list_or_on_failure() -> None:
    model, request = m.request_files(refreshing("far"))
    assert request is not None
    model = m.receive_files(model, request.token, page("a", next="c1", snapshot="snap-2"))
    model, request = m.request_files(model, more=True)
    assert request is not None
    ended = m.receive_files(model, request.token, page("b", snapshot="snap-2"), more=True)
    assert not m.reselecting(ended) and ended.selected_path is None
    failed = m.fail(model, "files", request.token, "internal")
    assert not m.reselecting(failed)


def test_the_search_for_a_missing_file_stops_after_a_bounded_number_of_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(m, "MAX_RESELECT_FILES", 3)
    model, request = m.request_files(refreshing("far"))
    assert request is not None
    model = m.receive_files(model, request.token, page("a", "b", next="c1", snapshot="snap-2"))
    model, request = m.request_files(model, more=True)
    assert request is not None
    model = m.receive_files(
        model, request.token, page("c", next="c2", snapshot="snap-2"), more=True
    )
    assert not m.reselecting(model)


def test_forward_back_forward_follows_the_page_held_not_the_furthest_seen() -> None:
    model = stream(3, 500)  # pages 0, 1, 2 loaded in turn; only page 2 is held
    assert model.diff is not None and model.diff.complete
    model, back = m.request_diff(model, page=m.earlier_page(model))
    assert back is not None and back.page == 1
    model = m.receive_diff(model, back.token, diff_page("a", 500, "c2", start=501), 1)
    assert model.diff is not None and not model.diff.complete  # Show more is offered again
    model, forward = m.request_diff(model)
    assert forward is not None and (forward.page, forward.cursor) == (2, "c2")
    model = m.receive_diff(model, forward.token, diff_page("a", 500, None, start=1001), 2)
    assert model.diff is not None and model.diff.complete

    model, back = m.request_diff(model, page=1)
    assert back is not None
    model = m.receive_diff(model, back.token, diff_page("a", 500, "c2", start=501), 1)
    model, back = m.request_diff(model, page=0)
    assert back is not None
    model = m.receive_diff(model, back.token, diff_page("a", 500, "c1"), 0)
    model, forward = m.request_diff(model)  # from page 0 the next one is page 1, not page 3
    assert forward is not None and forward.page == 1
