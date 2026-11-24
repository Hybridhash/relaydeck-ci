"""Canonical inputs shared by guarded captures and cheap ref probes."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterable
from dataclasses import dataclass

from relaydeck.git.locator import Worktree
from relaydeck.git.refs import valid_ref
from relaydeck.git.results import ChangesError


@dataclass(frozen=True, slots=True)
class GraphProbe:
    digest: str
    guard: str
    refs: str | None


def guard_stamp(worktree: Worktree, choice: str, *, history: bool = True) -> str:
    identities: list[int] = []
    for path in (worktree.root, worktree.gitdir, worktree.common_dir):
        info = os.lstat(path)
        if not stat.S_ISDIR(info.st_mode):
            raise ChangesError("stale_snapshot")
        identities.extend((info.st_dev, info.st_ino))
    if tuple(identities) != worktree.identity:
        raise ChangesError("stale_snapshot")
    values = [choice.encode(), repr(worktree.identity).encode()]
    for directory in sorted({worktree.gitdir, worktree.common_dir}):
        for name in ("shallow", "info/grafts") if history else ():
            if os.path.lexists(os.path.join(directory, name)):
                raise ChangesError("unsupported_config")
        for name in ("config", "config.worktree", "info/attributes", "objects/info/alternates"):
            value: tuple[int, ...]
            try:
                info = os.lstat(os.path.join(directory, name))
                value = (
                    info.st_dev,
                    info.st_ino,
                    info.st_mode,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                )
            except FileNotFoundError:
                value = ()
            values.append(repr(value).encode())
    return digest_parts(values)


def digest_parts(values: list[bytes]) -> str:
    result = hashlib.sha256()
    for value in values:
        result.update(len(value).to_bytes(8, "big"))
        result.update(value)
    return result.hexdigest()


def input_digest(
    head: str,
    branch: bytes | None,
    base: bytes,
    base_oid: str,
    guard: str,
    locals_: Iterable[tuple[bytes, ...]],
    upstreams: Iterable[tuple[bytes, ...]],
    tags: Iterable[tuple[bytes, ...]],
    defaults: Iterable[tuple[bytes, bytes]],
    flags: tuple[bool, ...],
) -> str:
    values = [
        b"probe-v2",
        head.encode(),
        branch or b"",
        base,
        base_oid.encode(),
        guard.encode(),
        bytes(flags),
    ]
    for label, rows in ((b"locals", locals_), (b"upstreams", upstreams), (b"tags", tags)):
        values.append(label)
        values.extend(b"\0".join(row) for row in sorted(rows))
    values.append(b"defaults")
    values.extend(
        b"\0".join((name, target))
        for name, target in sorted(defaults)
        if valid_ref(name) and name.endswith(b"/HEAD") and valid_ref(target) and name != target
    )
    return digest_parts(values)
