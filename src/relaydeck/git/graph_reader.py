"""Bounded history queries with lossless ref identities and one request deadline."""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from dataclasses import dataclass

from relaydeck.contracts.git import MAX_HISTORY as MAX_HISTORY
from relaydeck.git.graph_inputs import GraphProbe, guard_stamp, input_digest
from relaydeck.git.locator import Worktree
from relaydeck.git.reader import Commit, ReadError, RepositoryReader, commit_subject
from relaydeck.git.refs import valid_ref as valid_ref
from relaydeck.git.results import ChangesError, GraphLabel
from relaydeck.git.runner import STATUS_TIMEOUT_S, GitFailed, GitRunner

MAX_LOCAL_REFS = 512
MAX_UPSTREAM_REFS = 512
MAX_TAG_REFS = 4096
MAX_REF_BYTES = 2 * 1024 * 1024
MAX_AHEAD = 100_000
PAGE_SIZE = 100
_OID = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_FORMAT = "--format=tformat:%H%x00%P%x00%ct%x00%s"
_LOG = (
    "log",
    "--no-show-signature",
    "--no-decorate",
    "--no-notes",
    "--encoding=UTF-8",
    "-z",
    _FORMAT,
)


@dataclass(frozen=True, slots=True)
class RefCapture:
    revision: str
    head: str
    branch: bytes | None
    base_name: bytes
    base_oid: str
    split_oid: str
    roots: tuple[str, ...]
    labels: tuple[GraphLabel, ...]
    partial: bool
    probe: GraphProbe


def short_ref(name: bytes) -> bytes:
    for prefix in (b"refs/heads/", b"refs/remotes/", b"refs/tags/"):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def _oid(raw: bytes) -> str:
    if _OID.fullmatch(raw) is None:
        raise ReadError("malformed")
    return raw.decode("ascii")


def parse_graph_commits(raw: bytes) -> tuple[Commit, ...]:
    fields = raw.split(b"\0")
    if fields.pop() != b"" or len(fields) % 4:
        raise ReadError("malformed")
    out = []
    for start in range(0, len(fields), 4):
        oid, parents, stamp, subject = fields[start : start + 4]
        parent_oids = tuple(_oid(p) for p in parents.split())
        if len(parent_oids) > 64 or not re.fullmatch(rb"-?[0-9]{1,16}", stamp):
            raise ReadError("malformed")
        seconds = int(stamp)
        if not -(2**63) <= seconds * 1000 < 2**63:
            raise ReadError("malformed")
        out.append(Commit(_oid(oid), parent_oids, seconds, commit_subject(subject)))
    return tuple(out)


def _refs(raw: bytes, fields: int) -> tuple[list[tuple[bytes, ...]], bool]:
    rows = []
    skipped = False
    for line in raw.split(b"\n"):
        if not line:
            continue
        values = tuple(line.split(b"\0"))
        if len(values) != fields:
            raise ReadError("malformed")
        if not valid_ref(values[0]):
            skipped = True
            continue
        rows.append(values)
    return rows, skipped


