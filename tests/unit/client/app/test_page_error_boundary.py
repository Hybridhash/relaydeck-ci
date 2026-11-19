"""Page error dispatch has generic feedback, stable identity and bounded ownership."""

from __future__ import annotations

import asyncio
import importlib
import logging
from types import SimpleNamespace
from typing import Any

import flet as ft
import pytest

from tests.support.host_harness import render_component, run

MARKER = "private page error diagnostic payload"


def _boundary() -> Any:
    return importlib.import_module("relaydeck.client.app.error_boundary")


def _page() -> Any:
    return SimpleNamespace(on_error=None, on_close=None)


def test_installation_is_idempotent_and_restores_only_owned_slots() -> None:
    boundary = _boundary()

    def old_error(event: Any) -> None:
        pass

    def old_close(event: Any) -> None:
        pass

    page = SimpleNamespace(on_error=old_error, on_close=old_close)
    owner = boundary.install_page_errors(page)
    assert boundary.install_page_errors(page) is owner
    installed = page.on_error
    owner.close()
    assert page.on_error is old_error and page.on_close is old_close
    installed(object())
    assert not owner.open

    owner = boundary.install_page_errors(page)

    def replacement(event: Any) -> None:
        pass

    page.on_error = replacement
    page.on_close = replacement
    owner.close()
    assert page.on_error is replacement and page.on_close is replacement


