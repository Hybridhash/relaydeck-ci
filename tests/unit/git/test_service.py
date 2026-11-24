"""ChangesService end to end on synthetic repositories."""

from __future__ import annotations

import asyncio
import subprocess
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from relaydeck.contracts.git import WorktreeSummary
from relaydeck.git import cache as cache_module
from relaydeck.git import content as content_module
from relaydeck.git import flights as flights_module
from relaydeck.git import service as service_module
from relaydeck.git.base_store import BaseStore
from relaydeck.git.parser import DiffParser, page_size
from relaydeck.git.runner import GitRunner
from relaydeck.git.service import ChangesError, ChangesService
from tests.support.git_identity import AUTHOR_CONFIG
from tests.support.git_pem import PEM_PRIVATE_HEADER

_ID = AUTHOR_CONFIG


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *_ID, *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "a.txt").write_text("one\ntwo\n")
    git(path, "add", "a.txt")
    git(path, "commit", "-qm", "base")
    return path


@pytest.fixture
def service(tmp_path: Path) -> ChangesService:
    svc = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))
    yield svc
    svc.close()


async def settled(svc: ChangesService, folders: dict[str, str | None]) -> list[WorktreeSummary]:
    for _ in range(200):
        out = await svc.summaries("acct", "h1", folders)
        if all(s.state != "calculating" for s in out) and not svc._workers.flights:
            return await svc.summaries("acct", "h1", folders)
        await asyncio.sleep(0.02)
    raise AssertionError("summaries never settled")


def assert_cache_accounted(service: ChangesService) -> None:
    with service._cache.lock:
        manifests = sum(
            cache_module.manifest_bytes(snap) for snap in service._cache.snapshots.values()
        )
        pages = sum(
            page_size(page)
            for known in service._cache.known.values()
            for group in known.pages.values()
            for page in group
        )
        assert service._cache.bytes == manifests + pages


def test_worktree_churn_retires_snapshots_and_debits_pages_once(
    service: ChangesService, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cache_module, "MAX_WORKTREES", 2)
    monkeypatch.setattr(cache_module, "MAX_CACHE_BYTES", 15_000)

    async def run() -> None:
        previous = []
        for index in range(6):
            folder = tmp_path / f"checkout-{index}"
            git(repo, "worktree", "add", "--detach", str(folder))
            (folder / "a.txt").write_text("synthetic " + "x" * 1000 + "\n")
            (summary,) = await settled(service, {"t1": str(folder)})
            key = summary.worktree_id
            snap = await service.snapshot("acct", "h1", key, "working")
            page, _ = await service.file_diff(snap, 0, "file", 0)
            assert page.hunks
            previous.append(snap)
            assert_cache_accounted(service)
            assert len(service._cache.known) <= 2
            for old in previous[:-2]:
                with pytest.raises(ChangesError, match="stale_snapshot"):
                    service.cached(old.account, old.host, old.worktree, old.scope, old.revision)
        known = service._cache.known[key]
        different = replace(known.worktree, identity=(*known.worktree.identity, 999))
        monkeypatch.setattr(service._locator, "locate", lambda *args: different)
        monkeypatch.setattr(service._bases, "worktree_key", lambda worktree: key)
        service._locate(str(folder))
        assert not service._cache.known[key].pages
        with pytest.raises(ChangesError, match="stale_snapshot"):
            service.cached(snap.account, snap.host, key, snap.scope, snap.revision)
        assert_cache_accounted(service)

    asyncio.run(run())


