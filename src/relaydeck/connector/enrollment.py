"""Provision host-local Ed25519 credentials and redeem public-key-bound invitations with proof
of possession. Keep the seed private and verify the gateway's echoed fingerprint.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from relaydeck.contracts import paths
from relaydeck.contracts.enrollment import fingerprint_of_openssh, parse_qr_payload
from relaydeck.contracts.pubkeys import openssh_to_raw
from relaydeck.host.files.descriptors import (
    PrivateFileError,
    create_private,
    open_private_dir,
    read_private_path,
    replace_private,
)
from relaydeck.host.security.enrollment import generate, sign_proof

DEFAULT_CREDS = "~/.config/relaydeck/connector.json"
DEFAULT_KEY = "~/.config/relaydeck/connector_key.json"
# The connector files are owner-only (0600); their folder may be readable by others, as the
# config folder may, but never writable by them.
FOLDER_MASK = 0o022


class EnrollmentError(RuntimeError):
    pass


class UncertainWriteError(OSError):
    """The private file is in place but its folder could not be synced to disk."""


@dataclass(frozen=True)
class ConnectorCreds:
    gateway_url: str
    host_id: str
    host_token: str


@dataclass(frozen=True)
class ConnectorKeys:
    seed_b64: str
    public_openssh: str

    @property
    def fingerprint(self) -> str:
        return fingerprint_of_openssh(self.public_openssh)


def creds_path(path: str = DEFAULT_CREDS) -> Path:
    return Path(os.path.expanduser(path))


def key_path(path: str = DEFAULT_KEY) -> Path:
    return Path(os.path.expanduser(path))


def _write_private(target: Path, body: bytes) -> Path:
    """Create or replace an owner-only file through a pinned parent. The parent is made 0700 if
    missing; one others can write to, a linked path or a swapped file is refused."""
    try:
        dir_fd = open_private_dir(target.parent, create_missing=True, leaf_mask=FOLDER_MASK)
    except PrivateFileError as exc:
        raise EnrollmentError(f"{target.name}: the folder is not private ({exc.code})") from None
    try:
        try:
            replace_private(dir_fd, target.name, body)
        except PrivateFileError as exc:
            if exc.code != "missing":
                raise
            create_private(dir_fd, target.name, body)
    except PrivateFileError as exc:
        raise _refusal(target, exc) from None
    finally:
        os.close(dir_fd)
    return target


def _refusal(target: Path, exc: PrivateFileError) -> Exception:
    if exc.code == "uncertain":
        return UncertainWriteError(f"{target.name}: write failed ({exc.code})")
    if exc.code == "io":
        return OSError(f"{target.name}: write failed ({exc.code})")
    return EnrollmentError(f"{target.name}: refused ({exc.code})")


def _read_private(target: Path) -> bytes | None:
    """The file's bytes, None when it is absent; anything unsafe raises EnrollmentError."""
    try:
        return read_private_path(target, leaf_mask=FOLDER_MASK)
    except PrivateFileError as exc:
        if exc.code == "missing":
            return None
        raise EnrollmentError(f"{target.name}: refused ({exc.code})") from None


def save_creds(creds: ConnectorCreds, path: str = DEFAULT_CREDS) -> Path:
    return _write_private(
        creds_path(path),
        json.dumps(
            {
                "gateway_url": creds.gateway_url,
                "host_id": creds.host_id,
                "host_token": creds.host_token,
            }
        ).encode(),
    )


def load_creds(path: str = DEFAULT_CREDS) -> ConnectorCreds | None:
    target = creds_path(path)
    raw = _read_private(target)
    if raw is None:
        return None
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise EnrollmentError(f"credentials corrupt: {target.name}") from exc
    try:
        return ConnectorCreds(
            gateway_url=str(doc["gateway_url"]),
            host_id=str(doc["host_id"]),
            host_token=str(doc["host_token"]),
        )
    except (KeyError, TypeError) as exc:
        raise EnrollmentError(f"credentials incomplete: {target.name}") from exc


def wipe_creds(path: str = DEFAULT_CREDS) -> bool:
    target = creds_path(path)
    if not target.exists():
        return False
    target.unlink()
    return True


