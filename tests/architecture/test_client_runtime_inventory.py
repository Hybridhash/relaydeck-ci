"""The client ships only live implementations and shared release limits have one owner."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "src/relaydeck/client"
REMOVED = (
    "features/sync/connectivity.py",
    "features/terminal/writer_gate.py",
    "features/terminal/announcer.py",
    "models/lifecycle/stores.py",
    "ports/auth.py",
    "widgets/sessions/status.py",
    "widgets/sessions/status_order.py",
)


@pytest.mark.parametrize("name", REMOVED)
def test_unused_client_modules_are_not_shipped(name: str) -> None:
    assert not (CLIENT / name).exists()


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("features/terminal/sessions.py", "fresh_emulator_hint"),
        ("screens/changes/parts/header.py", "graph_summary_text"),
        ("app/shell/destinations.py", "DEST_HOSTS"),
    ],
)
def test_unused_client_names_are_absent(module: str, name: str) -> None:
    tree = ast.parse((CLIENT / module).read_text())
    assert not any(
        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == name
        or isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Store)
        and node.id == name
        for node in ast.walk(tree)
    )


def test_manifest_size_limit_has_one_definition() -> None:
    definitions = []
    for path in (ROOT / "src/relaydeck/releases").glob("*.py"):
        tree = ast.parse(path.read_text())
        if any(
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Store)
            and node.id == "MAX_MANIFEST_BYTES"
            for node in ast.walk(tree)
        ):
            definitions.append(path.name)
    assert definitions == ["bundles.py"]
