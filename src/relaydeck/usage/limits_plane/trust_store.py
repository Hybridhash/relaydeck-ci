"""Read supported tool-owned JSON or hashed-workspace trust records without modifying them.
Unsupported shapes and read failures report unknown trust.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

MAX_BYTES = 1024 * 1024
SUPPORTED_VERSION = 1
_PERMISSIONS_FILE = "permissions.yaml"
_TRUSTED_KEY = "workspaceTrusted"


def _read_bounded(path: Path) -> bytes | None:
    """A regular file's bytes, read from one descriptor; None for anything else, a link, a
    special file (opened without blocking), a read problem or more than MAX_BYTES."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        data = os.read(fd, MAX_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    return data if len(data) <= MAX_BYTES else None


def trusted_folders(store: str) -> tuple[Path, ...] | None:
    """Existing real folders the file marks trusted, shortest path first, or None when the file
    is unreadable or not the version-1 shape."""
    try:
        raw = _read_bounded(Path(os.path.expanduser(store)))
        if raw is None:
            return None
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema_version") != SUPPORTED_VERSION:
        return None
    projects = data.get("projects")
    if not isinstance(projects, dict):
        return None
    out: set[Path] = set()
    for folder, entry in projects.items():
        if (
            isinstance(folder, str)
            and folder.startswith("/")
            and "\x00" not in folder
            and isinstance(entry, dict)
            and entry.get("decision") == "trusted"
        ):
            try:
                real = Path(folder).resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if real.is_dir():
                out.add(real)
    return tuple(sorted(out, key=lambda item: (len(str(item)), str(item))))


def is_trusted(folder: str, trusted: tuple[Path, ...]) -> bool:
    try:
        return Path(folder).resolve(strict=True) in trusted
    except (OSError, RuntimeError):
        return False


def _hashed_permissions_trusts(root: Path, folder: Path) -> bool:
    try:
        real = folder.resolve(strict=True)
        digest = hashlib.sha256(str(real).encode()).hexdigest()[:16]
        path = root / digest / _PERMISSIONS_FILE
        raw = _read_bounded(path)
        if raw is None:
            return False
        lines = raw.decode("utf-8").splitlines()
    except (OSError, RuntimeError, UnicodeDecodeError):
        return False
    for line in lines:
        key, mark, value = line.partition(":")
        if mark and key.strip() == _TRUSTED_KEY:
            return value.strip().lower() == "true"
    return False


def folder_is_trusted(store: str, folder: str | Path) -> bool:
    """Whether the tool's own trust store already trusts this one folder.

    A directory store means the hashed-permissions layout (its folders cannot be
    enumerated, so `trusted_folders` says nothing about it); anything else goes
    through the JSON reader."""
    try:
        path = Path(os.path.expanduser(store))
        target = Path(folder).expanduser()
    except (OSError, RuntimeError, ValueError):
        return False
    if path.is_dir():
        return _hashed_permissions_trusts(path, target)
    trusted = trusted_folders(store)
    if not trusted:
        return False
    return is_trusted(str(target), trusted)


STATE_DIR_TOKEN = "{state_dir}"  # noqa: S105 - template placeholder


def probe_folder(template: str | None, state_dir: Path) -> Path | None:
    """The recipe's own empty folder, `{state_dir}/name` made concrete."""
    if not template:
        return None
    return Path(template.replace(STATE_DIR_TOKEN, str(state_dir), 1))


def ensure_probe_folder(folder: Path) -> bool:
    """Create the folder (0700) when missing. Nothing is ever written into it."""
    try:
        if folder.is_symlink():
            return False
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        return folder.is_dir()
    except OSError:
        return False
