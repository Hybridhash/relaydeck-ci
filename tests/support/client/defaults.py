"""Client defaults applied only to the tests that use their owners."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

TESTS = Path(__file__).resolve().parents[2]

WATCH_FIRST_MODULES = frozenset(
    {
        "architecture/test_contract_paths.py",
        "architecture/test_native_entry_seams.py",
        "integration/auth/test_logout_wiring.py",
        "integration/auth/test_oidc_login_authorization.py",
        "integration/client/app/native/test_packaged_entrypoint_pairing.py",
        "integration/companion/app/hosted_web/test_browser_session_recovery.py",
        "integration/companion/app/hosted_web/test_console_assets.py",
        "integration/companion/app/hosted_web/test_cookie_transition.py",
        "integration/companion/app/hosted_web/test_flet_auth_boundary.py",
        "integration/companion/app/hosted_web/test_flet_reconnect_continuity.py",
        "integration/companion/app/hosted_web/test_flet_websocket_registration.py",
        "integration/companion/app/hosted_web/test_web_cookie_registration.py",
        "integration/companion/app/hosted_web/test_web_session_30d.py",
        "integration/companion/app/test_companion_lifecycle.py",
        "integration/companion/app/test_herdr_reconnect.py",
        "integration/companion/app/test_shutdown_bounds.py",
        "integration/companion/test_setup_fresh_home.py",
        "integration/creation/test_launch_journals.py",
        "integration/pairing/test_pairing_cli_and_asgi.py",
        "integration/sync/test_serving_and_connector_transport.py",
        "integration/test_durable_signins.py",
        "integration/test_terminal_recovery_checkpoint.py",
        "integration/test_terminal_ui_reconnect.py",
        "integration/test_usage_endpoint.py",
        "integration/test_usage_lifecycle.py",
        "security/test_frame_bounds.py",
        "security/test_origin_csrf.py",
        "ui/client/app/native/test_packaged_entrypoint.py",
        "ui/client/app/snapshots/test_console_snapshots.py",
        "ui/client/app/snapshots/test_sheet_snapshots.py",
        "ui/client/app/test_agent_rename.py",
        "ui/client/app/test_ask_page_lifetime.py",
        "ui/client/app/test_ask_root.py",
        "ui/client/app/test_busy_prompt_recovery.py",
        "ui/client/app/test_commit_shell_back.py",
        "ui/client/app/test_commit_shell_width.py",
        "ui/client/app/test_compact_header.py",
        "ui/client/app/test_composer_shortcut.py",
        "ui/client/app/test_console_compact.py",
        "ui/client/app/test_console_integration.py",
        "ui/client/app/test_console_stable_order.py",
        "ui/client/app/test_console_summary_window.py",
        "ui/client/app/test_create_fixes.py",
        "ui/client/app/test_creation_dialogs_keep_shell_mounted.py",
        "ui/client/app/test_diagnostics_disclosure.py",
        "ui/client/app/test_draft_editing_lifetime.py",
        "ui/client/app/test_draft_native_boundaries.py",
        "ui/client/app/test_foundation_integration.py",
        "ui/client/app/test_frozen_transport.py",
        "ui/client/app/test_host_local_id_twins.py",
        "ui/client/app/test_host_switching.py",
        "ui/client/app/test_in_app_logout.py",
        "ui/client/app/test_instant_open.py",
        "ui/client/app/test_inventory_paging.py",
        "ui/client/app/test_ios_compose_selection.py",
        "ui/client/app/test_live_resource_and_input.py",
        "ui/client/app/test_management_root.py",
        "ui/client/app/test_metadata_task_supervision.py",
        "ui/client/app/test_navigation.py",
        "ui/client/app/test_open_terminal_from_root.py",
        "ui/client/app/test_open_timeline.py",
        "ui/client/app/test_parked_reconnect.py",
        "ui/client/app/test_pending_creation.py",
        "ui/client/app/test_phone_carrier_and_compose_focus.py",
        "ui/client/app/test_phone_composer_dismiss.py",
        "ui/client/app/test_phone_focus_handoff.py",
        "ui/client/app/test_pin_agents.py",
        "ui/client/app/test_prompt_dismiss_ui.py",
        "ui/client/app/test_rename_publication.py",
        "ui/client/app/test_rename_redesign.py",
        "ui/client/app/test_reset_loop.py",
        "ui/client/app/test_retained_root_identity.py",
        "ui/client/app/test_retry_banner_delay.py",
        "ui/client/app/test_return_size_release.py",
        "ui/client/app/test_root_lifecycle_quiet_output.py",
        "ui/client/app/test_root_names_budget.py",
        "ui/client/app/test_same_identity_reconnect.py",
        "ui/client/app/test_sheet_layout.py",
        "ui/client/app/test_shell_chooser.py",
        "ui/client/app/test_source_replacement.py",
        "ui/client/app/test_space_preferences.py",
        "ui/client/app/test_spaces_filter.py",
        "ui/client/app/test_split_full_path_identity.py",
        "ui/client/app/test_split_sticky.py",
        "ui/client/app/test_split_typing.py",
        "ui/client/app/test_status_dot.py",
        "ui/client/app/test_switch_history.py",
        "ui/client/app/test_tab_returns_to_list.py",
        "ui/client/app/test_warm_idle_deadline.py",
        "ui/client/app/test_warm_resize.py",
        "ui/client/app/test_warm_resize_native.py",
        "ui/client/app/test_warm_transition.py",
        "ui/client/app/test_web_visibility_wiring.py",
        "ui/client/design_system/snapshots/test_shared_inputs.py",
        "ui/client/design_system/test_accessibility.py",
        "ui/client/features/rename/test_rename_source_replacement.py",
        "ui/client/screens/changes/snapshots/test_changes_snapshots.py",
        "ui/client/screens/changes/test_ask_bookmark.py",
        "ui/client/screens/changes/test_ask_sheet.py",
        "ui/client/screens/changes/test_changes_followups.py",
        "ui/client/screens/changes/test_changes_route.py",
        "ui/client/screens/changes/test_changes_screen.py",
        "ui/client/screens/changes/test_commit_back_order.py",
        "ui/client/screens/changes/test_commit_file_pane.py",
        "ui/client/screens/changes/test_commit_navigation.py",
        "ui/client/screens/changes/test_commit_recovery.py",
        "ui/client/screens/changes/test_commit_reopen.py",
        "ui/client/screens/changes/test_commit_view.py",
        "ui/client/screens/changes/test_diff_view.py",
        "ui/client/screens/changes/test_fold_siblings.py",
        "ui/client/screens/changes/test_graph.py",
        "ui/client/screens/changes/test_graph_design.py",
        "ui/client/screens/changes/test_graph_refresh_scroll.py",
        "ui/client/screens/changes/test_graph_virtual.py",
        "ui/client/screens/changes/test_lazy_list.py",
        "ui/client/screens/changes/test_navigation_model.py",
        "ui/client/screens/changes/test_owner_layout.py",
        "ui/client/screens/changes/test_review_keys.py",
        "ui/client/screens/changes/test_tablet_hunk.py",
        "ui/client/screens/pairing/test_panel_unmount.py",
        "ui/client/screens/settings/test_haptics_ui.py",
        "ui/client/screens/settings/test_update_notice.py",
        "ui/client/screens/snapshots/test_settings_pairing_snapshots.py",
        "ui/client/screens/terminal/test_agent_switcher.py",
        "ui/client/screens/terminal/test_compose_picture_visibility.py",
        "ui/client/screens/test_ipad_layout.py",
        "ui/client/screens/test_mobile_terminal_layout.py",
        "ui/client/screens/test_phone_terminal_ancestry.py",
        "ui/client/widgets/hosts/test_hosts_connect_design.py",
        "ui/client/widgets/sessions/test_layout_rules.py",
        "ui/client/widgets/terminal/test_geometry_identity_and_compact_affordances.py",
        "ui/test_terminal_window_design.py",
        "unit/client/app/test_client_hooks_layout.py",
        "unit/client/app/test_declarative_smoke.py",
        "unit/client/app/test_page_error_boundary.py",
        "unit/client/app/workflows/test_ask_runtime.py",
        "unit/client/models/test_pin_agents.py",
        "unit/companion/app/hosted_web/test_companion_log_redaction.py",
        "unit/companion/app/hosted_web/test_host_session_cookies.py",
        "unit/companion/app/hosted_web/test_hosted_security_headers.py",
        "unit/companion/app/hosted_web/test_ios_picture_logs.py",
        "unit/companion/app/hosted_web/test_pairing_complete_fallback.py",
        "unit/companion/app/hosted_web/test_policy_rejection_recovery.py",
        "unit/companion/app/hosted_web/test_ws_guard_recovery.py",
        "unit/companion/app/test_close_bound.py",
        "unit/companion/app/test_companion_changes.py",
        "unit/companion/app/test_companion_deadlines.py",
        "unit/companion/app/test_companion_heartbeat_cost.py",
        "unit/companion/app/test_companion_route_shutdown.py",
        "unit/companion/app/test_companion_update_wiring.py",
        "unit/companion/app/test_herdr_tool_unavailable.py",
        "unit/companion/app/test_usage_history_handoff.py",
        "unit/companion/app/test_usage_limits_provider_wiring.py",
        "unit/companion/app/test_usage_limits_runtime.py",
        "unit/companion/inspection/test_setup_check.py",
        "unit/companion/inspection/test_setup_detect.py",
        "unit/companion/inspection/test_setup_prereqs.py",
        "unit/companion/updates/test_update_commands.py",
        "unit/companion/updates/test_update_instructions.py",
        "unit/gateway/http/test_gateway_auth_challenge.py",
        "unit/gateway/http/test_gateway_error_envelopes.py",
        "unit/gateway/http/test_gateway_metrics.py",
        "unit/gateway/http/test_request_admission.py",
        "unit/pictures/test_presenter_cancellation_races.py",
        "unit/test_client_foundations.py",
        "unit/test_companion_web_default.py",
        "unit/test_entry_task_owners.py",
        "unit/test_lease_diagnostics.py",
        "unit/test_limiter_retirement.py",
        "unit/test_loop_offload.py",
        "unit/test_notify_prefs_web.py",
        "unit/test_relaydeck_cli.py",
        "unit/test_setup_cli_and_service.py",
        "unit/test_setup_fixes.py",
        "unit/test_setup_git_and_web_archive.py",
        "unit/test_setup_sources.py",
        "unit/test_switcher_rows.py",
        "unit/test_tool_path.py",
        "unit/test_usage_coverage_prune.py",
        "unit/test_version.py",
        "unit/test_visibility_activation.py",
        "unit/test_web_app_docs.py",
        "unit/test_web_policy_handoff.py",
        "unit/test_webpush_cli.py",
        "unit/usage/hooks/test_hook_auto_upgrade.py",
    }
)

OPEN_HOLD_MODULES = WATCH_FIRST_MODULES | frozenset(
    {
        "integration/test_live_agent_status.py",
        "ui/client/app/test_split_done_keyboard.py",
        "ui/client/app/test_split_keep_typing_cap.py",
        "ui/client/design_system/snapshots/test_shared_spinners.py",
        "ui/client/design_system/test_page_accessibility.py",
        "ui/client/platform/flet/test_direct_invoke_orphan.py",
        "ui/client/screens/snapshots/test_terminal_snapshots.py",
        "ui/client/screens/terminal/test_open_hold.py",
        "ui/client/screens/terminal/test_open_layout_reserve.py",
        "ui/client/screens/terminal/test_phone_dock_spacer.py",
        "ui/client/screens/terminal/test_resize_coalesce.py",
        "ui/client/screens/terminal/test_resize_settle.py",
        "ui/client/screens/terminal/test_split_keyboard_inset.py",
        "ui/client/screens/terminal/test_split_keys_dock.py",
        "ui/client/screens/terminal/test_split_tools.py",
        "ui/client/screens/terminal/test_touch_keyboard_button.py",
        "ui/client/screens/terminal/test_working_label.py",
        "ui/client/screens/test_async_dispatch_and_rerender.py",
        "ui/client/screens/test_auto_takeover_layout.py",
        "ui/client/screens/test_clipboard_fallback_and_pickers.py",
        "ui/client/screens/test_compact_scroll_latest.py",
        "ui/client/screens/test_desktop_jump_to_latest.py",
        "ui/client/screens/test_ios_pictures.py",
        "ui/client/screens/test_latch_text.py",
        "ui/client/screens/test_mode_report.py",
        "ui/client/screens/test_picture_attachments.py",
        "ui/client/screens/test_renderer_binding.py",
        "ui/client/screens/test_scroll_overlay_hit.py",
        "ui/client/screens/test_scroll_routing_ui.py",
        "ui/client/screens/test_select_text_snapshot.py",
        "ui/client/screens/test_shell_terminal.py",
        "ui/client/screens/test_terminal_stream_cost.py",
        "ui/client/screens/test_wide_select_area.py",
        "ui/client/widgets/terminal/test_compact_paste_fallback.py",
        "ui/client/widgets/terminal/test_direct_continuation.py",
        "ui/client/widgets/terminal/test_direct_dictation.py",
        "ui/client/widgets/terminal/test_direct_focus_cancellation.py",
        "ui/client/widgets/terminal/test_direct_parked_editor.py",
        "ui/client/widgets/terminal/test_direct_reset_storm.py",
        "ui/client/widgets/terminal/test_direct_safety.py",
        "ui/client/widgets/terminal/test_direct_tap_burst.py",
        "ui/client/widgets/terminal/test_direct_tools_focus.py",
        "ui/client/widgets/terminal/test_direct_typing.py",
        "ui/client/widgets/terminal/test_keyboard_hit_region.py",
        "ui/client/widgets/terminal/test_keyboard_toggle.py",
        "ui/client/widgets/terminal/test_phone_select_sheet.py",
        "ui/client/widgets/terminal/test_tools_popover.py",
        "ui/terminal/test_browser_paste.py",
        "ui/terminal/test_composer_auth_loss_fencing.py",
        "ui/terminal/test_paste_entry.py",
        "ui/test_notice_dismiss.py",
        "ui/test_swipe_native.py",
        "unit/client/app/test_agent_space_tabs.py",
        "unit/client/features/terminal/test_native_text.py",
    }
)

POLLING_MODULES = frozenset(
    {
        "ui/client/app/snapshots/test_console_snapshots.py",
        "ui/client/app/snapshots/test_sheet_snapshots.py",
        "ui/client/app/test_ask_page_lifetime.py",
        "ui/client/app/test_ask_root.py",
        "ui/client/app/test_commit_shell_back.py",
        "ui/client/app/test_commit_shell_width.py",
        "ui/client/app/test_compact_header.py",
        "ui/client/design_system/snapshots/test_catalog_snapshots.py",
        "ui/client/design_system/snapshots/test_shared_badges.py",
        "ui/client/design_system/snapshots/test_shared_buttons.py",
        "ui/client/design_system/snapshots/test_shared_dialogs.py",
        "ui/client/design_system/snapshots/test_shared_headers.py",
        "ui/client/design_system/snapshots/test_shared_inputs.py",
        "ui/client/design_system/snapshots/test_shared_notices.py",
        "ui/client/design_system/snapshots/test_shared_overlays.py",
        "ui/client/design_system/snapshots/test_shared_spinners.py",
        "ui/client/design_system/snapshots/test_shared_stats.py",
        "ui/client/screens/changes/snapshots/test_changes_snapshots.py",
        "ui/client/screens/changes/test_ask_bookmark.py",
        "ui/client/screens/changes/test_ask_sheet.py",
        "ui/client/screens/changes/test_changes_followups.py",
        "ui/client/screens/changes/test_changes_route.py",
        "ui/client/screens/changes/test_changes_screen.py",
        "ui/client/screens/changes/test_commit_back_order.py",
        "ui/client/screens/changes/test_commit_file_pane.py",
        "ui/client/screens/changes/test_commit_navigation.py",
        "ui/client/screens/changes/test_commit_recovery.py",
        "ui/client/screens/changes/test_commit_reopen.py",
        "ui/client/screens/changes/test_commit_view.py",
        "ui/client/screens/changes/test_diff_view.py",
        "ui/client/screens/changes/test_fold_siblings.py",
        "ui/client/screens/changes/test_graph.py",
        "ui/client/screens/changes/test_graph_design.py",
        "ui/client/screens/changes/test_graph_refresh_scroll.py",
        "ui/client/screens/changes/test_graph_virtual.py",
        "ui/client/screens/changes/test_lazy_list.py",
        "ui/client/screens/changes/test_navigation_model.py",
        "ui/client/screens/changes/test_owner_layout.py",
        "ui/client/screens/changes/test_review_keys.py",
        "ui/client/screens/changes/test_tablet_hunk.py",
        "ui/client/screens/snapshots/test_settings_pairing_snapshots.py",
        "ui/client/screens/snapshots/test_terminal_snapshots.py",
        "ui/client/screens/usage/snapshots/test_usage_snapshots.py",
        "ui/client/widgets/sessions/test_layout_rules.py",
        "ui/snapshots/test_snapshot_helper.py",
    }
)

OVERLAY_MODULES = frozenset(
    {
        "ui/client/app/test_agent_rename.py",
        "ui/client/app/test_rename_redesign.py",
    }
)


def _applies(request: pytest.FixtureRequest, paths: frozenset[str]) -> bool:
    return request.node.path.relative_to(TESTS).as_posix() in paths


@pytest.fixture(autouse=True)
def _watch_first_unless_opted_in(request: pytest.FixtureRequest, monkeypatch: Any) -> None:
    if not _applies(request, WATCH_FIRST_MODULES):
        return
    if request.node.get_closest_marker("auto_takeover") is not None:
        return

    async def watch_first(session: Any) -> None:
        return None

    monkeypatch.setattr("relaydeck.client.app.hooks.actions._auto_take_control", watch_first)


@pytest.fixture(autouse=True)
def _open_hold_released_unless_opted_in(request: pytest.FixtureRequest, monkeypatch: Any) -> None:
    if not _applies(request, OPEN_HOLD_MODULES):
        return
    if request.node.get_closest_marker("open_hold") is not None:
        return
    monkeypatch.setattr("relaydeck.client.screens.terminal.agent_hooks.OPEN_HOLD_S", 0.0)


@pytest.fixture(autouse=True)
def fast_polling(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if not _applies(request, POLLING_MODULES):
        return
    import relaydeck.client.features.changes.refresh as refresh
    import relaydeck.client.screens.changes.parts.diff_view as diff_view

    monkeypatch.setattr(refresh, "REVISION_WORKING", 0.05)
    monkeypatch.setattr(refresh, "REVISION_IDLE", 0.05)
    monkeypatch.setattr(refresh, "REVISION_MIN_GAP", 0.01)
    monkeypatch.setattr(diff_view, "SCROLL_SETTLE_S", 0.0)


@pytest.fixture(autouse=True)
def client_takes_scroll_requests(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not _applies(request, POLLING_MODULES):
        return
    from relaydeck.client.design_system.lists import virtual_list

    async def answered(listing: object, offset: float) -> bool:
        return True

    monkeypatch.setattr(virtual_list, "scroll_list", answered)


@pytest.fixture(autouse=True)
def no_open_overlay_is_removed(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    if not _applies(request, OVERLAY_MODULES):
        yield
        return
    import flet as ft
    from flet.messaging.session import Session

    overlays = (ft.BottomSheet, ft.AlertDialog, ft.SnackBar)
    original = Session._Session__get_update_control_patch  # type: ignore[attr-defined]
    violations: list[str] = []

    def watch(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = original(self, *args, **kwargs)
        kept = {control._i for control in result[1]}
        for removed in result[2]:
            if isinstance(removed, overlays) and removed.open and removed._i not in kept:
                violations.append(f"{type(removed).__name__} {removed.key} removed while open")
        return result

    monkeypatch.setattr(Session, "_Session__get_update_control_patch", watch)
    yield
    assert not violations, violations
