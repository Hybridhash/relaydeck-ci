"""Canvas topology, page boundaries and honest bounded lane projection."""

from __future__ import annotations

from dataclasses import replace

import flet as ft
import flet.canvas as cv
import pytest

from relaydeck.client.features.changes.graph_layout import GraphRow, Segment, append, project, seed
from relaydeck.client.screens.changes.parts.graph_canvas import graph_canvas
from relaydeck.client.screens.changes.parts.graph_style import STYLES
from tests.support.graph_pages import graph_commit


def test_approved_all_and_branch_lane_geometry() -> None:
    parents = [(2,), (5,), (5, 4), (5,), (7,), (7,), (8,), (9,), (10,)]
    commits = tuple(graph_commit(i, parents=p) for i, p in enumerate(parents, 1))
    start = seed("all", commits[0].oid, commits[2].oid, commits[4].oid)
    rows, _ = append(start, commits)
    assert [r.lane for r in rows] == [1, 1, 0, 2, 0, 2, 0, 0, 0]
    expected = [
        [("down", 1, 1)],
        [("up", 1, 1), ("down", 1, 1)],
        [("down", 0, 0), ("out", 0, 2), ("pass", 1, 1)],
        [("up", 2, 2), ("down", 2, 2), ("pass", 0, 0), ("pass", 1, 1)],
        [("up", 0, 0), ("in", 1, 0), ("in", 2, 0), ("down", 0, 0)],
        [("down", 2, 2), ("pass", 0, 0)],
        [("up", 0, 0), ("in", 2, 0), ("down", 0, 0)],
        [("up", 0, 0), ("down", 0, 0)],
        [("up", 0, 0), ("down", 0, 0)],
    ]
    assert [[(s.kind, s.start, s.end) for s in r.segments] for r in rows] == expected
    assert rows[0].head and rows[2].merge
    assert [r.color for r in rows[:4]] == [1, 1, 0, 2]
    mine = seed("branch", commits[0].oid, commits[2].oid, commits[4].oid)
    branch, _ = append(mine, (commits[0], commits[1], commits[4]))
    assert [r.lane for r in branch] == [0, 0, 0]
    assert [s.kind for s in branch[-1].segments] == ["up"]
    assert branch[-1].dim and not branch[-1].ahead


def test_append_equals_one_pass_at_every_boundary_and_reuses_freed_lane() -> None:
    commits = (
        graph_commit(1, parents=(3,)),
        graph_commit(2, parents=(3,)),
        graph_commit(3, parents=(5,)),
        graph_commit(4, parents=(5,)),
        graph_commit(5),
    )
    start = seed("all", commits[0].oid, commits[1].oid, commits[2].oid)
    whole, final = append(start, commits)
    for split in range(1, len(commits)):
        first, state = append(start, commits[:split])
        last, state = append(state, commits[split:])
        assert first + last == whole and state == final
    assert not final.tracks
    with pytest.raises(ValueError, match="order"):
        append(final, commits[:1])


def test_octopus_hidden_marker_reconnect_and_page_boundary() -> None:
    first = graph_commit(1, parents=tuple(range(2, 11)))
    commits = (
        (first,) + tuple(graph_commit(i, parents=(11,)) for i in range(2, 11)) + (graph_commit(11),)
    )
    initial = seed("all", first.oid, graph_commit(50).oid, graph_commit(11).oid)
    rows, checkpoint = append(initial, commits[:8])
    tail, after = append(checkpoint, commits[8:])
    whole, end = append(initial, commits)
    assert rows + tail == whole and after == end
    hidden = [r for r in whole if r.lane >= 6]
    assert hidden and all(project(r, 6).slot(r.lane) == 5 for r in hidden)
    assert any(s.kind == "in" and s.start >= 6 for s in whole[-1].segments)
    assert project(whole[-1], 6).hidden > 0
    assert project(whole[-1], 10).slot(whole[-1].lane) == whole[-1].lane
    assert whole[0].head and whole[0].merge
    assert whole == rows + tail  # changing projection never mutates canonical geometry
    marker = GraphRow("a" * 40, 8, 1, (Segment("up", 8, 8, 1),), head=True)
    projected = project(marker, 6)
    assert projected.hidden == 1 and projected.slot(marker.lane) == 5
    canvas = graph_canvas(marker, style=STYLES["phone"], lanes=6)
    assert isinstance(canvas.content, ft.Stack)
    drawing = canvas.content.controls[0]
    assert isinstance(drawing, cv.Canvas)
    assert len(drawing.shapes) == 4  # incoming stub, fill, outline, HEAD ring


def test_first_parent_joins_preseeded_base_without_line_above_tip() -> None:
    head, base = graph_commit(1, parents=(2,)), graph_commit(2)
    rows, _ = append(seed("all", head.oid, base.oid, base.oid), (head, base))
    assert [(s.kind, s.start, s.end) for s in rows[0].segments] == [("out", 1, 0)]
    assert [s.kind for s in rows[1].segments] == ["up"]
    same, _ = append(seed("all", base.oid, base.oid, base.oid), (replace(base, ahead=False),))
    assert same[0].lane == 0 and same[0].color == 1 and same[0].head


def test_split_uses_its_actual_incoming_track_after_base_was_merged() -> None:
    commits = (
        graph_commit(1, parents=(2, 4)),
        graph_commit(2, parents=(3,)),
        graph_commit(3, parents=(5,)),
        graph_commit(4, parents=(5,)),
    )
    initial = seed("branch", commits[0].oid, commits[-1].oid, commits[-1].oid)
    rows, final = append(initial, commits)
    assert rows[-1].lane == 1 and rows[-1].dim
    assert rows[-1].segments == (Segment("up", 1, 1, 2),)
    assert not final.tracks
    first, checkpoint = append(initial, commits[:3])
    last, after = append(checkpoint, commits[3:])
    assert first + last == rows and after == final


def test_split_joins_every_matching_track_without_excluded_parent_edges() -> None:
    commits = (
        graph_commit(1, parents=(2, 3)),
        graph_commit(2, parents=(4,)),
        graph_commit(3, parents=(4,)),
        graph_commit(4, parents=(5,)),
    )
    rows, _ = append(seed("branch", commits[0].oid, commits[-1].oid, commits[-1].oid), commits)
    assert rows[-1].segments == (Segment("up", 0, 0, 1), Segment("in", 1, 0, 2))


def test_equal_head_and_base_reuses_lane_one_for_another_root() -> None:
    same = graph_commit(1, parents=(3,))
    other = graph_commit(2, parents=(3,))
    rows, _ = append(seed("all", same.oid, same.oid, same.oid), (same, other, graph_commit(3)))
    assert rows[1].lane == 1 and rows[1].color == 2
