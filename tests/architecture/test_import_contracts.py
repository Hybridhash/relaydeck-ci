"""Import contracts reject upward imports and permit only exact permanent exceptions."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from tests.support.architecture.contracts import (
    contract_modules,
    run_lint_imports,
    uncovered_slices,
)

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
FIXTURES = ROOT / "tests" / "fixtures" / "architecture"
FEATURES = ROOT / "src" / "relaydeck" / "client" / "features"
FEATURE_CONTRACT = "Client features independent except recorded edges"
EDGE = re.compile(r'^\s*"(?P<edge>[^"]+)",\s*#\s*(?P<why>.+)$')
WHY = re.compile(r"^Permanent(?::| )\s*\S.*$")


@pytest.mark.parametrize(
    "reason,allowed",
    [
        ("Permanent: fixture mode", True),
        ("Permanent public entry", True),
        ("migration debt", False),
        ("temporary exception", False),
        ("Permanent", False),
    ],
)
def test_ignore_reasons_are_permanent(reason: str, allowed: bool) -> None:
    assert bool(WHY.fullmatch(reason)) is allowed


def _importlinter() -> dict[str, object]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["importlinter"]


def test_import_linter_is_pinned_in_the_dev_group() -> None:
    dev = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["dependency-groups"]["dev"]
    assert "import-linter==2.15" in dev


def test_every_contract_alerts_on_an_unused_ignore() -> None:
    table = _importlinter()
    assert table["include_external_packages"] is True
    assert table["exclude_type_checking_imports"] is False
    contracts = table["contracts"]
    assert isinstance(contracts, list) and len(contracts) >= 14
    for contract in contracts:
        assert contract["unmatched_ignore_imports_alerting"] == "error", contract["name"]


def test_ignored_edges_are_exact_and_permanent() -> None:
    lines = PYPROJECT.read_text(encoding="utf-8").split("[tool.importlinter]", 1)[1].splitlines()
    inside = False
    edges = 0
    for line in lines:
        if line.startswith("ignore_imports"):
            inside = True
            continue
        if inside and line.strip() == "]":
            inside = False
            continue
        if inside and line.strip().startswith('"'):
            edges += 1
            match = EDGE.match(line)
            assert match is not None, f"ignored edge without a comment: {line.strip()}"
            assert "*" not in match["edge"], f"wildcard ignore: {line.strip()}"
            assert WHY.fullmatch(match["why"]), f"unexplained ignore: {line.strip()}"
        elif inside:
            assert line.strip().startswith("#"), f"unexpected line in ignore_imports: {line}"
    contracts = _importlinter()["contracts"]
    assert isinstance(contracts, list)
    assert edges == sum(len(contract.get("ignore_imports", [])) for contract in contracts)
    assert edges > 0


def test_the_independence_contract_names_every_client_feature() -> None:
    listed = contract_modules(PYPROJECT, FEATURE_CONTRACT)
    assert uncovered_slices(FEATURES, "relaydeck.client.features", listed) == []


def test_a_slice_missing_from_a_contract_is_found() -> None:
    listed = contract_modules(FIXTURES / "slices" / "config.toml", "Slices independent")
    uncovered = uncovered_slices(FIXTURES / "slices" / "features", "fxslices.features", listed)
    assert uncovered == ["c"]


def test_the_contracts_hold_on_the_current_tree() -> None:
    result = run_lint_imports(PYPROJECT, ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_broken_contract_fails_with_its_output() -> None:
    graph = FIXTURES / "contract_graph"
    result = run_lint_imports(graph / "broken.toml", graph)
    assert result.returncode != 0
    assert "fxgraph.low" in result.stdout and "BROKEN" in result.stdout


def test_an_exact_ignore_passes() -> None:
    graph = FIXTURES / "contract_graph"
    result = run_lint_imports(graph / "ignored.toml", graph)
    assert result.returncode == 0, result.stdout + result.stderr


def test_an_ignore_that_matches_nothing_fails() -> None:
    graph = FIXTURES / "contract_graph"
    result = run_lint_imports(graph / "stale.toml", graph)
    assert result.returncode != 0
    assert "fxgraph.high" in result.stdout + result.stderr


def test_rename_cannot_import_an_unrecorded_feature(tmp_path: Path) -> None:
    table = _importlinter()
    contracts = table["contracts"]
    assert isinstance(contracts, list)
    contract = next(c for c in contracts if c["name"] == FEATURE_CONTRACT)
    assert "relaydeck.client.features.rename" in contract["modules"]
    modules = [m.replace("relaydeck.client", "fxclient", 1) for m in contract["modules"]]
    for name in ["fxclient", "fxclient.features", *modules]:
        package = tmp_path.joinpath(*name.split("."))
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "fxclient/features/rename/bad.py").write_text(
        "import fxclient.features.pairing\n", encoding="utf-8"
    )
    config = tmp_path / "contract.toml"
    config.write_text(
        '[tool.importlinter]\nroot_package = "fxclient"\n\n'
        '[[tool.importlinter.contracts]]\nname = "Features independent"\n'
        'type = "independence"\nmodules = ' + repr(modules).replace("'", '"') + "\n",
        encoding="utf-8",
    )
    result = run_lint_imports(config, tmp_path)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "fxclient.features.rename" in result.stdout
