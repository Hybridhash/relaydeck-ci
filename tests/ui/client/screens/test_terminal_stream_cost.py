"""Packet counters do not invalidate a mounted terminal's declarative screen."""

import asyncio
from dataclasses import replace

from relaydeck.client.features.terminal.models import TerminalViewState
from relaydeck.client.screens.shell.screen import mount_shell_screen
from tests.support.host_harness import render_component
from tests.support.terminal.sessions import _session


def test_mounted_session_streaming_does_not_patch_status_per_packet() -> None:
    async def scenario() -> None:
        session, _, _ = _session()
        await session.open("terminal")
        controller = session.controller
        assert controller is not None
        controller._client.epoch = "stream"
        app = await render_component(mount_shell_screen, session, lambda: None, lambda: None)
        state = TerminalViewState(status="live", epoch="stream", lease_mode="view")
        controller._handle_output(0, b"warm")
        await app.settle()
        notifications: list[int] = []
        unsubscribe = session.subscribe(lambda _: notifications.append(1))
        app.conn.sent.clear()
        try:
            for offset in range(1, 121):
                controller._handle_output(4 + (offset - 1) * 1024, b"x" * 1024)
                await asyncio.sleep(1 / 120)
            await app.settle()
            assert session.term_state.applied_offset == 4 + 120 * 1024
            assert notifications == []
            assert len(app.conn.sent) <= 2
            session.update_term_state(replace(state, status="reset"))
            assert notifications
        finally:
            unsubscribe()
            await session.dispose()

    asyncio.run(scenario())


def test_semantic_state_burst_coalesces_and_unsubscribe_retires_timer() -> None:
    from relaydeck.client.features.terminal.models import NoticeModel

    async def scenario() -> None:
        session, _, _ = _session()
        notices: list[str] = []
        unsubscribe = session.subscribe(lambda _: notices.append(session.term_state.status))
        state = TerminalViewState(status="live", epoch="epoch")
        session.update_term_state(state)
        for index in range(100):
            session.update_term_state(replace(state, notice=NoticeModel("warning", str(index))))
        assert notices == ["live"]
        await asyncio.sleep(0.12)
        assert notices == ["live", "live"]
        session.update_term_state(replace(state, status="reset"))
        assert notices[-1] == "reset"
        session.update_term_state(replace(state, notice=NoticeModel("warning", "pending")))
        unsubscribe()
        count = len(notices)
        await asyncio.sleep(0.12)
        assert len(notices) == count
        await session.dispose()

    asyncio.run(scenario())


def test_mounted_jump_label_remains_semantic_while_output_accumulates() -> None:
    from relaydeck.client.features.terminal.models import apply_bytes, user_scroll_away
    from tests.support.host_harness import button_label

    async def scenario() -> None:
        session, _, _ = _session()
        app = await render_component(mount_shell_screen, session, lambda: None, lambda: None)
        state = user_scroll_away(TerminalViewState(status="live", epoch="epoch", applied_offset=1))
        session.update_term_state(state)
        await app.settle()
        notices: list[int] = []
        unsubscribe = session.subscribe(lambda _: notices.append(1))
        app.conn.sent.clear()
        try:
            for _ in range(2):
                state = apply_bytes(state, 4096)
                session.update_term_state(state)
                await app.settle()
                assert button_label(app.find("scroll-jump")) == "Jump to latest"
            assert session.term_state.scroll.unseen_bytes == 8192
            assert not notices and not app.conn.sent
            session.update_term_state(replace(state, lease_mode="control"))
            await app.settle()
            assert button_label(app.find("scroll-jump")) == "Jump to latest"
        finally:
            unsubscribe()
            await session.dispose()

    asyncio.run(scenario())


def test_phone_viewer_keybar_appears_once_when_output_becomes_available() -> None:
    from relaydeck.client.features.terminal.models import apply_bytes

    async def scenario() -> None:
        session, _, _ = _session()
        app = await render_component(
            mount_shell_screen, session, lambda: None, lambda: None, phone=True
        )
        state = TerminalViewState(status="live", epoch="epoch", lease_mode="view")
        session.update_term_state(state)
        await app.settle()
        assert app.find("shell-mobile-keybar")._b.visible is False
        notices: list[int] = []
        unsubscribe = session.subscribe(lambda _: notices.append(1))
        try:
            state = apply_bytes(state, 4096)
            session.update_term_state(state)
            await app.settle(rounds=15)
            assert app.find("shell-mobile-keybar")._b.visible is True
            assert app.find("terminal-window-status").value == "live · view"
            assert len(notices) == 1
            app.conn.sent.clear()
            session.update_term_state(apply_bytes(state, 4096))
            await app.settle()
            assert len(notices) == 1 and not app.conn.sent
        finally:
            unsubscribe()
            await session.dispose()

    asyncio.run(scenario())


def test_phone_detail_menu_status_does_not_render_stream_counters() -> None:
    from relaydeck.client.features.terminal.models import apply_bytes
    from relaydeck.client.screens.terminal.screen import mount_agent_screen
    from tests.support.terminal.sessions import _agent_session

    async def scenario() -> None:
        session, _, _, _ = _agent_session()
        session.open()
        state = TerminalViewState(status="live", epoch="stream", applied_offset=1)
        session.update_term_state(state)
        app = await render_component(mount_agent_screen, session, phone=True)
        await app.settle()
        try:
            assert app.find("terminal-window-status").value == "live · view"
            app.conn.sent.clear()
            session.update_term_state(apply_bytes(state, 8192))
            await app.settle()
            assert app.find("terminal-window-status").value == "live · view"
            assert not app.conn.sent
        finally:
            await session.dispose()

    asyncio.run(scenario())
