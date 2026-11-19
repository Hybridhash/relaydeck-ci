"""Presentational lists: connection banner, search field, scroll helper and the
agent / shell / host lists (components, unchanged from the single module)."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Sequence
from dataclasses import replace
from functools import partial
from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as console_tokens
from relaydeck.client.design_system.accessibility.announcer import LiveRegion
from relaydeck.client.design_system.appearance import chrome_style
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.design_system.inputs import SearchField as NativeSearchField
from relaydeck.client.design_system.lists.virtual_list import Place, Row, VirtualList
from relaydeck.client.design_system.notices import Notice
from relaydeck.client.design_system.theme import (
    BANNER_TONES,
    FONT_SIZE,
    MIN_TOUCH,
    Palette,
    palette,
)
from relaydeck.client.features.rename.api import RenameSource, card_label, shown_title, space_label
from relaydeck.client.features.rename.ui import use_rename_flow
from relaydeck.client.models.changes_summary import changes_row
from relaydeck.client.models.inventory_selectors import AgentRow, BannerModel, HostRow, ShellRow
from relaydeck.client.platform.diagnostics.terminal import diagnostics_logger
from relaydeck.client.platform.flet.events import event_text
from relaydeck.client.widgets.hosts.hosts import HostGroupLabel, HostsHeader
from relaydeck.client.widgets.sessions.cards import AgentCard
from relaydeck.client.widgets.sessions.gestures import AgentGestures
from relaydeck.client.widgets.sessions.rows import agent_tile, empty_hint, host_tile, shell_tile
from relaydeck.client.widgets.sessions.shell_cards import ShellCard, ShellListRow
from relaydeck.domain.models import AppState

LOADING_BAR_HEIGHT = 4
LOADING_BAR_DELAY_S = 0.3  # a load that finishes sooner never shows the bar


def use_slow_flag(busy: bool) -> bool:
    """True once `busy` has lasted LOADING_BAR_DELAY_S; false again as soon as it ends."""
    slow, set_slow = ft.use_state(False)
    timer: Any = ft.use_ref(None)

    def arm() -> None:
        if busy:
            timer.current = asyncio.get_running_loop().call_later(
                LOADING_BAR_DELAY_S, lambda: set_slow(True)
            )
        else:
            set_slow(False)

    def disarm() -> None:
        if timer.current is not None:
            timer.current.cancel()
            timer.current = None

    ft.use_effect(arm, dependencies=[busy], cleanup=disarm)
    return slow and busy


@ft.component
def ConnectionBanner(model: BannerModel, on_action: Callable[[], Any]) -> Any:
    ready, set_ready = ft.use_state(model.delay_seconds == 0)
    delay_task: Any = ft.use_ref(None)
    delay_generation: Any = ft.use_ref(0)

    def cleanup_delay() -> None:
        delay_generation.current += 1
        if delay_task.current is not None:
            delay_task.current.cancel()
            delay_task.current = None

    def delay() -> None:
        cleanup_delay()
        generation = delay_generation.current
        set_ready(model.delay_seconds == 0)

        async def reveal() -> None:
            loop = asyncio.get_running_loop()
            elapsed = 0 if model.not_live_since is None else loop.time() - model.not_live_since
            await asyncio.sleep(max(0, model.delay_seconds - elapsed))
            if generation == delay_generation.current:
                set_ready(True)

        if model.delay_seconds:
            delay_task.current = asyncio.create_task(reveal(), name="relaydeck-reconnect-banner")

    # Changing reason/attempt does not restart the same outage's timer.
    ft.use_effect(
        delay, dependencies=[model.delay_seconds, model.not_live_since], cleanup=cleanup_delay
    )
    if model.delay_seconds and not ready:
        return ft.Container(key="connection-banner-pending", height=0)
    # ft.Banner requires at least one action; action-less notices use a
    # slim dark strip in the console palette (not a stock Material card).
    if not model.action:
        return Notice(
            key="connection-banner",
            margin=ft.Margin.only(left=28, right=28, top=12),
            padding=ft.Padding.symmetric(horizontal=16, vertical=10),
            border_radius=14,
            bgcolor=console_tokens.CARD,
            border=ft.Border.all(1, console_tokens.BORDER),
            content=ft.Row(
                spacing=10,
                controls=[
                    ft.Icon(ft.Icons.INFO_OUTLINE, size=18, color=BANNER_TONES[model.tone]),
                    ft.Container(
                        expand=True,
                        content=LiveRegion(  # type: ignore[call-arg]
                            key="banner-live",
                            text=model.text,
                            content=ft.Text(
                                model.text,
                                key="banner-text",
                                color=console_tokens.TEXT_SECONDARY,
                                size=13,
                            ),
                        ),
                    ),
                ],
            ),
        )
    return ft.Banner(
        key="connection-banner",
        leading=ft.Icon(ft.Icons.INFO, color=BANNER_TONES[model.tone]),
        content=LiveRegion(  # type: ignore[call-arg]  # key= popped by @ft.component
            key="banner-live",
            text=model.text,
            content=ft.Text(model.text, key="banner-text"),
        ),
        actions=[
            Button(
                model.action,
                variant="text",
                key="banner-action",
                height=MIN_TOUCH,
                on_click=lambda e: on_action(),
            )
        ],
    )


@ft.component
def SearchField(value: str, on_change: Callable[[str], Any], label: str, dark: bool = True) -> Any:
    tokens = palette(dark)
    return NativeSearchField(
        key="inventory-query",
        label=label,
        value=value,
        color=tokens.text,
        label_style=ft.TextStyle(color=tokens.muted),
        hint_style=ft.TextStyle(color=tokens.muted),
        text_size=16,
        filled=True,
        fill_color=tokens.raised,
        cursor_color=tokens.accent_text,
        border={
            ft.ControlState.DEFAULT: ft.OutlineInputBorder(
                border_radius=8, side=ft.BorderSide(1, tokens.outline)
            ),
            ft.ControlState.FOCUSED: ft.OutlineInputBorder(
                border_radius=8, side=ft.BorderSide(2, tokens.accent_text)
            ),
        },
        content_padding=12,
        on_change=lambda e: on_change(event_text(e)),
    )


@ft.component
def InventoryScroll(
    items: list[Any],
    control_key: str,
    spacing: int,
    positions: Any = None,
) -> Any:
    """Restore list position after a destination unmounts its scroll controller."""
    column: Any = ft.use_ref(lambda: ft.Ref[ft.Column]())
    alive = ft.use_ref(True)

    def remember(event: ft.OnScrollEvent) -> None:
        if alive.current and positions is not None and math.isfinite(event.pixels):
            positions.current[control_key] = max(0.0, event.pixels)

    def restore() -> None:
        offset = positions.current.get(control_key, 0.0) if positions is not None else 0.0
        if not isinstance(offset, int | float) or not offset:
            return  # nothing kept, or a row place kept by a windowed list of the same name

        async def run() -> None:
            # Let the mount patch reach the carrier before requesting its scroll.
            await asyncio.sleep(0.05)
            if alive.current and column.current.current is not None:
                try:
                    # Pinned scroll_to delegates here but has no timeout argument.
                    # Bound the native request; do not cancel its registry waiter.
                    await column.current.current._invoke_method(
                        "scroll_to", {"offset": offset, "duration": 0}, timeout=1
                    )
                except (TimeoutError, RuntimeError):
                    pass  # Keep the recorded position for the next explicit visit.

        asyncio.create_task(run())

    def stop() -> None:
        alive.current = False

    ft.use_effect(restore, dependencies=[], cleanup=stop)
    return ft.Column(
        key=control_key,
        ref=column.current,
        controls=items,
        spacing=spacing,
        scroll=ft.ScrollMode.AUTO,
        on_scroll=remember,
        expand=True,
    )


AGENT_CARD_ESTIMATE = 96.0  # dp; cards are measured as they are built, this only starts the guess
SHELL_ESTIMATE = 72.0


def where(positions: Any, name: str) -> Place | None:
    """The row the reader was at, which survives rows being measured differently next time (a
    pixel offset would not). An empty list's scroller stores pixels under the same name."""
    saved = positions.current.get(name) if positions is not None else None
    return saved if isinstance(saved, Place) and saved.pixels > 0 else None


