"""Embedded connector calls preserve logging owned by their caller."""

import json
import logging
import logging.config
import sys
from pathlib import Path
from typing import Any

import pytest

from relaydeck.connector import cli, enrollment
from relaydeck.host.observability import exceptions


@pytest.mark.parametrize(
    "arguments,expected",
    [
        (["run"], 2),
        (["run", "--fixture", "--live-herdr"], 2),
        (["run", "--live-herdr"], 2),
        (["run", "--fixture"], 1),
    ],
)
def test_explicit_arguments_preserve_caller_logging(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str], expected: int
) -> None:
    def caller_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        return logging.LogRecord(*args, **kwargs)

    handler = logging.NullHandler()
    root = logging.getLogger()
    monkeypatch.setattr(logging, "_logRecordFactory", caller_factory)
    monkeypatch.setattr(root, "handlers", [handler])
    monkeypatch.setattr(root, "level", logging.ERROR)
    monkeypatch.delenv("RELAYDECK_ALLOW_LIVE_HERDR", raising=False)
    monkeypatch.setattr(enrollment, "load_creds", lambda: None)

    assert cli.main(arguments) == expected
    assert logging.getLogRecordFactory() is caller_factory
    assert root.handlers == [handler]
    assert root.level == logging.ERROR


def test_console_entry_owns_default_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(sys, "argv", ["relaydeck-connector", "run", "--fixture"])
    monkeypatch.setattr(enrollment, "load_creds", lambda: None)
    monkeypatch.setattr(exceptions, "install_exception_safety", lambda: calls.append("factory"))
    monkeypatch.setattr(logging.config, "dictConfig", lambda config: calls.append("config"))

    assert cli.main() == 1
    assert calls == ["factory", "config"]


def test_explicit_log_config_is_honored_without_claiming_the_factory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "logging.json"
    document = {"version": 1, "disable_existing_loggers": False}
    config.write_text(json.dumps(document))
    calls = []
    factory = logging.getLogRecordFactory()
    monkeypatch.setattr(enrollment, "load_creds", lambda: None)
    monkeypatch.setattr(logging.config, "dictConfig", calls.append)

    assert cli.main(["run", "--fixture", "--log-config", str(config)]) == 1
    assert calls == [document]
    assert logging.getLogRecordFactory() is factory
