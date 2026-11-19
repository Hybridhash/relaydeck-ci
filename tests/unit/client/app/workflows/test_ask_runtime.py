"""Latest identity and metadata govern each one-shot terminal handoff."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from relaydeck.client.app.workflows.ask import AskRuntime
from relaydeck.client.features.ask_agent.model import AskScope
from relaydeck.client.features.changes.reading_bookmark import ReadingBookmark
from relaydeck.client.features.changes.state import ChangesModel
from relaydeck.domain.models import AgentStatus
from tests.support.fakes import FakeCredentialStore
from tests.support.scripted_sessions import make_scripted_sessions
from tests.support.ui.navigation import _state


async def runtime() -> tuple[AskRuntime, Any, Any, Any]:
    state = _state()
    key, agent = next(iter(state.inventory.agents.items()))
    host = state.inventory.hosts[agent.host_id]
    env = make_scripted_sessions()
    session = env.factory.make_agent(
        host_id=agent.host_id,
        host_epoch=host.epoch,
        agent_id=agent.terminal_id,
        target=agent.terminal_id,
    )
    session._draft_store = FakeCredentialStore()
    await session.prepare_draft()
    root = AskRuntime(lambda: None)

    def navigate(key: str) -> None:
        root.current["selection"], root.current["section"] = key, ""

    root.current = dict(
        account="account",
        transport=object(),
        state=state,
        destination="agents",
        selection=key,
        section="changes",
        foreground=True,
        navigate=lambda *a, **k: None,
        open_terminal=navigate,
        entry=SimpleNamespace(kind="agent", key=key, epoch=host.epoch, session=session),
    )
    assert root.start(
        AskScope("branch", "working", "snapshot", "a" * 40),
        "Why?",
        ReadingBookmark.capture(ChangesModel()),
        "Session",
        identity=root.gate(key)[0],
    )
    return root, env, session, agent


def test_latest_revision_working_dispatch_and_no_later_repeat() -> None:
    async def scenario() -> None:
        root, env, session, agent = await runtime()
        state = root.current["state"]
        updated = replace(agent, revision=17, status=AgentStatus.WORKING)
        root.current["state"] = replace(
            state, inventory=replace(state.inventory, agents={root.current["selection"]: updated})
        )
        await asyncio.gather(root.drive(), root.drive())
        assert len(env.submitted) == 1 and env.submitted[0]["expected_revision"] == 17
        root.current["state"] = replace(
            state,
            inventory=replace(
                state.inventory,
                agents={root.current["selection"]: replace(updated, status=AgentStatus.DONE)},
            ),
        )
        await root.drive()
        assert len(env.submitted) == 1 and not env.ops.attaches
        await session.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change", ["epoch", "account", "transport", "section", "recipient", "revoked", "disposed"]
)
def test_identity_and_route_changes_cancel(change: str) -> None:
    async def scenario() -> None:
        root, env, session, agent = await runtime()
        state = root.current["state"]
        if change == "epoch":
            host = state.inventory.hosts[agent.host_id]
            root.current["state"] = replace(
                state,
                inventory=replace(state.inventory, hosts={host.id: replace(host, epoch="next")}),
            )
        elif change == "account":
            root.current["account"] = "different"
        elif change == "transport":
            root.current["transport"] = object()
        elif change == "section":
            root.current["section"] = "changes"
            assert root.handoff.pending is not None
            root.handoff.pending.state = "waiting_for_readiness"
        elif change == "recipient":
            root.current["selection"] = "host:other"
        elif change == "revoked":
            root.current["state"] = replace(state, revoked=True)
        else:
            await session.dispose()
        await root.drive()
        assert root.handoff.pending is None and not env.submitted
        await session.dispose()

    asyncio.run(scenario())


def test_blocked_during_navigation_is_not_delayed_until_idle() -> None:
    async def scenario() -> None:
        root, env, session, agent = await runtime()
        state = root.current["state"]
        root.current["state"] = replace(
            state,
            inventory=replace(
                state.inventory,
                agents={root.current["selection"]: replace(agent, status=AgentStatus.BLOCKED)},
            ),
        )
        await root.drive()
        assert root.handoff.pending is not None
        assert root.handoff.pending.state == "refused"
        root.current["state"] = state
        await root.drive()
        assert not env.submitted and not env.ops.attaches
        await session.dispose()

    asyncio.run(scenario())


def test_live_metadata_and_route_override_an_older_render() -> None:
    async def scenario() -> None:
        root, env, session, agent = await runtime()
        old = root.current["state"]
        blocked = replace(
            old,
            inventory=replace(
                old.inventory,
                agents={root.current["selection"]: replace(agent, status=AgentStatus.BLOCKED)},
            ),
        )
        root.current["read_state"] = lambda: blocked
        await root.drive()
        assert not env.submitted
        root.handoff.clear()
        key = root.current["selection"]
        root.current["read_state"] = lambda: old
        root.current["read_route"] = lambda: SimpleNamespace(
            destination="agents", selection="host:other", section="changes"
        )
        assert not root.start(
            AskScope("branch", "working", "snapshot", "a" * 40),
            "Why?",
            ReadingBookmark.capture(ChangesModel()),
            "Session",
            recipient_key=key,
            identity=root.gate(key)[0],
        )
        root.close()
        assert root.handoff.pending is None and root.handoff.returned is None
        await session.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["epoch", "account", "transport"])
def test_sheet_send_rejects_replaced_identity_before_request(change: str) -> None:
    from functools import partial

    async def scenario() -> None:
        root, env, old_session, agent = await runtime()
        root.handoff.clear()
        root.current["section"] = "changes"
        key = root.current["selection"]
        frozen = root.gate(key)[0]
        send = partial(root.start, identity=frozen)
        state = root.current["state"]
        epoch = frozen.epoch
        if change == "epoch":
            epoch = "replacement-epoch"
            host = state.inventory.hosts[agent.host_id]
            root.current["state"] = replace(
                state,
                inventory=replace(state.inventory, hosts={host.id: replace(host, epoch=epoch)}),
            )
        elif change == "account":
            root.current["account"] = "different"
        else:
            root.current["transport"] = object()
        replacement = env.factory.make_agent(
            host_id=agent.host_id,
            host_epoch=epoch,
            agent_id=agent.terminal_id,
            target=agent.terminal_id,
        )
        replacement._draft_store = FakeCredentialStore()
        await replacement.prepare_draft()
        root.current["entry"] = SimpleNamespace(
            kind="agent", key=key, epoch=epoch, session=replacement
        )
        try:
            assert not send(
                AskScope("branch", "working", "old-snapshot", "a" * 40),
                "Why?",
                ReadingBookmark.capture(ChangesModel()),
                "Session",
            )
            await root.drive()
            assert root.handoff.pending is None and not env.submitted and not env.ops.attaches
        finally:
            await replacement.dispose()
            await old_session.dispose()

    asyncio.run(scenario())


def _patched_send(session: Any, outcome: Any) -> None:
    async def send(*, prompt_only: bool = False) -> Any:
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    session.on_send = send


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ("delivered-via-prompt", "settled"),
        ("failed", "refused"),
        ("ignored", "refused"),
        ("blocked-review", "refused"),
        ("unknown", "uncertain"),
    ],
)
def test_send_status_maps_to_the_origin_state(status: str, state: str) -> None:
    async def scenario() -> None:
        root, env, session, _agent = await runtime()
        _patched_send(session, status)
        await root.drive()
        pending = root.handoff.pending
        assert pending is not None and pending.state == state
        if state == "settled":
            assert pending.reason == ""
        await session.dispose()

    asyncio.run(scenario())


def test_a_raising_send_refuses_repaints_and_allows_a_new_ask_after_dismiss() -> None:
    async def scenario() -> None:
        root, _env, session, _agent = await runtime()
        changed: list[bool] = []
        root.changed = lambda: changed.append(True)
        _patched_send(session, RuntimeError("secret detail"))
        with pytest.raises(RuntimeError):
            await root.drive()
        pending = root.handoff.pending
        assert pending is not None
        assert pending.state == "refused" and pending.reason == "Could not send the question."
        assert changed  # the strip repainted even though the send raised
        root.dismiss()
        root.current["section"] = "changes"
        key = root.current["selection"]
        assert root.start(
            AskScope("branch", "working", "snapshot", "a" * 40),
            "Again?",
            ReadingBookmark.capture(ChangesModel()),
            "Session",
            identity=root.gate(key)[0],
        )
        await session.dispose()

    asyncio.run(scenario())


def test_a_cancelled_send_is_uncertain_never_settled_or_claimed() -> None:
    async def scenario() -> None:
        root, _env, session, _agent = await runtime()
        changed: list[bool] = []
        root.changed = lambda: changed.append(True)
        never = asyncio.Event()

        async def hang(*, prompt_only: bool = False) -> str:
            await never.wait()
            return "delivered-via-prompt"

        session.on_send = hang
        task = asyncio.create_task(root.drive())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        pending = root.handoff.pending
        assert pending is not None and pending.state == "uncertain"
        assert changed
        await session.dispose()

    asyncio.run(scenario())


def test_repeated_wakes_keep_one_drive_and_one_send_in_flight() -> None:
    async def scenario() -> None:
        root, _env, session, _agent = await runtime()
        calls = 0
        release = asyncio.Event()

        async def send(*, prompt_only: bool = False) -> str:
            nonlocal calls
            calls += 1
            await release.wait()
            return "delivered-via-prompt"

        session.on_send = send
        tasks: list[asyncio.Task[Any]] = []
        root._schedule = lambda cb: tasks.append(asyncio.create_task(cb()))
        for _ in range(5):
            root.wake()
        await asyncio.sleep(0.05)
        assert calls == 1 and root.driving
        release.set()
        await asyncio.gather(*tasks)
        assert calls == 1 and not root.driving
        assert root.handoff.pending is not None and root.handoff.pending.state == "settled"
        await session.dispose()

    asyncio.run(scenario())


def test_page_task_failures_log_the_type_name_only(caplog: pytest.LogCaptureFixture) -> None:
    from relaydeck.client.app.workflows.page_tasks import PageTasks

    async def scenario() -> None:
        connection = object()
        page = SimpleNamespace(session=SimpleNamespace(connection=connection))
        tasks = PageTasks(page, lambda: None)

        async def fail() -> None:
            raise ValueError("secret detail")

        with caplog.at_level("WARNING"):
            tasks.start(fail)
            await asyncio.sleep(0.05)
        assert tasks.tasks == set()

    asyncio.run(scenario())
    assert "ValueError" in caplog.text and "secret detail" not in caplog.text


def test_start_keeps_frozen_identity_and_rejects_key_only_authority() -> None:
    async def scenario() -> None:
        root, _, session, _ = await runtime()
        root.handoff.clear()
        root.current["section"] = "changes"
        key = root.current["selection"]
        scope = AskScope("branch", "working", "snapshot", "a" * 40)
        bookmark = ReadingBookmark.capture(ChangesModel())
        assert not root.start(scope, "Why?", bookmark, "Session", recipient_key=key)
        frozen = root.gate(key)[0]
        assert root.start(scope, "Why?", bookmark, "Session", identity=frozen)
        assert root.handoff.pending is not None and root.handoff.pending.identity is frozen
        await session.dispose()

    asyncio.run(scenario())
