"""Prompt refusals retain a sendable draft; missing IDs are fenced before retry."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from relaydeck.client.features.commands.observation import CommandObserver
from relaydeck.client.features.commands.recovery import PendingCommands
from relaydeck.client.features.composer.model import NOT_SENT_MESSAGE, ComposerStore
from relaydeck.client.platform.storage.memory_store import MemoryCommandStore
from relaydeck.client.platform.transport.transport import HttpxGatewayTransport
from relaydeck.client.ports.commands import RecoveryError
from relaydeck.contracts import paths
from relaydeck.devtools.fakes.gateway import FakeBackend
from relaydeck.gateway.app.backend import JournalBackend
from relaydeck.gateway.app.bootstrap import create_app
from relaydeck.gateway.app.state import GatewayState
from relaydeck.host.persistence.command_journal import TOMBSTONE_MS, CommandJournal
from tests.support.creation.pending_command_records import command


def test_refused_busy_prompt_keeps_draft_and_allows_a_fresh_send() -> None:
    async def scenario() -> None:
        posts = []

        def handler(request: httpx.Request) -> httpx.Response:
            posts.append(json.loads(request.content))
            return httpx.Response(
                409, json={"code": "stale_revision", "message": "Agent changed", "retryable": True}
            )

        async def token() -> str:
            return "synthetic"

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            transport = HttpxGatewayTransport("https://gw", token, http)
            transport.commands = PendingCommands(MemoryCommandStore(), "https://gw", "acct", "dev")
            store = ComposerStore(
                target="w1:p1",
                host_id="home-mac",
                host_epoch="epoch-7",
                agent_id="w1:p1",
                submit_command=transport.submit_command,
                prompt_text=None,
                observe_command=CommandObserver(transport.command_status).observe,
            )
            store.agent_status_did_change("working")
            store.replace_draft("kept question")
            assert await store.send(prompt_only=True) == "failed"
            assert store.draft == "kept question" and store.can_send
            assert all(
                not transport.commands.unresolved(r) for r in transport.commands.records.values()
            )
            store.agent_status_did_change("idle")
            assert await store.send(prompt_only=True) == "failed"
            assert len(posts) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("age_ms", [0, 25 * 60 * 60 * 1000])
def test_check_status_fences_missing_prompt_before_a_delayed_post(
    tmp_path: Path, age_ms: int
) -> None:
    async def scenario() -> None:
        journal = CommandJournal(tmp_path / "commands.db")
        backend = JournalBackend(journal)
        backend.snapshots["home-mac"] = json.loads(
            Path("tests/fixtures/gateway/snapshot.json").read_text()
        )
        state = GatewayState(backend=backend, cursor_secret=b"s" * 32)
        access, _ = state.sessions.create(
            "acct",
            frozenset({"sessions.read", "agents.prompt"}),
            frozenset({"home-mac"}),
            state.now_ms(),
            "dev",
        )

        async def token() -> str:
            return access

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state))) as http:
            transport = HttpxGatewayTransport("https://gw", token, http)
            pending = PendingCommands(
                MemoryCommandStore(),
                "https://gw",
                "acct",
                "dev",
                clock=lambda: state.now_ms() - age_ms,
            )
            transport.commands = pending
            body = command()
            await pending.begin(body)
            await pending.observe(body["host_id"], body["command_id"], None)
            await pending.reconcile(transport.command_status, frozenset({"home-mac"}))
            row = pending.records[(body["host_id"], body["command_id"])]
            assert row["outcome"] == "rejected"
            assert not pending.unresolved(row)
            stored = journal.record(body["command_id"])
            assert stored is not None and stored["status"] == "expired"
            delayed = await http.post(
                "https://gw/v1/hosts/home-mac/commands",
                json=body,
                headers={"Authorization": f"Bearer {access}"},
            )
            assert delayed.status_code == 409 and delayed.json()["code"] == "command_conflict"
            assert backend.drain_outbox("home-mac") == []
        journal.close()
        state.push.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["accepted", "dispatched", "unknown", "succeeded", "failed"])
def test_existing_prompt_is_only_observed_never_replayed(tmp_path: Path, outcome: str) -> None:
    async def scenario() -> None:
        journal = CommandJournal(tmp_path / "commands.db")
        backend = JournalBackend(journal)
        state = GatewayState(backend=backend, cursor_secret=b"s" * 32)
        access, _ = state.sessions.create(
            "acct",
            frozenset({"sessions.read", "agents.prompt"}),
            frozenset({"home-mac"}),
            state.now_ms(),
            "dev",
        )
        body = command()
        journal.submit(
            command_id=body["command_id"],
            principal="acct",
            host_id="home-mac",
            kind="agent.prompt",
            target=body["target"],
            payload=body["payload"],
        )
        if outcome != "accepted":
            journal.mark_dispatched(body["command_id"])
        if outcome == "unknown":
            journal.mark_unknown(body["command_id"])
        elif outcome in ("succeeded", "failed"):
            journal.mark_result(body["command_id"], outcome)

        async def token() -> str:
            return access

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state))) as http:
            transport = HttpxGatewayTransport("https://gw", token, http)
            pending = PendingCommands(MemoryCommandStore(), "https://gw", "acct", "dev")
            transport.commands = pending
            await pending.begin(body)
            await pending.observe("home-mac", body["command_id"], None)
            await pending.reconcile(transport.command_status, frozenset({"home-mac"}))
            stored = journal.record(body["command_id"])
            assert stored is not None and stored["status"] == outcome
            assert backend.drain_outbox("home-mac") == []
            if outcome == "succeeded":
                assert not pending.records
            else:
                assert next(iter(pending.records.values()))["outcome"] == outcome
        journal.close()
        state.push.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("recovery", ["check", "idle"])
def test_recovery_settles_the_store_and_preserves_an_edited_draft(
    tmp_path: Path, recovery: str
) -> None:
    async def scenario() -> None:
        journal = CommandJournal(tmp_path / "commands.db")
        backend = JournalBackend(journal)
        state = GatewayState(backend=backend, cursor_secret=b"s" * 32)
        access, _ = state.sessions.create(
            "acct",
            frozenset({"sessions.read", "agents.prompt"}),
            frozenset({"home-mac"}),
            state.now_ms(),
            "dev",
        )
        pending = PendingCommands(MemoryCommandStore(), "https://gw", "acct", "dev")

        async def token() -> str:
            return access

        async def lost_before_acceptance(body: dict[str, Any]) -> dict[str, Any]:
            await pending.begin(body)
            raise httpx.ReadError("Response lost")

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state))) as http:
            transport = HttpxGatewayTransport("https://gw", token, http, commands=pending)
            store = ComposerStore(
                target="w1:p1",
                host_id="home-mac",
                host_epoch="epoch-7",
                agent_id="w1:p1",
                prompt_text=None,
                submit_command=lost_before_acceptance,
                observe_command=CommandObserver(transport.command_status).observe,
                subscribe_command=pending.subscribe_outcomes,
            )
            store.agent_status_did_change("working")
            store.replace_draft("first question")
            assert await store.send(prompt_only=True) == "unknown"
            store.replace_draft("edited question")
            if recovery == "check":
                await pending.reconcile(transport.command_status, frozenset({"home-mac"}))
            else:
                store.agent_status_did_change("idle")
                async with asyncio.timeout(2):
                    while store.submission == "unknown":
                        await asyncio.sleep(0.001)
            assert store.submission == "failed"
            assert store.messages[0].state.kind == "failed"
            assert store.draft == "edited question" and store.can_send
            assert store.error == NOT_SENT_MESSAGE
            store.clear_sensitive()
            assert not pending.outcome_listeners
        journal.close()
        state.push.close()

    asyncio.run(scenario())


def test_absence_resolution_is_scoped_and_retention_bounded(tmp_path: Path) -> None:
    now = TOMBSTONE_MS + 1_000
    journal = CommandJournal(tmp_path / "commands.db", clock=lambda: now)
    try:
        assert (
            journal.resolve_prompt("missing-old", "acct", "dev", "home-mac", "a", 1_000, now=now)
            is None
        )
        assert journal.record("missing-old") is None
        result = journal.resolve_prompt(
            "missing-new", "acct", "dev", "home-mac", "a", now - 1_000, now=now
        )
        assert result is not None and result["status"] == "rejected"
        for account, host, target in [
            ("other", "home-mac", "a"),
            ("acct", "other", "a"),
            ("acct", "home-mac", "other"),
        ]:
            assert (
                journal.resolve_prompt(
                    "missing-new", account, "dev", host, target, now - 1_000, now=now
                )
                is None
            )
    finally:
        journal.close()


def test_slow_authentication_fails_before_any_pending_intent_or_http() -> None:
    async def scenario() -> None:
        calls = []

        async def token() -> str:
            await asyncio.Future()
            raise AssertionError

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            raise AssertionError

        pending = PendingCommands(MemoryCommandStore(), "https://gw", "acct", "dev")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            transport = HttpxGatewayTransport(
                "https://gw", token, http, timeout=0.001, commands=pending
            )
            store = ComposerStore(
                target="a",
                host_id="home-mac",
                host_epoch="epoch-7",
                agent_id="a",
                prompt_text=None,
                submit_command=transport.submit_command,
            )
            store.replace_draft("kept question")
            assert await store.send(prompt_only=True) == "failed"
            assert store.draft == "kept question" and store.can_send
            assert not calls and not pending.records

    asyncio.run(scenario())


def test_status_subscription_does_not_retain_closed_terminal_drafts() -> None:
    import gc
    import weakref

    pending = PendingCommands(MemoryCommandStore(), "https://gw", "acct", "dev")

    async def submit(body: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError

    store = ComposerStore(
        target="a",
        host_id="home-mac",
        host_epoch="epoch-7",
        agent_id="a",
        prompt_text=None,
        submit_command=submit,
        subscribe_command=pending.subscribe_outcomes,
    )
    store.replace_draft("private retained draft")
    ref = weakref.ref(store)
    assert len(pending.outcome_listeners) == 1
    del store
    gc.collect()
    assert ref() is None and not pending.outcome_listeners


def test_hosted_submission_timeout_leaves_recoverable_draft_not_an_endless_spinner() -> None:
    async def scenario() -> None:
        pending = PendingCommands(MemoryCommandStore(), "https://gw", "acct", "dev")

        async def token() -> str:
            return "synthetic"

        async def handler(request: httpx.Request) -> httpx.Response:
            await asyncio.Future()
            raise AssertionError

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            transport = HttpxGatewayTransport(
                "https://gw", token, http, timeout=0.001, commands=pending
            )
            store = ComposerStore(
                target="a",
                host_id="home-mac",
                host_epoch="epoch-7",
                agent_id="a",
                prompt_text=None,
                submit_command=transport.submit_command,
            )
            store.replace_draft("kept question")
            assert await store.send(prompt_only=True) == "unknown"
            assert store.draft == "kept question" and store.can_send
            assert len(pending.records) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("first", ["submit", "resolve"])
def test_acceptance_and_absence_fence_have_one_ordered_winner(tmp_path: Path, first: str) -> None:
    async def scenario() -> None:
        journal = CommandJournal(tmp_path / "commands.db")
        backend = JournalBackend(journal)
        backend.snapshots["home-mac"] = json.loads(
            Path("tests/fixtures/gateway/snapshot.json").read_text()
        )
        state = GatewayState(backend=backend, cursor_secret=b"s" * 32)
        access, _ = state.sessions.create(
            "acct",
            frozenset({"sessions.read", "agents.prompt"}),
            frozenset({"home-mac"}),
            state.now_ms(),
            "dev",
        )
        body = command()
        headers = {"Authorization": f"Bearer {access}"}
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(state))
            ) as http:

                def post(kind: str) -> Any:
                    if kind == "submit":
                        return http.post(
                            "https://gw" + paths.host_commands("home-mac"),
                            headers=headers,
                            json=body,
                        )
                    return http.post(
                        "https://gw" + paths.host_command_resolve("home-mac", body["command_id"]),
                        headers=headers,
                        json={
                            "protocol": body["protocol"],
                            "agent_id": body["target"]["agent_id"],
                            "created_ms": state.now_ms(),
                        },
                    )

                async with backend.command_lock.hold():
                    one = asyncio.create_task(post(first))
                    await asyncio.sleep(0.01)
                    two = asyncio.create_task(post("resolve" if first == "submit" else "submit"))
                    await asyncio.sleep(0.01)
                a, b = await asyncio.gather(one, two)
                if first == "submit":
                    assert (a.status_code, b.status_code) == (202, 200)
                    assert a.json()["status"] == b.json()["status"] == "accepted"
                    assert len(backend.drain_outbox("home-mac")) == 1
                else:
                    assert (a.status_code, b.status_code) == (200, 409)
                    assert (
                        a.json()["status"] == "rejected" and b.json()["code"] == "command_conflict"
                    )
                    assert backend.drain_outbox("home-mac") == []
        finally:
            journal.close()
            state.push.close()

    asyncio.run(scenario())


def test_auth_timeout_after_hiding_settles_only_the_original_message() -> None:
    async def scenario() -> None:
        pending = PendingCommands(MemoryCommandStore(), "https://gw", "acct", "dev")
        waiting = asyncio.Event()

        async def token() -> str:
            waiting.set()
            await asyncio.Future()
            raise AssertionError

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("Authentication did not complete")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            transport = HttpxGatewayTransport(
                "https://gw", token, http, timeout=0.01, commands=pending
            )
            store = ComposerStore(
                target="a",
                host_id="home-mac",
                host_epoch="epoch-7",
                agent_id="a",
                prompt_text=None,
                submit_command=transport.submit_command,
            )
            store.replace_draft("kept question")
            sending = asyncio.create_task(store.send(prompt_only=True))
            await waiting.wait()
            store.reconnect("fence_hidden")
            store.replace_draft("newer draft")
            assert await sending == "ignored"
            assert store.messages[0].state.kind == "failed"
            assert store.submission == "failed"
            assert store.draft == "newer draft" and store.can_send
            assert not pending.records

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "age_ms", [25 * 60 * 60 * 1000, TOMBSTONE_MS - 1, TOMBSTONE_MS, TOMBSTONE_MS + 1, -1]
)
def test_absence_resolution_uses_the_dedupe_window(tmp_path: Path, age_ms: int) -> None:
    now = 2 * TOMBSTONE_MS
    journal = CommandJournal(tmp_path / "commands.db", clock=lambda: now)
    try:
        result = journal.resolve_prompt(
            "retained-prompt", "acct", "dev", "home-mac", "a", now - age_ms, now
        )
        if 0 <= age_ms < TOMBSTONE_MS:
            assert result is not None and result["status"] == "rejected"
            assert not journal.mark_dispatched("retained-prompt", now=now)
        else:
            assert result is None and journal.record("retained-prompt") is None
    finally:
        journal.close()


def test_absence_reservation_is_terminal_with_a_zero_clock(tmp_path: Path) -> None:
    journal = CommandJournal(tmp_path / "commands.db", clock=lambda: 0)
    try:
        result = journal.resolve_prompt("zero-time-prompt", "acct", "dev", "home-mac", "a", 0, 0)
        assert result is not None and result["status"] == "rejected"
        assert not journal.mark_dispatched("zero-time-prompt", now=0)
    finally:
        journal.close()


@pytest.mark.parametrize("reason", ["expired-history", "future-clock", "non-durable"])
def test_unresolvable_idle_checks_do_not_retry_or_drain_other_prompts(
    tmp_path: Path, reason: str
) -> None:
    async def scenario() -> None:
        now = int(time.time() * 1000)
        journal = CommandJournal(tmp_path / "commands.db", clock=lambda: now)
        backend = (
            FakeBackend(now_ms=lambda: now)
            if reason == "non-durable"
            else JournalBackend(journal, now_ms=lambda: now)
        )
        backend.snapshots["home-mac"] = json.loads(
            Path("tests/fixtures/gateway/snapshot.json").read_text()
        )
        state = GatewayState(backend=backend, cursor_secret=b"s" * 32, now_ms=lambda: now)
        access, _ = state.sessions.create(
            "acct",
            frozenset({"sessions.read", "agents.prompt"}),
            frozenset({"home-mac"}),
            now,
            "dev",
        )
        created_ms = (
            now - TOMBSTONE_MS
            if reason == "expired-history"
            else now + 5_000
            if reason == "future-clock"
            else now
        )
        pending = PendingCommands(
            MemoryCommandStore(), "https://gw", "acct", "dev", clock=lambda: created_ms
        )
        requests: list[tuple[str, str]] = []
        waits: list[float] = []
        elapsed = [0.0]

        async def requested(request: httpx.Request) -> None:
            requests.append((request.method, request.url.path))

        async def token() -> str:
            return access

        async def sleep(delay: float) -> None:
            elapsed[0] += delay
            waits.append(delay)
            await asyncio.sleep(0)

        async def lost(body: dict[str, Any]) -> dict[str, Any]:
            await pending.begin(body)
            raise httpx.ReadError("Response unavailable")

        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(state)),
                event_hooks={"request": [requested]},
            ) as http:
                transport = HttpxGatewayTransport("https://gw", token, http, commands=pending)
                store = ComposerStore(
                    target="agent-1",
                    host_id="home-mac",
                    host_epoch="epoch-7",
                    agent_id="agent-1",
                    prompt_text=None,
                    submit_command=lost,
                    observe_command=CommandObserver(transport.command_status).observe,
                    initial_status="working",
                    sleep=sleep,
                    monotonic=lambda: elapsed[0],
                )
                store.replace_draft("kept question")
                assert await store.send(prompt_only=True) == "unknown"
                identity = store.last_command_id
                assert identity is not None
                for index, status in enumerate(("idle", "done"), start=1):
                    store.agent_status_did_change(status)
                    async with asyncio.timeout(2):
                        while store._reconcile_tasks:
                            await asyncio.sleep(0)
                    assert (
                        requests
                        == [
                            ("GET", paths.host_command("home-mac", identity)),
                            ("POST", paths.host_command_resolve("home-mac", identity)),
                        ]
                        * index
                    )
                    assert not waits
                    assert store.submission == "unknown" and store.draft == "kept question"
                    assert pending.unresolved(pending.records[("home-mac", identity)])
                    with pytest.raises(RecoveryError):
                        await pending.acknowledge("home-mac", identity)
                    with pytest.raises(RecoveryError):
                        await pending.begin(command("retry"))
                other = command("other", target="agent-2")
                response = await http.post(
                    "https://gw" + paths.host_commands("home-mac"),
                    json=other,
                    headers={"Authorization": f"Bearer {access}"},
                )
                assert response.status_code == 202, response.text
        finally:
            journal.close()
            state.push.close()

    asyncio.run(scenario())
