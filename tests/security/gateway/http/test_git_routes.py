"""Read-only changes routes: capability, host and object binding, revocation, caching and logs."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from relaydeck.gateway.app.bootstrap import create_app
from relaydeck.gateway.app.state import GatewayState
from relaydeck.gateway.cursors import decode_cursor, encode_cursor
from relaydeck.git.api import GitReadPort
from relaydeck.git.base_store import BaseStore
from relaydeck.git.runner import GitRunner
from relaydeck.git.service import ChangesService
from tests.security.helpers import ALL_CAPS, auth, make_state, mint_token
from tests.support.git_identity import AUTHOR_CONFIG

HOST = "home-mac"
SECRET_BRANCH = "feature/confidential-roadmap"
SECRET_FILE = "confidential-plan.txt"
_ID = AUTHOR_CONFIG


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *_ID, *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / SECRET_FILE).write_text("one\n")
    git(path, "add", SECRET_FILE)
    git(path, "commit", "-qm", "base")
    git(path, "checkout", "-qb", SECRET_BRANCH)
    (path / SECRET_FILE).write_text("two\n")
    return path


@pytest.fixture
def setup(repo: Path, tmp_path: Path) -> Iterator[tuple[TestClient, GatewayState, list[Any]]]:
    state, _ = make_state(hosts=(HOST, "other-mac"))
    service = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))
    hooks: list[Any] = []

    def folders(ids: Any) -> dict[str, str | None]:
        for hook in hooks:
            hook()
        return {agent: str(repo) for agent in ids}

    state.git = GitReadPort(HOST, service, folders)
    yield TestClient(create_app(state)), state, hooks
    service.close()


def summary(client: TestClient, token: str, host: str = HOST) -> Any:
    state: GatewayState = client.app.state.relaydeck  # type: ignore[attr-defined]
    for _ in range(200):
        now = state.now_ms() + 1_000  # polls a second apart, as the app does
        state.now_ms = lambda now=now: now  # type: ignore[misc]
        response = client.post(
            f"/v1/hosts/{host}/git/summaries", json={"agent_ids": ["t1"]}, headers=auth(token)
        )
        if response.status_code != 200 or response.json()["summaries"][0]["state"] != "calculating":
            return response
        time.sleep(0.02)
    raise AssertionError("never settled")


def test_flow_returns_no_store_and_no_paths_in_ids(setup: Any) -> None:
    client, state, _ = setup
    token = mint_token(state, hosts=frozenset({HOST}))
    first = summary(client, token)
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    item = first.json()["summaries"][0]
    assert item["state"] == "ok" and item["branch"] == SECRET_BRANCH
    wt = item["worktree_id"]
    assert "repo" not in wt and SECRET_FILE not in wt
    changes = client.get(f"/v1/hosts/{HOST}/git/worktrees/{wt}/changes", headers=auth(token))
    assert changes.status_code == 200 and changes.headers["cache-control"] == "no-store"
    (entry,) = changes.json()["files"]
    assert entry["path"] == SECRET_FILE and SECRET_FILE not in entry["file_id"]
    diff = client.get(
        f"/v1/hosts/{HOST}/git/worktrees/{wt}/files/{entry['file_id']}/diff", headers=auth(token)
    )
    assert diff.status_code == 200 and diff.json()["render"] == "ok"
    commits = client.get(f"/v1/hosts/{HOST}/git/worktrees/{wt}/commits", headers=auth(token))
    assert commits.status_code == 200  # no remote: the local main is the default base
    bases = client.get(f"/v1/hosts/{HOST}/git/worktrees/{wt}/bases", headers=auth(token)).json()
    main = next(c for c in bases["candidates"] if c["name"] == "main")
    assert main["kind"] == "remote_default"  # choosing it follows the default, nothing is pinned
    saved = client.put(
        f"/v1/hosts/{HOST}/git/worktrees/{wt}/base",
        json={"base_id": main["base_id"]},
        headers=auth(token),
    )
    assert saved.status_code == 200 and saved.json() == {"base_name": "main"}
    branch = client.get(
        f"/v1/hosts/{HOST}/git/worktrees/{wt}/changes?scope=branch", headers=auth(token)
    )
    assert branch.status_code == 200 and branch.json()["base_name"] == "main"


def test_a_base_id_issued_before_names_were_sealed_still_saves(setup: Any) -> None:
    client, state, _ = setup
    token = mint_token(state, hosts=frozenset({HOST}))
    wt = summary(client, token).json()["summaries"][0]["worktree_id"]
    url = f"/v1/hosts/{HOST}/git/worktrees/{wt}"
    bases = client.get(f"{url}/bases", headers=auth(token)).json()["candidates"]
    default = next(c for c in bases if c["kind"] == "remote_default")
    payload = decode_cursor(state.cursor_secret, default["base_id"])
    assert payload["ref"] == "" and payload["n"] == "main"  # follows the default, named
    older = {k: v for k, v in payload.items() if k != "n"} | {"ref": "refs/heads/main"}
    saved = client.put(
        f"{url}/base",
        json={"base_id": encode_cursor(state.cursor_secret, older)},
        headers=auth(token),
    )
    assert saved.status_code == 200 and saved.json() == {"base_name": "main"}
    unnamed = {k: v for k, v in payload.items() if k != "n"}  # following, with no name: refused
    refused = client.put(
        f"{url}/base",
        json={"base_id": encode_cursor(state.cursor_secret, unnamed)},
        headers=auth(token),
    )
    assert refused.json()["code"] == "forbidden"


def test_terminal_view_is_required(setup: Any) -> None:
    client, state, _ = setup
    token = mint_token(state, caps=ALL_CAPS - {"terminal.view"})
    response = client.post(
        f"/v1/hosts/{HOST}/git/summaries", json={"agent_ids": ["t1"]}, headers=auth(token)
    )
    assert response.status_code == 403 and response.json()["code"] == "unsupported_capability"


def test_anonymous_and_foreign_hosts_are_refused(setup: Any) -> None:
    client, state, _ = setup
    body = {"agent_ids": ["t1"]}
    assert client.post(f"/v1/hosts/{HOST}/git/summaries", json=body).status_code == 401
    stranger = mint_token(state, "acct-b", hosts=frozenset({"other-mac"}))
    assert (
        client.post(
            f"/v1/hosts/{HOST}/git/summaries", json=body, headers=auth(stranger)
        ).status_code
        == 404
    )
    both = mint_token(state, hosts=frozenset({HOST, "other-mac"}))
    other = client.post("/v1/hosts/other-mac/git/summaries", json=body, headers=auth(both))
    assert other.status_code == 404  # an owned host that is not this Mac


def test_ids_are_bound_to_account_purpose_and_worktree(setup: Any) -> None:
    client, state, _ = setup
    token_a = mint_token(state, "acct-a", hosts=frozenset({HOST}))
    token_b = mint_token(state, "acct-b", hosts=frozenset({HOST}))
    wt = summary(client, token_a).json()["summaries"][0]["worktree_id"]
    url = f"/v1/hosts/{HOST}/git/worktrees/{wt}/changes"
    assert client.get(url, headers=auth(token_b)).json()["details"]["reason"] == "mismatch"
    file_id = client.get(url, headers=auth(token_a)).json()["files"][0]["file_id"]
    as_worktree = client.get(
        f"/v1/hosts/{HOST}/git/worktrees/{file_id}/changes", headers=auth(token_a)
    )
    assert as_worktree.json()["details"]["reason"] == "mismatch"
    forged = client.get(f"/v1/hosts/{HOST}/git/worktrees/{wt}x/changes", headers=auth(token_a))
    assert forged.status_code == 400


def test_worktree_ids_are_stable_and_file_ids_expire(setup: Any) -> None:
    client, state, _ = setup
    token = mint_token(state, hosts=frozenset({HOST}))
    wt = summary(client, token).json()["summaries"][0]["worktree_id"]
    url = f"/v1/hosts/{HOST}/git/worktrees/{wt}/changes"
    file_id = client.get(url, headers=auth(token)).json()["files"][0]["file_id"]
    now = state.now_ms()
    state.now_ms = lambda: now + 16 * 60_000
    token = mint_token(state, hosts=frozenset({HOST}), now=now + 16 * 60_000)
    assert summary(client, token).json()["summaries"][0]["worktree_id"] == wt
    assert client.get(url, headers=auth(token)).status_code == 200
    late = client.get(
        f"/v1/hosts/{HOST}/git/worktrees/{wt}/files/{file_id}/diff", headers=auth(token)
    )
    assert late.status_code == 400 and late.json()["details"]["reason"] == "expired"


def test_cached_diff_pages_answer_with_the_callers_file_id(setup: Any) -> None:
    client, state, _ = setup
    token = mint_token(state, hosts=frozenset({HOST}))
    wt = summary(client, token).json()["summaries"][0]["worktree_id"]
    url = f"/v1/hosts/{HOST}/git/worktrees/{wt}/changes"
    first = client.get(url, headers=auth(token)).json()["files"][0]["file_id"]
    second = client.get(url, headers=auth(token)).json()["files"][0]["file_id"]
    for file_id in (first, second):
        reply = client.get(
            f"/v1/hosts/{HOST}/git/worktrees/{wt}/files/{file_id}/diff", headers=auth(token)
        ).json()
        assert reply["file_id"] == file_id


def test_revocation_during_the_request_returns_nothing(setup: Any) -> None:
    client, state, hooks = setup
    token = mint_token(state, hosts=frozenset({HOST}))
    summary(client, token)
    hooks.append(lambda: state.sessions.revoke_session(token))
    response = client.post(
        f"/v1/hosts/{HOST}/git/summaries", json={"agent_ids": ["t1"]}, headers=auth(token)
    )
    assert response.status_code == 401 and "summaries" not in response.json()


def test_malformed_requests(setup: Any) -> None:
    client, state, _ = setup
    token = mint_token(state, hosts=frozenset({HOST}))
    url = f"/v1/hosts/{HOST}/git/summaries"
    for body in ({}, {"agent_ids": "t1"}, {"agent_ids": ["x"] * 201}, {"agent_ids": [""]}):
        assert client.post(url, json=body, headers=auth(token)).status_code == 400


def test_logs_never_carry_paths_or_branch_names(
    setup: Any, repo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    client, state, _ = setup
    token = mint_token(state, hosts=frozenset({HOST}))
    caplog.set_level(logging.DEBUG)
    wt = summary(client, token).json()["summaries"][0]["worktree_id"]
    file_id = client.get(
        f"/v1/hosts/{HOST}/git/worktrees/{wt}/changes", headers=auth(token)
    ).json()["files"][0]["file_id"]
    (repo / SECRET_FILE).write_text("three\n")  # stale snapshot: an error path is logged too
    stale = client.get(
        f"/v1/hosts/{HOST}/git/worktrees/{wt}/files/{file_id}/diff", headers=auth(token)
    )
    assert stale.status_code == 409
    client.get(f"/v1/hosts/{HOST}/git/worktrees/{wt}/commits", headers=auth(token))
    text = caplog.text
    for secret in (SECRET_BRANCH, SECRET_FILE, str(repo), "confidential"):
        assert secret not in text


def _gone_response(client: TestClient, state: GatewayState) -> Any:
    token = mint_token(state, hosts=frozenset({HOST}))
    wt = summary(client, token).json()["summaries"][0]["worktree_id"]
    return token, wt


def _assert_unavailable(response: Any, repo: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "host_offline" and body["details"] == {"reason": "unavailable"}
    assert str(repo) not in response.text and str(repo) not in caplog.text
    assert not [r for r in caplog.records if r.exc_info]


def test_a_worktree_that_was_removed_is_unavailable_without_its_path(
    setup: Any, repo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    client, state, _ = setup
    caplog.set_level(logging.DEBUG)
    token, wt = _gone_response(client, state)
    shutil.rmtree(repo)
    gone = client.get(f"/v1/hosts/{HOST}/git/worktrees/{wt}/changes", headers=auth(token))
    _assert_unavailable(gone, repo, caplog)


def test_a_worktree_that_became_unreadable_is_unavailable_without_its_path(
    setup: Any, repo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    client, state, _ = setup
    caplog.set_level(logging.DEBUG)
    token, wt = _gone_response(client, state)
    repo.parent.chmod(0)
    try:
        gone = client.get(f"/v1/hosts/{HOST}/git/worktrees/{wt}/changes", headers=auth(token))
    finally:
        repo.parent.chmod(0o700)
    _assert_unavailable(gone, repo, caplog)
