"""Controlled cache retirement and observations for read-service boundary tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from relaydeck.contracts.git import FileDiff
from relaydeck.git.cache import manifest_bytes
from relaydeck.git.graph_cache import memory_size
from relaydeck.git.parser import page_size
from relaydeck.git.results import Snapshot
from relaydeck.git.service import ChangesService


class CacheControl:
    def __init__(self, service: ChangesService) -> None:
        self._cache = service._cache
        self._service = service

    @property
    def first_worktree(self) -> str:
        return next(iter(self._cache.known))

    @property
    def graph_count(self) -> int:
        return len(self._cache.graphs)

    @property
    def scopes(self) -> tuple[str, ...]:
        return tuple(s.scope for s in self._cache.snapshots.values())

    @property
    def bytes(self) -> int:
        return self._cache.bytes

    def pages(self, key: str) -> dict[tuple[str, int], tuple[FileDiff, ...]]:
        return self._cache.worktree(key).pages.copy()

    def watch_pages(self, key: str) -> Callable[[], bool]:
        known = self._cache.worktree(key)
        return lambda: bool(known.pages or known.page_bytes)

    def retire_graphs(self) -> None:
        with self._cache.lock:
            for key in list(self._cache.graphs):
                self._cache._drop_graph(key)

    def retire_scope(self, scope: str) -> None:
        with self._cache.lock:
            for key in list(self._cache.snapshots):
                if key[3] == scope:
                    self._cache._drop_snapshot(key)

    def retire_snapshot(self, snap: Snapshot) -> None:
        with self._cache.lock:
            self._cache._drop_snapshot(
                (snap.account, snap.host, snap.worktree, snap.scope, snap.revision)
            )

    def retire_worktree(self, key: str) -> None:
        with self._cache.lock:
            self._cache._drop_worktree(key)

    def replace_worktree(self, key: str) -> None:
        known = self._cache.worktree(key)
        self._cache.remember_worktree(
            key, replace(known.worktree, identity=(*known.worktree.identity, 1))
        )

    def expire_graphs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        entry = next(iter(self._cache.graphs.values()))
        monkeypatch.setattr(self._cache, "_clock", lambda: entry.context.created + 901)

    def use_clock(self, monkeypatch: pytest.MonkeyPatch, clock: Callable[[], float]) -> None:
        monkeypatch.setattr(self._cache, "_clock", clock)

    def close(self) -> None:
        self._cache.close()

    @property
    def maintenance_done(self) -> bool:
        task = self._service._maintenance
        return task is None or task.done()

    def watch_sweeps(self, monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
        original = self._cache.retire_idle
        count = [0]

        def sweep() -> None:
            count[0] += 1
            original()

        monkeypatch.setattr(self._cache, "retire_idle", sweep)
        return lambda: count[0]

    def sweep(self) -> None:
        self._cache.retire_idle()

    def assert_graph_membership_charged(self) -> None:
        with self._cache.lock:
            for entry in self._cache.graphs.values():
                assert entry.size >= memory_size(entry.context.ahead)

    def assert_accounted(self) -> None:
        with self._cache.lock:
            assert self._cache.bytes == (
                sum(manifest_bytes(s) for s in self._cache.snapshots.values())
                + sum(e.size for e in self._cache.graphs.values())
                + sum(
                    page_size(p)
                    for k in self._cache.known.values()
                    for ps in k.pages.values()
                    for p in ps
                )
            )
