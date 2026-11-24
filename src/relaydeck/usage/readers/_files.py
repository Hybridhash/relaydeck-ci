"""Enumerate configured format layouts through bounded no-follow reads and stage complete file
units. Commit checkpoints only with their events and bounded accounting context.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from relaydeck.contracts.canonical import canonical_json
from relaydeck.usage.contracts import (
    MAX_CURSOR_BYTES,
    MAX_CURSOR_STATE_BYTES,
    MAX_CURSOR_STATE_ENTRIES,
    MAX_DIR_ENTRIES,
    MAX_EVENTS_PER_BATCH,
    MAX_EXAMINED_ENTRIES,
    MAX_FILE_BYTES,
    MAX_FILE_CONTEXT_BYTES,
    MAX_KEY_CHARS,
    MAX_LINE_BYTES,
    MAX_MODEL_CHARS,
    MAX_TRACKED_FILES,
    Basis,
    Batch,
    Budget,
    Counters,
    Coverage,
    Cursor,
    FileCursor,
    LimitSnapshot,
    UsageEvent,
    encoded_cursor_bytes,
    encoded_state_bytes,
)

HEAD_BYTES = 4096
CHUNK_BYTES = 64 * 1024
MAX_DEPTH = 12
ENUM_SHARE = 0.4  # fraction of the deadline the enumeration may use; reads keep the rest
DISCARD_PREFIX = "x:"
GAP_PREFIX = "g:"
PENDING_PREFIX = "p:"  # a file read partly because of the budget: known unread work
OVERFLOW_KEY = "o:"
ENUM_GAP_KEY = "e:"
SCHEMA_KEY = "v:"
LEGACY_POSITION_KEY = "d:"
REASON_KEY = "k"  # per-file gap reasons: a bounded list drawn from REASONS, beside "g"
# Multiple gap reasons may coexist; an unrecorded cause remains unknown.
REASON_SKIP = "skip"  # a malformed or refused record was skipped
REASON_LEGACY = "legacy"  # cumulative-only usage: counted as history the ledger cannot derive
REASON_OVERSIZE = "oversize"  # a line or file over the bound
REASON_UNREADABLE = "unreadable"  # could not open, stat or read the file
REASON_UNKNOWN = "unknown"  # a gap flag written before reasons existed
REASONS = frozenset(
    {REASON_SKIP, REASON_LEGACY, REASON_OVERSIZE, REASON_UNREADABLE, REASON_UNKNOWN}
)
# Per-file entries of the earlier layout (now in FileCursor.context): removed when found.
PRUNED_PREFIXES = (DISCARD_PREFIX, GAP_PREFIX, PENDING_PREFIX, "t:", "f:")


class LineHandler:
    """Receive complete lines: line=False stops before committing that line, and end=True
    records a file gap. The driver sets reached_end before end and commits events/context
    only when the whole file unit fits.
    """

    skipped: int = 0
    reached_end: bool = False
    # Set by `end`: the file's prefix is not verified, so the gap is of unknown origin.
    unknown_gap: bool = False
    collector: EventCollector

    def begin(self, key: str, rel: str, restarted: bool) -> None:
        return None

    def line(self, key: str, rel: str, offset: int, data: bytes) -> bool:
        return True

    def end(self, key: str, rel: str) -> bool:
        return False

    def reader_context(self, key: str) -> dict[str, object] | None:
        return None


@dataclass
class PassState:
    budget: Budget
    started: float = field(default_factory=lambda: time.monotonic())
    bytes_left: int = 0
    files_left: int = 0
    examined: int = 0
    skipped: int = 0
    reads: int = 0  # units that read bytes this pass
    partial: bool = False
    stopped: bool = False

    def __post_init__(self) -> None:
        self.bytes_left = self.budget.max_bytes
        self.files_left = self.budget.max_files

    def out_of_time(self) -> bool:
        return (time.monotonic() - self.started) * 1000 >= self.budget.deadline_ms

    def enum_out_of_time(self) -> bool:
        return (time.monotonic() - self.started) * 1000 >= self.budget.deadline_ms * ENUM_SHARE

    def skip(self, count: int = 1) -> None:
        self.skipped += count


def file_key(rel: str) -> str:
    """Opaque cursor key: a digest of the relative name, so no path is stored."""
    return hashlib.sha256(rel.encode("utf-8", "surrogatepass")).hexdigest()[:32]


def canonical_root(root: Path) -> Path | None:
    try:
        real = Path(os.path.realpath(root))
        return real if real.is_dir() else None
    except OSError:
        return None


def open_root(root: Path) -> int | None:
    real = canonical_root(root)
    if real is None:
        return None
    try:
        return os.open(real, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return None


def _open_dir(parent: int, name: str) -> int:
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


def open_confined(root_fd: int, rel: str) -> tuple[int, os.stat_result]:
    """Open a regular file below the root with no-follow opens and return (fd, fstat).
    Raises OSError for anything else; callers count it, never describe it."""
    parts = rel.split("/")
    current = root_fd
    opened: list[int] = []
    try:
        for name in parts[:-1]:
            current = _open_dir(current, name)
            opened.append(current)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=current)
    finally:
        for descriptor in opened:
            os.close(descriptor)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError("not a regular file")
    except OSError:
        os.close(fd)
        raise
    return fd, st


@dataclass(frozen=True)
class Layout:
    """Where a reader looks, relative to its configured root. `descend` decides whether a child
    directory (its path parts) is listed; `accept` decides whether a file path is a unit."""

    descend: Callable[[tuple[str, ...]], bool]
    accept: Callable[[tuple[str, ...]], bool]


@dataclass(frozen=True)
class Listed:
    rel: str
    st: os.stat_result


def enumerate_layout(root_fd: int, layout: Layout, state: PassState) -> tuple[list[Listed], bool]:
    """Every file the layout accepts, and whether the enumeration was complete. Entries are
    counted while iterating, so the caps and the deadline hold before anything is sorted."""
    found: list[Listed] = []
    complete = True

    def walk(dir_fd: int, parts: tuple[str, ...], depth: int) -> None:
        nonlocal complete
        if depth > MAX_DEPTH:
            complete = False
            return
        try:
            with os.scandir(dir_fd) as scan:
                for count, entry in enumerate(scan, 1):
                    if count > MAX_DIR_ENTRIES:
                        complete = False
                        return
                    if state.examined >= MAX_EXAMINED_ENTRIES or state.enum_out_of_time():
                        complete = False
                        return
                    state.examined += 1
                    child = (*parts, entry.name)
                    if entry.is_symlink():
                        state.skip()
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if not layout.descend(child):
                            continue
                        try:
                            sub = _open_dir(dir_fd, entry.name)
                        except OSError:
                            complete = False
                            continue
                        try:
                            walk(sub, child, depth + 1)
                        finally:
                            os.close(sub)
                        continue
                    if not entry.is_file(follow_symlinks=False) or not layout.accept(child):
                        continue
                    try:
                        st = os.stat(entry.name, dir_fd=dir_fd, follow_symlinks=False)
                    except OSError:
                        complete = False
                        continue
                    found.append(Listed("/".join(child), st))
        except OSError:
            complete = False

    walk(root_fd, (), 0)
    return found, complete


def _head(fd: int, size: int) -> bytes:
    return os.pread(fd, min(size, HEAD_BYTES), 0)


def head_digest_fd(fd: int, size: int) -> str:
    """SHA-256 of the first min(size, HEAD_BYTES) bytes of an open file."""
    return hashlib.sha256(_head(fd, size)).hexdigest()


def _unchanged(prev: FileCursor, st: os.stat_result) -> bool:
    return (
        prev.dev == st.st_dev
        and prev.ino == st.st_ino
        and prev.size == st.st_size
        and prev.mtime_ns == st.st_mtime_ns
    )


def _restart_needed(prev: FileCursor, fd: int, st: os.stat_result) -> bool:
    if prev.dev != st.st_dev or prev.ino != st.st_ino:
        return True  # rotation: a different file at this name
    if st.st_size < prev.offset:
        return True  # truncation
    # Replacement: the head the stored cursor covered changed. Appends keep that prefix.
    return head_digest_fd(fd, min(prev.size, HEAD_BYTES)) != prev.head_digest


def settled(prev: FileCursor, listed: Listed, root_fd: int) -> bool:
    """True when the file is unchanged and read to its end. The file is opened and its identity,
    size, modification time and head digest are checked before it is skipped."""
    if not _unchanged(prev, listed.st) or prev.offset != prev.size:
        return False
    try:
        fd, fst = open_confined(root_fd, listed.rel)
    except OSError:
        return False
    try:
        return _unchanged(prev, fst) and head_digest_fd(fd, min(prev.size, HEAD_BYTES)) == (
            prev.head_digest
        )
    finally:
        os.close(fd)


# Reverify unchanged stat stamps periodically because coarse timestamps can hide same-size rewrites.

RECONCILE_PASSES = 16
MAX_RECONCILE_PASSES = 256
MAX_INDEX_ROOTS = 32


@dataclass(frozen=True)
class _IndexedFile:
    dev: int
    ino: int
    size: int
    mtime_ns: int
    ctime_ns: int
    head_digest: str
    verified_pass: int


def _stamp_matches(entry: _IndexedFile, st: os.stat_result) -> bool:
    return (
        entry.dev == st.st_dev
        and entry.ino == st.st_ino
        and entry.size == st.st_size
        and entry.mtime_ns == st.st_mtime_ns
        and entry.ctime_ns == st.st_ctime_ns
    )


class _ChangeIndex:
    def __init__(self) -> None:
        self.files: dict[str, _IndexedFile] = {}
        self.pass_no = 0
        self.reconcile_every = RECONCILE_PASSES
        self.round_checked = 0
        self.round_dirty = False


_IndexKey = tuple[int, int, str, int]
_indexes: OrderedDict[_IndexKey, _ChangeIndex] = OrderedDict()
_index_lock = threading.Lock()


def _index_for(root_fd: int, schema: str, layout: Layout) -> _ChangeIndex:
    st = os.fstat(root_fd)
    key: _IndexKey = (st.st_dev, st.st_ino, schema, id(layout))
    with _index_lock:
        index = _indexes.get(key)
        if index is None:
            index = _ChangeIndex()
            _indexes[key] = index
            while len(_indexes) > MAX_INDEX_ROOTS:
                _indexes.popitem(last=False)
        else:
            _indexes.move_to_end(key)
        index.pass_no += 1
        return index


def _index_skip(index: _ChangeIndex, key: str, prev: FileCursor, st: os.stat_result) -> bool:
    """True when this file's head was verified against exactly this stamp recently enough."""
    with _index_lock:
        entry = index.files.get(key)
    if entry is None or entry.head_digest != prev.head_digest:
        return False
    if not _stamp_matches(entry, st):
        return False
    return index.pass_no - entry.verified_pass < index.reconcile_every


