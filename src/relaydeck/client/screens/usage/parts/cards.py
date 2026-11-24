"""Summary, KPI row, footnote, state cards, skeleton and the Console card."""

from __future__ import annotations

import flet as ft

from relaydeck.client.design_system.loading import LoadingBars
from relaydeck.client.design_system.theme import UsageColors
from relaydeck.client.design_system.typography import ui
from relaydeck.client.screens.usage import model as sm
from relaydeck.client.screens.usage.parts.primitives import (
    badge,
    card,
    divider,
    num,
    overline,
)


def _credits_controls(summary: sm.SummaryModel, tokens: UsageColors) -> list[ft.Control]:
    """The separate credits line ("+ 162.2 credits (Tool A)"): credits are never in the token total
    or the cost."""
    if not summary.credits_line:
        return []
    return [
        ui(
            summary.credits_line,
            color=tokens.text_muted,
            size=13,
            weight=600,
            max_lines=2,
            key="summary-credits",
        )
    ]


def summary_card(summary: sm.SummaryModel, *, tokens: UsageColors) -> ft.Container:
    """Tokens used, then the estimated priced cost with a Partial badge and its one-line
    explanation."""
    tokens_block = [
        overline("Tokens used", color=tokens.text_muted, key="summary-tokens-label"),
        num(summary.tokens, color=tokens.text, size=44, weight=700, key="summary-tokens"),
        ui(
            summary.context,
            color=tokens.text_muted,
            size=13,
            weight=500,
            max_lines=2,
            key="summary-context",
        ),
        *_credits_controls(summary, tokens),
    ]
    cost_head: list[ft.Control] = [
        overline("Est. cost (priced)", color=tokens.text_muted, key="summary-cost-label"),
        ft.Row(
            controls=[
                num(summary.cost, color=tokens.text, size=28, weight=700, key="summary-cost"),
                badge("Partial", tokens=tokens, tone="warn", key="summary-partial")
                if summary.partial
                else ft.Container(),
            ],
            spacing=10,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
    ]
    if summary.cost_caption:
        cost_head.append(
            ui(summary.cost_caption, color=tokens.text_muted, size=13, weight=500, max_lines=2)
        )
    if summary.partial_note:
        cost_head.append(
            ui(
                summary.partial_note,
                color=tokens.text_muted,
                size=13,
                weight=500,
                max_lines=3,
                key="summary-partial-note",
            )
        )
    return card(
        [*tokens_block, divider(tokens), *cost_head], tokens=tokens, key="usage-summary", padding=20
    )


def kpi_cards(summary: sm.SummaryModel, *, tokens: UsageColors) -> list[ft.Container]:
    """The three summary cards: tokens, priced cost (with the Partial note inside it), busiest
    day."""
    cost_controls: list[ft.Control] = [
        overline("Est. cost (priced)", color=tokens.text_muted),
        ft.Row(
            controls=[
                num(
                    summary.cost,
                    color=tokens.text,
                    size=26,
                    weight=700,
                    key="kpi-cost",
                    max_lines=None,
                ),
                badge("Partial", tokens=tokens, tone="warn") if summary.partial else ft.Container(),
            ],
            spacing=8,
        ),
    ]
    if summary.partial and summary.partial_note:
        # The Partial explanation sits inside the cost card, under the amount.
        cost_controls.append(
            ui(
                summary.partial_note,
                color=tokens.text_muted,
                size=13,
                weight=500,
                max_lines=None,
                key="kpi-partial-note",
            )
        )
    if summary.cost_caption:
        cost_controls.append(
            ui(summary.cost_caption, color=tokens.text_muted, size=12, weight=500, max_lines=2)
        )
    tokens_card = card(
        [
            overline("Tokens used", color=tokens.text_muted),
            num(
                summary.tokens,
                color=tokens.text,
                size=40,
                weight=700,
                key="kpi-tokens",
                max_lines=None,
            ),
            ui(summary.context, color=tokens.text_muted, size=12, weight=500, max_lines=2),
            *_credits_controls(summary, tokens),
        ],
        tokens=tokens,
        key="usage-kpi-tokens",
        padding=22,
        expand=True,
    )
    cost = card(cost_controls, tokens=tokens, key="usage-kpi-cost", padding=22, expand=True)
    busiest = card(
        [
            overline("Busiest day", color=tokens.text_muted),
            num(
                summary.busiest_value,
                color=tokens.text,
                size=26,
                weight=700,
                key="kpi-busiest",
                max_lines=None,
            ),
            ui(
                summary.busiest_caption,
                color=tokens.text_muted,
                size=12,
                weight=500,
                max_lines=2,
            ),
        ],
        tokens=tokens,
        key="usage-kpi-busiest",
        padding=22,
        expand=True,
    )
    return [tokens_card, cost, busiest]


def kpi_row(summary: sm.SummaryModel, *, tokens: UsageColors, wide: bool = True) -> ft.Row:
    """The summary cards on a tablet or a phone: wrapped fixed-width cards (medium), never
    clipped."""
    cards = kpi_cards(summary, tokens=tokens)
    for item in cards:
        item.expand = None
        item.width = 230
    return ft.Row(
        controls=list(cards),
        spacing=16,
        run_spacing=12,
        wrap=True,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )


def caveat_line(text: str, *, tokens: UsageColors) -> ft.Control:
    """The token-totals caveat: one line that wraps and is never truncated."""
    return ft.Container(
        key="usage-caveat",
        padding=ft.Padding.symmetric(horizontal=4),
        content=ft.Text(
            text,
            key="usage-caveat-text",
            size=12,
            color=tokens.text_muted,
            max_lines=None,
            overflow=ft.TextOverflow.VISIBLE,
        ),
        tooltip=text,
    )


def footnote(text: str, *, tokens: UsageColors) -> ft.Container:
    return ft.Container(
        key="usage-footnote",
        padding=ft.Padding.symmetric(horizontal=4, vertical=4),
        content=ui(text, color=tokens.text_muted, size=12, weight=500, max_lines=3),
    )


def message_card(
    message: str, *, tokens: UsageColors, key: str, tone: str = "normal"
) -> ft.Container:
    """A state that replaces the data: over capacity, unavailable, turned off, no providers."""
    color = tokens.warn if tone == "warn" else tokens.text
    return card(
        [ui(message, color=color, size=15, weight=700, max_lines=3, key=f"{key}-text")],
        tokens=tokens,
        key=key,
        padding=20,
    )


def empty_caption(text: str, *, tokens: UsageColors, key: str) -> ft.Container:
    return card(
        [ui(text, color=tokens.text_muted, size=14, weight=600, max_lines=2)],
        tokens=tokens,
        key=key,
        padding=16,
    )


def skeleton(*, tokens: UsageColors, reduce_motion: bool = False) -> ft.Column:
    """The loading state: the animated bar-chart icon with "Loading usage…", then placeholder
    blocks in the layout's shape. The blocks are static (no pulse); the icon is the one animation
    and is static under Reduce Motion."""

    def block(height: float) -> ft.Container:
        return ft.Container(height=height, bgcolor=tokens.surface_raised, border_radius=14)

    indicator = ft.Row(
        key="usage-loading",
        spacing=10,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            LoadingBars(color=tokens.accent, size=28, reduce_motion=reduce_motion),
            ui(
                "Loading usage…",
                color=tokens.text_muted,
                size=15,
                weight=700,
                key="usage-loading-text",
            ),
        ],
    )
    return ft.Column(
        key="usage-skeleton",
        spacing=16,
        controls=[indicator, block(120), block(180), block(140), block(160)],
    )
