"""Typed read-only git queries for one verified worktree."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field

from relaydeck.git.locator import Worktree
from relaydeck.git.refs import LOCAL_DEFAULTS, fallback_default, remote_head
from relaydeck.git.results import ChangesError
from relaydeck.git.runner import DIFF_TIMEOUT_S, STATUS_TIMEOUT_S, GitResult, GitRunner
from relaydeck.git.safe_fs import Unreadable, read_contained

MAX_STATUS_BYTES = 8 * 1024 * 1024
MAX_PATCH_BYTES = 4 * 1024 * 1024
MAX_SIDE_BYTES = 2 * 1024 * 1024
RENAME_LIMIT = 1000
MAX_BASES = 200
EMPTY_TREE = {
    "sha1": "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
    "sha256": "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321",
}
_OID = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_DIFF = ("diff", "--no-ext-diff", "--no-textconv", "--no-color", f"-l{RENAME_LIMIT}")


class ReadError(RuntimeError):
    """A fixed reason: truncated, malformed or unsupported."""


@dataclass(frozen=True, slots=True)
class Entry:
    """One porcelain v2 status record; paths are raw bytes."""

    path: bytes
    old_path: bytes | None
    kind: str  # "1" ordinary, "2" rename/copy, "u" unmerged, "?" untracked
    staged: bool
    unstaged: bool
    submodule: bool
    mode_head: str
    mode_worktree: str


@dataclass(frozen=True, slots=True)
class Status:
    head: str | None  # None while HEAD is unborn
    branch: str | None  # None when detached or unborn without a name
    detached: bool
    entries: tuple[Entry, ...]
    raw: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class NumStat:
    path: bytes
    old_path: bytes | None
    additions: int | None  # None for binary
    deletions: int | None


@dataclass(frozen=True, slots=True)
class RawChange:
    """One `diff --raw` record: status letter, modes and blob OIDs (zero OID: working tree)."""

    status: str
    old_mode: str
    new_mode: str
    old_oid: str
    new_oid: str
    path: bytes
    old_path: bytes | None


@dataclass(frozen=True, slots=True)
class Commit:
    oid: str
    parents: tuple[str, ...]
    time_s: int
    subject: str


class RepositoryReader:
    """Runs fixed queries in one worktree; inputs are OIDs and manifest paths, never client text."""

    def __init__(
        self,
        runner: GitRunner,
        worktree: Worktree,
        cancel: threading.Event | None = None,
        *,
        deadline: float | None = None,
        strict_refs: bool = False,
    ) -> None:
        self._runner = runner
        self._wt = worktree
        self._cancel = cancel
        self._empty: str | None = None
        self.deadline = deadline if deadline is not None else float("inf")
        self._strict_refs = strict_refs
        self._default_partial = False
        self._default_heads: tuple[tuple[bytes, bytes], ...] = ()

    def status(self) -> Status:
        out = self._run(
            ("status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all", "--no-renames"),
            STATUS_TIMEOUT_S,
            MAX_STATUS_BYTES,
        )
        return parse_status(out)

    def configuration_digest(self) -> str:
        """Guarded fallback when config metadata cannot prove an immutable input."""
        result = self._runner.run(
            self._wt.root,
            ("config", "--list", "-z", "--includes"),
            timeout_s=self._remaining(STATUS_TIMEOUT_S),
            max_bytes=256 * 1024,
            ok_codes=frozenset({0, 1}),
            cancel=self._cancel,
        )
        if result.truncated:
            raise ReadError("truncated")
        return hashlib.sha256(result.stdout).hexdigest()

    def empty_tree(self) -> str:
        """The empty tree's OID in this repository's object format (also the attribute source)."""
        if self._empty is None:
            result = self._runner.run(
                self._wt.root,
                ("rev-parse", "--show-object-format"),
                timeout_s=self._remaining(STATUS_TIMEOUT_S),
                max_bytes=64,
                cancel=self._cancel,
            )
            if result.truncated:
                raise ReadError("truncated")
            tree = EMPTY_TREE.get(result.stdout.strip().decode("ascii", "replace"))
            if tree is None:
                raise ReadError("unsupported")
            self._empty = tree
        return self._empty

    def raw(
        self, old: str, new: str | None, paths: tuple[bytes, ...] = ()
    ) -> tuple[RawChange, ...]:
        """Change kinds, modes and blob OIDs from `old` to `new` (or the working tree)."""
        args: tuple[str, ...] = (
            "diff",
            "--raw",
            "-z",
            "--no-abbrev",
            "-M",
            f"-l{RENAME_LIMIT}",
            _oid(old),
        )
        if new is not None:
            args = (*args, _oid(new))
        if paths:
            args = (*args, "--", *_paths(paths))
        return parse_raw(self._run(args, DIFF_TIMEOUT_S, MAX_STATUS_BYTES))

    def blob_size(self, oid: str) -> int:
        out = self._run(("cat-file", "-s", _oid(oid)), STATUS_TIMEOUT_S, 64)
        return int(out.strip() or b"0")

    def numstat(self, old: str, new: str | None) -> tuple[NumStat, ...]:
        """Diffstat from `old` to `new`, or to the working tree when `new` is None."""
        args: tuple[str, ...] = ("diff", "--numstat", "-z", "-M", f"-l{RENAME_LIMIT}", _oid(old))
        if new is not None:
            args = (*args, _oid(new))
        return parse_numstat(self._run(args, DIFF_TIMEOUT_S, MAX_STATUS_BYTES))

    def resolve(self, ref: str) -> str | None:
        """Commit OID of a fully qualified ref, or None."""
        if not ref.startswith("refs/") or ".." in ref:
            raise ValueError("ref_must_be_qualified")
        out = self._call(
            ("rev-parse", "--verify", "-q", "--end-of-options", f"{ref}^{{commit}}"),
            timeout_s=STATUS_TIMEOUT_S,
            max_bytes=128,
            ok_codes=frozenset({0, 1}),
        ).stdout.strip()
        oid = out.decode("ascii", "replace")
        return oid if _OID.match(oid) else None

    def remote_default(self) -> str | None:
        """The default base: what origin/HEAD points at, else the only remote HEAD, else the
        conventional trunk `fallback_default` picks; None when there is none."""
        raw = self._run(
            ("for-each-ref", "--format=%(refname)%00%(symref)", "refs/remotes/"),
            STATUS_TIMEOUT_S,
            2 * 1024 * 1024 if self._strict_refs else 1024 * 1024,
        )
        rows = []
        for line in raw.split(b"\n"):
            if line:
                fields = line.split(b"\0")
                if len(fields) != 2:
                    raise ReadError("malformed")
                rows.append((fields[0], fields[1]))
        self._default_heads = tuple(rows)
        result, self._default_partial = remote_head(rows, strict=self._strict_refs)
        if result is not None:
            return result
        names = self._run(
            ("for-each-ref", "--format=%(refname)", *map(os.fsdecode, LOCAL_DEFAULTS)),
            STATUS_TIMEOUT_S,
            4096,
        ).split(b"\n")
        current = self.head_ref()
        remote_names = (name for name, _ in rows)
        return fallback_default(remote_names, names, os.fsencode(current) if current else None)

    def head_ref(self) -> str | None:
        """Fully qualified branch HEAD is on, or None when detached."""
        out = self._call(
            ("rev-parse", "--symbolic-full-name", "--verify", "-q", "--end-of-options", "HEAD"),
            timeout_s=STATUS_TIMEOUT_S,
            max_bytes=4200,
            ok_codes=frozenset({0, 1}),
        ).stdout.strip()
        name = out.decode("utf-8", "replace")
        return name if name.startswith("refs/heads/") else None

    def merge_bases(self, base: str, head: str) -> tuple[str, ...]:
        out = self._call(
            ("merge-base", "--all", _oid(base), _oid(head)),
            timeout_s=STATUS_TIMEOUT_S,
            max_bytes=64 * 1024,
            ok_codes=frozenset({0, 1}),
        ).stdout
        return tuple(line for line in out.decode("ascii", "replace").split() if _OID.match(line))

    def count(self, base: str, head: str) -> int:
        out = self._run(
            ("rev-list", "--count", f"{_oid(base)}..{_oid(head)}"), STATUS_TIMEOUT_S, 64
        )
        return int(out.strip() or b"0")

    def commits(self, base: str, head: str, *, skip: int, limit: int) -> tuple[Commit, ...]:
        out = self._run(
            (
                "log",
                "--no-show-signature",
                "--no-decorate",
                "--format=%H%x00%P%x00%ct%x00%s%x1e",
                f"--skip={max(0, skip)}",
                f"-n{max(1, min(limit, 100))}",
                f"{_oid(base)}..{_oid(head)}",
            ),
            STATUS_TIMEOUT_S,
            1024 * 1024,
        )
        return parse_commits(out)

    def base_candidates(self) -> tuple[tuple[str, str], ...]:
        """(qualified ref, OID) for local and remote-tracking branches, at most MAX_BASES."""
        out = self._run(
            (
                "for-each-ref",
                f"--count={MAX_BASES + 1}",
                "--format=%(refname)%00%(objectname)",
                "refs/heads",
                "refs/remotes",
            ),
            STATUS_TIMEOUT_S,
            1024 * 1024,
        )
        refs: list[tuple[str, str]] = []
        for line in out.decode("utf-8", "replace").split("\n"):
            name, _, oid = line.partition("\0")
            if name and _OID.match(oid) and not name.endswith("/HEAD"):
                refs.append((name, oid))
        return tuple(refs[:MAX_BASES])

    def patch(self, old: str, new: str | None, paths: tuple[bytes, ...]) -> bytes:
        """One file's unified diff from `old` to `new` (or the working tree) for manifest paths."""
        args: tuple[str, ...] = (*_DIFF, "-M", "-U3", _oid(old))
        if new is not None:
            args = (*args, _oid(new))
        return self._run((*args, "--", *_paths(paths)), DIFF_TIMEOUT_S, MAX_PATCH_BYTES)

    def sides(
        self,
        old: str,
        new: str | None,
        paths: tuple[bytes, ...],
        *,
        changes: tuple[RawChange, ...] | None = None,
    ) -> tuple[bytes, bytes]:
        """Bounded complete sides for a single manifest file; no filters or path expressions."""
        raw = self.raw(old, new, paths) if changes is None else changes
        matches = [change for change in raw if change.path == paths[-1]]
        if not matches:
            return b"", b""
        if len(matches) != 1:
            raise ReadError("unsupported")
        change = matches[0]

        def blob(oid: str) -> bytes:
            if not oid.strip("0"):
                return b""
            result = self._call(("cat-file", "blob", _oid(oid)), DIFF_TIMEOUT_S, MAX_SIDE_BYTES + 1)
            if result.truncated or len(result.stdout) > MAX_SIDE_BYTES:
                raise ReadError("too_large")
            return result.stdout

        left = blob(change.old_oid)
        if new is not None:
            return left, blob(change.new_oid)
        if change.status == "D":
            return left, b""
        try:
            return left, read_contained(self._wt.root, change.path, MAX_SIDE_BYTES)
        except Unreadable as exc:
            raise ReadError("too_large" if str(exc) == "too_large" else "unsupported") from exc

    def untracked_patch(self, path: bytes) -> bytes:
        """Diff of an untracked regular file against nothing.

        The bytes are read here without following links and diffed in a private folder outside the
        repository, so no repository setting applies and a swapped parent cannot redirect the read.
        """
        _paths((path,))
        try:
            data = read_contained(self._wt.root, path, MAX_SIDE_BYTES)
        except Unreadable as exc:
            raise ReadError("too_large" if str(exc) == "too_large" else "unsupported") from exc
        with tempfile.TemporaryDirectory(prefix="relaydeck-diff-") as folder:
            for name, content in (("old", b""), ("new", data)):
                with open(os.path.join(folder, name), "wb") as handle:
                    handle.write(content)
            result = self._runner.run(
                folder,
                (*_DIFF, "--no-index", "--", "old", "new"),
                timeout_s=self._remaining(DIFF_TIMEOUT_S),
                max_bytes=MAX_PATCH_BYTES,
                ok_codes=frozenset({0, 1}),
                cancel=self._cancel,
            )
        if result.truncated:
            raise ReadError("truncated")
        return result.stdout

    def _remaining(self, timeout: float) -> float:
        remaining = min(timeout, self.deadline - time.monotonic())
        if remaining <= 0:
            raise ChangesError("busy", 1)
        return remaining

    def _run(self, args: tuple[str, ...], timeout_s: float, max_bytes: int) -> bytes:
        result = self._call(args, timeout_s, max_bytes)
        if result.truncated:
            raise ReadError("truncated")
        return result.stdout

    def _call(
        self,
        args: tuple[str, ...],
        timeout_s: float,
        max_bytes: int,
        ok_codes: frozenset[int] = frozenset({0}),
    ) -> GitResult:
        """Every repository read takes attributes from the empty tree: no filter or driver runs."""
        empty = self.empty_tree()
        result = self._runner.run(
            self._wt.root,
            args,
            timeout_s=self._remaining(timeout_s),
            max_bytes=max_bytes,
            ok_codes=ok_codes,
            cancel=self._cancel,
            attr_source=empty,
        )
        if result.truncated and self._strict_refs:
            raise ReadError("truncated")
        return result


