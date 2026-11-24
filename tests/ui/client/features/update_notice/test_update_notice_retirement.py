"""Retiring a notice subscriber releases its pending read."""

from __future__ import annotations

import asyncio
from typing import Any

import flet as ft
import pytest

from relaydeck.client.features.update_notice.hooks import use_update_notice
from tests.support.host_harness import render_component, run


@pytest.mark.parametrize("replacement", [False, True])
def test_notice_read_is_cancelled_on_unmount_or_source_replacement(replacement: bool) -> None:
    async def scenario() -> None:
        tasks: list[asyncio.Task[Any]] = []
        cancelled = asyncio.Event()
        holder: dict[str, Any] = {}

        class PendingSource:
            async def updates(self) -> dict[str, Any]:
                task = asyncio.current_task()
                assert task is not None
                tasks.append(task)
                try:
                    await asyncio.Future[None]()
                finally:
                    cancelled.set()
                return {}

        old = PendingSource()

        @ft.component
        def Notice(source: Any) -> Any:
            use_update_notice(source, True)
            return ft.Text("Mounted")

        @ft.component
        def View() -> Any:
            visible, show = ft.use_state(True)
            source, change = ft.use_state(old)
            holder.update(show=show, change=change)
            return Notice(source) if visible else ft.Text("Signed out")

        app = await render_component(View)
        assert len(tasks) == 1 and not tasks[0].done()
        if replacement:
            holder["change"](object())
        else:
            holder["show"](False)
        await app.settle()
        assert cancelled.is_set()
        assert tasks[0].done() and tasks[0].cancelled()

    run(scenario())
