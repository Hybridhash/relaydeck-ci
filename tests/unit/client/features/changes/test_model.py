"""ChangesModel transitions: tokens, late replies, refresh, eviction and the base picker."""

from __future__ import annotations

from dataclasses import replace

import pytest

import relaydeck.client.features.changes.model as m
from relaydeck.client.features.changes.model import ChangesModel
from relaydeck.contracts.git import BaseCandidate, FileDiff
from tests.support.changes_model import (
    diff_page,
    listing,
    loaded,
    stream,
    summary,
    with_diff,
)

# -- selection and tokens ----------------------------------------------------------------------


def test_a_new_selection_starts_calculating_and_never_clean() -> None:
    model = m.select(ChangesModel(), "agent-1", "wt-1")
    assert model.condition == "calculating" and not m.is_clean(model)
    model, request = m.request_files(model)
    assert request is not None and not m.is_clean(model)


def test_an_empty_list_is_clean_only_after_the_reply_says_so() -> None:
    model = loaded()
    assert model.condition == "ok" and m.is_clean(model)
    assert not m.is_clean(loaded("a"))
    assert not m.is_clean(replace(loaded(), condition="no_repo"))


def test_a_reply_with_an_old_token_is_dropped() -> None:
    model = m.select(ChangesModel(), "agent-1", "wt-1")
    model, first = m.request_files(model)
    model, second = m.request_files(model)  # supersedes the first
    assert first is not None and second is not None and second.token > first.token
    assert m.receive_files(model, first.token, listing("old")) is model
    applied = m.receive_files(model, second.token, listing("new"))
    assert [f.file_id for f in applied.files] == ["new"]
    assert (
        m.receive_files(applied, second.token, listing("again")) is applied
    )  # a reply applies once


def test_a_reply_that_arrives_after_another_agent_was_selected_is_dropped() -> None:
    model = m.select(ChangesModel(), "agent-1", "wt-1")
    model, request = m.request_files(model)
    assert request is not None
    other = m.select(model, "agent-2", "wt-2")
    assert m.receive_files(other, request.token, listing("late")) is other
    assert other.files == () and other.agent_id == "agent-2"


def test_tokens_keep_growing_across_selections_so_an_old_token_can_never_match() -> None:
    model = m.select(ChangesModel(), "agent-1")
    seen = []
    for agent in ("agent-2", "agent-3", "agent-1", "agent-2"):
        model, request = m.request_files(model)
        assert request is not None
        seen.append(request.token)
        model = m.select(model, agent)
    assert seen == sorted(set(seen))


def test_nothing_is_requested_without_a_selected_agent() -> None:
    model, request = m.request_files(ChangesModel())
    assert request is None and model == ChangesModel()


def test_changing_scope_resets_the_list_but_keeps_the_branch_line_and_base() -> None:
    model = loaded("a", "b", branch="feature", base_name="origin/main", base_revision="base-1")
    branch = m.set_scope(model, "branch")
    assert branch.scope == "branch" and branch.files == () and branch.snapshot is None
    assert branch.condition == "calculating"
    assert (branch.branch, branch.base_name, branch.base_revision) == (
        "feature",
        "origin/main",
        "base-1",
    )
    assert m.set_scope(branch, "branch") is branch


def test_a_scope_change_drops_replies_requested_under_the_old_scope() -> None:
    model = m.select(ChangesModel(), "agent-1", "wt-1")
    model, request = m.request_files(model)
    assert request is not None
    other = m.set_scope(model, "branch")
    assert m.receive_files(other, request.token, listing("a")) is other


# -- list pages --------------------------------------------------------------------------------


