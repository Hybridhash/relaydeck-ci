"""Bounded immutable usage reads with typed page and refusal results."""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import secrets
from collections import OrderedDict
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from relaydeck.gateway.features.usage_read.model import UsagePage, UsageRefusal, UsageResult
from relaydeck.usage.api import (
    HOOK_FILENAME,
    MAX_QUERY_ROWS,
    READERS,
    Coverage,
    HookSetupCheck,
    RefreshDecision,
    SqliteUsageStore,
    UsageQuery,
    UsageUnavailable,
    default_hook_path,
    upgrade_outdated_hook,
)

if TYPE_CHECKING:
    from relaydeck.usage.api import UsageConfig


LOG = logging.getLogger(__name__)
CURSOR_TTL_MS = 300_000
MAX_REPORT_BYTES = 8 * 1024 * 1024
MAX_CACHE_BYTES = 32 * 1024 * 1024
MAX_REPORTS = 16
BUSY_WAIT_SECONDS = 1.0
MAX_INFLIGHT = 16
MAX_WAITERS = 32
RETRY_AFTER_SECONDS = 2


class _Busy(Exception):
    """The bounded wait for the worker slot expired."""


@dataclass(frozen=True)
class _Snapshot:
    binding: tuple[str, ...]
    expires: int
    pages: tuple[bytes, ...]
    cursors: tuple[str, ...]
    generation: int = -1
    price_digest: str = ""
    query_key: tuple[str, str, bool] = ("", "", False)
    identity: str = field(default_factory=lambda: '"' + secrets.token_urlsafe(24) + '"')

    @property
    def size(self) -> int:
        return sum(map(len, self.pages))


