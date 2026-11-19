"""A Herdr executable that failed the trust checks is never started by name: the companion says the
tool is unavailable and refuses every operation that would spawn it."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from relaydeck.companion.app import herdr_retry
from relaydeck.companion.app.runtime import CompanionRuntime
from relaydeck.companion.config import load_config
from relaydeck.connector import attach as attach_module
from relaydeck.connector.attach import ToolUnavailable
from relaydeck.connector.herdr import HerdrAdapter
from relaydeck.connector.source import HerdrSource
from relaydeck.usage.linking import process as proc_link
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)
from tests.support.companion.asgi_companion import config_file

pytestmark = pytest.mark.usefixtures("hosted_web_bundle")


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CompanionRuntime:
    """The only Herdr on this Mac is in a folder others can write, and that folder is on PATH."""
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    unsafe = home / ".local" / "bin"
    unsafe.mkdir(parents=True)
    (unsafe / "herdr").write_text("#!/bin/sh\nexit 0\n")
    (unsafe / "herdr").chmod(0o755)
    unsafe.chmod(0o777)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(unsafe))
    monkeypatch.delenv("UV_TOOL_BIN_DIR", raising=False)
    monkeypatch.delenv("XDG_BIN_HOME", raising=False)
    return CompanionRuntime(load_config(config_file(tmp_path, web_ui=False)), connect_herdr=True)


@pytest.fixture
def nothing_spawns(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    spawned: list[Any] = []

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        spawned.append(args)
        raise AssertionError("a subprocess was started")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(attach_module.subprocess, "Popen", forbidden)
    return spawned


def source_of(runtime: CompanionRuntime) -> HerdrSource:
    source = runtime.herdr._new_source()
    assert isinstance(source, HerdrSource)
    return source


def test_the_rejected_candidate_does_not_become_a_bare_command(runtime: CompanionRuntime) -> None:
    assert runtime.herdr._herdr_binary() is None
    source = source_of(runtime)
    assert source._binary is None and runtime.herdr.herdr_tool_missing


def test_the_unavailable_tool_is_visible_in_the_companion_report(runtime: CompanionRuntime) -> None:
    runtime.herdr._new_source()
    rows = dict(runtime.setup_report())
    assert rows["Herdr command"] == herdr_retry.TOOL_ISSUE


def test_a_trusted_tool_shows_no_such_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    safe = home / ".local" / "bin"
    safe.mkdir(parents=True)
    (safe / "herdr").write_text("#!/bin/sh\nexit 0\n")
    (safe / "herdr").chmod(0o755)
    for folder in (home / ".local", safe):
        folder.chmod(0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    one = CompanionRuntime(load_config(config_file(tmp_path, web_ui=False)), connect_herdr=True)
    source = source_of(one)
    assert source._binary == str(safe / "herdr") and "Herdr command" not in dict(one.setup_report())


@pytest.mark.parametrize("operation", ["shell", "agent", "observe"])
def test_terminal_operations_refuse_before_anything_is_pooled_or_spawned(
    runtime: CompanionRuntime, nothing_spawns: list[Any], operation: str
) -> None:
    source = source_of(runtime)
    with pytest.raises(ToolUnavailable) as caught:
        if operation == "shell":
            source.attach_shell("t1")
        elif operation == "agent":
            source.attach_agent("t1")
        else:
            source.observe_terminal("t1", "shell")
    assert caught.value.code == "tool_unavailable" and nothing_spawns == []
    assert source._attached == {}


def test_the_process_probe_is_off_without_a_tool(
    runtime: CompanionRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a probe command was started")

    monkeypatch.setattr(proc_link, "default_run", forbidden)
    source = source_of(runtime)
    assert source._proc_probe("w1:p1", (), lambda: 0.0, 1.0) is None


def test_a_source_can_be_built_without_a_tool_for_socket_reads() -> None:
    source = HerdrSource(HerdrAdapter(socket_path="/synthetic/none.sock"), binary=None)
    assert source.bootstrapped is False  # constructed and usable for socket reads


def test_a_viewer_opening_a_terminal_gets_a_reported_failure_and_nothing_starts(
    runtime: CompanionRuntime, nothing_spawns: list[Any]
) -> None:
    source = source_of(runtime)
    failures = source.note_epochs(
        {
            "t1": {"epoch": "e1", "kind": "shell", "mode": "control"},
            "t2": {"epoch": "e1", "kind": "agent", "mode": "control"},
        }
    )
    assert failures == {"t1": "ToolUnavailable", "t2": "ToolUnavailable"}
    assert nothing_spawns == [] and source._attached == {}
