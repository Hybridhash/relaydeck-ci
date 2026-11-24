"""Fetches the open commit's pages and recovers a commit whose id expired before it was opened."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Protocol

import relaydeck.client.features.changes.api as m
from relaydeck.client.features.changes.api import (
    ChangesFailure,
    ChangesSource,
    commit_receive,
    open_commit,
)


class _Host(Protocol):
    source: ChangesSource
    host_id: str
    model: m.ChangesModel

    def apply(self, step: tuple[m.ChangesModel, m.Request | None] | m.ChangesModel) -> None: ...

    def reload(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _Reopen:
    """A commit to open again once one particular graph refresh answers, if the reader is still
    looking at the same graph then."""

    oid: str
    token: int  # the graph request that refreshes the graph
    context: tuple[object, ...]


def _context(model: m.ChangesModel) -> tuple[object, ...]:
    return (
        model.view,
        model.scope,
        model.graph.mode,
        model.agent_id,
        model.worktree_id,
        model.base_name,
        model.base_revision,
    )


class CommitLoader:
    def __init__(self, host: _Host) -> None:
        self._host = host
        self._reopen: _Reopen | None = None
        self._retried: str | None = None  # a commit already refreshed once for an expired id
        self._last = 0  # the newest opening number given out

    def next_generation(self) -> int:
        """A number for one opening of a commit; never given out twice by this loader."""
        self._last += 1
        return self._last

    def resume_after(self, generation: int) -> None:
        """A commit restored from a bookmark keeps its number; later openings go past it."""
        self._last = max(self._last, generation)

    def reader_opened(self) -> None:
        """The reader chose a commit: nothing waits for an earlier choice any more."""
        self._reopen = self._retried = None

    def observe(self) -> None:
        """Drop a pending reopen when its refresh was superseded or failed, or the reader moved
        to another view, scope, graph, agent or base."""
        pending = self._reopen
        if pending is None:
            return
        model = self._host.model
        load = model.graph_load
        if (
            _context(model) != pending.context
            or load.token != pending.token
            or load.status == "error"
        ):
            self._reopen = None

    def graph_arrived(self, token: int) -> None:
        """The first page of a graph arrived. Only the refresh a reopen is waiting for, and only
        while the reader still looks at that graph with nothing else open, opens the commit; a
        commit that is not on the new first page is reported, not searched for."""
        host, pending = self._host, self._reopen
        if pending is None or pending.token != token:
            return  # another response does not consume the intent
        self._reopen = None
        model = host.model
        if (
            model.graph_load.token != token
            or model.graph_load.status != "ready"
            or model.graph.page is None
            or model.view != "graph"
            or model.commit is not None
            or _context(model) != pending.context
        ):
            return
        if any(c.oid == pending.oid for c in model.graph.commits):
            host.apply(
                open_commit(
                    model,
                    model.graph.mode,
                    model.graph.page.snapshot,
                    pending.oid,
                    self.next_generation(),
                )
            )
        else:
            host.apply(replace(model, commit_lost=True))

    async def fetch(self, request: m.Request, worktree: str) -> None:
        """A page of the open commit's files or of one of its file diffs."""
        host, opening = self._host, request.commit
        reading = host.model.commit
        if opening is None or reading is None or reading.opening != opening:
            return
        commit_id, file_id = reading.commit.commit_id, reading.model.selected_file
        if commit_id is None:
            return
        try:
            if request.slot == "files":
                page = await host.source.commit_changes(
                    host.host_id, worktree, commit_id, request.cursor
                )
                more = request.cursor is not None
                host.apply(
                    commit_receive(
                        host.model,
                        opening,
                        lambda i: m.receive_files(i, request.token, page, more=more),
                    )
                )
            elif request.slot == "diff" and file_id is not None:
                diff = await host.source.file_diff(
                    host.host_id, worktree, file_id, request.cursor, request.load
                )
                host.apply(
                    commit_receive(
                        host.model,
                        opening,
                        lambda i: m.receive_diff(i, request.token, diff, request.page or 0),
                    )
                )
        except asyncio.CancelledError:
            raise
        except ChangesFailure as failure:
            self._failed(opening, request, failure.reason)
        except Exception:  # noqa: BLE001 - shown as a fixed state; nothing from the error is kept
            self._failed(opening, request, "unavailable")

    def _failed(self, opening: m.CommitOpening, request: m.Request, reason: str) -> None:
        host = self._host
        host.apply(
            commit_receive(
                host.model, opening, lambda i: m.fail(i, request.slot, request.token, reason)
            )
        )
        reading = host.model.commit
        first_page = request.slot == "files" and request.cursor is None
        if (
            reason == "expired"
            and first_page
            and reading is not None
            and reading.opening == opening
            and self._retried != opening.key.oid
        ):
            # The row was tapped after its id lapsed: read the graph again and open the same commit.
            self._retried = opening.key.oid
            host.reload()
            model = host.model
            if model.view == "graph" and model.graph_load.status == "loading":
                self._reopen = _Reopen(opening.key.oid, model.graph_load.token, _context(model))
