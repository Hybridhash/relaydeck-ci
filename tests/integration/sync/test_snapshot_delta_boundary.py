"""Snapshot cursor advancement must preserve earlier seed status transitions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from relaydeck.client.app.state.sync import SyncController
from relaydeck.client.platform.transport.transport import HttpxGatewayTransport
from relaydeck.client.ports.transport import SseFrame
from relaydeck.connector.herdr import HerdrAdapter, HerdrProtocolError
from relaydeck.connector.link import ConnectorLink, LinkConfig
from relaydeck.connector.source import HerdrSource
from relaydeck.devtools.fakes.gateway import FakeBackend
from relaydeck.domain.models import ConnectionState
from relaydeck.gateway.app.backend import JournalBackend
from relaydeck.gateway.app.bootstrap import create_app
from relaydeck.gateway.app.state import GatewayState
from relaydeck.gateway.cursors import decode_cursor
from relaydeck.host.persistence.command_journal import CommandJournal
from tests.security.helpers import NOW, SECRET, mint_token, snapshot_doc
from tests.support.host_harness import run
from tests.support.paths import TESTS_DIR

HOST = "home-mac"


@pytest.mark.parametrize("durable", [False, True])
def test_legacy_seed_then_delta_survives_a_page_read_and_a_fresh_stream(
    durable: bool,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        journal = CommandJournal(tmp_path / "journal.db")
        backend = JournalBackend(journal) if durable else FakeBackend()
        state = GatewayState(backend=backend, cursor_secret=SECRET, now_ms=lambda: NOW)
        snapshot = snapshot_doc()
        snapshot["agents"].append(
            {
                **snapshot["agents"][0],
                "terminal_id": "term-2",
                "pane_id": "w1:p2",
                "tab_id": "w1:t2",
                "status": "idle",
            }
        )
        assert backend.ingest_snapshot(HOST, snapshot, NOW, SECRET).get("ok")

        def ingest(
            event_id: str, kind: str, resource: str, payload: dict[str, Any], revision: int = 11
        ) -> dict[str, Any]:
            result = backend.ingest_event(
                HOST,
                {
                    "event_id": event_id,
                    "host_epoch": "epoch-7",
                    "type": kind,
                    "resource_id": resource,
                    "resource_revision": revision,
                    "payload": payload,
                },
                NOW,
                SECRET,
            )
            assert "ok" in result
            stored: dict[str, Any] = result["ok"]
            return stored

        ingest("seed", "pane.upserted", "w1:p2", {"agent_status": "working"})
        seeded = backend.snapshot(HOST)
        assert seeded["agents"][1]["status"] == "working"
        assert decode_cursor(SECRET, seeded["cursor"])["sequence"] == 0
        ingest(
            "command",
            "command.updated",
            "cmd-1",
            {
                "status": "succeeded",
                "target": {"agent_id": "term-1"},
                "kind": "agent.prompt",
            },
        )
        ingest("delta", "agent.upserted", "term-1", {"status": "blocked", "status_changed": True})
        token = mint_token(state)

        async def access() -> str:
            return token

        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(state)), base_url="http://gw"
            ) as http:
                transport = HttpxGatewayTransport("http://gw", access, http)
                page = await transport.agents(HOST)
                assert {a["terminal_id"]: a["status"] for a in page["agents"]} == {
                    "term-1": "blocked",
                    "term-2": "working",
                }
                fresh = SyncController(transport)
                doc = await transport.snapshot(HOST)
                fresh.accept_snapshot(HOST, doc)
                fresh._set(
                    replace(
                        fresh.state,
                        connection=replace(
                            fresh.state.connection,
                            host_id=HOST,
                            state=ConnectionState.LIVE,
                        ),
                    )
                )
                sequence = decode_cursor(SECRET, doc["cursor"])["sequence"]
                assert sequence == 3 and not backend.events_after(HOST, sequence)
                fresh._accept_stream_open(
                    SseFrame(
                        "stream_open",
                        json.dumps(
                            {
                                "host_id": HOST,
                                "cursor": doc["cursor"],
                                "sequence": sequence,
                            }
                        ),
                    ),
                    HOST,
                    doc["cursor"],
                )
                assert fresh.state.inventory.agents[f"{HOST}:term-2"].status.value == "working"
                assert not fresh.state.commands  # fresh snapshots do not replay command history
                assert any(e["type"] == "command.updated" for e in backend.events_after(HOST, 0))

                stream = cast(AsyncGenerator[dict[str, Any] | None], backend.follow(HOST, sequence))
                pending = asyncio.ensure_future(anext(stream))
                try:
                    await asyncio.sleep(0)
                    assert not pending.done()
                    event = ingest(
                        "next-delta",
                        "agent.upserted",
                        "term-1",
                        {
                            "status": "done",
                            "status_changed": True,
                        },
                        revision=12,
                    )
                    received = await asyncio.wait_for(pending, 1)
                    assert received == event
                    fresh._apply_frame(SseFrame("metadata", json.dumps(received)))
                    assert fresh.state.connection.last_sequence == 4
                    assert fresh.state.inventory.agents[f"{HOST}:term-1"].status.value == "done"
                    assert fresh.state.inventory.agents[f"{HOST}:term-2"].status.value == "working"
                finally:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                    await stream.aclose()
        finally:
            state.push.close()
            journal.close()

    run(scenario())


@pytest.mark.parametrize("status", [None, [], {}, "invalid-status"])
def test_invalid_legacy_status_cannot_change_the_snapshot(status: Any) -> None:
    backend = FakeBackend()
    assert backend.ingest_snapshot(HOST, snapshot_doc(), NOW, SECRET).get("ok")
    held = backend.snapshot(HOST)
    result = backend.ingest_event(
        HOST,
        {
            "event_id": "invalid-legacy-status",
            "host_epoch": "epoch-7",
            "type": "pane.upserted",
            "resource_id": "w1:p1",
            "payload": {"agent_status": status},
        },
        NOW,
        SECRET,
    )
    assert "ok" in result
    assert backend.snapshot(HOST) is held


@pytest.mark.parametrize("mode", ["status", "structural", "unknown", "failed", "mixed"])
def test_bootstrap_seeds_use_the_live_metadata_classification_path(
    mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        fixtures = TESTS_DIR / "fixtures/upstream"
        raw: dict[str, Any] = json.loads((fixtures / "session_snapshot.json").read_text())[
            "result"
        ]["snapshot"]
        agents: list[dict[str, Any]] = json.loads((fixtures / "agent_list.json").read_text())[
            "result"
        ]["agents"]
        second = agents[1]
        agents[1] = {**second, "agent_status": "idle"}
        adapter = HerdrAdapter(socket_path=str(tmp_path / "unused.sock"))
        calls: list[str] = []

        async def get(target: str) -> dict[str, Any]:
            calls.append("get")
            if mode == "failed":
                raise HerdrProtocolError("bad_result", "synthetic lookup failure")
            agent = next(a for a in agents if a["terminal_id"] == target)
            return {
                "type": "agent_info",
                "agent": {
                    **agent,
                    "agent_status": "working",
                    "revision": agent["revision"] + 1,
                },
            }

        async def snapshot() -> dict[str, Any]:
            calls.append("snapshot")
            return {"snapshot": raw}

        async def listed() -> list[dict[str, Any]]:
            calls.append("list")
            return agents

        monkeypatch.setattr(adapter, "agent_get", get)
        monkeypatch.setattr(adapter, "session_snapshot", snapshot)
        monkeypatch.setattr(adapter, "agent_list", listed)
        source = HerdrSource(adapter)
        source._snapshot, source._agents = raw, agents
        status = {
            "event_id": "status-seed",
            "host_epoch": source.epoch,
            "type": "pane.upserted",
            "resource_id": second["pane_id"],
            "payload": {"agent_status": "working"},
        }
        structural = {
            **status,
            "event_id": "structural-seed",
            "type": "tab.upserted",
            "resource_id": second["tab_id"],
            "payload": {"label": "Fresh tab"},
        }
        seeds = [structural] if mode == "structural" else [status]
        if mode == "unknown":
            seeds = [{**status, "resource_id": "unknown-pane"}]
        elif mode == "mixed":
            seeds.append(structural)
        source._seeds = seeds
        backend = FakeBackend()
        frames: list[dict[str, Any]] = []

        class Tunnel:
            async def recv_text(self) -> str:
                raise AssertionError("initial publication does not receive")

            async def send_text(self, text: str) -> None:
                doc = json.loads(text)
                frames.append(doc)
                result = (
                    backend.ingest_snapshot(HOST, doc["snapshot"], NOW, SECRET)
                    if doc["kind"] == "snapshot"
                    else backend.ingest_event(HOST, doc["event"], NOW, SECRET)
                )
                assert result.get("ok")

            async def close(self) -> None:
                pass

        link = ConnectorLink(
            LinkConfig(
                "http://localhost",
                HOST,
                "synthetic",
                str(tmp_path / "connector.db"),
            ),
            source,
        )
        try:
            await link._initial_push(Tunnel())
            events = [f["event"] for f in frames if f["kind"] == "event"]
            assert len(events) == len(seeds) and source.drain_seeds() == []
            if mode == "status":
                assert calls == ["get"]
                assert len(frames) == 2 and events[0]["type"] == "agent.upserted"
                assert events[0]["payload"]["status_changed"] is True
                assert events[0]["resource_revision"] == second["revision"] + 1
                assert backend.snapshot(HOST)["agents"][1]["status"] == "working"
            else:
                assert calls == (["get"] if mode == "failed" else []) + ["snapshot", "list"]
                assert [f["kind"] for f in frames[:2]] == ["snapshot", "snapshot"]
                assert events == seeds
        finally:
            await source.close()
            await link.close()

    run(scenario())
