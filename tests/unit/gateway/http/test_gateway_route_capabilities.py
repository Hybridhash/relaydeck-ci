"""Router views expose only their owned capabilities and observe live replacements."""

from __future__ import annotations

import dataclasses

import pytest

from relaydeck.devtools.fakes.gateway import FakeBackend
from relaydeck.gateway.app.capabilities import build_capabilities
from relaydeck.gateway.app.state import GatewayState
from relaydeck.gateway.http.context import RouteContext
from relaydeck.gateway.http.ports import IdentityCapabilities


def test_context_cannot_carry_application_state() -> None:
    names = {field.name for field in dataclasses.fields(RouteContext)}
    assert "state" not in names
    assert names == {
        "routes",
        "register_limiter",
        "current_session",
        "session_allowed",
        "device_allowed",
        "publish_epochs",
        "emit_command_update",
        "host_guard",
        "command_io",
        "revoke_host",
        "release_lease",
    }


def test_live_views_keep_replacements_and_mutable_sign_in_state() -> None:
    state = GatewayState(backend=FakeBackend(now_ms=lambda: 1))
    try:
        views = build_capabilities(state)
        original = views.agents.backend
        replacement = FakeBackend(now_ms=lambda: 2)
        state.backend = replacement
        state.now_ms = lambda: 37
        state.cursor_secret = b"replacement"
        assert views.agents.backend is replacement
        assert views.agents.backend is not original
        assert views.git_access.now_ms() == 37
        assert views.git_access.cursor_secret == b"replacement"
        assert views.git_access.git is None
        # No cast or captured owner is allowed between the view and the mutable slot.
        marker = object()
        state.__dict__["git"] = marker
        assert views.git_access.git is marker
        state.__dict__["usage"] = marker
        assert views.usage.usage is marker
        views.identity.pending_codes = {"pending": {"expires_ms": 38}}
        assert state.pending_codes == {"pending": {"expires_ms": 38}}
    finally:
        state.push.close()


@pytest.mark.parametrize(
    "role",
    [
        "agents",
        "commands",
        "devices",
        "enrollments",
        "events",
        "git_access",
        "health",
        "hosts",
        "identity",
        "metrics",
        "pairing",
        "pictures",
        "sockets",
        "terminals",
        "updates",
        "usage",
    ],
)
def test_views_do_not_expose_unrelated_state(role: str) -> None:
    state = GatewayState(backend=FakeBackend(now_ms=lambda: 1))
    try:
        view = getattr(build_capabilities(state), role)
        for name in ("revoke_spool", "push_worker", "debug_docs", "web_push_sink"):
            assert not hasattr(view, name)
        assert not hasattr(view, "state")
    finally:
        state.push.close()


def test_pending_code_slot_is_the_only_writable_router_property() -> None:
    assert isinstance(IdentityCapabilities.pending_codes, property)
    assert IdentityCapabilities.pending_codes.fset is not None
    state = GatewayState(backend=FakeBackend(now_ms=lambda: 1))
    try:
        view = build_capabilities(state).git_access
        with pytest.raises(AttributeError):
            view.git = None
    finally:
        state.push.close()
