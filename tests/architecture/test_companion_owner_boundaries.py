"""Resource owners cannot cycle or import their composition root, even only for typing."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from tests.support.architecture.contracts import run_lint_imports

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "src/relaydeck/companion/app"
PREFIX = "relaydeck.companion.app"
ACYCLIC = "Companion application owners are acyclic"
ROOT_FENCE = "Companion owners exclude the composition root"


def contracts() -> list[dict[str, Any]]:
    table = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"]
    assert table["exclude_type_checking_imports"] is False
    selected = [row for row in table["contracts"] if row["name"] in (ACYCLIC, ROOT_FENCE)]
    assert len(selected) == 2
    return selected


def uncovered(app: Path, sources: list[str]) -> list[str]:
    owners = [
        path
        for path in app.rglob("*.py")
        if path.name != "__init__.py" and path != app / "runtime.py"
    ]
    found = []
    for path in owners:
        module = PREFIX + "." + ".".join(path.relative_to(app).with_suffix("").parts)
        if not any(module == source or module.startswith(source + ".") for source in sources):
            found.append(module)
    return sorted(found)


def test_every_owner_is_covered_without_ignores_or_runtime_exemption() -> None:
    by_name = {row["name"]: row for row in contracts()}
    assert by_name[ACYCLIC]["ancestors"] == [PREFIX]
    assert by_name[ACYCLIC]["type"] == "acyclic_siblings"
    assert by_name[ROOT_FENCE]["type"] == "forbidden"
    assert by_name[ROOT_FENCE]["forbidden_modules"] == [PREFIX + ".runtime"]
    for row in by_name.values():
        assert not row.get("ignore_imports")
        assert not row.get("skip_descendants")
        assert not row.get("allow_indirect_imports")
    sources = by_name[ROOT_FENCE]["source_modules"]
    assert uncovered(APP, sources) == []
    assert PREFIX + ".runtime" not in sources


def fixture(tmp_path: Path) -> Path:
    for path in APP.rglob("*.py"):
        dest = tmp_path / "fxowners/app" / path.relative_to(APP)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("")
    (tmp_path / "fxowners/__init__.py").write_text("")
    config = '[tool.importlinter]\nroot_package = "fxowners"\n'
    config += "exclude_type_checking_imports = false\n"
    for row in contracts():
        config += "\n[[tool.importlinter.contracts]]\n"
        for key, value in row.items():
            encoded = json.dumps(value).replace("relaydeck.companion", "fxowners")
            config += f"{key} = {encoded}\n"
    path = tmp_path / "pyproject.toml"
    path.write_text(config)
    return path


def write(tmp_path: Path, name: str, text: str) -> None:
    (tmp_path / "fxowners/app" / (name.replace(".", "/") + ".py")).write_text(text + "\n")


def test_runtime_can_compose_owners_with_one_way_dependencies(tmp_path: Path) -> None:
    config = fixture(tmp_path)
    write(tmp_path, "runtime", "import fxowners.app.bindings\nimport fxowners.app.herdr_link")
    write(tmp_path, "bindings", "import fxowners.app.maintenance")
    result = run_lint_imports(config, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("typing_only", [False, True])
@pytest.mark.parametrize("owner", ["bindings", "hosted_web.binding"])
def test_even_a_one_way_owner_import_of_runtime_is_refused(
    tmp_path: Path, typing_only: bool, owner: str
) -> None:
    config = fixture(tmp_path)
    text = "import fxowners.app.runtime"
    if typing_only:
        text = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    " + text
    write(tmp_path, owner, text)
    result = run_lint_imports(config, tmp_path)
    assert result.returncode != 0, result.stdout
    assert ROOT_FENCE in result.stdout and "BROKEN" in result.stdout
    assert "fxowners.app." + owner in result.stdout


@pytest.mark.parametrize(
    "left,right",
    [
        ("bindings", "herdr_link"),
        ("hosted_web.binding", "hosted_web.static"),
    ],
)
def test_sibling_and_nested_owner_cycles_are_refused(tmp_path: Path, left: str, right: str) -> None:
    config = fixture(tmp_path)
    write(tmp_path, left, "import fxowners.app." + right)
    write(tmp_path, right, "import fxowners.app." + left)
    result = run_lint_imports(config, tmp_path)
    assert result.returncode != 0, result.stdout
    assert ACYCLIC in result.stdout and "BROKEN" in result.stdout


@pytest.mark.parametrize("relative", ["new_owner.py", "new_owner/runtime.py"])
def test_new_owner_must_be_added_to_the_contract(tmp_path: Path, relative: str) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    sources = next(row["source_modules"] for row in contracts() if row["name"] == ROOT_FENCE)
    assert uncovered(tmp_path, sources) == [
        PREFIX + "." + ".".join(Path(relative).with_suffix("").parts)
    ]
