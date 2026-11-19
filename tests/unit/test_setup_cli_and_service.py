"""The `setup`/`pair` command line, the login service arguments, the state lock and the
connector's strict key and credential reads."""

from __future__ import annotations

import os
import plistlib
from pathlib import Path
from typing import Any

import pytest

from relaydeck import cli
from relaydeck.companion.adapters import launchd as service
from relaydeck.companion.app import lifecycle
from relaydeck.companion.setup import SetupReport
from relaydeck.connector.enrollment import (
    ConnectorCreds,
    ConnectorKeys,
    EnrollmentError,
    generate_keys,
    load_creds,
    load_keys,
    save_creds,
    save_keys,
)
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)

# -- command line ------------------------------------------------------------------------------


def test_pair_without_a_terminal_exits_2_and_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(
        "relaydeck.companion.pairing.wizard.run_pair", lambda *a, **k: pytest.fail("must not run")
    )
    assert cli.main(["--config", str(tmp_path / "c.toml"), "pair"]) == 2
    assert "needs a terminal" in capsys.readouterr().out


def test_pair_with_an_unloadable_config_points_at_setup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert cli.main(["--config", str(tmp_path / "missing.toml"), "pair"]) == 2
    assert "relaydeck setup" in capsys.readouterr().out


def test_pair_passes_the_ttl_and_no_flag_can_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    parser = cli.build_parser()
    namespace = parser.parse_args(["pair", "--ttl", "120"])
    assert namespace.ttl == 120
    for flag in ("--yes", "--approve", "--code"):
        with pytest.raises(SystemExit):
            parser.parse_args(["pair", flag])


def test_setup_forwards_re_enroll_and_no_probe_and_yes_stays_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []

    def capture(**kwargs: Any) -> SetupReport:
        seen.append(kwargs)
        return SetupReport((), 0)

    monkeypatch.setattr("relaydeck.companion.setup.run_setup", capture)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert cli.main(["setup", "--re-enroll", "--no-probe"]) == 0
    assert (seen[0]["re_enroll"], seen[0]["skip_route_probe"], seen[0]["assume_yes"]) == (
        True,
        True,
        False,
    )
    assert cli.main(["setup", "--yes"]) == 0
    assert (seen[1]["re_enroll"], seen[1]["assume_yes"]) == (False, True)


def test_setup_without_a_terminal_still_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(cli, "_pending_remedies", lambda _config: ())
    monkeypatch.setattr(
        "relaydeck.companion.setup.run_setup", lambda **k: pytest.fail("must not run")
    )
    before = sorted(tmp_path.rglob("*"))
    assert cli.main(["--config", str(tmp_path / "c.toml"), "setup", "--re-enroll"]) == 1
    assert sorted(tmp_path.rglob("*")) == before


# -- the login service arguments -----------------------------------------------------------------


def test_the_default_profile_adds_no_config_argument_and_another_one_adds_it(
    tmp_path: Path,
) -> None:
    exe = Path("/x/relaydeck")
    assert service.program_arguments(exe) == ["/x/relaydeck", "start", "--connect-herdr"]
    assert service.program_arguments(exe, Path("/p/b.toml")) == [
        "/x/relaydeck",
        "--config",
        "/p/b.toml",
        "start",
        "--connect-herdr",
    ]
    document = service.plist_document(exe, home=tmp_path, path_env="/usr/bin", config=Path("/p/b"))
    assert plistlib.loads(plistlib.dumps(document))["ProgramArguments"] == [
        str(exe),
        "--config",
        "/p/b",
        "service-run",
        "--log-directory",
        str(service.log_dir(tmp_path)),
    ]


