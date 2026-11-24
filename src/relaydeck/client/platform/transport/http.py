"""Adapt identity JSON requests to pooled HTTP and explicit unavailable-response results."""

from __future__ import annotations

from typing import Any

import httpx


class HttpxTransport:
    """Production HTTP seam (httpx, connection-pooled by the app)."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def post_json(
        self, url: str, body: dict[str, Any], headers: dict[str, str] | None = None
    ) -> tuple[int, Any]:
        try:
            response = await self._client.post(url, json=body, headers=headers, timeout=30.0)
        except httpx.HTTPError:
            return 503, {
                "code": "host_offline",
                "message": "Identity response unavailable. Start a new sign-in.",
            }
        try:
            payload: Any = response.json()
        except ValueError:
            payload = {}
        return response.status_code, payload
