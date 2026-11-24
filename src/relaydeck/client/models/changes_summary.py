"""The changes line of an agent card, as a value."""

from __future__ import annotations

from dataclasses import dataclass

from relaydeck.contracts.git import WorktreeSummary


@dataclass(frozen=True)
class ChangesRow:
    """Text of a card's changes row. An empty `label` is a row with nothing to say yet."""

    label: str = ""
    branch: str = ""
    dirty: bool = False
    plus: str = ""
    minus: str = ""
    note: str = ""
    blocks: tuple[str, ...] = ()  # five of "add", "delete" or "none": the share of each
    openable: bool = False  # a repository: the row opens Changes and shows a chevron


MINUS = "−"


BLOCKS = 5


_UNAVAILABLE = frozenset({"folder_gone", "unavailable", "unsupported_config", "git_unavailable"})


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _message(text: str, spoken: str) -> ChangesRow:
    return ChangesRow(label=f"Changes: {spoken}. Open changes.", note=text)


def changes_row(summary: WorktreeSummary | None) -> ChangesRow:
    if summary is None:
        return ChangesRow()
    if summary.state == "calculating":
        return _message("Calculating…", "calculating")
    if summary.state == "no_repo":
        return _message("No repository", "no repository")
    if summary.state in _UNAVAILABLE:
        return _message("Changes unavailable", "unavailable")
    return _repository_row(summary)


def _repository_row(summary: WorktreeSummary) -> ChangesRow:
    work = summary.working
    dirty = work.staged + work.unstaged + work.untracked + work.conflicts > 0
    where = summary.short_oid if summary.detached else summary.branch
    if where and summary.detached:
        spoken_where = f"detached at {where}"
    elif where:
        spoken_where = f"branch {where}"
    else:
        spoken_where = "no branch"
    spoken: list[str] = []
    plus = minus = ""
    notes: list[str] = []
    if not dirty:
        notes.append("Clean")
        spoken.append("no uncommitted changes")
        ahead = summary.branch_scope.commits
        if ahead and summary.base_name:
            spoken.append(f"{_plural(ahead, 'commit')} ahead of {summary.base_name}")
    else:
        spoken.append("uncommitted changes")
        if work.additions is not None and work.deletions is not None:
            plus, minus = f"+{work.additions:,}", f"{MINUS}{work.deletions:,}"
            spoken += [
                _plural(work.additions, "line") + " added",
                _plural(work.deletions, "line") + " removed",
            ]
        else:
            notes.append(_plural(work.files, "file"))
            spoken.append(_plural(work.files, "file") + " changed")
        # The strip keeps the branch readable: untracked files are spoken, unmerged ones also shown.
        if work.untracked:
            spoken.append(_plural(work.untracked, "untracked file"))
        if work.conflicts:
            notes.append(f"· {work.conflicts} unmerged")
            spoken.append(_plural(work.conflicts, "unmerged file"))
    if summary.partial:
        spoken.append("counts are incomplete")
    if summary.shared:
        spoken.append("shared checkout")
    return ChangesRow(
        label=f"Changes, {spoken_where}: {', '.join(spoken)}. Open changes.",
        branch=where or "",
        dirty=dirty,
        plus=plus,
        minus=minus,
        note=" ".join(notes),
        blocks=share_blocks(work.additions, work.deletions) if dirty else (),
        openable=True,
    )


def share_blocks(additions: int | None, deletions: int | None) -> tuple[str, ...]:
    """Five blocks split between added and removed lines, each side at least one when present."""
    if additions is None or deletions is None or additions + deletions == 0:
        return ("none",) * BLOCKS
    added = round(BLOCKS * additions / (additions + deletions))
    added = min(max(added, 1 if additions else 0), BLOCKS - (1 if deletions else 0))
    return ("add",) * added + ("delete",) * (BLOCKS - added)
