"""A fresh synthetic home and every effect setup uses, faked: Tailscale, Herdr probes, the web
fetch, launchd, the companion's published state, the clock and the typed answers. Nothing here
reaches the real home, network, Keychain or launchd."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

from relaydeck.companion.config import CompanionConfig, load_config
from relaydeck.companion.inspection import running
from relaydeck.companion.inspection.detect import DetectReport
from relaydeck.companion.setup import SetupReport, WebFetchError, run_setup
from relaydeck.companion.setup.steps import Step
from tests.support.setup.prereqs import Serve, probes
from tests.support.setup.remedies import FakeRemedies

NO_CLIS = DetectReport((), "ok", "ok")


class Answers:
    """Answers by a fragment of the question, default No; every question is recorded."""

    def __init__(self, rules: dict[str, bool] | None = None, default: bool = False) -> None:
        self.rules = dict(rules or {})
        self.default = default
        self.asked: list[str] = []

    def __call__(self, prompt: str) -> bool:
        self.asked.append(prompt)
        for fragment, answer in self.rules.items():
            if fragment in prompt:
                return answer
        return self.default

    def count(self, fragment: str) -> int:
        return sum(1 for question in self.asked if fragment in question)


class FakeWeb:
    def __init__(self, installed: bool = False, fail: str | None = None) -> None:
        self.is_installed = installed
        self.fail = fail
        self.fetched: list[str] = []

    def installed(self, version: str) -> bool:
        return self.is_installed

    def fetch(self, version: str) -> str:
        self.fetched.append(version)
        if self.fail:
            raise WebFetchError(self.fail)
        self.is_installed = True
        return version


class Clock:
    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class FakeLaunchd:
    """Records launchctl calls; the agent counts as loaded after a bootstrap."""

    def __init__(self, companion: FakeCompanion) -> None:
        self.calls: list[list[str]] = []
        self.loaded = False
        self.companion = companion

    def __call__(self, args: Any) -> int:
        argv = list(args)
        self.calls.append(argv)
        if argv[1] == "bootstrap":
            self.loaded = True
            self.companion.up = True
        if argv[1] == "bootout":
            self.loaded = False
        if argv[1] == "print":
            return 0 if self.loaded else 113
        return 0


class FakeCompanion:
    """The status document `lifecycle.status` would return."""

    def __init__(self) -> None:
        self.up = False
        self.override: dict[str, Any] | None = None
        self.config_path: Path | None = None

    def __call__(self, state_dir: Path) -> dict[str, Any]:
        if self.override is not None:
            return self.override
        if not self.up or self.config_path is None:
            return {"state": "stopped"}
        return healthy_body(load_config(self.config_path))


def healthy_body(config: CompanionConfig, **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "version": 1,
        "instance": "synthetic-instance",
        "state": "setup_required",
        "failed": False,
        "advertised_origin": config.advertised_origin,
        "local_endpoint": config.local_endpoint,
        "api_ready": True,
        "connector_ready": True,
        "pairing_available": True,
        "synthetic": False,
    }
    body.update(changes)
    assert running.ready(body, config) or changes
    return body


class SetupRig:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.home = tmp_path.resolve() / "home"
        self.home.mkdir(mode=0o700)
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.delenv("RELAYDECK_DURABLE_SIGNINS", raising=False)
        self.config_path = self.home / ".config" / "relaydeck" / "companion.toml"
        self.serve = Serve()
        self.answers = Answers()
        self.web = FakeWeb()
        self.companion = FakeCompanion()
        self.companion.config_path = self.config_path
        self.launchd = FakeLaunchd(self.companion)
        self.clock = Clock()
        self.said: list[str] = []
        self.stops: list[Path] = []
        self.routes: list[str] = []
        self.pairs = 0
        self.remedies = FakeRemedies()
        self.environ: dict[str, str] = {}

    def make_config_folder(self, mode: int = 0o700) -> Path:
        """`~/.config/relaydeck`, independent of the test process's umask."""
        parent = self.config_path.parent.parent
        parent.mkdir(exist_ok=True)
        parent.chmod(0o755)
        folder = self.config_path.parent
        folder.mkdir(exist_ok=True)
        folder.chmod(mode)
        return folder

    def request_stop(self, state_dir: Path) -> bool:
        self.stops.append(state_dir)
        self.companion.up = False
        return True

    def route_probe(self, url: str) -> dict[str, str]:
        self.routes.append(url)
        return {"state": "reachable"}

    def run(self, steps: list[Step] | None = None, **over: Any) -> SetupReport:
        options: dict[str, Any] = {
            "config_path": self.config_path,
            "assume_yes": False,
            "confirm": self.answers,
            "probes": probes(self.serve),
            "runner": self.serve,
            "source_detect": lambda _path: NO_CLIS,
            "emit_line": self.said.append,
            "home": self.home,
            "web": self.web,
            "launchctl": self.launchd,
            "executable": Path("/synthetic/bin/relaydeck"),
            "uid": 501,
            "clock": self.clock,
            "sleep": self.clock.sleep,
            "now": self.clock,
            "companion_state": self.companion,
            "request_stop": self.request_stop,
            "route_probe": self.route_probe,
            "environ": self.environ,
            "remediation": self.remedies.tools(),
        }
        return run_setup(steps=steps, **(options | over))

    @property
    def text(self) -> str:
        return "\n".join(self.said)

    def config(self) -> CompanionConfig:
        return load_config(self.config_path)

    def snapshot(self) -> dict[str, tuple[int, bytes | None]]:
        """Every file and folder under the home: (mode, bytes for a file)."""
        seen: dict[str, tuple[int, bytes | None]] = {}
        for base, dirs, files in os.walk(self.home):
            for name in dirs:
                path = Path(base, name)
                seen[str(path.relative_to(self.home))] = (stat.S_IMODE(path.lstat().st_mode), None)
            for name in files:
                path = Path(base, name)
                info = path.lstat()
                body = path.read_bytes() if stat.S_ISREG(info.st_mode) else b"<special>"
                seen[str(path.relative_to(self.home))] = (stat.S_IMODE(info.st_mode), body)
        return seen
