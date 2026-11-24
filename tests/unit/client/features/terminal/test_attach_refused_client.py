"""A lease-conflict reset shows the holder panel; nothing retries until an explicit Take over."""

from __future__ import annotations

import asyncio

from relaydeck.client.features.terminal.agent_session import AGENT_CONFLICT
from relaydeck.client.features.terminal.controller import (
    EXTERNAL_HOLDER_LABEL,
    TerminalController,
)
from relaydeck.client.features.terminal.models import STATUS_RESET
from relaydeck.client.features.terminal.session.base import DRIVE_REFUSED_EXITED
from relaydeck.client.platform.transport.terminal_stream import (
    TerminalStreamClient as _terminal_stream_factory,
)
from tests.support.scripted_sessions import ScriptedTerminalSocket
from tests.support.terminal.sessions import _agent_session


def test_lease_conflict_reset_marks_owned_elsewhere_without_reattach() -> None:
    async def scenario() -> None:
        socket = ScriptedTerminalSocket()
        controller = TerminalController(
            lambda _: socket,
            "lease",
            1,
            "control",
            lambda _: None,
            stream_factory=_terminal_stream_factory,
        )
        await controller.start("ticket", "e1", 0)
        controller._handle_reset("lease_conflict")
        assert controller.owned_elsewhere and not controller.reattach_needed
        assert controller.pending_resume is None
        assert controller.state.status == STATUS_RESET and controller.state.notice is not None
        controller._handle_reset("stale_epoch")  # an ordinary reset still asks to reattach
        assert controller.reattach_needed
        await controller.close()

    asyncio.run(scenario())


def test_session_enters_conflict_and_refuses_recovery_drives() -> None:
    async def scenario() -> None:
        session, _ops, _factory, _submitted = _agent_session()
        session.open()
        await session.confirm_takeover()
        controller = session._controller
        assert controller is not None
        controller._handle_reset("lease_conflict")
        assert session._classify_run_exit() == AGENT_CONFLICT
        assert session.conflict_owner == EXTERNAL_HOLDER_LABEL
        assert await session.drive_recovery() == DRIVE_REFUSED_EXITED
        await session.dispose()

    asyncio.run(scenario())
