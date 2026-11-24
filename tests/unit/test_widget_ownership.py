"""Widget ownership, pure route values, and shared imported objects."""

from __future__ import annotations

import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

C = "relaydeck.client"


@pytest.mark.parametrize(
    "name",
    [
        "components.command_recovery",
        "components.host_context",
        "components.session_rows",
        "components.design_system.connect",
        "components.design_system.hosts",
        "features.inventory.routing",
        "features.inventory.agent_gestures",
        "features.inventory.components.context",
        "features.inventory.components.models",
        "features.inventory.components.panes",
        "features.inventory.components.shell",
        "features.inventory.components.shell_frame",
        "features.inventory.components.shell_panes",
        "features.inventory.components.state",
        "features.git.ask_runtime",
        "features.git.wiring",
        "features.git.page_tasks",
        "features.usage.components.loading",
        "starter_shell",
    ],
)
def test_old_widget_and_frame_module_paths_are_absent(name: str) -> None:
    module = f"{C}.{name}"
    try:
        spec = importlib.util.find_spec(module)
    except ModuleNotFoundError as error:
        assert error.name is not None
        assert module == error.name or module.startswith(error.name + ".")
    else:
        assert spec is None


@pytest.mark.parametrize(
    ("path", "symbol"),
    [
        ("widgets.command_recovery", "CommandRecovery"),
        ("widgets.hosts.host_context", "HostContextHeader"),
        ("widgets.hosts.hosts", "HostCard"),
        ("widgets.sessions.rows", "agent_tile"),
        ("widgets.sessions.gestures", "AgentGestures"),
        ("widgets.connect.connect", "ConnectPanel"),
        ("widgets.terminal.tools_keys", "ToolsKeysDock"),
        ("widgets.usage.card", "UsageCard"),
        ("widgets.usage.model", "ConsoleCardModel"),
        ("widgets.usage.cards", "console_card"),
        ("design_system.loading", "LoadingBars"),
        ("design_system.typography", "ui"),
        ("features.rename.menu", "AgentMenu"),
        ("features.pairing.approval", "approve_command"),
        ("features.usage.hooks", "use_usage_feed"),
        ("features.usage.presentation", "screen_state"),
        ("models.routing", "RouteState"),
        ("app.navigation", "RouteBridge"),
        ("app.shell.shell", "AppShell"),
        ("app.shell.navigation", "AppShell"),
        ("app.workflows.ask", "AskRuntime"),
        ("app.workflows.changes", "use_changes_wiring"),
        ("app.workflows.page_tasks", "PageTasks"),
    ],
)
def test_moved_symbols_have_one_declared_owner(path: str, symbol: str) -> None:
    module = importlib.import_module(f"{C}.{path}")
    obj = getattr(module, symbol)
    # Flet components carry the original callable as their wrapped function.
    assert obj.__module__ == module.__name__


def test_residual_usage_imports_share_the_canonical_objects() -> None:
    from relaydeck.client.features.usage import hooks, presentation
    from relaydeck.client.screens.usage import model as screen_model
    from relaydeck.client.screens.usage import screen as panel

    assert panel.use_usage_feed is hooks.use_usage_feed
    assert panel.screen_state is presentation.screen_state
    for name in (
        "STATE_LOADING",
        "STATE_READY",
        "STATE_OFFLINE",
        "STATE_CAPACITY",
        "STATE_PAGE_TOO_LARGE",
        "STATE_UNAVAILABLE",
        "STATE_DISABLED",
        "STATE_NO_PROVIDERS",
        "money",
        "zone_label",
        "reset",
    ):
        assert getattr(screen_model, name) is getattr(presentation, name), name
    assert screen_model.relative_time is presentation.relative
    assert screen_model.reading_as_of is presentation.as_of
    assert not hasattr(panel, "UsageCard")
    assert not hasattr(screen_model, "ConsoleCardModel")


def test_pairing_widget_uses_the_canonical_approval_objects() -> None:
    from relaydeck.client.features.pairing import approval
    from relaydeck.client.widgets.connect import connect

    assert connect.ConnectState is approval.ConnectState
    assert connect.valid_request is approval.valid_request
    assert connect.approve_command is approval.approve_command


def test_usage_renderers_share_the_design_system_helpers() -> None:
    from relaydeck.client.design_system import loading, typography
    from relaydeck.client.screens.usage.parts import cards, primitives
    from relaydeck.client.widgets.usage import cards as widget_cards

    assert primitives.ui is typography.ui
    assert cards.ui is typography.ui
    assert widget_cards.ui is typography.ui
    assert cards.LoadingBars is loading.LoadingBars
    assert widget_cards.LoadingBars is loading.LoadingBars


def test_console_usage_rendering_does_not_load_screen_components() -> None:
    root = Path(__file__).resolve().parents[2]
    code = f"""
import json, sys
sys.path.insert(0, {str(root / "src")!r})
import relaydeck.client.widgets.usage.card
print(json.dumps([n for n in sys.modules
                  if n.startswith('relaydeck.client.screens.usage.parts')]))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == []


def test_key_encoder_and_dock_share_the_canonical_input_values() -> None:
    from relaydeck.client.features.terminal import keys
    from relaydeck.client.models.input import keys as values
    from relaydeck.client.widgets.terminal import tools_keys

    for name in ("MOD_CTRL", "MOD_ALT", "MOD_SHIFT", "MODIFIERS", "SHIFT_TAB", "SHIFT_ENTER"):
        assert getattr(keys, name) is getattr(values, name)
        assert getattr(tools_keys, name) is getattr(values, name)
    assert not hasattr(keys, "ToolsKeysDock")


def test_moved_loggers_keep_their_declared_names() -> None:
    from relaydeck.client.app.workflows import page_tasks
    from relaydeck.client.widgets.sessions import gestures

    assert gestures.LOG.name == "relaydeck.inventory"
    assert page_tasks.logger.name == page_tasks.__name__


def test_presentation_values_import_without_ui_or_theme() -> None:
    root = Path(__file__).resolve().parents[2]
    code = f"""
import importlib, json, sys
sys.path.insert(0, {str(root / "src")!r})
for name in ('{C}.models.routing', '{C}.features.pairing.approval',
             '{C}.features.usage.presentation', '{C}.widgets.usage.model'):
    importlib.import_module(name)
print(json.dumps([n for n in sys.modules if n == 'flet' or n.startswith('flet.')
                 or n.startswith('{C}.design_system')]))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == []
