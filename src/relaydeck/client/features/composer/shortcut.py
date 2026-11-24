"""Ctrl/Cmd+Enter sends the agent draft through the same entry point as the Send button. The
page has one keyboard handler.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Callable
from typing import Any

import flet as ft
from flet.controls.context import context

from relaydeck.client.platform.flet.events import spawn


async def _await(awaitable: Any) -> None:
    await awaitable


LOG = logging.getLogger("relaydeck.client.composer")
_REGISTRY_ATTR = "_relaydeck_send_shortcuts"
_LISTENERS_ATTR = "_relaydeck_key_listeners"
_ENTER_KEYS = ("Enter", "Numpad Enter")
_SWALLOW_WINDOW_S = 1.0


def platform_label() -> str:
    """Hint for the Send tooltip: the Command key on Apple platforms, otherwise Ctrl."""
    try:
        platform = str(getattr(context.page, "platform", "") or "").lower()
    except RuntimeError:
        platform = ""
    return "⌘+Enter" if "macos" in platform or "ios" in platform else "Ctrl+Enter"


def inserted_one_newline(before: str, after: str) -> bool:
    """True when `after` is `before` with exactly one newline added somewhere."""
    if len(after) != len(before) + 1:
        return False
    cut = 0
    while cut < len(before) and before[cut] == after[cut]:
        cut += 1
    return after[cut] == "\n" and after[cut + 1 :] == before[cut:]


def _dispatch(event: Any) -> None:
    page = context.page
    for listener in tuple(getattr(page, _LISTENERS_ATTR, ())):
        try:
            listener(event)
        except Exception:  # noqa: BLE001 - a listener never stops the composer's own shortcut
            LOG.warning("key_listener_failed")
    if getattr(event, "key", "") not in _ENTER_KEYS:
        return
    if not (getattr(event, "ctrl", False) or getattr(event, "meta", False)):
        return
    if getattr(event, "shift", False) or getattr(event, "alt", False):
        return
    for entry in tuple(getattr(page, _REGISTRY_ATTR, ())):
        entry()


def _release(page: Any) -> None:
    """Remove the page handler once nothing is registered with it."""
    idle = not getattr(page, _REGISTRY_ATTR, None) and not getattr(page, _LISTENERS_ATTR, None)
    if idle and page.on_keyboard_event is _dispatch:
        page.on_keyboard_event = None


def add_key_listener(listener: Callable[[Any], None]) -> Callable[[], None]:
    """Receive every page key press until the returned function is called.

    Returns a no-op when there is no page (nothing is registered then).
    """
    try:
        page = context.page
    except RuntimeError:
        return lambda: None
    listeners: list[Callable[[Any], None]] | None = getattr(page, _LISTENERS_ATTR, None)
    if listeners is None:
        listeners = []
        setattr(page, _LISTENERS_ATTR, listeners)
    listeners.append(listener)
    if page.on_keyboard_event is not _dispatch:
        page.on_keyboard_event = _dispatch

    def remove() -> None:
        if listener in listeners:
            listeners.remove(listener)
        _release(page)

    return remove


class SendShortcut:
    def __init__(self) -> None:
        self.focused = False
        self.draft = ""
        self.can_send = False
        self.send: Callable[[], Any] = lambda: None
        self._pending: tuple[str, float] | None = None

    def focus(self, focused: bool) -> None:
        self.focused = focused

    def fire(self) -> None:
        if not self.focused or not self.can_send:
            return
        self._pending = (self.draft, time.monotonic())
        result = self.send()
        if inspect.isawaitable(result):
            spawn(_await(result), label="Send with shortcut")

    def swallows(self, text: str) -> bool:
        """True for the change that is only the newline this shortcut's Enter inserted."""
        pending, self._pending = self._pending, None
        if pending is None or time.monotonic() - pending[1] > _SWALLOW_WINDOW_S:
            return False
        # A send that already cleared the field leaves the newline on its own.
        return text == "\n" or inserted_one_newline(pending[0], text)


def use_send_shortcut(
    *, draft: str, can_send: bool, send: Callable[[], Any]
) -> tuple[SendShortcut, str]:
    """Register this composer for the shortcut; returns its controller and the tooltip hint.

    Only the composer whose draft field has focus acts, so parked composers stay silent.
    """
    holder: Any = ft.use_ref(SendShortcut)
    shortcut: SendShortcut = holder.current
    shortcut.draft, shortcut.can_send, shortcut.send = draft, can_send, send

    detach_ref: Any = ft.use_ref(None)

    def attach() -> None:
        try:
            page = context.page
        except RuntimeError:
            return
        registry: list[Callable[[], None]] | None = getattr(page, _REGISTRY_ATTR, None)
        if registry is None:
            registry = []
            setattr(page, _REGISTRY_ATTR, registry)
        registry.append(shortcut.fire)
        if page.on_keyboard_event is not _dispatch:
            page.on_keyboard_event = _dispatch

        def detach() -> None:
            if shortcut.fire in registry:
                registry.remove(shortcut.fire)
            _release(page)

        detach_ref.current = detach

    def detach_all() -> None:
        if detach_ref.current is not None:
            detach_ref.current()
            detach_ref.current = None

    ft.use_effect(attach, dependencies=[], cleanup=detach_all)
    return shortcut, platform_label()