@pytest.mark.parametrize("payload", [MARKER, {"token": MARKER}, ValueError(MARKER), None])
def test_error_feedback_never_interpolates_payload(
    payload: Any, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    boundary = _boundary()
    logger = logging.getLogger("test.page_errors")
    monkeypatch.setattr(boundary, "diagnostics_logger", lambda: logger)
    page = _page()
    owner = boundary.install_page_errors(page)
    states: list[bool] = []
    owner.subscribe(object(), states.append)
    page.on_error(payload)
    assert states == [False, True]
    assert [record.getMessage() for record in caplog.records] == ["page_error"]
    assert all(record.args == () and record.exc_info is None for record in caplog.records)
    assert MARKER not in caplog.text


def test_replaced_views_have_one_current_notice_and_stale_dismiss_is_inert() -> None:
    boundary = _boundary()
    page = _page()
    owner = boundary.install_page_errors(page)
    first, second = object(), object()
    a: list[bool] = []
    b: list[bool] = []
    leave_first = owner.subscribe(first, a.append)
    page.on_error(object())
    leave_second = owner.subscribe(second, b.append)
    assert a[-1] is False and b[-1] is True
    owner.dismiss(first)
    assert b[-1] is True and owner.open
    leave_second()
    assert a[-1] is True
    owner.dismiss(first)
    assert a[-1] is False and not owner.open
    leave_first()
    assert owner.listeners == {}
    for _ in range(100):
        leave = owner.subscribe(object(), lambda shown: None)
        assert len(owner.listeners) == 1
        leave()
    assert owner.listeners == {}


def test_disposal_and_displaced_handler_cannot_report_or_call_a_stale_view(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    boundary = _boundary()
    monkeypatch.setattr(boundary, "diagnostics_logger", lambda: logging.getLogger("test.closed"))
    page = _page()
    owner = boundary.install_page_errors(page)
    states: list[bool] = []
    owner.subscribe(object(), states.append)
    stale = page.on_error
    page.on_error = lambda event: None
    stale(MARKER)
    assert states == [False]
    owner.close()
    stale(MARKER)
    owner.dismiss(object())
    assert states == [False] and caplog.records == []


def test_failing_or_reentrant_diagnostics_and_view_callbacks_do_not_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = _boundary()
    page = _page()
    owner = boundary.install_page_errors(page)
    reports: list[str] = []

    class Sink:
        def warning(self, message: str) -> None:
            reports.append(message)
            page.on_error(MARKER)
            raise ValueError(MARKER)

    def unavailable(shown: bool) -> None:
        raise ValueError(MARKER)

    monkeypatch.setattr(boundary, "diagnostics_logger", lambda: Sink())
    owner.subscribe(object(), unavailable)
    page.on_error(MARKER)
    assert reports == ["page_error"] and owner.open


@pytest.mark.parametrize("asynchronous", [False, True])
def test_page_close_restores_slots_and_preserves_prior_close_callback(asynchronous: bool) -> None:
    async def scenario() -> None:
        boundary = _boundary()
        closed: list[str] = []

        async def async_close() -> None:
            closed.append("prior")

        def sync_close(event: Any) -> None:
            closed.append("prior")

        prior = async_close if asynchronous else sync_close
        page = SimpleNamespace(on_error=None, on_close=prior)
        owner = boundary.install_page_errors(page)
        await page.on_close(None)
        assert owner.closed and owner.listeners == {}
        assert page.on_error is None and page.on_close is prior
        assert closed == ["prior"]

    run(scenario())


@pytest.mark.parametrize("failing_surface", [False, True])
def test_real_sdk_error_dispatch_and_dismissal_preserve_input_and_safe_outputs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failing_surface: bool
) -> None:
    async def scenario() -> None:
        boundary = _boundary()
        monkeypatch.setattr(
            boundary, "diagnostics_logger", lambda: logging.getLogger("test.sdk.page_errors")
        )
        if failing_surface:

            def unavailable(*args: Any, **kwargs: Any) -> None:
                raise ValueError(MARKER)

            monkeypatch.setattr(boundary, "ActionToast", unavailable)
        app = await render_component(lambda: ft.Text("Starting"))
        owner = boundary.install_page_errors(app.page)

        @ft.component
        def Body() -> Any:
            notice = boundary.use_error_notice()
            controls = [ft.TextField(key="retained-input", value="retained draft")]
            if notice is not None:
                controls.append(notice)
            return ft.Column(controls=controls)

        app.page.render(Body)
        app.session.get_page_patch()
        await app.settle()
        input_id = app.find("retained-input")._i
        initial_children = len(app.page.views[0].controls._b.controls)
        assert initial_children == 1
        app.conn.sent.clear()
        await app.session.dispatch_event(app.page._i, "error", MARKER)
        await app.settle()
        field = app.find("retained-input")
        assert field._i == input_id and field.value == "retained draft"
        if failing_surface:
            with pytest.raises(KeyError):
                app.find("page-error-notice")
        else:
            notice = app.find("page-error-notice")
            assert notice.action.label == "Dismiss"
            assert notice.content.live_region is True
            assert notice.duration == 5000
            assert [span.text for span in notice.content.content.controls[1].spans] == [
                "Something went wrong."
            ]
            await app.session.dispatch_event(notice.action._i, "click", None)
            await app.settle()
            assert not owner.open
            with pytest.raises(KeyError):
                app.find("page-error-notice")
        assert MARKER not in repr(app.conn.sent)
        assert MARKER not in repr(app.session.get_page_patch())
        assert MARKER not in caplog.text
        assert [record.getMessage() for record in caplog.records] == ["page_error"]
        assert all(record.exc_info is None and record.args == () for record in caplog.records)
        app.session.close()
        await app.settle()
        assert owner.closed

    run(scenario())


def test_real_root_replacement_keeps_the_handler_and_retires_old_dismissal() -> None:
    async def scenario() -> None:
        boundary = _boundary()
        app = await render_component(lambda: ft.Text("Starting"))
        owner = boundary.install_page_errors(app.page)

        @ft.component
        def Body(label: str) -> Any:
            notice = boundary.use_error_notice()
            return [ft.Text(label), notice] if notice is not None else ft.Text(label)

        boundary.render_page(app.page, Body, label="anonymous")
        app.session.get_page_patch()
        await app.settle()
        handler = app.page.on_error
        await app.session.dispatch_event(app.page._i, "error", MARKER)
        await app.settle()
        stale = app.find("page-error-notice").action.on_click
        boundary.render_page(app.page, Body, label="authenticated")
        app.session.get_page_patch()
        await app.settle()
        assert app.page.on_error is handler
        assert len(owner.listeners) == 1
        stale(None)
        await app.settle()
        assert owner.open and app.find("page-error-notice").open
        app.session.close()
        await app.settle()
        assert owner.closed and owner.listeners == {}

    run(scenario())


def test_detached_failure_uses_its_owned_sink_instead_of_the_page_handler(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        from relaydeck.client.platform.flet.events import spawn

        owner = _boundary().install_page_errors(_page())

        async def fail() -> None:
            raise ValueError(MARKER)

        task = spawn(fail(), label="terminal-input")
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        assert not owner.open
        assert "terminal listener failed: stage=terminal-input category=ValueError" in caplog.text
        assert "page_error" not in caplog.text and MARKER not in caplog.text

    run(scenario())


def test_native_entry_installs_before_first_render_and_uses_actual_pairing_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        import httpx

        from relaydeck.client.app.native import entry
        from relaydeck.client.app.native.runtime import PackagedRuntime
        from tests.support.fakes import FakeClipboard, FakeCredentialStore, FakeNotifications
        from tests.support.ui.packaged import Connectivity, Preferences

        boundary = _boundary()
        app = await render_component(lambda: ft.Text("Starting"))
        owners: list[Any] = []
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: pytest.fail("No network is expected"))
        )

        async def open_browser(url: str) -> None:
            pytest.fail("An error event must not navigate")

        def factory(page: Any, notify: Any) -> Any:
            owner = PackagedRuntime(
                page,
                FakeCredentialStore(),
                Preferences({}),
                open_browser,
                notify,
                client=client,
                clipboard=FakeClipboard(),
            )
            owners.append(owner)
            return owner

        original = entry.PackagedApp

        @ft.component
        def Injected(page: Any) -> Any:
            return original(
                page=page,
                runtime_factory=factory,
                notifications_factory=FakeNotifications,
                connectivity_factory=Connectivity,
            )

        monkeypatch.setattr(entry, "PackagedApp", Injected)
        render = app.page.render

        def checked(component: Any, *args: Any, **kwargs: Any) -> None:
            assert callable(app.page.on_error)
            render(component, *args, **kwargs)

        monkeypatch.setattr(app.page, "render", checked)
        try:
            entry.main(app.page)
            app.session.get_page_patch()
            await app.settle()
            owner = owners[0]
            phase, pairing = owner.phase, owner.pairing.snapshot
            await app.session.dispatch_event(app.page._i, "error", MARKER)
            await app.settle()
            assert app.find("page-error-notice").action.label == "Dismiss"
            assert app.find("packaged-connect") is not None
            assert owner.phase == phase and owner.pairing.snapshot == pairing
            assert boundary.install_page_errors(app.page).open
        finally:
            for owner in owners:
                await owner.close()
            await client.aclose()
            app.session.close()
            await app.settle()

    run(scenario())


