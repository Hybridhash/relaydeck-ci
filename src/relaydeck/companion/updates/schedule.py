"""Persisted update notices and check scheduling."""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from relaydeck.host.files.descriptors import PrivateFileError, read_private_path
from relaydeck.host.files.private import write_json
from relaydeck.releases.records import (
    Record,
    UpdateError,
    offered,
    parse_version,
    record_from_doc,
    strict_load,
)

FIRST_DELAY_S = 120


TICK_S = 600


DAY_S = 24 * 3600


RETRY_S = 6 * 3600


JITTER_MAX_S = 3600


STALE_AFTER_S = 60 * DAY_S


ROLLBACK_SLACK_S = 300


ROLLBACK_RECHECK_S = 600


STATE_FILE = "update.json"


MAX_TIMESTAMP = 253_402_300_799


_OUTCOMES = frozenset(
    {"ok", "network", "signature", "record", "version", "conflict", "older", "state", "never"}
)


@dataclass(frozen=True)
class State:
    next_due: int | None = None
    last_attempt: int | None = None
    last_success: int | None = None
    outcome: str = "never"
    record: Record | None = None
    jitter: int = 0
    failures: int = 0  # consecutive network failures

    def as_doc(self) -> dict[str, Any]:
        return {
            "next_due": self.next_due,
            "last_attempt": self.last_attempt,
            "last_success": self.last_success,
            "outcome": self.outcome,
            "record": None if self.record is None else self.record.as_doc(),
            "jitter": self.jitter,
            "failures": self.failures,
        }


def _opt_int(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= MAX_TIMESTAMP:
        raise ValueError("bad timestamp")
    return value


def state_from_doc(doc: object) -> State:
    keys = {"next_due", "last_attempt", "last_success", "outcome", "record", "jitter", "failures"}
    if not isinstance(doc, dict) or set(doc) != keys:
        raise ValueError("bad state")
    jitter, failures, outcome = doc["jitter"], doc["failures"], doc["outcome"]
    if (
        type(jitter) is not int
        or not 0 <= jitter <= JITTER_MAX_S
        or type(failures) is not int
        or not 0 <= failures <= 1_000
        or not isinstance(outcome, str)
        or outcome not in _OUTCOMES
    ):
        raise ValueError("bad state")
    try:
        record = None if doc["record"] is None else record_from_doc(doc["record"], today=None)
    except UpdateError:
        raise ValueError("bad record") from None
    return State(
        _opt_int(doc["next_due"]),
        _opt_int(doc["last_attempt"]),
        _opt_int(doc["last_success"]),
        outcome,
        record,
        jitter,
        failures,
    )


def load_state(state_dir: Path) -> State:
    """The stored state; absent means never checked, unreadable or invalid means reset with
    outcome `state`. Read-only: nothing is created."""
    try:
        raw = read_private_path(state_dir / STATE_FILE, limit=64 * 1024)
    except PrivateFileError as exc:
        return State() if exc.code == "missing" else State(outcome="state")
    except (OSError, ValueError):
        return State(outcome="state")
    try:
        return state_from_doc(strict_load(raw))
    except (ValueError, TypeError, OverflowError):  # UnicodeDecodeError is a ValueError
        return State(outcome="state")


def save_state(state_dir: Path, state: State) -> None:
    """Atomic replace through the private-file helpers. Raises OSError when it cannot."""
    write_json(state_dir / STATE_FILE, state.as_doc(), pinned=True)


def repair(state: State, now: int) -> State:
    """Clamp timestamps that cannot be true. A `last_attempt` far ahead of now means the clock
    went back: look again in ten minutes.
    """
    attempt, success, due = state.last_attempt, state.last_success, state.next_due
    if success is not None and success > now:
        success = now
    if attempt is not None and attempt > now + ROLLBACK_SLACK_S:
        return replace(
            state, last_attempt=now, last_success=success, next_due=now + ROLLBACK_RECHECK_S
        )
    if attempt is not None and attempt > now:
        attempt = now
    if due is not None and attempt is not None:
        due = min(due, attempt + DAY_S + JITTER_MAX_S)
    return replace(state, last_attempt=attempt, last_success=success, next_due=due)


def is_due(state: State, now: int) -> bool:
    return state.next_due is None or state.next_due <= now


def with_jitter(state: State, rng: Callable[[int, int], int] = random.randrange) -> State:
    """The jitter offset is created once, on the first write."""
    if state.jitter or state.last_attempt is not None:
        return state
    return replace(state, jitter=rng(0, JITTER_MAX_S + 1))


def begin_attempt(state: State, now: int) -> State:
    """What is persisted before the network work starts, so a crash or restart never causes an
    immediate re-check."""
    return replace(state, last_attempt=now, next_due=now + DAY_S + state.jitter)


def finish_attempt(state: State, now: int, outcome: str, record: Record | None = None) -> State:
    """The state after an attempt. `record` is the stored record to keep (the new high-water mark
    on `ok`)."""
    stored = record if record is not None else state.record
    if outcome in ("network", "conflict"):
        failures = state.failures + 1
        wait = RETRY_S if failures == 1 else DAY_S
    else:
        failures = 0
        wait = DAY_S + state.jitter
    return replace(
        state,
        outcome=outcome,
        record=stored,
        failures=failures,
        next_due=now + wait,
        last_success=now if outcome == "ok" else state.last_success,
    )


@dataclass(frozen=True)
class Notice:
    """An immutable view of the last published result."""

    installed: str
    check: bool
    available: Record | None
    last_attempt: int | None
    last_success: int | None
    outcome: str


def notice_from(state: State, installed: str, check: bool) -> Notice:
    number = parse_version(installed)
    return Notice(
        installed=installed,
        check=check,
        available=offered(state.record, number) if check else None,
        last_attempt=state.last_attempt,
        last_success=state.last_success,
        outcome=state.outcome,
    )


def moment(seconds: int | None) -> str | None:
    if seconds is None or not 0 <= seconds <= MAX_TIMESTAMP:
        return None
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def status_object(notice: Notice) -> dict[str, Any]:
    """The additive `update` object of `relaydeck status` and `relaydeck doctor`."""
    found = notice.available
    return {
        "installed": notice.installed,
        "available": None
        if found is None
        else {
            "version": found.version,
            "commit": found.commit,
            "issued": found.issued,
            "notes": list(found.notes),
        },
        "check": "on" if notice.check else "off",
        "last_attempt": moment(notice.last_attempt),
        "last_success": moment(notice.last_success),
        "outcome": None if notice.outcome == "never" else notice.outcome,
    }


def skip_reason(installed: str, environ: Mapping[str, str]) -> str | None:
    """`version` when this install cannot be compared (unknown version) or is a developer run."""
    if parse_version(installed) is None or environ.get("FLET_WEB_PATH"):
        return "version"
    return None
