"""Every screen is covered by the independence contract, including indirect imports."""

from __future__ import annotations

import json
import tomllib
from itertools import permutations
from pathlib import Path

import pytest

from tests.support.architecture.contracts import run_lint_imports

ROOT = Path(__file__).resolve().parents[2]
SCREENS = ROOT / "src/relaydeck/client/screens"
NAME = "Client screens are independent"


def _contract() -> dict[str, object]:
    contracts = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"][
        "contracts"
    ]
    return next(contract for contract in contracts if contract["name"] == NAME)


def test_every_screen_is_independent_without_exceptions() -> None:
    contract = _contract()
    discovered = {
        "relaydeck.client.screens." + path.name
        for path in SCREENS.iterdir()
        if (path / "__init__.py").exists()
    }
    assert set(contract["modules"]) == discovered
    assert len(discovered) >= 2
    assert contract["type"] == "independence"
    assert not contract.get("ignore_imports")
    assert not contract.get("allow_indirect_imports", False)
    assert contract["unmatched_ignore_imports_alerting"] == "error"


def _graph(tmp_path: Path) -> tuple[Path, list[str]]:
    modules = [
        str(module).replace("relaydeck.client", "fxscreens", 1) for module in _contract()["modules"]
    ]
    for module in [*modules, "fxscreens.features.shared"]:
        parts = module.split(".")
        for length in range(1, len(parts) + 1):
            directory = tmp_path.joinpath(*parts[:length])
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "__init__.py").write_text("")
    config = tmp_path / "contracts.toml"
    config.write_text(
        '[tool.importlinter]\nroot_package = "fxscreens"\n'
        "exclude_type_checking_imports = false\n\n"
        '[[tool.importlinter.contracts]]\nname = "Screens independent"\n'
        'type = "independence"\nmodules = ' + json.dumps(modules) + "\n"
        'unmatched_ignore_imports_alerting = "error"\n'
    )
    return config, modules


def test_a_clean_screen_graph_passes(tmp_path: Path) -> None:
    config, _modules = _graph(tmp_path)
    result = run_lint_imports(config, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("type_only", [False, True])
@pytest.mark.parametrize("indirect", [False, True])
@pytest.mark.parametrize("pair", list(permutations(range(len(_contract()["modules"])), 2)))
def test_cross_screen_imports_fail(
    tmp_path: Path, type_only: bool, indirect: bool, pair: tuple[int, int]
) -> None:
    config, modules = _graph(tmp_path)
    source, target = modules[pair[0]], modules[pair[1]]
    destination = "fxscreens.features.shared" if indirect else target
    import_line = f"import {destination}\n"
    source_file = tmp_path.joinpath(*source.split("."), "bad.py")
    source_file.write_text(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    " + import_line
        if type_only
        else import_line
    )
    if indirect:
        tmp_path.joinpath("fxscreens/features/shared/__init__.py").write_text(f"import {target}\n")
    result = run_lint_imports(config, tmp_path)
    assert result.returncode != 0, result.stdout + result.stderr
    assert source in result.stdout and target in result.stdout
