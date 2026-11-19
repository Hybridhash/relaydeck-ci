"""Dynamic series roles select color fields without exposing record internals."""

from dataclasses import fields

import pytest

from relaydeck.client.design_system.tokens import usage_palette
from relaydeck.client.screens.usage.parts.rows import _series_color


@pytest.mark.parametrize("dark", [False, True])
def test_each_color_field_retains_its_role(dark: bool) -> None:
    colors = usage_palette(dark)
    for field in fields(colors):
        assert _series_color(field.name, colors) == getattr(colors, field.name)


@pytest.mark.parametrize("role", [None, "", "unknown", "__dict__", "__module__", "__class__"])
@pytest.mark.parametrize("dark", [False, True])
def test_unknown_roles_use_the_first_series(role: str | None, dark: bool) -> None:
    colors = usage_palette(dark)
    assert _series_color(role, colors) == colors.series_1
