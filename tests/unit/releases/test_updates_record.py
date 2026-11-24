"""The signed release record: strict loading, the three-way identity and every refusal."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from relaydeck.releases import records as release_records
from tests.support.updates.signing import (
    COMMIT,
    OTHER_COMMIT,
    doc,
    public_blob,
    raw_of,
    sign,
    signed,
)

TODAY = date(2026, 12, 2)
PRIVATE = Ed25519PrivateKey.generate()
KEY = public_blob(PRIVATE)


def verify(raw: bytes, signature: bytes) -> release_records.Record:
    return release_records.verify_record(raw, signature, key=KEY, today=TODAY)


def refused(body: Any) -> str:
    raw = body if isinstance(body, bytes) else raw_of(body)
    signature = sign(PRIVATE, raw, "relaydeck-latest 0.3.1 " + COMMIT)
    with pytest.raises(release_records.UpdateError) as caught:
        verify(raw, signature)
    return caught.value.outcome


def test_a_valid_record_verifies_and_is_read_as_data() -> None:
    raw, signature = signed(PRIVATE)
    record = verify(raw, signature)
    assert (record.version, record.commit, record.issued) == ("0.3.1", COMMIT, "2026-12-01")
    assert record.notes == ("Pairing is faster.", "A fix for Changes.")
    assert record.number == (0, 3, 1)


@pytest.mark.parametrize(
    "change",
    [
        {"schema": "relaydeck.latest/2"},
        {"version": "0.3"},
        {"version": "01.3.1"},
        {"version": "0.3.1-rc1"},
        {"commit": "0123456789ABCDEF0123456789abcdef01234567"},
        {"commit": "0123456789abcdef"},
        {"issued": "2026-13-01"},
        {"issued": "2026-1-1"},
        {"issued": "2026-12-04"},
        {"notes": "not a list"},
        {"notes": [""]},
        {"notes": ["x" * 201]},
        {"notes": ["fine"] * 13},
        {"notes": [3]},
        {"extra": "field"},
    ],
)
def test_each_schema_violation_is_refused(change: dict[str, Any]) -> None:
    assert refused(doc(**change)) == "record"


def test_a_missing_field_is_refused() -> None:
    body = doc()
    del body["notes"]
    assert refused(body) == "record"


@pytest.mark.parametrize("char", ["\t", "\n", "\x1b", "\x85", "‮", "​", " "])
def test_control_and_format_characters_in_notes_are_refused(char: str) -> None:
    assert refused(doc(notes=[f"fine{char}text"])) == "record"


def test_the_longest_allowed_notes_pass() -> None:
    body = doc(notes=["n" * 200] * 12)
    raw = raw_of(body)
    verify(raw, sign(PRIVATE, raw, "relaydeck-latest 0.3.1 " + COMMIT))


def test_a_duplicate_key_is_refused_before_anything_is_trusted() -> None:
    text = json.dumps(doc()).replace('"issued"', '"version": "9.9.9", "issued"', 1)
    assert refused(text.encode()) == "record"


@pytest.mark.parametrize("raw", [b"", b"[]", b"null", b"{", b"\xff\xfe", b'{"version": NaN}'])
def test_garbage_is_refused(raw: bytes) -> None:
    assert refused(raw) == "record"


def test_an_oversize_record_is_refused() -> None:
    raw = raw_of(doc(notes=["n" * 200] * 12)) + b" " * release_records.MAX_RECORD_BYTES
    assert refused(raw) == "record"


def test_a_signature_from_another_key_is_refused() -> None:
    raw = raw_of(doc())
    other = sign(Ed25519PrivateKey.generate(), raw, "relaydeck-latest 0.3.1 " + COMMIT)
    with pytest.raises(release_records.UpdateError) as caught:
        verify(raw, other)
    assert caught.value.outcome == "signature"


def test_a_changed_record_is_refused() -> None:
    raw, signature = signed(PRIVATE)
    with pytest.raises(release_records.UpdateError) as caught:
        verify(raw.replace(b"faster", b"slower"), signature)
    assert caught.value.outcome == "signature"


@pytest.mark.parametrize(
    "comment",
    [
        "relaydeck-latest 0.3.2 " + COMMIT,
        "relaydeck-latest 0.3.1 " + OTHER_COMMIT,
        "relaydeck-web 0.3.1 " + COMMIT,
        "0.3.1",
    ],
)
def test_a_trusted_comment_for_another_release_is_refused(comment: str) -> None:
    raw = raw_of(doc())
    with pytest.raises(release_records.UpdateError) as caught:
        verify(raw, sign(PRIVATE, raw, comment))
    assert caught.value.outcome == "signature"


def test_versions_compare_as_numbers_not_text() -> None:
    assert release_records.parse_version("0.10.0") > release_records.parse_version("0.9.9")  # type: ignore[operator]
    assert release_records.parse_version("0.3.0+unknown") is None
    assert release_records.parse_version("") is None
