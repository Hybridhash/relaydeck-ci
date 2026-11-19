"""Real root routing and retained-session delivery with a synthetic Git transport."""

from __future__ import annotations

import asyncio
import json

import pytest

from relaydeck.client.app.navigation import RouteBridge
from relaydeck.client.features.composer.persist import draft_key
from relaydeck.client.models.preferences import PREFS_KEY
from tests.support.fakes import FakeCredentialStore
from tests.support.host_harness import run
from tests.support.ui.navigation import _state
from tests.support.ui.packaged import Preferences
from tests.support.ui.retained_root import _render
from tests.ui.git.harness import Transport, click, present, until


def test_cold_restore_conflict_replace_manual_and_retained_identity() -> None:
    async def scenario() -> None:
        gate = asyncio.Event()

        class Storage(FakeCredentialStore):
            async def get(self, key: str) -> str | None:
                await gate.wait()
                return await super().get(key)

        storage = Storage()
        key, agent = next(iter(_state().inventory.agents.items()))
        await storage.set(
            draft_key(agent.host_id + ":" + agent.terminal_id),
            json.dumps({"draft": "kept question", "markers": [], "pictures": []}),
        )
        bridge = RouteBridge(route=f"/agents/{key}/changes", width=1440, height=1000)
        app, _, sessions, pool, _ = await _render(
            bridge=bridge,
            transport=Transport(),
            draft_store=storage,
            preferences=Preferences({PREFS_KEY: json.dumps({"auto_takeover": False})}),
        )
        try:
            await app.wait_for("changes-ask")
            await until(lambda: not app.find("changes-ask").disabled)
            await click(app, "changes-ask")
            await click(app, "changes-ask-send")
            await until(lambda: len(sessions.factory.created) == 1)
            session = sessions.factory.created[0]
            await app.wait_for("changes-origin")
            assert session.draft_loading and not sessions.submitted
            assert (
                app.find("composer-draft").disabled
                or not app.find("composer-draft").can_request_focus
            )
            gate.set()
            await until(lambda: app.find("changes-origin-label").value == "Draft in the way")
            assert session.store.draft == "kept question"
            engine = session.current_term
            await click(app, "changes-replace-draft")
            await until(lambda: session.store.draft.startswith("About the working changes"))
            assert not sessions.submitted and not sessions.ops.attaches
            await session.on_send()
            await app.settle()
            assert len(sessions.submitted) == 1
            assert sessions.submitted[0]["target"]["agent_id"] == agent.terminal_id
            assert session.current_term is engine
            await click(app, "changes-origin-dismiss")
            await app.settle()
            assert not present(app, "changes-origin") and session.current_term is engine
            assert session._draft_preparation.native is app.find("composer-draft"), (
                "native registration differs"
            )
            app.session.apply_patch(app.find("composer-draft")._i, {"value": "native draft"})
            assert session._draft_preparation.native.value == "native draft", (
                "native registration missed edit"
            )
            bridge.set_route(f"/agents/{key}/changes")
            await app.wait_for("changes-files")
            assert session._draft_preparation.native.value == "native draft", (
                "native edit lost on route"
            )
            await click(app, "changes-ask")
            await click(app, "changes-ask-send")
            await until(lambda: app.find("changes-origin-label").value == "Draft in the way")
            assert session.store.draft == "native draft" and len(sessions.submitted) == 1
            await click(app, "changes-origin-dismiss")
            bridge.set_route(f"/agents/{key}/changes")
            await app.wait_for("changes-files")
            bridge.set_route(f"/agents/{key}")
            await app.settle()
            assert not present(app, "changes-origin") and len(sessions.submitted) == 1
        finally:
            gate.set()
            await pool.shutdown()

    run(scenario())


