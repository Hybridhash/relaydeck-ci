"""The gateway's registered routes, pinned in registration order, so moving a route group out of
`create_app` cannot silently add, drop, rename or reorder an endpoint."""

from __future__ import annotations

from typing import cast

from starlette.routing import Route, WebSocketRoute

from relaydeck.devtools.fakes.gateway import FakeBackend
from relaydeck.gateway.app.bootstrap import create_app
from relaydeck.gateway.app.state import GatewayState

ROUTES: list[tuple[str, str, tuple[str, ...], str]] = [
    ("http", "/v1/pictures", ("POST",), "begin"),
    ("http", "/v1/pictures/{identifier}/content", ("PUT",), "upload"),
    ("http", "/v1/pictures/{identifier}", ("GET",), "result"),
    ("http", "/v1/pictures/{identifier}", ("DELETE",), "cancel"),
    ("http", "/healthz", ("GET",), "healthz"),
    ("http", "/readyz", ("GET",), "readyz"),
    ("http", "/v1/pairing/requests", ("POST",), "request_pairing"),
    ("http", "/v1/pairing/{request_id}/status", ("POST",), "pairing_status"),
    ("http", "/v1/pairing/{request_id}/redeem", ("POST",), "redeem_pairing"),
    ("http", "/v1/authorize", ("GET",), "authorize"),
    ("http", "/v1/identity/callback", ("GET",), "identity_callback"),
    ("http", "/v1/token", ("POST",), "token_exchange"),
    ("http", "/v1/logout", ("POST",), "logout_session"),
    ("http", "/v1/devices", ("POST",), "register_device"),
    ("http", "/v1/devices/{device_id}/rotate", ("POST",), "rotate_device_token"),
    ("http", "/v1/devices", ("GET",), "list_devices"),
    ("http", "/v1/devices/{device_id}", ("DELETE",), "delete_device"),
    ("http", "/v1/deep-links/resolve", ("POST",), "resolve_deep_link"),
    ("http", "/v1/metrics", ("GET",), "server_metrics"),
    ("http", "/v1/enrollments", ("POST",), "create_enrollment"),
    ("http", "/v1/enrollments/{enrollment_id}", ("GET",), "enrollment_status"),
    ("http", "/v1/enrollments/{enrollment_id}/redeem", ("POST",), "redeem_enrollment"),
    ("websocket", "/v1/connectors", (), "connector_socket"),
    ("http", "/v1/hosts/{host_id}/usage/limits/refresh", ("POST",), "refresh_limits"),
    ("http", "/v1/hosts/{host_id}/usage", ("GET",), "get_usage"),
    ("http", "/v1/me", ("GET",), "get_me"),
    ("http", "/v1/hosts", ("GET",), "list_hosts"),
    ("http", "/v1/hosts/{host_id}", ("DELETE",), "delete_host"),
    ("http", "/v1/hosts/{host_id}/snapshot", ("GET",), "get_snapshot"),
    ("http", "/v1/hosts/{host_id}/agents", ("GET",), "list_agents"),
    ("http", "/v1/hosts/{host_id}/agents/{agent_id}/read", ("POST",), "mark_agent_read"),
    ("http", "/v1/events", ("GET",), "get_events"),
    ("http", "/v1/hosts/{host_id}/commands", ("POST",), "post_command"),
    ("http", "/v1/hosts/{host_id}/commands/{command_id}", ("GET",), "get_command"),
    ("http", "/v1/hosts/{host_id}/commands/{command_id}/resolve", ("POST",), "resolve_prompt"),
    ("http", "/v1/hosts/{host_id}/git/summaries", ("POST",), "git_summaries"),
    ("http", "/v1/hosts/{host_id}/git/worktrees/{worktree_id}/changes", ("GET",), "git_changes"),
    (
        "http",
        "/v1/hosts/{host_id}/git/worktrees/{worktree_id}/files/{file_id}/diff",
        ("GET",),
        "git_file_diff",
    ),
    ("http", "/v1/hosts/{host_id}/git/worktrees/{worktree_id}/commits", ("GET",), "git_commits"),
    ("http", "/v1/hosts/{host_id}/git/worktrees/{worktree_id}/graph", ("GET",), "git_graph"),
    (
        "http",
        "/v1/hosts/{host_id}/git/worktrees/{worktree_id}/commits/{commit_id}/changes",
        ("GET",),
        "git_commit_changes",
    ),
    ("http", "/v1/hosts/{host_id}/git/worktrees/{worktree_id}/bases", ("GET",), "git_bases"),
    ("http", "/v1/hosts/{host_id}/git/worktrees/{worktree_id}/base", ("PUT",), "git_set_base"),
    (
        "http",
        "/v1/hosts/{host_id}/terminals/{terminal_id}/attachments",
        ("POST",),
        "create_attachment",
    ),
    ("http", "/v1/hosts/{host_id}/terminals/{terminal_id}/control", ("GET",), "get_control_holder"),
    ("http", "/v1/terminal-attachments/{attachment_id}/leases", ("POST",), "create_lease"),
    ("http", "/v1/terminal-attachments/{attachment_id}", ("DELETE",), "delete_attachment"),
    ("http", "/v1/terminal-leases/{lease_id}", ("DELETE",), "delete_lease"),
    ("http", "/v1/terminal-leases/{lease_id}/renew", ("POST",), "renew_lease"),
    ("http", "/v1/terminal-tickets", ("POST",), "issue_terminal_ticket"),
    ("websocket", "/v1/terminal/{lease_id}", (), "terminal_socket"),
]


def registered(updates: object = None) -> list[tuple[str, str, tuple[str, ...], str]]:
    state = GatewayState(
        backend=FakeBackend(now_ms=lambda: 1_000_000),
        cursor_secret=b"x" * 32,
        now_ms=lambda: 1_000_000,
    )
    state.updates = updates
    return [
        (
            "websocket" if "WebSocket" in type(route).__name__ else "http",
            cast(Route | WebSocketRoute, route).path,
            tuple(sorted(getattr(route, "methods", None) or ())),
            cast(Route | WebSocketRoute, route).name,
        )
        for route in create_app(state).routes
    ]


def test_gateway_route_table_is_unchanged() -> None:
    assert registered() == ROUTES


def test_the_companion_only_updates_route_sits_right_after_the_pictures() -> None:
    """Registered through a companion-only state attribute: absent on a bare gateway."""
    updates_route = ("http", "/v1/updates", ("GET",), "get_updates")
    assert registered(updates=object()) == [*ROUTES[:4], updates_route, *ROUTES[4:]]
