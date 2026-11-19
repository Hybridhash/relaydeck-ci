"""Read-only git execution: trusted binary, fixed argv, scrubbed environment, bounded output."""

from __future__ import annotations

import os
import re
import selectors
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from relaydeck.host.executables import probe_environment, trusted_executable

MIN_VERSION = (2, 45)
MAX_OUTPUT = 8 * 1024 * 1024
STATUS_TIMEOUT_S = 5.0
DIFF_TIMEOUT_S = 10.0
_CHUNK = 64 * 1024

SUBCOMMANDS = frozenset(
    {
        "cat-file",
        "config",
        "diff",
        "for-each-ref",
        "log",
        "merge-base",
        "rev-list",
        "rev-parse",
        "status",
    }
)
_GLOBAL = (
    "--no-pager",
    "--literal-pathspecs",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.quotePath=false",
    "-c",
    "diff.external=",
    "-c",
    "maintenance.auto=false",
    "-c",
    "gc.auto=0",
    "-c",
    "log.showSignature=false",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "core.attributesFile=/dev/null",
)
_GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_ALLOW_PROTOCOL": "",
    "GIT_ADVICE": "0",
    "LC_ALL": "C",
}
_VERSION = re.compile(rb"git version (\d+)\.(\d+)")


class GitUnavailable(RuntimeError):
    """No trusted, supported and usable git executable."""


class GitFailed(RuntimeError):
    """Non-zero exit, timeout or cancellation; the reason is a fixed code, never git's stderr."""


@dataclass(frozen=True, slots=True)
class GitResult:
    stdout: bytes
    truncated: bool


class GitRunner:
    """Runs allowlisted read-only git commands in a validated worktree folder."""

    def __init__(
        self, executable: str, *, max_processes: int = 2, environ: Mapping[str, str] | None = None
    ) -> None:
        self._git = executable
        self._env = {**probe_environment(environ), **_GIT_ENV}
        self._slots = threading.BoundedSemaphore(max_processes)

    @property
    def default_ignore_file(self) -> str | None:
        # XDG_CONFIG_HOME is excluded from the runner's environment. Explicit
        # core.excludesFile and includes are rejected by ConfigGuard.
        home = self._env.get("HOME")
        if home is None or not os.path.isabs(home):
            return None
        return os.path.join(home, ".config", "git", "ignore")

    @classmethod
    def create(cls, *, max_processes: int = 2) -> GitRunner:
        """Resolve a trusted git and check its version; raises GitUnavailable."""
        git = trusted_executable("git")
        if git is None:
            raise GitUnavailable("git_missing")
        runner = cls(git, max_processes=max_processes)
        try:
            version = runner.run(None, ("--version",), timeout_s=STATUS_TIMEOUT_S).stdout
        except GitFailed as exc:
            if str(exc) == "git_cancelled":
                raise
            reason = "git_timeout" if str(exc) == "git_timeout" else "git_execution_failed"
            raise GitUnavailable(reason) from exc
        except subprocess.TimeoutExpired as exc:
            raise GitUnavailable("git_timeout") from exc
        except OSError as exc:
            raise GitUnavailable("git_execution_failed") from exc
        match = _VERSION.match(version)
        if match is None or (int(match[1]), int(match[2])) < MIN_VERSION:
            raise GitUnavailable("git_too_old")
        return runner

    def run(
        self,
        cwd: str | None,
        args: Sequence[str],
        *,
        timeout_s: float,
        max_bytes: int = MAX_OUTPUT,
        cancel: threading.Event | None = None,
        ok_codes: frozenset[int] = frozenset({0}),
        attr_source: str | None = None,
    ) -> GitResult:
        """Run an allowlisted Git subcommand with capped stdout and a deadline including
        process-slot waiting. When attr_source is supplied, read attributes from that tree
        instead of the worktree.
        """
        argv = self._argv(args)
        env = self._env if attr_source is None else {**self._env, "GIT_ATTR_SOURCE": attr_source}
        deadline = time.monotonic() + timeout_s
        if not self._slots.acquire(timeout=timeout_s):
            raise GitFailed("git_busy")
        try:
            proc = subprocess.Popen(  # noqa: S603 - trusted absolute git, allowlisted argv, no shell
                argv,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
            try:
                out, truncated = _read_bounded(proc, deadline, max_bytes, cancel)
                code = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
            except BaseException:
                _kill_group(proc)
                raise
            finally:
                if proc.stdout is not None:
                    proc.stdout.close()
        finally:
            self._slots.release()
        if not truncated and code not in ok_codes:
            raise GitFailed("git_exit")
        return GitResult(out, truncated)

    def _argv(self, args: Sequence[str]) -> list[str]:
        if isinstance(args, str) or not args:
            raise ValueError("args_must_be_a_sequence")
        if any("\0" in arg for arg in args):
            raise ValueError("nul_in_argument")
        if args[0] == "--version" and len(args) == 1:
            return [self._git, "--version"]
        if args[0] not in SUBCOMMANDS:
            raise ValueError("subcommand_not_allowed")
        return [self._git, *_GLOBAL, *args]


def _read_bounded(
    proc: subprocess.Popen[bytes], deadline: float, max_bytes: int, cancel: threading.Event | None
) -> tuple[bytes, bool]:
    assert proc.stdout is not None  # noqa: S101
    chunks: list[bytes] = []
    size = 0
    with selectors.DefaultSelector() as sel:
        sel.register(proc.stdout, selectors.EVENT_READ)
        while True:
            if cancel is not None and cancel.is_set():
                raise GitFailed("git_cancelled")
            left = deadline - time.monotonic()
            if left <= 0:
                raise GitFailed("git_timeout")
            if not sel.select(timeout=min(left, 0.25)):
                continue
            chunk = os.read(proc.stdout.fileno(), _CHUNK)
            if not chunk:
                return b"".join(chunks), False
            take = chunk[: max_bytes - size]
            chunks.append(take)
            size += len(take)
            if size >= max_bytes:
                _kill_group(proc)
                return b"".join(chunks), True


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass  # an unkillable child must never block the caller
