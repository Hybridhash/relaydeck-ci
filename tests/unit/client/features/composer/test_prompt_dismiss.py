"""A prompt that status checks can no longer settle can be dismissed, and nothing else can."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from relaydeck.client.features.commands.recovery import PendingCommands
from relaydeck.client.features.composer.model import ComposerStore
from relaydeck.client.models.commands import wording as words
from relaydeck.client.models.commands.pending import DISMISSED
from relaydeck.client.models.commands.wording import dismissed
from relaydeck.client.platform.storage.memory_store import MemoryCommandStore
from relaydeck.client.ports.commands import RecoveryError
from relaydeck.client.ports.transport import TransportError
from tests.support.creation.pending_command_records import command, record
from tests.support.host_harness import run


def _journal(store: MemoryCommandStore | None = None, account: str = "acct") -> PendingCommands:
    return PendingCommands(store or MemoryCommandStore(), "https://gw", account, "dev")


def _backing(journal: PendingCommands) -> MemoryCommandStore:
    assert isinstance(journal.store, MemoryCommandStore)
    return journal.store


async def _stuck(journal: PendingCommands, identity: str = "one", **kw: str) -> dict[str, Any]:
    body = command(identity, **kw)
    await journal.begin(body)
    await journal.observe(body["host_id"], body["command_id"], None, unresolvable=True)
    return body


def _row(journal: PendingCommands, body: dict[str, Any]) -> dict[str, Any]:
    return journal.records[(body["host_id"], body["command_id"])]


def test_only_a_prompt_the_last_check_could_not_settle_offers_dismiss() -> None:
    async def scenario() -> None:
        journal = _journal()
        fresh = command("fresh")
        await journal.begin(fresh)
        assert not journal.can_dismiss(_row(journal, fresh))

        # A read that failed proves nothing, so a recent message keeps checking.
        failed_read = command("failedread", target="agent-2")
        await journal.begin(failed_read)
        await journal.observe("home-mac", failed_read["command_id"], None)
        assert _row(journal, failed_read)["outcome"] == "unknown"
        assert not journal.can_dismiss(_row(journal, failed_read))

        for status, target in (("accepted", "agent-3"), ("dispatched", "agent-4")):
            body = command(status, target=target)
            await journal.begin(body)
            await journal.observe("home-mac", body["command_id"], record(body, status))
            assert not journal.can_dismiss(_row(journal, body))

        proven = command("proven", target="agent-5")
        await journal.begin(proven)
        await journal.observe("home-mac", proven["command_id"], record(proven, "rejected"))
        assert not journal.can_dismiss(_row(journal, proven))

        stuck = await _stuck(journal, "stuck", target="agent-6")
        assert journal.can_dismiss(_row(journal, stuck))

    run(scenario())


def test_a_later_failed_read_keeps_the_answer_and_a_record_replaces_it() -> None:
    async def scenario() -> None:
        journal = _journal()
        body = await _stuck(journal)
        await journal.observe(body["host_id"], body["command_id"], None)
        assert journal.can_dismiss(_row(journal, body))
        await journal.observe(body["host_id"], body["command_id"], record(body, "dispatched"))
        assert not journal.can_dismiss(_row(journal, body))
        assert "unresolvable" not in _row(journal, body)

    run(scenario())


def test_an_unreadable_or_wrong_target_record_keeps_the_answer() -> None:
    async def scenario() -> None:
        journal = _journal()
        body = await _stuck(journal)
        await journal.observe(body["host_id"], body["command_id"], {"nonsense": True})
        assert journal.can_dismiss(_row(journal, body))
        wrong = record(body, "accepted")
        wrong["target"] = {**body["target"], "agent_id": "someone-else"}
        await journal.observe(body["host_id"], body["command_id"], wrong)
        assert journal.can_dismiss(_row(journal, body))
        await journal.observe(body["host_id"], body["command_id"], record(body, "dispatched"))
        assert "unresolvable" not in _row(journal, body)

        proven = command("proven-final", target="agent-7")
        await journal.begin(proven)
        await journal.observe("home-mac", proven["command_id"], record(proven, "rejected"))
        await journal.observe("home-mac", proven["command_id"], None, unresolvable=True)
        assert _row(journal, proven)["outcome"] == "rejected"
        assert "unresolvable" not in _row(journal, proven)

    run(scenario())


def test_the_answer_survives_a_restart() -> None:
    async def scenario() -> None:
        journal = _journal()
        body = await _stuck(journal)
        restored = _journal(_backing(journal))
        await restored.load()
        assert restored.can_dismiss(_row(restored, body))

    run(scenario())


def test_dismiss_releases_the_agent_sends_nothing_and_survives_a_restart() -> None:
    async def scenario() -> None:
        journal = _journal()
        body = await _stuck(journal)
        with pytest.raises(RecoveryError):
            await journal.begin(command("again"))
        heard: list[dict[str, Any]] = []
        journal.subscribe_outcomes(heard.append)
        writes = len(_backing(journal).data)

        await journal.dismiss(body["host_id"], body["command_id"], "agent-1")

        assert not journal.records and len(_backing(journal).data) == writes
        assert [(d["command_id"], d["status"]) for d in heard] == [(body["command_id"], DISMISSED)]
        await journal.begin(command("again"))
        assert [row["id"] for row in journal.records.values()] == ["pending-again"]
        restored = _journal(_backing(journal))
        await restored.load()
        assert set(restored.records) == {("home-mac", "pending-again")}

    run(scenario())


def test_dismiss_is_scoped_to_the_account_host_agent_and_message() -> None:
    async def scenario() -> None:
        journal = _journal()
        body = await _stuck(journal)
        host, identity = body["host_id"], body["command_id"]
        for args in (
            (host, identity, "agent-2"),
            ("other-mac", identity, "agent-1"),
            (host, "pending-other", "agent-1"),
        ):
            with pytest.raises(RecoveryError):
                await journal.dismiss(*args)
        # Another account on the same device never sees this account's rows.
        other = _journal(_backing(journal), account="someone-else")
        with pytest.raises(RecoveryError):
            await other.dismiss(host, identity, "agent-1")
        assert journal.can_dismiss(_row(journal, body))
        restored = _journal(_backing(journal))
        await restored.load()
        assert set(restored.records) == {(host, identity)}

    run(scenario())


@pytest.mark.parametrize("outcome", ["pending", "accepted", "dispatched", "rejected"])
def test_a_message_that_can_still_resolve_or_is_proven_cannot_be_dismissed(outcome: str) -> None:
    async def scenario() -> None:
        journal = _journal()
        body = command()
        await journal.begin(body)
        if outcome != "pending":
            await journal.observe("home-mac", body["command_id"], record(body, outcome))
        with pytest.raises(RecoveryError):
            await journal.dismiss("home-mac", body["command_id"], "agent-1")
        assert (body["host_id"], body["command_id"]) in journal.records

    run(scenario())


@pytest.mark.parametrize("kind", ["rejected", "other-kind"])
def test_stored_rows_cannot_carry_the_mark_where_it_does_not_apply(kind: str) -> None:
    async def scenario() -> None:
        journal = _journal()
        body = await _stuck(journal)
        doc = json.loads(_backing(journal).data[journal.key])
        row = doc["records"][0]
        if kind == "rejected":
            row["outcome"] = "rejected"
        else:
            row.update(kind="agent.rename")
        _backing(journal).data[journal.key] = json.dumps(doc)
        restored = _journal(_backing(journal))
        with pytest.raises(RecoveryError):
            await restored.load()
        assert body

    run(scenario())


def test_dismissal_settles_the_composer_message_and_keeps_the_draft() -> None:
    async def scenario() -> None:
        journal = _journal()
        submitted: list[dict[str, Any]] = []

        async def lost(body: dict[str, Any]) -> dict[str, Any]:
            await journal.begin(body)
            submitted.append(body)
            raise TimeoutError

        store = ComposerStore(
            target="agent-1",
            host_id="home-mac",
            host_epoch="epoch-7",
            agent_id="agent-1",
            prompt_text=None,
            submit_command=lost,
            subscribe_command=journal.subscribe_outcomes,
        )
        store.agent_label = "Docs"
        store.replace_draft("first")
        assert await store.send(prompt_only=True) == "unknown"
        identity = submitted[0]["command_id"]
        await journal.observe("home-mac", identity, None, unresolvable=True)
        store.replace_draft("something new")

        await journal.dismiss("home-mac", identity, "agent-1")

        assert store.messages[0].state.message == dismissed("Docs")
        assert store.submission == "idle" and store.error is None
        assert store.draft == "something new"
        assert len(submitted) == 1

    run(scenario())


def test_dismissal_of_one_agents_message_leaves_another_agents_composer_alone() -> None:
    async def scenario() -> None:
        journal = _journal()
        calls: list[str] = []

        async def lost(body: dict[str, Any]) -> dict[str, Any]:
            await journal.begin(body)
            calls.append(body["command_id"])
            raise TimeoutError

        stores = []
        for agent in ("agent-1", "agent-2"):
            store = ComposerStore(
                target=agent,
                host_id="home-mac",
                host_epoch="epoch-7",
                agent_id=agent,
                prompt_text=None,
                submit_command=lost,
                subscribe_command=journal.subscribe_outcomes,
            )
            store.replace_draft(f"for {agent}")
            assert await store.send(prompt_only=True) == "unknown"
            stores.append(store)
        for identity in calls:
            await journal.observe("home-mac", identity, None, unresolvable=True)

        await journal.dismiss("home-mac", calls[0], "agent-1")

        assert stores[0].submission == "idle"
        assert stores[1].submission == "unknown"

    run(scenario())


@pytest.mark.parametrize("agent", [False, True])
def test_the_mark_is_ignored_on_a_row_that_is_not_a_prompt(agent: bool) -> None:
    from tests.support.creation.pending_creation import _creation

    async def scenario() -> None:
        journal = _journal()
        body = _creation(agent)
        await journal.begin(body)
        await journal.observe("home-mac", body["command_id"], None, unresolvable=True)
        assert "unresolvable" not in _row(journal, body)
        assert not journal.can_dismiss(_row(journal, body))
        restored = _journal(_backing(journal))
        await restored.load()  # a poisoned store would refuse to load
        assert set(restored.records) == set(journal.records)

    run(scenario())


@pytest.mark.parametrize(
    "code,expected",
    [
        ("too_large", words.too_long("Docs")),
        ("forbidden", words.not_allowed("Docs")),
        ("unsupported_capability", words.not_allowed("Docs")),
        ("stale_epoch", words.agent_gone("Docs")),
        ("agent_not_found", words.agent_gone("Docs")),
        ("rate_limited", words.TOO_MANY),
        ("stale_revision", words.not_sent("Docs")),
        ("unauthenticated", words.not_sent("Docs")),
    ],
)
def test_a_refusal_before_acceptance_says_why_when_the_cause_will_not_go_away(
    code: str, expected: str
) -> None:
    async def scenario() -> None:
        calls: list[dict[str, Any]] = []

        async def refused(body: dict[str, Any]) -> dict[str, Any]:
            calls.append(body)
            raise TransportError(code, "server words that must not show", False)

        store = ComposerStore(
            target="agent-1",
            host_id="home-mac",
            host_epoch="epoch-7",
            agent_id="agent-1",
            prompt_text=None,
            submit_command=refused,
        )
        store.agent_label = "Docs"
        store.replace_draft("kept question")
        assert await store.send(prompt_only=True) == "failed"
        assert store.error == expected and "server words" not in str(store.error)
        assert store.draft == "kept question" and store.can_send and len(calls) == 1

    run(scenario())


async def _two_sends(
    journal: PendingCommands, standing: bool
) -> tuple[ComposerStore, list[dict[str, Any]]]:
    """A store whose first send is lost and whose second is refused for it."""
    submitted: list[dict[str, Any]] = []

    async def check() -> None:
        return None

    async def submit(body: dict[str, Any]) -> dict[str, Any]:
        await journal.begin(body, check=check)
        submitted.append(body)
        raise TimeoutError

    store = ComposerStore(
        target="agent-1",
        host_id="home-mac",
        host_epoch="epoch-7",
        agent_id="agent-1",
        prompt_text=None,
        submit_command=submit,
        subscribe_command=journal.subscribe_outcomes,
    )
    store.agent_label = "Docs"
    store.replace_draft("first")
    assert await store.send(prompt_only=True) == "unknown"
    if standing:
        await journal.observe("home-mac", submitted[0]["command_id"], None, unresolvable=True)
    store.replace_draft("second")
    assert await store.send(prompt_only=True) == "failed"
    assert store.error == words.earlier_unconfirmed("Docs")
    return store, submitted


async def _checked(store: ComposerStore) -> None:
    check = store.error.check  # type: ignore[union-attr]
    check()
    async with asyncio.timeout(2):
        while store._reconcile_tasks:
            await asyncio.sleep(0)


def test_dismissing_clears_the_refusal_that_waited_for_it() -> None:
    async def scenario() -> None:
        journal = _journal()
        store, submitted = await _two_sends(journal, standing=True)
        await journal.dismiss("home-mac", submitted[0]["command_id"], "agent-1")
        assert store.error is None and store.submission == "idle"
        assert store.draft == "second" and len(submitted) == 1

    run(scenario())


def test_a_check_that_leaves_it_dismissable_points_at_the_banner() -> None:
    async def scenario() -> None:
        journal = _journal()
        store, submitted = await _two_sends(journal, standing=True)
        await _checked(store)
        assert store.error == words.still_unconfirmed()
        assert store.submission == "failed" and store.draft == "second"
        await journal.dismiss("home-mac", submitted[0]["command_id"], "agent-1")
        assert store.error is None and store.submission == "idle"

    run(scenario())


def test_a_check_that_leaves_it_still_being_checked_keeps_the_line() -> None:
    async def scenario() -> None:
        journal = _journal()
        store, _ = await _two_sends(journal, standing=False)
        await _checked(store)
        assert store.error == words.earlier_unconfirmed("Docs")
        assert store.submission == "failed" and getattr(store.error, "check", None) is not None

    run(scenario())


def test_a_check_that_released_the_earlier_message_clears_the_line() -> None:
    async def scenario() -> None:
        journal = _journal()
        store, submitted = await _two_sends(journal, standing=False)
        await journal.observe(
            "home-mac", submitted[0]["command_id"], record(submitted[0], "rejected")
        )
        await _checked(store)
        assert store.error is None and store.submission == "idle"

    run(scenario())
