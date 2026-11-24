"""Session close command envelope."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from relaydeck.contracts.schemas import GATEWAY_PROTOCOL


def envelope(
    *,
    command_id: str,
    host_id: str,
    host_epoch: str,
    kind: str,
    target: dict[str, str],
    payload: dict[str, Any],
    now: datetime | None = None,
    ttl_seconds: int = 120,
) -> dict[str, Any]:
    at = now or datetime.now(UTC)
    return {
        "protocol": GATEWAY_PROTOCOL,
        "command_id": command_id,
        "host_id": host_id,
        "host_epoch": host_epoch,
        "kind": kind,
        "target": target,
        "payload": payload,
        "expires_at": (at + timedelta(seconds=ttl_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def session_close_command(
    *,
    command_id: str,
    host_id: str,
    host_epoch: str,
    terminal_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Close the Herdr pane for a shell terminal. Payload is empty."""
    return envelope(
        command_id=command_id,
        host_id=host_id,
        host_epoch=host_epoch,
        kind="session.close",
        target={"terminal_id": terminal_id},
        payload={},
        now=now,
    )
