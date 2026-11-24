"""Session-side helpers of the terminal detail screens: drive constants, the
snooping socket, the recovery driver, `DetailSessions`, gated composer send.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from relaydeck.client.features.composer.api import ComposerStore
from relaydeck.client.features.terminal.gap_recovery import (
    DRIVE_DEFERRED,
    DRIVE_EXHAUSTED,
    DRIVE_REACQUIRE,
)
from relaydeck.client.features.terminal.input_modes import (
    ImeState,
    InputModeState,
    KeyEventDeduper,
    submit_allowed,
)
from relaydeck.client.features.terminal.keys import (
    KeyInputSession,
    bind_keys_session,
)
from relaydeck.client.features.terminal.paste_review import (
    STALE_MESSAGE,
    PasteReviewSession,
    bind_review_session,
)
from relaydeck.client.ports.terminal import ShellOpError
from relaydeck.client.ports.transport import TerminalSocket

if TYPE_CHECKING:
    from relaydeck.client.features.terminal.controller import TerminalController


DRIVE_RESUMED = "resumed"
DRIVE_REATTACHED = "reattached"
DRIVE_IDLE = "idle"
DRIVE_REFUSED_EXITED = "refused-exited"

SEND_BLOCKED_IME = "blocked-ime-composing"

# Bye details that prove the remote ended the terminal. The gateway never emits them yet; reserved
# so a future exit control lands in the terminal state, not a reconnect loop.
EXIT_DETAILS = frozenset({"closed", "exited"})

# Agent attach always takes upstream ownership: the agent surface
# discloses this before confirming.
TAKEOVER_DISCLOSURE_AGENT = (
    "Attaching takes upstream ownership of this terminal and may resize it. "
    "Another client's attachment will be disconnected. "
    "While you control this pane it uses this device's size; it returns when you leave."
)


class SnoopingTerminalSocket(TerminalSocket):
    """A `TerminalSocket` wrapper that records the last control doc and the bytes seen. The
    controller swallows control docs (`_handle_control` is a no-op) and offers no exit hook;
    the detail classifies run() exit from this record instead (bye/closed|exited -> terminal
    exit, anything else -> disconnected).
    """

    def __init__(self, inner: TerminalSocket) -> None:
        self._inner = inner
        self.last_control: dict[str, Any] | None = None
        self.bytes_seen = 0
        self.texts_seen = 0

    async def connect(self) -> None:
        await self._inner.connect()

    async def send_bytes(self, data: bytes) -> None:
        await self._inner.send_bytes(data)

    async def send_text(self, data: str) -> None:
        await self._inner.send_text(data)

    async def receive(self) -> tuple[str, Any]:
        kind, payload = await self._inner.receive()
        if kind == "text":
            self.texts_seen += 1
            try:
                doc = json.loads(payload)
            except (ValueError, TypeError):
                doc = None
            if isinstance(doc, dict):
                self.last_control = doc
        else:
            self.bytes_seen += len(payload)
        return (kind, payload)

    async def close(self) -> None:
        await self._inner.close()


def offered_position(controller: TerminalController) -> tuple[str, int | None]:
    """The stream client's current (epoch, offered_offset) for reattach."""
    return controller.offered_stream_position()


async def drive_recovery(
    controller: TerminalController,
    ticket_fn: Callable[[str], Awaitable[str]],
    *,
    exited: bool = False,
    fresh_viewer: bool = False,
    current: Callable[[], bool] = lambda: True,
) -> str:
    """Perform the recovery action indicated by resume or reattach state. Exited terminals
    refuse recovery without network traffic.
    """
    if exited or not current():
        return DRIVE_REFUSED_EXITED
    pending = controller.pending_resume
    epoch, offered = offered_position(controller)
    target: tuple[str, int] | None = None
    action = DRIVE_IDLE
    if fresh_viewer:
        target = ("", 0)  # a new renderer needs the capped authoritative baseline
        action = DRIVE_REATTACHED
    elif controller.live_edge_pending:
        if controller._live_edge_opening:
            return DRIVE_DEFERRED
        if controller._overflow_attempts >= 3:
            return DRIVE_EXHAUSTED
        target = (controller._client.epoch, controller._client.next_offset)
        action = DRIVE_RESUMED
    elif pending is not None and not (
        pending.epoch == "" and pending.offset == 0 and offered is not None
    ):
        target = (pending.epoch, pending.offset)
        action = DRIVE_RESUMED
    elif offered is not None:
        target = (epoch, offered)
        action = DRIVE_REATTACHED if controller.reattach_needed else DRIVE_RESUMED
    elif controller.reattach_needed:
        target = ("", 0)
        action = DRIVE_REATTACHED
    if target is None:
        return DRIVE_IDLE
    if (
        not fresh_viewer
        and not controller.live_edge_pending
        and not await controller.wait_gap_recovery(current)
    ):
        if controller.gap_recovery.exhausted:
            return DRIVE_EXHAUSTED
        return DRIVE_DEFERRED if current() else DRIVE_REFUSED_EXITED
    if (
        not fresh_viewer
        and not controller.live_edge_pending
        and controller.mode == "control"
        and controller.gap_recovery.detail
        and (
            controller.gap_recovery.delay > 2
            or time.monotonic() - controller.gap_recovery.gap_at >= 5
        )
    ):
        return DRIVE_REACQUIRE
    if not current():
        return DRIVE_REFUSED_EXITED
    if (
        not fresh_viewer
        and controller.live_edge_pending
        and not controller.begin_live_edge_recovery()
    ):
        return DRIVE_EXHAUSTED
    try:
        ticket = await ticket_fn(controller.lease_id)
    except BaseException as exc:
        controller._live_edge_opening = False
        if (
            controller.live_edge_pending
            and isinstance(exc, ShellOpError)
            and exc.code == "lease_expired"
        ):
            controller.invalidate_writer("lease_expired")
            return DRIVE_REFUSED_EXITED
        raise
    if not current():
        return DRIVE_REFUSED_EXITED
    await controller.close()
    if not current():
        return DRIVE_REFUSED_EXITED
    pending = controller.pending_resume
    reattach = controller.reattach_needed
    live_edge_pending = controller.live_edge_pending
    if fresh_viewer:
        controller.live_edge_pending = False
    controller.pending_resume = None
    controller.reattach_needed = False
    try:
        await controller.start(ticket, target[0], target[1])
    except Exception:
        # The reopen failed (transport down): restore both flags so the
        # recovery stays actionable for an explicit retry instead of
        # silently dropping to idle with a dead stream.
        controller._live_edge_opening = False
        controller.live_edge_pending = live_edge_pending
        controller.pending_resume = pending
        controller.reattach_needed = reattach
        raise
    return action


