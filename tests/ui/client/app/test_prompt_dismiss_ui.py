"""The stuck-prompt flow reads plainly and Dismiss works on a phone and a tablet."""

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
from relaydeck.client.models.commands.wording import (
    cannot_confirm_body,
    cannot_confirm_title,
    checking,
    dismiss_body,
    dismissed,
    earlier_unconfirmed,
    not_sent,
    refused,
    still_unconfirmed,
)
from relaydeck.client.platform.storage.memory_store import MemoryCommandStore
from relaydeck.client.platform.transport.transport import HttpxGatewayTransport
from tests.support.creation.pending_command_records import command, record
from tests.support.host_harness import run
from tests.support.ui.retained_root import _open, _render
from tests.support.ui.screen_text import screen_text

SIZES = [(390, 844), (1376, 1032)]
INTERNAL = ("operation", "command_recovery", "unresolved", "herdr", "agent.prompt", "gateway")
AGENT = "web"


async def _stuck(pending: PendingCommands, identity: str = "stuck") -> dict[str, Any]:
    body = command(identity, host="home-mac", target="term-1")
    await pending.begin(body)
    await pending.observe("home-mac", body["command_id"], None, unresolvable=True)
    return body


async def _show(app: Any, controller: Any, width: int) -> None:
    controller.push(replace(controller.state, granted_hosts=frozenset({"home-mac"})))
    await app.settle()
    if width < 600:
        await app.set_props_and_fire("terminal-operations", {}, "click")
    await app.set_props_and_fire("command-recovery-toggle", {}, "click")
    await app.settle()


def _clean(texts: list[str]) -> None:
    joined = " ".join(texts).lower()
    assert not [word for word in INTERNAL if word in joined], texts


@pytest.mark.parametrize("width,height", SIZES)
def test_unresolvable_prompt_is_worded_plainly_and_dismissed_after_confirming(
    width: int, height: int
) -> None:
    async def scenario() -> None:
        pending = PendingCommands(MemoryCommandStore(), "https://gw", "acct", "dev")
        submissions: list[Any] = []
        app, controller, sessions, pool, _ = await _render(
            bridge=RouteBridge(width=width, height=height), pending_commands=pending
        )

        async def refuse(body: dict[str, Any]) -> dict[str, Any]:
            submissions.append(body)
            raise AssertionError

        sessions.factory.submit_command = refuse
        try:
            await _open(app, "home-mac:term-1")
            session = pool.foreground.session
            session.on_draft_change("kept draft")
            body = await _stuck(pending)
            await _show(app, controller, width)

            texts = screen_text(app)
            assert "1 message needs your attention" in texts
            assert cannot_confirm_title(AGENT) == f"Couldn't confirm a message to {AGENT}"
            assert cannot_confirm_title(AGENT) in texts
            assert cannot_confirm_body(AGENT) in texts
            assert app.find("command-recovery-open-terminal") is not None
            assert app.find("command-recovery-dismiss") is not None
            _clean(texts)

            await app.set_props_and_fire("command-recovery-dismiss", {}, "click")
            await app.settle()
            texts = screen_text(app)
            assert "Dismiss this message?" in texts and dismiss_body(AGENT) in texts
            _clean(texts)

            await app.set_props_and_fire("command-recovery-dismiss-cancel", {}, "click")
            await app.settle()
            assert ("home-mac", body["command_id"]) in pending.records
            assert cannot_confirm_title(AGENT) in screen_text(app)

            await app.set_props_and_fire("command-recovery-dismiss", {}, "click")
            await app.set_props_and_fire("command-recovery-dismiss-confirm", {}, "click")
            async with asyncio.timeout(3):
                while pending.records:
                    await asyncio.sleep(0.01)
            await app.settle()
            assert dismissed(AGENT) == f"Dismissed. You can send to {AGENT} again."
            assert dismissed(AGENT) in screen_text(app)
            assert session.store.draft == "kept draft"
            assert submissions == []
            await pending.begin(command("fresh", host="home-mac", target="term-1"))
        finally:
            await pool.shutdown()

    run(scenario())


@pytest.mark.parametrize("width,height", SIZES)
def test_a_message_still_being_checked_or_proven_unsent_offers_no_dismiss(
    width: int, height: int
) -> None:
    async def scenario() -> None:
        pending = PendingCommands(MemoryCommandStore(), "https://gw", "acct", "dev")
        app, controller, _, pool, _ = await _render(
            bridge=RouteBridge(width=width, height=height), pending_commands=pending
        )
        try:
            await _open(app, "home-mac:term-1")
            recent = command("recent", host="home-mac", target="term-1")
            await pending.begin(recent)
            await pending.observe("home-mac", recent["command_id"], None)
            proven = command("proven", host="home-mac", target="term-2")
            await pending.begin(proven)
            await pending.observe("home-mac", proven["command_id"], record(proven, "rejected"))
            await _show(app, controller, width)

            texts = screen_text(app)
            assert "2 messages need your attention" in texts
            assert checking(AGENT) in texts
            assert not_sent("agent-2") in texts
            assert "Check now" in texts
            with pytest.raises(KeyError):
                app.find("command-recovery-dismiss")
            _clean(texts)
        finally:
            await pool.shutdown()

    run(scenario())


