"""`GET /v1/updates`: the Mac's release notice for Settings. Read-only; it never fetches."""

import asyncio
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from relaydeck.contracts import paths
from relaydeck.contracts.schemas import validate
from relaydeck.gateway.features.access.api import Session
from relaydeck.gateway.http.auth import host_denial, require_scope
from relaydeck.gateway.http.helpers import NO_STORE
from relaydeck.gateway.http.ports import UpdatesCapabilities

HealthReader = Annotated[Session, Depends(require_scope("server.health"))]


def install(app: FastAPI, state: UpdatesCapabilities) -> None:
    @app.get(paths.UPDATES)
    async def get_updates(session: HealthReader) -> JSONResponse:
        service = state.updates
        # The health panel's audience, and only for the Mac this companion runs on.
        if (denied := host_denial(session, service.host_id)) is not None:
            raise denied
        document = await asyncio.to_thread(service.document)
        validate("relaydeck/gateway/v1/updates", document)
        return JSONResponse(document, headers=NO_STORE)
