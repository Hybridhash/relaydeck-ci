"""`relaydeck pair`: open a pairing window, show each device request and approve one only when
the owner types the code shown on that device. No flag ever approves.
"""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from relaydeck.companion.config import CompanionConfig
from relaydeck.companion.inspection import running
from relaydeck.companion.inspection import status as lifecycle
from relaydeck.contracts.pairing import MAX_WINDOW_S
from relaydeck.gateway.features.access.api import (
    HostRecord,
    HostStore,
    PairingError,
    PairingStore,
    known_host,
)

DEFAULT_TTL_S = 300
POLL_S = 1.0
PROMPT = "Type the code shown on the device (d = deny, Enter = keep waiting): "


def normalize_code(typed: str) -> str | None:
    """`123456`, `123-456` or `123 456` as `123-456`; anything else is not a code."""
    digits = re.sub(r"[\s-]", "", typed)
    if len(digits) != 6 or not digits.isascii() or not digits.isdigit():
        return None
    return f"{digits[:3]}-{digits[3:]}"


def _same(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode(), right.encode())


class _Ended(Exception):
    """The window closed, expired or was replaced."""


def run_pair(
    config: CompanionConfig,
    *,
    prompt: Callable[[str], str],
    say: Callable[[str], None],
    ttl_s: int = DEFAULT_TTL_S,
    status: Callable[[Path], dict[str, Any]] = lifecycle.status,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    if not 1 <= ttl_s <= MAX_WINDOW_S:
        say(f"The pairing window must be 1 to {MAX_WINDOW_S} seconds.")
        return 1
    try:
        body = status(config.state_dir)
    except (OSError, ValueError):
        body = {}
    if not running.ready(body, config):
        say("RelayDeck is not running and ready. Start it (relaydeck start --connect-herdr) first.")
        return 1
    try:
        record: HostRecord | None = HostStore.observe(
            config.state_dir / "hosts.json"
        ).record_for_host(config.host_id)
        host = known_host(record)
        store = PairingStore(config.state_dir / "pairing.json")
    except (PairingError, ValueError, OSError):
        say("This Mac is not enrolled, or its pairing state cannot be read. Run: relaydeck setup")
        return 1
    origin = config.advertised_origin

    def ms() -> int:
        return int(clock() * 1000)

    try:
        ours = store.open_window_if_absent(host, origin, ms(), ttl_s)
    except (PairingError, ValueError, OSError):
        say("The pairing window could not be opened.")
        return 1
    if ours is None:
        say("A pairing window is already open. Close it first with: relaydeck pairing close")
        return 1
    say(f"Pairing is open for {ttl_s} seconds. On the device, open {origin} and ask to pair.")
    say(f"Host {ascii(host.host_id)}; fingerprint {ascii(host.fingerprint)}")

    def alive() -> None:
        """Raises unless the window this wizard opened is still the open one."""
        try:
            info = store.window_info(host, origin, ms())
        except (PairingError, ValueError, OSError):
            raise _Ended from None
        if info is None or info["window_id"] != ours:
            raise _Ended

    def waiting() -> list[dict[str, Any]]:
        try:
            return store.pending(host, origin, ms())
        except (PairingError, ValueError, OSError):
            raise _Ended from None

    try:
        return _wizard(store, host, origin, alive, waiting, prompt, say, sleep, ms)
    except _Ended:
        say("The pairing window ended; nothing more was approved.")
        return 1
    except (KeyboardInterrupt, EOFError):
        say("Pairing cancelled.")
        return 130
    finally:
        try:
            store.close_window_if_owned(ours)
        except (PairingError, ValueError, OSError):
            pass


def _wizard(
    store: PairingStore,
    host: HostRecord,
    origin: str,
    alive: Callable[[], None],
    waiting: Callable[[], list[dict[str, Any]]],
    prompt: Callable[[str], str],
    say: Callable[[str], None],
    sleep: Callable[[float], None],
    ms: Callable[[], int],
) -> int:
    shown: set[str] = set()
    while True:
        alive()
        rows = waiting()
        if not rows:
            sleep(POLL_S)
            continue
        for row in rows:
            if row["request_id"] in shown:
                continue
            shown.add(row["request_id"])
            # Labels are untrusted; ascii() escapes terminal controls and non-ASCII.
            say(f"Request from a device named {ascii(row['device_label'])} (untrusted name)")
            say("  may: " + ", ".join(row["capabilities"]) + f"; host {ascii(row['host_id'])} only")
        typed = prompt(PROMPT).strip()
        alive()
        rows = waiting()  # re-read: the list may have changed while the owner was typing
        if not typed:
            sleep(POLL_S)
            continue
        deny = typed.lower() in ("d", "deny") or typed.lower().startswith("d ")
        code = normalize_code(typed[1:] if deny and typed.lower().startswith("d ") else typed)
        pending = [row for row in rows if row["state"] == "pending"]
        if deny and typed.lower() in ("d", "deny"):
            target = pending[0] if len(pending) == 1 else None
        else:
            matches = [r for r in pending if code and _same(r["matching_code"], code)]
            target = matches[0] if len(matches) == 1 else None
        if target is None:
            say("No single waiting request matches. Nothing was changed.")
            continue
        try:
            store.decide(
                target["request_id"], target["matching_code"], host, origin, ms(), approve=not deny
            )
        except PairingError:
            say("That request is no longer waiting. Nothing was changed.")
            continue
        if deny:
            say("Request denied.")
            continue
        say("Approved. Waiting for the device to finish...")
        return _until_consumed(target["request_id"], alive, waiting, say, sleep)


def _until_consumed(
    request_id: str,
    alive: Callable[[], None],
    waiting: Callable[[], list[dict[str, Any]]],
    say: Callable[[str], None],
    sleep: Callable[[float], None],
) -> int:
    while True:
        alive()
        if request_id not in {row["request_id"] for row in waiting()}:
            say("Device paired. The pairing window is closed.")
            return 0
        sleep(POLL_S)
