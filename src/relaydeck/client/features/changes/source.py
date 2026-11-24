"""What the Changes screen needs from the network; the repository adapter implements it."""

from __future__ import annotations

from typing import Protocol

from relaydeck.client.features.changes.model import ChangesPage, Scope
from relaydeck.contracts.git import BaseCandidate, FileDiff, GraphMode, GraphPage, WorktreeSummary


class ChangesFailure(Exception):
    """A typed reason (`details.reason`, else the envelope code) and an optional retry delay."""

    def __init__(self, reason: str, retry_after_s: float | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after_s = retry_after_s


class ChangesSource(Protocol):
    async def summaries(self, host_id: str, agent_ids: list[str]) -> list[WorktreeSummary]: ...

    async def changes(
        self, host_id: str, worktree_id: str, scope: Scope, cursor: str | None
    ) -> ChangesPage: ...

    async def file_diff(
        self, host_id: str, worktree_id: str, file_id: str, cursor: str | None, load: bool
    ) -> FileDiff: ...

    async def graph(
        self, host_id: str, worktree_id: str, mode: GraphMode, cursor: str | None
    ) -> GraphPage: ...

    async def commit_changes(
        self, host_id: str, worktree_id: str, commit_id: str, cursor: str | None
    ) -> ChangesPage: ...

    async def bases(
        self, host_id: str, worktree_id: str, cursor: str | None
    ) -> tuple[tuple[BaseCandidate, ...], str | None]: ...

    async def set_base(self, host_id: str, worktree_id: str, base_id: str) -> str: ...
