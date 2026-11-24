"""Typed usage results preserve exact response bytes, status and headers."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.responses import JSONResponse, Response

from relaydeck.gateway.features.usage_read.api import UsagePage, UsageRefusal, UsageService
from relaydeck.gateway.http.usage import page_response
from relaydeck.usage.api import UsageConfig, UsageQuery


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            UsagePage(b'{"as_of":1}', etag='"revision"'),
            Response(
                b'{"as_of":1}',
                media_type="application/json",
                headers={"Cache-Control": "no-store", "ETag": '"revision"'},
            ),
        ),
        (
            UsagePage(b"", 304, '"revision"'),
            Response(
                status_code=304,
                headers={"Cache-Control": "no-store", "ETag": '"revision"'},
            ),
        ),
        (
            UsagePage(b'{"cursor":"next"}'),
            Response(
                b'{"cursor":"next"}',
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            ),
        ),
        (
            UsageRefusal("invalid_cursor", 400),
            JSONResponse(
                {"error": "invalid_cursor"},
                status_code=400,
                headers={"Cache-Control": "no-store"},
            ),
        ),
        (
            UsageRefusal("usage_unavailable", 503),
            JSONResponse(
                {"error": "usage_unavailable"},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            ),
        ),
        (
            UsageRefusal("usage_busy", 503, 2),
            JSONResponse(
                {"error": "usage_busy"},
                status_code=503,
                headers={"Cache-Control": "no-store", "Retry-After": "2"},
            ),
        ),
    ],
)
def test_usage_boundary_preserves_the_wire(
    result: UsagePage | UsageRefusal, expected: Response
) -> None:
    response = page_response(result)
    assert response.status_code == expected.status_code
    assert response.body == expected.body
    assert response.raw_headers == expected.raw_headers


def test_unavailable_service_returns_a_typed_refusal(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = UsageService(None, UsageConfig("UTC", ()), "host", tmp_path, lambda: 1_000)
        try:
            result = await service.page(UsageQuery("host", "2026-01-01", "2026-01-01"), "a", "d")
            assert result == UsageRefusal("usage_unavailable", 503)
            assert not isinstance(result, Response)
        finally:
            await service.close()

    asyncio.run(scenario())
