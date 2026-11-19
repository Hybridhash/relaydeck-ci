"""One-shot staging waits for proven drafts and preserves conflicting payloads."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from relaydeck.client.features.ask_agent.handoff import AskHandoff, Identity, PendingAsk
from relaydeck.client.features.ask_agent.model import AskScope
from relaydeck.client.features.changes.reading_bookmark import ReadingBookmark
from relaydeck.client.features.changes.state import ChangesModel
from relaydeck.client.features.composer.model import ComposerStore
from relaydeck.client.features.composer.persist import draft_key
from relaydeck.client.features.composer.question import install
from relaydeck.client.ports.commands import CommandRefused
from tests.support.fakes import FakeCredentialStore
from tests.support.scripted_sessions import make_scripted_sessions


def request() -> PendingAsk:
    return PendingAsk(
        Identity("account", object(), "host:term", "epoch"),
        AskScope("branch", "working", "snapshot", "a" * 40),
        "Question?",
        ReadingBookmark.capture(ChangesModel()),
        "Session",
    )


@pytest.mark.parametrize("site", ["initial", "submit", "observe", "unknown"])
def test_prompt_only_blocked_races_never_attach(site: str) -> None:
    async def scenario() -> None:
        sent: list[Any] = []
        commands: list[Any] = []
        reads = 0

        async def submit(command: dict[str, Any]) -> dict[str, Any]:
            commands.append(command)
            if site == "submit":
                raise CommandRefused("agent_blocked")
            return {"command_id": command["command_id"]}

        async def observe(host: str, command: str) -> dict[str, Any]:
            nonlocal reads
            reads += 1
            if site == "unknown" and reads == 1:
                raise TimeoutError
            raise CommandRefused("agent_blocked")

        store = ComposerStore(
            target="term",
            submit_command=submit,
            prompt_text=None,
            host_id="host",
            host_epoch="epoch",
            agent_id="term",
            initial_status="blocked" if site == "initial" else "idle",
            observe_command=observe,
            reconcile_interval_s=0.001,
            reconcile_budget_s=0.02,
        )

        async def writer(data: bytes) -> None:
            sent.append(data)

        store.bind_writer(lambda: 1, writer)
        assert install(store, "Question?")
        await store.send()
        if site == "unknown":
            store._start_reconcile(store.messages[-1], "reconnect_flow")
            for _ in range(20):
                await asyncio.sleep(0.005)
        assert sent == [] and store.pending_blocked is None
        assert len(commands) == (0 if site == "initial" else 1)
        assert store.draft == "Question?"
        assert store.messages[-1].state.kind == "failed"

    asyncio.run(scenario())


@pytest.mark.parametrize("draft", ["existing", " ", "\n"])
def test_conflict_and_replacement_without_send(draft: str) -> None:
    async def scenario() -> None:
        env = make_scripted_sessions()
        session = env.factory.make_agent(
            host_id="host", host_epoch="epoch", agent_id="term", target="term"
        )
        session._draft_store = FakeCredentialStore()
        await session.prepare_draft()
        session.on_draft_change(draft)
        before = session.store.export_draft()
        handoff = AskHandoff()
        pending = request()
        handoff.begin(pending)
        assert handoff.observe(pending.identity, terminal=True, changes=False)
        await handoff.drain(session)
        assert session.store.export_draft() == before
        assert pending.state == "draft_conflict" and not env.submitted
        await handoff.drain(session, replace=True)
        assert session.store.draft == "Question?" and session.store.markers == ()
        assert not env.submitted and pending.state == "staged"
        session.set_agent_status("blocked")
        await session.on_send()
        assert not env.submitted and not env.ops.attaches
        await session.dispose()

    asyncio.run(scenario())


def test_delayed_restore_native_edit_warm_reopen_and_one_shot() -> None:
    async def scenario() -> None:
        gate = asyncio.Event()

        class Delayed(FakeCredentialStore):
            async def get(self, key: str) -> str | None:
                await gate.wait()
                return await super().get(key)

        storage = Delayed()
        await storage.set(
            draft_key("host:term"), json.dumps({"draft": "saved", "markers": [], "pictures": []})
        )
        env = make_scripted_sessions()
        session = env.factory.make_agent(
            host_id="host", host_epoch="epoch", agent_id="term", target="term"
        )
        session._draft_store = storage
        session.begin_draft_preparation()
        assert session.question_readiness and session.draft_loading
        task = asyncio.create_task(session.prepare_draft())
        await asyncio.sleep(0)
        assert session.stage_question("Question?")
        gate.set()
        await task
        assert session.store.draft == "saved"
        session.on_draft_change("")
        native = SimpleNamespace(value="native edit", can_request_focus=True, disabled=False)
        session.composer_callbacks()["on_draft_change"].remember_field(native)
        assert session.stage_question("Question?") == "draft_conflict"
        assert session.store.draft == "native edit"
        await session.prepare_draft()
        assert session.store.draft == "native edit"
        session.on_draft_change("")
        native.value = ""
        handoff, pending = AskHandoff(), request()
        handoff.begin(pending)
        handoff.observe(pending.identity, terminal=True, changes=False)
        await asyncio.gather(handoff.drain(session), handoff.drain(session))
        await handoff.drain(session)
        assert len(env.submitted) == len(env.prompted) == 1
        assert pending.state == "settled" and not env.ops.attaches
        await session.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("available", [False, True])
def test_unverifiable_restore_is_not_empty(available: bool) -> None:
    async def scenario() -> None:
        env = make_scripted_sessions()
        session = env.factory.make_agent(
            host_id="host", host_epoch="epoch", agent_id="term", target="term"
        )
        storage = FakeCredentialStore(available=available)
        if available:
            await storage.set(draft_key("host:term"), "corrupt")
        session._draft_store = storage
        await session.prepare_draft()
        assert session.question_readiness and session.stage_question("Question?")
        assert not env.submitted
        await session.dispose()

    asyncio.run(scenario())


def test_cancellation_and_sensitive_repr() -> None:
    handoff, pending = AskHandoff(), request()
    handoff.begin(pending)
    assert not handoff.observe(pending.identity, terminal=False, changes=True)
    assert handoff.pending is pending
    other = Identity(pending.identity.account, pending.identity.transport, "host:other", "epoch")
    assert not handoff.observe(other, terminal=True, changes=False)
    assert handoff.pending is None
    assert "Question?" not in repr(pending) and "host:term" not in repr(pending.identity)


@pytest.mark.parametrize("payload", ["picture", "marker", "resolved", "unknown", "review"])
def test_nontext_payload_and_unresolved_delivery_are_occupied(payload: str) -> None:
    from relaydeck.client.features.composer.question import occupied
    from relaydeck.client.models.pictures import Picture

    async def scenario() -> None:
        env = make_scripted_sessions()
        session = env.factory.make_agent(
            host_id="host", host_epoch="epoch", agent_id="term", target="term"
        )
        session._draft_store = FakeCredentialStore()
        await session.prepare_draft()
        store = session.store
        if payload == "picture":
            store.add_picture(Picture("a" * 32, "/synthetic/picture.png", 1000))
        elif payload in ("marker", "resolved"):
            store.import_draft(
                {"draft": "", "markers": [{"token": "[item]", "resolved": payload == "resolved"}]}
            )
        elif payload == "unknown":
            store._submission = "unknown"
        else:
            store.replace_draft("first\nsecond")
            store.agent_status_did_change("blocked")

            async def writer(data: bytes) -> None:
                return None

            store.bind_writer(lambda: 1, writer)
            await store.send()
            store.agent_status_did_change("idle")
        assert occupied(store)
        before = store.export_draft()
        result = session.stage_question("Question?")
        assert result and store.export_draft() == before and not env.submitted
        result = session.stage_question("Question?", replace=True)
        if payload in ("review", "unknown"):
            assert result and store.export_draft() == before
        else:
            assert not result and not store.pictures and not store.markers
        await session.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["hidden", "paused", "disposed", "revoked", "ime"])
def test_inactive_session_never_stages_or_sends(state: str) -> None:
    async def scenario() -> None:
        env = make_scripted_sessions()
        session = env.factory.make_agent(
            host_id="host", host_epoch="epoch", agent_id="term", target="term"
        )
        session._draft_store = FakeCredentialStore()
        await session.prepare_draft()
        if state == "hidden":
            session.set_foreground(False)
        elif state == "paused":
            session._paused = True
        elif state == "disposed":
            await session.dispose()
        elif state == "revoked":
            session._on_auth_revoked()
        else:
            session._sessions.ime.begin()
        before = session.store.export_draft()
        assert session.question_readiness and session.stage_question("Question?")
        assert session.store.export_draft() == before and not env.submitted
        await session.dispose()

    asyncio.run(scenario())


def test_takeover_during_question_observation_reconciles_without_resending() -> None:
    from relaydeck.client.platform.session_drafts import session_drafts

    async def scenario() -> None:
        observed, release = asyncio.Event(), asyncio.Event()

        async def observe(host: str, command: str) -> dict[str, str]:
            observed.set()
            await release.wait()
            return {"command_id": command}

        env = make_scripted_sessions()
        env.factory.draft_store = session_drafts(None)
        env.factory.observe_command = observe
        session = env.factory.make_agent(
            host_id="host", host_epoch="epoch", agent_id="term", target="term"
        )
        session.open()
        try:
            await session.prepare_draft()
            assert not session.stage_question("Question?")
            sending = asyncio.create_task(session.on_send(prompt_only=True))
            await observed.wait()
            await session.auto_take_control()
            release.set()
            await sending
            async with asyncio.timeout(2):
                while session.store.messages[-1].state.kind != "delivered":
                    await asyncio.sleep(0.01)
            assert len(env.submitted) == 1 and not env.prompted
            assert session.store.draft == "" and len(env.ops.attaches) == 1
            assert all(
                kind != "bytes" for _, _, socket in env.streams.opened for kind, _ in socket.sent
            )
        finally:
            release.set()
            await session.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ("delivered-via-prompt", "settled"),
        ("unknown", "uncertain"),
        ("superseded", "uncertain"),
        ("ignored", "refused"),
        ("failed", "refused"),
    ],
)
def test_a_send_replaced_after_submission_is_uncertain_not_refused(status: str, state: str) -> None:
    class Session:
        question_readiness = ""

        def stage_question(self, prompt: str, *, replace: bool = False) -> str:
            return ""

        async def on_send(self, *, prompt_only: bool = False) -> str:
            assert prompt_only
            return status

    async def scenario() -> None:
        handoff = AskHandoff()
        pending = request()
        assert handoff.begin(pending)
        assert handoff.observe(pending.identity, terminal=True, changes=False)
        await handoff.drain(Session())
        assert pending.state == state
        assert (pending.reason == "Could not send the question.") == (state == "refused")

    asyncio.run(scenario())
