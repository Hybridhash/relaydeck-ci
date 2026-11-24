"""Fast host sessions for presentation-only catalog specimens."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

from relaydeck.devtools.catalog import Preview
from relaydeck.devtools.catalog_model import Case
from tests.support.host_harness import HostApp, render_component


async def _turns(app: HostApp, rounds: int = 20) -> None:
    del app
    for _ in range(rounds):
        await asyncio.sleep(0)


async def render(case: Case) -> HostApp:
    # Catalog callbacks use loop turns; they perform no transport, timers or storage work.
    with patch.object(HostApp, "settle", _turns):
        return await render_component(Preview, case)


def controls(app: HostApp) -> Iterator[Any]:
    from tests.support.ui.focus import iter_controls

    roots = [app.page.views[0].controls]
    dialogs = getattr(app.page, "_dialogs", None)
    if dialogs is not None:
        roots.extend(dialogs.controls or [])
    yield from iter_controls(roots)
