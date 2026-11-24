"""Explicit diff loading belongs to one file, snapshot, scope and worktree."""

from dataclasses import replace

import pytest

from relaydeck.client.features.changes.reading_bookmark import ReadingBookmark
from relaydeck.client.features.changes.state import ChangesModel, DiffWindow


def loaded_model() -> ChangesModel:
    return ChangesModel(
        worktree_id="worktree",
        snapshot="snapshot",
        selected_file="file",
        selected_path="pkg/a.py",
        diff=DiffWindow("file", "snapshot", loaded=True),
    )


@pytest.mark.parametrize("changed", ["file", "snapshot", "worktree", "scope"])
def test_load_decision_is_not_restored_to_another_identity(changed: str) -> None:
    original = loaded_model()
    bookmark = ReadingBookmark.capture(original)
    empty = replace(original, diff=None)
    if changed == "file":
        empty = replace(empty, selected_file="other")
    elif changed == "snapshot":
        empty = replace(empty, snapshot="new")
    elif changed == "worktree":
        empty = replace(empty, worktree_id="other")
    else:
        empty = replace(empty, scope="branch")
    assert bookmark.restore(empty).diff is None


def test_capture_does_not_borrow_another_files_load_decision() -> None:
    model = loaded_model()
    assert ReadingBookmark.capture(model).loaded
    assert not ReadingBookmark.capture(replace(model, selected_file="other")).loaded
    assert not ReadingBookmark.capture(replace(model, snapshot="other")).loaded
