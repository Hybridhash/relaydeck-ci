"""Stable results and failures at the read-service boundary."""

from __future__ import annotations

from dataclasses import dataclass

from relaydeck.contracts.git import (
    BranchCounts,
    ChangesScope,
    FileStatus,
    GraphMode,
    GraphPage,
    GraphRefKind,
    WorkingCounts,
    WorktreeState,
    WorktreeSummary,
)

Scope = ChangesScope


@dataclass(frozen=True, slots=True)
class GraphLabel:
    oid: str
    name: bytes
    kind: GraphRefKind


@dataclass(frozen=True, slots=True)
class GraphContext:
    account: str
    host: str
    worktree: str
    revision: str
    mode: GraphMode
    head: str
    branch: bytes | None
    base_name: bytes
    base_oid: str
    split_oid: str
    roots: tuple[str, ...]
    labels: tuple[GraphLabel, ...]
    ahead: frozenset[str]
    behind: int | None
    ref_limited: bool
    created: float
    as_of_ms: int
    probe_digest: str = ""
    probe_guard: str = ""
    probe_refs: str | None = None


@dataclass(frozen=True, slots=True)
class GraphResult:
    page: GraphPage
    next_offset: int | None


@dataclass(frozen=True, slots=True)
class FileRecord:
    """One file of a snapshot; paths stay bytes until display."""

    path: bytes
    old_path: bytes | None
    status: FileStatus
    staged: bool
    unstaged: bool
    untracked: bool
    additions: int | None
    deletions: int | None
    generated: bool
    withheld: bool
    reason: str | None  # why the content cannot be shown, or "no_net_diff"

    @property
    def renderable(self) -> bool:
        return self.reason is None


def file_totals(rows: tuple[FileRecord, ...]) -> tuple[int, int]:
    """Changes-screen totals: every listed file with known counts, untracked included."""
    return (
        sum(row.additions or 0 for row in rows),
        sum(row.deletions or 0 for row in rows),
    )


def display(path: bytes) -> str:
    """UTF-8 with undecodable bytes and control characters shown as escapes."""
    text = path.decode("utf-8", "backslashreplace")
    return "".join(ch if ch.isprintable() else f"\\x{ord(ch):02x}" for ch in text)


class ChangesError(RuntimeError):
    """A typed failure for the gateway envelope; never carries paths or git output."""

    def __init__(self, code: str, retry_after_s: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after_s = retry_after_s


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Immutable comparison of one worktree, bound to the account and host that asked for it."""

    account: str
    host: str
    worktree: str
    scope: Scope
    revision: str
    head: str | None
    branch: str | None
    old: str
    base_name: str | None
    commits: int
    rows: tuple[FileRecord, ...]
    partial: bool
    as_of_ms: int
    policy_stamp: str | None = None
    policy_version: str = ""


def summary_state(code: str) -> WorktreeState:
    known: dict[str, WorktreeState] = {
        "no_repo": "no_repo",
        "folder_gone": "folder_gone",
        "unsupported_config": "unsupported_config",
        "git_unavailable": "git_unavailable",
    }
    return known.get(code, "unavailable")


def empty_summary(agent_id: str, code: str, *, worktree: str = "") -> WorktreeSummary:
    state: WorktreeState = "calculating" if code == "calculating" else summary_state(code)
    return WorktreeSummary(
        agent_id=agent_id,
        worktree_id=worktree,
        state=state,
        detached=False,
        working=WorkingCounts(files=0, staged=0, unstaged=0, untracked=0, conflicts=0),
        branch_scope=BranchCounts(),
        shared=False,
        revision="",
        as_of_ms=0,
        partial=False,
    )