def current_of(latest: Any) -> Callable[[str], Any]:
    """The callback as of the latest render, for tiles that outlive it."""
    return lambda key: latest.current(key)


def tile_list(
    name: str, specs: Sequence[Row], estimate: float, gap: float, positions: Any = None
) -> Any:
    """The shell lists: only the tiles near the screen are built, measured as they are,
    and the reader's place is kept for the next visit."""

    def remember(place: Place) -> None:
        if positions is not None:
            positions.current[name] = place

    return VirtualList(
        name=name,
        count=len(specs),
        height=estimate,
        row=lambda index: specs[index],
        keys=lambda index: specs[index].key,
        estimate=estimate + gap,
        revision=specs,
        gap=gap,
        context=(name,),
        place=where(positions, name),
        on_scroll=remember,
    )


def _count_header(
    shown: int, total: int | None, dark: bool, tokens: Palette, on_start_agent: Callable[[], Any]
) -> Any:
    """The agent count with the Start agent button."""
    return ft.Row(
        wrap=True,
        controls=[
            ft.Text(
                f"{shown} of {total} agents" if total is not None else f"{shown} agents",
                key="agents-count",
                size=FONT_SIZE["body"],
                color=tokens.text,
            ),
            Button(
                "Start agent",
                key="start-agent-open",
                height=MIN_TOUCH,
                style=chrome_style(dark),
                on_click=lambda e: on_start_agent(),
            ),
        ],
    )


