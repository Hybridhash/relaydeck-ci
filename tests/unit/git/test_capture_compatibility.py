"""Complete-side capture preserves Git text normalization, byte limits and failure reasons."""

from __future__ import annotations

import asyncio
import subprocess
import tracemalloc
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from relaydeck.contracts.git import FileDiff
from relaydeck.git import cache as cache_module
from relaydeck.git import content as content_module
from relaydeck.git.base_store import BaseStore
from relaydeck.git.content import parse_pages
from relaydeck.git.locator import RepositoryLocator
from relaydeck.git.reader import MAX_SIDE_BYTES, ReadError, RepositoryReader
from relaydeck.git.runner import GitFailed, GitResult, GitRunner
from relaydeck.git.safe_fs import Unreadable
from relaydeck.git.secret_policy import UnsafeKeyContext, _Side
from relaydeck.git.service import ChangesError, ChangesService
from tests.support.git_identity import AUTHOR_CONFIG
from tests.support.git_pem import PEM_PRIVATE_HEADER_BYTES


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            *AUTHOR_CONFIG,
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    folder = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(folder)], check=True)
    (folder / "README.txt").write_text("synthetic fixture\n")
    git(folder, "add", "README.txt")
    git(folder, "commit", "-qm", "initial fixture")
    return folder


@pytest.fixture
def service(tmp_path: Path) -> Iterator[ChangesService]:
    svc = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))
    try:
        yield svc
    finally:
        svc.close()


def comparison(repo: Path, old: bytes, new: bytes, scope: str) -> None:
    target = repo / "config.txt"
    target.write_bytes(old)
    git(repo, "add", "config.txt")
    git(repo, "commit", "-qm", "comparison fixture")
    git(repo, "branch", "comparison")
    target.write_bytes(new)
    if scope == "branch":
        git(repo, "add", "config.txt")
        git(repo, "commit", "-qm", "changed fixture")


async def diff(service: ChangesService, repo: Path, scope: str, path: bytes) -> FileDiff:
    service._locate(str(repo))
    key = next(iter(service._cache.known))
    if scope == "branch":
        await service.set_base("account", "host", key, "refs/heads/comparison")
    snap = await service.snapshot("account", "host", key, scope)
    row = next(i for i, entry in enumerate(snap.rows) if entry.path == path)
    page, _ = await service.file_diff(snap, row, "file", 0, load=True)
    return page


@pytest.mark.parametrize("autocrlf", ["true", "input"])
@pytest.mark.parametrize("scope", ["working", "branch"])
@pytest.mark.parametrize("key_block", [False, True])
def test_crlf_normalization_keeps_verification_and_key_masking(
    repo: Path, service: ChangesService, autocrlf: str, scope: str, key_block: bool
) -> None:
    git(repo, "config", "core.autocrlf", autocrlf)
    if key_block:
        lines = [b"public", PEM_PRIVATE_HEADER_BYTES]
        lines.extend(f"FAKE_NOT_A_KEY_{i:02d}".encode() for i in range(30))
        lines.append(b"-----END PRIVATE KEY-----")
        old = b"\r\n".join(lines) + b"\r\n"
        new = old.replace(b"FAKE_NOT_A_KEY_15", b"FAKE_NOT_A_KEY_EDIT")
    else:
        old, new = b"one\r\ntwo\r\nthree\r\n", b"one\r\nTWO\r\nthree\r\n"
    comparison(repo, old, new, scope)
    page = asyncio.run(diff(service, repo, scope, b"config.txt"))
    assert page.render == "ok" and page.hunks
    rows = [line for hunk in page.hunks for line in hunk.lines]
    if key_block:
        assert all(line.masked and not line.marks for line in rows)
        assert all("FAKE" not in line.text for line in rows)
    else:
        assert any(line.text == "TWO" and line.kind == "add" for line in rows)
        assert not any(line.masked for line in rows)


def payload(size: int, first: bytes) -> bytes:
    prefix = first + b"\n"
    block = b"x" * 1023 + b"\n"
    count, remaining = divmod(size - len(prefix), len(block))
    return prefix + block * count + block[:remaining]


@pytest.mark.parametrize("scope", ["working", "branch"])
@pytest.mark.parametrize("side", ["old", "new"])
@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_complete_side_byte_limit_is_inclusive_for_both_comparisons(
    repo: Path, service: ChangesService, scope: str, side: str, delta: int
) -> None:
    sizes = {"old": MAX_SIDE_BYTES - 2, "new": MAX_SIDE_BYTES - 2}
    sizes[side] = MAX_SIDE_BYTES + delta
    comparison(repo, payload(sizes["old"], b"old"), payload(sizes["new"], b"new"), scope)
    runner = GitRunner.create()
    reader = RepositoryReader(runner, RepositoryLocator(runner).locate(str(repo)))
    old, new = git(repo, "rev-parse", "comparison"), None
    if scope == "branch":
        new = git(repo, "rev-parse", "HEAD")
    if delta > 0:
        with pytest.raises(ReadError, match="^too_large$"):
            reader.sides(old, new, (b"config.txt",))
    else:
        left, right = reader.sides(old, new, (b"config.txt",))
        assert (len(left), len(right)) == (sizes["old"], sizes["new"])
    page = asyncio.run(diff(service, repo, scope, b"config.txt"))
    if delta > 0:
        assert page.render == "too_large" and not page.hunks
    else:
        assert page.render == "ok" and page.hunks


def test_an_oversized_untracked_file_is_too_large_not_secret_withheld(
    repo: Path, service: ChangesService
) -> None:
    (repo / "notes.txt").write_bytes(b"x" * (MAX_SIDE_BYTES + 1))
    page = asyncio.run(diff(service, repo, "working", b"notes.txt"))
    assert page.render == "too_large" and not page.hunks


