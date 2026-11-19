"""Bind narrow router views to the application-owned live state."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, cast

from relaydeck.gateway.app.state import GatewayState
from relaydeck.gateway.features.access.api import (
    AuthorizationFlow,
    EnrollmentStore,
    HostStore,
    IdentityProvider,
    PairingStore,
    SessionStore,
    TicketStore,
)
from relaydeck.gateway.features.metadata.api import InventoryView, MetadataStreams
from relaydeck.gateway.features.notifications.api import (
    NotificationTransitions,
    ProviderPort,
    PushStore,
)
from relaydeck.gateway.features.pictures.api import PictureStore
from relaydeck.gateway.features.terminals.api import SocketRegistry
from relaydeck.gateway.features.usage_read.api import UsageService
from relaydeck.gateway.http.context import RouteCapabilities
from relaydeck.gateway.http.middleware.metrics import Metrics
from relaydeck.gateway.http.ports import (
    AgentsBackend,
    AgentsCapabilities,
    CommandsBackend,
    CommandsCapabilities,
    DevicesBackend,
    DevicesCapabilities,
    EnrollmentsCapabilities,
    EventsBackend,
    EventsCapabilities,
    GitAccessCapabilities,
    HealthCapabilities,
    HostsBackend,
    HostsCapabilities,
    IdentityBackend,
    IdentityCapabilities,
    Limit,
    MetricsCapabilities,
    NoticeSource,
    PairingCapabilities,
    PicturesCapabilities,
    SocketsBackend,
    SocketsCapabilities,
    TerminalsBackend,
    TerminalsCapabilities,
    UpdatesCapabilities,
    UsageBackend,
    UsageCapabilities,
)
from relaydeck.git.api import GitReadPort


class _Bound:
    def __init__(self, state: GatewayState) -> None:
        self._state = state


class _AgentsView(_Bound, AgentsCapabilities):
    @property
    def backend(self) -> AgentsBackend:
        return self._state.backend

    @property
    def cursor_secret(self) -> bytes:
        return self._state.cursor_secret

    @property
    def inventory(self) -> InventoryView:
        return self._state.inventory


class _CommandsView(_Bound, CommandsCapabilities):
    @property
    def backend(self) -> CommandsBackend:
        return self._state.backend

    @property
    def commands(self) -> Limit:
        return cast(Limit, self._state.commands)

    @property
    def now_ms(self) -> Callable[[], int]:
        return self._state.now_ms

    @property
    def sockets(self) -> SocketRegistry:
        return self._state.sockets


class _DevicesView(_Bound, DevicesCapabilities):
    @property
    def backend(self) -> DevicesBackend:
        return self._state.backend

    @property
    def metadata(self) -> MetadataStreams:
        return self._state.metadata

    @property
    def metrics(self) -> Metrics:
        return self._state.metrics

    @property
    def now_ms(self) -> Callable[[], int]:
        return self._state.now_ms

    @property
    def push(self) -> PushStore:
        return self._state.push

    @property
    def sessions(self) -> SessionStore:
        return self._state.sessions

    @property
    def sockets(self) -> SocketRegistry:
        return self._state.sockets


class _EnrollmentsView(_Bound, EnrollmentsCapabilities):
    @property
    def enrollments(self) -> EnrollmentStore:
        return self._state.enrollments

    @property
    def hosts(self) -> HostStore:
        return self._state.hosts

    @property
    def now_ms(self) -> Callable[[], int]:
        return self._state.now_ms

    @property
    def public_origin(self) -> str:
        return self._state.public_origin


class _EventsView(_Bound, EventsCapabilities):
    @property
    def backend(self) -> EventsBackend:
        return self._state.backend

    @property
    def cursor_secret(self) -> bytes:
        return self._state.cursor_secret

    @property
    def metadata(self) -> MetadataStreams:
        return self._state.metadata

    @property
    def now_ms(self) -> Callable[[], int]:
        return self._state.now_ms

    @property
    def sessions(self) -> SessionStore:
        return self._state.sessions

    @property
    def started(self) -> bool:
        return self._state.started


class _GitAccessView(_Bound, GitAccessCapabilities):
    @property
    def cursor_secret(self) -> bytes:
        return self._state.cursor_secret

    @property
    def git(self) -> GitReadPort | None:
        return self._state.git

    @property
    def now_ms(self) -> Callable[[], int]:
        return self._state.now_ms


class _HealthView(_Bound, HealthCapabilities):
    @property
    def hosts(self) -> HostStore:
        return self._state.hosts

    @property
    def notifications(self) -> NotificationTransitions:
        return self._state.notifications

    @property
    def push_provider(self) -> ProviderPort | None:
        return self._state.push_provider

    @property
    def sockets(self) -> SocketRegistry:
        return self._state.sockets

    @property
    def started(self) -> bool:
        return self._state.started


class _HostsView(_Bound, HostsCapabilities):
    @property
    def backend(self) -> HostsBackend:
        return self._state.backend

    @property
    def hosts(self) -> HostStore:
        return self._state.hosts


class _IdentityView(_Bound, IdentityCapabilities):
    @property
    def authorization(self) -> AuthorizationFlow:
        return self._state.authorization

    @property
    def authorization_slots(self) -> asyncio.Semaphore:
        return self._state.authorization_slots

    @property
    def backend(self) -> IdentityBackend:
        return self._state.backend

    @property
    def identity_provider(self) -> IdentityProvider | None:
        return self._state.identity_provider

    @property
    def metadata(self) -> MetadataStreams:
        return self._state.metadata

    @property
    def metrics(self) -> Metrics:
        return self._state.metrics

    @property
    def now_ms(self) -> Callable[[], int]:
        return self._state.now_ms

    @property
    def pending_codes(self) -> dict[str, dict[str, Any]]:
        return self._state.pending_codes

    @pending_codes.setter
    def pending_codes(self, value: dict[str, dict[str, Any]]) -> None:
        self._state.pending_codes = value

    @property
    def public_origin(self) -> str:
        return self._state.public_origin

    @property
    def push(self) -> PushStore:
        return self._state.push

    @property
    def sessions(self) -> SessionStore:
        return self._state.sessions

    @property
    def sockets(self) -> SocketRegistry:
        return self._state.sockets


class _MetricsView(_Bound, MetricsCapabilities):
    @property
    def backend(self) -> object:
        return self._state.backend

    @property
    def metadata(self) -> MetadataStreams:
        return self._state.metadata

    @property
    def metrics(self) -> Metrics:
        return self._state.metrics

    @property
    def sockets(self) -> SocketRegistry:
        return self._state.sockets

    @property
    def usage(self) -> UsageService | None:
        return self._state.usage

    @property
    def web_tabs(self) -> Callable[[], int] | None:
        return self._state.web_tabs


class _PairingView(_Bound, PairingCapabilities):
    @property
    def hosts(self) -> HostStore:
        return self._state.hosts

    @property
    def now_ms(self) -> Callable[[], int]:
        return self._state.now_ms

    @property
    def pairing(self) -> PairingStore | None:
        return self._state.pairing

    @property
    def public_origin(self) -> str:
        return self._state.public_origin

    @property
    def sessions(self) -> SessionStore:
        return self._state.sessions


class _PicturesView(_Bound, PicturesCapabilities):
    @property
    def hosts(self) -> HostStore:
        return self._state.hosts

    @property
    def now_ms(self) -> Callable[[], int]:
        return self._state.now_ms

    @property
    def pictures(self) -> PictureStore | None:
        return self._state.pictures


class _SocketsView(_Bound, SocketsCapabilities):
    @property
    def backend(self) -> SocketsBackend:
        return self._state.backend

    @property
    def hosts(self) -> HostStore:
        return self._state.hosts

    @property
    def input_bytes(self) -> Limit:
        return cast(Limit, self._state.input_bytes)

    @property
    def now_ms(self) -> Callable[[], int]:
        return self._state.now_ms

    @property
    def sockets(self) -> SocketRegistry:
        return self._state.sockets

    @property
    def tickets(self) -> TicketStore:
        return self._state.tickets


class _TerminalsView(_Bound, TerminalsCapabilities):
    @property
    def backend(self) -> TerminalsBackend:
        return self._state.backend

    @property
    def now_ms(self) -> Callable[[], int]:
        return self._state.now_ms

    @property
    def sockets(self) -> SocketRegistry:
        return self._state.sockets

    @property
    def tickets(self) -> TicketStore:
        return self._state.tickets


class _UpdatesView(_Bound, UpdatesCapabilities):
    @property
    def updates(self) -> NoticeSource:
        return cast(NoticeSource, self._state.updates)


class _UsageView(_Bound, UsageCapabilities):
    @property
    def backend(self) -> UsageBackend:
        return self._state.backend

    @property
    def cursor_secret(self) -> bytes:
        return self._state.cursor_secret

    @property
    def hosts(self) -> HostStore:
        return self._state.hosts

    @property
    def now_ms(self) -> Callable[[], int]:
        return self._state.now_ms

    @property
    def public_origin(self) -> str:
        return self._state.public_origin

    @property
    def usage(self) -> UsageService | None:
        return self._state.usage


def build_capabilities(state: GatewayState) -> RouteCapabilities:
    return RouteCapabilities(
        agents=_AgentsView(state),
        commands=_CommandsView(state),
        devices=_DevicesView(state),
        enrollments=_EnrollmentsView(state),
        events=_EventsView(state),
        git_access=_GitAccessView(state),
        health=_HealthView(state),
        hosts=_HostsView(state),
        identity=_IdentityView(state),
        metrics=_MetricsView(state),
        pairing=_PairingView(state),
        pictures=_PicturesView(state),
        sockets=_SocketsView(state),
        terminals=_TerminalsView(state),
        updates=_UpdatesView(state),
        usage=_UsageView(state),
    )
