"""Settings update notice: section and copy, fetch cadence, 404 hidden, rail dot."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import flet as ft
import pytest

from relaydeck.client.app.hooks.preferences import use_preferences
from relaydeck.client.app.shell.destinations import DEST_SETTINGS, DESTINATIONS
from relaydeck.client.app.shell.shell import AppShell
from relaydeck.client.design_system.navigation import ConsoleRail
from relaydeck.client.features.update_notice.hooks import use_update_notice
from relaydeck.client.features.update_notice.model import FETCH_INTERVAL_MS, parse
from relaydeck.client.features.update_notice.source import UpdateNoticeSource
from relaydeck.client.models.preferences import TerminalPrefs, encode
from relaydeck.client.ports.transport import TransportError
from relaydeck.client.screens.settings.parts.update_notice import UpdateNoticePanel
from relaydeck.client.screens.settings.screen import SettingsPage
from tests.support.host_harness import render_component, run
from tests.support.ui.focus import iter_controls
from tests.support.ui.navigation import _state

DOC: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[4] / "fixtures/gateway/updates.json").read_text()
)


class NoticeSource(UpdateNoticeSource):
    def __init__(self, *, error: str = "") -> None:
        self.calls = 0
        self.error = error

    async def updates(self) -> dict[str, Any]:
        self.calls += 1
        if self.error:
            raise TransportError(self.error, "x", False)
        return DOC


def _probe(transport: UpdateNoticeSource, now: list[int]) -> Any:
    @ft.component
    def Probe() -> Any:
        foreground, set_foreground = ft.use_state(True)
        notice = use_update_notice(transport, foreground, clock=lambda: now[0])
        return ft.Column(
            [
                ft.TextButton(
                    "f", key="toggle-foreground", on_click=lambda e: set_foreground(not foreground)
                ),
                UpdateNoticePanel(notice, phone=False),
            ]
        )

    return Probe


def _texts(app: Any) -> list[str]:
    return [
        c.value
        for c in iter_controls([app.page.views[0].controls])
        if isinstance(c, ft.Text) and c.value
    ]


def _has(app: Any, key: str) -> bool:
    try:
        app.find(key)
    except KeyError:
        return False
    return True


def test_the_section_shows_the_release_and_copies_the_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copied: list[str] = []

    class FakeClipboard:
        async def set(self, value: str) -> None:
            copied.append(value)

    monkeypatch.setattr(ft, "Clipboard", FakeClipboard)
    transport = NoticeSource()

    async def scenario() -> None:
        app = await render_component(_probe(transport, [1_000]))
        await app.wait_for("update-notice-issued")
        texts = _texts(app)
        assert "RelayDeck 0.3.1 is available on your Mac" in texts
        assert "Released 2026-12-01" in texts
        assert "A fix for pairing." in texts
        assert "Last checked 2026-12-02" in texts
        block = app.find("update-notice-instructions")
        assert "uv tool install" in block.content.value
        await app.set_props_and_fire("settings-update-copy", {}, "click")
        await app.settle(3)
        assert copied == ["\n".join(DOC["instructions"])]

    run(scenario())


def test_a_404_hides_the_section_silently() -> None:
    transport = NoticeSource(error="not_found")

    async def scenario() -> None:
        app = await render_component(_probe(transport, [1_000]))
        await app.settle(5)
        assert transport.calls == 1
        assert not _has(app, "settings-update-notice")
        assert "available on your Mac" not in " ".join(_texts(app))

    run(scenario())


def test_foreground_returns_refetch_hourly_and_an_error_keeps_the_notice() -> None:
    transport = NoticeSource()
    now = [1_000]

    async def scenario() -> None:
        app = await render_component(_probe(transport, now))
        await app.wait_for("update-notice-issued")
        assert transport.calls == 1
        for _ in range(2):  # away and back inside the hour: no refetch
            await app.set_props_and_fire("toggle-foreground", {}, "click")
            await app.settle(2)
        assert transport.calls == 1
        transport.error = "host_offline"
        now[0] += FETCH_INTERVAL_MS
        for _ in range(2):  # away and back after the hour: one failing refetch
            await app.set_props_and_fire("toggle-foreground", {}, "click")
            await app.settle(2)
        assert transport.calls == 2
        assert _has(app, "update-notice-issued")  # the last value stayed

    run(scenario())


def test_the_settings_page_places_the_notice_before_server_health() -> None:
    async def scenario() -> None:
        app = await render_component(
            SettingsPage,
            TerminalPrefs(),
            lambda value: None,
            phone=False,
            account="me",
            version="0",
            on_logout=lambda: None,
            management=ft.Text("management"),
            update_notice=ft.Text("notice", key="settings-update-notice"),
            server_health=ft.Text("health", key="settings-server-health"),
            diagnostics=(("a", "b"),),
        )
        keys = [
            getattr(c, "key", None)
            for c in iter_controls([app.page.views[0].controls])
            if getattr(c, "key", None)
            in ("settings-update-notice", "settings-management", "settings-server-health")
        ]
        assert keys == [
            "settings-update-notice",
            "settings-management",
            "settings-server-health",
        ]

    run(scenario())


def test_the_rail_dot_marks_settings_only_when_asked() -> None:
    @ft.component
    def Rail(dot: bool) -> Any:
        return ConsoleRail(
            "settings",
            lambda value: None,
            destinations=DESTINATIONS,
            settings_id=DEST_SETTINGS,
            settings_dot=dot,
        )

    async def scenario() -> None:
        plain = await render_component(Rail, False)
        assert not _has(plain, "console-rail-settings-dot")
        dotted = await render_component(Rail, True)
        assert _has(dotted, "console-rail-settings-dot")

    run(scenario())


def test_opening_settings_acknowledges_the_version_and_clears_the_dot() -> None:
    transport = NoticeSource()
    saved: list[TerminalPrefs] = []

    @ft.component
    def Parent() -> Any:
        prefs, set_prefs = ft.use_state(TerminalPrefs())
        destination, set_destination = ft.use_state("agents")

        def on_prefs(value: TerminalPrefs) -> None:
            saved.append(value)
            set_prefs(value)

        return AppShell(
            state=_state(),
            is_wide=True,
            viewport_width=1280,
            destination=destination,
            command_transport=transport,
            foreground=True,
            dark=True,
            prefs=prefs,
            on_prefs=on_prefs,
            on_destination=set_destination,
            on_select_agent=lambda value: None,
            on_select_shell=lambda value: None,
            on_select_host=lambda value: None,
            on_banner_action=lambda: None,
            on_open_terminal=lambda: None,
            on_start_agent=lambda: None,
            on_new_terminal=lambda: None,
        )

    async def scenario() -> None:
        app = await render_component(Parent)
        await app.wait_for("console-rail-settings-dot")
        assert transport.calls == 1  # one fetch for both the dot and the section
        await app.set_props_and_fire("console-rail-settings", {}, "click")
        await app.wait_for("update-notice-issued")
        assert saved and saved[-1].acknowledged_update == "0.3.1"
        await app.settle(3)
        assert not _has(app, "console-rail-settings-dot")

    run(scenario())


class _DelayedTransport:
    def __init__(self) -> None:
        self.future: asyncio.Future[dict[str, Any]] | None = None
        self.calls = 0

    async def updates(self) -> dict[str, Any]:
        self.calls += 1
        self.future = asyncio.get_running_loop().create_future()
        try:
            return await asyncio.shield(self.future)
        except asyncio.CancelledError:
            return await self.future


class _NoOfferTransport:
    async def updates(self) -> dict[str, Any]:
        return DOC | {"available": None, "instructions": None}


def _switching_probe(first: Any, second: Any) -> Any:
    @ft.component
    def Probe() -> Any:
        target, set_target = ft.use_state(0)
        notice = use_update_notice(first if target == 0 else second, True)
        value = "none" if notice is None else (notice.available_version or "none")
        return ft.Column(
            [
                ft.Text(value, key="notice-value"),
                ft.TextButton("switch", key="switch", on_click=lambda e: set_target(1)),
            ]
        )

    return Probe


def test_old_transport_response_cannot_replace_new_transport_notice() -> None:
    delayed, current = _DelayedTransport(), _NoOfferTransport()

    async def scenario() -> None:
        app = await render_component(_switching_probe(delayed, current))
        await app.settle(5)
        assert delayed.future is not None
        await app.set_props_and_fire("switch", {}, "click")
        await app.settle(5)
        assert app.find("notice-value").value == "none"
        delayed.future.set_result(DOC)
        await app.settle(5)
        assert app.find("notice-value").value == "none"

    run(scenario())


def test_replacement_without_updates_capability_clears_old_notice() -> None:
    async def scenario() -> None:
        app = await render_component(_switching_probe(NoticeSource(), object()))
        await app.settle(5)
        assert app.find("notice-value").value == "0.3.1"
        await app.set_props_and_fire("switch", {}, "click")
        await app.settle(5)
        assert app.find("notice-value").value == "none"

    run(scenario())


def _shell(transport: Any, prefs: Any, on_prefs: Any, *, dark: bool = True) -> Any:
    return AppShell(
        state=_state(),
        is_wide=True,
        viewport_width=1280,
        destination="settings",
        command_transport=transport,
        foreground=True,
        dark=dark,
        prefs=prefs,
        on_prefs=on_prefs,
        on_destination=lambda value: None,
        on_select_agent=lambda value: None,
        on_select_shell=lambda value: None,
        on_select_host=lambda value: None,
        on_banner_action=lambda: None,
        on_open_terminal=lambda: None,
        on_start_agent=lambda: None,
        on_new_terminal=lambda: None,
    )


def test_light_settings_actually_displays_the_notice_it_acknowledges() -> None:
    saved: list[TerminalPrefs] = []

    @ft.component
    def Parent() -> Any:
        prefs, set_prefs = ft.use_state(TerminalPrefs())

        def changed(value: TerminalPrefs) -> None:
            saved.append(value)
            set_prefs(value)

        return _shell(NoticeSource(), prefs, changed, dark=False)

    async def scenario() -> None:
        app = await render_component(Parent)
        await app.settle(8)
        assert saved[-1].acknowledged_update == "0.3.1"
        assert app.find("settings-update-notice") is not None

    run(scenario())


def test_automatic_acknowledge_preserves_preferences_still_loading() -> None:
    original = encode(TerminalPrefs(font_size=20))

    class Store:
        def __init__(self) -> None:
            self.gate: asyncio.Future[None] | None = None
            self.saved: list[dict[str, Any]] = []

        async def get(self, key: str) -> str:
            self.gate = asyncio.get_running_loop().create_future()
            await self.gate
            return original

        async def set(self, key: str, value: str) -> None:
            self.saved.append(json.loads(value))

    store = Store()
    last: list[TerminalPrefs] = []

    @ft.component
    def Parent() -> Any:
        prefs, _ref, _saved, set_prefs = use_preferences(preferences=store)
        last.append(prefs)
        return _shell(NoticeSource(), prefs, set_prefs)

    async def scenario() -> None:
        app = await render_component(Parent)
        await app.settle(8)
        assert store.gate is not None
        store.gate.set_result(None)
        await app.settle(8)
        assert last[-1].font_size == 20
        assert all(row["font_size"] == 20 for row in store.saved)

    run(scenario())


@pytest.mark.parametrize("read_fails", [False, True])
def test_acknowledgement_does_not_erase_saved_preferences_when_read_fails(
    read_fails: bool,
) -> None:
    original = encode(
        TerminalPrefs(font_size=20, auto_takeover=False, pinned_agents=("host:agent",))
    )

    class Store:
        def __init__(self) -> None:
            self.gate: asyncio.Future[None] | None = None
            self.value = original
            self.writes: list[str] = []

        async def get(self, key: str) -> str:
            self.gate = asyncio.get_running_loop().create_future()
            await self.gate
            if read_fails:
                raise RuntimeError("synthetic temporary storage read error")
            return self.value

        async def set(self, key: str, value: str) -> None:
            self.value = value
            self.writes.append(value)

    store = Store()

    @ft.component
    def Parent() -> Any:
        prefs, _ref, _saved, setter = use_preferences(preferences=store)
        return _shell(NoticeSource(), prefs, setter)

    async def scenario() -> None:
        app = await render_component(Parent)
        await app.settle(8)
        assert store.gate is not None
        assert not store.writes  # no premature write while the read is pending
        store.gate.set_result(None)
        await app.settle(8)
        doc = json.loads(store.value)
        assert doc["font_size"] == 20
        assert doc["auto_takeover"] is False
        assert doc["pinned_agents"] == ["host:agent"]
        if not read_fails:
            assert doc["acknowledged_update"] == "0.3.1"
        else:
            assert store.writes == [] and store.value == original

    run(scenario())


def test_a_failed_read_recovers_on_the_next_edit_and_merges_everything() -> None:
    original = encode(
        TerminalPrefs(font_size=20, auto_takeover=False, pinned_agents=("host:agent",))
    )

    class Store:
        def __init__(self) -> None:
            self.reads = 0
            self.value = original
            self.writes: list[str] = []

        async def get(self, key: str) -> str:
            self.reads += 1
            if self.reads <= 2:  # the first load and the acknowledgement's retry both fail
                raise RuntimeError("synthetic temporary storage read error")
            return self.value

        async def set(self, key: str, value: str) -> None:
            self.value = value
            self.writes.append(value)

    store = Store()
    last: list[TerminalPrefs] = []

    @ft.component
    def Parent() -> Any:
        prefs, _ref, _saved, setter = use_preferences(preferences=store)
        last.append(prefs)
        return ft.Column(
            controls=[
                _shell(NoticeSource(), prefs, setter),
                ft.TextButton(
                    "edit",
                    key="edit-again",
                    on_click=lambda e: setter(replace(prefs, haptics=False)),
                ),
            ]
        )

    async def scenario() -> None:
        app = await render_component(Parent)
        await app.settle(8)
        assert store.writes == [] and store.value == original  # the failed read saved nothing
        assert last[-1].acknowledged_update == "0.3.1"  # kept in memory
        await app.set_props_and_fire("edit-again", {}, "click")
        await app.settle(8)
        doc = json.loads(store.value)
        assert doc["font_size"] == 20
        assert doc["auto_takeover"] is False
        assert doc["pinned_agents"] == ["host:agent"]
        assert doc["acknowledged_update"] == "0.3.1"
        assert doc["haptics"] is False
        assert last[-1].font_size == 20 and last[-1].haptics is False

    run(scenario())


def test_the_notice_value_alone_drives_the_panel() -> None:
    async def scenario() -> None:
        app = await render_component(UpdateNoticePanel, parse(DOC), phone=True)
        await app.wait_for("update-notice-issued")
        assert parse(DOC) is not None
        hidden = await render_component(
            UpdateNoticePanel, parse(DOC | {"available": None}), phone=True
        )
        await hidden.settle(2)
        assert not _has(hidden, "settings-update-notice")

    run(scenario())
