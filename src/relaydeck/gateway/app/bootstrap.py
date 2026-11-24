"""Compose gateway routes, policy boundaries and owned background tasks. Protected operations
resolve their required credentials; health, login and pairing entry points use their own
anonymous or proof-based rules.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from relaydeck.gateway.app.bindings import (
    include_connector_routes,
    include_host_routes,
    include_routes,
    include_terminal_routes,
    include_terminal_socket,
)
from relaydeck.gateway.app.bucket_retirement import BucketRetirement
from relaydeck.gateway.app.capabilities import build_capabilities
from relaydeck.gateway.app.command_io import command_io
from relaydeck.gateway.app.lifespan import build_lifespan
from relaydeck.gateway.app.revocation import revoke_host_and_close
from relaydeck.gateway.app.state import GatewayState
from relaydeck.gateway.app.terminal_epochs import (
    publish_terminal_routes,
    release_lease_and_close,
    wake_terminal_routes,
)
from relaydeck.gateway.features.access.api import Session
from relaydeck.gateway.http.auth import durable_session_allowed, host_denial, resolve_session
from relaydeck.gateway.http.context import RouteContext
from relaydeck.gateway.http.enrollments import install_enrollments
from relaydeck.gateway.http.errors import error_response, install_error_handlers
from relaydeck.gateway.http.helpers import docs_settings
from relaydeck.gateway.http.helpers import utc_z as _utc_z
from relaydeck.gateway.http.middleware.body_cap import BodyCapMiddleware
from relaydeck.gateway.http.middleware.request_id import install_request_id
from relaydeck.gateway.http.observers import install_observers
from relaydeck.gateway.http.rate_limits import RateLimiter
from relaydeck.gateway.http.usage import install as install_usage
from relaydeck.gateway.ports import BackendError


def create_app(state: GatewayState) -> FastAPI:
    ownership = getattr(state.backend, "ownership", None)
    if ownership is not None:
        # A shortened lease deadline (reader handoff) must wake the epoch
        # reconciler so it recomputes its expiry-bounded sleep.
        ownership.on_deadline_shortened = wake_terminal_routes

    retirement = BucketRetirement(lambda: state.now_ms())
    for limiter in (state.commands, state.input_bytes):
        if isinstance(limiter, RateLimiter):
            retirement.register(limiter)
    capabilities = build_capabilities(state)
    docs = docs_settings(state.debug_docs)
    app = FastAPI(
        title="RelayDeck gateway",
        version="1.0.0",
        lifespan=build_lifespan(state, retirement),
        **docs,
    )
    app.state.relaydeck = state
    app.add_middleware(BodyCapMiddleware)

    install_request_id(app)
    install_observers(app, state.metrics)
    install_error_handlers(app)

    def session_allowed(session: Session) -> bool:
        return durable_session_allowed(state, session)

    def current_session(request: Request) -> Session | None:
        return resolve_session(state, request)

    def device_allowed(session: Session, device_id: str) -> bool:
        # Paired Console grants are not account-wide device administration.
        return not session.host_binding or device_id == session.device_id

    from relaydeck.gateway.http.pictures import install as install_pictures

    install_pictures(app, capabilities.pictures, current_session, session_allowed, error_response)
    if state.updates is not None:
        from relaydeck.gateway.http.updates import install as install_updates

        install_updates(app, capabilities.updates)

    async def publish_epochs(host_id: str) -> None:
        await publish_terminal_routes(state, host_id)

    def emit_command_update(command: dict[str, Any] | None) -> None:
        if command is None:
            return
        try:
            snapshot = state.backend.snapshot(str(command.get("host_id", "")))
            host_epoch = str(snapshot["host"]["epoch"])
        except (BackendError, KeyError):
            return
        state.backend.publish_command_updated(
            command, host_epoch, state.cursor_secret, state.now_ms()
        )

    def _host_guard(session: Any, host_id: str) -> JSONResponse | None:
        denied = host_denial(session, host_id)
        if denied is None:
            return None
        return error_response(denied.code, denied.message)

    ctx = RouteContext(
        routes=capabilities,
        register_limiter=retirement.register,
        command_io=lambda operation, *args: command_io(state.backend, operation, *args),
        revoke_host=lambda host_id: revoke_host_and_close(state, host_id),
        release_lease=lambda lease_id, account, device_id: release_lease_and_close(
            state, lease_id, account, device_id
        ),
        current_session=current_session,
        session_allowed=session_allowed,
        device_allowed=device_allowed,
        publish_epochs=publish_epochs,
        emit_command_update=emit_command_update,
        host_guard=_host_guard,
    )
    include_routes(app, ctx)

    install_enrollments(
        app,
        capabilities.enrollments,
        current_session,
        error_response=error_response,
        revoke_host_and_close=ctx.revoke_host,
        utc_z=_utc_z,
    )

    include_connector_routes(app, state, ctx)

    install_usage(app, capabilities.usage, current_session, _host_guard)
    include_host_routes(app, ctx)
    include_terminal_routes(app, ctx)

    include_terminal_socket(app, ctx)

    return app
