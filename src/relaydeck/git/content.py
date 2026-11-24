"""Bounded file capture, side-aware masking and diff parsing."""

from __future__ import annotations

import os
from dataclasses import replace

from relaydeck.contracts.git import FileDiff, RenderState
from relaydeck.git.locator import Worktree
from relaydeck.git.parser import DiffParser
from relaydeck.git.probe import ChangeProbe
from relaydeck.git.reader import MAX_SIDE_BYTES, RawChange, ReadError, RepositoryReader
from relaydeck.git.results import ChangesError, Snapshot
from relaydeck.git.results import FileRecord as Row
from relaydeck.git.safe_fs import Unreadable, open_contained, read_contained
from relaydeck.git.secret_policy import LineMasker, UnsafeKeyContext

UNTRACKED_SCAN_BYTES = 64 * 1024 * 1024


def capture_diff(
    reader: RepositoryReader, worktree: Worktree, snap: Snapshot, row: int, base_key: str | None
) -> tuple[bytes, tuple[bytes, bytes]] | RenderState:
    if snap.scope == "commit":
        return capture_commit(reader, worktree, snap, row)
    probe = ChangeProbe(worktree, policy_version=snap.policy_version)
    if probe.probe(reader, base_key).revision != snap.revision:
        raise ChangesError("stale_snapshot")
    entry = snap.rows[row]
    sides: tuple[bytes, bytes] | None
    try:
        if entry.untracked:
            sides = (b"", read_contained(worktree.root, entry.path, MAX_SIDE_BYTES))
            patch = reader.untracked_patch(entry.path)
        else:
            paths = (entry.path,) if entry.old_path is None else (entry.old_path, entry.path)
            new = snap.head if snap.scope == "branch" else None
            changes = reader.raw(snap.old, new, paths)
            if too_large(reader, worktree, new, changes):
                return "too_large"
            sides = _sides(reader, snap.old, new, paths, changes)
            if sides is None:
                return "withheld"
            patch = reader.patch(snap.old, new, paths)
    except ReadError as exc:
        if str(exc) in ("truncated", "too_large"):
            return "too_large"
        if str(exc) == "invalid_encoding":
            return "invalid_encoding"
        raise ChangesError("unavailable") from exc
    except Unreadable as exc:
        if str(exc) == "too_large":
            return "too_large"
        raise ChangesError("unavailable") from exc
    if probe.probe(reader, base_key).revision != snap.revision:
        raise ChangesError("stale_snapshot")
    return patch, sides


def capture_commit(
    reader: RepositoryReader,
    worktree: Worktree,
    snap: Snapshot,
    row: int,
) -> tuple[bytes, tuple[bytes, bytes]] | RenderState:
    if snap.head is None:
        raise ChangesError("unavailable")
    entry = snap.rows[row]
    if entry.untracked:
        raise ChangesError("unavailable")
    paths = (entry.path,) if entry.old_path is None else (entry.old_path, entry.path)
    try:
        changes = reader.raw(snap.old, snap.head, paths)
        if too_large(reader, worktree, snap.head, changes):
            return "too_large"
        sides = _sides(reader, snap.old, snap.head, paths, changes)
        if sides is None:
            return "withheld"
        patch = reader.patch(snap.old, snap.head, paths)
    except ReadError as exc:
        if str(exc) in ("truncated", "too_large"):
            return "too_large"
        if str(exc) == "invalid_encoding":
            return "invalid_encoding"
        raise ChangesError("unavailable") from None
    return patch, sides


def _sides(
    reader: RepositoryReader,
    old: str,
    new: str | None,
    paths: tuple[bytes, ...],
    changes: tuple[RawChange, ...],
) -> tuple[bytes, bytes] | None:
    try:
        return reader.sides(old, new, paths, changes=changes)
    except ReadError as exc:
        if str(exc) == "unsupported":
            return None
        raise


def parse_pages(
    patch: bytes, sides: tuple[bytes, bytes], file_id: str, revision: str
) -> tuple[FileDiff, ...]:
    try:
        masker = LineMasker.from_sides(*sides)
    except UnsafeKeyContext:
        return (render_only(file_id, revision, "withheld"),)
    return DiffParser().parse(patch, file_id=file_id, snapshot=revision, masker=masker)


def render_only(file_id: str, snapshot: str, render: RenderState) -> FileDiff:
    return FileDiff(file_id=file_id, snapshot=snapshot, render=render, hunks=())


def too_large(
    reader: RepositoryReader,
    worktree: Worktree,
    new: str | None,
    changes: tuple[RawChange, ...],
) -> bool:
    """True when either side of the file is above MAX_SIDE_BYTES."""
    for change in changes:
        if change.old_oid.strip("0") and reader.blob_size(change.old_oid) > MAX_SIDE_BYTES:
            return True
        if change.new_oid.strip("0"):
            if reader.blob_size(change.new_oid) > MAX_SIDE_BYTES:
                return True
        elif new is None:
            try:
                fd, info = open_contained(worktree.root, change.path)
            except Unreadable:
                continue
            os.close(fd)
            if info.st_size > MAX_SIDE_BYTES:
                return True
    return False


def with_untracked_lines(worktree: Worktree, rows: tuple[Row, ...]) -> tuple[Row, ...]:
    """Line counts for untracked regular text files, within a total scan budget."""
    budget = UNTRACKED_SCAN_BYTES
    out: list[Row] = []
    for row in rows:
        if not row.untracked or row.withheld or budget <= 0:
            out.append(row)
            continue
        try:
            data = read_contained(worktree.root, row.path, min(MAX_SIDE_BYTES, budget))
        except Unreadable:
            out.append(row)
            continue
        budget -= len(data)
        if b"\0" in data:
            out.append(replace(row, status="binary", reason="binary"))
            continue
        lines = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
        out.append(replace(row, additions=lines, deletions=0))
    return tuple(out)
