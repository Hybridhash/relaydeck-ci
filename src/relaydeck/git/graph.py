"""Frozen graph contexts and pages under worktree ownership and cache limits."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace

from relaydeck.contracts.git import GraphCommit, GraphLimit, GraphMode, GraphPage, GraphRef
from relaydeck.git.base_store import BaseStore
from relaydeck.git.cache import MAX_WORKTREES, Known, SnapshotCache
from relaydeck.git.graph_probe import GraphProbe, guard_stamp, ref_stamp
from relaydeck.git.graph_reader import MAX_HISTORY, PAGE_SIZE, GraphReader
from relaydeck.git.locator import Worktree
from relaydeck.git.manifest import branch_rows
from relaydeck.git.reader import ReadError
from relaydeck.git.results import ChangesError, GraphContext, GraphResult, Snapshot, display
from relaydeck.git.runner import GitFailed, GitUnavailable
from relaydeck.git.secret_policy import SecretPolicy

GRAPH_REQUEST_S = 15.0


@dataclass(frozen=True, slots=True)
class SharedProbe:
    owner: Known
    result: GraphProbe
    completed: float
    refs: str | None


class GraphService:
    def __init__(
        self,
        cache: SnapshotCache,
        bases: BaseStore,
        reader: Callable[[Worktree, float], GraphReader],
        clock: Callable[[], float],
        wall_ms: Callable[[], int],
        policy: SecretPolicy,
        probe: Callable[[Worktree, float, str | None, str], GraphProbe],
    ) -> None:
        self._cache, self._bases, self._reader = cache, bases, reader
        self._clock, self._wall_ms = clock, wall_ms
        self._policy = policy
        self._probe = probe
        self._probes: OrderedDict[str, SharedProbe] = OrderedDict()
        self._probe_lock = threading.Lock()
        self._closed = False

    def commit_snapshot(
        self,
        account: str,
        host: str,
        key: str,
        graph_revision: str,
        mode: GraphMode,
        oid: str,
        deadline: float,
        max_rows: int,
    ) -> Snapshot:
        if time.monotonic() >= deadline:
            raise ChangesError("busy", 1)
        known = self._cache.worktree(key)
        if not known.lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            raise ChangesError("busy", 1)
        try:
            entry = self._cache.graph((account, host, key, mode, graph_revision))
            commit = self._cache.issued_commit(entry, known, oid)
            reader = self._reader(known.worktree, deadline)
            reader.check_history()
            old = commit.parents[0] if commit.parents else reader.empty_tree()
            identity = json.dumps(
                (
                    "commit-v1",
                    account,
                    host,
                    key,
                    repr(known.worktree.identity),
                    commit.oid,
                    old,
                    max_rows,
                    self._policy.version,
                ),
                separators=(",", ":"),
            ).encode()
            revision = hashlib.sha256(identity).hexdigest()
            cached = self._cache.selected_snapshot(entry, known, revision)
            if cached is not None:
                return cached
            rows = branch_rows(
                reader.raw(old, commit.oid)[: max_rows + 1],
                reader.numstat(old, commit.oid),
                self._policy,
            )
            snap = Snapshot(
                account,
                host,
                key,
                "commit",
                revision,
                commit.oid,
                None,
                old,
                None,
                0,
                rows[:max_rows],
                len(rows) > max_rows,
                self._wall_ms(),
            )
            self._cache.remember(snap, known, graph=entry)
            return self._cache.cached((account, host, key, "commit", revision))
        except ReadError as exc:
            raise ChangesError("too_large" if str(exc) == "truncated" else "unavailable") from None
        except (GitFailed, GitUnavailable, OSError):
            raise ChangesError("unavailable") from None
        finally:
            known.lock.release()

    def read(
        self,
        account: str,
        host: str,
        key: str,
        mode: GraphMode,
        revision: str | None,
        offset: int,
        deadline: float,
        requested: float,
    ) -> GraphResult:
        if time.monotonic() >= deadline:
            raise ChangesError("busy", 1)
        if (
            mode not in ("all", "branch")
            or type(offset) is not int
            or not 0 <= offset < MAX_HISTORY
        ):
            raise ChangesError("stale_snapshot")
        known = self._cache.worktree(key)
        if not known.lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            raise ChangesError("busy", 1)
        try:
            if revision is not None:
                entry = self._cache.graph((account, host, key, mode, revision))
                page = self._cache.graph_page(entry, known, offset)
                if page is not None:
                    return page
                reader = self._reader(known.worktree, deadline)
                reader.check_history()
                page = self._page(reader, entry.context, offset)
                return self._cache.put_graph_page(entry, known, offset, page)
            if offset:
                raise ChangesError("stale_snapshot")
            saved = self._bases.get(account, host, known.worktree)
            choice = saved[1] if saved is not None else "default"
            base = saved[0] if saved is not None else None
            guard = guard_stamp(known.worktree, choice)
            latest = self._cache.latest_graph(account, host, key, mode)
            candidate = latest or self._cache.shared_graph(key, None, guard)
            if candidate is not None and candidate.context.probe_guard == guard:
                current = self._shared_probe(key, known, guard, requested)
                if current is None:
                    stamp = ref_stamp(known.worktree, deadline)
                    if stamp is not None and stamp == candidate.context.probe_refs:
                        current = GraphProbe(candidate.context.probe_digest, guard, stamp)
                    else:
                        current = self._current_probe(
                            key, known, deadline, base, choice, requested, stamp
                        )
                if guard_stamp(known.worktree, choice) != guard:
                    raise ChangesError("busy", 1)
                if latest is not None and current.digest == latest.context.probe_digest:
                    if current.refs != latest.context.probe_refs:
                        self._cache.update_graph_probe(
                            latest, known, current.digest, current.guard, current.refs
                        )
                    cached_page = self._cache.graph_page(latest, known, 0)
                    if cached_page is not None:
                        self._remember_probe(key, known, current)
                        return cached_page
                shared = self._cache.shared_graph(key, current.digest, current.guard)
                if shared is not None:
                    context = replace(shared.context, account=account, host=host, mode=mode)
                    if shared.context.mode == mode:
                        page = self._cache.graph_page(shared, known, 0)
                    else:
                        page = None
                    if page is None:
                        reader = self._reader(known.worktree, deadline)
                        reader.check_history()
                        page = self._page(reader, context, 0)
                    entry = self._cache.remember_graph(context, known)
                    found = self._cache.put_graph_page(entry, known, 0, page)
                    self._remember_probe(key, known, current)
                    return found
            reader = self._reader(known.worktree, deadline)
            reader.check_history()
            for _ in range(2):
                saved = self._bases.get(account, host, known.worktree)
                choice = saved[1] if saved is not None else "default"
                initial_refs = ref_stamp(known.worktree, deadline)
                initial = (
                    self._probe(known.worktree, deadline, saved[0] if saved else None, choice)
                    if initial_refs is None
                    else None
                )
                ref, choice = self._choice(account, host, known.worktree, reader)
                before = reader.capture(ref, choice)
                if initial is None:
                    initial = replace(before.probe, refs=initial_refs)
                if initial.guard != guard:
                    raise ChangesError("busy", 1)
                try:
                    cached_entry = self._cache.graph((account, host, key, mode, before.revision))
                    cached_page = self._cache.graph_page(cached_entry, known, 0)
                except ChangesError as exc:
                    if exc.code != "stale_snapshot":
                        raise
                    cached_page = None
                if cached_page is not None:
                    final_refs = ref_stamp(known.worktree, deadline)
                    current = (
                        self._probe(known.worktree, deadline, saved[0] if saved else None, choice)
                        if initial_refs is None or final_refs is None
                        else replace(before.probe, refs=final_refs)
                    )
                    if initial == current and guard_stamp(known.worktree, choice) == current.guard:
                        self._cache.update_graph_probe(
                            cached_entry, known, current.digest, current.guard, current.refs
                        )
                        self._remember_probe(key, known, current)
                        return cached_page
                    continue
                ahead = reader.membership(before.head, before.base_oid)
                behind = reader.behind(before.head, before.base_oid)
                context = GraphContext(
                    account,
                    host,
                    key,
                    before.revision,
                    mode,
                    before.head,
                    before.branch,
                    before.base_name,
                    before.base_oid,
                    before.split_oid,
                    before.roots,
                    before.labels,
                    ahead,
                    behind,
                    before.partial,
                    self._clock(),
                    self._wall_ms(),
                    initial.digest,
                    initial.guard,
                    initial.refs,
                )
                page = self._page(reader, context, 0)
                ref, choice = self._choice(account, host, known.worktree, reader)
                after = reader.capture(ref, choice)
                final_refs = ref_stamp(known.worktree, deadline)
                final = (
                    self._probe(known.worktree, deadline, saved[0] if saved else None, choice)
                    if initial_refs is None or final_refs is None
                    else replace(after.probe, refs=final_refs)
                )
                if final.guard != guard:
                    raise ChangesError("busy", 1)
                if before.revision == after.revision and initial == final:
                    entry = self._cache.remember_graph(context, known)
                    found = self._cache.put_graph_page(entry, known, 0, page)
                    self._remember_probe(key, known, final)
                    return found
            raise ChangesError("busy", 1)
        except ReadError as exc:
            raise ChangesError("too_large" if str(exc) == "truncated" else "unavailable") from None
        except (GitFailed, GitUnavailable, OSError):
            raise ChangesError("unavailable") from None
        finally:
            known.lock.release()

    def _current_probe(
        self,
        key: str,
        known: Known,
        deadline: float,
        base: str | None,
        choice: str,
        requested: float,
        refs: str | None,
    ) -> GraphProbe:
        guard = guard_stamp(known.worktree, choice)
        shared = self._shared_probe(key, known, guard, requested)
        if shared is not None and shared.refs == refs:
            return shared
        if refs is None:
            self._reader(known.worktree, deadline)
        result = self._probe(known.worktree, deadline, base, choice)
        self._remember_probe(key, known, result)
        return result

    def _shared_probe(
        self, key: str, known: Known, guard: str, requested: float
    ) -> GraphProbe | None:
        with self._probe_lock:
            sample = self._probes.get(key)
        if (
            sample is not None
            and sample.owner is known
            and sample.result.guard == guard
            and requested <= sample.completed
        ):
            return replace(sample.result, refs=sample.refs)
        return None

    def _remember_probe(self, key: str, known: Known, result: GraphProbe) -> None:
        with self._probe_lock:
            if self._closed:
                return
            self._probes[key] = SharedProbe(known, result, time.monotonic(), result.refs)
            self._probes.move_to_end(key)
            while len(self._probes) > MAX_WORKTREES:
                self._probes.popitem(last=False)

    def close(self) -> None:
        with self._probe_lock:
            self._closed = True
            self._probes.clear()

    def _choice(
        self, account: str, host: str, worktree: Worktree, reader: GraphReader
    ) -> tuple[str, str]:
        saved = self._bases.get(account, host, worktree)
        kept = saved is not None and reader.resolve(saved[0]) is not None
        # A deleted saved base falls back to the default, keeping the choice's revision.
        ref = saved[0] if kept and saved is not None else reader.remote_default()
        if ref is None:
            raise ChangesError("no_base")
        return ref, saved[1] if saved is not None else "default"

    def _page(self, reader: GraphReader, context: GraphContext, offset: int) -> GraphResult:
        if context.mode == "branch":
            roots = context.head, f"^{context.base_oid}"
            rows = reader.records(roots, offset)[:PAGE_SIZE] if offset < len(context.ahead) else ()
            if offset + len(rows) == len(context.ahead) and len(rows) < PAGE_SIZE:
                rows += (reader.one(context.split_oid),)
            more = offset + len(rows) < len(context.ahead) + 1
        else:
            found = reader.records(context.roots, offset)
            rows, more = found[:PAGE_SIZE], len(found) > PAGE_SIZE
        capped = more and offset + len(rows) >= MAX_HISTORY
        next_offset = offset + len(rows) if more and not capped else None
        limit: GraphLimit | None = (
            "history_limit" if capped else "ref_limit" if context.ref_limited else None
        )
        labels: dict[str, list[GraphRef]] = {}
        for label in context.labels:
            value = GraphRef(name=display(label.name), kind=label.kind)
            values = labels.setdefault(label.oid, [])
            if value not in values:
                values.append(value)
        commits = tuple(
            GraphCommit(
                oid=row.oid,
                parents=row.parents,
                subject=row.subject,
                time_ms=row.time_s * 1000,
                ahead=row.oid in context.ahead,
                refs=tuple(sorted(labels.get(row.oid, []), key=lambda r: (r.kind, r.name))),
            )
            for row in rows
        )
        return GraphResult(
            GraphPage(
                snapshot=context.revision,
                mode=context.mode,
                head=context.head,
                branch=display(context.branch.removeprefix(b"refs/heads/"))
                if context.branch
                else None,
                base_name=display(context.base_name),
                base_oid=context.base_oid,
                split_oid=context.split_oid,
                ahead=len(context.ahead),
                behind=context.behind,
                commits=commits,
                as_of_ms=context.as_of_ms,
                partial=context.ref_limited or capped,
                limit_reason=limit,
            ),
            next_offset,
        )
