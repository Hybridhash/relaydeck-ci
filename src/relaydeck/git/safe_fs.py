"""Read a file below a worktree root without following any symbolic link on the way."""

from __future__ import annotations

import hashlib
import os
import stat
import time


class Unreadable(OSError):
    """Not a regular file reachable without links, or larger than allowed."""


def open_contained(root: str, path: bytes) -> tuple[int, os.stat_result]:
    """Descriptor and stat of a regular file under `root`; every component is opened no-follow."""
    parts = path.split(b"/")
    if not path or any(part in (b"", b".", b"..") for part in parts):
        raise Unreadable("path")
    fds: list[int] = []
    try:
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fds.append(parent)
        for part in parts[:-1]:
            parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            fds.append(parent)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except OSError as exc:
        raise Unreadable("unreadable") from exc
    finally:
        for opened in reversed(fds):
            os.close(opened)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise Unreadable("not_regular")
    return fd, info


def read_contained(root: str, path: bytes, limit: int) -> bytes:
    """The bytes of a regular file under `root`; raises Unreadable above `limit` bytes."""
    fd, info = open_contained(root, path)
    try:
        if info.st_size > limit:
            raise Unreadable("too_large")
        chunks: list[bytes] = []
        size = 0
        while chunk := os.read(fd, 64 * 1024):
            size += len(chunk)
            if size > limit:
                raise Unreadable("too_large")
            chunks.append(chunk)
        after = os.fstat(fd)
        if (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
            raise Unreadable("changed")
        return b"".join(chunks)
    finally:
        os.close(fd)


def fingerprint_contained(root: str, paths: tuple[bytes, ...], limit: int, deadline: float) -> str:
    """Fingerprint bounded ref files using no-follow descriptors for every component."""
    digest = hashlib.sha256()
    budget = limit

    def visit(parent: int, name: bytes, relative: bytes) -> None:
        nonlocal budget
        if time.monotonic() >= deadline:
            raise Unreadable("deadline")
        budget -= len(relative) + 64
        if budget < 0:
            raise Unreadable("too_large")
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            digest.update(len(relative).to_bytes(8, "big") + relative + b"missing")
            return
        if stat.S_ISDIR(info.st_mode):
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                before = os.fstat(fd)
                if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
                    raise Unreadable("changed")
                names = []
                with os.scandir(fd) as entries:
                    for entry in entries:
                        names.append(os.fsencode(entry.name))
                        if len(names) * 64 > budget:
                            raise Unreadable("too_large")
                for child in sorted(names):
                    visit(fd, child, relative + b"/" + child)
                after = os.fstat(fd)
                if (before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    raise Unreadable("changed")
            finally:
                os.close(fd)
        elif stat.S_ISREG(info.st_mode):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                before = os.fstat(fd)
                if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino) or not stat.S_ISREG(
                    before.st_mode
                ):
                    raise Unreadable("changed")
                if before.st_size > budget:
                    raise Unreadable("too_large")
                digest.update(
                    len(relative).to_bytes(8, "big") + relative + before.st_size.to_bytes(8, "big")
                )
                size = 0
                while chunk := os.read(fd, min(64 * 1024, budget + 1)):
                    if time.monotonic() >= deadline:
                        raise Unreadable("deadline")
                    size += len(chunk)
                    budget -= len(chunk)
                    if budget < 0:
                        raise Unreadable("too_large")
                    digest.update(chunk)
                after = os.fstat(fd)
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ) or size != after.st_size:
                    raise Unreadable("changed")
                digest.update(b"\0")
            finally:
                os.close(fd)
        else:
            raise Unreadable("not_regular")

    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for path in paths:
            # Paths are fixed reader-owned names; nested roots are opened component by component.
            parts = path.split(b"/")
            if any(part in (b"", b".", b"..") for part in parts):
                raise Unreadable("path")
            parent = fd
            opened = []
            try:
                for part in parts[:-1]:
                    parent = os.open(
                        part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
                    )
                    opened.append(parent)
                visit(parent, parts[-1], path)
            except FileNotFoundError:
                digest.update(len(path).to_bytes(8, "big") + path + b"missing")
            finally:
                for item in reversed(opened):
                    os.close(item)
    finally:
        os.close(fd)
    return digest.hexdigest()
