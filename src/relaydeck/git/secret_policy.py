"""Keeps secrets out of what the Mac sends: a path deny list and masking of key blocks and tokens.

Defence in depth, not a guarantee: it covers well-known shapes only.
"""

from __future__ import annotations

import hashlib
import json
import re
from array import array
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, replace

from relaydeck.contracts.git import DiffLine, FileDiff

BULLET = "•"
POLICY_VERSION = 1  # Bump when path or content masking rules change.

_BEGIN_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")
_END_KEY = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")
_BEGIN_BYTES = re.compile(_BEGIN_KEY.pattern.encode("ascii"))
_END_BYTES = re.compile(_END_KEY.pattern.encode("ascii"))
_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"gh[pousr]_[A-Za-z0-9]{36,}"
    r"|github_pat_[A-Za-z0-9_]{22,}"
    r"|sk-[A-Za-z0-9_-]{20,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])"
    r"|glpat-[A-Za-z0-9_-]{20,}"
    r"|AIza[0-9A-Za-z_-]{35}"
    r"|npm_[A-Za-z0-9]{36}"
    r")"
)


def _components(path: bytes) -> tuple[bytes, ...]:
    return tuple(part for part in path.lower().split(b"/") if part not in (b"", b"."))


def _secret_name(name: bytes) -> bool:
    return (
        name == b".env"
        or name == b"secrets"
        or name.startswith((b".env.", b"id_", b"credentials"))
        or name.endswith((b".pem", b".key", b".p12"))
    )


class SecretPolicy:
    """Withhold configured repository-relative paths and known secret names, matching every
    component case-insensitively. Each withheld directory covers its subtree.
    """

    def __init__(self, configured: Iterable[bytes] = ()) -> None:
        self._configured = tuple(parts for path in configured if (parts := _components(path)))

    @property
    def version(self) -> str:
        paths = [[part.hex() for part in prefix] for prefix in sorted(set(self._configured))]
        identity = json.dumps((POLICY_VERSION, paths), separators=(",", ":")).encode()
        return hashlib.sha256(identity).hexdigest()

    def withheld(self, path: bytes) -> bool:
        parts = _components(path)
        if any(_secret_name(part) for part in parts):
            return True
        return any(parts[: len(prefix)] == prefix for prefix in self._configured)

    def withheld_any(self, *paths: bytes | None) -> bool:
        """True when any side of a change is withheld, e.g. both names of a rename."""
        return any(path is not None and self.withheld(path) for path in paths)


def _scan(text: str, inside: bool) -> tuple[bool, bool]:
    """Return (whole line is key material, still inside a key block afterwards)."""
    if inside:
        return True, _END_KEY.search(text) is None
    begin = _BEGIN_KEY.search(text)
    if begin is None:
        return False, False
    return True, _END_KEY.search(text, begin.end()) is None


class UnsafeKeyContext(ValueError):
    """The complete sides cannot safely establish the patch's secret ranges."""


@dataclass(frozen=True, slots=True)
class _Side:
    """Index one byte buffer; decode only the rows that need verification."""

    raw: bytes
    offsets: array[int]
    starts: tuple[int, ...]
    ends: tuple[int, ...]

    @classmethod
    def read(cls, raw: bytes) -> _Side:
        offsets = array("I", [0]) if raw else array("I")
        newline = raw.find(b"\n")
        while newline >= 0:
            offsets.append(newline + 1)
            newline = raw.find(b"\n", newline + 1)
        if _BEGIN_BYTES.search(raw) is None:
            if _END_BYTES.search(raw) is not None:
                raise UnsafeKeyContext
            return cls(raw, offsets, (), ())
        starts: list[int] = []
        ends: list[int] = []
        expected: bytes | None = None
        for number, start in enumerate(offsets, 1):
            stop = offsets[number] - 1 if number < len(offsets) else len(raw)
            begin, end = _BEGIN_BYTES.search(raw, start, stop), _END_BYTES.search(raw, start, stop)
            if begin is not None:
                if expected is not None or _BEGIN_BYTES.search(raw, begin.end(), stop) is not None:
                    raise UnsafeKeyContext
                expected = begin[0].replace(b"BEGIN", b"END", 1)
                starts.append(number)
            if end is not None:
                if expected != end[0] or (begin is not None and end.start() < begin.end()):
                    raise UnsafeKeyContext
                ends.append(number)
                expected = None
        if expected is not None:
            raise UnsafeKeyContext
        return cls(raw, offsets, tuple(starts), tuple(ends))

    def masks(self, number: int | None, text: str) -> bool:
        if number is None or not 1 <= number <= len(self.offsets):
            raise UnsafeKeyContext
        start = self.offsets[number - 1]
        stop = self.offsets[number] - 1 if number < len(self.offsets) else len(self.raw)
        line = self.raw[start:stop].decode("utf-8", "surrogateescape")
        if line.removesuffix("\r") != text.removesuffix("\r"):
            raise UnsafeKeyContext
        index = bisect_right(self.starts, number) - 1
        return index >= 0 and number <= self.ends[index]


class LineMasker:
    """Masks key blocks and known token shapes with same-length runs of bullets.

    One instance per file diff, fed rows in order: the old and the new side each remember whether a
    key block is open, so a block that starts on one page stays masked on the next.
    """

    def __init__(self) -> None:
        self._inside_old = False
        self._inside_new = False
        self._old_side: _Side | None = None
        self._new_side: _Side | None = None

    @classmethod
    def from_sides(cls, old: bytes, new: bytes) -> LineMasker:
        masker = cls()
        masker._old_side, masker._new_side = _Side.read(old), _Side.read(new)
        return masker

    def mask_line(self, line: DiffLine) -> DiffLine:
        if line.kind == "no_newline":
            return line
        whole = False
        if line.kind != "add":
            if self._old_side is not None:
                found = self._old_side.masks(line.old, line.text)
            else:
                found, self._inside_old = _scan(line.text, self._inside_old)
            whole = whole or found
        if line.kind != "delete":
            if self._new_side is not None:
                found = self._new_side.masks(line.new, line.text)
            else:
                found, self._inside_new = _scan(line.text, self._inside_new)
            whole = whole or found
        text = BULLET * len(line.text) if whole else _TOKEN.sub(_bullets, line.text)
        if not whole and text == line.text:
            return line
        # Marks would show which characters of a secret differ.
        return replace(line, text=text, marks=(), masked=True)

    def mask_page(self, page: FileDiff) -> FileDiff:
        hunks = tuple(
            replace(hunk, lines=tuple(self.mask_line(line) for line in hunk.lines))
            for hunk in page.hunks
        )
        return replace(page, hunks=hunks)

    @classmethod
    def mask_pages(cls, pages: Iterable[FileDiff]) -> tuple[FileDiff, ...]:
        """Mask all pages of one file in order."""
        masker = cls()
        return tuple(masker.mask_page(page) for page in pages)


def _bullets(match: re.Match[str]) -> str:
    return BULLET * len(match[0])
