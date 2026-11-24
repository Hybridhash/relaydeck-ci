"""A connector's typed attach refusal severs the viewer with a lease conflict, not a stale epoch."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from relaydeck.gateway.app.bootstrap import create_app
from tests.support.asgi_socket import AsgiSocket
from tests.support.terminal.sockets import _state


def test_attach_refused_closes_viewers_with_a_lease_conflict(monkeypatch: Any) -> None:
    async def scenario() -> None:
        state, backend, _ = _state()
        token = state.hosts.enroll("home-mac", account="acct-1")
        closed = asyncio.Event()

        class Viewer:
            def __init__(self) -> None:
                self.texts: list[str] = []
                self.close_calls: list[dict[str, Any]] = []

            async def send_text(self, text: str) -> None:
                self.texts.append(text)

            async def close(self, **kwargs: Any) -> None:
                self.close_calls.append(kwargs)
                closed.set()

        viewer = Viewer()
        state.sockets.register("acct-1", "lease", viewer)
        calls: list[tuple[str, str, str, str]] = []

        def refuse(host: str, terminal: str, epoch: str, account: str) -> tuple[str, ...]:
            calls.append((host, terminal, epoch, account))
            return ("lease",)

        monkeypatch.setattr(backend, "refuse_attachment", refuse)
        resets: list[Any] = []
        monkeypatch.setattr(backend, "reset_terminal", lambda *a: resets.append(a) or ())
        socket = await AsgiSocket(create_app(state), "/v1/connectors", token=token).open()
        try:
            await socket.recv_text()
            await socket.send_text(
                json.dumps(
                    {
                        "kind": "terminal.attach_refused",
                        "host_id": "home-mac",
                        "terminal_id": "term",
                        "epoch": "e1",
                        "reason": "terminal_owned",
                    }
                )
            )
            await asyncio.wait_for(closed.wait(), 2)
            assert calls == [("home-mac", "term", "e1", "acct-1")] and resets == []
            assert [json.loads(t) for t in viewer.texts] == [
                {"kind": "reset", "detail": "lease_conflict"}
            ]
            assert viewer.close_calls and all(
                "lease_conflict" in str(call) for call in viewer.close_calls
            )
            await socket.send_text(json.dumps({"kind": "bye"}))
        finally:
            await socket.close()
            await state.sockets.close_lease("lease", "closed")

    asyncio.run(scenario())


def test_attach_refused_with_an_unknown_reason_fails_the_tunnel_closed() -> None:
    async def scenario() -> None:
        state, _backend, _ = _state()
        token = state.hosts.enroll("home-mac", account="acct-1")
        socket = await AsgiSocket(create_app(state), "/v1/connectors", token=token).open()
        try:
            await socket.recv_text()
            await socket.send_text(
                json.dumps(
                    {
                        "kind": "terminal.attach_refused",
                        "host_id": "home-mac",
                        "terminal_id": "term",
                        "epoch": "e1",
                        "reason": "owned by someone",
                    }
                )
            )
            message = await socket.receive()
            assert message["type"] == "websocket.close"
        finally:
            await socket.close()

    asyncio.run(scenario())
