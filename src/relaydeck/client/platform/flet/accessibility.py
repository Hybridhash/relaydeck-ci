"""The page's native accessibility feature reader."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import flet as ft

_FEATURES = (
    "accessible_navigation",
    "bold_text",
    "disable_animations",
    "high_contrast",
    "invert_colors",
    "reduce_motion",
    "on_off_switch_labels",
)
_SERVICE = "_relaydeck_accessibility_service"


def page_accessibility_reader(page: Any) -> Callable[[], Awaitable[Mapping[str, bool]]] | None:
    try:
        if ft.context.page is not page:
            return None
        service = getattr(page, _SERVICE, None)
        if service is None:
            service = ft.SemanticsService()
            setattr(page, _SERVICE, service)
    except (AttributeError, RuntimeError):
        return None

    async def read() -> Mapping[str, bool]:
        features = await service.get_accessibility_features()
        return {name: getattr(features, name, False) is True for name in _FEATURES}

    return read
