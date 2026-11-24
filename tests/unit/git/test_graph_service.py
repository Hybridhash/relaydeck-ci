"""Graph service on real branch, tag and merge histories."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from relaydeck.git import graph_reader
from relaydeck.git.base_store import BaseStore
from relaydeck.git.graph_reader import GraphReader, parse_graph_commits, valid_ref
from relaydeck.git.locator import RepositoryLocator
from relaydeck.git.reader import ReadError
from relaydeck.git.results import ChangesError
from relaydeck.git.runner import GitRunner
from relaydeck.git.service import ChangesService
from tests.support.git_cache import CacheControl
from tests.support.git_identity import AUTHOR_LINE
from tests.support.graph_repos import commit, git, located, repository


@pytest.fixture
def service(tmp_path: Path) -> Iterator[ChangesService]:
    svc = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))
    yield svc
    svc.close()


def test_four_field_parser_accepts_roots_empty_subject_and_separator() -> None:
    raw = b"a" * 40 + b"\0\0-1\0\0" + b"b" * 40 + b"\0" + b"a" * 40 + b"\0" + b"2\0one\x1etwo\xff\0"
    root, child = parse_graph_commits(raw)
    assert root.parents == () and root.subject == "" and root.time_s == -1
    assert child.subject == "one\\u001etwo�" and child.parents == (root.oid,)
    assert parse_graph_commits(b"") == ()
    for invalid in (b"bad", raw[:-1], b"z" * 40 + b"\0\0a\0x\0"):
        with pytest.raises(ReadError, match="malformed"):
            parse_graph_commits(invalid)


@pytest.mark.parametrize(
    "name",
    [
        b"-n10",
        b"refs/heads/a..b",
        b"refs/heads/a@{b",
        b"refs/heads/x*",
        b"refs/heads/.x",
        b"refs/heads/x.lock",
        b"refs/heads/a\\b",
        b"refs/heads/a\nb",
    ],
)
def test_ref_validation_rejects_revspec_patterns(name: bytes) -> None:
    assert not valid_ref(name)


def test_allowed_roots_tags_upstreams_detached_and_raw_ref_identity(
    tmp_path: Path,
    service: ChangesService,
) -> None:
    repo = repository(tmp_path / "repo")
    base = git(repo, "rev-parse", "HEAD").decode()
    git(repo, "checkout", "-qb", "feature")
    head = commit(repo)
    git(repo, "update-ref", "refs/remotes/origin/feature", head)
    git(repo, "config", "branch.feature.remote", "origin")
    git(repo, "config", "branch.feature.merge", "refs/heads/feature")
    git(repo, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    git(repo, "tag", "release", base)
    git(repo, "tag", "-am", "Tag", "annotated", head)
    git(repo, "tag", "-am", "Tag", "nested", "annotated")
    packed = repo / ".git" / "packed-refs"
    packed.write_bytes(base.encode() + b" refs/heads/raw-\xff\n")
    git(repo, "checkout", "--orphan", "unrelated")
    other = commit(repo)
    git(repo, "update-ref", "refs/remotes/origin/excluded", other)
    git(repo, "checkout", "feature")
    git(repo, "branch", "-D", "unrelated")

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/main")
        result = await service.graph("account", "host", key, "all")
        page = result.page
        assert {c.oid for c in page.commits} == {base, head}
        assert page.head == head and page.base_oid == page.split_oid == base and page.ahead == 1
        labels = {(r.name, r.kind) for c in page.commits for r in c.refs}
        by_oid = {c.oid: [(r.name, r.kind) for r in c.refs] for c in page.commits}
        assert by_oid[head] == [
            ("feature", "head"),
            ("annotated", "tag"),
            ("nested", "tag"),
            ("origin/feature", "upstream"),
        ]
        assert by_oid[base] == [
            ("main", "base"),
            ("raw-\\xff", "local"),
            ("split point", "split"),
            ("release", "tag"),
        ]
        assert ("origin/feature", "upstream") in labels
        assert ("raw-\\xff", "local") in labels
        assert ("nested", "tag") in labels and ("annotated", "tag") in labels
        assert all(c.commit_id is None for c in page.commits)
        packed.write_bytes(packed.read_bytes() + base.encode() + b" refs/heads/raw-\xfe\n")
        changed = await service.graph("account", "host", key, "all")
        assert changed.page.snapshot != page.snapshot
        git(repo, "config", "branch.feature.remote", ".")
        git(repo, "config", "branch.feature.merge", os.fsdecode(b"refs/heads/raw-\xff"))
        tracked = await service.graph("account", "host", key, "all")
        assert any(
            r.name == "raw-\\xff" and r.kind == "upstream"
            for c in tracked.page.commits
            for r in c.refs
        )
        git(repo, "checkout", "--detach", head)
        detached = await service.graph("account", "host", key, "branch")
        assert detached.page.branch is None
        assert [c.oid for c in detached.page.commits] == [head, base]
        assert detached.page.commits[-1].ahead is False

    asyncio.run(run())


def test_frozen_paging_caps_accounting_and_retirement(
    tmp_path: Path,
    service: ChangesService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = repository(tmp_path / "repo", 202)
    base = git(repo, "rev-list", "--max-parents=0", "HEAD").decode()
    git(repo, "branch", "base", base)

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/base")
        first = await service.graph("account", "host", key, "branch")
        assert len(first.page.commits) == 100 and first.next_offset == 100
        revision = first.page.snapshot
        commit(repo, "Moved after page one")
        original = GraphReader.capture
        monkeypatch.setattr(
            GraphReader, "capture", lambda *args: pytest.fail("continuation recaptured refs")
        )
        second = await service.graph(
            "account", "host", key, "branch", revision=revision, offset=100
        )
        last = await service.graph("account", "host", key, "branch", revision=revision, offset=200)
        assert len(second.page.commits) == 100 and second.page.snapshot == revision
        assert len(last.page.commits) == 2 and last.page.commits[-1].oid == base
        assert last.next_offset is None and first.page.ahead == 201
        assert len({c.oid for p in (first, second, last) for c in p.page.commits}) == 202
        assert (
            await service.graph("account", "host", key, "branch", revision=revision, offset=100)
            == second
        )
        with pytest.raises(ChangesError, match="stale_snapshot"):
            await service.graph("account", "host", key, "all", revision=revision, offset=100)
        with pytest.raises(ChangesError, match="stale_snapshot"):
            await service.graph("account", "host", key, "branch", revision=revision, offset=50)
        CacheControl(service).assert_accounted()
        CacheControl(service).assert_graph_membership_charged()
        monkeypatch.setattr(GraphReader, "capture", original)
        fresh = await service.graph("account", "host", key, "branch")
        assert fresh.page.snapshot != revision
        await service.set_base("account", "host", key, "refs/heads/main")
        with pytest.raises(ChangesError, match="stale_snapshot"):
            await service.graph("account", "host", key, "branch", revision=revision, offset=100)

    asyncio.run(run())


def test_ref_cap_keeps_head_base_and_reports_partial(
    tmp_path: Path,
    service: ChangesService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = repository(tmp_path / "repo")
    git(repo, "checkout", "-qb", "zz-head")
    head = commit(repo)
    git(repo, "branch", "aa-other")
    monkeypatch.setattr(graph_reader, "MAX_LOCAL_REFS", 1)

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/main")
        page = (await service.graph("account", "host", key, "all")).page
        assert page.head == head and page.partial and page.limit_reason == "ref_limit"
        assert {"head", "base"} <= {r.kind for c in page.commits for r in c.refs}

    asyncio.run(run())


def test_zero_ahead_and_unrelated_base(tmp_path: Path, service: ChangesService) -> None:
    repo = repository(tmp_path / "repo")

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/main")
        page = (await service.graph("account", "host", key, "branch")).page
        assert page.ahead == 0 and len(page.commits) == 1 and not page.commits[0].ahead
        git(repo, "checkout", "--orphan", "other")
        commit(repo)
        with pytest.raises(ChangesError, match="no_base"):
            await service.graph("account", "host", key, "all")

    asyncio.run(run())


def test_deadline_and_history_guard(tmp_path: Path) -> None:
    repo = repository(tmp_path / "repo")
    runner = GitRunner.create()
    worktree = RepositoryLocator(runner).locate(str(repo))
    with pytest.raises(ChangesError, match="busy"):
        GraphReader(runner, worktree, deadline=time.monotonic() - 1).records(
            (git(repo, "rev-parse", "HEAD").decode(),), 0
        )
    (repo / ".git" / "info" / "grafts").write_text("")
    with pytest.raises(ChangesError, match="unsupported_config"):
        GraphReader(runner, worktree, deadline=time.monotonic() + 10).check_history()


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_root_and_octopus_parents_are_not_rewritten(
    tmp_path: Path,
    service: ChangesService,
    object_format: str,
) -> None:
    repo = repository(tmp_path / "repo", object_format=object_format)
    root = git(repo, "rev-parse", "HEAD").decode()
    tips = []
    for name in ("first", "second", "third"):
        git(repo, "checkout", "-qb", name, root)
        tips.append(commit(repo, name))
    git(repo, "checkout", "first")
    git(repo, "merge", "--no-ff", "--no-edit", "second", "third")
    merge = git(repo, "rev-parse", "HEAD").decode()

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/main")
        result = (await service.graph("account", "host", key, "all")).page
        by_oid = {c.oid: c for c in result.commits}
        assert by_oid[merge].parents == tuple(tips)
        assert by_oid[root].parents == () and not by_oid[root].ahead
        order = {c.oid: i for i, c in enumerate(result.commits)}
        assert all(order[c.oid] < order[p] for c in result.commits for p in c.parents)
        assert result.ahead == 4
        (repo / "working.txt").write_text("Uncommitted")
        again = await service.graph("account", "host", key, "all")
        assert again.page.snapshot == result.snapshot

    asyncio.run(run())


def test_exact_page_boundary_split_row_and_history_limit(
    tmp_path: Path,
    service: ChangesService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = repository(tmp_path / "repo", 101)
    git(repo, "branch", "base", git(repo, "rev-list", "--max-parents=0", "HEAD").decode())

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/base")
        first = await service.graph("account", "host", key, "branch")
        assert first.next_offset == 100
        last = await service.graph(
            "account", "host", key, "branch", revision=first.page.snapshot, offset=100
        )
        assert len(last.page.commits) == 1 and last.page.commits[0].oid == first.page.split_oid
        assert last.next_offset is None
        from relaydeck.git import graph as graph_module

        monkeypatch.setattr(graph_module, "MAX_HISTORY", 100)
        capped = await service.graph("account", "host", key, "all")
        assert capped.page.partial and capped.page.limit_reason == "history_limit"
        assert capped.next_offset is None

    asyncio.run(run())


def test_missing_and_multiple_merge_bases_remain_unavailable(
    tmp_path: Path, service: ChangesService
) -> None:
    repo = repository(tmp_path / "repo")
    root = git(repo, "rev-parse", "HEAD").decode()
    tree = git(repo, "rev-parse", "HEAD^{tree}").decode()
    left = git(repo, "commit-tree", tree, "-p", root, "-m", "Left").decode()
    right = git(repo, "commit-tree", tree, "-p", root, "-m", "Right").decode()
    one = git(repo, "commit-tree", tree, "-p", left, "-p", right, "-m", "Join one").decode()
    two = git(repo, "commit-tree", tree, "-p", right, "-p", left, "-m", "Join two").decode()
    git(repo, "update-ref", "refs/heads/main", one)
    git(repo, "checkout", "-qb", "feature", two)

    async def run() -> None:
        key = await located(service, repo)
        for ref in ("refs/heads/missing", "refs/heads/main"):
            await service.set_base("account", "host", key, ref)
            with pytest.raises(ChangesError, match="no_base"):
                await service.graph("account", "host", key, "all")

    asyncio.run(run())


def test_fresh_page_retries_a_ref_race_and_keeps_topology_with_skewed_time(
    tmp_path: Path,
    service: ChangesService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = repository(tmp_path / "repo")
    root = git(repo, "rev-parse", "HEAD").decode()
    data = (
        b"commit refs/heads/feature\ncommitter " + AUTHOR_LINE.encode() + b" 1600000000 +0000\n"
        b"data 5\nOlder\nfrom " + root.encode() + b"\n\n"
    )
    git(repo, "fast-import", "--quiet", data=data)
    git(repo, "checkout", "feature")
    original = GraphReader.capture
    captures = 0

    def capture(reader: GraphReader, base: str, choice: str) -> graph_reader.RefCapture:
        nonlocal captures
        value = original(reader, base, choice)
        captures += 1
        if captures == 1:
            commit(repo, "Concurrent update")
        return value

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/main")
        monkeypatch.setattr(GraphReader, "capture", capture)
        page = (await service.graph("account", "host", key, "all")).page
        assert captures == 4 and page.head == git(repo, "rev-parse", "HEAD").decode()
        assert len(page.commits) == 3
        assert page.commits[1].time_ms < page.commits[-1].time_ms
        assert page.commits[1].parents == (root,)

    asyncio.run(run())


def test_graph_budget_expiry_worktree_replacement_and_account_isolation(
    tmp_path: Path,
    service: ChangesService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = repository(tmp_path / "repo", 102)
    git(repo, "branch", "base", git(repo, "rev-list", "--max-parents=0", "HEAD").decode())

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/base")
        first = await service.graph("account", "host", key, "all")
        with pytest.raises(ChangesError, match="stale_snapshot"):
            await service.graph(
                "other", "host", key, "all", revision=first.page.snapshot, offset=100
            )
        cache = CacheControl(service)
        cache.assert_accounted()
        cache.expire_graphs(monkeypatch)
        with pytest.raises(ChangesError, match="stale_snapshot"):
            await service.graph(
                "account", "host", key, "all", revision=first.page.snapshot, offset=100
            )
        assert cache.graph_count == 0
        cache.use_clock(monkeypatch, time.monotonic)
        await service.graph("account", "host", key, "all")
        cache.replace_worktree(key)
        assert cache.graph_count == 0 and cache.bytes == 0

    asyncio.run(run())


def test_fingerprint_ignores_unadmitted_upstream_prefix_descendants(
    tmp_path: Path, service: ChangesService
) -> None:
    repo = repository(tmp_path / "repo")
    root = git(repo, "rev-parse", "HEAD").decode()
    tree = git(repo, "rev-parse", "HEAD^{tree}").decode()
    tip = git(repo, "commit-tree", tree, "-p", root, "-m", "Remote update").decode()
    git(repo, "branch", "other")
    git(repo, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    for local, upstream in (("main", "missing"), ("other", "tracked")):
        git(repo, "config", f"branch.{local}.remote", "origin")
        git(repo, "config", f"branch.{local}.merge", f"refs/heads/{upstream}")
    git(repo, "update-ref", "refs/remotes/origin/missing/child", root)
    git(repo, "update-ref", "refs/remotes/origin/tracked", root)

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/main")
        first = (await service.graph("account", "host", key, "all")).page
        git(repo, "update-ref", "refs/remotes/origin/missing/child", tip)
        same = (await service.graph("account", "host", key, "all")).page
        assert same.snapshot == first.snapshot and {c.oid for c in same.commits} == {root}
        git(repo, "update-ref", "refs/remotes/origin/tracked", tip)
        changed = (await service.graph("account", "host", key, "all")).page
        assert changed.snapshot != first.snapshot and tip in {c.oid for c in changed.commits}

    asyncio.run(run())
