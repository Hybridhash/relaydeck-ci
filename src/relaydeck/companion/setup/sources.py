"""Usage sources: detected CLIs added to the config, each with the owner's consent."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from relaydeck.companion import config_edit
from relaydeck.companion.inspection.detect import DetectReport
from relaydeck.companion.setup.files import EXIT_BY_STATE, apply_text_result

SOURCE_HISTORY_DEFAULT_DAYS = 90
RECEIPT_DIR = "setup-receipts"


@dataclass(frozen=True)
class SourceCandidate:
    kind: str
    display_name: str
    format: str
    root: str  # absolute, as written to the file
    root_display: str  # with ~ for the home directory, as shown


def _tilde(path: str, home: str) -> str:
    return "~" + path[len(home) :] if home and path.startswith(home) else path


def source_candidates(report: DetectReport, home: str) -> tuple[list[SourceCandidate], list[str]]:
    """The CLIs that can be added (installed, with a reader format, not yet configured), and the
    CLIs that are installed but have no reader yet (listed only; never written)."""
    candidates: list[SourceCandidate] = []
    unsupported: list[str] = []
    for row in report.rows:
        if (
            row.next_step == "can be added"
            and row.reader_root is not None
            and os.path.isdir(row.reader_root)
            and row.adapter != "not yet"
        ):
            root = row.reader_root
            candidates.append(
                SourceCandidate(
                    kind=row.id,
                    display_name=row.display_name,
                    format=row.adapter,
                    root=root,
                    root_display=_tilde(root, home),
                )
            )
        elif row.next_step == "needs an adapter (not supported yet)":
            unsupported.append(row.display_name)
    return candidates, unsupported


def _preview(candidate: SourceCandidate, retention_days: int) -> list[str]:
    return [
        f"{candidate.display_name} ({candidate.kind})",
        f"  kind:    {candidate.kind}",
        f"  format:  {candidate.format}",
        f"  root:    {candidate.root_display}",
        f"  history: up to {retention_days} days",
        "  reads:   token counts and timestamps only; prompts and file contents are never",
        "           stored or sent",
    ]


def offer_sources(
    config_path: Path,
    *,
    confirm: Callable[[str], bool],
    detect_fn: Callable[[Path], DetectReport],
    retention_days: int | None = None,
    emit: Callable[[str], None] | None = None,
) -> tuple[list[str], int]:
    """The interactive source step: one question per CLI, default No.

    `--yes` never reaches this step.
    """
    home = os.path.expanduser("~")
    report = detect_fn(config_path)
    candidates, unsupported = source_candidates(report, home)
    lines: list[str] = []

    def say(line: str) -> None:
        lines.append(line)
        if emit is not None:
            emit(line)

    if unsupported:
        say("Not supported yet (never written): " + ", ".join(unsupported))
    if not candidates:
        say("Usage sources: nothing to add.")
        return lines, 0
    days = retention_days or SOURCE_HISTORY_DEFAULT_DAYS
    try:
        text = config_path.read_text(encoding="utf-8")
        present = _present_sources(text)
    except (OSError, ValueError):
        say("Usage sources: the configuration could not be read; nothing added.")
        return lines, 1
    candidates = [c for c in candidates if (c.kind, c.format) not in present]
    if not candidates:
        say("Usage sources: nothing to add.")
        return lines, 0
    approved: list[SourceCandidate] = []
    for candidate in candidates:
        for line in _preview(candidate, days):
            say(line)
        if confirm(f"Add {candidate.display_name}? [y/N] "):
            approved.append(candidate)
            say("  -> approved")
        else:
            say("  -> not added")
    if not approved:
        return lines, 0
    entries = [config_edit.SourceEntry(c.kind, c.format, c.root, c.display_name) for c in approved]
    new_text = config_edit.append_sources(text, entries)
    result = apply_text_result(
        config_path,
        text,
        new_text,
        action="add",
        entries=[{"kind": c.kind, "format": c.format, "root": c.root_display} for c in approved],
    )
    for line in result.lines:
        say(line)
    return lines, EXIT_BY_STATE[result.state]


def _present_sources(text: str) -> set[tuple[str, str]]:
    """(kind, format) of every usage source already in the file: a rerun never duplicates one."""

    rows = (tomllib.loads(text).get("usage") or {}).get("sources") or []
    return {(row.get("kind"), row.get("format")) for row in rows}


def undo_sources(config_path: Path) -> tuple[list[str], int]:
    """Removes only the sources setup added that are still unchanged. Owner's edits are kept."""
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return ["No configuration file: nothing to undo."], 0
    new_text, removed, kept = config_edit.remove_unchanged(text)
    lines = [f"Setup-added sources kept (changed since setup): {', '.join(kept)}"] if kept else []
    if not removed:
        return [*lines, "No unchanged setup-added sources to undo."], 0
    result = apply_text_result(
        config_path,
        text,
        new_text,
        action="undo",
        entries=[{"kind": kind} for kind in removed],
    )
    return [*lines, f"Removed: {', '.join(removed)}", *result.lines], (EXIT_BY_STATE[result.state])
