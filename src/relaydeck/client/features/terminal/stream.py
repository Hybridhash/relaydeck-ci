"""Assemble ordered terminal output and map discontinuities to explicit resume or reattach
recovery. Epoch changes, offset gaps and fresh-emulator uncertainty never become silent
continuation.
"""

from __future__ import annotations

from dataclasses import dataclass

from relaydeck.client.features.terminal.models import OLDER_OUTPUT_NOT_KEPT
from relaydeck.contracts.wire.terminal import OutputChunk

EVENT_DATA = "data"
EVENT_GAP = "gap"
EVENT_RESET = "reset"

ACTION_CONTINUE = "continue"
ACTION_RESUME = "resume"
ACTION_REATTACH = "reattach"

EMULATOR_PRESERVED = "preserved"
EMULATOR_FRESH = "fresh"

INCOMPLETE_HISTORY = OLDER_OUTPUT_NOT_KEPT
VIEWER_OVERFLOW_HISTORY = (
    "Output lost (viewer too slow): reattached live; earlier output unavailable."
)


@dataclass(frozen=True)
class StreamEvent:
    kind: str
    offset: int = 0
    data: bytes = b""
    detail: str = ""


@dataclass(frozen=True)
class ResumeRequest:
    epoch: str
    offset: int


@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    notice: str | None = None
    resume: ResumeRequest | None = None


class TerminalByteStream:
    """Exactly-once ordered assembly over OutputChunks. Parser state is
    preserved by delivering every byte once, in order, with chunk
    boundaries invisible downstream (the emulator decodes incrementally)."""

    def __init__(self, epoch: str = "", next_offset: int = 0) -> None:
        self._epoch = epoch
        self._next = next_offset

    @property
    def epoch(self) -> str:
        return self._epoch

    @property
    def next_offset(self) -> int:
        return self._next

    def push(self, chunk: OutputChunk) -> StreamEvent:
        if self._epoch and chunk.epoch != self._epoch:
            return StreamEvent(
                EVENT_RESET, self._next, b"", f"epoch {self._epoch} -> {chunk.epoch}"
            )
        self._epoch = chunk.epoch
        if chunk.offset != self._next:
            return StreamEvent(EVENT_GAP, self._next, b"", f"offset {self._next} -> {chunk.offset}")
        self._next = chunk.offset + len(chunk.data)
        return StreamEvent(EVENT_DATA, chunk.offset, chunk.data)


def decide_recovery(
    event: StreamEvent,
    stream: TerminalByteStream,
    emulator: str,
    start_offset: int = 0,
) -> RecoveryDecision:
    """Map a stream event to resume/reattach. `emulator` is preserved when the live emulator
    kept its state across the reconnect (same process) and fresh after an app restart or a
    brand-new viewer.
    """
    if event.kind == EVENT_RESET:
        return RecoveryDecision(ACTION_REATTACH, INCOMPLETE_HISTORY)
    if event.kind == EVENT_GAP:
        if emulator == EMULATOR_PRESERVED:
            return RecoveryDecision(
                ACTION_RESUME, None, ResumeRequest(stream.epoch, stream.next_offset)
            )
        return RecoveryDecision(ACTION_REATTACH, INCOMPLETE_HISTORY)
    if emulator == EMULATOR_FRESH and start_offset > 0:
        # Live edge, not a replay: continue the bytes but label the
        # screen incomplete rather than whole.
        return RecoveryDecision(ACTION_CONTINUE, INCOMPLETE_HISTORY)
    return RecoveryDecision(ACTION_CONTINUE)


def decide_viewer_overflow() -> RecoveryDecision:
    """The display queue overflowed: bytes were lost downstream, so the
    emulator state is untrustworthy. Reset and reattach with notice."""
    return RecoveryDecision(ACTION_REATTACH, VIEWER_OVERFLOW_HISTORY)
