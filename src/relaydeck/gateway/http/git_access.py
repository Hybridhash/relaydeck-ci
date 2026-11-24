"""Shared authorization, signed identities and error handling for read-only changes."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

from fastapi import Request

from relaydeck.gateway.cursors import CursorError, decode_cursor, encode_cursor
from relaydeck.gateway.features.git_read.api import CURSOR_TTL_MS
from relaydeck.gateway.http.auth import host_denial, scope_denial
from relaydeck.gateway.http.context import RouteContext
from relaydeck.gateway.http.errors import GatewayError
from relaydeck.gateway.http.rate_limits import RateLimiter, retry_details
from relaydeck.git.api import ChangesError, GitReadPort

NO_STORE = {"Cache-Control": "no-store"}
_ERRORS: dict[str, tuple[str, bool]] = {
    "stale_snapshot": ("stale_revision", False),
    "busy": ("rate_limited", True),
    "too_large": ("too_large", False),
    "not_found": ("not_found", False),
    "no_base": ("not_found", False),
}


def gateway_error(exc: ChangesError) -> GatewayError:
    code, retryable = _ERRORS.get(exc.code, ("host_offline", False))
    details: dict[str, Any] = {"reason": exc.code}
    if exc.retry_after_s is not None:
        details["retry_after_s"] = exc.retry_after_s
    return GatewayError(code, "changes unavailable", retryable, details=details)


class GitAccess:
    def __init__(self, ctx: RouteContext) -> None:
        self.ctx = ctx
        self.limits = {
            "summaries": RateLimiter(capacity=5, refill_per_ms=1 / 1000),
            "diff": RateLimiter(capacity=10, refill_per_ms=5 / 1000),
            "read": RateLimiter(capacity=5, refill_per_ms=2 / 1000),
            "base": RateLimiter(capacity=2, refill_per_ms=1 / 1000),
        }
        for limiter in self.limits.values():
            ctx.register_limiter(limiter)

    def port(self, session: Any, host_id: str, group: str) -> GitReadPort:
        if (denied := scope_denial(session, "terminal.view")) is not None:
            raise denied
        state = self.ctx.routes.git_access
        found = state.git
        if found is None or found.host_id != host_id:
            raise GatewayError("not_found", "changes are not available for this host")
        now = state.now_ms()
        for key in (f"{session.account}:{host_id}", f"host:{host_id}"):
            if not self.limits[group].check(f"{group}:{key}", now):
                raise GatewayError(
                    "rate_limited",
                    "too many change requests",
                    True,
                    details=retry_details(self.limits[group], f"{group}:{key}", now) or None,
                )
        return found

    def recheck(
        self, request: Request, host_id: str, session: Any, owner: GitReadPort | None = None
    ) -> None:
        current = self.ctx.current_session(request)
        if (
            current is None
            or (current.account, current.device_id) != (session.account, session.device_id)
            or host_denial(current, host_id) is not None
            or scope_denial(current, "terminal.view") is not None
            or owner is not None
            and self.ctx.routes.git_access.git is not owner
        ):
            raise GatewayError("unauthenticated", "session changed")

    def seal(self, purpose: str, session: Any, host_id: str, **fields: Any) -> str:
        state = self.ctx.routes.git_access
        payload = {"p": purpose, "a": session.account, "h": host_id, **fields}
        if purpose != "wt":
            payload["x"] = state.now_ms() + CURSOR_TTL_MS
        return encode_cursor(state.cursor_secret, payload)

    def opened(
        self, purpose: str, token: str, session: Any, host_id: str, **bound: Any
    ) -> dict[str, Any]:
        try:
            payload = decode_cursor(self.ctx.routes.git_access.cursor_secret, token)
        except CursorError:
            raise GatewayError("forbidden", "invalid id", details={"reason": "malformed"}) from None
        expected = {"p": purpose, "a": session.account, "h": host_id, **bound}
        if any(payload.get(key) != value for key, value in expected.items()):
            raise GatewayError("forbidden", "invalid id", details={"reason": "mismatch"})
        if purpose != "wt" and (
            type(payload.get("x")) is not int or payload["x"] < self.ctx.routes.git_access.now_ms()
        ):
            raise GatewayError("forbidden", "invalid id", details={"reason": "expired"})
        return payload

    def worktree_key(self, token: str, session: Any, host_id: str) -> str:
        key = self.opened("wt", token, session, host_id).get("w")
        if not isinstance(key, str):
            raise GatewayError("forbidden", "invalid id", details={"reason": "malformed"})
        return key

    async def run[T](
        self,
        request: Request,
        host_id: str,
        session: Any,
        call: Awaitable[T],
        *,
        owner: GitReadPort | None = None,
    ) -> T:
        try:
            result = await call
        except ChangesError as exc:
            raise gateway_error(exc) from None
        self.recheck(request, host_id, session, owner)
        return result
