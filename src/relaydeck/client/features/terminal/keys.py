"""Terminal key encodings and generation-bound key/text emission. The encoder uses pinned xterm2
bytes over text frames because upstream key-combo names cannot express the full navigation
set.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from relaydeck.client.models.input.keys import MOD_ALT as MOD_ALT
from relaydeck.client.models.input.keys import MOD_CTRL as MOD_CTRL
from relaydeck.client.models.input.keys import MOD_SHIFT as MOD_SHIFT
from relaydeck.client.models.input.keys import MODIFIERS as MODIFIERS
from relaydeck.client.models.input.keys import SHIFT_ENTER as SHIFT_ENTER
from relaydeck.client.models.input.keys import SHIFT_TAB as SHIFT_TAB

if TYPE_CHECKING:
    from relaydeck.client.ports.terminal import KeyWriter as TerminalController


# Canonical special-key names (lowercase). Single characters address the
# the character case and are matched case-sensitively.

_SPECIAL_BYTES: dict[str, bytes] = {
    "escape": b"\x1b",
    "tab": b"\t",
    "enter": b"\r",
    "backspace": b"\x7f",
    "left": b"\x1b[D",
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "home": b"\x1b[H",
    "end": b"\x1b[F",
    "insert": b"\x1b[2~",
    "delete": b"\x1b[3~",
    "f1": b"\x1bOP",
    "f2": b"\x1bOQ",
    "f3": b"\x1bOR",
    "f4": b"\x1bOS",
    "f5": b"\x1b[15~",
    "f6": b"\x1b[17~",
    "f7": b"\x1b[18~",
    "f8": b"\x1b[19~",
    "f9": b"\x1b[20~",
    "f10": b"\x1b[21~",
    "f11": b"\x1b[23~",
    "f12": b"\x1b[24~",
    "page-up": b"\x1b[5~",
    "page-down": b"\x1b[6~",
}

_SHIFTED_SPECIAL: dict[str, bytes] = {
    "tab": b"\x1b[Z",  # CSI Z (keytab shift-tab)
    "enter": b"\n",  # Ctrl-J (Shift+Enter override)
}

_KEY_ALIASES: dict[str, str] = {
    # Quick-key case names.
    "esc": "escape",
    "shiftTab": SHIFT_TAB,
    "shiftEnter": SHIFT_ENTER,
    "arrowLeft": "left",
    "arrowUp": "up",
    "arrowDown": "down",
    "arrowRight": "right",
    "forwardDelete": "delete",
    "forward-delete": "delete",
    "pageUp": "page-up",
    "pageDown": "page-down",
    # Common spellings.
    "return": "enter",
    "bs": "backspace",
    "del": "delete",
    "pgup": "page-up",
    "pgdn": "page-down",
    "pagedown": "page-down",
    "pageup": "page-up",
    "space": " ",
}

# Lowercased alias index so case-name spellings resolve
# case-insensitively ("ShiftTab", "ARROWLEFT"); values agree on collision.
_KEY_ALIASES_LOWER: dict[str, str] = {key.lower(): value for key, value in _KEY_ALIASES.items()}

_UNSHIFTED = "`1234567890-=[]\\;',./"
_SHIFTED = '~!@#$%^&*()_+{}|:"<>?'

_CTRL_PUNCTUATION: dict[str, int] = {
    "@": 0x00,
    "[": 0x1B,
    "\\": 0x1C,
    "]": 0x1D,
    "^": 0x1E,
    "_": 0x1F,
    "?": 0x7F,
}


def normalize_key_name(token: str) -> str | None:
    """Resolve a key token to its canonical name. Single characters are character keys (case
    preserved: typing "C" sends "C").
    """
    if len(token) == 1:
        # Single characters are character keys verbatim (this check runs
        # before strip so the space key survives).
        return token
    text = token.strip()
    if len(text) == 1:
        return text
    lowered = text.lower()
    if lowered in _SPECIAL_BYTES or lowered in (SHIFT_TAB, SHIFT_ENTER):
        return lowered
    alias = _KEY_ALIASES_LOWER.get(lowered)
    if alias is not None:
        return alias
    if lowered.startswith("f") and lowered[1:].isdigit() and len(lowered) <= 3:
        number = int(lowered[1:])
        if 1 <= number <= 12:
            return f"f{number}"
        return None
    if len(text) == 4 and text.startswith("key") and text[3:].isalpha():
        return text[3:]
    return None


def shifted_char(char: str) -> str:
    """US-layout shift
    pairing, otherwise uppercase. Single characters only."""
    if char in _UNSHIFTED:
        return _SHIFTED[_UNSHIFTED.index(char)]
    return char.upper()


def _control_code(char: str) -> int | None:
    """C0 control pairing for Ctrl+character: ASCII letters to 0x01-0x1A,
    space to NUL, and the standard `@[\\]^_?` punctuation pairing. Digits,
    other punctuation, and non-ASCII return None (unencodable)."""
    if char == " ":
        return 0x00
    upper = char.upper()
    if len(upper) == 1 and "A" <= upper <= "Z":
        return ord(upper) - 64
    return _CTRL_PUNCTUATION.get(char)


def encode_key(name: str, modifiers: frozenset[str] = frozenset()) -> bytes | None:
    """Encode one key plus modifiers to exact terminal bytes. Returns None for unknown keys,
    unknown modifier tokens, and combos no fixture pins (Ctrl/Shift with arrows, Home/End,
    Insert/Delete, PgUp/PgDn, function keys; Ctrl with digits or non-ASCII).
    """
    key = normalize_key_name(name)
    if key is None:
        return None
    mods = set(modifiers)
    if not mods <= MODIFIERS:
        return None
    if key in (SHIFT_TAB, SHIFT_ENTER):
        # Shift-tab/shift-enter buttons resolve through the shifted form;
        # only an armed Shift may redundantly accompany them.
        if mods - {MOD_SHIFT}:
            return None
        key = "tab" if key == SHIFT_TAB else "enter"
        mods = {MOD_SHIFT}
    if key in _SPECIAL_BYTES:
        return _encode_special(key, mods)
    return _encode_character(key, mods)


def _encode_special(key: str, mods: set[str]) -> bytes | None:
    base = _SPECIAL_BYTES[key]
    if not mods:
        return base
    rest = set(mods)
    if MOD_SHIFT in rest:
        shifted = _SHIFTED_SPECIAL.get(key)
        if shifted is None:
            return None
        base = shifted
        rest.discard(MOD_SHIFT)
    if rest == {MOD_ALT}:
        return b"\x1b" + base
    if not rest:
        return base
    return None


def _encode_character(char: str, mods: set[str]) -> bytes | None:
    rest = set(mods)
    if MOD_SHIFT in rest:
        char = shifted_char(char)
        rest.discard(MOD_SHIFT)
    if MOD_CTRL in rest:
        code = _control_code(char)
        if code is None:
            return None
        data = bytes((code,))
        rest.discard(MOD_CTRL)
    else:
        data = char.encode("utf-8")
    if rest == {MOD_ALT}:
        return b"\x1b" + data
    if not rest:
        return data
    return None


class KeyInputError(Exception):
    """Keys/direct-text input failure. `no_writer` means no live control
    writer: an explicit failure with no hidden fallback."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class KeyEmission:
    """One bound emission: exact bytes for a target terminal at a writer
    generation. `flush` sends only emissions still bound to the live
    target+generation; anything else is dropped, never delayed."""

    target_id: str
    generation: int
    data: bytes
    label: str