def test_competing_diff_fills_charge_only_the_retained_pages(
    service: ChangesService, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = threading.Barrier(2)
    original = DiffParser.parse

    def parse(self: DiffParser, *args: object, **kwargs: object) -> object:
        pages = original(self, *args, **kwargs)
        gate.wait(timeout=5)
        return pages

    monkeypatch.setattr(DiffParser, "parse", parse)

    async def run() -> None:
        (repo / "a.txt").write_text("different\n")
        (summary,) = await settled(service, {"t1": str(repo)})
        snap = await service.snapshot("acct", "h1", summary.worktree_id, "working")
        first, second = await asyncio.gather(
            asyncio.to_thread(service._pages, snap, 0, "first"),
            asyncio.to_thread(service._pages, snap, 0, "second"),
        )
        assert first is second
        assert_cache_accounted(service)

    asyncio.run(run())


def test_a_diff_fill_cannot_publish_after_its_snapshot_is_retired(
    service: ChangesService, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = DiffParser.parse

    def parse(self: DiffParser, *args: object, **kwargs: object) -> object:
        pages = original(self, *args, **kwargs)
        with service._cache.lock:
            for key in list(service._cache.snapshots):
                service._cache._drop_snapshot(key)
        return pages

    monkeypatch.setattr(DiffParser, "parse", parse)

    async def run() -> None:
        (repo / "a.txt").write_text("different\n")
        (summary,) = await settled(service, {"t1": str(repo)})
        snap = await service.snapshot("acct", "h1", summary.worktree_id, "working")
        with pytest.raises(ChangesError, match="stale_snapshot"):
            await service.file_diff(snap, 0, "file", 0)
        assert not service._cache.known[snap.worktree].pages
        assert_cache_accounted(service)

    asyncio.run(run())


def test_unavailable_complete_side_withholds_the_diff(
    service: ChangesService, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from relaydeck.git.reader import ReadError, RepositoryReader

    def fail(*args: object, **kwargs: object) -> None:
        raise ReadError("unsupported")

    monkeypatch.setattr(RepositoryReader, "sides", fail)

    async def run() -> None:
        (repo / "a.txt").write_text("different\n")
        (summary,) = await settled(service, {"t1": str(repo)})
        snap = await service.snapshot("acct", "h1", summary.worktree_id, "working")
        page, _ = await service.file_diff(snap, 0, "file", 0)
        assert page.render == "withheld" and not page.hunks

    asyncio.run(run())


@pytest.mark.parametrize("scope", ["working", "branch"])
@pytest.mark.parametrize("shape", ["interior", "separated", "missing_end"])
def test_private_key_interiors_are_masked_from_complete_sides(
    service: ChangesService, repo: Path, scope: str, shape: str
) -> None:
    lines = ["public header", PEM_PRIVATE_HEADER]
    lines.extend(f"FAKE-NOT-A-KEY-{i:04d}" for i in range(40))
    if shape != "missing_end":
        lines.append("-----END PRIVATE KEY-----")
    lines.append("public footer")
    target = repo / "config.txt"
    target.write_text("\n".join(lines) + "\n")
    git(repo, "add", "config.txt")
    git(repo, "commit", "-qm", "synthetic key-shaped text")
    git(repo, "branch", "comparison")
    lines[16] = "FAKE-NOT-A-KEY-REPLACED"
    if shape == "separated":
        lines[32] = "FAKE-NOT-A-KEY-SECOND"
    target.write_text("\n".join(lines) + "\n")
    if scope == "branch":
        git(repo, "add", "config.txt")
        git(repo, "commit", "-qm", "synthetic interior edit")

    async def run() -> None:
        (summary,) = await settled(service, {"t1": str(repo)})
        key = summary.worktree_id
        assert key is not None
        if scope == "branch":
            await service.set_base("acct", "h1", key, "refs/heads/comparison")
        snap = await service.snapshot("acct", "h1", key, scope)
        row = next(i for i, entry in enumerate(snap.rows) if entry.path == b"config.txt")
        page, count = await service.file_diff(snap, row, "file", 0)
        assert count == 1
        if shape == "missing_end":
            assert page.render == "withheld" and not page.hunks
        else:
            assert page.render == "ok" and page.hunks
            if shape == "separated":
                assert len(page.hunks) == 2
            for hunk in page.hunks:
                for line in hunk.lines:
                    assert line.masked and not line.marks
                    assert "FAKE" not in line.text
                    assert "BEGIN" not in line.text and "END" not in line.text

    asyncio.run(run())


def test_first_summary_is_calculating_then_counts(service: ChangesService, repo: Path) -> None:
    async def run() -> None:
        (repo / "a.txt").write_text("one\nTWO\nthree\n")
        (repo / "u.txt").write_text("u\n")
        first = await service.summaries("acct", "h1", {"t1": str(repo)})
        assert first[0].state == "calculating"
        (summary,) = await settled(service, {"t1": str(repo)})
        assert summary.state == "ok" and summary.branch == "main" and not summary.detached
        working = summary.working
        assert (working.files, working.unstaged, working.untracked) == (2, 1, 1)
        assert (working.additions, working.deletions) == (2, 1)
        assert summary.base_name is None and summary.branch_scope.commits is None

    asyncio.run(run())


def test_shared_checkout_and_missing_or_plain_folders(
    service: ChangesService, repo: Path, tmp_path: Path
) -> None:
    async def run() -> None:
        plain = tmp_path / "plain"
        plain.mkdir()
        folders = {"t1": str(repo), "t2": str(repo / "."), "t3": None, "t4": str(plain)}
        out = {s.agent_id: s for s in await settled(service, folders)}
        assert out["t1"].shared and out["t2"].shared
        assert out["t1"].worktree_id == out["t2"].worktree_id
        assert out["t3"].state == "no_repo" and out["t4"].state == "no_repo"

    asyncio.run(run())


def test_unsupported_config_is_a_state(service: ChangesService, repo: Path) -> None:
    async def run() -> None:
        git(repo, "config", "filter.x.clean", "helper")
        (summary,) = await settled(service, {"t1": str(repo)})
        assert summary.state == "unsupported_config"

    asyncio.run(run())


def test_working_snapshot_rows_and_diff(service: ChangesService, repo: Path) -> None:
    async def run() -> None:
        (repo / "a.txt").write_text("one\nTWO\n")
        (repo / ".env").write_text("TOKEN=x\n")
        (summary,) = await settled(service, {"t1": str(repo)})
        snap = await service.snapshot("acct", "h1", summary.worktree_id, "working")
        paths = [row.path for row in snap.rows]
        assert paths == [b".env", b"a.txt"]
        env, a = snap.rows
        assert env.withheld and env.reason == "withheld" and env.untracked
        withheld, _ = await service.file_diff(snap, 0, "f0", 0)
        assert withheld.render == "withheld" and not withheld.hunks
        page, total = await service.file_diff(snap, 1, "f1", 0)
        assert total == 1 and page.render == "ok"
        texts = [(line.kind, line.text) for line in page.hunks[0].lines]
        assert ("delete", "two") in texts and ("add", "TWO") in texts

    asyncio.run(run())


def test_staged_change_reverted_in_the_worktree_is_listed_with_no_net_diff(
    service: ChangesService, repo: Path
) -> None:
    async def run() -> None:
        (repo / "a.txt").write_text("changed\n")
        git(repo, "add", "a.txt")
        (repo / "a.txt").write_text("one\ntwo\n")
        (summary,) = await settled(service, {"t1": str(repo)})
        assert summary.working.files == 1 and summary.working.additions == 0
        snap = await service.snapshot("acct", "h1", summary.worktree_id, "working")
        assert snap.rows[0].reason == "no_net_diff"

    asyncio.run(run())


def test_edit_after_the_snapshot_is_stale(service: ChangesService, repo: Path) -> None:
    async def run() -> None:
        (repo / "a.txt").write_text("one\nTWO\n")
        (summary,) = await settled(service, {"t1": str(repo)})
        snap = await service.snapshot("acct", "h1", summary.worktree_id, "working")
        (repo / "a.txt").write_text("one\nSIX\n")
        with pytest.raises(ChangesError, match="stale_snapshot"):
            await service.file_diff(snap, 0, "f0", 0)

    asyncio.run(run())


def test_large_file_needs_an_explicit_load(
    service: ChangesService, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "LOAD_REQUIRED_ROWS", 3)

    async def run() -> None:
        (repo / "a.txt").write_text("".join(f"line {n}\n" for n in range(10)))
        (summary,) = await settled(service, {"t1": str(repo)})
        snap = await service.snapshot("acct", "h1", summary.worktree_id, "working")
        gated, _ = await service.file_diff(snap, 0, "f0", 0)
        loaded, _ = await service.file_diff(snap, 0, "f0", 0, load=True)
        assert gated.render == "load_required" and loaded.render == "ok"

    asyncio.run(run())


def test_branch_scope_needs_a_base_then_uses_remote_default_or_saved_choice(
    service: ChangesService, repo: Path
) -> None:
    async def run() -> None:
        git(repo, "branch", "-m", "main", "stable")  # no conventional local trunk
        git(repo, "checkout", "-qb", "feature")
        for n in range(2):
            (repo / "a.txt").write_text(f"v{n}\n")
            git(repo, "commit", "-qam", f"step {n}")
        (summary,) = await settled(service, {"t1": str(repo)})
        key = summary.worktree_id
        with pytest.raises(ChangesError, match="no_base"):
            await service.snapshot("acct", "h1", key, "branch")
        main = git(repo, "rev-parse", "stable")
        git(repo, "update-ref", "refs/remotes/origin/main", main)
        git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        snap = await service.snapshot("acct", "h1", key, "branch")
        assert snap.base_name == "origin/main" and snap.commits == 2 and len(snap.rows) == 1
        commits = await service.commits(snap, 0, 10)
        assert [c.subject for c in commits] == ["step 1", "step 0"]
        bases = await service.bases("acct", "h1", key)
        assert bases[0][:2] == ("refs/remotes/origin/main", "remote_default")
        assert "refs/heads/feature" not in [ref for ref, _, _ in bases]
        await service.set_base("acct", "h1", key, "refs/heads/stable")
        mine = await service.snapshot("acct", "h1", key, "branch")
        assert mine.base_name == "stable" and mine.commits == 2

    asyncio.run(run())


def test_without_a_remote_default_a_local_trunk_is_the_base(
    service: ChangesService, repo: Path
) -> None:
    async def run() -> None:
        git(repo, "checkout", "-qb", "feature")
        (repo / "a.txt").write_text("changed\n")
        git(repo, "commit", "-qam", "step")
        (summary,) = await settled(service, {"t1": str(repo)})
        snap = await service.snapshot("acct", "h1", summary.worktree_id, "branch")
        assert snap.base_name == "main" and snap.commits == 1
        git(repo, "checkout", "-q", "main")  # the trunk itself has nothing to compare with
        with pytest.raises(ChangesError, match="no_base"):
            await service.snapshot("acct", "h1", summary.worktree_id, "branch")

    asyncio.run(run())


def test_a_deleted_saved_base_falls_back_and_the_default_can_be_chosen_again(
    service: ChangesService, repo: Path
) -> None:
    async def run() -> None:
        git(repo, "branch", "release")
        git(repo, "checkout", "-qb", "feature")
        (repo / "a.txt").write_text("changed\n")
        git(repo, "commit", "-qam", "step")
        (summary,) = await settled(service, {"t1": str(repo)})
        key = summary.worktree_id
        await service.set_base("acct", "h1", key, "refs/heads/release")
        assert (await service.snapshot("acct", "h1", key, "branch")).base_name == "release"
        git(repo, "branch", "-D", "release")
        fallback = await service.snapshot("acct", "h1", key, "branch")
        assert fallback.base_name == "main" and fallback.commits == 1
        graph = await service.graph("acct", "h1", key, "branch")
        assert graph.page.base_name == "main"
        git(repo, "branch", "release", "main")
        assert (await service.snapshot("acct", "h1", key, "branch")).base_name == "release"
        await service.set_base("acct", "h1", key, "")  # follow the default again
        assert (await service.snapshot("acct", "h1", key, "branch")).base_name == "main"

    asyncio.run(run())


def test_unknown_worktree_and_closed_service(service: ChangesService) -> None:
    async def run() -> None:
        with pytest.raises(ChangesError, match="not_found"):
            await service.snapshot("acct", "h1", "nope", "working")
        service.close()
        with pytest.raises(ChangesError, match="unavailable"):
            await service.summaries("acct", "h1", {})

    asyncio.run(run())


def test_branch_rows_classify_every_kind_of_change(service: ChangesService, repo: Path) -> None:
    async def run() -> None:
        (repo / "gone.txt").write_text("x\n")
        (repo / "old-name.txt").write_text("".join(f"line {n}\n" for n in range(20)))
        (repo / "tool.sh").write_text("echo\n")
        (repo / "data.bin").write_bytes(b"\0\1")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "more")
        main = git(repo, "rev-parse", "HEAD")
        git(repo, "update-ref", "refs/remotes/origin/main", main)
        git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        git(repo, "checkout", "-qb", "feature")
        (repo / "new.txt").write_text("new\n")
        (repo / "gone.txt").unlink()
        git(repo, "mv", "old-name.txt", "new-name.txt")
        (repo / "tool.sh").chmod(0o755)
        (repo / "link").symlink_to("a.txt")
        (repo / "data.bin").write_bytes(b"\0\2")
        sub = repo.parent / "sub"
        subprocess.run(["git", "init", "-q", str(sub)], check=True)
        git(sub, "commit", "-q", "--allow-empty", "-m", "s")
        git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "vendor")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "changes")
        (summary,) = await settled(service, {"t1": str(repo)})
        snap = await service.snapshot("acct", "h1", summary.worktree_id, "branch")
        kinds = {row.path: (row.status, row.reason) for row in snap.rows}
        assert kinds[b"new.txt"] == ("A", None)
        assert kinds[b"gone.txt"] == ("D", None)
        assert kinds[b"new-name.txt"] == ("R", None)
        assert kinds[b"tool.sh"] == ("mode", "mode")
        assert kinds[b"link"] == ("symlink", "symlink")
        assert kinds[b"vendor"] == ("submodule", "submodule")
        assert kinds[b"data.bin"] == ("binary", "binary")
        symlink_row = [row.path for row in snap.rows].index(b"link")
        page, _ = await service.file_diff(snap, symlink_row, "f", 0)
        assert page.render == "unsupported" and not page.hunks

    asyncio.run(run())