class UsageService:
    def __init__(
        self,
        store: SqliteUsageStore | None,
        config: UsageConfig,
        host_id: str,
        state_dir: Path,
        clock: Callable[[], int],
        *,
        workspace_labels: Callable[[], dict[str, str]] | None = None,
        limits_status: Callable[[], Mapping[str, Mapping[str, Any]]] | None = None,
        limits_refresh_status: Callable[[], Mapping[str, Mapping[str, Any]]] | None = None,
        request_limits_refresh: Callable[[str], RefreshDecision] | None = None,
        on_usage_open: Callable[[], object] | None = None,
        hooks_dir: Path | None = None,
    ) -> None:
        self.store, self.config, self.host_id, self.clock = store, config, host_id, clock
        self.workspace_labels = workspace_labels or (lambda: {})
        self._limits_status = limits_status
        self._limits_refresh_status = limits_refresh_status
        self.request_limits_refresh = request_limits_refresh
        self.on_usage_open = on_usage_open
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="usage-query")
        self._validator = ThreadPoolExecutor(max_workers=1, thread_name_prefix="usage-validate")
        # Cache validation never queues behind reports. Identical requests share `_inflight`;
        # only misses wait for the report slot, bounded by `wait_s`.
        self._slot = asyncio.Lock()
        self._validation_slot = asyncio.Lock()
        self._waiters = 0
        self._workers: set[asyncio.Future[Any]] = set()
        self._inflight: dict[tuple[Any, ...], asyncio.Task[_Snapshot]] = {}
        self.wait_s = BUSY_WAIT_SECONDS
        self.request_wait_s = BUSY_WAIT_SECONDS
        self._closed = False
        self._reports: OrderedDict[str, _Snapshot] = OrderedDict()
        self._cursors: dict[str, tuple[str, int]] = {}
        self._reuse: OrderedDict[tuple[str, ...], _Snapshot] = OrderedDict()
        self._hooks_dir = hooks_dir
        script = (hooks_dir or default_hook_path().parent) / HOOK_FILENAME
        self._sources = {(s.kind, s.format): s for s in config.sources}
        self._checks = {
            (s.kind, s.format): HookSetupCheck(s.limits_settings, s.limits_cache, script)
            for s in config.sources
        }

    def _refresh_hook_script(self, source: Any) -> None:
        # A previous shipped hook is replaced once the check calls it outdated. An upgrade
        # that cannot proceed safely is left for later; startup never fails over it.
        settings, cache = source.limits_settings, source.limits_cache
        if settings is None or cache is None:
            return
        try:
            outcome = upgrade_outdated_hook(settings, cache, hooks_dir=self._hooks_dir)
        except Exception:
            LOG.warning("usage_hook_upgrade_failed source=%s", f"{source.kind}/{source.format}")
            return
        if outcome != "not_upgraded":
            # The script changed in both cases, so the cached diagnosis is stale.
            self._checks[(source.kind, source.format)].invalidate()
        if outcome == "upgraded":
            LOG.info("usage_hook_upgraded source=%s", f"{source.kind}/{source.format}")
        elif outcome == "uncertain":
            LOG.warning("usage_hook_upgrade_uncertain source=%s", f"{source.kind}/{source.format}")

    def check_setup(self) -> None:
        for check in self._checks.values():
            check.check(self.clock())

    def run_startup_upgrades(self) -> None:
        # Startup only, called synchronously on the event-loop thread before the server
        # starts: each outdated source gets exactly one attempt. No awaits happen here,
        # so startup cancellation cannot land mid-transaction; shutdown only runs after.
        for source in self._sources.values():
            self._refresh_hook_script(source)

    def _fingerprint(self, now: int) -> str:
        """Digest of snapshot inputs that live outside the usage store
        (live labels, limits plane state, hook setup checks), so a cached
        report is reused only while every embedded field would be identical."""
        try:
            checks = [
                self._checks[source.kind, source.format].check(now)
                for source in self.config.sources
            ]
            payload = [
                self.workspace_labels(),
                self._limits_status() if self._limits_status is not None else {},
                self._limits_refresh_status() if self._limits_refresh_status is not None else {},
                checks,
            ]
            return hashlib.sha256(
                json.dumps(payload, sort_keys=True, default=str).encode()
            ).hexdigest()
        except Exception:
            return ""

    def refresh_attempts(self) -> list[tuple[str, int | None, str | None]]:
        """(kind, finished_ms, result) of each tool's last on-demand refresh."""
        status = self._limits_refresh_status() if self._limits_refresh_status is not None else {}
        out: list[tuple[str, int | None, str | None]] = []
        for kind, entry in status.items():
            last = entry.get("last_attempt")
            out.append((kind, last["finished_ms"], last["result"]) if last else (kind, None, None))
        return out

    def _metadata(self, now: int) -> list[dict[str, Any]]:
        kinds: dict[str, dict[str, Any]] = {}
        plane = self._limits_status() if self._limits_status is not None else {}
        refresh = self._limits_refresh_status() if self._limits_refresh_status is not None else {}
        for source in self.config.sources:
            kind = kinds.setdefault(
                source.kind,
                {
                    "kind": source.kind,
                    "display_name": source.display_name,
                    "tokens_reported": False,
                    "credits_reported": False,
                    "limits_setup": None,
                    "sources": [],
                },
            )
            if source.kind in plane and "limits_state" not in kind:
                kind["limits_state"] = dict(plane[source.kind])
            if "limits_refresh" not in kind:
                # every kind reports refresh availability; kinds
                # without an on-demand block are explicitly not configured.
                kind["limits_refresh"] = (
                    dict(refresh[source.kind])
                    if source.kind in refresh
                    else {
                        "available": False,
                        "reason": "not_configured",
                        "in_progress": False,
                        "cooldown_until_ms": None,
                        "last_error": None,
                        "last_attempt": None,
                    }
                )
            kind["tokens_reported"] |= bool(
                getattr(READERS[source.format], "tokens_reported", False)
            )
            # kinds whose readers report credits (never tokens) instead.
            kind["credits_reported"] |= bool(
                getattr(READERS[source.format], "credits_reported", False)
            )
            check = self._checks[source.kind, source.format].check(now)
            if check is not None:
                kind["limits_setup"] = check  # v1 field: the last configured source's state
            # Every configured source of the kind, with its own format and setup state (v2).
            kind["sources"].append({"format": source.format, "limits_setup": check})
        return list(kinds.values())

    def _report(self, query: UsageQuery, now: int) -> dict[str, Any]:
        if self.store is None:
            raise RuntimeError
        report = self.store.report_snapshot(query, now, inferred=self.config.inferred_linking)
        body = asdict(report)
        body["coverage"] = list(body["coverage"])
        # A kind with provider readings hides log-derived rows; kinds without them retain log rows.
        plane_kinds = {entry["kind"] for entry in body["limits"] if entry.get("source")}
        if plane_kinds:
            body["limits"] = [
                entry
                for entry in body["limits"]
                if entry.get("source") or entry["kind"] not in plane_kinds
            ]
        # Each space's last day with usage comes from the store, read before any space filter: a
        # space filter never erases another space's recency. No row in the period means null.
        last_day = {space: day for space, day in body.pop("workspace_days")}
        if query.space_id is not None:
            body["rows"] = tuple(row for row in body["rows"] if row["scope"] == "workspace")
        # Every space the host knows about (its inventory labels), plus any space that has rows.
        # Superseded generations show the label stored with their episodes; the current
        # generation prefers the live inventory label.
        labels = self.workspace_labels()
        stored = dict(body.pop("workspace_labels"))
        body["workspaces"] = [
            {
                "workspace_id": space,
                "label": labels.get(space, stored.get(space, space)),
                "last_used_day": last_day.get(space),
            }
            for space in sorted({*labels, *last_day})
        ]
        body["snapshot_labels"] = [
            {"date": day, "label": label} for day, label in self.store.prices.snapshot_labels
        ]
        body["kinds"] = self._metadata(now)
        activity = dict(body.pop("kind_activity"))
        for entry in body["kinds"]:
            entry["last_activity_ms"] = activity.get(entry["kind"])
        present = {row["kind"] for row in body["coverage"]}
        body["coverage"].extend(
            {
                "kind": kind["kind"],
                "coverage": Coverage.UNAVAILABLE,
                "observed_ms": None,
                "unmapped_sessions": 0,
                "linked_sessions": 0,
                "inferred_sessions": 0,
            }
            for kind in body["kinds"]
            if kind["kind"] not in present
        )
        return body

    def _snapshot(
        self,
        query: UsageQuery,
        now: int,
        binding: tuple[str, ...],
        price_digest: str = "",
    ) -> _Snapshot:
        body = self._report(query, now)
        generation = body.pop("data_generation")  # The report's consistent read, not validation.
        rows = body["rows"]
        cursors = tuple(
            secrets.token_urlsafe(32) for _ in range((max(1, len(rows)) - 1) // MAX_QUERY_ROWS)
        )
        pages: list[bytes] = []
        size = 0
        for index, start in enumerate(range(0, max(1, len(rows)), MAX_QUERY_ROWS)):
            page = {
                **body,
                "rows": rows[start : start + MAX_QUERY_ROWS],
                "next_cursor": cursors[index] if index < len(cursors) else None,
            }
            value = json.dumps(page, separators=(",", ":"), allow_nan=False).encode()
            size += len(value)
            if size > MAX_REPORT_BYTES:
                raise RuntimeError
            pages.append(value)
        return _Snapshot(
            binding,
            now + CURSOR_TTL_MS,
            tuple(pages),
            cursors,
            generation=generation,
            price_digest=price_digest,
            query_key=(query.from_day, query.to_day, query.hour),
        )

    def _drop(self, key: str) -> None:
        snap = self._reports.pop(key)
        for cursor in snap.cursors:
            self._cursors.pop(cursor, None)
        for binding, reusable in tuple(self._reuse.items()):
            if reusable is snap:
                del self._reuse[binding]  # Never hand off a page with evicted cursors.

    def _prune(self, now: int) -> None:
        for key, snap in tuple(self._reports.items()):
            if now >= snap.expires:
                self._drop(key)
        for reuse_binding, reuse_snap in tuple(self._reuse.items()):
            if now >= reuse_snap.expires:
                del self._reuse[reuse_binding]

    async def page(
        self,
        query: UsageQuery,
        account: str,
        device: str,
        *,
        selector: str | None = None,
        if_none_match: str | None = None,
    ) -> UsageResult:
        now = self.clock()
        self._prune(now)
        binding = (
            account,
            device,
            query.host_id,
            selector or f"range:{query.from_day}:{query.to_day}",
            query.space_id or "",
            self.config.timezone,
        )
        if self._closed or self.store is None or not self.store.accepting_work:
            return UsageRefusal("usage_unavailable", 503)
        if query.cursor is not None:
            pointer = self._cursors.get(query.cursor)
            if pointer is None:
                return UsageRefusal("invalid_cursor", 400)
            key, offset = pointer
            snap = self._reports[key]
            if snap.binding != binding:
                return UsageRefusal("invalid_cursor", 400)
            return UsagePage(snap.pages[offset])
        flight = (binding, query.from_day, query.to_day, query.hour)
        task = self._inflight.get(flight)
        cached = self._cached(query, binding)
        if (
            task is None
            and len(self._inflight) < MAX_INFLIGHT
            and (cached is not None or self._waiters < MAX_WAITERS)
        ):
            task = asyncio.get_running_loop().create_task(self._produce(query, now, binding))
            self._inflight[flight] = task
            task.add_done_callback(functools.partial(self._finished, flight))
        if cached is not None and if_none_match is None:
            # Immutable, explicitly as-of data stays available during a single-flight
            # refresh, including when refresh admission is temporarily saturated.
            return self._first_page(cached, None)
        if task is None or self._waiters >= MAX_WAITERS:
            if cached is not None:
                # Admission cannot validate freshness: serve immutable as-of
                # data with 200, never an unvalidated 304.
                return self._first_page(cached, None)
            return self._busy_response()
        self._waiters += 1
        try:
            # Waiters share the computation; one disconnecting cancels only its own wait.
            snap = await asyncio.wait_for(asyncio.shield(task), self.request_wait_s)
        except (_Busy, TimeoutError):
            return self._busy_response()
        except UsageUnavailable as exc:
            if exc.code == "busy":
                return self._busy_response()
            return UsageRefusal("usage_unavailable", 503)
        except asyncio.CancelledError:
            if task.cancelled():
                return UsageRefusal("usage_unavailable", 503)
            raise
        except Exception:
            return UsageRefusal("usage_unavailable", 503)
        finally:
            self._waiters -= 1
        return self._first_page(snap, if_none_match)

    @staticmethod
    def _first_page(snap: _Snapshot, if_none_match: str | None) -> UsagePage:
        if if_none_match == snap.identity:
            return UsagePage(b"", 304, snap.identity)
        return UsagePage(snap.pages[0], 200, snap.identity)

    def _cached(self, query: UsageQuery, binding: tuple[str, ...]) -> _Snapshot | None:
        snap = self._reuse.get(binding)
        if (
            snap is not None
            and self.clock() < snap.expires
            and snap.query_key == (query.from_day, query.to_day, query.hour)
        ):
            return snap
        return None

    @staticmethod
    def _busy_response() -> UsageRefusal:
        return UsageRefusal("usage_busy", 503, RETRY_AFTER_SECONDS)

    @property
    def _busy(self) -> bool:
        """True while a computation holds the worker slot."""
        return self._slot.locked()

    def _finished(self, key: tuple[Any, ...], done: asyncio.Task[_Snapshot]) -> None:
        if self._inflight.get(key) is done:
            del self._inflight[key]
        if not done.cancelled():
            _ = done.exception()  # retrieved: a failure is reported to the waiters

    def _validate(self, now: int, cached: _Snapshot | None) -> tuple[_Snapshot | None, int, str]:
        # Even cache validation can wait for the collector's SQLite lock or read hook files.
        gen_fn = getattr(self.store, "data_generation", None)
        digest_fn = getattr(self.store, "price_digest", None)
        generation = gen_fn() if callable(gen_fn) else -1
        digest = digest_fn() if callable(digest_fn) else ""
        if generation >= 0:
            digest = f"{digest}:{self._fingerprint(now)}"
        if (
            cached is not None
            and generation >= 0
            and cached.generation == generation
            and cached.price_digest == digest
            and now < cached.expires
        ):
            return cached, generation, digest
        return None, generation, digest

    async def _compute(
        self, query: UsageQuery, now: int, binding: tuple[str, ...], digest: str
    ) -> _Snapshot:
        """Only cache misses take the bounded report slot, until the worker truly finishes."""
        try:
            async with asyncio.timeout(self.wait_s):
                await self._slot.acquire()
        except TimeoutError:
            raise _Busy from None
        return await self._work(
            self._executor, self._slot, self._snapshot, query, now, binding, digest
        )

    async def _work[T](
        self,
        executor: ThreadPoolExecutor,
        slot: asyncio.Lock,
        call: Callable[..., T],
        *args: Any,
    ) -> T:
        if self._closed:
            slot.release()
            raise _Busy
        try:
            future = asyncio.get_running_loop().run_in_executor(executor, call, *args)
        except BaseException:
            slot.release()
            raise
        self._workers.add(future)

        def release(done: asyncio.Future[Any]) -> None:
            # Cancellation never frees a slot while its native call is still running.
            slot.release()
            self._workers.discard(done)
            if not done.cancelled():
                _ = done.exception()

        future.add_done_callback(release)
        # Only HTTP waiters time out. Keep the producer alive to cache a slow
        # report, retaining its slot until native work finishes or shutdown begins.
        return await asyncio.shield(future)

    async def _produce(self, query: UsageQuery, now: int, binding: tuple[str, ...]) -> _Snapshot:
        """Coalesce validation and report work; cache hits bypass report admission entirely."""
        try:
            async with asyncio.timeout(self.wait_s):
                await self._validation_slot.acquire()
        except TimeoutError:
            raise _Busy from None
        if self._closed:
            self._validation_slot.release()
            raise _Busy
        snap, _, digest = await self._work(
            self._validator,
            self._validation_slot,
            self._validate,
            now,
            self._cached(query, binding),
        )
        if snap is None:
            snap = await self._compute(query, now, binding, digest)
        if self._closed or self.clock() >= snap.expires:
            raise RuntimeError
        if self._reuse.get(binding) is snap:
            return snap  # A validated cache hit keeps its existing report/cursor identity.
        while self._reports and (
            len(self._reports) >= MAX_REPORTS
            or sum(s.size for s in self._reports.values()) + snap.size > MAX_CACHE_BYTES
        ):
            self._drop(next(iter(self._reports)))
        if snap.cursors:
            key = secrets.token_urlsafe(24)
            self._reports[key] = snap
            self._cursors.update({cursor: (key, i + 1) for i, cursor in enumerate(snap.cursors)})
        self._reuse[binding] = snap
        self._reuse.move_to_end(binding)
        while len(self._reuse) > MAX_REPORTS:
            self._reuse.popitem(last=False)
        return snap

    async def close(self) -> None:
        self._closed = True
        tasks = tuple(self._inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Never enqueue joins behind the shared pool (also used by journal/attach work).
        # Runtime shutdown interrupts SQL; mutex waiters observe the shutdown flag.
        if self._workers:
            await asyncio.wait(self._workers, timeout=2.0)
        self._validator.shutdown(wait=False, cancel_futures=True)
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._reports.clear()
        self._cursors.clear()
        self._reuse.clear()
