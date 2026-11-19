"""Fake effects for setup's remedies: nothing here downloads, opens, spawns or sleeps."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from relaydeck.companion.inspection import prereqs
from relaydeck.companion.setup import installer, remediate
from relaydeck.releases import web as webrelease


class FakeClock:
    """A clock that only the fake sleep moves, so a wait takes no real time."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []
        self.ends: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        self.ends.append(self.now)


class FakeRemedies:
    """Records every effect a remedy would have. Defaults: the download fails, there is no
    terminal and nothing opens, so an unprepared test can never change the Mac."""

    def __init__(self) -> None:
        self.clock = FakeClock()
        self.downloads: list[str] = []
        self.opened: list[list[str]] = []
        self.scripts: list[tuple[bytes, dict[str, str], int]] = []
        self.body: bytes | None = None
        self.fail_download: str | None = "network"
        self.has_terminal = False
        self.terminal_path = os.devnull
        self.opened_terminals: list[int] = []
        self.open_code = 1
        self.ran = installer.Ran(0)
        self.raise_on_run: BaseException | None = None

    def download(self, url: str) -> bytes:
        self.downloads.append(url)
        if self.fail_download is not None or self.body is None:
            raise webrelease.ReleaseError(self.fail_download or "network")
        return self.body

    def open_terminal(self) -> int:
        """A fresh descriptor each time, as the real one gives, so the caller may close it."""
        if not self.has_terminal:
            raise OSError("no terminal")
        fd = os.open(self.terminal_path, os.O_WRONLY | os.O_APPEND)
        self.opened_terminals.append(fd)
        return fd

    def run_script(self, script: Path, env: Mapping[str, str], terminal: int) -> installer.Ran:
        self.scripts.append((script.read_bytes(), dict(env), terminal))
        if self.raise_on_run is not None:
            raise self.raise_on_run
        return self.ran

    def opener(self, argv: Sequence[str]) -> int:
        self.opened.append(list(argv))
        return self.open_code

    def allow_install(
        self, body: bytes = b"echo synthetic installer\n", terminal: str | None = None
    ) -> None:
        self.body, self.fail_download, self.has_terminal = body, None, True
        if terminal is not None:
            self.terminal_path = terminal

    def allow_open(self) -> None:
        self.open_code = 0

    def bounded(self, call: remediate.Recheck, seconds: float) -> prereqs.Report | None:
        """The probe runs inline; one that outlasts its budget on the fake clock is given up on,
        as the real helper thread would."""
        started = self.clock.now
        report = call()
        return report if self.clock.now - started <= seconds else None

    def tools(self) -> remediate.Tools:
        return remediate.Tools(
            download=self.download,
            open_terminal=self.open_terminal,
            run_script=self.run_script,
            opener=self.opener,
            clock=self.clock,
            sleep=self.clock.sleep,
            bounded=self.bounded,
        )
