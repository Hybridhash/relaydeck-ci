"""How the wide graph and an open commit share the width the Changes screen is given."""

from __future__ import annotations

from dataclasses import dataclass

GRAPH_SHARE = 0.45
GRAPH_MIN = 360.0  # dp; the two-line rows need this much
COMMIT_MIN = 360.0  # dp; metadata, actions, files and diff need this much
DIVIDER = 1.0
# Before the screen is measured the panes share the width in the same proportion.
GRAPH_FLEX = round(GRAPH_SHARE * 100)
COMMIT_FLEX = 100 - GRAPH_FLEX


@dataclass(frozen=True, slots=True)
class GraphSplit:
    """`side_by_side` is False when both panes cannot be usable at once. `graph` is the graph's
    width in dp, or None while the screen has not been measured (the panes then share the width
    by proportion)."""

    side_by_side: bool
    graph: float | None = None


def graph_split(available: float) -> GraphSplit:
    """The split for `available` dp, the width the screen is really given by the surrounding shell
    (rail, sidebar, handle and padding already taken off)."""
    if available <= 0:
        return GraphSplit(True)
    graph = max(GRAPH_MIN, float(round(available * GRAPH_SHARE)))
    return GraphSplit(available - graph - DIVIDER >= COMMIT_MIN, graph)
