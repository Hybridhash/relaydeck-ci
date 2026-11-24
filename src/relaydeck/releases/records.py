"""Signed release records and verification."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from relaydeck.releases import minisign
from relaydeck.releases import web as webrelease

LATEST_URL = "https://github.com/Hybridhash/relaydeck/releases/latest/download/latest.json"


SCHEMA = "relaydeck.latest/1"


MAX_RECORD_BYTES = 4096


MAX_NOTES = 12


MAX_NOTE_CHARS = 200


DEADLINE_S = 20.0


_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


_COMMIT = re.compile(r"[0-9a-f]{40}")


_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


_FORBIDDEN = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


class UpdateError(Exception):
    """A check ended without a verified record; `outcome` is one fixed code."""

    def __init__(self, outcome: str) -> None:
        super().__init__(outcome)
        self.outcome = outcome


@dataclass(frozen=True)
class Record:
    version: str
    commit: str
    issued: str
    notes: tuple[str, ...]

    @property
    def number(self) -> tuple[int, int, int]:
        parsed = parse_version(self.version)
        assert parsed is not None  # noqa: S101 - validated on construction paths
        return parsed

    def as_doc(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "version": self.version,
            "commit": self.commit,
            "issued": self.issued,
            "notes": list(self.notes),
        }


def parse_version(text: object) -> tuple[int, int, int] | None:
    if not isinstance(text, str) or _VERSION.fullmatch(text) is None:
        return None
    major, minor, patch = (int(part) for part in text.split("."))
    return major, minor, patch


def trusted_comment(version: str, commit: str) -> str:
    return f"relaydeck-latest {version} {commit}"


def signature_url(version: str) -> str:
    return f"{webrelease.RELEASE_BASE}/v{version}/latest.json.minisig"


def strict_load(raw: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        keys = [key for key, _ in items]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate key")
        return dict(items)

    def refuse(_name: str) -> Any:
        raise ValueError("not a JSON number")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=refuse)
    except RecursionError:  # absurd nesting is malformed input like any other
        raise ValueError("nested too deeply") from None


def _clean_note(note: object) -> bool:
    return (
        isinstance(note, str)
        and 1 <= len(note) <= MAX_NOTE_CHARS
        and not any(unicodedata.category(char) in _FORBIDDEN for char in note)
    )


def record_from_doc(doc: object, *, today: date | None) -> Record:
    """The record a document describes, or UpdateError('record'). `today` bounds `issued` (one day
    ahead at most); None skips that bound, for a record already verified and stored."""
    if not isinstance(doc, dict) or set(doc) != {"schema", "version", "commit", "issued", "notes"}:
        raise UpdateError("record")
    version, commit, issued, notes = doc["version"], doc["commit"], doc["issued"], doc["notes"]
    if (
        doc["schema"] != SCHEMA
        or parse_version(version) is None
        or not isinstance(commit, str)
        or _COMMIT.fullmatch(commit) is None
        or not isinstance(issued, str)
        or _DATE.fullmatch(issued) is None
        or not isinstance(notes, list)
        or len(notes) > MAX_NOTES
        or not all(_clean_note(note) for note in notes)
    ):
        raise UpdateError("record")
    try:
        day = date.fromisoformat(issued)
    except ValueError:
        raise UpdateError("record") from None
    if today is not None and day > today + timedelta(days=1):
        raise UpdateError("record")
    return Record(version, commit, issued, tuple(notes))


def locate(raw: bytes) -> tuple[str, str]:
    """(version, commit) from record bytes that are NOT yet verified: only a locator for the
    signature file and the expected trusted comment, never shown or stored."""
    if len(raw) > MAX_RECORD_BYTES:
        raise UpdateError("record")
    try:
        doc = strict_load(raw)
    except (ValueError, UnicodeDecodeError):
        raise UpdateError("record") from None
    version = doc.get("version") if isinstance(doc, dict) else None
    commit = doc.get("commit") if isinstance(doc, dict) else None
    if parse_version(version) is None or not isinstance(commit, str):
        raise UpdateError("record")
    if _COMMIT.fullmatch(commit) is None:
        raise UpdateError("record")
    return str(version), commit


def verify_record(
    raw: bytes,
    signature: bytes,
    *,
    key: bytes | str | None = None,
    today: date | None = None,
) -> Record:
    """Verify the raw bytes first, then parse. The signature, the trusted comment and the record
    must all name the same version and commit."""
    version, commit = locate(raw)
    try:
        minisign.verify(raw, signature, key, trusted_comment=trusted_comment(version, commit))
    except minisign.MinisignError:
        raise UpdateError("signature") from None
    try:
        doc = strict_load(raw)
    except (ValueError, UnicodeDecodeError):
        raise UpdateError("record") from None
    record = record_from_doc(doc, today=today or datetime.now(UTC).date())
    if (record.version, record.commit) != (version, commit):
        raise UpdateError("record")
    return record


def fetch_record(
    *,
    http: webrelease.HttpGetter | None = None,
    clock: Callable[[], float],
    key: bytes | str | None = None,
    today: date | None = None,
) -> Record:
    """Download latest.json and its signature (20 s in all), then verify. Raises UpdateError."""
    download = webrelease._Download(http or webrelease._HttpxGetter(clock=clock), clock, DEADLINE_S)
    raw = bytearray()
    signature = bytearray()

    def take(into: bytearray, limit: int) -> Callable[[bytes], None]:
        def sink(chunk: bytes) -> None:
            into.extend(chunk)
            if len(into) > limit:
                raise webrelease.ReleaseError("too_large")

        return sink

    try:
        download.fetch_to(LATEST_URL, take(raw, MAX_RECORD_BYTES))
    except webrelease.ReleaseError as exc:
        raise UpdateError("record" if exc.reason == "too_large" else "network") from None
    version, _commit = locate(bytes(raw))
    try:
        download.fetch_to(signature_url(version), take(signature, webrelease.MAX_SIGNATURE_BYTES))
    except webrelease.ReleaseError as exc:
        raise UpdateError("signature" if exc.reason == "too_large" else "network") from None
    return verify_record(bytes(raw), bytes(signature), key=key, today=today)


def decide(stored: Record | None, fetched: Record) -> str:
    """store | refresh | conflict | older: how a freshly verified record relates to the stored
    high-water mark."""
    if stored is None or fetched.number > stored.number:
        return "store"
    if fetched.number < stored.number:
        return "older"
    return "refresh" if fetched.commit == stored.commit else "conflict"


def offered(stored: Record | None, installed: tuple[int, int, int] | None) -> Record | None:
    """The stored record, only while it is newer than what is installed."""
    if stored is None or installed is None or stored.number <= installed:
        return None
    return stored
