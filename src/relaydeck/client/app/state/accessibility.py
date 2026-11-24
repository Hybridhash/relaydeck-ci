"""One accessibility snapshot and read owner for each page."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, fields
from typing import Any

import flet as ft

from relaydeck.client.app.workflows.page_tasks import connection_for
from relaydeck.client.platform.flet.accessibility import page_accessibility_reader

READ_TIMEOUT_S = 2.0
_OWNER = "_relaydeck_accessibility_owner"


@dataclass(frozen=True)
class AccessibilitySnapshot:
    accessible_navigation: bool = False
    bold_text: bool = False
    disable_animations: bool = False
    high_contrast: bool = False
    invert_colors: bool = False
    reduce_motion: bool = False
    on_off_switch_labels: bool = False

    @property
    def motion_reduced(self) -> bool:
        return self.disable_animations or self.reduce_motion


class PageAccessibility:
    def __init__(
        self,
        read: Callable[[], Awaitable[Mapping[str, bool]]] | None,
        *,
        current: Callable[[], bool] = lambda: True,
        timeout: float = READ_TIMEOUT_S,
        snapshot: AccessibilitySnapshot | None = None,
    ) -> None:
        self.snapshot = snapshot if snapshot is not None else AccessibilitySnapshot()
        self._read = read
        self._current = current
        self._timeout = timeout
        self._started = snapshot is not None
        self._finished = snapshot is not None
        self._closed = False
        self._task: asyncio.Task[None] | None = None
        self._listeners: dict[object, Callable[[AccessibilitySnapshot], None]] = {}

    def subscribe(self, changed: Callable[[AccessibilitySnapshot], None]) -> Callable[[], None]:
        token = object()
        if not self._closed:
            self._listeners[token] = changed
            if self._finished and self._current():
                changed(self.snapshot)

        def unsubscribe() -> None:
            self._listeners.pop(token, None)

        return unsubscribe

    def start(self) -> None:
        if self._started or self._closed:
            return
        self._started = True
        if self._read is None:
            if self._current():
                self._finished = True
            else:
                self.close()
            return
        self._task = asyncio.get_running_loop().create_task(self._load())

    async def _load(self) -> None:
        try:
            assert self._read is not None  # noqa: S101
            values = await asyncio.wait_for(self._read(), self._timeout)
            snapshot = AccessibilitySnapshot(
                **{
                    field.name: values.get(field.name) is True
                    for field in fields(AccessibilitySnapshot)
                }
            )
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001 - unsupported service preserves the existing defaults
            snapshot = AccessibilitySnapshot()
        finally:
            self._task = None
        if self._closed or not self._current():
            self.close()
            return
        self._finished = True
        self.snapshot = snapshot
        for token, changed in tuple(self._listeners.items()):
            if token in self._listeners:
                try:
                    changed(snapshot)
                except RuntimeError:
                    self._listeners.pop(token, None)

    def close(self) -> None:
        self._closed = True
        self._listeners.clear()
        if self._task is not None:
            self._task.cancel()
            self._task = None


def use_page_accessibility() -> AccessibilitySnapshot:
    page = ft.context.page
    owner = getattr(page, _OWNER, None)
    if owner is None or owner._closed or not owner._current():
        completed = owner.snapshot if owner is not None and owner._finished else None
        if owner is not None:
            owner.close()
        connection = connection_for(page)
        owner = PageAccessibility(
            page_accessibility_reader(page),
            current=lambda: connection is not None and connection_for(page) is connection,
            snapshot=completed,
        )
        setattr(page, _OWNER, owner)
    initial: AccessibilitySnapshot = owner.snapshot
    snapshot, set_snapshot = ft.use_state(initial)
    subscription: Any = ft.use_ref(None)

    def start() -> None:
        subscription.current = (owner, page, owner.subscribe(set_snapshot))
        owner.start()

    def stop() -> None:
        installed = subscription.current
        subscription.current = None
        if installed is not None:
            installed[2]()
            if connection_for(installed[1]) is None:
                installed[0].close()

    ft.use_effect(start, dependencies=[owner], cleanup=stop)
    return snapshot