def test_cold_empty_autosend_once_back_restores_hunk() -> None:
    async def scenario() -> None:
        key, agent = next(iter(_state().inventory.agents.items()))
        bridge = RouteBridge(route=f"/agents/{key}/changes", width=1440, height=1000)
        app, _, sessions, pool, _ = await _render(
            bridge=bridge,
            transport=Transport(),
            draft_store=FakeCredentialStore(),
            preferences=Preferences({PREFS_KEY: json.dumps({"auto_takeover": False})}),
        )
        try:
            await app.wait_for("changes-file-0")
            await click(app, "changes-file-0")
            await app.wait_for("diff-hunk-0-1")
            await click(app, "diff-hunk-0-1")
            await click(app, "diff-ask")
            await click(app, "changes-ask-send")
            await until(lambda: len(sessions.submitted) == 1)
            session = sessions.factory.created[0]
            assert not sessions.ops.attaches
            assert sessions.submitted[0]["target"]["agent_id"] == agent.terminal_id
            for width in (390, 1440):
                bridge.set_width(width)
                await app.settle()
            assert len(sessions.submitted) == 1
            await click(app, "changes-origin-back")
            await app.wait_for("diff-ask")
            await click(app, "diff-ask")
            assert "hunk" in app.find("changes-ask-title").value
            assert len(sessions.submitted) == 1 and session.store.draft == ""
        finally:
            await pool.shutdown()

    run(scenario())


def test_native_question_survives_parent_render_and_send_reads_latest_edit() -> None:
    async def scenario() -> None:
        key = next(iter(_state().inventory.agents))
        bridge = RouteBridge(route=f"/agents/{key}/changes", width=1440, height=1000)
        app, _, sessions, pool, _ = await _render(
            bridge=bridge,
            transport=Transport(),
            draft_store=FakeCredentialStore(),
            preferences=Preferences({PREFS_KEY: json.dumps({"auto_takeover": False})}),
        )
        try:
            await app.wait_for("changes-ask")
            await until(lambda: not app.find("changes-ask").disabled)
            await click(app, "changes-ask")
            app.session.apply_patch(
                app.find("changes-ask-question")._i,
                {"value": "Please explain the validation edge case."},
            )
            bridge.set_width(1300)
            await app.settle()
            assert (
                app.find("changes-ask-question").value == "Please explain the validation edge case."
            )
            app.session.apply_patch(
                app.find("changes-ask-question")._i, {"value": "Please explain the boundary case."}
            )
            await click(app, "changes-ask-send")
            await until(lambda: len(sessions.submitted) == 1)
            assert sessions.submitted[0]["payload"]["text"].endswith(
                "Please explain the boundary case."
            )
        finally:
            await pool.shutdown()

    run(scenario())


def test_old_sheet_send_checks_live_epoch_before_another_render() -> None:
    from dataclasses import replace

    async def scenario() -> None:
        key = next(iter(_state().inventory.agents))
        bridge = RouteBridge(route=f"/agents/{key}/changes", width=1440, height=1000)
        app, controller, sessions, pool, _ = await _render(
            bridge=bridge,
            transport=Transport(),
            draft_store=FakeCredentialStore(),
            preferences=Preferences({PREFS_KEY: json.dumps({"auto_takeover": False})}),
        )
        try:
            await app.wait_for("changes-ask")
            await until(lambda: not app.find("changes-ask").disabled)
            await click(app, "changes-ask")
            send = app.find("changes-ask-send").on_click
            assert send is not None
            state = controller.state
            agent = state.inventory.agents[key]
            hosts = dict(state.inventory.hosts)
            hosts[agent.host_id] = replace(hosts[agent.host_id], epoch="replacement-epoch")
            controller.push(replace(state, inventory=replace(state.inventory, hosts=hosts)))
            send(None)  # The event still carries the previous rendered sheet.
            await app.settle()
            assert not sessions.submitted and not sessions.factory.created
        finally:
            await pool.shutdown()

    run(scenario())


