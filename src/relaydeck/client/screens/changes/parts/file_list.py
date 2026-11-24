"""The Changes file list: rows, search, the generated-files group and the phone file picker."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

import flet as ft

from relaydeck.client.design_system import tokens as t
from relaydeck.client.design_system.badges import Badge
from relaydeck.client.design_system.fonts import FAMILY_MONO
from relaydeck.client.design_system.inputs import SearchField
from relaydeck.client.platform.flet.events import event_text
from relaydeck.client.screens.changes.parts.file_tree import tree_rows
from relaydeck.contracts.git import FileEntry

FILE_ROW = 56
INDENT = 16
_BADGES = {
    "A": ("A", "Added", t.DIFF_ADD_SIGN),
    "M": ("M", "Modified", t.PRESENCE_WARN),
    "D": ("D", "Deleted", t.DIFF_DELETE_SIGN),
    "R": ("R", "Renamed", t.PRIMARY_TEXT),
    "U": ("U", "Untracked", t.DIFF_ADD_SIGN),
    "conflict": ("!", "Conflict", t.BLOCKED),
    "binary": ("B", "Binary", t.TEXT_MUTED),
    "submodule": ("S", "Submodule", t.TEXT_MUTED),
    "mode": ("T", "Mode changed", t.TEXT_MUTED),
    "symlink": ("L", "Symbolic link", t.TEXT_MUTED),
}


def file_ref(entry: FileEntry) -> str:
    """A name for the file that survives a new snapshot and reveals no path (for Viewed marks)."""
    material = f"{entry.path}\0{entry.old_path or ''}".encode()
    return hashlib.blake2b(material, digest_size=12).hexdigest()


def filter_files(files: Sequence[FileEntry], query: str) -> list[tuple[int, FileEntry]]:
    """Files whose path (or former path) contains `query`, with their place in the full list."""
    needle = query.strip().casefold()
    return [
        (index, entry)
        for index, entry in enumerate(files)
        if not needle
        or needle in entry.path.casefold()
        or (entry.old_path is not None and needle in entry.old_path.casefold())
    ]


def search_field(
    key: str,
    query: str,
    on_change: Callable[[str], None],
    on_focus: Callable[[bool], None] | None = None,
    *,
    expand: bool = False,
) -> ft.Control:
    return SearchField(
        key=key,
        expand=expand,
        value=query,
        hint_text="Search files",
        hint_style=ft.TextStyle(color=t.TEXT_MUTED),
        color=t.TEXT,
        height=t.MIN_TOUCH,
        text_size=14,
        filled=True,
        fill_color=t.CARD,
        cursor_color=t.PRIMARY_TEXT,
        border={
            ft.ControlState.DEFAULT: ft.OutlineInputBorder(
                border_radius=8, side=ft.BorderSide(1, t.BORDER)
            ),
            ft.ControlState.FOCUSED: ft.OutlineInputBorder(
                border_radius=8, side=ft.BorderSide(2, t.PRIMARY_TEXT)
            ),
        },
        content_padding=ft.Padding.symmetric(horizontal=12),
        on_change=lambda event: on_change(event_text(event)),
        on_focus=(lambda _: on_focus(True)) if on_focus is not None else None,
        on_blur=(lambda _: on_focus(False)) if on_focus is not None else None,
    )


@dataclass(frozen=True, slots=True)
class FileLine:
    """One line of the list, described and not yet built: a file, a folder or the generated group.

    Lines are small immutable values, so the whole list can be held while only the lines near the
    screen become controls."""

    kind: Literal["file", "dir", "group"]
    ordinal: int  # its place among the lines
    index: int = -1  # a file's place in the full list
    entry: FileEntry | None = None
    depth: int = 0
    name: str = ""
    path: str = ""
    count: int = 0
    folded: bool = False
    opened: bool = False


def file_lines(
    matches: Sequence[tuple[int, FileEntry]],
    *,
    generated_open: bool,
    tree: frozenset[str] | None = None,
    open_all: bool = False,
) -> list[FileLine]:
    """Regular files in list order, then one expandable group for generated files and lockfiles.

    With `tree` (the folded directories) the regular files are shown as a directory tree."""
    regular = [(i, e) for i, e in matches if not e.generated]
    generated = [(i, e) for i, e in matches if e.generated]
    lines: list[FileLine] = []
    if tree is not None:
        by_index = dict(regular)
        for node in tree_rows(regular, tree, open_all=open_all):
            ordinal = len(lines)
            if node.kind == "dir":
                folded = not open_all and node.path in tree
                lines.append(
                    FileLine(
                        "dir", ordinal, name=node.name, path=node.path, count=node.count,
                        depth=node.depth, folded=folded,
                    )
                )  # fmt: skip
            else:
                lines.append(
                    FileLine("file", ordinal, node.index, by_index[node.index], depth=node.depth)
                )
    else:
        lines = [FileLine("file", n, i, e) for n, (i, e) in enumerate(regular)]
    if generated:
        lines.append(FileLine("group", len(lines), count=len(generated), opened=generated_open))
        if generated_open:
            lines.extend(
                FileLine("file", len(lines) + n, i, e) for n, (i, e) in enumerate(generated)
            )
    return lines


def dir_row(
    ordinal: int,
    name: str,
    count: int,
    depth: int,
    folded: bool,
    path: str,
    toggle: Callable[[str], None],
) -> ft.Control:
    noun = "file" if count == 1 else "files"
    return ft.Container(
        key=f"changes-dir-{ordinal}",
        height=FILE_ROW,
        padding=ft.Padding.only(left=t.PAD_PHONE + depth * INDENT, right=t.PAD_PHONE),
        on_click=lambda _: toggle(path),
        tooltip=f"Folder {name}, {count} {noun}. {'Show' if folded else 'Hide'}",
        content=ft.Row(
            spacing=8,
            controls=[
                ft.Icon(
                    ft.Icons.CHEVRON_RIGHT if folded else ft.Icons.EXPAND_MORE,
                    size=20,
                    color=t.TEXT_SECONDARY,
                ),
                ft.Text(
                    name + "/",
                    font_family=FAMILY_MONO,
                    size=13,
                    color=t.TEXT_SECONDARY,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    expand=True,
                ),
                ft.Text(str(count), font_family=FAMILY_MONO, size=12, color=t.TEXT_MUTED),
            ],
        ),
    )


def generated_group(count: int, opened: bool, toggle: Callable[[], None]) -> ft.Control:
    noun = "file" if count == 1 else "files"
    return ft.Container(
        key="changes-generated",
        height=FILE_ROW,
        padding=ft.Padding.symmetric(horizontal=t.PAD_PHONE),
        on_click=lambda _: toggle(),
        tooltip=f"Generated {noun}, {count}. {'Hide' if opened else 'Show'}",
        content=ft.Row(
            spacing=8,
            controls=[
                ft.Icon(
                    ft.Icons.EXPAND_LESS if opened else ft.Icons.EXPAND_MORE,
                    size=20,
                    color=t.TEXT_SECONDARY,
                ),
                ft.Text(
                    f"Generated and lock files ({count})",
                    size=13,
                    color=t.TEXT_SECONDARY,
                    expand=True,
                ),
            ],
        ),
    )


def file_row(
    index: int,
    entry: FileEntry,
    selected: bool,
    seen: bool,
    on_pick: Callable[[str], None],
    prefix: str,
    depth: int = 0,
) -> ft.Control:
    letter, meaning, colour = _BADGES.get(entry.status, ("?", entry.status, t.TEXT_MUTED))
    name = entry.path.rsplit("/", 1)[-1]
    folder = entry.path[: len(entry.path) - len(name)]
    was = f"was {entry.old_path.rsplit('/', 1)[-1]}" if entry.old_path else ""
    marks = [s for s, on in (("staged", entry.staged), ("unstaged", entry.unstaged)) if on]
    counts = ""
    figure: list[ft.TextSpan] = []
    if entry.additions is not None and entry.deletions is not None:
        counts = f"+{entry.additions} −{entry.deletions}"
        if entry.additions:
            figure.append(ft.TextSpan(f"+{entry.additions:,}", ft.TextStyle(color=t.DIFF_ADD_SIGN)))
        if entry.deletions:
            gap = " " if figure else ""
            figure.append(
                ft.TextSpan(f"{gap}−{entry.deletions:,}", ft.TextStyle(color=t.DIFF_DELETE_SIGN))
            )
    else:
        figure.append(ft.TextSpan(entry.reason or "", ft.TextStyle(color=t.TEXT_MUTED)))
    label = ", ".join([name, meaning, *marks, counts, "viewed" if seen else ""]).strip(", ")
    return ft.Container(
        key=f"{prefix}-{index}",
        height=FILE_ROW,
        bgcolor=t.CARD_SELECTED if selected else None,
        border=ft.Border(bottom=ft.BorderSide(1, t.BORDER)),
        padding=ft.Padding.only(left=t.PAD_PHONE + depth * INDENT, right=t.PAD_PHONE),
        on_click=lambda _: on_pick(entry.file_id),
        tooltip=label,
        content=ft.Row(
            spacing=12,
            controls=[
                Badge(
                    width=22,
                    height=22,
                    border_radius=5,
                    bgcolor=ft.Colors.with_opacity(0.18, colour),
                    alignment=ft.Alignment.CENTER,
                    content=ft.Text(
                        letter,
                        font_family=FAMILY_MONO,
                        size=12,
                        color=colour,
                        weight=ft.FontWeight.W_700,
                    ),
                ),
                ft.Column(
                    expand=True,
                    spacing=0,
                    alignment=ft.MainAxisAlignment.CENTER,
                    controls=[
                        ft.Text(
                            name,
                            font_family=FAMILY_MONO,
                            size=13,
                            weight=ft.FontWeight.W_700,
                            color=t.TEXT_MUTED if seen or entry.status == "D" else t.TEXT,
                            style=ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH)
                            if entry.status == "D"
                            else None,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.Text(
                            " · ".join(
                                part
                                for part in (
                                    folder,
                                    was,
                                    " ".join(marks),
                                )
                                if part
                            ),
                            font_family=FAMILY_MONO,
                            size=11,
                            color=t.TEXT_MUTED,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                    ],
                ),
                ft.Text(
                    spans=figure,
                    font_family=FAMILY_MONO,
                    size=12,
                    weight=ft.FontWeight.W_700,
                    color=t.TEXT_SECONDARY,
                ),
            ],
        ),
    )
