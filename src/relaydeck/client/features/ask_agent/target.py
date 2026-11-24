"""Resolve exact resident identities to metadata, never to copied code."""

from __future__ import annotations

from dataclasses import replace

from relaydeck.client.features.ask_agent.model import AskScope, build_prompt
from relaydeck.client.features.changes.api import ChangesModel, hunk_groups
from relaydeck.contracts.git import DiffLine


def target(
    model: ChangesModel,
    kind: str = "branch",
    *,
    page: int | None = None,
    hunk: int | None = None,
    row: int | None = None,
) -> AskScope:
    if model.scope == "commit":
        return _commit_target(model, kind, page, hunk, row)
    if model.snapshot is None or model.condition != "ok":
        raise ValueError("Wait for changes to load.")
    if model.head is None and not model.comparison_ready:
        raise ValueError("Comparison is unavailable.")
    scope = AskScope("branch", model.scope, model.snapshot, model.head, model.base_revision)
    if kind == "branch":
        build_prompt(scope, "Question")
        return scope
    return _selection(model, scope, kind, page, hunk, row)


def _selection(
    model: ChangesModel,
    scope: AskScope,
    kind: str,
    page: int | None,
    hunk: int | None,
    row: int | None,
) -> AskScope:
    """The file, hunk or line open in the diff, named inside `scope`'s comparison."""
    entry = next((f for f in model.files if f.file_id == model.selected_file), None)
    if entry is None or entry.withheld:
        raise ValueError("This file is unavailable for questions.")
    scope = replace(scope, kind="file", file_id=entry.file_id, path=entry.path)
    if kind == "file":
        build_prompt(scope, "Question")
        return scope
    window = model.diff
    if window is None or window.snapshot != model.snapshot:
        raise ValueError("Wait for the diff to load.")
    group = next((g for g in hunk_groups(model) if (g.page, g.hunk) == (page, hunk)), None)
    places: list[tuple[int, int, int]]
    if row is None:
        if group is None or group.partial:
            raise ValueError("Load the complete visible hunk first.")
        places = [(line.page, line.hunk, line.row) for line in group.lines]
    else:
        places = [(page if page is not None else -1, hunk if hunk is not None else -1, row)]
    lines: list[DiffLine] = []
    for p, h, r in places:
        diff = next((d for index, d in window.pages if index == p), None)
        if diff is None or diff.render != "ok" or not 0 <= h < len(diff.hunks):
            raise ValueError("This selection is unavailable.")
        rows = diff.hunks[h].lines
        if not 0 <= r < len(rows):
            raise ValueError("This selection is unavailable.")
        line = rows[r]
        if line.masked:
            raise ValueError("Hidden lines cannot be included.")
        if line.kind != "no_newline":
            lines.append(line)
    new = [line.new for line in lines if line.new is not None]
    old = [line.old for line in lines if line.old is not None]
    numbers = new or old
    if not numbers:
        raise ValueError("No visible line numbers.")
    scope = replace(
        scope,
        kind="line" if row is not None else "hunk",
        start=min(numbers),
        end=max(numbers),
        old=not bool(new),
        path=entry.path if new else entry.old_path or entry.path,
        page=page,
        hunk=hunk,
        row=row,
    )
    build_prompt(scope, "Question")
    return scope


def _commit_target(
    model: ChangesModel, kind: str, page: int | None, hunk: int | None, row: int | None
) -> AskScope:
    """The commit open in the graph, or a file, hunk or line of it."""
    if model.snapshot is None or model.condition != "ok" or model.head is None:
        raise ValueError("Wait for the commit to load.")
    if model.base_revision is None:
        raise ValueError("Comparison is unavailable.")
    scope = AskScope(
        "commit", "branch", model.snapshot, model.head, model.base_revision, commit=model.head
    )
    if kind in ("branch", "commit"):
        build_prompt(scope, "Question")
        return scope
    return _selection(model, scope, kind, page, hunk, row)


def validate(model: ChangesModel, scope: AskScope) -> str:
    try:
        current = target(model, scope.kind, page=scope.page, hunk=scope.hunk, row=scope.row)
        if current == scope and (
            scope.kind != "hunk" or model.selected_hunk == (scope.page, scope.hunk)
        ):
            return ""
    except ValueError:
        pass
    return "The review context changed. Open the question again."
