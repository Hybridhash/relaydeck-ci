"""Optional Changes setup cannot prevent an in-process companion from starting."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

import relaydeck.companion.app.bindings as runtime_module
from relaydeck.companion.app.bindings import ChangesBinding
from relaydeck.companion.app.runtime import CompanionRuntime
from relaydeck.companion.config import load_config
from relaydeck.git.runner import GitFailed, GitRunner
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)
from tests.support.companion.asgi_companion import AsgiServer, config_file

pytestmark = pytest.mark.usefixtures("hosted_web_bundle")


@pytest.mark.parametrize("failure", ["timeout", "execution", "store", "unsafe_store"])
def test_changes_setup_failure_keeps_the_companion_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        if failure == "timeout":
            raise GitFailed("git_timeout")
        raise FileNotFoundError("private detail")

    if failure == "store":
        monkeypatch.setattr(runtime_module, "BaseStore", fail)
    elif failure != "unsafe_store":
        monkeypatch.setattr(GitRunner, "run", fail)

    async def scenario() -> None:
        config = load_config(config_file(tmp_path, web_ui=False, tailscale_auto=False))
        if failure == "unsafe_store":
            config.state_dir.mkdir(mode=0o700)
            key = config.state_dir / "changes-bases.key"
            key.write_bytes(b"obviously-fake-store-key" * 2)
            key.chmod(0o644)
        runtime = CompanionRuntime(config, server_factory=AsgiServer)
        try:
            with caplog.at_level(logging.WARNING):
                await runtime.start(asyncio.Event())
            assert runtime.gateway.git is None
            assert runtime.web.server.started and runtime.web.app is not None
            reason = {
                "timeout": "git_timeout",
                "execution": "git_execution_failed",
                "store": "store_unavailable",
                "unsafe_store": "store_unavailable",
            }[failure]
            assert f"changes_unavailable reason={reason}" in caplog.text
            assert "private detail" not in caplog.text
        finally:
            await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [asyncio.CancelledError(), RuntimeError("unexpected")])
def test_changes_setup_preserves_cancellation_and_programming_errors(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(runtime_module.GitRunner, "create", fail)
    binding = ChangesBinding.__new__(ChangesBinding)
    with pytest.raises(type(failure)) as caught:
        binding._prepare_changes()
    assert caught.value is failure