@pytest.mark.parametrize("old", [b"same changed\r\n", b"same\r\r\n"])
def test_crlf_tolerance_still_rejects_content_and_extra_carriage_return_mismatches(
    old: bytes,
) -> None:
    patch = b"@@ -1 +1 @@\n-same\n+new\n"
    (page,) = parse_pages(patch, (old, b"new\n"), "file", "snapshot")
    assert page.render == "withheld" and not page.hunks


@pytest.mark.parametrize(
    "stage,failure,expected",
    [
        ("sides", ReadError("unsupported"), "withheld"),
        ("sides", ReadError("too_large"), "too_large"),
        ("sides", ReadError("truncated"), "too_large"),
        ("sides", ReadError("invalid_encoding"), "invalid_encoding"),
        ("sides", ReadError("malformed"), "unavailable"),
        ("sides", GitFailed("git_timeout"), "unavailable"),
        ("patch", GitFailed("git_exit"), "unavailable"),
        ("patch", ReadError("unsupported"), "unavailable"),
        ("contained", Unreadable("too_large"), "too_large"),
        ("contained", Unreadable("unreadable"), "unavailable"),
        ("contained", Unreadable("changed"), "unavailable"),
    ],
)
def test_capture_preserves_size_encoding_and_operational_failure_reasons(
    repo: Path,
    service: ChangesService,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    failure: Exception,
    expected: str,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise failure

    if stage == "contained":
        (repo / "config.txt").write_bytes(b"untracked\n")
        monkeypatch.setattr(content_module, "read_contained", fail)
    else:
        comparison(repo, b"old\n", b"new\n", "working")
        monkeypatch.setattr(RepositoryReader, stage, fail)
    if expected == "unavailable":
        with pytest.raises(ChangesError, match="^unavailable$"):
            asyncio.run(diff(service, repo, "working", b"config.txt"))
    else:
        page = asyncio.run(diff(service, repo, "working", b"config.txt"))
        assert page.render == expected and not page.hunks


@pytest.mark.parametrize("source", ["working", "branch", "untracked"])
def test_changed_invalid_bytes_still_report_invalid_encoding(
    repo: Path, service: ChangesService, source: str
) -> None:
    if source == "untracked":
        (repo / "config.txt").write_bytes(b"invalid \xff\n")
    else:
        comparison(repo, b"old\n", b"invalid \xff\n", source)
    scope = "branch" if source == "branch" else "working"
    page = asyncio.run(diff(service, repo, scope, b"config.txt"))
    assert page.render == "invalid_encoding" and not page.hunks


@pytest.mark.parametrize("scope", ["working", "branch"])
def test_an_uncached_tracked_diff_uses_one_raw_query(
    repo: Path, service: ChangesService, monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    comparison(repo, b"old\n", b"new\n", scope)

    async def run() -> None:
        service._locate(str(repo))
        key = next(iter(service._cache.known))
        if scope == "branch":
            await service.set_base("account", "host", key, "refs/heads/comparison")
        snap = await service.snapshot("account", "host", key, scope)
        row = next(i for i, entry in enumerate(snap.rows) if entry.path == b"config.txt")
        queries = 0
        original = service._runner.run

        def query(cwd: str | None, args: Sequence[str], **kwargs: Any) -> GitResult:
            nonlocal queries
            queries += int(args[0] == "diff" and "--raw" in args)
            return original(cwd, args, **kwargs)

        monkeypatch.setattr(service._runner, "run", query)
        page, _ = await service.file_diff(snap, row, "file", 0)
        assert page.render == "ok" and queries == 1
        await service.file_diff(snap, row, "file", 0)
        assert queries == 1

    asyncio.run(run())


@pytest.mark.parametrize("key_block", [False, True])
def test_complete_side_verification_keeps_compact_indexes_not_decoded_file_copies(
    key_block: bool,
) -> None:
    prefix = (
        PEM_PRIVATE_HEADER_BYTES + b"\nFAKE-NOT-A-KEY\n-----END PRIVATE KEY-----\n"
        if key_block
        else b"plain\n"
    )
    block = b"x" * 1023 + b"\n"
    count, tail = divmod(MAX_SIDE_BYTES - len(prefix), len(block))
    raw = prefix + block * count + block[:tail]
    tracemalloc.start()
    try:
        side = _Side.read(raw)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < len(raw) // 4
    assert not side.masks(1500, "x" * 1023)
    if key_block:
        assert side.masks(2, "FAKE-NOT-A-KEY")
    else:
        assert not side.starts and not side.ends
    with pytest.raises(UnsafeKeyContext):
        side.masks(1500, "different")


def test_complete_side_buffers_are_released_before_cache_serialization(
    repo: Path, service: ChangesService, monkeypatch: pytest.MonkeyPatch
) -> None:
    comparison(repo, b"old\n", b"new\n", "working")
    released: list[bool] = []
    original, page_size = RepositoryReader.sides, cache_module.page_size

    class CapturedBytes(bytes):
        def __del__(self) -> None:
            released.append(True)

    def capture(self: RepositoryReader, *args: Any, **kwargs: Any) -> tuple[bytes, bytes]:
        old, new = original(self, *args, **kwargs)
        return CapturedBytes(old), CapturedBytes(new)

    def measure(page: FileDiff) -> int:
        assert len(released) == 2
        return page_size(page)

    monkeypatch.setattr(RepositoryReader, "sides", capture)
    monkeypatch.setattr(cache_module, "page_size", measure)
    page = asyncio.run(diff(service, repo, "working", b"config.txt"))
    assert page.render == "ok"