def _index_reverifying(index: _ChangeIndex, key: str, st: os.stat_result) -> bool:
    """True when an indexed file with an unchanged stamp is being re-verified this pass."""
    with _index_lock:
        entry = index.files.get(key)
    return entry is not None and _stamp_matches(entry, st)


def _index_store(index: _ChangeIndex, key: str, st: os.stat_result, head_digest: str) -> None:
    entry = _IndexedFile(
        dev=st.st_dev,
        ino=st.st_ino,
        size=st.st_size,
        mtime_ns=st.st_mtime_ns,
        ctime_ns=st.st_ctime_ns,
        head_digest=head_digest,
        verified_pass=index.pass_no,
    )
    with _index_lock:
        if key not in index.files and len(index.files) >= MAX_TRACKED_FILES:
            index.files.clear()  # bounded: forget rather than grow past the cursor bound
        index.files[key] = entry


def _index_drop(index: _ChangeIndex, key: str) -> None:
    with _index_lock:
        index.files.pop(key, None)


def _index_prune(index: _ChangeIndex, present: set[str]) -> None:
    with _index_lock:
        for key in list(index.files):
            if key not in present:
                index.files.pop(key, None)


def _index_note_round(index: _ChangeIndex, *, dirty: bool) -> None:
    with _index_lock:
        index.round_checked += 1
        index.round_dirty = index.round_dirty or dirty


