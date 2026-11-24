"""Web session memory is authoritative without replacing failed secure storage."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import flet as ft
import httpx
import pytest

from relaydeck.client.app.resources import build_resources
from relaydeck.client.features.composer.persist import draft_key
from relaydeck.client.platform.session_drafts import SessionDrafts, session_drafts
from tests.support.fakes import FakeCredentialStore
from tests.support.host_harness import render_component, run
from tests.support.scripted_sessions import make_scripted_sessions


def test_web_composition_prepares_and_clears_its_owned_draft_memory() -> None:
    async def scenario() -> None:
        app = await render_component(lambda: ft.Text("Session"))

        async def token() -> str:
            return "synthetic"

        async with httpx.AsyncClient() as client:
            resources = build_resources(
                app.page,
                base_url="https://synthetic.example",
                get_token=token,
                host_id="host",
                account="account",
                device_id="device",
                client=client,
            )
            session: Any = resources.props["session_factory"].make_agent(
                host_id="host",
                host_epoch="epoch",
                agent_id="term",
                target="term",
            )
            try:
                await session.prepare_draft()
                assert not session.question_readiness
                memory = resources.draft_memory
                assert memory is not None
                await memory.set("draft", "synthetic")
            finally:
                await session.dispose()
                await resources.close()
            assert await memory.get("draft") is None

    run(scenario())


def test_session_memory_is_separate_and_restored_drafts_block_questions() -> None:
    async def scenario() -> None:
        memory = session_drafts(None)
        assert isinstance(memory, SessionDrafts)
        await memory.set(draft_key("host:term"), json.dumps({"draft": "kept draft", "markers": []}))
        assert await session_drafts(None).get(draft_key("host:term")) is None
        env = make_scripted_sessions()
        env.factory.draft_store = memory
        session = env.factory.make_agent(
            host_id="host", host_epoch="epoch", agent_id="term", target="term"
        )
        try:
            await session.prepare_draft()
            assert session.stage_question("Question?") == "draft_conflict"
            assert session.store.draft == "kept draft" and not env.submitted
        finally:
            await session.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("available", [False, True])
def test_configured_unavailable_or_corrupt_storage_never_falls_back(available: bool) -> None:
    async def scenario() -> None:
        store = FakeCredentialStore(available=available)
        if available:
            await store.set(draft_key("host:term"), "corrupt")
        assert session_drafts(store) is store
        env = make_scripted_sessions()
        env.factory.draft_store = session_drafts(store)
        session = env.factory.make_agent(
            host_id="host", host_epoch="epoch", agent_id="term", target="term"
        )
        try:
            await session.prepare_draft()
            assert session.question_readiness and session.stage_question("Question?")
            assert not env.submitted
        finally:
            await session.dispose()

    asyncio.run(scenario())
