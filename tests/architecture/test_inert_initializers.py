"""Every package initializer under `relaydeck` is inert, with no exemptions.

Inert means no lazy export, no import inside a function and no framework loaded by importing
the package. Initializers that only re-export or register siblings qualify.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.architecture.inert import (
    discover,
    findings,
)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "relaydeck"
FIXTURES = ROOT / "tests" / "fixtures" / "architecture" / "inert"

SCOPE = ("relaydeck",)


def _packages() -> dict[str, Path]:
    return discover(SRC, SCOPE)


def test_every_package_is_in_scope() -> None:
    packages = _packages()
    assert "relaydeck.client" in packages
    assert "relaydeck.client.features.changes" in packages
    assert "relaydeck.gateway.features.metadata" in packages
    assert "relaydeck.usage.readers" in packages
    assert len(packages) > 100


@pytest.mark.parametrize("package", sorted(_packages()))
def test_initializer_is_inert(package: str) -> None:
    init = _packages()[package]
    assert findings(init, package) == [], f"{init.relative_to(ROOT)} is not inert"


def test_fixture_clean_initializer_passes() -> None:
    assert findings(FIXTURES / "fx_clean" / "__init__.py", "fx_clean", FIXTURES) == []


def test_fixture_lazy_export_fails() -> None:
    found = findings(FIXTURES / "fx_lazy_export" / "__init__.py", "fx_lazy_export", FIXTURES)
    assert any("lazy export" in item for item in found), found


def test_fixture_function_level_import_fails() -> None:
    found = findings(FIXTURES / "fx_hidden_import" / "__init__.py", "fx_hidden_import", FIXTURES)
    assert any("import inside a function" in item for item in found), found


def test_fixture_that_loads_flet_fails_in_a_fresh_process() -> None:
    found = findings(FIXTURES / "fx_loads_flet" / "__init__.py", "fx_loads_flet", FIXTURES)
    assert "loads flet" in found, found


def test_fresh_process_uses_the_active_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.support.architecture import inert

    helper = tmp_path / "tests/support/architecture/inert.py"
    helper.parent.mkdir(parents=True)
    helper.touch()
    package = tmp_path / "src/only_in_this_checkout"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('"""Synthetic package."""\n')
    monkeypatch.setattr(inert, "__file__", str(helper))
    assert inert.fresh_process_findings("only_in_this_checkout") == []
