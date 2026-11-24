"""Control-tree snapshots of Settings (all rendered sections) and Pairing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import flet as ft

from relaydeck.client.app.appearance import AppearanceBridge
from relaydeck.client.features.pairing.service import PairingSnapshot
from relaydeck.client.screens.pairing.screen import PairingPanel
from relaydeck.companion.app.hosted_web.setup_panel import SetupPanel
from tests.support.host_harness import render_component, run
from tests.support.ui.navigation import _render_shell, _state
from tests.support.ui.snapshot import snapshot_scenario

GOLDEN = Path(__file__).parents[3] / "snapshots" / "golden"


def _snap(name: str, render: Callable[[], Awaitable[Any]]) -> None:
    run(snapshot_scenario(render, GOLDEN, name))


def test_settings_desktop_dark_snapshot() -> None:
    async def render() -> Any:
        return await _render_shell(
            _state(), destination="settings", viewport_width=1440, is_wide=True, dark=True
        )

    _snap("settings-1440-dark", render)


def test_settings_phone_dark_snapshot() -> None:
    async def render() -> Any:
        return await _render_shell(
            _state(), destination="settings", viewport_width=320, is_wide=False, dark=True
        )

    _snap("settings-320-dark", render)


def test_settings_desktop_light_snapshot() -> None:
    async def render() -> Any:
        return await _render_shell(
            _state(), destination="settings", viewport_width=1440, is_wide=True, dark=False
        )

    _snap("settings-1440-light", render)


def test_settings_acknowledgements_open_snapshot() -> None:
    async def render() -> Any:
        app = await _render_shell(
            _state(), destination="settings", viewport_width=390, is_wide=False, dark=True
        )
        await app.set_props_and_fire("settings-acknowledgements-toggle", {}, "click")
        await app.settle()
        return app

    _snap("settings-acknowledgements-390-dark", render)


class _FakeFlow:
    """The pairing panel's surface: a fixed snapshot, no network, no credentials."""

    def __init__(self, snapshot: PairingSnapshot) -> None:
        self.snapshot = snapshot

    def subscribe(self, _listener: Any) -> Callable[[], None]:
        return lambda: None

    def invalidate(self) -> None:
        pass

    async def begin(self, *_args: Any) -> None:
        pass


def _pairing(snapshot: PairingSnapshot) -> Callable[[], Awaitable[Any]]:
    async def render() -> Any:
        flow = _FakeFlow(snapshot)

        @ft.component
        def Host() -> Any:
            return ft.Column(
                controls=[
                    PairingPanel(
                        flow,  # type: ignore[arg-type]
                        endpoint="https://test-mac.synthetic.ts.net",
                        host_id="home-mac",
                    )
                ]
            )

        return await render_component(Host)

    return render


def test_pairing_setup_snapshot() -> None:
    _snap("pairing-setup", _pairing(PairingSnapshot()))


def test_pairing_waiting_snapshot() -> None:
    waiting = replace(
        PairingSnapshot(),
        phase="waiting",
        message="Compare this code with the one on your Mac.",
        request_id="synthetic-request",
        matching_code="482 910",
        fingerprint="SHA256:syntheticfingerprint",
        scope="home-mac",
        expiry="about 4 minutes left",
        busy=True,
    )
    _snap("pairing-waiting", _pairing(waiting))


def _setup_panel(dark: bool) -> Callable[[], Awaitable[Any]]:
    async def render() -> Any:
        appearance = AppearanceBridge(dark)

        @ft.component
        def Host() -> Any:
            return ft.Column(
                controls=[
                    SetupPanel(
                        report=lambda: (
                            ("Private address", "https://test-mac.synthetic.ts.net"),
                            ("Pairing", "Pair this Mac explicitly."),
                        ),
                        appearance=appearance,
                    )
                ]
            )

        return await render_component(Host)

    return render


def test_companion_setup_light_snapshot() -> None:
    _snap("companion-setup-light", _setup_panel(False))


def test_companion_setup_dark_snapshot() -> None:
    _snap("companion-setup-dark", _setup_panel(True))
