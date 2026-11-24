"""Read-only changes for agents' worktrees: summaries, snapshots, file diffs, commits and bases."""

from __future__ import annotations

import asyncio
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from relaydeck.contracts.git import (
    BranchCounts,
    Commit,
    FileDiff,
    GraphMode,
    RenderState,
    WorktreeSummary,
)
from relaydeck.git.base_store import BaseStore
from relaydeck.git.cache import Known, SnapshotCache
from relaydeck.git.comparison import base_options, comparison_base
from relaydeck.git.config_guard import ConfigGuard, UnsupportedConfig
from relaydeck.git.content import capture_diff, parse_pages, with_untracked_lines
from relaydeck.git.content import render_only as _render_only
from relaydeck.git.flights import WorkerFlights
from relaydeck.git.graph import GRAPH_REQUEST_S, GraphService
from relaydeck.git.graph_probe import GraphProbe, probe
from relaydeck.git.graph_reader import GraphReader
from relaydeck.git.locator import LocateError, RepositoryLocator, Worktree
from relaydeck.git.manifest import (
    branch_counts,
    branch_rows,
    working_counts,
    working_rows,
)
from relaydeck.git.polling import POLL_RETRY_MAX_S, POLL_RETRY_S, config_stamp, metadata_stamp
from relaydeck.git.probe import ChangeProbe, Probe
from relaydeck.git.reader import ReadError, RepositoryReader
from relaydeck.git.results import ChangesError as ChangesError
from relaydeck.git.results import FileRecord as Row
from relaydeck.git.results import GraphResult
from relaydeck.git.results import Scope as Scope
from relaydeck.git.results import Snapshot as Snapshot
from relaydeck.git.results import empty_summary as _empty
from relaydeck.git.runner import GitFailed, GitRunner, GitUnavailable
from relaydeck.git.safe_fs import Unreadable
from relaydeck.git.secret_policy import SecretPolicy

LOAD_REQUIRED_ROWS = 1000
REFRESH_INTERVAL_S = 10.0
CLOSE_WAIT_S = 2.0
MAX_ROWS = 50_000
_RENDER: dict[str, RenderState] = {
    "withheld": "withheld",
    "binary": "binary",
    "conflict": "unsupported",
    "submodule": "unsupported",
    "mode": "unsupported",
    "symlink": "unsupported",
}


