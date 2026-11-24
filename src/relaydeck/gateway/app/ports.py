"""Backend capability interfaces for gateway metadata, command intent and terminal ownership."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from relaydeck.gateway.features.terminals.api import (
    Attachment,
    LeaseError,
    LeaseRecord,
    StreamFanout,
)
from relaydeck.gateway.ports import BackendError, OutputBatch, StreamBatch, TerminalLease


def to_lease(rec: LeaseRecord) -> TerminalLease:
    return TerminalLease(
        lease_id=rec.lease_id,
        host_id=rec.host_id,
        terminal_id=rec.terminal_id,
        generation=rec.generation,
        owner=rec.account,
        expires_ms=rec.expires_ms,
        epoch=rec.epoch,
        mode=rec.mode,
        device_id=rec.device_id,
        attachment_id=rec.attachment_id,
        resume_pending=rec.handoff_original_expiry_ms is not None,
    )


def reraise(exc: LeaseError) -> BackendError:
    return BackendError(exc.code, exc.message, exc.retryable, exc.details)


class Backend(ABC):
    requires_connector = False

    def epochs_for_host(self, host_id: str, account: str) -> list[dict[str, Any]]:
        return []

    def next_epoch_change_ms(self, host_id: str, account: str) -> int | None:
        """Earliest time `epochs_for_host` can change on its own, if known."""
        return None

    def rotate_streams(self, host_id: str) -> tuple[str, ...]:
        """Rotate continuity and return downstream leases to sever."""
        return ()

    def reset_terminal(
        self, host_id: str, terminal_id: str, epoch: str, account: str
    ) -> tuple[str, ...]:
        return ()

    def refuse_attachment(
        self, host_id: str, terminal_id: str, epoch: str, account: str
    ) -> tuple[str, ...]:
        """Upstream refused the attach (owned elsewhere): close the attachment and return the
        leases to sever with a typed conflict. The next attach is a fresh, explicit decision."""
        return ()

    @abstractmethod
    def snapshot(self, host_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def events_after(self, host_id: str, sequence: int) -> list[dict[str, Any]]: ...

    async def follow(
        self, host_id: str, after_sequence: int, heartbeat_s: float = 15.0
    ) -> AsyncIterator[dict[str, Any] | None]:
        """Live event feed: replay, then new events, None per heartbeat. The default is a finite
        replay; production backends override with a push feed.
        """
        for event in self.events_after(host_id, after_sequence):
            yield event

    @abstractmethod
    def submit_command(self, command: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def command_status(self, command_id: str) -> dict[str, Any] | None: ...

    def command_owner(self, command_id: str) -> tuple[str, str] | None:
        return None

    @abstractmethod
    def agents_page(self, host_id: str, limit: int, offset: int) -> dict[str, Any]: ...

    @abstractmethod
    def issue_lease(
        self, host_id: str, terminal_id: str, owner: str, generation: int, epoch: str = "e1"
    ) -> TerminalLease: ...

    @abstractmethod
    def open_lease(self, lease_id: str) -> TerminalLease | None: ...

    @abstractmethod
    def write_input(
        self, lease_id: str, generation: int, input_seq: int, kind: str, data: bytes
    ) -> bool:
        """Store one input frame. False when the sequence is a duplicate
        within this generation (deduplicated, not an error)."""

    @abstractmethod
    def read_output(self, lease_id: str, after_offset: int) -> OutputBatch: ...

    @abstractmethod
    def publish_output(self, attachment_id: str, epoch: str, data: bytes) -> tuple[int, int]:
        """Append upstream bytes to an attachment ring; returns
        (start_offset, end_offset). Wrong-epoch publishes fail stale_epoch."""

    @abstractmethod
    def read_stream(self, lease_id: str, epoch: str, after_offset: int) -> StreamBatch:
        """Read one lease's retained output within its epoch and offset bounds. Stale reads
        carry no bytes and require explicit reset rather than rendering a partial tail.
        """

    @abstractmethod
    def close_lease(self, lease_id: str) -> None: ...

    @abstractmethod
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
        """Authorize or take over upstream ownership. Returns the attachment plus displaced
        downstream lease ids (close their sockets).
        """

    @abstractmethod
    def get_attachment(self, attachment_id: str) -> Attachment | None: ...

    @abstractmethod
    def acquire_control(
        self,
        attachment_id: str,
        account: str,
        device_id: str,
        *,
        expected_generation: int | None = None,
    ) -> TerminalLease:
        """Exclusive control writer (re-acquire by the holder renews)."""

    @abstractmethod
    def issue_view(self, attachment_id: str, account: str, device_id: str) -> TerminalLease:
        """Read-only observer of an already-owned stream."""

    @abstractmethod
    def control_holder(
        self, host_id: str, terminal_id: str, account: str, device_id: str
    ) -> dict[str, Any]:
        """Read-only holder state plus its opaque control_revision."""

    @abstractmethod
    def renew_lease(self, lease_id: str, account: str, device_id: str) -> TerminalLease: ...

    @abstractmethod
    def release_lease(self, lease_id: str, account: str, device_id: str) -> Attachment:
        """Detach one lease; the attachment and remote process survive.
        Holder-only: a device mismatch is not_found (mirrors renew)."""

    @abstractmethod
    def lease_host(self, lease_id: str) -> str | None:
        """Host of a lease regardless of liveness (route guard only)."""

    @abstractmethod
    def close_attachment(self, attachment_id: str, account: str) -> tuple[str, ...]:
        """Explicit close; returns dead downstream lease ids."""

    @abstractmethod
    def revoke_leases_account(self, account: str) -> tuple[str, ...]: ...

    @abstractmethod
    def revoke_leases_device(self, account: str, device_id: str) -> tuple[str, ...]: ...

    @abstractmethod
    def revoke_leases_host(self, host_id: str) -> tuple[str, ...]: ...

    # Journal/tunnel/live-push seam: defaults fail closed (no fanout, outbox or ack/result
    # ingestion); concrete backends override.

    def fanout_for(self, attachment_id: str) -> StreamFanout | None:
        """Live ring fanout for one attachment, if the backend keeps one."""
        return None

    def begin_reader_handoff(
        self, lease_id: str, account: str, device_id: str, generation: int
    ) -> bool:
        """Optional backend support; unknown backends release rather than retain control."""
        return False

    def finish_reader_handoff(
        self, lease_id: str, account: str, device_id: str, generation: int
    ) -> TerminalLease:
        raise BackendError("lease_expired", "reader handoff unsupported")

    def resolve_attachment(self, host_id: str, terminal_id: str) -> Attachment | None:
        """Open attachment for a (host, terminal) pair, if any."""
        return None

    def drain_outbox(self, host_id: str) -> list[dict[str, Any]]:
        """Pending gateway->connector tunnel messages; draining consumes."""
        return []

    def restore_outbox(
        self, host_id: str, pending: list[dict[str, Any]], taken: list[dict[str, Any]] | None = None
    ) -> None:
        """Return unattempted intents after a connection-local pump is cancelled."""
        return None

    def mark_command_dispatched(self, command_id: str, now_ms: int) -> bool:
        """Write-ahead dispatch marker for the tunnel pump: True when the
        intent moved accepted -> dispatched (pump must send); False when
        it already moved (pump must skip the send, never re-dispatch)."""
        return False

    def note_command_ack(self, command_id: str, host_id: str, now_ms: int) -> dict[str, Any] | None:
        """Ingest a connector command.ack; None when refused (unknown id
        or host binding mismatch)."""
        return None

    def note_command_result(
        self,
        command_id: str,
        host_id: str,
        outcome: str,
        result_digest: str,
        error_code: str,
        result: dict[str, Any] | None,
        now_ms: int,
    ) -> dict[str, Any] | None:
        """Ingest a connector command.result (outcome succeeded, failed,
        rejected, or unknown); None when refused."""
        return None

    def drain_socket_closes(self) -> tuple[str, ...]:
        """Lease ids whose sockets the app must sever now (session.close
        path); draining consumes."""
        return ()

    def ingest_terminal_output(
        self,
        host_id: str,
        terminal_id: str,
        epoch: str,
        data: bytes,
        now_ms: int,
    ) -> dict[str, Any]:
        """Ingest one tunnel terminal.output frame into the attachment
        ring. Returns ok offsets or an error code."""
        return {"error": "not_found"}

    def ingest_snapshot(
        self, host_id: str, snapshot: dict[str, Any], now_ms: int, cursor_secret: bytes
    ) -> dict[str, Any]:
        """Store a tunnel-pushed snapshot (replaces), stamping the
        resume cursor at the current event boundary: clients stream
        from `snapshot.cursor`, which must be a server HMAC cursor
        (the connector value is ignored). Returns ok or an error code.
        """
        return {"error": "not_found"}

    def ingest_event(
        self,
        host_id: str,
        partial: dict[str, Any],
        now_ms: int,
        cursor_secret: bytes,
    ) -> dict[str, Any]:
        """Store a tunnel-pushed metadata event with server-stamped sequence/cursor.
        Deduplicates by event_id (re-push safe).
        """
        return {"error": "not_found"}

    def publish_command_updated(
        self,
        status_record: dict[str, Any],
        host_epoch: str,
        cursor_secret: bytes,
        now_ms: int,
    ) -> dict[str, Any] | None:
        """Emit a valid command-status event once per command ID and status. Return the stored
        event on replay or None when identity/status/epoch cannot form a valid envelope.
        """
        return None
