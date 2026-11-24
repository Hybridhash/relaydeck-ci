"""Golden: theme.toml must reproduce the look that predates it byte-for-byte.

The fixture was generated from the code before the theme file existed: every
value that existed then must still resolve identically; new names (loader
internals, unified roles) are allowed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

from relaydeck.client.design_system import theme
from relaydeck.client.design_system import tokens as tokens_module
from relaydeck.domain.agent_order import CONSOLE_STATUSES, status_rank

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "theme_golden.json"


def _json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    return value


def _public(module: object) -> dict[str, object]:
    out: dict[str, object] = {}
    for name in sorted(dir(module)):
        if name.startswith("__"):
            continue
        value = getattr(module, name)
        if callable(value) or isinstance(value, type):
            continue
        try:
            value = _json_value(value)
            json.dumps(value)
        except (TypeError, ValueError):
            continue
        out[name] = value
    return out


def _current() -> dict[str, object]:
    return {
        "theme": _public(theme),
        "palette_dark": _json_value(theme.palette(True)),
        "palette_light": _json_value(theme.palette(False)),
        # The ranking comes from the shared domain order.
        "tokens": {
            **_public(tokens_module),
            "STATUS_RANK": {status: status_rank(status) for status in CONSOLE_STATUSES},
        },
        "usage_dark": _json_value(tokens_module.usage_palette(True)),
        "usage_light": _json_value(tokens_module.usage_palette(False)),
        "types": {
            name: _json_value(getattr(tokens_module, name))
            for name in dir(tokens_module)
            if name.startswith("TYPE_")
        },
        "identities": {
            kind: [
                theme.agent_identity(kind, True).color,
                theme.agent_identity(kind, False).color,
                theme.agent_badge_ink(kind, False),
            ]
            for kind in ["alpha", "beta", "gamma", "delta", "shell", "", "zzz"]
        },
    }


def test_theme_values_match_pre_toml_golden() -> None:
    fixture = json.loads(FIXTURE.read_text())
    current = json.loads(json.dumps(_current()))
    for section, values in fixture.items():
        assert section in current
        if isinstance(values, dict):
            for key, expected in values.items():
                assert current[section][key] == expected, f"{section}.{key}"
        else:
            assert current[section] == values, section
