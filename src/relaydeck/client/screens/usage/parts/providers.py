"""The Providers screen. A phone pushes it inside the Usage tab; a wide screen shows it in a
dialog.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as T
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.design_system.dialogs import Dialog
from relaydeck.client.design_system.inputs import SearchField
from relaydeck.client.design_system.theme import UsageColors
from relaydeck.client.design_system.typography import ui
from relaydeck.client.screens.usage import model as sm
from relaydeck.client.screens.usage.parts import primitives as p
from relaydeck.client.screens.usage.parts.primitives import overline

ALL_KINDS = ""  # the page's own id for "all providers"


def _action(on_action: Callable[[str, str], Any], kind: str, action: str) -> Callable[[Any], Any]:
    return lambda _e: on_action(kind, action)


def _select(on_select: Callable[[str], Any], kind: str) -> Callable[[Any], Any]:
    return lambda _e: on_select(kind)


@ft.component
def ProvidersView(
    *,
    lines: Sequence[sm.ProviderLine],
    selected_kind: str,
    tokens: UsageColors,
    on_select: Callable[[str], Any],
    on_action: Callable[[str, str], Any],
) -> Any:
    """The screen's content: meta, search, the connected section and its rows."""
    query, set_query = ft.use_state("")
    needle = query.strip().casefold()
    shown = [line for line in lines if not needle or needle in line.name.casefold()]
    connected = len(lines)
    rows: list[ft.Control] = [
        ft.Container(
            key="usage-providers-all",
            height=56,
            padding=ft.Padding.symmetric(horizontal=12),
            border_radius=14,
            bgcolor=tokens.surface_accent if selected_kind == ALL_KINDS else tokens.surface,
            border=ft.Border.all(1, tokens.accent if selected_kind == ALL_KINDS else tokens.border),
            on_click=lambda _e: on_select(ALL_KINDS),
            content=ft.Row(
                controls=[
                    ui("All CLIs", color=tokens.text, size=15, weight=800, max_lines=1),
                    ft.Container(expand=True),
                    ui(
                        "Filter Usage",
                        color=tokens.text_muted,
                        size=12,
                        weight=600,
                        max_lines=1,
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
    ]
    for line in shown:
        action: ft.Control
        if line.action == "setup":
            action = p.action_button(
                "Set up",
                tokens=tokens,
                key=f"usage-providers-setup-{line.kind}",
                on_click=_action(on_action, line.kind, "setup"),
                primary=True,
            )
        elif line.action == "refresh":
            action = p.action_button(
                "Refresh",
                tokens=tokens,
                key=f"usage-providers-refresh-{line.kind}",
                on_click=_action(on_action, line.kind, "refresh"),
            )
        else:
            action = ft.Icon(ft.Icons.CHEVRON_RIGHT, color=tokens.text_muted, size=20)
        rows.append(
            ft.Container(
                key=f"usage-providers-row-{line.kind}",
                padding=ft.Padding.symmetric(vertical=10, horizontal=2),
                on_click=_select(on_select, line.kind),
                content=ft.Row(
                    controls=[
                        p.monogram(line.monogram, tokens=tokens, size=36),
                        ft.Column(
                            expand=True,
                            spacing=2,
                            tight=True,
                            controls=[
                                ui(
                                    line.name,
                                    color=tokens.text,
                                    size=15,
                                    weight=700,
                                    max_lines=1,
                                ),
                                ui(
                                    line.status,
                                    color=tokens.warn if line.warn else tokens.text_muted,
                                    size=13,
                                    weight=600,
                                    max_lines=None,
                                    key=f"usage-providers-status-{line.kind}",
                                ),
                                *(
                                    [
                                        ui(
                                            line.links,
                                            color=tokens.text_2,
                                            size=13,
                                            weight=600,
                                            max_lines=2,
                                            key=f"usage-providers-links-{line.kind}",
                                        )
                                    ]
                                    if line.links
                                    else []
                                ),
                            ],
                        ),
                        action,
                    ],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )
        )
    if needle and not shown:
        rows.append(
            ui(
                f'No CLIs match "{query.strip()}"',
                color=tokens.text_muted,
                size=14,
                weight=600,
                max_lines=2,
                key="usage-providers-empty",
            )
        )
    return ft.Column(
        key="usage-providers",
        spacing=12,
        tight=True,
        controls=[
            ui(
                f"{connected} connected",
                color=tokens.text_muted,
                size=13,
                weight=600,
                max_lines=2,
                key="usage-providers-meta",
            ),
            SearchField(
                key="usage-providers-search",
                hint_text="Search CLIs",
                value=query,
                on_change=lambda e: set_query(e.control.value or ""),
                prefix_icon=ft.Icons.SEARCH,
                dense=True,
                border_radius=T.RADIUS_KEY,
            ),
            overline(
                f"Connected · {connected}",
                color=tokens.text_muted,
                key="usage-providers-connected",
            ),
            ft.Column(spacing=6, tight=True, controls=rows),
        ],
    )


@ft.component
def ProvidersDialog(
    *,
    open: bool,
    lines: Sequence[sm.ProviderLine],
    selected_kind: str,
    tokens: UsageColors,
    on_select: Callable[[str], Any],
    on_action: Callable[[str, str], Any],
    on_close: Callable[[], Any],
) -> ft.AlertDialog:
    """The wide layout's Providers screen: the same content in a dialog."""
    return Dialog(
        key="usage-providers-dialog",
        open=open,
        bgcolor=tokens.surface_sheet,
        radius=24,
        title=ui("Providers", color=tokens.text, size=20, weight=800, max_lines=1),
        content=ft.Container(
            width=520,
            content=ft.Column(
                scroll=ft.ScrollMode.AUTO,
                tight=True,
                controls=[
                    ProvidersView(
                        lines=lines,
                        selected_kind=selected_kind,
                        tokens=tokens,
                        on_select=on_select,
                        on_action=on_action,
                    )
                ],
            ),
        ),
        actions=[
            Button(
                variant="text",
                key="usage-providers-close",
                content=ui("Close", color=tokens.link, size=14, weight=800, max_lines=1),
                on_click=lambda _e: on_close(),
            )
        ],
        on_dismiss=lambda _e: on_close(),
    )
