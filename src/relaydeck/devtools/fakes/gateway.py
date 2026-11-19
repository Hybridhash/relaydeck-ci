"""In-memory gateway backend with deterministic command, lease and terminal state."""

from __future__ import annotations

import asyncio
import json as _json
import struct
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from relaydeck.contracts.schemas import (
    GATEWAY_PROTOCOL,
)
from relaydeck.gateway.app.ports import Backend, reraise, to_lease
from relaydeck.gateway.cursors import CursorError, encode_cursor
from relaydeck.gateway.features.metadata.api import (
    EventLogIndex,
    build_command_updated,
    materialize_agent,
    prepare_event,
)
from relaydeck.gateway.features.terminals.api import (
    STATUS_OK,
    Attachment,
    LeaseError,
    LeaseStore,
    RingStore,
    StreamFanout,
)
from relaydeck.gateway.ports import (
    BackendError,
    OutputBatch,
    ResizeRequest,
    StreamBatch,
    TerminalLease,
)


@dataclass
class FakeBackend(Backend):
    """Deterministic in-memory backend for tests and development."""

    now_ms: Callable[[], int] = field(default=lambda: 0)
    snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)
    event_logs: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    commands: dict[str, dict[str, Any]] = field(default_factory=dict)
    _command_owners: dict[str, tuple[str, str]] = field(default_factory=dict)
    ownership: LeaseStore = field(default_factory=LeaseStore)
    rings: RingStore = field(default_factory=RingStore)
    attach_index: dict[tuple[str, str], str] = field(default_factory=dict)
    resize_log: list[ResizeRequest] = field(default_factory=list)
    output_logs: dict[str, list[bytes]] = field(default_factory=dict)
    input_logs: dict[str, list[tuple[int, int, str, bytes]]] = field(default_factory=dict)
    offline_hosts: set[str] = field(default_factory=set)
    _followers: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]]] = (
        field(default_factory=dict)
    )
    _event_index: EventLogIndex = field(default_factory=EventLogIndex)

    def snapshot(self, host_id: str) -> dict[str, Any]:
        if host_id in self.offline_hosts:
            raise BackendError("host_offline", f"host {host_id} offline", True)
        try:
            return self.snapshots[host_id]
        except KeyError:
            raise BackendError("not_found", f"unknown host {host_id}") from None

    def events_after(self, host_id: str, sequence: int) -> list[dict[str, Any]]:
        if host_id in self.offline_hosts:
            raise BackendError("host_offline", f"host {host_id} offline", True)
        return [e for e in self.event_logs.get(host_id, []) if e.get("sequence", 0) > sequence]

    def publish(self, host_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """Append to the journal with the next sequence; wake followers.

        Thread-safe: followers may live on the ASGI loop while the
        publisher runs elsewhere (Herdr subscriber, tests).
        """
        published = dict(event, sequence=self._event_index.next_sequence(self.event_logs, host_id))
        self._event_index.append(self.event_logs, host_id, published)
        self._wake(host_id, published)
        return published

    def publish_raw(self, host_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """Fault injection: append with an explicit sequence (may gap)."""
        self._event_index.append(self.event_logs, host_id, event)
        self._wake(host_id, event)
        return event

    def _wake(self, host_id: str, event: dict[str, Any]) -> None:
        for loop, queue in self._followers.get(host_id, []):
            loop.call_soon_threadsafe(queue.put_nowait, event)

    async def follow(
        self, host_id: str, after_sequence: int, heartbeat_s: float = 15.0
    ) -> AsyncIterator[dict[str, Any] | None]:
        if host_id in self.offline_hosts:
            raise BackendError("host_offline", f"host {host_id} offline", True)
        # Subscribe before replaying: anything published in between is
        # delivered twice and deduplicated downstream by sequence.
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        entry = (asyncio.get_running_loop(), queue)
        self._followers.setdefault(host_id, []).append(entry)
        try:
            for event in self.events_after(host_id, after_sequence):
                yield event
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
                except TimeoutError:
                    yield None
        finally:
            followers = self._followers.get(host_id, [])
            if entry in followers:
                followers.remove(entry)

    @staticmethod
    def _canonical(command: dict[str, Any]) -> str:
        return _json.dumps(
            {
                "host_id": command.get("host_id"),
                "kind": command.get("kind"),
                "target": command.get("target"),
                "payload": command.get("payload"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def submit_command(self, command: dict[str, Any]) -> dict[str, Any]:
        cid = str(command["command_id"])
        current = self.commands.get(cid)
        if current is not None:
            if self._command_owners.get(cid) != (
                str(command.get("principal", "")),
                str(command["host_id"]),
            ):
                raise BackendError("command_conflict", "command belongs to another principal")
            if current.get("canonical") == self._canonical(command):
                return {k: v for k, v in current.items() if k not in ("payload", "canonical")}
            raise BackendError("command_conflict", f"command {cid} conflicts")
        record = {
            "protocol": GATEWAY_PROTOCOL,
            "command_id": cid,
            "host_id": command["host_id"],
            "kind": command["kind"],
            "target": command["target"],
            "status": "accepted",
            "updated_ms": self.now_ms(),
            "payload": command.get("payload", {}),
            "canonical": self._canonical(command),
        }
        self.commands[cid] = record
        self._command_owners[cid] = (str(command.get("principal", "")), str(command["host_id"]))
        return {k: v for k, v in record.items() if k not in ("payload", "canonical")}

    def agents_page(self, host_id: str, limit: int, offset: int) -> dict[str, Any]:
        snapshot = self.snapshot(host_id)
        agents = snapshot.get("agents", [])
        page = agents[offset : offset + limit]
        return {
            "host_id": host_id,
            "agents": [
                {
                    "terminal_id": a.get("terminal_id", ""),
                    "name": a.get("name", ""),
                    "agent": a.get("agent", ""),
                    "title": a.get("title", ""),
                    "status": a.get("status", "unknown"),
                    "focused": bool(a.get("focused", False)),
                    "revision": int(a.get("revision", 0)),
                }
                for a in page
            ],
            "total": len(agents),
        }

    def command_status(self, command_id: str) -> dict[str, Any] | None:
        record = self.commands.get(command_id)
        if record is None:
            return None
        return {k: v for k, v in record.items() if k not in ("payload", "canonical")}

    def command_owner(self, command_id: str) -> tuple[str, str] | None:
        return self._command_owners.get(command_id)

    def issue_lease(
        self, host_id: str, terminal_id: str, owner: str, generation: int, epoch: str = "e1"
    ) -> TerminalLease:
        """Replace a control lease without checking ownership for synthetic test and development
        scenarios.
        """
        rec = self.ownership.legacy_issue(
            host_id, terminal_id, owner, generation, epoch, self.now_ms()
        )
        return to_lease(rec)

    def open_lease(self, lease_id: str) -> TerminalLease | None:
        rec = self.ownership.open_lease(lease_id, self.now_ms())
        return to_lease(rec) if rec is not None else None

    def begin_reader_handoff(
        self, lease_id: str, account: str, device_id: str, generation: int
    ) -> bool:
        return self.ownership.begin_reader_handoff(
            lease_id, account, device_id, generation, self.now_ms()
        )

    def finish_reader_handoff(
        self, lease_id: str, account: str, device_id: str, generation: int
    ) -> TerminalLease:
        try:
            return to_lease(
                self.ownership.finish_reader_handoff(
                    lease_id, account, device_id, generation, self.now_ms()
                )
            )
        except LeaseError as exc:
            raise reraise(exc) from None

    def write_input(
        self, lease_id: str, generation: int, input_seq: int, kind: str, data: bytes
    ) -> bool:
        try:
            fresh = self.ownership.check_input(lease_id, generation, input_seq, self.now_ms())
        except LeaseError as exc:
            raise reraise(exc) from None
        if fresh:
            if kind == "resize" and len(data) != 4:
                raise BackendError(
                    "forbidden", "bad resize payload", details={"reason": "bad_resize"}
                )
            self.input_logs.setdefault(lease_id, []).append((generation, input_seq, kind, data))
            if kind == "resize":
                cols, rows = struct.unpack(">HH", data)
                self.resize_log.append(ResizeRequest(lease_id, generation, input_seq, cols, rows))
        return fresh

    def read_output(self, lease_id: str, after_offset: int) -> OutputBatch:
        log = self.output_logs.get(lease_id, [])
        chunks: list[tuple[int, bytes]] = []
        offset = 0
        for data in log:
            if offset >= after_offset:
                chunks.append((offset, data))
            offset += len(data)
        return OutputBatch(tuple(chunks), offset)

    def publish_output(self, attachment_id: str, epoch: str, data: bytes) -> tuple[int, int]:
        attachment = self.ownership.get_attachment(attachment_id)
        if attachment is None or attachment.closed:
            raise BackendError("not_found", "no such attachment")
        if epoch != attachment.epoch:
            raise BackendError("stale_epoch", "ring epoch rotated")
        fanout = self.rings.get(attachment_id)
        if fanout is None:
            fanout = self.rings.rotate(attachment_id, epoch, self.now_ms())
        if fanout.ring.epoch != epoch:  # pragma: no cover - store invariant
            raise BackendError("stale_epoch", "ring epoch rotated")
        return fanout.publish(data, self.now_ms())

    def read_stream(self, lease_id: str, epoch: str, after_offset: int) -> StreamBatch:
        rec = self.ownership.open_lease(lease_id, self.now_ms())
        if rec is None:
            raise BackendError("lease_expired", "no such lease")
        current = rec.epoch
        if epoch and epoch != current:
            return StreamBatch("stale_epoch", (), self._ring_end(rec.attachment_id), current)
        if not epoch and after_offset > 0:
            # No epoch knowledge plus a nonzero offset: the cursor cannot
            # address this stream (docstring rule). Zero-byte stale batch,
            # mirroring the stale path above; offset 0 still first-connects.
            return StreamBatch("stale_epoch", (), self._ring_end(rec.attachment_id), current)
        if after_offset < 0:
            raise BackendError("forbidden", "bad resume offset")
        fanout = self.rings.get(rec.attachment_id)
        if fanout is None:
            if after_offset == 0:
                return StreamBatch(STATUS_OK, (), 0, current)
            return StreamBatch("stale_offset", (), 0, current)
        ring = fanout.ring
        if after_offset == 0 and not epoch:
            # First connect without epoch knowledge: serve from the live
            # edge. The client derives completeness from the first chunk
            # offset (0 = full replay, >0 = incomplete-history notice).
            read = ring.read(current, ring.base_offset)
        else:
            read = ring.read(current, after_offset)
        return StreamBatch(read.status, read.chunks, read.next_offset, read.epoch)

    def _ring_end(self, attachment_id: str) -> int:
        fanout = self.rings.get(attachment_id)
        return fanout.ring.end_offset if fanout is not None else 0

    def close_lease(self, lease_id: str) -> None:
        self.ownership.hard_remove(lease_id)

    def create_attachment(
        self,
        host_id: str,
        terminal_id: str,
        kind: str,
        account: str,
        device_id: str,
        *,
        takeover: bool,
        owner_label: str = "",
        expected_control_revision: str | None = None,
    ) -> tuple[Attachment, tuple[str, ...]]:
        try:
            attachment, displaced = self.ownership.attach(
                host_id=host_id,
                terminal_id=terminal_id,
                kind=kind,
                account=account,
                device_id=device_id,
                takeover=takeover,
                owner_label=owner_label,
                now_ms=self.now_ms(),
                expected_control_revision=expected_control_revision,
            )
        except LeaseError as exc:
            raise reraise(exc) from None
        # First attach opens the ring; every takeover rebinds it so old
        # offsets can never address the new stream .
        self.rings.rotate(attachment.attachment_id, attachment.epoch, self.now_ms())
        self.attach_index[(host_id, terminal_id)] = attachment.attachment_id
        return attachment, displaced

    def get_attachment(self, attachment_id: str) -> Attachment | None:
        return self.ownership.get_attachment(attachment_id)

    def acquire_control(
        self,
        attachment_id: str,
        account: str,
        device_id: str,
        *,
        expected_generation: int | None = None,
    ) -> TerminalLease:
        try:
            rec = self.ownership.acquire_control(
                attachment_id,
                account,
                device_id,
                self.now_ms(),
                expected_generation=expected_generation,
            )
        except LeaseError as exc:
            raise reraise(exc) from None
        return to_lease(rec)

    def issue_view(self, attachment_id: str, account: str, device_id: str) -> TerminalLease:
        try:
            rec = self.ownership.issue_view(attachment_id, account, device_id, self.now_ms())
        except LeaseError as exc:
            raise reraise(exc) from None
        return to_lease(rec)

    def control_holder(
        self, host_id: str, terminal_id: str, account: str, device_id: str
    ) -> dict[str, Any]:
        return self.ownership.control_holder(
            host_id, terminal_id, account, device_id, self.now_ms()
        )

    def renew_lease(self, lease_id: str, account: str, device_id: str) -> TerminalLease:
        try:
            rec = self.ownership.renew(lease_id, account, device_id, self.now_ms())
        except LeaseError as exc:
            raise reraise(exc) from None
        return to_lease(rec)

    def release_lease(self, lease_id: str, account: str, device_id: str) -> Attachment:
        try:
            return self.ownership.release(lease_id, account, device_id)
        except LeaseError as exc:
            raise reraise(exc) from None

    def lease_host(self, lease_id: str) -> str | None:
        return self.ownership.lease_host(lease_id)

    def close_attachment(self, attachment_id: str, account: str) -> tuple[str, ...]:
        try:
            dead = self.ownership.close_attachment(attachment_id, account)
        except LeaseError as exc:
            raise reraise(exc) from None
        self.rings.drop(attachment_id)
        for key, att_id in list(self.attach_index.items()):
            if att_id == attachment_id:
                del self.attach_index[key]
        return dead

    def revoke_leases_account(self, account: str) -> tuple[str, ...]:
        return self.ownership.revoke_account(account)

    def revoke_leases_device(self, account: str, device_id: str) -> tuple[str, ...]:
        return self.ownership.revoke_device(account, device_id)

    def revoke_leases_host(self, host_id: str) -> tuple[str, ...]:
        return self.ownership.revoke_host(host_id)

    def append_output(self, lease_id: str, data: bytes) -> None:
        self.output_logs.setdefault(lease_id, []).append(data)

    def fanout_for(self, attachment_id: str) -> StreamFanout | None:
        return self.rings.get(attachment_id)

    def next_epoch_change_ms(self, host_id: str, account: str) -> int | None:
        return self.ownership.next_expiry_ms(host_id, account, self.now_ms())

    def epochs_for_host(self, host_id: str, account: str) -> list[dict[str, Any]]:
        return [
            {
                "terminal_id": att.terminal_id,
                "epoch": att.epoch,
                "kind": att.kind,
                "mode": self.ownership.upstream_mode(att.attachment_id, self.now_ms()),
                "takeover": att.takeover_requested,
            }
            for att in self.ownership.attachments_for_host(host_id, account)
        ]

    def reset_terminal(
        self, host_id: str, terminal_id: str, epoch: str, account: str
    ) -> tuple[str, ...]:
        att = self.resolve_attachment(host_id, terminal_id)
        if att is None or att.account != account or att.epoch != epoch:
            return ()
        renewed, dead = self.ownership.rotate_attachment(att.attachment_id)
        self.rings.rotate(renewed.attachment_id, renewed.epoch, self.now_ms())
        return dead

    def refuse_attachment(
        self, host_id: str, terminal_id: str, epoch: str, account: str
    ) -> tuple[str, ...]:
        att = self.resolve_attachment(host_id, terminal_id)
        if att is None or att.account != account or att.epoch != epoch:
            return ()
        return self.ownership.refuse_attachment(att.attachment_id)

    def rotate_streams(self, host_id: str) -> tuple[str, ...]:
        dead: list[str] = []
        for att, leases in self.ownership.rotate_host(host_id):
            self.rings.rotate(att.attachment_id, att.epoch, self.now_ms())
            dead.extend(leases)
        return tuple(dead)

    def resolve_attachment(self, host_id: str, terminal_id: str) -> Attachment | None:
        att_id = self.attach_index.get((host_id, terminal_id))
        if att_id is None:
            return None
        attachment = self.ownership.get_attachment(att_id)
        if attachment is None or attachment.closed or attachment.host_id != host_id:
            return None
        return attachment

    def ingest_terminal_output(
        self,
        host_id: str,
        terminal_id: str,
        epoch: str,
        data: bytes,
        now_ms: int,
    ) -> dict[str, Any]:
        attachment = self.resolve_attachment(host_id, terminal_id)
        if attachment is None:
            return {"error": "not_found"}
        try:
            start, end = self.publish_output(attachment.attachment_id, epoch, data)
        except BackendError as exc:
            return {"error": exc.code}
        return {"ok": [start, end]}

    def ingest_snapshot(
        self, host_id: str, snapshot: dict[str, Any], now_ms: int, cursor_secret: bytes
    ) -> dict[str, Any]:
        if host_id in self.offline_hosts:
            return {"error": "host_offline"}
        host = snapshot.get("host", {})
        if not isinstance(host, dict) or host.get("id") != host_id:
            return {"error": "forbidden"}
        sequence = self._event_index.last_sequence(self.event_logs, host_id)
        try:
            stamped = dict(
                snapshot,
                cursor=encode_cursor(cursor_secret, {"host_id": host_id, "sequence": sequence}),
            )
        except CursorError as exc:
            return {"error": "forbidden", "detail": str(exc)}
        self.snapshots[host_id] = stamped
        return {"ok": True}

    def ingest_event(
        self,
        host_id: str,
        partial: dict[str, Any],
        now_ms: int,
        cursor_secret: bytes,
    ) -> dict[str, Any]:
        if host_id in self.offline_hosts:
            return {"error": "host_offline"}
        wanted = partial.get("event_id") if isinstance(partial, dict) else None
        if isinstance(wanted, str) and wanted:
            existing = self._event_index.find(self.event_logs, host_id, wanted)
            if existing is not None:
                return {"ok": existing, "duplicate": True}
        sequence = self._event_index.next_sequence(self.event_logs, host_id)
        try:
            stored = prepare_event(host_id, partial, sequence, cursor_secret, now_ms)
        except ValueError as exc:
            return {"error": "forbidden", "detail": str(exc)}
        materialize_agent(self.snapshots, host_id, stored)
        self._event_index.append(self.event_logs, host_id, stored)
        self._wake(host_id, stored)
        return {"ok": stored}

    def publish_command_updated(
        self,
        status_record: dict[str, Any],
        host_epoch: str,
        cursor_secret: bytes,
        now_ms: int,
    ) -> dict[str, Any] | None:
        partial = build_command_updated(status_record, host_epoch)
        if partial is None:
            return None
        host_id = str(status_record.get("host_id", ""))
        if not host_id:
            return None
        event_id = str(partial["event_id"])
        existing = self._event_index.find(self.event_logs, host_id, event_id)
        if existing is not None:
            return existing
        try:
            stored = prepare_event(
                host_id,
                partial,
                self._event_index.next_sequence(self.event_logs, host_id),
                cursor_secret,
                now_ms,
            )
        except ValueError:
            return None
        self._event_index.append(self.event_logs, host_id, stored)
        self._wake(host_id, stored)
        return stored
