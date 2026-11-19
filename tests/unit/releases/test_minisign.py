"""The minisign verifier against vectors made by the real CLI with a throwaway key.

Only the test public key, sample payloads and signatures are committed; the
throwaway secret key was deleted after the fixtures were generated.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from relaydeck.releases import minisign as minisign

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "minisign"
ROOT = Path(__file__).resolve().parents[3]


def _key() -> bytes:
    return (FIXTURES / "test.pub").read_bytes()


def _sig(name: str = "sample.bin.minisig") -> bytes:
    return (FIXTURES / name).read_bytes()


def test_a_real_signature_verifies_and_the_comment_is_checked() -> None:
    parsed = minisign.verify(
        (FIXTURES / "sample.bin").read_bytes(), _sig(), _key(), trusted_comment="sample.bin"
    )
    assert parsed.trusted_comment == b"sample.bin"
    with pytest.raises(minisign.MinisignError):
        minisign.verify(
            (FIXTURES / "sample.bin").read_bytes(), _sig(), _key(), trusted_comment="other.bin"
        )


def test_a_default_trusted_comment_verifies_without_an_expectation() -> None:
    parsed = minisign.verify(
        (FIXTURES / "other.bin").read_bytes(), _sig("other.bin.minisig"), _key()
    )
    assert parsed.trusted_comment.startswith(b"timestamp:")


def test_the_packaged_key_is_the_committed_public_key_file() -> None:
    assert minisign.PACKAGED_PUBLIC_KEY == minisign.parse_public_key(
        (ROOT / "minisign.pub").read_bytes()
    )
    assert minisign.PACKAGED_PUBLIC_KEY.key_id[::-1].hex().upper() == "BFF2B17C3DAB0B82"


def test_a_wrong_key_id_is_refused() -> None:
    with pytest.raises(minisign.MinisignError):
        minisign.verify(
            (FIXTURES / "sample.bin").read_bytes(), _sig(), minisign.PACKAGED_PUBLIC_KEY
        )


def _mutate_line(data: bytes, index: int, replacement: bytes) -> bytes:
    lines = data.split(b"\n")
    lines[index] = replacement
    return b"\n".join(lines)


def test_one_changed_byte_anywhere_is_refused() -> None:
    payload = bytearray((FIXTURES / "sample.bin").read_bytes())
    payload[0] ^= 1
    with pytest.raises(minisign.MinisignError):
        minisign.verify(bytes(payload), _sig(), _key())
    lines = _sig().split(b"\n")
    blob = bytearray(base64.b64decode(lines[1]))
    blob[-1] ^= 1
    with pytest.raises(minisign.MinisignError):
        minisign.verify(
            (FIXTURES / "sample.bin").read_bytes(),
            _mutate_line(_sig(), 1, base64.b64encode(bytes(blob))),
            _key(),
        )
    global_sig = bytearray(base64.b64decode(lines[3]))
    global_sig[0] ^= 1
    with pytest.raises(minisign.MinisignError):
        minisign.verify(
            (FIXTURES / "sample.bin").read_bytes(),
            _mutate_line(_sig(), 3, base64.b64encode(bytes(global_sig))),
            _key(),
        )


def test_a_legacy_algorithm_is_refused() -> None:
    lines = _sig().split(b"\n")
    blob = bytearray(base64.b64decode(lines[1]))
    blob[:2] = b"Ed"  # legacy (unhashed) signatures are never accepted
    with pytest.raises(minisign.MinisignError):
        minisign.verify(
            (FIXTURES / "sample.bin").read_bytes(),
            _mutate_line(_sig(), 1, base64.b64encode(bytes(blob))),
            _key(),
        )


@pytest.mark.parametrize(
    "broken",
    [
        b"",
        b"not a signature\n",
        b"untrusted comment: x\n!!!!\ntrusted comment: y\nAAAA\n",
        b"untrusted comment: x\n"
        + base64.b64encode(b"ED" + b"\x00" * 10)
        + b"\ntrusted: y\nAAAA\n",
    ],
)
def test_malformed_signatures_are_refused(broken: bytes) -> None:
    with pytest.raises(minisign.MinisignError):
        minisign.verify(b"payload", broken, _key())


def test_extra_lines_control_characters_and_oversize_are_refused() -> None:
    with pytest.raises(minisign.MinisignError):
        minisign.verify(b"payload", _sig() + b"extra line\n", _key())
    with pytest.raises(minisign.MinisignError):
        minisign.verify(b"payload", _sig().replace(b"sample.bin", b"sam\x00ple.bin"), _key())
    with pytest.raises(minisign.MinisignError):
        minisign.verify(b"payload", b"x" * (minisign.MAX_SIGNATURE_BYTES + 1), _key())
    with pytest.raises(minisign.MinisignError):
        minisign.parse_public_key(b"x" * (minisign.MAX_PUBLIC_KEY_BYTES + 1))


def test_a_truncated_signature_is_refused() -> None:
    with pytest.raises(minisign.MinisignError):
        minisign.verify((FIXTURES / "sample.bin").read_bytes(), _sig()[:120], _key())
