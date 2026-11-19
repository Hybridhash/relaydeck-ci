"""Sign and verify connector enrollment proofs using host-local Ed25519 seeds and gateway-bound
public keys. Fingerprints use the OpenSSH SHA256 form.
"""

from __future__ import annotations

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from relaydeck.contracts.enrollment import proof_message
from relaydeck.contracts.pubkeys import _ssh_blob, openssh_to_raw


def generate() -> tuple[bytes, str]:
    """Fresh Ed25519 identity: (32-byte seed, OpenSSH public line)."""
    private = Ed25519PrivateKey.generate()
    seed = private.private_bytes_raw()
    public = private.public_key().public_bytes_raw()
    blob = _ssh_blob(public)
    line = f"ssh-ed25519 {base64.b64encode(blob).decode()}"
    return seed, line


def sign_proof(seed: bytes, enrollment_id: str, host_id: str) -> bytes:
    if len(seed) != 32:
        raise ValueError("ed25519 seed is 32 bytes")
    private = Ed25519PrivateKey.from_private_bytes(seed)
    raw = private.public_key().public_bytes_raw()
    return private.sign(proof_message(enrollment_id, host_id, raw))


def verify_proof(openssh_pubkey: str, enrollment_id: str, host_id: str, signature: bytes) -> bool:
    try:
        raw = openssh_to_raw(openssh_pubkey)
        Ed25519PublicKey.from_public_bytes(raw).verify(
            signature, proof_message(enrollment_id, host_id, raw)
        )
    except (ValueError, InvalidSignature):
        return False
    return True
