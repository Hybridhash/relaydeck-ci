"""Refuse repositories whose own configuration could run programs during a read."""

from __future__ import annotations

import os
import re
import stat
import threading

from relaydeck.git.runner import STATUS_TIMEOUT_S, GitRunner

MAX_CONFIG_BYTES = 256 * 1024

# Lower-case keys that can run a helper, read other files or redirect the worktree.
_UNSAFE = re.compile(
    r"^(?:filter\..+\.(?:clean|smudge|process|required)"
    r"|diff\..+\.(?:command|textconv|cachetextconv)"
    r"|merge\..+\.driver"
    r"|core\.(?:sshcommand|worktree|attributesfile|excludesfile|alternaterefscommand)"
    r"|credential\..*helper"
    r"|include\.path|includeif\..+"
    r"|uploadpack\.packobjectshook"
    r"|gpg\..*program)$"
)
_BOOLEAN_ONLY = frozenset({"core.fsmonitor"})
_DRIVER = re.compile(rb"(?:^|\s)(?:filter|diff|merge|textconv)\s*=", re.MULTILINE)
_BOOLEANS = frozenset({"true", "false", "yes", "no", "on", "off", "1", "0", ""})


class UnsupportedConfig(RuntimeError):
    """The repository's configuration cannot be read safely."""


class ConfigGuard:
    """Checks one worktree's effective configuration before any status or diff."""

    def __init__(self, runner: GitRunner) -> None:
        self._runner = runner

    def check(
        self,
        worktree: str,
        gitdir: str,
        common_dir: str,
        cancel: threading.Event | None = None,
        *,
        timeout_s: float = STATUS_TIMEOUT_S,
    ) -> None:
        """Raise UnsupportedConfig unless no unsafe key and no object alternates are present."""
        for directory in {gitdir, common_dir}:
            if os.path.lexists(os.path.join(directory, "objects", "info", "alternates")):
                raise UnsupportedConfig("alternates")
            if _assigns_drivers(os.path.join(directory, "info", "attributes")):
                raise UnsupportedConfig("info_attributes")
        result = self._runner.run(
            worktree,
            ("config", "--list", "-z", "--includes"),
            timeout_s=timeout_s,
            max_bytes=MAX_CONFIG_BYTES,
            ok_codes=frozenset({0, 1}),
            cancel=cancel,
        )
        if result.truncated:
            raise UnsupportedConfig("config_too_large")
        for key, value in _entries(result.stdout):
            if unsafe(key, value):
                raise UnsupportedConfig("unsafe_key")


def unsafe(key: str, value: str) -> bool:
    """True for a configuration entry that could execute a helper or widen what is read."""
    name = key.lower()
    if name == "core.attributesfile" and value == "/dev/null":
        return False  # the runner's own pin
    if name in _BOOLEAN_ONLY:
        return value.lower() not in _BOOLEANS
    return _UNSAFE.match(name) is not None


def _entries(raw: bytes) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        key, _, value = record.partition(b"\n")
        entries.append((key.decode("utf-8", "replace"), value.decode("utf-8", "replace")))
    return entries


def _assigns_drivers(path: str) -> bool:
    """Return True when repository attributes assign a driver or cannot be opened as a small
    regular file without following links. An absent file returns False; Git consults this
    file even with empty-tree worktree attributes.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONFIG_BYTES:
            return True
        text = os.read(fd, MAX_CONFIG_BYTES + 1)
    except OSError:
        return True
    finally:
        os.close(fd)
    return len(text) > MAX_CONFIG_BYTES or _DRIVER.search(text) is not None
