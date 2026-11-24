"""The connector tunnel and terminal viewer WebSocket routes."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from relaydeck.contracts import paths
from relaydeck.contracts.schemas import SchemaError, validate
from relaydeck.contracts.tunnel import parse_terminal_input
from relaydeck.contracts.wire import terminal as terminal_wire
from relaydeck.gateway.features.terminals.api import (
    FIRST_REPLAY_MAX_BYTES,
    MAX_LEASE_ID_CHARS,
    VIEWER_QUEUE_MAX_BYTES,
)
from relaydeck.gateway.http.bounds import (
    TERMINAL_LEASE_CHECK_MAX_S,
    TUNNEL_MAX_TEXT,
)
from relaydeck.gateway.http.context import RouteContext
from relaydeck.gateway.ports import BackendError
from relaydeck.lease_diagnostics import LeaseTrace, opaque

_Wake = tuple[asyncio.AbstractEventLoop, asyncio.Event]
tunnel_wakes: dict[str, set[_Wake]] = {}
_viewer_wakes: dict[tuple[str, str], set[_Wake]] = {}


def register_wake[K](index: dict[K, set[_Wake]], key: K) -> asyncio.Event:
    event = asyncio.Event()
    index.setdefault(key, set()).add((asyncio.get_running_loop(), event))
    return event


def unregister_wake[K](index: dict[K, set[_Wake]], key: K, event: asyncio.Event) -> None:
    bucket = index.get(key)
    if bucket is None:
        return
    bucket.difference_update({entry for entry in bucket if entry[1] is event})
    if not bucket:
        index.pop(key, None)


def _signal(wakes: set[_Wake]) -> None:
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    for loop, event in tuple(wakes):
        if loop.is_closed():
            continue
        if running is loop:
            event.set()
        else:
            loop.call_soon_threadsafe(event.set)


def wake_tunnel(host_id: str) -> None:
    """Wake a host tunnel's outbox pump after new work was enqueued for it."""
    _signal(tunnel_wakes.get(host_id, set()))


def wake_terminal_viewers(host_id: str, terminal_id: str) -> None:
    """Wake live viewers of one terminal after output or an epoch change."""
    _signal(_viewer_wakes.get((host_id, terminal_id), set()))


def wake_host_viewers(host_id: str) -> None:
    for (host, _terminal), wakes in tuple(_viewer_wakes.items()):
        if host == host_id:
            _signal(wakes)


