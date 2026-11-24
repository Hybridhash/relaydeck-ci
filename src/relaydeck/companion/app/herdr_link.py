"""Connector source and retry lifetime."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable, Coroutine
from contextlib import suppress
from typing import Any

from relaydeck.companion.app import herdr_retry
from relaydeck.companion.config import CompanionConfig
from relaydeck.connector.enrollment import load_creds
from relaydeck.connector.herdr import HerdrError
from relaydeck.connector.link import ConnectorLink, LinkConfig, TunnelTransport, backoff_ms
from relaydeck.host.executables import tool_directories, trusted_executable
from relaydeck.host.files.private import read_private
from relaydeck.usage.api import UsageCollector

LOG = logging.getLogger(__name__)


class HerdrLink:
    def __init__(
        self,
        config: CompanionConfig,
        *,
        gateway: Callable[[], Any],
        stop: Callable[[], asyncio.Event | None],
        connect_herdr: bool,
        fixture: bool,
        source_factory: Callable[[], Any] | None,
        opener: Callable[[], Coroutine[Any, Any, TunnelTransport]] | None,
    ) -> None:
        self.config = config
        self._gateway = gateway
        self._stop = stop
        self.connect_herdr = connect_herdr
        self.fixture = fixture
        self.source_factory = source_factory
        self.opener = opener
        self.link_task: asyncio.Task[Any] | None = None
        self.retry_task: asyncio.Task[None] | None = None
        self._retry_random = random.Random()  # noqa: S311 - retry jitter, not security
        self.source: Any = None
        self._source_closed = False
        self.herdr_tool_missing = False
        self._failure: str | None = None  # why the last attempt failed, until its close is done
        self._cleanup: tuple[Any, asyncio.Task[bool]] | None = None
        self.issue = "Start with --connect-herdr after local attachment authorization."
        self.usage_collector: UsageCollector | None = None
        self._usage_digest: Callable[[str], str | None] | None = None
        self._usage_repo_fp: Callable[[str], str | None] | None = None

    def configure_usage(
        self,
        collector: UsageCollector | None,
        digest: Callable[[str], str | None] | None,
        repo_fingerprint: Callable[[str], str | None] | None,
    ) -> None:
        self.usage_collector = collector
        self._usage_digest = digest
        self._usage_repo_fp = repo_fingerprint

    @property
    def gateway(self) -> Any:
        return self._gateway()

    @property
    def stop(self) -> asyncio.Event | None:
        return self._stop()

    @staticmethod
    def _herdr_binary() -> str | None:
        """Herdr from the same validated search path setup used, so a Herdr only in
        `~/.local/bin` is found when the service's PATH is the one setup wrote."""
        return trusted_executable("herdr", directories=tool_directories())

    def _enrolled(self) -> Any:
        """The connector's credentials when this Mac is enrolled with this endpoint; otherwise
        None, with the reason in `issue`. Checked again on every retry, so a revocation ends it."""
        credentials = self.config.connector_credentials
        if not credentials.exists():
            self.issue = (
                "Enroll this Mac's connector using a public-key-bound invitation, "
                "then restart explicitly."
            )
            return None
        _ = read_private(credentials)
        creds = load_creds(str(credentials))
        if (
            creds is None
            or creds.host_id != self.config.host_id
            or creds.gateway_url.rstrip("/") != self.config.advertised_origin
            or self.gateway.hosts.lookup(creds.host_token) is None
        ):
            self.issue = (
                "Connector enrollment expired or belongs to another endpoint; renew it explicitly."
            )
            return None
        return creds

    def _new_source(self) -> Any:
        if self.source_factory:
            return self.source_factory()
        if self.fixture:
            from relaydeck.devtools.fakes.connector import FixtureAdapter

            return FixtureAdapter()
        from relaydeck.companion.inspection.detect import launch_programs, load_catalog
        from relaydeck.connector.herdr import HerdrAdapter
        from relaydeck.connector.source import HerdrSource

        binary = self._herdr_binary()
        self.herdr_tool_missing = binary is None
        if binary is None:
            LOG.warning("herdr_tool_unavailable")
        return HerdrSource(
            HerdrAdapter(socket_path=self.config.herdr_socket),
            binary=binary,
            agent_kinds=self.config.agent_kinds,
            launch_programs=launch_programs(load_catalog()),
            controller_viewport=self.config.terminal.controller_viewport,
        )

    async def _close_source(self, source: Any) -> bool:
        """Close `source` and say whether it is closed. The close is a task the runtime owns, so a
        cancelled caller does not abandon it; a failed one is tried again by the next call."""
        if not hasattr(source, "close") or (source is self.source and self._source_closed):
            return True
        running = self._cleanup
        if running is None or running[0] is not source or running[1].done():
            task = asyncio.create_task(self._close_once(source), name="companion-source-close")
            self._cleanup = (source, task)
        else:
            task = running[1]
        return await asyncio.shield(task)

    async def _close_once(self, source: Any) -> bool:
        try:
            async with asyncio.timeout(6):
                await source.close()
        except Exception:
            LOG.warning("companion_source_close_failed")
            return False
        if source is self.source:
            self._source_closed = True
        return True

    async def _settle_failure(self) -> str | None:
        """Close the source the last attempt left behind and then report why it failed. While the
        close keeps failing it is retried, whatever the failure was, and no other source is made;
        a failure waiting cannot fix stops here once the close has succeeded."""
        if not await self._close_source(self.source):
            self.issue = herdr_retry.CLEANUP_ISSUE
            return herdr_retry.TRANSIENT
        failure, self._failure = self._failure, None
        if failure == herdr_retry.TRANSIENT:
            self.issue = herdr_retry.WAITING_ISSUE
            return herdr_retry.TRANSIENT
        self.issue = herdr_retry.INCOMPATIBLE_ISSUE
        return None

    async def _attach(self, creds: Any) -> str | None:
        """Start a source and, when it answers, the one connector link. Returns None when
        settled (linked, or failed for a reason waiting cannot fix) or `TRANSIENT` when Herdr
        is simply not answering.
        """
        if self._failure is not None:
            return await self._settle_failure()  # the last attempt's close is still owed
        previous = self.source
        if previous is not None and not await self._close_source(previous):
            self.issue = herdr_retry.CLEANUP_ISSUE
            return herdr_retry.TRANSIENT
        source = self._new_source()
        self.source = source
        self._source_closed = False
        if hasattr(source, "bootstrap"):
            if self.usage_collector is not None and hasattr(source, "configure_usage"):
                source.configure_usage(
                    self.usage_collector.sources,
                    digest=self._usage_digest,
                    repo_fingerprint=self._usage_repo_fp,
                )
            try:
                async with asyncio.timeout(10):
                    await source.bootstrap()
            except (HerdrError, OSError, RuntimeError, TimeoutError) as exc:
                self._failure = herdr_retry.classify(exc)
                return await self._settle_failure()
            except asyncio.CancelledError:
                await self._close_source(source)
                raise
        assert self.stop is not None  # noqa: S101
        if self.stop.is_set() or self.link_task is not None:
            # A start that finished while the companion was stopping, or a second one: no link.
            await self._close_source(source)
            return None
        link = ConnectorLink(
            LinkConfig(
                gateway_url=self.config.local_endpoint,
                host_id=creds.host_id,
                host_token=creds.host_token,
                journal_path=str(self.config.state_dir / "connector-journal.db"),
            ),
            source,
            opener=self.opener,
        )
        self.link_task = asyncio.create_task(link.run(self.stop), name="companion-connector")
        self.issue = "Waiting for authenticated connector synchronization."
        return None

    async def _connect(self) -> None:
        if not (self.connect_herdr or self.fixture):
            return
        if self.link_task is not None or (self.retry_task and not self.retry_task.done()):
            return  # already connected, or already waiting: one link and one retry at most
        creds = self._enrolled()
        if creds is None:
            return
        waiting = await self._attach(creds) == herdr_retry.TRANSIENT
        if waiting and self.stop is not None and not self.stop.is_set():
            self.retry_task = asyncio.create_task(
                self._retry_attach(), name="companion-herdr-retry"
            )

    async def _retry_attach(self) -> None:
        """The one owned retry: wait with the connector's backoff, check the enrollment again,
        and try once more, until the link exists or the failure stops being transient."""
        assert self.stop is not None  # noqa: S101
        attempts = 0
        while not self.stop.is_set():
            delay = backoff_ms(attempts, rng=self._retry_random) / 1000
            with suppress(TimeoutError):
                await asyncio.wait_for(self.stop.wait(), delay)
            if self.stop.is_set():
                return
            creds = self._enrolled()
            if creds is None or await self._attach(creds) != herdr_retry.TRANSIENT:
                return
            attempts += 1

    async def _stop_retry(self) -> None:
        """Cancel the retry and wait until it is gone, before anything it uses is closed."""
        task, self.retry_task = self.retry_task, None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
