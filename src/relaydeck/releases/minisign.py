"""Verify prehashed minisign ED signatures, including key ID and the global trusted-comment
signature, with the packaged public key. Reject malformed or oversized input.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MAX_PUBLIC_KEY_BYTES = 1024
MAX_SIGNATURE_BYTES = 4096

# The release signing key, the same value as the `minisign.pub` file in the
# repository root (a test asserts they match).
PACKAGED_PUBLIC_KEY_B64 = "RWSCC6s9fLHyv+Bb77GuBxWXRp+ZD8egQsZMh6IIH65WpiEZPKRjXIro"

_PUBLIC_ALG = b"Ed"  # the first two decoded bytes of a public key blob
_PREHASHED_ALG = b"ED"


class MinisignError(ValueError):
    """A key or signature does not parse, or does not verify. Fixed messages only."""


@dataclass(frozen=True, slots=True)
class PublicKey:
    key_id: bytes  # the 8 raw bytes from the key blob
    key: bytes  # the 32-byte Ed25519 public key


@dataclass(frozen=True, slots=True)
class Signature:
    key_id: bytes
    signature: bytes  # the 64-byte signature over the prehashed message
    trusted_comment: bytes
    global_signature: bytes  # over signature + trusted_comment


def _clean(data: bytes | str, limit: int) -> bytes:
    raw = data.encode() if isinstance(data, str) else bytes(data)
    if not raw or len(raw) > limit:
        raise MinisignError("oversized input")
    if any(byte < 0x20 and byte not in (0x09, 0x0A) or byte == 0x7F for byte in raw):
        raise MinisignError("control characters in input")
    return raw


def _b64(text: bytes, expected: int) -> bytes:
    try:
        decoded = base64.b64decode(text.strip(), validate=True)
    except (ValueError, TypeError) as exc:
        raise MinisignError("malformed base64") from exc
    if len(decoded) != expected:
        raise MinisignError("unexpected length")
    return decoded


def _lines(raw: bytes) -> list[bytes]:
    lines = raw.split(b"\n")
    while lines and lines[-1] == b"":
        lines.pop()
    return [line.rstrip(b"\r") for line in lines]


def parse_public_key(data: bytes | str) -> PublicKey:
    """Parse a public-key file, base64 line or raw 42-byte blob with an Ed prefix, key ID and
    public key.
    """
    if isinstance(data, bytes) and len(data) == 42 and data[:2] == _PUBLIC_ALG:
        return PublicKey(key_id=data[2:10], key=data[10:])
    lines = _lines(_clean(data, MAX_PUBLIC_KEY_BYTES))
    if lines and lines[0].startswith(b"untrusted comment:"):
        lines = lines[1:]
    if len(lines) != 1:
        raise MinisignError("unexpected public key shape")
    blob = _b64(lines[0], 42)
    if blob[:2] != _PUBLIC_ALG:
        raise MinisignError("unsupported public key algorithm")
    return PublicKey(key_id=blob[2:10], key=blob[10:])


PACKAGED_PUBLIC_KEY = parse_public_key(PACKAGED_PUBLIC_KEY_B64)


def parse_signature(data: bytes | str) -> Signature:
    """Parse a `.minisig` file. The untrusted comment line is skipped, never read."""
    lines = _lines(_clean(data, MAX_SIGNATURE_BYTES))
    if lines and lines[0].startswith(b"untrusted comment:"):
        lines = lines[1:]
    if len(lines) != 3:
        raise MinisignError("unexpected signature shape")
    blob = _b64(lines[0], 74)
    if blob[:2] != _PREHASHED_ALG:
        raise MinisignError("unsupported signature algorithm")
    trusted = lines[1]
    if not trusted.startswith(b"trusted comment:"):
        raise MinisignError("missing trusted comment")
    comment = trusted[len(b"trusted comment:") :].strip()
    if not comment:
        raise MinisignError("empty trusted comment")
    return Signature(
        key_id=blob[2:10],
        signature=blob[10:],
        trusted_comment=comment,
        global_signature=_b64(lines[2], 64),
    )


def _resolve_key(public_key: PublicKey | bytes | str | None) -> PublicKey:
    if public_key is None:
        return PACKAGED_PUBLIC_KEY
    if isinstance(public_key, PublicKey):
        return public_key
    return parse_public_key(public_key)


def verify_prehashed(
    digest: bytes,
    signature_file: bytes | str,
    public_key: PublicKey | bytes | str | None = None,
    *,
    trusted_comment: str | None = None,
) -> Signature:
    """Verify against a BLAKE2b-512 digest of the message. Returns the parsed
    signature; raises MinisignError on any mismatch."""
    if len(digest) != 64:
        raise MinisignError("unexpected digest length")
    key = _resolve_key(public_key)
    signature = parse_signature(signature_file)
    if signature.key_id != key.key_id:
        raise MinisignError("key id mismatch")
    if trusted_comment is not None and signature.trusted_comment != trusted_comment.encode():
        raise MinisignError("trusted comment mismatch")
    verifier = Ed25519PublicKey.from_public_bytes(key.key)
    try:
        verifier.verify(signature.signature, digest)
        verifier.verify(signature.global_signature, signature.signature + signature.trusted_comment)
    except InvalidSignature as exc:
        raise MinisignError("signature does not verify") from exc
    return signature


def verify(
    message: bytes,
    signature_file: bytes | str,
    public_key: PublicKey | bytes | str | None = None,
    *,
    trusted_comment: str | None = None,
) -> Signature:
    """Verify a whole message held in memory (tests and small inputs)."""
    digest = hashlib.blake2b(message, digest_size=64).digest()
    return verify_prehashed(digest, signature_file, public_key, trusted_comment=trusted_comment)
