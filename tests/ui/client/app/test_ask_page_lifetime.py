"""A page-owned subscription stops before its connection can schedule more work."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import flet as ft
import pytest

from relaydeck.client.app.workflows.ask import use_ask_runtime
from tests.support.host_harness import render_component, run
from tests.support.ui.navigation import _state


class Observable:
    def __init__(self) -> None:
        self.listeners: list[Any] = []

    def subscribe(self, changed: Any) -> Any:
        self.listeners.append(changed)
        return lambda: self.listeners.remove(changed)

    def emit(self) -> None:
        for listener in tuple(self.listeners):
            listener(self)


def test_disconnected_page_drops_subscription_without_creating_a_coroutine() -> None:
    async def scenario() -> None:
        observed = Observable()
        state = _state()
        key = next(iter(state.inventory.agents))

        @ft.component
        def Parent() -> ft.Control:
            use_ask_runtime(
                account="account",
                transport=None,
                state=state,
                destination="agents",
                selection=key,
                section="",
                foreground=True,
                navigate=lambda *a, **k: None,
                entry=SimpleNamespace(kind="agent", key=key, session=observed),
            )
            return ft.Text("Session")

        app = await render_component(Parent)
        assert len(observed.listeners) == 1
        await app.session.disconnect(0)
        observed.emit()
        assert not observed.listeners

    run(scenario())


def test_component_teardown_releases_listener_and_queued_work() -> None:
    import asyncio

    from relaydeck.client.app.workflows.page_tasks import PageTasks
    from tests.ui.git.harness import click

    async def scenario() -> None:
        observed = Observable()
        state = _state()
        key = next(iter(state.inventory.agents))

        @ft.component
        def Binding() -> ft.Control:
            use_ask_runtime(
                account="account",
                transport=None,
                state=state,
                destination="agents",
                selection=key,
                section="",
                foreground=True,
                navigate=lambda *a, **k: None,
                entry=SimpleNamespace(kind="agent", key=key, session=observed),
            )
            return ft.Text("Session")

        @ft.component
        def Parent() -> ft.Control:
            opened, set_opened = ft.use_state(True)
            return ft.Column(
                controls=[
                    Binding() if opened else ft.Text("Closed"),
                    ft.TextButton(
                        "Close", key="close-binding", on_click=lambda _: set_opened(False)
                    ),
                ]
            )

        app = await render_component(Parent)
        assert len(observed.listeners) == 1
        await click(app, "close-binding")
        assert not observed.listeners
        calls: list[bool] = []

        async def drive() -> None:
            calls.append(True)

        tasks = PageTasks(app.page, lambda: None)
        tasks.watch(observed, drive)
        observed.emit()
        tasks.close()
        await asyncio.sleep(0)
        assert not calls and not observed.listeners and not tasks.tasks

    run(scenario())


def test_disconnect_between_notification_and_dispatch_starts_no_task() -> None:
    import asyncio

    from relaydeck.client.app.workflows.page_tasks import PageTasks

    async def scenario() -> None:
        app = await render_component(lambda: ft.Text("Session"))
        observed = Observable()
        calls: list[bool] = []
        lost: list[bool] = []

        async def drive() -> None:
            calls.append(True)

        tasks = PageTasks(app.page, lambda: lost.append(True))
        tasks.watch(observed, drive)
        observed.emit()
        await app.session.disconnect(0)
        await asyncio.sleep(0)
        assert lost == [True] and not calls and not tasks.tasks and not observed.listeners

    run(scenario())


def test_destroyed_page_and_started_delivery_are_fenced_independently() -> None:
    import asyncio

    from relaydeck.client.app.workflows.page_tasks import PageTasks

    class GonePage:
        @property
        def session(self) -> Any:
            raise RuntimeError("Session destroyed")

    async def scenario() -> None:
        calls: list[bool] = []

        async def drive() -> None:
            calls.append(True)

        gone = PageTasks(GonePage(), lambda: None)
        gone.run(drive)
        assert gone.closed and not calls
        app = await render_component(lambda: ft.Text("Session"))
        owner = PageTasks(app.page, lambda: None)
        started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def send() -> None:
            started.set()
            await release.wait()
            finished.set()

        owner.run(send)
        await started.wait()
        owner.close()
        release.set()
        await finished.wait()
        await asyncio.sleep(0)
        assert not owner.tasks

    run(scenario())


def test_replacement_connection_drops_pending_even_without_a_session_notification() -> None:
    import asyncio

    from relaydeck.client.features.ask_agent.handoff import PendingAsk
    from relaydeck.client.features.ask_agent.model import AskScope
    from relaydeck.client.features.changes.reading_bookmark import ReadingBookmark
    from relaydeck.client.features.changes.state import ChangesModel
    from tests.support.host_harness import FakeConnection
    from tests.ui.git.harness import click

    async def scenario() -> None:
        observed = Observable()
        state = _state()
        key = next(iter(state.inventory.agents))
        control: dict[str, Any] = {}

        @ft.component
        def Parent() -> ft.Control:
            _, tick = ft.use_state(0)
            runtime = use_ask_runtime(
                account="account",
                transport=None,
                state=state,
                destination="agents",
                selection=key,
                section="changes",
                foreground=True,
                navigate=lambda *a, **k: None,
                entry=SimpleNamespace(kind="agent", key=key, session=observed),
            )
            control["runtime"] = runtime
            return ft.TextButton(
                "Refresh", key="refresh-binding", on_click=lambda _: tick(lambda n: n + 1)
            )

        app = await render_component(Parent)
        runtime = control["runtime"]
        runtime.handoff.begin(
            PendingAsk(
                runtime.gate(key)[0],
                AskScope("branch", "working", "snapshot", "a" * 40),
                "Question?",
                ReadingBookmark.capture(ChangesModel()),
                "Session",
            )
        )
        await app.session.disconnect(0)
        connection = FakeConnection()
        connection.loop = asyncio.get_running_loop()
        connection.executor = app.conn.executor
        connection.pubsubhub = app.conn.pubsubhub
        app.session.attach_connection(connection)
        await click(app, "refresh-binding")
        assert runtime.handoff.pending is None and len(observed.listeners) == 1

    run(scenario())


@pytest.mark.parametrize("inflight", [False, True], ids=["created-not-started", "claimed-send"])
def test_connection_replacement_between_task_creation_and_driver_execution(inflight: bool) -> None:
    import asyncio

    from relaydeck.client.features.ask_agent.handoff import PendingAsk
    from relaydeck.client.features.ask_agent.model import AskScope
    from relaydeck.client.features.changes.reading_bookmark import ReadingBookmark
    from relaydeck.client.features.changes.state import ChangesModel
    from relaydeck.client.platform.session_drafts import session_drafts
    from tests.support.host_harness import FakeConnection
    from tests.support.scripted_sessions import make_scripted_sessions

    async def scenario() -> None:
        state = _state()
        key, agent = next(iter(state.inventory.agents.items()))
        epoch = state.inventory.hosts[agent.host_id].epoch
        scripted = make_scripted_sessions()
        scripted.factory.draft_store = session_drafts(None)
        entered, release = asyncio.Event(), asyncio.Event()

        async def observe(host: str, command: str) -> dict[str, str]:
            entered.set()
            await release.wait()
            return {"command_id": command}

        if inflight:
            scripted.factory.observe_command = observe
        session = scripted.factory.make_agent(
            host_id=agent.host_id,
            host_epoch=epoch,
            agent_id=agent.terminal_id,
            target=agent.terminal_id,
            initial_status=agent.status.value,
            expected_revision=agent.revision,
        )
        await session.prepare_draft()
        control: dict[str, Any] = {}

        @ft.component
        def Parent() -> ft.Control:
            control["runtime"] = use_ask_runtime(
                account="account",
                transport=None,
                state=state,
                destination="agents",
                selection=key,
                section="",
                foreground=True,
                navigate=lambda *a, **k: None,
                entry=SimpleNamespace(kind="agent", key=key, epoch=epoch, session=session),
            )
            return ft.Text("Session")

        app = await render_component(Parent)
        runtime = control["runtime"]
        draft = session.store.export_draft()
        request = PendingAsk(
            runtime.gate(key)[0],
            AskScope("branch", "working", "snapshot", "a" * 40),
            "Question?",
            ReadingBookmark.capture(ChangesModel()),
            "Session",
        )
        runtime.handoff.begin(request)
        connection = FakeConnection()
        connection.loop = asyncio.get_running_loop()
        connection.executor = app.conn.executor
        connection.pubsubhub = app.conn.pubsubhub
        try:
            runtime.enqueue()
            if inflight:
                await entered.wait()
                assert request.state == "claimed" and len(scripted.submitted) == 1
                app.session.attach_connection(connection)
                session.set_agent_status("done")
                release.set()
            else:
                # start() creates a task; replacement runs before that task's first instruction.
                asyncio.get_running_loop().call_soon(app.session.attach_connection, connection)
            await app.settle()
            assert app.session.connection is connection and runtime.handoff.pending is None
            if inflight:
                assert len(scripted.submitted) == 1 and not scripted.prompted
                assert session.store.messages[-1].state.kind == "delivered"
            else:
                assert not scripted.submitted and not scripted.prompted
                assert session.store.export_draft() == draft and not session.store.messages
            assert not scripted.ops.attaches
        finally:
            release.set()
            await session.dispose()

    run(scenario())
