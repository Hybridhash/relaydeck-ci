"""Offer an empty private probe folder while leaving tool trust to the user's own terminal
prompt. Never send trust answers or write the tool's trust store.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from relaydeck.usage.limits_plane.trust_store import (
    ensure_probe_folder,
    folder_is_trusted,
)

TRUSTED = "trusted"


NOT_TRUSTED = "not trusted"


NOT_INSTALLED = "not installed"


FIX_HINT = "run: relaydeck-companion usage install"


Spawn = Callable[[Sequence[str], Path], int]


@dataclass(frozen=True)
class ProbeTarget:
    kind: str
    binary: str
    folder: Path
    trust_store: str | None


def on_path(name: str) -> str | None:
    return shutil.which(name)


def probe_state(target: ProbeTarget, which: Callable[[str], Any] = on_path) -> str:
    if which(target.binary) is None:
        return NOT_INSTALLED
    if target.trust_store and folder_is_trusted(target.trust_store, target.folder):
        return TRUSTED
    return NOT_TRUSTED


def doctor_line(target: ProbeTarget, state: str) -> str:
    detail = f"{NOT_TRUSTED} ({FIX_HINT})" if state == NOT_TRUSTED else state
    if state == NOT_INSTALLED:
        detail = f"{target.binary} not installed"
    return f"{target.binary} probe folder {target.folder}: {detail}"


def run_in_terminal(argv: Sequence[str], cwd: Path) -> int:
    # Inherits stdin, stdout and stderr: the user answers the tool's prompt themselves.
    return subprocess.run(list(argv), cwd=cwd, check=False).returncode  # noqa: S603 - recipe binary found via which; interactive trust prompt


def offer_trust(
    target: ProbeTarget,
    *,
    ask: Callable[[str], bool],
    spawn: Spawn = run_in_terminal,
    interactive: bool | None = None,
    which: Callable[[str], Any] = on_path,
    say: Callable[[str], None] | None = None,
) -> str:
    """Create the folder, and when it isn't trusted offer to open the tool there. Returns the
    state afterwards.
    """
    out = say or (lambda text: print(text, file=sys.stderr))
    if which(target.binary) is None:
        return NOT_INSTALLED
    if not ensure_probe_folder(target.folder):
        out(
            f"Could not prepare the {target.binary} probe folder {target.folder}. "
            "It must be a plain folder you own; fix that, then try again."
        )
        return probe_state(target, which)
    state = probe_state(target, which)
    if state == TRUSTED:
        return state
    live = sys.stdin.isatty() if interactive is None else interactive
    if not live:
        name = target.binary
        out(f"Trust RelayDeck's {name} folder once: run `{name}` in {target.folder}, choose Trust.")
        return state
    out(
        f"RelayDeck reads {target.binary} usage in its own empty folder; "
        f"{target.binary} asks once whether to trust it."
    )
    if not ask(f"Open {target.binary} there now so you can choose Trust? [y/N] "):
        out(f"Skipped. Later: run `{target.binary}` in {target.folder} and choose Trust.")
        return state
    out(f"Choose Trust in {target.binary}, then type /quit.")
    try:
        spawn((target.binary,), target.folder)
    except OSError:
        # The exit code says nothing about trust either way; only the tool's own record does.
        out(
            f"Could not open {target.binary}. "
            f"Later: run `{target.binary}` in {target.folder} and choose Trust."
        )
        return probe_state(target, which)
    state = probe_state(target, which)
    out(f"{target.binary} probe folder is now {state}.")
    return state


def ask_on_terminal(prompt: str) -> bool:
    print(prompt, end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().strip().lower() in ("y", "yes")
