"""Setup's own remedies for a failed prerequisite, run only by the interactive `relaydeck
setup`. Each remedy is asked for first and does one thing: install Herdr with its official
installer, or open Tailscale (or one of its pages) for the owner to finish, then wait a
bounded time for the check to pass.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from relaydeck.companion.config import CompanionConfig
from relaydeck.companion.inspection import prereqs
from relaydeck.companion.setup import installer
from relaydeck.host.executables import inspect_executable, probe_environment
from relaydeck.releases import web as webrelease

POLL_S = 3.0
WAIT_S = 300.0
OPEN = "/usr/bin/open"
OPEN_TIMEOUT_S = 15
TAILSCALE_DOWNLOAD = "https://tailscale.com/download/mac"
TAILSCALE_DNS = "https://login.tailscale.com/admin/dns"
HERDR_MANUAL = "curl -fsSL https://herdr.dev/install.sh | sh"
HERDR_BIN = (".local", "bin", "herdr")

Recheck = Callable[[], prereqs.Report]


def run_bounded(call: Recheck, seconds: float) -> prereqs.Report | None:
    """`call()` on a helper thread, given up on (not waited for) when `seconds` run out. The
    probes it runs are read-only and end by their own timeouts."""
    outcome: list[prereqs.Report | BaseException] = []
    finished = threading.Event()

    def work() -> None:
        try:
            outcome.append(call())
        except BaseException as exc:  # noqa: BLE001 - handed to the caller, not lost
            outcome.append(exc)
        finally:
            finished.set()

    threading.Thread(target=work, name="setup-probe", daemon=True).start()
    if not finished.wait(max(seconds, 0.0)):
        return None
    result = outcome[0]
    if isinstance(result, BaseException):
        raise result
    return result


def open_target(argv: Sequence[str]) -> int:
    """Run `/usr/bin/open` with fixed arguments; its output is discarded."""
    try:
        done = subprocess.run(  # noqa: S603 - fixed program and arguments, no shell
            [OPEN, *argv],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=OPEN_TIMEOUT_S,
            check=False,
            env=probe_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1
    return done.returncode


@dataclass
class Tools:
    """Everything a remedy touches outside the process, so tests inject fakes."""

    download: Callable[[str], bytes] = installer.download_installer
    open_terminal: Callable[[], int] = installer.open_terminal
    run_script: Callable[[Path, Mapping[str, str], int], installer.Ran] = installer.run_script
    opener: Callable[[Sequence[str]], int] = open_target
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    bounded: Callable[[Recheck, float], prereqs.Report | None] = run_bounded


def real_tools() -> Tools:
    return Tools()


@dataclass
class Effects:
    """What this run changed on the Mac; reported apart from RelayDeck's own files."""

    installed: list[str] = field(default_factory=list)
    partial: list[str] = field(default_factory=list)
    opened: list[str] = field(default_factory=list)

    def lines(self) -> tuple[str, ...]:
        return (
            *(f"Installed: {name}." for name in self.installed),
            *(f"{name} may be partly installed." for name in self.partial),
            *(f"Opened: {name}." for name in self.opened),
        )

    def __bool__(self) -> bool:
        return bool(self.installed or self.partial or self.opened)


@dataclass(frozen=True)
class Remedied:
    report: prereqs.Report
    effects: Effects


Action = Callable[[prereqs.Result], prereqs.Result | None]


class RemedyContext(Protocol):
    assume_yes: bool
    committed: list[str]
    home: Path
    environ: Mapping[str, str]
    config: CompanionConfig | None
    remediation: Tools

    @property
    def confirm(self) -> Callable[[str], bool]: ...

    def say(self, line: str) -> None: ...


