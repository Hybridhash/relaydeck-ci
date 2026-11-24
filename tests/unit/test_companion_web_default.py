"""The companion start path points FLET_WEB_PATH at the verified signed bundle
for exactly this version when it is unset; an explicit value always wins;
with neither, start refuses before the runtime exists and never serves the stock web client.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from relaydeck.companion import cli as companion_cli
from relaydeck.releases import web as webrelease
from relaydeck.version import __version__
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)
from tests.support.companion.asgi_companion import config_file
from tests.support.companion.web_assets import write_web_assets


def _unset(monkeypatch: Any) -> None:
    # set-then-delete so monkeypatch restores the original (absent) state afterwards
    monkeypatch.setenv("FLET_WEB_PATH", "placeholder")
    monkeypatch.delenv("FLET_WEB_PATH")


def test_unset_env_uses_the_signed_bundle_for_this_version(
    tmp_path: Path, monkeypatch: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = write_web_assets(tmp_path / "web" / "signed" / ("a" * 64))
    seen: list[str] = []

    def installed(
        version: str, *, store: Any = None, compatible: Any = None
    ) -> webrelease.SignedBundle:
        seen.append(version)
        return webrelease.SignedBundle(version, bundle, "a" * 64)

    _unset(monkeypatch)
    monkeypatch.setattr(webrelease, "installed_signed", installed)
    companion_cli._select_web_bundle()
    assert seen == [__version__]
    assert os.environ["FLET_WEB_PATH"] == str(bundle)
    shown = capsys.readouterr().out
    assert "Web bundle:" in shown and str(bundle) not in shown


def test_an_explicit_env_value_always_wins(tmp_path: Path, monkeypatch: Any) -> None:
    explicit = write_web_assets(tmp_path / "explicit")
    monkeypatch.setenv("FLET_WEB_PATH", str(explicit))

    def forbidden(version: str, *, store: Any = None, compatible: Any = None) -> None:
        raise AssertionError("the store must not be consulted")

    monkeypatch.setattr(webrelease, "installed_signed", forbidden)
    companion_cli._select_web_bundle()
    assert os.environ["FLET_WEB_PATH"] == str(explicit)


def test_no_signed_bundle_refuses_with_the_metadata_code_and_never_falls_back(
    monkeypatch: Any,
) -> None:
    _unset(monkeypatch)
    monkeypatch.setattr(
        webrelease, "installed_signed", lambda version, *, store=None, compatible=None: None
    )
    with pytest.raises(companion_cli.WebBundleMissing, match="^csp_metadata_required$"):
        companion_cli._select_web_bundle()
    assert "FLET_WEB_PATH" not in os.environ


def _start(tmp_path: Path, monkeypatch: Any, **config: Any) -> list[str]:
    """Run `start` with the supervisor replaced; returns the calls that reached it."""
    reached: list[str] = []

    async def supervise(*args: Any, **kwargs: Any) -> int:
        reached.append("supervise")
        return 0

    class Runtime:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            reached.append("runtime")

    monkeypatch.setattr(companion_cli, "supervise", supervise)
    monkeypatch.setattr(companion_cli, "CompanionRuntime", Runtime)
    tmp_path.mkdir(mode=0o700, exist_ok=True)
    path = config_file(tmp_path.resolve(), **config)
    reached.append(f"exit {companion_cli.main(['--config', str(path), 'start'])}")
    return reached


@pytest.mark.parametrize("service_output", [False, True])
@pytest.mark.parametrize("explicit_path", [False, True])
def test_start_refuses_before_the_runtime_exists_when_no_bundle_can_be_served(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: pytest.CaptureFixture[str],
    service_output: bool,
    explicit_path: bool,
) -> None:
    from relaydeck.companion.adapters import service_output as child_output

    monkeypatch.setattr(child_output, "configure_child", lambda: None)
    monkeypatch.setenv("RELAYDECK_SERVICE_OUTPUT", "1" if service_output else "0")
    _unset(monkeypatch)
    if explicit_path:
        monkeypatch.setenv("FLET_WEB_PATH", str(tmp_path / "missing-private-bundle"))
    monkeypatch.setattr(
        webrelease, "installed_signed", lambda version, *, store=None, compatible=None: None
    )
    reached = _start(tmp_path, monkeypatch)
    assert reached == ["exit 2"]
    prefix = "companion: " if service_output else ""
    hint = (
        "Check FLET_WEB_PATH or unset it and run relaydeck web fetch."
        if explicit_path
        else "Run relaydeck web fetch to install the web bundle for this version."
    )
    assert capsys.readouterr().out == (prefix + "csp_metadata_required\n" + hint + "\n")
    assert bool(os.environ.get("FLET_WEB_PATH")) is explicit_path


def test_start_goes_on_with_the_signed_bundle_an_explicit_path_or_no_web_app(
    tmp_path: Path, monkeypatch: Any
) -> None:
    bundle = write_web_assets(tmp_path / "signed")
    _unset(monkeypatch)
    monkeypatch.setattr(
        webrelease,
        "installed_signed",
        lambda version, *, store=None, compatible=None: webrelease.SignedBundle(
            version, bundle, "a" * 64
        ),
    )
    assert _start(tmp_path / "a", monkeypatch)[-1] == "exit 0"
    assert os.environ["FLET_WEB_PATH"] == str(bundle)
    monkeypatch.delenv("FLET_WEB_PATH")
    monkeypatch.setattr(
        webrelease, "installed_signed", lambda version, *, store=None, compatible=None: None
    )
    assert _start(tmp_path / "b", monkeypatch, web_ui=False)[-1] == "exit 0"
    monkeypatch.setenv("FLET_WEB_PATH", str(write_web_assets(tmp_path / "explicit")))
    assert _start(tmp_path / "c", monkeypatch)[-1] == "exit 0"
