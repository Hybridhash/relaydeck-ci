"""HTTP adapters cannot recover application composition through a type-only import."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from tests.support.architecture.contracts import run_lint_imports

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("member", ["context", "ports", "auth", "commands"])
@pytest.mark.parametrize("type_only", [False, True])
def test_http_composition_boundary_rejects_imports(
    tmp_path: Path, member: str, type_only: bool
) -> None:
    contracts = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"][
        "contracts"
    ]
    contract = next(c for c in contracts if c["name"] == "Gateway HTTP excludes composition")
    assert contract["source_modules"] == ["relaydeck.gateway.http"]
    assert contract["forbidden_modules"] == ["relaydeck.gateway.app"]
    assert not contract.get("ignore_imports")
    for package in ("fx", "fx/http", "fx/app"):
        path = tmp_path / package
        path.mkdir(exist_ok=True)
        (path / "__init__.py").touch()
    source = "from fx.app import state\n"
    if type_only:
        source = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    " + source
    (tmp_path / "fx/http" / (member + ".py")).write_text(source)
    (tmp_path / "fx/app/state.py").write_text("value = 1\n")
    config = tmp_path / "imports.toml"
    config.write_text(
        '[tool.importlinter]\nroot_package = "fx"\nexclude_type_checking_imports = false\n'
        '[[tool.importlinter.contracts]]\nname = "HTTP excludes composition"\ntype = "forbidden"\n'
        'source_modules = ["fx.http"]\nforbidden_modules = ["fx.app"]\n'
    )
    result = run_lint_imports(config, tmp_path)
    assert result.returncode != 0
    assert "BROKEN" in result.stdout
    assert json.dumps(member)[1:-1] in result.stdout
