"""Two fixed queries detect changes to the admitted graph inputs."""

from __future__ import annotations

import os
import threading
import time

from relaydeck.git.graph_inputs import GraphProbe as GraphProbe
from relaydeck.git.graph_inputs import digest_parts, input_digest
from relaydeck.git.graph_inputs import guard_stamp as guard_stamp
from relaydeck.git.graph_reader import (
    MAX_LOCAL_REFS,
    MAX_REF_BYTES,
    MAX_TAG_REFS,
    MAX_UPSTREAM_REFS,
    _oid,
    _refs,
)
from relaydeck.git.locator import Worktree
from relaydeck.git.reader import ReadError
from relaydeck.git.refs import fallback_default, remote_head, valid_ref
from relaydeck.git.results import ChangesError
from relaydeck.git.runner import STATUS_TIMEOUT_S, GitRunner
from relaydeck.git.safe_fs import Unreadable, fingerprint_contained, read_contained


def probe(
    runner: GitRunner,
    worktree: Worktree,
    deadline: float,
    base: str | None,
    choice: str,
    cancel: threading.Event | None,
) -> GraphProbe:
    def query(args: tuple[str, ...], cap: int) -> bytes:
        remaining = min(STATUS_TIMEOUT_S, deadline - time.monotonic())
        if remaining <= 0:
            raise ChangesError("busy", 1)
        result = runner.run(
            worktree.root,
            args,
            timeout_s=remaining,
            max_bytes=cap,
            cancel=cancel,
            ok_codes=frozenset({0, 1}) if args[0] == "rev-parse" else frozenset({0}),
        )
        if result.truncated:
            raise ReadError("truncated")
        return result.stdout

    guard = guard_stamp(worktree, choice)
    before_refs = ref_stamp(worktree, deadline)
    raw = query(
        (
            "for-each-ref",
            "--sort=refname",
            "--format=%(refname)%00%(objectname)%00%(upstream)%00%(objecttype)%00%(symref)",
            "refs/heads/",
            "refs/remotes/",
            "refs/tags/",
        ),
        MAX_REF_BYTES,
    )
    raw_rows = [tuple(line.split(b"\0")) for line in raw.split(b"\n") if line]
    if any(len(row) != 5 for row in raw_rows):
        raise ReadError("malformed")

    def listed(prefix: bytes, limit: int) -> tuple[list[tuple[bytes, ...]], bool]:
        selected = [row for row in raw_rows if row[0].startswith(prefix)][: limit + 1]
        return _refs(b"\n".join(b"\0".join(row) for row in selected), 5)

    locals_, local_skipped = listed(b"refs/heads/", MAX_LOCAL_REFS)
    tags, tag_skipped = listed(b"refs/tags/", MAX_TAG_REFS)
    head = query(("rev-parse", "--verify", "-q", "--end-of-options", "HEAD^{commit}"), 128).strip()
    if not head:
        raise ChangesError("no_base")
    _oid(head)
    symbolic = read_contained(worktree.gitdir, b"HEAD", 4096).strip()
    branch = symbolic[5:] if symbolic.startswith(b"ref: refs/heads/") else None
    names = {row[2] for row in locals_[:MAX_LOCAL_REFS] if row[2]}
    if any(not valid_ref(name) or name.startswith(b"refs/tags/") for name in names):
        raise ReadError("malformed")
    admitted = set(sorted(names)[:MAX_UPSTREAM_REFS])
    upstreams = []
    upstream_skipped = False
    for row in raw_rows:
        name, oid, _, kind, symref = row
        if not valid_ref(name):
            upstream_skipped |= any(
                name == prefix or name.startswith(prefix + b"/") for prefix in admitted
            )
        if name in admitted and not symref and kind == b"commit" and not name.endswith(b"/HEAD"):
            upstreams.append((name, oid, kind, symref))
    defaults = [(r[0], r[4]) for r in raw_rows if r[0].startswith(b"refs/remotes/")]
    default, default_skipped = remote_head(defaults)
    if default is None:
        default = fallback_default(
            (r[0] for r in raw_rows if r[0].startswith(b"refs/remotes/")),
            (r[0] for r in raw_rows if r[0].startswith(b"refs/heads/")),
            branch,
        )
    # A saved base that no longer exists falls back to the default, as the full read does. Saved
    # bases are only refs/heads and refs/remotes branches (BaseStore.put), which are all listed
    # here, so "listed" agrees with the full read's "resolves to a commit".
    rows_by_name = {r[0]: r for r in raw_rows}
    selected_base = os.fsencode(base or "")
    if selected_base not in rows_by_name:
        selected_base = os.fsencode(default or "")
    base_row = rows_by_name.get(selected_base)
    if base_row is None:
        raise ChangesError("no_base")
    base_oid = base_row[1]
    if base_row[3] != b"commit":
        if not valid_ref(selected_base):
            raise ReadError("malformed")
        base_oid = query(
            (
                "rev-parse",
                "--verify",
                "-q",
                "--end-of-options",
                os.fsdecode(selected_base) + "^{commit}",
            ),
            128,
        ).strip()
    digest = input_digest(
        _oid(head),
        branch,
        selected_base,
        _oid(base_oid),
        guard,
        [row[:3] for row in locals_[:MAX_LOCAL_REFS]],
        upstreams,
        [(row[0], row[1], row[3]) for row in tags[:MAX_TAG_REFS]],
        defaults if base is None else (),
        (
            local_skipped,
            len(locals_) > MAX_LOCAL_REFS,
            upstream_skipped,
            len(names) > MAX_UPSTREAM_REFS,
            tag_skipped,
            len(tags) > MAX_TAG_REFS,
            default_skipped if base is None else False,
        ),
    )
    final_refs = ref_stamp(worktree, deadline)
    if guard_stamp(worktree, choice) != guard or before_refs != final_refs:
        raise ChangesError("busy", 1)
    return GraphProbe(digest, guard, final_refs)


def ref_stamp(worktree: Worktree, deadline: float) -> str | None:
    values = []
    try:
        for directory in sorted({worktree.gitdir, worktree.common_dir}):
            if os.path.lexists(os.path.join(directory, "reftable")):
                return None
            for name in (b"config", b"config.worktree"):
                if os.path.lexists(os.path.join(directory, os.fsdecode(name))):
                    if b"reftable" in read_contained(directory, name, 256 * 1024).lower():
                        return None
            values.append(
                fingerprint_contained(
                    directory,
                    (b"HEAD", b"packed-refs", b"refs/heads", b"refs/remotes", b"refs/tags"),
                    MAX_REF_BYTES,
                    deadline,
                ).encode()
            )
    except (OSError, Unreadable):
        if time.monotonic() >= deadline:
            raise ChangesError("busy", 1) from None
        return None
    return digest_parts(values)
