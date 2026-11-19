"""Catalog imports point toward the design system, never back into the client."""

from __future__ import annotations

import ast
import json
import tomllib
from pathlib import Path

import flet as ft
import pytest

from relaydeck.devtools.catalog_model import ENTRIES
from tests.support.architecture.contracts import run_lint_imports

ROOT = Path(__file__).resolve().parents[2]
MODULES = (
    "catalog",
    "catalog_model",
    "catalog_controls",
    "catalog_renderer",
    "catalog_screenshots",
)


def test_catalog_boundary_has_no_exception() -> None:
    contracts = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"][
        "contracts"
    ]
    rule = next(c for c in contracts if c["name"] == "Client excludes the component catalog")
    assert rule["source_modules"] == ["relaydeck.client"]
    assert rule["forbidden_modules"] == [f"relaydeck.devtools.{name}" for name in MODULES]
    assert not rule.get("ignore_imports")


@pytest.mark.parametrize("module", MODULES)
@pytest.mark.parametrize("type_only", [False, True])
def test_client_cannot_import_any_catalog_module(
    tmp_path: Path, module: str, type_only: bool
) -> None:
    for folder in ("fixture", "fixture/client", "fixture/devtools"):
        path = tmp_path / folder
        path.mkdir(exist_ok=True)
        (path / "__init__.py").touch()
    (tmp_path / "fixture/devtools" / (module + ".py")).write_text("value = 1\n")
    source = f"from fixture.devtools.{module} import value\n"
    if type_only:
        source = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    " + source
    (tmp_path / "fixture/client/view.py").write_text(source)
    config = tmp_path / "imports.toml"
    config.write_text(
        '[tool.importlinter]\nroot_package = "fixture"\nexclude_type_checking_imports = false\n'
        '[[tool.importlinter.contracts]]\nname = "Client excludes catalog"\ntype = "forbidden"\n'
        'source_modules = ["fixture.client"]\nforbidden_modules = '
        + json.dumps([f"fixture.devtools.{module}"])
        + "\n"
    )
    result = run_lint_imports(config, tmp_path)
    assert result.returncode != 0 and "BROKEN" in result.stdout


def _control_producers(sources: dict[str, str]) -> set[tuple[str, str]]:
    prefix = "relaydeck.client.design_system."
    declarations = {}
    aliases = {}
    for module, source in sources.items():
        tree = ast.parse(source)
        aliases[module] = {
            alias.asname or alias.name: (node.module.removeprefix(prefix), alias.name)
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(prefix)
            for alias in node.names
        }
        for node in tree.body:
            if isinstance(node, ast.ClassDef | ast.FunctionDef):
                declarations[module, node.name] = node
    found: set[tuple[str, str]] = set()
    edges: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for identity, node in declarations.items():
        module, _name = identity
        edges[identity] = set()
        expressions = (
            node.bases
            if isinstance(node, ast.ClassDef)
            else [call.func for call in ast.walk(node) if isinstance(call, ast.Call)]
        )
        for expr in expressions:
            if isinstance(expr, ast.Name):
                target = (module, expr.id)
                edges[identity].add(
                    target if target in declarations else aliases[module].get(expr.id, target)
                )
            if (
                isinstance(expr, ast.Attribute)
                and isinstance(expr.value, ast.Name)
                and expr.value.id == "ft"
            ):
                cls = getattr(ft, expr.attr, None)
                if isinstance(cls, type) and issubclass(cls, ft.Control):
                    found.add(identity)
    while True:
        admitted = {identity for identity, targets in edges.items() if targets & found}
        if admitted <= found:
            break
        found.update(admitted)
    return {identity for identity in found if not identity[1].startswith("_")}


def test_catalog_covers_every_public_control_producer() -> None:
    folder = ROOT / "src/relaydeck/client/design_system"
    sources = {
        str(path.relative_to(folder).with_suffix("")).replace("/", "."): path.read_text()
        for path in folder.rglob("*.py")
    }
    assert _control_producers(sources) == {(entry.module, entry.symbol) for entry in ENTRIES}


@pytest.mark.parametrize(
    ("sources", "expected"),
    [
        ({"editors": "def Native(): return ft.TextField()"}, {("editors", "Native")}),
        (
            {
                "editors": "def _native(): return ft.TextField()\n"
                "def Inner(): return _native()\n"
                "def Outer(): return Inner()"
            },
            {("editors", "Inner"), ("editors", "Outer")},
        ),
        (
            {
                "editors": "def Native(): return ft.TextField()",
                "forms": "from relaydeck.client.design_system.editors import Native as Field\n"
                "def Form(): return Field()",
            },
            {("editors", "Native"), ("forms", "Form")},
        ),
        ({"values": "def Value(): return ft.TextStyle()\ndef Factory(): return Value()"}, set()),
        ({"cycles": "def First(): return Second()\ndef Second(): return First()"}, set()),
        (
            {"forms": "from unrelated.editors import Native as Field\ndef Form(): return Field()"},
            set(),
        ),
    ],
)
def test_catalog_finds_delegating_producers_without_exemptions(sources, expected) -> None:
    assert _control_producers(sources) == expected


def test_catalog_uses_only_design_system_client_modules() -> None:
    for name in MODULES:
        tree = ast.parse((ROOT / f"src/relaydeck/devtools/{name}.py").read_text())
        imported = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]
        assert all(
            not name.startswith("relaydeck.client.")
            or name == "relaydeck.client.design_system"
            or name.startswith("relaydeck.client.design_system.")
            for name in imported
        )
