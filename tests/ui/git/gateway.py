"""A stand-in for the gateway's git routes, answering the way the companion does.

It speaks the wire shapes (so the real repository adapter decodes every reply) from a mutable
synthetic worktree: the worktree id is stable, the snapshot changes with the content, file ids
belong to one snapshot, and diffs are paged from real patches by the real parser.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import Any

from relaydeck.client.ports.transport import TransportError
from relaydeck.contracts.git import (
    BaseCandidate,
    BranchCounts,
    FileEntry,
    GraphCommit,
    GraphPage,
    GraphRef,
    WorkingCounts,
    WorktreeSummary,
)
from relaydeck.domain.models import scope_key
from relaydeck.git.parser import DiffParser
from tests.support.git_patches import two_hunk_patch

HOST, AGENT = "h1", "t1"
KEY = scope_key(HOST, AGENT)
WORKTREE = "wt-stable"
LOAD_GATE_ROWS = 1000
LIST_PAGE = 100
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


@dataclass(slots=True)
class FileSpec:
    path: str
    patch: bytes
    status: str = "M"
    generated: bool = False


@dataclass(slots=True)
class Gateway:
    files: list[FileSpec]
    revision: str = "rev-1"
    state: str = "ok"
    clock_ms: int = field(default_factory=lambda: int(time.time() * 1000) - 12_000)
    commit_total: int = 5
    graph_page_size: int = 2
    graph_revision: str = ""
    graph_partial: bool = False
    calls: list[tuple[Any, ...]] = field(default_factory=list)
    failures: dict[str, TransportError] = field(default_factory=dict)
    base: str | None = "origin/main"
    base_names: list[str] = field(default_factory=lambda: ["origin/main", "origin/develop"])
    base_page: int = 100
    commit_files: dict[int, list[FileSpec]] = field(default_factory=dict)
    commit_page: int = LIST_PAGE
    issue_commit_ids: bool = True
    merge_at: int | None = None  # that commit is a merge of its next two commits
    fail_once: dict[str, TransportError] = field(default_factory=dict)

    # -- the world changing ------------------------------------------------------------------

    def edit(self, *files: FileSpec) -> None:
        """The agent changed files: a new revision, so new snapshot and file ids."""
        self.files = list(files) if files else self.files
        self.revision = f"rev-{int(self.revision.split('-')[1]) + 1}"
        self.clock_ms += 1_000

    def snapshot(self, scope: str) -> str:
        """Branch snapshots carry the base too, so they differ from the Working revision."""
        return self.revision if scope == "working" else f"{self.revision}+{self.base}"

    def count(self, name: str) -> int:
        return sum(1 for call in self.calls if call[0] == name)

    def _fail(self, name: str) -> None:
        once = self.fail_once.pop(name, None)
        if once is not None:
            raise once
        failure = self.failures.get(name)
        if failure is not None:
            raise failure

    # -- the routes (same names and keywords as the transport) --------------------------------

    async def git_summaries(self, host_id: str, agent_ids: Sequence[str]) -> dict[str, Any]:
        self.calls.append(("summaries", tuple(agent_ids)))
        self._fail("summaries")
        return {"summaries": [self.summary(agent).to_dict() for agent in agent_ids]}

    def summary(self, agent: str = AGENT) -> WorktreeSummary:
        ok = self.state == "ok"
        return WorktreeSummary(
            agent_id=agent,
            worktree_id=WORKTREE if ok else "",
            state=self.state,  # type: ignore[arg-type]
            branch="feature" if ok else None,
            detached=False,
            base_name=self.base if ok else None,
            working=WorkingCounts(
                files=len(self.files), staged=0, unstaged=1, untracked=0, conflicts=0
            ),
            branch_scope=BranchCounts(
                commits=self.commit_total,
                revision=self.snapshot("branch") if ok and self.base else None,
            ),
            shared=False,
            revision=self.revision if ok else "",  # the service sends none with a state
            as_of_ms=self.clock_ms if ok else 0,
            partial=False,
        )

    def entries(self, scope: str = "working") -> list[FileEntry]:
        return [
            FileEntry(
                file_id=f"{self.snapshot(scope)}:{index}",
                path=spec.path,
                status=spec.status,  # type: ignore[arg-type]
                staged=False,
                unstaged=True,
                additions=3,
                deletions=1,
                generated=spec.generated,
                withheld=False,
                rename_limited=False,
                renderable=True,
            )
            for index, spec in enumerate(self.files)
        ]

    async def git_changes(
        self, host_id: str, worktree_id: str, *, scope: str, cursor: str | None = None
    ) -> dict[str, Any]:
        self.calls.append(("changes", worktree_id, scope, cursor))
        self._fail("changes")
        if scope == "branch" and self.base is None:
            raise TransportError("conflict", "no base", False, {"reason": "no_base"})
        start = int(cursor) if cursor else 0
        entries = self.entries(scope)
        page = entries[start : start + LIST_PAGE]
        more = start + LIST_PAGE < len(entries)
        branch_scope = scope == "branch"
        return {
            "scope": scope,
            "snapshot": self.snapshot(scope),
            "head": "a" * 40,
            "base": "b" * 40 if branch_scope else None,
            "branch": "feature",
            "detached": False,
            "short_oid": "aaaaaaa",
            "additions": 3 * len(entries),
            "deletions": len(entries),
            "base_name": self.base if branch_scope else None,
            "commits": self.commit_total if branch_scope else None,
            "partial": False,
            "as_of_ms": self.clock_ms,
            "total_files": len(entries),
            "files": [entry.to_dict() for entry in page],
            "next": str(start + LIST_PAGE) if more else None,
        }

    async def git_file_diff(
        self,
        host_id: str,
        worktree_id: str,
        file_id: str,
        *,
        cursor: str | None = None,
        load: bool = False,
    ) -> dict[str, Any]:
        self.calls.append(("diff", file_id, cursor, load))
        self._fail("diff")
        if file_id.startswith("commit:"):
            _, number, row = file_id.split(":")
            snapshot, spec = (
                self.commit_snapshot(int(number)),
                self.commit_specs(int(number))[int(row)],
            )
        else:
            snapshot, _, row = file_id.partition(":")
            if snapshot.split("+")[0] != self.revision:
                raise TransportError(
                    "forbidden", "stale", False, {"reason": "stale_snapshot", "retry_after_s": None}
                )
            spec = self.files[int(row)]
        pages = DiffParser().parse(spec.patch, file_id=file_id, snapshot=snapshot)
        total = sum(len(h.lines) for page in pages for h in page.hunks)
        if total > LOAD_GATE_ROWS and not load:
            gate = replace(pages[0], render="load_required", hunks=(), next=None)
            return gate.to_dict()
        index = int(cursor.removeprefix("pg:")) if cursor else 0
        page = pages[index]
        following = f"pg:{index + 1}" if index + 1 < len(pages) else None
        return replace(page, next=following).to_dict()

    # -- commits: the files a commit changed against its first parent --------------------------

    def parents(self, number: int) -> tuple[str, ...]:
        if number >= self.commit_total:
            return ()
        following = (number + 1, number + 2) if number == self.merge_at else (number + 1,)
        return tuple(f"{n:040x}" for n in following)

    def commit_id(self, mode: str, number: int) -> str:
        return f"cid:{mode}:{self.graph_revision or 'r'}:{number}"

    def commit_snapshot(self, number: int) -> str:
        return f"commit-{number:040x}"

    def commit_specs(self, number: int) -> list[FileSpec]:
        paths = [f"src/c{number}/{name}.py" for name in "ab"]
        default = [FileSpec(path, two_hunk_patch(path)) for path in paths]
        return self.commit_files.get(number, default)

    async def git_commit_changes(
        self, host_id: str, worktree_id: str, commit_id: str, *, cursor: str | None = None
    ) -> dict[str, Any]:
        self.calls.append(("commit_changes", commit_id, cursor))
        self._fail("commit_changes")
        parts = commit_id.split(":")
        if len(parts) != 4 or parts[0] != "cid" or not parts[3].isdigit():
            raise TransportError("forbidden", "bad id", False, {"reason": "malformed"})
        number = int(parts[3])
        entries = [
            FileEntry(
                file_id=f"commit:{number}:{row}",
                path=spec.path,
                status=spec.status,  # type: ignore[arg-type]
                staged=False,
                unstaged=False,
                additions=3,
                deletions=1,
                generated=spec.generated,
                withheld=False,
                rename_limited=False,
                renderable=True,
            )
            for row, spec in enumerate(self.commit_specs(number))
        ]
        start = int(cursor) if cursor else 0
        page = entries[start : start + self.commit_page]
        more = start + self.commit_page < len(entries)
        root = number >= self.commit_total
        return {
            "scope": "commit",
            "snapshot": self.commit_snapshot(number),
            "head": f"{number:040x}",
            "base": EMPTY_TREE if root else f"{number + 1:040x}",
            "branch": None,
            "detached": False,
            "short_oid": f"{number:040x}"[:12],
            "additions": 3 * len(entries),
            "deletions": len(entries),
            "base_name": None,
            "commits": None,
            "partial": False,
            "as_of_ms": self.clock_ms,
            "total_files": len(entries),
            "files": [entry.to_dict() for entry in page],
            "next": str(start + self.commit_page) if more else None,
        }

    async def git_graph(
        self, host_id: str, worktree_id: str, *, mode: str = "all", cursor: str | None = None
    ) -> dict[str, Any]:
        self.calls.append(("graph", cursor, mode))
        self._fail("graph")
        start = int(cursor) if cursor else 0
        size = self.graph_page_size
        total = self.commit_total + 1
        commits = tuple(
            GraphCommit(
                oid=f"{n:040x}",
                parents=self.parents(n),
                subject=f"Commit number {n}",
                time_ms=self.clock_ms - n * 60_000,
                ahead=n < self.commit_total,
                refs=(GraphRef(name="split point", kind="split"),)
                if n == self.commit_total
                else (),
                commit_id=self.commit_id(mode, n) if self.issue_commit_ids else None,
            )
            for n in range(start, min(start + size, total))
        )
        return GraphPage(
            snapshot=self.snapshot("branch") + ":graph" + self.graph_revision,
            mode="branch" if mode == "branch" else "all",
            head=f"{0:040x}",
            branch="feature",
            base_name=self.base or "main",
            base_oid=f"{self.commit_total:040x}",
            split_oid=f"{self.commit_total:040x}",
            ahead=self.commit_total,
            commits=commits,
            as_of_ms=self.clock_ms,
            next=str(start + size) if start + size < total else None,
            partial=self.graph_partial,
            limit_reason="ref_limit" if self.graph_partial else None,
        ).to_dict()

    async def git_bases(
        self, host_id: str, worktree_id: str, *, cursor: str | None = None
    ) -> dict[str, Any]:
        self.calls.append(("bases", cursor))
        self._fail("bases")
        start = int(cursor) if cursor else 0
        names = self.base_names[start : start + self.base_page]
        candidates = [
            BaseCandidate(
                base_id=f"id:{name}",
                name=name,
                kind="remote_default" if name == "origin/main" else "remote",
                oid=f"{n:040x}",
            )
            for n, name in enumerate(names, start)
        ]
        more = start + self.base_page < len(self.base_names)
        return {
            "candidates": [c.to_dict() for c in candidates],
            "next": str(start + self.base_page) if more else None,
        }

    async def git_set_base(self, host_id: str, worktree_id: str, base_id: str) -> dict[str, Any]:
        self.calls.append(("set_base", base_id))
        self.base = base_id.removeprefix("id:")
        return {"base_name": self.base}


def pager(status: str = "working") -> Any:
    """The slice of the Console's pager the wiring reads."""
    row = {"host_id": HOST, "terminal_id": AGENT, "status": status, "name": "Synthetic agent"}
    return SimpleNamespace(visible_rows=[row], rows={KEY: row})
