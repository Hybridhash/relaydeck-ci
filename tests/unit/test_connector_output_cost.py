"""Long outages and a fast producer keep bounded publication cost."""

import asyncio
import base64
from pathlib import Path
from typing import Any

from relaydeck.connector.link import CHUNK_BYTES, ConnectorLink, LinkConfig
from relaydeck.devtools.fakes.connector import FixtureAdapter


def test_backoff_is_finite_after_an_unbounded_outage(tmp_path: Path) -> None:
    link = ConnectorLink(
        LinkConfig(
            "http://localhost", "host", "secret", journal_path=str(tmp_path / "commands.db")
        ),
        FixtureAdapter(),
    )
    try:
        for attempt in (0, 10, 1023, 1024, 10**100):
            delay = link._backoff_ms(attempt)
            assert 0 <= delay <= 1.5 * link._config.backoff_max_ms
    finally:
        link._journal.close()


def test_sustained_output_batches_and_idle_first_byte_remains_prompt(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = FixtureAdapter()
        link = ConnectorLink(
            LinkConfig(
                "http://localhost", "host", "secret", journal_path=str(tmp_path / "commands.db")
            ),
            source,
        )
        frames: list[bytes] = []
        sent = asyncio.Event()

        class Transport:
            async def send_text(self, text: str) -> None:
                import json

                body: dict[str, Any] = json.loads(text)
                frames.append(base64.b64decode(body["data_b64"]))
                sent.set()

        available = asyncio.Event()
        task = asyncio.create_task(link._forward_pty(Transport(), available))  # type: ignore[arg-type]
        try:
            source.script_output("terminal", "epoch", b"first")
            available.set()
            await asyncio.wait_for(sent.wait(), 0.05)
            assert frames == [b"first"]
            for _ in range(100):
                source.script_output("terminal", "epoch", b"x" * 1024)
                available.set()
                await asyncio.sleep(0.001)
            await asyncio.sleep(0.04)
            assert b"".join(frames) == b"first" + b"x" * (100 * 1024)
            assert max(map(len, frames)) <= CHUNK_BYTES
            assert len(frames) < 25
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            link._journal.close()

    asyncio.run(scenario())
