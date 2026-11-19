"""Opt-in first-page measurement without writing a commit-graph acceleration file."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from relaydeck.git.base_store import BaseStore
from relaydeck.git.runner import GitRunner
from relaydeck.git.service import ChangesService
from tests.support.graph_repos import git, located, repository


@pytest.mark.skipif(
    os.environ.get("RUN_GRAPH_BENCHMARK") != "1", reason="opt-in history measurement"
)
def test_first_page_of_large_history(tmp_path: Path) -> None:
    repo = repository(tmp_path / "repo", 100_000)
    base = git(repo, "rev-list", "--max-parents=0", "HEAD").decode()
    git(repo, "branch", "base", base)
    service = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))

    async def measure() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/base")
        started = time.monotonic()
        try:
            result = await service.graph("account", "host", key, "all")
        finally:
            print(f"first_page_seconds={time.monotonic() - started:.3f}")
        assert len(result.page.commits) == 100 and result.page.ahead == 99_999
        assert not (repo / ".git/objects/info/commit-graph").exists()

    try:
        asyncio.run(measure())
    finally:
        service.close()
