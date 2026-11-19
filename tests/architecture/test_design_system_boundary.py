"""The design system imports nothing of the project except itself, models and two platform helpers.

Found by walking the files, not by listing forbidden targets: within `relaydeck`, a design-system
module may import `relaydeck.client.design_system.*`, `relaydeck.client.models.*`,
`relaydeck.client.platform.flet.events` and
`relaydeck.client.platform.diagnostics`. A flat client module nobody listed, or a platform module
added later, is refused the same way. Standard-library and framework imports are not checked.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.support.architecture.boundary import (
    boundary_violations,
    project_imports,
    python_modules,
)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "relaydeck"
FIXTURE = ROOT / "tests" / "fixtures" / "architecture" / "design_boundary" / "fxds"
DESIGN_SYSTEM = "relaydeck.client.design_system"
ALLOWED = (
    "relaydeck.client.models",
    "relaydeck.client.platform.flet.events",
    "relaydeck.client.platform.diagnostics",
)
FIXTURE_ALLOWED = tuple(a.replace("relaydeck", "fxds", 1) for a in ALLOWED)
FIXTURE_PACKAGE = "fxds.client.design_system"


def _fixture(tmp_path: Path) -> Path:
    copy = tmp_path / "fxds"
    shutil.copytree(FIXTURE, copy)
    return copy


def test_the_design_system_imports_only_models_itself_and_two_platform_helpers() -> None:
    assert boundary_violations(SRC, DESIGN_SYSTEM, ALLOWED) == []


def test_every_design_system_file_is_checked_and_the_allowed_targets_exist() -> None:
    files = python_modules(SRC / "client" / "design_system")
    assert len(files) > 20
    for target in ALLOWED:
        base = ROOT / "src" / Path(*target.split("."))
        assert base.with_suffix(".py").exists() or (base / "__init__.py").exists(), target


def test_the_clean_fixture_passes() -> None:
    assert boundary_violations(FIXTURE, FIXTURE_PACKAGE, FIXTURE_ALLOWED) == []
    seen = {m for _, m in project_imports(FIXTURE / "client/design_system/clean.py", FIXTURE)}
    assert "fxds.client.platform.flet.events" in seen
    assert "fxds.client.platform.diagnostics.terminal" in seen
    assert "fxds.client.design_system.sibling" in seen


def test_an_omitted_flat_client_module_is_refused(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    (root / "client/design_system/flat.py").write_text(
        "from fxds.client import open_timing\n", encoding="utf-8"
    )
    found = boundary_violations(root, FIXTURE_PACKAGE, FIXTURE_ALLOWED)
    assert [f for f in found if "fxds.client.open_timing" in f], found


def test_a_function_level_import_of_a_flat_module_is_refused(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    (root / "client/design_system/lazy.py").write_text(
        "def load() -> object:\n    import fxds.client.open_timing as module\n    return module\n",
        encoding="utf-8",
    )
    found = boundary_violations(root, FIXTURE_PACKAGE, FIXTURE_ALLOWED)
    assert [f for f in found if "lazy.py" in f], found


def test_a_newly_discovered_platform_module_is_refused(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    (root / "client/platform/clipboard.py").write_text('"""New."""\n', encoding="utf-8")
    (root / "client/design_system/clip.py").write_text(
        "from fxds.client.platform import clipboard\n", encoding="utf-8"
    )
    found = boundary_violations(root, FIXTURE_PACKAGE, FIXTURE_ALLOWED)
    assert [f for f in found if "fxds.client.platform.clipboard" in f], found


def test_the_storage_platform_module_is_refused(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    (root / "client/design_system/store.py").write_text(
        "from fxds.client.platform.storage.memory import __doc__ as doc\n", encoding="utf-8"
    )
    found = boundary_violations(root, FIXTURE_PACKAGE, FIXTURE_ALLOWED)
    assert [f for f in found if "storage.memory" in f], found


def test_a_relative_import_out_of_the_package_is_refused(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    (root / "client/design_system/rel.py").write_text(
        "from .. import open_timing\n", encoding="utf-8"
    )
    found = boundary_violations(root, FIXTURE_PACKAGE, FIXTURE_ALLOWED)
    assert [f for f in found if "rel.py" in f and "open_timing" in f], found


def test_pure_models_are_allowed(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    models = root / "client/models"
    models.mkdir(exist_ok=True)
    (models / "__init__.py").write_text("", encoding="utf-8")
    (models / "layout.py").write_text('DESKTOP = "desktop"\n', encoding="utf-8")
    (root / "client/design_system/layout.py").write_text(
        "from fxds.client.models.layout import DESKTOP\n", encoding="utf-8"
    )
    assert boundary_violations(root, FIXTURE_PACKAGE, FIXTURE_ALLOWED) == []


def test_theme_and_routing_share_layout_names() -> None:
    from relaydeck.client.design_system import theme
    from relaydeck.client.models import layout
    from relaydeck.client.models.routing import RouteState

    for name in ("PHONE", "COMPACT", "DESKTOP"):
        assert getattr(theme, name) is getattr(layout, name)
    assert RouteState().layout is layout.DESKTOP
    assert RouteState(layout=layout.DESKTOP).is_wide
    assert not RouteState(layout=layout.COMPACT).is_wide
    assert not RouteState(layout=layout.PHONE).is_wide


@pytest.mark.parametrize("layer", ["app", "screens", "widgets", "features", "ports"])
def test_other_client_layers_are_refused(tmp_path: Path, layer: str) -> None:
    root = _fixture(tmp_path)
    package = root / "client" / layer
    package.mkdir(exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (root / "client/design_system/upward.py").write_text(
        f"import fxds.client.{layer}\n", encoding="utf-8"
    )
    found = boundary_violations(root, FIXTURE_PACKAGE, FIXTURE_ALLOWED)
    assert any(f"fxds.client.{layer}" in item for item in found), found