class DetailSessions:
    """One detail's input sessions. Owns the `KeyInputSession` (via `bind_keys_session`: sole
    current writer probe + exact-bytes sender), the `PasteReviewSession` (via
    `bind_review_session`), the `ImeState`, and the `KeyEventDeduper` for one terminal
    detail, plus its `InputModeState`.
    """

    def __init__(self, *, modes: InputModeState) -> None:
        self._modes = modes
        self._keys: KeyInputSession | None = None
        self._paste: PasteReviewSession | None = None
        self._writer_probe: Callable[[], int | None] | None = None
        self._epoch = 0
        self.ime = ImeState()
        self.deduper = KeyEventDeduper()

    @property
    def modes(self) -> InputModeState:
        return self._modes

    @property
    def keys(self) -> KeyInputSession:
        if self._keys is None:
            raise RuntimeError("DetailSessions not bound")
        return self._keys

    @property
    def paste(self) -> PasteReviewSession:
        if self._paste is None:
            raise RuntimeError("DetailSessions not bound")
        return self._paste

    @property
    def bound(self) -> bool:
        return self._keys is not None and self._paste is not None

    @property
    def epoch(self) -> int:
        """Mount generation: bumped on every bind/retarget/invalidate so
        a remount never resurrects stale input-callback tokens (a fresh
        `KeyInputSession` restarts its own token at 0)."""
        return self._epoch

    def bind(self, controller: TerminalController) -> None:
        """First mount: bind both sessions to the live controller."""
        self._keys = bind_keys_session(controller)
        self._paste = bind_review_session(controller)
        self._writer_probe = controller.key_generation
        self._epoch += 1

    def has_live_writer(self) -> bool:
        """Whether the bound target currently has a live control writer
        (the keys.py mount contract for `ToolsKeysDock enabled=`): bound
        sessions whose generation probe still resolves. Unbound, view
        leases, and closed sockets all report False."""
        if self._keys is None or self._writer_probe is None:
            return False
        return self._writer_probe() is not None

    def retarget(self, controller: TerminalController) -> str | None:
        """Rebind to a replacement controller (takeover/reconnect/new
        selection). Returns `STALE_MESSAGE` when an open paste review was
        dropped (surface it; nothing was sent), else None."""
        if self._keys is None:
            self.bind(controller)
            return None
        dropped = self._paste is not None and self._paste.pending is not None
        self._keys.retarget(
            controller.lease_id,
            controller.key_generation,
            controller.send_key_bytes,
        )
        self._paste = bind_review_session(controller)
        self._writer_probe = controller.key_generation
        self._epoch += 1
        self.deduper.reset()
        self.ime.reset()
        return STALE_MESSAGE if dropped else None

    def invalidate(self) -> str | None:
        """Unmount/teardown: drop queued input, clear modifiers, kill the
        token, reset deduper + IME, drop any open review (fail closed)."""
        dropped = self._paste is not None and self._paste.pending is not None
        if self._keys is not None:
            self._keys.invalidate()
        if self._paste is not None:
            self._paste.cancel_paste()
        self._keys = None
        self._paste = None
        self._writer_probe = None
        self._epoch += 1
        self.deduper.reset()
        self.ime.reset()
        return STALE_MESSAGE if dropped else None

    def submit_allowed(self) -> bool:
        """IME completion gate for every Send call site."""
        return submit_allowed(self.ime)


async def gated_composer_send(
    store: ComposerStore, ime: ImeState, *, prompt_only: bool = False
) -> str:
    """Gate Send on committed IME input before delegating to the composer store. Active
    composition returns SEND_BLOCKED_IME without changing the store or submitting a command.
    """
    if not submit_allowed(ime):
        return SEND_BLOCKED_IME
    return await store.send(prompt_only=True) if prompt_only else await store.send()