def _index_close_round(index: _ChangeIndex) -> None:
    """Back the re-verification interval off after a clean round; reset it after a dirty one."""
    with _index_lock:
        if index.round_checked:
            index.reconcile_every = (
                RECONCILE_PASSES
                if index.round_dirty
                else min(MAX_RECONCILE_PASSES, index.reconcile_every * 2)
            )
        index.round_checked = 0
        index.round_dirty = False


def context_of(file: FileCursor | None) -> dict[str, object]:
    """The per-file context as a dict; an absent or unreadable context is empty."""
    if file is None or not file.context:
        return {}
    try:
        loaded = json.loads(file.context)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _discard_in(prev: FileCursor | None, start: int) -> int | None:
    loaded = context_of(prev).get("x")
    if not isinstance(loaded, dict) or loaded.get("s") != start:
        return None
    scan = loaded.get("p")
    return scan if isinstance(scan, int) and not isinstance(scan, bool) and scan >= start else None


@dataclass
class ReadResult:
    offset: int
    reached_end: bool
    gap: bool
    tail: bool
    discard: dict[str, int] | None
    resumable: bool = False  # stopped by the pass budget or a refused line: more to read


def read_lines(
    fd: int,
    start: int,
    key: str,
    rel: str,
    state: PassState,
    handler: LineHandler,
    discard_at: int | None,
) -> ReadResult:
    """Feed complete lines from `start` to the handler. Discard progress resumes at `discard_at`."""
    base = start
    read_abs = discard_at if discard_at is not None else start
    buf = bytearray()
    discarding = discard_at is not None
    gap = False
    while True:
        if state.bytes_left <= 0 or state.out_of_time():
            if not discarding and read_abs - base >= state.budget.max_bytes:
                # The unterminated line alone fills a whole pass budget, so no pass can parse it.
                # Discard its tail and persist the position, so later files are not starved.
                discarding = True
                buf = bytearray()
                gap = True
            if discarding:
                return ReadResult(base, False, True, False, {"s": base, "p": read_abs}, True)
            return ReadResult(base, False, gap, False, None, True)
        chunk = os.pread(fd, min(CHUNK_BYTES, state.bytes_left), read_abs)
        if not chunk:
            break
        state.bytes_left -= len(chunk)
        chunk_start = read_abs
        read_abs += len(chunk)
        if discarding:
            newline = chunk.find(b"\n")
            if newline < 0:
                continue
            base = chunk_start + newline + 1
            discarding = False
            state.skip()
            buf = bytearray(chunk[newline + 1 :])
        else:
            buf.extend(chunk)
        while True:
            newline = buf.find(b"\n")
            if newline < 0:
                break
            data = bytes(buf[:newline])
            line_offset = base
            if len(data) > MAX_LINE_BYTES:
                state.skip()
                gap = True
            elif handler.line(key, rel, line_offset, data) is False:
                state.stopped = True
                return ReadResult(base, False, gap, False, None, True)
            del buf[: newline + 1]
            base = line_offset + len(data) + 1
        if not discarding and len(buf) > MAX_LINE_BYTES:
            buf = bytearray()  # an oversize line that has not ended: skip to its end
            discarding = True
            gap = True
    if discarding:
        return ReadResult(base, False, True, False, {"s": base, "p": read_abs}, False)
    return ReadResult(base, True, gap, bool(buf), None, False)


