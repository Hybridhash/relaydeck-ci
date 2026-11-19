"""One descriptor-owning writer with a fixed private log retention budget."""

from __future__ import annotations

import fcntl
import os
import stat
from pathlib import Path

from relaydeck.host.files.descriptors import open_private_dir

MAX_BYTES = 5 * 1024 * 1024
ARCHIVES = 3
_FLAGS = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def _check(fd: int) -> os.stat_result:
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or info.st_nlink != 1
    ):
        raise OSError("Unsafe log file")
    return info


class RotatingOutput:
    def __init__(self, directory: Path, name: str, *, limit: int = MAX_BYTES) -> None:
        if name not in ("companion.out.log", "companion.err.log") or limit < 1:
            raise ValueError("Invalid log policy")
        self.name, self.limit = name, limit
        self.directory = open_private_dir(directory)
        self.fd = self.lock = -1
        try:
            self.lock = os.open(
                name + ".lock", os.O_RDWR | os.O_CREAT | _FLAGS, 0o600, dir_fd=self.directory
            )
            if _check(self.lock).st_size:
                raise OSError("Unsafe log lock")
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Validate the complete fixed inventory before changing any file.
            for suffix in ("", ".1", ".2", ".3", ".trim"):
                self._validate(name + suffix)
            try:
                os.unlink(name + ".trim", dir_fd=self.directory)
            except FileNotFoundError:
                pass
            for suffix in ("", ".1", ".2", ".3"):
                self._bound_existing(name + suffix)
            self.fd = self._open()
            self.size = _check(self.fd).st_size
        except BaseException:
            self.close()
            raise

    def _validate(self, name: str) -> None:
        try:
            fd = os.open(name, os.O_RDONLY | _FLAGS, dir_fd=self.directory)
        except FileNotFoundError:
            return
        try:
            _check(fd)
        finally:
            os.close(fd)

    def _bound_existing(self, name: str) -> None:
        try:
            fd = os.open(name, os.O_RDWR | _FLAGS, dir_fd=self.directory)
        except FileNotFoundError:
            return
        try:
            info = _check(fd)
            if info.st_size > self.limit:
                tail = os.pread(fd, self.limit, info.st_size - self.limit)
                self._publish_tail(name, tail, info)
        finally:
            os.close(fd)

    def _publish_tail(self, name: str, tail: bytes, original: os.stat_result) -> None:
        temporary = self.name + ".trim"
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FLAGS, 0o600, dir_fd=self.directory
        )
        try:
            self._write_all(fd, memoryview(tail))
            os.fsync(fd)
            current = os.stat(name, dir_fd=self.directory, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
                raise OSError("Log replaced while trimming")
            os.replace(temporary, name, src_dir_fd=self.directory, dst_dir_fd=self.directory)
        finally:
            os.close(fd)
            try:
                os.unlink(temporary, dir_fd=self.directory)
            except FileNotFoundError:
                pass

    def _open(self) -> int:
        fd = os.open(
            self.name, os.O_WRONLY | os.O_CREAT | os.O_APPEND | _FLAGS, 0o600, dir_fd=self.directory
        )
        try:
            _check(fd)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _identity(self) -> None:
        opened = _check(self.fd)
        named = os.stat(self.name, dir_fd=self.directory, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise OSError("Log replaced while open")

    def _rotate(self) -> None:
        self._identity()
        for index in range(1, ARCHIVES + 1):
            self._validate(f"{self.name}.{index}")
        os.close(self.fd)
        self.fd = -1
        for index in range(ARCHIVES, 0, -1):
            old = self.name if index == 1 else f"{self.name}.{index - 1}"
            try:
                os.replace(
                    old,
                    f"{self.name}.{index}",
                    src_dir_fd=self.directory,
                    dst_dir_fd=self.directory,
                )
            except FileNotFoundError:
                continue
        self.fd = self._open()
        self.size = 0

    @staticmethod
    def _write_all(fd: int, data: memoryview) -> None:
        while data:
            written = os.write(fd, data)
            if written <= 0:
                raise OSError("Log write incomplete")
            data = data[written:]

    def write(self, data: bytes) -> None:
        self._identity()
        remaining = memoryview(data)
        while remaining:
            if self.size >= self.limit:
                self._rotate()
            chunk = remaining[: self.limit - self.size]
            self._write_all(self.fd, chunk)
            self.size += len(chunk)
            remaining = remaining[len(chunk) :]

    def close(self) -> None:
        for name in ("fd", "lock", "directory"):
            fd = getattr(self, name, -1)
            if fd >= 0:
                os.close(fd)
                setattr(self, name, -1)
