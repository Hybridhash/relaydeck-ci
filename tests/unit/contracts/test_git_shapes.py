"""The changes shapes round-trip through JSON and reject anything malformed."""

from __future__ import annotations

import json
from typing import Any

import pytest

from relaydeck.contracts.git import (
    BaseCandidate,
    BranchCounts,
    ChangesPage,
    Commit,
    DiffLine,
    FileDiff,
    FileEntry,
    GraphCommit,
    GraphPage,
    GraphRef,
    Hunk,
    Shape,
    WorkingCounts,
    WorktreeSummary,
)
from tests.support.graph_pages import graph_commit, graph_page


def test_graph_shapes_round_trip_without_identity_metadata() -> None:
    commit = graph_commit(1)
    commit = GraphCommit(
        **{**commit.to_dict(), "parents": (), "refs": (GraphRef(name="main", kind="base"),)}
    )
    page = graph_page((commit,))
    assert GraphPage.from_dict(json.loads(json.dumps(page.to_dict()))) == page
    assert "commit_id" not in commit.to_dict()
    for field in ("author", "email", "committer", "body"):
        with pytest.raises(ValueError):
            GraphCommit.from_dict({**commit.to_dict(), field: "hidden"})


def test_issued_graph_id_and_commit_changes_keep_explicit_nullable_keys() -> None:
    commit = GraphCommit.from_dict({**graph_commit(1).to_dict(), "commit_id": "sealed-id"})
    assert GraphCommit.from_dict(commit.to_dict()) == commit
    page = ChangesPage(
        scope="commit",
        snapshot="revision",
        head="a" * 40,
        base="b" * 40,
        branch=None,
        detached=False,
        short_oid="a" * 12,
        additions=1,
        deletions=0,
        base_name=None,
        commits=None,
        partial=False,
        as_of_ms=1,
        total_files=1,
        files=(_entry(),),
        next=None,
    )
    wire = json.loads(json.dumps(page.to_dict()))
    assert {k for k, v in wire.items() if v is None} == {"branch", "base_name", "commits", "next"}
    assert ChangesPage.from_dict(wire) == page
    with pytest.raises(ValueError):
        ChangesPage.from_dict({**wire, "scope": "HEAD~1"})
    with pytest.raises(ValueError):
        ChangesPage.from_dict({k: v for k, v in wire.items() if k != "next"})
    for token in ("", "x" * 8193, True):
        with pytest.raises(ValueError):
            GraphCommit.from_dict({**commit.to_dict(), "commit_id": token})


