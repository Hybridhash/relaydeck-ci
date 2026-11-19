"""Pure Console Usage view model: no Flet or I/O. Numbers, rankings, rollups, states and labels
are computed from Usage selectors; controls render these values without arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass

import relaydeck.client.features.usage.api as s
from relaydeck.client.features.usage.api import (
    STATE_CAPACITY,
    STATE_DISABLED,
    STATE_UNAVAILABLE,
    UsagePage,
    as_of,
    money,
    relative,
    reset,
)
from relaydeck.client.features.usage.api import console_card as usage_console_card


@dataclass(frozen=True)
class ConsoleCardModel:
    """The Console's Today card. `available` False means unavailable, never zero or no usage."""

    available: bool
    capacity: bool
    headline: str
    pick: str
    age: str
    stale_day: bool
    # "Partial" is its own badge, not part of the headline text.
    partial: bool = False
    # Line 2 inputs (see `console_line2`): each tool's top fresh window as "Agent A 51% weekly", in
    # rank order; the first one's reset as "resets Sun 06:58"; and the fallback line shown when no
    # tool has a limit reading ("Most used: Agent A 1.2B", or "" for none).
    picks: tuple[str, ...] = ()
    first_reset: str = ""
    fallback: str = ""


CONSOLE_TWO_FIT = 38  # characters: two tools fit one phone line when the text is this short


def console_line2(card: ConsoleCardModel, *, wide: bool = False) -> str:
    """The strip's second line. Generic: tools come from the page, none is named here."""
    if not card.available:
        return ""
    if not card.picks:
        return card.pick or card.fallback
    if len(card.picks) == 1:
        return card.pick
    two = f"{card.picks[0]} · {card.picks[1]}"
    if wide or len(two) <= CONSOLE_TWO_FIT:
        text, left = two, len(card.picks) - 2
    else:
        text, left = card.picks[0], len(card.picks) - 1
        if card.first_reset:
            text = f"{text} · {card.first_reset}"
    return f"{text} · +{left}" if left > 0 else text


def console_card(page: UsagePage | None, *, state: str, now_ms: int) -> ConsoleCardModel:
    """The Console's Today card. Unavailable and capacity are never shown as 'No usage today'."""
    if page is None or state in (STATE_CAPACITY, STATE_UNAVAILABLE, STATE_DISABLED):
        return ConsoleCardModel(
            available=False,
            capacity=state == STATE_CAPACITY,
            headline=(
                "Too much data today · open Usage"
                if state == STATE_CAPACITY
                else "Usage is unavailable right now."
            ),
            pick="",
            age="",
            stale_day=False,
        )
    card = usage_console_card(page, now_ms)
    if not card.available:
        return ConsoleCardModel(False, False, "Usage is unavailable right now.", "", "", False)
    partial = False
    if card.stale_day:
        headline = "Last reading is from an earlier day; open Usage to refresh"
    elif card.has_today:
        if card.priced_micro_usd is None:
            cost = "Unpriced"
        else:
            cost = money(card.priced_micro_usd)
        partial = card.priced_micro_usd is not None and bool(card.unpriced_tokens)
        headline = f"Today {cost} · {s.tokens_text(card.tokens)} tokens"
    else:
        headline = "No usage today"
    short: list[str] = []
    for item in card.picks:
        window = s.window_label(item.window_minutes).lower()
        short.append(f"{item.display_name} {round(item.used_percent)}% {window}")
    first_reset = ""
    if card.pick is not None and card.pick.resets_at_ms is not None:
        first_reset = f"resets {reset(card.pick.resets_at_ms, page.timezone)}"
    fallback = ""
    if card.top_tool is not None and not short:
        fallback = f"Most used: {card.top_tool[0]} {s.tokens_chart_text(card.top_tool[1])}"
    pick = (short[0] + (f" · {first_reset}" if first_reset else "")) if short else fallback
    return ConsoleCardModel(
        available=True,
        capacity=False,
        headline=headline,
        pick=pick,
        age=as_of(page) or f"snapshot {relative(now_ms, page.generated_ms, page.timezone)}",
        stale_day=card.stale_day,
        partial=partial,
        picks=tuple(short),
        first_reset=first_reset,
        fallback=fallback,
    )
