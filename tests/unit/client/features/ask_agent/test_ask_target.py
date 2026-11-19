"""Selection resolution preserves sides and rejects incomplete or stale context."""

from dataclasses import replace

import pytest

from relaydeck.client.features.ask_agent.model import build_prompt
from relaydeck.client.features.ask_agent.target import target, validate
from relaydeck.client.features.changes.state import ChangesModel, DiffWindow
from relaydeck.contracts.git import DiffLine, FileDiff, Hunk
from tests.support.git_client.entries import entry


def model(*lines: DiffLine, old: int = 1, new: int = 1, fragment: int = 0) -> ChangesModel:
    hunk = Hunk(
        header="sentinel header",
        old_start=8,
        old_lines=old,
        new_start=4,
        new_lines=new,
        fragment=fragment,
        lines=lines,
    )
    diff = FileDiff(file_id="a", snapshot="snapshot", render="ok", hunks=(hunk,))
    return ChangesModel(
        condition="ok",
        snapshot="snapshot",
        head="a" * 40,
        comparison_ready=True,
        files=(replace(entry("a"), old_path="old/a.txt"),),
        selected_file="a",
        diff=DiffWindow("a", "snapshot", pages=((0, diff),)),
    )


def test_context_deletion_uses_new_side_but_deleted_line_uses_old_path() -> None:
    current = model(
        DiffLine(kind="context", old=8, new=4, text="sentinel code"),
        DiffLine(kind="delete", old=9, text="sentinel secret"),
        old=2,
    )
    hunk = target(current, "hunk", page=0, hunk=0)
    assert (hunk.start, hunk.end, hunk.old) == (4, 4, False)
    line = target(current, "line", page=0, hunk=0, row=1)
    assert (line.start, line.end, line.old, line.path) == (9, 9, True, "old/a.txt")
    text = build_prompt(hunk, "Why?")
    assert "sentinel" not in text and "secret" not in text and "code" not in text
    assert validate(replace(current, snapshot="new"), hunk)
    assert validate(replace(current, selected_file=None), hunk)


def test_pure_deletion_and_no_newline_marker() -> None:
    current = model(
        DiffLine(kind="delete", old=8, text="sentinel"),
        DiffLine(kind="no_newline", text="sentinel marker"),
        new=0,
    )
    scope = target(current, "hunk", page=0, hunk=0)
    assert scope.old and scope.start == scope.end == 8
    with pytest.raises(ValueError):
        target(current, "line", page=0, hunk=0, row=1)


@pytest.mark.parametrize("fragment", [0, 1])
def test_partial_and_masked_hunks_but_visible_line_and_file(fragment: int) -> None:
    current = model(
        DiffLine(kind="add", new=4, text="visible"),
        DiffLine(kind="add", new=5, text="sentinel", masked=True),
        old=0,
        new=3,
        fragment=fragment,
    )
    with pytest.raises(ValueError):
        target(current, "hunk", page=0, hunk=0)
    assert target(current, "file").kind == "file"
    assert target(current, "line", page=0, hunk=0, row=0).start == 4
    with pytest.raises(ValueError):
        target(current, "line", page=0, hunk=0, row=1)
    with pytest.raises(ValueError):
        target(replace(current, files=(replace(current.files[0], withheld=True),)), "file")


def test_branch_needs_base_working_unborn_and_binary_file_metadata() -> None:
    current = model()
    with pytest.raises(ValueError):
        target(replace(current, scope="branch"))
    assert "empty tree" in build_prompt(target(replace(current, head=None)), "Why?")
    assert target(replace(current, files=(replace(current.files[0], renderable=False),)), "file")


def commit_model(*lines: DiffLine, **kwargs: int) -> ChangesModel:
    """The inner model of a commit open in the graph: scope "commit", the commit as its head."""
    return replace(model(*lines, **kwargs), scope="commit", base_revision="b" * 40)


def test_a_commit_is_asked_about_as_a_whole_or_by_file_hunk_and_line() -> None:
    current = commit_model(
        DiffLine(kind="context", old=8, new=4, text="sentinel code"),
        DiffLine(kind="delete", old=9, text="sentinel secret"),
        old=2,
    )
    whole = target(current, "commit")
    assert (whole.kind, whole.commit, whole.path) == ("commit", "a" * 40, None)
    for kind, place, lines in (
        ("file", {}, (None, None)),
        ("hunk", {"page": 0, "hunk": 0}, (4, 4)),
        ("line", {"page": 0, "hunk": 0, "row": 1}, (9, 9)),
    ):
        scope = target(current, kind, **place)
        assert scope.kind == kind and scope.commit == "a" * 40
        assert (scope.start, scope.end) == lines
        assert scope.base == "b" * 40 and scope.head == scope.commit
        prompt = build_prompt(scope, "Why?")
        assert prompt.startswith("About ") and f"(Commit: {'a' * 8}): Why?" in prompt
        assert "sentinel" not in prompt
        assert scope.label.endswith(f"Commit {'a' * 8}")
    assert target(current, "line", page=0, hunk=0, row=1).path == "old/a.txt"  # the old side


def test_a_commit_selection_is_refused_when_incomplete_stale_or_unreadable() -> None:
    current = commit_model(
        DiffLine(kind="add", new=4, text="visible"),
        DiffLine(kind="add", new=5, text="sentinel", masked=True),
        old=0,
        new=3,
        fragment=1,
    )
    with pytest.raises(ValueError):
        target(current, "hunk", page=0, hunk=0)  # not the whole hunk
    with pytest.raises(ValueError):
        target(current, "line", page=0, hunk=0, row=1)  # hidden
    file_scope = target(current, "file")
    assert validate(current, file_scope) == ""
    assert validate(replace(current, selected_file=None), file_scope)
    assert validate(replace(current, snapshot="newer"), file_scope)
    with pytest.raises(ValueError):
        target(replace(current, files=(replace(current.files[0], withheld=True),)), "file")
    with pytest.raises(ValueError):
        target(replace(current, base_revision=None), "file")
    with pytest.raises(ValueError):
        target(replace(current, head=None), "commit")