def cursor_for(fd: int, st: os.stat_result, offset: int) -> FileCursor:
    """The stored identity of an open file, with the offset the unit has committed. The context
    is set by the commit, which knows the whole per-file state."""
    return FileCursor(
        dev=st.st_dev,
        ino=st.st_ino,
        offset=offset,
        size=st.st_size,
        mtime_ns=st.st_mtime_ns,
        head_digest=head_digest_fd(fd, st.st_size),
    )


# -- the commit ledger: every change is applied only if the whole cursor still fits --------------


def _pair_len(key: str, value_json: str) -> int:
    return len(json.dumps(key)) + 1 + len(value_json)


def _object_len(total: int, count: int) -> int:
    """Encoded length of a JSON object with `count` pairs whose pair lengths sum to `total`."""
    return 2 + total + max(count - 1, 0)


def _file_json(cursor: FileCursor) -> str:
    return json.dumps(
        [
            cursor.dev,
            cursor.ino,
            cursor.offset,
            cursor.size,
            cursor.mtime_ns,
            cursor.head_digest,
            cursor.context,
        ],
        separators=(",", ":"),
    )


class Ledger:
    """The next cursor, built one unit at a time. Sizes are tracked incrementally and match
    contracts.encoded_cursor_bytes, so a unit is checked without re-encoding the whole cursor."""

    def __init__(
        self,
        files: Mapping[str, FileCursor],
        state: Mapping[str, str],
        reserved: Mapping[str, str] | None = None,
    ) -> None:
        self.files = dict(files)
        self.state = dict(state)
        self._reserved = {k: _pair_len(k, json.dumps(v)) for k, v in (reserved or {}).items()}
        self._fpair = {k: _pair_len(k, _file_json(v)) for k, v in self.files.items()}
        self._spair = {k: _pair_len(k, json.dumps(v)) for k, v in self.state.items()}
        self._ftotal = sum(self._fpair.values())
        self._stotal = sum(self._spair.values())

    def _plan(
        self,
        files_set: dict[str, FileCursor | None],
        state_set: dict[str, str],
        state_del: set[str],
    ) -> tuple[int, int, int, int]:
        ftotal, fcount = self._ftotal, len(self._fpair)
        for key, value in files_set.items():
            old = self._fpair.get(key)
            if old is not None:
                ftotal -= old
                fcount -= 1
            if value is not None:
                ftotal += _pair_len(key, _file_json(value))
                fcount += 1
        stotal, scount = self._stotal, len(self._spair)
        for key in set(state_set) | state_del:
            old_state = self._spair.get(key)
            if old_state is not None:
                stotal -= old_state
                scount -= 1
        for key, text in state_set.items():
            stotal += _pair_len(key, json.dumps(text))
            scount += 1
        # Global metadata ("v:", "e:", "o:") is reserved: room for it is kept in every plan.
        for key, pair in self._reserved.items():
            present = key in state_set or (key in self._spair and key not in state_del)
            if not present:
                stotal += pair
                scount += 1
        return ftotal, fcount, stotal, scount

    @staticmethod
    def _within(ftotal: int, fcount: int, stotal: int, scount: int) -> bool:
        state_bytes = _object_len(stotal, scount)
        cursor_bytes = (
            len('{"files":')
            + _object_len(ftotal, fcount)
            + len(',"state":')
            + state_bytes
            + len("}")
        )
        return (
            fcount <= MAX_TRACKED_FILES
            and cursor_bytes <= MAX_CURSOR_BYTES
            and scount <= MAX_CURSOR_STATE_ENTRIES
            and state_bytes <= MAX_CURSOR_STATE_BYTES
        )

    def would_fit(
        self,
        files_set: dict[str, FileCursor | None],
        state_set: dict[str, str],
        state_del: set[str],
    ) -> bool:
        for value in files_set.values():
            if value is not None and len(value.context.encode()) > MAX_FILE_CONTEXT_BYTES:
                return False
        return self._within(*self._plan(files_set, state_set, state_del))

    def try_commit(
        self,
        files_set: dict[str, FileCursor | None],
        state_set: dict[str, str],
        state_del: set[str],
    ) -> bool:
        if not self.would_fit(files_set, state_set, state_del):
            return False
        self.apply(files_set, state_set, state_del)
        return True

    def apply(
        self,
        files_set: dict[str, FileCursor | None],
        state_set: dict[str, str],
        state_del: set[str],
    ) -> None:
        for key, value in files_set.items():
            old = self._fpair.pop(key, None)
            if old is not None:
                self._ftotal -= old
            if value is None:
                self.files.pop(key, None)
            else:
                pair = _pair_len(key, _file_json(value))
                self.files[key] = value
                self._fpair[key] = pair
                self._ftotal += pair
        for key in set(state_set) | state_del:
            old_state = self._spair.pop(key, None)
            if old_state is not None:
                self._stotal -= old_state
            self.state.pop(key, None)
        for key, text in state_set.items():
            pair = _pair_len(key, json.dumps(text))
            self.state[key] = text
            self._spair[key] = pair
            self._stotal += pair

    def encoded_bytes(self) -> int:
        """The encoded size of the whole cursor, by the same arithmetic as the commit check."""
        return (
            len('{"files":')
            + _object_len(self._ftotal, len(self._fpair))
            + len(',"state":')
            + _object_len(self._stotal, len(self._spair))
            + len("}")
        )

    def summarise_gaps(self) -> bool:
        """Replace per-file gap flags with one retained overflow marker when space is needed.
        Preserve accounting context and return whether a flag was summarized.
        """
        changed: dict[str, FileCursor | None] = {}
        for key, file in self.files.items():
            context = context_of(file)
            if context.get("g") == 1:
                context.pop("g")
                context.pop(REASON_KEY, None)  # the overflow marker keeps the uncertainty instead
                text = canonical_json(context) if context else ""
                changed[key] = replace(file, context=text)
        if not changed:
            return False
        self.apply(changed, {OVERFLOW_KEY: "1"}, set())
        return True

    def drop_absent(self, present: set[str]) -> None:
        """Delete the files a complete enumeration did not find (their context goes with them),
        and any per-file entries of the earlier layout."""
        files_set: dict[str, FileCursor | None] = {
            key: None for key in self.files if key not in present
        }
        state_del = {
            key
            for key in self.state
            if key == LEGACY_POSITION_KEY or (key[:2] in PRUNED_PREFIXES and key[2:] not in present)
        }
        self.apply(files_set, {}, state_del)