def parse_status(raw: bytes) -> Status:
    head: str | None = None
    branch: str | None = None
    detached = False
    entries: list[Entry] = []
    records = raw.split(b"\0")
    i = 0
    while i < len(records):
        record = records[i]
        i += 1
        if not record:
            continue
        if record.startswith(b"# branch.oid "):
            oid = record[13:].decode("ascii", "replace")
            head = oid if _OID.match(oid) else None
        elif record.startswith(b"# branch.head "):
            name = record[14:].decode("utf-8", "replace")
            detached = name == "(detached)"
            branch = None if detached else name
        elif record[:2] in (b"1 ", b"u "):
            fields = record.split(b" ", 10 if record[:1] == b"u" else 8)
            entries.append(_entry(fields, fields[-1], None))
        elif record[:2] == b"2 ":
            fields = record.split(b" ", 9)
            old = records[i] if i < len(records) else b""
            i += 1
            entries.append(_entry(fields, fields[-1], old))
        elif record[:2] == b"? ":
            entries.append(Entry(record[2:], None, "?", False, True, False, "000000", "100644"))
        elif not record.startswith((b"# ", b"! ")):
            raise ReadError("malformed")
    return Status(head, branch, detached, tuple(entries), raw)


def _entry(fields: list[bytes], path: bytes, old: bytes | None) -> Entry:
    if len(fields) < 6:
        raise ReadError("malformed")
    xy = fields[1].decode("ascii", "replace")
    kind = fields[0].decode("ascii")
    return Entry(
        path=path,
        old_path=old,
        kind=kind,
        staged=kind == "u" or xy[:1] not in (".", ""),
        unstaged=kind == "u" or xy[1:2] not in (".", ""),
        submodule=fields[2][:1] == b"S",
        mode_head=fields[3].decode("ascii", "replace"),
        mode_worktree=fields[5].decode("ascii", "replace") if kind != "u" else fields[6].decode(),
    )


