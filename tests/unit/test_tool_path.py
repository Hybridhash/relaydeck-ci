"""One validated tool search path: setup finds Herdr where its installer put it, the login
service's PATH carries that directory, and the companion resolves the same executable."""

from __future__ import annotations

import os
import plistlib
from pathlib import Path

import pytest

from relaydeck.companion.adapters import launchd as service
from relaydeck.companion.app.herdr_link import HerdrLink
from relaydeck.companion.app.runtime import CompanionRuntime
from relaydeck.companion.config import load_config
from relaydeck.companion.inspection import prereqs
from relaydeck.connector.source import HerdrSource
from relaydeck.host import executables as trusted
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)
from tests.support.companion.asgi_companion import config_file

pytestmark = pytest.mark.usefixtures("hosted_web_bundle")

BOOTSTRAP_PATH = "/usr/bin:/bin"


def folder(path: Path, mode: int = 0o700) -> Path:
    path.mkdir(exist_ok=True)
    path.chmod(mode)
    return path


def tool(path: Path, mode: int = 0o755) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(mode)
    return path


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = folder(tmp_path.resolve() / "home")
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("PATH", BOOTSTRAP_PATH)
    for name in ("UV_TOOL_BIN_DIR", "XDG_BIN_HOME"):
        monkeypatch.delenv(name, raising=False)
    return path


def user_bin(home: Path) -> Path:
    return folder(folder(home / ".local") / "bin")


def plist_path(home: Path, path_env: str) -> str:
    outcome = service.install(
        executable=Path("/synthetic/bin/relaydeck"),
        home=home,
        uid=501,
        yes=False,
        load=False,
        path_env=path_env,
    )
    document = next(line for line in outcome.lines if line.startswith("<?xml"))
    return str(plistlib.loads(document.encode())["EnvironmentVariables"]["PATH"])


def test_the_path_keeps_safe_current_entries_then_the_user_bin_then_the_system(home: Path) -> None:
    herdr_bin = user_bin(home)
    other = folder(home / "tools")
    found = trusted.tool_directories({"PATH": f"{other}:/usr/bin"}, home)
    assert found[0] == str(other)
    assert str(herdr_bin) in found and found.index(str(herdr_bin)) > 0
    assert found[-1] == "/sbin" and "/usr/bin" in found


def test_an_unsafe_or_missing_directory_never_joins_the_path(home: Path) -> None:
    open_bin = folder(user_bin(home), 0o777)
    loose = folder(home / "loose", 0o775)
    found = trusted.tool_directories({"PATH": f"{loose}:{home}/missing:relative/bin"}, home)
    assert str(open_bin) not in found and str(loose) not in found
    assert not any("missing" in item or item.startswith("relative") for item in found)


def test_uvs_configured_tool_bin_joins_when_it_is_safe(home: Path) -> None:
    custom = folder(home / "uvbin")
    found = trusted.tool_directories({"PATH": "", "UV_TOOL_BIN_DIR": str(custom)}, home)
    assert str(custom) in found


def test_herdr_only_in_the_user_bin_is_found_by_setup_the_service_and_the_companion(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    herdr = tool(user_bin(home) / "herdr")
    assert trusted.trusted_executable("herdr", directories=BOOTSTRAP_PATH.split(":")) is None

    # setup: the same lookup its prerequisite probes use
    assert prereqs._locate("herdr") == str(herdr)

    # the login service's PATH is built from that search path, not copied from the bootstrap's
    written = plist_path(home, BOOTSTRAP_PATH)
    assert str(user_bin(home)) in written.split(":")

    # the companion started by launchd with exactly that PATH
    monkeypatch.setenv("PATH", written)
    assert HerdrLink._herdr_binary() == str(herdr)
    runtime = CompanionRuntime(load_config(config_file(tmp_path, web_ui=False)), connect_herdr=True)
    source = runtime.herdr._new_source()
    assert isinstance(source, HerdrSource) and source._binary == str(herdr)


def test_an_unsafe_user_bin_is_not_trusted_by_any_of_them(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = user_bin(home)
    tool(bin_dir / "herdr")
    bin_dir.chmod(0o777)
    assert prereqs._locate("herdr") is None
    assert str(bin_dir) not in plist_path(home, BOOTSTRAP_PATH).split(":")
    assert HerdrLink._herdr_binary() is None
    assert os.environ["PATH"] == BOOTSTRAP_PATH
