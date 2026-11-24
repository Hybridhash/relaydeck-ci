"""Bounded resident diff pages, cursor chains and reading-position transitions."""

from __future__ import annotations

from dataclasses import replace

from relaydeck.client.features.changes.loads import _current, _ready, _start
from relaydeck.client.features.changes.state import (
    MAX_ANCHORS,
    MAX_RESIDENT_ROWS,
    Anchor,
    ChangesModel,
    DiffWindow,
    Load,
    Request,
)
from relaydeck.contracts.git import FileDiff


def select_file(model: ChangesModel, file_id: str) -> ChangesModel:
    if file_id == model.selected_file or all(f.file_id != file_id for f in model.files):
        return model
    entry = next(f for f in model.files if f.file_id == file_id)
    return replace(
        model,
        selected_file=file_id,
        selected_hunk=None,
        selected_path=entry.path,
        diff=None,
        diff_load=Load(model.diff_load.token + 1),
    )


def step_file(model: ChangesModel, delta: int) -> ChangesModel:
    """Previous (-1) or next (1) file in list order; the ends stay put."""
    ids = [f.file_id for f in model.files]
    if model.selected_file not in ids:
        return model
    target = ids.index(model.selected_file) + delta
    return select_file(model, ids[target]) if 0 <= target < len(ids) else model


def resident_pages(model: ChangesModel) -> tuple[FileDiff, ...]:
    return tuple(page for _, page in model.diff.pages) if model.diff else ()


def _rows(page: FileDiff) -> int:
    return sum(len(hunk.lines) for hunk in page.hunks)


def resident_rows(model: ChangesModel) -> int:
    return sum(_rows(page) for page in resident_pages(model))


def _opening(model: ChangesModel, file_id: str, snapshot: str) -> tuple[DiffWindow, int]:
    """A window for a file that has none yet, and the page to start with.

    A file the reader was part-way through opens at the page they left, using the cursors kept
    with their position; any other file starts at the first page.
    """
    anchor = anchor_for(model, file_id)
    if anchor is not None and 0 < anchor.page < len(anchor.cursors):
        return DiffWindow(file_id, snapshot, cursors=anchor.cursors), anchor.page
    return DiffWindow(file_id, snapshot), 0


def request_diff(
    model: ChangesModel, *, page: int | None = None
) -> tuple[ChangesModel, Request | None]:
    """Fetch the next page of the selected file, or page `page` again after it was evicted."""
    if model.selected_file is None or model.snapshot is None:
        return model, None
    window = model.diff
    first = 0
    if window is None or window.file_id != model.selected_file:
        window, first = _opening(model, model.selected_file, model.snapshot)
        if page is None:
            page = first
    elif page is None:
        if window.complete or model.diff_load.status == "loading":
            return model, None
        page = window.last_page + 1  # the page after the last one held
        anchor = anchor_for(model, model.selected_file)
        if not window.pages and anchor is not None and 0 <= anchor.page < len(window.cursors):
            page = anchor.page
    if page < 0 or page >= len(window.cursors):
        return model, None
    if any(index == page for index, _ in window.pages):
        return model, None
    started, token = _start(replace(model, diff=window), "diff")
    cursor = window.cursors[page]
    return started, Request("diff", token, cursor=cursor, page=page, load=window.loaded)


def load_diff(model: ChangesModel) -> tuple[ChangesModel, Request | None]:
    """The reader chose "Load diff": the gate page is replaced by the file's real page.

    The choice belongs to this file in this snapshot and rides on every page requested after it.
    """
    window = model.diff
    if window is None or window.file_id != model.selected_file or not window.pages:
        return model, None
    index, gate = window.pages[-1]
    if gate.render != "load_required":
        return model, None
    opened = replace(
        window,
        pages=window.pages[:-1],
        end=None,
        render=None,
        loaded=True,
    )
    return request_diff(replace(model, diff=opened), page=index)


def earlier_page(model: ChangesModel) -> int | None:
    """The page just before the first resident one, when the window was cut or entered mid-file."""
    window = model.diff
    if window is None or not window.pages:
        return None
    before = window.pages[0][0] - 1
    return before if 0 <= before < len(window.cursors) else None


def receive_diff(model: ChangesModel, token: int, page: FileDiff, index: int) -> ChangesModel:
    window = model.diff
    if not _current(model, "diff", token) or window is None:
        return model
    if page.file_id != window.file_id:
        return model
    if page.snapshot != window.snapshot:
        return replace(_ready(model, "diff"), updated_available=True)
    cursors = window.cursors
    if index == len(cursors) - 1 and page.next is not None:
        cursors = cursors + (page.next,)
    pages = _insert(window.pages, index, page)
    window = replace(
        window,
        pages=_evict(pages, index),
        cursors=cursors,
        end=index if page.next is None else window.end,
        render=page.render if index == 0 or page.render != "ok" else window.render,
    )
    return _rebased(replace(_ready(model, "diff"), diff=window))


def _insert(
    pages: tuple[tuple[int, FileDiff], ...], index: int, page: FileDiff
) -> tuple[tuple[int, FileDiff], ...]:
    if not pages:
        return ((index, page),)
    if index == pages[-1][0] + 1:
        return pages + ((index, page),)
    if index == pages[0][0] - 1:
        return ((index, page),) + pages
    return ((index, page),)  # not adjacent to what is resident: keep only the new page


def _evict(
    pages: tuple[tuple[int, FileDiff], ...], newest: int
) -> tuple[tuple[int, FileDiff], ...]:
    kept = list(pages)
    while len(kept) > 1 and sum(_rows(page) for _, page in kept) > MAX_RESIDENT_ROWS:
        kept.pop(0 if kept[0][0] != newest else -1)
    return tuple(kept)


def set_anchor(model: ChangesModel, anchor: Anchor) -> ChangesModel:
    """Remember where the reader is in a file; the least recently used files are forgotten."""
    window = model.diff
    if not anchor.cursors and window is not None and window.file_id == anchor.file_id:
        anchor = replace(anchor, cursors=window.cursors)
    kept = tuple(a for a in model.anchors if a.file_id != anchor.file_id) + (anchor,)
    return replace(model, anchors=kept[-MAX_ANCHORS:])


def anchor_for(model: ChangesModel, file_id: str) -> Anchor | None:
    return next((a for a in model.anchors if a.file_id == file_id), None)


def page_to_reload(model: ChangesModel) -> int | None:
    """The page holding the selected file's anchored row when it is no longer resident."""
    window = model.diff
    if window is None or model.selected_file is None:
        return None
    anchor = anchor_for(model, model.selected_file)
    if anchor is None or any(index == anchor.page for index, _ in window.pages):
        return None
    return anchor.page


def _rebased(model: ChangesModel) -> ChangesModel:
    """Move the reader's anchor to the edge of the pages now held when its page was dropped.

    Reading forward continues at the first row of the new page, reading back at the last row.
    """
    window, file_id = model.diff, model.selected_file
    anchor = anchor_for(model, file_id) if file_id is not None else None
    if window is None or anchor is None or not window.pages:
        return model
    if any(index == anchor.page for index, _ in window.pages):
        return model
    forward = anchor.page < window.pages[0][0]
    index, page = window.pages[0] if forward else window.pages[-1]
    if not page.hunks:
        return model
    hunk_at = 0 if forward else len(page.hunks) - 1
    hunk = page.hunks[hunk_at]
    if forward:
        row = -1 if hunk.fragment == 0 else 0
    else:
        row = len(hunk.lines) - 1
    edge = Anchor(anchor.file_id, index, hunk_at, hunk.fragment, row, window.cursors)
    return set_anchor(model, edge)
