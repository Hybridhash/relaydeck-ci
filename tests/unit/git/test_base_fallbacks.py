"""The default base and its fallbacks: the graph's cheap check and its full read always agree."""

from __future__ import annotations

import asyncio
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from relaydeck.git.base_store import BaseStore
from relaydeck.git.graph_probe import probe
from relaydeck.git.results import ChangesError
from relaydeck.git.runner import GitRunner
from relaydeck.git.service import ChangesService
from tests.support.git_identity import AUTHOR_CONFIG

_ID = AUTHOR_CONFIG


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *_ID, *args], check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "a.txt").write_text("one\n")
    git(path, "add", "a.txt")
    git(path, "commit", "-qm", "base")
    git(path, "checkout", "-qb", "feature")
    (path / "a.txt").write_text("two\n")
    git(path, "commit", "-qam", "step")
    return path


@pytest.fixture
def service(tmp_path: Path) -> Iterator[ChangesService]:
    svc = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))
    yield svc
    svc.close()


async def worktree(service: ChangesService, repo: Path) -> str:
    for _ in range(200):
        (summary,) = await service.summaries("acct", "h1", {"t1": str(repo)})
        if summary.state != "calculating":
            return summary.worktree_id
        await asyncio.sleep(0.02)
    raise AssertionError("never settled")


def effective(service: ChangesService, key: str) -> str:
    """The base the full read chooses, after checking the cheap probe hashes the same inputs."""
    graphs = service._graphs
    found = service._cache.worktree(key).worktree
    saved = graphs._bases.get("acct", "h1", found)
    deadline = time.monotonic() + 10
    reader = graphs._reader(found, deadline)
    ref, choice = graphs._choice("acct", "h1", found, reader)
    capture = reader.capture(ref, choice)
    cheap = probe(
        service._runner,
        found,
        deadline,
        saved[0] if saved else None,
        saved[1] if saved else "default",
        None,
    )
    assert cheap.digest == capture.probe.digest
    return ref


def test_every_fallback_keeps_the_cheap_check_and_the_full_read_in_step(
    service: ChangesService, repo: Path
) -> None:
    async def run() -> None:
        key = await worktree(service, repo)
        assert effective(service, key) == "refs/heads/main"  # no remote: the local trunk
        git(repo, "branch", "release", "main")
        await service.set_base("acct", "h1", key, "refs/heads/release")
        assert effective(service, key) == "refs/heads/release"
        git(repo, "branch", "-D", "release")
        assert effective(service, key) == "refs/heads/main"  # a deleted pin falls back
        await service.set_base("acct", "h1", key, "")
        assert effective(service, key) == "refs/heads/main"
        git(repo, "checkout", "-q", "--detach")
        assert effective(service, key) == "refs/heads/main"
        git(repo, "checkout", "-q", "feature")
        main = git(repo, "rev-parse", "main")
        git(repo, "update-ref", "refs/remotes/origin/main", main)  # fetched, no origin/HEAD
        assert effective(service, key) == "refs/remotes/origin/main"  # not the local copy
        git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        assert effective(service, key) == "refs/remotes/origin/main"

    asyncio.run(run())


def test_a_trunk_is_not_compared_with_a_leftover_sibling_trunk(
    service: ChangesService, repo: Path
) -> None:
    async def run() -> None:
        git(repo, "branch", "master", "main")
        git(repo, "checkout", "-q", "main")
        key = await worktree(service, repo)
        with pytest.raises(ChangesError, match="no_base"):
            await service.snapshot("acct", "h1", key, "branch")
        with pytest.raises(ChangesError, match="no_base"):
            await service.graph("acct", "h1", key, "branch")

    asyncio.run(run())


def test_unpinning_and_a_deleted_pin_give_the_graph_a_new_revision(
    service: ChangesService, repo: Path
) -> None:
    async def run() -> None:
        key = await worktree(service, repo)
        await service.set_base("acct", "h1", key, "refs/heads/main")  # pinned to the default
        pinned = (await service.graph("acct", "h1", key, "branch")).page.snapshot
        await service.set_base("acct", "h1", key, "")
        following = (await service.graph("acct", "h1", key, "branch")).page
        assert following.base_name == "main" and following.snapshot != pinned
        git(repo, "branch", "release", "main")
        await service.set_base("acct", "h1", key, "refs/heads/release")
        first = (await service.graph("acct", "h1", key, "branch")).page.snapshot
        git(repo, "branch", "-D", "release")
        fallback = (await service.graph("acct", "h1", key, "branch")).page
        assert fallback.base_name == "main" and fallback.snapshot != first
        git(repo, "branch", "release", "main")
        back = (await service.graph("acct", "h1", key, "branch")).page
        assert back.base_name == "release"

    asyncio.run(run())