def test_the_first_page_fills_the_header_and_the_list() -> None:
    model = loaded(
        "a",
        "b",
        next="c1",
        branch="feature",
        short_oid="abc1234",
        base_name="origin/main",
        base_revision="base-1",
        additions=5,
        deletions=2,
        as_of_ms=99,
        partial=True,
    )
    assert [f.file_id for f in model.files] == ["a", "b"] and model.files_next == "c1"
    assert (model.snapshot, model.revision_baseline, model.updated_available) == (
        "snap-1",
        "rev-1",
        False,
    )
    assert (model.branch, model.base_name, model.additions, model.deletions) == (
        "feature",
        "origin/main",
        5,
        2,
    )
    assert model.partial and model.as_of_ms == 99 and model.files_load.status == "ready"


def test_more_pages_append_and_stop_at_the_last() -> None:
    model = loaded("a", next="c1")
    model, request = m.request_files(model, more=True)
    assert request is not None and request.cursor == "c1"
    assert m.request_files(model, more=True)[1] is None  # one request at a time
    model = m.receive_files(model, request.token, listing("b", next=None), more=True)
    assert [f.file_id for f in model.files] == ["a", "b"] and model.files_next is None
    assert m.request_files(model, more=True)[1] is None


def test_a_page_from_another_snapshot_is_not_mixed_into_the_list() -> None:
    model = loaded("a", next="c1")
    model, request = m.request_files(model, more=True)
    assert request is not None
    model = m.receive_files(model, request.token, listing("x", snapshot="snap-2"), more=True)
    assert [f.file_id for f in model.files] == ["a"] and model.updated_available


def test_the_selected_file_survives_a_reload_only_if_it_is_still_listed() -> None:
    model = m.select_file(loaded("a", "b"), "b")
    kept = m.begin_refresh(model)
    kept, request = m.request_files(kept)
    assert request is not None and kept.selected_file == "b"
    assert (
        m.receive_files(kept, request.token, listing("b", "c", snapshot="snap-2")).selected_file
        == "b"
    )
    gone = m.receive_files(kept, request.token, listing("c", snapshot="snap-2"))
    assert gone.selected_file is None


def test_a_page_without_the_branch_details_keeps_what_the_model_already_knows() -> None:
    model = m.select(ChangesModel(), "agent-1", "wt-1")
    model = m.apply_summary(model, summary(detached=True, short_oid="def5678", branch=None))
    model = replace(model, additions=7, deletions=1)
    model, request = m.request_files(model)
    assert request is not None
    kept = m.receive_files(model, request.token, listing("a"))
    assert (kept.detached, kept.short_oid, kept.additions, kept.deletions) == (
        True,
        "def5678",
        7,
        1,
    )


def test_a_page_that_knows_the_details_replaces_them() -> None:
    model = replace(m.select(ChangesModel(), "agent-1", "wt-1"), detached=True, additions=7)
    model, request = m.request_files(model)
    assert request is not None
    page = listing("a", detached=False, short_oid="1234567", additions=0, deletions=4)
    fresh = m.receive_files(model, request.token, page)
    assert (fresh.detached, fresh.short_oid, fresh.additions, fresh.deletions) == (
        False,
        "1234567",
        0,
        4,
    )


def test_totals_of_a_dropped_snapshot_are_not_carried_into_the_reload() -> None:
    model = replace(loaded("a", additions=5, deletions=2), updated_available=True)
    fresh = m.begin_refresh(model)
    assert (fresh.additions, fresh.deletions) == (None, None)
    fresh, request = m.request_files(fresh)
    assert request is not None
    again = m.receive_files(fresh, request.token, listing("a", snapshot="snap-2"))
    assert (again.additions, again.deletions) == (None, None)


# -- revision and refresh ----------------------------------------------------------------------


def test_a_newer_revision_only_raises_a_flag_and_never_changes_what_is_shown() -> None:
    model = m.select_file(loaded("a", "b"), "a")
    model = with_diff(model, "a", diff_page("a", 4))
    newer = m.note_revision(model, "rev-2")
    assert newer.updated_available and newer.revision_seen == "rev-2"
    assert newer.files == model.files and newer.diff == model.diff and newer.snapshot == "snap-1"
    assert m.note_revision(newer, "rev-1").updated_available  # once offered, it stays offered


