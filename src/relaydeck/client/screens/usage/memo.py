"""View-owned memoization of immutable usage aggregates, independent of the clock."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from typing import Any, TypeVar, cast

from relaydeck.client.features.usage.api import UsagePage

T = TypeVar("T")
MAX_ENTRIES = 16


class PageMemo:
    def __init__(self) -> None:
        self._page: UsagePage | None = None
        self._values: OrderedDict[tuple[object, ...], object] = OrderedDict()

    def bind(self, page: UsagePage | None) -> None:
        if self._page is not page:
            self._values.clear()
            self._page = page

    def call(self, function: Callable[..., T], page: UsagePage, *args: Any, **kwargs: Any) -> T:
        key = (function, id(page), *args, *sorted(kwargs.items()))
        if key in self._values:
            self._values.move_to_end(key)
            return cast(T, self._values[key])
        result = function(page, *args, **kwargs)
        self._values[key] = result
        while len(self._values) > MAX_ENTRIES:
            self._values.popitem(last=False)
        return result
