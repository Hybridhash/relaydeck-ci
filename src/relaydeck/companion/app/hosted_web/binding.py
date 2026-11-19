"""Hosted web and private route lifetime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from contextlib import suppress
from typing import Any

from relaydeck.companion.app.hosted_web.headers import security_headers
from relaydeck.companion.app.hosted_web.serve import (
    WebSessionStore,
    build_web_app,
    make_relaydeck_main,
)
from relaydeck.companion.app.hosted_web.static import static_cache, static_gzip
from relaydeck.companion.config import CompanionConfig
from relaydeck.gateway.app.bounds import GRACEFUL_SHUTDOWN_S, WS_MAX_SIZE
from relaydeck.host.observability.logging import logging_config

_uvicorn_logging_config = logging_config


def _route_first_server(config: Any, before_exit: Callable[[], None]) -> Any:
    import uvicorn

    class _RouteFirstServer(uvicorn.Server):
        def handle_exit(self, sig: int, frame: Any) -> None:
            with suppress(Exception):
                before_exit()
            super().handle_exit(sig, frame)

    return _RouteFirstServer(config)


class HostedWeb:
    def __init__(
        self,
        config: CompanionConfig,
        *,
        gateway: Callable[[], Any],
        persistence: Callable[[], Any],
        server_factory: Callable[[Any], Any] | None,
        route_factory: Callable[[], Any] | None,
        setup_report: Callable[[], tuple[tuple[str, str], ...]],
        request_shutdown: Callable[[], None],
        serve_owned: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        self.config = config
        self._gateway = gateway
        self._persistence = persistence
        self.setup_report = setup_report
        self._request_shutdown = request_shutdown
        self._serve_owned = serve_owned
        self.server_factory = server_factory
        self.route_factory = route_factory
        self.route: Any = None
        self.server: Any = None
        self.server_task: asyncio.Task[Any] | None = None
        self.app: Any = None
        self.web_store: WebSessionStore | None = None

    @property
    def gateway(self) -> Any:
        return self._gateway()

    @property
    def persistence(self) -> Any:
        return self._persistence()

    def _web_app(self, gateway_app: Any, cookie: bytes) -> Any:
        self.web_store = WebSessionStore(
            self.config.advertised_origin,
            cookie,
            persistence=self.persistence,
            gateway_sessions=self.gateway.sessions,
        )
        from relaydeck.companion.app.hosted_web.storage import ServerCommandStore

        self.web_store.command_store = ServerCommandStore(
            self.config.state_dir / "pending-commands.db"
        )
        flet_main = make_relaydeck_main(
            store=self.web_store,
            base_url=self.config.local_endpoint,
            gateway_app=gateway_app,
            gateway_state=self.gateway,
            host_id=self.config.host_id,
            setup_report=self.setup_report,
        )
        app, _ = build_web_app(
            gateway_state=self.gateway,
            gateway_app=gateway_app,
            flet_main=flet_main,
            public_origin=self.config.advertised_origin,
            cookie_secret=cookie,
            store=self.web_store,
        )
        return security_headers(
            static_cache(static_gzip(app)), public_origin=self.config.advertised_origin
        )

    async def _start_server(self) -> None:
        if self.server_factory:
            self.server = self.server_factory(self.app)
        else:
            import uvicorn

            self.server = _route_first_server(
                uvicorn.Config(
                    self.app,
                    host=self.config.bind_host,
                    port=self.config.port,
                    workers=1,
                    log_config=_uvicorn_logging_config(),
                    access_log=False,
                    proxy_headers=False,
                    timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,
                    ws_max_size=WS_MAX_SIZE,
                ),
                self._request_shutdown,
            )
        self.server_task = asyncio.create_task(self._serve_owned(), name="companion-web")
        async with asyncio.timeout(10):
            while not self.server.started:
                if self.server_task.done():
                    cause = None if self.server_task.cancelled() else self.server_task.exception()
                    raise RuntimeError(
                        "loopback server could not start; preserve the existing listener"
                    ) from cause
                await asyncio.sleep(0.02)

    def _open_route(self) -> None:
        if self.route_factory is not None:
            self.route = self.route_factory()
        else:
            from relaydeck.companion.adapters.tailscale import PrivateRoute

            self.route = PrivateRoute(self.config.tailscale_https_port, self.config.port)
        self.route.ensure()

    def _remove_route(self) -> None:
        """Idempotent: drop this run's tailnet route before the listener closes."""
        route, self.route = self.route, None
        if route is not None:
            with suppress(Exception):
                route.remove()