def test_a_revision_seen_before_any_list_is_remembered_without_a_flag() -> None:
    model = m.note_revision(m.select(ChangesModel(), "agent-1"), "snap-1")
    assert model.revision_seen == "snap-1" and not model.updated_available


def test_refresh_clears_the_data_but_keeps_agent_scope_and_selection() -> None:
    model = with_diff(m.select_file(loaded("a", "b", scope="branch"), "b"), "b", diff_page("b"))
    model = m.set_anchor(m.note_revision(model, "snap-2"), m.Anchor("b", 0, 0, 0, 1))
    fresh = m.begin_refresh(model)
    assert (fresh.agent_id, fresh.worktree_id, fresh.scope) == ("agent-1", "wt-1", "branch")
    assert fresh.selected_file == "b"
    assert (fresh.files, fresh.diff, fresh.anchors, fresh.snapshot) == ((), None, (), None)
    assert not fresh.updated_available and fresh.condition == "calculating"


def test_replies_requested_before_a_refresh_are_dropped() -> None:
    model = m.select(ChangesModel(), "agent-1", "wt-1")
    model, request = m.request_files(model)
    assert request is not None
    refreshed = m.begin_refresh(model)
    assert m.receive_files(refreshed, request.token, listing("old")) is refreshed


def test_a_summary_fills_the_branch_line_of_a_screen_with_nothing_loaded_yet() -> None:
    fresh = m.select(ChangesModel(), "agent-1", "wt-1")
    updated = m.apply_summary(fresh, summary(branch="other", shared=True))
    assert (updated.branch, updated.shared, updated.base_name) == ("other", True, "origin/main")


def test_a_summary_never_rewrites_what_a_loaded_list_shows_it_only_offers_the_update() -> None:
    model = loaded("a", branch="feature", base_name="origin/main")
    updated = m.apply_summary(
        model, summary(branch="other", base_name="origin/dev", revision="rev-3", shared=True)
    )
    assert (updated.branch, updated.base_name, updated.shared) == ("feature", "origin/main", False)
    assert updated.updated_available and updated.files == model.files


def test_a_summary_for_another_agent_is_ignored() -> None:
    model = loaded("a")
    assert m.apply_summary(model, summary(agent_id="agent-9", revision="snap-9")) is model


def test_a_summary_for_a_different_worktree_starts_over() -> None:
    model = loaded("a")
    moved = m.apply_summary(model, summary(worktree_id="wt-2", revision="snap-7"))
    assert moved.worktree_id == "wt-2" and moved.files == () and moved.snapshot is None


def test_a_calculating_summary_does_not_downgrade_data_already_on_screen() -> None:
    assert m.apply_summary(loaded("a"), summary(state="calculating")).condition == "ok"
    fresh = m.select(ChangesModel(), "agent-1", "wt-1")
    assert m.apply_summary(fresh, summary(state="calculating")).condition == "calculating"


@pytest.mark.parametrize(
    "state", ["no_repo", "folder_gone", "unavailable", "unsupported_config", "git_unavailable"]
)
def test_summary_states_are_carried_as_conditions(state: str) -> None:
    model = m.apply_summary(m.select(ChangesModel(), "agent-1", "wt-1"), summary(state=state))
    assert model.condition == state and not m.is_clean(model)


def test_branch_scope_without_a_base_needs_one() -> None:
    model = m.select(ChangesModel(), "agent-1", "wt-1", "branch")
    assert m.apply_summary(model, summary(base_name=None)).condition == "no_base"
    working = m.select(ChangesModel(), "agent-1", "wt-1", "working")
    assert m.apply_summary(working, summary(base_name=None)).condition == "ok"


