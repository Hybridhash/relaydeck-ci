"""On-demand plan limits blocks, offered with the owner's consent."""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from relaydeck.companion import config_edit
from relaydeck.companion.inspection.detect import DetectReport
from relaydeck.companion.setup.files import EXIT_BY_STATE, apply_text_result


@dataclass(frozen=True)
class LimitsCandidate:
    kind: str
    display_name: str
    format: str


def limits_candidates(report: DetectReport, source_kinds: set[str]) -> list[LimitsCandidate]:
    """Installed CLIs with a TUI limits recipe whose kind is already a usage
    source. The tool binary must be present (its screen is read); the block
    itself resolves Herdr at runtime."""
    candidates: list[LimitsCandidate] = []
    for row in report.rows:
        if row.limits == "tui-usage-pane-v1" and row.binary is not None and row.id in source_kinds:
            candidates.append(
                LimitsCandidate(kind=row.id, display_name=row.display_name, format=row.limits)
            )
    return candidates


def _preview_limits(candidate: LimitsCandidate) -> list[str]:
    return [
        f"{candidate.display_name} ({candidate.kind}) plan limits",
        f"  kind:    {candidate.kind}",
        f"  format:  {candidate.format}",
        "  reads:   a manual refresh opens a temporary tool tab, reads the",
        "           /usage screen numbers, then quits and closes the tab. Only",
        "           numbers, reset times and the plan label are kept.",
    ]


def _present_limits(text: str) -> set[tuple[str, str]]:
    """(kind, format) of every limits block already in the file."""

    rows = (tomllib.loads(text).get("usage") or {}).get("limits") or []
    return {(row.get("kind"), row.get("format")) for row in rows}


def offer_limits(
    config_path: Path,
    *,
    confirm: Callable[[str], bool],
    detect_fn: Callable[[Path], DetectReport],
    emit: Callable[[str], None] | None = None,
) -> tuple[list[str], int]:
    """The interactive limits step: one question per CLI, default No.

    `--yes` never reaches this step.
    """
    report = detect_fn(config_path)
    lines: list[str] = []

    def say(line: str) -> None:
        lines.append(line)
        if emit is not None:
            emit(line)

    try:
        text = config_path.read_text(encoding="utf-8")
        present = _present_limits(text)
        import tomllib

        rows = (tomllib.loads(text).get("usage") or {}).get("sources") or []
        source_kinds = {
            row["kind"]
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("kind"), str)
        }
    except (OSError, ValueError):
        say("Plan limits: the configuration could not be read; nothing added.")
        return lines, 1
    candidates = [
        c for c in limits_candidates(report, source_kinds) if (c.kind, c.format) not in present
    ]
    if not candidates:
        say("Plan limits: nothing to add.")
        return lines, 0
    approved: list[LimitsCandidate] = []
    for candidate in candidates:
        for line in _preview_limits(candidate):
            say(line)
        if confirm(f"Add {candidate.display_name} plan limits? [y/N] "):
            approved.append(candidate)
            say("  -> approved")
        else:
            say("  -> not added")
    if not approved:
        return lines, 0
    entries = [config_edit.LimitsEntry(c.kind, c.format) for c in approved]
    new_text = config_edit.append_limits(text, entries)
    result = apply_text_result(
        config_path,
        text,
        new_text,
        action="add-limits",
        entries=[{"kind": c.kind, "format": c.format} for c in approved],
    )
    for line in result.lines:
        say(line)
    return lines, EXIT_BY_STATE[result.state]
