"""Page appearance: installs the themes and fonts and mirrors brightness changes."""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from relaydeck.client.design_system.appearance import build_theme
from relaydeck.client.design_system.fonts import page_fonts


class AppearanceBridge:
    """Mirror page brightness without remounting the connected application."""

    def __init__(self, dark: bool) -> None:
        self.dark = dark
        self._listeners: list[Callable[[bool], None]] = []

    def set_dark(self, dark: bool) -> None:
        if self.dark == dark:
            return
        self.dark = dark
        for listener in tuple(self._listeners):
            listener(dark)

    def subscribe(self, listener: Callable[[bool], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe


def configure_page_theme(
    page: ft.Page, *, mode: ft.ThemeMode = ft.ThemeMode.DARK
) -> AppearanceBridge:
    """Default to the purple presentation until the user explicitly chooses light or system
    mode.
    """
    page.theme_mode = mode
    page.fonts = {**(page.fonts or {}), **page_fonts()}
    page.theme = build_theme(False)
    page.dark_theme = build_theme(True)
    page.padding = 0
    page.spacing = 0
    page.bgcolor = ft.Colors.SURFACE_CONTAINER_LOWEST
    bridge = AppearanceBridge(
        mode == ft.ThemeMode.DARK
        or (mode == ft.ThemeMode.SYSTEM and page.platform_brightness == ft.Brightness.DARK)
    )

    def on_brightness(event: ft.Event[ft.Page]) -> None:
        if page.theme_mode != ft.ThemeMode.SYSTEM:
            return
        # Flet1.0.3 declares a typed payload, but its carrier sends a scalar
        # after updating platform_brightness. Scalar dispatch yields Event.
        for value in (getattr(event, "brightness", None), event.data, page.platform_brightness):
            try:
                brightness = ft.Brightness(value)
            except (TypeError, ValueError):
                continue
            bridge.set_dark(brightness == ft.Brightness.DARK)
            return

    page.on_platform_brightness_change = on_brightness
    return bridge
