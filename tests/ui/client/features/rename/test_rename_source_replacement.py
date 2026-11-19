"""An in-flight rename is never replayed on a replacement source."""

from __future__ import annotations

import asyncio
from typing import Any

import flet as ft
import pytest

from relaydeck.client.features.rename.rename import RenameDialog
from tests.support.host_harness import render_component, run
from tests.support.ui.navigation import _state


@pytest.mark.parametrize("pending_operation", ["submit", "status"])
def test_rename_replacement_reports_once_without_resubmitting(pending_operation: str) -> None:
    async def scenario() -> None:
        gate = asyncio.Event()
        entered = asyncio.Event()
        holder: dict[str, Any] = {}
        outcomes: list[tuple[str, bool]] = []
        closed: list[bool] = []

        class Source:
            def __init__(self, retired: bool) -> None:
                self.retired = retired
                self.submitted: list[dict[str, Any]] = []
                self.reads = 0

            def record(self, status: str) -> dict[str, Any]:
                return {
                    **{
                        key: self.submitted[0][key]
                        for key in ("protocol", "command_id", "host_id", "kind", "target")
                    },
                    "status": status,
                    "updated_ms": 1,
                }

            async def submit_command(self, body: dict[str, Any]) -> dict[str, Any]:
                self.submitted.append(body)
                if self.retired and pending_operation == "submit":
                    entered.set()
                    await gate.wait()
                return self.record("accepted")

            async def command_status(self, host_id: str, command_id: str) -> dict[str, Any]:
                self.reads += 1
                assert host_id == "home-mac"
                assert command_id == self.submitted[0]["command_id"]
                entered.set()
                await gate.wait()
                return self.record("succeeded")

        old, new = Source(True), Source(False)

        @ft.component
        def View() -> Any:
            source, change = ft.use_state(old)
            holder["change"] = change
            return RenameDialog(
                agent_key="home-mac:term-1",
                initial_name="web",
                state=_state(),
                transport=source,
                preset="new-name",
                on_close=lambda: closed.append(True),
                on_result=lambda name, ok: outcomes.append((name, ok)),
            )

        app = await render_component(View)
        assert entered.is_set() and len(old.submitted) == 1
        holder["change"](new)
        await app.settle()
        assert not new.submitted and outcomes == []
        gate.set()
        await app.settle()
        assert outcomes == [("new-name", False)]
        assert not new.submitted and new.reads == 0
        assert old.reads == int(pending_operation == "status")
        assert len(old.submitted) == 1 and closed == []
        await app.settle()
        assert outcomes == [("new-name", False)]

    run(scenario())
