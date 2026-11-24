"""The file list of one snapshot: status and diffstat merged into ordered rows."""

from __future__ import annotations

import fnmatch

from relaydeck.contracts.git import BranchCounts, FileStatus, WorkingCounts
from relaydeck.git.reader import Entry, NumStat, RawChange
from relaydeck.git.results import FileRecord as Row
from relaydeck.git.results import display as display
from relaydeck.git.results import file_totals as file_totals
from relaydeck.git.secret_policy import SecretPolicy

GENERATED = (
    "uv.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Package.resolved",
    "Cargo.lock",
    "poetry.lock",
    "*.min.js",
    "*.min.css",
)
_SYMLINK = "120000"
_GITLINK = "160000"


def working_rows(
    entries: tuple[Entry, ...], stats: tuple[NumStat, ...], policy: SecretPolicy
) -> tuple[Row, ...]:
    """Union of staged, unstaged, untracked and unmerged entries; HEAD → working tree counts."""
    by_path = {stat.path: stat for stat in stats}
    renamed_from = {stat.old_path for stat in stats if stat.old_path is not None}
    rows: list[Row] = []
    seen: set[bytes] = set()
    for entry in entries:
        if entry.path in seen or (entry.path in renamed_from and entry.path not in by_path):
            continue
        seen.add(entry.path)
        stat = by_path.get(entry.path)
        old = stat.old_path if stat is not None else entry.old_path
        status = _status(entry, stat)
        rows.append(
            _row(
                entry.path,
                old,
                status,
                staged=entry.staged,
                unstaged=entry.unstaged,
                untracked=entry.kind == "?",
                stat=stat,
                policy=policy,
                net_empty=stat is None and entry.kind not in ("?", "u") and not entry.submodule,
            )
        )
    return _ordered(rows)


def branch_rows(
    changes: tuple[RawChange, ...], stats: tuple[NumStat, ...], policy: SecretPolicy
) -> tuple[Row, ...]:
    """Tree-to-tree changes from raw kinds/modes and their diffstat."""
    by_path = {stat.path: stat for stat in stats}
    rows = [
        _row(
            change.path,
            change.old_path,
            _raw_status(change, by_path.get(change.path)),
            staged=False,
            unstaged=False,
            untracked=False,
            stat=by_path.get(change.path),
            policy=policy,
            net_empty=False,
        )
        for change in changes
    ]
    return _ordered(rows)


def _raw_status(change: RawChange, stat: NumStat | None) -> FileStatus:
    modes = (change.old_mode, change.new_mode)
    if _GITLINK in modes:
        return "submodule"
    if _SYMLINK in modes:
        return "symlink"
    if change.status == "R":
        return "R"
    if change.status == "A":
        return "A" if stat is None or stat.additions is not None else "binary"
    if change.status == "D":
        return "D" if stat is None or stat.additions is not None else "binary"
    if stat is not None and stat.additions is None:
        return "binary"
    if change.status == "T" or (
        change.old_mode != change.new_mode
        and stat is not None
        and stat.additions == stat.deletions == 0
    ):
        return "mode"
    return "M"


def working_counts(rows: tuple[Row, ...]) -> WorkingCounts:
    tracked = [row for row in rows if not row.untracked and row.status != "conflict"]
    return WorkingCounts(
        files=len(rows),
        staged=sum(row.staged for row in rows),
        unstaged=sum(row.unstaged and not row.untracked for row in rows),
        untracked=sum(row.untracked for row in rows),
        conflicts=sum(row.status == "conflict" for row in rows),
        additions=sum(row.additions or 0 for row in tracked),
        deletions=sum(row.deletions or 0 for row in tracked),
    )


def branch_counts(rows: tuple[Row, ...], commits: int) -> BranchCounts:
    return BranchCounts(
        files=len(rows),
        additions=sum(row.additions or 0 for row in rows),
        deletions=sum(row.deletions or 0 for row in rows),
        commits=commits,
    )


def _status(entry: Entry, stat: NumStat | None) -> FileStatus:
    if entry.kind == "u":
        return "conflict"
    if entry.kind == "?":
        return "U"
    if entry.submodule or _GITLINK in (entry.mode_head, entry.mode_worktree):
        return "submodule"
    if stat is not None and stat.old_path is not None:
        return "R"
    if _SYMLINK in (entry.mode_head, entry.mode_worktree):
        return "symlink"
    if stat is not None and stat.additions is None:
        return "binary"
    if entry.mode_head == "000000":
        return "A"
    if entry.mode_worktree == "000000":
        return "D"
    if (
        stat is not None
        and stat.additions == stat.deletions == 0
        and entry.mode_head != entry.mode_worktree
    ):
        return "mode"
    return "M"


def _row(
    path: bytes,
    old: bytes | None,
    status: FileStatus,
    *,
    staged: bool,
    unstaged: bool,
    untracked: bool,
    stat: NumStat | None,
    policy: SecretPolicy,
    net_empty: bool,
) -> Row:
    withheld = policy.withheld_any(path, old)
    return Row(
        path=path,
        old_path=old,
        status=status,
        staged=staged,
        unstaged=unstaged,
        untracked=untracked,
        additions=0 if net_empty else (stat.additions if stat is not None else None),
        deletions=0 if net_empty else (stat.deletions if stat is not None else None),
        generated=_generated(path),
        withheld=withheld,
        reason=_reason(status, withheld, net_empty),
    )


def _reason(status: FileStatus, withheld: bool, net_empty: bool) -> str | None:
    if withheld:
        return "withheld"
    if status in ("conflict", "binary", "submodule", "mode", "symlink"):
        return status
    return "no_net_diff" if net_empty else None


def _generated(path: bytes) -> bool:
    name = path.rsplit(b"/", 1)[-1].decode("utf-8", "replace")
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in GENERATED)


def _ordered(rows: list[Row]) -> tuple[Row, ...]:
    return tuple(sorted(rows, key=lambda row: (row.status != "conflict", row.path)))
