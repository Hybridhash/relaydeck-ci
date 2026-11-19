"""Shutdown, page navigation and revocation have distinct retained lifetimes."""

from __future__ import annotations

import asyncio
import importlib
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)

MARKER = "private task diagnostic payload"


def _serve() -> Any:
    return importlib.import_module("relaydeck.companion.app.hosted_web.serve")


def _page() -> Any:
    return SimpleNamespace(
        session=SimpleNamespace(connection=object()),
        views=[SimpleNamespace(controls=object())],
        on_close=None,
        on_disconnect=None,
    )


@pytest.mark.parametrize("retire", ["reset", "close", "disconnect", "connection", "view"])
def test_navigation_retains_work_and_fences_an_uncooperative_late_completion(retire: str) -> None:
    async def scenario() -> None:
        page = _page()
        owners: dict[int, Any] = {}
        owner = _serve()._PageNavigation(page, owners)
        entered, finish, canceled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        published: list[str] = []

        async def operation(current: Any) -> None:
            entered.set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                canceled.set()
                await finish.wait()
            if current():
                published.append("stale")

        task = owner.launch(operation)
        await entered.wait()
        if retire == "disconnect":
            await page.on_disconnect(None)
        elif retire == "connection":
            page.session.connection = object()
        elif retire == "view":
            page.views[0].controls = object()
        else:
            getattr(owner, retire)()
        if retire in {"reset", "close", "disconnect"}:
            await canceled.wait()
        assert task in owner.tasks and owners[id(page)] is owner
        finish.set()
        await task
        await asyncio.sleep(0)
        assert published == [] and owner.tasks == set()
        owner.close()
        assert owners == {} and page.on_close is None and page.on_disconnect is None

    asyncio.run(scenario())


def test_retired_or_detached_navigation_does_not_construct_a_coroutine() -> None:
    async def scenario() -> None:
        page = _page()
        owner = _serve()._PageNavigation(page, {})
        calls: list[bool] = []

        async def operation(current: Any) -> None:
            calls.append(True)

        page.session.connection = None
        assert owner.launch(operation) is None
        owner.close()
        page.session.connection = object()
        assert owner.launch(operation) is None
        await asyncio.sleep(0)
        assert calls == [] and owner.tasks == set()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_navigation_completion_consumes_failure_and_preserves_cancellation(
    cancel: bool, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        serve = _serve()
        monkeypatch.setattr(serve, "diagnostics_logger", lambda: logging.getLogger("test.nav"))
        owner = serve._PageNavigation(_page(), {})
        entered, finish = asyncio.Event(), asyncio.Event()
        unhandled: list[Any] = []
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda loop, context: unhandled.append(context))

        async def operation(current: Any) -> None:
            entered.set()
            await finish.wait()
            raise ValueError(MARKER)

        task = owner.launch(operation)
        await entered.wait()
        if cancel:
            owner.close()
        else:
            finish.set()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        assert task.cancelled() is cancel
        assert not owner.tasks and unhandled == []
        assert MARKER not in caplog.text
        if not cancel:
            assert [r.getMessage() for r in caplog.records] == ["web_navigation_failed"]
        owner.close()

    asyncio.run(scenario())


