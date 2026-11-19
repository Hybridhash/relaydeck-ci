"""Immutable, issued-page selections use bounded first-parent object comparisons."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from relaydeck.contracts.git import GraphMode, RenderState
from relaydeck.git import cache as cache_module
from relaydeck.git import secret_policy as policy_module
from relaydeck.git import service as service_module
from relaydeck.git.base_store import BaseStore
from relaydeck.git.graph_reader import GraphReader
from relaydeck.git.parser import page_size
from relaydeck.git.probe import ChangeProbe
from relaydeck.git.reader import ReadError, RepositoryReader
from relaydeck.git.results import ChangesError, Snapshot
from relaydeck.git.runner import GitRunner
from relaydeck.git.secret_policy import SecretPolicy
from relaydeck.git.service import ChangesService
from tests.support.git_cache import CacheControl
from tests.support.git_pem import PEM_PRIVATE_HEADER_BYTES
from tests.support.graph_repos import commit, git, located, repository


@pytest.fixture
def service(tmp_path: Path) -> Iterator[ChangesService]:
    value = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))
    yield value
    value.close()


async def selection(
    service: ChangesService, repo: Path, oid: str, mode: GraphMode = "all"
) -> Snapshot:
    key = await located(service, repo)
    await service.set_base("account", "host", key, "refs/heads/main")
    page = (await service.graph("account", "host", key, mode)).page
    return await service.commit_snapshot("account", "host", key, page.snapshot, mode, oid)


def save(repo: Path, path: str, data: bytes) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    git(repo, "add", "--", path)
    return commit(repo)


def accounted(service: ChangesService) -> None:
    CacheControl(service).assert_accounted()


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
@pytest.mark.parametrize("mode", ["all", "branch"])
def test_root_split_row_compares_with_object_format_empty_tree(
    tmp_path: Path, service: ChangesService, object_format: str, mode: GraphMode
) -> None:
    repo = repository(tmp_path / "repo", object_format=object_format)
    (repo / "root.txt").write_text("root content\n")
    git(repo, "add", ".")
    git(repo, "commit", "--amend", "--no-edit", "-q")
    root = git(repo, "rev-parse", "HEAD").decode()

    async def run() -> None:
        snap = await selection(service, repo, root, mode)
        empty = git(repo, "hash-object", "-t", "tree", "--stdin", data=b"").decode()
        assert snap.scope == "commit" and snap.head == root and snap.old == empty
        assert snap.branch is None and snap.base_name is None
        assert [(r.path, r.status, r.additions, r.deletions) for r in snap.rows] == [
            (b"root.txt", "A", 1, 0)
        ]
        diff, count = await service.file_diff(snap, 0, "file", 0)
        assert count == 1 and diff.render == "ok"
        assert [line.text for h in diff.hunks for line in h.lines] == ["root content"]
        accounted(service)

    asyncio.run(run())


def test_merge_uses_only_first_parent_and_empty_commit_has_no_files(
    tmp_path: Path, service: ChangesService
) -> None:
    repo = repository(tmp_path / "repo")
    root = git(repo, "rev-parse", "HEAD").decode()
    git(repo, "checkout", "-qb", "left")
    left = save(repo, "left.txt", b"left\n")
    git(repo, "checkout", "-qb", "right", root)
    right = save(repo, "right.txt", b"right\n")
    git(repo, "checkout", "left")
    git(repo, "merge", "--no-ff", "--no-edit", "right")
    merge = git(repo, "rev-parse", "HEAD").decode()
    empty = commit(repo)

    async def run() -> None:
        snap = await selection(service, repo, merge)
        assert snap.old == left and snap.old != right
        assert [r.path for r in snap.rows] == [b"right.txt"]
        diff, _ = await service.file_diff(snap, 0, "file", 0)
        assert [line.text for h in diff.hunks for line in h.lines] == ["right"]
        other = await selection(service, repo, empty)
        assert other.old == merge and other.rows == () and not other.partial

    asyncio.run(run())


def test_only_issued_rows_authorize_selection_before_any_git_read(
    tmp_path: Path, service: ChangesService, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = repository(tmp_path / "repo", 102)
    root = git(repo, "rev-list", "--max-parents=0", "HEAD").decode()

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/main")
        first = (await service.graph("account", "host", key, "all")).page
        original = service._graphs._reader
        monkeypatch.setattr(
            service._graphs, "_reader", lambda *a: pytest.fail("read before membership")
        )
        with pytest.raises(ChangesError, match="not_found"):
            await service.commit_snapshot("account", "host", key, first.snapshot, "all", root)
        for account, host, mode in (
            ("other", "host", "all"),
            ("account", "other", "all"),
            ("account", "host", "branch"),
        ):
            with pytest.raises(ChangesError, match="stale_snapshot"):
                await service.commit_snapshot(account, host, key, first.snapshot, mode, root)  # type: ignore[arg-type]
        with pytest.raises(ChangesError, match="not_found"):
            await service.snapshot("account", "host", key, "commit")
        monkeypatch.setattr(service._graphs, "_reader", original)
        await service.graph("account", "host", key, "all", revision=first.snapshot, offset=100)
        snap = await service.commit_snapshot("account", "host", key, first.snapshot, "all", root)
        assert snap.head == root

    asyncio.run(run())


def test_immutable_capture_ignores_dirty_index_head_and_base_movement(
    tmp_path: Path, service: ChangesService, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = repository(tmp_path / "repo")
    oid = save(repo, "file.txt", b"committed\n")

    async def run() -> None:
        snap = await selection(service, repo, oid)
        (repo / "file.txt").write_text("staged\n")
        git(repo, "add", ".")
        commit(repo)
        (repo / "file.txt").write_text("dirty\n")
        git(repo, "checkout", "--detach")
        monkeypatch.setattr(ChangeProbe, "probe", lambda *a: pytest.fail("mutable probe"))
        monkeypatch.setattr(service, "_base", lambda *a: pytest.fail("base resolution"))
        diff, _ = await service.file_diff(snap, 0, "file", 0)
        assert [line.text for h in diff.hunks for line in h.lines] == ["committed"]
        cache = CacheControl(service)
        pages = cache.pages(snap.worktree)
        await service.set_base("account", "host", snap.worktree, "refs/heads/main")
        assert cache.graph_count == 0 and cache.pages(snap.worktree) == pages
        assert service.cached("account", "host", snap.worktree, "commit", snap.revision) == snap
        monkeypatch.setattr(service, "_reader", lambda *a: pytest.fail("cached diff recaptured"))
        assert (await service.file_diff(snap, 0, "new-id", 0))[0].file_id == "new-id"
        accounted(service)

    asyncio.run(run())


@pytest.mark.parametrize("retirement", ["graph", "expired", "worktree", "close"])
def test_late_manifest_cannot_publish_after_owner_retirement(
    tmp_path: Path, service: ChangesService, monkeypatch: pytest.MonkeyPatch, retirement: str
) -> None:
    repo = repository(tmp_path / "repo")
    oid = save(repo, "file.txt", b"content\n")
    original = RepositoryReader.raw

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/main")
        page = (await service.graph("account", "host", key, "all")).page
        cache = CacheControl(service)

        def raw(reader: RepositoryReader, *args: Any, **kwargs: Any) -> Any:
            value = original(reader, *args, **kwargs)
            if retirement == "graph":
                cache.retire_graphs()
            elif retirement == "expired":
                cache.expire_graphs(monkeypatch)
            elif retirement == "worktree":
                cache.replace_worktree(key)
            else:
                CacheControl(service).close()
            return value

        monkeypatch.setattr(RepositoryReader, "raw", raw)
        with pytest.raises(ChangesError, match="stale_snapshot"):
            await service.commit_snapshot("account", "host", key, page.snapshot, "all", oid)
        assert "commit" not in cache.scopes
        accounted(service)

    asyncio.run(run())


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("binary", "binary"),
        ("invalid", "invalid_encoding"),
        ("symlink", "unsupported"),
        ("oversize", "too_large"),
        ("old_oversize", "too_large"),
        ("submodule", "unsupported"),
        ("pem", "ok"),
        ("unterminated", "withheld"),
        ("crlf", "ok"),
        ("no_newline", "ok"),
        ("large", "load_required"),
    ],
)
def test_commit_content_reuses_masking_and_render_limits(
    tmp_path: Path, service: ChangesService, kind: str, expected: str
) -> None:
    repo = repository(tmp_path / "repo")
    if kind in ("pem", "unterminated"):
        old = (
            PEM_PRIVATE_HEADER_BYTES
            + b"\n"
            + b"".join(f"old-secret-{i:02}\n".encode() for i in range(100))
        )
        if kind == "pem":
            old += b"-----END PRIVATE KEY-----\n"
        save(repo, "file.txt", old)
        oid = save(
            repo,
            "file.txt",
            old.replace(b"old-secret-10", b"new-secret-10").replace(
                b"old-secret-70", b"new-secret-70"
            ),
        )
    elif kind == "symlink":
        (repo / "file.txt").symlink_to("outside")
        git(repo, "add", ".")
        oid = commit(repo)
    elif kind == "submodule":
        root = git(repo, "rev-parse", "HEAD").decode()
        git(repo, "update-index", "--add", "--cacheinfo", f"160000,{root},module")
        oid = commit(repo)
    elif kind == "old_oversize":
        save(repo, "file.txt", b"x" * (2 * 1024 * 1024 + 1))
        oid = save(repo, "file.txt", b"small\n")
    else:
        data = {
            "binary": b"a\0b",
            "invalid": b"bad\xff\n",
            "oversize": b"x" * (2 * 1024 * 1024 + 1),
            "crlf": b"one\r\ntwo\r\n",
            "no_newline": b"last",
            "large": b"line\n" * 1200,
        }[kind]
        oid = save(repo, "file.txt", data)

    async def run() -> None:
        snap = await selection(service, repo, oid)
        diff, _ = await service.file_diff(snap, 0, "file", 0)
        assert diff.render == expected
        wire = json.dumps(diff.to_dict())
        assert "old-secret" not in wire and "new-secret" not in wire
        if kind == "pem":
            assert len(diff.hunks) == 2
        if kind == "large":
            loaded, count = await service.file_diff(snap, 0, "file", 0, load=True)
            assert loaded.render == "ok" and count > 1
        if kind == "no_newline":
            assert any(line.kind == "no_newline" for h in diff.hunks for line in h.lines)
        accounted(service)

    asyncio.run(run())


@pytest.mark.parametrize("reverse", [False, True])
def test_renamed_denied_path_and_commit_manifest_cap(
    tmp_path: Path, service: ChangesService, monkeypatch: pytest.MonkeyPatch, reverse: bool
) -> None:
    repo = repository(tmp_path / "repo")
    old, new = ("public.txt", ".env") if reverse else (".env", "public.txt")
    save(repo, old, b"secret=value\n")
    git(repo, "mv", old, new)
    oid = commit(repo)

    async def run() -> None:
        snap = await selection(service, repo, oid)
        assert snap.rows[0].old_path == old.encode() and snap.rows[0].withheld
        assert (await service.file_diff(snap, 0, "file", 0))[0].render == "withheld"
        for i in range(4):
            (repo / f"item-{i}.txt").write_text("line\n")
        git(repo, "add", ".")
        tip = commit(repo)
        monkeypatch.setattr(service_module, "MAX_ROWS", 2)
        capped = await selection(service, repo, tip)
        assert capped.partial and len(capped.rows) == 2

    asyncio.run(run())


def test_commit_file_kinds_and_deleted_blob_content(
    tmp_path: Path, service: ChangesService
) -> None:
    repo = repository(tmp_path / "repo")
    for path, text in (
        ("gone.txt", "deleted\n"),
        ("before.txt", "renamed\n"),
        ("run.sh", "run\n"),
        ("edit.txt", "before\n"),
    ):
        (repo / path).write_text(text)
    git(repo, "add", ".")
    parent = commit(repo)
    (repo / "gone.txt").unlink()
    (repo / "edit.txt").write_text("after\n")
    (repo / "run.sh").chmod(0o755)
    git(repo, "mv", "before.txt", "after.txt")
    (repo / "uv.lock").write_text("generated\n")
    git(repo, "add", "-A")
    oid = commit(repo)

    async def run() -> None:
        snap = await selection(service, repo, oid)
        assert snap.old == parent
        rows = {row.path: row for row in snap.rows}
        assert {path: row.status for path, row in rows.items()} == {
            b"gone.txt": "D",
            b"after.txt": "R",
            b"run.sh": "mode",
            b"edit.txt": "M",
            b"uv.lock": "A",
        }
        assert rows[b"after.txt"].old_path == b"before.txt" and rows[b"uv.lock"].generated
        for index, row in enumerate(snap.rows):
            diff, _ = await service.file_diff(snap, index, "file", 0)
            if row.path == b"gone.txt":
                assert [(line.kind, line.text) for h in diff.hunks for line in h.lines] == [
                    ("delete", "deleted")
                ]
            elif row.path == b"run.sh":
                assert diff.render == "unsupported" and not diff.hunks
            else:
                assert diff.render == "ok"
        accounted(service)

    asyncio.run(run())


def test_same_comparison_is_reused_across_chips_but_isolated_by_account(
    tmp_path: Path, service: ChangesService
) -> None:
    repo = repository(tmp_path / "repo")
    oid = save(repo, "file.txt", b"content\n")

    async def run() -> None:
        key = await located(service, repo)
        revisions = []
        for account, mode in (("account", "all"), ("account", "branch"), ("other", "all")):
            await service.set_base(account, "host", key, "refs/heads/main")
            page = (await service.graph(account, "host", key, mode)).page  # type: ignore[arg-type]
            snap = await service.commit_snapshot(account, "host", key, page.snapshot, mode, oid)  # type: ignore[arg-type]
            revisions.append(snap.revision)
        assert revisions[0] == revisions[1] and revisions[0] != revisions[2]
        accounted(service)

    asyncio.run(run())


def test_cached_selection_rechecks_graph_retirement(
    tmp_path: Path, service: ChangesService, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = repository(tmp_path / "repo")
    oid = save(repo, "file.txt", b"content\n")

    async def run() -> None:
        snap = await selection(service, repo, oid)
        page = (await service.graph("account", "host", snap.worktree, "all")).page
        original = GraphReader.check_history

        def retired(reader: GraphReader) -> None:
            original(reader)
            CacheControl(service).retire_graphs()

        monkeypatch.setattr(GraphReader, "check_history", retired)
        with pytest.raises(ChangesError, match="stale_snapshot"):
            await service.commit_snapshot(
                "account", "host", snap.worktree, page.snapshot, "all", oid
            )

    asyncio.run(run())


@pytest.mark.parametrize("retirement", ["snapshot", "worktree", "close"])
def test_late_commit_diff_cannot_repopulate_a_retired_owner(
    tmp_path: Path, service: ChangesService, monkeypatch: pytest.MonkeyPatch, retirement: str
) -> None:
    repo = repository(tmp_path / "repo")
    oid = save(repo, "file.txt", b"content\n")

    async def run() -> None:
        snap = await selection(service, repo, oid)
        cache = CacheControl(service)
        has_pages = cache.watch_pages(snap.worktree)
        original = page_size

        def measure(value: Any) -> int:
            if retirement == "close":
                CacheControl(service).close()
                return original(value)
            if retirement == "snapshot":
                cache.retire_snapshot(snap)
            else:
                cache.retire_worktree(snap.worktree)
            return original(value)

        monkeypatch.setattr(cache_module, "page_size", measure)
        with pytest.raises(ChangesError, match="stale_snapshot"):
            await service.file_diff(snap, 0, "file", 0)
        assert not has_pages()
        accounted(service)

    asyncio.run(run())


def test_commit_diff_refuses_cache_closed_before_publication(
    tmp_path: Path, service: ChangesService, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = repository(tmp_path / "repo")
    oid = save(repo, "file.txt", b"content\n")

    async def run() -> None:
        snap = await selection(service, repo, oid)

        def retire(value: Any) -> int:
            CacheControl(service).close()
            return page_size(value)

        monkeypatch.setattr(cache_module, "page_size", retire)
        with pytest.raises(ChangesError, match="^stale_snapshot$"):
            await service.file_diff(snap, 0, "file", 0)

    asyncio.run(run())


@pytest.mark.parametrize("retirement", ["close", "snapshot", "worktree"])
@pytest.mark.parametrize("reason", ["unsupported", "invalid_encoding", "too_large", "render"])
def test_commit_cache_rejects_render_only_completion(
    tmp_path: Path,
    service: ChangesService,
    monkeypatch: pytest.MonkeyPatch,
    retirement: str,
    reason: str,
) -> None:
    repo = repository(tmp_path / "repo")
    oid = save(repo, "file.txt", b"content\n")

    async def run() -> None:
        snap = await selection(service, repo, oid)
        cache = CacheControl(service)
        has_pages = cache.watch_pages(snap.worktree)

        def retire() -> None:
            if retirement == "close":
                CacheControl(service).close()
            else:
                if retirement == "snapshot":
                    cache.retire_snapshot(snap)
                else:
                    cache.retire_worktree(snap.worktree)

        def retired_sides(*args: Any, **kwargs: Any) -> None:
            retire()
            raise ReadError(reason)

        def retired_render(*args: Any, **kwargs: Any) -> RenderState:
            retire()
            return "unsupported"

        if reason == "render":
            monkeypatch.setattr(service_module, "capture_diff", retired_render)
        else:
            monkeypatch.setattr(RepositoryReader, "sides", retired_sides)
        with pytest.raises(ChangesError, match="^stale_snapshot$"):
            await service.file_diff(snap, 0, "file", 0)
        assert not has_pages()
        accounted(service)

    asyncio.run(run())


@pytest.mark.parametrize("scope", ["working", "branch"])
@pytest.mark.parametrize(
    "reason,render",
    [
        ("unsupported", "withheld"),
        ("invalid_encoding", "invalid_encoding"),
        ("too_large", "too_large"),
    ],
)
def test_legacy_render_only_completion_keeps_closed_cache_behavior(
    tmp_path: Path,
    service: ChangesService,
    monkeypatch: pytest.MonkeyPatch,
    scope: service_module.Scope,
    reason: str,
    render: str,
) -> None:
    repo = repository(tmp_path / "repo")
    git(repo, "branch", "base")
    save(repo, "file.txt", b"content\n")
    if scope == "working":
        (repo / "file.txt").write_text("working\n")

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/base")
        snap = await service.snapshot("account", "host", key, scope)

        def retire(*args: Any, **kwargs: Any) -> None:
            CacheControl(service).close()
            raise ReadError(reason)

        monkeypatch.setattr(RepositoryReader, "sides", retire)
        diff, count = await service.file_diff(snap, 0, "file", 0)
        assert diff.render == render and not diff.hunks and count == 1
        assert CacheControl(service).bytes == 0

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["raw", "sides", "patch"])
def test_commit_unsupported_side_policy_matches_branch_capture(
    tmp_path: Path, service: ChangesService, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    repo = repository(tmp_path / "repo")
    oid = save(repo, "file.txt", b"content\n")

    async def run() -> None:
        snap = await selection(service, repo, oid)

        def unsupported(*args: Any, **kwargs: Any) -> None:
            raise ReadError("unsupported")

        monkeypatch.setattr(RepositoryReader, stage, unsupported)
        if stage == "sides":
            diff, _ = await service.file_diff(snap, 0, "file", 0)
            assert diff.render == "withheld" and not diff.hunks
        else:
            with pytest.raises(ChangesError, match="^unavailable$"):
                await service.file_diff(snap, 0, "file", 0)

    asyncio.run(run())


def test_commit_revision_separates_manifest_caps_and_policy_versions(
    tmp_path: Path, service: ChangesService, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = repository(tmp_path / "repo")
    for i in range(4):
        (repo / f"file-{i}.txt").write_text("content\n")
    git(repo, "add", ".")
    oid = commit(repo)

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/main")
        page = (await service.graph("account", "host", key, "all")).page

        async def open_commit() -> Snapshot:
            return await service.commit_snapshot("account", "host", key, page.snapshot, "all", oid)

        monkeypatch.setattr(service_module, "MAX_ROWS", 2)
        limited = await open_commit()
        assert limited.partial and len(limited.rows) == 2
        monkeypatch.setattr(service_module, "MAX_ROWS", 4)
        full = await open_commit()
        assert full.revision != limited.revision and len(full.rows) == 4 and not full.partial
        monkeypatch.setattr(policy_module, "POLICY_VERSION", policy_module.POLICY_VERSION + 1)
        updated = await open_commit()
        assert updated.revision != full.revision and updated.rows == full.rows
        monkeypatch.setattr(service._graphs, "_policy", SecretPolicy((b"file-0.txt",)))
        denied = await open_commit()
        assert denied.revision != updated.revision and denied.rows[0].withheld
        assert not full.rows[0].withheld
        accounted(service)

    asyncio.run(run())


def test_commit_deadline_includes_worker_queue_time(
    tmp_path: Path, service: ChangesService, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = repository(tmp_path / "repo")
    root = git(repo, "rev-parse", "HEAD").decode()
    release = threading.Event()
    started = [threading.Event(), threading.Event()]

    def occupy(index: int) -> None:
        started[index].set()
        assert release.wait(3)

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/main")
        page = (await service.graph("account", "host", key, "all")).page
        blockers = [
            asyncio.create_task(service._workers.call(("occupied", str(i)), occupy, i))
            for i in range(2)
        ]
        while not all(event.is_set() for event in started):
            await asyncio.sleep(0.005)
        monkeypatch.setattr(service_module, "GRAPH_REQUEST_S", 0.05)
        monkeypatch.setattr(
            service._graphs, "_reader", lambda *a: pytest.fail("expired queued read")
        )
        before = time.monotonic()
        try:
            with pytest.raises(ChangesError, match="busy"):
                await service.commit_snapshot("account", "host", key, page.snapshot, "all", root)
            assert time.monotonic() - before < 0.5
        finally:
            release.set()
            await asyncio.gather(*blockers)
        while service._workers.flights:
            await asyncio.sleep(0.005)

    try:
        asyncio.run(run())
    finally:
        release.set()
