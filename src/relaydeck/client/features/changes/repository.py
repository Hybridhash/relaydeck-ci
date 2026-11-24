"""The Changes screens' view of the gateway: routes in, model types out. Every reply is checked
against its exact shape.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from relaydeck.client.features.changes.model import ChangesPage, Scope
from relaydeck.client.features.changes.source import ChangesFailure
from relaydeck.client.ports.transport import GatewayTransport, TransportError
from relaydeck.contracts.git import (
    BaseCandidate,
    FileDiff,
    FileEntry,
    GraphMode,
    GraphPage,
    WorktreeSummary,
)

INCOMPATIBLE = "upstream_incompatible"
_CHANGES_KEYS = {
    "scope",
    "snapshot",
    "head",
    "base",
    "branch",
    "detached",
    "short_oid",
    "additions",
    "deletions",
    "base_name",
    "commits",
    "partial",
    "as_of_ms",
    "total_files",
    "files",
    "next",
}


class ChangesRepository:
    """Implements `ChangesSource` over a `GatewayTransport`."""

    def __init__(self, transport: GatewayTransport) -> None:
        self._transport = transport

    async def summaries(self, host_id: str, agent_ids: list[str]) -> list[WorktreeSummary]:
        return await _fetch(self._transport.git_summaries(host_id, agent_ids), _summaries)

    async def changes(
        self, host_id: str, worktree_id: str, scope: Scope, cursor: str | None
    ) -> ChangesPage:
        request = self._transport.git_changes(host_id, worktree_id, scope=scope, cursor=cursor)
        return await _fetch(request, lambda doc: _changes(doc, scope))

    async def file_diff(
        self, host_id: str, worktree_id: str, file_id: str, cursor: str | None, load: bool
    ) -> FileDiff:
        request = self._transport.git_file_diff(
            host_id, worktree_id, file_id, cursor=cursor, load=load
        )
        return await _fetch(request, FileDiff.from_dict)

    async def graph(
        self, host_id: str, worktree_id: str, mode: GraphMode, cursor: str | None
    ) -> GraphPage:
        request = self._transport.git_graph(host_id, worktree_id, mode=mode, cursor=cursor)
        return await _fetch(request, lambda doc: _graph(doc, mode))

    async def commit_changes(
        self, host_id: str, worktree_id: str, commit_id: str, cursor: str | None
    ) -> ChangesPage:
        request = self._transport.git_commit_changes(host_id, worktree_id, commit_id, cursor=cursor)
        return await _fetch(request, lambda doc: _changes(doc, "commit"))

    async def bases(
        self, host_id: str, worktree_id: str, cursor: str | None
    ) -> tuple[tuple[BaseCandidate, ...], str | None]:
        request = self._transport.git_bases(host_id, worktree_id, cursor=cursor)
        return await _fetch(request, _bases)

    async def set_base(self, host_id: str, worktree_id: str, base_id: str) -> str:
        request = self._transport.git_set_base(host_id, worktree_id, base_id)
        return await _fetch(request, _base_name)


async def _fetch[T](request: Awaitable[dict[str, Any]], decode: Callable[[dict[str, Any]], T]) -> T:
    try:
        doc = await request
    except TransportError as exc:
        raise _failure(exc) from None
    try:
        return decode(doc)
    except (ValueError, TypeError, KeyError):
        raise ChangesFailure(INCOMPATIBLE) from None


def _failure(exc: TransportError) -> ChangesFailure:
    details = exc.details or {}
    reason, delay = details.get("reason"), details.get("retry_after_s")
    return ChangesFailure(
        reason if isinstance(reason, str) and reason else exc.code,
        float(delay) if isinstance(delay, int | float) and not isinstance(delay, bool) else None,
    )


def _summaries(doc: dict[str, Any]) -> list[WorktreeSummary]:
    return [
        WorktreeSummary.from_dict(item) for item in _list(_only(doc, {"summaries"})["summaries"])
    ]


def _changes(doc: dict[str, Any], scope: Scope) -> ChangesPage:
    if set(doc) != _CHANGES_KEYS or doc["scope"] != scope:
        raise ValueError("changes reply")
    _nullable(doc, "head", "base", "branch", "base_name", "next", kind=str)
    _nullable(doc, "commits", "additions", "deletions", kind=int)
    _nullable(doc, "short_oid", kind=str)
    detached = _exact(doc["detached"], bool)
    snapshot = _exact(doc["snapshot"], str)
    partial = _exact(doc["partial"], bool)
    as_of_ms = _exact(doc["as_of_ms"], int)
    _exact(doc["total_files"], int)
    return ChangesPage(
        snapshot=snapshot,
        files=tuple(FileEntry.from_dict(item) for item in _list(doc["files"])),
        next=doc["next"],
        partial=partial,
        branch=doc["branch"],
        detached=detached,
        authoritative=True,
        head=doc["head"],
        short_oid=doc["short_oid"],
        additions=doc["additions"],
        deletions=doc["deletions"],
        base_name=doc["base_name"],
        base_revision=doc["base"],
        as_of_ms=as_of_ms,
    )


def _graph(doc: dict[str, Any], mode: GraphMode) -> GraphPage:
    page = GraphPage.from_dict(doc)
    if page.mode != mode:
        raise ValueError("graph mode mismatch")
    return page


def _bases(doc: dict[str, Any]) -> tuple[tuple[BaseCandidate, ...], str | None]:
    _only(doc, {"candidates", "next"})
    _nullable(doc, "next", kind=str)
    candidates = tuple(BaseCandidate.from_dict(item) for item in _list(doc["candidates"]))
    return candidates, doc["next"]


def _base_name(doc: dict[str, Any]) -> str:
    return _exact(_only(doc, {"base_name"})["base_name"], str)


def _only(doc: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    if set(doc) != keys:
        raise ValueError("unexpected reply keys")
    return doc


def _list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError("expected a list")
    return value


def _exact[T](value: Any, kind: type[T]) -> T:
    """`value` if it is exactly `kind` (a bool is not an int)."""
    if type(value) is not kind:
        raise ValueError(f"expected {kind.__name__}")
    return value


def _nullable(doc: dict[str, Any], *names: str, kind: type) -> None:
    for name in names:
        if doc[name] is not None:
            _exact(doc[name], kind)
