"""Resident graph pages and canonical lane checkpoints for one reading context."""

from __future__ import annotations

from dataclasses import dataclass

from relaydeck.client.features.changes.graph_layout import GraphRow, LaneState
from relaydeck.contracts.git import GraphCommit, GraphMode, GraphPage


@dataclass(frozen=True, slots=True)
class GraphAnchor:
    oid: str
    offset: float = 0.0


@dataclass(frozen=True, slots=True, repr=False)
class GraphState:
    mode: GraphMode = "all"
    page: GraphPage | None = None
    commits: tuple[GraphCommit, ...] = ()
    rows: tuple[GraphRow, ...] = ()
    checkpoints: tuple[LaneState, ...] = ()
    next: str | None = None
    selected: str | None = None
    anchor: GraphAnchor | None = None
    history_limited: bool = False
    other: GraphState | None = None

    @property
    def lanes(self) -> int:
        """The most lanes any loaded row needs; it only grows as pages arrive."""
        return self.checkpoints[-1].width if self.checkpoints else 1
