"""A real gateway holding no record of a prompt that the phone still has pending."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from relaydeck.client.features.commands.observation import CommandObserver
from relaydeck.client.features.commands.recovery import PendingCommands
from relaydeck.client.features.composer.model import ComposerStore
from relaydeck.client.platform.storage.memory_store import MemoryCommandStore
from relaydeck.client.platform.transport.transport import HttpxGatewayTransport
from relaydeck.devtools.fakes.gateway import FakeBackend
from relaydeck.gateway.app.backend import JournalBackend
from relaydeck.gateway.app.bootstrap import create_app
from relaydeck.gateway.app.state import GatewayState
from relaydeck.host.persistence.command_journal import TOMBSTONE_MS, CommandJournal

REASONS = ("expired-history", "future-clock", "non-durable")


@dataclass
class Scene:
    pending: PendingCommands
    transport: HttpxGatewayTransport
    http: httpx.AsyncClient
    access: str
    store: ComposerStore
    requests: list[tuple[str, str]] = field(default_factory=list)
    submitted: list[dict[str, Any]] = field(default_factory=list)

    async def post(self, body: dict[str, Any]) -> httpx.Response:
        return await self.http.post(
            f"https://gw/v1/hosts/{body['host_id']}/commands",
            json=body,
            headers={"Authorization": f"Bearer {self.access}"},
        )


@asynccontextmanager
async def stuck_prompt(tmp_path: Path, reason: str) -> AsyncIterator[Scene]:
    """A sent prompt whose outcome is unknown and that the gateway cannot settle."""
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
        "acct", frozenset({"sessions.read", "agents.prompt"}), frozenset({"home-mac"}), now, "dev"
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
    submitted: list[dict[str, Any]] = []

    async def seen(request: httpx.Request) -> None:
        requests.append((request.method, request.url.path))

    async def token() -> str:
        return access

    async def lost(body: dict[str, Any]) -> dict[str, Any]:
        await pending.begin(body)
        submitted.append(body)
        raise httpx.ReadError("Response unavailable")

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(state)),
            event_hooks={"request": [seen]},
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
                subscribe_command=pending.subscribe_outcomes,
                initial_status="working",
            )
            store.replace_draft("kept question")
            assert await store.send(prompt_only=True) == "unknown"
            scene = Scene(pending, transport, http, access, store, requests, submitted)
            yield scene
    finally:
        journal.close()
        state.push.close()
        await asyncio.sleep(0)
