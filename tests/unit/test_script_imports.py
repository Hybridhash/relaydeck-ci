"""Every `relaydeck` import inside a shell script, the installer template or the plugin resolves."""

from __future__ import annotations

import importlib
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCES = sorted(
    [
        *ROOT.glob("scripts/*.sh"),
        ROOT / "install.sh.in",
        *(p for p in (ROOT / "plugin").rglob("*") if p.is_file()),
    ]
)
_FROM = re.compile(r"^\s*from\s+(relaydeck[\w.]*)\s+import\s+([\w, ]+?)\s*$")
_IMPORT = re.compile(r"^\s*import\s+(relaydeck[\w.]*)")


def _imports(path: Path) -> list[tuple[str, str | None]]:
    found: list[tuple[str, str | None]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if match := _FROM.match(line):
            found.extend((match.group(1), name.strip()) for name in match.group(2).split(","))
        elif match := _IMPORT.match(line):
            found.append((match.group(1), None))
    return found


def _resolves(module: str, name: str | None) -> bool:
    try:
        if importlib.util.find_spec(module) is None:
            return False
    except ModuleNotFoundError:
        return False
    if name is None:
        return True
    if importlib.util.find_spec(f"{module}.{name}") is not None:
        return True
    return hasattr(importlib.import_module(module), name)


def test_the_scan_sees_the_release_script_import() -> None:
    assert _imports(ROOT / "scripts" / "release_web_bundle.sh")


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_relaydeck_import_resolves(path: Path) -> None:
    unresolved = [(module, name) for module, name in _imports(path) if not _resolves(module, name)]
    assert unresolved == []
