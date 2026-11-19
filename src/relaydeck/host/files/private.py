"""Private local state; reject unsafe existing paths instead of repairing them."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any


def private_dir(path: Path) -> None:
    """The directory owner-only, with any missing parents made 0755 whatever the umask, so a
    parent is never group-writable only because of the caller's umask."""
    missing: list[Path] = []
    probe = path.parent
    while not probe.exists() and probe != probe.parent:
        missing.append(probe)
        probe = probe.parent
    for parent in reversed(missing):
        parent.mkdir(mode=0o755, exist_ok=True)
        parent.chmod(0o755)
    path.mkdir(mode=0o700, exist_ok=True)
    check_private(path, directory=True)


def check_private(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not kind or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError(
            f"{path.name}: expected an owner-only {'directory' if directory else 'file'}"
        )


def read_private(path: Path) -> str:
    check_private(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, encoding="utf-8") as handle:
        if os.fstat(handle.fileno()).st_size > 1024 * 1024:
            raise ValueError(f"{path.name}: file too large")
        return handle.read()


def _write_pinned(path: Path, body: dict[str, Any]) -> None:
    """Publish through a pinned parent descriptor; a swapped-in link cannot redirect it."""
    from relaydeck.host.files.descriptors import (
        PrivateFileError,
        create_private,
        open_private_dir,
        replace_private,
    )

    data = json.dumps(body, separators=(",", ":")).encode()
    try:
        dir_fd = open_private_dir(path.parent)
        try:
            try:
                replace_private(dir_fd, path.name, data)
            except PrivateFileError as exc:
                if exc.code != "missing":
                    raise
                create_private(dir_fd, path.name, data)
        finally:
            os.close(dir_fd)
    except PrivateFileError as exc:
        raise OSError(f"{path.name}: private write failed ({exc.code})") from None


def write_json(
    path: Path, body: dict[str, Any], *, durable: bool = True, pinned: bool = False
) -> None:
    if pinned:
        _write_pinned(path, body)
        return
    if path.exists() or path.is_symlink():
        check_private(path)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".companion-", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(body, handle, separators=(",", ":"))
            handle.flush()
            if durable:
                os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
