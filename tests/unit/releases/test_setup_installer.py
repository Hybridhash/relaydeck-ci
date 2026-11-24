"""Running another project's installer for one authorized action: where it is downloaded from, what
environment it gets, where its output goes, and that nothing it started outlives a stop."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest

from relaydeck.companion.setup import installer, remediate
from relaydeck.releases import web as webrelease

HOST = installer.HERDR_INSTALLER_URL


class Reply:
    def __init__(self, status: int, body: bytes = b"", headers: Mapping[str, str] | None = None):
        self.status_code = status
        self.headers = dict(headers or {})
        self._body = body

    def chunks(self) -> Iterable[bytes]:
        yield self._body

    def close(self) -> None:
        pass


class Getter:
    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)
        self.urls: list[str] = []

    def get(self, url: str, *, timeout: float) -> Reply:
        self.urls.append(url)
        return self.replies.pop(0)


def fetch(getter: Getter, *, limit: int = 1024, clock: Any = None) -> bytes:
    return webrelease.download_bytes(
        HOST,
        hosts=installer.INSTALLER_HOSTS,
        limit=limit,
        deadline_s=60.0,
        http=getter,
        clock=clock or (lambda: 0.0),
    )


# -- the download -------------------------------------------------------------------------------


def test_the_installer_comes_from_herdr_dev_over_tls_only() -> None:
    assert installer.HERDR_INSTALLER_URL == "https://herdr.dev/install.sh"
    assert installer.INSTALLER_HOSTS == frozenset({"herdr.dev"})
    assert fetch(Getter(Reply(200, b"echo hi\n"))) == b"echo hi\n"


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/install.sh",
        "http://herdr.dev/install.sh",
        "https://github.com/x/install.sh",
        "https://herdr.dev:8443/install.sh",
        "https://user@herdr.dev/install.sh",
    ],
)
def test_a_redirect_off_the_list_or_off_tls_is_refused(target: str) -> None:
    getter = Getter(Reply(302, headers={"location": target}), Reply(200, b"echo hi\n"))
    with pytest.raises(webrelease.ReleaseError) as caught:
        fetch(getter)
    assert caught.value.reason == "network" and len(getter.urls) == 1


def test_a_redirect_inside_the_list_is_followed() -> None:
    getter = Getter(
        Reply(301, headers={"location": "https://herdr.dev/v1/install.sh"}), Reply(200, b"ok\n")
    )
    assert fetch(getter) == b"ok\n"


def test_the_size_cap_and_the_deadline_apply() -> None:
    with pytest.raises(webrelease.ReleaseError) as caught:
        fetch(Getter(Reply(200, b"x" * 2000)), limit=1024)
    assert caught.value.reason == "too_large"
    ticks = iter([0.0, 0.0, 100.0, 100.0])
    with pytest.raises(webrelease.ReleaseError) as late:
        fetch(Getter(Reply(200, b"ok\n")), clock=lambda: next(ticks, 100.0))
    assert late.value.reason == "network"
    assert installer.MAX_INSTALLER_BYTES == 1 << 20


def test_the_real_download_uses_the_one_host_the_size_cap_and_a_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake(url: str, **kwargs: Any) -> bytes:
        seen.update(kwargs, url=url)
        return b"echo hi\n"

    monkeypatch.setattr(webrelease, "download_bytes", fake)
    assert installer.download_installer() == b"echo hi\n"
    assert seen["hosts"] == installer.INSTALLER_HOSTS and seen["limit"] == 1 << 20
    assert 0 < seen["deadline_s"] <= 120 and seen["url"] == HOST


def test_an_empty_installer_is_a_failed_download(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webrelease, "download_bytes", lambda url, **kw: b"  \n")
    with pytest.raises(webrelease.ReleaseError):
        installer.download_installer()


# -- the environment and the terminal -----------------------------------------------------------


def test_the_installer_environment_is_five_names_and_no_loader_or_index_override() -> None:
    hostile = {
        "HOME": "/synthetic/ignored",
        "USER": "synthetic",
        "LANG": "en_US.UTF-8",
        "PATH": "/evil/bin",
        "DYLD_INSERT_LIBRARIES": "/evil.dylib",
        "PYTHONPATH": "/evil",
        "UV_INDEX_URL": "https://evil.example",
        "SHELLOPTS": "xtrace",
        "ENV": "/evil/rc",
        "BASH_ENV": "/evil/rc",
        "TMPDIR": "/evil/tmp",
    }
    env = installer.installer_environment(Path("/synthetic/home"), Path("/synthetic/tmp"), hostile)
    assert env == {
        "HOME": "/synthetic/home",
        "PATH": installer.SYSTEM_PATH,
        "TMPDIR": "/synthetic/tmp",
        "USER": "synthetic",
        "LANG": "en_US.UTF-8",
    }


def test_the_terminal_must_be_a_real_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[int] = []
    monkeypatch.setattr(os, "open", lambda *a, **k: 99)
    monkeypatch.setattr(os, "isatty", lambda fd: False)
    monkeypatch.setattr(os, "close", closed.append)
    with pytest.raises(OSError):
        installer.open_terminal()
    assert closed == [99]

    def refused(*args: Any, **kwargs: Any) -> int:
        raise OSError("no controlling terminal")

    monkeypatch.setattr(os, "open", refused)
    with pytest.raises(OSError):
        installer.open_terminal()


# -- running the script -------------------------------------------------------------------------


def script_in(tmp_path: Path, text: str) -> Path:
    folder = tmp_path / "scripts"
    folder.mkdir(exist_ok=True, mode=0o700)
    path = folder / "install.sh"
    path.write_text(text)
    return path


def run(
    tmp_path: Path, text: str, *, timeout: float = 10.0, env: Mapping[str, str] | None = None
) -> tuple[installer.Ran, str]:
    out = tmp_path / "terminal.txt"
    out.write_text("")
    fd = os.open(out, os.O_WRONLY | os.O_APPEND)
    try:
        ran = installer.run_script(
            script_in(tmp_path, text), env or {"PATH": installer.SYSTEM_PATH}, fd, timeout
        )
    finally:
        os.close(fd)
    return ran, out.read_text()


def test_output_goes_to_the_given_terminal_not_to_this_process(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    ran, terminal = run(tmp_path, "echo OUT-LINE; echo ERR-LINE >&2\n")
    assert ran == installer.Ran(0) and "OUT-LINE" in terminal and "ERR-LINE" in terminal
    captured = capfd.readouterr()
    assert "OUT-LINE" not in captured.out + captured.err
    assert "ERR-LINE" not in captured.out + captured.err


def test_the_exit_code_is_returned(tmp_path: Path) -> None:
    assert run(tmp_path, "exit 7\n")[0] == installer.Ran(7)


def test_the_script_sees_only_the_given_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "/evil.dylib")
    monkeypatch.setenv("UV_INDEX_URL", "https://evil.example")
    given = {"HOME": str(tmp_path), "PATH": installer.SYSTEM_PATH, "LANG": "C"}
    _ran, terminal = run(tmp_path, "env\n", env=given)
    names = {line.split("=", 1)[0] for line in terminal.splitlines() if "=" in line}
    assert names <= set(given) | {"PWD", "SHLVL", "_", "OLDPWD", "__CF_USER_TEXT_ENCODING"}
    assert "DYLD_INSERT_LIBRARIES" not in names and "UV_INDEX_URL" not in names


def test_the_script_has_no_stdin(tmp_path: Path) -> None:
    _ran, terminal = run(tmp_path, "if read line; then echo GOT; else echo EOF; fi\n")
    assert "EOF" in terminal and "GOT" not in terminal


def test_the_script_runs_in_its_own_process_group(tmp_path: Path) -> None:
    _ran, terminal = run(tmp_path, "ps -o pgid= -p $$; ps -o pgid= -p $PPID\n")
    mine = os.getpgrp()
    assert str(mine) not in terminal.split()[:1]


def wait_for_absence(path: Path, seconds: float = 1.6) -> bool:
    """True when `path` is still absent after the descendant would have written it."""
    time.sleep(seconds)
    return not path.exists()


def test_a_deadline_stops_the_script_and_a_descendant_that_would_write_later(
    tmp_path: Path,
) -> None:
    mark = tmp_path / "late.txt"
    text = f"( sleep 1; echo late > '{mark}' ) &\nsleep 30\n"
    started = time.monotonic()
    ran, _terminal = run(tmp_path, text, timeout=0.3)
    assert ran == installer.Ran(None) and time.monotonic() - started < 10
    assert wait_for_absence(mark)


def test_an_interrupt_stops_the_whole_group_and_is_raised_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mark = tmp_path / "late.txt"
    real = subprocess.Popen.wait
    calls: list[int] = []

    def interrupted(self: Any, timeout: float | None = None) -> int:
        calls.append(1)
        if len(calls) == 1:
            time.sleep(0.2)  # let the script start its background work
            raise KeyboardInterrupt
        return real(self, timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", interrupted)
    text = f"( sleep 1; echo late > '{mark}' ) &\nsleep 30\n"
    with pytest.raises(KeyboardInterrupt):
        run(tmp_path, text)
    assert wait_for_absence(mark)


def test_setup_output_can_be_redirected_while_the_installer_still_has_the_terminal(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    # `relaydeck setup >setup.log` redirects fd 1 and 2; the installer gets the terminal fd only.
    log = tmp_path / "setup.log"
    saved = (os.dup(1), os.dup(2))
    with open(log, "w") as handle:
        os.dup2(handle.fileno(), 1)
        os.dup2(handle.fileno(), 2)
        try:
            _ran, terminal = run(tmp_path, "echo INSTALLER-TEXT; echo MORE >&2\n")
        finally:
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            for fd in saved:
                os.close(fd)
    assert "INSTALLER-TEXT" in terminal and "INSTALLER-TEXT" not in log.read_text()
    assert "MORE" not in log.read_text()


# -- opening an app or a page ------------------------------------------------------------


def test_open_target_runs_usr_bin_open_with_fixed_arguments_and_discards_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake(argv: list[str], **kwargs: Any) -> Any:
        seen.update(kwargs, argv=argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake)
    assert remediate.open_target(["-a", "Tailscale"]) == 0
    assert seen["argv"] == ["/usr/bin/open", "-a", "Tailscale"]
    assert seen["stdout"] == subprocess.DEVNULL and seen["stderr"] == subprocess.DEVNULL
    assert "shell" not in seen or not seen["shell"]


def test_open_target_reports_failure_as_a_code_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(argv: list[str], **kwargs: Any) -> Any:
        raise OSError("synthetic")

    monkeypatch.setattr(subprocess, "run", broken)
    assert remediate.open_target(["https://tailscale.com/download/mac"]) == 1
