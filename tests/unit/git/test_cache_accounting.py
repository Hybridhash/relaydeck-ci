"""Serialization stays outside the cache lock; retirement still fences publication."""

from dataclasses import replace
from pathlib import Path

import pytest

from relaydeck.contracts.git import FileDiff
from relaydeck.git import cache as cache_module
from relaydeck.git.cache import SnapshotCache, manifest_bytes
from relaydeck.git.locator import Worktree
from relaydeck.git.parser import page_size
from relaydeck.git.results import ChangesError, Scope, Snapshot


def populated(tmp_path: Path) -> tuple[SnapshotCache, Snapshot, FileDiff]:
    cache = SnapshotCache()
    worktree = Worktree(str(tmp_path), str(tmp_path), str(tmp_path), (1, 2, 3))
    cache.remember_worktree("tree", worktree)
    snap = Snapshot(
        "account",
        "host",
        "tree",
        "working",
        "revision",
        None,
        None,
        "old",
        None,
        0,
        (),
        False,
        0,
    )
    cache.remember(snap, cache.worktree("tree"))
    return cache, snap, FileDiff(file_id="file", snapshot="revision", render="ok", hunks=())


def test_serialization_is_unlocked_and_retirement_debits_stored_sizes_without_reserializing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache, snap, page = populated(tmp_path)
    known = cache.worktree("tree")
    original, calls = page_size, []

    def measure(value: FileDiff) -> int:
        assert cache.lock.acquire(blocking=False), "serialization held the accounting lock"
        cache.lock.release()
        calls.append(value)
        return original(value)

    monkeypatch.setattr(cache_module, "page_size", measure)
    cache.put_pages(snap, known, 0, (page,))
    assert cache.bytes == manifest_bytes(snap) + original(page)
    cache.put_pages(snap, known, 0, (page,))
    assert len(calls) == 2 and cache.bytes == manifest_bytes(snap) + original(page)
    cache.clear_branch("account", "host", "tree", known)
    assert len(calls) == 2 and cache.bytes == manifest_bytes(snap)
    assert not known.pages and not known.page_bytes
    monkeypatch.setattr(cache_module, "MAX_CACHE_BYTES", 0)
    cache.put_pages(snap, known, 0, (page,))
    assert len(calls) == 3 and cache.bytes == 0
    assert not cache.snapshots and not known.pages and not known.page_bytes


@pytest.mark.parametrize("retire", ["snapshot", "worktree"])
def test_retirement_during_unlocked_serialization_prevents_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retire: str
) -> None:
    cache, snap, page = populated(tmp_path)
    known, original = cache.worktree("tree"), page_size

    def measure(value: FileDiff) -> int:
        with cache.lock:
            if retire == "worktree":
                cache._drop_worktree("tree")
            else:
                cache._drop_snapshot(("account", "host", "tree", "working", "revision"))
        return original(value)

    monkeypatch.setattr(cache_module, "page_size", measure)
    with pytest.raises(ChangesError, match="^stale_snapshot$"):
        cache.put_pages(snap, known, 0, (page,))
    assert cache.bytes == 0 and not known.pages and not known.page_bytes


@pytest.mark.parametrize("scope", ["working", "branch"])
def test_closed_cache_preserves_legacy_completion_without_retaining_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: Scope
) -> None:
    cache, original, page = populated(tmp_path)
    known = cache.worktree("tree")
    snap = replace(original, scope=scope)
    cache.remember(snap, known)

    def close(value: FileDiff) -> int:
        cache.close()
        return page_size(value)

    monkeypatch.setattr(cache_module, "page_size", close)
    assert cache.put_pages(snap, known, 0, (page,)) == (page,)
    assert cache.bytes == 0 and not cache.snapshots and not cache.known


@pytest.mark.parametrize("scope", ["working", "branch", "commit"])
@pytest.mark.parametrize("fenced", [False, True])
@pytest.mark.parametrize("completion", ["pages", "render"])
def test_closed_cache_completion_uses_explicit_fence_intent(
    tmp_path: Path, scope: Scope, fenced: bool, completion: str
) -> None:
    cache, original, page = populated(tmp_path)
    known = cache.worktree("tree")
    snap = replace(original, scope=scope)
    cache.remember(snap, known)
    cache.close()

    def complete() -> None:
        if completion == "pages":
            assert cache.put_pages(snap, known, 0, (page,), fenced=fenced) == (page,)
        else:
            cache.validate_completion(snap, known, fenced=fenced)

    if fenced:
        with pytest.raises(ChangesError, match="^stale_snapshot$"):
            complete()
    else:
        complete()
    assert cache.bytes == 0 and not cache.snapshots and not cache.known
