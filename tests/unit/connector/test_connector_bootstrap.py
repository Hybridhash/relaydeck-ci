"""The connector command composes its runtime without eagerly loading fixture data."""

import subprocess
import sys
from pathlib import Path

import pytest

from relaydeck.connector import bootstrap, cli

ROOT = Path(__file__).resolve().parents[3]


def test_console_import_keeps_the_fixture_unloaded() -> None:
    code = """
import sys
from pathlib import Path
from relaydeck.connector import bootstrap, cli
assert Path(cli.__file__).resolve().is_relative_to(Path.cwd())
assert 'relaydeck.devtools.fakes.connector' not in sys.modules
assert cli.run_link is bootstrap.run_link
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_run_command_forwards_the_parsed_options(monkeypatch: pytest.MonkeyPatch) -> None:
    received = []

    def run(args: object, *, configure_logging: bool) -> int:
        assert not configure_logging
        received.append(args)
        return 7

    monkeypatch.setattr(cli, "run_link", run)
    assert cli.main(["run", "--fixture", "--once"]) == 7
    assert len(received) == 1
    assert received[0].fixture and received[0].once
    assert not received[0].live_herdr


def test_public_bootstrap_entry_is_the_command_owner() -> None:
    assert cli.run_link is bootstrap.run_link