@pytest.mark.auto_takeover
@pytest.mark.parametrize("entry", ["file", "section"])
@pytest.mark.parametrize("stored", [False, True])
def test_tablet_cold_question_with_automatic_takeover(entry: str, stored: bool) -> None:
    from dataclasses import replace

    from relaydeck.domain.models import AgentStatus

    async def scenario() -> None:
        key = next(iter(_state().inventory.agents))
        bridge = RouteBridge(route=f"/agents/{key}/changes", width=1376, height=1000)
        app, controller, sessions, pool, _ = await _render(
            bridge=bridge,
            transport=Transport(),
            draft_store=FakeCredentialStore() if stored else None,
            touch=True,
            platform_name="ios",
            preferences=Preferences({PREFS_KEY: json.dumps({"auto_takeover": True})}),
        )
        state = controller.state
        agents = dict(state.inventory.agents)
        agents[key] = replace(agents[key], status=AgentStatus.IDLE)
        controller.push(replace(state, inventory=replace(state.inventory, agents=agents)))
        try:
            await app.wait_for("changes-file-0")
            await click(app, "changes-file-0")
            await app.wait_for("diff-ask")
            await click(app, "diff-ask" if entry == "file" else "changes-ask")
            await click(app, "changes-ask-send")
            await until(lambda: len(sessions.factory.created) == 1)
            session = sessions.factory.created[0]
            await until(lambda: session.phase == "live")
            await until(lambda: len(sessions.submitted) == 1)
            assert session.store.messages[-1].state.kind == "delivered"
            assert session.store.draft == "" and len(sessions.ops.attaches) == 1
            await app.settle()
            assert len(sessions.submitted) == len(sessions.prompted) == 1
            assert all(
                kind != "bytes"
                for _, _, socket in sessions.streams.opened
                for kind, _ in socket.sent
            )
        finally:
            await pool.shutdown()

    run(scenario())


@pytest.mark.parametrize("opens_commit", [False, True])
def test_cold_graph_question_sends_once_and_back_restores_graph(opens_commit: bool) -> None:
    from types import SimpleNamespace

    async def scenario() -> None:
        key, agent = next(iter(_state().inventory.agents.items()))
        transport = Transport()
        transport.git.issue_commit_ids = opens_commit  # without an id a row is only selected
        bridge = RouteBridge(route=f"/agents/{key}/changes", width=1376, height=1000)
        app, _, sessions, pool, _ = await _render(
            bridge=bridge,
            transport=transport,
            draft_store=FakeCredentialStore(),
            preferences=Preferences({PREFS_KEY: json.dumps({"auto_takeover": False})}),
        )
        try:
            await app.wait_for("changes-view-graph")
            await click(app, "changes-view-graph")
            await app.wait_for("changes-more-graph")
            await click(app, "graph-mode-branch")
            await app.wait_for("changes-more-graph")
            await click(app, "changes-more-graph")
            await until(lambda: app.find("changes-graph").data["rows"] == 4)
            revision = transport.git.snapshot("branch") + ":graph"
            row = f"graph-row-{revision}-{3:040x}"
            await click(app, row)
            app.find("changes-graph").on_scroll(SimpleNamespace(pixels=175.0))
            asking = "commit-ask" if opens_commit else "changes-ask"
            await app.wait_for(asking)
            await until(lambda: not app.find(asking).disabled)
            await click(app, asking)
            await click(app, "changes-ask-send")
            await until(lambda: len(sessions.submitted) == 1)
            subject = "this commit" if opens_commit else "this branch"
            assert sessions.submitted[0]["payload"]["text"].startswith(f"About {subject}")
            assert sessions.submitted[0]["target"]["agent_id"] == agent.terminal_id
            await click(app, "changes-origin-back")
            await app.wait_for(row)
            assert app.find(row).data["selected"]
            assert opens_commit == present(app, "commit-subject")
            if opens_commit:  # the same commit, and its file is open again
                await app.wait_for("diff-rows")
            assert app.find("changes-graph").data["rows"] == 4
            await app.settle()
            assert len(sessions.submitted) == 1
        finally:
            await pool.shutdown()

    run(scenario())
