"""File list helpers: search, Viewed names that hide paths, and the age shown in the header."""

from __future__ import annotations

import pytest

from relaydeck.client.screens.changes.parts.file_list import file_ref, filter_files
from relaydeck.client.screens.changes.parts.header import age_text
from relaydeck.contracts.git import FileEntry


def entry(path: str, old_path: str | None = None) -> FileEntry:
    return FileEntry(
        file_id=f"id-{path}",
        path=path,
        old_path=old_path,
        status="M",
        staged=False,
        unstaged=True,
        generated=False,
        withheld=False,
        rename_limited=False,
        renderable=True,
    )


FILES = (entry("src/Alpha.py"), entry("src/beta.py", old_path="src/gamma.py"), entry("README.md"))


def test_search_matches_any_part_of_the_path_ignoring_case() -> None:
    assert [i for i, _ in filter_files(FILES, "alpha")] == [0]
    assert [i for i, _ in filter_files(FILES, "SRC/")] == [0, 1]
    assert [i for i, _ in filter_files(FILES, "")] == [0, 1, 2]
    assert [i for i, _ in filter_files(FILES, "  ")] == [0, 1, 2]
    assert filter_files(FILES, "nothing") == []


def test_search_also_finds_a_renamed_file_by_its_old_path() -> None:
    assert [i for i, _ in filter_files(FILES, "gamma")] == [1]


def test_search_keeps_each_files_place_in_the_full_list() -> None:
    assert [(i, e.path) for i, e in filter_files(FILES, "md")] == [(2, "README.md")]


def test_a_viewed_name_is_stable_across_snapshots_and_reveals_no_path() -> None:
    first = file_ref(entry("src/secret_name.py"))
    moved = file_ref(
        FileEntry.from_dict({**entry("src/secret_name.py").to_dict(), "file_id": "other-id"})
    )
    assert first == moved  # a new snapshot gives the file a new id, not a new name
    assert "secret" not in first and "src" not in first
    assert file_ref(entry("src/other.py")) != first
    assert file_ref(entry("new.py", "old.py")) != file_ref(entry("new.py"))


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0, "just now"),
        (4, "just now"),
        (5, "5 s ago"),
        (59, "59 s ago"),
        (60, "1 min ago"),
        (3599, "59 min ago"),
        (3600, "1 h ago"),
        (86_399, "23 h ago"),
        (86_400, "1 d ago"),
        (-30, "just now"),  # a clock a little ahead of the Mac's
    ],
)
def test_age_is_shown_in_the_coarsest_honest_unit(seconds: int, text: str) -> None:
    now = 10_000_000_000
    assert age_text(now - seconds * 1000, now) == text


def test_an_unknown_time_reads_as_now() -> None:
    assert age_text(None, 1) == "just now"
