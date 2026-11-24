"""Native launch identity and the typed notification consumer seam."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)

ROOT = Path(__file__).resolve().parents[2]


def test_the_thin_launcher_uses_the_canonical_native_main() -> None:
    launcher = importlib.import_module("relaydeck.client.main")
    entry = importlib.import_module("relaydeck.client.app.native.entry")
    root = importlib.import_module("relaydeck.client.app.root")
    assert launcher.main is entry.main
    assert not hasattr(root, "main")


def _mypy(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    target = tmp_path / "consumer.py"
    target.write_text(source)
    env = dict(
        os.environ,
        PYTHONDONTWRITEBYTECODE="1",
        MYPYPATH=os.pathsep.join(
            str(ROOT / p)
            for p in ("src", ".", "extensions/terminal/src", "extensions/notifications/src")
        ),
    )
    return subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "mypy",
            "--strict",
            "--no-incremental",
            "--cache-dir=/dev/null",
            str(target),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_the_concrete_native_owner_and_resources_satisfy_the_consumer(tmp_path: Path) -> None:
    result = _mypy(
        tmp_path,
        """
from typing import cast
from relaydeck.client.app.notifications import _NotificationOwner, _NotificationResources
from relaydeck.client.app.resources import AppResources
from relaydeck.client.app.native.runtime import PackagedRuntime

def owner(value: _NotificationOwner) -> None: pass
def resources(value: _NotificationResources) -> None: pass
owner(cast(PackagedRuntime, None))
resources(cast(AppResources, None))
""",
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("member", ["notify", "alive"])
def test_a_wrong_owner_member_is_rejected(tmp_path: Path, member: str) -> None:
    source = """
from collections.abc import Callable
from typing import Any
from relaydeck.client.app.notifications import _NotificationOwner, _NotificationResources
from relaydeck.client.features.auth.service import AuthService

class BadOwner:
    alive: bool
    endpoint: str
    auth: AuthService | None
    resources: _NotificationResources | None
    notify: Callable[[str], Any]
    def input_allowed(self) -> bool: return True
    async def callback(self, uri: str) -> None: pass

def owner(value: _NotificationOwner) -> None: pass
owner(BadOwner())
"""
    if member == "alive":
        source = source.replace("alive: bool", "alive: str")
        source = source.replace("notify: Callable[[str], Any]", "notify: Callable[[], Any]")
    result = _mypy(tmp_path, source)
    assert result.returncode != 0
    assert result.stdout.count("error:") == 1, result.stdout
    assert "[arg-type]" in result.stdout
    if member == "notify":
        assert "def () -> Any" in result.stdout
        assert "def (str, /) -> Any" in result.stdout
    else:
        assert "alive" in result.stdout
    assert "BadOwner" in result.stdout


def test_a_wrong_resource_property_is_rejected(tmp_path: Path) -> None:
    result = _mypy(
        tmp_path,
        """
from typing import Any
from relaydeck.client.app.notifications import _NotificationResources
from relaydeck.client.app.state.sync import SyncController
from relaydeck.client.ports.transport import GatewayTransport

class BadResources:
    props: dict[str, Any]
    transport: GatewayTransport
    controller: SyncController
    bridge: str

def resources(value: _NotificationResources) -> None: pass
resources(BadResources())
""",
    )
    assert result.returncode != 0
    assert result.stdout.count("error:") == 1, result.stdout
    assert "[arg-type]" in result.stdout
    assert "bridge" in result.stdout
    assert "BadResources" in result.stdout


def test_fresh_launcher_defers_services_and_preserves_flet_arguments(tmp_path: Path) -> None:
    import flet

    assert flet.__file__ is not None
    paths = [
        ROOT / "src",
        ROOT / "extensions/terminal/src",
        ROOT / "extensions/notifications/src",
        Path(flet.__file__).resolve().parent.parent,
    ]
    code = f"""
import importlib, pathlib, runpy, sys
sys.dont_write_bytecode = True
sys.path = {[str(p) for p in paths]!r} + sys.path
assert not any("editable" in type(f).__name__.lower() for f in sys.meta_path)
import flet as ft
import flet_secure_storage
import httpx
from relaydeck_notifications import RelayDeckNotifications
calls = []
def refused(*args, **kwargs):
    raise AssertionError("native service constructed during import or launcher dispatch")
for cls in (
    httpx.Client, httpx.AsyncClient, flet_secure_storage.SecureStorage, RelayDeckNotifications
):
    cls.__init__ = refused
ft.run = lambda *args, **kwargs: calls.append((args, kwargs))
launcher = importlib.import_module("relaydeck.client.main")
entry = importlib.import_module("relaydeck.client.app.native.entry")
from relaydeck.client.asset_paths import CLIENT_ASSETS
assert launcher.main is entry.main
assert calls == []
runpy.run_path(launcher.__file__, run_name="__main__")
assert calls == [((entry.main,), {{"assets_dir": str(CLIENT_ASSETS)}})]
for name, module in list(sys.modules.items()):
    if name.startswith("relaydeck.") and getattr(module, "__file__", None):
        assert pathlib.Path(module.__file__).resolve().is_relative_to({str(ROOT / "src")!r}), name
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
