"""Lossless qualified-ref validation and default base selection."""

from __future__ import annotations

import os
from collections.abc import Iterable

# Tried in order when no remote names a default branch (a local-only repository, or several
# remotes and none of them origin).
LOCAL_DEFAULTS = (b"refs/heads/main", b"refs/heads/master", b"refs/heads/trunk")


def valid_ref(name: bytes) -> bool:
    """The fully qualified subset of check-ref-format rules used for reader-owned refs."""
    return (
        name.startswith((b"refs/heads/", b"refs/remotes/", b"refs/tags/"))
        and len(name) <= 1024
        and not any(c <= 32 or c == 127 or c in b"~^:?*[\\" for c in name)
        and not any(part in name for part in (b"..", b"@{", b"//"))
        and not name.endswith((b"/", b"."))
        and all(p and not p.startswith(b".") and not p.endswith(b".lock") for p in name.split(b"/"))
    )


def remote_head(
    rows: Iterable[tuple[bytes, bytes]], *, strict: bool = True
) -> tuple[str | None, bool]:
    heads: dict[bytes, bytes] = {}
    partial = False
    for name, target in rows:
        if strict and not valid_ref(name):
            partial = True
            continue
        if (
            name.endswith(b"/HEAD")
            and target != name
            and (valid_ref(target) if strict else target.startswith(b"refs/remotes/"))
        ):
            heads[name] = target
    selected = heads.get(b"refs/remotes/origin/HEAD")
    if selected is None and len(heads) == 1:
        selected = next(iter(heads.values()))
    return (os.fsdecode(selected) if selected is not None else None), partial


def fallback_default(
    remote_names: Iterable[bytes], local_names: Iterable[bytes], current: bytes | None
) -> str | None:
    """Choose a conventional base when no remote names a default branch. Prefer an unambiguous
    remote trunk, then a suitable local trunk, otherwise require reader choice.
    """
    remotes = [n for n in remote_names if n.startswith(b"refs/remotes/") and n.count(b"/") == 3]
    for trunk in LOCAL_DEFAULTS:
        tail = trunk.removeprefix(b"refs/heads")
        found = [n for n in remotes if n.endswith(tail)]
        if len(found) == 1:
            return os.fsdecode(found[0])
    if current in LOCAL_DEFAULTS:
        return None
    present = set(local_names)
    local = next((n for n in LOCAL_DEFAULTS if n in present), None)
    return os.fsdecode(local) if local is not None else None
