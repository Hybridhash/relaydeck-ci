"""Read prerequisite state for setup and diagnostics without installing, logging in or changing
services. Report only fixed guidance, versions and port numbers.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from relaydeck.companion.adapters import tailscale
from relaydeck.connector.herdr import DEFAULT_SOCKET, HerdrError, HerdrProtocolError, resolve_socket
from relaydeck.git.api import GitRunner, GitUnavailable
from relaydeck.host.executables import probe_environment, tool_directories, trusted_executable

STATE_OK = "ok"
STATE_MISSING = "missing"
STATE_NOT_RUNNING = "not_running"
STATE_NOT_LOGGED_IN = "not_logged_in"
STATE_NEEDS_ACTION = "needs_action"
STATE_UNKNOWN = "unknown"

TIMEOUT_S = 15
UV_VERSION = re.compile(r"\buv (\d+\.\d+\.\d+)")
HERDR_VERSION = re.compile(r"\b(\d+\.\d+\.\d+)\b")
PYTHON_MIN = (3, 13)
PYTHON_BELOW = (3, 14)

# One fixed guidance line per cause. Nothing here is built from runtime data.
GUIDANCE: dict[str, str] = {
    "tailscale_missing": "Install Tailscale from https://tailscale.com/download/mac, then open it.",
    "tailscale_stopped": "Start Tailscale on this Mac, then re-run setup.",
    "tailscale_login": "Open Tailscale and log in.",
    "tailscale_approval": "Ask the tailnet admin to approve this Mac.",
    "tailscale_dns": "Enable MagicDNS and HTTPS certificates for the tailnet.",
    "tailscale_unknown": (
        "Tailscale did not answer clearly. Check it is running, then re-run setup."
    ),
    "serve_funnel": (
        "Public Funnel is on for the target HTTPS port. Turn it off, then re-run setup."
    ),
    "serve_unknown": "Tailscale Serve did not answer clearly. Re-run setup.",
    "serve_after_tailscale": "Fix the Tailscale check first, then re-run setup.",
    "serve_conflict": (
        "The configured HTTPS port is occupied. Explicitly change tailscale_https_port in the "
        "config to a free private port, then re-run setup; no alternative port was selected."
    ),
    "herdr_missing": "Install Herdr, start it, then re-run setup.",
    "herdr_down": "Start Herdr separately, then re-run setup.",
    "herdr_protocol": "Update Herdr or the companion so both speak the same Herdr protocol.",
    "herdr_unknown": "Herdr did not answer clearly. Check its socket, then re-run setup.",
    "uv_missing": (
        "Install uv (https://docs.astral.sh/uv/getting-started/installation/), then re-run."
    ),
    "uv_unknown": "uv did not report a version. Check uv on PATH.",
    "python": (
        "Use Python 3.13 in a uv tool environment: uv tool install --managed-python --python 3.13."
    ),
    "ok": "",
}

# Git is needed only for the Changes screen, so a missing or old git is a warning, never a block.
GIT_WARNINGS: dict[str, str] = {
    "git_missing": (
        "Changes screen unavailable: install Apple's developer tools with `xcode-select --install`"
    ),
    "git_too_old": (
        "Changes screen unavailable: git 2.45 or newer is needed; update Apple's developer tools "
        "with `xcode-select --install`"
    ),
    "git_unknown": (
        "Changes screen unavailable: git did not answer clearly; check it with `git --version`"
    ),
}


class _Unknown(Exception):
    """Internal: a probe answered in a way that cannot be classified."""


@dataclass(frozen=True)
class Result:
    check: str
    state: str
    guidance: str = ""
    detail: str = ""  # content-free: a version or a port number
    reason: str = ""  # the GUIDANCE key of a failed check; actions dispatch on it, never on text

    def line(self) -> str:
        text = f"{self.check}: {self.state}"
        if self.detail:
            text += f" ({self.detail})"
        if self.state != STATE_OK and self.guidance:
            text += f" - {self.guidance}"
        return text

    def as_doc(self) -> dict[str, str]:
        return {
            "check": self.check,
            "state": self.state,
            "guidance": "" if self.state == STATE_OK else self.guidance,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Report:
    results: tuple[Result, ...]
    warnings: tuple[str, ...] = ()
    dns_name: str | None = None  # this Mac's MagicDNS name once the Tailscale check passed

    @property
    def ready(self) -> bool:
        return all(result.state == STATE_OK for result in self.results)

    def lines(self) -> tuple[str, ...]:
        return (
            *(result.line() for result in self.results),
            *(f"warning: {w}" for w in self.warnings),
        )

    def as_doc(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "checks": [result.as_doc() for result in self.results],
            "warnings": list(self.warnings),
        }


def _fail(check: str, state: str, reason: str, detail: str = "") -> Result:
    return Result(check, state, GUIDANCE[reason], detail, reason)


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
Ping = Callable[[str], Coroutine[Any, Any, None]]


def _normal(name: object) -> str:
    return str(name).strip().rstrip(".").lower()


def _locate(program: str) -> str | None:
    """A trusted executable on the companion's tool search path: PATH, then `~/.local/bin`, where
    uv and Herdr's installer put theirs (a fresh shell may not list it yet)."""
    return trusted_executable(program, directories=tool_directories())


