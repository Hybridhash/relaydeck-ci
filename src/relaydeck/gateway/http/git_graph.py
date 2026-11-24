"""Graph pages from frozen, account-bound worktree contexts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from relaydeck.contracts import paths
from relaydeck.contracts.git import MAX_HISTORY, ChangesPage, GraphMode
from relaydeck.gateway.features.access.api import Session
from relaydeck.gateway.features.git_read.api import file_entry
from relaydeck.gateway.http.auth import HostReader
from relaydeck.gateway.http.errors import GatewayError, error_response
from relaydeck.gateway.http.git_access import NO_STORE, GitAccess
from relaydeck.git.api import GitReadPort, Snapshot, file_totals

FILES_PER_PAGE = 100


def _malformed() -> GatewayError:
    return GatewayError("forbidden", "invalid commit request", details={"reason": "malformed"})


async def _open_selection(
    access: GitAccess, found: GitReadPort, session: Session, host_id: str, key: str, commit_id: str
) -> tuple[Snapshot, int]:
    payload = access.opened("graph_commit", commit_id, session, host_id, w=key)
    revision, raw_mode, oid = payload.get("r"), payload.get("m"), payload.get("oid")
    if (
        not isinstance(revision, str)
        or not revision
        or raw_mode not in ("all", "branch")
        or not isinstance(oid, str)
        or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", oid) is None
    ):
        raise _malformed()
    mode: GraphMode = "all" if raw_mode == "all" else "branch"
    snap = await found.service.commit_snapshot(session.account, host_id, key, revision, mode, oid)
    return snap, 0


async def _continue_files(
    access: GitAccess,
    found: GitReadPort,
    session: Session,
    host_id: str,
    key: str,
    token: str,
    selection_key: str,
) -> tuple[Snapshot, int]:
    # Keep cached reads inside access.run for error mapping and the post-read owner check.
    payload = access.opened(
        "commit_files", token, session, host_id, w=key, s="commit", c=selection_key
    )
    revision, offset = payload.get("r"), payload.get("o")
    if (
        not isinstance(revision, str)
        or type(offset) is not int
        or offset < 0
        or offset % FILES_PER_PAGE
    ):
        raise _malformed()
    snap = found.service.cached(session.account, host_id, key, "commit", revision)
    if offset >= len(snap.rows):
        raise _malformed()
    return snap, offset


def build_graph_router(access: GitAccess) -> APIRouter:
    router = APIRouter()

    @router.get(paths.GIT_GRAPH)
    async def git_graph(
        host_id: str, worktree_id: str, request: Request, session: HostReader
    ) -> JSONResponse:
        found = access.port(session, host_id, "read")
        key = access.worktree_key(worktree_id, session, host_id)
        raw_mode = request.query_params.get("mode", "all")
        if raw_mode not in ("all", "branch") or set(request.query_params) - {"mode", "cursor"}:
            return error_response("forbidden", "invalid graph request")
        mode: GraphMode = "branch" if raw_mode == "branch" else "all"
        revision, offset = None, 0
        token = request.query_params.get("cursor")
        if token:
            payload = access.opened("graph", token, session, host_id, w=key, m=mode)
            revision, raw_offset = payload.get("r"), payload.get("o")
            if (
                not isinstance(revision, str)
                or type(raw_offset) is not int
                or not 0 <= raw_offset < MAX_HISTORY
            ):
                return error_response("forbidden", "invalid graph cursor")
            offset = raw_offset
        result = await access.run(
            request,
            host_id,
            session,
            found.service.graph(
                session.account, host_id, key, mode, revision=revision, offset=offset
            ),
            owner=found,
        )
        cursor = None
        if result.next_offset is not None:
            cursor = access.seal(
                "graph",
                session,
                host_id,
                w=key,
                m=mode,
                r=result.page.snapshot,
                o=result.next_offset,
            )
        commits = tuple(
            replace(
                commit,
                commit_id=access.seal(
                    "graph_commit",
                    session,
                    host_id,
                    w=key,
                    m=mode,
                    r=result.page.snapshot,
                    oid=commit.oid,
                ),
            )
            for commit in result.page.commits
        )
        return JSONResponse(
            replace(result.page, commits=commits, next=cursor).to_dict(), headers=NO_STORE
        )

    @router.get(paths.GIT_COMMIT_CHANGES)
    async def git_commit_changes(
        host_id: str, worktree_id: str, commit_id: str, request: Request, session: HostReader
    ) -> JSONResponse:
        found = access.port(session, host_id, "read")
        key = access.worktree_key(worktree_id, session, host_id)
        if set(request.query_params) - {"cursor"} or not 0 < len(commit_id) <= 8192:
            raise _malformed()
        token = request.query_params.get("cursor")
        if "cursor" in request.query_params and (
            not token or len(request.query_params.getlist("cursor")) != 1
        ):
            raise _malformed()
        selection_key = hashlib.sha256(commit_id.encode()).hexdigest()
        read = (
            _continue_files(access, found, session, host_id, key, token, selection_key)
            if token
            else _open_selection(access, found, session, host_id, key, commit_id)
        )
        snap, offset = await access.run(request, host_id, session, read, owner=found)
        files = tuple(
            file_entry(
                row,
                access.seal(
                    "file",
                    session,
                    host_id,
                    w=key,
                    s="commit",
                    r=snap.revision,
                    i=offset + n,
                ),
            )
            for n, row in enumerate(snap.rows[offset : offset + FILES_PER_PAGE])
        )
        more = offset + FILES_PER_PAGE < len(snap.rows)
        following = (
            access.seal(
                "commit_files",
                session,
                host_id,
                w=key,
                s="commit",
                r=snap.revision,
                c=selection_key,
                o=offset + FILES_PER_PAGE,
            )
            if more
            else None
        )
        additions, deletions = file_totals(snap.rows)
        doc = ChangesPage(
            scope="commit",
            snapshot=snap.revision,
            head=snap.head,
            base=snap.old,
            branch=None,
            detached=False,
            short_oid=snap.head[:12] if snap.head else None,
            additions=additions,
            deletions=deletions,
            base_name=None,
            commits=None,
            partial=snap.partial,
            as_of_ms=snap.as_of_ms,
            total_files=len(snap.rows),
            files=files,
            next=following,
        )
        return JSONResponse(doc.to_dict(), headers=NO_STORE)

    return router