def test_native_close_is_retained_outside_the_set_it_cancels(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        entry = importlib.import_module("relaydeck.client.app.native.entry")
        entered, finish = asyncio.Event(), asyncio.Event()
        normal = asyncio.create_task(asyncio.Event().wait())

        class Runtime:
            def __init__(self) -> None:
                self._tasks = {normal}
                self.closed = False

            async def close(self) -> None:
                assert asyncio.current_task() not in self._tasks
                for task in self._tasks:
                    task.cancel()
                await asyncio.gather(*self._tasks, return_exceptions=True)
                entered.set()
                await finish.wait()
                self.closed = True

        runtime = Runtime()
        task = entry._close_runtime(runtime)
        await entered.wait()
        assert task in entry._closing_tasks and not task.done()
        assert normal.cancelled() and task not in runtime._tasks
        finish.set()
        await task
        await asyncio.sleep(0)
        assert runtime.closed and task not in entry._closing_tasks

    asyncio.run(scenario())


def test_native_close_failure_is_consumed_without_private_text(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        entry = importlib.import_module("relaydeck.client.app.native.entry")
        monkeypatch.setattr(entry, "diagnostics_logger", lambda: logging.getLogger("test.close"))

        async def close() -> None:
            raise ValueError(MARKER)

        task = entry._close_runtime(SimpleNamespace(close=close))
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        assert not entry._closing_tasks
        assert [r.getMessage() for r in caplog.records] == ["native_close_failed"]
        assert MARKER not in caplog.text

    asyncio.run(scenario())


@pytest.mark.parametrize("fail", [False, True])
def test_logout_owner_keeps_revocation_independent_of_page_retirement(
    fail: bool, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        serve = _serve()
        monkeypatch.setattr(serve, "diagnostics_logger", lambda: logging.getLogger("test.logout"))
        store = serve.WebSessionStore("https://private.test", b"x" * 32)
        sid = store.create_session("owner", "device", "access", "refresh", 2_000_000)
        entered, finish = asyncio.Event(), asyncio.Event()
        revoked: list[str] = []

        async def revoke() -> None:
            entered.set()
            await finish.wait()
            if fail:
                raise ValueError(MARKER)
            store.retire_local(sid)
            revoked.append(sid)

        task = store.start_logout(sid, revoke)
        await entered.wait()
        assert store.start_logout(sid, revoke) is task
        page = _page()
        navigation = serve._PageNavigation(page, store._navigation_owners)
        navigation.close()
        assert not task.done() and not task.cancelled()
        assert store._logout_tasks[sid] is task
        finish.set()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        assert store._logout_tasks == {}
        assert revoked == ([] if fail else [sid])
        assert MARKER not in caplog.text
        if fail:
            assert [r.getMessage() for r in caplog.records] == ["web_logout_failed"]
        else:
            assert store.start_logout(sid, revoke) is None

    asyncio.run(scenario())


def test_actual_native_unmount_retains_blocked_close(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        import flet as ft
        import httpx

        from relaydeck.client.app.native import entry
        from relaydeck.client.app.native.runtime import PackagedRuntime
        from tests.support.fakes import FakeClipboard, FakeCredentialStore, FakeNotifications
        from tests.support.host_harness import render_component
        from tests.support.ui.packaged import Connectivity, Preferences

        entered, finish = asyncio.Event(), asyncio.Event()
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: pytest.fail("No network is expected"))
        )
        owners: list[Any] = []
        original_close = PackagedRuntime.close

        async def blocked_close(runtime: Any) -> None:
            assert asyncio.current_task() not in runtime._tasks
            entered.set()
            await finish.wait()
            await original_close(runtime)

        monkeypatch.setattr(PackagedRuntime, "close", blocked_close)

        async def browser(url: str) -> None:
            pytest.fail("Cleanup must not navigate")

        def factory(page: Any, notify: Any) -> Any:
            runtime = PackagedRuntime(
                page,
                FakeCredentialStore(),
                Preferences({}),
                browser,
                notify,
                client=client,
                clipboard=FakeClipboard(),
            )
            owners.append(runtime)
            return runtime

        @ft.component
        def Parent() -> Any:
            mounted, replace = ft.use_state(True)
            return ft.Column(
                controls=[
                    entry.PackagedApp(
                        page=ft.context.page,
                        runtime_factory=factory,
                        notifications_factory=FakeNotifications,
                        connectivity_factory=Connectivity,
                    )
                    if mounted
                    else ft.Text("Retired"),
                    ft.Button("Retire", key="retire-native", on_click=lambda event: replace(False)),
                ]
            )

        app = await render_component(Parent)
        try:
            await app.set_props_and_fire("retire-native", {}, "click")
            await asyncio.wait_for(entered.wait(), 2)
            assert len(entry._closing_tasks) == 1
            task = next(iter(entry._closing_tasks))
            assert task not in owners[0]._tasks and not task.done()
            finish.set()
            await task
            await asyncio.sleep(0)
            assert not owners[0].alive and not entry._closing_tasks
        finally:
            finish.set()
            if entry._closing_tasks:
                await asyncio.gather(*entry._closing_tasks, return_exceptions=True)
            await client.aclose()
            app.session.close()
            await app.settle()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["reload", "pairing-finish"])
def test_actual_anonymous_navigation_is_owned_and_late_failure_cannot_publish(
    kind: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        from dataclasses import replace

        import flet as ft

        from relaydeck.gateway.app.bootstrap import create_app
        from tests.security.helpers import NOW, make_state
        from tests.support.host_harness import render_component

        serve = _serve()
        monkeypatch.setattr(serve, "diagnostics_logger", lambda: logging.getLogger("test.real.nav"))
        entered, canceled, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
        urls: list[str] = []

        async def navigate(launcher: Any, url: str, **kwargs: Any) -> bool:
            urls.append(url)
            entered.set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                canceled.set()
                await finish.wait()
            raise ValueError(MARKER)

        monkeypatch.setattr(ft.UrlLauncher, "launch_url", navigate)
        app = await render_component(lambda: ft.Text("Starting"))
        state, _ = make_state()
        store = serve.WebSessionStore("https://private.test", b"x" * 32, now_ms=lambda: NOW)
        if kind == "pairing-finish":
            store.browsers["synthetic-browser"] = NOW + 600_000
            store.browser_pages[app.session.id] = "synthetic-browser"
        main = serve.make_relaydeck_main(
            store=store,
            base_url=store.public_origin,
            gateway_app=create_app(state),
            gateway_state=state,
            host_id="home-mac",
        )
        try:
            main(app.page, (None,))
            app.session.get_page_patch()
            await app.settle()
            if kind == "reload":
                stale = app.find("connect-reload").on_click
                await app.session.dispatch_event(app.find("connect-reload")._i, "click", None)
                flow = None
            else:
                flow = store.pairing_flows[app.session.id]
                flow.emit(replace(flow.snapshot, phase="ready"))
            await asyncio.wait_for(entered.wait(), 2)
            navigation = store._navigation_owners[id(app.page)]
            task = next(iter(navigation.tasks))
            before = flow.snapshot if flow is not None else None
            main(app.page, (None,))
            app.session.get_page_patch()
            await app.settle()
            await asyncio.wait_for(canceled.wait(), 2)
            assert task in navigation.tasks and not task.done()
            finish.set()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)
            assert navigation.tasks == set()
            if flow is not None:
                assert flow.snapshot == before
                assert urls == [store.public_origin + serve.paths.AUTH_PAIRING_FINISH]
            else:
                stale(None)
                await asyncio.sleep(0)
                assert urls == [store.public_origin + "/"] and not navigation.tasks
            assert MARKER not in caplog.text and MARKER not in repr(app.conn.sent)
        finally:
            finish.set()
            app.session.close()
            await app.settle()
            state.push.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["recovery", "logout"])
