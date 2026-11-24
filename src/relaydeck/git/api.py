"""What the gateway needs from the Mac to serve changes for one host."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from relaydeck.contracts.git import Commit, FileDiff, GraphMode, WorktreeSummary
from relaydeck.git.base_store import BaseStore
from relaydeck.git.results import (
    ChangesError,
    FileRecord,
    GraphResult,
    Scope,
    Snapshot,
    display,
    file_totals,
)
from relaydeck.git.runner import GitRunner, GitUnavailable
from relaydeck.git.service import ChangesService


class ReadService(Protocol):
    async def summaries(
        self, account: str, host: str, folders: Mapping[str, str | None]
    ) -> list[WorktreeSummary]: ...

    async def snapshot(self, account: str, host: str, key: str, scope: Scope) -> Snapshot: ...

    def cached(self, account: str, host: str, key: str, scope: str, revision: str) -> Snapshot: ...

    async def file_diff(
        self, snap: Snapshot, row: int, file_id: str, page: int, *, load: bool = False
    ) -> tuple[FileDiff, int]: ...

    async def commits(self, snap: Snapshot, skip: int, limit: int) -> tuple[Commit, ...]: ...

    async def graph(
        self,
        account: str,
        host: str,
        key: str,
        mode: GraphMode,
        *,
        revision: str | None = None,
        offset: int = 0,
    ) -> GraphResult: ...

    async def commit_snapshot(
        self, account: str, host: str, key: str, graph_revision: str, mode: GraphMode, oid: str
    ) -> Snapshot: ...

    async def bases(
        self, account: str, host: str, key: str
    ) -> tuple[tuple[str, str, str], ...]: ...

    async def set_base(self, account: str, host: str, key: str, ref: str) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class GitReadPort:
    """The service for `host_id` and a lookup of agents' pane folders, run on the source's loop."""

    host_id: str
    service: ReadService
    folders: Callable[[Sequence[str]], dict[str, str | None]]


def source_folders(source: object) -> Callable[[Sequence[str]], dict[str, str | None]]:
    """Folder lookup over a connector source's `pane_folder`; unknown agents map to None."""

    def lookup(agent_ids: Sequence[str]) -> dict[str, str | None]:
        pane_folder = getattr(source, "pane_folder", None)
        out: dict[str, str | None] = {}
        for agent_id in agent_ids:
            found = pane_folder(agent_id) if pane_folder is not None else None
            out[agent_id] = found[2] if found is not None else None
        return out

    return lookup


__all__ = [
    "BaseStore",
    "ChangesError",
    "ChangesService",
    "FileRecord",
    "GitReadPort",
    "GitRunner",
    "GitUnavailable",
    "Scope",
    "Snapshot",
    "display",
    "file_totals",
    "source_folders",
]
