"""Compose the inventory shell with stable hooks, derived layout and shared builder context."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from functools import partial
from typing import Any

import flet as ft

from relaydeck.client.app.resources import FeatureSources, use_sources
from relaydeck.client.app.shell.context import ShellContext
from relaydeck.client.app.shell.models import DESTINATIONS, host_context
from relaydeck.client.app.shell.shell_frame import build_body, build_frame, build_nav, build_split
from relaydeck.client.app.shell.shell_panes import (
    apply_collapsed_strip,
    build_pane,
    build_sizing,
    with_console_usage,
)
from relaydeck.client.app.shell.state import use_filter_state, use_sidebar_state
from relaydeck.client.design_system.layout import DESKTOP, PHONE, layout_mode, sidebar_width
from relaydeck.client.design_system.theme import LIST_WIDTH, palette
from relaydeck.client.features.inventory.paging import InventoryFilters
from relaydeck.client.features.update_notice.hooks import use_update_notice
from relaydeck.client.features.update_notice.model import dot_visible
from relaydeck.client.models.inventory_selectors import (
    AgentRow,
    DiagnosticsModel,
    connection_banner,
)
from relaydeck.client.models.preferences import TerminalPrefs
from relaydeck.client.screens.console.screen import host_chips, space_chips
from relaydeck.client.widgets.hosts.host_context import HostContextHeader
from relaydeck.domain.models import AppState


def _acknowledge_update(
    prefs: TerminalPrefs | None,
    on_prefs: Callable[[TerminalPrefs], Any] | None,
    version: str,
) -> None:
    # Called only for a version the Settings panel actually showed.
    if prefs is not None and on_prefs is not None and prefs.acknowledged_update != version:
        on_prefs(replace(prefs, acknowledged_update=version))


@ft.component
def AppShell(
    state: AppState,
    is_wide: bool,
    destination: str,
    on_destination: Callable[[str], Any],
    on_select_agent: Callable[[str], Any],
    on_select_shell: Callable[[str], Any],
    on_select_host: Callable[[str], Any],
    on_banner_action: Callable[[], Any],
    on_open_terminal: Callable[[], Any],
    on_start_agent: Callable[[], Any],
    on_new_terminal: Callable[[], Any],
    on_back: Callable[[], Any] | None = None,
    account: str = "",
    scenarios: tuple[str, ...] = (),
    on_apply_scenario: Callable[[str], Any] | None = None,
    on_logout: Callable[[], Any] | None = None,
    diagnostics: DiagnosticsModel | None = None,
    dark: bool = True,
    viewport_width: float | None = None,
    live_detail: Any = None,
    on_open_shell: Callable[[], Any] | None = None,
    paged_agents: tuple[AgentRow, ...] | None = None,
    inventory_total: int | None = None,
    inventory_busy: bool = False,
    inventory_more: bool = False,
    inventory_error: str = "",
    on_inventory_filters: Callable[[InventoryFilters], Any] | None = None,
    on_inventory_more: Callable[[], Any] | None = None,
    on_inventory_refresh: Callable[[], Any] | None = None,
    retained_views: tuple[Any, ...] = (),
    retained_active: bool = False,
    compact_terminal: bool = False,
    typing_split: bool = False,
    management: Any = None,
    usage_fetch: Any = None,
    usage_identity: Any = None,
    usage_host_id: str = "",
    on_open_usage: Callable[[], Any] | None = None,
    foreground: bool = True,
    notifications_panel: Any = None,
    prefs: TerminalPrefs | None = None,
    on_prefs: Callable[[TerminalPrefs], Any] | None = None,
    prefs_saved: bool = True,
    live_shells: frozenset[str] = frozenset(),
    command_transport: Any = None,
    sources: FeatureSources | None = None,
    on_changes: Callable[[str], Any] | None = None,
    on_window: Callable[[tuple[str, ...]], Any] | None = None,
    reduce_motion: bool = False,
) -> Any:
    (
        query,
        set_query,
        shell_query,
        set_shell_query,
        shell_host,
        filter_host,
        filter_project,
        filter_status,
        set_filter_status,
        filters_open,
        set_filters_open,
        scroll_positions,
        projects,
        project,
        shell_space,
        pick_space,
        pick_host,
        pick_shell_host,
    ) = use_filter_state(
        state=state,
        prefs=prefs,
        on_prefs=on_prefs,
        on_inventory_filters=on_inventory_filters,
    )

    def hosts(host: str, *, shells: bool = False) -> Any:
        return host_chips(state, pick_shell_host if shells else pick_host, host, shells=shells)

    def spaces(host: str, *, shells: bool = False) -> Any:
        selected = shell_space if shells else (filter_project if project else "")
        return space_chips(state, pick_space, selected, host, shells=shells)

    (remembered_width, set_width, collapsed, set_collapsed) = use_sidebar_state(
        destination=destination
    )
    sources = use_sources(command_transport, account, sources)
    update_notice = use_update_notice(sources.update_notice, foreground)
    acknowledged = prefs.acknowledged_update if prefs is not None else ""

    settings_dot = dot_visible(update_notice, acknowledged)
    width = viewport_width if viewport_width is not None else (1280 if is_wide else 360)
    mode = layout_mode(width)
    has_rail = mode != PHONE
    is_wide = mode == DESKTOP
    pane_width = sidebar_width(mode, remembered_width, width)
    console_layout = dark and destination in ("agents", "terminals", "usage", "settings")
    selection = state.selection.session_key
    tokens = palette(dark)
    banner = connection_banner(state)
    host_label, host_status, host_reachable = host_context(state)
    context_header = HostContextHeader(
        key="host-context-header",
        host_label=host_label,
        status_text=host_status,
        reachable=host_reachable,
        dark=dark,
    )  # type: ignore[call-arg]
    list_destination = destination in ("agents", "terminals")
    detail: Any = live_detail

    def resize(change: float) -> None:
        if mode == DESKTOP:
            next_width = sidebar_width(mode, (pane_width or LIST_WIDTH) + change, width)
            if next_width is not None:
                set_width(next_width)

    sizing: list[Any] = []
    if is_wide and list_destination:
        sizing = build_sizing(
            dark=dark,
            is_wide=is_wide,
            list_destination=list_destination,
            mode=mode,
            pane_width=pane_width,
            resize=resize,
            set_collapsed=set_collapsed,
        )
    ctx = ShellContext(
        reduce_motion=reduce_motion,
        account=account,
        banner=banner,
        collapsed=collapsed,
        compact_terminal=compact_terminal,
        console_layout=console_layout,
        context_header=context_header,
        dark=dark,
        destination=destination,
        diagnostics=diagnostics,
        filter_host=filter_host,
        filter_project=filter_project,
        filter_status=filter_status,
        filters_open=filters_open,
        has_rail=has_rail,
        hosts=hosts,
        inventory_busy=inventory_busy,
        inventory_error=inventory_error,
        inventory_more=inventory_more,
        inventory_total=inventory_total,
        is_wide=is_wide,
        labels={d: label for d, label, _ in DESTINATIONS},
        list_destination=list_destination,
        live_detail=live_detail,
        live_shells=live_shells,
        management=management,
        usage_fetch=usage_fetch,
        usage_identity=usage_identity,
        foreground=foreground,
        health_source=sources.health,
        rename_source=sources.rename,
        update_notice=update_notice,
        settings_dot=settings_dot,
        on_update_presented=partial(_acknowledge_update, prefs, on_prefs),
        usage_host_id=usage_host_id,
        on_open_usage=on_open_usage,
        mode=mode,
        notifications_panel=notifications_panel,
        on_apply_scenario=on_apply_scenario,
        on_back=on_back,
        on_banner_action=on_banner_action,
        on_destination=on_destination,
        on_inventory_more=on_inventory_more,
        on_inventory_refresh=on_inventory_refresh,
        on_logout=on_logout,
        on_new_terminal=on_new_terminal,
        on_open_shell=on_open_shell,
        on_open_terminal=on_open_terminal,
        on_prefs=on_prefs,
        on_select_agent=on_select_agent,
        on_changes=on_changes,
        on_window=on_window,
        on_select_host=on_select_host,
        on_select_shell=on_select_shell,
        on_start_agent=on_start_agent,
        paged_agents=paged_agents,
        pane_width=pane_width,
        pick_host=pick_host,
        pick_space=pick_space,
        prefs=prefs,
        prefs_saved=prefs_saved,
        project=project,
        projects=projects,
        query=query,
        resize=resize,
        retained_active=retained_active,
        retained_views=retained_views,
        scenarios=scenarios,
        scroll_positions=scroll_positions,
        selection=selection,
        set_collapsed=set_collapsed,
        set_filter_status=set_filter_status,
        set_filters_open=set_filters_open,
        set_query=set_query,
        set_shell_query=set_shell_query,
        shell_host=shell_host,
        shell_query=shell_query,
        shell_space=shell_space,
        sizing=sizing,
        spaces=spaces,
        split_gap=20 if console_layout and is_wide else 8,
        state=state,
        tokens=tokens,
        typing_split=typing_split,
        width=width,
    )
    console_rows, detail, pane = build_pane(ctx, detail=detail)
    pane = with_console_usage(ctx, pane)
    showing_detail = list_destination and (is_wide or bool(selection) or live_detail is not None)
    showing_list = not list_destination or (not collapsed if is_wide else not showing_detail)
    pane_width, showing_list, pane = apply_collapsed_strip(
        ctx,
        pane=pane,
        pane_width=pane_width,
        showing_list=showing_list,
        console_rows=console_rows,
    )
    if list_destination:
        pane.width = pane_width  # presentation-only control created in this render
    if typing_split and list_destination:
        # Typing hides the list at any width but keeps the remembered collapse state.
        showing_list = False
    split = build_split(
        ctx,
        showing_list=showing_list,
        showing_detail=showing_detail,
        pane_width=pane_width,
        pane=pane,
        detail=detail,
    )
    body = build_body(
        ctx,
        showing_list=showing_list,
        showing_detail=showing_detail,
        pane_width=pane_width,
        split=split,
    )
    nav = build_nav(ctx)
    return build_frame(ctx, body=body, nav=nav, showing_detail=showing_detail)
