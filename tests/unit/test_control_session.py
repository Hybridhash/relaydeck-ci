"""Controller sessions: the pane follows the controller viewport, commands are JSON lines."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import os as module_os
import stat
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from relaydeck.connector import source as module
from relaydeck.connector.attach import (
    AttachError,
    CommandWriter,
    ControlSession,
    _record,
    control_argv,
)
from relaydeck.connector.herdr import HerdrAdapter
from relaydeck.connector.source import GEOMETRY_REASSERT_S, HerdrSource

# Emits one frame at the requested viewport, then echoes every stdin command as a frame whose
# bytes are the command line; `terminal.release` ends the stream like Herdr does.
_BRIDGE_STUB = """#!{python}
import base64, json, sys
args = sys.argv[1:]
cols, rows = int(args[args.index("--cols") + 1]), int(args[args.index("--rows") + 1])
def frame(payload, width=None, height=None):
    print(json.dumps({{"type": "terminal.frame", "encoding": "ansi", "seq": 1, "full": True,
                       "width": width or cols, "height": height or rows,
                       "bytes": base64.b64encode(payload).decode()}}), flush=True)
frame(b"ready")
for line in sys.stdin:
    command = json.loads(line)
    if command["type"] == "terminal.release":
        print(json.dumps({{"type": "terminal.closed", "reason": "detached"}}), flush=True)
        break
    if command["type"] == "terminal.resize":
        frame(line.encode(), command["cols"], command["rows"])
    else:
        frame(line.encode())
"""

_REFUSE_STUB = """#!{python}
import json
print(json.dumps({{"type": "terminal.closed", "reason": "terminal attach failed: terminal "
                  "term_1 already has an attached client; retry with --takeover"}}), flush=True)
