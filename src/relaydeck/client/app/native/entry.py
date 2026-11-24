"""Secure packaged entrypoint; hosted web retains its own authenticated boundary."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import flet as ft
from flet_secure_storage import SecureStorage
from relaydeck_notifications import RelayDeckNotifications

from relaydeck.client.app.appearance import configure_page_theme
from relaydeck.client.app.error_boundary import install_page_errors, render_page, use_error_notice
from relaydeck.client.app.native.runtime import PackagedRuntime
from relaydeck.client.app.notifications import NotificationRuntime
from relaydeck.client.app.root import Root
from relaydeck.client.design_system.appearance import chrome_style
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.design_system.headers import ScreenHeader
from relaydeck.client.design_system.theme import MIN_TOUCH, palette
from relaydeck.client.platform.diagnostics.terminal import diagnostics_logger
from relaydeck.client.platform.flet.invokes import install_invoke_result_guard
from relaydeck.client.platform.flet.notifications import ExtensionNotifications
from relaydeck.client.platform.flet.secure_storage import FletSecureCredentialStore
from relaydeck.client.screens.pairing.screen import PairingPanel

_closing_tasks: set[asyncio.Task[None]] = set()


def _close_runtime(runtime: PackagedRuntime) -> asyncio.Task[None]:
    task = asyncio.create_task(runtime.close())
    _closing_tasks.add(task)

    def finished(done: asyncio.Task[None]) -> None:
        if not done.cancelled() and done.exception() is not None:
            try:
                diagnostics_logger().warning("native_close_failed")
            except Exception:  # noqa: BLE001,S110 - never recurse through a failing diagnostic sink
                pass
        _closing_tasks.discard(done)

    task.add_done_callback(finished)
    return task


@ft.component
def PackagedApp(
    page: ft.Page,
    runtime_factory: Callable[[ft.Page, Callable[[], Any]], PackagedRuntime] | None = None,
    notifications_factory: Callable[[], Any] | None = None,
    connectivity_factory: Callable[[], Any] | None = None,
) -> Any:
    _, redraw = ft.use_state(0)
    owner: Any = ft.use_ref(None)
    tasks: Any = ft.use_ref(set())
    closing: Any = ft.use_ref(None)
    previous_lifecycle: Any = ft.use_ref(None)
    subscriptions: Any = ft.use_ref([])
    if owner.current is None:
        browser = ft.UrlLauncher()

        async def open_browser(url: str) -> None:
            if not await browser.launch_url(url, mode=ft.LaunchMode.EXTERNAL_APPLICATION):
                raise RuntimeError("System browser unavailable")

        notify = lambda: redraw(time.monotonic_ns())  # noqa: E731 - stable service callback
        runtime = (
            runtime_factory(page, notify)
            if runtime_factory is not None
            else PackagedRuntime(
                page,
                FletSecureCredentialStore(SecureStorage()),
                ft.SharedPreferences(),
                open_browser,
                notify,
            )
        )
        notifications = (
            notifications_factory()
            if notifications_factory is not None
            else (ExtensionNotifications(RelayDeckNotifications()))
        )
        connectivity = connectivity_factory() if connectivity_factory else ft.Connectivity()
        runtime.notifications = NotificationRuntime(notifications, runtime)
        owner.current = (runtime, notifications, connectivity)
    runtime, notifications, connectivity = owner.current
    error_notice = use_error_notice()
    tokens = palette(True)

    def launch(operation: Any) -> None:
        if not runtime.alive:
            operation.close()
            return

        async def run() -> None:
            try:
                await operation
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - no URI, credential or provider exception in UI/logs
                if runtime.alive:
                    runtime.fence_input()
                    runtime.phase = "error"
                    runtime.notice = (
                        "Connection or identity unavailable. Check your private endpoint."
                    )
                    runtime.notify()

        task = asyncio.create_task(run())
        tasks.current.add(task)
        task.add_done_callback(tasks.current.discard)

    def start() -> None:
        push = runtime.notifications
        subscriptions.current = [
            notifications.on_app_link(lambda uri: launch(push.app_link(uri))),
            notifications.on_tap(lambda tap: launch(push.tap(tap))),
            notifications.on_token_refresh(
                lambda token: launch(push.token_changed(token, generation=push.generation))
            ),
        ]
        previous = page.on_app_lifecycle_state_change

        def lifecycle(event: Any) -> None:
            if event.state in {
                ft.AppLifecycleState.HIDE,
                ft.AppLifecycleState.PAUSE,
                ft.AppLifecycleState.INACTIVE,
            }:
                runtime.background()
            elif event.state == ft.AppLifecycleState.RESUME:
                runtime.request_resume()

        page.on_app_lifecycle_state_change = lifecycle
        connectivity.on_change = lambda event: runtime.connectivity_changed(event.connectivity)
        previous_lifecycle.current = previous
        launch(runtime.start())
        launch(push.initial())

    def stop() -> None:
        runtime.alive = False
        runtime.interactive = False
        page.on_app_lifecycle_state_change = previous_lifecycle.current
        connectivity.on_change = None
        for unsubscribe in subscriptions.current:
            unsubscribe()
        subscriptions.current.clear()
        for task in tuple(tasks.current):
            task.cancel()
        tasks.current.clear()
        if closing.current is None:
            closing.current = _close_runtime(runtime)

    ft.use_effect(start, dependencies=[], cleanup=stop)
    if runtime.resources is not None and runtime.phase in {"ready", "paused", "resuming", "error"}:
        enabled = runtime.input_allowed()
        return ft.Stack(
            key="packaged-workbench",
            expand=True,
            controls=[
                ft.Container(
                    key="packaged-root-holder",
                    expand=True,
                    disabled=not enabled,
                    content=Root(  # type: ignore[call-arg]
                        key=f"packaged-{runtime.resources.props['account']}",
                        **runtime.resources.props,
                        foreground=enabled,
                        notification_status=(
                            runtime.notifications.notice,
                            runtime.notifications.busy,
                        ),
                        on_notifications_enable=lambda: launch(runtime.notifications.enable()),
                    ),
                ),
                ft.Container(
                    key="packaged-resume-overlay",
                    visible=not enabled,
                    bgcolor=tokens.surface,
                    padding=16,
                    content=ft.Column(
                        controls=[
                            ft.Text(
                                runtime.notice
                                or "Paused. Checking current identity and state before input.",
                                color=tokens.text,
                            ),
                            Button(
                                "Resume current state",
                                key="packaged-resume",
                                height=MIN_TOUCH,
                                style=chrome_style(True),
                                on_click=lambda e: runtime.request_resume(),
                                disabled=not runtime.foreground
                                or not runtime.online
                                or runtime.phase == "resuming",
                            ),
                        ]
                    ),
                ),
                *([error_notice] if error_notice is not None else []),
            ],
        )
    # expand: without it the SafeArea sizes to its content, the inner column
    # gets unbounded height and never scrolls (iPhone pairing overflowed).
    return ft.SafeArea(
        expand=True,
        content=ft.Container(
            padding=16,
            bgcolor=tokens.surface,
            expand=True,
            content=ft.Column(
                scroll=ft.ScrollMode.AUTO,
                controls=[
                    ScreenHeader("RelayDeck", key="packaged-title", color=tokens.text, size=24),
                    ft.Text(runtime.notice, key="packaged-notice", color=tokens.muted),
                    ft.Text(
                        "Use your Mac's approved private route. "
                        "Pairing alone does not provide connectivity.",
                        key="packaged-private-route",
                        color=tokens.muted,
                    ),
                    ft.Container(
                        key="packaged-connect",
                        disabled=runtime.phase == "unavailable" or not runtime.pairing_available,
                        content=PairingPanel(
                            runtime.pairing,
                            endpoint=runtime.endpoint,
                            label=_default_device_label(page),
                            begin_callback=runtime.begin_pairing,
                        ),
                    ),
                    *([error_notice] if error_notice is not None else []),
                ],
            ),
        ),
    )


def _default_device_label(page: Any) -> str:
    """Editable pairing label suggestion matching the running platform."""
    platform = getattr(page, "platform", None)
    if platform == ft.PagePlatform.IOS:
        return "My iPhone or iPad"
    if platform == ft.PagePlatform.ANDROID:
        return "My Android device"
    return "My device"


def main(page: ft.Page) -> None:
    install_page_errors(page)
    install_invoke_result_guard(page.session)
    page.title = "RelayDeck"
    configure_page_theme(page)
    render_page(page, PackagedApp, page=page)
