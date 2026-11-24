"""Pending context stays in memory and never enters routes or diagnostics."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from relaydeck.client.features.ask_agent.handoff import AskHandoff, Identity, PendingAsk
from relaydeck.client.features.ask_agent.model import AskScope
from relaydeck.client.features.changes.reading_bookmark import ReadingBookmark
from relaydeck.client.features.changes.state import ChangesModel
from relaydeck.client.models.routing import format_route


def test_pending_context_has_no_serialization_or_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    async def scenario() -> None:
        scope = AskScope("file", "working", "snapshot", "a" * 40, path="private/sentinel.txt")
        bookmark = ReadingBookmark.capture(ChangesModel(selected_path="private/sentinel.txt"))
        identity = Identity("account", object(), "host:term", "epoch")
        request = PendingAsk(identity, scope, "sentinel question", bookmark, "Session")
        handoff = AskHandoff()
        handoff.begin(request)
        handoff.observe(identity, terminal=True, changes=False)

        class Unprepared:
            question_readiness = "Stored draft could not be verified."

            def stage_question(self, *args: Any, **kwargs: Any) -> str:
                pytest.fail("unverified storage cannot stage a question")

        await handoff.drain(Unprepared())
        assert "sentinel" not in repr(request) + repr(scope) + repr(bookmark) + repr(identity)
        assert "sentinel" not in format_route("agents", identity.key)
        handoff.clear()
        assert handoff.pending is None and handoff.returned is None

    asyncio.run(scenario())
    assert "sentinel" not in caplog.text
