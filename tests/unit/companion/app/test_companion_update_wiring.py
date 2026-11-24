"""The companion starts the update check beside everything else, and a failure in it never stops
the companion."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import relaydeck.companion.app.bindings as runtime_module
from relaydeck.companion.app.runtime import CompanionRuntime
from relaydeck.companion.config import load_config
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)
from tests.support.companion.asgi_companion import config_file

pytestmark = pytest.mark.usefixtures("hosted_web_bundle")


def make_runtime(tmp_path: Path, **kwargs: object) -> CompanionRuntime:
    root = tmp_path.resolve() / "cfg"
    root.mkdir(mode=0o700)
    runtime = CompanionRuntime(load_config(config_file(root)), **kwargs)  # type: ignore[arg-type]
    runtime.gateway = SimpleNamespace(updates=None)
    return runtime


def test_the_service_is_attached_and_the_worker_task_follows_the_stop_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FLET_WEB_PATH", raising=False)
    monkeypatch.setattr("relaydeck.companion.updates.worker.__version__", "0.3.0")

    async def scenario() -> tuple[bool, bool]:
        runtime = make_runtime(tmp_path)
        stop = asyncio.Event()
        runtime.updates._start_updates(stop)
        assert runtime.gateway.updates is not None and runtime.updates.updates_task is not None
        assert runtime.gateway.updates.host_id == runtime.config.host_id
        stop.set()
        await runtime._finish(runtime.updates.updates_task)
        return runtime.updates.updates_task.done(), runtime.updates.updates_task.cancelled()

    assert asyncio.run(scenario()) == (True, False)


def test_the_synthetic_fixture_run_attaches_the_route_but_never_checks(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = make_runtime(tmp_path, fixture=True)
        runtime.updates._start_updates(asyncio.Event())
        assert runtime.gateway.updates is not None and runtime.updates.updates_task is None

    asyncio.run(scenario())


def test_a_failure_building_the_feature_is_logged_as_a_code_and_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secret detail that must not be logged")

    monkeypatch.setattr(runtime_module, "build_updates", broken)

    async def scenario() -> None:
        runtime = make_runtime(tmp_path)
        with caplog.at_level(logging.INFO):
            runtime.updates._start_updates(asyncio.Event())
        assert runtime.gateway.updates is None and runtime.updates.updates_task is None

    asyncio.run(scenario())
    text = " ".join(record.getMessage() for record in caplog.records)
    assert "update_check outcome=state" in text and "secret detail" not in text
