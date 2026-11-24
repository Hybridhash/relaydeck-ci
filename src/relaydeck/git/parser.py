"""Parse a single file's diff body into paged rows and word marks, ignoring header paths. Report
unrepresentable content as a render state rather than altering it.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, replace
from typing import Any

from relaydeck.contracts.cursors import MAX_CURSOR_LEN
from relaydeck.contracts.git import DiffLine, FileDiff, Hunk, LineKind, RenderState
from relaydeck.git.secret_policy import LineMasker, UnsafeKeyContext

MAX_PAGE_ROWS = 500
MAX_PAGE_BYTES = 256 * 1024
MAX_LINE_BYTES = 16 * 1024
MAX_FILE_ROWS = 20_000
MARK_MAX_CHARS = 200
# Sum of (old tokens x new tokens) over all matched pairs of one file.
MARK_WORK_BUDGET = 500_000

_HUNK = re.compile(rb"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_MODE = re.compile(rb"(old|new|new file|deleted file) mode (\d{6})$")
_TOKEN = re.compile(r"\w+|\s+|[^\w\s]")
_KINDS: dict[bytes, LineKind] = {b" ": "context", b"-": "delete", b"+": "add"}

Marks = tuple[tuple[int, int], ...]


class _Reject(Exception):
    """The patch cannot be rendered; `state` says why."""

    def __init__(self, state: RenderState) -> None:
        super().__init__(state)
        self.state = state


def _json_len(doc: Any) -> int:
    return len(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def page_size(page: FileDiff) -> int:
    """Serialized size of a page, as the gateway sends it (compact UTF-8 JSON)."""
    return _json_len(page.to_dict())


@dataclass(slots=True)
class _Head:
    old_mode: str | None = None
    new_mode: str | None = None
    binary: bool = False


@dataclass(frozen=True, slots=True)
class _RawHunk:
    header: str
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: list[DiffLine]


class _MarkBudget:
    def __init__(self, work: int) -> None:
        self._left = work

    def marks(self, old: str, new: str) -> tuple[Marks, Marks]:
        """Changed word ranges of a paired delete/add, in code points; empty past the budget."""
        if len(old) > MARK_MAX_CHARS or len(new) > MARK_MAX_CHARS:
            return (), ()
        a, b = _TOKEN.findall(old), _TOKEN.findall(new)
        cost = len(a) * len(b)
        if cost > self._left:
            return (), ()
        self._left -= cost
        starts_a, starts_b = _offsets(a), _offsets(b)
        left: list[tuple[int, int]] = []
        right: list[tuple[int, int]] = []
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, a, b, autojunk=False
        ).get_opcodes():
            if tag == "equal":
                continue
            if i2 > i1:
                left.append((starts_a[i1], starts_a[i2]))
            if j2 > j1:
                right.append((starts_b[j1], starts_b[j2]))
        return _useful(left, len(old)), _useful(right, len(new))


def _offsets(tokens: list[str]) -> list[int]:
    out = [0]
    for token in tokens:
        out.append(out[-1] + len(token))
    return out


def _useful(ranges: list[tuple[int, int]], length: int) -> Marks:
    """A mark over the whole line says nothing the line colour does not."""
    return () if ranges == [(0, length)] else tuple(ranges)


def _mark_pairs(lines: list[DiffLine], budget: _MarkBudget) -> list[DiffLine]:
    """Pair the n-th delete of a run with the n-th add after it; notices do not end a run."""
    out = list(lines)
    dels: list[int] = []
    adds: list[int] = []

    def flush() -> None:
        for d, a in zip(dels, adds, strict=False):
            old_marks, new_marks = budget.marks(out[d].text, out[a].text)
            if old_marks:
                out[d] = replace(out[d], marks=old_marks)
            if new_marks:
                out[a] = replace(out[a], marks=new_marks)
        dels.clear()
        adds.clear()

    for index, line in enumerate(out):
        if line.kind == "delete":
            if adds:
                flush()
            dels.append(index)
        elif line.kind == "add":
            adds.append(index)
        elif line.kind == "context":
            flush()
    flush()
    return out


class DiffParser:
    """Parses one file's `git diff` bytes; the limits default to the wire contract."""

    def __init__(
        self,
        *,
        max_rows: int = MAX_PAGE_ROWS,
        max_bytes: int = MAX_PAGE_BYTES,
        max_line_bytes: int = MAX_LINE_BYTES,
        max_file_rows: int = MAX_FILE_ROWS,
        mark_work: int = MARK_WORK_BUDGET,
    ) -> None:
        self._max_rows = max_rows
        self._max_bytes = max_bytes
        self._max_line_bytes = max_line_bytes
        self._max_file_rows = max_file_rows
        self._mark_work = mark_work

    def parse(
        self, patch: bytes, *, file_id: str, snapshot: str, masker: LineMasker | None = None
    ) -> tuple[FileDiff, ...]:
        """Build nonempty pages within wire byte limits, masking rows before paging when
        requested. Leave next-cursor creation to the gateway.
        """
        rows = patch.split(b"\n")
        if rows and rows[-1] == b"":
            rows.pop()
        head = _Head()
        try:
            start = self._read_head(rows, head)
            if head.binary:
                return (self._single(file_id, snapshot, head, "binary"),)
            hunks = self._read_hunks(rows, start)
        except _Reject as reject:
            return (self._single(file_id, snapshot, head, reject.state),)
        budget = _MarkBudget(self._mark_work)
        marked = [replace(hunk, lines=_mark_pairs(hunk.lines, budget)) for hunk in hunks]
        if masker is not None:
            try:
                marked = [
                    replace(hunk, lines=[masker.mask_line(line) for line in hunk.lines])
                    for hunk in marked
                ]
            except UnsafeKeyContext:
                return (self._single(file_id, snapshot, head, "withheld"),)
        return self._paginate(marked, file_id, snapshot, head)

    @staticmethod
    def _single(file_id: str, snapshot: str, head: _Head, render: RenderState) -> FileDiff:
        return FileDiff(
            file_id=file_id,
            snapshot=snapshot,
            old_mode=head.old_mode,
            new_mode=head.new_mode,
            render=render,
            hunks=(),
        )

    @staticmethod
    def _read_head(rows: list[bytes], head: _Head) -> int:
        seen_file = False
        for index, row in enumerate(rows):
            if row.startswith(b"@@"):
                return index
            if row.startswith(b"diff --git "):
                if seen_file:
                    raise _Reject("unsupported")
                seen_file = True
            elif row.startswith(b"Binary files ") or row == b"GIT binary patch":
                head.binary = True
            elif (mode := _MODE.match(row)) is not None:
                value = mode[2].decode("ascii")
                if mode[1] in (b"old", b"deleted file"):
                    head.old_mode = value
                else:
                    head.new_mode = value
        return len(rows)

    def _read_hunks(self, rows: list[bytes], index: int) -> list[_RawHunk]:
        hunks: list[_RawHunk] = []
        total = 0
        while index < len(rows):
            if not rows[index].startswith(b"@@"):
                # A second file, or rows the hunk counts did not account for: never drop them.
                raise _Reject("unsupported")
            hunk, index = self._read_hunk(rows, index)
            total += len(hunk.lines)
            if total > self._max_file_rows:
                raise _Reject("too_large")
            hunks.append(hunk)
        return hunks

    def _read_hunk(self, rows: list[bytes], index: int) -> tuple[_RawHunk, int]:
        match = _HUNK.match(rows[index])
        if match is None:
            raise _Reject("unsupported")
        old_start, old_lines = int(match[1]), int(match[2] or 1)
        new_start, new_lines = int(match[3]), int(match[4] or 1)
        # Only the range: git appends source text from the function around the hunk, which is never
        # masked like the rows are, so it is dropped rather than sent.
        header = match[0].decode("ascii")
        lines: list[DiffLine] = []
        old, new = old_start, new_start
        left_old, left_new = old_lines, new_lines
        index += 1
        while left_old > 0 or left_new > 0:
            if index >= len(rows):
                raise _Reject("unsupported")
            row = rows[index]
            kind = _KINDS.get(row[:1] or b" ")
            if (
                kind is None
                or (kind != "add" and left_old == 0)
                or (kind != "delete" and left_new == 0)
            ):
                raise _Reject("unsupported")
            text = self._text(row[1:])
            if kind == "context":
                lines.append(DiffLine(kind=kind, old=old, new=new, text=text))
                old, new, left_old, left_new = old + 1, new + 1, left_old - 1, left_new - 1
            elif kind == "delete":
                lines.append(DiffLine(kind=kind, old=old, text=text))
                old, left_old = old + 1, left_old - 1
            else:
                lines.append(DiffLine(kind=kind, new=new, text=text))
                new, left_new = new + 1, left_new - 1
            index += 1
            while index < len(rows) and rows[index].startswith(b"\\"):
                lines.append(DiffLine(kind="no_newline", text=self._text(rows[index])))
                index += 1
        return _RawHunk(header, old_start, old_lines, new_start, new_lines, lines), index

    def _text(self, raw: bytes) -> str:
        if len(raw) > self._max_line_bytes:
            raise _Reject("too_large")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raise _Reject("invalid_encoding") from None

    def _paginate(
        self, hunks: list[_RawHunk], file_id: str, snapshot: str, head: _Head
    ) -> tuple[FileDiff, ...]:
        probe = FileDiff(
            file_id=file_id,
            snapshot=snapshot,
            old_mode=head.old_mode,
            new_mode=head.new_mode,
            render="ok",
            hunks=(),
            next="x" * MAX_CURSOR_LEN,
        )
        pager = _Pager(self._max_rows, self._max_bytes - page_size(probe))
        for hunk in hunks:
            pager.add_hunk(hunk)
        pages = pager.finish()
        return tuple(
            replace(probe, hunks=tuple(page), next=None, truncated=forced) for page, forced in pages
        )


