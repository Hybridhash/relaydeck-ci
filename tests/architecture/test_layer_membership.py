"""The client layers named in the contracts, and that the contracts refuse upward imports.

The negative tests derive their configuration from the real contracts in pyproject.toml (module
names renamed to a fixture package), so removing a layer from a contract makes them fail. The
coverage test walks the client packages and requires each to be a source, a forbidden target
or a named exception.
"""

from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path

import pytest

from tests.support.architecture.contracts import run_lint_imports

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
CLIENT = ROOT / "src" / "relaydeck" / "client"
FIXTURE = ROOT / "tests" / "fixtures" / "architecture" / "layers"
GENERIC = "Generic controls exclude product layers"
FOUNDATIONS = "Client foundations exclude application policy"
WIDGETS = "Product widgets exclude application owners"
FEATURE_SCREENS = "Client features exclude screens"
SCREEN_OWNERS = "Client screens exclude application owners and transports"
PREFIX = "relaydeck.client."


def _contract(name: str) -> dict[str, object]:
    table = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["importlinter"]
    return next(c for c in table["contracts"] if c["name"] == name)


def _modules(name: str, key: str) -> list[str]:
    value = _contract(name)[key]
    assert isinstance(value, list)
    return [str(v) for v in value]


def _fixture_config(name: str, destination: Path) -> Path:
    """The named real contract, renamed to the fixture package and stripped of its ignores."""

    def lines(key: str) -> str:
        items = ", ".join(f'"{m.replace("relaydeck", "fxlayers", 1)}"' for m in _modules(name, key))
        return f"{key} = [{items}]"

    config = destination / "layers.toml"
    config.write_text(
        '[tool.importlinter]\nroot_package = "fxlayers"\n\n'
        "[[tool.importlinter.contracts]]\n"
        f'name = "{name}"\ntype = "forbidden"\n'
        f"{lines('source_modules')}\n{lines('forbidden_modules')}\n",
        encoding="utf-8",
    )
    return config


def _tree(tmp_path: Path) -> Path:
    copy = tmp_path / "tree"
    shutil.copytree(FIXTURE, copy)
    return copy


def test_the_generic_controls_contract_names_the_generic_layers() -> None:
    sources = _modules(GENERIC, "source_modules")
    for layer in ("components", "design_system"):
        assert PREFIX + layer in sources


def test_the_foundation_contract_forbids_every_product_layer() -> None:
    forbidden = _modules(FOUNDATIONS, "forbidden_modules")
    for layer in (
        "app",
        "app.hooks",
        "features",
        "components",
        "adapters",
        "design_system",
        "widgets",
        "screens",
    ):
        assert PREFIX + layer in forbidden


def test_every_client_package_is_placed_in_the_layer_contracts() -> None:
    """A new client package must be named by a contract or listed here on purpose."""
    foundations = {m.removeprefix(PREFIX) for m in _modules(FOUNDATIONS, "source_modules")}
    forbidden = {m.removeprefix(PREFIX) for m in _modules(FOUNDATIONS, "forbidden_modules")}
    generic = {m.removeprefix(PREFIX) for m in _modules(GENERIC, "source_modules")}
    other = {"assets"}  # data files, not code
    packages = {p.name for p in CLIENT.iterdir() if (p / "__init__.py").exists()}
    unplaced = packages - foundations - forbidden - generic - other
    assert unplaced == set(), f"add to a layer contract: {sorted(unplaced)}"
    assert generic - {"components"} <= forbidden


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("models", "design_system"),
        ("ports", "widgets"),
        ("platform", "design_system"),
        ("models", "screens"),
    ],
)
def test_a_foundation_importing_a_product_layer_is_refused(
    tmp_path: Path, source: str, target: str
) -> None:
    tree = _tree(tmp_path)
    (tree / "fxlayers/client" / source / "upward.py").write_text(
        f"import fxlayers.client.{target}\n", encoding="utf-8"
    )
    result = run_lint_imports(_fixture_config(FOUNDATIONS, tree), tree)
    assert result.returncode != 0, result.stdout
    assert f"fxlayers.client.{source}.upward" in result.stdout


