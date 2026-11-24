"""Launchd stream owner: capture the companion from its first byte through shutdown."""

from __future__ import annotations

import argparse
import logging.config
import os
import selectors
import signal
import subprocess
import sys
import syslog
import threading
import time
from collections.abc import Sequence
from contextlib import ExitStack, suppress
from pathlib import Path
from types import FrameType, TracebackType

from relaydeck.host.files.descriptors import PrivateFileError
from relaydeck.host.observability.exceptions import exception_category, install_exception_safety
from relaydeck.host.observability.rotation import RotatingOutput

# Longer than the companion's own stop bounds and shorter than the 20 s launchd allows before it
# kills the supervisor itself.
STOP_SECONDS = 18.0
PARENT_GONE_STOP_SECONDS = 12.0
DRAIN_SECONDS = 1.0
CHUNK_BYTES = 16384


def _uncaught(
    kind: type[BaseException], value: BaseException | None, trace: TracebackType | None
) -> None:
    # Even an application import failure must not print paths, source lines or exception text.
    sys.stderr.write("service_failure category=unexpected\n")


def watch_parent() -> None:
    descriptor = os.environ.pop("RELAYDECK_SERVICE_PARENT_FD", None)
    if descriptor is None:
        return
    fd = int(descriptor)

    def wait() -> None:
        try:
            os.read(fd, 1)
        finally:
            with suppress(OSError):
                os.close(fd)
            os.kill(os.getpid(), signal.SIGTERM)
            # Graceful shutdown has the same bound even if its owner is gone.
            time.sleep(PARENT_GONE_STOP_SECONDS)
            os._exit(1)

    threading.Thread(target=wait, name="service-parent", daemon=True).start()


_CHILD_CONFIGURED = False


def configure_child() -> None:
    """Install content-free diagnostics before the service imports application code."""
    global _CHILD_CONFIGURED
    if os.environ.get("RELAYDECK_SERVICE_OUTPUT") != "1" or _CHILD_CONFIGURED:
        return
    _CHILD_CONFIGURED = True
    sys.excepthook = _uncaught
    threading.excepthook = lambda args: _uncaught(args.exc_type, args.exc_value, args.exc_traceback)
    watch_parent()
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(line_buffering=True)
    from relaydeck.host.observability.logging import logging_config

    install_exception_safety()
    logging.config.dictConfig(logging_config())


def program_arguments(executable: Path, config: Path | None = None) -> list[str]:
    """`--config` is added only for a profile other than the default one."""
    chosen = ["--config", str(config)] if config is not None else []
    return [str(executable), *chosen, "start", "--connect-herdr"]


def _report_unavailable(exc: BaseException) -> None:
    with suppress(Exception):
        syslog.syslog(
            syslog.LOG_ERR,
            f"relaydeck service_output_unavailable category={exception_category(exc)}",
        )


def supervise_output(
    command: Sequence[str],
    directory: Path,
    *,
    limit: int | None = None,
    watch_parent: bool = False,
) -> int:
    """Only the launcher thread writes files; pipe capacity bounds pending output."""
    with ExitStack() as cleanup:
        outputs = []
        for name in ("companion.out.log", "companion.err.log"):
            output = RotatingOutput(
                directory, name, **({"limit": limit} if limit is not None else {})
            )
            cleanup.callback(output.close)
            outputs.append(output)
        selector = cleanup.enter_context(selectors.DefaultSelector())
        wake_read, wake_write = os.pipe()
        for fd in (wake_read, wake_write):
            cleanup.callback(os.close, fd)
            os.set_blocking(fd, False)
        previous_wakeup = signal.set_wakeup_fd(wake_write, warn_on_full_buffer=False)
        cleanup.callback(signal.set_wakeup_fd, previous_wakeup)
        child: subprocess.Popen[bytes] | None = None
        deadline: float | None = None
        failure = False

        def interrupt(sig: int, frame: FrameType | None) -> None:
            nonlocal deadline
            if sig == signal.SIGUSR1:
                if child is not None and child.poll() is None:
                    child.send_signal(sig)
            elif sig != signal.SIGCHLD:
                if deadline is None:
                    deadline = time.monotonic() + STOP_SECONDS
                if child is not None and child.poll() is None:
                    child.send_signal(sig)

        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGCHLD, signal.SIGUSR1):
            old = signal.signal(sig, interrupt)
            cleanup.callback(signal.signal, sig, old)
        selector.register(wake_read, selectors.EVENT_READ, None)
        parent_reader = None
        parent_fds: tuple[int, ...] = ()
        environment = dict(os.environ)
        environment.pop("RELAYDECK_SERVICE_PARENT_FD", None)
        if watch_parent:
            read_fd, write_fd = os.pipe()
            parent_reader = cleanup.enter_context(os.fdopen(read_fd, "rb", buffering=0))
            cleanup.enter_context(os.fdopen(write_fd, "wb", buffering=0))
            parent_fds = (read_fd,)
            environment["RELAYDECK_SERVICE_PARENT_FD"] = str(read_fd)
            environment["RELAYDECK_SERVICE_OUTPUT"] = "1"
        child = subprocess.Popen(  # noqa: S603 - fixed executable/argv from the service plist
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=parent_fds,
            env=environment,
        )
        if parent_reader is not None:
            parent_reader.close()
        try:
            if deadline is not None:
                child.terminate()
            for pipe, output in zip((child.stdout, child.stderr), outputs, strict=True):
                assert pipe is not None  # noqa: S101
                cleanup.callback(pipe.close)
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, output)
            exited_at: float | None = None
            while True:
                now = time.monotonic()
                code = child.poll()
                if code is not None and exited_at is None:
                    exited_at = now
                if code is not None and (
                    len(selector.get_map()) == 1
                    or (exited_at is not None and now - exited_at >= DRAIN_SECONDS)
                ):
                    return 1 if failure else code if code >= 0 else 128 - code
                if deadline is not None and code is None and now >= deadline:
                    child.kill()
                    deadline = None
                waits = [
                    value - now
                    for value in (
                        deadline,
                        None if exited_at is None else exited_at + DRAIN_SECONDS,
                    )
                    if value is not None
                ]
                for key, _ in selector.select(max(0, min(waits)) if waits else None):
                    if key.data is None:
                        try:
                            os.read(wake_read, CHUNK_BYTES)
                        except BlockingIOError:
                            pass
                        continue
                    try:
                        chunk = os.read(key.fd, CHUNK_BYTES)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if not failure:
                        try:
                            key.data.write(chunk)
                        except OSError:
                            # Terminate instead of retaining an unlogged running service.
                            failure = True
                            if child.poll() is None:
                                child.terminate()
                            deadline = time.monotonic() + STOP_SECONDS
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()


def main(argv: Sequence[str], *, executable: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="relaydeck")
    parser.add_argument("--config", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    service = commands.add_parser("service-run")
    service.add_argument("--log-directory", required=True, type=Path)
    args = parser.parse_args(argv)
    installed = executable if executable is not None else Path(sys.argv[0]).absolute()
    if not installed.is_absolute():
        return 2
    try:
        return supervise_output(
            program_arguments(installed, args.config), args.log_directory, watch_parent=True
        )
    except (OSError, ValueError, PrivateFileError) as exc:
        _report_unavailable(exc)
        return 1
