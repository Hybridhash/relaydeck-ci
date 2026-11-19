"""What can be copied from the resident part of a diff, and when a copy target goes stale."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import relaydeck.client.features.changes.model as m
from relaydeck.client.features.changes.copy_target import (
    CopyTarget,
    hunk_groups,
    resolve,
    target_for,
)
from relaydeck.contracts.git import FileDiff
from relaydeck.git.parser import DiffParser
from relaydeck.git.secret_policy import LineMasker
from tests.support.git_client.entries import entry
from tests.support.git_patches import added_patch, two_hunk_patch
from tests.support.git_pem import read_fixture

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "git"


def with_pages(*pages: tuple[int, FileDiff], snapshot: str = "snap-1") -> m.ChangesModel:
    window = m.DiffWindow("a", snapshot, pages=pages)
    model = m.select(m.ChangesModel(), "agent", "wt")
    return replace(model, snapshot=snapshot, files=(entry("a"),), selected_file="a", diff=window)


def parsed(patch: bytes) -> tuple[FileDiff, ...]:
    return DiffParser().parse(patch, file_id="a", snapshot="snap-1")


def test_a_hunk_whose_lines_all_arrived_is_complete() -> None:
    model = with_pages((0, parsed(two_hunk_patch("a"))[0]))
    first, second = hunk_groups(model)
    assert first.complete and second.complete and not first.partial
    assert first.code == "first\nsecond new\nthird"
    assert first.diff == "@@ -1,3 +1,3 @@\n first\n-second old\n+second new\n third"
    assert second.code.split("\n")[-1] == "twenty-two"
    assert second.diff.split("\n")[2:5] == [
        "-twenty-one old",
        "+twenty-one new",
        "+twenty-one extra",
    ]


def test_a_hunk_split_over_pages_is_whole_only_when_every_page_is_resident() -> None:
    pages = parsed(added_patch("a", 900))
    assert len(pages) == 2
    both = with_pages((0, pages[0]), (1, pages[1]))
    (group,) = hunk_groups(both)
    assert group.complete and len(group.code.split("\n")) == 900
    first_only = with_pages((0, pages[0]))
    (head,) = hunk_groups(first_only)
    assert not head.complete and head.partial and len(head.code.split("\n")) == 500


def test_a_hunk_that_lost_its_start_is_partial_and_keeps_its_own_lines() -> None:
    pages = parsed(added_patch("a", 900))
    (tail,) = hunk_groups(with_pages((1, pages[1])))
    assert tail.partial and (tail.page, tail.hunk) == (1, 0)
    assert tail.code.split("\n")[0] == "line 00500" and len(tail.code.split("\n")) == 400


def test_hidden_lines_make_a_hunk_partial_and_are_never_copied() -> None:
    patch = read_fixture(FIXTURES / "pem_key_pages.diff")
    pages = LineMasker.mask_pages(parsed(patch))
    hidden = [line for page in pages for hunk in page.hunks for line in hunk.lines if line.masked]
    assert hidden
    model = with_pages(*enumerate(pages))
    groups = hunk_groups(model)
    assert any(group.partial for group in groups)
    assert all("•" not in group.code and "•" not in group.diff for group in groups)


def test_a_copy_target_resolves_only_against_the_same_file_snapshot_and_place() -> None:
    model = with_pages((0, parsed(two_hunk_patch("a"))[0]))
    group = hunk_groups(model)[1]
    hunk = target_for(model, group)
    line = target_for(model, group, group.lines[1])
    assert hunk is not None and line is not None
    assert resolve(model, hunk) == group.code and resolve(model, line) == "twenty-one old"
    as_diff = target_for(model, group, form="diff")
    assert as_diff is not None and resolve(model, as_diff) == group.diff
    assert resolve(replace(model, selected_file="b"), hunk) is None
    newer = with_pages((0, parsed(two_hunk_patch("a"))[0]), snapshot="snap-2")
    assert resolve(newer, hunk) is None
    assert resolve(model, CopyTarget("a", "snap-1", 0, 9)) is None  # no such hunk
    assert resolve(model, CopyTarget("a", "snap-1", 0, 1, 99)) is None  # no such row
    assert resolve(replace(model, diff=None), hunk) is None


def test_the_no_newline_marker_is_not_text_to_copy() -> None:
    patch = (FIXTURES / "no_newline.diff").read_bytes()
    model = with_pages((0, parsed(patch)[0]))
    (group,) = hunk_groups(model)
    assert all("No newline" not in line for line in group.code.split("\n"))
    assert group.diff.split("\n")[-1].startswith("\\ No newline")
