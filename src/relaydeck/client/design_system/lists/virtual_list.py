"""Build and cache keyed controls only near the visible row window. Explicit extents preserve
row positions while mounted work follows the viewport rather than total data size.
"""

from __future__ import annotations

import asyncio
import bisect
import math
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as t

DEFAULT_VIEWPORT = 800.0  # dp, assumed until the list reports its own
FOOTER_EXTENT = float(t.MIN_TOUCH)  # a footer that does not say its height is one touch target
MIN_SCREEN_ROWS = 8
SETTLE_DELAY_S = (
    0.12  # quiet time after the last scroll or size report before the window is checked
)
SIZE_TOLERANCE = 2.0  # dp a reported height must differ by to count as a change
SIZE_INTERVAL_MS = 100


@dataclass(frozen=True, slots=True)
class Window:
    """Rows `first` up to (not including) `last` are built."""

    first: int
    last: int

    def __len__(self) -> int:
        return self.last - self.first


@dataclass(frozen=True, slots=True)
class Geometry:
    """Where each row sits. Rows of one `height` need nothing more; rows of different heights give
    `offsets`: the top of every row, then the end of the last."""

    count: int
    height: float = 0.0
    offsets: tuple[float, ...] | None = None

    @property
    def total(self) -> float:
        return self.offsets[self.count] if self.offsets is not None else self.count * self.height

    @property
    def average(self) -> float:
        return self.total / self.count if self.count else (self.height or 1.0)

    def top(self, index: int) -> float:
        return self.offsets[index] if self.offsets is not None else index * self.height

    def index_at(self, pixels: float) -> int:
        """The row holding `pixels` (clamped to the first and last row)."""
        if self.count == 0:
            return 0
        if self.offsets is None:
            found = int(pixels // self.height)
        else:
            found = bisect.bisect_right(self.offsets, pixels, 0, self.count) - 1
        return min(max(0, found), self.count - 1)


def screen_rows(viewport: float, geometry: Geometry) -> int:
    return max(MIN_SCREEN_ROWS, math.ceil(viewport / geometry.average))


def visible(offset: float, viewport: float, geometry: Geometry) -> tuple[int, int]:
    """The rows the viewport shows, as [first, last)."""
    if geometry.count == 0:
        return 0, 0
    first = geometry.index_at(offset)
    last = geometry.index_at(offset + viewport - 1e-6) + 1
    return first, max(first, last)


def window_for(
    offset: float, viewport: float, geometry: Geometry, margin: int | None = None
) -> Window:
    """The visible rows with a screenful (or `margin` rows) built above and below."""
    first, last = visible(offset, viewport, geometry)
    around = screen_rows(viewport, geometry) if margin is None else margin
    return Window(max(0, first - around), min(geometry.count, last + around))


def needs_shift(window: Window, offset: float, viewport: float, geometry: Geometry) -> bool:
    """True when the viewport has come within half a screen of an unbuilt row."""
    first, last = visible(offset, viewport, geometry)
    guard = screen_rows(viewport, geometry) // 2
    return (first < window.first + guard and window.first > 0) or (
        last > window.last - guard and window.last < geometry.count
    )


JUMP_MARGIN = 2  # rows kept around the view right after a jump


def shift(
    window: Window, offset: float, viewport: float, geometry: Geometry
) -> tuple[Window, bool] | None:
    """The window to build when the reader has come near an unbuilt row, or None to keep it.

    A jump, which lands where nothing was built, gets only the rows in view and a few around them
    (`True`): the first paint is quick, and the margins follow on the next tick."""
    if not needs_shift(window, offset, viewport, geometry):
        return None
    first, last = visible(offset, viewport, geometry)
    jumped = last <= window.first or first >= window.last
    margin = JUMP_MARGIN if jumped else None
    return window_for(offset, viewport, geometry, margin), jumped


@dataclass(frozen=True, slots=True)
class Row:
    """One logical row: who it is, what makes it look different, and how to build it."""

    key: str
    signature: Hashable
    build: Callable[[], ft.Control]


class RowCache:
    """Built rows by key, each valid for the signature it was built with."""

    def __init__(self) -> None:
        self._rows: dict[str, tuple[Hashable, ft.Control]] = {}

    def get(self, row: Row) -> ft.Control:
        hit = self._rows.get(row.key)
        if hit is not None and hit[0] == row.signature:
            return hit[1]
        control = row.build()
        if control.key is None:
            # Flet matches a list's children by key; without one it matches them by position,
            # so a step that drops rows at the front would re-send every row that remains.
            control.key = row.key
        self._rows[row.key] = (row.signature, control)
        return control

    def keep(self, keys: set[str]) -> None:
        """Forget rows that left the window, so memory follows the screen."""
        for key in [key for key in self._rows if key not in keys]:
            del self._rows[key]

    def __len__(self) -> int:
        return len(self._rows)


# Flet's decorator leaves its type variable unsolved for mypy; the class is a ListView to Flet.
@ft.control("ListView")  # type: ignore[arg-type]
class PlacedListView(ft.ListView):
    """Place children at cumulative explicit extents while allowing their natural height to be
    measured. This keeps a sliding window from inheriting the prior child positions.
    """

    item_extents: list[float] | None = None


def extent_of(control: ft.Control) -> float:
    """The height a control outside the rows takes: its own, or one touch target."""
    height = getattr(control, "height", None)
    return float(height) if height else FOOTER_EXTENT


async def scroll_list(listing: Any, offset: float) -> bool:
    """Move a list to `offset` pixels (negative: the end); a missed request keeps the position.

    True when the client answered the request."""
    try:
        # Pinned scroll_to has no timeout argument; bound the native request here.
        await listing._invoke_method("scroll_to", {"offset": offset, "duration": 0}, timeout=1)
    except (TimeoutError, RuntimeError):
        return False
    return True


@dataclass(frozen=True, slots=True)
class _Built:
    """The window as last set, with what it was set for. `lean` marks a window holding only the
    rows in view, set after a jump; the margins follow on the next tick."""

    context: Hashable
    window: Window
    total: int
    lean: bool = False


@dataclass(frozen=True, slots=True)
class Place:
    """Where the reader was: a row, how far into it, and the scroll offset that is."""

    index: int
    offset: float = 0.0
    pixels: float = 0.0


FILL_DELAY_S = 0.03  # after a jump, the margins are built this long after the rows in view
RESTORE_DELAY_S = 0.05
MEASURE_DEBOUNCE_S = 0.05  # row heights reported close together are applied in one render


class _Measures:
    """Heights the client reported for rows whose height the caller could not know.

    Rows not yet measured count as the average of those that were, until the reader leaves the
    top: from then on the average is frozen, because rows above the reader that move with it would
    move the content under the reader's eyes (the client keeps its pixel offset)."""

    def __init__(self) -> None:
        self.heights: dict[str, float] = {}
        self.pending = False
        self.alive = True
        self.frozen: float | None = None
        self.away = False  # the reader is past the top of the list
        self.context: Hashable = None
        self.heights_revision = 0
        self._cached: Geometry | None = None
        self._cache_key: tuple[Any, ...] | None = None
        self._cache_revision: Any = None
        self._pruned_for: tuple[int, Any] | None = None

    def enter(self, context: Hashable, away: bool) -> None:
        """Another context starts over: its own average, away from the top only if it opens so."""
        if context != self.context:
            self.context, self.frozen, self.away = context, None, away

    def record(self, key: str, size: float) -> bool:
        """Learn one row's height. True when it changed enough to matter."""
        if size <= 0 or abs(self.heights.get(key, -1.0) - size) < 1.0:
            return False
        self.heights[key] = size
        self.heights_revision += 1
        return True

    def keep_only(self, live: set[str]) -> None:
        """Forget rows that are gone: their heights would otherwise stay for as long as the list
        lives, and count in the average."""
        if len(self.heights) > 0 and not self.heights.keys() <= live:
            self.heights = {key: size for key, size in self.heights.items() if key in live}
            self.heights_revision += 1

    def prune(self, count: int, revision: Any, keys: Callable[[int], str]) -> None:
        """`keep_only` for a render: the full key scan runs only when the owner's
        data (count or revision) changed, never on an unchanged render."""
        marker = (count, revision)
        if self._pruned_for is not None and _same_marker(self._pruned_for, marker):
            return
        self._pruned_for = marker
        self.keep_only({keys(index) for index in range(count)})

    def geometry(
        self, count: int, keys: Callable[[int], str], estimate: float, revision: Any = None
    ) -> Geometry:
        key = (
            count,
            estimate,
            self.heights_revision,
            len(self.heights),
            self.context,
            self.frozen,
            self.away,
        )
        if (
            self._cached is not None
            and self._cache_key == key
            and _same_revision(self._cache_revision, revision)
        ):
            return self._cached
        known = self.heights
        average = sum(known.values()) / len(known) if known else estimate
        if self.frozen is not None:
            average = self.frozen
        elif self.away:
            self.frozen = average
        tops, at = [], 0.0
        for index in range(count):
            tops.append(at)
            at += known.get(keys(index), average)
        result = Geometry(count, average, (*tops, at))
        self._cached, self._cache_key, self._cache_revision = result, key, revision
        return result


def _same_revision(left: Any, right: Any) -> bool:
    """Owner revisions are usually the row sequence itself: identity for those,
    equality for hashable tokens."""
    if left is right:
        return True
    if isinstance(left, Hashable) and isinstance(right, Hashable):
        return bool(left == right)
    return False


def _same_marker(left: tuple[int, Any], right: tuple[int, Any]) -> bool:
    return left[0] == right[0] and _same_revision(left[1], right[1])


@ft.component
def VirtualList(
    *,
    name: str,
    count: int,
    height: float,
    row: Callable[[int], Row],
    context: Hashable,
    offsets: tuple[float, ...] | None = None,
    keys: Callable[[int], str] | None = None,
    estimate: float | None = None,
    revision: Any = None,
    gap: float = 0.0,
    place: Place | None = None,
    open_at: Place | None = None,
    at: float | None = None,
    on_scroll: Callable[[Place], None] | None = None,
    on_window: Callable[[tuple[str, ...]], None] | None = None,
    ref: ft.Ref[ft.ListView] | None = None,
    footer: Sequence[ft.Control] = (),
    expand: bool = True,
    horizontal_padding: float = 0.0,
) -> ft.Control:
    """Build a cached visible row window keyed by context, with optional measured heights and
    reader-position callbacks. Owner revisions avoid per-row scans on unchanged renders.
    """
    measures: Any = ft.use_ref(_Measures)
    tick, set_tick = ft.use_state(0)
    measured = estimate is not None and keys is not None
    near = place or open_at
    measures.current.enter(context, (at or 0) > 0 or (near is not None and near.index > 0))
    if measured and keys is not None:
        measures.current.prune(count, revision, keys)
    geometry = (
        measures.current.geometry(count, keys, estimate, revision)
        if measured and keys is not None and estimate is not None
        else Geometry(count, height, offsets)
    )
    own_ref: Any = ft.use_ref(lambda: ft.Ref[ft.ListView]())
    listing: Any = ref if ref is not None else own_ref.current
    cache: Any = ft.use_ref(RowCache)
    viewport: Any = ft.use_ref(None)  # None until the client reports it

    def port() -> float:
        return float(viewport.current or DEFAULT_VIEWPORT)

    placed: Any = ft.use_ref(None)
    if at is not None:
        start = at
    elif near is not None:
        start = geometry.top(min(near.index, max(0, count - 1))) + near.offset
    else:
        start = 0.0
    reading: Any = ft.use_ref(start)  # the offset the reader is at
    reported: Any = ft.use_ref(None)  # the last offset the list itself reported
    awaiting: Any = ft.use_ref(None)  # a scroll request the list has not yet confirmed
    anchor: Any = ft.use_ref(None)  # (row, how far into it) of the first row in view
    opening = window_for(start, port(), geometry)
    # The window is kept with the context it belongs to. Another context adopts its own on this
    # render, with the reader's offset and place: coming back to an earlier context must not find
    # the window of the one in between, whatever the client does or does not report.
    held: Any = ft.use_ref(None)
    _shifts, set_shifts = ft.use_state(0)
    if held.current is None or held.current.context != context:
        held.current = _Built(context, opening, count)
        reading.current = start
        anchor.current = None
        # A list is where it last reported (a new one, at the top) until it says it moved: the
        # new context's offset is only a request, and that position stays the truth meanwhile.
        moves = abs(start - float(reported.current or 0.0)) >= SIZE_TOLERANCE
        awaiting.current = start if moves else None
    built: _Built = held.current

    def set_built(update: _Built) -> None:
        held.current = update
        set_shifts(lambda value: value + 1)

    window = Window(min(built.window.first, count), min(built.window.last, count))
    if count > built.total and built.window.last >= built.total:
        # Rows arrived at the end of the data while the window was there: build them now.
        window = Window(window.first, window_for(reading.current, port(), geometry).last)

    latest: Any = ft.use_ref(None)  # what the last render built, for checks that run later
    latest.current = (window, geometry, count, context)
    settling: Any = ft.use_ref(None)

    def settle() -> None:
        """Build the rows the reader is at if the window does not cover them: the viewport, the
        count or the position moved without a scroll event reaching the build. A scroll request
        the list never confirmed gives way to where the list reported it is."""
        settling.current = None
        if not measures.current.alive:
            return
        if awaiting.current is not None:
            reading.current = float(reported.current or 0.0)
            awaiting.current = None
        built_window, geo, total, ctx = latest.current
        shifted = shift(built_window, reading.current, port(), geo)
        if shifted is not None:
            set_built(_Built(ctx, shifted[0], total, lean=shifted[1]))

    def settle_soon(now: bool = False) -> None:
        # One check follows a burst of reports; the end of a scroll does not wait.
        if settling.current is not None:
            settling.current.cancel()
            settling.current = None
        if now:
            settle()
        else:
            settling.current = asyncio.get_running_loop().call_later(SETTLE_DELAY_S, settle)

    def moved(event: ft.OnScrollEvent) -> None:
        if count == 0 or not math.isfinite(event.pixels):
            return
        pixels = max(0.0, event.pixels)
        kind = _kind(event)
        if kind == "overscroll" and (getattr(event, "overscroll", 0) or 0) > 0:
            pixels = max(pixels, geometry.total - port())  # pulled past the end: the end is reached
        reading.current = reported.current = pixels
        measures.current.away = measures.current.away or pixels > 0
        shown = getattr(event, "viewport_dimension", None)
        if isinstance(shown, int | float) and shown > 0:
            viewport.current = float(shown)
        if awaiting.current is not None and abs(pixels - awaiting.current) <= port() / 2:
            awaiting.current = None  # the list is where it was asked to be
        index = geometry.index_at(pixels)
        anchor.current = (index, pixels - geometry.top(index))
        if on_scroll is not None:
            on_scroll(Place(index, pixels - geometry.top(index), pixels))
        shifted = shift(window, pixels, port(), geometry)
        if shifted is not None:
            set_built(_Built(context, shifted[0], count, lean=shifted[1]))
        settle_soon(now=kind == "end" and shifted is None)  # a shift is the check already

    def resized(event: Any) -> None:
        size = float(event.height)
        if size <= 0 or abs(size - float(viewport.current or 0.0)) < SIZE_TOLERANCE:
            return
        viewport.current = size
        settle_soon()

    ft.use_effect(settle_soon, dependencies=[count])
    ft.use_effect(
        lambda: settle_soon() if awaiting.current is not None else None, dependencies=[context]
    )

    filling: Any = ft.use_ref(None)

    def fill() -> None:
        if not (built.lean and built.context == context):
            return

        def apply() -> None:
            filling.current = None
            if measures.current.alive:
                set_built(_Built(context, window_for(reading.current, port(), geometry), count))

        # After a short wait, so the patch for the rows in view stands alone.
        filling.current = asyncio.get_running_loop().call_later(FILL_DELAY_S, apply)

    def stop_filling() -> None:
        if filling.current is not None:
            filling.current.cancel()
            filling.current = None

    ft.use_effect(fill, dependencies=[built], cleanup=stop_filling)

    def release() -> None:
        measures.current.alive = False
        if settling.current is not None:
            settling.current.cancel()
            settling.current = None

    ft.use_effect(lambda: None, dependencies=[], cleanup=release)

    def measure(key: str) -> Callable[[Any], None]:
        def note(event: Any) -> None:
            known = measures.current
            if not known.record(key, float(event.height)):
                return
            if not known.pending and known.alive:
                known.pending = True

                def apply() -> None:
                    known.pending = False
                    if known.alive:
                        set_tick(lambda value: value + 1)

                asyncio.get_running_loop().call_later(MEASURE_DEBOUNCE_S, apply)

        return note

    async def restore() -> None:
        if listing.current is None:
            return
        previous = placed.current
        placed.current = context
        if place is not None:
            anchor.current = (min(place.index, max(0, count - 1)), place.offset)
        target: float | None = None
        if at is not None:
            await asyncio.sleep(RESTORE_DELAY_S)  # let the mount reach the client first
            target = at
        elif place is not None:
            target = geometry.top(min(place.index, max(0, count - 1))) + place.offset
        elif previous is not None and previous != context:
            target = 0.0
        if target is not None and await scroll_list(listing.current, target):
            if placed.current == context:
                awaiting.current = None  # the client took the request

    ft.use_effect(restore, dependencies=[context, height])

    def keep_place() -> None:
        # A height learned above the first row in view moves it; scroll so it stays where it was.
        if anchor.current is None or listing.current is None:
            return
        index, into = anchor.current
        wanted = geometry.top(min(index, max(0, count - 1))) + into
        if abs(wanted - reading.current) >= 1.0:
            reading.current = wanted
            asyncio.ensure_future(scroll_list(listing.current, wanted))

    ft.use_effect(keep_place, dependencies=[tick])
    controls: list[ft.Control] = []
    extents: list[float] = []
    kept: set[str] = set()
    kept_order: list[str] = []
    for index in range(window.first, window.last):
        described = row(index)
        kept.add(described.key)
        kept_order.append(described.key)
        if measured:
            controls.append(cache.current.get(_measuring(described, measure(described.key), gap)))
        else:
            controls.append(cache.current.get(described))
        extents.append(geometry.top(index + 1) - geometry.top(index))
    cache.current.keep(kept)
    shown = tuple(kept_order)
    ft.use_effect(lambda: on_window(shown) if on_window is not None else None, dependencies=[shown])
    trailing = geometry.total - geometry.top(window.last)
    controls.append(ft.Container(key=f"{name}-after", height=trailing))
    extents.append(trailing)
    controls.extend(footer)
    extents.extend(extent_of(control) for control in footer)
    del tick
    return PlacedListView(
        key=name,
        ref=listing,
        expand=expand,
        spacing=0,
        controls=controls,
        # Each child's height: the client places the children by these, not by where a child at
        # the same index was before. The space above the window is padding, not a child.
        item_extents=extents,
        data={"rows": count, "built": len(window)},
        padding=ft.Padding(
            left=horizontal_padding, top=geometry.top(window.first), right=horizontal_padding
        ),
        on_scroll=moved,
        scroll_interval=100,
        on_size_change=resized,
        size_change_interval=SIZE_INTERVAL_MS,
        build_controls_on_demand=True,
    )


def _kind(event: Any) -> str:
    """The scroll event's kind ("update", "end", "overscroll"...); "" when the event has none."""
    kind = getattr(event, "event_type", None)
    return str(getattr(kind, "value", kind) or "")


def _measuring(described: Row, note: Callable[[Any], None], gap: float) -> Row:
    """The row inside a container that reports its height as laid out, with `gap` below it."""
    return Row(
        described.key,
        described.signature,
        lambda: ft.Container(
            content=described.build(),
            on_size_change=note,
            padding=ft.Padding.only(bottom=gap) if gap else None,
        ),
    )
