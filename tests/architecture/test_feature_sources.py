"""Feature sources are narrow interfaces, with transport construction in the app."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

from tests.support.architecture.contracts import run_lint_imports

ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "src/relaydeck/client"
CONTRACT = "Feature sources exclude concrete transports"
EXPECTED = {
    "update_notice": ("UpdateNoticeSource", {"updates"}),
    "web_push": (
        "WebPushSource",
        {"web_push_state", "web_push_preferences", "set_web_push_preferences", "web_push_disable"},
    ),
    "management": (
        "HostsSource",
        {
            "me",
            "hosts",
            "devices",
            "create_enrollment",
            "enrollment_status",
            "revoke_device",
            "revoke_host",
        },
    ),
    "inventory": ("InventorySource", {"agents", "mark_agent_read"}),
    "rename": ("RenameSource", {"submit_command", "command_status"}),
    "usage": ("UsageSource", {"usage", "refresh_limits"}),
}


@pytest.mark.parametrize("feature", EXPECTED)
def test_each_feature_has_only_its_required_source_operations(feature: str) -> None:
    name, expected = EXPECTED[feature]
    tree = ast.parse((CLIENT / "features" / feature / "source.py").read_text())
    source = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    assert any(ast.unparse(base) == "Protocol" for base in source.bases)
    assert {n.name for n in source.body if isinstance(n, ast.AsyncFunctionDef)} == expected
    assert not any(
        isinstance(n, ast.Attribute) and n.attr in {"_request", "base_url"} for n in ast.walk(tree)
    )


def _contract() -> dict[str, object]:
    contracts = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"][
        "contracts"
    ]
    return next(c for c in contracts if c["name"] == CONTRACT)


SOURCE_MODULES = {
    "update_notice",
    "web_push",
    "management",
    "inventory.paging",
    "inventory.source",
    "rename",
    "usage.source",
}


def test_source_contract_covers_consumers_without_exceptions() -> None:
    contract = _contract()
    assert set(contract["source_modules"]) == {
        "relaydeck.client.features." + name for name in SOURCE_MODULES
    }
    assert contract["forbidden_modules"] == ["relaydeck.client.platform.transport"]
    assert not contract.get("ignore_imports")
    assert not contract.get("allow_indirect_imports", False)


@pytest.mark.parametrize("feature", sorted(SOURCE_MODULES))
@pytest.mark.parametrize("type_only", [False, True])
def test_a_feature_source_importing_the_concrete_transport_is_refused(
    tmp_path: Path, feature: str, type_only: bool
) -> None:
    contract = _contract()
    source = [str(m).replace("relaydeck", "fxsources", 1) for m in contract["source_modules"]]
    targets = [str(m).replace("relaydeck", "fxsources", 1) for m in contract["forbidden_modules"]]
    for name in [*source, *targets]:
        parts = name.split(".")
        for i in range(1, len(parts) + 1):
            folder = tmp_path.joinpath(*parts[:i])
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "__init__.py").write_text("", encoding="utf-8")
    directory = tmp_path / "fxsources/client/features" / feature.replace(".", "/")
    forbidden = "import fxsources.client.platform.transport\n"
    (directory / "bad.py").write_text(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    " + forbidden
        if type_only
        else forbidden,
        encoding="utf-8",
    )
    config = tmp_path / "contract.toml"
    config.write_text(
        '[tool.importlinter]\nroot_package = "fxsources"\n\n'
        '[[tool.importlinter.contracts]]\nname = "Source boundary"\ntype = "forbidden"\n'
        f"source_modules = {source!r}\nforbidden_modules = {targets!r}\n",
        encoding="utf-8",
    )
    result = run_lint_imports(config, tmp_path)
    assert result.returncode != 0, result.stdout + result.stderr
    assert f"fxsources.client.features.{feature}" in result.stdout


@pytest.mark.parametrize(
    ("path", "name", "method"),
    [
        ("ports/health.py", "ServerHealthSource", "metrics"),
        ("ports/commands.py", "CommandStatusSource", "command_status"),
    ],
)
def test_health_and_recovery_sources_have_one_read(path: str, name: str, method: str) -> None:
    tree = ast.parse((CLIENT / path).read_text())
    source = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name
    )
    assert any(ast.unparse(base) == "Protocol" for base in source.bases)
    assert {node.name for node in source.body if isinstance(node, ast.AsyncFunctionDef)} == {method}


def test_shell_test_double_is_not_shipped() -> None:
    tree = ast.parse((CLIENT / "platform/transport/shell_ops.py").read_text())
    assert not any(
        isinstance(node, ast.ClassDef) and node.name == "ScriptedShellGatewayOps"
        for node in tree.body
    )


def test_screen_tests_use_sources_instead_of_transport_implementations() -> None:
    for path in (ROOT / "tests/ui/client/screens").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("relaydeck.client.platform.transport"), (
                    path
                )
            elif isinstance(node, ast.Import):
                assert not any(
                    alias.name.startswith("relaydeck.client.platform.transport")
                    for alias in node.names
                ), path
