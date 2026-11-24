"""Liveness stays cheap without weakening real persistence operations."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from relaydeck.companion.app.lifecycle import StateLease
from relaydeck.companion.app.runtime import CompanionRuntime
from relaydeck.companion.config import load_config
from relaydeck.companion.inspection.status import status
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)
from tests.support.companion.asgi_companion import config_file

pytestmark = pytest.mark.usefixtures("hosted_web_bundle")


def test_unchanged_beats_do_not_fsync_and_transitions_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync = Mock()
    monkeypatch.setattr("relaydeck.host.files.private.os.fsync", sync)
    with StateLease(tmp_path) as lease:
        for _ in range(240):
            lease.publish("ready", durable_signins=True)
        assert sync.call_count == 1
        assert status(tmp_path)["state"] == "ready"
        lease.publish("ready", durable_signins=False)
        assert sync.call_count == 2
        assert status(tmp_path)["durable_signins"] is False
        lease.publish("stopping")
        assert sync.call_count == 3


def test_heartbeat_report_never_runs_storage_diagnostics(tmp_path: Path) -> None:
    runtime = CompanionRuntime(load_config(config_file(tmp_path)))
    store = Mock()
    runtime.storage.persistence = store
    for _ in range(240):
        runtime.report()
    store.status.assert_not_called()


def test_storage_diagnostics_run_off_loop_and_publish_cached_result(tmp_path: Path) -> None:
    import asyncio
    import threading
    from types import SimpleNamespace

    from relaydeck.host.persistence.contracts import StorageStatus

    runtime = CompanionRuntime(load_config(config_file(tmp_path)))
    loop_thread = threading.get_ident()

    async def scenario() -> None:
        runtime.stop = asyncio.Event()

        def diagnostics() -> StorageStatus:
            assert threading.get_ident() != loop_thread
            assert runtime.stop is not None
            loop.call_soon_threadsafe(runtime.stop.set)
            return StorageStatus(False, "keys_unavailable")

        runtime.storage.persistence = Mock()
        runtime.storage.persistence.sweep.return_value = SimpleNamespace(more=False)
        runtime.storage.persistence.status.side_effect = diagnostics
        runtime.gateway = Mock()
        runtime.gateway.now_ms.return_value = 1
        loop = asyncio.get_running_loop()
        await runtime.storage._maintain_storage()
        assert runtime.storage.persistence_status.reason == "keys_unavailable"
        assert runtime.report()["durable_signins"] is False
        assert runtime.storage.persistence.status.call_count == 1

    asyncio.run(scenario())
