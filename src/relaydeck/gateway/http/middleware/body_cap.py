"""Bound HTTP admission and body collection without timing out live streams."""

from __future__ import annotations

import asyncio
import json
import math
from typing import Literal

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from relaydeck.contracts import paths
from relaydeck.contracts.schemas import ERROR_STATUS, validate
from relaydeck.gateway.http.bounds import (
    BODY_RECEIVE_SECONDS,
    MAX_EVENT_STREAMS,
    MAX_HTTP_REQUESTS,
    MAX_JSON_BODY,
    MAX_WEBSOCKETS,
)


class _AdmissionPool:
    """Event-loop-owned, fail-fast admission: no waiter queue."""

    def __init__(self, limit: int) -> None:
        if type(limit) is not int or limit < 1:
            raise ValueError("admission limit must be positive")
        self.limit = limit
        self.active = 0

    def acquire(self) -> bool:
        if self.active >= self.limit:
            return False
        self.active += 1
        return True

    def release(self) -> None:
        self.active -= 1


async def _reject(
    scope: Scope,
    send: Send,
    code: Literal["too_large", "forbidden", "rate_limited"],
    message: str,
    reason: str,
) -> None:
    body = {
        "code": code,
        "message": message,
        "retryable": code == "rate_limited",
        "details": {"reason": reason},
    }
    validate("relaydeck/gateway/v1/error", body)
    payload = json.dumps(body).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode()),
        (b"cache-control", b"no-store"),
    ]
    if scope.get("http_version", "1.1") in ("1.0", "1.1"):
        headers.append((b"connection", b"close"))
    if code == "rate_limited":
        headers.append((b"retry-after", b"1"))
    await send({"type": "http.response.start", "status": ERROR_STATUS[code], "headers": headers})
    await send({"type": "http.response.body", "body": payload})


def _declared_length(scope: Scope) -> Literal["ok", "invalid", "large"]:
    values = [v for k, v in scope.get("headers", ()) if k.lower() == b"content-length"]
    if not values:
        return "ok"
    if len(values) != 1 or any(k.lower() == b"transfer-encoding" for k, _ in scope["headers"]):
        return "invalid"
    value = values[0]
    if not value or any(byte < 48 or byte > 57 for byte in value):
        return "invalid"
    value = value.lstrip(b"0") or b"0"
    maximum = str(MAX_JSON_BODY).encode()
    if len(value) > len(maximum) or (len(value) == len(maximum) and value > maximum):
        return "large"
    return "ok"


class BodyCapMiddleware:
    """Separate ordinary HTTP, SSE and WebSocket admission without a stream lifetime timeout.
    Apply body deadlines and retain the authenticated picture-upload route budget.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        http_limit: int = MAX_HTTP_REQUESTS,
        event_limit: int = MAX_EVENT_STREAMS,
        websocket_limit: int = MAX_WEBSOCKETS,
        body_timeout: float = BODY_RECEIVE_SECONDS,
    ) -> None:
        if not math.isfinite(body_timeout) or body_timeout <= 0:
            raise ValueError("body receive deadline must be positive")
        self._app = app
        self._http = _AdmissionPool(http_limit)
        self._events = _AdmissionPool(event_limit)
        self._websockets = _AdmissionPool(websocket_limit)
        self._body_timeout = body_timeout

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return
        if (
            kind == "http"
            and scope.get("method") == "PUT"
            and paths.PICTURE_CONTENT_RE.fullmatch(scope.get("path", ""))
        ):
            await self._app(scope, receive, send)
            return
        pool = self._websockets if kind == "websocket" else self._http
        if kind == "http" and scope.get("method") == "GET" and scope.get("path") == paths.EVENTS:
            pool = self._events
        if not pool.acquire():
            if kind == "websocket":
                # A close before accept becomes HTTP 403, which tunnel clients treat as fatal.
                if (await receive())["type"] == "websocket.connect":
                    await send({"type": "websocket.accept"})
                    await send({"type": "websocket.close", "code": 1013, "reason": "busy"})
            else:
                await _reject(scope, send, "rate_limited", "request capacity exhausted", "capacity")
            return
        try:
            if kind == "websocket":
                await self._app(scope, receive, send)
            else:
                await self._http_request(scope, receive, send)
        finally:
            pool.release()

    async def _http_request(self, scope: Scope, receive: Receive, send: Send) -> None:
        declared = _declared_length(scope)
        if declared == "invalid":
            await _reject(scope, send, "forbidden", "invalid body framing", "body_framing")
            return
        if declared == "large":
            await _reject(scope, send, "too_large", "body too large", "body_size")
            return
        body = bytearray()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._body_timeout
        disconnected = False
        oversized = False
        try:
            async with asyncio.timeout_at(deadline):
                chunks = 0
                while True:
                    if loop.time() >= deadline:
                        raise TimeoutError
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        disconnected = True
                        break
                    piece = message.get("body", b"")
                    if len(body) + len(piece) > MAX_JSON_BODY:
                        oversized = True
                        break
                    body.extend(piece)
                    if not message.get("more_body"):
                        break
                    chunks += 1
                    if chunks % 32 == 0:
                        await asyncio.sleep(0)
        except TimeoutError:
            await _reject(
                scope, send, "rate_limited", "body receive budget exhausted", "body_timeout"
            )
            return
        if oversized:
            await _reject(scope, send, "too_large", "body too large", "body_size")
            return
        if disconnected:
            await self._app(scope, _disconnected_receive, send)
            return
        full = bytes(body)
        del body, message, piece
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": full, "more_body": False}
            return await receive()

        await self._app(scope, replay, send)


async def _disconnected_receive() -> Message:
    return {"type": "http.disconnect"}
