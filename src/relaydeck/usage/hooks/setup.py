"""Read bounded installation evidence without executing commands. Preserve the managed tool
interpreter entry, including its symlink spelling, rather than substituting a base
interpreter.
"""

from __future__ import annotations

import hashlib
import math
import os
import shlex
import stat
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from relaydeck.usage.contracts import LIMIT_STALE_MS
from relaydeck.usage.hooks import files as fs
from relaydeck.usage.hooks.files import HookFileError, document

HOOK_VERSION = 1
HOOK_FILENAME = "statusline_limits_hook.py"
HOOK_SHA256 = "563433525dc3da91f97cffac4e371776be53a424a73ba33961ba0d3e8746a458"
# Previous RelayDeck hook versions this build may upgrade from (shipped allow-list): earlier
# revisions that may be installed on the owner's Mac. Nothing else is upgraded.
PREVIOUS_HOOK_SHA256 = (
    "eece0979aa2ccd21be47121fd2ba2a958a7a2caf8ccf4c8a3b8b24c7995c0c64",
    "f5632268feff5fa6ee8ec65e97049bbbd30f3f42f8000d977267fced84f0e5e7",
    "81833a623b5e14b8b0cf242d87ffdbe3fafb01ceee87e06845bc946fcde4914c",
    "1cf6b549e22c8d0bc1efaf245da1630dbb242ce35c6acc85225b2909eef4b9a6",
    "bc6f77c25219ec3b759df89de39b39960ee3c35b3393b8ad39b3131eb56e59eb",
)
MAX_SETUP_BYTES = 1024 * 1024
CHECK_INTERVAL_S = 60


def default_hook_path() -> Path:
    return Path.home() / ".config/relaydeck/hooks" / HOOK_FILENAME


def managed_command(script: Path, cache: Path, interpreter: Path) -> str:
    return " ".join(shlex.quote(str(p)) for p in (interpreter, script)) + (
        " --cache-path " + shlex.quote(str(cache))
    )


def bound_interpreter(recorded: object, current: Path) -> Path:
    """The recorded spelling when it is the current interpreter's own directory entry reached
    through another path (a linked parent folder), else `current`. A sibling environment on
    the same base Python, the base interpreter itself and a copy are different entries.
    """
    if recorded == str(current):
        return current
    try:
        if not isinstance(recorded, str) or "\0" in recorded:
            return current
        path = Path(recorded)
        if not path.is_absolute() or ".." in path.parts or path.name != current.name:
            return current
        mine, theirs = os.lstat(path), os.lstat(current)
        if (mine.st_dev, mine.st_ino) != (theirs.st_dev, theirs.st_ino):
            return current
        info = path.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or not info.st_mode & 0o111
            or info.st_mode & 0o022
            or info.st_uid not in (0, os.getuid())
        ):
            return current
    except (OSError, ValueError, TypeError):
        return current
    return path


def _read(path: Path, *, private: bool) -> bytes:
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & (0o077 if private else 0o022)
            or info.st_size > MAX_SETUP_BYTES
        ):
            raise ValueError
        value = stream.read(MAX_SETUP_BYTES + 1)
    if len(value) > MAX_SETUP_BYTES:
        raise ValueError
    return value


