"""Missing-prompt fences require host and prompt grants and never cross ownership."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from relaydeck.contracts import paths
from relaydeck.gateway.app.backend import JournalBackend
from relaydeck.gateway.app.bootstrap import create_app
from relaydeck.gateway.app.state import GatewayState
from relaydeck.host.persistence.command_journal import CommandJournal


@pytest.mark.parametrize(
    "access", ["anonymous", "read-only", "foreign-host", "foreign-account", "wrong-target"]
)
def test_missing_prompt_resolution_is_authorized_and_identity_bound(
    tmp_path: Path, access: str
) -> None:
    async def scenario() -> None:
        journal = CommandJournal(tmp_path / "journal.db")
        state = GatewayState(backend=JournalBackend(journal), cursor_secret=b"s" * 32)
        account = "other" if access == "foreign-account" else "acct"
        caps = {"sessions.read", "agents.prompt"}
        if access == "read-only":
            caps.remove("agents.prompt")
        hosts = {"other-host"} if access == "foreign-host" else {"home-mac"}
        token, _ = state.sessions.create(
            account, frozenset(caps), frozenset(hosts), state.now_ms(), "dev"
        )
        if access in {"foreign-account", "wrong-target"}:
            journal.submit(
                command_id="scoped-prompt",
                principal="acct",
                host_id="home-mac",
                kind="agent.prompt",
                target={"agent_id": "agent-1"},
                payload={"text": "private"},
            )
            journal.mark_dispatched("scoped-prompt")
            journal.mark_unknown("scoped-prompt")
        headers = {} if access == "anonymous" else {"Authorization": f"Bearer {token}"}
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(state))
            ) as http:
                response = await http.post(
                    "https://gw" + paths.host_command_resolve("home-mac", "scoped-prompt"),
                    headers=headers,
                    json={
                        "protocol": "relaydeck.gateway.v1",
                        "agent_id": "wrong" if access == "wrong-target" else "agent-1",
                        "created_ms": state.now_ms(),
                    },
                )
                assert response.status_code in {400, 401, 403, 404}
                assert response.json()["code"] in {
                    "unauthenticated",
                    "unsupported_capability",
                    "not_found",
                }
                row = journal.record("scoped-prompt")
                if access in {"foreign-account", "wrong-target"}:
                    assert row is not None and row["status"] == "unknown"
                else:
                    assert row is None
        finally:
            journal.close()
            state.push.close()

    asyncio.run(scenario())
