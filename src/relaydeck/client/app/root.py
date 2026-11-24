"""RelayDeck declarative application root: the connected tree over `SyncController` state."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import flet as ft

from relaydeck.client.app.appearance import AppearanceBridge
from relaydeck.client.app.error_boundary import render_page, use_error_notice
from relaydeck.client.app.hooks.actions import (
    build_navigation_actions,
    build_open_terminal,
)
from relaydeck.client.app.hooks.appearance import use_appearance
from relaydeck.client.app.hooks.creation import (
    build_creation_actions,
    build_shell_origin_actions,
)
from relaydeck.client.app.hooks.inventory import use_inventory
from relaydeck.client.app.hooks.navigation import (
    use_navigation,
    use_route_effects,
    use_route_state,
)
from relaydeck.client.app.hooks.preferences import use_preferences
from relaydeck.client.app.hooks.retained import (
    use_retained_sessions,
    use_root_trace,
    use_terminal_effects,
)
from relaydeck.client.app.hooks.sheets import use_creation_sheets
from relaydeck.client.app.hooks.support import (
    RETAINED_PARK_OFFSET as RETAINED_PARK_OFFSET,  # re-exported for callers
)
from relaydeck.client.app.hooks.support import (
    SUBMIT_ERRORS as SUBMIT_ERRORS,  # re-exported for callers
)
from relaydeck.client.app.hooks.support import (
    new_command_id as new_command_id,  # re-exported for callers
)
from relaydeck.client.app.hooks.support import (
    selection_host as selection_host,  # re-exported for callers
)
from relaydeck.client.app.hooks.sync import (
    use_host_effects,
    use_sync_state,
    use_sync_subscription,
)
from relaydeck.client.app.hooks.typing_focus import (
    log_touch_platform,
    use_typing_focus,
    use_typing_split,
)
from relaydeck.client.app.hooks.views import (
    build_creation_sheets,
    build_inventory_shell,
    build_retained_views,
    build_status_overlays,
)
from relaydeck.client.app.navigation import RouteBridge
from relaydeck.client.app.resources import build_resources, use_sources
from relaydeck.client.app.sessions.factory import SessionFactory
from relaydeck.client.app.sessions.retained import RetainedSessions
from relaydeck.client.app.shell.navigation import AppShell as AppShell  # re-exported for callers
from relaydeck.client.app.state.accessibility import use_page_accessibility
from relaydeck.client.app.state.sync import SyncController
from relaydeck.client.app.workflows.ask import use_ask_runtime
from relaydeck.client.app.workflows.changes import use_changes_wiring
from relaydeck.client.features.terminal.connection_status import connection_status
from relaydeck.client.models.input.key_bar import width_class
from relaydeck.client.models.inventory_selectors import agent_names, connection_banner, diagnostics
from relaydeck.client.platform.transport.transport import HttpxGatewayTransport
from relaydeck.domain.models import ConnectionState
from relaydeck.version import __version__

logger = logging.getLogger("relaydeck.client.app")


@ft.component
def Root(
    controller: SyncController,
    transport: HttpxGatewayTransport,
    host_id: str,
    bridge: RouteBridge,
    go: Callable[[str], Any],
    session_factory: SessionFactory,
    account: str = "",
    scenarios: tuple[str, ...] = (),
    on_apply_scenario: Callable[[str], Any] | None = None,
    on_logout: Callable[[], Any] | None = None,
    on_session_recovery: Callable[[], Any] | None = None,
    dark: bool = True,
    appearance: AppearanceBridge | None = None,
    retained_sessions: RetainedSessions | None = None,
    interaction_guard: Callable[[], bool] | None = None,
    foreground: bool = True,
    notification_status: tuple[str, bool] | None = None,
    on_notifications_enable: Callable[[], Any] | None = None,
    pending_commands: Any = None,
    preferences: Any = None,
    touch: bool = False,
    platform_name: str = "",
    reduce_motion: bool | None = None,
) -> Any:
    error_notice = use_error_notice()
    accessibility = use_page_accessibility()
    reduce_motion = accessibility.motion_reduced if reduce_motion is None else reduce_motion
    sources = use_sources(transport, account)
    dark = use_appearance(appearance=appearance, dark=dark)

    (prefs, prefs_ref, prefs_saved, set_prefs) = use_preferences(preferences=preferences)

    (
        destination,
        initial,
        is_wide,
        keyboard_open,
        operations_height,
        operations_open,
        selection,
        set_destination,
        set_is_wide,
        set_keyboard_open,
        set_operations_height,
        set_operations_open,
        set_selection,
        set_viewport,
        viewport,
    ) = use_route_state(bridge=bridge)
    section, set_section = ft.use_state(initial.section)
    step, set_step = ft.use_state(initial.step)

    (
        cancel_typing_blur,
        on_composer_focus,
        set_typing_focus,
        typing_focus,
        exit_typing,
        field_focused,
        keep_typing_mode,
    ) = use_typing_focus(sticky=touch, viewport=viewport, route=(destination, selection))

    (active_host, app_state, app_state_box, set_active_host, set_app_state) = use_sync_state(
        host_id=host_id, initial=initial
    )

    (
        inventory_account,
        inventory_alive,
        inventory_filters,
        inventory_hosts,
        inventory_task,
        inventory_tick,
        more_inventory,
        pager,
        refresh_inventory,
        release_inventory,
        set_inventory_filters,
    ) = use_inventory(
        account=account,
        app_state=app_state,
        controller=controller,
        foreground=foreground,
        transport=sources.inventory,
    )

    (
        creation_drafts,
        launch_alive,
        launch_busy,
        launch_task,
        main_screen_revision,
        management_fields,
        set_main_screen_revision,
        set_sheet,
        set_sheet_error,
        set_sheet_status,
        set_submitting,
        sheet,
        sheet_error,
        sheet_status,
        shell_attempts,
        shell_origin,
        submitting,
    ) = use_creation_sheets()

    (
        _purge_revoked_stash,
        _revoked_now,
        close_terminal,
        expiry_task,
        open_error,
        opening_box,
        retained,
        retained_tick,
        session_box,
        set_open_error,
        set_retained_tick,
        set_terminal_open,
        terminal_host_box,
        terminal_kind_box,
        terminal_open,
    ) = use_retained_sessions(
        app_state_box=app_state_box,
        controller=controller,
        creation_drafts=creation_drafts,
        foreground=foreground,
        management_fields=management_fields,
        phone=width_class(viewport[0]) == "compact",
        retained_sessions=retained_sessions,
        shell_attempts=shell_attempts,
        shell_origin=shell_origin,
    )

    close_view_overlays = use_navigation(
        bridge=bridge,
        cancel_typing_blur=cancel_typing_blur,
        initial=initial,
        retained=retained,
        set_destination=set_destination,
        set_is_wide=set_is_wide,
        set_keyboard_open=set_keyboard_open,
        set_open_error=set_open_error,
        set_operations_open=set_operations_open,
        set_selection=set_selection,
        set_sheet=set_sheet,
        set_typing_focus=set_typing_focus,
        set_viewport=set_viewport,
        set_section=set_section,
        set_step=set_step,
    )

    sync_restart = use_sync_subscription(
        active_host=active_host,
        controller=controller,
        foreground=foreground,
        set_app_state=set_app_state,
    )

    use_host_effects(
        active_host=active_host,
        app_state=app_state,
        app_state_box=app_state_box,
        controller=controller,
        foreground=foreground,
        session_box=session_box,
        terminal_kind_box=terminal_kind_box,
        terminal_open=terminal_open,
        transport=transport,
    )

    use_route_effects(
        active_host=active_host,
        app_state=app_state,
        controller=controller,
        destination=destination,
        inventory_hosts=inventory_hosts,
        inventory_tick=inventory_tick,
        pager=pager,
        selection=selection,
        set_active_host=set_active_host,
    )

    use_terminal_effects(
        _purge_revoked_stash=_purge_revoked_stash,
        app_state=app_state,
        close_terminal=close_terminal,
        controller=controller,
        destination=destination,
        expiry_task=expiry_task,
        retained=retained,
        retained_tick=retained_tick,
        selection=selection,
        session_box=session_box,
        set_retained_tick=set_retained_tick,
        terminal_host_box=terminal_host_box,
        terminal_open=terminal_open,
    )

    open_terminal = build_open_terminal(
        _revoked_now=_revoked_now,
        close_terminal=close_terminal,
        controller=controller,
        destination=destination,
        foreground=foreground,
        interaction_guard=interaction_guard,
        launch_alive=launch_alive,
        opening_box=opening_box,
        prefs_ref=prefs_ref,
        retained=retained,
        selection=selection,
        session_box=session_box,
        session_factory=session_factory,
        set_open_error=set_open_error,
        set_terminal_open=set_terminal_open,
        terminal_host_box=terminal_host_box,
        terminal_kind_box=terminal_kind_box,
        transport=transport,
    )

    (
        go_destination,
        handle_logout,
        navigate,
        new_terminal_from_shell,
        select_inventory_agent,
        select_shell,
    ) = build_navigation_actions(
        account=account,
        app_state=app_state,
        bridge=bridge,
        close_terminal=close_terminal,
        close_view_overlays=close_view_overlays,
        controller=controller,
        destination=destination,
        go=go,
        inventory_account=inventory_account,
        inventory_alive=inventory_alive,
        inventory_task=inventory_task,
        on_logout=on_logout,
        open_error=open_error,
        open_terminal=open_terminal,
        operations_open=operations_open,
        pager=pager,
        release_inventory=release_inventory,
        retained=retained,
        selection=selection,
        set_active_host=set_active_host,
        set_destination=set_destination,
        set_main_screen_revision=set_main_screen_revision,
        set_selection=set_selection,
        set_sheet=set_sheet,
        set_sheet_error=set_sheet_error,
        set_sheet_status=set_sheet_status,
        sheet=sheet,
    )

    (cancel_creation, start_creation, submit_agent, submit_shell) = build_creation_actions(
        _revoked_now=_revoked_now,
        app_state=app_state,
        app_state_box=app_state_box,
        controller=controller,
        creation_drafts=creation_drafts,
        interaction_guard=interaction_guard,
        launch_alive=launch_alive,
        launch_busy=launch_busy,
        launch_task=launch_task,
        navigate=navigate,
        open_terminal=open_terminal,
        pending_commands=pending_commands,
        set_open_error=set_open_error,
        set_sheet=set_sheet,
        set_sheet_error=set_sheet_error,
        set_sheet_status=set_sheet_status,
        set_submitting=set_submitting,
        sheet=sheet,
        transport=transport,
    )

    (choose_shell, create_workspace_shell, open_shell_for_agent) = build_shell_origin_actions(
        _revoked_now=_revoked_now,
        controller=controller,
        launch_busy=launch_busy,
        navigate=navigate,
        open_terminal=open_terminal,
        set_open_error=set_open_error,
        set_sheet=set_sheet,
        set_sheet_status=set_sheet_status,
        shell_attempts=shell_attempts,
        shell_origin=shell_origin,
        start_creation=start_creation,
    )

    hosts = tuple((host.id, host.label or host.id) for host in app_state.inventory.hosts.values())

    grouped: dict[str, list[tuple[str, str]]] = {}

    for workspace in app_state.inventory.workspaces.values():
        grouped.setdefault(workspace.host_id, []).append(
            (workspace.id, workspace.label or workspace.id)
        )

    workspaces_arg = {key: tuple(value) for key, value in grouped.items()}

    conn = app_state.connection

    diag = diagnostics(
        app_state,
        now_ms=0,
        gateway_reachable=conn.state != ConnectionState.DISCONNECTED,
        socket_status=conn.state.value,
        app_version=__version__,
    )

    def back_from_terminal() -> None:
        close_terminal()
        navigate(destination)

    def retry_sync() -> None:
        controller.backoff.reset()
        if controller.state.connection.detail in ("sync_task_failed", "gap_stalled"):
            # Reuses the subscription's guards; metadata recovery never reattaches the terminal.
            restart = sync_restart.current
            if callable(restart):
                restart(explicit_retry=True)

    retained_foreground = retained.foreground

    active_entry = (
        retained_foreground
        if retained_foreground is not None
        and retained_foreground.key == terminal_open
        and retained_foreground.session is session_box.current
        else None
    )

    live_detail: Any = (
        ft.Container(key="retained-detail-placeholder") if active_entry is not None else None
    )

    retained_views: list[Any] = []

    permitted = {
        host.id: host.epoch
        for host in app_state.inventory.hosts.values()
        if not app_state.revoked
        and (app_state.granted_hosts is None or host.id in app_state.granted_hosts)
    }

    tier = width_class(viewport[0])

    phone = tier != "expanded"

    typing_split = use_typing_split(
        platform=platform_name,
        touch=touch,
        viewport=viewport,
        focused=typing_focus,
        entry=active_entry,
    )

    compact_terminal = tier == "compact" and active_entry is not None

    banner_model = connection_banner(app_state)

    connection_live, connection_text = connection_status(
        state_value=app_state.connection.state.value,
        stale=app_state.stale,
        banner_text=banner_model.text if banner_model is not None else None,
        active_entry=active_entry,
    )

    font_size = prefs.effective_font_size(phone)

    live_shells = frozenset(
        entry.key
        for entry in retained.entries()
        if entry.kind == "shell" and getattr(entry.session, "phase", "") == "live"
    )

    ask_runtime = use_ask_runtime(
        account=account,
        transport=transport,
        state=app_state,
        read_state=lambda: controller.state,
        read_route=lambda: bridge.state,
        destination=destination,
        selection=selection,
        section=section,
        entry=active_entry,
        navigate=navigate,
        foreground=foreground,
        open_terminal=select_inventory_agent,
    )
    build_retained_views(
        reduce_motion=reduce_motion,
        context_strip=ask_runtime.strip(is_wide),
        active_entry=active_entry,
        app_state=app_state,
        back_from_terminal=back_from_terminal,
        connection_live=connection_live,
        connection_text=connection_text,
        dark=dark,
        font_size=font_size,
        handle_logout=handle_logout,
        keyboard_open=keyboard_open,
        new_terminal_from_shell=new_terminal_from_shell,
        on_composer_focus=on_composer_focus,
        on_typing_exit=exit_typing,
        field_focused=field_focused,
        on_keep_typing=keep_typing_mode,
        on_session_recovery=on_session_recovery,
        open_shell_for_agent=open_shell_for_agent,
        pending_commands=pending_commands,
        permitted=permitted,
        phone=phone,
        prefs=prefs,
        retained=retained,
        retained_views=retained_views,
        retry_sync=retry_sync,
        select_inventory_agent=select_inventory_agent,
        set_operations_open=set_operations_open,
        tier=tier,
        touch=touch,
        typing_split=typing_split,
        viewport=viewport,
    )

    use_root_trace(
        retained=retained,
        controller=controller,
        active=active_entry is not None,
        wide=is_wide,
        dependencies=[retained_tick, terminal_open, is_wide, destination],
    )

    changes = use_changes_wiring(
        ask_runtime=ask_runtime,
        account=account,
        connected=app_state.connection.state == ConnectionState.LIVE,
        transport=transport,
        pager=pager,
        destination=destination,
        selection=selection,
        section=section,
        step=step,
        foreground=foreground,
        wide=is_wide,
        phone=width_class(viewport[0]) == "compact",
        navigate=navigate,
        names=agent_names(app_state),
    )
    shell = build_inventory_shell(
        reduce_motion=reduce_motion,
        sources=sources,
        account=account,
        active_entry=active_entry,
        changes=changes,
        app_state=app_state,
        compact_terminal=compact_terminal,
        controller=controller,
        dark=dark,
        destination=destination,
        diag=diag,
        foreground=foreground,
        go_destination=go_destination,
        handle_logout=handle_logout,
        inventory_hosts=inventory_hosts,
        is_wide=is_wide,
        live_detail=live_detail,
        live_shells=live_shells,
        main_screen_revision=main_screen_revision,
        management_fields=management_fields,
        more_inventory=more_inventory,
        navigate=navigate,
        notification_status=notification_status,
        on_apply_scenario=on_apply_scenario,
        on_notifications_enable=on_notifications_enable,
        on_session_recovery=on_session_recovery,
        open_shell_for_agent=open_shell_for_agent,
        open_terminal=open_terminal,
        pager=pager,
        prefs=prefs,
        prefs_saved=prefs_saved,
        refresh_inventory=refresh_inventory,
        retained_views=retained_views,
        retry_sync=retry_sync,
        scenarios=scenarios,
        select_inventory_agent=select_inventory_agent,
        select_shell=select_shell,
        selection=selection,
        set_inventory_filters=set_inventory_filters,
        set_prefs=set_prefs,
        set_sheet=set_sheet,
        set_sheet_error=set_sheet_error,
        set_sheet_status=set_sheet_status,
        transport=transport,
        typing_split=typing_split,
        viewport=viewport,
    )

    # Inventory stays mounted beside the live detail; its detail slot also owns the phone layout.
    content: Any = shell

    overlays: list[Any] = [content]

    build_creation_sheets(
        active_host=active_host,
        app_state=app_state,
        cancel_creation=cancel_creation,
        choose_shell=choose_shell,
        create_workspace_shell=create_workspace_shell,
        creation_drafts=creation_drafts,
        dark=dark,
        hosts=hosts,
        inventory_filters=inventory_filters,
        open_error=open_error,
        overlays=overlays,
        retained=retained,
        set_open_error=set_open_error,
        set_sheet=set_sheet,
        sheet=sheet,
        sheet_error=sheet_error,
        sheet_status=sheet_status,
        shell_origin=shell_origin,
        submit_agent=submit_agent,
        submit_shell=submit_shell,
        submitting=submitting,
        workspaces_arg=workspaces_arg,
    )

    controls = build_status_overlays(
        app_state=app_state,
        compact_terminal=compact_terminal,
        content=content,
        dark=dark,
        go=go,
        handle_logout=handle_logout,
        on_session_recovery=on_session_recovery,
        operations_height=operations_height,
        operations_open=operations_open,
        overlays=overlays,
        pending_commands=pending_commands,
        set_operations_height=set_operations_height,
        set_operations_open=set_operations_open,
        tier=tier,
        command_source=sources.command_recovery,
        viewport=viewport,
    )

    if error_notice is not None:
        controls.append(error_notice)
    return ft.Stack(controls=controls, expand=True)


def is_touch_platform(platform: Any) -> bool:
    """pointer-coarse platforms get touch scroll behavior."""
    return platform in (ft.PagePlatform.IOS, ft.PagePlatform.ANDROID)


def run_app(
    page: ft.Page,
    *,
    base_url: str,
    get_token: Callable[[], Any],
    host_id: str,
    account: str = "",
    device_id: str = "",
    on_logout: Callable[[], Any] | None = None,
    on_session_recovery: Callable[[], Any] | None = None,
    refresh_identity: Callable[[], Any] | None = None,
    recovery_current: Callable[[], bool] = lambda: True,
    command_store: Any = None,
    command_origin: str | None = None,
    draft_store: Any = None,
) -> Any:
    """Bridge page events into Root and render (used by all entrypoints)."""
    resources = build_resources(
        page,
        base_url=base_url,
        get_token=get_token,
        host_id=host_id,
        account=account,
        device_id=device_id,
        on_logout=on_logout,
        on_session_recovery=on_session_recovery,
        refresh_identity=refresh_identity,
        recovery_current=recovery_current,
        command_store=command_store,
        command_current=recovery_current,
        command_origin=command_origin,
        draft_store=draft_store,
        track_keyboard=True,
    )
    # Touch platforms show scroll controls at every width; the platform is fixed per session.
    resources.props["touch"] = is_touch_platform(page.platform)
    resources.props["platform_name"] = log_touch_platform(page.platform, resources.props["touch"])

    render_page(page, Root, **resources.props)

    async def recover() -> None:
        try:
            me = await resources.transport.me()
            if me["account"] != account or me["device_id"] != device_id:
                return
            hosts = await resources.transport.hosts()
            resources.controller.apply_grants(hosts["hosts"])
            await resources.commands.reconcile(
                resources.transport.command_status,
                resources.controller.state.granted_hosts or frozenset(),
            )
        except Exception as exc:  # noqa: BLE001 - the type name only is logged
            logger.warning("pending_recovery_failed type=%s", type(exc).__name__)
            resources.commands.error = (
                "Pending command recovery unavailable. Check status before another mutation."
            )
            resources.commands.changed()

    task = page.run_task(recover)
    resources.tasks.add(task)
    task.add_done_callback(resources.tasks.discard)
    return resources
