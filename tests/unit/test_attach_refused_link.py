"""An upstream attach refusal crosses the link as one typed message, never as a reset."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from relaydeck.connector.herdr import HerdrAdapter
from relaydeck.connector.link import ConnectorLink, LinkConfig
from relaydeck.connector.source import HerdrSource


def test_refusal_is_sent_once_for_the_current_epoch_only(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = HerdrSource(HerdrAdapter(socket_path=str(tmp_path / "unused.sock")))
        link = ConnectorLink(
            LinkConfig("http://127.0.0.1:9", "h1", "synthetic", str(tmp_path / "c.db")), source
        )
        link._last_epochs = {"owned": "e-1", "stale": "e-9"}
        source._epochs = {"owned": "e-1", "stale": "e-1"}
        source._attach_refused = {"owned": "e-1", "stale": "e-1"}
        sent: list[dict[str, Any]] = []

        class Transport:
            async def send_text(self, text: str) -> None:
                sent.append(json.loads(text))

        try:
            await link._push_terminal_updates(Transport())  # type: ignore[arg-type]
        finally:
            link._journal.close()
        refusals = [doc for doc in sent if doc["kind"] == "terminal.attach_refused"]
        assert refusals == [
            {
                "kind": "terminal.attach_refused",
                "host_id": "h1",
                "terminal_id": "owned",
                "epoch": "e-1",
                "reason": "terminal_owned",
            }
        ]
        assert not [doc for doc in sent if doc["kind"] == "terminal.reset_required"]

    asyncio.run(scenario())


def test_routes_carry_the_viewer_takeover_flag(tmp_path: Path) -> None:
    source = HerdrSource(HerdrAdapter(socket_path=str(tmp_path / "unused.sock")))
    link = ConnectorLink(
        LinkConfig("http://127.0.0.1:9", "h1", "synthetic", str(tmp_path / "c.db")), source
    )
    seen: list[dict[str, dict[str, Any]]] = []
    link._note_routes = seen.append  # type: ignore[method-assign]
    try:
        link._handle_epochs(
            {
                "host_id": "h1",
                "epochs": [
                    {"terminal_id": "a", "epoch": "e1", "kind": "shell", "takeover": True},
                    {"terminal_id": "b", "epoch": "e1", "kind": "shell", "takeover": "yes"},
                    {"terminal_id": "c", "epoch": "e1", "kind": "agent"},
                ],
            }
        )
    finally:
        link._journal.close()
    assert [route["takeover"] for route in seen[0].values()] == [True, False, False]
