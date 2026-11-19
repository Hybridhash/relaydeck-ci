"""Running a third party's installer for one authorized setup action. The script is downloaded
under the release download rules from one allowlisted host, written to an owner-only
directory made by this run, and run by `/bin/sh` in its own process group with a minimal
environment.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from relaydeck.releases import web as webrelease

HERDR_INSTALLER_URL = "https://herdr.dev/install.sh"
INSTALLER_HOSTS = frozenset({"herdr.dev"})
MAX_INSTALLER_BYTES = 1 << 20
DOWNLOAD_DEADLINE_S = 60.0
RUN_DEADLINE_S = 600.0
KILL_GRACE_S = 3.0
SHELL = "/bin/sh"
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
_PASSED = ("USER", "LANG")


@dataclass(frozen=True)
class Ran:
    """How a script ended: its exit code, or None when it was stopped at the deadline."""

    code: int | None


def download_installer(url: str = HERDR_INSTALLER_URL) -> bytes:
    """The installer text. Raises `webrelease.ReleaseError` with a fixed reason code."""
    body = webrelease.download_bytes(
        url,
        hosts=INSTALLER_HOSTS,
        limit=MAX_INSTALLER_BYTES,
        deadline_s=DOWNLOAD_DEADLINE_S,
    )
    if not body.strip():
        raise webrelease.ReleaseError("network")
    return body


def open_terminal() -> int:
    """A write descriptor on the controlling terminal. Raises OSError without one."""
    fd = os.open("/dev/tty", os.O_WRONLY | os.O_NOCTTY | os.O_CLOEXEC)
    if not os.isatty(fd):
        os.close(fd)
        raise OSError("not a terminal")
    return fd


def installer_environment(home: Path, tmpdir: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """HOME, PATH, LANG, TMPDIR and USER only: no loader, interpreter or installer overrides."""
    env = {"HOME": str(home), "PATH": SYSTEM_PATH, "TMPDIR": str(tmpdir)}
    env.update({name: environ[name] for name in _PASSED if name in environ})
    return env


def _stop_group(process: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=KILL_GRACE_S)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def run_script(
    script: Path,
    env: Mapping[str, str],
    terminal: int,
    timeout_s: float = RUN_DEADLINE_S,
) -> Ran:
    """Run `script` with `/bin/sh`, output to `terminal`. The group is stopped and reaped when the
    deadline passes or this call is interrupted, and the interrupt is raised again."""
    process = subprocess.Popen(  # noqa: S603 - fixed shell, one file this run wrote
        [SHELL, str(script)],
        stdin=subprocess.DEVNULL,
        stdout=terminal,
        stderr=terminal,
        env=dict(env),
        cwd=script.parent,
        close_fds=True,
        start_new_session=True,
    )
    try:
        return Ran(process.wait(timeout=timeout_s))
    except subprocess.TimeoutExpired:
        _stop_group(process)
        return Ran(None)
    except BaseException:
        _stop_group(process)
        raise