def generate_keys() -> ConnectorKeys:
    seed, public = generate()
    return ConnectorKeys(base64.b64encode(seed).decode(), public)


def save_keys(keys: ConnectorKeys, path: str = DEFAULT_KEY) -> Path:
    return _write_private(
        key_path(path),
        json.dumps({"seed_b64": keys.seed_b64, "public_openssh": keys.public_openssh}).encode(),
    )


def publish_keys(keys: ConnectorKeys, path: str = DEFAULT_KEY) -> ConnectorKeys:
    """Create the key file only if it is absent and return the key that is on disk: this one, or
    the valid key another setup published first. An existing key is never replaced."""
    target = key_path(path)
    body = json.dumps({"seed_b64": keys.seed_b64, "public_openssh": keys.public_openssh}).encode()
    try:
        dir_fd = open_private_dir(target.parent, create_missing=True, leaf_mask=FOLDER_MASK)
    except PrivateFileError as exc:
        raise EnrollmentError(f"{target.name}: the folder is not private ({exc.code})") from None
    try:
        create_private(dir_fd, target.name, body)
    except PrivateFileError as exc:
        if exc.code != "exists":
            raise _refusal(target, exc) from None
        existing = load_keys(path)
        if existing is None:
            raise EnrollmentError(f"{target.name}: refused (missing)") from None
        return existing
    finally:
        os.close(dir_fd)
    return keys


def load_keys(path: str = DEFAULT_KEY) -> ConnectorKeys | None:
    target = key_path(path)
    raw = _read_private(target)
    if raw is None:
        return None
    try:
        doc = json.loads(raw)
        keys = ConnectorKeys(
            seed_b64=str(doc["seed_b64"]), public_openssh=str(doc["public_openssh"])
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise EnrollmentError(f"keys corrupt: {target.name}") from exc
    try:
        seed = base64.b64decode(keys.seed_b64, validate=True)
        _ = keys.fingerprint
        if len(seed) != 32:
            raise ValueError("seed length")
        derived = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
        if derived != openssh_to_raw(keys.public_openssh):
            raise ValueError("public key does not belong to the seed")
    except ValueError as exc:
        raise EnrollmentError(f"keys invalid: {target.name}") from exc
    return keys


def parse_invitation(payload: str) -> tuple[str, str, str, str]:
    """Split a scanned QR reference into (origin, id, code, expiry)."""
    try:
        doc = parse_qr_payload(payload)
    except ValueError as exc:
        raise EnrollmentError(f"bad invitation: {exc}") from exc
    return doc["gw"], doc["id"], doc["code"], doc["exp"]


async def claim_enrollment(
    post_json: Callable[[str, dict[str, Any]], Any],
    gateway_url: str,
    host_id: str,
    invitation_id: str,
    code: str,
    keys: ConnectorKeys,
) -> tuple[str, str]:
    """Redeem an invitation with a proof of possession. Returns (host_token, fingerprint)."""
    if not host_id or not invitation_id or not code:
        raise EnrollmentError("host id, invitation id, and code are required")
    try:
        seed = base64.b64decode(keys.seed_b64, validate=True)
    except ValueError as exc:
        raise EnrollmentError("keys invalid") from exc
    signature = sign_proof(seed, invitation_id, host_id)
    status, payload = await post_json(
        gateway_url.rstrip("/") + paths.enrollment_redeem(invitation_id),
        {
            "code": code,
            "host_id": host_id,
            "pubkey": keys.public_openssh,
            "proof": base64.urlsafe_b64encode(signature).decode().rstrip("="),
        },
    )
    if status != 200 or not isinstance(payload, dict):
        detail = (
            payload.get("message", "enroll failed")
            if isinstance(payload, dict)
            else "enroll failed"
        )
        raise EnrollmentError(str(detail))
    token = payload.get("host_token", "")
    echoed = payload.get("fingerprint", "")
    if not token:
        raise EnrollmentError("gateway issued no token")
    if echoed != keys.fingerprint:
        raise EnrollmentError("gateway echoed a different fingerprint")
    return str(token), str(echoed)
