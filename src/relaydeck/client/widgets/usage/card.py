"""Console usage card backed by its own usage feed."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from typing import Any

import flet as ft

import relaydeck.client.features.usage.api as presentation_usage
import relaydeck.client.features.usage.ui as usage_hooks
import relaydeck.client.widgets.usage.cards as cards_usage
import relaydeck.client.widgets.usage.model as model_usage
from relaydeck.client.design_system.tokens import usage_palette
from relaydeck.client.features.usage.api import UsageFetch, screen_state
from relaydeck.client.features.usage.ui import use_usage_feed


@ft.component
def UsageCard(
    *,
    fetch: UsageFetch | None,
    identity: Hashable,
    host_id: str,
    active: bool,
    dark: bool,
    on_open: Callable[[], Any],
    reduce_motion: bool = False,
    wide: bool = False,
) -> Any:
    """The Console's Today card: the host's today totals, with the unavailable and capacity
    states."""
    tokens = usage_palette(dark)
    feed_state, _request, _busy = use_usage_feed(
        fetch=fetch,
        identity=identity,
        host_id=host_id,
        days=1,
        space_id="",
        active=active,
    )
    state_key = screen_state(feed_state)
    if state_key == presentation_usage.STATE_LOADING:
        return cards_usage.console_loading(tokens=tokens, reduce_motion=reduce_motion)
    page = (
        feed_state.page
        if state_key in (presentation_usage.STATE_READY, presentation_usage.STATE_OFFLINE)
        else None
    )
    card_model = model_usage.console_card(page, state=state_key, now_ms=usage_hooks.now_ms())
    return cards_usage.console_card(card_model, tokens=tokens, on_open=on_open, wide=wide)