class HookSetupCheck:
    def __init__(
        self,
        settings: Path | None,
        cache: Path | None,
        script: Path | None,
        *,
        interpreter: Path | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings, self.cache, self.script = settings, cache, script
        # Keep the tool environment, including its symlinked Python. Resolving it
        # would change the managed command to the base/system interpreter.
        self.interpreter = interpreter or Path(sys.executable)
        self.monotonic = monotonic
        self._lock = threading.Lock()
        self._next = float("-inf")
        self._result: dict[str, Any] | None = None

    def invalidate(self) -> None:
        """Forget the cached result so the next check re-evaluates immediately."""
        with self._lock:
            self._next = float("-inf")

    def check(self, now_ms: int) -> dict[str, Any] | None:
        if self.settings is None:
            return None  # Unmanaged is not a broken installation.
        with self._lock:
            now = self.monotonic()
            if now >= self._next:
                self._next = now + CHECK_INTERVAL_S
                self._result = {"state": self._state(), "checked_ms": now_ms}
            return dict(self._result) if self._result is not None else None

    def _own_spelling(self, line: dict[str, Any]) -> bool:
        """Our command with only the interpreter spelled through a linked folder."""
        assert self.script is not None and self.cache is not None  # noqa: S101
        if set(line) != {"type", "command"} or line["type"] != "command":
            return False
        try:
            parts = shlex.split(line["command"])
        except ValueError:
            return False
        if (
            len(parts) != 4
            or parts[1] != str(self.script)
            or parts[2] != "--cache-path"
            or parts[3] != str(self.cache)
        ):
            return False
        recorded = Path(parts[0])
        return (
            line["command"] == managed_command(self.script, self.cache, recorded)
            and bound_interpreter(parts[0], self.interpreter) == recorded
        )

    def _state(self) -> str:
        if self.script is None or self.cache is None:
            return "not_configured"
        assert self.settings is not None  # noqa: S101
        try:
            try:
                script = _read(self.script, private=True)
            except FileNotFoundError:
                return "script_missing"
            if hashlib.sha256(script).hexdigest() != HOOK_SHA256:
                return "script_outdated"
            info = self.interpreter.stat()
            if (
                not (3, 13) <= sys.version_info[:2] < (3, 14)
                or not self.interpreter.is_absolute()
                or info.st_uid not in (0, os.getuid())
                or not stat.S_ISREG(info.st_mode)
                or not (info.st_mode & 0o111)
                or info.st_mode & 0o022
            ):
                return "invalid"
            try:
                raw = _read(self.settings, private=False)
            except FileNotFoundError:
                return "settings_missing"
            body = document(raw)
            if "statusLine" not in body:
                return "statusline_missing"
            line = body["statusLine"]
            if not isinstance(line, dict) or not isinstance(line.get("command"), str):
                return "invalid"
            expected = managed_command(self.script, self.cache, self.interpreter)
            if line != {"type": "command", "command": expected} and not self._own_spelling(line):
                return "conflict"
            return "installed"  # Exact packaged script and command; invocation is not proven.
        except (PermissionError, OSError):
            return "unreadable"
        except (ValueError, TypeError, RecursionError, HookFileError):
            return "invalid"


class HookSource(Protocol):
    @property
    def kind(self) -> str: ...
    @property
    def format(self) -> str: ...
    @property
    def limits_format(self) -> str | None: ...
    @property
    def limits_settings(self) -> Path | None: ...
    @property
    def limits_cache(self) -> Path | None: ...


def cache_state(cache: Path | None, now_ms: int) -> str:
    if cache is None:
        return "missing"
    try:
        with fs.directory(cache.parent, private=True) as fd:
            image = fs.read(fd, cache.name, private=True, bound=64 * 1024)
        if image.raw is None:
            return "missing"
        body = fs.document(image.raw)
        if (
            set(body) != {"captured_ms", "windows"}
            or type(body["captured_ms"]) is not int
            or not 0 <= body["captured_ms"] <= now_ms
            or not isinstance(body["windows"], dict)
            or not body["windows"]
            or set(body["windows"]) - {"five_hour", "seven_day"}
        ):
            return "invalid"
        expired = False
        for row in body["windows"].values():
            if (
                not isinstance(row, dict)
                or set(row) != {"used_percentage", "resets_at"}
                or type(row["used_percentage"]) not in (int, float)
                or not math.isfinite(row["used_percentage"])
                or not 0 <= row["used_percentage"] <= 100
                or type(row["resets_at"]) is not int
                or not 0 <= row["resets_at"] <= 253402300799
            ):
                return "invalid"
            expired |= now_ms >= row["resets_at"] * 1000
        return (
            "expired"
            if expired
            else "stale"
            if now_ms - body["captured_ms"] > LIMIT_STALE_MS
            else "fresh"
        )
    except FileNotFoundError:
        return "missing"
    except (OSError, ValueError, TypeError, OverflowError, RecursionError, fs.HookFileError):
        return "invalid"
