"""Synthetic snapshots, terminal greetings and loopback sign-in for explicit development modes.

The hosted runner loads these only for dev login or demo sessions; native payloads exclude them.
"""

from __future__ import annotations

import hmac
import time
from typing import TYPE_CHECKING, Any, Protocol

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from relaydeck.devtools.fakes.gateway import FakeBackend as FakeBackend

if TYPE_CHECKING:
    from relaydeck.gateway.features.access.api import SessionStore


class _WebSessions(Protocol):
    def create_session(
        self, account: str, device_id: str, access: str, refresh: str, access_expires_ms: int
    ) -> str: ...

    def cookie_headers(self, sid: str) -> dict[str, str]: ...


class _GatewaySessions(Protocol):
    @property
    def sessions(self) -> SessionStore: ...


def dev_snapshot(host_id: str, cursor_secret: bytes) -> dict[str, Any]:
    """Build a schema-valid empty inventory for the local runner using an authenticated
    sequence-zero cursor.
    """
    from relaydeck.gateway.cursors import encode_cursor

    cursor = encode_cursor(cursor_secret, {"host_id": host_id, "sequence": 0})
    return {
        "host": {
            "id": host_id,
            "label": host_id,
            "epoch": "dev-epoch-0",
            "reachable": True,
            "protocol": 22,
            "version": "0.9.1",
            "capabilities": [
                "sessions.read",
                "terminal.view",
                "terminal.control",
                "agents.prompt",
                "agents.start",
                "shells.create",
                "terminal.attach",
            ],
        },
        "cursor": cursor,
        "workspaces": [],
        "tabs": [],
        "panes": [],
        "agents": [],
    }


_DEV_LOGIN_TOKEN = {"value": ""}
_DEV_HOSTS = {"value": frozenset({"home-mac"})}


class DemoBackend(FakeBackend):
    """Demo backend that emits fixed synthetic terminal bytes for each attachment. Input is
    logged without a PTY or connector.
    """

    def create_attachment(
        self,
        host_id: str,
        terminal_id: str,
        kind: str,
        account: str,
        device_id: str,
        *,
        takeover: bool,
        owner_label: str = "",
        expected_control_revision: str | None = None,
    ) -> tuple[Any, tuple[str, ...]]:
        attachment, displaced = super().create_attachment(
            host_id,
            terminal_id,
            kind,
            account,
            device_id,
            takeover=takeover,
            owner_label=owner_label,
            expected_control_revision=expected_control_revision,
        )
        try:
            self.publish_output(attachment.attachment_id, attachment.epoch, DEMO_GREETING)
        except Exception as exc:  # noqa: BLE001 - demo-only; attach already succeeded
            print(f"WARNING: demo greeting failed ({exc})")
        return attachment, displaced


DEMO_SHELL_TAB = "demo-shell"
DEMO_AGENT_TAB = "demo-agent-tab"
DEMO_AGENT_ID = "demo-agent-1"
DEMO_GREETING = b"\x1b[1;36mRelayDeck demo terminal\x1b[0m\r\n\x1b[32mdemo $\x1b[0m "


def dev_demo_snapshot(host_id: str, cursor_secret: bytes) -> dict[str, Any]:
    """Build a schema-valid demo workspace with one shell and one agent for the explicitly
    enabled local demo mode.
    """
    doc = dev_snapshot(host_id, cursor_secret)
    doc["workspaces"] = [
        {
            "id": "demo-ws",
            "host_id": host_id,
            "number": 1,
            "label": "demo",
            "focused": True,
            "pane_count": 2,
            "tab_count": 2,
            "active_tab_id": DEMO_SHELL_TAB,
            "agent_status": "idle",
        }
    ]
    doc["tabs"] = [
        {
            "id": DEMO_SHELL_TAB,
            "host_id": host_id,
            "workspace_id": "demo-ws",
            "number": 1,
            "label": DEMO_SHELL_TAB,
            "focused": True,
            "pane_count": 1,
            "agent_status": "unknown",
        },
        {
            "id": DEMO_AGENT_TAB,
            "host_id": host_id,
            "workspace_id": "demo-ws",
            "number": 2,
            "label": DEMO_AGENT_TAB,
            "focused": False,
            "pane_count": 1,
            "agent_status": "idle",
        },
    ]
    doc["panes"] = [
        {
            "id": "demo-shell-p1",
            "host_id": host_id,
            "workspace_id": "demo-ws",
            "tab_id": DEMO_SHELL_TAB,
            "focused": True,
            "agent_status": "unknown",
        },
        {
            "id": "demo-agent-p1",
            "host_id": host_id,
            "workspace_id": "demo-ws",
            "tab_id": DEMO_AGENT_TAB,
            "focused": False,
            "agent_status": "idle",
        },
    ]
    doc["agents"] = [
        {
            "terminal_id": DEMO_AGENT_ID,
            "host_id": host_id,
            "workspace_id": "demo-ws",
            "tab_id": DEMO_AGENT_TAB,
            "pane_id": "demo-agent-p1",
            "name": "demo-agent",
            "agent": "demo",
            "title": "demo agent",
            "status": "idle",
            "focused": False,
            "launch_pending": False,
            "interactive_ready": True,
            "state_change_seq": 0,
            "revision": 1,
        }
    ]
    return doc


async def _dev_login(request: Request) -> Any:
    """Loopback-only single-use dev login. Enabled only with an explicit
    local-run flag; never in production (no upstream IdP is configured)."""
    store: _WebSessions = request.app.state.rd_store
    gateway_state: _GatewaySessions = request.app.state.rd_gateway_state
    client = request.client.host if request.client else ""
    if client not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse(
            status_code=403, content={"code": "forbidden", "message": "loopback only"}
        )
    presented = request.query_params.get("token", "")
    expected = _DEV_LOGIN_TOKEN["value"]
    if not presented or not expected or not hmac.compare_digest(presented, expected):
        return JSONResponse(status_code=403, content={"code": "forbidden", "message": "bad token"})
    _DEV_LOGIN_TOKEN["value"] = ""
    now_ms = int(time.time() * 1000)
    access, refresh = gateway_state.sessions.create(
        "dev",
        frozenset(
            {
                "sessions.read",
                "terminal.view",
                "terminal.control",
                "agents.prompt",
                "agents.start",
                "shells.create",
                "terminal.attach",
            }
        ),
        _DEV_HOSTS["value"],
        now_ms,
    )
    principal = gateway_state.sessions.lookup(access, now_ms)
    assert principal is not None  # noqa: S101
    sid = store.create_session(
        principal.account, principal.device_id, access, refresh, now_ms + 900_000
    )
    return RedirectResponse(url="/", status_code=302, headers=store.cookie_headers(sid))
