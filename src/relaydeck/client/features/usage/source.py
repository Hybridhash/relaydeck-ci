"""The gateway adapter for the usage report. Maps the transport's usage call onto the feed's
fetch, and its errors onto the fixed usage codes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from relaydeck.client.features.usage.api import (
    RefreshOutcome,
    UsageHttpError,
    UsageQuery,
)
from relaydeck.client.ports.transport import TransportError


class UsageSource(Protocol):
    async def usage(
        self,
        host_id: str,
        *,
        days: int | None = None,
        start: str | None = None,
        end: str | None = None,
        space_id: str | None = None,
        cursor: str | None = None,
        bucket: str | None = None,
        if_none_match: str | None = None,
    ) -> Mapping[str, object]: ...

    async def refresh_limits(self, host_id: str, kind: str) -> RefreshOutcome: ...


class GatewayUsageFetch:
    """`fetch(query) -> document` over a gateway transport, plus `refresh_limits(host, kind)`."""

    def __init__(self, transport: UsageSource) -> None:
        self._transport = transport

    async def __call__(self, query: UsageQuery) -> Mapping[str, object]:
        transport = self._transport
        if not callable(getattr(transport, "usage", None)):
            raise UsageHttpError(0, "usage_unavailable")  # a transport without the usage read
        # The bucket is sent only for an hourly query, so a transport without it keeps working
        # daily.
        bucket = {"bucket": "hour"} if query.hour else {}
        conditional = {"if_none_match": query.report_id} if query.report_id else {}
        try:
            return await transport.usage(
                query.host_id,
                days=query.days,
                start=query.start,
                end=query.end,
                space_id=query.space_id,
                cursor=query.cursor,
                **bucket,
                **conditional,
            )
        except TransportError as error:
            raise UsageHttpError(0, error.code) from error

    async def refresh_limits(self, host_id: str, kind: str) -> RefreshOutcome:
        return await self._transport.refresh_limits(host_id, kind)
