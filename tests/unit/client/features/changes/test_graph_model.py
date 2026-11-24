"""Graph requests never replace file snapshots or accept late contexts."""

from __future__ import annotations

import relaydeck.client.features.changes.model as m
from relaydeck.client.features.changes.graph_model import graph_anchor, select_graph
from relaydeck.client.features.changes.graph_state import GraphAnchor
from relaydeck.client.features.changes.reading_bookmark import ReadingBookmark
from tests.support.changes_model import loaded
from tests.support.graph_pages import graph_commit, graph_page


def test_mode_change_and_refresh_drop_late_pages_without_changing_file_identity() -> None:
    original = loaded("a", scope="branch")
    model, request = m.set_view(original, "graph")
    assert request is not None
    branch, fresh = m.set_graph_mode(model, "branch")
    assert fresh is not None and fresh.token > request.token
    assert m.receive_graph(branch, request.token, graph_page((graph_commit(1),))) is branch
    arrived = m.receive_graph(branch, fresh.token, graph_page((graph_commit(1),), mode="branch"))
    assert arrived.snapshot == original.snapshot and arrived.files == original.files
    assert arrived.graph.page is not None and arrived.graph.page.mode == "branch"
    refreshed = m.begin_refresh(arrived)
    assert refreshed.graph.page is None and refreshed.graph.mode == "branch"
    assert m.receive_graph(refreshed, fresh.token, graph_page()) is refreshed


def test_snapshot_and_duplicate_pages_do_not_advance_lane_checkpoints() -> None:
    model, first = m.set_view(loaded("a", scope="branch"), "graph")
    assert first is not None
    model = m.receive_graph(model, first.token, graph_page((graph_commit(1),), next="more"))
    model, request = m.request_graph(model, more=True)
    assert request is not None
    stale = m.receive_graph(
        model, request.token, graph_page((graph_commit(2),), snapshot="other"), more=True
    )
    assert stale.updated_available and len(stale.graph.checkpoints) == 1
    duplicate = m.receive_graph(model, request.token, graph_page((graph_commit(1),)), more=True)
    assert duplicate.graph_load.error == "upstream_incompatible"
    assert duplicate.graph.commits == model.graph.commits
    accepted = m.receive_graph(model, request.token, graph_page((graph_commit(2),)), more=True)
    assert (
        m.receive_graph(accepted, request.token, graph_page((graph_commit(2),)), more=True)
        is accepted
    )
    assert len(accepted.graph.checkpoints) == 2


def test_foreground_check_offers_refresh_and_stale_row_callbacks_are_ignored() -> None:
    model, first = m.set_view(loaded("a", scope="branch"), "graph")
    assert first is not None
    model = m.receive_graph(model, first.token, graph_page((graph_commit(1),)))
    oid = model.graph.commits[0].oid
    assert select_graph(model, "all", "older-revision", oid) is model
    assert graph_anchor(model, "all", "older-revision", GraphAnchor(oid)) is model
    model = select_graph(model, "all", "graph-1", oid)
    model = graph_anchor(model, "all", "graph-1", GraphAnchor(oid, 4))
    checking, request = m.request_graph(model, check=True)
    assert request is not None
    updated = m.receive_graph(checking, request.token, graph_page(snapshot="graph-2"), check=True)
    assert updated.updated_available
    assert updated.graph.page == model.graph.page and updated.graph.selected == oid
    bookmark = ReadingBookmark.capture(updated)
    seeded = bookmark.seed(m.ChangesModel(worktree_id=updated.worktree_id))
    assert seeded.graph == updated.graph and seeded.view == "graph"
    foreign = m.ChangesModel(worktree_id="another")
    assert bookmark.seed(foreign) is foreign and foreign.graph.page is None


def test_switching_modes_retains_both_resident_page_sets_and_positions() -> None:
    model, request = m.set_view(loaded("a", scope="branch"), "graph")
    assert request is not None
    model = m.receive_graph(model, request.token, graph_page((graph_commit(1),)))
    model = select_graph(model, "all", "graph-1", graph_commit(1).oid)
    model = graph_anchor(model, "all", "graph-1", GraphAnchor(graph_commit(1).oid, 3))
    original = model.graph
    branch, request = m.set_graph_mode(model, "branch")
    assert request is not None
    branch = m.receive_graph(branch, request.token, graph_page((graph_commit(2),), mode="branch"))
    returned, checking = m.set_graph_mode(branch, "all")
    assert checking is not None and checking.check
    assert returned.graph.commits == original.commits and returned.graph.anchor == original.anchor
    assert returned.graph.selected == original.selected
    assert returned.graph.other is not None and returned.graph.other.commits == branch.graph.commits


def test_old_mode_callbacks_cannot_change_a_new_mode_with_the_same_revision() -> None:
    model, request = m.set_view(loaded("a", scope="branch"), "graph")
    assert request is not None
    model = m.receive_graph(model, request.token, graph_page((graph_commit(1),)))
    branch, request = m.set_graph_mode(model, "branch")
    assert request is not None
    branch = m.receive_graph(branch, request.token, graph_page((graph_commit(1),), mode="branch"))
    assert select_graph(branch, "all", "graph-1", graph_commit(1).oid) is branch
    assert graph_anchor(branch, "all", "graph-1", GraphAnchor(graph_commit(1).oid)) is branch


def test_freshness_check_never_claims_paging_load_or_replaces_resident_rows() -> None:
    model, initial = m.set_view(loaded("a", scope="branch"), "graph")
    assert initial is not None
    model = m.receive_graph(model, initial.token, graph_page((graph_commit(1),), next="more"))
    checking, check = m.request_graph(model, check=True)
    assert check is not None and check.slot == "graph_check"
    assert checking.graph_load is model.graph_load and checking.graph is model.graph
    paging, more = m.request_graph(checking, more=True)
    assert more is not None and more.cursor == "more"
    appended = m.receive_graph(paging, more.token, graph_page((graph_commit(2),)), more=True)
    checked = m.receive_graph(appended, check.token, graph_page(), check=True)
    assert checked.graph is appended.graph and checked.graph_load is appended.graph_load
    assert checked.graph_check.status == "ready" and not checked.updated_available
    assert m.receive_graph(checked, check.token, graph_page(snapshot="late"), check=True) is checked


def test_mode_refresh_and_view_changes_invalidate_old_checks() -> None:
    model, initial = m.set_view(loaded("a", scope="branch"), "graph")
    assert initial is not None
    model = m.receive_graph(model, initial.token, graph_page((graph_commit(1),)))
    checking, check = m.request_graph(model, check=True)
    assert check is not None
    branch, _ = m.set_graph_mode(checking, "branch")
    refreshed = m.begin_refresh(checking)
    files, _ = m.set_view(checking, "files")
    for changed in (branch, refreshed, files):
        assert (
            m.receive_graph(changed, check.token, graph_page(snapshot="stale"), check=True)
            is changed
        )