def test_agent_state_compares_program_and_arguments(tmp_path: Path) -> None:
    home = tmp_path.resolve()
    home.chmod(0o700)
    exe = Path("/x/relaydeck")
    calls: list[list[str]] = []

    def launchctl(args: Any) -> int:
        calls.append(list(args))
        return 0

    assert service.agent_state(home, 501, exe, None, launchctl) == "install"
    service.install(executable=exe, home=home, uid=501, yes=True, load=False, runner=launchctl)
    assert service.agent_state(home, 501, exe, None, launchctl) == "done"
    assert service.agent_state(home, 501, exe, Path("/p/b"), launchctl) == "replace"
    assert service.agent_state(home, 501, Path("/y/relaydeck"), None, launchctl) == "replace"
    assert service.agent_state(home, 501, exe, None, lambda a: 113) == "load"
    assert all(call[1] == "print" for call in calls)


# -- the state lock ----------------------------------------------------------------------------


def test_setup_and_the_companion_contend_on_the_same_lock(tmp_path: Path) -> None:
    state = tmp_path.resolve() / "state"
    with lifecycle.state_lock(state):
        with pytest.raises(lifecycle.AlreadyRunning):
            with lifecycle.state_lock(state):
                pass
        with pytest.raises(lifecycle.AlreadyRunning):
            with lifecycle.StateLease(state):
                pass
    with lifecycle.StateLease(state):
        with pytest.raises(lifecycle.AlreadyRunning):
            with lifecycle.state_lock(state):
                pass
    with lifecycle.state_lock(state):  # released by both
        assert lifecycle.status(state)["state"] in ("starting", "stopped", "unresponsive")


def test_a_held_setup_lock_reads_as_a_non_running_state(tmp_path: Path) -> None:
    state = tmp_path.resolve() / "state"
    with lifecycle.state_lock(state):
        assert lifecycle.status(state)["state"] == "starting"


# -- the connector's strict reads ---------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path.resolve() / "keys"
    path.mkdir(mode=0o700)
    return path


def test_keys_and_credentials_round_trip_privately(home: Path) -> None:
    keys = generate_keys()
    save_keys(keys, str(home / "k.json"))
    assert load_keys(str(home / "k.json")) == keys
    creds = ConnectorCreds("https://gw", "h1", "rdh_x")
    save_creds(creds, str(home / "c.json"))
    assert load_creds(str(home / "c.json")) == creds
    assert oct((home / "k.json").stat().st_mode & 0o777) == "0o600"


def test_a_public_half_that_is_not_derived_from_the_seed_is_refused(home: Path) -> None:
    one, two = generate_keys(), generate_keys()
    save_keys(ConnectorKeys(one.seed_b64, two.public_openssh), str(home / "k.json"))
    with pytest.raises(EnrollmentError):
        load_keys(str(home / "k.json"))


@pytest.mark.parametrize("fault", ["group", "symlink", "hardlink", "fifo"])
def test_unsafe_key_files_are_refused_without_blocking(home: Path, fault: str) -> None:
    target = home / "k.json"
    save_keys(generate_keys(), str(home / "real.json"))
    if fault == "group":
        os.rename(home / "real.json", target)
        target.chmod(0o640)
    elif fault == "symlink":
        target.symlink_to(home / "real.json")
    elif fault == "hardlink":
        os.link(home / "real.json", target)
    else:
        os.mkfifo(target, 0o600)
    with pytest.raises(EnrollmentError):
        load_keys(str(target))


def test_saving_into_a_folder_others_can_write_is_refused(tmp_path: Path) -> None:
    wide = tmp_path.resolve() / "wide"
    wide.mkdir()
    wide.chmod(0o775)
    with pytest.raises(EnrollmentError):
        save_keys(generate_keys(), str(wide / "k.json"))
    assert list(wide.iterdir()) == []


def test_the_service_command_carries_a_non_default_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[Path | None] = []

    def capture(**kwargs: Any) -> service.Outcome:
        seen.append(kwargs["config"])
        return service.Outcome(("ok",), False, 0)

    monkeypatch.setattr(service, "install", capture)
    monkeypatch.setenv("HOME", str(tmp_path))
    profile = tmp_path / "b.toml"
    assert cli.main(["--config", str(profile), "service", "install"]) == 0
    assert cli.main(["service", "install"]) == 0
    assert seen == [profile, None]
