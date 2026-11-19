"""The companion reaches the client only from hosted_web; git and usage consumers are named."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from tests.support.architecture.contracts import run_lint_imports

ROOT = Path(__file__).resolve().parents[2]
COMPANION = ROOT / "src/relaydeck/companion"
TABLE = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"]["contracts"]
FENCE = "Companion reaches the client only from hosted_web"
HOSTED = "relaydeck.companion.app.hosted_web"


def _contract(name: str) -> dict[str, Any]:
    return next(row for row in TABLE if row["name"] == name)


def _modules() -> list[str]:
    """Each package or module directly under `companion` and `companion.app` but hosted_web."""
    found = []
    for folder in (COMPANION, COMPANION / "app"):
        for path in folder.iterdir():
            if path.name in ("__init__.py", "__pycache__", "data", "app"):
                continue
            if path.is_dir() or path.suffix == ".py":
                parts = path.relative_to(COMPANION.parent).with_suffix("").parts
                found.append("relaydeck." + ".".join(parts))
    return sorted(name for name in found if name != HOSTED)


def test_the_fence_names_every_companion_module_except_hosted_web() -> None:
    row = _contract(FENCE)
    assert row["type"] == "forbidden"
    assert row["forbidden_modules"] == ["relaydeck.client"]
    assert not row.get("ignore_imports")
    assert sorted(row["source_modules"]) == _modules()
    assert HOSTED not in row["source_modules"]


def _fixture(tmp_path: Path, importer: str) -> Path:
    """A copy of the fence over empty stand-in packages, with one module importing the client."""
    row = dict(_contract(FENCE))
    row["source_modules"] = [name.replace("relaydeck.", "fxc.") for name in row["source_modules"]]
    row["forbidden_modules"] = ["fxc.client"]
    packages = ["fxc", "fxc.client", "fxc.companion", "fxc.companion.app", *row["source_modules"]]
    packages += [HOSTED.replace("relaydeck.", "fxc.")]
    for name in packages:
        folder = tmp_path.joinpath(*name.split("."))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "__init__.py").write_text("")
    (tmp_path / "fxc/client/screen.py").write_text("")
    (tmp_path.joinpath(*importer.split(".")) / "__init__.py").write_text(
        "import fxc.client.screen\n"
    )
    text = '[tool.importlinter]\nroot_package = "fxc"\nexclude_type_checking_imports = false\n'
    text += "\n[[tool.importlinter.contracts]]\n"
    text += "".join(f"{key} = {json.dumps(value)}\n" for key, value in row.items())
    config = tmp_path / "contracts.toml"
    config.write_text(text)
    return config


def test_hosted_web_may_import_the_client(tmp_path: Path) -> None:
    config = _fixture(tmp_path, "fxc.companion.app.hosted_web")
    result = run_lint_imports(config, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("importer", ["fxc.companion.adapters", "fxc.companion.app.runtime"])
def test_other_companion_modules_may_not_import_the_client(tmp_path: Path, importer: str) -> None:
    config = _fixture(tmp_path, importer)
    result = run_lint_imports(config, tmp_path)
    assert result.returncode != 0 and "BROKEN" in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize(
    "name", ["Git consumers use the public entry", "Usage consumers use the public entry"]
)
def test_the_command_line_and_scripts_are_consumers(name: str) -> None:
    sources = _contract(name)["source_modules"]
    assert "relaydeck.cli" in sources and "scripts" in sources
