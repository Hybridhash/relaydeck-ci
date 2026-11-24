"""Streaming response that closes its producer when the stream is cancelled."""

from __future__ import annotations

import anyio
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send


class MetadataResponse(StreamingResponse):
    """Close the producer even when ASGI cancellation interrupts a yielded frame."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            close = getattr(self.body_iterator, "aclose", None)
            if close is not None:
                with anyio.CancelScope(shield=True):
                    await close()
