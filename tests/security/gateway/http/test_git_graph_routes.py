"""Graph route authorization and frozen cursor ownership on disposable histories."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient

from relaydeck.contracts import paths
from relaydeck.contracts.git import ChangesPage, FileDiff, RenderState
from relaydeck.gateway.app.bootstrap import create_app
from relaydeck.gateway.app.state import GatewayState
from relaydeck.gateway.cursors import decode_cursor, encode_cursor
from relaydeck.git import cache as cache_module
from relaydeck.git import service as service_module
from relaydeck.git.api import GitReadPort
from relaydeck.git.base_store import BaseStore
from relaydeck.git.parser import page_size
from relaydeck.git.reader import ReadError, RepositoryReader
from relaydeck.git.results import GraphResult, Snapshot
from relaydeck.git.runner import GitRunner
from relaydeck.git.service import ChangesService
from tests.security.helpers import ALL_CAPS, auth, make_state, mint_token
from tests.support.git_cache import CacheControl
from tests.support.git_identity import AUTHOR_EMAIL, AUTHOR_NAME
from tests.support.git_pem import PEM_PRIVATE_HEADER_BYTES
from tests.support.graph_repos import commit, git, repository


@dataclass
class Environment:
    client: TestClient
    state: GatewayState
    service: ChangesService
    repo: Path
    token: str
    worktree: str

    def get(self, **params: str) -> httpx.Response:
        return self.request(paths.git_graph("host", self.worktree), **params)

    def request(self, url: str, **params: str) -> httpx.Response:
        now = self.state.now_ms() + 2000
        self.state.now_ms = lambda: now
        return cast(
            httpx.Response,
            self.client.get(url, params=params, headers=auth(self.token)),
        )

    def open(self, commit_id: str, **params: str) -> httpx.Response:
        return self.request(paths.git_commit_changes("host", self.worktree, commit_id), **params)


@pytest.fixture
def environment(tmp_path: Path) -> Iterator[Environment]:
    repo = repository(tmp_path / "repo", 203)
    base = git(repo, "rev-list", "--max-parents=0", "HEAD").decode()
    git(repo, "branch", "base", base)
    state, _ = make_state(hosts=("host", "other"))
    service = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))
    service._locate(str(repo))
    key = CacheControl(service).first_worktree
    asyncio.run(service.set_base("acct-1", "host", key, "refs/heads/base"))
    state.git = GitReadPort("host", service, lambda ids: {i: str(repo) for i in ids})
    token = mint_token(state, hosts=frozenset({"host", "other"}))
    worktree = encode_cursor(state.cursor_secret, {"p": "wt", "a": "acct-1", "h": "host", "w": key})
    with TestClient(create_app(state)) as client:
        yield Environment(client, state, service, repo, token, worktree)
    service.close()


def test_graph_flow_is_frozen_paged_and_identity_free(environment: Environment) -> None:
    env = environment
    first = env.get(mode="branch")
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    doc = first.json()
    assert len(doc["commits"]) == 100 and doc["ahead"] == 202
    assert AUTHOR_EMAIL not in first.text and AUTHOR_NAME not in first.text
    assert not {"author", "email", "committer"} & set(doc["commits"][0])
    assert all(isinstance(c["commit_id"], str) and c["commit_id"] for c in doc["commits"])
    commit(env.repo, "Moved")
    second = env.get(mode="branch", cursor=doc["next"])
    assert second.status_code == 200 and second.json()["snapshot"] == doc["snapshot"]
    last = env.get(mode="branch", cursor=second.json()["next"])
    assert len(last.json()["commits"]) == 3 and "next" not in last.json()
    fresh = env.get(mode="branch")
    assert fresh.json()["snapshot"] != doc["snapshot"]


@pytest.mark.parametrize(
    "change", ["account", "host", "mode", "purpose", "tamper", "offset", "bool"]
)
def test_graph_cursor_is_bound(environment: Environment, change: str) -> None:
    env = environment
    token = env.get().json()["next"]
    payload = decode_cursor(env.state.cursor_secret, token)
    mode = "all"
    if change == "account":
        env.token = mint_token(env.state, "acct-b", hosts=frozenset({"host"}))
    elif change == "host":
        payload["h"] = "other"
    elif change == "mode":
        mode = "branch"
    elif change == "purpose":
        payload["p"] = "commits"
    elif change == "offset":
        payload["o"] = 50
    elif change == "bool":
        payload["o"] = True
    if change != "tamper":
        token = encode_cursor(env.state.cursor_secret, payload)
    else:
        token += "invalid"
    result = env.get(mode=mode, cursor=token)
    assert result.status_code in (400, 403, 409)
    assert "commits" not in result.json()


def test_graph_auth_and_revspec_parameters(environment: Environment) -> None:
    env = environment
    url = paths.git_graph("host", env.worktree)
    assert env.client.get(url).status_code == 401
    missing = mint_token(env.state, caps=ALL_CAPS - {"terminal.view"}, hosts=frozenset({"host"}))
    assert env.client.get(url, headers=auth(missing)).status_code == 403
    for params in (
        {"oid": "HEAD~1"},
        {"mode": "--all"},
        {"ref": "refs/stash"},
        {"mode": "HEAD^{tree}"},
    ):
        assert env.get(**params).status_code == 400


@pytest.mark.parametrize("replacement", [False, True])
def test_graph_rechecks_after_service_work(
    environment: Environment,
    monkeypatch: pytest.MonkeyPatch,
    replacement: bool,
) -> None:
    env = environment
    original = env.service.graph

    async def moved(*args: Any, **kwargs: Any) -> GraphResult:
        result = await original(*args, **kwargs)
        if replacement:
            env.state.git = None
        else:
            env.state.sessions.revoke_session(env.token)
        return result

    monkeypatch.setattr(env.service, "graph", moved)
    assert env.get().status_code == 401


def test_graph_eviction_expiry_and_no_sensitive_diagnostics(
    environment: Environment,
    caplog: pytest.LogCaptureFixture,
) -> None:
    env = environment
    caplog.set_level(logging.DEBUG, logger="relaydeck")
    first = env.get().json()
    CacheControl(env.service).retire_graphs()
    stale = env.get(cursor=first["next"])
    assert stale.status_code == 409
    now = env.state.now_ms() + 16 * 60_000
    env.state.now_ms = lambda: now
    env.token = mint_token(env.state, hosts=frozenset({"host"}), now=now)
    assert env.get(cursor=first["next"]).status_code == 400
    logs = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("relaydeck"))
    for private in (first["snapshot"], first["head"], first["base_name"], str(env.repo), env.token):
        assert private not in logs


@pytest.mark.parametrize("mode", ["all", "branch"])
def test_root_split_selection_has_sealed_id_and_empty_changes(
    environment: Environment, mode: str
) -> None:
    env = environment
    page = env.get(mode=mode).json()
    while page.get("next"):
        page = env.get(mode=mode, cursor=page["next"]).json()
    root = page["commits"][-1]
    assert root["parents"] == [] and not root["ahead"]
    result = env.open(root["commit_id"])
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    body = ChangesPage.from_dict(result.json())
    assert body.scope == "commit" and body.head == root["oid"]
    assert body.files == () and body.total_files == 0 and body.next is None
    assert body.base == git(env.repo, "hash-object", "-t", "tree", "--stdin", data=b"").decode()


@pytest.mark.parametrize(
    "change,reason,status",
    [
        ("tamper", "malformed", 400),
        ("purpose", "mismatch", 400),
        ("account", "mismatch", 400),
        ("host", "mismatch", 400),
        ("worktree", "mismatch", 400),
        ("expired", "expired", 400),
        ("invalid_oid", "malformed", 400),
        ("invalid_mode", "malformed", 400),
        ("missing_revision", "malformed", 400),
        ("unissued", "not_found", 404),
        ("unknown", "not_found", 404),
        ("retired", "stale_snapshot", 409),
        ("wrong_mode", "stale_snapshot", 409),
    ],
)
def test_commit_id_requires_bound_issued_selection(
    environment: Environment, change: str, reason: str, status: int
) -> None:
    env = environment
    page = env.get().json()
    token = page["commits"][0]["commit_id"]
    payload = decode_cursor(env.state.cursor_secret, token)
    if change in ("account", "host", "worktree", "purpose"):
        payload[{"account": "a", "host": "h", "worktree": "w", "purpose": "p"}[change]] = "other"
    elif change == "expired":
        payload["x"] = env.state.now_ms() - 1
    elif change == "invalid_oid":
        payload["oid"] = "HEAD~1"
    elif change == "invalid_mode":
        payload["m"] = "--all"
    elif change == "missing_revision":
        del payload["r"]
    elif change == "unissued":
        payload["oid"] = git(env.repo, "rev-list", "--max-parents=0", "HEAD").decode()
    elif change == "unknown":
        payload["oid"] = "1" * 40
    elif change == "retired":
        CacheControl(env.service).retire_graphs()
    elif change == "wrong_mode":
        payload["m"] = "branch"
    token = encode_cursor(env.state.cursor_secret, payload)
    if change == "tamper":
        token += "invalid"
    response = env.open(token)
    assert response.status_code == status
    assert response.json()["details"]["reason"] == reason
    assert "files" not in response.json()


def test_commit_route_rejects_query_injection_and_scope_bypass(environment: Environment) -> None:
    env = environment
    selected = env.get().json()["commits"][0]["commit_id"]
    for name in ("oid", "ref", "scope", "mode", "path", "base"):
        response = env.open(selected, **{name: "HEAD~1"})
        assert response.status_code == 400
        assert response.json()["details"]["reason"] == "malformed"
    assert env.open(selected, cursor="").status_code == 400
    duplicate = env.client.get(
        paths.git_commit_changes("host", env.worktree, selected) + "?cursor=one&cursor=two",
        headers=auth(env.token),
    )
    assert duplicate.status_code == 400 and duplicate.json()["details"]["reason"] == "malformed"
    assert env.request(paths.git_changes("host", env.worktree), scope="commit").status_code == 400
    url = paths.git_commit_changes("host", env.worktree, selected)
    assert env.client.get(url).status_code == 401
    missing = mint_token(env.state, caps=ALL_CAPS - {"terminal.view"}, hosts=frozenset({"host"}))
    assert env.client.get(url, headers=auth(missing)).status_code == 403


def test_commit_files_and_diff_cursors_survive_selection_expiry_and_graph_retirement(
    environment: Environment,
) -> None:
    env = environment
    (env.repo / "000-large.txt").write_text("line\n" * 1200)
    for i in range(101):
        (env.repo / f"item-{i:03}.txt").write_text("content\n")
    git(env.repo, "add", ".")
    oid = commit(env.repo)
    page = env.get().json()
    selected = page["commits"][0]["commit_id"]
    payload = decode_cursor(env.state.cursor_secret, selected)
    payload["x"] = env.state.now_ms() + 10_000
    selected = encode_cursor(env.state.cursor_secret, payload)
    response = env.open(selected)
    assert response.status_code == 200
    doc = response.json()
    assert set(doc) == {
        "scope",
        "snapshot",
        "head",
        "base",
        "branch",
        "detached",
        "short_oid",
        "additions",
        "deletions",
        "base_name",
        "commits",
        "partial",
        "as_of_ms",
        "total_files",
        "files",
        "next",
    }
    parsed = ChangesPage.from_dict(doc)
    assert parsed.head == oid and len(parsed.files) == 100 and parsed.total_files == 102
    assert parsed.branch is None and parsed.base_name is None and parsed.commits is None
    file = parsed.files[0].file_id
    diff_url = paths.git_file_diff("host", env.worktree, file)
    assert FileDiff.from_dict(env.request(diff_url).json()).render == "load_required"
    diff = env.request(diff_url, load="1").json()
    assert FileDiff.from_dict(diff).render == "ok" and diff["next"]
    key = payload["w"]
    asyncio.run(env.service.set_base("acct-1", "host", key, "refs/heads/base"))
    now = env.state.now_ms() + 20_000
    env.state.now_ms = lambda: now
    expired = env.open(selected)
    assert expired.status_code == 400 and expired.json()["details"]["reason"] == "expired"
    following = env.open(selected, cursor=doc["next"])
    assert following.status_code == 200 and len(following.json()["files"]) == 2
    assert following.json()["snapshot"] == doc["snapshot"] and following.json()["next"] is None
    final_diff = env.request(diff_url, load="1", cursor=diff["next"])
    assert final_diff.status_code == 200 and FileDiff.from_dict(final_diff.json()).hunks
    altered = encode_cursor(env.state.cursor_secret, {**payload, "x": now + 60_000})
    wrong = env.open(altered, cursor=doc["next"])
    assert wrong.status_code == 400 and wrong.json()["details"]["reason"] == "mismatch"
    CacheControl(env.service).retire_scope("commit")
    stale = env.open(selected, cursor=doc["next"])
    assert stale.status_code == 409
    assert stale.json()["details"]["reason"] == "stale_snapshot"


def test_commit_diff_masking_and_errors_do_not_expose_content_or_identity(
    environment: Environment, caplog: pytest.LogCaptureFixture
) -> None:
    env = environment
    old = (
        PEM_PRIVATE_HEADER_BYTES
        + b"\n"
        + b"".join(f"private-row-{i:02}\n".encode() for i in range(20))
        + b"-----END PRIVATE KEY-----\n"
    )
    target = env.repo / "file.txt"
    target.write_bytes(old)
    git(env.repo, "add", ".")
    commit(env.repo)
    target.write_bytes(old.replace(b"private-row-10", b"private-changed"))
    git(env.repo, "add", ".")
    commit(env.repo)
    caplog.set_level(logging.DEBUG, logger="relaydeck")
    row = env.get().json()["commits"][0]
    doc = env.open(row["commit_id"]).json()
    result = env.request(paths.git_file_diff("host", env.worktree, doc["files"][0]["file_id"]))
    assert result.status_code == 200 and FileDiff.from_dict(result.json()).render == "ok"
    assert "private-row" not in result.text and "private-changed" not in result.text
    assert AUTHOR_EMAIL not in result.text and AUTHOR_NAME not in result.text
    assert env.open(row["commit_id"], oid="HEAD~1").status_code == 400
    logs = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("relaydeck"))
    for private in (
        row["oid"],
        row["commit_id"],
        doc["snapshot"],
        env.token,
        str(env.repo),
        "private-row",
        "private-changed",
    ):
        assert private not in logs


@pytest.mark.parametrize("operation", ["selection", "diff", "files"])
@pytest.mark.parametrize("replacement", [False, True])
def test_commit_routes_recheck_ownership_after_service_work(
    environment: Environment, monkeypatch: pytest.MonkeyPatch, operation: str, replacement: bool
) -> None:
    env = environment
    for index in range(101 if operation == "files" else 1):
        (env.repo / f"item-{index:03}.txt").write_text("content\n")
    git(env.repo, "add", ".")
    commit(env.repo)
    selected = env.get().json()["commits"][0]["commit_id"]

    def retire() -> None:
        if replacement:
            env.state.git = None
        else:
            env.state.sessions.revoke_session(env.token)

    if operation == "selection":
        original = env.service.commit_snapshot

        async def changed(*args: Any, **kwargs: Any) -> Snapshot:
            result = await original(*args, **kwargs)
            retire()
            return result

        monkeypatch.setattr(env.service, "commit_snapshot", changed)
        result = env.open(selected)
    else:
        doc = env.open(selected).json()
        if operation == "files":
            cached = env.service.cached

            def changed_cached(*args: Any, **kwargs: Any) -> Snapshot:
                result = cached(*args, **kwargs)
                retire()
                return result

            monkeypatch.setattr(env.service, "cached", changed_cached)
            result = env.open(selected, cursor=doc["next"])
        else:
            file_diff = env.service.file_diff

            async def changed_diff(*args: Any, **kwargs: Any) -> tuple[FileDiff, int]:
                result = await file_diff(*args, **kwargs)
                retire()
                return result

            monkeypatch.setattr(env.service, "file_diff", changed_diff)
            result = env.request(
                paths.git_file_diff("host", env.worktree, doc["files"][0]["file_id"])
            )
    assert result.status_code == 401


def test_commit_diff_returns_typed_stale_error_if_cache_closes_before_completion(
    environment: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = environment
    (env.repo / "file.txt").write_text("content\n")
    git(env.repo, "add", ".")
    commit(env.repo)
    selected = env.get().json()["commits"][0]["commit_id"]
    doc = env.open(selected).json()
    owner = env.state.git

    def close(value: FileDiff) -> int:
        CacheControl(env.service).close()
        return page_size(value)

    monkeypatch.setattr(cache_module, "page_size", close)
    result = env.request(paths.git_file_diff("host", env.worktree, doc["files"][0]["file_id"]))
    assert env.state.git is owner
    assert result.status_code == 409
    assert result.json() == {
        "code": "stale_revision",
        "message": "changes unavailable",
        "retryable": False,
        "details": {"reason": "stale_snapshot"},
    }


@pytest.mark.parametrize("retirement", ["close", "snapshot", "worktree"])
@pytest.mark.parametrize("reason", ["unsupported", "invalid_encoding", "too_large", "render"])
def test_render_only_commit_retirement_returns_typed_stale_error(
    environment: Environment, monkeypatch: pytest.MonkeyPatch, retirement: str, reason: str
) -> None:
    env = environment
    (env.repo / "file.txt").write_text("content\n")
    git(env.repo, "add", ".")
    commit(env.repo)
    selected = env.get().json()["commits"][0]["commit_id"]
    doc = env.open(selected).json()
    owner = env.state.git
    cache = CacheControl(env.service)
    key = cache.first_worktree

    def retire() -> None:
        if retirement == "close":
            CacheControl(env.service).close()
        else:
            if retirement == "snapshot":
                cache.retire_scope("commit")
            else:
                cache.retire_worktree(key)

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
    result = env.request(paths.git_file_diff("host", env.worktree, doc["files"][0]["file_id"]))
    assert env.state.git is owner
    assert result.status_code == 409
    assert result.json() == {
        "code": "stale_revision",
        "message": "changes unavailable",
        "retryable": False,
        "details": {"reason": "stale_snapshot"},
    }


def test_graph_checks_keep_open_commit_continuation_and_diff_alive(
    environment: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    env = environment
    (env.repo / "000-large.txt").write_text("line\n" * 1200)
    for index in range(101):
        (env.repo / f"item-{index:03}.txt").write_text("content\n")
    git(env.repo, "add", ".")
    commit(env.repo)
    cache = CacheControl(env.service)
    now = [time.monotonic()]
    cache.use_clock(monkeypatch, lambda: now[0])
    selected = env.get().json()["commits"][0]["commit_id"]
    opened = env.open(selected).json()
    url = paths.git_file_diff("host", env.worktree, opened["files"][0]["file_id"])
    diff = env.request(url, load="1").json()
    assert opened["next"] and diff["next"]
    for _ in range(9):
        now[0] += 10
        assert env.get().status_code == 200
        cache.sweep()
    continued = env.open(selected, cursor=opened["next"])
    assert continued.status_code == 200 and len(continued.json()["files"]) == 2
    next_diff = env.request(url, load="1", cursor=diff["next"])
    assert next_diff.status_code == 200 and FileDiff.from_dict(next_diff.json()).hunks
    cache.assert_accounted()
