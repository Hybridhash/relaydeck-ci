"""ChangesRepository over the real gateway transport and a scripted HTTP server."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

from relaydeck.client.features.changes.model import ChangesPage
from relaydeck.client.features.changes.repository import ChangesRepository
from relaydeck.client.features.changes.source import ChangesFailure
from relaydeck.client.platform.transport.transport import HttpxGatewayTransport
from relaydeck.client.ports.transport import GatewayTransport
from relaydeck.contracts.git import (
    BaseCandidate,
    BranchCounts,
    FileDiff,
    FileEntry,
    GraphPage,
    WorkingCounts,
    WorktreeSummary,
)
from tests.support.graph_pages import graph_commit, graph_page

Handler = Callable[[httpx.Request], httpx.Response]
HOST, WORKTREE = "home-mac", "wt-token"


def repository(handler: Handler) -> tuple[ChangesRepository, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    async def token() -> str:
        return "token-a"

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    transport = HttpxGatewayTransport(base_url="https://gateway.test", token=token, client=client)
    return ChangesRepository(transport), seen


def replying(body: Any, status: int = 200, headers: dict[str, str] | None = None) -> Handler:
    return lambda request: httpx.Response(status, json=body, headers=headers)


def summary_doc(**over: Any) -> dict[str, Any]:
    return WorktreeSummary(
        **{
            "agent_id": "agent-1",
            "worktree_id": WORKTREE,
            "state": "ok",
            "branch": "feature",
            "detached": False,
            "short_oid": "abc1234",
            "base_name": "origin/main",
            "working": WorkingCounts(files=2, staged=1, unstaged=1, untracked=1, conflicts=0),
            "branch_scope": BranchCounts(commits=3),
            "shared": False,
            "revision": "rev-1",
            "as_of_ms": 7,
            "partial": False,
            **over,
        }
    ).to_dict()


def entry_doc(file_id: str = "file-token") -> dict[str, Any]:
    return FileEntry(
        file_id=file_id,
        path="dir/a.txt",
        status="M",
        staged=False,
        unstaged=True,
        additions=2,
        deletions=1,
        generated=False,
        withheld=False,
        rename_limited=False,
        renderable=True,
    ).to_dict()


def changes_doc(**over: Any) -> dict[str, Any]:
    return {
        "scope": "branch",
        "snapshot": "snap-1",
        "head": "a" * 40,
        "base": "b" * 40,
        "branch": "feature",
        "detached": False,
        "short_oid": "aaaaaaaaaaaa",
        "additions": 12,
        "deletions": 3,
        "base_name": "origin/main",
        "commits": 3,
        "partial": True,
        "as_of_ms": 99,
        "total_files": 1,
        "files": [entry_doc()],
        "next": "cursor-2",
        **over,
    }


def diff_doc(**over: Any) -> dict[str, Any]:
    doc = FileDiff(file_id="file-token", snapshot="snap-1", render="ok", hunks=()).to_dict()
    return {**doc, "next": "page-2", **over}


def run[T](call: Awaitable[T]) -> T:
    return asyncio.run(_await(call))


async def _await[T](call: Awaitable[T]) -> T:
    return await call


# -- every route ---------------------------------------------------------------------------------


def test_summaries_posts_the_agent_ids_and_decodes_every_summary() -> None:
    repo, seen = repository(replying({"summaries": [summary_doc(), summary_doc(agent_id="b")]}))
    result = run(repo.summaries(HOST, ["agent-1", "b"]))
    assert [s.agent_id for s in result] == ["agent-1", "b"]
    assert result[0].branch == "feature" and result[0].working.untracked == 1
    request = seen[0]
    assert (request.method, request.url.path) == ("POST", "/v1/hosts/home-mac/git/summaries")
    assert json.loads(request.content) == {"agent_ids": ["agent-1", "b"]}
    assert request.headers["authorization"] == "Bearer token-a"


def test_changes_maps_the_reply_to_a_page_and_sends_scope_and_cursor() -> None:
    repo, seen = repository(replying(changes_doc()))
    page = run(repo.changes(HOST, WORKTREE, "branch", "cursor-1"))
    assert isinstance(page, ChangesPage)
    assert (page.snapshot, page.next, page.partial) == ("snap-1", "cursor-2", True)
    assert (page.branch, page.base_name, page.base_revision, page.as_of_ms) == (
        "feature",
        "origin/main",
        "b" * 40,
        99,
    )
    assert [f.file_id for f in page.files] == ["file-token"] and page.files[0].additions == 2
    request = seen[0]
    assert request.method == "GET"
    assert request.url.path == f"/v1/hosts/{HOST}/git/worktrees/{WORKTREE}/changes"
    assert dict(request.url.params) == {"scope": "branch", "cursor": "cursor-1"}


def test_the_first_page_of_a_working_list_sends_no_cursor() -> None:
    doc = changes_doc(scope="working", base=None, commits=None, next=None, partial=False)
    repo, seen = repository(replying(doc))
    page = run(repo.changes(HOST, WORKTREE, "working", None))
    assert page.next is None and page.base_revision is None and not page.partial
    assert dict(seen[0].url.params) == {"scope": "working"}


def test_file_diff_sends_cursor_and_load_and_decodes_the_page() -> None:
    repo, seen = repository(replying(diff_doc()))
    page = run(repo.file_diff(HOST, WORKTREE, "file/token", "page-1", True))
    assert isinstance(page, FileDiff) and page.next == "page-2" and page.render == "ok"
    request = seen[0]
    assert request.url.raw_path.split(b"?")[0] == (
        f"/v1/hosts/{HOST}/git/worktrees/{WORKTREE}/files/file%2Ftoken/diff".encode()
    )
    assert dict(request.url.params) == {"cursor": "page-1", "load": "1"}
    run(repo.file_diff(HOST, WORKTREE, "f", None, False))
    assert dict(seen[1].url.params) == {}


def test_graph_decodes_revision_and_carries_mode_and_cursor() -> None:
    page = graph_page((graph_commit(1),), next="more")
    repo, seen = repository(replying(page.to_dict()))
    decoded = run(repo.graph(HOST, WORKTREE, "all", "c1"))
    assert decoded == page and isinstance(decoded, GraphPage)
    assert seen[0].url.path.endswith("/graph")
    assert dict(seen[0].url.params) == {"mode": "all", "cursor": "c1"}


def commit_doc(**over: Any) -> dict[str, Any]:
    fixed = {
        "scope": "commit",
        "branch": None,
        "base_name": None,
        "commits": None,
        "partial": False,
        "next": None,
    }
    return changes_doc(**{**fixed, **over})


def test_commit_changes_reads_the_commits_files_by_its_id_and_cursor() -> None:
    repo, seen = repository(replying(commit_doc(next="more")))
    page = run(repo.commit_changes(HOST, WORKTREE, "commit/id 1", "cursor-1"))
    assert (page.snapshot, page.next, page.branch, page.base_name) == ("snap-1", "more", None, None)
    assert page.head == "a" * 40 and page.base_revision == "b" * 40 and page.authoritative
    assert [f.file_id for f in page.files] == ["file-token"]
    request = seen[0]
    assert request.method == "GET"
    assert request.url.raw_path.decode().startswith(
        f"/v1/hosts/{HOST}/git/worktrees/{WORKTREE}/commits/commit%2Fid%201/changes"
    )
    assert dict(request.url.params) == {"cursor": "cursor-1"}


def test_the_first_page_of_a_commit_sends_no_query() -> None:
    repo, seen = repository(replying(commit_doc()))
    run(repo.commit_changes(HOST, WORKTREE, "id", None))
    assert dict(seen[0].url.params) == {}


def test_bases_decode_candidates_and_the_next_cursor() -> None:
    candidate = BaseCandidate(base_id="base-token", name="main", kind="local", oid="c" * 40)
    repo, seen = repository(replying({"candidates": [candidate.to_dict()], "next": None}))
    candidates, more = run(repo.bases(HOST, WORKTREE, None))
    assert candidates == (candidate,) and more is None
    assert (seen[0].method, seen[0].url.path.endswith("/bases")) == ("GET", True)
    assert dict(seen[0].url.params) == {}


def test_set_base_puts_the_base_id_and_returns_the_new_name() -> None:
    repo, seen = repository(replying({"base_name": "origin/main"}))
    assert run(repo.set_base(HOST, WORKTREE, "base-token")) == "origin/main"
    request = seen[0]
    assert (request.method, request.url.path.endswith(f"/{WORKTREE}/base")) == ("PUT", True)
    assert json.loads(request.content) == {"base_id": "base-token"}


# -- errors -------------------------------------------------------------------------------------

ROUTES: dict[str, Callable[[ChangesRepository], Awaitable[object]]] = {
    "summaries": lambda r: r.summaries(HOST, ["a"]),
    "changes": lambda r: r.changes(HOST, WORKTREE, "branch", None),
    "file_diff": lambda r: r.file_diff(HOST, WORKTREE, "f", None, False),
    "graph": lambda r: r.graph(HOST, WORKTREE, "all", None),
    "commit_changes": lambda r: r.commit_changes(HOST, WORKTREE, "id", None),
    "bases": lambda r: r.bases(HOST, WORKTREE, None),
    "set_base": lambda r: r.set_base(HOST, WORKTREE, "b"),
}


def failure(repo: ChangesRepository, route: str) -> ChangesFailure:
    with pytest.raises(ChangesFailure) as caught:
        run(ROUTES[route](repo))
    return caught.value


def envelope(code: str, **details: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {"code": code, "message": "m", "retryable": False}
    return {**doc, "details": details} if details else doc


# The mapping is shared by every route: each route checks the common replies,
# and `summaries` alone checks the rest.
COMMON_ERRORS = [
    (429, envelope("rate_limited", reason="busy", retry_after_s=3), {}, "busy", 3.0),
    (409, envelope("stale_revision", reason="stale_snapshot"), {}, "stale_snapshot", None),
    (404, envelope("not_found", reason="no_base"), {}, "no_base", None),
    (413, envelope("too_large", reason="too_large"), {}, "too_large", None),
    (403, envelope("unsupported_capability"), {}, "unsupported_capability", None),
    (404, envelope("not_found"), {}, "not_found", None),
    (403, envelope("forbidden", reason="expired"), {}, "expired", None),
    (401, envelope("unauthenticated"), {}, "unauthenticated", None),
]
SUMMARIES_ONLY_ERRORS = [
    (503, envelope("host_offline", reason="git_unavailable"), {}, "git_unavailable", None),
    (503, envelope("host_offline", reason="unsupported_config"), {}, "unsupported_config", None),
    (429, envelope("rate_limited", retry_after_s=1), {}, "rate_limited", 1.0),
    (429, envelope("rate_limited"), {"Retry-After": "7"}, "rate_limited", 7.0),
    (500, envelope("internal"), {}, "internal", None),
    # An unusable reason falls back to the code; an unusable delay is dropped.
    (409, envelope("stale_revision", reason=""), {}, "stale_revision", None),
    (409, envelope("stale_revision", reason=7), {}, "stale_revision", None),
    (429, envelope("rate_limited", retry_after_s=True), {}, "rate_limited", None),
    (429, envelope("rate_limited", retry_after_s="3"), {}, "rate_limited", None),
    (429, envelope("rate_limited", retry_after_s=0), {}, "rate_limited", 0.0),
    (429, envelope("rate_limited", retry_after_s=1.5), {}, "rate_limited", 1.5),
]
ERROR_CASES = [
    (route, *case)
    for route in ROUTES
    for case in (COMMON_ERRORS + SUMMARIES_ONLY_ERRORS if route == "summaries" else COMMON_ERRORS)
]


@pytest.mark.parametrize(("route", "status", "body", "headers", "reason", "delay"), ERROR_CASES)
def test_an_error_reply_becomes_a_failure_with_the_reason_first_then_the_code(
    route: str, status: int, body: dict[str, Any], headers: dict[str, str], reason: str, delay: Any
) -> None:
    repo, _ = repository(replying(body, status, headers))
    caught = failure(repo, route)
    assert (caught.reason, caught.retry_after_s) == (reason, delay)


@pytest.mark.parametrize("route", ROUTES)
def test_a_reply_that_is_not_an_envelope_is_incompatible(route: str) -> None:
    repo, _ = repository(lambda request: httpx.Response(502, text="<html>bad gateway</html>"))
    assert failure(repo, route).reason == "upstream_incompatible"


@pytest.mark.parametrize("route", ROUTES)
def test_an_unreachable_gateway_is_host_offline(route: str) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    repo, _ = repository(refuse)
    assert failure(repo, route).reason == "host_offline"


def test_a_transport_without_the_feature_reports_it_unsupported() -> None:
    class Minimal(GatewayTransport):
        async def me(self) -> dict[str, Any]:
            return {}

        async def hosts(self) -> dict[str, Any]:
            return {}

        async def snapshot(self, host_id: str) -> dict[str, Any]:
            return {}

        async def agents(self, host_id: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {}

        def stream(self, host_id: str, cursor: str) -> Any:  # pragma: no cover
            raise NotImplementedError

        async def submit_command(self, command: dict[str, Any]) -> dict[str, Any]:
            return {}

        async def command_status(self, host_id: str, command_id: str) -> Any:
            return None

        async def terminal_ticket(self, lease_id: str) -> dict[str, Any]:
            return {}

    repo = ChangesRepository(Minimal())
    for route in ROUTES:
        assert failure(repo, route).reason == "unsupported_capability"


# -- malformed replies --------------------------------------------------------------------------

BAD: dict[str, list[Any]] = {
    "summaries": [
        {},
        {"summaries": {}},
        {"summaries": [], "extra": 1},
        {"summaries": [{**summary_doc(), "extra": 1}]},
        {"summaries": [summary_doc(state="done")]},
        {"summaries": [{k: v for k, v in summary_doc().items() if k != "revision"}]},
        {"summaries": [summary_doc(as_of_ms="7")]},
        {"summaries": ["text"]},
    ],
    "changes": [
        {},
        {k: v for k, v in changes_doc(scope="working").items() if k != "partial"},
        changes_doc(scope="working"),  # another scope than asked for
        changes_doc(extra=1),
        changes_doc(snapshot=5),
        changes_doc(partial=1),
        changes_doc(as_of_ms=True),
        changes_doc(total_files="1"),
        changes_doc(commits="3"),
        changes_doc(branch=3),
        changes_doc(next=4),
        changes_doc(files={}),
        changes_doc(files=[{**entry_doc(), "extra": 1}]),
        changes_doc(files=[{**entry_doc(), "status": "X"}]),
    ],
    "file_diff": [
        {},
        diff_doc(extra=1),
        diff_doc(render="pending"),
        diff_doc(hunks="none"),
        diff_doc(next=3),
        {**diff_doc(), "hunks": [{"header": "@@"}]},
    ],
    "graph": [
        {},
        {**graph_page().to_dict(), "extra": 1},
        {**graph_page().to_dict(), "snapshot": 1},
        {**graph_page().to_dict(), "mode": "branch"},
        {**graph_page().to_dict(), "commits": [{"oid": "a"}]},
        {**graph_page().to_dict(), "commits": "none"},
        {**graph_page().to_dict(), "next": 5},
    ],
    "commit_changes": [
        {},
        commit_doc(extra=1),
        changes_doc(),  # a Branch reply is not a commit's
        commit_doc(files="none"),
        commit_doc(detached="no"),
    ],
    "bases": [
        {},
        {"candidates": [], "next": None, "extra": 1},
        {"candidates": [{"base_id": "b", "name": "n", "kind": "tag", "oid": "o"}], "next": None},
        {"candidates": "none", "next": None},
        {"candidates": [], "next": 5},
    ],
    "set_base": [{}, {"base_name": 3}, {"base_name": "a", "extra": 1}, {"base_name": None}],
}


@pytest.mark.parametrize(
    ("route", "body"), [(route, body) for route, docs in BAD.items() for body in docs]
)
def test_a_malformed_reply_is_rejected_as_incompatible(route: str, body: Any) -> None:
    repo, _ = repository(replying(body))
    caught = failure(repo, route)
    assert (caught.reason, caught.retry_after_s) == ("upstream_incompatible", None)


def test_a_reply_that_is_not_a_json_object_is_incompatible() -> None:
    repo, _ = repository(replying(["not", "an", "object"]))
    assert failure(repo, "summaries").reason == "upstream_incompatible"


def test_a_cancelled_request_is_not_turned_into_a_failure() -> None:
    async def scenario() -> None:
        gate = asyncio.Event()

        async def hold(request: httpx.Request) -> httpx.Response:
            await gate.wait()
            return httpx.Response(200, json={"summaries": []})

        async def token() -> str:
            return "t"

        client = httpx.AsyncClient(transport=httpx.MockTransport(hold))
        repo = ChangesRepository(HttpxGatewayTransport("https://gateway.test", token, client))
        task = asyncio.ensure_future(repo.summaries(HOST, []))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
