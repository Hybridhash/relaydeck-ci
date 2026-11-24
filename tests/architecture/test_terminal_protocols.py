"""Terminal capability boundaries retain concrete static conformance."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from inspect import Parameter, signature
from pathlib import Path

import pytest

from relaydeck.client.features.terminal.agent_session import AgentSession
from relaydeck.client.features.terminal.controller import TerminalController
from relaydeck.client.features.terminal.session.base import TerminalSessionBase
from relaydeck.client.features.terminal.session.facade import SessionFacade
from relaydeck.client.features.terminal.session.rendering import RendererBinding
from relaydeck.client.features.terminal.shell_session import ShellSession

ROOT = Path(__file__).resolve().parents[2]


def _mypy(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "mypy",
            "--no-incremental",
            "--cache-dir=/dev/null",
            str(path),
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "MYPYPATH": str(ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_concrete_terminal_owners_satisfy_capability_protocols() -> None:
    result = _mypy(ROOT / "tests/typing/terminal_protocols.py")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_writer_with_an_incompatible_generation_type_is_rejected(tmp_path: Path) -> None:
    fixture = tmp_path / "wrong_terminal_writer.py"
    fixture.write_text(
        "from relaydeck.client.ports.terminal import KeyWriter\n"
        "class WrongWriter:\n"
        "    lease_id = 'lease'\n"
        "    def key_generation(self) -> str: return 'wrong'\n"
        "    async def send_key_bytes(self, data: bytes) -> None: pass\n"
        "def writer() -> KeyWriter: return WrongWriter()\n"
    )
    result = _mypy(fixture)
    assert result.returncode != 0
    assert "key_generation" in result.stdout


@pytest.mark.parametrize("bind", [RendererBinding.bind_view, SessionFacade.bind_view])
def test_renderer_binding_requires_the_view_output_writer(bind: Callable[..., int]) -> None:
    parameter = signature(bind).parameters["write"]
    assert parameter.kind is Parameter.KEYWORD_ONLY
    assert parameter.default is Parameter.empty
    with pytest.raises(TypeError, match="write"):
        bind(object(), object())


def test_a_stream_factory_with_an_incompatible_result_is_rejected(tmp_path: Path) -> None:
    fixture = tmp_path / "wrong_terminal_stream_factory.py"
    fixture.write_text(
        "from relaydeck.client.ports.transport import TerminalStreamFactory\n"
        "def wrong(**kwargs: object) -> object: return object()\n"
        "def factory() -> TerminalStreamFactory: return wrong\n"
    )
    result = _mypy(fixture)
    assert result.returncode != 0
    assert "TerminalStreamFactory" in result.stdout
    assert "return-value" in result.stdout


@pytest.mark.parametrize(
    "owner", [TerminalController, TerminalSessionBase, AgentSession, ShellSession]
)
def test_terminal_construction_requires_an_explicit_stream_factory(owner: type) -> None:
    constructor = signature(owner)
    parameter = constructor.parameters["stream_factory"]
    assert parameter.kind is Parameter.KEYWORD_ONLY
    assert parameter.default is Parameter.empty
    required = {
        name: None
        for name, argument in constructor.parameters.items()
        if argument.default is Parameter.empty and name != "stream_factory"
    }
    with pytest.raises(TypeError, match="stream_factory"):
        constructor.bind(**required)