def _label(key: str, mods: set[str]) -> str:
    parts = sorted(mods)
    parts.append(key)
    return "+".join(parts)


class KeyInputSession:
    """One-shot modifiers plus generation/target-bound key/text emissions. `generation_probe`
    returns the current writer generation, or None when no writer is live.
    """

    def __init__(
        self,
        *,
        target_id: str,
        generation_probe: Callable[[], int | None],
        sender: Callable[[bytes], Awaitable[None]],
    ) -> None:
        self._target_id = target_id
        self._probe = generation_probe
        self._sender = sender
        self._pending: set[str] = set()
        self._outbox: list[KeyEmission] = []
        self._token = 0

    @property
    def target_id(self) -> str:
        return self._target_id

    @property
    def token(self) -> int:
        """Callback token: callbacks capture it at bind time and pass it
        back; a mismatch means unmount/retarget happened since."""
        return self._token

    @property
    def pending_modifiers(self) -> frozenset[str]:
        return frozenset(self._pending)

    @property
    def outbox(self) -> tuple[KeyEmission, ...]:
        return tuple(self._outbox)

    # -- one-shot modifiers ----------------------------------------------
    def is_modifier_armed(self, modifier: str) -> bool:
        return modifier in self._pending

    def set_modifier_armed(self, modifier: str, armed: bool) -> None:
        if modifier not in MODIFIERS:
            raise KeyInputError("bad_modifier", f"unknown modifier {modifier!r}")
        if armed:
            self._pending.add(modifier)
        else:
            self._pending.discard(modifier)

    def toggle_modifier(self, modifier: str) -> None:
        self.set_modifier_armed(modifier, not self.is_modifier_armed(modifier))

    # -- emissions ------------------------------------------------------------
    def _live(self, token: int | None) -> int | None:
        if token is not None and token != self._token:
            return None
        return self._probe()

    def press_key(
        self,
        name: str,
        *,
        physical_modifiers: frozenset[str] = frozenset(),
        token: int | None = None,
    ) -> KeyEmission | None:
        """Encode a tools/dock key (union of armed + physical modifiers) and queue it bound to
        the live target+generation. Stale token, dead writer, and unencodable combos return
        None with the armed set preserved (blocked or failed keys never consume).
        """
        generation = self._live(token)
        if generation is None:
            return None
        mods = self._pending | set(physical_modifiers)
        if not mods <= MODIFIERS:
            return None
        data = encode_key(name, frozenset(mods))
        if data is None:
            return None
        key = normalize_key_name(name)
        assert key is not None  # encode_key succeeded, so the name resolved  # noqa: S101
        emission = KeyEmission(self._target_id, generation, data, _label(key, mods))
        self._outbox.append(emission)
        self._pending.clear()
        return emission

    def commit_text(self, text: str, *, token: int | None = None) -> KeyEmission | None:
        """Queue committed (post-IME) text bound to the live target. A single character with
        armed modifiers encodes as a character key (LF/CR as Enter); when that encoding fails
        the original text still flows and the armed set stays.
        """
        if not text:
            return None
        generation = self._live(token)
        if generation is None:
            return None
        if self._pending and len(text) == 1:
            key_name = "enter" if text in ("\n", "\r") else text
            data = encode_key(key_name, frozenset(self._pending))
            if data is not None:
                emission = KeyEmission(
                    self._target_id, generation, data, _label(key_name, set(self._pending))
                )
                self._outbox.append(emission)
                self._pending.clear()
                return emission
        payload = b"\r" if text == "\n" else text.encode("utf-8")
        emission = KeyEmission(self._target_id, generation, payload, f"text:{len(text)}")
        self._outbox.append(emission)
        return emission

    def commit_bytes(self, data: bytes, *, token: int | None = None) -> KeyEmission | None:
        """Queue exact engine-encoded bytes bound to the live target. The Dart/xterm2
        physical-key path (protocol ORIGIN_KEY) and the blocked-composer-adjacent raw paths
        arrive pre-encoded: modifiers were already applied engine-side, so the armed set is
        neither consulted nor consumed.
        """
        if not data:
            return None
        generation = self._live(token)
        if generation is None:
            return None
        emission = KeyEmission(self._target_id, generation, data, f"raw:{len(data)}")
        self._outbox.append(emission)
        return emission

    def interrupt(self, *, token: int | None = None) -> KeyEmission | None:
        """Control-only interrupt: pre-armed
        Alt/Shift are discarded so the chord cannot become Ctrl+Alt+C."""
        if token is not None and token != self._token:
            return None
        self._pending.clear()
        generation = self._probe()
        if generation is None:
            return None
        data = encode_key("c", frozenset({MOD_CTRL}))
        assert data == b"\x03"  # C0 pairing fixture  # noqa: S101
        emission = KeyEmission(self._target_id, generation, data, "ctrl+c")
        self._outbox.append(emission)
        return emission

    async def flush(self) -> int:
        """Send queued emissions still bound to the live target and generation, returning the
        count sent before failure. Clear the queue before sending so stale or partial input
        cannot be blindly replayed.
        """
        pending = self._outbox
        self._outbox = []
        current = self._probe()
        if current is None:
            return 0
        sent = 0
        for emission in pending:
            if emission.target_id != self._target_id or emission.generation != current:
                continue
            await self._sender(emission.data)
            sent += 1
        return sent

    def cancel_outbox(self) -> int:
        """Drop queued emissions. Provably sends nothing."""
        count = len(self._outbox)
        self._outbox.clear()
        return count

    # -- target lifecycle -------------------------------------------------------
    def retarget(
        self,
        target_id: str,
        generation_probe: Callable[[], int | None],
        sender: Callable[[bytes], Awaitable[None]],
    ) -> None:
        """Bind a new target terminal: queued input is dropped, modifiers
        cleared, and the callback token killed."""
        self._target_id = target_id
        self._probe = generation_probe
        self._sender = sender
        self._pending.clear()
        self._outbox.clear()
        self._token += 1

    def invalidate(self) -> None:
        """Unmount/teardown: drop queued input, clear modifiers, and kill
        the callback token. Later callbacks with the old token emit nothing."""
        self._pending.clear()
        self._outbox.clear()
        self._token += 1


def bind_keys_session(
    controller: TerminalController, target_id: str | None = None
) -> KeyInputSession:
    """Wire a session to the sole current writer: the controller's live
    control-lease generation probe plus its exact-bytes sender. The target
    defaults to the controller's lease identity."""
    return KeyInputSession(
        target_id=target_id if target_id is not None else controller.lease_id,
        generation_probe=controller.key_generation,
        sender=controller.send_key_bytes,
    )
