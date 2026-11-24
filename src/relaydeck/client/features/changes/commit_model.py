"""Pure transitions for the commit open in the Graph tab. A commit has its own file list and
diff, kept in a `ChangesModel` of scope "commit", so the file, diff and copy transitions are
the ones the lists already use.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import relaydeck.client.features.changes.model as m
from relaydeck.client.features.changes.graph_model import select_graph
from relaydeck.client.features.changes.state import (
    ChangesModel,
    CommitKey,
    CommitOpening,
    CommitReading,
    Load,
    Request,
)
from relaydeck.contracts.git import GraphMode

Step = Callable[[ChangesModel], ChangesModel | tuple[ChangesModel, Request | None]]


def open_commit(
    model: ChangesModel, mode: GraphMode, revision: str, oid: str, generation: int
) -> tuple[ChangesModel, Request | None]:
    """Select a graph row and open its commit; a row with no commit id is only selected.

    `generation` is allocated by whoever owns the screen's requests and is never handed out twice,
    so a reply to an earlier opening of this commit cannot be taken for this one's."""
    selected = select_graph(model, mode, revision, oid)
    if selected is model:
        return model, None
    selected = replace(selected, commit_lost=False)
    commit = next(c for c in model.graph.commits if c.oid == oid)
    key = CommitKey(mode, revision, oid)
    if commit.commit_id is None or (model.commit is not None and model.commit.key == key):
        return selected, None
    inner = ChangesModel(
        agent_id=model.agent_id,
        worktree_id=model.worktree_id,
        scope="commit",
        condition="calculating",
        head=oid,
    )
    opened = replace(selected, commit=CommitReading(key, commit, inner, generation))
    return commit_step(opened, m.request_files)


def close_commit(model: ChangesModel) -> ChangesModel:
    return replace(model, commit=None)


def commit_step(model: ChangesModel, step: Step) -> tuple[ChangesModel, Request | None]:
    """Apply a file, diff or copy transition to the open commit, marking its requests as its own."""
    reading = model.commit
    if reading is None:
        return model, None
    result = step(reading.model)
    inner, request = result if isinstance(result, tuple) else (result, None)
    if inner is reading.model and request is None:
        return model, None
    changed = replace(model, commit=replace(reading, model=inner))
    return changed, None if request is None else replace(request, commit=reading.opening)


def commit_receive(model: ChangesModel, opening: CommitOpening, step: Step) -> ChangesModel:
    """Apply a reply, success or failure, to the opening that asked, if it is still the one open."""
    if model.commit is None or model.commit.opening != opening:
        return model
    return commit_step(model, step)[0]


def resume(model: ChangesModel) -> tuple[ChangesModel, Request | None]:
    """Bring a commit restored from a bookmark back to life: nothing it was waiting for arrives."""
    reading = model.commit
    if reading is None:
        return model, None
    inner = reading.model
    idle = replace(
        inner,
        files_load=Load(inner.files_load.token + 1),
        diff_load=Load(inner.diff_load.token + 1),
    )
    revived = replace(model, commit=replace(reading, model=idle))
    if idle.snapshot is None:
        return commit_step(revived, m.request_files)
    if idle.selected_file is not None and not m.resident_pages(idle):
        return commit_step(revived, m.request_diff)
    return revived, None