def _discard(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        task.exception()


def build_terminal_socket_router(ctx: RouteContext) -> APIRouter:
    router = APIRouter()
    state = ctx.routes.sockets

    @router.websocket(paths.TERMINAL_SOCKET)
    async def terminal_socket(websocket: WebSocket, lease_id: str) -> None:
        ticket = websocket.query_params.get("ticket", "")
        grant = state.tickets.redeem(ticket, lease_id, state.now_ms()) if ticket else None
        if grant is None:
            await websocket.close(code=1008, reason="unauthenticated")
            return
        if "terminal.view" not in grant.capabilities:
            await websocket.close(code=1008, reason="unsupported_capability")
            return
        lease = state.backend.open_lease(lease_id)
        if lease is None or lease.host_id not in grant.hosts or grant.account != lease.owner:
            await websocket.close(code=1008, reason="lease_expired")
            return
        if grant.generation and grant.generation != lease.generation:
            await websocket.close(code=1008, reason="lease_expired")
            return
        reset_protocol = websocket.query_params.get("output_reset") == "v1"
        live_edge = websocket.query_params.get("live_edge") == "1"
        if live_edge and (not reset_protocol or lease.device_id != grant.device_id):
            await websocket.close(code=1008, reason="forbidden")
            return
        resume_params = "epoch" in websocket.query_params or "offset" in websocket.query_params
        resume_epoch = websocket.query_params.get("epoch", "")
        try:
            resume_offset = int(websocket.query_params.get("offset", "0"))
        except ValueError:
            await websocket.close(code=1008, reason="forbidden")
            return
        if resume_offset < 0 or len(resume_epoch) > MAX_LEASE_ID_CHARS:
            await websocket.close(code=1008, reason="forbidden")
            return
        if lease.resume_pending and not (
            resume_params
            and grant.resume
            and grant.generation == lease.generation
            and grant.device_id == lease.device_id
        ):
            await websocket.close(code=1008, reason="lease_expired")
            return
        await websocket.accept()
        close_logged = False

        class StreamTrace(LeaseTrace):
            def emit(self, event: str, **fields: Any) -> None:
                nonlocal close_logged
                if event == "socket_close":
                    if close_logged:
                        return
                    close_logged = True
                super().emit(event, **fields)

        trace = StreamTrace(
            opaque("principal", grant.account, grant.device_id),
            opaque("terminal", lease.host_id, lease.terminal_id),
            lease_id,
            lease.mode,
            lease.generation,
        )
        state.sockets.register(
            grant.account, lease_id, websocket, grant.device_id, diagnostics=trace
        )
        trace.emit("socket_open", reason="ok", source="server", socket=websocket)
        fanout = state.backend.fanout_for(lease.attachment_id)
        viewer_id = str(id(websocket))
        recovery_close = False
        viewer_wake = register_wake(_viewer_wakes, (lease.host_id, lease.terminal_id))
        lease_expires_ms = lease.expires_ms
        next_lease_check = time.monotonic()

        def disconnected() -> bool:
            return (
                getattr(websocket, "application_state", None) == WebSocketState.DISCONNECTED
                or getattr(websocket, "client_state", None) == WebSocketState.DISCONNECTED
            )

        def preserve_reader() -> None:
            # Publish the handoff before sending reset: the client can mint its replacement
            # ticket immediately, before this handler's close/finally gets another turn.
            if (
                lease.mode == "control"
                and grant.device_id == lease.device_id
                and not state.sockets.has_device_lease(
                    grant.account, grant.device_id, lease_id, excluding=websocket
                )
            ):
                state.backend.begin_reader_handoff(
                    lease_id, grant.account, grant.device_id, lease.generation
                )

        async def close_stream(reason: str) -> None:
            # Reset/revoke can close this socket while receive is suspended.
            # Registry removal claims the close before its first await; do not
            # send a second close or overwrite the first reset/revoke reason.
            if disconnected():
                return
            if reason in ("stale_epoch", "stale_offset", "overflow", "reset"):
                preserve_reader()
            await state.sockets.close_socket(websocket, reason)

        async def report_input_limit() -> bool:
            """Drop this input only; a rate limit does not end the output stream."""
            if disconnected():
                return False
            try:
                await state.sockets.send_text(
                    websocket, json.dumps({"kind": "gap", "detail": "rate_limited"})
                )
            except Exception:
                # Revocation/close can win after the state check. Never send again.
                return False
            return True

        async def forward_input(chunk: terminal_wire.InputChunk) -> None:
            record = state.hosts.record_for_host(lease.host_id)
            # FakeBackend remains an explicit synthetic recorder. Production
            # never treats a local input log as delivery to the home host.
            if record is None and not state.backend.requires_connector:
                return
            if record is None:
                raise BackendError("host_offline", "connector unavailable", True)
            if record.account != lease.owner or not record.account:
                raise BackendError("not_found", "no such terminal")
            if not state.sockets.host_count(lease.host_id, record):
                raise BackendError("host_offline", "connector unavailable", True)
            doc: dict[str, Any] = {
                "kind": "terminal.resize" if chunk.kind == "resize" else "terminal.input",
                "host_id": lease.host_id,
                "terminal_id": lease.terminal_id,
                "epoch": lease.epoch,
                "lease_id": lease_id,
                "generation": chunk.generation,
                "input_seq": chunk.input_seq,
            }
            if chunk.kind == "resize":
                doc.update(cols=chunk.cols, rows=chunk.rows)
            else:
                doc.update(input_kind=chunk.kind, data_b64=base64.b64encode(chunk.data).decode())
            parse_terminal_input(doc, lease.host_id)

            refused = ""

            async def still_allowed() -> bool:
                nonlocal refused
                current_lease = state.backend.open_lease(lease_id)
                valid = (
                    current_lease is not None
                    and current_lease.mode == "control"
                    and current_lease.generation == chunk.generation
                    and current_lease.epoch == lease.epoch
                    and current_lease.owner == grant.account
                    and current_lease.device_id == grant.device_id
                    and state.hosts.record_for_host(lease.host_id) == record
                )
                refused = (
                    "stale_revision"
                    if not valid
                    else (
                        "input_failed"
                        if current_lease is not None and current_lease.resume_pending
                        else ""
                    )
                )
                return not refused

            if not await state.sockets.notify_host(
                lease.host_id, json.dumps(doc), binding=record, before=still_allowed
            ):
                raise BackendError(
                    refused or "host_offline", "input not delivered; not replayed", True
                )

        async def push_pending() -> str | None:
            """Send queued live bytes. Returns None while flowing, else
            the reset detail that must close the socket: an overflowed
            (slow) viewer or a rotated epoch gets an explicit reset doc
            plus close, never silent loss presented as valid bytes."""
            nonlocal lease_expires_ms
            fresh_lease = state.backend.open_lease(lease_id)
            if fresh_lease is None:
                return "lease_expired"
            lease_expires_ms = fresh_lease.expires_ms
            queue = fanout.viewer(viewer_id) if fanout is not None else None
            if queue is None:
                return None
            if queue.reset_pending or queue.overflowed:
                detail = "stale_epoch" if queue.reset_pending else "overflow"
                preserve_reader()
                try:
                    await state.sockets.send_text(
                        websocket, json.dumps({"kind": "reset", "detail": detail})
                    )
                except Exception:  # noqa: S110 - reset notice is best-effort
                    pass
                return detail
            try:
                for offset, data in queue.pop_all():
                    # A queued payload may exceed the 64 KiB wire chunk:
                    # re-chunk with continuous offsets (never fail the
                    # viewer on a large upstream write).
                    for at in range(0, len(data), terminal_wire.MAX_OUTPUT_CHUNK):
                        piece = data[at : at + terminal_wire.MAX_OUTPUT_CHUNK]
                        await state.sockets.send_bytes(
                            websocket, terminal_wire.encode_output(queue.epoch, offset + at, piece)
                        )
            except Exception:
                return "reset"
            return None

        async def maybe_push(*, force_lease_check: bool = False) -> str | None:
            """Push only when bytes are queued or the lease re-check is due;
            an idle viewer does no lease lookup between expiry checks. After
            handling an incoming frame the lease is always re-checked, so a
            release that raced that frame closes the viewer promptly."""
            nonlocal next_lease_check
            queue = fanout.viewer(viewer_id) if fanout is not None else None
            queued = queue is not None and (
                queue.pending_bytes > 0 or queue.reset_pending or queue.overflowed
            )
            if not force_lease_check and not queued and time.monotonic() < next_lease_check:
                return None
            detail = await push_pending()
            expiry_delay = (
                (lease_expires_ms - state.now_ms()) / 1000
                if lease_expires_ms
                else TERMINAL_LEASE_CHECK_MAX_S
            )
            # A non-positive delay with a still-live lease means the state
            # and backend clocks disagree; re-check soon instead of spinning.
            next_lease_check = time.monotonic() + min(
                TERMINAL_LEASE_CHECK_MAX_S, expiry_delay if expiry_delay > 0 else 1.0
            )
            return detail

        async def push_or_close() -> bool:
            """Forced post-frame lease check shared by every nonterminal
            frame outcome. True when the stream is closing, so the caller
            returns instead of starting the next receive."""
            nonlocal recovery_close
            detail = await maybe_push(force_lease_check=True)
            if detail is None:
                return False
            recovery_close = detail in ("stale_epoch", "overflow", "reset")
            await close_stream(detail)
            return True

        recv_task: asyncio.Task[Any] | None = None
        wake_task: asyncio.Task[bool] | None = None
        try:
            if fanout is not None:
                # Least-privilege subscription (OWASP): this socket sees
                # only its own lease's attachment stream, and the id is
                # per-connection so co-viewers never share a queue.
                queue = fanout.subscribe(viewer_id)
                queue.on_change = lambda: wake_terminal_viewers(lease.host_id, lease.terminal_id)
            baseline: dict[str, Any] | None = None
            if reset_protocol and (live_edge or (not resume_epoch and resume_offset == 0)):
                # Subscribe and capture the authorized boundary without yielding. New output
                # enters this viewer's queue while the baseline and capped replay are sent.
                epoch = fanout.ring.epoch if fanout is not None else lease.epoch
                if live_edge and resume_epoch != epoch:
                    recovery_close = True
                    preserve_reader()
                    await state.sockets.send_text(
                        websocket,
                        json.dumps(
                            {
                                "kind": "reset",
                                "detail": "stale_epoch",
                            }
                        ),
                    )
                    await close_stream("stale_epoch")
                    return
                end = fanout.ring.end_offset if fanout is not None else 0
                base = fanout.ring.base_offset if fanout is not None else 0
                start = end if live_edge else max(base, end - FIRST_REPLAY_MAX_BYTES)
                baseline = {
                    "kind": "history_reset",
                    "epoch": epoch,
                    "offset": start,
                    "incomplete": live_edge or start > base,
                }
                resume_epoch, resume_offset, resume_params = epoch, start, True
            if resume_params:
                # ring resume: exact continuation while retained,
                # else an explicit reset doc (never a forged tail).
                try:
                    stream = state.backend.read_stream(lease_id, resume_epoch, resume_offset)
                except BackendError as exc:
                    await close_stream(exc.code)
                    return
                if stream.status != "ok":
                    recovery_close = stream.status in ("stale_epoch", "stale_offset")
                    if recovery_close:
                        preserve_reader()
                    await state.sockets.send_text(
                        websocket, json.dumps({"kind": "reset", "detail": stream.status})
                    )
                    await close_stream(stream.status)
                    return
                if lease.resume_pending:
                    try:
                        state.backend.finish_reader_handoff(
                            lease_id, grant.account, grant.device_id, grant.generation
                        )
                    except BackendError as exc:
                        await close_stream(exc.code)
                        return
                if grant.resume:
                    state.tickets.discard_resume(lease_id, grant.account, grant.device_id)
                if baseline is not None:
                    await state.sockets.send_text(websocket, json.dumps(baseline))
                for offset, data in stream.chunks:
                    await state.sockets.send_bytes(
                        websocket, terminal_wire.encode_output(stream.epoch, offset, data)
                    )
            else:
                # Ticket-only first connect replays retained ring bytes like a first-connect resume,
                # so pre-connect publishes (the attach greeting) reach the viewer; the ring is the
                # single backlog authority. Stale reads surface as explicit reset docs, never
                # blanks.
                try:
                    stream = state.backend.read_stream(lease_id, "", 0)
                except BackendError as exc:
                    await close_stream(exc.code)
                    return
                if stream.status != "ok":
                    recovery_close = stream.status in ("stale_epoch", "stale_offset")
                    if recovery_close:
                        preserve_reader()
                    await state.sockets.send_text(
                        websocket, json.dumps({"kind": "reset", "detail": stream.status})
                    )
                    await close_stream(stream.status)
                    return
                if sum(len(data) for _, data in stream.chunks) > VIEWER_QUEUE_MAX_BYTES:
                    recovery_close = True
                    await state.sockets.send_text(
                        websocket, json.dumps({"kind": "reset", "detail": "history_incomplete"})
                    )
                    await close_stream("history_incomplete")
                    return
                for offset, data in stream.chunks:
                    await state.sockets.send_bytes(
                        websocket, terminal_wire.encode_output(stream.epoch, offset, data)
                    )
            while True:
                if recv_task is None:
                    recv_task = asyncio.create_task(websocket.receive())
                if not recv_task.done():
                    push_detail = await maybe_push()
                    if push_detail is not None:
                        recovery_close = push_detail in ("stale_epoch", "overflow", "reset")
                        await close_stream(push_detail)
                        return
                if not recv_task.done():
                    # Queue changes wake via the fanout hook; the only timer
                    # left is the lease re-check at its known expiry.
                    lease_in = max(0.0, next_lease_check - time.monotonic())
                    wake_task = asyncio.create_task(viewer_wake.wait())
                    done: set[asyncio.Task[Any]] = set()
                    try:
                        done, _pending = await asyncio.wait(
                            {recv_task, wake_task},
                            timeout=lease_in,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    finally:
                        wake_task.cancel()
                        wake_task.add_done_callback(_discard)
                    if wake_task in done:
                        viewer_wake.clear()
                    continue
                message = recv_task.result()
                # Do not start the next receive until this frame and its
                # trailing lease check are handled.
                recv_task = None
                if message["type"] == "websocket.disconnect":
                    break
                if "bytes" in message and message["bytes"] is not None:
                    if "terminal.control" not in grant.capabilities:
                        await close_stream("unsupported_capability")
                        return
                    try:
                        chunk = terminal_wire.decode_input(message["bytes"])
                    except ValueError:
                        await close_stream("forbidden")
                        return
                    if chunk.lease_id != lease_id:
                        await close_stream("forbidden")
                        return
                    if chunk.generation > lease.generation:
                        await close_stream("forbidden")
                        return
                    if not state.input_bytes.check(lease_id, state.now_ms(), cost=len(chunk.data)):
                        if not await report_input_limit():
                            return
                        if await push_or_close():
                            return
                        continue
                    try:
                        fresh = state.backend.write_input(
                            lease_id,
                            chunk.generation,
                            chunk.input_seq,
                            chunk.kind,
                            chunk.data,
                        )
                        if fresh:
                            await forward_input(chunk)
                    except BackendError as exc:
                        if exc.code == "rate_limited":
                            if not await report_input_limit():
                                return
                            if await push_or_close():
                                return
                            continue
                        if exc.code == "input_failed":
                            await state.sockets.send_text(
                                websocket,
                                json.dumps(
                                    {
                                        "kind": "gap",
                                        "detail": "input_failed",
                                        "generation": chunk.generation,
                                        "input_seq": chunk.input_seq,
                                    }
                                ),
                            )
                            if await push_or_close():
                                return
                            continue
                        recovery_close = exc.code in (
                            "stale_revision",
                            "host_offline",
                        )
                        trace.emit(
                            "socket_close", reason=exc.code, source="server", socket=websocket
                        )
                        if not disconnected():
                            with suppress(Exception):
                                await state.sockets.send_text(
                                    websocket,
                                    json.dumps(
                                        {
                                            "kind": "gap"
                                            if exc.code in ("stale_revision", "host_offline")
                                            else "bye",
                                            "detail": exc.code,
                                            **(
                                                {
                                                    "generation": chunk.generation,
                                                    "input_seq": chunk.input_seq,
                                                }
                                                if 1 <= chunk.generation <= 0xFFFFFFFF
                                                and 1 <= chunk.input_seq <= 0xFFFFFFFF
                                                else {}
                                            ),
                                        }
                                    ),
                                )
                        if exc.code not in ("stale_revision", "host_offline"):
                            await close_stream(exc.code)
                        return
                elif "text" in message and message["text"] is not None:
                    # Character count, not bytes: a parse-bomb bound, not a
                    # byte bound (multibyte input may exceed it in bytes).
                    if len(message["text"]) > TUNNEL_MAX_TEXT:
                        # Tunnel parity: never parse an unbounded control
                        # string (valid control docs are ~300 bytes).
                        await close_stream("forbidden")
                        return
                    try:
                        control = json.loads(message["text"])
                        validate("relaydeck/gateway/v1/terminal-control", control)
                    except (ValueError, SchemaError):
                        await close_stream("forbidden")
                        return
                    if control["kind"] == "bye":
                        break
                if await push_or_close():
                    return
        except (WebSocketDisconnect, TimeoutError):
            trace.emit("socket_close", reason="receive_failure", source="server", socket=websocket)
        finally:
            # Close hygiene (OWASP): unsubscribe the per-connection viewer
            # on EVERY exit so a dead socket never retains its 1 MiB queue.
            if recv_task is not None:
                # No await here: a closing client may already be cancelling
                # this handler, and the cleanup below must still run.
                recv_task.cancel()
                recv_task.add_done_callback(_discard)
            if wake_task is not None:
                wake_task.cancel()
                wake_task.add_done_callback(_discard)
            unregister_wake(_viewer_wakes, (lease.host_id, lease.terminal_id), viewer_wake)
            trace.emit("socket_close", reason="stream_closed", source="server", socket=websocket)
            if fanout is not None:
                fanout.unsubscribe(viewer_id)
            state.sockets.unregister(websocket)
            if (
                lease.mode == "control"
                and grant.device_id == lease.device_id
                and not state.sockets.has_device_lease(grant.account, grant.device_id, lease_id)
            ):
                replacement_ticket = state.tickets.has_resume(
                    lease_id, grant.account, grant.device_id, lease.generation, state.now_ms()
                )
                handoff = (
                    recovery_close or replacement_ticket
                ) and state.backend.begin_reader_handoff(
                    lease_id, grant.account, grant.device_id, lease.generation
                )
                if not handoff:
                    with suppress(BackendError):
                        await ctx.release_lease(lease_id, grant.account, grant.device_id)

    return router