# -- failures ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "condition"),
    [
        ("unsupported_config", "unsupported_config"),
        ("git_unavailable", "git_unavailable"),
        ("no_base", "no_base"),
        ("host_offline", "host_offline"),
        ("unsupported_capability", "not_permitted"),
        ("forbidden", "not_permitted"),
        ("not_found", "folder_gone"),
    ],
)
def test_a_failed_list_request_sets_the_matching_condition(reason: str, condition: str) -> None:
    model, request = m.request_files(m.select(ChangesModel(), "agent-1", "wt-1"))
    assert request is not None
    failed = m.fail(model, "files", request.token, reason)
    assert failed.condition == condition and failed.files_load == m.Load(
        request.token, "error", reason
    )


@pytest.mark.parametrize("reason", ["busy", "rate_limited", "internal", "partial"])
def test_a_transient_failure_keeps_the_condition_and_the_data(reason: str) -> None:
    model = loaded("a")
    model, request = m.request_files(model, more=False)
    assert request is not None
    failed = m.fail(model, "files", request.token, reason)
    assert (
        failed.condition == "ok"
        and failed.files == model.files
        and failed.files_load.error == reason
    )


def test_a_stale_snapshot_failure_offers_the_update() -> None:
    model = with_diff(m.select_file(loaded("a"), "a"), "a", diff_page("a", next="c1"))
    model, request = m.request_diff(model)
    assert request is not None
    failed = m.fail(model, "diff", request.token, "stale_snapshot")
    assert failed.updated_available and failed.diff_load.status == "error"


@pytest.mark.parametrize("reason", ["stale_snapshot", "stale_epoch", "stale_revision", "expired"])
def test_every_stale_reason_offers_the_update(reason: str) -> None:
    model, request = m.request_files(m.select(ChangesModel(), "agent-1", "wt-1"))
    assert request is not None
    failed = m.fail(model, "files", request.token, reason)
    assert failed.updated_available and failed.condition == "calculating"


def test_a_failure_with_an_old_token_is_ignored() -> None:
    model, request = m.request_files(m.select(ChangesModel(), "agent-1", "wt-1"))
    assert request is not None
    again, _ = m.request_files(model)
    assert m.fail(again, "files", request.token, "no_base") is again


# -- selected file and diff pages --------------------------------------------------------------


def test_selecting_a_file_resets_its_diff_and_unknown_files_are_ignored() -> None:
    model = with_diff(loaded("a", "b"), "a", diff_page("a"))
    assert m.select_file(model, "zzz") is model and m.select_file(model, "a") is model
    other = m.select_file(model, "b")
    assert other.selected_file == "b" and other.diff is None and m.resident_pages(other) == ()


def test_a_diff_reply_for_a_file_that_is_no_longer_selected_is_dropped() -> None:
    model = m.select_file(loaded("a", "b"), "a")
    model, request = m.request_diff(model)
    assert request is not None
    other = m.select_file(model, "b")
    assert m.receive_diff(other, request.token, diff_page("a"), 0) is other


def test_stepping_moves_through_the_list_and_stops_at_the_ends() -> None:
    model = m.select_file(loaded("a", "b", "c"), "b")
    assert m.step_file(model, 1).selected_file == "c"
    assert m.step_file(model, -1).selected_file == "a"
    assert m.step_file(m.step_file(model, 1), 1).selected_file == "c"
    assert m.step_file(m.select_file(model, "a"), -1).selected_file == "a"
    assert m.step_file(loaded("a"), 1).selected_file is None


def test_pages_load_in_order_and_the_last_one_completes_the_file() -> None:
    model = m.select_file(loaded("a"), "a")
    model, first = m.request_diff(model)
    assert first is not None and (first.cursor, first.page) == (None, 0)
    model = m.receive_diff(model, first.token, diff_page("a", 5, next="c1"), 0)
    assert model.diff is not None and not model.diff.complete and model.diff.render == "ok"
    model, second = m.request_diff(model)
    assert second is not None and (second.cursor, second.page) == ("c1", 1)
    model = m.receive_diff(model, second.token, diff_page("a", 5, next=None, start=6), 1)
    assert model.diff is not None and model.diff.complete and model.diff.last_page == 1
    assert m.request_diff(model)[1] is None
    assert m.resident_rows(model) == 10


