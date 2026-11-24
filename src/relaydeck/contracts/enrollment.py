"""Shared enrollment key encodings, public QR references and proof messages. Reusable
credentials never enter a QR payload.
"""

from __future__ import annotations

import base64
import json
import secrets
from typing import Any

from relaydeck.contracts.pubkeys import fingerprint_of_raw, openssh_to_raw

PROOF_DOMAIN = b"relaydeck-enrollment-v1"
QR_PREFIX = "RELAYDECK-ENROLL"
QR_VERSION = 1
MAX_QR_LEN = 480
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LEN = 12


def fingerprint_of_openssh(line: str) -> str:
    """OpenSSH SHA256 fingerprint (`SHA256:...`, tool-compatible)."""
    return fingerprint_of_raw(openssh_to_raw(line))


def mint_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LEN))


def proof_message(enrollment_id: str, host_id: str, raw_pubkey: bytes) -> bytes:
    for field in (enrollment_id, host_id):
        if not field or "\x00" in field or len(field) > 128:
            raise ValueError("bad proof field")
    if len(raw_pubkey) != 32:
        raise ValueError("bad proof key")
    return (
        PROOF_DOMAIN
        + b"\x00"
        + enrollment_id.encode()
        + b"\x00"
        + host_id.encode()
        + b"\x00"
        + raw_pubkey
    )


def build_qr_payload(gateway_origin: str, enrollment_id: str, code: str, expires_at: str) -> str:
    """Bounded public enrollment reference (no reusable secrets)."""
    doc = {
        "gw": gateway_origin,
        "id": enrollment_id,
        "code": code,
        "exp": expires_at,
        "v": QR_VERSION,
    }
    body = (
        base64.urlsafe_b64encode(json.dumps(doc, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    payload = f"{QR_PREFIX}:{QR_VERSION}:{body}"
    if len(payload) > MAX_QR_LEN:
        raise ValueError("qr payload too large")
    return payload


def parse_qr_payload(payload: str) -> dict[str, Any]:
    """Parse and validate a scanned enrollment reference."""
    prefix, sep, rest = payload.partition(":")
    if prefix != QR_PREFIX or not sep:
        raise ValueError("bad qr prefix")
    version, sep, body = rest.partition(":")
    if not sep or version != str(QR_VERSION):
        raise ValueError("unsupported qr version")
    try:
        padded = body + "=" * (-len(body) % 4)
        doc = json.loads(base64.urlsafe_b64decode(padded))
    except ValueError as exc:
        raise ValueError("bad qr encoding") from exc
    if not isinstance(doc, dict) or set(doc) != {"gw", "id", "code", "exp", "v"}:
        raise ValueError("bad qr shape")
    if doc.get("v") != QR_VERSION:
        raise ValueError("bad qr version field")
    origin = str(doc.get("gw", ""))
    if origin and not _origin_ok(origin):
        raise ValueError("bad qr origin")
    if not str(doc.get("id", "")) or not str(doc.get("code", "")):
        raise ValueError("bad qr reference")
    return {k: str(v) for k, v in doc.items()}


def _origin_ok(origin: str) -> bool:
    from urllib.parse import urlparse

    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    if parsed.scheme == "https" and parsed.hostname:
        return True
    if parsed.scheme == "http" and (parsed.hostname or "").lower() in (
        "localhost",
        "127.0.0.1",
        "::1",
    ):
        return True
    return False
