"""Where the design-system modules live, which old paths still resolve, and what they alias."""

from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path

import pytest

import relaydeck.client as client_package
from relaydeck.client.components.design_system import font_versions as compat_font_versions
from relaydeck.client.design_system import font_versions, fonts, tokens

C = "relaydeck.client"


@pytest.mark.parametrize(
    "gone",
    [
        f"{C}.theme",
        f"{C}.components.appearance",
        f"{C}.components.layout",
        f"{C}.components.rail",
        f"{C}.components.input_chrome",
        f"{C}.components.status",
        f"{C}.components.virtual_list",
        f"{C}.components.design_system.cards",
        f"{C}.components.design_system.fonts",
        f"{C}.components.design_system.navigation",
        f"{C}.components.design_system.primitives",
        f"{C}.components.design_system.shell",
        f"{C}.components.design_system.shells",
        f"{C}.components.design_system.surfaces",
        f"{C}.components.design_system.terminal_window",
        f"{C}.components.design_system.toast",
        f"{C}.components.design_system.tokens",
        f"{C}.features.a11y",
        f"{C}.features.a11y.announcer",
        f"{C}.features.a11y.contrast",
        f"{C}.features.a11y.focus",
        f"{C}.design_system.rail",
        f"{C}.design_system.accessibility.contrast",
        f"{C}.design_system.accessibility.focus",
        f"{C}.ports.lifecycle",
    ],
)
def test_old_module_paths_are_gone(gone: str) -> None:
    try:
        spec = importlib.util.find_spec(gone)
    except ModuleNotFoundError:  # the parent package is gone too
        return
    assert spec is None


@pytest.mark.parametrize(
    ("module", "names"),
    [
        (f"{C}.design_system.theme", ["TOKENS", "palette", "agent_identity", "layout_mode"]),
        (f"{C}.design_system.layout", ["rail_width", "list_width", "sidebar_width"]),
        (f"{C}.design_system.status", ["StatusDot"]),
        (f"{C}.design_system.appearance", ["build_theme", "chrome_style"]),
        (f"{C}.design_system.surfaces", ["SurfaceFrame", "MutedText", "selected_outline"]),
        (f"{C}.design_system.inputs", ["FilterField"]),
        (f"{C}.design_system.navigation", ["ConsoleBottomBar", "ConsoleRail"]),
        (f"{C}.design_system.lists.virtual_list", ["VirtualList"]),
        (f"{C}.design_system.accessibility.announcer", ["LiveRegion"]),
        ("tests.support.ui.contrast", ["contrast_ratio"]),
        ("tests.support.ui.focus", ["focus_order"]),
        ("relaydeck.devtools.control_tree", ["iter_controls"]),
        (f"{C}.app.appearance", ["AppearanceBridge", "configure_page_theme"]),
        (f"{C}.app.shell.destinations", ["DESTINATIONS", "DEST_SETTINGS"]),
        (f"{C}.models.changes_summary", ["ChangesRow"]),
        (f"{C}.widgets.sessions.cards", ["AgentCard", "CollapsedAgentTile"]),
        (f"{C}.widgets.sessions.pills", ["AgentPill", "HostChip", "SpaceChip", "ConsoleHeader"]),
        (f"{C}.widgets.sessions.shell_cards", ["ShellCard", "ShellListRow", "TerminalsHeader"]),
    ],
)
def test_names_live_at_their_new_module(module: str, names: list[str]) -> None:
    loaded = importlib.import_module(module)
    for name in names:
        value = getattr(loaded, name)
        owner = getattr(value, "__module__", module)
        assert owner in {module, "dataclasses", "builtins", "typing"}, f"{module}.{name} is {owner}"


def test_split_names_left_the_modules_they_were_split_from() -> None:
    assert not hasattr(tokens, "STATUS_RANK")
    assert not hasattr(tokens, "status_rank")
    assert not hasattr(tokens, "sort_keys")
    primitives = importlib.import_module(f"{C}.design_system.primitives")
    for name in ("AgentPill", "HostChip", "SpaceChip", "ConsoleHeader"):
        assert not hasattr(primitives, name)
    navigation = importlib.import_module(f"{C}.design_system.navigation")
    assert not hasattr(navigation, "DESTINATIONS")
    assert not hasattr(navigation, "DEST_SETTINGS")


def test_the_terminal_window_logger_follows_its_module() -> None:
    window = importlib.import_module(f"{C}.widgets.terminal.window")
    assert window.logger.name == f"{C}.widgets.terminal.window"


def test_the_font_versions_compatibility_path_gives_the_same_objects() -> None:
    for name in ("FONTS_DIR", "URL_PREFIX", "fingerprint", "is_current", "versioned_url"):
        assert getattr(compat_font_versions, name) is getattr(font_versions, name), name


def test_fonts_share_the_asset_directories_with_font_versions() -> None:
    assert fonts.ASSETS_DIR is font_versions.ASSETS_DIR
    assert fonts.FONTS_DIR is font_versions.FONTS_DIR
    package_dir = Path(client_package.__file__).resolve().parent
    assert fonts.ASSETS_DIR == package_dir / "assets" / "design_system"
    assert fonts.FONTS_DIR.is_dir()
    assert fonts.DOT_TILE.is_file()


@pytest.mark.parametrize("module", [f"{C}.components", f"{C}.components.design_system"])
def test_the_compatibility_parents_are_inert(module: str) -> None:
    spec = importlib.util.find_spec(module)
    assert spec is not None and spec.origin is not None
    body = ast.parse(Path(spec.origin).read_text()).body
    assert len(body) == 1 and isinstance(body[0], ast.Expr), "only a docstring"
