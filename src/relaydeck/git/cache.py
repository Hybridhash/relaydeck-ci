"""Bounded worktree, snapshot and diff-page cache with one accounting lock."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from relaydeck.contracts.git import FileDiff, GraphCommit, WorktreeSummary
from relaydeck.git.graph_cache import (
    COMMIT_IDLE_S,
    GRAPH_IDLE_S,
    GRAPH_TTL_S,
    GraphEntry,
    GraphKey,
    graph_key,
    memory_size,
)
from relaydeck.git.locator import LocateError, Worktree
from relaydeck.git.parser import page_size
from relaydeck.git.results import ChangesError, GraphContext, GraphResult, Snapshot

MAX_SNAPSHOTS = 64
MAX_CACHE_BYTES = 64 * 1024 * 1024
MAX_WORKTREES = 512
LOCATE_TTL_S = 60.0
SNAPSHOTS_PER_WORKTREE = 16
SnapshotKey = tuple[str, str, str, str, str]


@dataclass(slots=True)
class Known:
    worktree: Worktree
    summary: WorktreeSummary | None = None
    checked: float = 0.0
    pages: dict[tuple[str, int], tuple[FileDiff, ...]] = field(default_factory=dict)
    page_bytes: dict[tuple[str, int], int] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    polls: OrderedDict[tuple[str, ...], tuple[str, str]] = field(default_factory=OrderedDict)
    poll_stamp: str | None = None
    poll_completed: float = 0.0
    poll_failures: int = 0
    poll_retry_at: float = 0.0


class SnapshotCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.lock = threading.Lock()
        self.known: OrderedDict[str, Known] = OrderedDict()
        self.folders: OrderedDict[str, tuple[float, str | LocateError]] = OrderedDict()
        self.snapshots: OrderedDict[SnapshotKey, Snapshot] = OrderedDict()
        self.graphs: OrderedDict[GraphKey, GraphEntry] = OrderedDict()
        self._touched: dict[SnapshotKey, float] = {}
        self.bytes = 0
        self.closed = False
        self._retained: Callable[[], None] | None = None
        self._clock = clock

    def lookup(self, key: str) -> Known | None:
        with self.lock:
            return self.known.get(key)

    def worktree(self, key: str) -> Known:
        known = self.lookup(key)
        if known is None:
            raise ChangesError("not_found")
        return known

    def located(self, folder: str | None) -> tuple[str | LocateError | None, bool]:
        if folder is None:
            return LocateError("no_repo"), False
        with self.lock:
            hit = self.folders.get(folder)
            if hit is None or isinstance(hit[1], str) and hit[1] not in self.known:
                return None, True
            if self._clock() - hit[0] < LOCATE_TTL_S:
                return hit[1], False
            return (hit[1] if isinstance(hit[1], str) else None), True

    def remember_worktree(self, key: str, worktree: Worktree) -> None:
        with self.lock:
            if self.closed:
                return
            known = self.known.get(key)
            if known is not None and known.worktree.identity != worktree.identity:
                self._drop_worktree(key)
                known = None
            if known is None:
                known = Known(worktree)
            self.known.pop(key, None)
            self.known[key] = known
            while len(self.known) > MAX_WORKTREES:
                self._drop_worktree(next(iter(self.known)))

    def remember_folder(self, folder: str, found: str | LocateError) -> None:
        with self.lock:
            if self.closed:
                return
            self.folders.pop(folder, None)
            self.folders[folder] = self._clock(), found
            while len(self.folders) > MAX_WORKTREES * 2:
                self.folders.popitem(last=False)

    def cached(self, key: SnapshotKey) -> Snapshot:
        with self.lock:
            snap = self.snapshots.get(key)
            if snap is None:
                raise ChangesError("stale_snapshot")
            self._touched[key] = self._clock()
            return snap

    def latest(self, account: str, host: str, worktree: str, scope: str) -> Snapshot | None:
        with self.lock:
            return next(
                (
                    snap
                    for key, snap in reversed(self.snapshots.items())
                    if key[:4] == (account, host, worktree, scope)
                ),
                None,
            )

    def page(self, snap: Snapshot, known: Known, row: int) -> tuple[FileDiff, ...] | None:
        with self.lock:
            self._check_owner(snap, known)
            return known.pages.get((snap.revision, row))

    def put_pages(
        self,
        snap: Snapshot,
        known: Known,
        row: int,
        pages: tuple[FileDiff, ...],
        *,
        fenced: bool = False,
    ) -> tuple[FileDiff, ...]:
        size = sum(page_size(page) for page in pages)
        with self.lock:
            if self.closed and not fenced:
                return pages
            self._check_owner(snap, known)
            key = snap.revision, row
            cached = known.pages.get(key)
            if cached is not None:
                return cached
            known.pages[key] = pages
            known.page_bytes[key] = size
            self.bytes += size
            self._evict()
            return pages

    def validate_completion(self, snap: Snapshot, known: Known, *, fenced: bool) -> None:
        if fenced:
            with self.lock:
                self._check_owner(snap, known)

    def _check_owner(self, snap: Snapshot, known: Known) -> None:
        key = snap.account, snap.host, snap.worktree, snap.scope, snap.revision
        if self.known.get(snap.worktree) is not known or key not in self.snapshots:
            raise ChangesError("stale_snapshot")
        self._touched[key] = self._clock()

    def remember(self, snap: Snapshot, known: Known, *, graph: GraphEntry | None = None) -> None:
        with self.lock:
            if graph is not None:
                self._check_graph(graph, known)
            if self.closed or self.known.get(snap.worktree) is not known:
                return
            key = snap.account, snap.host, snap.worktree, snap.scope, snap.revision
            self._drop_snapshot(key)
            self.snapshots[key] = snap
            self._touched[key] = self._clock()
            self.bytes += manifest_bytes(snap)
            mine = [key for key in self.snapshots if key[2] == snap.worktree]
            for old in mine[: max(0, len(mine) - SNAPSHOTS_PER_WORKTREE)]:
                self._drop_snapshot(old)
            while len(self.snapshots) > MAX_SNAPSHOTS:
                self._drop_snapshot(next(iter(self.snapshots)))
            self._evict()
            if snap.scope == "commit" and self._retained is not None:
                self._retained()

    def clear_branch(self, account: str, host: str, key: str, known: Known) -> None:
        with self.lock:
            for cache_key in [
                key_ for key_ in self.snapshots if key_[:4] == (account, host, key, "branch")
            ]:
                self._drop_snapshot(cache_key)
            for graph in [k for k in self.graphs if k[:3] == (account, host, key)]:
                self._drop_graph(graph)
            if self.known.get(key) is known:
                keep = frozenset(
                    s.revision
                    for s in self.snapshots.values()
                    if s.worktree == key and s.scope == "commit"
                )
                self._drop_pages(known, keep=keep)

    def graph(self, key: GraphKey) -> GraphEntry:
        with self.lock:
            entry = self.graphs.get(key)
            if entry is not None and self._clock() - entry.context.created >= GRAPH_TTL_S:
                self._drop_graph(key)
                entry = None
            if entry is None:
                raise ChangesError("stale_snapshot")
            self.graphs.move_to_end(key)
            return entry

    def latest_graph(self, account: str, host: str, key: str, mode: str) -> GraphEntry | None:
        with self.lock:
            for cache_key in reversed(list(self.graphs)):
                if cache_key[:4] == (account, host, key, mode):
                    entry = self.graphs[cache_key]
                    if self._clock() - entry.context.created >= GRAPH_TTL_S:
                        self._drop_graph(cache_key)
                        continue
                    return entry
            return None

    def shared_graph(self, key: str, digest: str | None, guard: str) -> GraphEntry | None:
        with self.lock:
            for cache_key in reversed(list(self.graphs)):
                entry = self.graphs[cache_key]
                if (
                    cache_key[2] == key
                    and (digest is None or entry.context.probe_digest == digest)
                    and entry.context.probe_guard == guard
                ):
                    if self._clock() - entry.context.created >= GRAPH_TTL_S:
                        self._drop_graph(cache_key)
                        continue
                    return entry
            return None

    def update_graph_probe(
        self, entry: GraphEntry, known: Known, digest: str, guard: str, refs: str | None
    ) -> None:
        context = replace(entry.context, probe_digest=digest, probe_guard=guard, probe_refs=refs)
        delta = memory_size(context) - memory_size(entry.context)
        with self.lock:
            self._check_graph(entry, known)
            entry.context = context
            entry.size += delta
            self.bytes += delta

    def remember_graph(self, context: GraphContext, known: Known) -> GraphEntry:
        size = memory_size(context) + 512
        with self.lock:
            if self.closed or self.known.get(context.worktree) is not known:
                raise ChangesError("stale_snapshot")
            key = graph_key(context)
            existing = self.graphs.get(key)
            if existing is not None and self._clock() - existing.context.created < GRAPH_TTL_S:
                return existing
            self._drop_graph(key)
            entry = GraphEntry(context, size=size, touched=self._clock())
            self.graphs[key] = entry
            self.bytes += size
            mine = [k for k in self.graphs if k[2] == context.worktree]
            for old in mine[:-SNAPSHOTS_PER_WORKTREE]:
                self._drop_graph(old)
            while len(self.graphs) > MAX_SNAPSHOTS:
                self._drop_graph(next(iter(self.graphs)))
            self._evict()
            if self._retained is not None:
                self._retained()
            return entry

    def graph_page(self, entry: GraphEntry, known: Known, offset: int) -> GraphResult | None:
        with self.lock:
            self._check_graph(entry, known)
            if offset and not any(p.next_offset == offset for p in entry.pages.values()):
                raise ChangesError("stale_snapshot")
            return entry.pages.get(offset)

    def issued_commit(self, entry: GraphEntry, known: Known, oid: str) -> GraphCommit:
        with self.lock:
            self._check_graph(entry, known)
            for page in entry.pages.values():
                for commit in page.page.commits:
                    if commit.oid == oid:
                        return commit
            raise ChangesError("not_found")

    def selected_snapshot(self, entry: GraphEntry, known: Known, revision: str) -> Snapshot | None:
        with self.lock:
            self._check_graph(entry, known)
            context = entry.context
            key = context.account, context.host, context.worktree, "commit", revision
            snap = self.snapshots.get(key)
            if snap is not None:
                self._touched[key] = self._clock()
            return snap

    def put_graph_page(
        self, entry: GraphEntry, known: Known, offset: int, page: GraphResult
    ) -> GraphResult:
        size = memory_size(page) + 128
        with self.lock:
            self._check_graph(entry, known)
            old = entry.pages.get(offset)
            if old is not None:
                return old
            entry.pages[offset] = page
            entry.size += size
            self.bytes += size
            self._evict()
            self._check_graph(entry, known)
            return page

    def _check_graph(self, entry: GraphEntry, known: Known) -> None:
        context = entry.context
        if (
            self.closed
            or self.known.get(context.worktree) is not known
            or self.graphs.get(graph_key(context)) is not entry
            or self._clock() - context.created >= GRAPH_TTL_S
        ):
            raise ChangesError("stale_snapshot")
        entry.touched = self._clock()
        for key, snap in self.snapshots.items():
            if snap.scope == "commit" and (snap.account, snap.host, snap.worktree) == (
                context.account,
                context.host,
                context.worktree,
            ):
                self._touched[key] = entry.touched

    def _drop_graph(self, key: GraphKey) -> None:
        entry = self.graphs.pop(key, None)
        if entry is not None:
            self.bytes -= entry.size

    def _drop_snapshot(self, key: SnapshotKey) -> None:
        snap = self.snapshots.pop(key, None)
        self._touched.pop(key, None)
        if snap is not None:
            self.bytes -= manifest_bytes(snap)

    def _drop_pages(self, known: Known, *, keep: frozenset[str] = frozenset()) -> None:
        for key in [k for k in known.pages if k[0] not in keep]:
            self.bytes -= known.page_bytes.pop(key)
            known.pages.pop(key)

    def _drop_worktree(self, key: str) -> None:
        known = self.known.pop(key, None)
        if known is None:
            return
        self._drop_pages(known)
        for graph in [k for k in self.graphs if k[2] == key]:
            self._drop_graph(graph)
        for snap_key in [item for item in self.snapshots if item[2] == key]:
            self._drop_snapshot(snap_key)
        for folder in [folder for folder, hit in self.folders.items() if hit[1] == key]:
            self.folders.pop(folder)

    def _evict(self) -> None:
        for known in self.known.values():
            if self.bytes <= MAX_CACHE_BYTES:
                return
            self._drop_pages(known)
        while self.bytes > MAX_CACHE_BYTES and self.graphs:
            self._drop_graph(next(iter(self.graphs)))
        while self.bytes > MAX_CACHE_BYTES and self.snapshots:
            self._drop_snapshot(next(iter(self.snapshots)))

    def on_retained(self, callback: Callable[[], None] | None) -> None:
        with self.lock:
            self._retained = callback

    def next_expiry(self) -> float | None:
        with self.lock:
            deadlines = [
                min(entry.touched + GRAPH_IDLE_S, entry.context.created + GRAPH_TTL_S)
                for entry in self.graphs.values()
            ]
            deadlines.extend(
                self._touched[key] + COMMIT_IDLE_S
                for key, snap in self.snapshots.items()
                if snap.scope == "commit"
            )
            return min(deadlines) - self._clock() if deadlines else None

    def retire_idle(self) -> None:
        with self.lock:
            now = self._clock()
            for key, entry in list(self.graphs.items()):
                if (
                    now - entry.touched >= GRAPH_IDLE_S
                    or now - entry.context.created >= GRAPH_TTL_S
                ):
                    self._drop_graph(key)
            for key, snap in list(self.snapshots.items()):
                if snap.scope == "commit" and now - self._touched.get(key, now) >= COMMIT_IDLE_S:
                    self._drop_snapshot(key)
                    known = self.known.get(snap.worktree)
                    if known is not None and not any(
                        other.worktree == snap.worktree and other.revision == snap.revision
                        for other in self.snapshots.values()
                    ):
                        for page_key in [k for k in known.pages if k[0] == snap.revision]:
                            self.bytes -= known.page_bytes.pop(page_key)
                            known.pages.pop(page_key)

    def close(self) -> None:
        with self.lock:
            self.closed = True
            self._retained = None
            self.known.clear()
            self.folders.clear()
            self.snapshots.clear()
            self._touched.clear()
            self.graphs.clear()
            self.bytes = 0


def manifest_bytes(snap: Snapshot) -> int:
    """Approximate memory of a snapshot's file table for the shared budget."""
    return sum(len(row.path) + len(row.old_path or b"") + 96 for row in snap.rows) + 512
