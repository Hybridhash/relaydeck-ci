"""Build retained terminal views, inventory navigation and overlays."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from functools import partial
from typing import Any

import flet as ft

from relaydeck.client.app.hooks.support import RETAINED_PARK_OFFSET
from relaydeck.client.app.resources import FeatureSources
from relaydeck.client.app.sessions.retained import RetainedSessions
from relaydeck.client.app.shell.models import agent_scope_label
from relaydeck.client.app.shell.shell import AppShell as InvShell
from relaydeck.client.app.state.sync import SyncController
from relaydeck.client.design_system.appearance import chrome_style
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.design_system.headers import ScreenHeader
from relaydeck.client.design_system.loading_spinner import Spinner
from relaydeck.client.design_system.theme import (
    MIN_TOUCH,
    SCRIM_COLOR,
    SCRIM_OPACITY,
    SHEET_MAX_WIDTH,
    palette,
)
from relaydeck.client.features.create.components import (
    CreationSheetCard,
    NewTerminalSheet,
    StartAgentSheet,
)
from relaydeck.client.features.create.shell_chooser import ShellChooser
from relaydeck.client.features.inventory.paging import InventoryPager
from relaydeck.client.features.terminal.switcher import switcher_rows
from relaydeck.client.features.web_push.web_push import WebPushPanel
from relaydeck.client.models.inventory_selectors import (
    agent_names,
    agent_subject,
    metadata_rows,
    with_changes,
    workspace_shells,
)
from relaydeck.client.platform.transport.transport import HttpxGatewayTransport
from relaydeck.client.ports.commands import CommandStatusSource
from relaydeck.client.screens.settings.screen import ManagementPane
from relaydeck.client.screens.shell.screen import mount_shell_screen
from relaydeck.client.screens.terminal.screen import mount_agent_screen
from relaydeck.client.widgets.command_recovery import CommandRecovery


def keyboard_inset(visible_height: float) -> float:
    """Px the keyboard takes from the page: layout height minus the published visible height.
    Zero outside a page context (tests) or when the browser shrank the layout itself."""
    try:
        layout_height = float(ft.context.page.height or 0.0)
    except Exception:  # noqa: BLE001 - no page in this context
        return 0.0
    return max(0.0, layout_height - float(visible_height or 0.0))


def host_line_for(state: Any, key: str) -> str:
    """Agent header second line: "host / space", then the chat subject when it differs."""
    agent = state.inventory.agents.get(key)
    scope = agent_scope_label(state, agent.host_id, agent.workspace_id) if agent else ""
    subject = agent_subject(state, key)
    return f"{scope} · {subject}" if subject else scope


def agent_space_tabs(
    state: Any, host_id: str, agent: Any, names: Mapping[str, str]
) -> tuple[tuple[str, str, str], ...]:
    """Agent switcher tabs: (id, label, status value) for the agents in the open agent's space
    (same host and workspace). Agents from another space cannot be switched to from here.
    """
    workspace_id = agent.workspace_id if agent is not None else None
    return tuple(
        (
            identifier,
            names.get(identifier, item.terminal_id),
            str(getattr(getattr(item, "status", "unknown"), "value", "unknown")),
        )
        for identifier, item in state.inventory.agents.items()
        if item.host_id == host_id and (workspace_id is None or item.workspace_id == workspace_id)
    )


def space_switcher_rows(
    state: Any, host_id: str, key: str, agent: Any, names: Mapping[str, str]
) -> tuple[Any, ...]:
    # Same space only: an agent in another space may not be loaded, so it can't be opened.
    same_space = {tab[0] for tab in agent_space_tabs(state, host_id, agent, names)}
    return tuple(row for row in switcher_rows(state, host_id, key, names) if row.key in same_space)


def build_retained_views(
    *,
    context_strip: Any = None,
    active_entry: Any,
    app_state: Any,
    back_from_terminal: Any,
    connection_live: Any,
    connection_text: Any,
    dark: bool,
    font_size: Any,
    handle_logout: Any,
    keyboard_open: Any,
    new_terminal_from_shell: Any,
    on_composer_focus: Any,
    on_typing_exit: Any = None,
    field_focused: bool = False,
    on_keep_typing: Any = None,
    on_session_recovery: Callable[[], Any] | None,
    open_shell_for_agent: Any,
    pending_commands: Any,
    permitted: Any,
    phone: Any,
    prefs: Any,
    retained: RetainedSessions,
    retained_views: Any,
    retry_sync: Any,
    select_inventory_agent: Any,
    set_operations_open: Any,
    tier: Any,
    touch: bool,
    typing_split: Any,
    viewport: Any,
    reduce_motion: bool = False,
) -> Any:
    """Mount one positioned slot per retained session (active slot on top, others parked)."""
    names = agent_names(app_state)
    for entry in retained.entries():
        if permitted.get(entry.host_id) != entry.epoch:
            continue
        active = entry is active_entry
        if entry.kind == "agent":
            # a reloaded page resumes with the Settings takeover rule.
            entry.session.auto_take_allowed = prefs.auto_takeover
            agent = app_state.inventory.agents.get(entry.key)
            entry.session.store.agent_label = names.get(entry.key, "") if agent else ""
            screen = mount_agent_screen(  # type: ignore[call-arg]  # declarative key
                entry.session,
                context_strip=context_strip if active else None,
                key=f"retained-agent-{entry.render_key}",
                dark=dark,
                on_back=back_from_terminal if phone else None,
                viewport_height=viewport[1],
                viewport_width=viewport[0],
                keyboard_inset=keyboard_inset(viewport[1]),
                on_open_shell=partial(open_shell_for_agent, entry.key),
                title=names.get(entry.key, "") if agent else "",
                host_line=host_line_for(app_state, entry.key) if agent else entry.host_id,
                confirm_takeover=prefs.confirm_takeover,
                show_key_row=prefs.show_key_row,
                haptics=prefs.haptics,
                font_size=font_size,
                phone=phone,
                width_tier=tier,
                keyboard_open=keyboard_open,
                touch=touch,
                connection_text=connection_text,
                connection_live=connection_live,
                on_connection_action=(on_session_recovery or handle_logout)
                if app_state.revoked
                else retry_sync,
                switcher_rows=space_switcher_rows(
                    app_state, entry.host_id, entry.key, agent, names
                ),
                # dock switches do not add a history entry.
                on_agent_tab=partial(select_inventory_agent, record=False),
                on_operations=(lambda: set_operations_open(True))
                if pending_commands is not None
                else None,
                typing_split=typing_split and active,
                on_composer_focus=on_composer_focus,
                on_typing_exit=on_typing_exit,
                keyboard_up=field_focused,
                on_keep_typing=on_keep_typing,
                reduce_motion=reduce_motion,
            )
        else:
            tab = app_state.inventory.tabs.get(entry.key)
            tab_host = app_state.inventory.hosts.get(entry.host_id)
            screen = mount_shell_screen(  # type: ignore[call-arg]  # declarative key
                entry.session,
                key=f"retained-shell-{entry.render_key}",
                on_back=back_from_terminal,
                on_new_terminal=new_terminal_from_shell,
                dark=dark,
                viewport_height=viewport[1],
                title=(tab.label or tab.id) if tab else "",
                host_line=f"{(tab_host.label if tab_host else '') or entry.host_id} · shell",
                show_back=phone,
                confirm_takeover=prefs.confirm_takeover,
                show_key_row=prefs.show_key_row,
                haptics=prefs.haptics,
                font_size=font_size,
                phone=phone,
                width_tier=tier,
                keyboard_open=keyboard_open,
                touch=touch,
                connection_text=connection_text,
                connection_live=connection_live,
                on_connection_action=(on_session_recovery or handle_logout)
                if app_state.revoked
                else retry_sync,
                on_operations=(lambda: set_operations_open(True))
                if pending_commands is not None
                else None,
                reduce_motion=reduce_motion,
            )
        # Hidden surfaces keep an identical Dart widget tree (Opacity/IgnorePointer toggles remount
        # the native engine). Inactive slots park off screen at the same width, so the PTY sees no
        # resize.
        offset = 0 if active else RETAINED_PARK_OFFSET
        retained_views.append(
            ft.Container(
                key=f"retained-slot-{entry.render_key}",
                left=-offset,
                right=offset,
                top=0,
                bottom=0,
                disabled=not active,
                content=screen,
            )
        )


def build_inventory_shell(
    *,
    sources: FeatureSources,
    account: str,
    active_entry: Any,
    app_state: Any,
    changes: Any,
    compact_terminal: Any,
    controller: SyncController,
    dark: bool,
    destination: Any,
    diag: Any,
    foreground: bool,
    go_destination: Any,
    handle_logout: Any,
    inventory_hosts: Any,
    is_wide: Any,
    live_detail: Any,
    live_shells: Any,
    main_screen_revision: Any,
    management_fields: Any,
    more_inventory: Any,
    navigate: Any,
    notification_status: tuple[str, bool] | None,
    on_apply_scenario: Callable[[str], Any] | None,
    on_notifications_enable: Callable[[], Any] | None,
    on_session_recovery: Callable[[], Any] | None,
    open_shell_for_agent: Any,
    open_terminal: Any,
    pager: InventoryPager | None,
    prefs: Any,
    prefs_saved: Any,
    refresh_inventory: Any,
    retained_views: Any,
    retry_sync: Any,
    scenarios: tuple[str, ...],
    select_inventory_agent: Any,
    select_shell: Any,
    selection: Any,
    set_inventory_filters: Any,
    set_prefs: Any,
    set_sheet: Any,
    set_sheet_error: Any,
    set_sheet_status: Any,
    transport: HttpxGatewayTransport,
    typing_split: Any,
    viewport: Any,
    reduce_motion: bool = False,
) -> Any:
    """The inventory shell around the retained workbench."""
    if changes.screen is not None:
        live_detail, active_entry = changes.screen, None
    shell = InvShell(
        reduce_motion=reduce_motion,
        state=app_state,
        sources=sources,
        is_wide=is_wide,
        destination=destination,
        on_destination=go_destination,
        on_select_agent=select_inventory_agent,
        on_changes=changes.on_changes,
        on_window=changes.on_window,
        on_select_shell=select_shell,
        on_select_host=lambda key: navigate("settings", key),
        usage_fetch=sources.usage,
        usage_identity=(account, id(transport)),
        usage_host_id=app_state.connection.host_id,
        on_open_usage=lambda: navigate("usage"),
        foreground=foreground,
        on_banner_action=(on_session_recovery or handle_logout)
        if app_state.revoked
        else retry_sync,
        paged_agents=with_changes(
            metadata_rows(
                (
                    row
                    for row in pager.visible_rows
                    if inventory_hosts.get(row["host_id"]) == row["host_epoch"]
                ),
                int(time.time() * 1000),
                names=agent_names(app_state),
            ),
            changes.summaries,
        )
        if pager is not None and pager.account == account
        else (() if pager is not None else None),
        inventory_total=pager.total if pager is not None else None,
        inventory_busy=pager.busy if pager is not None else False,
        inventory_more=pager.more if pager is not None else False,
        inventory_error=pager.error if pager is not None else "",
        on_inventory_filters=set_inventory_filters,
        on_inventory_more=more_inventory if pager is not None else None,
        on_inventory_refresh=refresh_inventory if pager is not None else None,
        command_transport=transport,
        on_open_terminal=open_terminal,
        on_start_agent=lambda: (
            set_sheet("start-agent"),
            set_sheet_error(""),
            set_sheet_status(""),
        ),
        on_new_terminal=lambda: (
            set_sheet("new-terminal"),
            set_sheet_error(""),
            set_sheet_status(""),
        ),
        on_back=lambda: navigate(destination),
        account=account,
        scenarios=scenarios,
        on_apply_scenario=on_apply_scenario,
        on_logout=handle_logout,
        management=(
            ManagementPane(  # type: ignore[call-arg]  # key handled by @ft.component
                key=f"management-{account}",
                transport=sources.hosts,
                account=account,
                destination=destination,
                selected_host=app_state.selection.host_id,
                on_hosts=controller.apply_grants,
                on_logout=handle_logout,
                dark=dark,
                # Settings frames it in its own panel (no card-in-card).
                embedded=dark and destination == "settings",
                initial_fields=management_fields.current,
                on_fields=lambda fields: setattr(management_fields, "current", fields),
                main_screen_revision=main_screen_revision,
            )
            if destination == "settings"
            else None
        ),
        diagnostics=diag,
        notifications_panel=(
            ft.Column(
                controls=[
                    ScreenHeader("Notifications", color=palette(dark).text),
                    ft.Text(
                        notification_status[0]
                        or "Enable notifications to receive session updates.",
                        key="notifications-status",
                        color=palette(dark).muted,
                    ),
                    Button(
                        "Enable notifications",
                        key="notifications-enable",
                        height=MIN_TOUCH,
                        style=chrome_style(dark),
                        disabled=notification_status[1] or not foreground,
                        on_click=lambda e: on_notifications_enable()
                        if on_notifications_enable
                        else None,
                    ),
                ]
            )
            if notification_status is not None
            # Browser app: this device's web push state and Turn off (no native enable here).
            else (
                WebPushPanel(  # type: ignore[call-arg]  # key handled by @ft.component
                    key=f"web-push-{account}",
                    transport=sources.web_push,
                    dark=dark,
                )
                if destination == "settings"
                else None
            )
        ),
        dark=dark,
        viewport_width=viewport[0],
        live_detail=live_detail,
        retained_views=tuple(retained_views),
        retained_active=active_entry is not None,
        compact_terminal=compact_terminal,
        typing_split=typing_split,
        on_open_shell=(lambda: open_shell_for_agent(selection))
        if destination == "agents" and selection
        else None,
        prefs=prefs,
        on_prefs=set_prefs,
        prefs_saved=prefs_saved,
        live_shells=live_shells,
    )
    return shell


def build_creation_sheets(
    *,
    active_host: Any,
    app_state: Any,
    cancel_creation: Any,
    choose_shell: Any,
    create_workspace_shell: Any,
    creation_drafts: Any,
    dark: bool,
    hosts: Any,
    inventory_filters: Any,
    open_error: Any,
    overlays: list[Any],
    retained: RetainedSessions,
    set_open_error: Any,
    set_sheet: Any,
    sheet: Any,
    sheet_error: Any,
    sheet_status: Any,
    shell_origin: Any,
    submit_agent: Any,
    submit_shell: Any,
    submitting: Any,
    workspaces_arg: Any,
) -> Any:
    """The creation sheet overlays (start agent, new terminal, shell chooser, launch)."""
    if sheet == "start-agent":
        overlays.append(
            StartAgentSheet(
                hosts=hosts,
                workspaces_by_host=workspaces_arg,
                kinds=(),
                kind_catalogs={
                    host.id: host.agent_catalog.available
                    if host.reachable and host.agent_catalog is not None
                    else ()
                    for host in app_state.inventory.hosts.values()
                },
                existing_names_by_host={
                    host.id: frozenset(
                        agent.name
                        for agent in app_state.inventory.agents.values()
                        if agent.host_id == host.id
                    )
                    for host in app_state.inventory.hosts.values()
                },
                submitting=submitting,
                error=sheet_error,
                command_status=sheet_status,
                on_submit=submit_agent,
                on_cancel=cancel_creation,
                initial_draft=creation_drafts.current.get("start-agent"),
                preferred_host=inventory_filters.project_host
                or inventory_filters.host_id
                or active_host,
                preferred_workspace=inventory_filters.project_id,
                on_draft_change=lambda value: creation_drafts.current.__setitem__(
                    "start-agent", value
                ),
                dark=dark,
            )
        )
    elif sheet == "new-terminal":
        overlays.append(
            NewTerminalSheet(
                hosts=hosts,
                workspaces_by_host=workspaces_arg,
                submitting=submitting,
                error=sheet_error,
                command_status=sheet_status,
                on_submit=submit_shell,
                on_cancel=cancel_creation,
                initial_draft=creation_drafts.current.get("new-terminal"),
                preferred_host=inventory_filters.project_host
                or inventory_filters.host_id
                or active_host,
                preferred_workspace=inventory_filters.project_id,
                on_draft_change=lambda value: creation_drafts.current.__setitem__(
                    "new-terminal", value
                ),
                dark=dark,
            )
        )
    elif sheet == "shell-chooser":
        origin = shell_origin.current
        choices = workspace_shells(app_state, origin[1], origin[2]) if origin else ()
        overlays.append(
            ShellChooser(
                tabs=choices,
                on_choose=choose_shell,
                on_new=lambda: create_workspace_shell(fresh=True),
                on_cancel=lambda: set_sheet(""),
                dark=dark,
            )
        )
    elif sheet == "shell-launch":
        overlays.append(
            ft.Column(
                key="shell-launch",
                controls=[
                    ft.Text(sheet_status, key="sheet-status"),
                    ft.Text(sheet_error, key="sheet-error", visible=bool(sheet_error)),
                    Spinner(visible=submitting),
                    Button(
                        "Done",
                        key="shell-launch-done",
                        height=48,
                        disabled=submitting,
                        on_click=lambda e: set_sheet(""),
                    ),
                ],
            )
        )
    elif open_error or retained.error:

        def dismiss_open_error() -> None:
            retained.error = ""
            set_open_error("")
            retained.changed()

        overlays.append(
            ft.Column(
                key="terminal-open-error-card",
                controls=[
                    ft.Text(open_error or retained.error, key="terminal-open-error"),
                    Button(
                        "Dismiss",
                        key="terminal-open-error-dismiss",
                        height=48,
                        on_click=lambda e: dismiss_open_error(),
                    ),
                ],
            )
        )


def build_status_overlays(
    *,
    app_state: Any,
    compact_terminal: Any,
    content: Any,
    dark: bool,
    go: Callable[[str], Any],
    handle_logout: Any,
    on_session_recovery: Callable[[], Any] | None,
    operations_height: Any,
    operations_open: Any,
    overlays: list[Any],
    pending_commands: Any,
    set_operations_height: Any,
    set_operations_open: Any,
    tier: Any,
    command_source: CommandStatusSource,
    viewport: Any,
) -> Any:
    """Revocation/command overlays and the final controls list."""
    if app_state.revoked:
        overlays = [
            content,
            ft.Column(
                key="connection-recovery-card",
                controls=[
                    ft.Text("Connection needs attention", key="connection-recovery-title"),
                    ft.Text(
                        "This page no longer has access. "
                        "Check the current connection to open a new page. "
                        "Revoked credentials require pairing with your Mac again.",
                        key="connection-recovery-explanation",
                    ),
                    Button(
                        "Check connection" if on_session_recovery else "Connect to your Mac",
                        key="connection-recovery-action",
                        height=MIN_TOUCH,
                        on_click=lambda e: (on_session_recovery or handle_logout)(),
                    ),
                ],
            ),
        ]

    # Keep the expanding overlay Stack mounted so terminal children retain native identity.
    # Place its scrim below the operations bar.
    if pending_commands is not None:
        content = ft.Column(
            expand=True,
            spacing=0,
            controls=[
                ft.Container(
                    key="terminal-operations-bar",
                    visible=not compact_terminal,
                    on_size_change=lambda e: set_operations_height(min(e.height, viewport[1]))
                    if 0 <= e.height < float("inf")
                    else None,
                    content=CommandRecovery(
                        pending_commands,
                        command_source,
                        app_state.granted_hosts or frozenset(),
                        go,
                        agent_names(app_state),
                    ),
                ),
                ft.Container(expand=True, content=content),
            ],
        )

    if (
        pending_commands is not None
        and operations_open
        and compact_terminal
        and not app_state.revoked
    ):
        overlays.append(
            ft.Column(
                key="terminal-operations-sheet",
                controls=[
                    Button(
                        "Close",
                        key="terminal-operations-close",
                        height=48,
                        on_click=lambda e: set_operations_open(False),
                    ),
                    CommandRecovery(
                        pending_commands,
                        command_source,
                        app_state.granted_hosts or frozenset(),
                        go,
                        agent_names(app_state),
                    ),
                ],
            )
        )

    controls: list[Any] = [
        ft.Container(
            key="workbench-shell",
            expand=True,
            disabled=len(overlays) > 1,
            content=content,
        )
    ]

    if len(overlays) > 1:
        viewport_width = viewport[0]
        card_width = min(SHEET_MAX_WIDTH, max(0, viewport_width - 32))
        sheet_top = (
            operations_height if pending_commands is not None and not compact_terminal else 0
        )
        nav_height = (76 if dark else 64) if tier == "compact" and not compact_terminal else 0
        controls.append(
            ft.Container(
                key="sheet-scrim",
                width=viewport_width,
                left=0,
                top=sheet_top,
                bottom=nav_height,
                alignment=ft.Alignment.CENTER,
                bgcolor=ft.Colors.with_opacity(SCRIM_OPACITY, SCRIM_COLOR),
                padding=16,
                content=CreationSheetCard(
                    overlays[1], card_width, max(0, viewport[1] - sheet_top - nav_height - 32)
                ),
            )
        )
    return controls