def test_actual_authenticated_callbacks_keep_their_distinct_owners_after_replacement(
    kind: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        import flet as ft

        from relaydeck.gateway.app.bootstrap import create_app
        from tests.security.helpers import ALL_CAPS, NOW, make_state
        from tests.support.host_harness import render_component

        serve = _serve()
        monkeypatch.setattr(
            serve, "diagnostics_logger", lambda: logging.getLogger("test.real.auth")
        )
        app = await render_component(lambda: ft.Text("Starting"))
        state, _ = make_state()
        store = serve.WebSessionStore("https://private.test", b"x" * 32, now_ms=lambda: NOW)
        access, refresh = state.sessions.create(
            "owner", ALL_CAPS, frozenset({"home-mac"}), NOW, "device"
        )
        sid = store.create_session("owner", "device", access, refresh, NOW + 900_000)
        store.bind_flet(sid, app.session.id, serve._Handshake("", "synthetic", ""))
        callbacks: dict[str, Any] = {}
        entered, canceled, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = serve.teardown_web_sid

        async def teardown(*args: Any, **kwargs: Any) -> None:
            entered.set()
            await finish.wait()
            await original(*args, **kwargs)

        async def navigate(launcher: Any, url: str, **kwargs: Any) -> bool:
            entered.set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                canceled.set()
                await finish.wait()
            raise ValueError(MARKER)

        async def close() -> None:
            pass

        def run_app(page: Any, **kwargs: Any) -> Any:
            callbacks.update(kwargs)
            page.render(lambda: ft.Text("Authenticated", key="authenticated"))
            return SimpleNamespace(close=close)

        monkeypatch.setattr("relaydeck.client.app.root.run_app", run_app)
        monkeypatch.setattr(serve, "teardown_web_sid", teardown)
        monkeypatch.setattr(ft.UrlLauncher, "launch_url", navigate)
        main = serve.make_relaydeck_main(
            store=store,
            base_url=store.public_origin,
            gateway_app=create_app(state),
            gateway_state=state,
            host_id="home-mac",
        )
        try:
            main(app.page, (store.identity(sid),))
            app.session.get_page_patch()
            await app.settle()
            callback = callbacks["on_logout" if kind == "logout" else "on_session_recovery"]
            callback()
            await asyncio.wait_for(entered.wait(), 2)
            navigation = store._navigation_owners[id(app.page)]
            task = store._logout_tasks[sid] if kind == "logout" else next(iter(navigation.tasks))
            main(app.page, (None,))
            app.session.get_page_patch()
            await app.settle()
            if kind == "recovery":
                await asyncio.wait_for(canceled.wait(), 2)
            assert not task.done()
            assert task in (store._logout_tasks.values() if kind == "logout" else navigation.tasks)
            finish.set()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)
            if kind == "logout":
                assert store.identity(sid) is None and store.owner_of(app.session.id) is None
                assert state.sessions.lookup(access, NOW) is None
                assert not store._logout_tasks
            else:
                assert not navigation.tasks
                assert store.identity(sid) is not None
            callback()
            await asyncio.sleep(0)
            assert not navigation.tasks and not store._logout_tasks
            assert MARKER not in caplog.text and MARKER not in repr(app.conn.sent)
        finally:
            finish.set()
            app.session.close()
            await app.settle()
            state.push.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["navigation", "logout", "native"])
