"""Usage request parsing, authorization and HTTP result rendering."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from relaydeck.contracts import paths
from relaydeck.gateway.cursors import CursorError, decode_cursor
from relaydeck.gateway.features.usage_read.api import UsageRefusal, UsageResult
from relaydeck.gateway.ports import BackendError
from relaydeck.host.persistence.contracts import StorageUnavailable
from relaydeck.usage.api import (
    MAX_QUERY_DAYS,
    UsageQuery,
)

if TYPE_CHECKING:
    from relaydeck.gateway.features.access.api import Session
    from relaydeck.gateway.http.ports import UsageCapabilities


LOG = logging.getLogger(__name__)

NO_STORE = {"Cache-Control": "no-store"}
_CURSOR = re.compile(r"[A-Za-z0-9_-]{43}")


def failure(code: str, status: int) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status, headers=NO_STORE)


def parse_query(request: Request, host: str, timezone: str, now_ms: int) -> tuple[UsageQuery, str]:
    pairs = list(request.query_params.multi_items())
    values = dict(pairs)
    allowed = {"from", "to", "days", "space_id", "cursor", "bucket"}
    if len(values) != len(pairs) or set(values) - allowed:
        raise ValueError
    if "days" in values:
        days = values["days"]
        if {"from", "to"} & values.keys() or not re.fullmatch(r"[1-9][0-9]?", days):
            raise ValueError
        if not 1 <= int(days) <= MAX_QUERY_DAYS:
            raise ValueError
        last = datetime.fromtimestamp(now_ms / 1000, ZoneInfo(timezone)).date()
        first = last - timedelta(days=int(days) - 1)
        selector = "days:" + days
    else:
        first, last = date.fromisoformat(values["from"]), date.fromisoformat(values["to"])
        if first.isoformat() != values["from"] or last.isoformat() != values["to"]:
            raise ValueError
        selector = f"range:{first}:{last}"
    hourly = "bucket" in values
    if (
        not 0 <= (last - first).days < MAX_QUERY_DAYS
        or (
            "space_id" in values
            and (
                not 1 <= len(values["space_id"]) <= 256
                or any(ord(c) < 32 for c in values["space_id"])
            )
        )
        or ("cursor" in values and not _CURSOR.fullmatch(values["cursor"]))
        # Hourly buckets exist for exactly one host day, and only the value "hour" is accepted.
        or (hourly and (values["bucket"] != "hour" or first != last))
    ):
        raise ValueError
    if hourly:
        selector += ":hour"  # a binding of its own: an hourly cursor never serves a daily page
    return UsageQuery(
        host,
        first.isoformat(),
        last.isoformat(),
        values.get("space_id"),
        values.get("cursor"),
        hour=hourly,
    ), selector


def live_workspace_labels(state: UsageCapabilities, host_id: str) -> dict[str, str]:
    """The connector's inventory + newer bounded metadata; no filesystem inference."""
    try:
        snapshot = state.backend.snapshot(host_id)
        labels = {
            row["id"]: row["label"]
            for row in snapshot.get("workspaces", [])[:4096]
            if isinstance(row, dict)
            and isinstance(row.get("id"), str)
            and isinstance(row.get("label"), str)
        }
        after = 0
        try:
            cursor = decode_cursor(state.cursor_secret, snapshot.get("cursor", ""))
            if cursor.get("host_id") == host_id:
                after = int(cursor.get("sequence", 0))
        except (CursorError, TypeError, ValueError):
            pass
        epoch = snapshot.get("host", {}).get("epoch")
        for event in state.backend.events_after(host_id, after)[-4096:]:
            if event.get("host_id") != host_id or event.get("host_epoch") != epoch:
                continue
            resource = event.get("resource_id")
            if not isinstance(resource, str):
                continue
            if event.get("type") == "workspace.upserted":
                payload = event.get("payload", {})
                if isinstance(payload, dict) and isinstance(payload.get("label"), str):
                    labels[resource] = payload["label"]
            elif event.get("type") == "resource.removed":
                labels.pop(resource, None)
        return labels
    except BackendError:
        return {}


