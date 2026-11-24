"""Grant and state builders for terminal sockets."""

from __future__ import annotations

from typing import Any

from fastapi.routing import APIRoute, APIWebSocketRoute

from relaydeck.devtools.fakes.gateway import FakeBackend
from relaydeck.gateway.app.bootstrap import create_app
from relaydeck.gateway.app.state import GatewayState
from relaydeck.gateway.features.access.identity import TicketGrant

CAPS = frozenset(
    {
        "sessions.read",
        "terminal.view",
        "terminal.control",
        "agents.prompt",
        "agents.start",
        "shells.create",
        "terminal.attach",
    }
)


def _state() -> tuple[GatewayState, FakeBackend, Any]:
    backend = FakeBackend(now_ms=lambda: 1_000_000)
    state = GatewayState(backend=backend, cursor_secret=b"x" * 32, now_ms=lambda: 1_000_000)
    app = create_app(state)
    endpoint = next(
        r.endpoint
        for r in app.routes
        if isinstance(r, APIRoute | APIWebSocketRoute) and r.path == "/v1/terminal/{lease_id}"
    )
    return state, backend, endpoint


def _grant(lease_id: str, caps: frozenset[str] = CAPS) -> TicketGrant:
    return TicketGrant("acct-1", "dev-1", caps, frozenset({"home-mac"}), lease_id, "home-mac")
