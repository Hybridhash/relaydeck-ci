"""Map an agent's host-local folder to the git worktree it belongs to."""

from __future__ import annotations

import os
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from relaydeck.git.runner import STATUS_TIMEOUT_S, GitFailed, GitRunner

CACHE_TTL_S = 5.0
_MAX_CACHE = 256


class LocateError(RuntimeError):
    """A fixed reason: folder_gone, no_repo or unavailable."""


@dataclass(frozen=True, slots=True)
class Worktree:
    """A verified worktree. Paths stay on the Mac; `identity` changes if it is recreated."""

    root: str
    gitdir: str
    common_dir: str
    identity: tuple[int, ...]


class RepositoryLocator:
    """Finds the worktree for a folder, checking ownership and symlinks along the way."""

    def __init__(
        self,
        runner: GitRunner,
        *,
        uid: int | None = None,
        lstat: Callable[[str], os.stat_result] = os.lstat,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runner = runner
        self._uid = os.getuid() if uid is None else uid
        self._lstat = lstat
        self._clock = clock
        self._cache: dict[str, tuple[float, Worktree]] = {}
        self._lock = threading.Lock()

    def locate(self, folder: str, cancel: threading.Event | None = None) -> Worktree:
        """Verified worktree for `folder`; raises LocateError. Call from a worker thread."""
        now = self._clock()
        with self._lock:
            hit = self._cache.get(folder)
        if hit is not None and now - hit[0] < CACHE_TTL_S and self._still(hit[1]):
            return hit[1]
        worktree = self._find(folder, cancel)
        with self._lock:
            if len(self._cache) >= _MAX_CACHE:
                self._cache.clear()
            self._cache[folder] = (now, worktree)
        return worktree

    def forget(self, folder: str) -> None:
        with self._lock:
            self._cache.pop(folder, None)

    def _find(self, folder: str, cancel: threading.Event | None = None) -> Worktree:
        if not os.path.isabs(folder) or "\0" in folder:
            raise LocateError("no_repo")
        real = os.path.realpath(folder)
        self._directory(real, owned=True, missing="folder_gone")
        try:
            out = self._runner.run(
                real,
                ("rev-parse", "--show-toplevel", "--absolute-git-dir", "--git-common-dir"),
                timeout_s=STATUS_TIMEOUT_S,
                max_bytes=3 * 4100,
                cancel=cancel,
            )
        except GitFailed as exc:
            raise LocateError("no_repo") from exc
        lines = out.stdout.decode("utf-8", "surrogateescape").split("\n")
        if out.truncated or len(lines) < 3 or not all(lines[:3]):
            raise LocateError("no_repo")
        top, gitdir, common = lines[:3]
        if not os.path.isabs(common):
            common = os.path.join(real, common)
        root, gitdir, common = (os.path.realpath(p) for p in (top, gitdir, common))
        if os.path.commonpath((root, real)) != root:
            raise LocateError("unavailable")
        return Worktree(
            root,
            gitdir,
            common,
            (
                *self._directory(root, owned=True),
                *self._directory(gitdir, owned=True),
                *self._directory(common, owned=True),
            ),
        )

    def _directory(
        self, path: str, *, owned: bool, missing: str = "unavailable"
    ) -> tuple[int, int]:
        """(device, inode) of a real directory with no symlinked ancestor, owned when asked."""
        parts = path.split(os.sep)
        for depth in range(2, len(parts) + 1):
            current = os.sep.join(parts[:depth]) or os.sep
            try:
                info = self._lstat(current)
            except OSError as exc:
                raise LocateError(missing) from exc
            if stat.S_ISLNK(info.st_mode):
                raise LocateError("unavailable")  # swapped in after realpath
            if not stat.S_ISDIR(info.st_mode):
                raise LocateError(missing)
        if owned and info.st_uid != self._uid:
            raise LocateError("unavailable")
        return (info.st_dev, info.st_ino)

    def _still(self, worktree: Worktree) -> bool:
        try:
            identity = (
                *self._directory(worktree.root, owned=True),
                *self._directory(worktree.gitdir, owned=True),
                *self._directory(worktree.common_dir, owned=True),
            )
        except LocateError:
            return False
        return identity == worktree.identity
