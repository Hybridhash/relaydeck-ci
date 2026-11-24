"""An open commit in a Graph model: builders for the commit model and runner tests."""

from __future__ import annotations

import itertools
from dataclasses import replace

import relaydeck.client.features.changes.model as m
from relaydeck.client.features.changes.commit_model import commit_receive, open_commit
from relaydeck.client.features.changes.graph_layout import GraphRow, append, seed
from relaydeck.client.features.changes.graph_state import GraphState
from relaydeck.contracts.git import FileEntry, GraphMode
from tests.support.changes_model import loaded
from tests.support.graph_pages import graph_commit, graph_page

REVISION = "graph-1"
_NUMBERS = itertools.count(1)


def fresh() -> int:
    """A new opening number, as the screen's loader would hand out."""
    return next(_NUMBERS)


def entry(file_id: str, path: str = "pkg/a.py") -> FileEntry:
    return FileEntry(
        file_id=file_id,
        path=path,
        status="M",
        staged=False,
        unstaged=False,
        additions=1,
        deletions=0,
        generated=False,
        withheld=False,
        rename_limited=False,
        renderable=True,
    )


def graph_model(*, ids: bool = True) -> m.ChangesModel:
    model, request = m.set_view(loaded("a", scope="branch"), "graph")
    assert request is not None
    commits = tuple(
        graph_commit(n, parents=(n + 1,), commit_id=f"id-{n}" if ids else None) for n in (1, 2)
    )
    return m.receive_graph(model, request.token, graph_page(commits, snapshot=REVISION))


def opened(number: int = 1) -> tuple[m.ChangesModel, m.Request]:
    model, request = open_commit(graph_model(), "all", REVISION, f"{number:040x}", fresh())
    assert request is not None
    return model, request


def arrive(model: m.ChangesModel, request: m.Request) -> m.ChangesModel:
    assert request.commit is not None
    page = m.ChangesPage(
        snapshot="commit-1",
        files=(entry("f1"), entry("f2", "pkg/b.py")),
        head=request.commit.key.oid,
        base_revision="p" * 40,
        authoritative=True,
        as_of_ms=1,
    )
    return commit_receive(
        model, request.commit, lambda inner: m.receive_files(inner, request.token, page)
    )


def resident_graph(count: int, *, mode: GraphMode = "all", ids: bool = True) -> GraphState:
    """A graph with `count` linear commits resident, built page by page as the client does."""
    commits = tuple(
        graph_commit(
            n, parents=(n + 1,) if n < count else (), commit_id=f"cid:{mode}:r:{n}" if ids else None
        )
        for n in range(1, count + 1)
    )
    checkpoint = seed(mode, commits[0].oid, commits[-1].oid, commits[-1].oid)
    rows: list[GraphRow] = []
    checkpoints = []
    for start in range(0, count, 100):
        chunk, checkpoint = append(checkpoint, commits[start : start + 100])
        rows.extend(chunk)
        checkpoints.append(checkpoint)
    page = replace(
        graph_page(commits[:100], mode=mode, snapshot="graph-1"),
        head=commits[0].oid,
        base_oid=commits[-1].oid,
        split_oid=commits[-1].oid,
        next="more",
    )
    return GraphState(
        mode=mode,
        page=page,
        commits=commits,
        rows=tuple(rows),
        checkpoints=tuple(checkpoints),
        next="more",
    )