def _list_footer(
    *,
    shown: int,
    total: int | None,
    dark: bool,
    show_header: bool,
    busy: bool,
    more: bool,
    on_refresh: Callable[[], Any] | None,
    on_more: Callable[[], Any] | None,
    tokens: Palette,
) -> Any:
    """Count (when no header shows it), Refresh and Load more."""
    return ft.Row(
        wrap=True,
        controls=[
            *(
                [
                    ft.Text(
                        f"{shown} of {total} agents" if total is not None else f"{shown} agents",
                        key="agents-count",
                        size=12,
                        color=tokens.muted,
                    )
                ]
                if not show_header and not dark  # header shows it
                else []
            ),
            Button(
                "Refresh",
                variant="text",
                key="inventory-refresh",
                height=MIN_TOUCH,
                disabled=busy,
                # the dark Console refreshes from its header icon.
                visible=on_refresh is not None and not dark,
                on_click=lambda e: on_refresh() if on_refresh is not None else None,
            ),
            Button(
                "Load more",
                variant="text",
                key="inventory-more",
                height=MIN_TOUCH,
                disabled=busy,
                visible=more,
                on_click=lambda e: on_more() if on_more is not None else None,
            ),
        ],
    )


@ft.component
def AgentsList(
    rows: tuple[AgentRow, ...],
    selected: str,
    on_select: Callable[[str], Any],
    on_start_agent: Callable[[], Any],
    dark: bool = True,
    phone: bool = False,
    total: int | None = None,
    busy: bool = False,
    more: bool = False,
    error: str = "",
    on_more: Callable[[], Any] | None = None,
    on_refresh: Callable[[], Any] | None = None,
    show_header: bool = True,
    scroll_positions: Any = None,
    command_transport: RenameSource | None = None,
    state: AppState | None = None,
    foreground: bool = True,
    pins: frozenset[str] = frozenset(),
    on_pin: Callable[[str], Any] | None = None,
    on_changes: Callable[[str], Any] | None = None,
    on_window: Callable[[tuple[str, ...]], Any] | None = None,
) -> Any:
    flow = use_rename_flow(rows)
    show_loading = use_slow_flag(busy)
    renamable = command_transport is not None and state is not None
    tokens = palette(dark)
    # Cards whose inputs did not change keep their control instances, so a render caused by one
    # row, a session tick or an inventory update behind an open terminal diffs only what moved.
    # The callbacks read the current flow and selector through refs, never a stale render's.
    current: Any = ft.use_ref(None)
    current.current = (flow, on_select, on_changes)

    def rename_of(row: AgentRow, title: str) -> Callable[[], None]:
        return lambda: current.current[0].open_rename(row.key, title)

    def menu_of(row: AgentRow) -> Callable[[str], None]:
        def open_menu(source: str) -> None:
            diagnostics_logger().info("agent_menu open source=%s", source)
            current.current[0].set_menu(row.key)

        return open_menu

    def card_menu_of(row: AgentRow) -> Callable[[], None]:
        return lambda: menu_of(row)("card")

    def select_of(row: AgentRow) -> Callable[[], Any]:
        return lambda: current.current[1](row.key)

    def select_card(key: str) -> Any:
        return current.current[1](key)

    def open_changes(key: str) -> Any:
        return current.current[2](key)

    def build_tile(row: AgentRow, title: str, summary: Any) -> Any:
        if dark:
            tile = AgentCard(
                agent_id=row.key,
                name=title,
                subject=row.subject,
                status=row.status.value,
                subtitle=row.subtitle,
                meta_left="",
                selected=row.key == selected,
                on_select=select_card,
                agent=row.agent,
                compact=phone,
                meta_right="Unread" if row.unread else "",
                highlighted=row.key == flow.highlight,
                on_name_double_tap=rename_of(row, title) if renamable and not phone else None,
                on_long_press=card_menu_of(row) if renamable else None,
                pinned=row.key in pins,
                space=space_label(row, state),
                changes=changes_row(summary),
                on_changes=open_changes if on_changes is not None else None,
            )
            # Set identity on this freshly built presentation control. The live
            # terminal owner below is untouched; row callbacks keep scoped IDs.
            tile.key = f"agent-{row.key}"
        else:
            tile = agent_tile(
                row,
                row.key == selected,
                select_card,
                dark,
                phone,
                title=title,
                highlighted=row.key == flow.highlight,
                on_name_double_tap=rename_of(row, title) if renamable and not phone else None,
            )
            if row.key in pins and isinstance(tile.title, ft.Control):
                tile.title = ft.Row(
                    tight=True,
                    spacing=6,
                    controls=[
                        tile.title,
                        ft.Icon(
                            ft.Icons.PUSH_PIN,
                            key=f"agent-pinned-{row.key}",
                            size=14,
                            color=tokens.muted,
                        ),
                    ],
                )
        if renamable:
            tile = AgentGestures(
                row.key,
                tile,
                card_label(row, title, space_label(row, state), row.key in pins),
                dark,
                on_menu=menu_of(row),
                on_rename=rename_of(row, title),
                on_select=select_of(row),
            )
        return tile

    def described(index: int) -> Row:
        row = rows[index]
        title = shown_title(row, flow.overrides)
        summary = row.changes
        signature = (
            # A card rebuilds when its changes revision or state moves, not on every refresh.
            replace(row, changes=None),
            (summary.revision, summary.state) if summary is not None else None,
            on_changes is not None,
            title,
            row.key == selected,
            row.key == flow.highlight,
            row.key in pins,
            renamable,
            phone,
            dark,
            space_label(row, state),
        )
        return Row(row.key, signature, lambda: build_tile(row, title, summary))

    positions = scroll_positions

    def remember(place: Place) -> None:
        if positions is not None:
            positions.current["agents-tiles"] = place

    spacing = 10 if dark else 4
    tiles: Any
    if rows:
        # Only the cards near the screen are built; their heights are measured as they appear.
        tiles = VirtualList(
            name="agents-tiles",
            count=len(rows),
            height=AGENT_CARD_ESTIMATE,
            row=described,
            keys=lambda i: rows[i].key,
            estimate=AGENT_CARD_ESTIMATE + spacing,
            revision=rows,
            gap=spacing,
            context=("agents",),
            place=where(positions, "agents-tiles"),
            on_scroll=remember,
            on_window=on_window,
        )
    else:
        tiles = InventoryScroll(
            items=[empty_hint("agents-empty", "No agents match these filters.")],
            control_key="agents-tiles",
            spacing=spacing,
            positions=scroll_positions,
        )
    return ft.Column(
        key="agents-list-bounds",
        expand=True,
        controls=[
            *(
                [_count_header(len(rows), total, dark, tokens, on_start_agent)]
                if show_header
                else []
            ),
            ft.Text(error, key="inventory-page-error", color=tokens.muted, visible=bool(error)),
            # The bar overlays the list's top edge and takes no space, so cards never move; it
            # appears only when a load is slow enough to notice.
            ft.Stack(
                key="agents-list-area",
                expand=True,
                controls=[
                    tiles,
                    ft.Container(
                        top=0,
                        left=0,
                        right=0,
                        content=ft.ProgressBar(
                            key="inventory-page-loading",
                            visible=show_loading,
                            bar_height=LOADING_BAR_HEIGHT,
                        ),
                    ),
                ],
            ),
            _list_footer(
                shown=len(rows),
                total=total,
                dark=dark,
                show_header=show_header,
                busy=busy,
                more=more,
                on_refresh=on_refresh,
                on_more=on_more,
                tokens=tokens,
            ),
            *flow.overlays(
                rows=rows,
                state=state if renamable else None,
                transport=command_transport,
                on_select=on_select,
                on_refresh=on_refresh,
                dark=dark,
                phone=phone,
                foreground=foreground,
                pins=pins,
                on_pin=on_pin if renamable else None,
            ),
        ],
    )


