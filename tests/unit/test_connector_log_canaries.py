"""Connector logs name a failure by a fixed category or a validated code, never its message."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from relaydeck.connector.herdr import HerdrAdapter, HerdrError, error_label
from relaydeck.connector.link import ConnectorLink, LinkConfig
from relaydeck.connector.source import HerdrSource

CANARY = "canary-secret-/synthetic/private folder/terminal text"


def _link(tmp_path: Path) -> tuple[ConnectorLink, HerdrSource]:
    source = HerdrSource(HerdrAdapter(socket_path=str(tmp_path / "unused.sock")))
    link = ConnectorLink(
        LinkConfig("http://127.0.0.1:9", "h1", "synthetic", str(tmp_path / "c.db")), source
    )
    return link, source


def _logged(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


def _raiser(exc: BaseException) -> Callable[..., Any]:
    async def fail(*_: object, **__: object) -> None:
        raise exc

    return fail


@pytest.mark.parametrize(
    ("exc", "label"),
    [
        (HerdrError("pane_not_found", CANARY), "pane_not_found"),
        (HerdrError("Bad Code " + CANARY, CANARY), "other"),
        (HerdrError("", CANARY), "other"),
        (HerdrError("x" * 65, CANARY), "other"),
        (ValueError(CANARY), "ValueError"),
        (OSError(5, CANARY, CANARY), "OSError"),
        (FileNotFoundError(2, CANARY, CANARY), "FileNotFoundError"),
    ],
)
def test_error_label_is_a_code_or_a_type_name(exc: BaseException, label: str) -> None:
    assert error_label(exc) == label
    assert CANARY not in error_label(exc)


def test_malformed_hello_entries_are_counted_not_printed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    link, _ = _link(tmp_path)
    caplog.set_level(logging.DEBUG)
    try:
        link._apply_hello_extras(
            {
                "epochs": [
                    CANARY,
                    {"terminal_id": CANARY, "epoch": 7, "kind": "shell"},
                    {"terminal_id": "ok", "epoch": "e1", "kind": CANARY},
                    {"terminal_id": "good", "epoch": "e1", "kind": "shell"},
                ]
            }
        )
    finally:
        link._journal.close()
    text = _logged(caplog)
    assert CANARY not in text and "skipped=3" in text
    assert list(link._last_epochs) == ["good"]


def test_a_hello_that_is_not_a_list_logs_fixed_text(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    link, _ = _link(tmp_path)
    caplog.set_level(logging.DEBUG)
    try:
        link._apply_hello_extras({"epochs": {"terminal_id": CANARY}})
    finally:
        link._journal.close()
    assert CANARY not in _logged(caplog)


@pytest.mark.parametrize("step", ["rebootstrap", "resync"])
@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (HerdrError("socket_closed", CANARY), "socket_closed"),
        (HerdrError("Bad Code", CANARY), "other"),
        (OSError(5, CANARY, CANARY), "OSError"),
    ],
)
def test_upstream_retry_logs_the_label_only(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    step: str,
    exc: BaseException,
    expected: str,
) -> None:
    async def scenario() -> str | None:
        link, source = _link(tmp_path)
        setattr(source, step, _raiser(exc))
        link._source_stale = step == "rebootstrap"
        link._sessions = 2
        try:
            return await link._prepare_source()
        finally:
            link._journal.close()

    caplog.set_level(logging.DEBUG)
    assert asyncio.run(scenario()) == "drop"
    text = _logged(caplog)
    assert CANARY not in text and f"retrying: {expected}" in text


def test_a_failed_source_logs_the_label_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> str:
        link, _ = _link(tmp_path)
        link._session = _raiser(ValueError(CANARY))  # type: ignore[method-assign]
        return (await link.run(asyncio.Event())).reason

    caplog.set_level(logging.DEBUG)
    assert asyncio.run(scenario()) == "source-failed"
    text = _logged(caplog)
    assert CANARY not in text and "source failed: ValueError" in text


def test_an_upstream_error_with_a_code_is_logged_by_code(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> str:
        link, _ = _link(tmp_path)
        link._session = _raiser(HerdrError("pane_gone", CANARY))  # type: ignore[method-assign]
        return (await link.run(asyncio.Event())).reason

    caplog.set_level(logging.DEBUG)
    assert asyncio.run(scenario()) == "source-failed"
    text = _logged(caplog)
    assert CANARY not in text and "source failed: pane_gone" in text


def test_a_failed_pane_subscription_logs_the_label_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        source = HerdrSource(HerdrAdapter(socket_path=str(tmp_path / "unused.sock")))
        source._pane_want = {"p1", "p2"}
        source._pane_dirty = True
        source._adapter.open_subscription = _raiser(RuntimeError(CANARY))  # type: ignore[method-assign]
        await source._ensure_pane_subscriptions()

    caplog.set_level(logging.DEBUG)
    asyncio.run(scenario())
    text = _logged(caplog)
    assert CANARY not in text and "(2 panes), retrying later: RuntimeError" in text


def test_an_attach_failure_reason_is_the_label_only(tmp_path: Path) -> None:
    source = HerdrSource(HerdrAdapter(socket_path=str(tmp_path / "unused.sock")))

    def fail(terminal_id: str, **_: object) -> None:
        raise HerdrError("spawn_blocked", CANARY)

    source.attach_shell = fail  # type: ignore[assignment]
    failures = source.note_epochs({"t1": {"epoch": "e1", "kind": "shell", "mode": "control"}})
    assert failures == {"t1": "spawn_blocked"}
