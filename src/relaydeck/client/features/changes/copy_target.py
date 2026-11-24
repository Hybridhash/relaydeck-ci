"""What the reader can copy from the resident part of a file's diff. Pure: no Flet."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import relaydeck.client.features.changes.model as m
from relaydeck.contracts.git import LineKind

CopyForm = Literal["code", "diff"]
_SIGNS: dict[LineKind, str] = {"context": " ", "add": "+", "delete": "-", "no_newline": ""}


@dataclass(frozen=True, slots=True)
class CopyTarget:
    file_id: str
    snapshot: str
    page: int
    hunk: int  # position of the hunk (or fragment) on its page
    row: int | None = None  # None: the whole hunk
    form: CopyForm = "code"  # how a whole hunk is copied


@dataclass(frozen=True, slots=True)
class CopyLine:
    page: int
    hunk: int
    row: int
    kind: LineKind
    text: str
    masked: bool
    number: int | None

    @property
    def copyable(self) -> bool:
        return not self.masked and self.kind != "no_newline"


@dataclass(frozen=True, slots=True)
class HunkGroup:
    """The resident lines of one hunk, with every fragment that is held."""

    page: int
    hunk: int
    header: str
    lines: tuple[CopyLine, ...]
    complete: bool

    @property
    def partial(self) -> bool:
        return not self.complete or any(line.masked for line in self.lines)

    @property
    def code(self) -> str:
        """The new version of the hunk's lines, without signs."""
        return "\n".join(
            line.text for line in self.lines if line.copyable and line.kind != "delete"
        )

    @property
    def diff(self) -> str:
        """The header and every visible line with its sign, including the no-newline marker."""
        rows = [_SIGNS[line.kind] + line.text for line in self.lines if not line.masked]
        return "\n".join([self.header, *rows] if self.header else rows)

    def text(self, form: CopyForm) -> str:
        return self.diff if form == "diff" else self.code


@dataclass(slots=True)
class _Open:
    page: int
    hunk: int
    header: str
    whole: bool  # its first fragment is resident
    expected: tuple[int, int]  # old and new line counts the header promises
    last_fragment: int
    lines: list[CopyLine]


def hunk_groups(model: m.ChangesModel) -> tuple[HunkGroup, ...]:
    window = model.diff
    if window is None:
        return ()
    groups: list[_Open] = []
    for page_index, page in window.pages:
        for hunk_index, hunk in enumerate(page.hunks):
            lines = [
                CopyLine(
                    page_index,
                    hunk_index,
                    number,
                    line.kind,
                    line.text,
                    line.masked,
                    line.new if line.kind != "delete" else line.old,
                )
                for number, line in enumerate(hunk.lines)
            ]
            if groups and hunk.fragment > 0 and hunk.fragment == groups[-1].last_fragment + 1:
                groups[-1].lines.extend(lines)
                groups[-1].last_fragment = hunk.fragment
                continue
            expected = (hunk.old_lines, hunk.new_lines)
            groups.append(
                _Open(
                    page_index, hunk_index, hunk.header, hunk.fragment == 0, expected,
                    hunk.fragment, lines,
                )
            )  # fmt: skip
    return tuple(
        HunkGroup(
            g.page,
            g.hunk,
            g.header,
            tuple(g.lines),
            complete=g.whole and _counts(g.lines) == g.expected,
        )
        for g in groups
    )


def _counts(lines: Sequence[CopyLine]) -> tuple[int, int]:
    old = sum(line.kind in ("context", "delete") for line in lines)
    new = sum(line.kind in ("context", "add") for line in lines)
    return old, new


def target_for(
    model: m.ChangesModel, group: HunkGroup, line: CopyLine | None = None, form: CopyForm = "code"
) -> CopyTarget | None:
    window = model.diff
    if window is None or model.selected_file is None:
        return None
    page, hunk, row = (
        (group.page, group.hunk, None) if line is None else (line.page, line.hunk, line.row)
    )
    return CopyTarget(model.selected_file, window.snapshot, page, hunk, row, form)


def resolve(model: m.ChangesModel, target: CopyTarget) -> str | None:
    """The text a target stands for now, or None if its file, snapshot or place is gone."""
    window = model.diff
    if (
        window is None
        or model.selected_file != target.file_id
        or window.snapshot != target.snapshot
    ):
        return None
    for group in hunk_groups(model):
        if target.row is None:
            if (group.page, group.hunk) == (target.page, target.hunk):
                return group.text(target.form) or None
            continue
        for line in group.lines:
            if (line.page, line.hunk, line.row) == (target.page, target.hunk, target.row):
                return line.text if line.copyable else None
    return None
