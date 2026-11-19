"""Authenticated connector sessions with bounded intake, ordered durable replies and exact-byte
terminal routing.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import Awaitable
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from relaydeck.contracts import paths
from relaydeck.contracts.schemas import SchemaError, validate
from relaydeck.contracts.tunnel import SnapshotIntake
from relaydeck.gateway.app.command_io import command_io
from relaydeck.gateway.app.state import GatewayState
from relaydeck.gateway.features.terminals.api import (
    TunnelCleanup,
)
from relaydeck.gateway.http.bounds import (
    MAX_SNAPSHOT_BYTES,
    TUNNEL_MAX_OUTPUT,
    TUNNEL_MAX_TEXT,
    TUNNEL_POLL_MAX_S,
    TUNNEL_POLL_S,
)
from relaydeck.gateway.http.context import RouteContext
from relaydeck.gateway.http.sockets import (
    register_wake,
    tunnel_wakes,
    unregister_wake,
    wake_host_viewers,
    wake_terminal_viewers,
)
from relaydeck.gateway.ports import BackendError
from relaydeck.host.concurrency.offload import settled
from relaydeck.host.transport.queues import (
    BoundedInbox,
    TunnelCapacityError,
    TunnelIntake,
    TunnelWriter,
    cancel_tasks,
)

LOG = logging.getLogger("relaydeck.gateway.app")


def build_connector_router(state: GatewayState, ctx: RouteContext) -> APIRouter:
    router = APIRouter()
    emit_command_update = ctx.emit_command_update

    @router.websocket(paths.CONNECTORS)
    async def connector_socket(websocket: WebSocket) -> None:
        # Bearer header preferred (query strings land in access logs);
        # the query form stays for compatibility.
        token = websocket.query_params.get("token", "")
        if not token:
            scheme, _, value = websocket.headers.get("authorization", "").partition(" ")
            if scheme.lower() == "bearer" and value:
                token = value
        record = state.hosts.lookup(token)
        if record is None:
            await websocket.close(code=1008, reason="unauthenticated")
            return
        if state.sockets.host_count(record.host_id) >= 2:
            # One current tunnel plus its retiring predecessor; replacement storms
            # cannot accumulate connection-local queues or blocked cleanup lanes.
            await websocket.close(code=1013, reason="tunnel_busy")
            return
        # Single live tunnel per host (OWASP flood/DoS bound): a new
        # tunnel replaces a stale one, and arrival order on the survivor
        # is the terminal-output order.
        displaced = state.sockets.host_sockets(record.host_id)
        await websocket.accept()
        state.sockets.register_host(record.host_id, websocket, record)
        intake = SnapshotIntake()
        last_epochs = state.backend.epochs_for_host(record.host_id, record.account)
        last_receive = time.monotonic()

        def current() -> bool:
            return state.hosts.lookup(token) == record and state.sockets.host_current(
                record.host_id, websocket
            )

        writer = TunnelWriter(websocket.send_text, current)
        state.sockets.bind_writer(websocket, writer)
        outbox_wake = register_wake(tunnel_wakes, record.host_id)
        pump_busy = False
        incoming: BoundedInbox[dict[str, Any] | None] = BoundedInbox()
        commands: BoundedInbox[dict[str, Any]] = BoundedInbox()
        admission = TunnelIntake(incoming, intake)
        cleanup = TunnelCleanup(state.sockets)
        cleanup.add(displaced, "", "replaced")

        async def timed[T](branch: str, work: Awaitable[T]) -> T:
            started = time.monotonic()
            try:
                return await work
            finally:
                elapsed = time.monotonic() - started
                if elapsed > 0.5:
                    LOG.warning("tunnel_stall branch=%s ms=%.0f", branch, elapsed * 1000)

        async def send_hello() -> None:
            await state.sockets.send_text(
                websocket,
                json.dumps(
                    {
                        "kind": "hello",
                        "host_id": record.host_id,
                        "snapshot_chunks": True,
                        "epochs": last_epochs,
                    }
                ),
            )

        async def pump_outbox() -> bool:
            """Drain journaled dispatches down this tunnel. The
            write-ahead marker is journaled BEFORE each send; a marker
            miss skips the send (never re-dispatch, including
            TTL-refused markers, which settle expired), and a send
            failure ends the tunnel (bytes are never silently lost)."""
            nonlocal last_epochs, pump_busy
            pump_busy = False
            if state.hosts.lookup(token) != record:
                await state.sockets.close_socket(websocket, "unauthenticated")
                return False
            epochs = state.backend.epochs_for_host(record.host_id, record.account)
            if epochs != last_epochs:
                pump_busy = True
                try:
                    await state.sockets.send_text(
                        websocket,
                        json.dumps(
                            {
                                "kind": "terminal.epochs",
                                "host_id": record.host_id,
                                "epochs": epochs,
                            }
                        ),
                    )
                except Exception:
                    return False
                last_epochs = epochs
            pending = await command_io(state.backend, "drain_outbox", record.host_id)
            pump_busy = pump_busy or bool(pending)
            consumed = 0
            try:
                for index, message in enumerate(pending):
                    consumed = index
                    if message.get("principal") != record.account or not record.account:
                        LOG.error(
                            "refused cross-account connector dispatch for host %s", record.host_id
                        )
                        consumed = index + 1
                        continue
                    command_id = str(message.get("command_id", ""))

                    async def before(command_id: str = command_id) -> bool:
                        if not command_id or not current():
                            return False
                        ready = await command_io(
                            state.backend, "mark_command_dispatched", command_id, state.now_ms()
                        )
                        emit_command_update(
                            await command_io(state.backend, "command_status", command_id)
                        )
                        return bool(ready)

                    try:
                        await writer.send(json.dumps(message), before=before)
                    except Exception:
                        return False
                    consumed = index + 1
                consumed = len(pending)
            finally:
                binding = state.hosts.record_for_host(record.host_id)
                restore = (
                    pending[consumed:]
                    if binding is not None and binding.account == record.account
                    else []
                )
                state.backend.restore_outbox(record.host_id, restore, pending)
            return True

        async def ingest_snapshot(snapshot: dict[str, Any], now_ms: int) -> bool:
            try:
                if len(json.dumps(snapshot).encode()) > MAX_SNAPSHOT_BYTES:
                    return False
                validate("relaydeck/gateway/v1/snapshot", snapshot)
            except (ValueError, TypeError, SchemaError):
                return False
            host = snapshot.get("host", {})
            if host.get("id") != record.host_id:
                return False
            try:
                previous_epoch = state.backend.snapshot(record.host_id).get("host", {}).get("epoch")
            except BackendError:
                previous_epoch = None
            if "ok" not in state.backend.ingest_snapshot(
                record.host_id, snapshot, now_ms, state.cursor_secret
            ):
                return False
            state.notifications.snapshot(record.account, record.host_id, snapshot, now_ms)
            if previous_epoch is not None and previous_epoch != host.get("epoch"):
                reset_doc = json.dumps({"kind": "reset", "detail": "stale_epoch"})
                for lease_id in state.backend.rotate_streams(record.host_id):
                    cleanup.add(state.sockets.lease_sockets(lease_id), reset_doc, "stale_revision")
                wake_host_viewers(record.host_id)
            return True

        async def handle_tunnel_doc(doc: dict[str, Any]) -> bool:
            """Validate one connector message, returning False to close a malformed or
            unauthorized tunnel. Only the ordered command lane awaits durable
            acknowledgments/results.
            """
            kind = doc.get("kind")
            now_ms = state.now_ms()
            if kind == "terminal.reset_required":
                if (
                    doc.get("host_id") != record.host_id
                    or not isinstance(doc.get("terminal_id"), str)
                    or not 1 <= len(doc["terminal_id"]) <= 128
                    or not isinstance(doc.get("epoch"), str)
                    or not 1 <= len(doc["epoch"]) <= 128
                ):
                    return False
                dead = state.backend.reset_terminal(
                    record.host_id, doc["terminal_id"], doc["epoch"], record.account
                )
                wake_terminal_viewers(record.host_id, doc["terminal_id"])
                for lease_id in dead:
                    cleanup.add(
                        state.sockets.lease_sockets(lease_id),
                        json.dumps({"kind": "reset", "detail": "stale_epoch"}),
                        "stale_revision",
                    )
                return True
            if kind == "terminal.attach_refused":
                if (
                    doc.get("host_id") != record.host_id
                    or not isinstance(doc.get("terminal_id"), str)
                    or not 1 <= len(doc["terminal_id"]) <= 128
                    or not isinstance(doc.get("epoch"), str)
                    or not 1 <= len(doc["epoch"]) <= 128
                    or doc.get("reason") != "terminal_owned"
                ):
                    return False
                refused = state.backend.refuse_attachment(
                    record.host_id, doc["terminal_id"], doc["epoch"], record.account
                )
                wake_terminal_viewers(record.host_id, doc["terminal_id"])
                for lease_id in refused:
                    # A typed conflict: the viewer shows the holder panel and never retries
                    # on its own; only an explicit Take over attaches again.
                    cleanup.add(
                        state.sockets.lease_sockets(lease_id),
                        json.dumps({"kind": "reset", "detail": "lease_conflict"}),
                        "lease_conflict",
                    )
                return True
            if kind == "terminal.input_failed":
                if (
                    doc.get("host_id") != record.host_id
                    or not isinstance(doc.get("lease_id"), str)
                    or type(doc.get("generation")) is not int
                    or type(doc.get("input_seq")) is not int
                    or not 1 <= doc["input_seq"] <= 0xFFFFFFFF
                ):
                    return False
                failed_lease = state.backend.open_lease(doc["lease_id"])
                if failed_lease is None:
                    return True
                if (
                    failed_lease.host_id != record.host_id
                    or failed_lease.owner != record.account
                    or failed_lease.generation != doc["generation"]
                ):
                    return False
                cleanup.add(
                    state.sockets.lease_sockets(failed_lease.lease_id),
                    json.dumps(
                        {
                            "kind": "gap",
                            "detail": "input_uncertain",
                            "generation": doc["generation"],
                            "input_seq": doc["input_seq"],
                        }
                    ),
                )
                return True
            if kind == "command.ack":
                if doc.get("host_id") != record.host_id or not isinstance(
                    doc.get("command_id"), str
                ):
                    return False
                command_id = str(doc["command_id"])
                if await command_io(state.backend, "command_owner", command_id) != (
                    record.account,
                    record.host_id,
                ):
                    return False
                if not current():
                    raise ConnectionError("tunnel_superseded")
                updated = await command_io(
                    state.backend, "note_command_ack", command_id, record.host_id, now_ms
                )
                emit_command_update(updated)
                return updated is not None
            if kind == "command.result":
                if (
                    doc.get("host_id") != record.host_id
                    or not isinstance(doc.get("command_id"), str)
                    or doc.get("status") not in ("succeeded", "failed", "rejected", "unknown")
                    or not isinstance(doc.get("result_digest", ""), str)
                    or not isinstance(doc.get("error_code", ""), str)
                    or ("result" in doc and not isinstance(doc["result"], dict))
                ):
                    return False
                if await command_io(state.backend, "command_owner", str(doc["command_id"])) != (
                    record.account,
                    record.host_id,
                ):
                    return False
                if not current():
                    raise ConnectionError("tunnel_superseded")
                updated = await command_io(
                    state.backend,
                    "note_command_result",
                    str(doc["command_id"]),
                    record.host_id,
                    str(doc["status"]),
                    str(doc.get("result_digest", "")),
                    str(doc.get("error_code", "")),
                    doc.get("result"),
                    now_ms,
                )
                emit_command_update(updated)
                return updated is not None
            if kind == "terminal.output":
                data_b64 = doc.get("data_b64", "")
                if (
                    doc.get("host_id") != record.host_id
                    or not isinstance(doc.get("terminal_id"), str)
                    or not doc.get("terminal_id")
                    or not isinstance(doc.get("epoch"), str)
                    or not isinstance(data_b64, str)
                    or not data_b64
                ):
                    return False
                try:
                    data = base64.b64decode(data_b64, validate=True)
                except ValueError:
                    return False
                if len(data) > TUNNEL_MAX_OUTPUT:
                    return False
                # Semantic misses (unknown terminal, rotated epoch) drop
                # the frame but keep the host tunnel: one stale stream
                # must not kill the whole host link.
                state.backend.ingest_terminal_output(
                    record.host_id, str(doc["terminal_id"]), str(doc["epoch"]), data, now_ms
                )
                wake_terminal_viewers(record.host_id, str(doc["terminal_id"]))
                return True
            if kind == "snapshot":
                # Connector metadata sync: schema-validated,
                # host-bound, replaces. A bad snapshot fails the tunnel
                # closed (never stored, never served).
                snapshot = doc.get("snapshot")
                if doc.get("host_id") != record.host_id or not isinstance(snapshot, dict):
                    return False
                return await ingest_snapshot(snapshot, now_ms)
            if kind == "event":
                # Connector metadata sync: partial event, the
                # server stamps sequence/cursor and dedupes by event_id.
                partial = doc.get("event")
                if doc.get("host_id") != record.host_id or not isinstance(partial, dict):
                    return False
                try:
                    current_epoch = state.backend.snapshot(record.host_id)["host"]["epoch"]
                except (BackendError, KeyError):
                    return False
                if partial.get("host_epoch") != current_epoch:
                    return False
                event_type = partial.get("type")
                if not isinstance(event_type, str):
                    return False
                status_key = {"pane.upserted": "agent_status", "agent.upserted": "status"}.get(
                    event_type
                )
                payload = partial.get("payload")
                if status_key and isinstance(payload, dict) and status_key in payload:
                    value = payload[status_key]
                    if not isinstance(value, str) or value not in {
                        "idle",
                        "working",
                        "blocked",
                        "done",
                        "unknown",
                    }:
                        return False
                result = state.backend.ingest_event(
                    record.host_id, partial, now_ms, state.cursor_secret
                )
                if "ok" not in result:
                    return False
                if not result.get("duplicate"):
                    state.notifications.event(record.account, record.host_id, result["ok"], now_ms)
                return True
            return False

        async def receive_documents() -> None:
            nonlocal last_receive
            while True:
                # Bound waits while a snapshot transfer is active; otherwise socket readiness wakes
                # intake.
                timeout = (
                    min(TUNNEL_POLL_S, max(0.0, intake.deadline - time.monotonic()))
                    if intake.active
                    else None
                )
                try:
                    # Not through `timed`: waiting for the next frame is
                    # idle time, not branch work, and must not log a stall.
                    message = await asyncio.wait_for(websocket.receive(), timeout=timeout)
                except TimeoutError:
                    intake.check_deadline(time.monotonic())
                    continue
                last_receive = time.monotonic()
                if not current():
                    raise ConnectionError("tunnel_superseded")
                if message["type"] == "websocket.disconnect":
                    incoming.put(None, 0, control=True)
                    await asyncio.Future()
                text = message.get("text")
                if not isinstance(text, str) or len(text.encode()) > TUNNEL_MAX_TEXT:
                    raise ValueError("bad_tunnel_frame")
                doc = json.loads(text)
                if not isinstance(doc, dict):
                    raise ValueError("bad_tunnel_frame")
                if doc.get("kind") == "ping":
                    writer.post((json.dumps({"kind": "pong"}),), control=True)
                else:
                    size = len(text.encode())
                    # Both consumers (including active work and partial snapshots)
                    # share the same byte budget; journal waits cannot stall output.
                    if (
                        incoming.bytes + commands.bytes + admission.batch_bytes + size
                        > incoming.byte_limit
                    ):
                        raise TunnelCapacityError("tunnel_queue_full")
                    if doc.get("kind") in ("command.ack", "command.result"):
                        intake.check_deadline(time.monotonic())
                        if intake.active:
                            raise ValueError("snapshot_interleaved")
                        commands.put(doc, size)
                    else:
                        admission.put(doc, size, record.host_id, time.monotonic())
                    if doc.get("kind") == "bye":
                        await asyncio.Future()

        async def process_documents() -> None:
            while True:
                entry = await incoming.get()
                try:
                    doc = entry[0]
                    if doc is None or doc.get("kind") == "bye":
                        # A graceful close still settles all preceding replies.
                        await commands.queue.join()
                        return
                    if not current():
                        return
                    kind = doc.get("kind")
                    branch = (
                        kind
                        if kind
                        in (
                            "snapshot",
                            "snapshot.begin",
                            "snapshot.chunk",
                            "snapshot.end",
                            "event",
                            "terminal.output",
                            "terminal.reset_required",
                            "terminal.attach_refused",
                            "terminal.input_failed",
                        )
                        else "unknown_doc"
                    )
                    if not await timed(str(branch), handle_tunnel_doc(doc)):
                        raise ValueError("bad_tunnel_document")
                    outbox_wake.set()  # epochs or ownership may have moved
                finally:
                    incoming.finish(entry)

        async def process_commands() -> None:
            """FIFO replies; started persistence settles before this tunnel retires."""
            while True:
                entry = await commands.get()
                try:
                    if not current():
                        return
                    # Own the whole reply, not only its individual journal awaits.
                    # Supersession during owner lookup is checked before mutation.
                    accepted = await settled(
                        asyncio.create_task(
                            timed(str(entry[0]["kind"]), handle_tunnel_doc(entry[0]))
                        )
                    )
                    if not accepted:
                        raise ValueError("bad_tunnel_document")
                finally:
                    commands.finish(entry)

        async def outbox() -> None:
            delay = TUNNEL_POLL_S
            while current():
                # Clear before pumping: a wake during the pump then makes
                # the wait below return at once instead of being lost.
                outbox_wake.clear()
                # Retain the drained list through cancellation, including the journal commit.
                if not await timed("pump_outbox", settled(asyncio.create_task(pump_outbox()))):
                    raise ConnectionError("tunnel_write_failed")
                if not current():
                    break
                delay = (
                    TUNNEL_POLL_S
                    if pump_busy
                    else min(max(TUNNEL_POLL_MAX_S, TUNNEL_POLL_S), delay * 2)
                )
                try:
                    async with asyncio.timeout(delay):
                        await outbox_wake.wait()
                except TimeoutError:
                    pass

        cleanup_task = asyncio.create_task(cleanup.run())
        tasks = [asyncio.create_task(writer.run())]
        try:
            await timed("hello", send_hello())
            tasks.extend(
                [
                    asyncio.create_task(outbox()),
                    asyncio.create_task(receive_documents()),
                    asyncio.create_task(process_documents()),
                    asyncio.create_task(process_commands()),
                ]
            )
            done, _ = await asyncio.wait(
                [*tasks, cleanup_task], return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()
        except TunnelCapacityError:
            with suppress(Exception):
                await state.sockets.close_socket(websocket, "tunnel_queue_full")
        except (WebSocketDisconnect, TimeoutError, ConnectionError, ValueError):
            with suppress(Exception):
                await state.sockets.close_socket(websocket, "forbidden")
        except asyncio.CancelledError:
            return  # An ASGI disconnect still joins all owned work below.
        finally:
            LOG.warning(
                "tunnel_loop idle_recv_s=%.3f queued=%d bytes=%d oldest_s=%.3f writer=%s "
                "write_queued=%d write_bytes=%d write_oldest_s=%.3f "
                "command_queued=%d command_bytes=%d command_oldest_s=%.3f",
                time.monotonic() - last_receive,
                incoming.count,
                incoming.bytes,
                incoming.oldest_age,
                writer.phase,
                writer.inbox.count,
                writer.inbox.bytes,
                writer.inbox.oldest_age,
                commands.count,
                commands.bytes,
                commands.oldest_age,
            )

            async def finish_tunnel() -> None:
                unregister_wake(tunnel_wakes, record.host_id, outbox_wake)
                state.sockets.unregister(websocket)
                await cancel_tasks(tasks)
                await cleanup.finish(cleanup_task)
                if not state.sockets.host_count(record.host_id):
                    state.notifications.drop_host(record.host_id)

            with suppress(asyncio.CancelledError):
                await settled(asyncio.create_task(finish_tunnel()))

    return router
