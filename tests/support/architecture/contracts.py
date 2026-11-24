"""Run the `lint-imports` executable of the interpreter's own environment."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest


def lint_imports_executable() -> Path:
    exe = Path(sys.executable).with_name("lint-imports")
    if not exe.exists():
        pytest.skip(
            "lint-imports is not installed in this environment (dev group: import-linter==2.15); "
            "run `uv sync --group dev`"
        )
    return exe


def run_lint_imports(config: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    """`lint-imports --config <config>` from `cwd`; the output is kept for the failure message."""
    return subprocess.run(
        [str(lint_imports_executable()), "--config", str(config), "--no-cache"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def contract_modules(config: Path, contract: str) -> list[str]:
    """The `modules` list of the named contract in a pyproject-style configuration."""
    table = tomllib.loads(config.read_text(encoding="utf-8"))["tool"]["importlinter"]
    for entry in table["contracts"]:
        if entry["name"] == contract:
            return list(entry["modules"])
    raise KeyError(contract)


def uncovered_slices(features: Path, package: str, listed: list[str]) -> list[str]:
    """Slice packages found under `features` that the contract does not name."""
    found = sorted(p.name for p in features.iterdir() if (p / "__init__.py").exists())
    return [name for name in found if f"{package}.{name}" not in listed]
