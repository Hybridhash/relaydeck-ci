"""Plan or apply a per-user launchd service and return result lines for its CLI caller. Change
nothing without confirmation and run launchctl only when explicitly requested.
"""

from __future__ import annotations

import fcntl
import os
import plistlib
import secrets
import stat
import subprocess
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from relaydeck.companion.adapters.service_output import program_arguments as program_arguments
from relaydeck.host.executables import probe_environment, tool_path, trusted_executable

LABEL = "dev.relaydeck.companion"
THROTTLE_INTERVAL_S = 30
QUIET_TIMEOUT_S = 5
Runner = Callable[[Sequence[str]], int]
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_LOCK = f".{LABEL}.lock"


class _CommittedError(OSError):
    """Replacement succeeded but durability could not be confirmed."""


class _LogDirectoryError(OSError):
    """The log directory is readable or writable by others, which the log supervisor refuses."""


def _check_directory(info: os.stat_result, *, ancestor: bool = False) -> None:
    owner_ok = info.st_uid in (0, os.getuid()) if ancestor else info.st_uid == os.getuid()
    writable = info.st_mode & 0o022
    if not stat.S_ISDIR(info.st_mode) or not owner_ok:
        raise OSError("Unsafe directory owner or type")
    if writable and not (ancestor and info.st_mode & stat.S_ISVTX):
        raise OSError("Unsafe directory permissions")


@contextmanager
def _home_directory(home: Path) -> Iterator[int]:
    if not home.is_absolute() or ".." in home.parts:
        raise OSError("An absolute home directory is required")
    fd = os.open(home.anchor, _DIRECTORY)
    try:
        _check_directory(os.fstat(fd), ancestor=True)
        for index, part in enumerate(home.parts[1:], 1):
            child = os.open(part, _DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
            _check_directory(os.fstat(fd), ancestor=index < len(home.parts) - 1)
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _directory(parent: int, name: str, *, create: bool, private: bool = False) -> Iterator[int]:
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
    fd = os.open(name, _DIRECTORY, dir_fd=parent)
    try:
        info = os.fstat(fd)
        _check_directory(info)
        if private and info.st_mode & 0o077:
            raise _LogDirectoryError("Log directory is not private")
        yield fd
    finally:
        os.close(fd)


def _check_file(info: os.stat_result) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or info.st_nlink != 1
    ):
        raise OSError("Unsafe file owner, type or permissions")


def _entry(directory: int, name: str) -> os.stat_result | None:
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None
    _check_file(info)
    return info


@contextmanager
def _locked(directory: int) -> Iterator[int]:
    fd = os.open(_LOCK, os.O_RDWR | os.O_CREAT | _FILE, 0o600, dir_fd=directory)
    try:
        _check_file(os.fstat(fd))
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd
    finally:
        os.close(fd)


def _identity(info: os.stat_result | None) -> tuple[int, ...] | None:
    return (
        (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if info
        else None
    )


def _ensure_log(directory: int, name: str) -> None:
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE, 0o600, dir_fd=directory)
    except FileExistsError:
        _entry(directory, name)
        return
    try:
        _check_file(os.fstat(fd))
    finally:
        os.close(fd)


def _write_agent(home: Path, body: bytes) -> None:
    with _home_directory(home) as home_fd, _directory(home_fd, "Library", create=True) as library:
        with _directory(library, "Logs", create=True) as logs:
            with _directory(logs, "RelayDeck", create=True, private=True) as output:
                for name in ("companion.out.log", "companion.err.log"):
                    for suffix in (".1", ".2", ".3", ".lock", ".trim"):
                        _entry(output, name + suffix)
                    _ensure_log(output, name)
        with (
            _directory(library, "LaunchAgents", create=True) as directory,
            _locked(directory) as lock,
        ):
            name = f"{LABEL}.plist"
            expected = _identity(_entry(directory, name))
            temporary = f".{name}.{secrets.token_hex(16)}.tmp"
            fd = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE, 0o600, dir_fd=directory
            )
            owned = os.fstat(fd)
            try:
                _check_file(owned)
                with os.fdopen(fd, "wb") as handle:
                    fd = -1
                    handle.write(body)
                    handle.flush()
                    os.fsync(handle.fileno())
                if _identity(_entry(directory, name)) != expected:
                    raise OSError("Agent changed during installation")
                if _identity(_entry(directory, _LOCK)) != _identity(os.fstat(lock)):
                    raise OSError("Lock changed during installation")
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                try:
                    os.fsync(directory)
                except OSError as exc:
                    raise _CommittedError("Agent written; directory sync failed") from exc
            finally:
                if fd >= 0:
                    os.close(fd)
                try:
                    info = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
                    if (info.st_dev, info.st_ino) == (owned.st_dev, owned.st_ino):
                        os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass


