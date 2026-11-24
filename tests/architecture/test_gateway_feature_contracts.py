"""Gateway feature membership, direction and public entry boundaries."""

from __future__ import annotations

import ast
import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from tests.support.architecture.contracts import run_lint_imports

ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / "src/relaydeck/gateway/features"
EXPORTS = json.loads(
    (ROOT / "tests/fixtures/architecture/gateway_feature_exports.json").read_text()
)
PREFIX = "relaydeck.gateway.features."
TABLE = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"]["contracts"]
LAYERS = {
    contract["containers"][0].removeprefix(PREFIX): contract
    for contract in TABLE
    if contract["name"].startswith("Gateway ") and contract["name"].endswith(" layer order")
}
COMPOSITION = (
    "src/relaydeck/gateway/app/",
    "src/relaydeck/companion/app/",
    "src/relaydeck/client/app/",
)


def _members() -> list[tuple[str, str, int]]:
    result = []
    for feature, contract in LAYERS.items():
        for path in sorted((FEATURES / feature).rglob("*.py")):
            if path == FEATURES / feature / "__init__.py":
                continue
            module = str(path.relative_to(FEATURES / feature).with_suffix("")).replace("/", ".")
            module = module.removesuffix(".__init__")
            role = next(
                (
                    index
                    for index, layer in enumerate(contract["layers"])
                    if any(
                        module == name.strip("()") or module.startswith(name.strip("()") + ".")
                        for name in layer.split(":")
                    )
                ),
                -1,
            )
            result.append((feature, module, role))
    return result


def _package(root: Path, name: str) -> Path:
    path = root.joinpath(*name.split("."))
    path.mkdir(parents=True, exist_ok=True)
    for parent in [path, *path.parents]:
        if parent == root:
            break
        (parent / "__init__.py").touch()
    return path


def _module(root: Path, name: str, *, package: bool = False) -> Path:
    if package:
        return _package(root, name) / "__init__.py"
    owner, leaf = name.rsplit(".", 1)
    path = _package(root, owner) / (leaf + ".py")
    path.touch()
    return path


def _fixture(tmp_path: Path, feature: str) -> tuple[Path, dict[str, Any]]:
    contract = dict(LAYERS[feature])
    base = "fxgateway.features." + feature
    for name in ["fxgateway", "fxgateway.features", "fxgateway.app", "fxgateway.http"]:
        _package(tmp_path, name)
    for name in LAYERS:
        _module(tmp_path, "fxgateway.features." + name + ".api")
    for layer in contract["layers"]:
        for name in layer.split(":"):
            name = name.strip("()")
            _module(tmp_path, base + "." + name, package=name == "adapters")
    for owner, module, _role in _members():
        if owner == feature:
            _module(tmp_path, base + "." + module, package=module == "adapters")
    aliases = [
        edge.replace("relaydeck.gateway", "fxgateway")
        for edge in contract.get("ignore_imports", [])
    ]
    api = tmp_path.joinpath(*base.split("."), "api.py")
    api.write_text("".join("import " + edge.split(" -> ")[1] + "\n" for edge in aliases))
    contract["containers"] = [base]
    contract["ignore_imports"] = aliases
    return api, contract


def _configuration(tmp_path: Path, contracts: list[dict[str, Any]]) -> Path:
    text = (
        '[tool.importlinter]\nroot_package = "fxgateway"\nexclude_type_checking_imports = false\n'
    )
    for contract in contracts:
        text += "\n[[tool.importlinter.contracts]]\n"
        for name, value in contract.items():
            text += name + " = " + json.dumps(value) + "\n"
    path = tmp_path / "contracts.toml"
    path.write_text(text)
    return path


def test_every_gateway_feature_and_module_has_a_contract() -> None:
    actual = {p.name for p in FEATURES.iterdir() if p.is_dir() and not p.name.startswith("_")}
    assert actual == set(LAYERS)
    assert all(role >= 0 for _feature, _module_name, role in _members())
    assert all(contract["exhaustive"] is True for contract in LAYERS.values())
    independent = next(c for c in TABLE if c["name"] == "Gateway features independent")
    assert set(independent["modules"]) == {PREFIX + feature for feature in actual}
    assert not independent.get("ignore_imports")
    forbidden = next(
        c for c in TABLE if c["name"] == "Gateway features exclude HTTP and composition"
    )
    assert set(forbidden["source_modules"]) == {PREFIX + feature for feature in actual}
    assert set(forbidden["forbidden_modules"]) == {
        "relaydeck.gateway.app",
        "relaydeck.gateway.http",
        "fastapi",
        "starlette",
        "uvicorn",
    }
    assert not forbidden.get("ignore_imports")


@pytest.mark.parametrize(("feature", "module", "role"), _members())
@pytest.mark.parametrize("type_only", [False, True])
def test_each_gateway_member_rejects_an_upward_or_cross_feature_import(
    tmp_path: Path, feature: str, module: str, role: int, type_only: bool
) -> None:
    _api, contract = _fixture(tmp_path, feature)
    base = "fxgateway.features." + feature
    if role == 0:
        target = "fxgateway.features." + next(name for name in LAYERS if name != feature) + ".api"
        contracts = [
            {
                "name": "Features independent",
                "type": "independence",
                "modules": ["fxgateway.features." + name for name in LAYERS],
                "unmatched_ignore_imports_alerting": "error",
            }
        ]
    else:
        target = base + (".adapters" if role == 1 else ".api")
        contracts = [contract]
    file = _module(tmp_path, base + "." + module, package=module == "adapters")
    code = "import " + target + "\n"
    if type_only:
        code = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    " + code
    file.write_text(file.read_text() + code)
    result = run_lint_imports(_configuration(tmp_path, contracts), tmp_path)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "BROKEN" in result.stdout and target in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize("feature", LAYERS)
