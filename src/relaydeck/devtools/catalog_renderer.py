"""Verify an explicitly selected local renderer without invoking SDK tools."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

FLET_VERSION = "1.0.3"
FLUTTER_VERSION = "3.44.8"


def verify(renderer: Path, manifest: Path) -> str:
    data = json.loads(manifest.read_text())
    if data.get("schema") != "relaydeck.build-manifest/1":
        raise ValueError("Unrecognised renderer build manifest")
    if data.get("flet", {}).get("version") != FLET_VERSION:
        raise ValueError("Renderer must use Flet " + FLET_VERSION)
    if data.get("flutter_sdk", {}).get("flutter_version") != FLUTTER_VERSION:
        raise ValueError("Renderer must use Flutter " + FLUTTER_VERSION)
    output = data.get("web_output", {})
    bundle = renderer / "main.dart.js"
    if not (renderer / "index.html").is_file() or not bundle.is_file():
        raise ValueError("Selected renderer is missing its web entry or bundle")
    if hashlib.sha256(bundle.read_bytes()).hexdigest() != output.get("main_dart_js", {}).get(
        "sha256"
    ):
        raise ValueError("Renderer bundle differs from its manifest")
    rows = []
    for path in sorted(renderer.rglob("*")):
        if path.is_symlink():
            raise ValueError("Renderer must be a self-contained directory without symlinks")
        if path.is_file():
            rows.append(
                f"{path.relative_to(renderer).as_posix()}\0"
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}\n"
            )
    digest = hashlib.sha256("".join(rows).encode()).hexdigest()
    if digest != output.get("tree_sha256"):
        raise ValueError("Renderer tree differs from its manifest")
    markers = output.get("repair_markers", {})
    for name in (
        "flet-reconnect-merge-patch",
        "flutter-file-upload-patch",
        "flutter-ios-pointer-patch",
    ):
        if markers.get(name, {}).get("ok") is not True:
            raise ValueError("Renderer is missing required repairs: " + name)
    return digest