@ft.component
def ShellsList(
    rows: tuple[ShellRow, ...],
    selected: str,
    on_select: Callable[[str], Any],
    on_new_terminal: Callable[[], Any],
    dark: bool = True,
    phone: bool = False,
    scroll_positions: Any = None,
) -> Any:
    tokens = palette(dark)
    latest: Any = ft.use_ref(None)
    latest.current = on_select
    pick = current_of(latest)
    tiles: Any
    if rows:
        specs = [
            Row(
                row.key,
                (row, row.key == selected, dark, phone),
                partial(shell_tile, row, row.key == selected, pick, dark, phone),
            )
            for row in rows
        ]
        tiles = tile_list("shells-tiles", specs, SHELL_ESTIMATE, 4, scroll_positions)
    else:
        tiles = InventoryScroll(
            items=[empty_hint("shells-empty", "No shell terminals yet.")],
            control_key="shells-tiles",
            spacing=4,
            positions=scroll_positions,
        )
    return ft.Column(
        key="shells-list-bounds",
        expand=True,
        controls=[
            ft.Row(
                wrap=True,
                controls=[
                    ft.Text(
                        f"{len(rows)} shells",
                        key="shells-count",
                        size=FONT_SIZE["body"],
                        color=tokens.text,
                    ),
                    Button(
                        "New terminal",
                        key="new-terminal-open",
                        height=MIN_TOUCH,
                        style=chrome_style(dark),
                        on_click=lambda e: on_new_terminal(),
                    ),
                ],
            ),
            tiles,
        ],
    )