class _Pager:
    """Fills pages up to a row and byte budget; a hunk that does not fit continues as a fragment."""

    def __init__(self, max_rows: int, budget: int) -> None:
        self._max_rows = max_rows
        self._budget = budget
        self._pages: list[tuple[list[Hunk], bool]] = []
        self._hunks: list[Hunk] = []
        self._used = 0
        self._rows = 0
        self._forced = False
        self._open: list[DiffLine] = []

    def add_hunk(self, raw: _RawHunk) -> None:
        fragment = 0
        if not raw.lines:
            self._close(raw, fragment)
            return
        for line in raw.lines:
            size = _json_len(line.to_dict())
            if self._rows and (
                self._rows >= self._max_rows
                or self._used + self._cost(raw, fragment, size) > self._budget
            ):
                continued = bool(self._open)
                self._close(raw, fragment)
                self._flush()
                fragment += continued
            self._used += self._cost(raw, fragment, size)
            self._open.append(line)
            self._rows += 1
            if self._rows == 1 and self._used > self._budget:
                self._forced = True
        self._close(raw, fragment)

    def _cost(self, raw: _RawHunk, fragment: int, size: int) -> int:
        if self._open:
            return size + 1
        return size + self._overhead(raw, fragment) + (1 if self._hunks else 0)

    @staticmethod
    def _overhead(raw: _RawHunk, fragment: int) -> int:
        return _json_len(_fragment(raw, fragment, ()).to_dict())

    def _close(self, raw: _RawHunk, fragment: int) -> None:
        if self._open or not raw.lines:
            self._hunks.append(_fragment(raw, fragment, tuple(self._open)))
        self._open = []

    def _flush(self) -> None:
        self._pages.append((self._hunks, self._forced))
        self._hunks, self._used, self._rows, self._forced = [], 0, 0, False

    def finish(self) -> list[tuple[list[Hunk], bool]]:
        if self._hunks or not self._pages:
            self._flush()
        return self._pages


def _fragment(raw: _RawHunk, fragment: int, lines: tuple[DiffLine, ...]) -> Hunk:
    return Hunk(
        header=raw.header,
        old_start=raw.old_start,
        old_lines=raw.old_lines,
        new_start=raw.new_start,
        new_lines=raw.new_lines,
        fragment=fragment,
        lines=lines,
    )
