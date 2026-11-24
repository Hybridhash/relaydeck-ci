"""Live service handles, registries and clocks shared by gateway composition."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from relaydeck.gateway.app.ports import Backend
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
from relaydeck.gateway.features.notifications import api as sqlite_store_adapters
from relaydeck.gateway.features.notifications.api import (
    NotificationTransitions,
    ProviderPort,
    Status,
    push_io,
)
from relaydeck.gateway.features.terminals.api import SocketRegistry
from relaydeck.gateway.features.usage_read.api import UsageService
from relaydeck.gateway.http.middleware.metrics import Metrics
from relaydeck.gateway.http.rate_limits import command_limiter, input_limiter
from relaydeck.git.api import GitReadPort
from relaydeck.host.concurrency.offload import run_blocking

if TYPE_CHECKING:
    from relaydeck.gateway.features.notifications.api import PushWorker
    from relaydeck.gateway.features.pictures.api import PictureStore


@dataclass
class GatewayState:
    backend: Backend
    sessions: SessionStore = field(default_factory=SessionStore)
    cursor_secret: bytes = b""
    now_ms: Callable[[], int] = field(default=lambda: int(time.time() * 1000))
    commands: Any = field(default_factory=command_limiter)
    input_bytes: Any = field(default_factory=input_limiter)
    pending_codes: dict[str, dict[str, Any]] = field(default_factory=dict)
    authorization: AuthorizationFlow = field(default_factory=AuthorizationFlow)
    identity_provider: IdentityProvider | None = None
    pairing: PairingStore | None = None
    pictures: PictureStore | None = None  # Mac companion only; no image store on cloud gateways
    updates: Any = None  # Mac companion only: the release notice; no route without it
    usage: UsageService | None = None
    git: GitReadPort | None = None  # Mac companion only: read-only agent changes
    authorization_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(8))
    hosts: HostStore = field(default_factory=HostStore)
    enrollments: EnrollmentStore = field(default_factory=EnrollmentStore)
    tickets: TicketStore = field(default_factory=TicketStore)
    sockets: SocketRegistry = field(default_factory=SocketRegistry)
    public_origin: str = ""
    # in-memory by default (tests inject a file-backed store).
    push: sqlite_store_adapters.PushStore = field(default_factory=sqlite_store_adapters.PushStore)
    push_provider: ProviderPort | None = None
    push_worker: PushWorker | None = None  # set by the lifespan while serving
    # queues browser/home-screen messages for the same eligible devices (set by runtime).
    web_push_sink: Callable[[str, frozenset[str], str, str, str, str], None] | None = None
    revoke_spool: str = ""
    revoke_poll_s: float = 5.0
    inventory: InventoryView = field(default_factory=InventoryView)
    metadata: MetadataStreams = field(default_factory=MetadataStreams)
    notifications: NotificationTransitions = field(default_factory=NotificationTransitions)
    debug_docs: bool = False  # serve /docs, /redoc and /openapi.json (off in production)
    started: bool = False  # set by the lifespan while the app is serving
    metrics: Metrics = field(default_factory=Metrics)
    web_tabs: Callable[[], int] | None = None  # the web app's live tab count (set by serve.py)

    def push_delivery_allowed(self, row: dict[str, Any]) -> bool:
        resolved = self.push.resolve_ref(str(row["host_ref"]))
        if resolved is None or resolved[0] != "host":
            return False
        host, account = resolved[1], str(row["account"])
        record = self.hosts.record_for_host(host)
        if record is None or record.account != account or not self.hosts.allowed(account, host):
            return False
        device = str(row["device_id"])
        # a category the device turned off is never sent, even if already queued.
        return device in self.sessions.notification_devices(
            account, host, self.now_ms(), owned=True
        ) and self.push.category_allowed(account, device, str(row["category"]))

    def _wake_push_worker(self) -> None:
        if self.push_worker is not None:
            self.push_worker.wake()

    def emit_notification(self, status: Status, category: str) -> None:
        record = self.hosts.record_for_host(status.host)
        if (
            record is None
            or record.account != status.account
            or not self.hosts.allowed(status.account, status.host)
        ):
            return
        devices = self.sessions.notification_devices(
            status.account, status.host, self.now_ms(), owned=True
        )
        if self.web_push_sink is not None and devices:
            self.web_push_sink(
                status.account,
                devices,
                status.event_id,
                category,
                status.host,
                status.session,
            )
        if devices:
            self.push.notify(
                account=status.account,
                account_hosts=frozenset({status.host}),
                host_id=status.host,
                session_id=status.session,
                event_id=status.event_id,
                category=category,
                now=self.now_ms(),
                device_ids=devices,
            )
            self._wake_push_worker()

    async def emit_notification_async(self, status: Status, category: str) -> None:
        record = self.hosts.record_for_host(status.host)
        if (
            record is None
            or record.account != status.account
            or not self.hosts.allowed(status.account, status.host)
        ):
            return
        devices = self.sessions.notification_devices(
            status.account, status.host, self.now_ms(), owned=True
        )
        if self.web_push_sink is not None and devices:
            await run_blocking(
                self.web_push_sink,
                status.account,
                devices,
                status.event_id,
                category,
                status.host,
                status.session,
            )
        if devices:
            await push_io(
                self.push,
                "notify",
                account=status.account,
                account_hosts=frozenset({status.host}),
                host_id=status.host,
                session_id=status.session,
                event_id=status.event_id,
                category=category,
                now=self.now_ms(),
                device_ids=devices,
            )
            self._wake_push_worker()