def parse_numstat(raw: bytes) -> tuple[NumStat, ...]:
    out: list[NumStat] = []
    records = raw.split(b"\0")
    i = 0
    while i < len(records):
        record = records[i]
        i += 1
        if not record:
            continue
        added, deleted, path = (record.split(b"\t", 2) + [b"", b""])[:3]
        old: bytes | None = None
        if not path:  # rename: "a\td\t" then old and new paths as separate records
            old, path = records[i], records[i + 1] if i + 1 < len(records) else b""
            i += 2
        out.append(NumStat(path, old, _count(added), _count(deleted)))
    return tuple(out)


def parse_raw(raw: bytes) -> tuple[RawChange, ...]:
    out: list[RawChange] = []
    records = raw.split(b"\0")
    i = 0
    while i < len(records):
        head = records[i]
        i += 1
        if not head:
            continue
        fields = head.lstrip(b":").split(b" ")
        if len(fields) != 5 or i >= len(records):
            raise ReadError("malformed")
        old_mode, new_mode, old_oid, new_oid, status = (
            f.decode("ascii", "replace") for f in fields
        )
        old_path: bytes | None = None
        if status[:1] in ("R", "C"):
            old_path, i = records[i], i + 1
        out.append(
            RawChange(status[:1], old_mode, new_mode, old_oid, new_oid, records[i], old_path)
        )
        i += 1
    return tuple(out)


def commit_subject(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")[:500]
    return "".join(c if c.isprintable() else f"\\u{ord(c):04x}" for c in text)


def parse_commits(raw: bytes) -> tuple[Commit, ...]:
    commits: list[Commit] = []
    for record in raw.split(b"\x1e"):
        record = record.strip(b"\n")
        if not record:
            continue
        parts = record.split(b"\0", 3)
        if len(parts) != 4 or not _OID.match(parts[0].decode("ascii", "replace")):
            raise ReadError("malformed")
        commits.append(
            Commit(
                oid=parts[0].decode("ascii"),
                parents=tuple(parts[1].decode("ascii").split()),
                time_s=int(parts[2] or b"0"),
                subject=commit_subject(parts[3]),
            )
        )
    return tuple(commits)


def _count(value: bytes) -> int | None:
    return int(value) if value.isdigit() else None


def _oid(value: str) -> str:
    if not _OID.match(value):
        raise ValueError("oid_required")
    return value


def _paths(paths: tuple[bytes, ...]) -> list[str]:
    names: list[str] = []
    for path in paths:
        if not path or path.startswith(b"/") or b"\0" in path or b".." in path.split(b"/"):
            raise ValueError("manifest_path_required")
        names.append(os.fsdecode(path))
    return names
