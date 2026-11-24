"""Dismissing a prompt the gateway can no longer settle frees the agent and sends nothing."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from relaydeck.client.features.composer.model import ComposerStore
from relaydeck.client.models.commands.wording import earlier_unconfirmed, still_unconfirmed
from relaydeck.client.ports.commands import UnconfirmedEarlier
from relaydeck.contracts import paths
from tests.support.creation.pending_command_records import command
from tests.support.creation.unresolvable_prompt import REASONS, stuck_prompt


@pytest.mark.parametrize("reason", REASONS)
def test_dismiss_is_offered_after_the_gateway_says_it_cannot_tell(
    tmp_path: Path, reason: str
) -> None:
    async def scenario() -> None:
        async with stuck_prompt(tmp_path, reason) as scene:
            (row,) = scene.pending.records.values()
            # Nothing has been asked yet, so the message is still only being checked.
            assert not scene.pending.can_dismiss(row)
            await scene.pending.reconcile(scene.transport.command_status, frozenset({"home-mac"}))
            (row,) = scene.pending.records.values()
            assert row["outcome"] == "unknown" and scene.pending.can_dismiss(row)

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", REASONS)
def test_dismiss_admits_a_fresh_prompt_and_causes_no_request(tmp_path: Path, reason: str) -> None:
    async def scenario() -> None:
        async with stuck_prompt(tmp_path, reason) as scene:
            identity = scene.submitted[0]["command_id"]
            await scene.pending.reconcile(scene.transport.command_status, frozenset({"home-mac"}))
            with pytest.raises(UnconfirmedEarlier):
                await scene.pending.begin(command("fresh", target="agent-1"))
            asked = list(scene.requests)

            await scene.pending.dismiss("home-mac", identity, "agent-1")

            assert scene.requests == asked
            assert len(scene.submitted) == 1
            assert scene.store.draft == "kept question" and scene.store.submission == "idle"
            fresh = command("fresh", target="agent-1")
            response = await scene.post(fresh)
            assert response.status_code == 202, response.text
            await scene.pending.begin(fresh)
            assert [r["id"] for r in scene.pending.records.values()] == ["pending-fresh"]
            assert ("GET", paths.host_command("home-mac", identity)) in asked

    asyncio.run(scenario())


def test_a_refused_send_offers_check_now_then_points_at_dismiss_and_dismissing_clears_it(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with stuck_prompt(tmp_path, "expired-history") as scene:
            second = ComposerStore(
                target="agent-1",
                host_id="home-mac",
                host_epoch="epoch-7",
                agent_id="agent-1",
                prompt_text=None,
                submit_command=scene.transport.submit_command,
                subscribe_command=scene.pending.subscribe_outcomes,
            )
            second.agent_label = "Docs"
            second.replace_draft("next question")
            posts = [r for r in scene.requests if r[0] == "POST"]

            assert await second.send(prompt_only=True) == "failed"

            assert second.error == earlier_unconfirmed("Docs")
            assert second.draft == "next question"
            assert [r for r in scene.requests if r[0] == "POST"] == posts
            check = getattr(second.error, "check", None)
            assert check is not None
            check()
            async with asyncio.timeout(2):
                while second.error == earlier_unconfirmed("Docs"):
                    await asyncio.sleep(0.001)
            identity = scene.submitted[0]["command_id"]
            assert ("POST", paths.host_command_resolve("home-mac", identity)) in scene.requests
            (row,) = scene.pending.records.values()
            assert scene.pending.can_dismiss(row)
            # Still unconfirmed: the line stays and points at the banner's Dismiss.
            assert second.error == still_unconfirmed() and second.submission == "failed"
            assert second.draft == "next question"
            await scene.pending.dismiss("home-mac", identity, "agent-1")
            assert second.error is None and second.submission == "idle"
            assert second.draft == "next question"

    asyncio.run(scenario())
