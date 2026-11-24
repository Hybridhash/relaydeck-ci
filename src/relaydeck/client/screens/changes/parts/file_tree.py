"""The wide layout's file tree: directories that fold, built from the (filtered) file list.
Pure.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from relaydeck.contracts.git import FileEntry


@dataclass(frozen=True, slots=True)
class TreeRow:
    kind: Literal["dir", "file"]
    depth: int
    name: str
    path: str = ""  # a directory's full path; its identity for folding
    index: int = -1  # a file's place in the full list
    count: int = 0  # files under a directory


@dataclass(slots=True)
class _Dir:
    name: str
    path: str
    items: list[_Dir | tuple[int, FileEntry]] = field(default_factory=list)
    dirs: dict[str, _Dir] = field(default_factory=dict)

    def files(self) -> int:
        return sum(item.files() if isinstance(item, _Dir) else 1 for item in self.items)


def tree_rows(
    matches: Sequence[tuple[int, FileEntry]], collapsed: frozenset[str], *, open_all: bool = False
) -> list[TreeRow]:
    root = _Dir("", "")
    for index, entry in matches:
        *folders, _name = entry.path.split("/")
        node = root
        for depth, folder in enumerate(folders):
            child = node.dirs.get(folder)
            if child is None:
                child = _Dir(folder, "/".join(folders[: depth + 1]))
                node.dirs[folder] = child
                node.items.append(child)
            node = child
        node.items.append((index, entry))
    rows: list[TreeRow] = []
    _walk(root, 0, collapsed, open_all, rows)
    return rows


def _walk(
    node: _Dir, depth: int, collapsed: frozenset[str], open_all: bool, rows: list[TreeRow]
) -> None:
    for item in node.items:
        if not isinstance(item, _Dir):
            index, entry = item
            rows.append(TreeRow("file", depth, entry.path.rsplit("/", 1)[-1], index=index))
            continue
        joined = item
        name = joined.name
        while len(joined.items) == 1 and isinstance(joined.items[0], _Dir):
            joined = joined.items[0]
            name = f"{name}/{joined.name}"
        rows.append(TreeRow("dir", depth, name, joined.path, count=joined.files()))
        if open_all or joined.path not in collapsed:
            _walk(joined, depth + 1, collapsed, open_all, rows)
