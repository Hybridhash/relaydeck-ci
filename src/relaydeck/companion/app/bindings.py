"""Companion resource bindings."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from relaydeck.companion.app.hosted_web.serve import WebSessionStore
from relaydeck.companion.app.maintenance import maintain_storage
from relaydeck.companion.config import CompanionConfig
from relaydeck.companion.inspection.detect import CatalogError, load_catalog
from relaydeck.companion.updates.worker import build as build_updates
from relaydeck.gateway.features.notifications import api as webpush
from relaydeck.gateway.features.notifications import api as webpush_store
from relaydeck.gateway.features.notifications.api import WebPushProvider, WebPushStore
from relaydeck.gateway.features.usage_read.api import UsageService
from relaydeck.gateway.http import webpush as webpush_routes
from relaydeck.gateway.http.usage import live_workspace_labels
from relaydeck.git.api import (
    BaseStore,
    ChangesService,
    GitReadPort,
    GitRunner,
    GitUnavailable,
    source_folders,
)
from relaydeck.host.concurrency.deadlines import DeadlineWake
from relaydeck.host.files.private import read_private
from relaydeck.host.persistence.contracts import (
    KeyProvider,
    KeyUnavailable,
    SecretMaterial,
    StorageStatus,
    StorageUnavailable,
)
from relaydeck.usage.api import FORMAT_ID as TUI_USAGE_PANE_FORMAT_ID
from relaydeck.usage.api import (
    PROVIDERS,
    READERS,
    STATE_FILENAME,
    LimitsProvider,
    LimitsScheduler,
    MappingGap,
    PaneCwdObservation,
    PriceTable,
    ProviderSpec,
    RefreshDecision,
    RefreshState,
    SessionMapping,
    SqliteUsageStore,
    TuiBundle,
    UsageCollector,
    UsageConfig,
    UsageSource,
    cwd_digest,
    ensure_probe_folder,
    load_or_create_link_key,
    load_price_table,
    probe_folder,
    recover_stale_tabs,
    repo_fingerprint,
    run_bounded_command,
)

LOG = logging.getLogger(__name__)


class WebPushBinding:
    def __init__(
        self,
        config: CompanionConfig,
        *,
        gateway: Callable[[], Any],
        stop: Callable[[], asyncio.Event | None],
        web_store: Callable[[], WebSessionStore | None],
    ) -> None:
        self.config = config
        self._gateway = gateway
        self._stop = stop
        self._web_store = web_store
        self.webpush_store: WebPushStore | None = None
        self.webpush_provider: WebPushProvider | None = None
        self.webpush_key: Any = None
        self.webpush_topic: bytes = b""
        self.webpush_task: asyncio.Task[None] | None = None
        self._webpush_wake = DeadlineWake()

    @property
    def gateway(self) -> Any:
        return self._gateway()

    @property
    def stop(self) -> asyncio.Event | None:
        return self._stop()

    @property
    def web_store(self) -> WebSessionStore | None:
        return self._web_store()

    def _start_web_push(self, gateway_app: Any) -> None:
        """keys, store, subscribe/unsubscribe routes and the event sink.

        The routes go on the gateway app before the web app copies its routes, so they sit
        ahead of the Flet mount."""
        state = self.config.state_dir
        self.webpush_key = webpush.load_or_create_vapid(state)
        self.webpush_topic = webpush.load_or_create_topic_key(state)
        self.webpush_store = WebPushStore(state)
        self.webpush_provider = WebPushProvider(
            self.webpush_key, subject=self.config.notifications.vapid_subject
        )

        def identity(request: Any) -> tuple[str, str] | None:
            # A bearer credential (the packaged app's gateway calls) or the browser session cookie.
            authorization = request.headers.get("authorization", "")
            if authorization.lower().startswith("bearer ") and self.gateway is not None:
                token = authorization[7:].strip()
                session = (
                    self.gateway.sessions.lookup(token, self.gateway.now_ms()) if token else None
                )
                if session is None or not session.device_id:
                    return None
                return (session.account, session.device_id)
            web = self.web_store
            if web is None:
                return None
            sid = web.verify_cookie(web.cookie_value(request.headers.raw))
            current = web.identity(sid) if sid else None
            return None if current is None else (current.account, current.device_id)

        gateway_app.include_router(
            webpush_routes.build_router(
                store=self.webpush_store,
                identity=identity,
                origin=self.config.advertised_origin,
                vapid_key=self.webpush_key,
                topic_key=self.webpush_topic,
                preferences=self.gateway.push,
                settings=webpush_routes.WebPushSettings(
                    enabled=True,
                    lock_screen_detail=self.config.notifications.lock_screen_detail,
                    subject=self.config.notifications.vapid_subject,
                ),
            )
        )
        if self.gateway is not None:
            self.gateway.web_push_sink = self._webpush_enqueue

    def _webpush_enqueue(
        self,
        account: str,
        devices: frozenset[str],
        event_id: str,
        category: str,
        host: str,
        session: str,
    ) -> None:
        store, gateway = self.webpush_store, self.gateway
        if store is None or gateway is None:
            return
        now = gateway.now_ms()
        for device_id in devices:
            if not gateway.push.category_allowed(account, device_id, category):
                continue  # the device turned this category off
            with suppress(ValueError):  # a malformed event never blocks the other devices
                store.enqueue(
                    account,
                    device_id,
                    event_id,
                    category,
                    now=now,
                    label=host[:64],
                    host=host,
                    session=session,
                )
                self._webpush_wake.wake()

    async def _webpush_device_active(self, account: str, device_id: str) -> bool:
        gateway = self.gateway
        return gateway is not None and device_id in gateway.sessions.device_metadata(
            account, gateway.now_ms()
        )

    def _webpush_pass(self, loop: asyncio.AbstractEventLoop) -> None:
        """Runs in a worker thread. Each send is re-authorized on the event loop, where the session
        store lives, immediately before it happens."""
        store, provider, gateway = self.webpush_store, self.webpush_provider, self.gateway
        if store is None or provider is None or gateway is None:
            return

        def authorized(account: str, device_id: str) -> bool:
            future = asyncio.run_coroutine_threadsafe(
                self._webpush_device_active(account, device_id), loop
            )
            return bool(future.result(timeout=5))

        webpush_store.pump(
            store,
            provider,
            topic_key=self.webpush_topic,
            now=gateway.now_ms(),
            lock_screen_detail=self.config.notifications.lock_screen_detail,
            authorized=authorized,
            allowed=gateway.push.category_allowed,
        )
        store.purge(gateway.now_ms())

    async def _webpush_loop(self) -> None:
        loop = asyncio.get_running_loop()
        self._webpush_wake.bind()
        while self.stop is None or not self.stop.is_set():
            self._webpush_wake.event.clear()
            try:
                await asyncio.to_thread(self._webpush_pass, loop)
                due = (
                    await asyncio.to_thread(self.webpush_store.next_due_ms)
                    if self.webpush_store is not None
                    else None
                )
                delay = max(0.01, (due - self.gateway.now_ms()) / 1000) if due is not None else None
            except Exception:  # noqa: BLE001 - bounded: nothing about subscriptions is logged
                LOG.warning("webpush_pass_failed")
                delay = 2.0
            await self._webpush_wake.wait(self.stop, delay)


class UsageBinding:
    def __init__(
        self,
        config: CompanionConfig,
        *,
        gateway: Callable[[], Any],
        source: Callable[[], Any],
        herdr_binary: Callable[[], str | None],
    ) -> None:
        self.config = config
        self._gateway = gateway
        self._source = source
        self._herdr_binary = herdr_binary
        self.usage_store: SqliteUsageStore | None = None
        self.usage_collector: UsageCollector | None = None
        self.limits_scheduler: LimitsScheduler | None = None
        self._usage_digest: Callable[[str], str | None] | None = None
        self._usage_repo_fp: Callable[[str], str | None] | None = None

    @property
    def gateway(self) -> Any:
        return self._gateway()

    @property
    def source(self) -> Any:
        return self._source()

    def _prepare_usage(self) -> None:
        config = self.config.usage
        assert config is not None  # noqa: S101
        prices = PriceTable()
        if config.price_tables:
            try:
                prices = load_price_table(config.price_tables)
            except (OSError, ValueError):
                LOG.warning("usage_prices_unavailable reason=invalid_price_table")
        try:
            self.usage_store = SqliteUsageStore(
                self.config.state_dir / "usage.db",
                timezone=config.timezone,
                prices=prices,
            )
            sources = tuple(
                UsageSource(
                    self.config.host_id, s.kind, s.format, s.root, s.limits_format, s.limits_cache
                )
                for s in config.sources
            )
            link_key = load_or_create_link_key(self.config.state_dir)
            if link_key is None:
                LOG.warning("usage_link_digests_unavailable reason=link_key_unavailable")
            self._usage_digest = (
                (lambda folder: cwd_digest(link_key, folder)) if link_key is not None else None
            )
            self._usage_repo_fp = (
                (lambda repo: repo_fingerprint(link_key, repo)) if link_key is not None else None
            )
            self.usage_collector = UsageCollector(
                self.usage_store,
                READERS,
                sources,
                self._usage_mappings,
                mapping_gaps=self._usage_mapping_gaps,
                acknowledge_mappings=self._acknowledge_usage,
                digest=self._usage_digest,
                cwd_observations=self._usage_cwd_observations,
                acknowledge_cwd=self._acknowledge_cwd,
                inferred_linking=config.inferred_linking,
            )
            self.limits_scheduler = self._prepare_limits(config)
            assert self.usage_store is not None  # noqa: S101
            retired = self.usage_store.retire_missing_sources(
                self.config.host_id,
                [s.source_key for s in sources],
                list(self.limits_scheduler.spec_keys) if self.limits_scheduler is not None else [],
            )
            if retired:
                LOG.info("usage_sources_retired count=%d", retired)
        except Exception:
            if self.usage_store is not None:
                self.usage_store.close()
            self.usage_store = None
            self.limits_scheduler = None
            LOG.warning("usage_unavailable reason=storage_unavailable")
        scheduler = self.limits_scheduler

        def request_refresh(kind: str) -> RefreshDecision:
            if scheduler is None:
                return RefreshDecision("not_configured")
            key = scheduler.on_demand_key(kind)
            if key is None:
                return RefreshDecision("not_configured")
            return scheduler.request_on_demand(key)

        self.gateway.usage = UsageService(
            self.usage_store,
            config,
            self.config.host_id,
            self.config.state_dir,
            self.gateway.now_ms,
            workspace_labels=lambda: live_workspace_labels(self.gateway, self.config.host_id),
            limits_status=(scheduler.status if scheduler is not None else None),
            limits_refresh_status=(scheduler.refresh_status if scheduler is not None else None),
            request_limits_refresh=request_refresh if scheduler is not None else None,
            on_usage_open=scheduler.notify_usage_open if scheduler is not None else None,
        )
        self.gateway.usage.check_setup()

    def _prepare_limits(self, config: UsageConfig) -> LimitsScheduler | None:
        """Build a limits scheduler only for enabled blocks with registered providers. Skip
        unknown formats and return no scheduler when none resolve.
        """
        assert self.usage_store is not None  # noqa: S101
        specs: dict[str, ProviderSpec] = {}
        providers: dict[str, LimitsProvider] = {}
        skipped = 0
        catalog: dict[str, Any] = {}
        try:
            for entry in load_catalog().entries:
                catalog[entry.id] = entry
                for alias in entry.aliases:
                    catalog.setdefault(alias, entry)
        except CatalogError:
            LOG.warning("usage_limits_skipped reason=catalog_unavailable")
        for block in config.limits:
            if not block.enabled:
                continue
            provider = PROVIDERS.get(block.format)
            if provider is None:
                skipped += 1
                continue
            bundle: TuiBundle | None = None
            if block.format == TUI_USAGE_PANE_FORMAT_ID:
                bundle = self._tui_bundle(config, block.kind, catalog)
                if bundle is None:
                    skipped += 1
                    continue
                if block.command:
                    LOG.warning(
                        "usage_limits_ignored reason=command_ignored_on_tui_block kind=%s",
                        block.kind,
                    )
            key = f"{self.config.host_id}/{block.kind}/{block.format}"
            specs[key] = ProviderSpec(
                key,
                block.kind,
                block.format,
                block.min_interval_seconds * 1000,
                float(block.timeout_seconds),
                command=block.command,
                on_demand=block.on_demand,
                tui=bundle,
                auto_interval_ms=block.auto_interval_seconds * 1000 if block.on_demand else 0,
            )
            providers[key] = provider
        if skipped:
            LOG.warning("usage_limits_skipped reason=unknown_format count=%d", skipped)
        if not specs:
            return None
        refresh: RefreshState | None = None
        if any(spec.on_demand for spec in specs.values()):
            refresh = RefreshState(self.config.state_dir / STATE_FILENAME)
            self._recover_refresh_tabs(refresh)
        return LimitsScheduler(
            self.usage_store,
            specs,
            providers,
            key_path=self.config.state_dir / "limits-account.key",
            refresh=refresh,
        )

    def _usage_mappings(self) -> tuple[SessionMapping, ...]:
        source = self.source
        provider = getattr(source, "usage_mappings", None)
        return tuple(provider()) if provider is not None else ()

    def _usage_mapping_gaps(self) -> tuple[MappingGap, ...]:
        provider = getattr(self.source, "usage_mapping_gaps", None)
        return tuple(provider()) if provider is not None else ()

    def _acknowledge_usage(
        self, source_key: str, mappings: tuple[SessionMapping, ...], gaps: tuple[MappingGap, ...]
    ) -> None:
        acknowledge = getattr(self.source, "acknowledge_usage", None)
        if acknowledge is not None:
            acknowledge(source_key, mappings, gaps)

    def _usage_cwd_observations(self) -> tuple[PaneCwdObservation, ...]:
        provider = getattr(self.source, "usage_cwd_observations", None)
        return tuple(provider()) if provider is not None else ()

    def _acknowledge_cwd(
        self, source_key: str, observations: tuple[PaneCwdObservation, ...]
    ) -> None:
        acknowledge = getattr(self.source, "acknowledge_cwd", None)
        if acknowledge is not None:
            acknowledge(source_key, observations)

    def _tui_bundle(
        self, config: UsageConfig, kind: str, catalog: dict[str, Any]
    ) -> TuiBundle | None:
        """Resolve one TUI block's recipe + herdr binary. None when the kind
        has no catalog recipe (the block is skipped, visibly)."""
        entry = catalog.get(kind)
        recipe = getattr(entry, "tui", None)
        if recipe is None:
            LOG.warning("usage_limits_skipped reason=no_tui_recipe kind=%s", kind)
            return None
        found = self._herdr_binary()
        herdr: tuple[str, ...] = ()
        if found is not None:
            herdr = (found,)
        else:
            LOG.warning("usage_limits_no_herdr kind=%s", kind)
        probe = probe_folder(recipe.probe_dir, self.config.state_dir)
        if probe is not None and not ensure_probe_folder(probe):
            LOG.warning("usage_probe_folder_unavailable kind=%s", kind)
        return TuiBundle(
            recipe,
            herdr,
            self.config.state_dir / STATE_FILENAME,
            config.timezone,
            os.path.expanduser(self.config.herdr_socket),
        )

    def _recover_refresh_tabs(self, refresh: RefreshState) -> None:
        """Restart recovery: close proven-ours leftover refresh tabs (bounded)."""
        found = self._herdr_binary()
        if found is None or not refresh.unresolved():
            return

        def run(argv: Any, timeout_s: float) -> bytes:
            return run_bounded_command(argv, timeout_s=timeout_s)

        try:
            outcome = recover_stale_tabs(
                refresh, (found,), os.path.expanduser(self.config.herdr_socket), run
            )
        except Exception:
            LOG.warning("usage_limits_recovery_failed")
            return
        if any(outcome.values()):
            LOG.info("usage_limits_recovered %r", outcome)


class StorageBinding:
    def __init__(
        self,
        config: CompanionConfig,
        *,
        gateway: Callable[[], Any],
        stop: Callable[[], asyncio.Event | None],
        durable_signins: bool | None,
        migrate_cookie_secret: bool | None,
        setup_persistence: bool | None,
        invalidate_restored_signins: bool | None,
        persistence_provider: KeyProvider | None,
    ) -> None:
        self.config = config
        self._gateway = gateway
        self._stop = stop
        self.durable_signins = (
            durable_signins
            if durable_signins is not None
            else os.getenv("RELAYDECK_DURABLE_SIGNINS") == "1"
        )
        self.migrate_cookie_secret = (
            migrate_cookie_secret
            if migrate_cookie_secret is not None
            else os.getenv("RELAYDECK_MIGRATE_COOKIE_SECRET") == "1"
        )
        self.setup_persistence = (
            setup_persistence
            if setup_persistence is not None
            else os.getenv("RELAYDECK_PERSISTENCE_SETUP") == "1"
        )
        self.invalidate_restored_signins = (
            invalidate_restored_signins
            if invalidate_restored_signins is not None
            else os.getenv("RELAYDECK_INVALIDATE_RESTORED_SIGNINS") == "1"
        )
        self.persistence_provider = persistence_provider
        self.persistence: Any = None
        self.persistence_task: asyncio.Task[Any] | None = None
        self.persistence_status = StorageStatus(False, "disabled")

    @property
    def gateway(self) -> Any:
        return self._gateway()

    @property
    def stop(self) -> asyncio.Event | None:
        return self._stop()

    def _storage_secrets(self) -> tuple[bytes, bytes]:
        if not self.durable_signins:
            return self.config.secrets()
        cursor = read_private(self.config.cursor_secret_file).strip().encode()
        if len(cursor) < 32:
            raise ValueError("cursor secret unavailable")
        from relaydeck.host.persistence.keychain import MacKeyProvider
        from relaydeck.host.persistence.sqlite_store import open_store

        try:
            provider = self.persistence_provider or MacKeyProvider()
            self.persistence_provider = provider
            provider.restore_generation(
                "authentication",
                create=self.setup_persistence,
                allow_prompt=self.setup_persistence,
            )
            if self.migrate_cookie_secret:
                # Preserve the exact EFFECTIVE bytes today's configured signer uses.
                cookie = self.config.secrets()[0]
                provider.import_secret(
                    "cookie-signing", SecretMaterial(cookie), allow_prompt=self.setup_persistence
                )
            cookie = provider.read_secret("cookie-signing").value
            if len(cookie) < 32 or cookie == cursor:
                raise StorageUnavailable("invalid_record")
            if self.invalidate_restored_signins:
                provider.advance_restore_generation("authentication")
            self.persistence = open_store(self.config, provider)
            self.persistence_status = self.persistence.status()
            if not self.persistence_status.available:
                raise StorageUnavailable(self.persistence_status.reason)
            return cookie, cursor
        except (KeyUnavailable, StorageUnavailable, ValueError) as exc:
            self.persistence_status = StorageStatus(
                False, exc.code if isinstance(exc, StorageUnavailable) else "keys_unavailable"
            )
            if self.persistence is not None:
                self.persistence.close()
                self.persistence = None
            # Startup stays available with NEW in-memory pairing. Do not bypass
            # a locked Keychain by reading the retained plaintext signing file.
            return secrets.token_bytes(32), cursor

    async def _maintain_storage(self) -> None:
        assert self.stop is not None and self.persistence is not None  # noqa: S101
        await maintain_storage(self)


class UpdatesBinding:
    def __init__(
        self,
        config: CompanionConfig,
        *,
        gateway: Callable[[], Any],
        config_path: Path | None,
        fixture: bool,
    ) -> None:
        self.config = config
        self._gateway = gateway
        self.config_path = config_path
        self.fixture = fixture
        self.updates_task: asyncio.Task[None] | None = None

    @property
    def gateway(self) -> Any:
        return self._gateway()

    def _start_updates(self, stop: asyncio.Event) -> None:
        """The release notice and its daily check; a failure here never stops the companion."""
        try:
            service, worker = build_updates(self.config, self.config_path, fixture=self.fixture)
            self.gateway.updates = service
            if worker is not None:
                self.updates_task = asyncio.create_task(worker.run(stop), name="companion-updates")
        except Exception:  # noqa: BLE001
            LOG.warning("update_check outcome=state")


class ChangesBinding:
    def __init__(
        self,
        config: CompanionConfig,
        *,
        gateway: Callable[[], Any],
        source: Callable[[], Any],
    ) -> None:
        self.config = config
        self._gateway = gateway
        self._source = source

    @property
    def gateway(self) -> Any:
        return self._gateway()

    @property
    def source(self) -> Any:
        return self._source()

    def _prepare_changes(self) -> None:
        """Read-only agent changes; without a trusted, recent git the routes answer not_found."""
        try:
            runner = GitRunner.create()
        except GitUnavailable as exc:
            reason = str(exc)
            if reason not in ("git_missing", "git_too_old", "git_timeout", "git_execution_failed"):
                reason = "git_unavailable"
            LOG.warning("changes_unavailable reason=%s", reason)
            return
        try:
            service = ChangesService(runner, BaseStore(str(self.config.state_dir)))
        except OSError:
            LOG.warning("changes_unavailable reason=store_unavailable")
            return
        self.gateway.git = GitReadPort(
            self.config.host_id, service, lambda ids: source_folders(self.source)(ids)
        )