# -- one unit, one pass --------------------------------------------------------------------------


@dataclass
class Unit:
    """What one file or document contributes to this pass. Applied only if it fits the cursor."""

    key: str
    file: FileCursor | None = None  # None keeps the stored file cursor
    discard: dict[str, int] | None = None  # None clears the discard checkpoint
    gap: bool = False  # permanent gap evidence for this file
    restarted: bool = False  # read from zero: its old gap evidence is dropped
    resumable: bool = False  # stopped by the budget with more to read in this file
    stop_pass: bool = False  # the pass ends after this unit
    reader: dict[str, object] | None = None  # reader context; None keeps the stored one
    state_del: set[str] = field(default_factory=set)  # legacy entries this unit migrates away
    skipped: int = 0
    partial: bool = False
    reasons: frozenset[str] = frozenset()  # why this unit's gap is set (only with gap)
    unreadable: bool = False  # a current obstruction this pass: the file could not be read
    oversize_now: bool = False  # a current obstruction this pass: an oversize line was hit
    capacity_now: bool = False  # a current obstruction this pass: the cursor could not hold it


def reasons_of(file: FileCursor | None) -> frozenset[str]:
    """Decode permanent gap reasons, retaining UNKNOWN when no cause was recorded. Return no
    reasons when there is no gap.
    """
    context = context_of(file)
    if context.get("g") != 1:
        return frozenset()
    raw = context.get(REASON_KEY)
    found = (
        frozenset(item for item in raw if isinstance(item, str) and item in REASONS)
        if isinstance(raw, list)
        else frozenset()
    )
    return found or frozenset({REASON_UNKNOWN})


def file_context(
    *,
    reader: dict[str, object] | None,
    discard: dict[str, int] | None,
    gap: bool,
    pending: bool,
    reasons: frozenset[str] = frozenset(),
) -> str:
    """The per-file context text: reader entries, discard checkpoint, gap, its reasons and pending
    marks. Reasons are written only beside a gap, as a sorted list of fixed codes."""
    context: dict[str, object] = {}
    if reader is not None:
        context["r"] = reader
    if discard is not None:
        context["x"] = discard
    if gap:
        context["g"] = 1
        if reasons:
            context[REASON_KEY] = sorted(reasons)
    if pending:
        context["p"] = 1
    return canonical_json(context) if context else ""


def _commit(ledger: Ledger, unit: Unit) -> bool:
    """Stage the file checkpoint and its bounded context, blocking the unit if they cannot fit.
    Preserve unread-file uncertainty in the global overflow marker without inventing a
    checkpoint.
    """
    key = unit.key
    prev = ledger.files.get(key)
    if unit.file is None and prev is None:
        if not (unit.gap or unit.partial):
            return True  # nothing read and nothing to record: the file stays unknown work
        return ledger.try_commit({}, {OVERFLOW_KEY: "1"}, set(unit.state_del))
    held = context_of(prev)
    held_reader = held.get("r")
    reader = unit.reader
    if reader is None and isinstance(held_reader, dict):
        reader = held_reader
    # Gap evidence from the earlier layout (a per-file state key) migrates into the file's context.
    legacy = {k for k in (GAP_PREFIX + key, DISCARD_PREFIX + key, PENDING_PREFIX + key)}
    legacy_del = legacy & set(ledger.state)
    unit.state_del |= legacy_del
    legacy_gap = (GAP_PREFIX + key) in legacy_del
    held_gap = not unit.restarted and (held.get("g") == 1 or legacy_gap)
    gap = unit.gap or held_gap
    # The reasons of a kept gap are kept with it; a gap with no known reason is unknown.
    reasons = frozenset(unit.reasons) | (reasons_of(prev) if held_gap and prev else frozenset())
    if legacy_gap:
        reasons |= {REASON_UNKNOWN}  # the old global marker has no known cause; never reclassified
    if gap and not reasons:
        reasons = frozenset({REASON_UNKNOWN})
    base = unit.file if unit.file is not None else prev
    assert base is not None  # noqa: S101

    def stage(with_gap: bool) -> FileCursor | None:
        text = file_context(
            reader=reader,
            discard=unit.discard,
            gap=with_gap,
            pending=unit.resumable,
            reasons=reasons if with_gap else frozenset(),
        )
        if len(text.encode()) > MAX_FILE_CONTEXT_BYTES:
            return None
        return replace(base, context=text)

    staged = stage(gap)
    if staged is not None and ledger.try_commit({key: staged}, {}, set(unit.state_del)):
        return True
    if gap:
        fallback = stage(False)
        if fallback is not None and ledger.try_commit(
            {key: fallback}, {OVERFLOW_KEY: "1"}, set(unit.state_del)
        ):
            return True
    return False


