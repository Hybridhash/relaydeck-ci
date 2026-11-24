"""Build shell-safe manual update commands for the signed release version without running them.
Chain install, fetch and restart so failure stops the sequence.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from pathlib import Path

from relaydeck.companion.adapters import launchd as service
from relaydeck.companion.config import DEFAULT_CONFIG
from relaydeck.releases import records as release_records

REPOSITORY = "https://github.com/Hybridhash/relaydeck"


def wheel_url(version: str) -> str:
    """The release wheel for `version`: the asset `install.sh` names in the same release."""
    return f"{REPOSITORY}/releases/download/v{version}/relaydeck-{version}-py3-none-any.whl"


MANUAL_NOTE = (
    "Manual update: if it fails, run the same commands again; there is no automatic rollback yet."
)

OLDER_SERVICE_NOTE = (
    "Your login service uses the older start command; run relaydeck {profile}setup once to "
    "replace it."
)


def say(text: str) -> str:
    """A command that prints `text`: one quoted argument, so no character in it can end the line."""
    return f"printf '%s\\n' {shlex.quote(text)}"


def printable(*values: str) -> bool:
    """False for a newline, a tab, an escape or any other character that does not print."""
    return all(char.isprintable() for value in values for char in value)


def quote_path(path: Path, home: Path) -> str:
    """A shell-safe path; one under home is written as "$HOME"/rest, since a tilde would not
    expand inside quotes."""
    try:
        rest = path.relative_to(home)
    except ValueError:
        return shlex.quote(str(path))
    return '"$HOME"/' + shlex.quote(str(rest))


def config_args(config_path: Path | None, home: Path) -> str:
    """`--config <path> ` for a profile other than the default, else nothing. It is a root option,
    so it goes before the subcommand."""
    if config_path is None or config_path == Path(DEFAULT_CONFIG).expanduser().absolute():
        return ""
    return f"--config {quote_path(config_path, home)} "


def render(version: str, profile: str, restart: str | None) -> list[str]:
    """`restart` is the chained restart command, or None to stop and start by hand."""
    lines = [
        "uv tool install --managed-python --python 3.13 --force \\",
        f'  "relaydeck[companion] @ {wheel_url(version)}" \\',
        f"  && relaydeck {profile}web fetch --version {version} \\",
        f"  && {restart or f'relaydeck {profile}stop'}",
    ]
    if restart is None:
        start = f"relaydeck {profile}start --connect-herdr"
        lines.append(say(f"Then start RelayDeck again in its terminal: {start}"))
    lines.append(say(MANUAL_NOTE))
    return lines


def instructions(
    record: release_records.Record,
    *,
    config_path: Path,
    home: Path,
    uid: int,
    executable: Path,
    runner: service.Runner = service.quiet_runner,
    agent_state: Callable[..., str] = service.agent_state,
    older_form_loaded: Callable[..., bool] = service.older_form_loaded,
) -> list[str]:
    """The lines for this install, or none when an input cannot be shown safely. The login service
    is restarted in place only when it is installed, loaded, and runs this executable with this
    profile, in the current form or the 0.2.x one."""
    custom = None if config_path == Path(DEFAULT_CONFIG).expanduser().absolute() else config_path
    profile = config_args(config_path, home)
    if not printable(str(config_path), profile, record.commit, record.version, str(uid)):
        return []
    restart: str | None = None
    older = False
    try:
        state = agent_state(home, uid, executable, custom, runner)
        older = state == "replace" and older_form_loaded(home, uid, executable, custom, runner)
        if state == "done" or older:
            restart = f"launchctl kickstart -k gui/{uid}/{service.LABEL}"
    except Exception:  # noqa: BLE001 - an unreadable agent only means a manual restart
        restart = None
        older = False
    lines = render(record.version, profile, restart)
    if older:
        lines.insert(-1, say(OLDER_SERVICE_NOTE.format(profile=profile)))
    return lines
