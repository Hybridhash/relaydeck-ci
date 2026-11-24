"""Terminal surface owners keep their documented shape after allocation."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[5]
CLIENT = ROOT / "src/relaydeck/client"
MAX_MODULE_LINES = 800
MAX_FUNCTION_LINES = 250
# The renderer and the two earlier widget owners were outside the surfaces package.
OTHER_WIDGET_OWNERS = {"screen", "tools_keys", "window"}
FUNCTION_EXCEPTIONS = {"AgentTerminalScreen"}
BUILDERS = (
    "agent_header",
    "terminal_host",
    "agent_dock",
    "agent_panels",
    "phone_dock",
    "input_area",
    "select_stack",
)
OWNERS = {
    "actions": "relaydeck.client.widgets.terminal.actions",
    "agent_dock": "relaydeck.client.screens.terminal.agent_dock",
    "agent_header": "relaydeck.client.screens.terminal.agent_header",
    "agent_hooks": "relaydeck.client.screens.terminal.agent_hooks",
    "agent_panels": "relaydeck.client.screens.terminal.agent_panels",
    "agent_screen": "relaydeck.client.screens.terminal.agent_screen",
    "agent_switcher": "relaydeck.client.widgets.terminal.agent_switcher",
    "common": "relaydeck.client.widgets.terminal.common",
    "direct_typing": "relaydeck.client.widgets.terminal.direct_typing",
    "direct_typing_state": "relaydeck.client.widgets.terminal.direct_typing_state",
    "input_area": "relaydeck.client.widgets.terminal.input_area",
    "keyboard_overlap": "relaydeck.client.widgets.terminal.keyboard_overlap",
    "mount": "relaydeck.client.screens.terminal.screen",
    "paste_entry": "relaydeck.client.widgets.terminal.paste_entry",
    "phone_composer": "relaydeck.client.widgets.terminal.phone_composer",
    "phone_dock": "relaydeck.client.widgets.terminal.phone_dock",
    "screen_overlays": "relaydeck.client.widgets.terminal.screen_overlays",
    "select_stack": "relaydeck.client.widgets.terminal.select_stack",
    "sessions": "relaydeck.client.features.terminal.sessions",
    "terminal_host": "relaydeck.client.widgets.terminal.terminal_host",
    "tools_popover": "relaydeck.client.widgets.terminal.tools_popover",
}
PUBLIC_OWNERS = {
    "mount_agent_screen": ("relaydeck.client.screens.terminal.screen", "mount_agent_screen"),
    "AgentTerminalScreen": (
        "relaydeck.client.screens.terminal.agent_screen",
        "AgentTerminalScreen",
    ),
    "DetailSessions": ("relaydeck.client.features.terminal.sessions", "DetailSessions"),
    "SnoopingTerminalSocket": (
        "relaydeck.client.features.terminal.sessions",
        "SnoopingTerminalSocket",
    ),
    "selection_actions": ("relaydeck.client.widgets.terminal.actions", "selection_actions"),
    "phone_selection_actions": (
        "relaydeck.client.widgets.terminal.actions",
        "phone_selection_actions",
    ),
    "request_terminal_keyboard": (
        "relaydeck.client.widgets.terminal.actions",
        "request_terminal_keyboard",
    ),
    "use_mobile_key_actions": (
        "relaydeck.client.widgets.terminal.actions",
        "use_mobile_key_actions",
    ),
    "use_direct_keyboard": (
        "relaydeck.client.widgets.terminal.direct_typing",
        "use_direct_keyboard",
    ),
    "use_paste_fallback": ("relaydeck.client.widgets.terminal.direct_typing", "use_paste_fallback"),
    "direct_typing_allowed": (
        "relaydeck.client.widgets.terminal.direct_typing",
        "direct_typing_allowed",
    ),
    "DirectTypingField": ("relaydeck.client.widgets.terminal.direct_typing", "DirectTypingField"),
    "drive_recovery": ("relaydeck.client.features.terminal.sessions", "drive_recovery"),
    "offered_position": ("relaydeck.client.features.terminal.sessions", "offered_position"),
    "gated_composer_send": ("relaydeck.client.features.terminal.sessions", "gated_composer_send"),
    "DRIVE_IDLE": ("relaydeck.client.features.terminal.sessions", "DRIVE_IDLE"),
    "DRIVE_RESUMED": ("relaydeck.client.features.terminal.sessions", "DRIVE_RESUMED"),
    "DRIVE_REATTACHED": ("relaydeck.client.features.terminal.sessions", "DRIVE_REATTACHED"),
    "DRIVE_REFUSED_EXITED": ("relaydeck.client.features.terminal.sessions", "DRIVE_REFUSED_EXITED"),
    "EXIT_DETAILS": ("relaydeck.client.features.terminal.sessions", "EXIT_DETAILS"),
    "SEND_BLOCKED_IME": ("relaydeck.client.features.terminal.sessions", "SEND_BLOCKED_IME"),
    "TAKEOVER_DISCLOSURE_AGENT": (
        "relaydeck.client.features.terminal.sessions",
        "TAKEOVER_DISCLOSURE_AGENT",
    ),
    "TAKEOVER_MESSAGE": ("relaydeck.client.widgets.terminal.common", "TAKEOVER_MESSAGE"),
    "PASTE_FALLBACK_HINT": (
        "relaydeck.client.widgets.terminal.direct_typing",
        "PASTE_FALLBACK_HINT",
    ),
    "DirectInput": ("relaydeck.client.widgets.terminal.actions", "DirectInput"),
    "AGENT_DETAIL_KEY": ("relaydeck.client.widgets.terminal.common", "AGENT_DETAIL_KEY"),
    "AGENT_HEADER_KEY": ("relaydeck.client.widgets.terminal.common", "AGENT_HEADER_KEY"),
    "AGENT_KEYS_SCROLL_KEY": ("relaydeck.client.widgets.terminal.common", "AGENT_KEYS_SCROLL_KEY"),
    "AGENT_MORE_KEY": ("relaydeck.client.widgets.terminal.common", "AGENT_MORE_KEY"),
    "AGENT_TITLE_KEY": ("relaydeck.client.widgets.terminal.common", "AGENT_TITLE_KEY"),
}


def _modules() -> dict[str, Path]:
    found = {}
    for folder in ("screens/terminal", "widgets/terminal"):
        for path in (CLIENT / folder).glob("*.py"):
            if path.name == "__init__.py":
                continue
            if folder == "widgets/terminal" and path.stem in OTHER_WIDGET_OWNERS:
                continue
            module = "relaydeck.client." + str(path.relative_to(CLIENT).with_suffix("")).replace(
                "/", "."
            )
            found[module] = path
    found["relaydeck.client.features.terminal.sessions"] = CLIENT / "features/terminal/sessions.py"
    return found


@pytest.mark.parametrize("name", sorted(_modules()))
def test_every_module_has_a_docstring_and_stays_within_the_size_budget(name: str) -> None:
    path = _modules()[name]
    tree = ast.parse(path.read_text())
    assert ast.get_docstring(tree), name
    assert len(path.read_text().splitlines()) <= MAX_MODULE_LINES, name
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            size = node.end_lineno - first + 1 if node.end_lineno else 0
            if node.name not in FUNCTION_EXCEPTIONS:
                assert size <= MAX_FUNCTION_LINES, f"{name}.{node.name} is {size} lines"


def test_the_documented_modules_exist() -> None:
    expected = {
        "sessions",
        "common",
        "actions",
        "direct_typing",
        "phone_composer",
        "agent_screen",
        "agent_hooks",
        "mount",
        *BUILDERS,
    }
    assert expected <= set(OWNERS)
    assert set(OWNERS.values()) <= set(_modules())
    assert len(OWNERS) == 21


def test_surface_initializers_are_documented_and_inert() -> None:
    for parent in {path.parent for path in _modules().values()}:
        tree = ast.parse((parent / "__init__.py").read_text())
        assert ast.get_docstring(tree)
        assert all(
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            for node in tree.body
        )


@pytest.mark.parametrize("name", BUILDERS)
def test_builders_are_plain_functions_not_components(name: str) -> None:
    module = importlib.import_module(OWNERS[name])
    tree = ast.parse(Path(module.__file__ or "").read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                assert "component" not in ast.unparse(decorator), (name, node.name)


def test_public_names_import_from_their_canonical_owners() -> None:
    for name, (owner, member) in PUBLIC_OWNERS.items():
        module = importlib.import_module(owner)
        assert hasattr(module, member), name
        tree = ast.parse(Path(module.__file__ or "").read_text())
        definitions = {
            node.name
            for node in tree.body
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        }
        definitions.update(
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        )
        assert member in definitions, name


def test_typing_clock_lives_where_the_keyboard_hook_reads_it() -> None:
    import relaydeck.client.widgets.terminal as terminal
    from relaydeck.client.widgets.terminal import direct_typing

    assert hasattr(direct_typing, "_typing_now")
    assert not hasattr(terminal, "_typing_now")