def test_clean_foundations_pass(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    result = run_lint_imports(_fixture_config(FOUNDATIONS, tree), tree)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("layer", ["design_system"])
@pytest.mark.parametrize("product", ["app", "app.hooks", "features", "adapters", "screens"])
def test_a_generic_layer_importing_a_product_layer_is_refused(
    tmp_path: Path, layer: str, product: str
) -> None:
    tree = _tree(tmp_path)
    (tree / "fxlayers/client" / layer / "upward.py").write_text(
        f"import fxlayers.client.{product}\n", encoding="utf-8"
    )
    result = run_lint_imports(_fixture_config(GENERIC, tree), tree)
    assert result.returncode != 0, result.stdout
    assert f"fxlayers.client.{layer}.upward" in result.stdout


def test_product_widgets_have_their_own_boundary() -> None:
    assert PREFIX + "widgets" in _modules(WIDGETS, "source_modules")
    assert PREFIX + "widgets" not in _modules(GENERIC, "source_modules")
    assert set(_modules(WIDGETS, "forbidden_modules")) == {
        PREFIX + name for name in ("app", "app.hooks", "adapters", "screens")
    }


@pytest.mark.parametrize("product", ["app", "app.hooks", "adapters", "screens"])
def test_a_widget_importing_an_application_owner_is_refused(tmp_path: Path, product: str) -> None:
    tree = _tree(tmp_path)
    (tree / "fxlayers/client/widgets/upward.py").write_text(
        f"import fxlayers.client.{product}\n", encoding="utf-8"
    )
    result = run_lint_imports(_fixture_config(WIDGETS, tree), tree)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "fxlayers.client.widgets.upward" in result.stdout


def test_widgets_may_use_features_without_importing_application_owners(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    (tree / "fxlayers/client/widgets/panel.py").write_text(
        "import fxlayers.client.features\n", encoding="utf-8"
    )
    result = run_lint_imports(_fixture_config(WIDGETS, tree), tree)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("type_only", [False, True])
def test_features_cannot_import_screens(tmp_path: Path, type_only: bool) -> None:
    tree = _tree(tmp_path)
    statement = "import fxlayers.client.screens\n"
    (tree / "fxlayers/client/features/upward.py").write_text(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    " + statement
        if type_only
        else statement
    )
    result = run_lint_imports(_fixture_config(FEATURE_SCREENS, tree), tree)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "fxlayers.client.features.upward" in result.stdout


@pytest.mark.parametrize("target", ["app", "app.hooks", "adapters", "platform.transport"])
@pytest.mark.parametrize("type_only", [False, True])
def test_screens_cannot_import_application_owners_or_transports(
    tmp_path: Path, target: str, type_only: bool
) -> None:
    tree = _tree(tmp_path)
    statement = f"import fxlayers.client.{target}\n"
    (tree / "fxlayers/client/screens/upward.py").write_text(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    " + statement
        if type_only
        else statement
    )
    result = run_lint_imports(_fixture_config(SCREEN_OWNERS, tree), tree)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "fxlayers.client.screens.upward" in result.stdout


@pytest.mark.parametrize("contract", [FEATURE_SCREENS, SCREEN_OWNERS])
def test_clean_screen_layer_contracts_pass(tmp_path: Path, contract: str) -> None:
    tree = _tree(tmp_path)
    result = run_lint_imports(_fixture_config(contract, tree), tree)
    assert result.returncode == 0, result.stdout + result.stderr


CLIENT_LAYERS = (
    "app",
    "screens",
    "widgets",
    "features",
    "design_system",
    "platform",
    "ports",
    "models",
)


def test_client_layer_order_covers_every_pair() -> None:
    contract = _contract("Client layer order")
    assert contract["type"] == "layers"
    assert contract["containers"] == ["relaydeck.client"]
    assert contract["layers"] == [
        "main:app",
        *CLIENT_LAYERS[1:4],
        "design_system:components:adapters",
        *CLIENT_LAYERS[5:7],
        "models:asset_paths",
    ]
    assert contract["exhaustive"] is True
    assert not contract.get("ignore_imports")


@pytest.mark.parametrize("type_only", [False, True])
@pytest.mark.parametrize(
    "pair",
    [(lower, upper) for i, upper in enumerate(CLIENT_LAYERS) for lower in CLIENT_LAYERS[i + 1 :]],
)
def test_every_upward_client_layer_import_is_refused(
    tmp_path: Path, pair: tuple[str, str], type_only: bool
) -> None:
    source, target = pair
    contract = _contract("Client layer order")
    layers = contract["layers"]
    for name in (*CLIENT_LAYERS, "components", "adapters", "main", "asset_paths"):
        package = tmp_path / "fxorder/client" / name
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text("")
    (tmp_path / "fxorder/__init__.py").write_text("")
    (tmp_path / "fxorder/client/__init__.py").write_text("")
    statement = f"import fxorder.client.{target}\n"
    (tmp_path / "fxorder/client" / source / "bad.py").write_text(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    " + statement
        if type_only
        else statement
    )
    config = tmp_path / "contract.toml"
    config.write_text(
        '[tool.importlinter]\nroot_package = "fxorder"\n'
        "exclude_type_checking_imports = false\n[[tool.importlinter.contracts]]\n"
        'name = "Client layer order"\ntype = "layers"\n'
        'containers = ["fxorder.client"]\nexhaustive = true\nlayers = ' + json.dumps(layers) + "\n"
    )
    result = run_lint_imports(config, tmp_path)
    assert result.returncode != 0, result.stdout + result.stderr
    assert f"fxorder.client.{source}.bad" in result.stdout


@pytest.mark.parametrize("type_only", [False, True])
def test_host_foundations_cannot_import_companion(tmp_path: Path, type_only: bool) -> None:
    contract = _contract("Host foundations exclude application policy")
    assert "relaydeck.companion" in contract["forbidden_modules"]
    for module in [*contract["source_modules"], *contract["forbidden_modules"]]:
        parts = module.replace("relaydeck", "fxhost", 1).split(".")
        for count in range(1, len(parts) + 1):
            package = tmp_path.joinpath(*parts[:count])
            package.mkdir(exist_ok=True, parents=True)
            (package / "__init__.py").write_text("")
    statement = "import fxhost.companion\n"
    (tmp_path / "fxhost/host/bad.py").write_text(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    " + statement
        if type_only
        else statement
    )
    config = tmp_path / "contract.toml"
    config.write_text(
        '[tool.importlinter]\nroot_package = "fxhost"\n'
        "exclude_type_checking_imports = false\n[[tool.importlinter.contracts]]\n"
        'name = "Host boundary"\ntype = "forbidden"\n'
        + "\n".join(
            key + " = " + json.dumps([m.replace("relaydeck", "fxhost", 1) for m in contract[key]])
            for key in ("source_modules", "forbidden_modules")
        )
    )
    result = run_lint_imports(config, tmp_path)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "fxhost.host.bad" in result.stdout
