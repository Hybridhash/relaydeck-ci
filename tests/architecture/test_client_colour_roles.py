"""Client colours come from theme roles or semantic framework colours."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import flet as ft
import pytest

CLIENT = Path(__file__).resolve().parents[2] / "src/relaydeck/client"
HEX = re.compile(r"#[0-9a-fA-F]{3,8}(?:,[0-9.]+)?")
SEMANTIC = {"TRANSPARENT", "SURFACE", "SURFACE_CONTAINER_LOWEST"}
NAMED_COLOURS = {colour.value for colour in ft.Colors if colour.name not in SEMANTIC}


def colour_findings(text: str) -> list[int]:
    tree = ast.parse(text)
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update((item.asname or item.name, item.name) for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            aliases.update(
                (item.asname or item.name, f"{node.module}.{item.name}") for item in node.names
            )

    def qualified(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return qualified(node.value) + "." + node.attr
        return ""

    for _ in range(len(aliases) + len(tree.body) + 1):
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                value = qualified(node.value)
                if value.startswith("flet"):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and aliases.get(target.id) != value:
                            aliases[target.id] = value
                            changed = True
        if not changed:
            break

    found = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and HEX.fullmatch(node.value)
        ):
            found.add(node.lineno)
        if (
            isinstance(node, ast.Attribute)
            and qualified(node.value) in {"flet.Colors", "flet.controls.colors.Colors"}
            and node.attr.isupper()
            and node.attr not in SEMANTIC
        ):
            found.add(node.lineno)
        if (
            isinstance(node, ast.keyword)
            and node.arg
            and node.arg.endswith("color")
            and isinstance(node.value, ast.Constant)
            and node.value.value in NAMED_COLOURS
        ):
            found.add(node.value.lineno)
    return sorted(found)


def test_client_has_no_colour_literals() -> None:
    found = {
        str(path.relative_to(CLIENT)): lines
        for path in CLIENT.rglob("*.py")
        if (lines := colour_findings(path.read_text()))
    }
    assert found == {}


@pytest.mark.parametrize(
    "text",
    [
        'value = "#A9A2C8"',
        'value = "#abc"',
        'value = "#80112233"',
        "import flet as ft\nvalue = ft.Colors.BLACK",
        "from flet import Colors as C\nvalue = C.RED_300",
        "from flet.controls.colors import Colors\nvalue = Colors.BLUE",
        'control(color="black")',
        "import flet as ft\nC = ft.Colors\nvalue = C.BLACK",
    ],
)
def test_colour_literals_are_refused(text: str) -> None:
    assert colour_findings(text)


@pytest.mark.parametrize(
    "text",
    [
        "import flet as ft\nvalue = ft.Colors.TRANSPARENT",
        "import flet as ft\nvalue = ft.Colors.SURFACE",
        "import flet as ft\nvalue = ft.Colors.with_opacity(0.5, tokens.scrim)",
        "value = tokens.text_muted",
        'control(value="black")',
    ],
)
def test_theme_roles_and_semantic_colours_are_allowed(text: str) -> None:
    assert colour_findings(text) == []


def test_scrim_role_keeps_the_native_colour_value() -> None:
    from relaydeck.client.design_system.theme import SCRIM_COLOR, SCRIM_OPACITY

    assert ft.Colors.with_opacity(SCRIM_OPACITY, SCRIM_COLOR) == ft.Colors.with_opacity(
        SCRIM_OPACITY, ft.Colors.BLACK
    )


@pytest.mark.parametrize("value", [None, 1, "#000000", "bad name", "black,0.5"])
def test_scrim_data_refuses_malformed_names(value: object) -> None:
    from relaydeck.client.design_system.theme import _scrim_colour

    with pytest.raises(ValueError):
        _scrim_colour({"semantic": {"overlay": {"scrim": value, "context_text_muted": "#A9A2C8"}}})
