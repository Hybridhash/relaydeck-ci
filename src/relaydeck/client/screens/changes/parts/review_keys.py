"""Keyboard review on the wide Changes layout: j next file, k previous file, v toggle Viewed.
The keys use the page's one keyboard handler (shared with the composer's send shortcut) and
are registered only while the wide screen is mounted.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

import flet as ft

from relaydeck.client.features.composer.ui import add_key_listener

KeyAction = Literal["next", "previous", "viewed"]
_KEYS: dict[str, KeyAction] = {"j": "next", "k": "previous", "v": "viewed"}


def key_action(event: Any) -> KeyAction | None:
    """The review action for a key press, or None (any other key, or a modifier held)."""
    if (
        getattr(event, "ctrl", False)
        or getattr(event, "meta", False)
        or getattr(event, "alt", False)
    ):
        return None
    return _KEYS.get(str(getattr(event, "key", "")).lower())


class ReviewKeys:
    def __init__(self) -> None:
        self.typing = False
        self.blocked = False
        self.on_next: Callable[[], None] = lambda: None
        self.on_previous: Callable[[], None] = lambda: None
        self.on_viewed: Callable[[], None] = lambda: None

    def focus(self, focused: bool) -> None:
        """Tell the keys whether the file search has focus."""
        self.typing = focused

    def handle(self, event: Any) -> None:
        action = key_action(event)
        if action is None or self.typing or self.blocked:
            return
        {"next": self.on_next, "previous": self.on_previous, "viewed": self.on_viewed}[action]()


def use_review_keys(
    *,
    wide: bool,
    blocked: bool,
    on_next: Callable[[], None],
    on_previous: Callable[[], None],
    on_viewed: Callable[[], None],
) -> ReviewKeys:
    holder: Any = ft.use_ref(ReviewKeys)
    keys: ReviewKeys = holder.current
    keys.blocked = blocked
    keys.on_next, keys.on_previous, keys.on_viewed = on_next, on_previous, on_viewed

    def register() -> Callable[[], None] | None:
        if not wide:
            return None
        return add_key_listener(keys.handle)

    def release(remove: Callable[[], None] | None) -> None:
        if remove is not None:
            remove()
            keys.typing = False

    effect_ref: Any = ft.use_ref(None)

    def attach() -> None:
        effect_ref.current = register()

    def detach() -> None:
        release(effect_ref.current)
        effect_ref.current = None

    ft.use_effect(attach, dependencies=[wide], cleanup=detach)
    return keys
