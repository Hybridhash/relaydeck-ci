"""The update commands: the signed version's release wheel (no git), chained, profile-safe,
restarting the login service only when it is this install's, and equal to the README's."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from relaydeck.cli import build_parser
from relaydeck.companion import cli as companion_cli
from relaydeck.companion.adapters import launchd as service
from relaydeck.companion.updates.instructions import instructions, render, wheel_url
from relaydeck.releases import records as release_records
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)
from tests.support.paths import TESTS_DIR
from tests.support.updates.signing import COMMIT

RECORD = release_records.Record("0.3.1", COMMIT, "2026-12-01", ("A note.",))
EXECUTABLE = Path("/synthetic/bin/relaydeck")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path.resolve() / "home"
    path.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(path))
    return path


def lines_for(home: Path, config: Path, state: str = "install", uid: int = 501) -> list[str]:
    return instructions(
        RECORD,
        config_path=config,
        home=home,
        uid=uid,
        executable=EXECUTABLE,
        agent_state=lambda *args: state,
    )


def commands(lines: list[str], home: Path) -> list[list[str]]:
    """The chained commands, with continuation lines joined and "$HOME" filled in."""
    code = "\n".join(line for line in lines if not line.startswith("printf ")).replace("\\\n", " ")
    tokens = [token.replace("$HOME", str(home)) for token in shlex.split(code)]
    found: list[list[str]] = [[]]
    for token in tokens:
        if token == "&&":
            found.append([])
        else:
            found[-1].append(token)
    return found


def test_the_default_profile_installs_the_signed_versions_wheel_and_is_chained(home: Path) -> None:
    default = home / ".config" / "relaydeck" / "companion.toml"
    lines = lines_for(home, default)
    text = "\n".join(lines)
    assert wheel_url("0.3.1") in text and "git+" not in text and "#egg" not in text
    assert "--config" not in text
    steps = commands(lines, home)
    assert [step[0] for step in steps] == ["uv", "relaydeck", "relaydeck"]
    assert steps[1] == ["relaydeck", "web", "fetch", "--version", "0.3.1"]
    assert steps[2] == ["relaydeck", "stop"]
    assert text.count("&&") == 2  # nothing restarts after a failed install or fetch


def test_the_foreground_install_ends_with_a_note_to_start_it_again_by_hand(home: Path) -> None:
    lines = lines_for(home, home / ".config" / "relaydeck" / "companion.toml")
    assert lines[-2] == (
        "printf '%s\\n' 'Then start RelayDeck again in its terminal: "
        "relaydeck start --connect-herdr'"
    )
    assert lines[-1].startswith("printf '%s\\n' 'Manual update:")
    assert "no automatic rollback" in lines[-1]
    assert all("launchctl" not in line for line in lines)
    assert not any(line.lstrip().startswith("#") for line in lines)  # no prose comments


@pytest.mark.parametrize(
    "name",
    [
        "Library/App Support/companion.toml",
        'work/it\'s "mine"/c.toml',
        "x/$(touch hacked)/c;rm -rf y.toml",
        "x/`id`/c&d|e>f<g.toml",
    ],
)
def test_every_line_for_a_custom_profile_parses_back_to_that_profile(home: Path, name: str) -> None:
    config = home / name
    lines = lines_for(home, config)
    steps = commands(lines, home)
    parser = build_parser()
    for step in steps[1:]:
        args = parser.parse_args(step[1:])
        assert args.config == str(config)
    note = next(line for line in lines if "Then start" in line)
    message = shlex.split(note)[2]  # the one quoted argument of printf
    start = [token.replace("$HOME", str(home)) for token in shlex.split(message.split(": ", 1)[1])]
    # start is delegated to the companion's own parser, which owns --connect-herdr
    started = companion_cli.build_parser().parse_args(start[1:])
    assert started.config == str(config) and started.command == "start"
    assert started.connect_herdr is True
    assert "--config" in "\n".join(lines) and '"$HOME"/' in "\n".join(lines)


def test_a_profile_outside_home_is_quoted_without_home(home: Path, tmp_path: Path) -> None:
    outside = tmp_path.resolve() / "other place" / "c.toml"
    lines = lines_for(home, outside)
    steps = commands(lines, home)
    assert build_parser().parse_args(steps[1][1:]).config == str(outside)
    assert "$HOME" not in "\n".join(lines)


def test_a_matching_login_service_is_restarted_in_place(home: Path) -> None:
    lines = lines_for(home, home / ".config" / "relaydeck" / "companion.toml", state="done")
    assert lines[3] == "  && launchctl kickstart -k gui/501/dev.relaydeck.companion"
    assert all("relaydeck stop" not in line and "Then start" not in line for line in lines)
    assert lines[-1].startswith("printf '%s\\n' 'Manual update:")


@pytest.mark.parametrize("state", ["install", "replace", "load"])
def test_a_missing_unrelated_or_unloaded_service_is_not_trusted(home: Path, state: str) -> None:
    lines = lines_for(home, home / ".config" / "relaydeck" / "companion.toml", state=state)
    assert "launchctl" not in "\n".join(lines) and "relaydeck stop" in "\n".join(lines)


def older_service(
    monkeypatch: pytest.MonkeyPatch, home: Path, arguments: list[str], loaded: bool = True
) -> service.Runner:
    """An installed 0.2.x login service with `arguments` after the executable."""
    monkeypatch.setattr(
        service,
        "status",
        lambda _home: {
            "installed": True,
            "readable": True,
            "program": str(EXECUTABLE),
            "arguments": arguments,
        },
    )
    return lambda args: 0 if loaded else 113


def test_a_loaded_older_login_service_is_restarted_in_place(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    runner = older_service(monkeypatch, home, ["start", "--connect-herdr"])
    default = home / ".config" / "relaydeck" / "companion.toml"
    assert service.agent_state(home, 501, EXECUTABLE, None, runner) == "replace"
    lines = instructions(
        RECORD, config_path=default, home=home, uid=501, executable=EXECUTABLE, runner=runner
    )
    assert lines[3] == "  && launchctl kickstart -k gui/501/dev.relaydeck.companion"
    assert all("relaydeck stop" not in line and "Then start" not in line for line in lines)
    assert any("relaydeck setup once" in line for line in lines)
    assert lines[-1].startswith("printf '%s\\n' 'Manual update:")


@pytest.mark.parametrize(
    ("arguments", "loaded"),
    [
        (["start", "--connect-herdr"], False),
        (["--config", "/elsewhere.toml", "start", "--connect-herdr"], True),
        (["start"], True),
    ],
)
def test_an_older_service_that_is_unloaded_or_another_profile_is_not_trusted(
    monkeypatch: pytest.MonkeyPatch, home: Path, arguments: list[str], loaded: bool
) -> None:
    runner = older_service(monkeypatch, home, arguments, loaded)
    default = home / ".config" / "relaydeck" / "companion.toml"
    text = "\n".join(
        instructions(
            RECORD, config_path=default, home=home, uid=501, executable=EXECUTABLE, runner=runner
        )
    )
    assert "launchctl" not in text and "relaydeck stop" in text and "setup once" not in text


def test_an_unreadable_service_is_a_manual_restart(home: Path) -> None:
    def broken(*args: object) -> str:
        raise OSError("synthetic")

    lines = instructions(
        RECORD,
        config_path=home / "c.toml",
        home=home,
        uid=501,
        executable=EXECUTABLE,
        agent_state=broken,
    )
    assert "launchctl" not in "\n".join(lines)


def test_the_real_agent_check_matches_only_this_executable_and_profile(home: Path) -> None:
    home.chmod(0o700)
    calls: list[list[str]] = []

    def runner(args: object) -> int:
        calls.append(list(args))  # type: ignore[call-overload]
        return 0

    default = home / ".config" / "relaydeck" / "companion.toml"
    installed = service.install(
        executable=EXECUTABLE, home=home, uid=501, yes=True, load=False, config=None, runner=runner
    )
    assert installed.exit_code == 0
    same = instructions(
        RECORD, config_path=default, home=home, uid=501, executable=EXECUTABLE, runner=runner
    )
    other_exe = instructions(
        RECORD,
        config_path=default,
        home=home,
        uid=501,
        executable=Path("/elsewhere/relaydeck"),
        runner=runner,
    )
    other_profile = instructions(
        RECORD,
        config_path=home / "second.toml",
        home=home,
        uid=501,
        executable=EXECUTABLE,
        runner=runner,
    )
    assert any("kickstart" in line for line in same)
    assert not any("kickstart" in line for line in other_exe + other_profile)


def test_the_readme_shows_the_same_lines() -> None:
    readme = (TESTS_DIR.parent / "docs/releasing/README.md").read_text(encoding="utf-8")
    section = readme.split("## Updating", 1)[1].split("</details>", 1)[0]
    block = section.split("```sh\n", 1)[1].split("\n```", 1)[0]
    assert block.splitlines() == render("<version>", "", None)


def test_the_wheel_url_is_in_the_release_the_installer_downloads_from() -> None:
    template = (TESTS_DIR.parent / "install.sh.in").read_text(encoding="utf-8")
    base = wheel_url("@VERSION@").rsplit("/", 1)[0].replace("@VERSION@", "$VERSION")
    assert f'RELEASE_URL="{base}"' in template


def test_no_update_line_needs_git() -> None:
    text = "\n".join(render("0.3.1", "", None))
    assert "git" not in text.replace("github.com", "")


# -- the text is pasted into a shell: run it against stub commands ---------------------------------

BASH = "/bin/bash"


def run_in_shell(
    lines: list[str], home: Path, *, uv_fails: bool = False
) -> tuple[list[list[str]], str]:
    """Run the lines with uv, relaydeck and launchctl replaced by functions that log their exact
    arguments. Returns (the calls, what was printed). Nothing real can run."""
    log = home / "calls.log"
    prelude = (
        f"record() {{ printf '%s\\0' \"$@\" >> {shlex.quote(str(log))}; printf '\\n' >> "
        f"{shlex.quote(str(log))}; }}\n"
        f'uv() {{ record uv "$@"; return {1 if uv_fails else 0}; }}\n'
        'relaydeck() { record relaydeck "$@"; }\n'
        'launchctl() { record launchctl "$@"; }\n'
    )
    done = subprocess.run(  # noqa: S603 - a fixed shell and stub functions only
        [BASH, "-c", prelude + "\n".join(lines)],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        cwd=home,
        timeout=20,
        check=False,
    )
    calls: list[list[str]] = []
    if log.exists():
        for row in log.read_bytes().split(b"\n"):
            if row:
                calls.append([part.decode() for part in row.split(b"\0") if part])
    return calls, done.stdout


def test_run_for_real_the_default_profile_calls_exactly_the_three_commands(home: Path) -> None:
    lines = lines_for(home, home / ".config" / "relaydeck" / "companion.toml")
    calls, shown = run_in_shell(lines, home)
    assert [call[0] for call in calls] == ["uv", "relaydeck", "relaydeck"]
    assert calls[0][1:5] == ["tool", "install", "--managed-python", "--python"]
    assert calls[0][-1] == f"relaydeck[companion] @ {wheel_url('0.3.1')}"
    assert calls[1] == ["relaydeck", "web", "fetch", "--version", "0.3.1"]
    assert calls[2] == ["relaydeck", "stop"]
    assert "Then start RelayDeck again in its terminal: relaydeck start --connect-herdr" in shown
    assert "no automatic rollback" in shown


def test_run_for_real_a_failed_install_stops_the_chain_but_still_prints_the_notes(
    home: Path,
) -> None:
    lines = lines_for(home, home / ".config" / "relaydeck" / "companion.toml")
    calls, shown = run_in_shell(lines, home, uv_fails=True)
    assert [call[0] for call in calls] == ["uv"]  # no fetch, no stop
    assert "no automatic rollback" in shown


def test_run_for_real_the_service_is_kicked_only_at_the_end_of_the_chain(home: Path) -> None:
    lines = lines_for(home, home / ".config" / "relaydeck" / "companion.toml", state="done")
    calls, _shown = run_in_shell(lines, home)
    assert [call[0] for call in calls] == ["uv", "relaydeck", "launchctl"]
    assert calls[2] == ["launchctl", "kickstart", "-k", "gui/501/dev.relaydeck.companion"]
    assert (
        run_in_shell(lines, home / "x", uv_fails=True)[0] == [] if (home / "x").is_dir() else True
    )


@pytest.mark.parametrize(
    "name",
    [
        "Library/App Support/companion.toml",
        'work/it\'s "mine"/c.toml',
        "x/$(touch hacked)/c;rm -rf y.toml",
        "x/`touch hacked`/c&d|e>f<g.toml",
        "x/$HOME/\\n-literal.toml",
    ],
)
def test_run_for_real_a_hostile_profile_reaches_the_commands_as_one_argument(
    home: Path, name: str
) -> None:
    config = home / name
    calls, shown = run_in_shell(lines_for(home, config), home)
    assert calls[1] == ["relaydeck", "--config", str(config), "web", "fetch", "--version", "0.3.1"]
    assert calls[2] == ["relaydeck", "--config", str(config), "stop"]
    assert not (home / "hacked").exists() and not list(home.glob("*hacked*"))


@pytest.mark.parametrize(
    "name",
    [
        "name\nprintf COMMENT_EXECUTED_MARKER\n# .toml",
        "tab\tname.toml",
        "escape\x1bname.toml",
        "carriage\rreturn.toml",
        "bidi‮name.toml",
        "zero​width.toml",
        "line separator.toml",
        "nul\x00name.toml",
    ],
)
def test_a_profile_with_a_line_break_or_control_character_gets_no_commands_at_all(
    home: Path, name: str
) -> None:
    assert lines_for(home, home / "profiles" / name) == []


def test_ordinary_unicode_and_spaces_in_a_profile_are_fine(home: Path) -> None:
    lines = lines_for(home, home / "Profiles é" / "mon profil.toml")
    assert lines and run_in_shell(lines, home)[0][1][2] == str(
        home / "Profiles é" / "mon profil.toml"
    )


def test_an_unsafe_version_or_commit_never_reaches_the_text(home: Path) -> None:
    for record in (
        release_records.Record("0.3.1\n", COMMIT, "2026-12-01", ()),
        release_records.Record("0.3.1", COMMIT + "\necho", "2026-12-01", ()),
        release_records.Record("0.3.1", COMMIT + "\x1b", "2026-12-01", ()),
    ):
        lines = instructions(
            record,
            config_path=home / "c.toml",
            home=home,
            uid=501,
            executable=EXECUTABLE,
            agent_state=lambda *args: "install",
        )
        assert lines == []


# -- the launchd question is silent and bounded ----------------------------------------------------


def test_the_quiet_runner_discards_both_streams_and_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(args: list[str], **kwargs: Any) -> SimpleNamespace:
        seen.update(kwargs, args=args)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(service, "trusted_executable", lambda path: path)
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert service.quiet_runner(["launchctl", "print", "gui/501/x"]) == 0
    assert seen["stdout"] is subprocess.DEVNULL and seen["stderr"] is subprocess.DEVNULL
    assert seen["stdin"] is subprocess.DEVNULL
    assert 0 < seen["timeout"] <= 5 and seen["args"] == ["/bin/launchctl", "print", "gui/501/x"]
    with pytest.raises(OSError):
        service.quiet_runner(["rm", "-rf", "/"])


def test_the_instruction_path_uses_the_quiet_runner_by_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], home: Path
) -> None:
    profile = home / "custom.toml"
    monkeypatch.setattr(
        service,
        "status",
        lambda _home: {
            "installed": True,
            "readable": True,
            "program": str(EXECUTABLE),
            "arguments": [
                "--config",
                str(profile),
                "service-run",
                "--log-directory",
                str(home / "Library/Logs/RelayDeck"),
            ],
        },
    )
    monkeypatch.setattr(service, "trusted_executable", lambda path: path)

    def fake_run(args: list[str], **kwargs: Any) -> SimpleNamespace:
        if kwargs.get("stdout") is None:
            print("PRIVATE LAUNCHD DESCRIPTION")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    lines = instructions(RECORD, config_path=profile, home=home, uid=501, executable=EXECUTABLE)
    assert any("kickstart" in line for line in lines)
    assert capsys.readouterr().out == ""
