"""Stamp and deduplicate bounded metadata and command-status events."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from relaydeck.contracts.schemas import (
    COMMAND_STATUSES,
    GATEWAY_PROTOCOL,
    SchemaError,
    validate_metadata_event,
)
from relaydeck.gateway.cursors import CursorError, encode_cursor

MAX_EVENT_LOG_PER_HOST = 5000


class EventLogIndex:
    """Index event IDs against bounded per-host logs and reconcile externally changed list
    lengths. Trim oldest events; older cursors replay what remains.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, dict[str, dict[str, Any]]] = {}
        self._lengths: dict[str, int] = {}
        self._last_seq: dict[str, int] = {}

    def _reconcile(
        self, logs: dict[str, list[dict[str, Any]]], host_id: str
    ) -> dict[str, dict[str, Any]]:
        log = logs.get(host_id, [])
        if self._lengths.get(host_id) != len(log) or host_id not in self._by_id:
            index: dict[str, dict[str, Any]] = {}
            last = 0
            for event in log:
                event_id = event.get("event_id")
                if isinstance(event_id, str) and event_id:
                    index[event_id] = event
                seq = event.get("sequence", 0)
                if isinstance(seq, int) and seq > last:
                    last = seq
            self._by_id[host_id] = index
            self._lengths[host_id] = len(log)
            self._last_seq[host_id] = last
        return self._by_id[host_id]

    def find(
        self, logs: dict[str, list[dict[str, Any]]], host_id: str, event_id: str
    ) -> dict[str, Any] | None:
        """Stored event for an event_id, if the host log holds it."""
        return self._reconcile(logs, host_id).get(event_id)

    def last_sequence(self, logs: dict[str, list[dict[str, Any]]], host_id: str) -> int:
        """Highest sequence in the host log (0 when empty)."""
        self._reconcile(logs, host_id)
        return self._last_seq.get(host_id, 0)

    def next_sequence(self, logs: dict[str, list[dict[str, Any]]], host_id: str) -> int:
        return self.last_sequence(logs, host_id) + 1

    def append(
        self, logs: dict[str, list[dict[str, Any]]], host_id: str, stored: dict[str, Any]
    ) -> None:
        """Append one stamped event, indexing it and trimming the log."""
        log = logs.setdefault(host_id, [])
        index = self._reconcile(logs, host_id)
        log.append(stored)
        event_id = stored.get("event_id")
        if isinstance(event_id, str) and event_id:
            index[event_id] = stored
        seq = stored.get("sequence", 0)
        if isinstance(seq, int) and seq > self._last_seq.get(host_id, 0):
            self._last_seq[host_id] = seq
        overflow = len(log) - MAX_EVENT_LOG_PER_HOST
        if overflow > 0:
            for evicted in log[:overflow]:
                evicted_id = evicted.get("event_id")
                if isinstance(evicted_id, str) and index.get(evicted_id) is evicted:
                    del index[evicted_id]
            del log[:overflow]
        self._lengths[host_id] = len(log)


def build_command_updated(status_record: dict[str, Any], host_epoch: str) -> dict[str, Any] | None:
    """Build a command-status event without prompt/result bytes. Return None when status, target
    or host epoch cannot form a valid envelope.
    """
    command_id = status_record.get("command_id")
    status = status_record.get("status")
    target = status_record.get("target")
    if (
        not isinstance(command_id, str)
        or not command_id
        or status not in COMMAND_STATUSES
        or not isinstance(target, dict)
        or not target
        or not host_epoch
    ):
        return None
    return {
        "event_id": f"cmdupd-{command_id}-{status}",
        "host_epoch": host_epoch,
        "type": "command.updated",
        "resource_id": command_id,
        "payload": {
            "command_id": command_id,
            "status": status,
            "target": dict(target),
            "kind": str(status_record.get("kind", "")),
            **(
                {"error_code": status_record["error_code"]}
                if status_record.get("error_code")
                else {}
            ),
        },
    }


def prepare_event(
    host_id: str,
    partial: dict[str, Any],
    sequence: int,
    cursor_secret: bytes,
    now_ms: int,
) -> dict[str, Any]:
    """Stamp a connector event with server-owned sequence, cursor, protocol and host binding.
    Ignore connector values for those fields and reject invalid resulting envelopes.
    """
    if not isinstance(partial, dict):
        raise ValueError("event is not an object")
    event_id = partial.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        event_id = f"ev-{secrets.token_hex(8)}"
    host_epoch = partial.get("host_epoch", "")
    observed_at = partial.get("observed_at")
    if not isinstance(observed_at, str) or not observed_at:
        observed_at = datetime.fromtimestamp(now_ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    full: dict[str, Any] = {
        "protocol": GATEWAY_PROTOCOL,
        "event_id": event_id[:128],
        "host_id": host_id,
        "host_epoch": host_epoch,
        "sequence": sequence,
        "cursor": "",
        "type": partial.get("type", ""),
        "observed_at": observed_at,
    }
    for key in ("resource_id", "resource_revision", "payload"):
        if key in partial:
            full[key] = partial[key]
    try:
        full["cursor"] = encode_cursor(cursor_secret, {"host_id": host_id, "sequence": sequence})
    except CursorError as exc:
        raise ValueError("cursor secret misconfigured") from exc
    try:
        validate_metadata_event(full)
    except SchemaError as exc:
        raise ValueError(f"invalid event: {exc}") from exc
    return full
