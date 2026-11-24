"""Shared handles the per-area routers receive from `create_app`."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from relaydeck.gateway.http.ports import (
    AgentsCapabilities,
    CommandsCapabilities,
    DevicesCapabilities,
    EnrollmentsCapabilities,
    EventsCapabilities,
    GitAccessCapabilities,
    HealthCapabilities,
    HostsCapabilities,
    IdentityCapabilities,
    MetricsCapabilities,
    PairingCapabilities,
    PicturesCapabilities,
    SocketsCapabilities,
    TerminalsCapabilities,
    UpdatesCapabilities,
    UsageCapabilities,
)

if TYPE_CHECKING:
    from fastapi import Request
    from fastapi.responses import JSONResponse

    from relaydeck.gateway.features.access.api import Session
    from relaydeck.gateway.http.rate_limits import RateLimiter


@dataclass(frozen=True)
class RouteCapabilities:
    agents: AgentsCapabilities
    commands: CommandsCapabilities
    devices: DevicesCapabilities
    enrollments: EnrollmentsCapabilities
    events: EventsCapabilities
    git_access: GitAccessCapabilities
    health: HealthCapabilities
    hosts: HostsCapabilities
    identity: IdentityCapabilities
    metrics: MetricsCapabilities
    pairing: PairingCapabilities
    pictures: PicturesCapabilities
    sockets: SocketsCapabilities
    terminals: TerminalsCapabilities
    updates: UpdatesCapabilities
    usage: UsageCapabilities


@dataclass(frozen=True)
class RouteContext:
    routes: RouteCapabilities
    current_session: Callable[[Request], Session | None]
    session_allowed: Callable[[Session], bool]
    device_allowed: Callable[[Session, str], bool]
    publish_epochs: Callable[[str], Awaitable[None]]
    emit_command_update: Callable[[dict[str, Any] | None], None]
    host_guard: Callable[[Any, str], JSONResponse | None]
    command_io: Callable[..., Awaitable[Any]]
    revoke_host: Callable[[str], Awaitable[tuple[bool, int, int]]]
    release_lease: Callable[[str, str, str], Awaitable[None]]
    register_limiter: Callable[[RateLimiter], None]
