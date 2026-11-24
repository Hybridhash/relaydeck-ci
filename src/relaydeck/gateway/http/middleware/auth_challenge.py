"""Applicable bearer challenges for protected API responses."""

from __future__ import annotations

from typing import Any

from relaydeck.contracts.paths import is_api_path

BEARER_AUTH_KEY = "relaydeck.bearer_auth"


class BearerChallengeMiddleware:
    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> Any:
        path = scope.get("path", "")
        if scope["type"] != "http" or not is_api_path(path):
            return await self._app(scope, receive, send)

        async def send_challenged(message: dict[str, Any]) -> None:
            if (
                message["type"] == "http.response.start"
                and message["status"] == 401
                and scope.get(BEARER_AUTH_KEY) is True
            ):
                headers = list(message.get("headers", []))
                if not any(
                    key.lower() == b"www-authenticate"
                    and value.lower().split(b" ", 1)[0] == b"bearer"
                    for key, value in headers
                ):
                    headers.append((b"www-authenticate", b"Bearer"))
                message = {**message, "headers": headers}
            await send(message)

        return await self._app(scope, receive, send_challenged)
