"""Builders for ChangesModel tests: file entries, list pages, diff pages and loaded models."""

from __future__ import annotations

from typing import Any

import relaydeck.client.features.changes.model as m
from relaydeck.client.features.changes.model import ChangesModel, ChangesPage
from relaydeck.contracts.git import (
    BranchCounts,
    DiffLine,
    FileDiff,
    FileEntry,
    Hunk,
    WorkingCounts,
    WorktreeSummary,
)


def entry(file_id: str) -> FileEntry:
    return FileEntry(
        file_id=file_id,
        path=f"dir/{file_id}.txt",
        status="M",
        staged=False,
        unstaged=True,
        generated=False,
        withheld=False,
        rename_limited=False,
        renderable=True,
    )


def listing(
    *ids: str, snapshot: str = "snap-1", next: str | None = None, **over: Any
) -> ChangesPage:
    return ChangesPage(snapshot=snapshot, files=tuple(entry(i) for i in ids), next=next, **over)


def diff_page(
    file_id: str = "a",
    rows: int = 3,
    next: str | None = None,
    snapshot: str = "snap-1",
    start: int = 1,
) -> FileDiff:
    lines = tuple(DiffLine(kind="add", new=start + i, text=f"row {i}") for i in range(rows))
    hunk = Hunk(
        header="@@ -0,0 +1 @@", old_start=0, old_lines=0, new_start=1, new_lines=rows, lines=lines
    )
    return FileDiff(file_id=file_id, snapshot=snapshot, render="ok", hunks=(hunk,), next=next)


def summary(**over: Any) -> WorktreeSummary:
    fields: dict[str, Any] = {
        "agent_id": "agent-1",
        "worktree_id": "wt-1",
        "state": "ok",
        "branch": "feature",
        "detached": False,
        "short_oid": "abc1234",
        "base_name": "origin/main",
        "working": WorkingCounts(files=1, staged=0, unstaged=1, untracked=0, conflicts=0),
        "branch_scope": BranchCounts(revision="brev-1"),
        "shared": False,
        "revision": "rev-1",
        "as_of_ms": 5,
        "partial": False,
    }
    return WorktreeSummary(**{**fields, **over})


def loaded(*ids: str, scope: m.Scope = "working", **over: Any) -> ChangesModel:
    """A model whose first list page has arrived."""
    seen = "brev-1" if scope == "branch" else "rev-1"
    model = m.note_revision(m.select(ChangesModel(), "agent-1", "wt-1", scope), seen)
    model, request = m.request_files(model)
    assert request is not None
    return m.receive_files(model, request.token, listing(*ids, **over))


def with_diff(model: ChangesModel, file_id: str, *pages: FileDiff) -> ChangesModel:
    model = m.select_file(model, file_id)
    for index, page in enumerate(pages):
        model, request = m.request_diff(model)
        assert request is not None and request.page == index
        model = m.receive_diff(model, request.token, page, index)
    return model


def stream(pages: int, rows: int) -> ChangesModel:
    """A file of `pages` pages of `rows` rows, all loaded in order."""
    model = m.select_file(loaded("a"), "a")
    for index in range(pages):
        model, request = m.request_diff(model)
        assert request is not None and request.page == index
        nxt = f"c{index + 1}" if index + 1 < pages else None
        model = m.receive_diff(
            model, request.token, diff_page("a", rows, nxt, start=index * rows + 1), index
        )
        assert m.resident_rows(model) <= m.MAX_RESIDENT_ROWS
    return model
