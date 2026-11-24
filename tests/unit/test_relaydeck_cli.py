"""the `relaydeck` entry point delegates companion commands unchanged, keeps
install commands local, refuses without `--yes`, and never runs launchctl or tailscale.
Temporary homes and stores only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from relaydeck import cli
from relaydeck.companion.adapters import launchd as service_agent
from relaydeck.releases import bundles as webbundle
from relaydeck.releases import web as webrelease
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)
from tests.support.companion.web_assets import write_bundle_manifest

GOOD_JS = (
    '"touchend";' * 4
    + '"touchcancel";' * 2
    + '"abort";' * 3
    + '"flet_reconnect_merge_rd1";'
    + '"flet_reconnect_wake_rd1";'
    + '"item_extents";'
)


def test_help_lists_the_companion_and_install_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0
    text = capsys.readouterr().out
    for name in ("setup", "service", "web", "start", "pairing"):
        assert name in text


def test_companion_commands_are_delegated_with_argv_unchanged(monkeypatch: Any) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr("relaydeck.companion.cli.main", lambda argv: seen.append(list(argv)) or 0)
    assert cli.main(["--config", "/tmp/x.toml", "pairing", "list"]) == 0
    assert cli.main(["start", "--connect-herdr"]) == 0
    assert seen == [
        ["--config", "/tmp/x.toml", "pairing", "list"],
        ["start", "--connect-herdr"],
    ]


def test_web_install_refuses_a_bundle_without_markers(
    tmp_path: Path, monkeypatch: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(webbundle, "default_root", lambda: tmp_path / "store")
    source = tmp_path / "src"
    source.mkdir()
    (source / "main.dart.js").write_text("var app = 1;")
    assert cli.main(["web", "install", str(source)]) == 1
    assert "web install refused" in capsys.readouterr().out
    assert not (tmp_path / "store").exists() or not any((tmp_path / "store").iterdir())


def test_web_install_then_list_shows_the_current_bundle(
    tmp_path: Path, monkeypatch: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(webbundle, "default_root", lambda: tmp_path / "store")
    source = tmp_path / "src"
    (source).mkdir()
    (source / "main.dart.js").write_text(GOOD_JS)
    (source / "index.html").write_text("<html>app</html>")
    write_bundle_manifest(source)
    assert cli.main(["web", "install", str(source)]) == 0
    assert "Installed" in capsys.readouterr().out
    assert cli.main(["web", "list"]) == 0
    assert "current" in capsys.readouterr().out


def test_web_fetch_installs_the_signed_bundle_and_reports_refusals_by_code(
    tmp_path: Path, monkeypatch: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = tmp_path / "signed" / ("a" * 64)
    seen: list[str] = []

    def fetch(version: str) -> webrelease.SignedBundle:
        seen.append(version)
        return webrelease.SignedBundle(version, bundle, "a" * 64)

    monkeypatch.setattr(webrelease, "fetch_release", fetch)
    assert cli.main(["web", "fetch", "--version", "1.2.3"]) == 0
    assert seen == ["1.2.3"]
    assert "signature verified" in capsys.readouterr().out

    def refused(version: str) -> webrelease.SignedBundle:
        raise webrelease.ReleaseError("signature")

    monkeypatch.setattr(webrelease, "fetch_release", refused)
    assert cli.main(["web", "fetch", "--version", "1.2.3"]) == 1
    assert capsys.readouterr().out.strip() == "web fetch refused: signature"
    assert cli.main(["web", "fetch", "--archive", "only.tar.gz"]) == 2


def test_service_install_without_yes_writes_nothing(
    tmp_path: Path, monkeypatch: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda _cls: tmp_path))
    assert cli.main(["service", "install"]) == 0
    out = capsys.readouterr().out
    assert "launchctl bootstrap gui/" in out and "Not installed" in out
    assert not service_agent.agent_path(tmp_path).exists()


def test_service_status_reads_the_file_without_launchctl(
    tmp_path: Path, monkeypatch: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda _cls: tmp_path))
    assert cli.main(["service", "status"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "installed": False,
        "agent": str(service_agent.agent_path(tmp_path)),
    }


def test_first_command_skips_the_config_option() -> None:
    assert cli._first_command(["--config", "x", "start"]) == "start"
    assert cli._first_command(["--config=x", "pairing"]) == "pairing"
    assert cli._first_command(["setup", "--yes"]) == "setup"
    assert cli._first_command(["--help"]) is None
