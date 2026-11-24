"""Register per-area gateway routers in explicit route precedence."""

from __future__ import annotations

from fastapi import FastAPI

import relaydeck.gateway.app.tunnel as tunnel_app
from relaydeck.gateway.app.state import GatewayState
from relaydeck.gateway.http import (
    agents,
    commands,
    devices,
    events,
    git,
    health,
    hosts,
    identity,
    metrics,
    pairing,
    sockets,
    terminals,
)
from relaydeck.gateway.http.context import RouteContext


def include_routes(app: FastAPI, ctx: RouteContext) -> None:
    app.include_router(health.build_router(ctx))
    app.include_router(pairing.build_router(ctx))
    app.include_router(identity.build_router(ctx))
    app.include_router(devices.build_router(ctx))
    app.include_router(metrics.build_router(ctx))


def include_host_routes(app: FastAPI, ctx: RouteContext) -> None:
    app.include_router(hosts.build_router(ctx))
    app.include_router(agents.build_router(ctx))
    app.include_router(events.build_router(ctx))
    app.include_router(commands.build_router(ctx))
    app.include_router(git.build_router(ctx))


def include_terminal_routes(app: FastAPI, ctx: RouteContext) -> None:
    app.include_router(terminals.build_router(ctx))


def include_connector_routes(app: FastAPI, state: GatewayState, ctx: RouteContext) -> None:
    app.include_router(tunnel_app.build_connector_router(state, ctx))


def include_terminal_socket(app: FastAPI, ctx: RouteContext) -> None:
    app.include_router(sockets.build_terminal_socket_router(ctx))
