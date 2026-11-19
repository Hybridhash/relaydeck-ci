"""Fixtures for a limits hook recorded through another path to the same interpreter."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from relaydeck.usage.hooks.setup import managed_command


def link_folder(python: Path, name: str = "alias") -> Path:
    """A directory link to the interpreter's folder; returns the interpreter spelled through it."""
    alias = python.parent.parent / name
    alias.symlink_to(python.parent, target_is_directory=True)
    return alias / python.name


def sibling_environment(python: Path, name: str = "sibling env") -> Path:
    """Another environment whose interpreter links to the same base Python."""
    folder = python.parent.parent / name
    folder.mkdir(mode=0o700)
    link = folder / python.name
    link.symlink_to(Path(sys.executable))
    return link


def copied_interpreter(python: Path, name: str = "copy env") -> Path:
    folder = python.parent.parent / name
    folder.mkdir(mode=0o700)
    copy = folder / python.name
    copy.write_text("never executed")
    copy.chmod(0o700)
    return copy


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def respell(receipt: Path, settings: Path, recorded: Path) -> None:
    """Record `recorded` as the interpreter in the receipt and the settings line, as an earlier
    install through that path would have left them."""
    body = json.loads(receipt.read_text())
    body["interpreter"] = str(recorded)
    body["managed"] = {
        "type": "command",
        "command": managed_command(Path(body["script"]), Path(body["cache"]), recorded),
    }
    _write(receipt, body)
    document = json.loads(settings.read_text())
    document["statusLine"] = body["managed"]
    _write(settings, document)


def fresh_cache(cache: Path, now_ms: int) -> None:
    cache.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    window = {"used_percentage": 10, "resets_at": now_ms // 1000 + 3600}
    _write(cache, {"captured_ms": now_ms - 1000, "windows": {"five_hour": window}})
