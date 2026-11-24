"""Page-lifetime error reporting and a declarative dismissible notice."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import flet as ft

from relaydeck.client.design_system.toast import ActionToast
from relaydeck.client.platform.diagnostics.terminal import diagnostics_logger

_OWNER = "_relaydeck_page_errors"


def _report() -> None:
    try:
        diagnostics_logger().warning("page_error")
    except Exception:  # noqa: BLE001,S110 - a failing sink cannot re-enter the page boundary
        pass


class PageErrors:
    def __init__(self, page: Any) -> None:
        self.page = page
        self.closed = False
        self.open = False
        self.reporting = False
        self.listeners: dict[object, Callable[[bool], None]] = {}
        self.previous_error = getattr(page, "on_error", None)
        self.previous_close = getattr(page, "on_close", None)

        def on_error(_event: Any) -> None:
            if self.closed or page.on_error is not on_error or self.reporting:
                return
            self.reporting = True
            try:
                _report()
                self.open = True
                self._notify()
            finally:
                self.reporting = False

        async def on_close(event: Any) -> None:
            self.close()
            if callable(self.previous_close):
                try:
                    result = (
                        self.previous_close()
                        if len(inspect.signature(self.previous_close).parameters) == 0
                        else self.previous_close(event)
                    )
                    if inspect.isawaitable(result):
                        await result
                except Exception:  # noqa: BLE001 - never return private callback text to the SDK
                    _report()

        self.on_error = on_error
        self.on_close = on_close
        page.on_error = on_error
        page.on_close = on_close

    def _notify(self) -> None:
        selected = next(reversed(self.listeners), None)
        for token, changed in tuple(self.listeners.items()):
            if token in self.listeners and not self.closed:
                try:
                    changed(self.open and token is selected)
                except Exception:  # noqa: BLE001,S110 - failed feedback cannot recurse
                    pass

    def subscribe(self, token: object, changed: Callable[[bool], None]) -> Callable[[], None]:
        if not self.closed:
            self.listeners[token] = changed
            self._notify()

        def unsubscribe() -> None:
            self.listeners.pop(token, None)
            if not self.closed:
                self._notify()

        return unsubscribe

    def dismiss(self, token: object) -> None:
        if not self.closed and next(reversed(self.listeners), None) is token:
            self.open = False
            self._notify()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.open = False
        self.listeners.clear()
        if self.page.on_error is self.on_error:
            self.page.on_error = self.previous_error
        if self.page.on_close is self.on_close:
            self.page.on_close = self.previous_close


def install_page_errors(page: Any) -> PageErrors:
    owner = getattr(page, _OWNER, None)
    if not isinstance(owner, PageErrors) or owner.closed:
        owner = PageErrors(page)
        setattr(page, _OWNER, owner)
    return owner


def render_page(target_page: Any, component: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    """Retire the previous render's notice subscriptions before replacing its tree."""
    owner = install_page_errors(target_page)
    owner.listeners.clear()
    target_page.render(component, *args, **kwargs)


def use_error_notice() -> Any:
    owner = getattr(ft.context.page, _OWNER, None)
    shown, set_shown = ft.use_state(False)
    token = ft.use_ref(object())
    subscription: Any = ft.use_ref(None)

    def start() -> None:
        if isinstance(owner, PageErrors) and not owner.closed:
            subscription.current = (owner, owner.subscribe(token.current, set_shown))

    def stop() -> None:
        installed = subscription.current
        subscription.current = None
        if installed is not None:
            installed[1]()

    ft.use_effect(start, dependencies=[owner], cleanup=stop)

    def dismiss() -> None:
        installed = subscription.current
        if installed is not None:
            installed[0].dismiss(token.current)

    if not shown:
        return None
    try:
        return ActionToast(
            "page-error-notice",
            "Something went wrong.",
            "Dismiss",
            dismiss,
            dismiss,
            error=True,
        )
    except Exception:  # noqa: BLE001 - unavailable feedback must not recurse into the page boundary
        return None
