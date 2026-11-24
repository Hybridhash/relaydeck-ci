"""Settings › update notice: "RelayDeck <version> is available on your Mac". Renders the same
app-owned notice as the Settings rail dot.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as T
from relaydeck.client.design_system.controls import PillButton, SettingsPanel
from relaydeck.client.design_system.fonts import FAMILY_INTERFACE, FAMILY_MONO
from relaydeck.client.design_system.primitives import text_style
from relaydeck.client.design_system.typography import TYPE_ROW
from relaydeck.client.features.update_notice.api import UpdateNotice
from relaydeck.client.platform.flet.events import spawn

PANEL_KEY = "settings-update-notice"


@ft.component
def UpdateNoticePanel(
    notice: UpdateNotice | None,
    *,
    phone: bool,
    on_presented: Callable[[str], Any] | None = None,
) -> Any:
    clipboard: Any = ft.use_ref(None)
    copied, set_copied = ft.use_state(False)
    available = notice.available_version if notice is not None else None

    def presented() -> None:
        # Acknowledgement follows presentation: only a version this panel
        # actually rendered is reported as seen.
        if available is not None and on_presented is not None:
            on_presented(available)

    ft.use_effect(presented, dependencies=[available])
    if notice is None or notice.available_version is None:
        return ft.Container(key="settings-update-notice-hidden", visible=False)

    def copy_instructions() -> None:
        if clipboard.current is None:
            clipboard.current = ft.Clipboard()
        set_copied(True)
        spawn(
            clipboard.current.set("\n".join(notice.instructions)),
            label="update-notice-copy",
        )

    rows: list[Any] = [
        ft.Text(
            f"Released {notice.issued}",
            key="update-notice-issued",
            style=text_style(FAMILY_MONO, TYPE_ROW, T.TEXT_MUTED),
        )
    ]
    rows.extend(
        ft.Container(
            padding=ft.Padding.only(top=6),
            content=ft.Text(
                note,
                key=f"update-notice-note-{index}",
                style=text_style(FAMILY_INTERFACE, T.TYPE_CARD_LINE, T.TEXT),
            ),
        )
        for index, note in enumerate(notice.notes)
    )
    if notice.instructions:
        rows.append(
            ft.Container(
                padding=ft.Padding.only(top=10),
                content=ft.Container(
                    key="update-notice-instructions",
                    padding=ft.Padding.all(12),
                    border_radius=ft.BorderRadius.all(T.RADIUS_TILE),
                    bgcolor=T.TERMINAL_BG,
                    content=ft.Text(
                        "\n".join(notice.instructions),
                        selectable=True,
                        style=text_style(FAMILY_MONO, TYPE_ROW, T.TEXT),
                    ),
                ),
            )
        )
        rows.append(
            ft.Container(
                padding=ft.Padding.only(top=8),
                content=PillButton(
                    "Copied" if copied else "Copy commands",
                    copy_instructions,
                    key="settings-update-copy",
                    tone="outline",
                ),
            )
        )
    if notice.last_checked:
        rows.append(
            ft.Container(
                padding=ft.Padding.only(top=8, bottom=4),
                content=ft.Text(
                    f"Last checked {notice.last_checked}",
                    key="update-notice-last-checked",
                    style=text_style(FAMILY_MONO, TYPE_ROW, T.TEXT_MUTED),
                ),
            )
        )
    return SettingsPanel(
        f"RelayDeck {notice.available_version} is available on your Mac",
        rows,
        key=PANEL_KEY,
        phone=phone,
    )
