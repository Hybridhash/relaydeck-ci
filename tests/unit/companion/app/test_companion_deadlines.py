"""Real SQLite retention deadlines and cross-thread wakes do not need idle scans."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from relaydeck.companion.app.runtime import CompanionRuntime
from relaydeck.companion.config import load_config
from relaydeck.gateway.features.notifications.adapters import webpush, webpush_store
from relaydeck.gateway.features.notifications.adapters.webpush_store import WebPushStore
from relaydeck.gateway.features.notifications.service import ProviderOutcome
from relaydeck.host.concurrency.deadlines import DeadlineWake
from relaydeck.host.persistence import contracts as limits
from relaydeck.host.persistence.contracts import DraftTarget, Table
from relaydeck.host.persistence.sqlite_store import open_store
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)
from tests.support.companion.asgi_companion import config_file
from tests.support.persistence.persistence import Config, MemoryKeys, record

pytestmark = pytest.mark.usefixtures("hosted_web_bundle")


def subscribe(store: WebPushStore, now: int) -> None:
    public = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    store.put_subscription(
        "acct",
        "device",
        "https://fcm.googleapis.com/synthetic",
        webpush.b64u(public),
        webpush.b64u(b"a" * 16),
        now,
    )
    store.set_sending(True)


def test_committed_writes_wake_after_unlock_but_reads_and_rollbacks_do_not(tmp_path: Path) -> None:
    store = open_store(Config(tmp_path / "private"), MemoryKeys())
    wakes: list[int | None] = []
    store.on_change = lambda: wakes.append(store.next_expiry_ms())
    try:
        assert store.next_expiry_ms() is None
        with store.transaction() as tx:
            assert tx.put(record(expiry=100), expected_revision=None)
        assert wakes == [100]
        with store.transaction() as tx:
            assert tx.get(Table.WEB_SESSIONS, "token") is not None
        with pytest.raises(RuntimeError):
            with store.transaction() as tx:
                tx.delete(Table.WEB_SESSIONS, "token", expected_revision=None)
                raise RuntimeError("rollback")
        assert wakes == [100]

        def broken() -> None:
            raise RuntimeError("synthetic wake failure")

        store.on_change = broken
        with store.transaction() as tx:
            assert tx.delete(Table.WEB_SESSIONS, "token", expected_revision=None)
        assert store.next_expiry_ms() is None  # committed even when scheduling hint fails
    finally:
        store.close()


def test_nearest_retry_and_expiry_survive_reopening(tmp_path: Path) -> None:
    store = WebPushStore(tmp_path)
    now = 1_700_000_000_000
    subscribe(store, now)
    assert store.enqueue("acct", "device", "event", "attention", now=now, ttl_ms=50_000)
    store.retry_or_drop(store.due(now)[0], now)
    assert store.next_due_ms() == now + webpush_store.BACKOFF_BASE_MS
    store.close()
    reopened = WebPushStore(tmp_path)
    try:
        assert reopened.next_due_ms() == now + webpush_store.BACKOFF_BASE_MS
        reopened.set_sending(False)
        assert reopened.next_due_ms() == now + 50_000  # purge still runs with sending disabled
        reopened.purge(now + 50_000)
        due = reopened.next_due_ms()
        assert due is not None and due > now + 50_000  # route token's independent expiry
    finally:
        reopened.close()


def test_deadline_wait_wakes_from_worker_and_cancellation_leaves_no_tasks() -> None:
    async def scenario() -> None:
        baseline = set(asyncio.all_tasks())
        signal = DeadlineWake()
        signal.bind()
        waiter = asyncio.create_task(signal.wait(None, None))
        await asyncio.to_thread(signal.wake)
        await asyncio.wait_for(waiter, 1)
        signal.event.clear()
        waiter = asyncio.create_task(signal.wait(asyncio.Event(), 60))
        await asyncio.sleep(0)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        assert not [t for t in asyncio.all_tasks() - baseline if not t.done()]

    asyncio.run(scenario())


def test_empty_key_rotation_wakes_and_advertises_pending_maintenance(tmp_path: Path) -> None:
    store = open_store(Config(tmp_path / "private"), MemoryKeys())
    wakes: list[int] = []
    store.on_change = lambda: wakes.append(1)
    try:
        assert store.next_expiry_ms() is None
        store.rotate_keys()
        assert wakes == [1]
        assert store.next_expiry_ms() is not None
        store.sweep(int(time.time() * 1000))
        assert store.next_expiry_ms() is None
    finally:
        store.close()


def test_draft_reads_rearm_idle_expiry_and_deleted_drafts_keep_tombstone_expiry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = open_store(Config(tmp_path / "private"), MemoryKeys())
    now = [10_000]
    monkeypatch.setattr(store, "_now_ms", lambda: now[0])
    drafts = store.scoped_drafts("origin", "acct", "device", "tab")
    target = DraftTarget("host", "terminal")

    async def scenario() -> None:
        saved = await drafts.save(target, "kept draft", None)
        assert store.next_expiry_ms() == now[0] + limits.DRAFT_IDLE_MS
        now[0] += 50_000
        assert await drafts.load(target) == saved
        assert store.next_expiry_ms() == now[0] + limits.DRAFT_IDLE_MS
        assert await drafts.delete_revision(target, saved.revision)
        assert store.next_expiry_ms() == saved.updated_ms + limits.DRAFT_IDLE_MS

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_runtime_picks_up_a_persisted_future_retry_after_reopening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(webpush_store, "BACKOFF_BASE_MS", 300)

    async def scenario() -> None:
        runtime = CompanionRuntime(load_config(config_file(tmp_path)))
        runtime.stop = asyncio.Event()
        runtime.gateway = SimpleNamespace(
            now_ms=lambda: int(time.time() * 1000),
            push=SimpleNamespace(category_allowed=lambda *a: True),
        )
        store = WebPushStore(runtime.config.state_dir)
        now = runtime.gateway.now_ms()
        subscribe(store, now)
        store.enqueue("acct", "device", "persisted", "attention", now=now, ttl_ms=1500)
        store.retry_or_drop(store.due(now)[0], now)
        store.close()
        store = WebPushStore(runtime.config.state_dir)
        runtime.web_push.webpush_store = store
        sent: list[int] = []

        class Provider:
            def send(self, *args: Any) -> ProviderOutcome:
                sent.append(runtime.gateway.now_ms())
                return ProviderOutcome.SENT

        async def active(account: str, device_id: str) -> bool:
            return True

        runtime.web_push.webpush_provider = Provider()  # type: ignore[assignment]
        runtime.web_push._webpush_device_active = active  # type: ignore[method-assign]
        task = asyncio.create_task(runtime.web_push._webpush_loop())
        try:
            async with asyncio.timeout(1.5):
                while not sent:
                    await asyncio.sleep(0.005)
            assert now + 300 <= sent[0] < now + 1500  # due, and still inside its durable TTL
            assert not store.due(runtime.gateway.now_ms())
        finally:
            runtime.stop.set()
            await asyncio.wait_for(task, 1)
            store.close()

    asyncio.run(scenario())


def test_runtime_sleeps_until_a_durable_write_or_expiry(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = CompanionRuntime(load_config(config_file(tmp_path)))
        runtime.stop = asyncio.Event()
        runtime.gateway = SimpleNamespace(now_ms=lambda: int(time.time() * 1000))
        store = open_store(Config(tmp_path / "private"), MemoryKeys())
        runtime.storage.persistence = store
        sweeps: list[int] = []
        sweep = store.sweep

        def counted(now_ms: int) -> Any:
            sweeps.append(now_ms)
            return sweep(now_ms)

        store.sweep = counted  # type: ignore[method-assign]
        task = asyncio.create_task(runtime.storage._maintain_storage())
        try:
            await asyncio.sleep(0.15)
            assert len(sweeps) == 1
            with store.transaction() as tx:
                tx.put(record(expiry=int(time.time() * 1000) + 70), expected_revision=None)
            await asyncio.sleep(0.18)
            assert len(sweeps) == 2 and store.status().records == 0
            await asyncio.sleep(0.15)
            assert len(sweeps) == 2
        finally:
            runtime.stop.set()
            await asyncio.wait_for(task, 1)
            assert store.on_change is None
            store.close()

    asyncio.run(scenario())


def test_webpush_enqueue_wakes_idle_runtime_and_retry_runs_at_its_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(webpush_store, "BACKOFF_BASE_MS", 90)

    async def scenario() -> None:
        runtime = CompanionRuntime(load_config(config_file(tmp_path)))
        runtime.stop = asyncio.Event()
        runtime.gateway = SimpleNamespace(
            now_ms=lambda: int(time.time() * 1000),
            push=SimpleNamespace(category_allowed=lambda *a: True),
        )
        store = WebPushStore(runtime.config.state_dir)
        runtime.web_push.webpush_store = store
        subscribe(store, runtime.gateway.now_ms())
        sends: list[float] = []
        passes: list[int] = []

        class Provider:
            def send(self, *args: Any) -> ProviderOutcome:
                sends.append(time.monotonic())
                return ProviderOutcome.RETRYABLE if len(sends) == 1 else ProviderOutcome.SENT

        runtime.web_push.webpush_provider = Provider()  # type: ignore[assignment]

        async def active(account: str, device_id: str) -> bool:
            return True

        runtime.web_push._webpush_device_active = active  # type: ignore[method-assign]
        original = runtime.web_push._webpush_pass

        def counted(loop: asyncio.AbstractEventLoop) -> None:
            passes.append(1)
            original(loop)

        runtime.web_push._webpush_pass = counted  # type: ignore[method-assign]
        task = asyncio.create_task(runtime.web_push._webpush_loop())
        try:
            await asyncio.sleep(0.15)
            assert len(passes) == 1
            await asyncio.to_thread(
                runtime.web_push._webpush_enqueue,
                "acct",
                frozenset({"device"}),
                "event",
                "attention",
                "host",
                "term",
            )
            async with asyncio.timeout(1):
                while len(sends) < 2:
                    await asyncio.sleep(0.005)
            assert 0.06 <= sends[1] - sends[0] < 0.5
            await asyncio.sleep(0.12)
            assert len(passes) == 3  # startup, enqueue, retry; route expiry is far in the future
            assert not store.due(runtime.gateway.now_ms())
        finally:
            runtime.stop.set()
            await asyncio.wait_for(task, 1)
            store.close()

    asyncio.run(scenario())


def test_enqueue_between_empty_deadline_read_and_wait_cannot_lose_a_wake(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = CompanionRuntime(load_config(config_file(tmp_path)))
        runtime.stop = asyncio.Event()
        runtime.gateway = SimpleNamespace(
            now_ms=lambda: int(time.time() * 1000),
            push=SimpleNamespace(category_allowed=lambda *a: True),
        )
        store = WebPushStore(runtime.config.state_dir)
        runtime.web_push.webpush_store = store
        subscribe(store, runtime.gateway.now_ms())
        captured, release = threading.Event(), threading.Event()
        sent = asyncio.Event()
        loop = asyncio.get_running_loop()
        original = store.next_due_ms

        def held() -> int | None:
            due = original()
            if not captured.is_set():
                captured.set()
                assert release.wait(2)
            return due

        store.next_due_ms = held  # type: ignore[method-assign]

        class Provider:
            def send(self, *args: Any) -> ProviderOutcome:
                loop.call_soon_threadsafe(sent.set)
                return ProviderOutcome.SENT

        async def active(account: str, device_id: str) -> bool:
            return True

        runtime.web_push.webpush_provider = Provider()  # type: ignore[assignment]
        runtime.web_push._webpush_device_active = active  # type: ignore[method-assign]
        task = asyncio.create_task(runtime.web_push._webpush_loop())
        try:
            assert await asyncio.to_thread(captured.wait, 1)
            await asyncio.to_thread(
                runtime.web_push._webpush_enqueue,
                "acct",
                frozenset({"device"}),
                "raced-event",
                "attention",
                "host",
                "term",
            )
            release.set()
            await asyncio.wait_for(sent.wait(), 1)
        finally:
            release.set()
            runtime.stop.set()
            await asyncio.wait_for(task, 1)
            store.close()

    asyncio.run(scenario())