@ft.component
def ShellCardsList(
    rows: Sequence[ShellListRow],
    selected: str,
    on_select: Callable[[str], Any],
    compact: bool = False,
    scroll_positions: Any = None,
) -> Any:
    """The dark Terminals pane's list of shell cards."""
    latest: Any = ft.use_ref(None)
    latest.current = on_select
    pick = current_of(latest)
    if not rows:
        return InventoryScroll(
            items=[empty_hint("shells-empty", "No shell terminals yet.")],
            control_key="shells-tiles",
            spacing=10,
            positions=scroll_positions,
        )
    specs = [
        Row(
            row.key,
            (row, row.key == selected, compact),
            partial(ShellCard, row, row.key == selected, pick, compact=compact),
        )
        for row in rows
    ]
    return tile_list("shells-tiles", specs, SHELL_ESTIMATE, 10, scroll_positions)


@ft.component
def HostsList(
    rows: tuple[HostRow, ...],
    selected: str,
    on_select: Callable[[str], Any],
    dark: bool = True,
    phone: bool = False,
) -> Any:
    """The host tiles at their natural height, for a page that scrolls as a whole."""

    def tile(row: HostRow) -> Any:
        return host_tile(row, row.key == selected, on_select, dark, phone)

    if not dark:
        return ft.Column(
            key="hosts-list-bounds",
            controls=[
                ft.Column(key="hosts-tiles", spacing=4, controls=[tile(row) for row in rows])
                if rows
                else ft.Text("No hosts", key="hosts-empty")
            ],
        )
    tiles: list[Any] = []
    for label, key, members in (
        ("Connected", "hosts-group-connected", [row for row in rows if row.reachable]),
        ("Offline", "hosts-group-offline", [row for row in rows if not row.reachable]),
    ):
        if members:
            tiles.append(HostGroupLabel(f"{label} · {len(members)}", key=key))
            tiles.extend(tile(row) for row in members)
    return ft.Column(
        key="hosts-list-bounds",
        spacing=18,
        controls=[
            HostsHeader("RelayDeck", "Hosts"),
            ft.Column(key="hosts-tiles", spacing=14, controls=tiles)
            if tiles
            else ft.Text("No hosts", key="hosts-empty"),
        ],
    )
