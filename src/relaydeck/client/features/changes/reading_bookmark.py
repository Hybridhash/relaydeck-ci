"""Bounded, memory-only reader position for an explicit return from a terminal."""

from __future__ import annotations

from dataclasses import dataclass, replace

from relaydeck.client.features.changes.graph_state import GraphState
from relaydeck.client.features.changes.state import (
    Anchor,
    ChangesModel,
    CommitReading,
    DiffWindow,
    Scope,
    View,
)


@dataclass(frozen=True, slots=True, repr=False)
class ReadingBookmark:
    worktree: str | None
    scope: Scope
    view: View
    snapshot: str | None
    path: str | None
    file_id: str | None
    hunk: tuple[int, int] | None
    anchors: tuple[Anchor, ...]
    query: str = ""
    generated: bool = False
    folded: frozenset[str] = frozenset()
    loaded: bool = False
    graph: GraphState | None = None
    commit: CommitReading | None = None

    @classmethod
    def capture(
        cls,
        model: ChangesModel,
        query: str = "",
        generated: bool = False,
        folded: frozenset[str] = frozenset(),
    ) -> ReadingBookmark:
        return cls(
            model.worktree_id,
            model.scope,
            model.view,
            model.snapshot,
            model.selected_path,
            model.selected_file,
            model.selected_hunk,
            model.anchors[-200:],
            query[:4096],
            generated,
            frozenset(tuple(folded)[:200]),
            bool(
                model.diff is not None
                and model.diff.loaded
                and model.diff.file_id == model.selected_file
                and model.diff.snapshot == model.snapshot
            ),
            model.graph if model.view == "graph" else None,
            model.commit if model.view == "graph" else None,
        )

    def section(self) -> str:
        """The route section of the screen level this position was left at."""
        if self.commit is None:
            return "changes"
        return "commit-file" if self.commit.model.selected_file is not None else "commit"

    def seed(self, model: ChangesModel) -> ChangesModel:
        if model.worktree_id != self.worktree:
            return model
        return replace(
            model,
            scope=self.scope,
            view=self.view,
            selected_path=self.path,
            graph=self.graph or GraphState(),
            commit=self.commit,
        )

    def restore(self, model: ChangesModel) -> ChangesModel:
        if model.worktree_id != self.worktree or model.scope != self.scope:
            return model
        if model.snapshot == self.snapshot and model.selected_file == self.file_id:
            window = model.diff
            if (
                self.loaded
                and window is None
                and self.file_id is not None
                and self.snapshot is not None
            ):
                anchor = next((a for a in self.anchors if a.file_id == self.file_id), None)
                window = DiffWindow(
                    self.file_id,
                    self.snapshot,
                    loaded=True,
                    cursors=anchor.cursors if anchor and anchor.cursors else (None,),
                )
            return replace(model, selected_hunk=self.hunk, anchors=self.anchors, diff=window)
        return replace(
            model,
            selected_hunk=None,
            anchors=(),
            reading_notice=(
                "Changes refreshed; the previous hunk is no longer selected." if self.hunk else ""
            ),
        )
