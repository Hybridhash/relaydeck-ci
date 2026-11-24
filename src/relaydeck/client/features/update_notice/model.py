"""The update notice as the app holds it: one value from `GET /v1/updates`. Flet-free."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

FETCH_INTERVAL_MS = 3_600_000  # foreground returns refetch at most once an hour

# The route is absent (bare gateway) or this session does not hold the scope:
# the section and dot hide. Every other failure keeps the last value silently.
HIDDEN_CODES = frozenset({"not_found", "forbidden", "unauthenticated", "unsupported_capability"})

_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_MOMENT = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


@dataclass(frozen=True)
class UpdateNotice:
    installed: str
    available_version: str | None
    issued: str = ""
    notes: tuple[str, ...] = ()
    check: str = "on"
    last_success: str | None = None
    instructions: tuple[str, ...] = ()

    @property
    def last_checked(self) -> str:
        """The date of the last successful check, or "" when there was none."""
        return self.last_success[:10] if self.last_success else ""


def _strings(value: Any, *, limit: int) -> tuple[str, ...] | None:
    if not isinstance(value, list) or len(value) > limit:
        return None
    if not all(isinstance(item, str) and item for item in value):
        return None
    return tuple(value)


def parse(doc: Any) -> UpdateNotice | None:
    """The typed notice from a schema-valid document; None for anything off-shape."""
    if not isinstance(doc, dict):
        return None
    installed = doc.get("installed")
    if not isinstance(installed, str) or not installed:
        return None
    check = doc.get("check")
    if check not in ("on", "off"):
        return None
    last_success = doc.get("last_success")
    if last_success is not None and (
        not isinstance(last_success, str) or _MOMENT.fullmatch(last_success) is None
    ):
        return None
    instructions = doc.get("instructions")
    lines = () if instructions is None else _strings(instructions, limit=32)
    if lines is None:
        return None
    available = doc.get("available")
    if available is None:
        return UpdateNotice(installed, None, check=check, last_success=last_success)
    if not isinstance(available, dict):
        return None
    version, issued = available.get("version"), available.get("issued")
    notes = _strings(available.get("notes"), limit=12)
    if (
        not isinstance(version, str)
        or _VERSION.fullmatch(version) is None
        or not isinstance(issued, str)
        or _DATE.fullmatch(issued) is None
        or notes is None
    ):
        return None
    return UpdateNotice(
        installed,
        version,
        issued=issued,
        notes=notes,
        check=check,
        last_success=last_success,
        instructions=lines,
    )


def fetch_due(last_fetch_ms: int | None, now_ms: int) -> bool:
    """The first fetch is always due; later ones wait out the hourly interval."""
    return last_fetch_ms is None or now_ms - last_fetch_ms >= FETCH_INTERVAL_MS


def after_error(previous: UpdateNotice | None, code: str) -> UpdateNotice | None:
    """A hidden-code failure clears the notice; anything else keeps it."""
    return None if code in HIDDEN_CODES else previous


def dot_visible(notice: UpdateNotice | None, acknowledged_version: str) -> bool:
    """The rail dot shows while a version newer than the acknowledged one waits."""
    return (
        notice is not None
        and notice.available_version is not None
        and notice.available_version != acknowledged_version
    )
