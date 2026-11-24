"""Resolve the Branch comparison without widening the chosen base."""

from relaydeck.git.base_store import BaseStore
from relaydeck.git.locator import Worktree
from relaydeck.git.reader import RepositoryReader
from relaydeck.git.results import ChangesError, Scope


def comparison_base(
    bases: BaseStore,
    account: str,
    host: str,
    worktree: Worktree,
    reader: RepositoryReader,
    scope: Scope,
) -> tuple[str | None, str | None, str | None, str | None]:
    """(qualified base ref, base key, merge base, the HEAD it was computed from); all None
    for Working.

    The base key is the base OID plus the saved choice's revision, so saving a base (even one
    pointing at the same commit) gives every Branch snapshot a new revision.
    """
    if scope == "working":
        return None, None, None, None
    saved = bases.get(account, host, worktree)
    oid = reader.resolve(saved[0]) if saved is not None else None
    # A saved base that was deleted falls back to the default rather than leaving no base.
    ref = saved[0] if oid is not None and saved is not None else reader.remote_default()
    if ref is None:
        raise ChangesError("no_base")
    oid = oid or reader.resolve(ref)
    if oid is None:
        raise ChangesError("no_base")
    head = reader.status().head
    if head is None:
        raise ChangesError("no_base")
    splits = reader.merge_bases(oid, head)
    if len(splits) != 1:
        raise ChangesError("no_base")
    return ref, f"{oid}:{saved[1] if saved is not None else 'default'}", splits[0], head


def base_options(reader: RepositoryReader) -> tuple[tuple[str, str, str], ...]:
    default, current = reader.remote_default(), reader.head_ref()
    out = []
    for ref, oid in reader.base_candidates():
        if ref == current:
            continue
        kind = (
            "remote_default"
            if ref == default
            else "remote"
            if ref.startswith("refs/remotes/")
            else "local"
        )
        out.append((ref, kind, oid))
    return tuple(sorted(out, key=lambda item: (item[1] != "remote_default", item[0])))
