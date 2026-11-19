"""A request ID on every HTTP request: echoed to the client and attached to log lines."""

from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import FastAPI

from relaydeck.host.observability.request_context import (
    RequestIdFilter as RequestIdFilter,
)
from relaydeck.host.observability.request_context import (
    current,
)
from relaydeck.host.observability.request_context import (
    current_request_id as current_request_id,
)

HEADER = "x-request-id"
SCOPE_KEY = "relaydeck.request_id"
_VALID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


class RequestIdMiddleware:
    """Pure ASGI, HTTP only. An incoming ID is kept only if it is short and plain."""

    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> Any:
        if scope["type"] != "http":
            return await self._app(scope, receive, send)
        incoming = dict(scope.get("headers", [])).get(HEADER.encode(), b"").decode("latin-1")
        request_id = incoming if _VALID.fullmatch(incoming) else uuid.uuid4().hex
        scope[SCOPE_KEY] = request_id
        token = current.set(request_id)

        async def send_with_id(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"
                ]
                headers.append((HEADER.encode(), request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            return await self._app(scope, receive, send_with_id)
        finally:
            current.reset(token)


def install_request_id(app: FastAPI) -> None:
    app.add_middleware(RequestIdMiddleware)
