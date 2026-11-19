"""Render typed usage results at the HTTP boundary for wire assertions."""

from __future__ import annotations

from typing import Any

from fastapi.responses import Response

from relaydeck.gateway.features.usage_read.api import UsageService
from relaydeck.gateway.http.usage import page_response


async def wire_page(service: UsageService, *args: Any, **kwargs: Any) -> Response:
    return page_response(await service.page(*args, **kwargs))
