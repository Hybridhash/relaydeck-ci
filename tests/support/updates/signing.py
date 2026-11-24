"""Throwaway minisign keys and signed release records for tests; nothing here is a real key."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from relaydeck.releases import records as release_records

KEY_ID = b"\x0a\x0b\x0c\x0d\x0e\x0f\x10\x11"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
OTHER_COMMIT = "fedcba9876543210fedcba9876543210fedcba98"


def public_blob(private: Ed25519PrivateKey) -> bytes:
    raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(b"Ed" + KEY_ID + raw)


def sign(private: Ed25519PrivateKey, data: bytes, comment: str) -> bytes:
    digest = hashlib.blake2b(data, digest_size=64).digest()
    signature = private.sign(digest)
    global_sig = private.sign(signature + comment.encode())
    return b"\n".join(
        [
            b"untrusted comment: signature from minisign secret key",
            base64.b64encode(b"ED" + KEY_ID + signature),
            f"trusted comment: {comment}".encode(),
            base64.b64encode(global_sig),
            b"",
        ]
    )


def doc(version: str = "0.3.1", **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema": release_records.SCHEMA,
        "version": version,
        "commit": COMMIT,
        "issued": "2026-12-01",
        "notes": ["Pairing is faster.", "A fix for Changes."],
    }
    body.update(over)
    return body


def raw_of(body: Mapping[str, Any]) -> bytes:
    return json.dumps(body).encode()


def signed(
    private: Ed25519PrivateKey, body: Mapping[str, Any] | None = None, comment: str | None = None
) -> tuple[bytes, bytes]:
    """(record bytes, signature file) with the trusted comment the record itself implies."""
    body = body if body is not None else doc()
    raw = raw_of(body)
    note = comment or f"relaydeck-latest {body.get('version')} {body.get('commit')}"
    return raw, sign(private, raw, note)


class Response:
    def __init__(
        self, status: int, body: bytes = b"", headers: Mapping[str, str] | None = None
    ) -> None:
        self.status_code = status
        self.headers: Mapping[str, str] = headers or {}
        self._body = body

    def chunks(self) -> Iterable[bytes]:
        yield self._body

    def close(self) -> None:
        pass


class Http:
    """Serves canned responses by URL and records every request."""

    def __init__(self, routes: Mapping[str, Response]) -> None:
        self.routes = dict(routes)
        self.calls: list[str] = []

    def get(self, url: str, *, timeout: float) -> Response:
        self.calls.append(url)
        return self.routes[url]