@dataclass
class PassResult:
    events: tuple[UsageEvent, ...]
    cursor: Cursor
    partial: bool
    more: bool
    skipped: int
    unavailable: bool = False
    # A current obstruction in this pass (unreadable file, incomplete enumeration, refused commit,
    # oversize line). Historical gap flags never set it.
    retry_blocked: bool = False


def _budget_gone(state: PassState, collector: EventCollector) -> bool:
    return (
        state.stopped
        or state.out_of_time()
        or state.files_left <= 0
        or state.bytes_left <= 0
        or len(collector.events) >= collector.limit
    )


def _known_unread(item: Listed, ledger: Ledger, excluded: set[str], observed: set[str]) -> bool:
    """Known unread work: a file whose change was observed this pass, a changed or never-seen file,
    or one read partly because of the budget. Tails, excluded files and permanent gaps are not
    work."""
    key = file_key(item.rel)
    if key in excluded:
        return False
    if key in observed:
        return True
    prev = ledger.files.get(key)
    if prev is None or not _unchanged(prev, item.st):
        return True
    return context_of(prev).get("p") == 1


def _stopped_before(
    rest: list[Listed], ledger: Ledger, excluded: set[str], observed: set[str]
) -> tuple[bool, bool]:
    """(more, partial) for a pass that stopped with `rest` unprocessed. Unprocessed candidates were
    never verified, so the batch is PARTIAL; `more` only for known unread work."""
    more = any(_known_unread(item, ledger, excluded, observed) for item in rest)
    return more, bool(rest)


def _has_legacy_entries(ledger: Ledger, key: str) -> bool:
    return any(
        prefix + key in ledger.state for prefix in (GAP_PREFIX, DISCARD_PREFIX, PENDING_PREFIX)
    )


def validate_cursor(cursor: Cursor) -> bool:
    """The rules the store applies to a cursor, as one shared check: counts, key shapes, identity
    values, context bounds and the whole-cursor size. A pass never hands out a cursor that fails
    it."""
    if len(cursor.files) > MAX_TRACKED_FILES or len(cursor.state) > MAX_CURSOR_STATE_ENTRIES:
        return False
    for key, value in cursor.state.items():
        if not _key_ok(key) or not isinstance(value, str):
            return False
    if encoded_state_bytes(cursor.state) > MAX_CURSOR_STATE_BYTES:
        return False
    for key, file in cursor.files.items():
        if not _key_ok(key):
            return False
        if (
            any(type(n) is not int or n < 0 for n in (file.dev, file.ino, file.offset, file.size))
            or file.offset > file.size
        ):
            return False
        if type(file.mtime_ns) is not int or file.mtime_ns < 0:
            return False
        if len(file.head_digest) != 64 or any(
            c not in "0123456789abcdef" for c in file.head_digest
        ):
            return False
        if not isinstance(file.context, str) or len(file.context.encode()) > MAX_FILE_CONTEXT_BYTES:
            return False
    return encoded_cursor_bytes(cursor) <= MAX_CURSOR_BYTES


def _key_ok(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_KEY_CHARS
        and all(ord(c) >= 32 for c in value)
    )


