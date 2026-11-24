"""Use bounded metadata checks to prove reuse of mutable captures without reading worktree
content. Fall back to guarded Git reads when traversal or ref storage is uncertain.
"""

from __future__ import annotations

import hashlib
import os
import stat
import time

from relaydeck.git.graph_inputs import guard_stamp
from relaydeck.git.graph_probe import ref_stamp
from relaydeck.git.locator import Worktree

MAX_NODES = 20_000
PROBE_SECONDS = 0.15
POLL_RETRY_S = 120.0
POLL_RETRY_MAX_S = 1800.0


def _file_stamp(root: str, path: str) -> str | None:
    """Metadata only; no links, special files or unbounded path walks."""
    parts = path.split("/")
    if len(parts) > 128 or any(part in ("", ".", "..") for part in parts):
        return None
    values: list[tuple[int, ...] | str] = []
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for index, part in enumerate(parts):
                parent = signature(os.fstat(fd))
                try:
                    info = os.stat(part, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    values.append("missing:" + part)
                    if signature(os.fstat(fd)) != parent:
                        return None
                    break
                values.append(signature(info))
                if index == len(parts) - 1:
                    if not stat.S_ISREG(info.st_mode) or info.st_size > 256 * 1024:
                        return None
                else:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    if signature(os.fstat(child)) != signature(info):
                        os.close(child)
                        return None
                    os.close(fd)
                    fd = child
        finally:
            os.close(fd)
        return hashlib.sha256(repr(values).encode()).hexdigest()
    except OSError:
        return None


def ignore_stamp(worktree: Worktree, default_file: str | None) -> str | None:
    if default_file is None or not os.path.isabs(default_file):
        return None
    values = [_file_stamp("/", default_file.lstrip("/"))]
    values.extend(
        _file_stamp(directory, name)
        for directory in sorted({worktree.gitdir, worktree.common_dir})
        for name in ("info/exclude", "info/sparse-checkout")
    )
    if any(value is None for value in values):
        return None
    return hashlib.sha256(repr(values).encode()).hexdigest()


def config_stamp(worktree: Worktree) -> str | None:
    """Only contained regular configuration can authorize a guard cache hit."""
    values = [guard_stamp(worktree, "", history=False)]
    try:
        for directory in sorted({worktree.gitdir, worktree.common_dir}):
            for path in ("config", "config.worktree", "info/attributes", "objects/info/alternates"):
                fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    parts = path.split("/")
                    for index, part in enumerate(parts):
                        try:
                            info = os.stat(part, dir_fd=fd, follow_symlinks=False)
                        except FileNotFoundError:
                            values.append("missing")
                            break
                        values.append(repr(signature(info)))
                        if index == len(parts) - 1:
                            if not stat.S_ISREG(info.st_mode) or info.st_size > 256 * 1024:
                                return None
                        else:
                            child = os.open(
                                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                            )
                            os.close(fd)
                            fd = child
                finally:
                    os.close(fd)
        return hashlib.sha256(repr(values).encode()).hexdigest()
    except OSError:
        return None


def metadata_stamp(worktree: Worktree, *, default_ignore_file: str | None = None) -> str | None:
    deadline = time.monotonic() + PROBE_SECONDS
    digest = hashlib.sha256()
    nodes = 0
    indexes: dict[str, tuple[tuple[str, tuple[int, ...]], ...]] = {}

    def names_of(fd: int) -> list[str]:
        names: list[str] = []
        with os.scandir(fd) as entries:
            for entry in entries:
                if len(names) + nodes >= MAX_NODES or time.monotonic() >= deadline:
                    raise OSError("probe_budget")
                names.append(entry.name)
        return sorted(names)

    def index_state(directory: str) -> tuple[tuple[str, tuple[int, ...]], ...]:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            names = names_of(fd)
            return tuple(
                (name, signature(os.stat(name, dir_fd=fd, follow_symlinks=False)))
                for name in sorted(names)
                if name == "index" or name.startswith("sharedindex.")
            )
        finally:
            os.close(fd)

    def visit(parent: int, name: str, *, root: bool = False) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_NODES or time.monotonic() >= deadline:
            raise OSError("probe_budget")
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        digest.update(os.fsencode(name) + b"\0")
        digest.update(repr(signature(before)).encode() + b"\0")
        if stat.S_ISDIR(before.st_mode):
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                if signature(os.fstat(fd)) != signature(before):
                    raise OSError("probe_changed")
                children = names_of(fd)
                for child in sorted(children):
                    if root and child == ".git":
                        continue
                    visit(fd, child)
                if signature(os.fstat(fd)) != signature(before):
                    raise OSError("probe_changed")
            finally:
                os.close(fd)
        if signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != signature(before):
            raise OSError("probe_changed")

    try:
        guard = config_stamp(worktree)
        ignores = ignore_stamp(worktree, default_ignore_file)
        if guard is None or ignores is None:
            return None
        refs = ref_stamp(worktree, deadline)
        if refs is None:
            return None
        digest.update(guard.encode() + ignores.encode() + refs.encode())
        # Index metadata suffices for unchanged reuse, including split-index changes.
        for directory in sorted({worktree.gitdir, worktree.common_dir}):
            indexes[directory] = index_state(directory)
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                for name in names_of(fd):
                    if name == "index" or name.startswith("sharedindex."):
                        visit(fd, name)
            finally:
                os.close(fd)
        parent = os.open(os.path.dirname(worktree.root), os.O_RDONLY | os.O_DIRECTORY)
        try:
            visit(parent, os.path.basename(worktree.root), root=True)
        finally:
            os.close(parent)
        if config_stamp(worktree) != guard:
            return None
        if ignore_stamp(worktree, default_ignore_file) != ignores:
            return None
        if ref_stamp(worktree, deadline) != refs or any(
            index_state(directory) != before for directory, before in indexes.items()
        ):
            return None
        return digest.hexdigest()
    except (OSError, RecursionError):
        return None


def signature(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )
