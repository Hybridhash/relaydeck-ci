"""The wide layout's file tree."""

from __future__ import annotations

from relaydeck.client.screens.changes.parts.file_list import filter_files
from relaydeck.client.screens.changes.parts.file_tree import tree_rows
from relaydeck.contracts.git import FileEntry


def entry(path: str) -> FileEntry:
    return FileEntry(
        file_id=f"id-{path}",
        path=path,
        status="M",
        staged=False,
        unstaged=True,
        generated=False,
        withheld=False,
        rename_limited=False,
        renderable=True,
    )


FILES = tuple(
    entry(p)
    for p in (
        "README.md",
        "src/app/a.py",
        "src/app/b.py",
        "src/lib/deep/c.py",
        "src/lib/deep/d.py",
    )
)


def rows(collapsed: frozenset[str] = frozenset(), query: str = "") -> list[tuple[str, int, str]]:
    matches = filter_files(FILES, query)
    return [
        (r.kind, r.depth, r.name)
        for r in tree_rows(matches, collapsed, open_all=bool(query.strip()))
    ]


def test_directories_hold_their_files_in_list_order() -> None:
    assert rows() == [
        ("file", 0, "README.md"),
        ("dir", 0, "src"),
        ("dir", 1, "app"),
        ("file", 2, "a.py"),
        ("file", 2, "b.py"),
        ("dir", 1, "lib/deep"),  # a directory holding only a directory is shown joined
        ("file", 2, "c.py"),
        ("file", 2, "d.py"),
    ]


def test_a_folded_directory_hides_its_contents_and_reports_how_many_files_it_holds() -> None:
    nodes = tree_rows(filter_files(FILES, ""), frozenset({"src/app"}))
    folded = next(r for r in nodes if r.path == "src/app")
    assert folded.count == 2 and [r.name for r in nodes if r.depth == 2][:1] == ["c.py"]
    assert tree_rows(filter_files(FILES, ""), frozenset({"src"}))[-1].name == "src"


def test_a_search_opens_every_directory_and_keeps_only_the_paths_that_match() -> None:
    assert rows(frozenset({"src", "src/app"}), "c.py") == [
        ("dir", 0, "src/lib/deep"),
        ("file", 1, "c.py"),
    ]


def test_files_keep_their_place_in_the_whole_list() -> None:
    indexes = [
        r.index for r in tree_rows(filter_files(FILES, "b.py"), frozenset()) if r.kind == "file"
    ]
    assert indexes == [2]
