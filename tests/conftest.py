"""Global theme and safety defaults, with opt-in hosted assets."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from typing import Any

import pytest

# The theme is chosen once at import from the date; pin the default so tests that compare
# colours do not change during a seasonal window. Overlay tests resolve themes explicitly.
os.environ.setdefault("RELAYDECK_THEME", "default")


@pytest.fixture(autouse=True)
def _setup_remedies_change_nothing(monkeypatch: Any) -> None:
    """A setup run built without explicit fakes must never download, open or spawn anything."""

    def fake_tools() -> Any:
        from tests.support.setup.remedies import FakeRemedies

        return FakeRemedies().tools()

    monkeypatch.setattr("relaydeck.companion.setup.remediate.real_tools", fake_tools)


@pytest.fixture(autouse=True)
def _process_logging_restored() -> Iterator[None]:
    """Entry points install a process-wide record factory and root handlers; keep them per test."""
    root = logging.getLogger()
    factory = logging.getLogRecordFactory()
    handlers, level = list(root.handlers), root.level
    yield
    logging.setLogRecordFactory(factory)
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture
def hosted_web_bundle(tmp_path: Any, monkeypatch: Any) -> Any:
    from tests.support.companion.web_assets import write_web_assets

    root = write_web_assets(tmp_path / "hosted-web")
    monkeypatch.setenv("FLET_WEB_PATH", str(root))
    return root