def _run_fixed(program: str, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    binary = _locate(program)
    if binary is None:
        raise FileNotFoundError("No trusted executable is available")
    return subprocess.run(  # noqa: S603 - fixed program, argument list, no shell
        [binary, *args],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
        env=probe_environment(),
    )


async def _default_ping(socket: str) -> None:
    from relaydeck.connector.herdr import HerdrAdapter

    await HerdrAdapter(socket, timeout=5.0).ping()


@dataclass
class Probes:
    """Every external read the preflight makes. Tests inject fakes; defaults are real."""

    tailscale: Runner
    find_tailscale: Callable[[], str]
    which: Callable[[str], str | None] = _locate
    herdr: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = field(
        default=lambda args: _run_fixed("herdr", args)
    )
    uv: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = field(
        default=lambda args: _run_fixed("uv", args)
    )
    ping: Ping = _default_ping
    socket_exists: Callable[[str], bool] = lambda path: Path(path).exists()  # noqa: E731
    python_version: tuple[int, int] = (sys.version_info[0], sys.version_info[1])
    git_state: Callable[[], str] = lambda: "ok"  # noqa: E731 - checked only by default_probes


def default_probes(tailscale_runner: Runner | None = None) -> Probes:
    runner = tailscale_runner or tailscale.default_runner
    return Probes(tailscale=runner, find_tailscale=tailscale.find_cli, git_state=git_state)


def _developer_tools_present() -> bool:
    """`/usr/bin/git` is a stub that opens an install dialog until the developer tools exist."""
    binary = trusted_executable("/usr/bin/xcode-select")
    if binary is None:
        return False
    try:
        done = subprocess.run(  # noqa: S603 - fixed program and argument, no shell
            [binary, "-p"],
            capture_output=True,
            timeout=5,
            check=False,
            env=probe_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def git_state() -> str:
    """ok, or git_missing / git_too_old / git_unknown, by the Changes screen's own rules."""
    git = trusted_executable("git")
    if git is None or (git == "/usr/bin/git" and not _developer_tools_present()):
        return "git_missing"
    try:
        GitRunner.create()
    except GitUnavailable as exc:
        return str(exc) if str(exc) in ("git_missing", "git_too_old") else "git_unknown"
    except Exception:  # noqa: BLE001 - any failure of this optional check is a warning
        return "git_unknown"
    return "ok"


# -- Tailscale ---------------------------------------------------------------------------------


def _status_doc(run: Runner) -> dict[str, Any]:
    try:
        result = run(["status", "--json", "--peers=false"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _Unknown from exc
    if result.returncode != 0:
        raise _Unknown
    try:
        doc = json.loads(result.stdout or "{}")
    except ValueError as exc:
        raise _Unknown from exc
    if not isinstance(doc, dict):
        raise _Unknown
    return doc


def check_tailscale(probes: Probes) -> tuple[Result, str | None]:
    """(result, this Mac's normalised MagicDNS name when ready)."""
    try:
        probes.find_tailscale()
    except tailscale.TailscaleError:
        return _fail("tailscale", STATE_MISSING, "tailscale_missing"), None
    try:
        doc = _status_doc(probes.tailscale)
    except _Unknown:
        return _fail("tailscale", STATE_UNKNOWN, "tailscale_unknown"), None
    backend = doc.get("BackendState")
    if backend == "Stopped":
        return _fail("tailscale", STATE_NOT_RUNNING, "tailscale_stopped"), None
    if backend == "NeedsLogin":
        return _fail("tailscale", STATE_NOT_LOGGED_IN, "tailscale_login"), None
    if backend == "NeedsMachineAuth":
        return _fail("tailscale", STATE_NEEDS_ACTION, "tailscale_approval"), None
    if backend != "Running":
        return _fail("tailscale", STATE_UNKNOWN, "tailscale_unknown"), None
    self_node = doc.get("Self")
    tailnet = doc.get("CurrentTailnet")
    certs = doc.get("CertDomains", [])
    if (
        not isinstance(self_node, dict)
        or not isinstance(tailnet, dict)
        or not isinstance(certs, list)
    ):
        return _fail("tailscale", STATE_UNKNOWN, "tailscale_unknown"), None
    dns = _normal(self_node.get("DNSName") or "")
    magic = tailnet.get("MagicDNSEnabled")
    if not isinstance(magic, bool):
        return _fail("tailscale", STATE_UNKNOWN, "tailscale_unknown"), None
    if not magic or not dns.endswith(".ts.net"):
        return _fail("tailscale", STATE_NEEDS_ACTION, "tailscale_dns"), None
    if dns not in {_normal(entry) for entry in certs if isinstance(entry, str)}:
        return _fail("tailscale", STATE_NEEDS_ACTION, "tailscale_dns"), None
    return Result("tailscale", STATE_OK), dns


def check_serve(probes: Probes, https_port: int, expected_target: str | None = None) -> Result:
    """Read `serve status --json`: refuse public (Funnel) access on the target HTTPS port."""
    try:
        doc = tailscale.serve_status(probes.tailscale)
    except tailscale.TailscaleError:
        return _fail("tailscale_serve", STATE_UNKNOWN, "serve_unknown")
    state = tailscale.inspect_serve(doc, https_port, expected_target)
    if state == "public":
        return _fail("tailscale_serve", STATE_NEEDS_ACTION, "serve_funnel")
    if state == "conflict":
        return _fail("tailscale_serve", STATE_NEEDS_ACTION, "serve_conflict")
    if state == "unknown":
        return _fail("tailscale_serve", STATE_UNKNOWN, "serve_unknown")
    return Result("tailscale_serve", STATE_OK, detail=f"port {https_port}")


# -- Herdr -----------------------------------------------------------------------------------


def _herdr_running(doc: Mapping[str, Any]) -> bool | None:
    if isinstance(doc.get("running"), bool):
        return bool(doc["running"])
    for key in ("status", "state"):
        value = doc.get(key)
        if isinstance(value, str):
            if value.lower() == "running":
                return True
            if value.lower() in ("stopped", "not_running", "absent"):
                return False
    return None


def check_herdr(probes: Probes, socket: str) -> Result:
    """Herdr on the selected socket. The socket's protocol answer is authoritative; Herdr's own
    `status server` describes the default endpoint only, so it is consulted only for that one."""
    if probes.which("herdr") is None:
        return _fail("herdr", STATE_MISSING, "herdr_missing")
    try:
        version = probes.herdr(["--version"])
    except (OSError, subprocess.TimeoutExpired):
        return _fail("herdr", STATE_UNKNOWN, "herdr_unknown")
    match = HERDR_VERSION.search(version.stdout or "") if version.returncode == 0 else None
    if match is None:
        return _fail("herdr", STATE_UNKNOWN, "herdr_unknown")
    detail = match.group(1)
    if socket == probe_socket(None):
        down = _default_endpoint_down(probes)
        if down is not None:
            return _fail("herdr", down[0], down[1], detail)
    if not probes.socket_exists(socket):
        return _fail("herdr", STATE_NOT_RUNNING, "herdr_down", detail)
    try:
        asyncio.run(probes.ping(socket))
    except HerdrProtocolError as exc:
        if exc.code == "protocol_mismatch":
            return _fail("herdr", STATE_NEEDS_ACTION, "herdr_protocol", detail)
        return _fail("herdr", STATE_UNKNOWN, "herdr_unknown", detail)
    except HerdrError:
        return _fail("herdr", STATE_UNKNOWN, "herdr_unknown", detail)
    except (TimeoutError, OSError):
        return _fail("herdr", STATE_NOT_RUNNING, "herdr_down", detail)
    return Result("herdr", STATE_OK, detail=detail)


def _default_endpoint_down(probes: Probes) -> tuple[str, str] | None:
    """(state, reason) when `herdr status server` says the default server is not up."""
    try:
        status = probes.herdr(["status", "server", "--json"])
    except (OSError, subprocess.TimeoutExpired):
        return STATE_UNKNOWN, "herdr_unknown"
    if status.returncode != 0:
        return STATE_NOT_RUNNING, "herdr_down"
    try:
        doc = json.loads(status.stdout or "{}")
    except ValueError:
        return STATE_UNKNOWN, "herdr_unknown"
    if not isinstance(doc, dict):
        return STATE_UNKNOWN, "herdr_unknown"
    running = _herdr_running(doc)
    if running is False:
        return STATE_NOT_RUNNING, "herdr_down"
    if running is None:
        return STATE_UNKNOWN, "herdr_unknown"
    return None


# -- uv and the runtime interpreter ----------------------------------------------------------


def check_uv(probes: Probes) -> Result:
    if probes.which("uv") is None:
        return _fail("uv", STATE_MISSING, "uv_missing")
    try:
        result = probes.uv(["--version"])
    except (OSError, subprocess.TimeoutExpired):
        return _fail("uv", STATE_UNKNOWN, "uv_unknown")
    match = UV_VERSION.search(result.stdout or "") if result.returncode == 0 else None
    if match is None:
        return _fail("uv", STATE_UNKNOWN, "uv_unknown")
    return Result("uv", STATE_OK, detail=match.group(1))


def check_python(probes: Probes) -> Result:
    version = probes.python_version
    if PYTHON_MIN <= version < PYTHON_BELOW:
        return Result("python", STATE_OK, detail=f"{version[0]}.{version[1]}")
    return _fail("python", STATE_NEEDS_ACTION, "python", f"{version[0]}.{version[1]}")


# -- the preflight ------------------------------------------------------------------------------


def run_prereqs(
    *,
    socket: str,
    https_port: int,
    probes: Probes,
    expected_target: str | None = None,
    usage_roots: Sequence[Path] = (),
) -> Report:
    tailscale_result, dns = check_tailscale(probes)
    if dns is not None:
        serve = check_serve(probes, https_port, expected_target)
    else:
        serve = _fail("tailscale_serve", STATE_UNKNOWN, "serve_after_tailscale")
    results = (
        tailscale_result,
        serve,
        check_herdr(probes, socket),
        check_uv(probes),
        check_python(probes),
    )
    git = probes.git_state()
    warnings = (
        *(
            f"usage source {index + 1}: root unavailable"
            for index, root in enumerate(usage_roots)
            if not Path(root).is_dir()
        ),
        *((GIT_WARNINGS[git],) if git != "ok" else ()),
    )
    return Report(results=results, warnings=warnings, dns_name=dns)


def probe_socket(configured: str | None) -> str:
    """The configured socket, or the default one when no config exists yet. An unresolvable path
    is returned unchanged; the Herdr check then reports it as unknown rather than raising."""
    try:
        return resolve_socket(configured or DEFAULT_SOCKET)
    except HerdrError:
        return configured or DEFAULT_SOCKET
