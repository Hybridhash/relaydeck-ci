"""Compose and close the companion resource owners."""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable, Coroutine
from contextlib import suppress
from pathlib import Path
from typing import Any

from relaydeck.companion.app import herdr_retry
from relaydeck.companion.app.bindings import (
    ChangesBinding,
    StorageBinding,
    UpdatesBinding,
    UsageBinding,
    WebPushBinding,
)
from relaydeck.companion.app.herdr_link import HerdrLink
from relaydeck.companion.app.hosted_web.binding import HostedWeb
from relaydeck.companion.config import CompanionConfig
from relaydeck.connector.link import TunnelTransport
from relaydeck.gateway.app.bootstrap import create_app
from relaydeck.gateway.app.config import ServeOptions, build_serve_state
from relaydeck.gateway.features.access.api import SessionStore
from relaydeck.host.files.private import private_dir
from relaydeck.host.persistence.contracts import (
    KeyProvider,
    KeyUnavailable,
    StorageStatus,
    StorageUnavailable,
)

LOG = logging.getLogger(__name__)


class CompanionRuntime:
    """All resources are locally owned; Herdr and agents are never stopped."""

    def __init__(
        self,
        config: CompanionConfig,
        *,
        connect_herdr: bool = False,
        fixture: bool = False,
        server_factory: Callable[[Any], Any] | None = None,
        route_factory: Callable[[], Any] | None = None,
        source_factory: Callable[[], Any] | None = None,
        opener: Callable[[], Coroutine[Any, Any, TunnelTransport]] | None = None,
        durable_signins: bool | None = None,
        migrate_cookie_secret: bool | None = None,
        setup_persistence: bool | None = None,
        invalidate_restored_signins: bool | None = None,
        persistence_provider: KeyProvider | None = None,
        config_path: Path | None = None,
    ) -> None:
        self.config = config
        self.fixture = fixture
        self.gateway: Any = None
        self.stop: asyncio.Event | None = None
        self.storage = StorageBinding(
            config,
            gateway=lambda: self.gateway,
            stop=lambda: self.stop,
            durable_signins=durable_signins,
            migrate_cookie_secret=migrate_cookie_secret,
            setup_persistence=setup_persistence,
            invalidate_restored_signins=invalidate_restored_signins,
            persistence_provider=persistence_provider,
        )
        self.herdr = HerdrLink(
            config,
            gateway=lambda: self.gateway,
            stop=lambda: self.stop,
            connect_herdr=connect_herdr,
            fixture=fixture,
            source_factory=source_factory,
            opener=opener,
        )
        self.usage = UsageBinding(
            config,
            gateway=lambda: self.gateway,
            source=lambda: self.herdr.source,
            herdr_binary=HerdrLink._herdr_binary,
        )
        self.changes = ChangesBinding(
            config,
            gateway=lambda: self.gateway,
            source=lambda: self.herdr.source,
        )
        self.updates = UpdatesBinding(
            config,
            gateway=lambda: self.gateway,
            config_path=config_path,
            fixture=fixture,
        )
        self.web = HostedWeb(
            config,
            gateway=lambda: self.gateway,
            persistence=lambda: self.storage.persistence,
            server_factory=server_factory,
            route_factory=route_factory,
            setup_report=self.setup_report,
            request_shutdown=self._request_shutdown,
            serve_owned=self._serve_owned,
        )
        self.web_push = WebPushBinding(
            config,
            gateway=lambda: self.gateway,
            stop=lambda: self.stop,
            web_store=lambda: self.web.web_store,
        )

    async def start(self, stop: asyncio.Event) -> None:
        self.stop = stop
        cookie, cursor = await asyncio.to_thread(self.storage._storage_secrets)
        private_dir(self.config.state_dir)
        private_dir(self.config.state_dir / "revoke")
        self.gateway = build_serve_state(
            ServeOptions(
                host=self.config.bind_host,
                port=self.config.port,
                origin=self.config.advertised_origin,
                journal_path=str(self.config.state_dir / "gateway-journal.db"),
                push_path=str(self.config.state_dir / "push.db"),
                cursor_secret=cursor,
                log_config="",
                ephemeral_secret=False,
                enrollment_store=str(self.config.state_dir / "enrollments.json"),
                revoke_spool=str(self.config.state_dir / "revoke"),
                host_store=str(self.config.state_dir / "hosts.json"),
                pairing_store=str(self.config.state_dir / "pairing.json"),
                fcm_credentials=str(self.config.fcm_credentials)
                if self.config.fcm_credentials
                else "",
                identity_config=str(self.config.identity_config)
                if self.config.identity_config
                else "",
            )
        )
        if self.storage.persistence is not None:
            provider = self.storage.persistence_provider
            assert provider is not None  # noqa: S101
            try:
                self.gateway.sessions = SessionStore(
                    persistence=self.storage.persistence,
                    origin=self.config.advertised_origin,
                    generation_reader=lambda: provider.restore_generation("authentication").value,
                )
            except (KeyUnavailable, StorageUnavailable):
                await asyncio.to_thread(self.storage.persistence.close)
                self.storage.persistence = None
                self.storage.persistence_status = StorageStatus(False, "keys_unavailable")
                cookie = secrets.token_bytes(32)
            else:
                self.storage.persistence_task = asyncio.create_task(
                    self.storage._maintain_storage()
                )
        from relaydeck.gateway.features.pictures.api import PictureStore

        self.gateway.pictures = PictureStore(host_id=self.config.host_id)
        self.updates._start_updates(stop)
        if self.config.usage is not None:
            await asyncio.to_thread(self.usage._prepare_usage)
            # Synchronous on this thread, with no awaits: startup cancellation cannot land
            # inside it, so no upgrade can still be running when shutdown starts.
            self.gateway.usage.run_startup_upgrades()
        self.herdr.configure_usage(
            self.usage.usage_collector, self.usage._usage_digest, self.usage._usage_repo_fp
        )
        self.changes._prepare_changes()
        gateway_app = create_app(self.gateway)
        if self.config.web_ui and self.config.notifications.web_push:
            # Web push is the default route: on with the web UI, switchable in [notifications].
            self.web_push._start_web_push(gateway_app)
        if self.config.web_ui:
            self.web.app = self.web._web_app(gateway_app, cookie)
        else:
            # Bridge only: the Android app pairs and talks to this API; no
            # browser UI, web sessions or Flet runtime are started.
            self.web.app = gateway_app
        await self.web._start_server()
        if self.config.tailscale_auto:
            self.web._open_route()
        await self.herdr._connect()
        if self.web_push.webpush_store is not None:
            self.web_push.webpush_task = asyncio.create_task(self.web_push._webpush_loop())
        if self.usage.usage_collector is not None:
            self.usage.usage_collector.start()
        if self.usage.limits_scheduler is not None:
            self.usage.limits_scheduler.start()

    async def _serve_owned(self) -> None:
        try:
            await self.web.server.serve()
        except SystemExit as exc:
            # Uvicorn exits on bind failure. Convert inside the owned task
            # so the foreground supervisor can clean every partial resource.
            raise RuntimeError("loopback server could not start") from exc

    def setup_report(self) -> tuple[tuple[str, str], ...]:
        connected = bool(self.gateway and self.gateway.sockets.host_count(self.config.host_id))
        tool = (("Herdr command", herdr_retry.TOOL_ISSUE),) if self.herdr.herdr_tool_missing else ()
        return (
            (
                "Sign-in storage",
                "Durable encrypted sign-ins available."
                if self.storage.persistence_status.available
                else "In-memory sign-ins only: " + self.storage.persistence_status.reason + ".",
            ),
            ("Private address", self.config.advertised_origin),
            (
                "Device authorization",
                "Approve a device on this Mac with a matching code (relaydeck pairing)."
                if self.gateway and self.gateway.pairing is not None
                else "Device pairing is unavailable; inspect the foreground companion setup.",
            ),
            (
                "Mac connector enrollment",
                "Authenticated connector connected." if connected else self.herdr.issue,
            ),
            (
                "Push provider",
                "FCM configured; delivery/device proof pending."
                if self.gateway and self.gateway.push_provider is not None
                else "Not configured; foreground status remains available.",
            ),
            (
                "Private network",
                "Use doctor --probe-private to check HTTPS reachability. The Mac must stay awake.",
            ),
            *tool,
        )

    def report(self) -> dict[str, Any]:
        server_dead = self.web.server_task is None or self.web.server_task.done()
        connected = bool(self.gateway and self.gateway.sockets.host_count(self.config.host_id))
        synchronized = bool(
            self.gateway and self.gateway.backend.snapshots.get(self.config.host_id)
        )
        connector_dead = self.herdr.link_task is not None and self.herdr.link_task.done()
        if connector_dead:
            self.herdr.issue = "Connector stopped; check enrollment/Herdr, then restart explicitly."
        return {
            "signin_storage": self.storage.persistence_status.reason,
            "durable_signins": self.storage.persistence_status.available,
            "state": "failed" if server_dead or connector_dead else "setup_required",
            "failed": server_dead or connector_dead,
            "advertised_origin": self.config.advertised_origin,
            "local_endpoint": self.config.local_endpoint,
            "web_ui": self.config.web_ui,
            "web_ready": self.config.web_ui and not server_dead,
            "api_ready": not server_dead,
            "connector_ready": connected and synchronized,
            "identity_ready": bool(self.gateway and self.gateway.identity_provider is not None),
            "pairing_available": bool(self.gateway and self.gateway.pairing is not None),
            "synthetic": self.fixture,
            "diagnostics": dict(self.setup_report()),
        }

    def _request_shutdown(self) -> None:
        """Shared stop/signal edge, before Uvicorn starts its graceful wait."""
        self.web._remove_route()
        if self.stop is not None:
            self.stop.set()
        if self.usage.usage_store is not None:
            self.usage.usage_store.begin_shutdown()

    async def close(self) -> None:
        self._request_shutdown()
        await self.herdr._stop_retry()
        # Stop accepting work before joining producers, so joins never queue behind
        # usage requests in the default pool.
        if self.web.server is not None:
            self.web.server.should_exit = True
        if self.gateway is not None and self.gateway.usage is not None:
            await self.gateway.usage.close()
        if self.gateway is not None and self.gateway.git is not None:
            service = self.gateway.git.service
            service.close()
            if not await service.wait_closed():
                LOG.warning("companion_shutdown_pending resource=changes_workers")
        await self._finish(self.web_push.webpush_task)
        await self._finish(self.updates.updates_task)
        if self.usage.usage_collector is not None:
            await asyncio.to_thread(self.usage.usage_collector.stop)
        if self.usage.limits_scheduler is not None:
            await asyncio.to_thread(self.usage.limits_scheduler.stop)
        await self._finish(self.storage.persistence_task)
        try:
            await self._finish(self.herdr.link_task)
            if self.herdr.source is not None and not await self.herdr._close_source(
                self.herdr.source
            ):
                LOG.warning("companion_shutdown_pending resource=herdr_source")
        finally:
            try:
                await self._finish(self.web.server_task)
            finally:
                if self.gateway is not None:
                    if self.usage.usage_store is not None:
                        try:
                            await asyncio.to_thread(self.usage.usage_store.close)
                        except Exception:
                            LOG.warning("companion_shutdown_pending resource=usage_store")
                    await asyncio.to_thread(self.gateway.backend.journal.close)
                    await asyncio.to_thread(self.gateway.push.close)
                if self.web_push.webpush_provider is not None:
                    self.web_push.webpush_provider.close()
                if self.web_push.webpush_store is not None:
                    self.web_push.webpush_store.close()
                if self.web.web_store is not None and self.web.web_store.command_store is not None:
                    self.web.web_store.command_store.close()
                if self.storage.persistence is not None:
                    await asyncio.to_thread(self.storage.persistence.close)

    @staticmethod
    async def _finish(task: asyncio.Task[Any] | None) -> None:
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), 6)
        except Exception:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
