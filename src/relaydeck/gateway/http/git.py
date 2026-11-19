"""Read-only changes in agents' worktrees: summaries, file lists, diffs, commits and base choice."""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from relaydeck.contracts import paths
from relaydeck.gateway.features.git_read.api import file_entry
from relaydeck.gateway.http.auth import HostReader
from relaydeck.gateway.http.context import RouteContext
from relaydeck.gateway.http.errors import error_response
from relaydeck.gateway.http.git_access import NO_STORE, GitAccess
from relaydeck.gateway.http.git_access import gateway_error as _gateway_error
from relaydeck.gateway.http.git_graph import build_graph_router
from relaydeck.git.api import (
    ChangesError,
    FileRecord,
    GitReadPort,
    Scope,
    Snapshot,
    display,
    file_totals,
)

MAX_AGENTS = 200
FILES_PER_PAGE = 100
COMMITS_PER_PAGE = 100
BASES_PER_PAGE = 100


def build_router(ctx: RouteContext) -> APIRouter:
    router = APIRouter()
    access = GitAccess(ctx)
    port, seal, opened = access.port, access.seal, access.opened
    worktree_key, run = access.worktree_key, access.run

    @router.post(paths.GIT_SUMMARIES)
    async def git_summaries(host_id: str, request: Request, session: HostReader) -> JSONResponse:
        found = port(session, host_id, "summaries")
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            return error_response("forbidden", "invalid summaries request")
        ids = (
            body.get("agent_ids") if isinstance(body, dict) and set(body) == {"agent_ids"} else None
        )
        if (
            not isinstance(ids, list)
            or len(ids) > MAX_AGENTS
            or not all(isinstance(i, str) and 0 < len(i) <= 128 for i in ids)
        ):
            return error_response("forbidden", "invalid summaries request")
        folders = found.folders(list(dict.fromkeys(ids)))
        summaries = await run(
            request, host_id, session, found.service.summaries(session.account, host_id, folders)
        )
        out = []
        for summary in summaries:
            doc = summary.to_dict()
            doc["worktree_id"] = (
                seal("wt", session, host_id, w=summary.worktree_id) if summary.worktree_id else ""
            )
            out.append(doc)
        return JSONResponse({"summaries": out}, headers=NO_STORE)

    @router.get(paths.GIT_CHANGES)
    async def git_changes(
        host_id: str, worktree_id: str, request: Request, session: HostReader
    ) -> JSONResponse:
        found = port(session, host_id, "read")
        key = worktree_key(worktree_id, session, host_id)
        raw_scope = request.query_params.get("scope", "working")
        if raw_scope not in ("working", "branch") or set(request.query_params) - {
            "scope",
            "cursor",
        }:
            return error_response("forbidden", "invalid changes request")
        scope: Scope = "branch" if raw_scope == "branch" else "working"
        token = request.query_params.get("cursor")
        offset = 0
        if token:
            payload = opened("files", token, session, host_id, w=key, s=scope)
            offset = int(payload.get("o", 0))
            snap = _cached(found, session.account, host_id, key, scope, str(payload.get("r")))
        else:
            snap = await run(
                request,
                host_id,
                session,
                found.service.snapshot(session.account, host_id, key, scope),
            )
        rows = snap.rows[offset : offset + FILES_PER_PAGE]
        additions, deletions = file_totals(snap.rows)
        files = [
            _entry(
                row, seal("file", session, host_id, w=key, s=scope, r=snap.revision, i=offset + n)
            )
            for n, row in enumerate(rows)
        ]
        more = offset + FILES_PER_PAGE < len(snap.rows)
        return JSONResponse(
            {
                "scope": scope,
                "snapshot": snap.revision,
                "head": snap.head,
                "base": snap.old if scope == "branch" else None,
                "branch": snap.branch,
                "detached": snap.branch is None and snap.head is not None,
                "short_oid": snap.head[:12] if snap.head else None,
                "additions": additions,
                "deletions": deletions,
                "base_name": snap.base_name,
                "commits": snap.commits if scope == "branch" else None,
                "partial": snap.partial,
                "as_of_ms": snap.as_of_ms,
                "total_files": len(snap.rows),
                "files": files,
                "next": seal(
                    "files",
                    session,
                    host_id,
                    w=key,
                    s=scope,
                    r=snap.revision,
                    o=offset + FILES_PER_PAGE,
                )
                if more
                else None,
            },
            headers=NO_STORE,
        )

    @router.get(paths.GIT_FILE_DIFF)
    async def git_file_diff(
        host_id: str, worktree_id: str, file_id: str, request: Request, session: HostReader
    ) -> JSONResponse:
        found = port(session, host_id, "diff")
        key = worktree_key(worktree_id, session, host_id)
        if set(request.query_params) - {"cursor", "load"}:
            return error_response("forbidden", "invalid diff request")
        file = opened("file", file_id, session, host_id, w=key)
        diff_key = (
            hashlib.sha256(file_id.encode()).hexdigest() if file.get("s") == "commit" else file_id
        )
        page = 0
        token = request.query_params.get("cursor")
        if token:
            page = int(opened("diff", token, session, host_id, w=key, f=diff_key).get("n", 0))
        snap = _cached(found, session.account, host_id, key, str(file.get("s")), str(file.get("r")))
        diff, total = await run(
            request,
            host_id,
            session,
            found.service.file_diff(
                snap,
                int(file.get("i", -1)),
                file_id,
                page,
                load=request.query_params.get("load") == "1",
            ),
            owner=found,
        )
        doc = diff.to_dict()
        if page + 1 < total:
            doc["next"] = seal("diff", session, host_id, w=key, f=diff_key, n=page + 1)
        return JSONResponse(doc, headers=NO_STORE)

    @router.get(paths.GIT_COMMITS)
    async def git_commits(
        host_id: str, worktree_id: str, request: Request, session: HostReader
    ) -> JSONResponse:
        found = port(session, host_id, "read")
        key = worktree_key(worktree_id, session, host_id)
        if set(request.query_params) - {"cursor"}:
            return error_response("forbidden", "invalid commits request")
        token = request.query_params.get("cursor")
        if token:
            payload = opened("commits", token, session, host_id, w=key)
            snap = _cached(found, session.account, host_id, key, "branch", str(payload.get("r")))
            skip = int(payload.get("o", 0))
        else:
            snap = await run(
                request,
                host_id,
                session,
                found.service.snapshot(session.account, host_id, key, "branch"),
            )
            skip = 0
        commits = await run(
            request, host_id, session, found.service.commits(snap, skip, COMMITS_PER_PAGE)
        )
        more = skip + COMMITS_PER_PAGE < snap.commits
        return JSONResponse(
            {
                "snapshot": snap.revision,
                "commits": [c.to_dict() for c in commits],
                "next": seal(
                    "commits", session, host_id, w=key, r=snap.revision, o=skip + COMMITS_PER_PAGE
                )
                if more
                else None,
            },
            headers=NO_STORE,
        )

    router.include_router(build_graph_router(access))

    @router.get(paths.GIT_BASES)
    async def git_bases(
        host_id: str, worktree_id: str, request: Request, session: HostReader
    ) -> JSONResponse:
        found = port(session, host_id, "read")
        key = worktree_key(worktree_id, session, host_id)
        if set(request.query_params) - {"cursor"}:
            return error_response("forbidden", "invalid bases request")
        offset = 0
        token = request.query_params.get("cursor")
        if token:
            offset = int(opened("bases", token, session, host_id, w=key).get("o", 0))
        candidates = await run(
            request, host_id, session, found.service.bases(session.account, host_id, key)
        )
        page = candidates[offset : offset + BASES_PER_PAGE]
        more = offset + BASES_PER_PAGE < len(candidates)
        return JSONResponse(
            {
                "candidates": [
                    {
                        # The default is chosen as "follow the default", not pinned to its branch.
                        "base_id": seal(
                            "base",
                            session,
                            host_id,
                            w=key,
                            ref="" if kind == "remote_default" else ref,
                            n=_short(ref),
                        ),
                        "name": _short(ref),
                        "kind": kind,
                        "oid": oid,
                    }
                    for ref, kind, oid in page
                ],
                "next": seal("bases", session, host_id, w=key, o=offset + BASES_PER_PAGE)
                if more
                else None,
            },
            headers=NO_STORE,
        )

    @router.put(paths.GIT_BASE)
    async def git_set_base(
        host_id: str, worktree_id: str, request: Request, session: HostReader
    ) -> JSONResponse:
        found = port(session, host_id, "base")
        key = worktree_key(worktree_id, session, host_id)
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            return error_response("forbidden", "invalid base")
        token = body.get("base_id") if isinstance(body, dict) and set(body) == {"base_id"} else None
        if not isinstance(token, str):
            return error_response("forbidden", "invalid base")
        chosen = opened("base", token, session, host_id, w=key)
        ref = chosen.get("ref")
        name = chosen.get("n") or (_short(ref) if isinstance(ref, str) and ref else None)
        if not isinstance(ref, str) or not isinstance(name, str):
            return error_response("forbidden", "invalid base")
        await run(
            request, host_id, session, found.service.set_base(session.account, host_id, key, ref)
        )
        return JSONResponse({"base_name": name}, headers=NO_STORE)

    return router


def _cached(
    found: GitReadPort, account: str, host: str, key: str, scope: str, revision: str
) -> Snapshot:
    try:
        return found.service.cached(account, host, key, scope, revision)
    except ChangesError as exc:
        raise _gateway_error(exc) from None


def _short(ref: str) -> str:
    return display(ref.removeprefix("refs/heads/").removeprefix("refs/remotes/").encode())


def _entry(row: FileRecord, file_id: str) -> dict[str, Any]:
    return file_entry(row, file_id).to_dict()
