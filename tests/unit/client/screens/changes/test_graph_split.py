"""The graph and a commit share the width the screen is given, or the commit is pushed."""

from __future__ import annotations

import pytest

from relaydeck.client.screens.changes.parts.graph_split import (
    COMMIT_FLEX,
    COMMIT_MIN,
    DIVIDER,
    GRAPH_FLEX,
    GRAPH_MIN,
    GRAPH_SHARE,
    graph_split,
)


def test_an_unmeasured_screen_splits_by_proportion() -> None:
    assert graph_split(0).side_by_side and graph_split(0).graph is None


@pytest.mark.parametrize("available", [721, 800, 964, 1200, 1600])
def test_a_wide_enough_screen_gives_each_pane_its_minimum(available: int) -> None:
    split = graph_split(available)
    assert split.side_by_side and split.graph is not None
    assert split.graph >= GRAPH_MIN and available - split.graph - DIVIDER >= COMMIT_MIN


@pytest.mark.parametrize("available", [1, 300, 548, 688, 720])
def test_a_screen_too_narrow_for_both_pushes_the_commit(available: int) -> None:
    assert not graph_split(available).side_by_side


def test_the_graph_takes_about_45_percent_above_its_floor() -> None:
    assert graph_split(1000).graph == 450
    assert graph_split(721).graph == GRAPH_MIN


def test_the_unmeasured_proportion_is_the_graph_share() -> None:
    assert (GRAPH_FLEX, COMMIT_FLEX) == (45, 55)
    assert GRAPH_FLEX / (GRAPH_FLEX + COMMIT_FLEX) == GRAPH_SHARE
