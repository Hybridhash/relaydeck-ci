"""Herdr and Tailscale as a setup test controls them: installed or not, answering or not."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from relaydeck.companion.inspection import prereqs
from relaydeck.connector.herdr import HerdrProtocolError
from relaydeck.host.executables import inspect_executable
from tests.support.setup.prereqs import Serve, _done, _status, probes
from tests.support.setup.rig import SetupRig


class HerdrWorld:
    def __init__(self, rig: SetupRig) -> None:
        self.rig = rig
        self.installed = False
        self.running = False
        self.protocol_ok = True
        self.ping_cost = 0.0
        self.pings = 0

    @property
    def destination(self) -> Path:
        return self.rig.home / ".local" / "bin" / "herdr"

    def install(self, mode: int = 0o700) -> None:
        """What a successful installer leaves behind, owned by the test user."""
        for folder in (self.destination.parent.parent, self.destination.parent):
            folder.mkdir(exist_ok=True)
            folder.chmod(0o700)  # whatever the test process's umask is
        self.destination.write_text("#!/bin/sh\n")
        self.destination.chmod(mode)
        self.installed = True

    def which(self, name: str) -> str | None:
        """Like the real lookup, Herdr is found only if the file there passes the trust checks."""
        if name == "herdr" and (
            not self.installed or inspect_executable(self.destination) != "safe"
        ):
            return None
        return f"/synthetic/{name}"

    def exists(self, path: str) -> bool:
        self.rig.remedies.clock.now += self.ping_cost
        return self.running

    def herdr(self, args: Any) -> subprocess.CompletedProcess[str]:
        argv = list(args)
        if argv[:2] == ["status", "server"]:
            self.rig.remedies.clock.now += self.ping_cost
            return _done(args, 0, json.dumps({"running": self.running}))
        return _done(args, 0, "herdr 0.9.1")

    async def ping(self, socket: str) -> None:
        self.pings += 1
        if not self.running:
            raise ConnectionRefusedError("synthetic refusal")
        if not self.protocol_ok:
            raise HerdrProtocolError("protocol_mismatch", "synthetic")

    def probes(self, serve: Serve | None = None, **changes: Any) -> prereqs.Probes:
        return probes(
            serve or self.rig.serve,
            which=self.which,
            herdr=self.herdr,
            ping=self.ping,
            socket_exists=self.exists,
            **changes,
        )


def tailscale_state(backend: str) -> Serve:
    return Serve(_status(BackendState=backend))


def private_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777
