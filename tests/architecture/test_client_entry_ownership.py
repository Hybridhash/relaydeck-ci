"""Client entry ownership and the retained compatibility imports."""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
MOVED = (
    ("client.hooks", "client.app.hooks"),
    ("client.hooks.actions", "client.app.hooks.actions"),
    ("client.hooks.appearance", "client.app.hooks.appearance"),
    ("client.hooks.command_context", "client.app.workflows.command_context"),
    ("client.hooks.creation", "client.app.hooks.creation"),
    ("client.hooks.inventory", "client.app.hooks.inventory"),
    ("client.hooks.navigation", "client.app.hooks.navigation"),
    ("client.hooks.preferences", "client.app.hooks.preferences"),
    ("client.hooks.retained", "client.app.hooks.retained"),
    ("client.hooks.sheets", "client.app.hooks.sheets"),
    ("client.hooks.support", "client.app.hooks.support"),
    ("client.hooks.sync", "client.app.hooks.sync"),
    ("client.hooks.typing_focus", "client.app.hooks.typing_focus"),
    ("client.hooks.views", "client.app.hooks.views"),
    ("client.packaged", "client.app.native.entry"),
    ("client.packaged_runtime", "client.app.native.runtime"),
    ("client.serve", "companion.app.hosted_web.serve"),
    ("client.adapters.server_commands", "companion.app.hosted_web.storage"),
    ("client.components.setup", "companion.app.hosted_web.setup_panel"),
    ("client.features.auth.web_recovery", "companion.app.hosted_web.web_recovery"),
)


@pytest.mark.parametrize(("old", "current"), MOVED)
def test_retired_entries_are_absent_and_canonical_owners_exist(old: str, current: str) -> None:
    old_module, current_module = "relaydeck." + old, "relaydeck." + current
    old_file = SRC.joinpath(*old_module.split(".")).with_suffix(".py")
    old_package = SRC.joinpath(*old_module.split(".")) / "__init__.py"
    assert not old_file.exists()
    assert not old_package.exists()
    try:
        old_spec = importlib.util.find_spec(old_module)
    except ModuleNotFoundError:
        old_spec = None
    assert old_spec is None, old_module
    current_spec = importlib.util.find_spec(current_module)
    assert current_spec is not None and current_spec.origin is not None
    expected = SRC.joinpath(*current_module.split("."))
    expected = expected / "__init__.py" if expected.is_dir() else expected.with_suffix(".py")
    assert Path(current_spec.origin).resolve() == expected.resolve()
    assert expected.is_file()


def test_compatibility_imports_are_headless_canonical_aliases(tmp_path: Path) -> None:
    code = f"""
import importlib, importlib.abc, pathlib, sys
sys.dont_write_bytecode = True
sys.path.insert(0, {str(SRC)!r})
attempts = []
class RefuseFramework(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('flet', 'relaydeck_terminal', 'relaydeck_notifications')):
            attempts.append(fullname)
            raise AssertionError('framework import: ' + fullname)
sys.meta_path.insert(0, RefuseFramework())
from relaydeck.contracts.storage import CredentialStore
from relaydeck.client.adapters import ports
from relaydeck.client.components.design_system import font_versions as old
from relaydeck.client.design_system import font_versions as current
assert ports.__all__ == ['CredentialStore']
assert ports.CredentialStore is CredentialStore
for name in ('FONTS_DIR', 'URL_PREFIX', 'fingerprint', 'is_current', 'versioned_url'):
    assert getattr(old, name) is getattr(current, name), name
for name, module in list(sys.modules.items()):
    if name.startswith('relaydeck.') and getattr(module, '__file__', None):
        assert pathlib.Path(module.__file__).resolve().is_relative_to({str(SRC)!r}), name
assert attempts == [], attempts
for parent in ('relaydeck.client.adapters', 'relaydeck.client.components',
               'relaydeck.client.components.design_system'):
    assert '__getattr__' not in vars(importlib.import_module(parent))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


COMPATIBILITY = {
    "adapters/ports.py",
    "components/design_system/font_versions.py",
}
PUBLIC_ENTRIES = {
    "screens/console/screen.py",
    "features/composer/ui.py",
    "features/terminal/ui.py",
    "features/usage/ui.py",
    "features/rename/ui.py",
}


def _forwarding_modules(root: Path) -> set[str]:
    found = set()
    for path in root.rglob("*.py"):
        if path.name in {"__init__.py", "api.py"}:
            continue
        tree = ast.parse(path.read_text())
        body = [
            node
            for node in tree.body
            if not (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            )
        ]
        if body and all(
            isinstance(node, ast.Import | ast.ImportFrom)
            or isinstance(node, ast.Assign)
            and all(
                isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
            )
            for node in body
        ):
            found.add(path.relative_to(root).as_posix())
    return found


def test_compatibility_paths_are_the_complete_set_and_expire_together() -> None:
    client = SRC / "relaydeck/client"
    assert _forwarding_modules(client) - PUBLIC_ENTRIES == COMPATIBILITY
    note = (ROOT / "docs/development/compatibility.md").read_text()
    for name in sorted(COMPATIBILITY):
        doc = ast.get_docstring(ast.parse((client / name).read_text()))
        assert doc and "0.3.2" in doc
        row = next(line for line in note.splitlines() if "client/" + name in line)
        assert "0.3.2" in row


def test_an_unlisted_forwarding_module_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "extra.py"
    path.write_text("from canonical import value as value\n")
    assert _forwarding_modules(tmp_path) - PUBLIC_ENTRIES - COMPATIBILITY == {"extra.py"}
