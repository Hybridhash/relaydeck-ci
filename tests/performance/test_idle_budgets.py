"""Idle-work budgets: background loops wake for work, not on a fixed timer."""

from __future__ import annotations

import asyncio
import base64
import collections
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from relaydeck.contracts.schemas import GATEWAY_PROTOCOL
from relaydeck.contracts.wire import terminal as terminal_wire
from relaydeck.gateway.app import terminal_epochs as epochs_module
from relaydeck.gateway.app import tunnel as tunnel_module
from relaydeck.gateway.app.backend import JournalBackend
from relaydeck.gateway.app.bootstrap import create_app
from relaydeck.gateway.app.state import GatewayState
from relaydeck.gateway.app.terminal_epochs import reconcile_terminal_routes
from relaydeck.gateway.features.access.hosts import HostRecord
from relaydeck.gateway.features.notifications import worker as push_worker_module
from relaydeck.gateway.features.notifications.adapters.sqlite_store import PushStore
from relaydeck.gateway.features.notifications.service import ProviderOutcome, pump_outbox
from relaydeck.gateway.features.notifications.transitions import (
    HOLD_MS,
    NotificationTransitions,
    Status,
)
from relaydeck.gateway.features.notifications.worker import PushWorker
from relaydeck.gateway.http import sockets as sockets_module
from relaydeck.host.persistence.command_journal import CommandJournal
from relaydeck.usage.contracts import Budget, Cursor
from relaydeck.usage.readers import _files
from relaydeck.usage.readers.jsonl_message_usage import READER
from tests.security.helpers import ALL_CAPS, NOW, make_state
from tests.support.asgi_socket import AsgiSocket
from tests.support.terminal.lease_gateway import _client as _lease_client
from tests.support.terminal.sockets import _grant, _state

JOURNAL_NOW = 1_700_000_000_000


def test_idle_transition_loop_waits_for_the_next_transition() -> None:
    async def scenario() -> None:
        transitions = NotificationTransitions()
        flushes = 0
        original = transitions.flush_async

        async def counted(now: int, emit: Any) -> None:
            nonlocal flushes
            flushes += 1
            await original(now, emit)

        transitions.flush_async = counted  # type: ignore[method-assign]
        now = [1_000_000]
        emitted: list[str] = []

        async def emit(status: Status, category: str) -> None:
            emitted.append(category)

        stop = asyncio.Event()
        task = asyncio.create_task(transitions.run(stop, lambda: now[0], emit))
        try:
            await asyncio.sleep(1.1)
            assert flushes == 1  # the initial pass only; no timer-driven flushes
            base = ("acct", "host", "e1", "term", "pane")
            transitions._observe(Status(*base, "working", "first"), now[0])
            transitions._observe(Status(*base, "blocked", "second"), now[0])
            await asyncio.sleep(0.05)
            assert emitted == []
            now[0] += HOLD_MS + 1
            async with asyncio.timeout(1):
                while not emitted:
                    await asyncio.sleep(0.005)
            assert emitted == ["attention"]
            assert flushes < 20
        finally:
            stop.set()
            await task

    asyncio.run(scenario())


class _Provider:
    def __init__(self) -> None:
        self.sends = 0

    def send(self, token: str, payload: dict[str, str]) -> ProviderOutcome:
        self.sends += 1
        return ProviderOutcome.SENT

    def close(self) -> None:
        return None


def test_push_worker_backs_off_when_idle_and_wakes_for_new_work(monkeypatch: Any) -> None:
    async def scenario() -> None:
        store = PushStore()
        store.register_device("acct", "dev", "synthetic-token", "android", 1_000_000)
        provider = _Provider()
        pumps = 0
        original = push_worker_module.pump_outbox

        def counted(*args: Any, **kwargs: Any) -> dict[str, int]:
            nonlocal pumps
            pumps += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(push_worker_module, "pump_outbox", counted)
        worker = PushWorker(store, provider, interval=0.05)
        worker.start()
        try:
            await asyncio.sleep(1.2)
            assert pumps <= 6  # doubling idle waits, not one pump per interval
            store.notify(
                account="acct",
                account_hosts={"host"},
                host_id="host",
                session_id="term",
                event_id="event-1",
                category="completion",
                now=int(time.time() * 1000),
                device_ids=frozenset({"dev"}),
            )
            worker.wake()
            async with asyncio.timeout(1):
                while provider.sends == 0:
                    await asyncio.sleep(0.005)
            assert store.pending_count() == 0
        finally:
            await worker.stop()
            store.close()

    asyncio.run(scenario())


