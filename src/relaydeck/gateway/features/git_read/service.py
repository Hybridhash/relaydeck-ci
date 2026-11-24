"""Cursor lifetime and wire-safe file entries for immutable repository reads."""

from __future__ import annotations

from relaydeck.contracts.git import FileEntry
from relaydeck.git.api import FileRecord, display

CURSOR_TTL_MS = 15 * 60_000


def file_entry(row: FileRecord, file_id: str) -> FileEntry:
    return FileEntry(
        file_id=file_id,
        path=display(row.path),
        old_path=display(row.old_path) if row.old_path is not None else None,
        status=row.status,
        staged=row.staged,
        unstaged=row.unstaged,
        additions=row.additions,
        deletions=row.deletions,
        generated=row.generated,
        withheld=row.withheld,
        rename_limited=False,
        renderable=row.renderable,
        reason=row.reason,
    )