def run_units(
    root: Path,
    cursor: Cursor,
    budget: Budget,
    layout: Layout,
    process: UnitProcess,
    schema: str = "",
    rewind: bool = True,
) -> PassResult:
    """Enumerate bounded layout candidates and process them newest first. Rewind incompatible
    cursors only when requested; otherwise preserve stored state for compatible conversion.
    """
    base = cursor
    if rewind and schema and cursor.state.get(SCHEMA_KEY) != schema:
        base = Cursor(files={}, state={}, revision=cursor.revision)
    state = PassState(budget=budget)
    reserved = {ENUM_GAP_KEY: "1", OVERFLOW_KEY: "1"}
    if schema:
        reserved[SCHEMA_KEY] = schema
    ledger = Ledger(base.files, base.state, reserved)
    if schema and ledger.state.get(SCHEMA_KEY) != schema:
        ledger.apply({}, {SCHEMA_KEY: schema}, set())
    collector = EventCollector(budget.max_events)
    root_fd = open_root(root)
    if root_fd is None:
        return PassResult((), cursor, True, False, 0, unavailable=True, retry_blocked=True)
    change_index = _index_for(root_fd, schema, layout)
    blocked: set[str] = set()  # could not commit this pass
    obstructed = False  # a current obstruction in this pass: the scheduler may back off
    unreadable: set[str] = set()  # a new file that could not be read: its gap is global
    observed: set[str] = set()  # a settled check saw a change: known unread work
    more = False
    partial = False
    complete = False
    present: set[str] = set()
    try:
        listed, complete = enumerate_layout(root_fd, layout, state)
        listed.sort(key=lambda item: (-item.st.st_mtime_ns, item.rel))
        present = {file_key(item.rel) for item in listed}
        excluded = blocked | unreadable
        for index, item in enumerate(listed):
            key = file_key(item.rel)
            prev = ledger.files.get(key)
            # The deadline and the examined-entry cap are checked before any open or hash.
            if _budget_gone(state, collector) or state.examined >= MAX_EXAMINED_ENTRIES:
                more, stopped_partial = _stopped_before(
                    listed[index:], ledger, blocked | unreadable, observed
                )
                partial = partial or stopped_partial
                break
            if prev is not None and _unchanged(prev, item.st) and prev.offset == prev.size:
                state.examined += 1
                if _index_skip(change_index, key, prev, item.st):
                    if _has_legacy_entries(ledger, key):
                        _commit(ledger, Unit(key=key))  # migrate the earlier layout, no read
                    continue
                reverifying = _index_reverifying(change_index, key, item.st)
                if settled(prev, item, root_fd):
                    _index_store(change_index, key, item.st, prev.head_digest)
                    if reverifying:
                        _index_note_round(change_index, dirty=False)
                    if _has_legacy_entries(ledger, key):
                        _commit(ledger, Unit(key=key))  # migrate the earlier layout, no read
                    continue
                if reverifying:
                    _index_note_round(change_index, dirty=True)
                _index_drop(change_index, key)
                observed.add(key)  # the head changed: the file is known unread work
                if _budget_gone(state, collector):
                    more, stopped_partial = _stopped_before(
                        listed[index:], ledger, blocked | unreadable, observed
                    )
                    partial = partial or stopped_partial
                    break
            pending = EventCollector(collector.limit - len(collector.events))
            unit = process(root_fd, item, prev, pending, state, ledger)
            state.reads += 1
            state.files_left -= 1
            state.skipped += unit.skipped
            if unit.partial or unit.resumable:
                partial = True
            obstructed = obstructed or unit.unreadable or unit.oversize_now or unit.capacity_now
            committed = _commit(ledger, unit)
            if not committed and ledger.summarise_gaps():
                committed = _commit(ledger, unit)  # room freed by the summarised gap flags
            if committed:
                for event in pending.events.values():
                    collector.offer(event)
                if prev is None and unit.file is None and unit.gap:
                    unreadable.add(key)
                # A file read to its end against this exact stamp is verified
                # work for the index; anything else must be re-verified.
                if (
                    unit.file is not None
                    and unit.file.offset == unit.file.size
                    and _unchanged(unit.file, item.st)
                ):
                    _index_store(change_index, key, item.st, unit.file.head_digest)
                else:
                    _index_drop(change_index, key)
            else:
                blocked.add(key)
                partial = True
                obstructed = True  # the cursor could not hold this file: a capacity refusal
            if unit.resumable:
                more = committed
                break
            if unit.stop_pass:
                more_after, stopped_partial = _stopped_before(
                    listed[index + 1 :], ledger, blocked | unreadable, observed
                )
                more = more_after
                partial = partial or stopped_partial
                break
    finally:
        os.close(root_fd)
    excluded = blocked | unreadable
    _index_close_round(change_index)
    if complete:
        ledger.drop_absent(present)
        _index_prune(change_index, present)
        ledger.apply({}, {}, {ENUM_GAP_KEY})
    else:
        partial = True
        more = False
        obstructed = True  # an enumeration that did not finish is a current obstruction
        ledger.apply({}, {ENUM_GAP_KEY: "1"}, set())
    permanent_gap = any(context_of(f).get("g") == 1 for f in ledger.files.values())
    if excluded or permanent_gap or OVERFLOW_KEY in ledger.state:
        partial = True
    final = Cursor(files=ledger.files, state=ledger.state, revision=cursor.revision)
    if not validate_cursor(final):
        return PassResult((), cursor, True, False, 0, unavailable=True, retry_blocked=True)
    return PassResult(
        events=collector.ordered(),
        cursor=final,
        partial=partial or more,
        more=more,
        skipped=state.skipped,
        retry_blocked=obstructed,
    )


def to_batch(
    result: PassResult,
    cursor: Cursor,
    clean: Coverage,
    limits: tuple[LimitSnapshot, ...] = (),
) -> Batch:
    """The reader's Batch: PARTIAL when the pass is partial, else `clean`."""
    if result.unavailable:
        return Batch((), cursor, Coverage.UNAVAILABLE, cursor.revision, retry_blocked=True)
    return Batch(
        events=result.events,
        next_cursor=result.cursor,
        coverage=Coverage.PARTIAL if result.partial else clean,
        expected_revision=cursor.revision,
        limits=limits,
        skipped=result.skipped,
        more=result.more,
        retry_blocked=result.retry_blocked,
    )