@pytest.mark.parametrize("width,height", SIZES)
def test_a_send_refused_for_an_unconfirmed_message_reads_plainly_with_check_now(
    width: int, height: int
) -> None:
    from tests.support.ui.navigation import _StubTransport

    async def scenario() -> None:
        pending = PendingCommands(MemoryCommandStore(), "https://gw", "acct", "dev")
        resolves: list[Any] = []

        async def token() -> str:
            return "synthetic"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and request.url.path.endswith("/resolve"):
                resolves.append(json.loads(request.content))
            return httpx.Response(
                404, json={"code": "not_found", "message": "Missing", "retryable": False}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            transport = HttpxGatewayTransport("https://gw", token, http, commands=pending)

            class RecoveryTransport(_StubTransport):
                async def command_status(
                    self, host_id: str, command_id: str
                ) -> dict[str, Any] | None:
                    return await transport.command_status(host_id, command_id)

            app, controller, sessions, pool, _ = await _render(
                bridge=RouteBridge(width=width, height=height),
                pending_commands=pending,
                transport=RecoveryTransport(),
            )
            sessions.factory.submit_command = transport.submit_command
            sessions.factory.observe_command = CommandObserver(transport.command_status).observe
            sessions.factory.subscribe_command = pending.subscribe_outcomes
            try:
                await _open(app, "home-mac:term-1")
                session = pool.foreground.session
                await _stuck(pending)
                session.on_draft_change("another question")
                await app.settle()
                assert await session.on_send() == "failed"
                await app.settle()

                assert session.store.error == earlier_unconfirmed(AGENT)
                assert session.store.draft == "another question"
                check = session.store.error.check
                assert check is not None
                texts = screen_text(app)
                assert earlier_unconfirmed(AGENT) in texts and "Check now" in texts
                _clean(texts)
                assert resolves == []

                check()
                async with asyncio.timeout(3):
                    while session.store.error == earlier_unconfirmed(AGENT):
                        await asyncio.sleep(0.01)
                await app.settle()
                assert resolves and session.store.draft == "another question"
                assert session.store.error == still_unconfirmed()
                assert still_unconfirmed() in screen_text(app)

                # Dismissing in the banner clears the refusal and keeps the draft.
                await _show(app, controller, width)
                await app.set_props_and_fire("command-recovery-dismiss", {}, "click")
                await app.set_props_and_fire("command-recovery-dismiss-confirm", {}, "click")
                async with asyncio.timeout(3):
                    while session.store.error is not None:
                        await asyncio.sleep(0.01)
                await app.settle()
                assert session.store.draft == "another question" and session.store.can_send
                assert still_unconfirmed() not in screen_text(app)
            finally:
                await pool.shutdown()

    run(scenario())


@pytest.mark.parametrize("width,height", SIZES)
@pytest.mark.parametrize("status,code", [(413, "too_large"), (409, "stale_epoch")])
def test_a_permanent_refusal_says_why_and_keeps_the_draft(
    width: int, height: int, status: int, code: str
) -> None:
    async def scenario() -> None:
        pending = PendingCommands(MemoryCommandStore(), "https://gw", "acct", "dev")
        posts: list[Any] = []

        async def token() -> str:
            return "synthetic"

        def handler(request: httpx.Request) -> httpx.Response:
            posts.append(json.loads(request.content))
            return httpx.Response(
                status, json={"code": code, "message": "server words", "retryable": False}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            transport = HttpxGatewayTransport("https://gw", token, http, commands=pending)
            app, _, sessions, pool, _ = await _render(
                bridge=RouteBridge(width=width, height=height), pending_commands=pending
            )
            sessions.factory.submit_command = transport.submit_command
            sessions.factory.observe_command = CommandObserver(transport.command_status).observe
            try:
                await _open(app, "home-mac:term-1")
                session = pool.foreground.session
                session.on_draft_change("a long question")
                await app.settle()
                assert await session.on_send() == "failed"
                await app.settle()

                said = refused(AGENT, code)
                assert said != not_sent(AGENT)
                assert session.store.error == said and said in screen_text(app)
                assert "server words" not in " ".join(screen_text(app))
                assert session.store.draft == "a long question" and session.store.can_send
                assert len(posts) == 1  # nothing was sent again
                assert not any(pending.unresolved(r) for r in pending.records.values())
            finally:
                await pool.shutdown()

    run(scenario())
