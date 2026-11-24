"""Snapshot revision for one worktree: changes whenever what Changes would show changes."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass

from relaydeck.git.locator import Worktree
from relaydeck.git.polling import config_stamp
from relaydeck.git.reader import RepositoryReader, Status

OPTIONS_VERSION = b"changes-v1"
MAX_ENTRIES = 50_000
MAX_HASHED_BYTES = 64 * 1024 * 1024
MAX_HASHED_FILE = 1024 * 1024
_ATTEMPTS = 2


@dataclass(frozen=True, slots=True)
class Probe:
    revision: str
    status: Status
    partial: bool  # limits hit: some files are identified by metadata only
    stable: bool  # False when the worktree kept changing while it was read
    policy: str | None = None


@dataclass(frozen=True, slots=True)
class _Identity:
    stamp: tuple[int, ...] | None
    digest: bytes = b""
    hashed: int = 0
    skipped: bool = False  # a regular file left unhashed by a size limit


class ChangeProbe:
    """Computes the revision; the caller runs at most one probe per worktree at a time."""

    def __init__(self, worktree: Worktree, *, policy_version: str = "") -> None:
        self._wt = worktree
        self._policy_version = policy_version

    def probe(self, reader: RepositoryReader, base_oid: str | None) -> Probe:
        result: Probe | None = None
        for _ in range(_ATTEMPTS):
            before = self._marker()
            policy, proven = self._policy(reader)
            status = reader.status()
            revision, partial = self._digest(status, base_oid, policy)
            stable = self._marker() == before and self._policy(reader)[0] == policy
            result = Probe(revision, status, partial, stable, proven)
            if stable:
                break
        assert result is not None  # noqa: S101
        return result

    def _policy(self, reader: RepositoryReader) -> tuple[str, str | None]:
        proven = config_stamp(self._wt)
        if proven is not None:
            return proven, proven
        # Config symlinks cannot authorize reuse. A fresh guarded config read
        # still identifies the capture; attributes are rechecked by the guard.
        inputs = (
            reader.configuration_digest(),
            tuple(
                _meta(directory, "info/attributes")
                for directory in sorted({self._wt.gitdir, self._wt.common_dir})
            ),
        )
        return hashlib.sha256(repr(inputs).encode()).hexdigest(), None

    def _marker(self) -> tuple[object, ...]:
        """HEAD and index identity; a change between reads means the probe raced an edit."""
        return (_meta(self._wt.gitdir, "HEAD"), _meta(self._wt.gitdir, "index"))

    def _digest(self, status: Status, base_oid: str | None, policy: str) -> tuple[str, bool]:
        h = hashlib.sha256()
        for part in (
            OPTIONS_VERSION,
            policy.encode(),
            self._policy_version.encode(),
            repr(self._wt.identity).encode(),
            (base_oid or "").encode(),
            (status.head or "").encode(),
            repr(_meta(self._wt.gitdir, "index")).encode(),
            status.raw,
        ):
            h.update(len(part).to_bytes(8, "big") + part)
        partial = len(status.entries) > MAX_ENTRIES
        budget = MAX_HASHED_BYTES
        try:
            root = os.open(self._wt.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            return h.hexdigest(), True
        try:
            for entry in status.entries[:MAX_ENTRIES]:
                if entry.submodule or not (entry.unstaged or entry.kind == "?"):
                    continue
                found = _file_identity(root, entry.path, budget)
                budget -= found.hashed
                partial = partial or found.skipped
                h.update(entry.path + b"\0" + repr(found.stamp).encode() + found.digest)
        finally:
            os.close(root)
        return h.hexdigest(), partial


def _meta(directory: str, name: str) -> tuple[int, ...] | None:
    try:
        info = os.lstat(os.path.join(directory, name))
    except OSError:
        return None
    return (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _file_identity(root: int, path: bytes, budget: int) -> _Identity:
    """Stamp and content hash of a file under `root`, opening every component without following."""
    parts = path.split(b"/")
    fds: list[int] = []
    try:
        parent = root
        for part in parts[:-1]:
            if part in (b"", b".", b".."):
                return _Identity(None)
            parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            fds.append(parent)
        info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        stamp = (info.st_mode, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if not stat.S_ISREG(info.st_mode):
            return _Identity(stamp)
        if info.st_size > min(MAX_HASHED_FILE, budget):
            return _Identity(stamp, skipped=True)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            data = os.read(fd, MAX_HASHED_FILE + 1)
        finally:
            os.close(fd)
        return _Identity(stamp, hashlib.sha256(data).digest(), len(data))
    except OSError:
        return _Identity(None)
    finally:
        for fd in reversed(fds):
            os.close(fd)
