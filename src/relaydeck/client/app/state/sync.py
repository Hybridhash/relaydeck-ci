"""Supervise metadata synchronization, reconnect state and subscribers."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from relaydeck.client.features.sync.inventory_updates import InventoryUpdates
from relaydeck.client.platform.transport.backoff import Backoff
from relaydeck.client.ports.transport import (
    GatewayTransport,
    SseFrame,
    TransportError,
)
from relaydeck.contracts.catalog import catalog_from_wire
from relaydeck.contracts.schemas import SchemaError, validate, validate_metadata_event
from relaydeck.domain import reducers
from relaydeck.domain.models import (
    Agent,
    AgentStatus,
    AppState,
    Connection,
    ConnectionState,
    EventType,
    MetadataEvent,
    Pane,
    Snapshot,
    Tab,
    Workspace,
)

MAX_IDENTICAL_GAP_FAILURES = 3


def snapshot_from_wire(doc: dict[str, Any]) -> Snapshot:
    host = doc.get("host", {})
    return Snapshot(
        host_id=str(host.get("id", "")),
        host_epoch=str(host.get("epoch", "")),
        cursor=str(doc.get("cursor", "")),
        host_protocol=int(host.get("protocol", 0)),
        host_version=str(host.get("version", "")),
        host_capabilities=frozenset(str(c) for c in host.get("capabilities", [])),
        agent_catalog=catalog_from_wire(host.get("agent_catalog")),
        workspaces=tuple(
            Workspace(
                id=str(w.get("id", "")),
                host_id=str(w.get("host_id", "")),
                number=int(w.get("number", 0)),
                label=str(w.get("label", "")),
                focused=bool(w.get("focused", False)),
                pane_count=int(w.get("pane_count", 0)),
                tab_count=int(w.get("tab_count", 0)),
                active_tab_id=str(w.get("active_tab_id", "")),
                agent_status=AgentStatus.coerce(str(w.get("agent_status", "unknown"))),
            )
            for w in doc.get("workspaces", [])
        ),
        tabs=tuple(
            Tab(
                id=str(t.get("id", "")),
                host_id=str(t.get("host_id", "")),
                workspace_id=str(t.get("workspace_id", "")),
                number=int(t.get("number", 0)),
                label=str(t.get("label", "")),
                focused=bool(t.get("focused", False)),
                pane_count=int(t.get("pane_count", 0)),
                agent_status=AgentStatus.coerce(str(t.get("agent_status", "unknown"))),
            )
            for t in doc.get("tabs", [])
        ),
        panes=tuple(
            Pane(
                id=str(p.get("id", "")),
                host_id=str(p.get("host_id", "")),
                workspace_id=str(p.get("workspace_id", "")),
                tab_id=str(p.get("tab_id", "")),
                focused=bool(p.get("focused", False)),
                agent_status=AgentStatus.coerce(str(p.get("agent_status", "unknown"))),
            )
            for p in doc.get("panes", [])
        ),
        agents=tuple(
            Agent(
                terminal_id=str(a.get("terminal_id", "")),
                host_id=str(a.get("host_id", "")),
                workspace_id=str(a.get("workspace_id", "")),
                tab_id=str(a.get("tab_id", "")),
                pane_id=str(a.get("pane_id", "")),
                name=str(a.get("name", "")),
                agent=str(a.get("agent", "")),
                title=str(a.get("title", "")),
                status=AgentStatus.coerce(str(a.get("status", "unknown"))),
                focused=bool(a.get("focused", False)),
                launch_pending=bool(a.get("launch_pending", False)),
                interactive_ready=bool(a.get("interactive_ready", False)),
                state_change_seq=int(a.get("state_change_seq", 0)),
                revision=int(a.get("revision", 0)),
            )
            for a in doc.get("agents", [])
        ),
    )


def _observed_ms(value: Any) -> int:
    if not isinstance(value, str):
        return 0
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            moment = datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
        return int(moment.timestamp() * 1000)
    return 0


def event_from_wire(doc: dict[str, Any]) -> MetadataEvent:
    return MetadataEvent(
        event_id=str(doc.get("event_id", "")),
        host_id=str(doc.get("host_id", "")),
        host_epoch=str(doc.get("host_epoch", "")),
        sequence=int(doc.get("sequence", 0)),
        cursor=str(doc.get("cursor", "")),
        type=EventType(str(doc.get("type", "resync.required"))),
        resource_id=str(doc.get("resource_id", "")),
        resource_revision=int(doc.get("resource_revision", 0)),
        observed_ms=_observed_ms(doc.get("observed_at")),
        payload=dict(doc.get("payload", {})),
    )


@dataclass(frozen=True)
class SyncConnection(Connection):
    """Immutable retry presentation data; reducers preserve these fields. The domain Connection
    remains unchanged.
    """

    retry_attempt: int = 0
    not_live_since: float | None = None


@dataclass
class SyncController:
    transport: GatewayTransport
    backoff: Backoff = field(default_factory=Backoff)
    sleep: Callable[[float], Any] = asyncio.sleep
    clock: Callable[[], float] = time.monotonic
    state: AppState = field(default_factory=AppState)
    recover_auth: Callable[[], Awaitable[bool]] | None = None
    _listeners: list[Callable[[AppState], None]] = field(default_factory=list)
    _resync_event: asyncio.Event = field(default_factory=asyncio.Event)
    _resume_snapshot: tuple[str, dict[str, Any]] | None = None
    _loop: asyncio.AbstractEventLoop | None = None
    _stream_started: float | None = None
    _retry_attempt: int = 0
    _outage_since: float | None = None
    _resume_listeners: list[Callable[[], None]] = field(default_factory=list)
    last_run_exit: str = "not_started"
    _gap_boundary: tuple[str, str, str] | None = None
    _gap_failures: int = 0
    _gap_sequence: int = 0
    inventory_updates: InventoryUpdates = field(default_factory=InventoryUpdates)

    def _reset_gap_failures(self) -> None:
        self._gap_boundary = None
        self._gap_failures = 0
        self._gap_sequence = 0

    def gap_stalled_at(self, host_id: str) -> bool:
        """Keep lifecycle resubscription stopped at the failed host boundary."""
        host = self.state.inventory.hosts.get(host_id)
        return self.state.connection.detail == "gap_stalled" and self._gap_boundary == (
            host_id,
            host.epoch if host else "",
            self.state.connection.cursor,
        )

    def _gap_stalled(self, host_id: str, code: str) -> bool:
        if code != "gap":
            self._reset_gap_failures()
            return False
        host = self.state.inventory.hosts.get(host_id)
        boundary = (host_id, host.epoch if host else "", self.state.connection.cursor)
        self._gap_failures = self._gap_failures + 1 if boundary == self._gap_boundary else 1
        self._gap_boundary = boundary
        self._gap_sequence = self.state.connection.last_sequence
        return self._gap_failures >= MAX_IDENTICAL_GAP_FAILURES

    def subscribe_resume(self, listener: Callable[[], None]) -> None:
        self._resume_listeners.append(listener)

    def unsubscribe_resume(self, listener: Callable[[], None]) -> None:
        if listener in self._resume_listeners:
            self._resume_listeners.remove(listener)

    def resume_ui(self) -> None:
        """Same verified identity: refresh subscribers, revive only a dead task.

        No Root rebuild, terminal attach, command retry or credential replay.
        """
        self._emit()
        for listener in list(self._resume_listeners):
            listener()

    def task_failed(self, error: BaseException) -> None:
        # Exception text can contain network/credential data. Record only
        # its type and a fixed actionable state; consume it at the owner.
        self.last_run_exit = f"error:{type(error).__name__}"
        if not self.state.revoked:
            self._set(
                reducers.set_connection(
                    self.state, ConnectionState.DISCONNECTED, "sync_task_failed"
                )
            )

    def _reconnecting(self, reason: str) -> None:
        if self.state.revoked:
            return
        self._retry_attempt += 1
        if self._outage_since is None:
            self._outage_since = self.clock()
        connection = self.state.connection
        connection = SyncConnection(
            state=ConnectionState.RECONNECTING,
            host_id=connection.host_id,
            cursor=connection.cursor,
            last_sequence=connection.last_sequence,
            detail=reason,
            retry_attempt=self._retry_attempt,
            not_live_since=self._outage_since,
        )
        self._set(replace(self.state, connection=connection, stale=True))

    def _stream_opened(self) -> None:
        if self.state.revoked:
            return
        if self._stream_started is None:
            self._stream_started = self.clock()
            self._set(reducers.set_connection(self.state, ConnectionState.LIVE))

    def _stable_stream(self) -> bool:
        return self._stream_started is not None and self.clock() - self._stream_started >= 30

    def subscribe(self, listener: Callable[[AppState], None]) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[AppState], None]) -> None:
        """Remove a previous subscriber (host switching must not leak
        emitters; idempotent)."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    def request_resync(self) -> None:
        """Recycle a possibly half-open stream through snapshot and cursor resume. Cancel the
        parked frame pull even if no more frames arrive.
        """
        loop = self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(self._resync_event.set)
            except RuntimeError:
                self._resync_event.set()  # loop closed; the flag still trips `wait()`
            return
        self._resync_event.set()

    def _emit(self) -> None:
        for listener in list(self._listeners):
            listener(self.state)

    def _host_ungranted(self, host_id: str) -> bool:
        granted = self.state.granted_hosts
        return granted is not None and host_id not in granted

    def _set(self, state: AppState) -> None:
        # Revocation is terminal: late status or metadata work must not replace it. A new
        # sign-in builds a new controller.
        if self.state.revoked and not (
            state.revoked and state.connection.state == ConnectionState.ACCESS_REVOKED
        ):
            return
        self.state = state
        if state.revoked:
            self.inventory_updates.reset()
        self._emit()

    def apply_grants(self, entries: list[dict[str, Any]]) -> AppState:
        """Merge verified host grants into authoritative state.

        Later sync emissions cannot restore revoked hosts or their selections.
        """
        self._set(reducers.merge_hosts(self.state, entries))
        return self.state

    def accept_snapshot(self, host_id: str, doc: dict[str, Any]) -> AppState:
        """Merge a creation read while preserving the current metadata cursor/liveness."""
        validate("relaydeck/gateway/v1/snapshot", doc)
        if doc["host"]["id"] != host_id:
            raise TransportError("upstream_incompatible", "snapshot host mismatch", False)
        updated = reducers.apply_snapshot(self.state, snapshot_from_wire(doc))
        self.inventory_updates.reset()
        self._set(replace(updated, connection=self.state.connection))
        return self.state

    def prepare_resume(self, host_id: str, doc: dict[str, Any]) -> None:
        """Seed the next metadata run from the lifecycle's validated fresh snapshot.

        This queues cursor reconnection; it does not assert a connected socket.
        """
        self.accept_snapshot(host_id, doc)
        self._resume_snapshot = (host_id, doc)

    def select(self, host_id: str, session_key: str, terminal_kind: str) -> AppState:
        """Store route selection in authoritative state so sync preserves it."""
        self._set(reducers.select_session(self.state, host_id, session_key, terminal_kind))
        return self.state

    async def run(self, host_id: str, stop: Callable[[], bool] = lambda: False) -> AppState:
        """Run until stop() or revocation. Returns the final state."""
        self._loop = asyncio.get_running_loop()
        self.last_run_exit = "running"
        self._retry_attempt = 0
        self._outage_since = None
        self._reset_gap_failures()
        recovered_auth = False
        self._set(reducers.set_connection(self.state, ConnectionState.CONNECTING))
        while not stop() and not self.state.revoked:
            try:
                self._stream_started = None
                await self._sync_once(host_id, stop)
                return self.state
            except TransportError as exc:
                if stop() or self.state.revoked:
                    return self.state
                if self._gap_stalled(host_id, exc.code):
                    self._reconnecting("gap")
                    self._set(
                        reducers.set_connection(
                            self.state, ConnectionState.DISCONNECTED, "gap_stalled"
                        )
                    )
                    return self.state
                if self._stable_stream():
                    recovered_auth = False
                    self.backoff.reset()
                    self._retry_attempt = 0
                    self._outage_since = None
                if exc.code in ("unauthenticated", "forbidden"):
                    if exc.code == "unauthenticated" and self.recover_auth and not recovered_auth:
                        recovered_auth = True
                        self._reconnecting("reauthenticating")
                        try:
                            recovered = await self.recover_auth()
                        except TransportError as recovery_error:
                            self._set(
                                reducers.set_connection(
                                    self.state, ConnectionState.DISCONNECTED, recovery_error.code
                                )
                            )
                            return self.state
                        if stop() or self.state.revoked:
                            return self.state
                        if recovered:
                            if self._host_ungranted(host_id):
                                self._set(
                                    reducers.set_connection(
                                        self.state, ConnectionState.DISCONNECTED, "host_revoked"
                                    )
                                )
                                return self.state
                            await self.sleep(self.backoff.next_delay_ms() / 1000)
                            continue
                    if exc.code == "forbidden" and self._host_ungranted(host_id):
                        # Per-host grant loss, not account revocation: the
                        # verified listing dropped this host, so a gateway
                        # denial for it must not nuke writes account-wide.
                        self._set(
                            reducers.set_connection(
                                self.state, ConnectionState.DISCONNECTED, "host_revoked"
                            )
                        )
                        return self.state
                    self._set(reducers.revoke(self.state))
                    return self.state
                if not exc.retryable:
                    self._set(
                        reducers.set_connection(self.state, ConnectionState.DISCONNECTED, exc.code)
                    )
                    return self.state
                self._reconnecting(exc.code)
                await self.sleep(self.backoff.next_delay_ms() / 1000)
        return self.state

    async def _sync_once(self, host_id: str, stop: Callable[[], bool]) -> None:
        # A signal received before this fresh snapshot is already covered
        # by it. Keep signals DURING the snapshot/pull: those still recycle.
        if self.state.revoked:
            return
        signaled = self._resync_event.is_set()
        self._resync_event.clear()
        if self.state.connection.state != ConnectionState.RECONNECTING:
            self._set(
                replace(
                    reducers.set_connection(self.state, ConnectionState.SYNCHRONIZING), stale=True
                )
            )
        prepared: tuple[str, dict[str, Any]] | None = None if signaled else self._resume_snapshot
        self._resume_snapshot = None
        raw = (
            prepared[1]
            if prepared is not None and prepared[0] == host_id
            else await self.transport.snapshot(host_id)
        )
        if self.state.revoked:
            return
        try:
            validate("relaydeck/gateway/v1/snapshot", raw)
        except SchemaError as exc:
            raise TransportError("upstream_incompatible", f"bad snapshot: {exc}", False) from exc
        snapshot = snapshot_from_wire(raw)
        self.inventory_updates.reset()
        self._set(replace(reducers.apply_snapshot(self.state, snapshot), stale=True))
        stream = self.transport.stream(host_id, snapshot.cursor)
        pull_iter = stream.__aiter__()
        try:
            while True:
                if stop() or self.state.revoked:
                    return
                # Race the next frame against the link signal so a parked pull (half-open socket)
                # still recycles. Cancelling the pull leaves no __anext__ in flight, so the final
                # close is legal.
                pull = asyncio.ensure_future(pull_iter.__anext__())
                wake = asyncio.ensure_future(self._resync_event.wait())
                watched: set[asyncio.Task[Any]] = {pull, wake}
                try:
                    done, _pending = await asyncio.wait(
                        watched, return_when=asyncio.FIRST_COMPLETED
                    )
                except BaseException:
                    pull.cancel()
                    wake.cancel()
                    await asyncio.gather(pull, wake, return_exceptions=True)
                    raise
                if self.state.revoked:
                    pull.cancel()
                    wake.cancel()
                    await asyncio.gather(pull, wake, return_exceptions=True)
                    return
                if wake in done:
                    # Link signal: recycle now. Rapid signals coalesce
                    # into this single recovery: the event clears exactly
                    # once, so N rapid signals yield one resync.
                    self._resync_event.clear()
                    pull.cancel()
                    wake.cancel()
                    await asyncio.gather(pull, wake, return_exceptions=True)
                    raise TransportError("link_change", "connectivity changed", True)
                wake.cancel()
                await asyncio.gather(wake, return_exceptions=True)
                try:
                    frame = pull.result()
                except StopAsyncIteration:
                    if stop():
                        return
                    raise TransportError("stream_closed", "Metadata stream closed", True) from None
                if frame.event == "stream_open":
                    # Internal transport signal after HTTP acceptance, not
                    # a gateway/upstream event. Idle hosts need this too.
                    self._accept_stream_open(frame, host_id, snapshot.cursor)
                else:
                    self._apply_frame(frame)
                if self._stable_stream():
                    self.backoff.reset()
                    self._retry_attempt = 0
                    self._outage_since = None
        finally:
            await stream.aclose()

    def _accept_stream_open(self, frame: SseFrame, host_id: str, cursor: str) -> None:
        if self.state.revoked:
            return
        if frame.data:
            try:
                boundary = json.loads(frame.data)
            except ValueError:
                boundary = None
            if (
                not isinstance(boundary, dict)
                or set(boundary) != {"host_id", "cursor", "sequence"}
                or boundary["host_id"] != host_id
                or boundary["cursor"] != cursor
                or type(boundary["sequence"]) is not int
                or boundary["sequence"] < 0
            ):
                raise TransportError("upstream_incompatible", "bad stream boundary", False)
            if self._stream_started is None:
                # apply_snapshot resets the reducer's sequence. Restore the
                # authenticated replay boundary before checking any event;
                # never infer it from the first event or parse opaque cursors.
                self._set(
                    replace(
                        self.state,
                        connection=replace(
                            self.state.connection, last_sequence=boundary["sequence"]
                        ),
                    )
                )
        self._stream_opened()

    def _apply_frame(self, frame: SseFrame) -> None:
        if frame.event == "error":
            try:
                doc = json.loads(frame.data or "{}")
            except ValueError:
                doc = {}
            code = str(doc.get("code", "upstream_incompatible"))
            if code in ("unauthenticated", "forbidden"):
                raise TransportError(code, code, False)
            elif code in ("stale_epoch", "stale_revision"):
                self._set(reducers.set_connection(self.state, ConnectionState.SYNCHRONIZING, code))
                raise TransportError(code, code, True)
            else:
                raise TransportError(code, code, bool(doc.get("retryable", True)))
            return
        if frame.event != "metadata":
            return
        try:
            doc = json.loads(frame.data)
            validate_metadata_event(doc)
        except (ValueError, SchemaError):
            self._set(
                reducers.set_connection(self.state, ConnectionState.SYNCHRONIZING, "bad_event")
            )
            raise TransportError("upstream_incompatible", "bad event", False) from None
        previous_sequence = self.state.connection.last_sequence
        before = self.state
        event = event_from_wire(doc)
        state, action = reducers.apply_event(self.state, event)
        if action not in ("gap", "stale_epoch"):
            # Scripted adapters can prove opening by a validated frame.
            # Production uses stream_open, including on an idle host.
            self._stream_opened()
            state, action = reducers.apply_event(self.state, event)
        self.inventory_updates.observe(before, event, action)
        self._set(state)
        if state.connection.last_sequence > previous_sequence and self._gap_boundary is not None:
            host = state.inventory.hosts.get(state.connection.host_id)
            identity = (state.connection.host_id, host.epoch if host else "")
            # A snapshot may rewind to a retained replay prefix. Reapplying
            # that prefix is not new progress past the previous gap boundary.
            if (
                identity != self._gap_boundary[:2]
                or state.connection.last_sequence > self._gap_sequence
            ):
                self._reset_gap_failures()
        if action in ("gap", "stale_epoch"):
            raise TransportError(action, action, True)
