"""The rows built follow the scroll offset; the rest are spacers."""

from __future__ import annotations

import pytest

from relaydeck.client.design_system.lists.virtual_list import (
    MIN_SCREEN_ROWS,
    Geometry,
    Window,
    _Measures,
    needs_shift,
    screen_rows,
    shift,
    visible,
    window_for,
)

H, PORT = 56, 800.0


def fixed(count: int) -> Geometry:
    return Geometry(count, H)


def test_the_visible_rows_follow_the_offset() -> None:
    assert visible(0, PORT, fixed(1000)) == (0, 15)
    assert visible(56 * 100 + 10, PORT, fixed(1000)) == (100, 115)
    assert visible(56 * 995, PORT, fixed(1000)) == (995, 1000)
    assert visible(0, PORT, fixed(0)) == (0, 0)


@pytest.mark.parametrize("total", [0, 1, 7, 100, 1000, 10_000])
def test_a_window_is_a_screen_either_side_and_never_leaves_the_data(total: int) -> None:
    geometry = fixed(total)
    for offset in (0.0, 5.0, 56.0 * max(0, total // 2), 56.0 * total):
        window = window_for(offset, PORT, geometry)
        assert 0 <= window.first <= window.last <= total
        assert len(window) <= 15 + 2 * screen_rows(PORT, geometry) + 1


def test_what_is_built_does_not_depend_on_how_much_is_loaded() -> None:
    small, large = window_for(0, PORT, fixed(100)), window_for(0, PORT, fixed(1_000_000))
    assert len(small) == len(large)
    middle = window_for(56.0 * 500_000, PORT, fixed(1_000_000))
    assert len(middle) == len(large) + screen_rows(PORT, fixed(1_000_000))


def test_the_window_holds_the_viewport() -> None:
    geometry = fixed(1000)
    for offset in range(0, 56 * 1000, 777):
        window = window_for(float(offset), PORT, geometry)
        first, last = visible(float(offset), PORT, geometry)
        assert window.first <= first and last <= window.last


def test_it_shifts_only_near_an_unbuilt_row() -> None:
    geometry = fixed(1000)
    window = window_for(56.0 * 100, PORT, geometry)
    assert not needs_shift(window, 56.0 * 100, PORT, geometry)
    assert not needs_shift(window, 56.0 * 105, PORT, geometry)
    assert needs_shift(window, 56.0 * 130, PORT, geometry)
    assert needs_shift(window, 56.0 * 70, PORT, geometry)


def test_the_ends_of_the_data_never_ask_for_more() -> None:
    geometry = fixed(1000)
    top, bottom = window_for(0, PORT, geometry), window_for(56.0 * 990, PORT, geometry)
    assert not needs_shift(top, 0, PORT, geometry)
    assert not needs_shift(bottom, 56.0 * 995, PORT, geometry)
    assert not needs_shift(Window(0, 5), 0, PORT, fixed(5))


def test_a_small_viewport_still_builds_a_useful_margin() -> None:
    assert screen_rows(100, fixed(100)) == MIN_SCREEN_ROWS


def test_a_step_near_the_edge_extends_the_window_and_a_jump_builds_only_what_is_in_view() -> None:
    geometry = fixed(1000)
    window = window_for(56.0 * 100, PORT, geometry)
    step, lean = shift(window, 56.0 * 128, PORT, geometry) or (None, None)
    assert step is not None and lean is False and len(step) == len(window)
    jump, lean = shift(window, 56.0 * 600, PORT, geometry) or (None, None)
    assert jump is not None and lean is True
    first, last = visible(56.0 * 600, PORT, geometry)
    assert jump.first == first - 2 and jump.last == last + 2  # the rows in view, a few around
    assert len(jump) < len(window)
    assert shift(window, 56.0 * 102, PORT, geometry) is None


def mixed(count: int) -> Geometry:
    """Every fifth row twice as tall, as a diff's selectable hunk headers are."""
    tops, at = [], 0.0
    for index in range(count):
        tops.append(at)
        at += 48.0 if index % 5 == 0 else 24.0
    return Geometry(count, 24.0, (*tops, at))


def test_rows_of_different_heights_find_their_rows_by_their_tops() -> None:
    geometry = mixed(100)
    assert geometry.top(0) == 0 and geometry.top(1) == 48 and geometry.top(2) == 72
    assert geometry.offsets is not None and geometry.total == geometry.offsets[100]
    for index in range(100):
        assert geometry.index_at(geometry.top(index)) == index
        assert geometry.index_at(geometry.top(index) + 0.5) == index
    assert geometry.index_at(-5) == 0 and geometry.index_at(10**9) == 99


def test_windows_over_mixed_heights_hold_the_view_and_stay_bounded() -> None:
    geometry = mixed(10_000)
    for offset in (0.0, 5_000.0, 120_000.0, geometry.total):
        window = window_for(offset, PORT, geometry)
        first, last = visible(offset, PORT, geometry)
        assert window.first <= first and last <= window.last
        assert len(window) < 100
        assert geometry.top(last) - geometry.top(first) >= min(PORT, geometry.total - offset) - 48


def keys(index: int) -> str:
    return f"row-{index}"


def anchor_offset(measures: _Measures, row: int, count: int = 4096) -> float:
    """Where a row sits from the top of the list, as the geometry now believes."""
    return measures.geometry(count, keys, 106.0).top(row)


def test_heights_learned_below_the_top_do_not_move_the_rows_the_reader_is_looking_at() -> None:
    measures = _Measures()
    measures.enter("agents", away=True)  # opened (or scrolled) away from the top
    first, anchor = 1_990, 2_000
    before = anchor_offset(measures, anchor)
    for row in range(first, first + 30):  # the cards built around the reader report 130 dp
        measures.heights[keys(row)] = 130.0
    after = anchor_offset(measures, anchor)
    # Only the measured rows above the anchor can move it: ten of them, 24 dp taller each.
    assert after - before == 10 * (130.0 - 106.0)
    assert anchor_offset(measures, first) == 106.0 * first  # nothing above the window moved


def test_at_the_top_the_average_still_learns_and_it_freezes_when_the_reader_leaves() -> None:
    measures = _Measures()
    measures.enter("agents", away=False)
    for row in range(8):
        measures.heights[keys(row)] = 130.0
    assert measures.geometry(100, keys, 106.0).top(50) == 8 * 130.0 + 42 * 130.0  # average 130
    measures.away = True
    measures.geometry(100, keys, 106.0)  # the average at the moment of leaving is kept
    for row in range(8, 20):
        measures.heights[keys(row)] = 200.0
    after = measures.geometry(100, keys, 106.0)
    assert after.top(50) == 8 * 130.0 + 12 * 200.0 + 30 * 130.0


def test_another_context_starts_over_with_its_own_average() -> None:
    measures = _Measures()
    measures.enter("a", away=True)
    measures.geometry(10, keys, 106.0)
    assert measures.frozen == 106.0
    measures.enter("b", away=False)
    assert measures.frozen is None and not measures.away


def test_an_unchanged_render_reuses_geometry_without_touching_every_row() -> None:
    measures = _Measures()
    measures.enter("agents", away=False)
    rows = [f"row-{index}" for index in range(20_000)]
    calls = 0

    def counting() -> object:
        def fresh(index: int) -> str:  # a new callback per render, as owners build
            nonlocal calls
            calls += 1
            return rows[index]

        return fresh

    first = measures.geometry(20_000, counting(), 106.0, rows)  # type: ignore[arg-type]
    assert calls == 20_000
    calls = 0
    measures.prune(20_000, rows, counting())  # type: ignore[arg-type]
    measures.prune(20_000, rows, counting())  # type: ignore[arg-type]
    assert calls == 20_000  # the second prune scanned nothing
    calls = 0
    again = measures.geometry(20_000, counting(), 106.0, rows)  # type: ignore[arg-type]
    assert again is first and calls == 0


def test_a_same_count_reorder_and_a_learned_height_invalidate_geometry() -> None:
    measures = _Measures()
    measures.enter("agents", away=False)
    ordered = ["tall", "short", "short-2"]
    measures.record("tall", 100.0)
    measures.record("short", 10.0)
    assert measures.geometry(3, lambda i: ordered[i], 50.0, ordered).top(1) == 100.0
    reordered = ["short", "tall", "short-2"]
    assert measures.geometry(3, lambda i: reordered[i], 50.0, reordered).top(1) == 10.0
    measures.record("short", 40.0)  # same key, new height
    assert measures.geometry(3, lambda i: reordered[i], 50.0, reordered).top(1) == 40.0
    measures.enter("other", away=False)
    assert measures.geometry(3, lambda i: reordered[i], 50.0, reordered).top(1) == 40.0


def test_pruning_forgets_gone_rows_only_when_the_data_changes() -> None:
    measures = _Measures()
    measures.enter("agents", away=False)
    measures.record("gone", 100.0)
    live = ["row-0", "row-1"]
    measures.prune(2, live, lambda i: live[i])
    assert "gone" not in measures.heights
    measures.heights["gone"] = 100.0
    measures.prune(2, live, lambda i: live[i])  # unchanged data: no rescan
    assert "gone" in measures.heights
    measures.prune(2, list(live), lambda i: live[i])  # new sequence: rescan
    assert "gone" not in measures.heights
