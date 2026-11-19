"""Real Git comparisons pin every input that can change cached rows."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from relaydeck.git.base_store import BaseStore
from relaydeck.git.runner import GitRunner
from relaydeck.git.service import ChangesError, ChangesService
from relaydeck.host.executables import trusted_executable
from tests.support.graph_repos import git, located, repository


def runner(home: Path) -> GitRunner:
    executable = trusted_executable("git")
    assert executable is not None
    return GitRunner(executable, environ={"HOME": str(home)})


@pytest.mark.parametrize(
    "source", ["exclude", "nested", "default", "linked_exclude", "linked_default", "common_exclude"]
)
@pytest.mark.parametrize("initially_ignored", [False, True])
def test_ignore_edit_warm_snapshot_matches_a_cold_capture(
    tmp_path: Path, source: str, initially_ignored: bool
) -> None:
    repo = repository(tmp_path / "repo")
    common = repo / ".git/info/exclude"
    if source == "common_exclude":
        linked = tmp_path / "linked"
        git(repo, "worktree", "add", "--detach", str(linked))
        repo = linked
    home = tmp_path / "home"
    (home / ".config/git").mkdir(parents=True)
    (repo / "nested").mkdir()
    (repo / "nested/visible.tmp").write_text("untracked\n")
    if source in ("default", "linked_default"):
        rules = home / ".config/git/ignore"
        pattern = "visible.tmp\n"
        if source == "linked_default":
            target = tmp_path / "default-ignore"
            rules.symlink_to(target)
            rules = target
    elif source == "nested":
        rules = repo / "nested/.gitignore"
        pattern = "visible.tmp\n"
    else:
        rules = common
        pattern = "nested/visible.tmp\n"
        if source == "linked_exclude":
            target = tmp_path / "exclude"
            rules.unlink()
            rules.symlink_to(target)
            rules = target
    rules.write_text(pattern if initially_ignored else "# empty\n")
    bases = BaseStore(str(tmp_path / "state"))
    warm = ChangesService(runner(home), bases)
    cold = ChangesService(runner(home), bases)

    async def scenario() -> None:
        key = await located(warm, repo)
        first = await warm.snapshot("account", "host", key, "working")
        path = b"nested/visible.tmp"
        assert (path in {row.path for row in first.rows}) is not initially_ignored
        unchanged = await warm.snapshot("account", "host", key, "working")
        assert unchanged.rows == first.rows and unchanged.revision == first.revision
        if source in ("linked_exclude", "linked_default"):
            assert unchanged is not first  # Uncertain input must take the guarded read.
        else:
            assert unchanged is first
        rules.write_text("# empty\n" if initially_ignored else pattern)
        current = await warm.snapshot("account", "host", key, "working")
        cold_key = await located(cold, repo)
        fresh = await cold.snapshot("account", "host", cold_key, "working")
        assert current.rows == fresh.rows
        assert (path in {row.path for row in current.rows}) is initially_ignored
        assert current.revision != first.revision

    try:
        asyncio.run(scenario())
    finally:
        warm.close()
        cold.close()


@pytest.mark.parametrize("linked_config", [False, True])
def test_branch_attributes_edit_recomputes_rows_revision_and_diff(
    tmp_path: Path, linked_config: bool
) -> None:
    repo = repository(tmp_path / "repo")
    (repo / "value.txt").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Base")
    git(repo, "branch", "base")
    (repo / "value.txt").write_text("changed\nline\n")
    git(repo, "commit", "-qam", "Change")
    if linked_config:
        config = repo / ".git/config"
        target = tmp_path / "config"
        target.write_bytes(config.read_bytes())
        config.unlink()
        config.symlink_to(target)
    bases = BaseStore(str(tmp_path / "state"))
    warm = ChangesService(GitRunner.create(), bases)
    cold = ChangesService(GitRunner.create(), bases)

    async def scenario() -> None:
        key = await located(warm, repo)
        await warm.set_base("account", "host", key, "refs/heads/base")
        before = await warm.snapshot("account", "host", key, "branch")
        assert before.rows[0].additions == 2 and before.rows[0].deletions == 1
        (repo / ".git/info/attributes").write_text("*.txt -diff\n")
        after = await warm.snapshot("account", "host", key, "branch")
        cold_key = await located(cold, repo)
        fresh = await cold.snapshot("account", "host", cold_key, "branch")
        assert after.rows == fresh.rows
        assert after.rows[0].reason == "binary" and after.rows[0].additions is None
        assert after.revision != before.revision
        reply, _ = await warm.file_diff(after, 0, "file", 0)
        assert reply.render == "binary"
        (repo / ".git/info/attributes").write_text("# text again\n")
        restored = await warm.snapshot("account", "host", key, "branch")
        assert restored.rows == before.rows and restored.revision != after.revision
        reply, _ = await warm.file_diff(restored, 0, "file", 0)
        assert reply.hunks

    try:
        asyncio.run(scenario())
    finally:
        warm.close()
        cold.close()


def test_explicit_external_excludes_remains_a_guarded_refusal(tmp_path: Path) -> None:
    repo = repository(tmp_path / "repo")
    service = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))

    async def scenario() -> None:
        key = await located(service, repo)
        await service.snapshot("account", "host", key, "working")
        git(repo, "config", "core.excludesFile", str(tmp_path / "external-ignore"))
        with pytest.raises(ChangesError, match="unsupported_config"):
            await service.snapshot("account", "host", key, "working")

    try:
        asyncio.run(scenario())
    finally:
        service.close()


@pytest.mark.parametrize(
    "kind", ["linked_file", "linked_directory", "oversize", "fifo", "unknown_home"]
)
def test_uncertain_ignore_metadata_disables_reuse_without_reading_file_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    import os

    from relaydeck.git.locator import RepositoryLocator
    from relaydeck.git.polling import ignore_stamp

    repo = repository(tmp_path / "repo")
    home = tmp_path / "home"
    (home / ".config/git").mkdir(parents=True)
    rules = repo / ".git/info/exclude"
    outside = tmp_path / "outside"
    outside.write_text("private\n")
    if kind == "linked_file":
        rules.unlink()
        rules.symlink_to(outside)
    elif kind == "linked_directory":
        (home / ".config/git").rmdir()
        (home / ".config/git").symlink_to(tmp_path, target_is_directory=True)
    elif kind == "oversize":
        rules.write_bytes(b"#" * (256 * 1024 + 1))
    elif kind == "fifo":
        rules.unlink()
        os.mkfifo(rules)
    worktree = RepositoryLocator(runner(home)).locate(str(repo))

    def no_read(*_args: object) -> bytes:
        pytest.fail("metadata proof read file bytes")

    monkeypatch.setattr(os, "read", no_read)
    assert (
        ignore_stamp(worktree, None if kind == "unknown_home" else str(home / ".config/git/ignore"))
        is None
    )


@pytest.mark.parametrize("directory", ["gitdir", "common_dir"])
def test_sparse_checkout_patterns_are_part_of_a_linked_worktree_proof(
    tmp_path: Path, directory: str
) -> None:
    from relaydeck.git.locator import RepositoryLocator
    from relaydeck.git.polling import metadata_stamp

    main = repository(tmp_path / "repo")
    linked = tmp_path / "linked"
    git(main, "worktree", "add", "--detach", str(linked))
    native = GitRunner.create()
    worktree = RepositoryLocator(native).locate(str(linked))
    info = Path(getattr(worktree, directory)) / "info"
    info.mkdir(exist_ok=True)
    patterns = info / "sparse-checkout"
    patterns.write_text("# initial\n")
    service = ChangesService(native, BaseStore(str(tmp_path / "state")))
    service._locate(str(linked))
    key = next(iter(service._cache.known))

    async def scenario() -> None:
        first = await service.snapshot("account", "host", key, "working")
        before = metadata_stamp(worktree, default_ignore_file=native.default_ignore_file)
        patterns.write_text("/*\n")
        after = metadata_stamp(worktree, default_ignore_file=native.default_ignore_file)
        assert before is not None and after is not None and before != after
        current = await service.snapshot("account", "host", key, "working")
        assert current is not first and current.rows == first.rows

    try:
        asyncio.run(scenario())
    finally:
        service.close()