def agent_path(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_dir(home: Path) -> Path:
    return home / "Library" / "Logs" / "RelayDeck"


def service_arguments(executable: Path, home: Path, config: Path | None = None) -> list[str]:
    chosen = ["--config", str(config)] if config is not None else []
    return [str(executable), *chosen, "service-run", "--log-directory", str(log_dir(home))]


def older_arguments(executable: Path, config: Path | None = None) -> list[str]:
    """The command a login service set up by RelayDeck 0.2.x runs. It still starts this install;
    `relaydeck setup` offers to replace it."""
    chosen = ["--config", str(config)] if config is not None else []
    return [str(executable), *chosen, "start", "--connect-herdr"]


def plist_document(
    executable: Path, *, home: Path, path_env: str, config: Path | None = None
) -> dict[str, object]:
    return {
        "Label": LABEL,
        "ProgramArguments": service_arguments(executable, home, config),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": THROTTLE_INTERVAL_S,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
        "EnvironmentVariables": {"PATH": path_env},
    }


def plist_bytes(
    executable: Path, *, home: Path, path_env: str, config: Path | None = None
) -> bytes:
    return plistlib.dumps(
        plist_document(executable, home=home, path_env=path_env, config=config), sort_keys=True
    )


def bootstrap_command(uid: int, plist: Path) -> str:
    return f"launchctl bootstrap gui/{uid} {plist}"


def bootout_command(uid: int, plist: Path) -> str:
    return f"launchctl bootout gui/{uid} {plist}"


def default_runner(args: Sequence[str]) -> int:
    if not args or args[0] != "launchctl":
        raise OSError("Unsupported service command")
    binary = trusted_executable("/bin/launchctl")
    if binary is None:
        raise FileNotFoundError("No trusted launchctl is available")
    return subprocess.run(  # noqa: S603 - fixed launchctl, argument list, no shell
        [binary, *args[1:]], check=False, timeout=30, env=probe_environment()
    ).returncode


def quiet_runner(args: Sequence[str]) -> int:
    """launchctl for a read-only question: both streams discarded (launchd describes the whole
    service, paths and environment included) and a short bound."""
    if not args or args[0] != "launchctl":
        raise OSError("Unsupported service command")
    binary = trusted_executable("/bin/launchctl")
    if binary is None:
        raise FileNotFoundError("No trusted launchctl is available")
    return subprocess.run(  # noqa: S603 - fixed launchctl, argument list, no shell
        [binary, *args[1:]],
        check=False,
        timeout=QUIET_TIMEOUT_S,
        env=probe_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode


@dataclass(frozen=True)
class Outcome:
    lines: tuple[str, ...]
    changed: bool
    exit_code: int


def install(
    *,
    executable: Path,
    home: Path,
    uid: int,
    yes: bool,
    load: bool,
    path_env: str | None = None,
    runner: Runner = default_runner,
    config: Path | None = None,
) -> Outcome:
    plist = agent_path(home)
    environment = dict(os.environ)
    if path_env is not None:
        environment["PATH"] = path_env
    body = plist_bytes(executable, home=home, path_env=tool_path(environment, home), config=config)
    lines = [
        f"Agent: {plist}",
        f"Runs: {' '.join(service_arguments(executable, home, config))} "
        "(RunAtLoad, KeepAlive, throttle "
        f"{THROTTLE_INTERVAL_S}s)",
        f"Logs: {log_dir(home)}/companion.{{out,err}}.log (5 MiB + 3 archives each)",
        "Existing agents need this plist installed and reloaded to enable bounded logs.",
        "Environment: PATH only; no secrets; loopback bind unchanged; no inbound ports.",
        "Plist:",
        body.decode("utf-8"),
        f"Load command: {bootstrap_command(uid, plist)}",
    ]
    if not yes:
        return Outcome(
            tuple([*lines, "Not installed. Re-run with --yes to write the agent."]), False, 0
        )
    try:
        _write_agent(home, body)
    except _CommittedError:
        return Outcome(
            tuple([*lines, "Agent written, but directory sync failed; not loaded."]), True, 1
        )
    except _LogDirectoryError:
        return Outcome(
            tuple(
                [
                    *lines,
                    f"Refused: log directory {log_dir(home)} must be private (mode 700).",
                ]
            ),
            False,
            1,
        )
    except OSError:
        return Outcome(
            tuple([*lines, "Refused unsafe, busy or unavailable LaunchAgent paths."]), False, 1
        )
    lines.append(f"Written: {plist}")
    if not load:
        lines.append("Not loaded. Load it yourself with the command above (or re-run with --load).")
        return Outcome(tuple(lines), True, 0)
    try:
        code = runner(["launchctl", "bootstrap", f"gui/{uid}", str(plist)])
    except (OSError, subprocess.TimeoutExpired):
        code = 1
    lines.append("Loaded." if code == 0 else f"launchctl bootstrap failed (exit {code}).")
    return Outcome(tuple(lines), True, 0 if code == 0 else 1)


def uninstall(
    *, home: Path, uid: int, yes: bool, unload: bool, runner: Runner = default_runner
) -> Outcome:
    plist = agent_path(home)
    lines = [f"Agent: {plist}", f"Unload command: {bootout_command(uid, plist)}"]
    if not yes:
        return Outcome(
            tuple([*lines, "Nothing removed. Re-run with --yes to remove the agent."]), False, 0
        )
    removed = False
    try:
        with (
            _home_directory(home) as home_fd,
            _directory(home_fd, "Library", create=False) as library,
        ):
            with _directory(library, "LaunchAgents", create=False) as directory, _locked(directory):
                existing = _entry(directory, plist.name)
                if unload:
                    try:
                        code = runner(["launchctl", "bootout", f"gui/{uid}", str(plist)])
                    except (OSError, subprocess.TimeoutExpired):
                        code = 1
                    lines.append(
                        "Unloaded." if code == 0 else f"launchctl bootout failed (exit {code})."
                    )
                if _identity(_entry(directory, plist.name)) != _identity(existing):
                    raise OSError("Agent changed during removal")
                if existing is not None:
                    os.unlink(plist.name, dir_fd=directory)
                    removed = True
                    os.fsync(directory)
                    lines.append("Removed the agent file.")
                else:
                    lines.append("No agent file was installed.")
    except FileNotFoundError:
        lines.append("No agent file was installed.")
    except OSError:
        if removed:
            return Outcome(tuple([*lines, "Agent removed, but directory sync failed."]), True, 1)
        return Outcome(
            tuple([*lines, "Refused unsafe, busy or unavailable LaunchAgent paths."]), False, 1
        )
    if not unload:
        lines.append("Still loaded if it was running. Unload with the command above.")
    return Outcome(tuple(lines), True, 0)


def agent_state(
    home: Path, uid: int, executable: Path, config: Path | None, runner: Runner = default_runner
) -> str:
    """done | load | replace | install: the installed agent against what install would write.
    Reads the agent file and asks launchctl whether it is loaded; writes nothing."""
    info = status(home)
    if not info.get("installed"):
        return "install"
    arguments = info.get("arguments")
    have = [info.get("program"), *(arguments if isinstance(arguments, list) else [])]
    if not info.get("readable") or have != service_arguments(executable, home, config):
        return "replace"
    try:
        code = runner(["launchctl", "print", f"gui/{uid}/{LABEL}"])
    except (OSError, subprocess.TimeoutExpired):
        return "load"
    return "done" if code == 0 else "load"


def older_form_loaded(
    home: Path, uid: int, executable: Path, config: Path | None, runner: Runner = default_runner
) -> bool:
    """Whether the installed agent runs this install in the 0.2.x form and is loaded, so a
    kickstart restarts it in place. Writes nothing."""
    info = status(home)
    arguments = info.get("arguments")
    have = [info.get("program"), *(arguments if isinstance(arguments, list) else [])]
    if not info.get("installed") or not info.get("readable"):
        return False
    if have != older_arguments(executable, config):
        return False
    try:
        return runner(["launchctl", "print", f"gui/{uid}/{LABEL}"]) == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def status(home: Path) -> dict[str, object]:
    plist = agent_path(home)
    try:
        with (
            _home_directory(home) as home_fd,
            _directory(home_fd, "Library", create=False) as library,
        ):
            with _directory(library, "LaunchAgents", create=False) as directory:
                fd = os.open(plist.name, os.O_RDONLY | _FILE, dir_fd=directory)
                with os.fdopen(fd, "rb") as handle:
                    info = os.fstat(handle.fileno())
                    _check_file(info)
                    data = plistlib.loads(handle.read(1024 * 1024))
    except FileNotFoundError:
        return {"installed": False, "agent": str(plist)}
    except (OSError, ValueError):
        return {"installed": True, "agent": str(plist), "readable": False}
    args = data.get("ProgramArguments") if isinstance(data, dict) else None
    return {
        "installed": True,
        "agent": str(plist),
        "readable": True,
        "label": data.get("Label") if isinstance(data, dict) else None,
        "program": args[0] if isinstance(args, list) and args else None,
        "arguments": args[1:] if isinstance(args, list) else None,
        "mode": oct(info.st_mode & 0o777),
    }
