"""Branch graph reachability, decoration and merged-base regression histories."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path

import flet as ft
import pytest

from relaydeck.client.features.changes.graph_layout import append, seed
from relaydeck.client.features.changes.graph_state import GraphState
from relaydeck.client.features.changes.state import ChangesModel
from relaydeck.client.screens.changes.parts.graph_rows import ref_badges
from relaydeck.git.base_store import BaseStore
from relaydeck.git.graph_reader import GraphReader, RefCapture
from relaydeck.git.locator import RepositoryLocator
from relaydeck.git.reader import ReadError
from relaydeck.git.results import ChangesError
from relaydeck.git.runner import GitRunner
from relaydeck.git.service import ChangesService
from tests.support.graph_repos import commit, git, located, repository


def test_missing_upstream_prefix_cannot_hide_another_tracked_upstream(tmp_path: Path) -> None:
    repo = repository(tmp_path / "repo")
    root = git(repo, "rev-parse", "HEAD").decode()
    tree = git(repo, "rev-parse", "HEAD^{tree}").decode()
    remote = git(repo, "commit-tree", tree, "-p", root, "-m", "Remote only").decode()
    git(repo, "branch", "other")
    git(repo, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    for local, upstream in (("main", "foo"), ("other", "z")):
        git(repo, "config", f"branch.{local}.remote", "origin")
        git(repo, "config", f"branch.{local}.merge", f"refs/heads/{upstream}")
    for name in ("foo/a", "foo/b", "foo/c", "foo/d"):
        git(repo, "update-ref", f"refs/remotes/origin/{name}", root)
    git(repo, "update-ref", "refs/remotes/origin/z", remote)
    service = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/main")
        first = (await service.graph("account", "host", key, "all")).page
        advanced = git(repo, "commit-tree", tree, "-p", remote, "-m", "Advanced").decode()
        git(repo, "update-ref", "refs/remotes/origin/z", advanced)
        second = (await service.graph("account", "host", key, "all")).page
        assert (
            remote in {c.oid for c in first.commits},
            first.partial,
            second.snapshot != first.snapshot,
            advanced in {c.oid for c in second.commits},
        ) == (True, False, True, True)
        assert [(r.name, r.kind) for c in first.commits if c.oid == remote for r in c.refs] == [
            ("origin/z", "upstream")
        ]

    try:
        asyncio.run(run())
    finally:
        service.close()


def test_split_label_survives_ordinary_branch_decoration(tmp_path: Path) -> None:
    repo = repository(tmp_path / "repo")
    for name in ("release", "staging", "stable", "production"):
        git(repo, "branch", name)
    git(repo, "checkout", "-qb", "feature")
    commit(repo)
    service = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/main")
        page = (await service.graph("account", "host", key, "branch")).page
        model = replace(ChangesModel(), graph=GraphState(mode="branch", page=page))
        split = page.commits[-1]
        badges = ref_badges(split, model)
        values = []
        for badge in badges.controls:
            if isinstance(badge, ft.Container):
                assert isinstance(badge.content, ft.Text)
                values.append(badge.content.value)
            else:
                assert isinstance(badge, ft.Text)
                values.append(badge.value)
        assert "split from main" in values
        assert values[:2] == ["main · base", "split from main"]

    try:
        asyncio.run(run())
    finally:
        service.close()


def test_ref_capture_skips_overlong_listed_refs_and_keeps_required_roots(tmp_path: Path) -> None:
    repo = repository(tmp_path / "repo")
    oid = git(repo, "rev-parse", "HEAD")
    long_name = b"section/" * 140 + b"tip"
    (repo / ".git/packed-refs").write_bytes(
        oid + b" refs/heads/" + long_name + b"\n" + oid + b" refs/tags/" + long_name + b"\n"
    )
    service = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/main")
        page = (await service.graph("account", "host", key, "all")).page
        assert page.partial and page.limit_reason == "ref_limit"
        assert [c.oid for c in page.commits] == [oid.decode()]
        assert [(r.name, r.kind) for r in page.commits[0].refs] == [
            ("main", "base"),
            ("main", "head"),
            ("split point", "split"),
        ]

    try:
        asyncio.run(run())
        (repo / ".git/HEAD").write_bytes(b"ref: refs/heads/" + long_name + b"\n")
        runner = GitRunner.create()
        reader = GraphReader(
            runner, RepositoryLocator(runner).locate(str(repo)), deadline=time.monotonic() + 5
        )
        with pytest.raises((ReadError, ChangesError), match="malformed|no_base"):
            reader.capture("refs/heads/main", "default")
    finally:
        service.close()


def test_fresh_cache_hit_avoids_full_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = repository(tmp_path / "repo")
    service = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))
    original = GraphReader.capture
    calls = 0

    def capture(reader: GraphReader, base: str, choice: str) -> RefCapture:
        nonlocal calls
        calls += 1
        return original(reader, base, choice)

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/main")
        first = await service.graph("account", "host", key, "all")
        monkeypatch.setattr(GraphReader, "capture", capture)
        second = await service.graph("account", "host", key, "all")
        assert second is first and calls == 0

    try:
        asyncio.run(run())
    finally:
        service.close()


@pytest.mark.parametrize("base", ["refs/heads/main", "refs/remotes/origin/main"])
def test_head_and_base_decorations_do_not_repeat_their_ref(
    tmp_path: Path,
    base: str,
) -> None:
    repo = repository(tmp_path / "repo")
    oid = git(repo, "rev-parse", "HEAD").decode()
    git(repo, "update-ref", "refs/remotes/origin/main", oid)
    git(repo, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    git(repo, "config", "branch.main.remote", "origin")
    git(repo, "config", "branch.main.merge", "refs/heads/main")
    service = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, base)
        page = (await service.graph("account", "host", key, "all")).page
        expected = [
            ("origin/main" if base.startswith("refs/remotes/") else "main", "base"),
            ("main", "head"),
            ("split point", "split"),
        ]
        if base == "refs/heads/main":
            expected.append(("origin/main", "upstream"))
        assert [(r.name, r.kind) for r in page.commits[0].refs] == expected

    try:
        asyncio.run(run())
    finally:
        service.close()


def test_merged_in_base_split_does_not_reparent_feature_commits(tmp_path: Path) -> None:
    repo = repository(tmp_path / "repo")
    root = git(repo, "rev-parse", "HEAD").decode()
    git(repo, "checkout", "-qb", "feature")
    older = commit(repo, "First feature")
    newer = commit(repo, "Second feature")
    git(repo, "checkout", "main")
    base = commit(repo, "Base update")
    git(repo, "checkout", "feature")
    git(repo, "merge", "--no-ff", "--no-edit", "main")
    head = git(repo, "rev-parse", "HEAD").decode()
    service = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/main")
        page = (await service.graph("account", "host", key, "branch")).page
        assert [c.oid for c in page.commits] == [head, newer, older, base]
        assert page.commits[-2].parents == (root,)
        rows, _ = append(seed(page.mode, page.head, page.base_oid, page.split_oid), page.commits)
        assert rows[-1].lane == 1 and [(s.kind, s.start, s.end) for s in rows[-1].segments] == [
            ("up", 1, 1)
        ]

    try:
        asyncio.run(run())
    finally:
        service.close()
