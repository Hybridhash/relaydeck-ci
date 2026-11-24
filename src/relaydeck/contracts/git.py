"""Immutable shapes for read-only changes, with strict wire decoding that rejects unknown fields
and invalid values.
"""

from __future__ import annotations

import dataclasses
import re
import types
import typing
from dataclasses import dataclass
from typing import Any, Literal, Self

WorktreeState = Literal[
    "ok",
    "calculating",
    "no_repo",
    "folder_gone",
    "unavailable",
    "unsupported_config",
    "git_unavailable",
]
FileStatus = Literal["A", "M", "D", "R", "U", "conflict", "binary", "submodule", "mode", "symlink"]
RenderState = Literal[
    "ok", "load_required", "too_large", "withheld", "binary", "invalid_encoding", "unsupported"
]
LineKind = Literal["context", "add", "delete", "no_newline"]
BaseKind = Literal["remote_default", "remote", "local"]


_HINTS: dict[type, dict[str, Any]] = {}


def _hints(cls: type) -> dict[str, Any]:
    if cls not in _HINTS:
        _HINTS[cls] = typing.get_type_hints(cls)
    return _HINTS[cls]


def _fail(where: str, expected: str, value: Any) -> typing.NoReturn:
    raise ValueError(f"{where or 'value'}: expected {expected}, got {type(value).__name__}")


