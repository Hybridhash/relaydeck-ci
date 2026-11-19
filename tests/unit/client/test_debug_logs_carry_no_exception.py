"""Client debug logs name a failure category, never the exception, its text or a traceback."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from relaydeck.client.features.pairing.service import PairingFlow
from relaydeck.client.features.terminal import haptics
from relaydeck.client.widgets.sessions.gestures import AgentGestures

CANARY = "canary-secret-/synthetic/private folder/token=abc"


class _Leaky(RuntimeError):
    def __init__(self) -> None:
        super().__init__(CANARY)


def _logged(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(
        f"{r.getMessage()} {r.exc_info} {r.exc_text} {r.stack_info}" for r in caplog.records
    )


def test_pairing_haptics_and_swipe_logs_carry_a_category_only(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.DEBUG)

    def dead_listener(_snapshot: Any) -> None:
        raise _Leaky()

    flow = SimpleNamespace(listeners=[dead_listener], snapshot=None)
    PairingFlow.emit(flow, SimpleNamespace())  # type: ignore[arg-type]
    assert flow.listeners == []

    def no_service() -> Any:
        raise _Leaky()

    monkeypatch.setattr(haptics, "context", SimpleNamespace(page=SimpleNamespace()))
    monkeypatch.setattr(haptics.ft, "HapticFeedback", no_service)
    assert haptics.page_haptic_service() is None

    class Refusing:
        async def light_impact(self) -> None:
            raise _Leaky()

    asyncio.run(haptics._light(Refusing()))

    class Control:
        async def _invoke_method(self, *_: Any, **__: Any) -> None:
            raise TimeoutError(CANARY)

    swipe: Any = AgentGestures(
        "a",
        tile=SimpleNamespace(),
        label="Agent",
        dark=True,
        on_menu=lambda _how: None,
        on_rename=lambda: None,
        on_select=lambda: None,
    )
    asyncio.run(swipe.on_confirm_dismiss(SimpleNamespace(control=Control())))

    text = _logged(caplog)
    assert CANARY not in text and "Traceback" not in text
    assert all(r.exc_info is None and r.exc_text is None for r in caplog.records)
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert sum("category=" in m for m in messages) == 4, messages