class _Remedy:
    def __init__(
        self, ctx: RemedyContext, tools: Tools, recheck: Recheck, effects: Effects
    ) -> None:
        self.ctx = ctx
        self.tools = tools
        self.recheck = recheck
        self.effects = effects

    def check(self, name: str) -> prereqs.Result:
        return self._pick(self.recheck(), name)

    @staticmethod
    def _pick(report: prereqs.Report, name: str) -> prereqs.Result:
        return next(item for item in report.results if item.check == name)

    def ask(self, question: str) -> bool:
        return self.ctx.assume_yes or self.ctx.confirm(question)

    def say(self, line: str) -> None:
        self.ctx.say(line)

    def wait_for_change(self, start: prereqs.Result) -> prereqs.Result:
        """Poll every few seconds, for at most five minutes of the injected clock, until the
        check no longer fails for the reason it started with. No probe starts without time
        left, and a probe is given up on when the deadline reaches it.
        """
        deadline = self.tools.clock() + WAIT_S
        latest = start
        while True:
            left = deadline - self.tools.clock()
            if left <= 0:
                return latest
            report = self.tools.bounded(self.recheck, left)
            if report is None:
                return latest
            latest = self._pick(report, start.check)
            left = deadline - self.tools.clock()
            if latest.reason != start.reason or left <= 0:
                return latest
            self.tools.sleep(min(POLL_S, left))

    def settle(self, name: str, actions: Mapping[str, Action]) -> prereqs.Result:
        """Apply the action for the check's reason, once per reason, until it passes or no action
        applies. An action returns the check as it stands afterwards, or None when nothing changed,
        so a reason that changes during a wait is handled at once."""
        handled: set[str] = set()
        result = self.check(name)
        while result.reason in actions and result.reason not in handled:
            handled.add(result.reason)
            after = actions[result.reason](result)
            if after is None:
                break
            result = after
        return result

    # -- Tailscale ---------------------------------------------------------------------------

    def _open(self, argv: Sequence[str], opened: str) -> bool:
        if self.tools.opener(argv) != 0:
            self.say(f"Could not open {opened}; open it yourself, then re-run setup.")
            return False
        self.effects.opened.append(opened)
        self.ctx.committed.append(f"opened {opened}")
        return True

    def _guided_open(
        self,
        result: prereqs.Result,
        question: str,
        argv: Sequence[str],
        opened: str,
        instruction: str,
    ) -> prereqs.Result | None:
        if not self.ask(question) or not self._open(argv, opened):
            return None
        self.say(f"{instruction} Setup continues when Tailscale is ready (Ctrl-C to stop).")
        return self.wait_for_change(result)

    def tailscale_missing(self, result: prereqs.Result) -> prereqs.Result | None:
        return self._guided_open(
            result,
            "Tailscale is not installed. Open its download page now? [Y/n] ",
            [TAILSCALE_DOWNLOAD],
            "Tailscale",
            "Install Tailscale, open it and sign in.",
        )

    def tailscale_stopped(self, result: prereqs.Result) -> prereqs.Result | None:
        return self._guided_open(
            result,
            "Tailscale is not running. Open it now? [Y/n] ",
            ["-a", "Tailscale"],
            "Tailscale",
            "Connect Tailscale.",
        )

    def tailscale_login(self, result: prereqs.Result) -> prereqs.Result | None:
        return self._guided_open(
            result,
            "Tailscale is not signed in. Open it now? [Y/n] ",
            ["-a", "Tailscale"],
            "Tailscale",
            "Sign in to Tailscale.",
        )

    def tailscale_dns(self, result: prereqs.Result) -> prereqs.Result | None:
        return self._guided_open(
            result,
            "Tailscale needs MagicDNS and HTTPS certificates. Open the admin DNS page now? [Y/n] ",
            [TAILSCALE_DNS],
            "the Tailscale DNS page",
            "Turn on MagicDNS and HTTPS certificates for the tailnet.",
        )

    def tailscale_approval(self, result: prereqs.Result) -> prereqs.Result | None:
        self.say(result.guidance)
        return None

    # -- Herdr -------------------------------------------------------------------------------

    @property
    def destination(self) -> Path:
        return self.ctx.home.joinpath(*HERDR_BIN)

    def herdr_missing(self, _result: prereqs.Result) -> prereqs.Result | None:
        where = inspect_executable(self.destination)
        if where == "unsafe":
            self.say(
                "Herdr: ~/.local/bin/herdr or a folder above it has unsafe ownership or "
                "permissions. Setup will not touch it and started no installer. Fix or remove "
                "it, then re-run setup."
            )
            return None
        if where == "safe":
            self.say(
                "Herdr is at ~/.local/bin/herdr but setup cannot use it from PATH. Add "
                "~/.local/bin to PATH, then re-run setup."
            )
            return None
        if not self.ask("Herdr is not installed. Install it now? [Y/n] "):
            self.say(f"Herdr: not installed. To install it yourself: {HERDR_MANUAL}")
            return None
        return self.check("herdr") if self._install_herdr() else None

    def _install_herdr(self) -> bool:
        try:
            body = self.tools.download(installer.HERDR_INSTALLER_URL)
        except webrelease.ReleaseError as exc:
            self.say(f"Herdr: not installed ({exc.reason}: the installer download failed).")
            self.say(f"To install it yourself: {HERDR_MANUAL}")
            return False
        try:
            terminal = self.tools.open_terminal()
        except OSError:
            self.say("Herdr: installing needs a terminal. Nothing was changed.")
            return False
        scratch: Path | None = None
        try:
            scratch = Path(tempfile.mkdtemp(prefix="relaydeck-installer-"))
            return self._run_installer(body, scratch, terminal)
        except OSError:
            self.say("Herdr: the installer could not be started. Nothing was changed.")
            self.say(f"To install it yourself: {HERDR_MANUAL}")
            return False
        finally:
            os.close(terminal)
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)

    def _partly_installed(self) -> None:
        self.effects.partial.append("Herdr")
        self.ctx.committed.append("Herdr install (may be partly installed)")

    def _run_installer(self, body: bytes, scratch: Path, terminal: int) -> bool:
        script = scratch / "install.sh"
        try:
            script.write_bytes(body)
            script.chmod(0o600)
            env = installer.installer_environment(self.ctx.home, scratch, self.ctx.environ)
            self.say("Installing Herdr with its own installer; its output follows on the terminal.")
            ran = self.tools.run_script(script, env, terminal)
        except OSError:
            self.say("Herdr: the installer could not be started. Nothing was changed.")
            self.say(f"To install it yourself: {HERDR_MANUAL}")
            return False
        except KeyboardInterrupt:
            self._partly_installed()
            self.say("Herdr install was interrupted; it may be partly installed.")
            raise
        if ran.code is None:
            self._partly_installed()
            self.say("Herdr install was interrupted; it may be partly installed.")
            self.say(f"To install it yourself: {HERDR_MANUAL}")
            return False
        if ran.code != 0:
            self._partly_installed()
            self.say(
                f"Herdr install failed (exit {ran.code}). To install it yourself: {HERDR_MANUAL}"
            )
            return False
        after = inspect_executable(self.destination)
        if after != "safe":
            self._partly_installed()
            if after == "unsafe":
                self.say(
                    "Herdr: the installed file has unsafe ownership or permissions, so setup will "
                    "not use it. Fix or remove ~/.local/bin/herdr, then re-run setup."
                )
            else:
                self.say("Herdr: the installer finished but ~/.local/bin/herdr is not there.")
            return False
        self.effects.installed.append("Herdr")
        self.ctx.committed.append("Herdr installed")
        return True

    def herdr_down(self, result: prereqs.Result) -> prereqs.Result | None:
        if prereqs.probe_socket(None) == self._socket():
            where = "Open Herdr: run `herdr` in another terminal window."
        else:
            where = (
                "Start Herdr so it listens on the socket set in your config "
                "(herdr_socket); setup cannot start it for you."
            )
        self.say(
            f"Herdr's server is not answering on its socket. {where} Setup continues when it "
            "answers (Ctrl-C to stop). After a restart or login, open Herdr again."
        )
        return self.wait_for_change(result)

    def herdr_protocol(self, result: prereqs.Result) -> prereqs.Result | None:
        self.say(
            "Herdr answers, but with a protocol this RelayDeck does not speak. Run `herdr update`, "
            "then re-run setup."
        )
        return None

    def _socket(self) -> str:
        config = self.ctx.config
        return prereqs.probe_socket(config.herdr_socket if config else None)