def page_response(result: UsageResult) -> Response:
    if isinstance(result, UsageRefusal):
        headers = dict(NO_STORE)
        if result.retry_after_s is not None:
            headers["Retry-After"] = str(result.retry_after_s)
        return JSONResponse({"error": result.code}, status_code=result.status, headers=headers)
    headers = dict(NO_STORE)
    if result.etag is not None:
        headers["ETag"] = result.etag
    return Response(
        result.payload,
        status_code=result.status,
        media_type="application/json" if result.status == 200 else None,
        headers=headers,
    )


def install(
    app: FastAPI,
    state: UsageCapabilities,
    current_session: Callable[[Request], Session | None],
    host_guard: Callable[[Session, str], JSONResponse | None],
) -> None:
    def authorized(request: Request, host: str) -> tuple[Session | None, JSONResponse | None]:
        try:
            session = current_session(request)
        except StorageUnavailable:
            return None, failure("unauthenticated", 503)
        if session is None:
            return None, failure("unauthenticated", 401)
        guard = host_guard(session, host)
        if guard is not None:
            return None, failure(
                "forbidden" if guard.status_code == 403 else "not_found", guard.status_code
            )
        record = state.hosts.record_for_host(host)
        if (
            record is None
            or record.account != session.account
            or not state.hosts.allowed(session.account, host)
        ):
            return None, failure("not_found", 404)
        return session, None

    @app.post(paths.USAGE_LIMITS_REFRESH, status_code=202)
    async def refresh_limits(request: Request, host_id: str) -> Response:
        """Start or join an on-demand allowance refresh after session, host and Origin checks.
        Keep the scheduler-owned attempt alive when a client disconnects.
        """
        origins = request.headers.getlist("origin")
        if origins and origins != [state.public_origin]:
            return failure("forbidden", 403)
        session, denied = authorized(request, host_id)
        if denied is not None:
            return denied
        if session is None:
            return failure("forbidden", 403)
        service = state.usage
        if service is None or service.host_id != host_id:
            return failure("usage_disabled", 404)
        try:
            raw = await request.body()
            if len(raw) > 1024:
                raise ValueError
            body = json.loads(raw)
            if set(body) != {"kind"} or not isinstance(body["kind"], str):
                raise ValueError
            kind = body["kind"]
            if not 0 < len(kind) <= 256 or any(ord(c) < 32 for c in kind):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            return failure("invalid_body", 400)
        if service.request_limits_refresh is None:
            return failure("not_configured", 404)
        decision = service.request_limits_refresh(kind)
        if decision.state == "not_configured":
            return failure("not_configured", 404)
        if decision.state == "cooldown":
            retry = decision.retry_after_s or 1
            return JSONResponse(
                {"state": "cooldown", "retry_after_s": retry},
                status_code=429,
                headers={**NO_STORE, "Retry-After": str(retry)},
            )
        return JSONResponse(
            {"state": decision.state, "attempt_id": decision.attempt_id},
            status_code=202,
            headers=NO_STORE,
        )

    @app.get(paths.USAGE)
    async def get_usage(request: Request, host_id: str) -> Response:
        session, denied = authorized(request, host_id)
        if denied is not None:
            return denied
        if session is None:
            return failure("forbidden", 403)
        service = state.usage
        if service is None or service.host_id != host_id:
            return failure("usage_disabled", 404)
        if service.on_usage_open is not None:
            # Production callback only sets a scheduler event: never do store/lock work
            # per poll in the shared pool. Auto-refresh is owned by the scheduler thread.
            try:
                service.on_usage_open()
            except Exception:
                LOG.warning("usage_auto_refresh_check_failed")
        try:
            query, selector = parse_query(request, host_id, service.config.timezone, state.now_ms())
        except (ValueError, KeyError, TypeError):
            return failure("invalid_query", 400)
        response = page_response(
            await service.page(
                query,
                session.account,
                session.device_id,
                selector=selector,
                if_none_match=request.headers.get("if-none-match"),
            )
        )
        # Revocation or a host ownership change during the off-loop query is authoritative.
        current, denied = authorized(request, host_id)
        if denied is not None:
            return denied
        if current is None or (current.account, current.device_id) != (
            session.account,
            session.device_id,
        ):
            return failure("unauthenticated", 401)
        return response
