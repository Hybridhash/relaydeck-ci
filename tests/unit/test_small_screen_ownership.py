"""Screen entries expose their canonical controls and shared typography."""

from __future__ import annotations

import importlib

import pytest

from relaydeck.client.design_system.theme import TypeStyle
from relaydeck.client.design_system.typography import TYPE_ROW
from relaydeck.client.screens.console import screen as console
from relaydeck.client.screens.console.parts import chips, lists
from relaydeck.client.screens.settings import screen
from relaydeck.client.screens.settings.parts import (
    hosts_pane,
    server_health,
    server_health_parts,
    update_notice,
)


def test_settings_entry_exposes_the_canonical_controls() -> None:
    assert screen.ManagementPane is hosts_pane.ManagementPane
    assert screen.HealthPanel is server_health.HealthPanel
    assert screen.UpdateNoticePanel is update_notice.UpdateNoticePanel


def test_settings_parts_share_the_canonical_row_typography() -> None:
    assert TYPE_ROW == TypeStyle(size=13, weight=400, height=18 / 13)
    assert server_health.TYPE_ROW is TYPE_ROW
    assert server_health_parts.TYPE_ROW is TYPE_ROW
    assert update_notice.TYPE_ROW is TYPE_ROW


def test_the_retired_settings_entry_is_not_forwarded() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("relaydeck.client.features.settings.components")


def test_console_entry_exposes_the_canonical_controls_and_builders() -> None:
    for name in ("filter_chip_row", "filter_field", "host_chips", "space_chips"):
        assert getattr(console, name) is getattr(chips, name)
    for name in (
        "AgentsList",
        "ConnectionBanner",
        "HostsList",
        "SearchField",
        "ShellCardsList",
        "ShellsList",
    ):
        assert getattr(console, name) is getattr(lists, name)
