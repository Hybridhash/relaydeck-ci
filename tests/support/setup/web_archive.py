"""A signed web release archive built with a throwaway key; nothing here is a real key."""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from tests.support.companion.web_assets import csp_record

KEY_ID = b"\x21\x22\x23\x24\x25\x26\x27\x28"
GOOD_JS = (
    '"touchend";' * 4
    + '"touchcancel";' * 2
    + '"abort";' * 3
    + '"flet_reconnect_merge_rd1";'
    + '"flet_reconnect_wake_rd1";'
    + '"item_extents";'
    + "var app = 1;"
)


def public_blob(private: Ed25519PrivateKey) -> bytes:
    raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(b"Ed" + KEY_ID + raw)


def _sign(private: Ed25519PrivateKey, data: bytes, comment: str) -> bytes:
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


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(data))


def archive_bytes(version: str) -> bytes:
    base = f"relaydeck-web-{version}"
    raw = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w") as tar,
    ):
        _add(tar, f"{base}/main.dart.js", GOOD_JS.encode())
        _add(tar, f"{base}/index.html", b"<html>app</html>")
        manifest = {
            "schema": "relaydeck.build-manifest/1",
            "version": version,
            "app": {"revision": "b" * 40},
            "web": {"csp": csp_record(b"<html>app</html>")},
        }
        _add(tar, f"{base}.build-manifest.json", json.dumps(manifest).encode())
    return raw.getvalue()


def signed_files(
    folder: Path, version: str, *, signer: Ed25519PrivateKey | None = None
) -> tuple[Path, Path, bytes]:
    """(archive, signature file, the public key that should verify it). A different `signer`
    makes a signature that the returned key rejects."""
    key = Ed25519PrivateKey.generate()
    data = archive_bytes(version)
    name = f"relaydeck-web-{version}.tar.gz"
    archive = folder / name
    archive.write_bytes(data)
    signature = folder / f"{name}.minisig"
    signature.write_bytes(_sign(signer or key, data, name))
    return archive, signature, public_blob(key)