def read_file_unit(
    root_fd: int,
    item: Listed,
    prev: FileCursor | None,
    base: Cursor,
    pending: EventCollector,
    state: PassState,
    handler: LineHandler,
) -> Unit:
    """Read one line-based file from its stored offset into a unit. Failures become gap evidence."""
    key = file_key(item.rel)
    if state.reads > 0 and state.bytes_left <= 0:
        return Unit(key=key, resumable=True)
    unreadable = Unit(
        key=key,
        gap=True,
        partial=True,
        skipped=1,
        reasons=frozenset({REASON_UNREADABLE}),
        unreadable=True,
    )
    try:
        fd, st = open_confined(root_fd, item.rel)
    except OSError:
        return unreadable
    try:
        if st.st_size > MAX_FILE_BYTES:
            return Unit(
                key=key,
                gap=True,
                partial=True,
                skipped=1,
                reasons=frozenset({REASON_OVERSIZE}),
                oversize_now=True,
            )
        restarted = prev is not None and _restart_needed(prev, fd, st)
        start = 0 if prev is None or restarted else prev.offset
        discard_at = None if restarted or prev is None else _discard_in(prev, start)
        handler.collector = pending
        skipped_before = handler.skipped
        handler.begin(key, item.rel, restarted)
        result = read_lines(fd, start, key, item.rel, state, handler, discard_at)
        # A verdict about the whole file needs its end read and no unterminated record left.
        handler.reached_end = result.reached_end and not result.tail
        legacy_gap = handler.end(key, item.rel)
        reasons: set[str] = set()
        if result.gap:
            reasons.add(REASON_OVERSIZE)
        if handler.skipped > skipped_before:
            reasons.add(REASON_SKIP)  # independent of legacy: a malformed record is its own cause
        if legacy_gap:
            reasons.add(REASON_LEGACY)
        if handler.unknown_gap:
            reasons.add(REASON_UNKNOWN)
        gap = result.gap or handler.skipped > skipped_before or legacy_gap or handler.unknown_gap
        return Unit(
            key=key,
            file=cursor_for(fd, st, result.offset),
            discard=result.discard,
            gap=gap,
            restarted=restarted,
            resumable=result.resumable,
            reader=handler.reader_context(key),
            skipped=handler.skipped - skipped_before,
            partial=not result.reached_end or result.tail or gap,
            reasons=frozenset(reasons),
            oversize_now=result.gap,
        )
    except OSError:
        return unreadable
    finally:
        os.close(fd)


# -- value helpers shared by the readers -----------------------------------------------------


def as_count(value: object) -> int | None:
    """A non-negative integer count; bools, floats, strings and negatives are rejected."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > 2**53:
        return None
    return value


def ms_of(value: object) -> int | None:
    """Milliseconds since the epoch from an ISO-8601 timestamp (UTC when no offset)."""
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1000)


def key_text(*parts: object) -> str:
    """Bounded, stable digest of identity parts (ids, epochs, totals); never includes content."""
    raw = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()[:48]


def session_text(value: object) -> str | None:
    """A session id as an opaque key: kept when short, digested when long."""
    if not isinstance(value, str) or not value:
        return None
    return value if len(value) <= MAX_KEY_CHARS else key_text("session", value)


def model_text(value: object) -> str | None:
    if isinstance(value, str) and 0 < len(value) <= MAX_MODEL_CHARS:
        return value
    return None


def counters_of(
    *,
    input_uncached: int = 0,
    cache_read: int = 0,
    cache_write_short: int = 0,
    cache_write_long: int = 0,
    cache_write_unknown: int = 0,
    output: int = 0,
    reasoning: int = 0,
) -> Counters | None:
    """Validated counters; None when a bucket is invalid (the record is then skipped, counted)."""
    try:
        return Counters(
            input_uncached=input_uncached,
            cache_read=cache_read,
            cache_write_short=cache_write_short,
            cache_write_long=cache_write_long,
            output=output,
            reasoning=min(reasoning, output),
            cache_write_unknown=cache_write_unknown,
        )
    except ValueError:
        return None


class EventCollector:
    """Deduplicates events by (session, event key). A new key beyond the limit is refused: the
    caller does not commit that line.
    """

    def __init__(self, limit: int = MAX_EVENTS_PER_BATCH) -> None:
        self.limit = limit
        self.events: dict[tuple[str, str], UsageEvent] = {}

    def offer(self, event: UsageEvent) -> bool:
        key = (event.session_key, event.event_key)
        if key not in self.events and len(self.events) >= self.limit:
            return False
        self.events[key] = event
        return True

    def ordered(self) -> tuple[UsageEvent, ...]:
        return tuple(
            sorted(self.events.values(), key=lambda e: (e.occurred_ms, e.session_key, e.event_key))
        )


def make_event(
    *,
    session: str,
    event_key: str,
    occurred_ms: int,
    model: object,
    counters: Counters,
    basis: Basis,
    parent: str | None = None,
    verified: bool = True,
    digest: str | None = None,
    meter: str = "tokens",
    credits_milli: int = 0,
) -> UsageEvent:
    return UsageEvent(
        session_key=session,
        event_key=event_key,
        occurred_ms=occurred_ms,
        model=model_text(model),
        counters=counters,
        basis=basis,
        parent_session_key=parent,
        accounting_verified=verified,
        cwd_digest=digest,
        meter=meter,
        credits_milli=credits_milli,
    )


UnitProcess = Callable[[int, Listed, FileCursor | None, EventCollector, PassState, Ledger], Unit]