def test_a_failing_task_sink_cannot_create_an_unhandled_private_exception(
    kind: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        serve = _serve()
        entry = importlib.import_module("relaydeck.client.app.native.entry")
        contexts: list[Any] = []
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda loop, context: contexts.append(context))

        class Sink:
            def warning(self, message: str) -> None:
                raise ValueError(MARKER)

        monkeypatch.setattr(serve, "diagnostics_logger", lambda: Sink())
        monkeypatch.setattr(entry, "diagnostics_logger", lambda: Sink())

        async def fail(*args: Any) -> None:
            raise ValueError(MARKER)

        if kind == "navigation":
            owner = serve._PageNavigation(_page(), {})
            task = owner.launch(fail)
        elif kind == "logout":
            store = serve.WebSessionStore("https://private.test", b"x" * 32)
            sid = store.create_session("owner", "device", "access", "refresh", 2_000_000)
            task = store.start_logout(sid, fail)
        else:
            task = entry._close_runtime(SimpleNamespace(close=fail))
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        assert contexts == [] and MARKER not in caplog.text
        if kind == "navigation":
            assert owner.tasks == set()
            owner.close()
        elif kind == "logout":
            assert store._logout_tasks == {}
        else:
            assert not entry._closing_tasks

    asyncio.run(scenario())


def test_closed_page_navigation_does_not_reinvoke_prior_callbacks() -> None:
    async def scenario() -> None:
        page = _page()
        calls: list[str] = []

        async def close() -> None:
            calls.append("close")

        def disconnect(event: Any) -> None:
            calls.append("disconnect")

        page.on_close = close
        page.on_disconnect = disconnect
        owner = _serve()._PageNavigation(page, {})
        held_close, held_disconnect = page.on_close, page.on_disconnect
        await held_disconnect(None)
        await held_close(None)
        await held_close(None)
        await held_disconnect(None)
        assert calls == ["disconnect", "close"]
        assert page.on_close is close and page.on_disconnect is disconnect and owner.closed

    asyncio.run(scenario())