"""


def _stub(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text(body.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _read_until(session: ControlSession, needle: bytes, timeout: float = 5.0) -> bytes:
    seen = b""
    deadline = time.monotonic() + timeout
    while needle not in seen and time.monotonic() < deadline:
        seen += session.read(timeout=0.2)
    assert needle in seen
    return seen


def _commands(seen: bytes) -> list[dict[str, Any]]:
    return [json.loads(line) for line in seen.decode().splitlines() if line.startswith("{")]


def test_control_argv_carries_viewport_and_optional_takeover() -> None:
    assert control_argv("herdr", "w1:p1", takeover=False, cols=53, rows=40) == [
        *("herdr", "terminal", "session", "control", "w1:p1", "--cols", "53", "--rows", "40")
    ]
    assert control_argv("herdr", "term_1", takeover=True, cols=91, rows=19)[-1] == "--takeover"


def test_input_resize_and_release_are_json_commands(tmp_path: Path) -> None:
    argv = control_argv(
        _stub(tmp_path, "bridge", _BRIDGE_STUB), "w1:p1", takeover=False, cols=53, rows=40
    )
    session = ControlSession(argv, cols=53, rows=40, capture_stderr=True)
    session.spawn()
    try:
        _read_until(session, b"ready")
        assert session.viewport == (53, 40)
        session.write(b"\x1b[A")
        session.resize(91, 19)
        session.resize(91, 19)  # a same-size resize is sent, never suppressed
        seen = _read_until(session, b"terminal.resize", timeout=5.0)
        while seen.count(b"terminal.resize") < 2:
            seen += session.read(timeout=0.2)
        commands = _commands(seen)
        assert commands[0] == {
            "type": "terminal.input",
            "bytes": base64.b64encode(b"\x1b[A").decode(),
        }
        assert commands[1:] == [{"type": "terminal.resize", "cols": 91, "rows": 19}] * 2
        assert session.viewport == (91, 19) and (session.cols, session.rows) == (91, 19)
    finally:
        status = session.close()
    assert status == 0 and session.running is False  # released, not killed


def test_refusal_is_a_closed_record_before_any_frame(tmp_path: Path) -> None:
    session = ControlSession([_stub(tmp_path, "refuse", _REFUSE_STUB)], capture_stderr=True)
    session.spawn()
    try:
        deadline = time.monotonic() + 5.0
        while session.running and time.monotonic() < deadline:
            session.read(timeout=0.1)
        session.read(timeout=0.5)
        assert session.frames_seen == 0 and session.closed_reason
        assert "takeover" in session.closed_reason
        assert session.collect_diagnostics() == "terminal_owned"
    finally:
        session.close()


class FakeControl(ControlSession):
    instances: list[FakeControl] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.alive = False
        self.output = b""
        self.resizes: list[tuple[int, int]] = []
        self.instances.append(self)

    def spawn(self) -> None:
        self.alive = True

    @property
    def running(self) -> bool:
        return self.alive

    def read(self, max_bytes: int = 65536, timeout: float = 0) -> bytes:
        data, self.output = self.output[:max_bytes], self.output[max_bytes:]
        return data

    def read_available(self, max_bytes: int = 65536) -> bytes:
        return self.read(max_bytes)

    def resize(self, cols: int, rows: int) -> None:
        self.resizes.append((cols, rows))
        self.cols, self.rows = cols, rows

    def close(self, timeout: float = 5) -> int | None:
        self.alive = False
        return 0


class Clock:
    now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def controlled(monkeypatch: pytest.MonkeyPatch) -> tuple[HerdrSource, FakeControl, Clock]:
    FakeControl.instances = []
    monkeypatch.setattr(module, "ControlSession", FakeControl)
    clock = Clock()
    source = HerdrSource(
        HerdrAdapter(), subscriptions=[], retry_clock=clock, controller_viewport=True
    )
    assert source.note_epochs({"t1": {"epoch": "e1", "kind": "shell", "mode": "control"}}) == {}
    (session,) = FakeControl.instances
    assert source.resize_terminal("t1", "e1", 53, 40) is None or True
    source._flush_resize("t1")
    session.resizes.clear()
    return source, session, clock


def test_frames_with_another_size_reassert_the_last_applied_size(
    controlled: tuple[HerdrSource, FakeControl, Clock], caplog: pytest.LogCaptureFixture
) -> None:
    source, session, clock = controlled
    caplog.set_level(logging.INFO, logger="relaydeck.connector.source")
    session.viewport = (53, 40)
    session.output = b"x"
    assert source.pump_live() == 1
    assert session.resizes == []  # frames agree: nothing to do
    session.viewport = (233, 49)  # the window took the pane back
    session.output = b"y"
    source.pump_live()
    assert session.resizes == [(53, 40)]
    assert "pane_geometry_reasserted cols=53 rows=40" in caplog.text
    session.output = b"z"
    source.pump_live()  # inside the window: no second resize, no second log line
    assert session.resizes == [(53, 40)]
    assert caplog.text.count("pane_geometry_reasserted") == 1
    clock.now += GEOMETRY_REASSERT_S
    session.output = b"w"
    source.pump_live()
    assert session.resizes == [(53, 40), (53, 40)]


def test_events_correct_only_a_mismatch_and_respect_the_cooldown(
    controlled: tuple[HerdrSource, FakeControl, Clock],
) -> None:
    source, session, clock = controlled
    session.viewport = (53, 40)
    source._consume_live_line("layout.updated", {})
    assert session.resizes == []  # frames agree: nothing to correct
    session.viewport = (233, 49)
    source._consume_live_line("layout.updated", {})
    assert session.resizes == [(53, 40)]
    source._consume_live_line("layout.updated", {})
    assert session.resizes == [(53, 40)]  # inside the cooldown: no second resize now
    clock.now += GEOMETRY_REASSERT_S
    source._consume_live_line("layout.updated", {})
    assert session.resizes == [(53, 40), (53, 40)]


def test_reassert_stops_when_control_ends(
    controlled: tuple[HerdrSource, FakeControl, Clock],
) -> None:
    source, session, clock = controlled
    session.viewport = (233, 49)
    source.drop_terminal("t1")
    clock.now += GEOMETRY_REASSERT_S
    assert source.reassert_geometry(force=True) == 0
    assert session.resizes == []


# -- one ordered command writer ----------------------------------------------------------


def _records(data: bytes) -> list[dict[str, Any]]:
    lines = data.decode().split("\n")
    assert lines[-1] == ""  # every record ends with its newline
    return [json.loads(line) for line in lines[:-1]]


def _short_writes(monkeypatch: pytest.MonkeyPatch, limit: int, *, fail_after: int = 0) -> None:
    """os.write that accepts at most `limit` bytes and blocks every other call."""
    real = module_os.write
    calls = {"n": 0}

    def write(fd: int, data: Any) -> int:
        calls["n"] += 1
        if fail_after and calls["n"] > fail_after:
            raise OSError("synthetic")
        if calls["n"] % 2 == 0:
            raise BlockingIOError
        return real(fd, bytes(data[:limit]))

    monkeypatch.setattr(module_os, "write", write)


def test_input_resize_and_release_stay_whole_under_short_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        reader, writer_fd = os.pipe()
        os.set_blocking(writer_fd, False)
        _short_writes(monkeypatch, 7)
        writer = CommandWriter(writer_fd)
        payload = _record({"type": "terminal.input", "bytes": "A" * 200})
        end = writer.submit(payload)
        assert not writer.done(end)
        writer.submit(_record({"type": "terminal.resize", "cols": 91, "rows": 19}))  # mid-record
        writer.submit(_record({"type": "terminal.release"}))
        await asyncio.wait_for(writer.wait(end), 5)
        deadline = time.monotonic() + 5
        while writer.pending and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        os.close(writer_fd)
        records = _records(os.read(reader, 65536))
        os.close(reader)
        assert [r["type"] for r in records] == [
            "terminal.input",
            "terminal.resize",
            "terminal.release",
        ]
        assert records[0]["bytes"] == "A" * 200

    asyncio.run(scenario())


def test_cancelled_waiter_leaves_its_record_intact(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        reader, writer_fd = os.pipe()
        os.set_blocking(writer_fd, False)
        _short_writes(monkeypatch, 5)
        writer = CommandWriter(writer_fd)
        end = writer.submit(_record({"type": "terminal.input", "bytes": "B" * 100}))
        task = asyncio.ensure_future(writer.wait(end))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        writer.submit(_record({"type": "terminal.resize", "cols": 53, "rows": 40}))
        deadline = time.monotonic() + 5
        while writer.pending and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        os.close(writer_fd)
        records = _records(os.read(reader, 65536))
        os.close(reader)
        assert [r["type"] for r in records] == ["terminal.input", "terminal.resize"]
        assert records[0]["bytes"] == "B" * 100

    asyncio.run(scenario())


def test_write_error_poisons_fails_waiters_and_refuses_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        reader, writer_fd = os.pipe()
        os.set_blocking(writer_fd, False)
        _short_writes(monkeypatch, 4, fail_after=1)
        poisoned: list[str] = []
        writer = CommandWriter(writer_fd, poisoned.append)
        end = writer.submit(_record({"type": "terminal.input", "bytes": "C" * 50}))
        with pytest.raises(AttachError, match="poisoned"):
            await asyncio.wait_for(writer.wait(end), 5)
        assert poisoned and writer.poisoned
        with pytest.raises(AttachError, match="poisoned"):
            writer.submit(_record({"type": "terminal.release"}))
        os.close(writer_fd)
        os.close(reader)

    asyncio.run(scenario())


def test_stalled_pipe_poisons_after_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        reader, writer_fd = os.pipe()
        os.set_blocking(writer_fd, False)
        monkeypatch.setattr(module_os, "write", lambda *_: (_ for _ in ()).throw(BlockingIOError))
        monkeypatch.setattr(CommandWriter, "DEADLINE_S", 0.05)
        writer = CommandWriter(writer_fd)
        end = writer.submit(_record({"type": "terminal.input", "bytes": "D"}))
        with pytest.raises(AttachError, match="write timeout"):
            await asyncio.wait_for(writer.wait(end), 5)
        os.close(writer_fd)
        os.close(reader)

    asyncio.run(scenario())


def test_queue_limit_refuses_a_whole_record() -> None:
    reader, writer_fd = os.pipe()
    os.set_blocking(writer_fd, False)
    writer = CommandWriter(writer_fd)
    with pytest.raises(AttachError, match="queue full"):
        writer.submit(b"x" * (CommandWriter.LIMIT + 1))
    assert writer.pending == 0 and writer.poisoned is None
    os.close(writer_fd)
    os.close(reader)


def test_poisoned_session_terminates_its_process(tmp_path: Path) -> None:
    stub = _stub(tmp_path, "bridge", _BRIDGE_STUB)
    argv = control_argv(stub, "w1:p1", takeover=False, cols=53, rows=40)
    session = ControlSession(argv, cols=53, rows=40, capture_stderr=True)
    session.spawn()
    try:
        _read_until(session, b"ready")
        session._commands()._poison("synthetic")
        deadline = time.monotonic() + 5
        while session.running and time.monotonic() < deadline:
            session.read(timeout=0.1)
        assert not session.running
        assert session.collect_diagnostics() == "command_stream_poisoned"
        with pytest.raises(AttachError):
            session.resize(1, 1)
    finally:
        session.close()


# -- typed refusal, scoped and eventually-corrected geometry -------------------------------


def test_refusal_is_typed_not_a_reset_and_never_respawns_under_that_epoch(
    controlled: tuple[HerdrSource, FakeControl, Clock],
) -> None:
    source, session, _clock = controlled
    session.closed_reason = "terminal attach failed: already attached; retry with --takeover"
    session.alive = False
    source.pump_live()
    assert source.drain_terminal_resets() == {}
    assert source.drain_attach_refusals() == {"t1": "e1"}
    before = len(FakeControl.instances)
    failures = source.note_epochs({"t1": {"epoch": "e1", "kind": "shell", "mode": "control"}})
    assert "Take over" in failures["t1"] and len(FakeControl.instances) == before
    # A new epoch carrying the viewer's explicit takeover attaches with --takeover.
    assert (
        source.note_epochs(
            {"t1": {"epoch": "e2", "kind": "shell", "mode": "control", "takeover": True}}
        )
        == {}
    )
    assert FakeControl.instances[-1].argv[-1] == "--takeover"
    assert source.drain_attach_refusals() == {}


def test_death_without_refusal_is_still_a_reset(
    controlled: tuple[HerdrSource, FakeControl, Clock],
) -> None:
    source, session, _clock = controlled
    session.alive = False
    source.pump_live()
    assert source.drain_terminal_resets() == {"t1": "e1"}
    assert source.drain_attach_refusals() == {}


def test_quiet_mismatch_inside_the_cooldown_is_corrected_by_a_timer(
    monkeypatch: pytest.MonkeyPatch, controlled: tuple[HerdrSource, FakeControl, Clock]
) -> None:
    source, session, clock = controlled
    monkeypatch.setattr(module, "GEOMETRY_REASSERT_S", 0.05)

    async def scenario() -> None:
        session.viewport = (233, 49)
        session.output = b"a"
        source.pump_live()
        assert session.resizes == [(53, 40)]  # first correction, cooldown starts
        session.output = b"b"
        source.pump_live()  # still mismatching, inside the cooldown: a timer is pending
        assert session.resizes == [(53, 40)] and "t1" in source._correction_timers
        clock.now += 0.06
        await asyncio.sleep(0.1)  # the pane stays quiet; the timer fires and rechecks
        assert session.resizes == [(53, 40), (53, 40)]
        assert "t1" not in source._correction_timers

    asyncio.run(scenario())


def test_correction_timer_is_cancelled_when_control_ends(
    monkeypatch: pytest.MonkeyPatch, controlled: tuple[HerdrSource, FakeControl, Clock]
) -> None:
    source, session, _clock = controlled
    monkeypatch.setattr(module, "GEOMETRY_REASSERT_S", 0.05)

    async def scenario() -> None:
        session.viewport = (233, 49)
        session.output = b"a"
        source.pump_live()
        session.output = b"b"
        source.pump_live()
        assert "t1" in source._correction_timers
        source.note_epochs({"t1": {"epoch": "e1", "kind": "shell", "mode": "observe"}})
        assert "t1" not in source._correction_timers
        await asyncio.sleep(0.1)
        assert session.resizes == [(53, 40)]

    asyncio.run(scenario())


def test_pane_events_touch_only_the_affected_mismatching_terminal(
    controlled: tuple[HerdrSource, FakeControl, Clock],
) -> None:
    source, session, _clock = controlled
    session.viewport = (53, 40)
    source._consume_live_line("pane.updated", {"pane_id": "w9:p9"})
    source._consume_live_line("layout.updated", {})
    assert session.resizes == []  # frames agree: an event is never a forced resize
    session.viewport = (233, 49)
    source._consume_live_line("pane.updated", {"pane_id": "w9:p9"})  # another pane
    assert session.resizes == []
    source._snapshot = {"panes": [{"pane_id": "w1:p1", "terminal_id": "t1"}]}
    source._consume_live_line("pane.updated", {"pane": {"pane_id": "w1:p1"}})
    assert session.resizes == [(53, 40)]
