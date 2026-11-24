"""Actual metric tile callers, including equal-height Usage cards."""

from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as T
from relaydeck.client.design_system.primitives import CountTile
from relaydeck.client.screens.settings.parts.server_health_model import Tile
from relaydeck.client.screens.settings.parts.server_health_parts import HealthTile
from relaydeck.client.screens.usage.model import SummaryModel
from relaydeck.client.screens.usage.parts.cards import kpi_cards
from relaydeck.client.screens.usage.parts.primitives import MatchedRow
from tests.support.host_harness import render_component

CASES = (
    "counts",
    "health-empty",
    "health-series",
    "usage-light",
    "usage-dark",
    "matched-light",
    "matched-dark",
)


def stat_view(case: str) -> ft.Column:
    if case == "counts":
        children = [
            CountTile(status, count, key=f"tile-{status}-{count}")
            for status in (*T.STATUS_LABELS, "invalid")
            for count in (0, 123456789)
        ]
    elif case.startswith("health-"):
        series = () if case == "health-empty" else (1.0, 3.0, 2.0)
        children = [
            HealthTile(
                Tile(
                    "example",
                    "Example metric",
                    "123",
                    "ms",
                    "Example note",
                    series,
                    "Example trend",
                )
            )
        ]
    else:
        summary = SummaryModel(
            tokens="12.3K",
            context="Example context",
            cost="$1.00",
            cost_caption="Estimate",
            partial=True,
            partial_note="Example pricing note",
            busiest_value="5.0K",
            busiest_caption="Example day",
            credits_line="Example separate credits",
        )
        cards = kpi_cards(summary, tokens=T.usage_palette(case.endswith("dark")))
        children = (
            [MatchedRow(cards=[cards[0], None, cards[1]], expands=[2, 1, 2])]
            if case.startswith("matched-")
            else cards
        )
    return ft.Column(controls=children)


async def render_stats(case: str) -> Any:
    return await render_component(stat_view, case)
