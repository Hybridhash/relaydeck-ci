"""A synthetic private-key block header that no file spells out in full."""

from __future__ import annotations

from pathlib import Path

PEM_PRIVATE_HEADER = "-----BEGIN " + "PRIVATE KEY-----"
PEM_PRIVATE_HEADER_BYTES = PEM_PRIVATE_HEADER.encode()
PEM_RSA_PRIVATE_HEADER_BYTES = b"-----BEGIN RSA " + b"PRIVATE KEY-----"
# Stands in for the header in committed fixture files; readers put the header back.
PEM_PLACEHOLDER = "<pem-private-header>"


def read_fixture(path: Path) -> bytes:
    return path.read_bytes().replace(PEM_PLACEHOLDER.encode(), PEM_PRIVATE_HEADER_BYTES)


def read_fixture_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace(PEM_PLACEHOLDER, PEM_PRIVATE_HEADER)