class ChangesService:
    """Async facade; git runs on a small pool and every result is bounded and cached."""

    def __init__(
        self,
        runner: GitRunner,
        base_store: BaseStore,
        *,
        policy: SecretPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        workers: int = 2,
    ) -> None:
        self._runner = runner
        self._locator = RepositoryLocator(runner)
        self._guard = ConfigGuard(runner)
        self._config_stamps: OrderedDict[tuple[int, ...], str] = OrderedDict()
        self._config_lock = threading.Lock()
        self._bases = base_store
        self._policy = policy or SecretPolicy()
        self._clock = clock
        self._wall_ms = wall_ms
        self._cache = SnapshotCache(clock)
        self._workers = WorkerFlights(workers)
        self._maintenance: asyncio.Task[None] | None = None
        self._retention_event: asyncio.Event | None = None
        self._graphs = GraphService(
            self._cache,
            self._bases,
            self._graph_reader,
            clock,
            wall_ms,
            self._policy,
            self._probe_graph,
        )

    # -- summaries --------------------------------------------------------------------------
    async def summaries(
        self, account: str, host: str, folders: Mapping[str, str | None]
    ) -> list[WorktreeSummary]:
        """One summary per agent from what is already known; never waits for git.

        An unknown folder is located by a background job, a stale summary is refreshed by one;
        until then the agent reads `calculating` (or its last known state).
        """
        if self._workers.closed:
            raise ChangesError("unavailable")
        requested = time.monotonic()
        located: dict[str, str | LocateError | None] = {}
        for agent_id, folder in folders.items():
            located[agent_id], due = self._cache.located(folder)
            if due and folder is not None:
                self._workers.schedule(("locate", folder), self._locate, folder)
        shared: dict[str, int] = {}
        for found in located.values():
            if isinstance(found, str):
                shared[found] = shared.get(found, 0) + 1
        out: list[WorktreeSummary] = []
        for agent_id, found in located.items():
            if found is None:
                out.append(_empty(agent_id, "calculating"))
                continue
            if isinstance(found, LocateError):
                out.append(_empty(agent_id, str(found)))
                continue
            known = self._cache.lookup(found)
            summary = known.summary if known is not None else None
            if known is not None and self._clock() - known.checked >= REFRESH_INTERVAL_S:
                self._workers.schedule(
                    ("summary", account, host, found),
                    self._summarize,
                    account,
                    host,
                    found,
                    requested,
                )
            if summary is None:
                out.append(_empty(agent_id, "calculating", worktree=found))
            else:
                out.append(replace(summary, agent_id=agent_id, shared=shared[found] > 1))
        return out

    def _locate(self, folder: str) -> None:
        try:
            worktree = self._locator.locate(folder, self._workers.stop_event())
        except LocateError as exc:
            self._cache.remember_folder(folder, exc)
            return
        except (GitFailed, GitUnavailable):
            self._cache.remember_folder(folder, LocateError("unavailable"))
            return
        key = self._bases.worktree_key(worktree)
        self._cache.remember_worktree(key, worktree)
        self._cache.remember_folder(folder, key)

    def _summarize(self, account: str, host: str, key: str, requested: float | None = None) -> None:
        known = self._cache.lookup(key)
        if known is None:
            return
        requested = time.monotonic() if requested is None else requested
        try:
            working = self._snapshot_now(account, host, key, "working", requested)
            branch = self._branch_or_none(account, host, key, requested)
        except Exception as exc:  # noqa: BLE001 - every failure becomes a state with a time
            code = exc.code if isinstance(exc, ChangesError) else "unavailable"
            if not self._workers.closed:
                known.summary = replace(
                    _empty("", code, worktree=key),
                    as_of_ms=known.summary.as_of_ms if known.summary is not None else 0,
                )
                known.checked = self._clock()
            return
        if self._workers.closed:
            return
        previous = known.summary
        if (
            previous is not None
            and previous.state == "ok"
            and previous.revision == working.revision
            and previous.branch_scope.revision == (branch.revision if branch is not None else None)
        ):
            known.summary = replace(previous, as_of_ms=self._wall_ms())
            known.checked = self._clock()
            return
        known.summary = WorktreeSummary(
            agent_id="",
            worktree_id=key,
            state="ok",
            branch=working.branch,
            detached=working.branch is None and working.head is not None,
            short_oid=working.head[:12] if working.head else None,
            base_name=branch.base_name if branch is not None else None,
            working=working_counts(working.rows),
            branch_scope=_branch_scope(branch),
            shared=False,
            revision=working.revision,
            as_of_ms=self._wall_ms(),
            partial=working.partial,
        )
        known.checked = self._clock()

    def _branch_or_none(
        self, account: str, host: str, key: str, requested: float | None = None
    ) -> Snapshot | None:
        try:
            return self._snapshot_now(account, host, key, "branch", requested)
        except ChangesError as exc:
            if exc.code == "no_base":
                return None
            raise

    # -- snapshots --------------------------------------------------------------------------
    async def snapshot(self, account: str, host: str, key: str, scope: Scope) -> Snapshot:
        """A fresh immutable snapshot of one scope (cached while the revision holds)."""
        return await self._workers.call(
            ("snapshot", account, host, key, scope),
            self._snapshot_now,
            account,
            host,
            key,
            scope,
            time.monotonic(),
        )

    def cached(self, account: str, host: str, key: str, scope: str, revision: str) -> Snapshot:
        return self._cache.cached((account, host, key, scope, revision))

    def _snapshot_now(
        self, account: str, host: str, key: str, scope: Scope, requested: float | None = None
    ) -> Snapshot:
        """Probe, read, probe again: a worktree that moved while it was read is retried once,
        then reported partial; nothing raced is ever presented as a complete snapshot."""
        if scope not in ("working", "branch"):
            raise ChangesError("not_found")
        requested = time.monotonic() if requested is None else requested
        known = self._cache.worktree(key)
        with known.lock:
            choice = (
                repr(self._bases.get(account, host, known.worktree)) if scope == "branch" else ""
            )
            poll_key = (account, host, scope, choice, self._policy.version)
            stamp = self._poll_stamp(known, requested)
            reuse = known.polls.get(poll_key)
            if stamp is not None and reuse is not None and reuse[0] == stamp:
                try:
                    return self._cache.cached((account, host, key, scope, reuse[1]))
                except ChangesError:
                    pass  # The bounded snapshot cache may have evicted this proof's capture.
            reader = self._reader(known.worktree)
            probe_of = ChangeProbe(known.worktree, policy_version=self._policy.version)
            for attempt in range(2):
                # Base, HEAD and merge base are taken afresh each attempt; the snapshot is only
                # stable when its probe saw the same HEAD the merge base was computed from.
                base_ref, base_key, merge_base, base_head = self._base(
                    account, host, known.worktree, reader, scope
                )
                probe = probe_of.probe(reader, base_key)
                old, rows, commits = self._read_scope(
                    reader,
                    known.worktree,
                    probe,
                    scope,
                    merge_base,
                    self._cache.latest(account, host, key, scope),
                )
                moved = probe_of.probe(reader, base_key).revision != probe.revision or (
                    scope == "branch" and probe.status.head != base_head
                )
                if not moved or attempt == 1:
                    break
            partial = probe.partial or not probe.stable or moved or len(rows) > MAX_ROWS
            snap = Snapshot(
                account=account,
                host=host,
                worktree=key,
                scope=scope,
                revision=probe.revision,
                head=probe.status.head,
                branch=probe.status.branch,
                old=old,
                base_name=_short(base_ref),
                commits=commits,
                rows=rows[:MAX_ROWS],
                partial=partial,
                as_of_ms=self._wall_ms(),
                policy_stamp=probe.policy,
                policy_version=self._policy.version,
            )
            if not moved:
                self._cache.remember(snap, known)
                if (
                    not partial
                    and stamp is not None
                    and self._poll_stamp(known, requested, verify=True) == stamp
                ):
                    known.polls[poll_key] = (stamp, snap.revision)
                    known.polls.move_to_end(poll_key)
                    while len(known.polls) > 16:
                        known.polls.popitem(last=False)
            return snap

    def _poll_stamp(self, known: Known, requested: float, *, verify: bool = False) -> str | None:
        """Called under the worktree lock; share overlap, never a timed positive proof."""
        now = self._clock()
        if now < known.poll_retry_at:
            return None
        if not verify and requested <= known.poll_completed:
            return known.poll_stamp
        stamp = metadata_stamp(known.worktree, default_ignore_file=self._runner.default_ignore_file)
        known.poll_stamp = stamp
        known.poll_completed = time.monotonic()
        if stamp is None:
            known.poll_failures = min(known.poll_failures + 1, 8)
            known.poll_retry_at = now + min(
                POLL_RETRY_MAX_S, POLL_RETRY_S * 2 ** (known.poll_failures - 1)
            )
        else:
            known.poll_failures = 0
            known.poll_retry_at = 0.0
        return stamp

    def _read_scope(
        self,
        reader: RepositoryReader,
        worktree: Worktree,
        probe: Probe,
        scope: Scope,
        merge_base: str | None,
        previous: Snapshot | None = None,
    ) -> tuple[str, tuple[Row, ...], int]:
        status = probe.status
        try:
            if scope == "working":
                old = status.head or reader.empty_tree()
                entries = status.entries[: MAX_ROWS + 1]  # one over the cap marks it partial
                rows = working_rows(entries, reader.numstat(old, None), self._policy)
                return old, with_untracked_lines(worktree, rows), 0
            if status.head is None or merge_base is None:
                raise ChangesError("no_base")
            if (
                previous is not None
                and not previous.partial
                and probe.policy is not None
                and previous.policy_stamp == probe.policy
                and previous.policy_version == self._policy.version
                and (previous.old, previous.head) == (merge_base, status.head)
            ):
                return merge_base, previous.rows, previous.commits
            rows = branch_rows(
                reader.raw(merge_base, status.head)[: MAX_ROWS + 1],
                reader.numstat(merge_base, status.head),
                self._policy,
            )
            return merge_base, rows, reader.count(merge_base, status.head)
        except ReadError as exc:
            raise ChangesError("too_large" if str(exc) == "truncated" else "unavailable") from exc

    def _base(
        self, account: str, host: str, worktree: Worktree, reader: RepositoryReader, scope: Scope
    ) -> tuple[str | None, str | None, str | None, str | None]:
        return comparison_base(self._bases, account, host, worktree, reader, scope)

    # -- file diffs -------------------------------------------------------------------------
    async def file_diff(
        self, snap: Snapshot, row: int, file_id: str, page: int, *, load: bool = False
    ) -> tuple[FileDiff, int]:
        """(page `page` of one file, total pages); content is read only if the snapshot holds."""
        if not 0 <= row < len(snap.rows):
            raise ChangesError("not_found")
        entry = snap.rows[row]
        if entry.reason is not None and entry.reason != "no_net_diff":
            return _render_only(file_id, snap.revision, _RENDER[entry.reason]), 1
        pages = await self._workers.call(
            ("diff", snap.account, snap.host, snap.worktree, snap.revision, str(row)),
            self._pages,
            snap,
            row,
            file_id,
        )
        rows = sum(len(h.lines) for p in pages for h in p.hunks)
        if rows > LOAD_REQUIRED_ROWS and not load:
            return _render_only(file_id, snap.revision, "load_required"), 1
        if not 0 <= page < len(pages):
            raise ChangesError("not_found")
        # Cached pages are shared by every caller; each reply carries the caller's own id.
        return replace(pages[page], file_id=file_id), len(pages)

    def _pages(self, snap: Snapshot, row: int, file_id: str) -> tuple[FileDiff, ...]:
        fenced = snap.scope == "commit"
        known = self._cache.worktree(snap.worktree)
        with known.lock:
            cached = self._cache.page(snap, known, row)
            if cached is not None:
                return cached
            reader = self._reader(known.worktree)
            base_key = None
            if snap.scope == "branch":
                _, base_key, _, _ = self._base(
                    snap.account, snap.host, known.worktree, reader, "branch"
                )
            captured = capture_diff(reader, known.worktree, snap, row, base_key)
        if isinstance(captured, str):
            self._cache.validate_completion(snap, known, fenced=fenced)
            return (_render_only(file_id, snap.revision, captured),)
        patch, sides = captured
        del captured
        pages = parse_pages(patch, sides, file_id, snap.revision)
        del patch, sides
        return self._cache.put_pages(snap, known, row, pages, fenced=fenced)

    # -- commits and bases ------------------------------------------------------------------
    async def commit_snapshot(
        self, account: str, host: str, key: str, graph_revision: str, mode: GraphMode, oid: str
    ) -> Snapshot:
        self._start_maintenance()
        deadline = time.monotonic() + GRAPH_REQUEST_S
        try:
            async with asyncio.timeout(GRAPH_REQUEST_S):
                return await self._workers.call(
                    ("commit", account, host, key, graph_revision, mode, oid),
                    self._graphs.commit_snapshot,
                    account,
                    host,
                    key,
                    graph_revision,
                    mode,
                    oid,
                    deadline,
                    MAX_ROWS,
                )
        except TimeoutError:
            raise ChangesError("busy", 1) from None

    async def graph(
        self,
        account: str,
        host: str,
        key: str,
        mode: GraphMode,
        *,
        revision: str | None = None,
        offset: int = 0,
    ) -> GraphResult:
        self._start_maintenance()
        requested = time.monotonic()
        deadline = requested + GRAPH_REQUEST_S
        try:
            async with asyncio.timeout(GRAPH_REQUEST_S):
                return await self._workers.call(
                    ("graph", account, host, key, mode, revision or "", str(offset)),
                    self._graphs.read,
                    account,
                    host,
                    key,
                    mode,
                    revision,
                    offset,
                    deadline,
                    requested,
                )
        except TimeoutError:
            raise ChangesError("busy", 1) from None

    async def commits(self, snap: Snapshot, skip: int, limit: int) -> tuple[Commit, ...]:
        if snap.scope != "branch" or snap.head is None:
            raise ChangesError("no_base")
        known = self._cache.worktree(snap.worktree)
        found = await self._workers.call(
            ("commits", snap.worktree, snap.revision, str(skip), str(limit)),
            lambda: self._reader(known.worktree).commits(
                snap.old, snap.head or "", skip=skip, limit=limit
            ),
        )
        return tuple(
            Commit(oid=c.oid, parents=c.parents, subject=c.subject, time_ms=c.time_s * 1000)
            for c in found
        )

    async def bases(self, account: str, host: str, key: str) -> tuple[tuple[str, str, str], ...]:
        """(qualified ref, kind, OID) candidates, remote default first."""
        known = self._cache.worktree(key)

        return await self._workers.call(
            ("bases", account, host, key), lambda: base_options(self._reader(known.worktree))
        )

    async def set_base(self, account: str, host: str, key: str, ref: str) -> None:
        """Save the base, or follow the default again when `ref` is empty; Branch snapshots and
        pages built on the old choice stop being served."""
        known = self._cache.worktree(key)

        def save() -> None:
            with known.lock:  # a scan holding the old choice publishes before the save retires it
                if ref:
                    self._bases.put(account, host, known.worktree, ref)
                else:
                    self._bases.clear(account, host, known.worktree)

        await self._workers.call(("set_base", account, host, key, ref), save)
        self._cache.clear_branch(account, host, key, known)

    def _start_maintenance(self) -> None:
        loop = asyncio.get_running_loop()
        if self._maintenance is None or self._maintenance.done():
            self._retention_event = asyncio.Event()

            def retained() -> None:
                if not loop.is_closed():
                    loop.call_soon_threadsafe(self._retention_changed)

            self._cache.on_retained(retained)
            self._maintenance = asyncio.create_task(self._retire_idle(self._retention_event))

    def _retention_changed(self) -> None:
        if self._workers.closed:
            return
        if self._retention_event is not None:
            self._retention_event.set()
        self._start_maintenance()

    async def _retire_idle(self, changed: asyncio.Event) -> None:
        while not self._workers.closed:
            changed.clear()
            delay = self._cache.next_expiry()
            if delay is None:
                return
            if delay <= 0:
                self._cache.retire_idle()
                await asyncio.sleep(0)
                continue
            try:
                await asyncio.wait_for(changed.wait(), timeout=delay)
            except TimeoutError:
                self._cache.retire_idle()

    # -- plumbing ---------------------------------------------------------------------------
    def close(self) -> None:
        """Retire cached data and cancel every queued or running worker."""
        self._cache.close()
        task = self._maintenance
        if task is not None and not task.done() and not task.get_loop().is_closed():
            task.get_loop().call_soon_threadsafe(task.cancel)
        self._workers.close()
        self._graphs.close()
        self._config_stamps.clear()

    async def wait_closed(self, timeout: float = CLOSE_WAIT_S) -> bool:
        """After `close`: whether maintenance ended and every worker exited within `timeout`."""
        loop = asyncio.get_running_loop()
        waits: list[asyncio.Future[Any]] = [self._workers.joined()]
        task = self._maintenance
        if task is not None and task.get_loop() is loop:
            waits.append(task)
        _, pending = await asyncio.wait(waits, timeout=timeout)
        return not pending

    def _reader(self, worktree: Worktree) -> RepositoryReader:
        self._check_config(worktree)
        return RepositoryReader(self._runner, worktree, self._workers.stop_event())

    def _probe_graph(
        self, worktree: Worktree, deadline: float, base: str | None, choice: str
    ) -> GraphProbe:
        try:
            return probe(self._runner, worktree, deadline, base, choice, self._workers.stop_event())
        except (OSError, Unreadable):
            raise ChangesError("unavailable") from None

    def _graph_reader(self, worktree: Worktree, deadline: float) -> GraphReader:
        self._check_config(worktree, deadline)
        return GraphReader(self._runner, worktree, self._workers.stop_event(), deadline=deadline)

    def _check_config(self, worktree: Worktree, deadline: float | None = None) -> None:
        remaining = 5.0 if deadline is None else min(5.0, deadline - time.monotonic())
        if remaining <= 0:
            raise ChangesError("busy", 1)
        try:
            stamp = config_stamp(worktree)
            with self._config_lock:
                if (
                    stamp is not None
                    and deadline is None
                    and self._config_stamps.get(worktree.identity) == stamp
                ):
                    return
            self._guard.check(
                worktree.root,
                worktree.gitdir,
                worktree.common_dir,
                self._workers.stop_event(),
                timeout_s=remaining,
            )
            if config_stamp(worktree) != stamp:
                raise ChangesError("busy", 1)
            with self._config_lock:
                if self._workers.closed:
                    return
                if stamp is None:
                    self._config_stamps.pop(worktree.identity, None)
                    return
                self._config_stamps[worktree.identity] = stamp
                self._config_stamps.move_to_end(worktree.identity)
                while len(self._config_stamps) > 512:
                    self._config_stamps.popitem(last=False)
        except UnsupportedConfig as exc:
            raise ChangesError("unsupported_config") from exc
        except GitFailed as exc:
            raise ChangesError("unavailable") from exc


def _branch_scope(snap: Snapshot | None) -> BranchCounts:
    if snap is None:
        return BranchCounts()
    return replace(branch_counts(snap.rows, snap.commits), revision=snap.revision)


def _short(ref: str | None) -> str | None:
    if ref is None:
        return None
    for prefix in ("refs/heads/", "refs/remotes/"):
        if ref.startswith(prefix):
            return ref[len(prefix) :]
    return ref
