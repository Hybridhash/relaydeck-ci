"""Shared gateway records, typed errors and service interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TerminalLease:
    lease_id: str
    host_id: str
    terminal_id: str
    generation: int
    owner: str
    expires_ms: int
    epoch: str = "e1"
    mode: str = "control"
    device_id: str = ""
    attachment_id: str = ""
    resume_pending: bool = False


@dataclass(frozen=True)
class OutputBatch:
    chunks: tuple[tuple[int, bytes], ...]
    next_offset: int


@dataclass(frozen=True)
class ResizeRequest:
    """One controller-authorized PTY geometry change. Recorded in the backend resize_log when a
    control lease's resize frame validates.
    """

    lease_id: str
    generation: int
    input_seq: int
    cols: int
    rows: int


@dataclass(frozen=True)
class StreamBatch:
    """One offset-addressed ring read. `status` is ok, stale_offset or
    stale_epoch; stale batches carry no bytes (ring.RingRead rule)."""

    status: str
    chunks: tuple[tuple[int, bytes], ...]
    next_offset: int
    epoch: str


class BackendError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = dict(details or {})