def test_only_one_sequential_page_is_requested_at_a_time() -> None:
    model = m.select_file(loaded("a"), "a")
    model, first = m.request_diff(model)
    assert first is not None
    assert m.request_diff(model)[1] is None


def test_a_page_of_another_file_or_snapshot_is_not_added() -> None:
    model = m.select_file(loaded("a", "b"), "a")
    model, request = m.request_diff(model)
    assert request is not None
    assert m.receive_diff(model, request.token, diff_page("b"), 0) is model
    stale = m.receive_diff(model, request.token, diff_page("a", snapshot="snap-0"), 0)
    assert stale.updated_available and m.resident_pages(stale) == ()


def test_a_non_ok_render_is_kept_from_the_first_page() -> None:
    model = m.select_file(loaded("a"), "a")
    model, request = m.request_diff(model)
    assert request is not None
    binary = FileDiff(file_id="a", snapshot="snap-1", render="binary", hunks=())
    model = m.receive_diff(model, request.token, binary, 0)
    assert model.diff is not None and model.diff.render == "binary" and model.diff.complete


# -- eviction and the reading anchor -----------------------------------------------------------


def test_loading_past_the_row_limit_evicts_the_earliest_page() -> None:
    model = stream(3, 200)
    assert model.diff is not None
    assert [index for index, _ in model.diff.pages] == [1, 2] and m.resident_rows(model) == 400


def test_a_forty_page_file_never_holds_more_than_five_hundred_rows() -> None:
    model = stream(40, 500)
    assert m.resident_rows(model) == 500 and model.diff is not None and model.diff.complete
    assert [index for index, _ in model.diff.pages] == [39] and model.diff.last_page == 39


def test_the_anchor_survives_eviction_and_names_the_page_to_fetch_again() -> None:
    model = stream(3, 200)
    anchor = m.Anchor("a", page=0, hunk=0, fragment=0, row=17)
    model = m.set_anchor(model, anchor)
    kept = m.anchor_for(model, "a")
    assert kept is not None and (kept.page, kept.row) == (0, 17)
    assert len(kept.cursors) == 3  # the chain of cursors travels with the position
    assert m.page_to_reload(model) == 0
    model = m.set_anchor(model, m.Anchor("a", page=2, hunk=0, fragment=0, row=3))
    assert m.page_to_reload(model) is None


def test_an_evicted_page_is_fetched_again_with_its_cursor_and_evicts_from_the_far_end() -> None:
    model = stream(3, 200)
    model, request = m.request_diff(model, page=0)
    assert request is not None and (request.page, request.cursor) == (0, None)
    model = m.receive_diff(model, request.token, diff_page("a", 200, "c1"), 0)
    assert model.diff is not None
    assert [index for index, _ in model.diff.pages] == [0, 1] and m.resident_rows(model) == 400
    model, request = m.request_diff(model, page=2)
    assert request is not None and request.cursor == "c2"


def test_requests_for_resident_unknown_or_future_pages_are_refused() -> None:
    model = stream(2, 100)
    assert m.request_diff(model, page=0)[1] is None  # resident
    assert m.request_diff(model, page=5)[1] is None  # never fetched, cursor unknown
    assert m.request_diff(model, page=-1)[1] is None


def test_anchors_are_kept_per_file_and_the_oldest_files_are_forgotten() -> None:
    model = m.set_anchor(loaded("a", "b"), m.Anchor("a", 0, 0, 0, 5))
    model = m.set_anchor(model, m.Anchor("b", 0, 0, 0, 9))
    model = m.set_anchor(model, m.Anchor("a", 0, 0, 0, 6))
    assert (
        m.anchor_for(model, "a") == m.Anchor("a", 0, 0, 0, 6)
        and m.anchor_for(model, "b") is not None
    )
    for index in range(m.MAX_ANCHORS + 5):
        model = m.set_anchor(model, m.Anchor(f"f{index}", 0, 0, 0, 0))
    assert len(model.anchors) == m.MAX_ANCHORS
    assert (
        m.anchor_for(model, "a") is None
        and m.anchor_for(model, f"f{m.MAX_ANCHORS + 4}") is not None
    )


