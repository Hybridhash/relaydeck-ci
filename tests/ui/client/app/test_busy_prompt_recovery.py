"""A refused busy prompt keeps its draft and an enabled Send on phone and tablet."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import httpx
import pytest

from relaydeck.client.app.navigation import RouteBridge
from relaydeck.client.features.commands.observation import CommandObserver
from relaydeck.client.features.commands.recovery import PendingCommands
from relaydeck.client.models.commands.wording import not_sent
from relaydeck.client.platform.storage.memory_store import MemoryCommandStore
from relaydeck.client.platform.transport.transport import HttpxGatewayTransport
from tests.support.creation.pending_command_records import record
from tests.support.host_harness import run
from tests.support.terminal.direct_typing import open_compose
from tests.support.ui.retained_root import _open, _render


@pytest.mark.parametrize("width,height", [(390, 844), (1376, 1000)])
def test_busy_refusal_retains_native_draft_and_send_for_explicit_retry(
    width: int, height: int
) -> None:
    async def scenario() -> None:
        posts = []
        accepted = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                body = json.loads(request.content)
                posts.append(body)
                if len(posts) == 1:
                    return httpx.Response(
                        409,
                        json={
                            "code": "stale_revision",
                            "message": "Agent changed",
                            "retryable": True,
                        },
                    )
                accepted.update(record(body, "succeeded"))
            return httpx.Response(200, json=accepted)

        async def token() -> str:
            return "synthetic"

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            pending = PendingCommands(MemoryCommandStore(), "https://gw", "acct", "dev")
            transport = HttpxGatewayTransport("https://gw", token, http, commands=pending)
            app, _, sessions, pool, _ = await _render(
                bridge=RouteBridge(width=width, height=height),
                touch=True,
                platform_name="ios",
            )
            sessions.factory.submit_command = transport.submit_command
            sessions.factory.observe_command = CommandObserver(transport.command_status).observe
            sessions.factory.subscribe_command = pending.subscribe_outcomes

            def send_control() -> Any:
                if width >= 600:
                    return app.find("composer-send")
                suffix = app.find("composer-draft").suffix_icon
                control = suffix.controls[0] if hasattr(suffix, "controls") else suffix
                assert control.key == "composer-send" and control._i in app.session.index
                return control

            try:
                await _open(app, "home-mac:term-1")
                session = pool.foreground.session
                session.store.agent_status_did_change("working")
                session.on_draft_change("kept multiline\nquestion")
                if width < 600:
                    await open_compose(app)
                await app.settle()
                send = send_control()
                assert not send.disabled and send.visible
                await app.session.dispatch_event(send._i, "click", "")
                async with asyncio.timeout(3):
                    while session.store.submission != "failed":
                        await asyncio.sleep(0.01)
                await app.settle()
                assert session.store.draft == "kept multiline\nquestion"
                assert app.find("composer-draft").value == session.store.draft
                send = send_control()
                assert not send.disabled and send.visible
                session.store.agent_status_did_change("idle")
                await app.session.dispatch_event(send._i, "click", "")
                async with asyncio.timeout(3):
                    while session.store.draft:
                        await asyncio.sleep(0.01)
                await app.settle()
                assert len(posts) == 2
                assert posts[0]["command_id"] != posts[1]["command_id"]
                assert posts[1]["payload"]["text"] == "kept multiline\nquestion"
                assert all(not pending.unresolved(row) for row in pending.records.values())
            finally:
                await pool.shutdown()

    run(scenario())


def test_rendered_check_status_resolves_the_retained_unknown_message() -> None:
    from tests.support.ui.navigation import _StubTransport

    async def scenario() -> None:
        pending = PendingCommands(MemoryCommandStore(), "https://gw", "acct", "dev")
        body: dict[str, Any] = {}
        resolutions = []

        async def token() -> str:
            return "synthetic"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(
                    404, json={"code": "not_found", "message": "Missing", "retryable": False}
                )
            resolutions.append(json.loads(request.content))
            return httpx.Response(200, json={**record(body, "rejected"), "error_code": "expired"})

        async def submit(command: dict[str, Any]) -> dict[str, Any]:
            body.update(command)
            await pending.begin(command)
            raise httpx.ReadError("connection lost before acceptance")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            transport = HttpxGatewayTransport("https://gw", token, http, commands=pending)

            class RecoveryTransport(_StubTransport):
                async def command_status(
                    self, host_id: str, command_id: str
                ) -> dict[str, Any] | None:
                    return await transport.command_status(host_id, command_id)

            app, controller, sessions, pool, _ = await _render(
                bridge=RouteBridge(width=1376, height=1000),
                pending_commands=pending,
                transport=RecoveryTransport(),
            )
            controller.push(replace(controller.state, granted_hosts=frozenset({"home-mac"})))
            sessions.factory.submit_command = submit
            sessions.factory.observe_command = CommandObserver(transport.command_status).observe
            sessions.factory.subscribe_command = pending.subscribe_outcomes
            try:
                await _open(app, "home-mac:term-1")
                session = pool.foreground.session
                session.set_agent_status("working")
                session.on_draft_change("kept question")
                assert await session.on_send(prompt_only=True) == "unknown"
                await pending.observe(body["host_id"], body["command_id"], None)
                await app.settle()
                assert session.store.messages[-1].state.kind == "unknown"
                await app.set_props_and_fire("command-recovery-check", {}, "click")
                async with asyncio.timeout(3):
                    while session.store.submission == "unknown":
                        await asyncio.sleep(0.01)
                await app.settle()
                assert session.store.messages[-1].state.kind == "failed"
                assert session.store.draft == "kept question"
                assert session.store.error == not_sent("web")
                assert app.find("composer-send").visible and not app.find("composer-send").disabled
                assert resolutions == [
                    {
                        "protocol": "relaydeck.gateway.v1",
                        "agent_id": body["target"]["agent_id"],
                        "created_ms": next(iter(pending.records.values()))["created_ms"],
                    }
                ]
            finally:
                await pool.shutdown()

    run(scenario())
