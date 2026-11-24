"""`[terminal] controller_viewport`: off keeps the raw attach client and never re-asserts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from relaydeck.companion.config import TerminalConfig, load_config, terminal_from
from relaydeck.connector import source as module
from relaydeck.connector.attach import AttachSession, ControlSession
from relaydeck.connector.herdr import HerdrAdapter
from relaydeck.connector.source import HerdrSource
from tests.support.companion.asgi_companion import config_file


def test_setting_defaults_off_and_reads_true(tmp_path: Path) -> None:
    assert load_config(config_file(tmp_path)).terminal == TerminalConfig(controller_viewport=False)
    path = config_file(tmp_path)
    path.write_text(path.read_text() + "\n[terminal]\ncontroller_viewport = true\n")
    assert load_config(path).terminal.controller_viewport is True


@pytest.mark.parametrize("value", [{"controller_viewport": "yes"}, {"viewport": True}, [True]])
def test_setting_is_shape_checked(value: Any) -> None:
    with pytest.raises(ValueError):
        terminal_from(value)
    assert terminal_from(None) == TerminalConfig()


class _Raw(AttachSession):
    instances: list[_Raw] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.alive = False
        self.resizes: list[tuple[int, int]] = []
        self.instances.append(self)

    def spawn(self) -> None:
        self.alive = True

    @property
    def running(self) -> bool:
        return self.alive

    def read(self, max_bytes: int = 65536, timeout: float = 0) -> bytes:
        return b""

    def read_available(self, max_bytes: int = 65536) -> bytes:
        return b""

    def resize(self, cols: int, rows: int) -> None:
        self.resizes.append((cols, rows))

    def close(self, timeout: float = 5) -> int | None:
        self.alive = False
        return 0


class _Control(_Raw, ControlSession):
    pass


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    _Raw.instances = []
    monkeypatch.setattr(module, "AttachSession", _Raw)
    monkeypatch.setattr(module, "ControlSession", _Control)


def _attach(source: HerdrSource) -> _Raw:
    table = {
        "shell-1": {"epoch": "e1", "kind": "shell", "mode": "control"},
        "agent-1": {"epoch": "e1", "kind": "agent", "mode": "control"},
    }
    assert source.note_epochs(table) == {}
    assert len(_Raw.instances) == 2
    return _Raw.instances[0]


def test_off_uses_the_raw_attach_client_and_never_reasserts(fakes: None) -> None:
    source = HerdrSource(HerdrAdapter(), subscriptions=[])
    shell, agent = _attach(source), _Raw.instances[1]
    assert type(shell) is _Raw and type(agent) is _Raw
    assert shell.argv == ["herdr", "terminal", "attach", "shell-1"]
    assert agent.argv == ["herdr", "agent", "attach", "agent-1", "--takeover"]
    assert source.resize_terminal("shell-1", "e1", 53, 40) is None
    source._flush_resize("shell-1")
    assert shell.resizes == [(53, 40)]  # the raw client is still told its size
    assert source._geometry == {}
    assert source.reassert_geometry(force=True) == 0
    source._consume_live_line("layout.updated", {})
    assert shell.resizes == [(53, 40)] and agent.resizes == []


def test_on_uses_the_controller_viewport_and_records_geometry(fakes: None) -> None:
    source = HerdrSource(HerdrAdapter(), subscriptions=[], controller_viewport=True)
    shell = _attach(source)
    assert type(shell) is _Control
    assert shell.argv[:5] == ["herdr", "terminal", "session", "control", "shell-1"]
    assert source._geometry == {"shell-1": (80, 24), "agent-1": (80, 24)}