def test_idle_tunnel_and_viewer_do_not_poll_backends() -> None:
    async def scenario() -> None:
        state, backend, _ = _state()
        counts: collections.Counter[str] = collections.Counter()
        original_io = tunnel_module.command_io

        async def counted_io(b: Any, op: str, *args: Any) -> Any:
            counts[op] += 1
            return await original_io(b, op, *args)

        tunnel_module.command_io = counted_io
        original_open = backend.open_lease
        original_epochs = backend.epochs_for_host

        def opened(*args: Any, **kwargs: Any) -> Any:
            counts["open_lease"] += 1
            return original_open(*args, **kwargs)

        def epochs(*args: Any, **kwargs: Any) -> Any:
            counts["epochs"] += 1
            return original_epochs(*args, **kwargs)

        backend.open_lease = opened  # type: ignore[method-assign]
        backend.epochs_for_host = epochs  # type: ignore[method-assign]
        token = state.hosts.enroll("home-mac", account="acct-1")
        app = create_app(state)
        tunnel = await AsgiSocket(app, "/v1/connectors", token=token).open()
        viewer: AsgiSocket | None = None
        try:
            await tunnel.recv_text()
            attachment, _ = backend.create_attachment(
                "home-mac", "term-0", "shell", "acct-1", "dev-1", takeover=True
            )
            lease = backend.acquire_control(attachment.attachment_id, "acct-1", "dev-1")
            ticket = state.tickets.issue(_grant(lease.lease_id), 1_000_000)
            viewer = await AsgiSocket(app, f"/v1/terminal/{lease.lease_id}?ticket={ticket}").open()
            await asyncio.sleep(0.2)
            counts.clear()
            await asyncio.sleep(3)
            assert counts["epochs"] <= 12
            assert counts["drain_outbox"] <= 12
            assert counts["open_lease"] <= 12
            # First delivery after the idle stretch arrives promptly.
            started = time.monotonic()
            await tunnel.send_text(
                json.dumps(
                    {
                        "kind": "terminal.output",
                        "host_id": "home-mac",
                        "terminal_id": "term-0",
                        "epoch": attachment.epoch,
                        "data_b64": base64.b64encode(b"hello").decode(),
                    }
                )
            )
            message = await asyncio.wait_for(viewer.outbound.get(), 1)
            assert time.monotonic() - started < 0.9
            chunk = terminal_wire.decode_output(message["bytes"])
            assert chunk.data == b"hello"
        finally:
            tunnel_module.command_io = original_io
            if viewer is not None:
                await viewer.close()
            await tunnel.close()

    asyncio.run(scenario())


def _counted_pass(root: Path, cursor: Cursor, monkeypatch: Any) -> tuple[Cursor, int, Any]:
    opens = 0
    original = os.open

    def counted(*args: Any, **kwargs: Any) -> int:
        nonlocal opens
        if args and str(args[0]).endswith(".jsonl"):
            opens += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(os, "open", counted)
    try:
        result = READER.read(root, cursor, Budget())
    finally:
        monkeypatch.undo()
    return result.next_cursor, opens, result


