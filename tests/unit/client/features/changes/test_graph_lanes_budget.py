"""Hidden lanes share one stub pair; the gutter fits the lanes used; wide histories stay small."""

from __future__ import annotations

import tracemalloc
from dataclasses import replace

import flet as ft
import flet.canvas as cv

import relaydeck.client.features.changes.model as m
from relaydeck.client.features.changes.graph_layout import GraphRow, Segment, append, seed
from relaydeck.client.features.changes.graph_state import GraphState
from relaydeck.client.screens.changes.parts.graph_canvas import graph_canvas
from relaydeck.client.screens.changes.parts.graph_style import STYLES
from tests.support.changes_model import loaded
from tests.support.graph_pages import graph_commit, graph_page


def drawing(row: GraphRow, lanes: int) -> cv.Canvas:
    canvas = graph_canvas(row, style=STYLES["phone"], lanes=lanes)
    assert isinstance(canvas.content, ft.Stack)
    shape = canvas.content.controls[0]
    assert isinstance(shape, cv.Canvas)
    return shape


def test_all_hidden_lanes_share_one_stub_above_and_one_below() -> None:
    passing = tuple(Segment("pass", lane, lane, 3) for lane in range(6, 30))
    row = GraphRow("a" * 40, 1, 2, (Segment("up", 1, 1, 2), Segment("down", 1, 1, 2), *passing))
    lines = [s for s in drawing(row, 6).shapes if isinstance(s, cv.Line)]
    assert len(lines) == 2  # one pair for 24 hidden lanes
    wider = replace(
        row, segments=row.segments + tuple(Segment("pass", lane, lane, 4) for lane in range(30, 60))
    )
    assert len([s for s in drawing(wider, 6).shapes if isinstance(s, cv.Line)]) == 2


def test_a_row_without_hidden_lanes_draws_no_bundle() -> None:
    row = GraphRow("a" * 40, 0, 1, (Segment("down", 0, 0, 1),))
    assert not [s for s in drawing(row, 6).shapes if isinstance(s, cv.Line)]


def test_the_gutter_follows_the_lanes_a_history_needs() -> None:
    line = tuple(graph_commit(n, parents=(n + 1,) if n < 5 else ()) for n in range(1, 6))
    state = seed("branch", line[0].oid, line[-1].oid, line[-1].oid)
    _, after = append(state, line)
    assert after.width == 1
    fork = (
        graph_commit(1, parents=(2, 3)),
        graph_commit(2, parents=(4,)),
        graph_commit(3, parents=(4,)),
        graph_commit(4),
    )
    _, forked = append(seed("branch", fork[0].oid, fork[-1].oid, fork[-1].oid), fork)
    assert forked.width == 2
    # Widths only grow as pages arrive, and are the same however the history is paged.
    first, mid = append(seed("branch", fork[0].oid, fork[-1].oid, fork[-1].oid), fork[:2])
    _, last = append(mid, fork[2:])
    assert last.width == forked.width >= mid.width


def test_the_graph_state_reports_the_widest_checkpoint() -> None:
    model, request = m.set_view(loaded("a", scope="branch"), "graph")
    assert request is not None
    commits = (
        graph_commit(1, parents=(2, 3)),
        graph_commit(2, parents=(4,)),
        graph_commit(3, parents=(4,)),
        graph_commit(4),
    )
    page = replace(
        graph_page(commits),
        head=commits[0].oid,
        base_oid=commits[-1].oid,
        split_oid=commits[-1].oid,
    )
    done = m.receive_graph(model, request.token, page)
    assert done.graph.lanes >= 2
    assert GraphState().lanes == 1


def test_only_the_newest_checkpoint_keeps_the_seen_set() -> None:
    model, request = m.set_view(loaded("a", scope="branch"), "graph")
    assert request is not None
    model = m.receive_graph(
        model, request.token, graph_page((graph_commit(1, parents=(2,)),), next="more")
    )
    model, more = m.request_graph(model, more=True)
    assert more is not None
    model = m.receive_graph(model, more.token, graph_page((graph_commit(2),)), more=True)
    old, newest = model.graph.checkpoints
    assert not old.seen and newest.seen == {graph_commit(1).oid, graph_commit(2).oid}


def test_a_wide_history_is_held_in_a_small_fraction_of_its_naive_size() -> None:
    n, frontier = 2000, 128
    commits = tuple(
        graph_commit(i, parents=(i + frontier,) if i + frontier <= n else ())
        for i in range(1, n + 1)
    )
    tracemalloc.start()
    state = seed("all", commits[0].oid, commits[-1].oid, commits[-1].oid)
    rows: list[GraphRow] = []
    for start in range(0, n, 100):
        chunk, state = append(state, commits[start : start + 100])
        rows.extend(chunk)
    current, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    segments = sum(len(r.segments) for r in rows)
    assert segments > 200_000  # a wide frontier: every row repeats every passing lane
    # Shared segment objects leave only a pointer per repeat (the objects themselves are few).
    assert current < segments * 12
    assert len({id(s) for r in rows for s in r.segments}) < 5_000
