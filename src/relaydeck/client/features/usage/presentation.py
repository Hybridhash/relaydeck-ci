"""Shared usage states and display formatting."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from relaydeck.client.features.usage import selectors as s
from relaydeck.client.features.usage.loop import (
    PAGE_TOO_LARGE,
    STATUS_DISABLED,
    STATUS_ERROR,
    STATUS_LOADING,
    STATUS_READY,
    STATUS_UNAVAILABLE,
    FeedState,
)
from relaydeck.client.features.usage.model import UsagePage

CAPACITY_CODE = "capacity_exceeded"


def screen_state(state: FeedState) -> str:
    """The screen's state from the feed's state. A refresh with data keeps the data on screen."""
    if state.status == STATUS_LOADING:
        return STATE_READY if state.page is not None else STATE_LOADING
    if state.status == STATUS_UNAVAILABLE:
        if state.error == CAPACITY_CODE:
            return STATE_CAPACITY
        if state.error == PAGE_TOO_LARGE:
            return STATE_PAGE_TOO_LARGE
        return STATE_UNAVAILABLE
    if state.status == STATUS_DISABLED:
        return STATE_DISABLED
    if state.status == STATUS_ERROR:
        return STATE_OFFLINE if state.page is not None else STATE_UNAVAILABLE
    if state.status == STATUS_READY:
        return STATE_READY
    return STATE_LOADING


STATE_LOADING = "loading"
STATE_READY = "ready"
STATE_OFFLINE = "offline"  # the same context's last page, kept, with its age
STATE_CAPACITY = "capacity"  # over the server cap: an explanation, never zeros
STATE_PAGE_TOO_LARGE = "page_too_large"  # a page too big to retain on this device
STATE_UNAVAILABLE = "unavailable"
STATE_DISABLED = "disabled"
STATE_NO_PROVIDERS = "no_providers"


def money(micro: int | None) -> str:
    """`$543.85`, `$1,204.10`. None is an em dash (nothing priced to show)."""
    if micro is None:
        return "—"
    return f"${micro / 1_000_000:,.2f}"


def relative(now_ms: int, then_ms: int, tz: str) -> str:
    return s.relative_time(now_ms, then_ms, tz)


def zone_label(zone_name: str) -> str:
    """The host's zone for people: the IANA name's last segment with underscores as spaces plus
    "time" (`Asia/Riyadh` -> "Riyadh time", `America/New_York` -> "New York time"). A name with no
    city (`UTC`, `Etc/GMT+3`) is said as it is."""
    last = zone_name.rsplit("/", 1)[-1].replace("_", " ")
    if "/" not in zone_name or zone_name.startswith("Etc/"):
        return last
    return f"{last} time"


def as_of(page: UsagePage) -> str | None:
    if page.as_of_ms is None:
        return None
    when = datetime.fromtimestamp(page.as_of_ms / 1000, ZoneInfo(page.timezone))
    return f"as of {when:%d %b %H:%M:%S} {zone_label(page.timezone)}"


def reset(resets_at_ms: int, tz: str) -> str:
    """The reset moment alone, as `Sun 06:58`; callers add the word 'Resets' when needed."""
    when = datetime.fromtimestamp(resets_at_ms / 1000, ZoneInfo(tz))
    return f"{when:%a} {when:%H:%M}"
