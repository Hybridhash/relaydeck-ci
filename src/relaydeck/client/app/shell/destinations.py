"""The console destinations: ids, labels and icons in display order."""

from __future__ import annotations

import flet as ft

DEST_CONSOLE = "console"
DEST_TERMINALS = "terminals"
DEST_USAGE = "usage"
DEST_SETTINGS = "settings"

DESTINATIONS: tuple[tuple[str, str, ft.IconData], ...] = (
    (DEST_CONSOLE, "Console", ft.Icons.VIEW_LIST),
    (DEST_TERMINALS, "Terminals", ft.Icons.TERMINAL),
    (DEST_USAGE, "Usage", ft.Icons.INSIGHTS),
    (DEST_SETTINGS, "Settings", ft.Icons.TUNE),
)