def remediate(ctx: RemedyContext, report: prereqs.Report, recheck: Recheck) -> Remedied:
    """Offer each applicable remedy, then return the preflight as it stands afterwards."""
    effects = Effects()
    remedy = _Remedy(ctx, ctx.remediation, recheck, effects)
    failed = {item.check: item.reason for item in report.results if item.reason}
    if "tailscale" in failed:
        remedy.settle(
            "tailscale",
            {
                "tailscale_missing": remedy.tailscale_missing,
                "tailscale_stopped": remedy.tailscale_stopped,
                "tailscale_login": remedy.tailscale_login,
                "tailscale_approval": remedy.tailscale_approval,
                "tailscale_dns": remedy.tailscale_dns,
            },
        )
    if "herdr" in failed:
        remedy.settle(
            "herdr",
            {
                "herdr_missing": remedy.herdr_missing,
                "herdr_down": remedy.herdr_down,
                "herdr_protocol": remedy.herdr_protocol,
            },
        )
    return Remedied(recheck(), effects)


def pending_actions(report: prereqs.Report) -> tuple[str, ...]:
    """What an interactive setup would offer for this report: lines for the no-terminal path."""
    notes = {
        "herdr_missing": "Install Herdr with its official installer (asked first).",
        "herdr_down": "Wait for you to open Herdr; setup never starts it.",
        "tailscale_missing": "Open the Tailscale download page (asked first).",
        "tailscale_stopped": "Open Tailscale so you can connect it (asked first).",
        "tailscale_login": "Open Tailscale so you can sign in (asked first).",
        "tailscale_dns": "Open the Tailscale DNS admin page (asked first).",
    }
    return tuple(notes[item.reason] for item in report.results if item.reason in notes)


__all__ = ["Effects", "Remedied", "Tools", "pending_actions", "real_tools", "remediate"]