def test_each_files_position_is_kept_while_switching_between_files() -> None:
    model = m.select_file(loaded("a", "b"), "a")
    model = m.set_anchor(model, m.Anchor("a", 0, 0, 0, 40))
    model = m.step_file(model, 1)
    model = m.step_file(model, -1)
    assert model.selected_file == "a" and m.anchor_for(model, "a") == m.Anchor("a", 0, 0, 0, 40)


# -- base picker -------------------------------------------------------------------------------


def candidate(name: str) -> BaseCandidate:
    return BaseCandidate(base_id=f"id-{name}", name=name, kind="local", oid="0" * 40)


def test_opening_the_picker_loads_candidates_once_and_pages_through_them() -> None:
    model, request = m.open_base_picker(loaded("a"))
    assert model.picker_open and request is not None and request.slot == "bases"
    model = m.receive_bases(model, request.token, (candidate("main"),), "c1")
    assert model.bases_load.status == "ready" and model.bases_next == "c1"
    model, more = m.request_bases(model, more=True)
    assert more is not None and more.cursor == "c1"
    model = m.receive_bases(model, more.token, (candidate("dev"),), None, more=True)
    assert [b.name for b in model.bases] == ["main", "dev"] and m.request_bases(model, more=True)[
        1
    ] is None
    reopened, again = m.open_base_picker(m.close_base_picker(model))
    assert reopened.picker_open and again is None


def test_a_late_candidate_list_is_dropped() -> None:
    model, request = m.open_base_picker(loaded("a"))
    assert request is not None
    model, newer = m.request_bases(model)
    assert newer is not None
    assert m.receive_bases(model, request.token, (candidate("x"),), None) is model


def test_saving_a_base_closes_the_picker_and_reloads_a_branch_list() -> None:
    model = m.set_scope(loaded("a", scope="working"), "branch")
    model = replace(model, condition="no_base", picker_open=True)
    model, request = m.request_save_base(model, "id-main")
    assert request.base_id == "id-main" and request.slot == "save"
    model = m.receive_base_saved(model, request.token, "main", "base-9")
    assert (model.base_name, model.base_revision, model.picker_open) == ("main", "base-9", False)
    assert model.files == () and model.snapshot is None and model.condition == "calculating"


def test_a_saved_base_without_a_revision_is_accepted() -> None:
    model, request = m.request_save_base(loaded("a"), "id-main")
    saved = m.receive_base_saved(model, request.token, "main", None)
    assert (saved.base_name, saved.base_revision, saved.save_load.status) == ("main", None, "ready")
    branch = m.set_scope(loaded("a"), "branch")
    branch, request = m.request_save_base(branch, "id-main")
    reloaded = m.receive_base_saved(branch, request.token, "main", None)
    assert reloaded.base_revision is None and reloaded.files == ()


def test_saving_a_base_does_not_disturb_a_working_list() -> None:
    model = loaded("a", "b")
    model, request = m.request_save_base(model, "id-main")
    saved = m.receive_base_saved(model, request.token, "main", "base-9")
    assert (
        saved.files == model.files and saved.base_revision == "base-9" and saved.condition == "ok"
    )


def test_a_save_reply_with_an_old_token_is_dropped_and_a_failed_save_keeps_the_picker() -> None:
    model, first = m.request_save_base(replace(loaded("a"), picker_open=True), "id-1")
    model, second = m.request_save_base(model, "id-2")
    assert m.receive_base_saved(model, first.token, "one", "r1") is model
    failed = m.fail(model, "save", second.token, "not_found")
    assert failed.picker_open and failed.save_load.error == "not_found" and failed.base_name is None