@pytest.mark.parametrize("type_only", [False, True])
def test_gateway_features_reject_composition_even_in_annotations(
    tmp_path: Path, feature: str, type_only: bool
) -> None:
    api, _contract = _fixture(tmp_path, feature)
    code = "import fxgateway.app\n"
    if type_only:
        code = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    " + code
    api.write_text(api.read_text() + code)
    contract = dict(
        next(c for c in TABLE if c["name"] == "Gateway features exclude HTTP and composition")
    )
    contract["source_modules"] = [
        name.replace("relaydeck.gateway", "fxgateway") for name in contract["source_modules"]
    ]
    contract["forbidden_modules"] = [
        name.replace("relaydeck.gateway", "fxgateway")
        for name in contract["forbidden_modules"]
        if name.startswith("relaydeck.gateway")
    ]
    result = run_lint_imports(_configuration(tmp_path, [contract]), tmp_path)
    assert result.returncode != 0 and "BROKEN" in result.stdout, result.stdout + result.stderr


def _bound_names(text: str) -> set[str]:
    actual = set()
    for node in ast.parse(text).body:
        if isinstance(node, ast.ImportFrom) and node.module != "__future__":
            actual.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            actual.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            actual.add(node.name)
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            actual.update(
                name.id
                for target in targets
                for name in ast.walk(target)
                if isinstance(name, ast.Name)
            )
    return actual


def test_public_gateway_entries_export_exactly_the_declared_names() -> None:
    for module, expected in EXPORTS.items():
        path = ROOT / "src" / Path(*module.split(".")).with_suffix(".py")
        assert _bound_names(path.read_text()) == set(expected), module


@pytest.mark.parametrize("module", EXPORTS)
@pytest.mark.parametrize("binding", ["unexpected_entry", "_hidden_entry"])
def test_an_extra_binding_cannot_hide_in_a_public_entry(module: str, binding: str) -> None:
    path = ROOT / "src" / Path(*module.split(".")).with_suffix(".py")
    changed = path.read_text() + "\n" + binding + " = object()\n"
    assert _bound_names(changed) != set(EXPORTS[module])


def _entry_errors(path: str, text: str) -> list[str]:
    if path.startswith(COMPOSITION):
        return []
    owner = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
    failures = []
    for node in ast.walk(ast.parse(text)):
        imported = []
        if isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported = [node.module]
            if node.module == PREFIX.removesuffix("."):
                imported = [node.module + "." + alias.name for alias in node.names]
            elif node.module.startswith(PREFIX) and node.module.count(".") == 3:
                imported = [node.module + "." + alias.name for alias in node.names]
        for name in imported:
            if not name.startswith(PREFIX):
                continue
            feature = name[len(PREFIX) :].partition(".")[0]
            base = PREFIX + feature
            if owner == base or owner.startswith(base + "."):
                continue
            if name != base + ".api":
                failures.append(name)
    return failures


def test_external_production_consumers_use_public_gateway_entries() -> None:
    failures = []
    for path in (ROOT / "src/relaydeck").rglob("*.py"):
        relative = str(path.relative_to(ROOT))
        failures.extend((relative, name) for name in _entry_errors(relative, path.read_text()))
    assert failures == []


@pytest.mark.parametrize("feature", LAYERS)
def test_an_implementation_import_cannot_hide_behind_an_entry_check(feature: str) -> None:
    member = next(
        module for owner, module, _role in _members() if owner == feature and module != "api"
    )
    prefix = PREFIX + feature
    assert _entry_errors("src/relaydeck/gateway/http/probe.py", f"import {prefix}.{member}\n")
    assert _entry_errors("src/relaydeck/gateway/http/probe.py", f"import {prefix}.api\n") == []
    assert _entry_errors("src/relaydeck/gateway/app/probe.py", f"import {prefix}.{member}\n") == []


ASGI_IMPORTS = {
    "relaydeck.gateway.features.metadata.metadata_streams": {
        "starlette.responses.StreamingResponse",
        "starlette.types.Receive",
        "starlette.types.Scope",
        "starlette.types.Send",
    }
}


def _framework_errors(module: str, text: str) -> set[str]:
    imported = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("starlette"):
            imported.update(node.module + "." + alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(
                alias.name for alias in node.names if alias.name.startswith("starlette")
            )
    return imported - ASGI_IMPORTS.get(module, set())


def test_features_have_only_the_declared_metadata_asgi_adapter_imports() -> None:
    failures = {}
    for path in FEATURES.rglob("*.py"):
        module = str(path.relative_to(ROOT / "src").with_suffix("")).replace("/", ".")
        errors = _framework_errors(module, path.read_text())
        if errors:
            failures[module] = errors
    assert failures == {}


@pytest.mark.parametrize(("feature", "module", "_role"), _members())
@pytest.mark.parametrize("type_only", [False, True])
def test_no_feature_member_can_add_another_framework_response(
    feature: str, module: str, _role: int, type_only: bool
) -> None:
    code = "from starlette.responses import Response\n"
    if type_only:
        code = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    " + code
    assert _framework_errors(PREFIX + feature + "." + module, code) == {
        "starlette.responses.Response"
    }