class GraphReader(RepositoryReader):
    def __init__(
        self,
        runner: GitRunner,
        worktree: Worktree,
        cancel: threading.Event | None = None,
        *,
        deadline: float,
    ) -> None:
        super().__init__(runner, worktree, cancel, deadline=deadline, strict_refs=True)

    def _query(self, *args: str, cap: int = MAX_REF_BYTES) -> bytes:
        return self._run(tuple(args), STATUS_TIMEOUT_S, cap)

    def check_history(self) -> None:
        if self._query("rev-parse", "--is-shallow-repository", cap=16).strip() != b"false":
            raise ChangesError("unsupported_config")
        for directory in {self._wt.gitdir, self._wt.common_dir}:
            if os.path.lexists(os.path.join(directory, "info", "grafts")):
                raise ChangesError("unsupported_config")

    def capture(self, base: str, choice: str) -> RefCapture:
        if not valid_ref(os.fsencode(base)):
            raise ChangesError("no_base")
        head_result = self._call(
            ("rev-parse", "--verify", "-q", "--end-of-options", "HEAD^{commit}"),
            STATUS_TIMEOUT_S,
            128,
            frozenset({0, 1}),
        )
        if not head_result.stdout.strip():
            raise ChangesError("no_base")
        head = _oid(head_result.stdout.strip())
        symbolic = self._query(
            "rev-parse",
            "--symbolic-full-name",
            "--verify",
            "-q",
            "--end-of-options",
            "HEAD",
            cap=2048,
        ).rstrip(b"\n")
        branch = symbolic if symbolic.startswith(b"refs/heads/") else None
        if branch is not None and not valid_ref(branch):
            raise ReadError("malformed")
        base_oid = self.resolve(base)
        if base_oid is None:
            raise ChangesError("no_base")
        splits = self.merge_bases(base_oid, head)
        if len(splits) != 1:
            raise ChangesError("no_base")
        local_raw = self._query(
            "for-each-ref",
            "--sort=refname",
            f"--count={MAX_LOCAL_REFS + 1}",
            "--format=%(refname)%00%(objectname)%00%(upstream)",
            "refs/heads/",
        )
        locals_, skipped = _refs(local_raw, 3)
        local_skipped = skipped
        partial = self._default_partial or skipped or len(locals_) > MAX_LOCAL_REFS
        roots = {head, base_oid}
        labels = [
            GraphLabel(head, short_ref(branch) if branch else b"HEAD", "head"),
            GraphLabel(base_oid, short_ref(os.fsencode(base)), "base"),
            GraphLabel(splits[0], b"split point", "split"),
        ]
        upstreams = set()
        for name, oid, upstream in locals_[:MAX_LOCAL_REFS]:
            resolved = _oid(oid)
            roots.add(resolved)
            if name not in (branch, os.fsencode(base)):
                labels.append(GraphLabel(resolved, short_ref(name), "local"))
            if upstream:
                if not valid_ref(upstream) or upstream.startswith(b"refs/tags/"):
                    raise ReadError("malformed")
                upstreams.add(upstream)
        admitted = sorted(upstreams)[:MAX_UPSTREAM_REFS]
        partial |= len(upstreams) > MAX_UPSTREAM_REFS
        captured = [local_raw]
        probe_upstreams = []
        upstream_skipped = False
        for start in range(0, len(admitted), 64):
            batch = admitted[start : start + 64]
            raw = self._query(
                "for-each-ref",
                "--sort=refname",
                "--format=%(refname)%00%(objectname)%00%(objecttype)%00%(symref)",
                *(os.fsdecode(name) for name in batch),
            )
            rows, skipped = _refs(raw, 4)
            upstream_skipped |= skipped
            partial |= skipped
            for name, oid, kind, symref in rows:
                if name not in batch or symref or name.endswith(b"/HEAD") or kind != b"commit":
                    continue
                resolved = _oid(oid)
                captured.append(name + b"\0" + oid)
                probe_upstreams.append((name, oid, kind, symref))
                roots.add(resolved)
                if name != os.fsencode(base):
                    labels.append(GraphLabel(resolved, short_ref(name), "upstream"))
        tags_raw = self._query(
            "for-each-ref",
            "--sort=refname",
            f"--count={MAX_TAG_REFS + 1}",
            "--format=%(refname)%00%(objectname)%00%(objecttype)%00%(*objectname)%00%(*objecttype)",
            "refs/tags/",
        )
        tags, skipped = _refs(tags_raw, 5)
        tag_skipped = skipped
        partial |= skipped or len(tags) > MAX_TAG_REFS
        peeled = []
        for name, oid, kind, target, target_kind in tags[:MAX_TAG_REFS]:
            tag_oid: str | None = None
            if kind == b"commit":
                tag_oid = _oid(oid)
            elif target_kind == b"commit":
                tag_oid = _oid(target)
            elif target_kind == b"tag":
                tag_oid = self.resolve(os.fsdecode(name))
            if tag_oid:
                labels.append(GraphLabel(tag_oid, short_ref(name), "tag"))
                peeled.append(name + b"\0" + tag_oid.encode())
        fingerprint = hashlib.sha256()
        for item in (
            b"graph-v1",
            self.empty_tree().encode(),
            repr(self._wt.identity).encode(),
            choice.encode(),
            os.fsencode(base),
            base_oid.encode(),
            head.encode(),
            symbolic,
            *captured,
            tags_raw,
            *peeled,
        ):
            fingerprint.update(len(item).to_bytes(8, "big"))
            fingerprint.update(item)
        guard = guard_stamp(self._wt, choice)
        digest = input_digest(
            head,
            branch,
            os.fsencode(base),
            base_oid,
            guard,
            locals_[:MAX_LOCAL_REFS],
            probe_upstreams,
            [row[:3] for row in tags[:MAX_TAG_REFS]],
            self._default_heads if choice == "default" else (),
            (
                local_skipped,
                len(locals_) > MAX_LOCAL_REFS,
                upstream_skipped,
                len(upstreams) > MAX_UPSTREAM_REFS,
                tag_skipped,
                len(tags) > MAX_TAG_REFS,
                self._default_partial if choice == "default" else False,
            ),
        )
        return RefCapture(
            fingerprint.hexdigest(),
            head,
            branch,
            short_ref(os.fsencode(base)),
            base_oid,
            splits[0],
            tuple(sorted(roots)),
            tuple(labels),
            partial,
            GraphProbe(digest, guard, None),
        )

    def membership(self, head: str, base: str) -> frozenset[str]:
        raw = self._query(
            "rev-list",
            f"--max-count={MAX_AHEAD + 1}",
            head,
            f"^{base}",
            "--",
            cap=8 * 1024 * 1024,
        )
        values = frozenset(_oid(line) for line in raw.splitlines())
        if len(values) > MAX_AHEAD:
            raise ReadError("truncated")
        return values

    def behind(self, head: str, base: str) -> int | None:
        if self.deadline - time.monotonic() < 0.5:
            return None
        try:
            raw = self._query(
                "rev-list", "--left-right", "--count", f"{head}...{base}", "--", cap=64
            )
        except GitFailed:
            return None
        fields = raw.split()
        if len(fields) != 2 or not all(v.isdigit() for v in fields):
            raise ReadError("malformed")
        return int(fields[1])

    def records(self, roots: tuple[str, ...], offset: int) -> tuple[Commit, ...]:
        raw = self._query(
            *_LOG,
            "--date-order",
            f"--skip={offset}",
            f"-n{PAGE_SIZE + 1}",
            *roots,
            "--",
            cap=1024 * 1024,
        )
        return parse_graph_commits(raw)

    def one(self, oid: str) -> Commit:
        rows = parse_graph_commits(self._query(*_LOG, "--no-walk=unsorted", "-n1", oid, "--"))
        if len(rows) != 1 or rows[0].oid != oid:
            raise ReadError("malformed")
        return rows[0]
