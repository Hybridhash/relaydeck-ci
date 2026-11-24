"""The file list as a window onto its lines: the list, the phone's picker and a commit's files."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as t
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.design_system.controls import PillButton
from relaydeck.client.design_system.lists.virtual_list import Place, Row, VirtualList
from relaydeck.client.screens.changes.parts.file_list import (
    FILE_ROW,
    FileLine,
    dir_row,
    file_lines,
    file_row,
    filter_files,
    generated_group,
    search_field,
)
from relaydeck.contracts.git import FileEntry

# A file line always carries its entry; this stands in only to satisfy the type of the field.
_NO_FILE = FileEntry(
    file_id="",
    path="",
    status="M",
    staged=False,
    unstaged=False,
    generated=False,
    withheld=False,
    rename_limited=False,
    renderable=False,
)


@ft.component
def FileListView(
    *,
    name: str,
    files: Sequence[FileEntry],
    query: str,
    selected: str | None,
    seen: Callable[[FileEntry], bool],
    on_pick: Callable[[str], None],
    on_toggle_generated: Callable[[], None],
    generated_open: bool,
    prefix: str = "changes-file",
    tree: frozenset[str] | None = None,
    on_toggle_dir: Callable[[str], None] | None = None,
    footer: Sequence[ft.Control] = (),
    expand: bool = True,
    place: Place | None = None,
    on_scroll: Callable[[Place], None] | None = None,
) -> ft.Control:
    """Only the lines near the screen are built; selecting one rebuilds the two that changed.

    `place` is where the list opens (a row and how far into it); `on_scroll` is told the row at
    the top as the reader scrolls, so a caller can keep the place while the list is off screen.
    """
    needle = bool(query.strip())
    # Rows are cached across renders, so what they call is read at the time of the click.
    latest: Any = ft.use_ref(None)
    latest.current = (on_pick, on_toggle_generated, on_toggle_dir, seen)
    lines: list[FileLine] = ft.use_memo(
        lambda: file_lines(
            filter_files(files, query),
            generated_open=generated_open or needle,
            tree=tree if on_toggle_dir is not None else None,
            open_all=needle,
        ),
        [files, query, tree, generated_open],
    )

    def row(i: int) -> Row:
        line = lines[i]
        if line.kind == "dir":

            def toggle(path: str) -> None:
                folding = latest.current[2]
                if folding is not None:  # a tree always has its folding callback
                    folding(path)

            return Row(
                f"dir:{line.path}",
                line,
                lambda: dir_row(
                    line.ordinal, line.name, line.count, line.depth, line.folded, line.path, toggle
                ),
            )
        if line.kind == "group":
            return Row(
                "generated",
                line,
                lambda: generated_group(line.count, line.opened, lambda: latest.current[1]()),
            )
        entry: FileEntry = line.entry or _NO_FILE
        picked = entry.file_id == selected
        viewed = latest.current[3](entry)
        return Row(
            entry.file_id,
            (line, picked, viewed, prefix),
            lambda: file_row(
                line.index,
                entry,
                picked,
                viewed,
                lambda id: latest.current[0](id),
                prefix,
                line.depth,
            ),
        )

    extra = list(footer)
    if not lines:
        extra.insert(
            0,
            ft.Container(
                height=FILE_ROW,
                padding=ft.Padding.symmetric(horizontal=16),
                alignment=ft.Alignment.CENTER_LEFT,
                content=ft.Text("No file matches.", color=t.TEXT_MUTED),
            ),
        )
    return VirtualList(
        name=name,
        count=len(lines),
        height=FILE_ROW,
        row=row,
        context=(name, query, tree is not None),
        footer=extra,
        expand=expand,
        place=place,
        on_scroll=on_scroll,
    )


def picker_sheet(
    files: Sequence[FileEntry],
    query: str,
    *,
    selected: str | None,
    seen: Callable[[FileEntry], bool],
    generated_open: bool,
    on_query: Callable[[str], None],
    on_toggle_generated: Callable[[], None],
    on_pick: Callable[[str], None],
    on_close: Callable[[], None],
    more: Callable[[], None] | None,
) -> ft.Control:
    """The phone's "pick a file" sheet: search, then the same rows as the list."""
    footer: list[ft.Control] = []
    if more is not None:
        footer.append(
            Button(
                "Show more files", variant="text", key="changes-picker-more", height=t.MIN_TOUCH,
                on_click=lambda _: more(),
            )
        )  # fmt: skip
    return ft.Container(
        key="changes-picker",
        bgcolor=t.RAISED,
        padding=12,
        height=420,
        content=ft.Column(
            spacing=6,
            controls=[
                ft.Row(
                    controls=[
                        ft.Text(
                            "Choose a file",
                            size=16,
                            weight=ft.FontWeight.W_700,
                            color=t.TEXT,
                            expand=True,
                        ),
                        PillButton("Close", on_close, key="changes-picker-close"),
                    ]
                ),
                search_field("changes-picker-search", query, on_query),
                FileListView(
                    name="changes-picker-list",
                    files=files,
                    query=query,
                    selected=selected,
                    seen=seen,
                    on_pick=on_pick,
                    prefix="changes-picker-file",
                    generated_open=generated_open,
                    on_toggle_generated=on_toggle_generated,
                    footer=footer,
                ),
            ],
        ),
    )