def test_hosted_anonymous_entry_installs_before_first_render_and_keeps_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        from relaydeck.companion.app.hosted_web import serve
        from relaydeck.gateway.app.bootstrap import create_app
        from tests.security.helpers import make_state

        app = await render_component(lambda: ft.Text("Starting"))
        state, _ = make_state()
        store = serve.WebSessionStore("https://private.test", b"x" * 32)
        main = serve.make_relaydeck_main(
            store=store,
            base_url=store.public_origin,
            gateway_app=create_app(state),
            gateway_state=state,
            host_id="home-mac",
        )
        render = app.page.render

        def checked(component: Any, *args: Any, **kwargs: Any) -> None:
            assert callable(app.page.on_error)
            render(component, *args, **kwargs)

        monkeypatch.setattr(app.page, "render", checked)
        try:
            main(app.page, (None,))
            app.session.get_page_patch()
            await app.settle()
            first = app.find("connect-reload")._i
            handler = app.page.on_error
            await app.session.dispatch_event(app.page._i, "error", MARKER)
            await app.settle()
            assert app.find("page-error-notice").action.label == "Dismiss"
            assert app.find("connect-reload")._i == first
            assert store.owner_of(app.session.id) is None and not store._sessions
            main(app.page, (None,))
            app.session.get_page_patch()
            await app.settle()
            assert app.page.on_error is handler
            assert len(_boundary().install_page_errors(app.page).listeners) == 1
        finally:
            state.push.close()
            app.session.close()
            await app.settle()

    run(scenario())


def test_actual_authenticated_root_feedback_keeps_state_and_existing_ancestry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        from tests.support.ui import retained_root

        boundary = _boundary()

        async def mounted(component: Any, *args: Any, **kwargs: Any) -> Any:
            app = await render_component(lambda: ft.Text("Starting"))
            boundary.install_page_errors(app.page)
            boundary.render_page(app.page, component, *args, **kwargs)
            app.session.get_page_patch()
            await app.settle()
            return app

        monkeypatch.setattr(retained_root, "render_component", mounted)
        app, controller, sessions, pool, now = await retained_root._render()
        before = controller.state
        root = app.page.views[0].controls
        try:
            await app.session.dispatch_event(app.page._i, "error", MARKER)
            await app.settle()
            assert app.find("page-error-notice").action.label == "Dismiss"
            assert controller.state is before
            assert app.page.views[0].controls is root
            await app.set_props_and_fire("test-unmount", {}, "click")
            assert len(boundary.install_page_errors(app.page).listeners) == 0
        finally:
            app.session.close()
            await app.settle()

    run(scenario())
