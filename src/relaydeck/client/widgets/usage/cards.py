"""Summary, KPI row, footnote, state cards, skeleton and the Console card.

Render-only: values and states come from the Usage presentation models, with no arithmetic here."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

import relaydeck.client.widgets.usage.model as model_usage
from relaydeck.client.design_system import tokens as T
from relaydeck.client.design_system.badges import Badge
from relaydeck.client.design_system.loading import LoadingBars
from relaydeck.client.design_system.theme import UsageColors
from relaydeck.client.design_system.typography import ui


def console_loading(*, tokens: UsageColors, reduce_motion: bool = False) -> ft.Control:
    """The Console strip during its first fetch: "Loading…", never "unavailable"."""
    strip = ft.Container(
        key="console-usage-card",
        expand=True,
        padding=ft.Padding.symmetric(horizontal=12, vertical=7),
        border_radius=T.RADIUS_KEY,
        bgcolor=tokens.surface,
        border=ft.Border.all(1, tokens.border),
        content=ft.Row(
            spacing=8,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                LoadingBars(
                    color=tokens.accent,
                    size=16,
                    reduce_motion=reduce_motion,
                    key="console-loading-icon",
                ),
                ui(
                    "Loading…",
                    color=tokens.text_muted,
                    size=13,
                    weight=700,
                    key="console-headline",
                ),
            ],
        ),
    )
    return ft.Row(spacing=0, controls=[strip])


def console_card(
    card_model: model_usage.ConsoleCardModel,
    *,
    tokens: UsageColors,
    on_open: Callable[[], Any],
    wide: bool = False,
) -> ft.Control:
    """The Console's compact Today strip, plus an explicit as-of time when supplied. Line 1 is
    the headline ("Today $149.02 · 1.07B tokens") with a Partial badge when some tokens are
    unpriced.
    """
    first: list[ft.Control] = [
        ui(
            card_model.headline,
            color=tokens.warn if not card_model.available else tokens.text,
            size=13,
            weight=700,
            max_lines=1,
            key="console-headline",
        )
    ]
    if card_model.available and card_model.partial:
        first.append(
            Badge(
                key="console-partial",
                padding=ft.Padding.symmetric(horizontal=6, vertical=1),
                border_radius=6,
                border=ft.Border.all(1, tokens.border),
                content=ui("Partial", color=tokens.text_muted, size=10, weight=600, max_lines=1),
            )
        )
    lines: list[ft.Control] = [ft.Row(spacing=6, tight=True, controls=first)]
    second = model_usage.console_line2(card_model, wide=wide)
    if second:
        lines.append(
            ui(
                second,
                color=tokens.text_muted,
                size=12,
                weight=600,
                max_lines=1,
                key="console-line2",
            )
        )
    label = "Open Usage. " + (card_model.headline + ". " if card_model.headline else "")
    if card_model.age.startswith("as of "):
        lines.append(
            ui(
                card_model.age,
                color=tokens.text_muted,
                size=12,
                max_lines=None,
                key="console-as-of",
            )
        )
    if second:
        label += second + ". "
    if card_model.age:
        label += card_model.age.capitalize() + "."
    detail = " · ".join(card_model.picks) if len(card_model.picks) > 1 else ""
    tip = "Open Usage"
    if detail:
        tip += " · " + detail
    if card_model.age:
        tip += " · " + card_model.age
    strip = ft.Container(
        key="console-usage-card",
        expand=True,  # the strip fills the content column at every size
        padding=ft.Padding.symmetric(horizontal=12, vertical=7),
        border_radius=T.RADIUS_KEY,
        bgcolor=tokens.surface,
        border=ft.Border.all(1, tokens.border),
        on_click=lambda _e: on_open(),
        tooltip=tip,
        content=ft.Column(
            spacing=1,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.START,
            controls=lines,
        ),
    )
    # A Row gives the expanded strip the column's whole width; a bare Container would wrap its text.
    return ft.Semantics(label=label, content=ft.Row(spacing=0, controls=[strip]))
