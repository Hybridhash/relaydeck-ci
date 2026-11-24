"""Everything a setup step touches outside its own files, injected so tests never reach the real
Tailscale, launchd, network or Keychain."""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from relaydeck.companion.adapters import launchd as service
from relaydeck.companion.adapters import tailscale
from relaydeck.companion.app import lifecycle
from relaydeck.companion.config import CompanionConfig, load_config
from relaydeck.companion.inspection import prereqs
from relaydeck.companion.inspection.detect import DetectReport
from relaydeck.companion.inspection.status import status as lifecycle_status
from relaydeck.companion.setup import remediate
from relaydeck.companion.setup.probes import load_probe_targets
from relaydeck.usage.api import ProbeTarget, Spawn

DEFAULT_KEY_PARTS = (".config", "relaydeck", "connector_key.json")


class WebBundles(Protocol):
    """The signed web bundle for one release version."""

    def installed(self, version: str) -> bool: ...

    def fetch(self, version: str) -> str:
        """Download, verify and install; returns nothing secret. Raises a `WebFetchError`."""
        ...


class WebFetchError(Exception):
    """`reason` is one fixed code (version, network, too_large, signature, archive, manifest,
    install)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ReleaseWeb:
    """The real thing, imported late so a missing module only matters when it is used."""

    def installed(self, version: str) -> bool:
        from relaydeck.releases import web as webrelease

        return webrelease.installed_signed(version) is not None

    def fetch(self, version: str) -> str:
        from relaydeck.releases import web as webrelease

        try:
            webrelease.fetch_release(version)
        except webrelease.ReleaseError as exc:
            raise WebFetchError(exc.reason) from None
        return version


class LocalWeb:
    """The same signed bundle from a local archive and signature (`setup --web-archive`); it never
    reaches the network. The packaged key is used unless a test passes its own."""

    def __init__(
        self,
        archive: Path,
        signature: Path,
        key: bytes | None = None,
        store: Path | None = None,
    ) -> None:
        self.archive = archive
        self.signature = signature
        self.key = key
        self.store = store

    def installed(self, version: str) -> bool:
        from relaydeck.releases import web as webrelease

        return webrelease.installed_signed(version, store=self.store) is not None

    def fetch(self, version: str) -> str:
        from relaydeck.releases import web as webrelease

        try:
            webrelease.install_archive(
                self.archive, self.signature, version, key=self.key, store=self.store
            )
        except webrelease.ReleaseError as exc:
            raise WebFetchError(exc.reason) from None
        return version


def _stop_companion(state_dir: Path) -> bool:
    return asyncio.run(lifecycle.request_stop(state_dir))


def _route_probe(url: str) -> dict[str, str]:
    from relaydeck.companion.inspection.running import probe_private

    return asyncio.run(probe_private(url))


@dataclass
class SetupContext:
    config_path: Path
    confirm: Callable[[str], bool]
    emit: Callable[[str], None] | None = None
    assume_yes: bool = False
    home: Path = field(default_factory=Path.home)
    runner: tailscale.Runner = tailscale.default_runner
    probes: prereqs.Probes | None = None
    install_limits_hook: bool = False
    re_enroll: bool = False
    source_confirm: Callable[[str], bool] | None = None
    source_detect: Callable[[Path], DetectReport] | None = None
    probe_targets: Callable[[Path, Path], Sequence[ProbeTarget]] = load_probe_targets
    trust_spawn: Spawn | None = None
    trust_which: Callable[[str], Any] | None = None
    web: WebBundles = field(default_factory=ReleaseWeb)
    remediation: remediate.Tools = field(default_factory=lambda: remediate.real_tools())  # noqa: PLW0108 - looked up at call time
    launchctl: service.Runner = service.default_runner
    executable: Path = field(default_factory=lambda: Path(sys.argv[0]).expanduser().resolve())
    uid: int = field(default_factory=os.getuid)
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.time
    companion_state: Callable[[Path], dict[str, Any]] = lifecycle_status
    request_stop: Callable[[Path], bool] = _stop_companion
    state_lock: Callable[[Path], AbstractContextManager[int]] = lifecycle.state_lock
    route_probe: Callable[[str], dict[str, str]] = _route_probe
    prompt: Callable[[str], str] = input
    token_hex: Callable[[int], str] = secrets.token_hex
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)
    provider_factory: Callable[[], Any] | None = None
    pair: Callable[[SetupContext], int] | None = None
    skip_route_probe: bool = False
    summary: bool = True
    # filled in as steps run
    config: CompanionConfig | None = None
    report: prereqs.Report | None = None
    configure_only: bool = False
    running: bool = False
    lines: list[str] = field(default_factory=list)
    committed: list[str] = field(default_factory=list)  # what this run has already changed

    def say(self, line: str) -> None:
        self.lines.append(line)
        if self.emit is not None:
            self.emit(line)

    @property
    def key_file(self) -> Path:
        return self.home.joinpath(*DEFAULT_KEY_PARTS)

    @property
    def ask(self) -> Callable[[str], bool]:
        return self.source_confirm or self.confirm

    def short(self, path: Path | str) -> str:
        """A display path with the home folder as ~."""
        text = str(path)
        home = str(self.home)
        if home and (text == home or text.startswith(home + os.sep)):
            return "~" + text[len(home) :]
        return text

    def load(self) -> CompanionConfig:
        """The config as it is now. Raises ValueError or OSError when it cannot be read."""
        self.config = load_config(
            self.config_path,
            resolve_origin=lambda port: tailscale.private_origin(port, self.runner),
        )
        return self.config

    def reload(self) -> CompanionConfig | None:
        try:
            return self.load()
        except (ValueError, OSError, tailscale.TailscaleError):
            self.config = None
            return None

    @property
    def sign_ins(self) -> str:
        durable = self.environ.get("RELAYDECK_DURABLE_SIGNINS") == "1"
        return "stored in the Keychain" if durable else "kept in memory (a restart ends them)"
