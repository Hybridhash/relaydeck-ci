"""The keyboard inset the engine measures from the visual viewport reaches the page's size owner."""

from __future__ import annotations

import gc

from relaydeck_terminal.protocol import PAGE_METRIC_UNAVAILABLE, page_bottom_inset

from relaydeck.client.features.terminal import visual_inset


def test_bottom_inset_is_the_space_below_the_visual_viewport() -> None:
    assert page_bottom_inset(0, 302, 648) == 346  # keyboard and accessory bar
    assert page_bottom_inset(0, 370, 648) == 278  # keyboard only
    assert page_bottom_inset(10, 302, 648) == 336  # scrolled visual viewport
    assert page_bottom_inset(-5, 302, 648) == 346  # a negative offset never adds
    assert page_bottom_inset(0, 648, 648) == 0
    assert page_bottom_inset(0, 700, 648) == 0
    assert page_bottom_inset(0, PAGE_METRIC_UNAVAILABLE, 648) == 0
    assert page_bottom_inset(0, 302, PAGE_METRIC_UNAVAILABLE) == 0


class _Page:
    pass


def test_sink_is_per_page_and_released_with_it() -> None:
    first, second = _Page(), _Page()
    seen: list[tuple[int, float]] = []
    visual_inset.register(first, lambda inset: seen.append((1, inset)))
    visual_inset.register(second, lambda inset: seen.append((2, inset)))
    visual_inset.publish(first, 346.0)
    visual_inset.publish(_Page(), 999.0)  # unknown page: dropped
    assert seen == [(1, 346.0)]
    visual_inset.unregister(first)
    visual_inset.publish(first, 1.0)
    assert seen == [(1, 346.0)]
    key = id(second)
    del second
    gc.collect()
    assert key not in visual_inset._sinks