def test_saving_a_base_retires_old_branch_snapshots(service: ChangesService, repo: Path) -> None:
    async def run() -> None:
        main = git(repo, "rev-parse", "HEAD")
        git(repo, "update-ref", "refs/remotes/origin/main", main)
        git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        git(repo, "checkout", "-qb", "feature")
        (repo / "a.txt").write_text("v\n")
        git(repo, "commit", "-qam", "step")
        (summary,) = await settled(service, {"t1": str(repo)})
        key = summary.worktree_id
        old = await service.snapshot("acct", "h1", key, "branch")
        await service.set_base("acct", "h1", key, "refs/remotes/origin/main")
        with pytest.raises(ChangesError, match="stale_snapshot"):
            service.cached("acct", "h1", key, "branch", old.revision)
        new = await service.snapshot("acct", "h1", key, "branch")
        assert new.revision != old.revision

    asyncio.run(run())


def test_a_git_failure_becomes_a_state_not_endless_calculating(
    service: ChangesService, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from relaydeck.git.runner import GitFailed

    def broken(*_args: object) -> object:
        raise GitFailed("git_timeout")

    monkeypatch.setattr(service, "_snapshot_now", broken)

    async def run() -> None:
        (summary,) = await settled(service, {"t1": str(repo)})
        assert summary.state == "unavailable"

    asyncio.run(run())


def test_the_first_call_answers_without_waiting_for_git(
    service: ChangesService, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    gate = threading.Event()
    real = service._locator.locate

    def slow(folder: str, cancel: object = None) -> object:
        gate.wait(5)
        return real(folder, cancel)  # type: ignore[arg-type]

    monkeypatch.setattr(service._locator, "locate", slow)

    async def run() -> None:
        (first,) = await asyncio.wait_for(service.summaries("acct", "h1", {"t1": str(repo)}), 1)
        assert first.state == "calculating"
        gate.set()
        (summary,) = await settled(service, {"t1": str(repo)})
        assert summary.state == "ok"

    asyncio.run(run())


def test_untracked_lines_are_counted_and_binary_untracked_is_marked(
    service: ChangesService, repo: Path
) -> None:
    async def run() -> None:
        (repo / "notes.txt").write_text("a\nb\nc")
        (repo / "blob.dat").write_bytes(b"\0\1\2")
        (summary,) = await settled(service, {"t1": str(repo)})
        assert summary.working.additions == 0  # the card counts tracked lines only
        snap = await service.snapshot("acct", "h1", summary.worktree_id, "working")
        rows = {row.path: row for row in snap.rows}
        assert rows[b"notes.txt"].additions == 3
        assert rows[b"blob.dat"].status == "binary"

    asyncio.run(run())


def test_a_side_above_the_limit_is_too_large(
    service: ChangesService, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(content_module, "MAX_SIDE_BYTES", 10)

    async def run() -> None:
        (repo / "a.txt").write_text("one\ntwo\nthree\nfour\n")
        (summary,) = await settled(service, {"t1": str(repo)})
        snap = await service.snapshot("acct", "h1", summary.worktree_id, "working")
        page, _ = await service.file_diff(snap, 0, "f0", 0)
        assert page.render == "too_large"

    asyncio.run(run())


def test_an_edit_while_the_patch_is_read_is_stale_and_not_cached(
    service: ChangesService, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from relaydeck.git.reader import RepositoryReader

    real = RepositoryReader.patch

    def racing(self: RepositoryReader, *args: object) -> bytes:
        out = real(self, *args)  # type: ignore[arg-type]
        (repo / "a.txt").write_text("one\nRACED\n")
        return out

    async def run() -> None:
        (repo / "a.txt").write_text("one\nTWO\n")
        (summary,) = await settled(service, {"t1": str(repo)})
        snap = await service.snapshot("acct", "h1", summary.worktree_id, "working")
        monkeypatch.setattr(RepositoryReader, "patch", racing)
        with pytest.raises(ChangesError, match="stale_snapshot"):
            await service.file_diff(snap, 0, "f0", 0)
        assert not service._cache.known[summary.worktree_id].pages

    asyncio.run(run())


def test_joining_callers_count_against_the_waiter_limit(
    service: ChangesService, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flights_module, "MAX_WAITING", 2)

    async def run() -> None:
        (summary,) = await settled(service, {"t1": str(repo)})
        key = summary.worktree_id
        calls = [service.snapshot("acct", "h1", key, "working") for _ in range(4)]
        results = await asyncio.gather(*calls, return_exceptions=True)
        busy = [r for r in results if isinstance(r, ChangesError) and r.code == "busy"]
        assert busy and len(busy) < 4

    asyncio.run(run())


def test_the_last_waiter_leaving_stops_the_shared_job(
    service: ChangesService, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    stopped = threading.Event()

    def waiting(*_args: object) -> object:
        if service._workers.stop_event().wait(5):
            stopped.set()
        raise ChangesError("unavailable")

    async def run() -> None:
        (summary,) = await settled(service, {"t1": str(repo)})
        monkeypatch.setattr(service, "_snapshot_now", waiting)
        task = asyncio.create_task(service.snapshot("acct", "h1", summary.worktree_id, "working"))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.get_running_loop().run_in_executor(None, stopped.wait, 2)

    asyncio.run(run())


def test_manifests_count_against_the_cache_budget(
    service: ChangesService, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        for n in range(30):
            (repo / f"f{n}.txt").write_text("x\n")
        (summary,) = await settled(service, {"t1": str(repo)})
        snap = await service.snapshot("acct", "h1", summary.worktree_id, "working")
        assert service._cache.bytes >= cache_module.manifest_bytes(snap)
        monkeypatch.setattr(cache_module, "MAX_CACHE_BYTES", 1)
        (repo / "f0.txt").write_text("y\n")
        await service.snapshot("acct", "h1", summary.worktree_id, "working")
        assert not service._cache.snapshots and service._cache.bytes == 0

    asyncio.run(run())


def test_the_manifest_stops_at_the_row_limit(
    service: ChangesService, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "MAX_ROWS", 5)

    async def run() -> None:
        for n in range(12):
            (repo / f"f{n:02d}.txt").write_text("x\n")
        (summary,) = await settled(service, {"t1": str(repo)})
        snap = await service.snapshot("acct", "h1", summary.worktree_id, "working")
        assert len(snap.rows) == 5 and snap.partial

    asyncio.run(run())


def test_the_summary_branch_revision_moves_with_the_base(
    service: ChangesService, repo: Path
) -> None:
    async def run() -> None:
        main = git(repo, "rev-parse", "HEAD")
        git(repo, "update-ref", "refs/remotes/origin/main", main)
        git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        git(repo, "checkout", "-qb", "feature")
        (repo / "a.txt").write_text("v\n")
        git(repo, "commit", "-qam", "step")
        (first,) = await settled(service, {"t1": str(repo)})
        assert first.branch_scope.revision
        await service.set_base("acct", "h1", first.worktree_id, "refs/remotes/origin/main")
        known = service._cache.known[first.worktree_id]
        known.checked = 0.0  # due for a refresh
        await service.summaries("acct", "h1", {"t1": str(repo)})
        (second,) = await settled(service, {"t1": str(repo)})
        assert second.revision == first.revision  # the working tree did not move
        assert second.branch_scope.revision != first.branch_scope.revision

    asyncio.run(run())


def test_finished_calls_leave_no_bookkeeping_behind(
    service: ChangesService, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        (summary,) = await settled(service, {"t1": str(repo)})
        key = summary.worktree_id
        await asyncio.gather(*(service.snapshot("acct", "h1", key, "working") for _ in range(3)))
        for n in range(5):
            (repo / "a.txt").write_text(f"v{n}\n")
            await service.snapshot("acct", "h1", key, "working")

        def failing(*_args: object) -> object:
            raise ChangesError("unavailable")

        monkeypatch.setattr(service, "_snapshot_now", failing)
        with pytest.raises(ChangesError):
            await service.snapshot("acct", "h1", key, "working")
        await asyncio.sleep(0.05)
        assert (
            not service._workers._subscribers
            and not service._workers.flights
            and not service._workers._flight_cancel
        )

    asyncio.run(run())
