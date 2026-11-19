"""Startup dependency direction must work without a warmed module cache."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "first",
    [
        "relaydeck.client.design_system.theme",
        "relaydeck.client.design_system.tokens",
        "relaydeck.client.design_system.fonts",
        "relaydeck.client.design_system.primitives",
    ],
)
def test_fresh_theme_application_import_and_public_exports(first: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"""
import {first}
import relaydeck.client.design_system.theme as theme
import relaydeck.client.app.root
import relaydeck.client.design_system as design_system
from relaydeck.client.design_system import fonts, tokens
assert theme.MIN_TOUCH == tokens.MIN_TOUCH == 48
assert theme.TEXT_FONT_FALLBACK[0] == fonts.FAMILY_SYMBOLS
# The package initializer exports nothing: only imported submodules appear on it.
exported = [
    name
    for name, value in vars(design_system).items()
    if not name.startswith('__') and not hasattr(value, '__file__')
]
assert exported == [], exported
try:
    design_system.nonexistent_component
except AttributeError:
    pass
else:
    raise AssertionError('Unknown export must fail')
""",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