def test_unchanged_usage_pass_opens_no_settled_files(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(_files, "RECONCILE_PASSES", 2)
    root = tmp_path / "logs"
    for i in range(30):
        path = root / f"proj{i % 3}" / f"s{i}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"type":"user"}\n')
    cursor = Cursor()
    for _ in range(20):
        result = READER.read(root, cursor, Budget())
        cursor = result.next_cursor
        if not result.more:
            break
    # Directory opens only while stamps are unchanged; periodic
    # re-verification opens the files again within a few passes.
    seen: list[int] = []
    for _ in range(5):
        cursor, opens, result = _counted_pass(root, cursor, monkeypatch)
        assert result.events == () and not result.more
        seen.append(opens)
    assert min(seen) == 0
    assert max(seen) >= 30
    # An appended file is read on the next pass, then settled again.
    target = root / "proj0" / "s0.jsonl"
    with target.open("a") as handle:
        handle.write('{"type":"user"}\n')
    cursor, opens, _ = _counted_pass(root, cursor, monkeypatch)
    assert opens >= 1
    # Same size and restored mtime still cannot hide a replacement: the
    # change time in the stamp differs, so the file is re-read.
    before = target.stat()
    target.write_text('{"type":"user"}\n{"type":"user"}\n')
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    cursor, opens, _ = _counted_pass(root, cursor, monkeypatch)
    assert opens >= 1


def test_command_submit_wakes_the_idle_tunnel(tmp_path: Path) -> None:
    async def scenario() -> None:
        journal = CommandJournal(tmp_path / "gw.db", clock=lambda: JOURNAL_NOW)
        backend = JournalBackend(
            journal,
            now_ms=lambda: JOURNAL_NOW,
            snapshots={
                "home-mac": json.loads(Path("tests/fixtures/gateway/snapshot.json").read_text())
            },
        )
        state = GatewayState(backend=backend, cursor_secret=b"x" * 32, now_ms=lambda: JOURNAL_NOW)
        caps = frozenset(
            {
                "sessions.read",
                "terminal.view",
                "terminal.control",
                "agents.prompt",
                "agents.start",
                "shells.create",
                "terminal.attach",
                "hosts.enroll",
            }
        )
        access, _ = state.sessions.create("acct-1", caps, frozenset({"home-mac"}), JOURNAL_NOW)
        host_token = state.hosts.enroll("home-mac", account="acct-1")
        app = create_app(state)
        tunnel = await AsgiSocket(app, "/v1/connectors", token=host_token).open()
        try:
            await tunnel.recv_text()
            await asyncio.sleep(1.6)  # let the pump's idle wait back off
            body = {
                "protocol": GATEWAY_PROTOCOL,
                "command_id": "wake-command-0001",
                "host_id": "home-mac",
                "host_epoch": "epoch-7",
                "kind": "agent.start",
                "target": {"workspace_id": "w1"},
                "payload": {"kind": "tool-a", "name": "web"},
                "expires_at": datetime.fromtimestamp((JOURNAL_NOW + 120_000) / 1000, UTC).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            }
            started = time.monotonic()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as http:
                response = await http.post(
                    "/v1/hosts/home-mac/commands",
                    headers={"authorization": f"Bearer {access}"},
                    json=body,
                )
            assert response.status_code == 202
            dispatch = json.loads(await asyncio.wait_for(tunnel.recv_text(), 0.5))
            assert time.monotonic() - started < 0.5
            assert dispatch["kind"] == "command.dispatch"
            assert dispatch["command_id"] == "wake-command-0001"
        finally:
            await tunnel.close()
            backend.close()

    asyncio.run(scenario())


def test_notification_emit_wakes_the_push_worker() -> None:
    async def scenario() -> None:
        state, _backend = make_state()
        state.push.close()
        state.push = PushStore(clock=lambda: NOW)
        state.hosts.enroll("home-mac", account="acct-1")
        state.sessions.create("acct-1", ALL_CAPS, {"home-mac"}, NOW, "d1")
        state.push.register_device("acct-1", "d1", "synthetic-token", "android", NOW)
        provider = _Provider()
        worker = PushWorker(state.push, provider, interval=0.05)
        state.push_worker = worker
        worker.start()
        try:
            await asyncio.sleep(1.2)  # backed off: next fallback pump is seconds away
            status = Status("acct-1", "home-mac", "e1", "term", "pane", "blocked", "evt-1")
            await state.emit_notification_async(status, "attention")
            async with asyncio.timeout(0.5):
                while provider.sends == 0:
                    await asyncio.sleep(0.005)
            assert state.push.pending_count() == 0
        finally:
            await worker.stop()
            state.push.close()

    asyncio.run(scenario())


class _FlakyProvider:
    """Fails the first send with a retryable outcome, then succeeds."""

    def __init__(self) -> None:
        self.sends = 0

    def send(self, token: str, payload: dict[str, str]) -> ProviderOutcome:
        self.sends += 1
        return ProviderOutcome.RETRYABLE if self.sends == 1 else ProviderOutcome.SENT

    def close(self) -> None:
        return None


def test_push_worker_retries_scheduled_work_before_it_expires(monkeypatch: Any) -> None:
    async def scenario() -> None:
        now = [1_700_000_000_000]
        store = PushStore(clock=lambda: now[0])
        store.register_device("acct", "dev", "synthetic-token", "android", now[0])
        row = store.notify(
            account="acct",
            account_hosts={"host"},
            host_id="host",
            session_id="term",
            event_id="retry-event",
            category="completion",
            now=now[0],
            ttl_ms=31_500,
        )[0]
        provider = _FlakyProvider()
        pumps = 0
        original = push_worker_module.pump_outbox

        def counted(*args: Any, **kwargs: Any) -> dict[str, int]:
            nonlocal pumps
            pumps += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(push_worker_module, "pump_outbox", counted)
        worker = PushWorker(store, provider, interval=0.02)
        worker.start()
        try:
            async with asyncio.timeout(1):
                while provider.sends == 0:
                    await asyncio.sleep(0.005)
            # A retry is now scheduled 30 s out inside a 31.5 s TTL. The
            # worker bounds its wait by that due time instead of polling:
            # almost no pumps while it is far away, then a prompt send.
            assert store.next_due_ms() == now[0] + 30_000
            pumps = 0
            await asyncio.sleep(0.5)
            assert pumps <= 2
            assert store.pending_count() == 1
            now[0] += 30_100  # retry due, still before expiry
            async with asyncio.timeout(1.5):
                while provider.sends < 2:
                    await asyncio.sleep(0.005)
            assert store.get_notification(str(row["notification_id"]))["status"] == "sent"
        finally:
            await worker.stop()
            store.close()

    asyncio.run(scenario())


def test_push_worker_picks_up_persisted_future_work_after_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        now = [1_700_000_000_000]
        path = tmp_path / "push.db"
        store = PushStore(path, clock=lambda: now[0])
        store.register_device("acct", "dev", "synthetic-token", "android", now[0])
        row = store.notify(
            account="acct",
            account_hosts={"host"},
            host_id="host",
            session_id="term",
            event_id="restart-event",
            category="completion",
            now=now[0],
            ttl_ms=31_500,
        )[0]
        provider = _FlakyProvider()
        assert pump_outbox(store, provider, now=now[0])["retried"] == 1
        store.close()

        now[0] += 29_000  # restart one second before the persisted retry is due
        reopened = PushStore(path, clock=lambda: now[0])
        worker = PushWorker(reopened, provider, interval=0.02)
        worker.start()
        try:
            await asyncio.sleep(0.3)
            assert provider.sends == 1  # not due yet, and not dropped
            now[0] += 1_100
            async with asyncio.timeout(1.5):
                while provider.sends < 2:
                    await asyncio.sleep(0.005)
            assert reopened.get_notification(str(row["notification_id"]))["status"] == "sent"
        finally:
            await worker.stop()
            reopened.close()

    asyncio.run(scenario())


def test_cancelled_terminal_handler_settles_its_tasks() -> None:
    async def scenario() -> None:
        state, backend, _ = _state()
        app = create_app(state)
        baseline = set(asyncio.all_tasks())
        for _ in range(5):
            attachment, _ = backend.create_attachment(
                "home-mac", "term-0", "shell", "acct-1", "dev-1", takeover=True
            )
            lease = backend.issue_view(attachment.attachment_id, "acct-1", "dev-1")
            ticket = state.tickets.issue(_grant(lease.lease_id), 1_000_000)
            socket = await AsgiSocket(app, f"/v1/terminal/{lease.lease_id}?ticket={ticket}").open()
            await asyncio.sleep(0.02)
            assert socket.task is not None
            socket.task.cancel()
            await asyncio.gather(socket.task, return_exceptions=True)
            assert socket.task.done()
            for _ in range(5):
                await asyncio.sleep(0)
            alive = [task for task in asyncio.all_tasks() - baseline if not task.done()]
            assert alive == []
            assert sockets_module._viewer_wakes == {}

    asyncio.run(scenario())


def test_epoch_reconciler_sleeps_until_the_lease_expiry(monkeypatch: Any) -> None:
    async def scenario() -> None:
        _client_obj, state, backend, clock = _lease_client()
        from relaydeck.gateway.features.access.hosts import HostRecord

        record = HostRecord("home-mac", "Mac", "test", "acct-1")
        monkeypatch.setattr(state.hosts, "hosts", lambda: (record,))
        monkeypatch.setattr(state.hosts, "record_for_host", lambda _: record)
        attachment = backend.create_attachment(
            "home-mac", "term-1", "agent", "acct-1", "dev-1", takeover=True
        )[0]
        backend.acquire_control(attachment.attachment_id, "acct-1", "dev-1")
        modes: list[str] = []
        delays: list[float] = []

        async def notify(_host: str, _text: str, **_kwargs: Any) -> int:
            modes.append(backend.epochs_for_host("home-mac", "acct-1")[0]["mode"])
            return 1

        monkeypatch.setattr(state.sockets, "notify_host", notify)

        async def fake_sleep(duration: float) -> None:
            delays.append(duration)
            if len(modes) == 1:
                clock.now += 60_001
            else:
                raise asyncio.CancelledError

        monkeypatch.setattr("relaydeck.gateway.app.terminal_epochs.asyncio.sleep", fake_sleep)
        try:
            await reconcile_terminal_routes(state)
        except asyncio.CancelledError:
            pass
        assert modes == ["control", "observe"]
        # The wait runs to the control lease expiry (60 s), not a 1 s scan.
        assert delays and delays[0] > 30

    asyncio.run(scenario())


def _epoch_setup(monkeypatch: Any) -> tuple[Any, Any, Any, Any]:
    _client_obj, state, backend, clock = _lease_client()
    record = HostRecord("home-mac", "Mac", "test", "acct-1")
    monkeypatch.setattr(state.hosts, "hosts", lambda: (record,))
    monkeypatch.setattr(state.hosts, "record_for_host", lambda _: record)
    attachment = backend.create_attachment(
        "home-mac", "term-1", "agent", "acct-1", "dev-1", takeover=True
    )[0]
    lease = backend.acquire_control(attachment.attachment_id, "acct-1", "dev-1")
    return state, backend, clock, lease


def test_epoch_reconciler_republishes_when_expiry_passes_during_publish(
    monkeypatch: Any,
) -> None:
    async def scenario() -> None:
        state, backend, clock, lease = _epoch_setup(monkeypatch)
        sent: list[str] = []
        clock.now = lease.expires_ms - 1

        async def notify(_host: str, text: str, **_kwargs: Any) -> int:
            sent.append(json.loads(text)["epochs"][0]["mode"])
            clock.now += 2  # the deadline passes while the send yields
            await asyncio.sleep(0)
            return 1

        async def wait(_delay: float) -> None:
            raise asyncio.CancelledError

        monkeypatch.setattr(state.sockets, "notify_host", notify)
        monkeypatch.setattr(epochs_module, "_wait_reconcile", wait)
        try:
            await reconcile_terminal_routes(state)
        except asyncio.CancelledError:
            pass
        # The expired route is republished in the same scan, not after a sleep.
        assert sent == ["control", "observe"]
        assert backend.epochs_for_host("home-mac", "acct-1")[0]["mode"] == "observe"

    asyncio.run(scenario())


def test_epoch_reconciler_republishes_when_expiry_passes_between_final_reads(
    monkeypatch: Any,
) -> None:
    async def scenario() -> None:
        state, backend, clock, lease = _epoch_setup(monkeypatch)
        sent: list[str] = []
        waits: list[float] = []
        clock.now = lease.expires_ms - 1
        original_due = backend.next_epoch_change_ms

        def due(*args: Any) -> int | None:
            clock.now += 2  # elapsed time between the independent reads
            return original_due(*args)

        async def notify(_host: str, text: str, **_kwargs: Any) -> int:
            sent.append(json.loads(text)["epochs"][0]["mode"])
            await asyncio.sleep(0)
            return 1

        async def wait(delay: float) -> None:
            waits.append(delay)
            raise asyncio.CancelledError

        monkeypatch.setattr(backend, "next_epoch_change_ms", due)
        monkeypatch.setattr(state.sockets, "notify_host", notify)
        monkeypatch.setattr(epochs_module, "_wait_reconcile", wait)
        try:
            await reconcile_terminal_routes(state)
        except asyncio.CancelledError:
            pass
        assert sent == ["control", "observe"]
        assert backend.epochs_for_host("home-mac", "acct-1")[0]["mode"] == "observe"
        assert waits == [30.0]

    asyncio.run(scenario())


def test_epoch_reconciler_uses_deadline_shortened_during_its_scan(monkeypatch: Any) -> None:
    async def scenario() -> None:
        state, backend, clock, lease = _epoch_setup(monkeypatch)
        records = (
            HostRecord("home-mac", "Mac", "test", "acct-1"),
            HostRecord("host-2", "Mac2", "test", "acct-1"),
        )
        monkeypatch.setattr(state.hosts, "hosts", lambda: records)
        monkeypatch.setattr(
            state.hosts,
            "record_for_host",
            lambda host: next(r for r in records if r.host_id == host),
        )
        waits: list[float] = []

        async def notify(host: str, _text: str, **_kwargs: Any) -> int:
            if host == "host-2":
                assert backend.begin_reader_handoff(
                    lease.lease_id, "acct-1", "dev-1", lease.generation
                )
                await epochs_module.publish_terminal_routes(state, "home-mac")
            return 1

        async def wait(delay: float) -> None:
            waits.append(delay)
            raise asyncio.CancelledError

        monkeypatch.setattr(state.sockets, "notify_host", notify)
        monkeypatch.setattr(epochs_module, "_wait_reconcile", wait)
        try:
            await reconcile_terminal_routes(state)
        except asyncio.CancelledError:
            pass
        # Sleep is bounded by the shortened five-second grace, not 30 s.
        assert waits and waits[0] < 6

    asyncio.run(scenario())


def test_reader_handoff_wakes_the_sleeping_epoch_reconciler(monkeypatch: Any) -> None:
    async def scenario() -> None:
        state, backend, clock, lease = _epoch_setup(monkeypatch)
        sent: list[str] = []

        async def notify(_host: str, text: str, **_kwargs: Any) -> int:
            sent.append(json.loads(text)["epochs"][0]["mode"])
            return 1

        monkeypatch.setattr(state.sockets, "notify_host", notify)
        task = asyncio.create_task(reconcile_terminal_routes(state))
        try:
            async with asyncio.timeout(1):
                while not epochs_module._waiters:
                    await asyncio.sleep(0)
            assert backend.begin_reader_handoff(lease.lease_id, "acct-1", "dev-1", lease.generation)
            shortened = backend.open_lease(lease.lease_id)
            assert shortened is not None
            clock.now = shortened.expires_ms + 1
            async with asyncio.timeout(1):
                while sent != ["control", "observe"]:
                    await asyncio.sleep(0.005)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
