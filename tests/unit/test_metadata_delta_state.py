"""Snapshot boundaries, ordered replay, and bounded client delta history."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from time import perf_counter

import pytest

from relaydeck.client.features.sync.inventory_updates import MAX_UPDATES, AgentUpdate, apply_rows
from relaydeck.client.ports.transport import SseFrame
from relaydeck.devtools.fakes.gateway import FakeBackend
from relaydeck.gateway.app.backend import JournalBackend
from relaydeck.gateway.cursors import decode_cursor
from relaydeck.gateway.features.metadata.events import MAX_EVENT_LOG_PER_HOST
from relaydeck.gateway.ports import BackendError
from relaydeck.host.persistence.command_journal import CommandJournal
from tests.security.helpers import NOW, SECRET, snapshot_doc
from tests.support.sync.delta_inventory import HOST, KEY, InventoryHarness


@pytest.mark.parametrize("durable", [False, True])
def test_snapshot_boundary_and_prompt_revision_remain_current_after_event_retention(
    durable: bool, tmp_path: Path
) -> None:
    journal = CommandJournal(tmp_path / "journal.db")
    backend = JournalBackend(journal) if durable else FakeBackend()
    snapshot = snapshot_doc()
    assert backend.ingest_snapshot(HOST, snapshot, NOW, SECRET).get("ok")
    untouched = backend.snapshots[HOST]["tabs"]
    try:
        for n in range(MAX_EVENT_LOG_PER_HOST + 2):
            result = backend.ingest_event(
                HOST,
                {
                    "event_id": f"delta-{n}",
                    "host_epoch": "epoch-7",
                    "type": "agent.upserted",
                    "resource_id": "term-1",
                    "resource_revision": 11 + n,
                    "observed_at": "2026-10-04T10:00:00Z",
                    "payload": {"status": "blocked", "pane_id": "w1:p1", "status_changed": True},
                },
                NOW,
                SECRET,
            )
            assert result.get("ok")
        current = backend.snapshots[HOST]
        assert current["agents"][0]["revision"] == 11 + n
        assert current["panes"][0]["agent_status"] == "blocked"
        assert current["tabs"] is untouched
        assert len(backend.event_logs[HOST]) == MAX_EVENT_LOG_PER_HOST
        assert decode_cursor(SECRET, current["cursor"])["sequence"] == n + 1
        assert (
            backend.ingest_event(
                HOST,
                {
                    **result["ok"],
                    "sequence": 0,
                },
                NOW,
                SECRET,
            ).get("duplicate")
            is True
        )
        if isinstance(backend, JournalBackend):
            command = {
                "kind": "agent.prompt",
                "target": {"agent_id": "term-1"},
                "expected_revision": 11 + n,
            }
            backend._enforce_expected_revision(command, HOST)
            with pytest.raises(BackendError) as exc:
                backend._enforce_expected_revision({**command, "expected_revision": 10}, HOST)
            assert exc.value.code == "stale_revision"
    finally:
        journal.close()


def test_equal_revision_status_transitions_apply_in_order_but_replay_cannot_roll_back() -> None:
    harness = InventoryHarness(1)
    first = harness.event(1)
    second = harness.event(2, status="working", resource_revision=44)
    # The real source can read one current revision for several already-buffered status events.
    assert harness.controller.state.inventory.agents[KEY].status.value == "working"
    version = harness.controller.inventory_updates.version
    harness.controller._apply_frame(SseFrame("metadata", json.dumps(first)))
    assert harness.controller.state.inventory.agents[KEY].status.value == "working"
    updates = harness.controller.inventory_updates.since(version, 0, 2)
    assert updates is not None and len(updates) == 2
    assert updates[1].event.resource_revision == second["resource_revision"]
    harness.state.push.close()


def test_delta_history_is_bounded_and_missing_prefix_forces_refresh() -> None:
    harness = InventoryHarness(1)
    version = harness.controller.inventory_updates.version
    for n in range(1, MAX_UPDATES + 3):
        harness.event(n)
    updates = harness.controller.inventory_updates
    assert len(updates.history) == MAX_UPDATES
    assert updates.since(version, 0, MAX_UPDATES + 2) is None
    assert updates.since(version, 2, MAX_UPDATES + 2) is not None
    harness.state.push.close()


@pytest.mark.parametrize("count", [1, 50, 4096])
def test_loaded_row_delta_has_small_cpu_budget_and_preserves_other_objects(count: int) -> None:
    harness = InventoryHarness(count)
    before = harness.controller.state
    event_doc = harness.event(1)
    update = harness.controller.inventory_updates.history[-1]
    rows = {
        key: {
            "host_epoch": "epoch-7",
            "revision": agent.revision,
            "status": agent.status.value,
            "terminal_id": agent.terminal_id,
            "name": agent.name,
        }
        for key, agent in before.inventory.agents.items()
    }
    held = dict(rows)
    started = perf_counter()
    assert apply_rows(rows, (AgentUpdate(update.event, True),))
    assert perf_counter() - started < 0.05
    assert all(row is rows[key] for key, row in held.items() if key != KEY)
    assert rows[KEY]["revision"] == event_doc["resource_revision"]
    obsolete = dict(rows)
    bad = AgentUpdate(replace(update.event, host_epoch="different"), True)
    assert not apply_rows(rows, (bad,)) and rows == obsolete
    harness.state.push.close()
