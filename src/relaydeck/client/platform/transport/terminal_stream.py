"""Track ordered terminal output, cumulative acknowledgements and resume position for one lease."""

from __future__ import annotations

import json
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from relaydeck.client.platform.diagnostics.open_timing import mark_current
from relaydeck.client.ports.transport import (
    TerminalSocket,
    TransportError,
)
from relaydeck.contracts.schemas import SchemaError, validate
from relaydeck.contracts.wire import terminal as terminal_wire
from relaydeck.lease_diagnostics import LeaseTrace


@dataclass
class TerminalStreamClient:
    """One lease: epoch/offset tracking, acks, generations, resume (wire v2). The socket factory
    takes a single-use ticket minted via `GatewayTransport.terminal_ticket`.
    """

    socket_factory: Callable[[str], TerminalSocket]
    on_output: Callable[[int, bytes], None]
    on_control: Callable[[dict[str, Any]], None]
    on_gap: Callable[[str], None]
    on_reset: Callable[[str], None] | None = None
    on_input_failure: Callable[[int, int], None] | None = None
    diagnostics: LeaseTrace | None = field(default=None, kw_only=True)
    lease_id: str = ""
    generation: int = 0
    epoch: str = ""
    next_offset: int = 0
    offered_offset: int | None = None
    _socket: TerminalSocket | None = None
    _acked: int = 0
    _input_seq: int = 0
    _opens: int = 0
    # True while opening again on a lease this client already streamed: the socket must then
    # carry `resume_query()`, because the server holds the lease for exactly that reader.
    reset_to_live: bool = False
    on_baseline: Callable[[dict[str, Any]], Awaitable[bool]] | None = None
    reopening: bool = False

    def _lease_trace(self) -> LeaseTrace:
        return self.diagnostics or LeaseTrace.for_session(
            self, "", "", self.lease_id, generation=self.generation
        )

    async def open(
        self, lease_id: str, generation: int, ticket: str, epoch: str = "", offset: int = 0
    ) -> None:
        same_writer = (self.lease_id, self.generation) == (lease_id, generation)
        self.lease_id = lease_id
        self.generation = generation
        self.epoch = epoch
        self.next_offset = offset
        self._acked = offset
        # Reader replacement is not a new writer. Preserve assigned sequence
        # numbers (including uncertain sends), never reuse or replay them.
        if not same_writer:
            self._input_seq = 0
        self.offered_offset = None
        self.reopening = self._opens > 0
        self._opens += 1
        socket = self.socket_factory(ticket)
        self._socket = socket
        trace = self._lease_trace()
        try:
            await socket.connect()
        except Exception:
            trace.emit("socket_close", reason="receive_failure", socket=socket)
            raise
        trace.emit("socket_open", reason="ok", socket=socket)
        mark_current("socket")

    async def run(self) -> None:
        socket = self._socket
        trace = self._lease_trace()
        if socket is None:
            return
        while self._socket is socket:
            try:
                kind, payload = await socket.receive()
            except Exception as exc:
                retired = self._socket is not socket
                trace.emit(
                    "socket_close",
                    reason="socket_replaced"
                    if retired
                    else (exc.code if isinstance(exc, TransportError) else "receive_failure"),
                    current_lease_id=self.lease_id,
                    socket=socket,
                )
                if retired:
                    return
                raise
            if self._socket is not socket:
                trace.emit(
                    "socket_close",
                    reason="socket_replaced",
                    current_lease_id=self.lease_id,
                    socket=socket,
                )
                return
            if kind == "bytes":
                try:
                    chunk = terminal_wire.decode_output(payload)
                except ValueError:
                    self.on_gap("bad_frame")
                    trace.emit("socket_close", reason="bad_frame", socket=socket)
                    return
                if self.epoch and chunk.epoch != self.epoch:
                    self._report_reset(f"epoch {self.epoch} -> {chunk.epoch}")
                    trace.emit("socket_close", reason="epoch_changed", socket=socket)
                    return
                self.epoch = chunk.epoch
                end_offset = chunk.offset + len(chunk.data)
                if chunk.offset < self.next_offset and end_offset <= self.next_offset:
                    # Skip retained bytes already rendered at the preserved cursor; never rewind the
                    # emulator.
                    # Empty current-cursor frames still report liveness.
                    continue
                if chunk.offset > self.next_offset:
                    self.offered_offset = chunk.offset
                    self.on_gap(f"offset {self.next_offset} -> {chunk.offset}")
                    trace.emit("socket_close", reason="offset_gap", socket=socket)
                    return
                offset = self.next_offset
                data = chunk.data[offset - chunk.offset :]
                self.next_offset = end_offset
                if data:
                    mark_current("first_byte")
                self.on_output(offset, data)
                if self.next_offset - self._acked >= 4096:
                    await self.ack()
            else:
                try:
                    doc = json.loads(payload)
                    validate("relaydeck/gateway/v1/terminal-control", doc)
                except (ValueError, SchemaError):
                    self.on_gap("bad_control")
                    trace.emit("socket_close", reason="bad_control", socket=socket)
                    return
                if doc.get("kind") == "history_reset":
                    if (
                        not isinstance(doc.get("epoch"), str)
                        or not 1 <= len(doc["epoch"]) <= 128
                        or type(doc.get("offset")) is not int
                        or doc["offset"] < 0
                        or type(doc.get("incomplete")) is not bool
                        or (not self.reset_to_live and (self.epoch or self.next_offset))
                        or (self.epoch and doc["epoch"] != self.epoch)
                        or self.on_baseline is None
                    ):
                        self.on_gap("bad_control")
                        return
                    if not await self.on_baseline(doc) or self._socket is not socket:
                        return
                    self.epoch = doc["epoch"]
                    self.next_offset = self._acked = doc["offset"]
                    self.offered_offset = None
                    self.reset_to_live = False
                    self.on_output(self.next_offset, b"")
                    continue
                if doc.get("kind") == "reset":
                    self._report_reset(str(doc.get("detail", "reset")))
                    trace.emit("socket_close", reason="reset", socket=socket)
                    return
                if (
                    doc.get("kind") in ("gap", "bye")
                    and self.on_input_failure is not None
                    and doc.get("generation") == self.generation
                    and type(doc.get("input_seq")) is int
                ):
                    self.on_input_failure(doc["generation"], doc["input_seq"])
                if doc.get("kind") == "gap":
                    if doc.get("detail") == "rate_limited":
                        # Only this input was refused; output continuity is intact.
                        # Report the control doc without recovery or input replay.
                        self.on_control(doc)
                        continue
                    self.on_gap(str(doc.get("detail", "gap")))
                    trace.emit("socket_close", reason=doc.get("detail", "gap"), socket=socket)
                    return
                self.on_control(doc)
                if doc.get("kind") == "bye":
                    trace.emit(
                        "socket_close",
                        reason=doc.get("detail", "bye"),
                        source="server",
                        socket=socket,
                    )
                    return

    def _report_reset(self, reason: str) -> None:
        """Require reattachment after epoch rotation or reset.

        Use the gap callback when no reset listener is installed.
        """
        if self.on_reset is not None:
            self.on_reset(reason)
        else:
            self.on_gap(reason)

    def resume_query(self) -> str:
        """Query fragment carrying (epoch, offset) so a reconnect resumes
        exactly where this client stopped. Factories append it to the
        ticket URL: `?ticket=...&` + resume_query()."""
        reset = "&live_edge=1" if self.reset_to_live else ""
        return (
            f"epoch={quote(self.epoch, safe='')}&offset={self.next_offset}&output_reset=v1{reset}"
        )

    async def _send(self, kind: str, data: bytes) -> bool:
        socket = self._socket
        if socket is None:
            if kind == "resize":
                return False
            raise TransportError("host_offline", "Terminal stream closed", True)
        self._input_seq += 1
        await socket.send_bytes(
            terminal_wire.encode_input(self.lease_id, self.generation, self._input_seq, kind, data)
        )
        return True

    async def send_input(self, data: bytes) -> None:
        await self._send("text", data)

    async def send_key(self, name: str) -> None:
        await self._send("key", name.encode())

    async def resize(self, cols: int, rows: int) -> bool:
        try:
            return await self._send("resize", struct.pack(">HH", cols, rows))
        except TransportError:
            return False  # A late size report is never retried after reader close.

    async def ack(self) -> None:
        socket = self._socket
        if socket is None:
            return
        self._acked = self.next_offset
        await socket.send_text(json.dumps({"kind": "ack", "applied": self.next_offset}))

    async def close(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            trace = self._lease_trace()
            try:
                try:
                    await socket.send_text(json.dumps({"kind": "bye"}))
                except TransportError:
                    pass
            finally:
                try:
                    await socket.close()
                finally:
                    trace.emit("socket_close", reason="local_close", socket=socket)

    def resume_offset(self) -> int:
        return self.next_offset