def _encode(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out: dict[str, Any] = {}
        for field in dataclasses.fields(value):
            item = getattr(value, field.name)
            if item is not None:
                out[field.name] = _encode(item)
        return out
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    return value


def _decode_tuple(args: tuple[Any, ...], value: Any, where: str) -> tuple[Any, ...]:
    if not isinstance(value, list):
        _fail(where, "a list", value)
    if len(args) == 2 and args[1] is Ellipsis:
        return tuple(_decode(args[0], item, f"{where}[{i}]") for i, item in enumerate(value))
    if len(value) != len(args):
        raise ValueError(f"{where}: expected {len(args)} items, got {len(value)}")
    return tuple(
        _decode(arg, item, f"{where}[{i}]")
        for i, (arg, item) in enumerate(zip(args, value, strict=True))
    )


def _decode(tp: Any, value: Any, where: str) -> Any:
    origin = typing.get_origin(tp)
    if origin is Literal:
        if value not in typing.get_args(tp):
            raise ValueError(f"{where}: not one of {typing.get_args(tp)!r}")
        return value
    if origin is types.UnionType or origin is typing.Union:
        if value is None and type(None) in typing.get_args(tp):
            return None
        inner = [arg for arg in typing.get_args(tp) if arg is not type(None)]
        return _decode(inner[0], value, where)
    if origin is tuple:
        return _decode_tuple(typing.get_args(tp), value, where)
    if tp is bool:
        if not isinstance(value, bool):
            _fail(where, "a bool", value)
        return value
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            _fail(where, "an int", value)
        return value
    if tp is str:
        if not isinstance(value, str):
            _fail(where, "a string", value)
        return value
    if isinstance(tp, type) and issubclass(tp, Shape):
        return tp._from(value, where)
    raise TypeError(f"{where}: unsupported type {tp!r}")


class Shape:
    """Mixin giving a dataclass the strict wire codec."""

    __slots__ = ()

    def to_dict(self) -> dict[str, Any]:
        return _encode(self)  # type: ignore[no-any-return]

    @classmethod
    def from_dict(cls, doc: Any) -> Self:
        return cls._from(doc, "")

    @classmethod
    def _from(cls, doc: Any, where: str) -> Self:
        if not isinstance(doc, dict):
            _fail(where, "an object", doc)
        fields = {field.name: field for field in dataclasses.fields(cls)}  # type: ignore[arg-type]
        unknown = sorted(set(doc) - set(fields))
        if unknown:
            raise ValueError(f"{where or cls.__name__}: unknown key {unknown[0]!r}")
        hints = _hints(cls)
        values: dict[str, Any] = {}
        for name, field in fields.items():
            here = f"{where}.{name}" if where else name
            if name in doc:
                values[name] = _decode(hints[name], doc[name], here)
            elif (
                field.default is dataclasses.MISSING
                and field.default_factory is dataclasses.MISSING
            ):
                raise ValueError(f"{here}: missing")
        return cls(**values)


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkingCounts(Shape):
    files: int
    staged: int
    unstaged: int
    untracked: int
    conflicts: int
    additions: int | None = None
    deletions: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class BranchCounts(Shape):
    files: int | None = None
    additions: int | None = None
    deletions: int | None = None
    commits: int | None = None
    revision: str | None = None  # the Branch snapshot's revision; it moves with base and HEAD


@dataclass(frozen=True, slots=True, kw_only=True)
class WorktreeSummary(Shape):
    agent_id: str
    worktree_id: str
    state: WorktreeState
    branch: str | None = None
    detached: bool
    short_oid: str | None = None
    base_name: str | None = None
    working: WorkingCounts
    branch_scope: BranchCounts
    shared: bool
    revision: str
    as_of_ms: int
    partial: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class FileEntry(Shape):
    file_id: str
    path: str
    old_path: str | None = None
    status: FileStatus
    staged: bool
    unstaged: bool
    additions: int | None = None
    deletions: int | None = None
    generated: bool
    withheld: bool
    rename_limited: bool
    renderable: bool
    reason: str | None = None


ChangesScope = Literal["working", "branch", "commit"]


@dataclass(frozen=True, slots=True, kw_only=True)
class ChangesPage(Shape):
    scope: ChangesScope
    snapshot: str
    head: str | None
    base: str | None
    branch: str | None
    detached: bool
    short_oid: str | None
    additions: int | None
    deletions: int | None
    base_name: str | None
    commits: int | None
    partial: bool
    as_of_ms: int
    total_files: int
    files: tuple[FileEntry, ...]
    next: str | None

    def to_dict(self) -> dict[str, Any]:
        """File-list replies retain explicit null keys across comparison scopes."""
        doc = Shape.to_dict(self)
        for name in (
            "head",
            "base",
            "branch",
            "short_oid",
            "additions",
            "deletions",
            "base_name",
            "commits",
            "next",
        ):
            doc.setdefault(name, None)
        return doc


@dataclass(frozen=True, slots=True, kw_only=True)
class DiffLine(Shape):
    """One row. `marks` are `(start, end)` code-point ranges of changed words within `text`."""

    kind: LineKind
    old: int | None = None
    new: int | None = None
    text: str
    marks: tuple[tuple[int, int], ...] = ()
    masked: bool = False

    def __post_init__(self) -> None:
        edge = 0
        for start, end in self.marks:
            if not edge <= start < end <= len(self.text):
                raise ValueError("marks must be ordered, disjoint ranges inside the text")
            edge = end


@dataclass(frozen=True, slots=True, kw_only=True)
class Hunk(Shape):
    """A hunk or, when a long hunk is paged, one fragment of it (same header and counts)."""

    header: str
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    fragment: int = 0
    lines: tuple[DiffLine, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class FileDiff(Shape):
    file_id: str
    snapshot: str
    old_mode: str | None = None
    new_mode: str | None = None
    render: RenderState
    hunks: tuple[Hunk, ...]
    next: str | None = None
    truncated: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class Commit(Shape):
    oid: str
    parents: tuple[str, ...]
    subject: str
    time_ms: int


@dataclass(frozen=True, slots=True, kw_only=True)
class BaseCandidate(Shape):
    base_id: str
    name: str
    kind: BaseKind
    oid: str


GraphMode = Literal["all", "branch"]
MAX_HISTORY = 10_000
GraphRefKind = Literal["head", "base", "local", "upstream", "tag", "split"]
GraphLimit = Literal["ref_limit", "history_limit"]
_FULL_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def _graph_oid(value: str) -> None:
    if _FULL_OID.fullmatch(value) is None:
        raise ValueError("invalid graph object")


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphRef(Shape):
    name: str
    kind: GraphRefKind

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > 8192 or not self.name.isprintable():
            raise ValueError("invalid graph label")


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphCommit(Shape):
    oid: str
    parents: tuple[str, ...]
    subject: str
    time_ms: int
    ahead: bool
    refs: tuple[GraphRef, ...] = ()
    commit_id: str | None = None

    def __post_init__(self) -> None:
        _graph_oid(self.oid)
        if len(self.parents) > 64 or len(self.refs) > 5124 or len(self.subject) > 4000:
            raise ValueError("graph record limit")
        for parent in self.parents:
            _graph_oid(parent)
        if not -(2**63) <= self.time_ms < 2**63:
            raise ValueError("invalid graph time")
        if self.commit_id is not None and not 0 < len(self.commit_id) <= 8192:
            raise ValueError("invalid commit identity")


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphPage(Shape):
    snapshot: str
    mode: GraphMode
    head: str
    base_name: str
    base_oid: str
    split_oid: str
    ahead: int
    commits: tuple[GraphCommit, ...]
    as_of_ms: int
    branch: str | None = None
    behind: int | None = None
    next: str | None = None
    partial: bool = False
    limit_reason: GraphLimit | None = None

    def __post_init__(self) -> None:
        for oid in (self.head, self.base_oid, self.split_oid):
            _graph_oid(oid)
        if not self.snapshot or len(self.snapshot) > 256 or len(self.commits) > 100:
            raise ValueError("invalid graph page")
        if not 0 <= self.ahead < 2**63 or self.behind is not None and not 0 <= self.behind < 2**63:
            raise ValueError("invalid graph count")
        if (
            not self.base_name
            or len(self.base_name) > 8192
            or self.branch is not None
            and len(self.branch) > 8192
            or self.next is not None
            and len(self.next) > 8192
        ):
            raise ValueError("graph field limit")
        if self.limit_reason is not None and not self.partial:
            raise ValueError("graph limit requires partial")