@pytest.mark.parametrize(
    "over",
    [
        {"mode": "other"},
        {"head": "HEAD~1"},
        {"ahead": True},
        {"ahead": -1},
        {"behind": False},
        {"commits": [{}]},
        {"partial": False, "limit_reason": "ref_limit"},
        {"limit_reason": "unknown"},
        {"as_of_ms": False},
    ],
)
def test_graph_page_rejects_invalid_fields(over: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        GraphPage.from_dict({**graph_page().to_dict(), **over})


def _line(**over: Any) -> DiffLine:
    return DiffLine(**{"kind": "add", "new": 4, "text": "value = new", "marks": ((8, 11),), **over})


def _diff(**over: Any) -> FileDiff:
    hunk = Hunk(
        header="@@ -1,2 +1,2 @@ section",
        old_start=1,
        old_lines=2,
        new_start=1,
        new_lines=2,
        fragment=1,
        lines=(
            DiffLine(kind="context", old=1, new=1, text="keep"),
            DiffLine(kind="delete", old=2, text="value = old", marks=((8, 11),)),
            _line(),
            DiffLine(kind="no_newline", text="\\ No newline at end of file"),
        ),
    )
    return FileDiff(
        **{
            "file_id": "f1",
            "snapshot": "s1",
            "old_mode": "100644",
            "new_mode": "100755",
            "render": "ok",
            "hunks": (hunk,),
            "next": "cursor",
            "truncated": True,
            **over,
        }
    )


def _summary(**over: Any) -> WorktreeSummary:
    return WorktreeSummary(
        **{
            "agent_id": "a1",
            "worktree_id": "w1",
            "state": "ok",
            "branch": "feature",
            "detached": False,
            "short_oid": "abc1234",
            "base_name": "origin/main",
            "working": WorkingCounts(
                files=3, staged=1, unstaged=2, untracked=1, conflicts=0, additions=10, deletions=4
            ),
            "branch_scope": BranchCounts(files=2, additions=5, deletions=1, commits=3),
            "shared": True,
            "revision": "r1",
            "as_of_ms": 1_700_000_000_000,
            "partial": False,
            **over,
        }
    )


def _entry(**over: Any) -> FileEntry:
    return FileEntry(
        **{
            "file_id": "f1",
            "path": "dir/new.txt",
            "old_path": "dir/old.txt",
            "status": "R",
            "staged": True,
            "unstaged": False,
            "additions": 2,
            "deletions": 1,
            "generated": False,
            "withheld": False,
            "rename_limited": True,
            "renderable": True,
            "reason": "limited",
            **over,
        }
    )


EVERY_SHAPE: list[Shape] = [
    _line(),
    _line(old=None, marks=()),
    _diff(),
    _diff(old_mode=None, new_mode=None, next=None, truncated=False, render="binary", hunks=()),
    _summary(),
    _summary(branch=None, short_oid=None, base_name=None, branch_scope=BranchCounts()),
    _entry(),
    _entry(old_path=None, additions=None, deletions=None, reason=None, status="conflict"),
    Commit(oid="a" * 40, parents=("b" * 40, "c" * 40), subject="Change", time_ms=1),
    Commit(oid="a" * 40, parents=(), subject="Root", time_ms=0),
    BaseCandidate(base_id="b1", name="origin/main", kind="remote_default", oid="d" * 40),
    WorkingCounts(files=0, staged=0, unstaged=0, untracked=0, conflicts=0),
    BranchCounts(files=1, additions=2, deletions=3, commits=4),
]


@pytest.mark.parametrize("shape", EVERY_SHAPE, ids=lambda s: type(s).__name__)
def test_every_shape_round_trips_through_json(shape: Shape) -> None:
    wire = json.loads(json.dumps(shape.to_dict()))
    assert type(shape).from_dict(wire) == shape


def test_unset_optional_fields_are_left_out_of_the_wire() -> None:
    wire = DiffLine(kind="context", old=1, new=1, text="x").to_dict()
    assert wire == {
        "kind": "context",
        "old": 1,
        "new": 1,
        "text": "x",
        "marks": [],
        "masked": False,
    }
    assert "old_path" not in _entry(old_path=None).to_dict()


def test_marks_are_lists_of_pairs_on_the_wire_and_tuples_in_memory() -> None:
    assert _line().to_dict()["marks"] == [[8, 11]]
    assert DiffLine.from_dict({"kind": "add", "text": "abcdefghijk", "marks": [[8, 11]]}).marks == (
        (8, 11),
    )


def test_missing_optional_fields_take_their_defaults() -> None:
    line = DiffLine.from_dict({"kind": "add", "text": "x"})
    assert (line.old, line.new, line.marks, line.masked) == (None, None, (), False)


def test_unknown_keys_are_rejected_at_every_depth() -> None:
    wire = _diff().to_dict()
    with pytest.raises(ValueError, match="unknown key 'extra'"):
        FileDiff.from_dict({**wire, "extra": 1})
    wire["hunks"][0]["lines"][0]["extra"] = 1
    with pytest.raises(ValueError, match="unknown key 'extra'"):
        FileDiff.from_dict(wire)


@pytest.mark.parametrize(
    ("sample", "patch"),
    [
        (_summary(), {"state": "done"}),
        (_entry(), {"status": "X"}),
        (_diff(), {"render": "pending"}),
        (_line(), {"kind": "move"}),
        (BaseCandidate(base_id="b", name="n", kind="local", oid="o"), {"kind": "tag"}),
    ],
    ids=["state", "status", "render", "kind", "base_kind"],
)
def test_a_value_outside_an_enum_is_rejected(sample: Shape, patch: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="not one of"):
        type(sample).from_dict({**sample.to_dict(), **patch})


@pytest.mark.parametrize(
    "wire",
    [
        {"oid": 1, "parents": [], "subject": "s", "time_ms": 1},
        {"oid": "a", "parents": "ab", "subject": "s", "time_ms": 1},
        {"oid": "a", "parents": [1], "subject": "s", "time_ms": 1},
        {"oid": "a", "parents": [], "subject": "s", "time_ms": True},
        {"oid": "a", "parents": [], "subject": "s", "time_ms": "1"},
        {"oid": "a", "parents": [], "subject": "s", "time_ms": 1.5},
        {"oid": "a", "parents": [], "subject": "s"},
        {"oid": "a", "parents": None, "subject": "s", "time_ms": 1},
    ],
    ids=[
        "str_as_int",
        "str_as_list",
        "int_in_list",
        "bool_as_int",
        "text_as_int",
        "float",
        "missing",
        "null",
    ],
)
def test_wrong_types_and_missing_fields_are_rejected(wire: dict[str, Any]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - the message differs per case
        Commit.from_dict(wire)


@pytest.mark.parametrize("doc", [None, [], "x", 3])
def test_a_document_that_is_not_an_object_is_rejected(doc: Any) -> None:
    with pytest.raises(ValueError, match="expected an object"):
        FileDiff.from_dict(doc)


def test_a_bool_is_not_accepted_where_text_or_numbers_are_expected() -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        DiffLine.from_dict({"kind": "add", "text": "x", "masked": 1})
    with pytest.raises(ValueError):  # noqa: PT011
        DiffLine.from_dict({"kind": "add", "text": True})


@pytest.mark.parametrize(
    "marks",
    [
        [[2, 1]],
        [[1, 1]],
        [[0, 9]],
        [[-1, 2]],
        [[0, 2], [1, 3]],
        [[3, 4], [0, 1]],
        [[1]],
        [[1, 2, 3]],
    ],
    ids=["reversed", "empty", "past_end", "negative", "overlap", "unordered", "short", "long"],
)
def test_marks_must_be_ordered_ranges_inside_the_text(marks: list[list[int]]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        DiffLine.from_dict({"kind": "add", "text": "abcd", "marks": marks})


def test_marks_count_code_points_not_utf16_units() -> None:
    text = "\U0001f600 ok"
    assert DiffLine(kind="add", text=text, marks=((2, 4),)).marks == ((2, 4),)
    with pytest.raises(ValueError):  # noqa: PT011
        DiffLine(kind="add", text=text, marks=((2, 5),))


def test_shapes_are_immutable() -> None:
    line = _line()
    with pytest.raises(AttributeError):
        line.text = "other"  # type: ignore[misc]
