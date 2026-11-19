"""Setup's remedies for a failed prerequisite: ask first, do one thing, wait a bounded time, then
the full preflight decides. Every effect is a fake; nothing downloads, opens or spawns."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from relaydeck.companion.inspection import prereqs
from relaydeck.companion.setup import STOPPED_LINE, installer, remediate
from relaydeck.companion.setup.prerequisites import STOPPED_AFTER_CHANGES_LINE
from tests.support.setup.herdr_world import HerdrWorld, private_mode, tailscale_state
from tests.support.setup.private import private_steps
from tests.support.setup.rig import SetupRig

INSTALLER = b"echo synthetic installer\n"


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SetupRig:
    return SetupRig(tmp_path, monkeypatch)


@pytest.fixture
def herdr(rig: SetupRig) -> HerdrWorld:
    return HerdrWorld(rig)


def run(rig: SetupRig, world: HerdrWorld, **over: Any) -> Any:
    options: dict[str, Any] = {"probes": world.probes()} | over
    return rig.run(steps=private_steps(), **options)


def nothing_written(rig: SetupRig) -> bool:
    return not rig.config_path.exists() and not any(
        name for name in rig.snapshot() if not name.startswith(".local")
    )


# -- Herdr is missing ----------------------------------------------------------------------


def test_missing_herdr_is_installed_after_a_yes_and_setup_goes_on(
    rig: SetupRig, herdr: HerdrWorld
) -> None:
    rig.remedies.allow_install(INSTALLER)
    rig.answers.rules = {"Install it now": True, "Create these private files": False}
    original = rig.remedies.run_script

    def installer_run(script: Path, env: Any, terminal: int) -> installer.Ran:
        ran = original(script, env, terminal)
        herdr.install()
        herdr.running = True
        return ran

    rig.remedies.run_script = installer_run  # type: ignore[method-assign]
    report = run(rig, herdr)
    assert rig.remedies.downloads == [installer.HERDR_INSTALLER_URL]
    body, env, terminal = rig.remedies.scripts[0]
    assert body == INSTALLER and terminal == rig.remedies.opened_terminals[0]
    assert set(env) <= {"HOME", "PATH", "TMPDIR", "USER", "LANG"} and "HOME" in env
    assert env["PATH"] == installer.SYSTEM_PATH and env["HOME"] == str(rig.home)
    assert "Installed: Herdr." in rig.text
    assert rig.answers.count("Install it now") == 1
    assert "Create these private files" in "\n".join(rig.answers.asked)  # the gate opened
    assert report.exit_code == 0 or "Setup stopped at 'Prerequisites'" not in rig.text


def test_the_question_is_asked_before_anything_is_downloaded(
    rig: SetupRig, herdr: HerdrWorld
) -> None:
    rig.remedies.allow_install()
    asked_downloads: list[int] = []
    rig.answers.rules = {"Install it now": False}
    original = rig.answers.__call__

    def watch(prompt: str) -> bool:
        asked_downloads.append(len(rig.remedies.downloads))
        return original(prompt)

    report = run(rig, herdr, confirm=watch)
    assert asked_downloads[0] == 0
    assert rig.remedies.downloads == [] and rig.remedies.scripts == []
    assert remediate.HERDR_MANUAL in rig.text
    assert STOPPED_LINE in rig.text and report.exit_code != 0
    assert nothing_written(rig)


def test_unsafe_destination_is_refused_before_a_question_a_download_or_an_installer(
    rig: SetupRig, herdr: HerdrWorld
) -> None:
    rig.remedies.allow_install()
    herdr.install(mode=0o666)  # present, writable by others
    herdr.installed = False  # the preflight still sees it as missing
    rig.answers.default = True
    report = run(rig, herdr)
    assert rig.answers.count("Install it now") == 0
    assert rig.remedies.downloads == [] and rig.remedies.scripts == []
    assert "unsafe ownership or permissions" in rig.text and report.exit_code != 0
    assert private_mode(herdr.destination) == 0o666  # nothing was repaired


def test_a_destination_folder_that_others_can_write_is_refused_too(
    rig: SetupRig, herdr: HerdrWorld
) -> None:
    rig.remedies.allow_install()
    for folder in (rig.home / ".local", rig.home / ".local" / "bin"):
        folder.mkdir()
        folder.chmod(0o700)
    folder.chmod(0o777)
    rig.answers.default = True
    run(rig, herdr)
    assert rig.remedies.downloads == [] and rig.remedies.scripts == []
    assert "unsafe ownership or permissions" in rig.text


def test_an_installer_that_leaves_an_unsafe_file_is_not_trusted(
    rig: SetupRig, herdr: HerdrWorld
) -> None:
    rig.remedies.allow_install()
    rig.answers.default = True
    original = rig.remedies.run_script

    def installer_run(script: Path, env: Any, terminal: int) -> installer.Ran:
        herdr.install(mode=0o777)
        return original(script, env, terminal)

    rig.remedies.run_script = installer_run  # type: ignore[method-assign]
    herdr.running = True
    run(rig, herdr)
    assert "unsafe ownership or permissions" in rig.text
    assert "Setup stopped" in rig.text


def test_a_failed_download_starts_no_installer(rig: SetupRig, herdr: HerdrWorld) -> None:
    rig.answers.rules = {"Install it now": True}
    report = run(rig, herdr)  # the default fake download fails
    assert rig.remedies.scripts == [] and report.exit_code != 0
    assert "the installer download failed" in rig.text and remediate.HERDR_MANUAL in rig.text


def test_without_a_terminal_nothing_is_installed(rig: SetupRig, herdr: HerdrWorld) -> None:
    rig.remedies.allow_install()
    rig.remedies.has_terminal = False
    rig.answers.rules = {"Install it now": True}
    run(rig, herdr)
    assert rig.remedies.scripts == []
    assert "needs a terminal" in rig.text


@pytest.mark.parametrize("code", [1, 7, 127])
def test_a_failed_installer_is_reported_by_its_exit_code_only(
    rig: SetupRig, herdr: HerdrWorld, code: int
) -> None:
    rig.remedies.allow_install()
    rig.remedies.ran = installer.Ran(code)
    rig.answers.rules = {"Install it now": True}
    report = run(rig, herdr)
    assert f"Herdr install failed (exit {code})." in rig.text
    assert remediate.HERDR_MANUAL in rig.text and "Installed: Herdr" not in rig.text
    assert report.exit_code != 0


def test_a_timed_out_installer_is_reported_as_possibly_partial(
    rig: SetupRig, herdr: HerdrWorld
) -> None:
    rig.remedies.allow_install()
    rig.remedies.ran = installer.Ran(None)
    rig.answers.rules = {"Install it now": True}
    run(rig, herdr)
    assert "Herdr install was interrupted; it may be partly installed." in rig.text


def test_ctrl_c_during_the_installer_stops_setup_and_says_what_may_have_changed(
    rig: SetupRig, herdr: HerdrWorld
) -> None:
    rig.remedies.allow_install()
    rig.remedies.raise_on_run = KeyboardInterrupt()
    rig.answers.rules = {"Install it now": True}
    report = run(rig, herdr)
    assert report.exit_code == 130
    assert "Herdr install was interrupted; it may be partly installed." in rig.text
    assert "Already changed before the interrupt: Herdr install (may be partly installed)." in (
        rig.text
    )
    assert nothing_written(rig)


def test_installer_output_goes_to_the_terminal_and_never_into_the_report(
    rig: SetupRig, herdr: HerdrWorld, tmp_path: Path
) -> None:
    out = tmp_path / "terminal.txt"
    out.write_text("")
    rig.remedies.allow_install(terminal=str(out))
    original = rig.remedies.run_script

    def noisy(script: Path, env: Any, terminal: int) -> installer.Ran:
        os.write(terminal, b"SYNTHETIC-INSTALLER-OUTPUT /Users/secret/path token=abc\n")
        return original(script, env, terminal)

    rig.remedies.run_script = noisy  # type: ignore[method-assign]
    rig.answers.rules = {"Install it now": True}
    run(rig, herdr)
    for fd in rig.remedies.opened_terminals:  # the setup code closes the one it was given
        with pytest.raises(OSError):
            os.fstat(fd)
    assert "SYNTHETIC-INSTALLER-OUTPUT" in out.read_text()
    assert "SYNTHETIC-INSTALLER-OUTPUT" not in rig.text and "token=abc" not in rig.text


def test_yes_answers_the_install_question_but_not_the_private_files(
    rig: SetupRig, herdr: HerdrWorld
) -> None:
    rig.remedies.allow_install()
    original = rig.remedies.run_script

    def installer_run(script: Path, env: Any, terminal: int) -> installer.Ran:
        herdr.install()
        herdr.running = True
        return original(script, env, terminal)

    rig.remedies.run_script = installer_run  # type: ignore[method-assign]
    run(rig, herdr, assume_yes=True)
    assert rig.answers.count("Install it now") == 0 and len(rig.remedies.scripts) == 1
    assert rig.answers.count("Create these private files") == 1  # still asked


# -- Herdr is installed but not answering --------------------------------------------------


def test_setup_waits_for_herdr_in_three_second_polls_and_never_starts_it(
    rig: SetupRig, herdr: HerdrWorld
) -> None:
    herdr.install()
    rig.remedies.allow_install()
    sleeps = rig.remedies.clock.sleeps
    original_sleep = rig.remedies.clock.sleep

    def opens_herdr(seconds: float) -> None:
        original_sleep(seconds)
        if len(sleeps) == 4:
            herdr.running = True

    rig.remedies.tools = lambda: remediate.Tools(  # type: ignore[method-assign]
        download=rig.remedies.download,
        open_terminal=rig.remedies.open_terminal,
        run_script=rig.remedies.run_script,
        opener=rig.remedies.opener,
        clock=rig.remedies.clock,
        sleep=opens_herdr,
    )
    run(rig, herdr, assume_yes=True)  # --yes still waits
    assert sleeps == [3.0, 3.0, 3.0, 3.0]
    assert "Herdr's server is not answering on its socket." in rig.text
    assert "run `herdr` in another terminal window" in rig.text
    assert rig.remedies.scripts == [] and rig.remedies.opened == []
    assert STOPPED_LINE not in rig.text and "Herdr 0.9.1" not in rig.text


def test_the_wait_is_five_minutes_in_total_including_each_probe(
    rig: SetupRig, herdr: HerdrWorld
) -> None:
    herdr.install()
    herdr.ping_cost = 4.0
    started: list[float] = []

    def watch(line: str) -> None:
        rig.said.append(line)
        if line.startswith("Herdr's server is not answering"):
            started.append(rig.remedies.clock.now)

    report = run(rig, herdr, emit_line=watch)
    clock = rig.remedies.clock
    assert started and clock.ends[-1] - started[0] <= remediate.WAIT_S
    assert clock.ends[-1] - started[0] >= remediate.WAIT_S - 3.0 - 4.0
    assert len(clock.sleeps) < 50  # not 100 polls: the probes' own time counts
    assert report.exit_code != 0 and STOPPED_LINE in rig.text and nothing_written(rig)


def test_a_custom_socket_message_does_not_tell_the_owner_to_run_bare_herdr(
    rig: SetupRig,
) -> None:
    said: list[str] = []
    ctx = SimpleNamespace(
        config=SimpleNamespace(herdr_socket="/synthetic/custom.sock"),
        say=said.append,
        assume_yes=True,
        confirm=lambda _q: True,
        remediation=remediate.Tools(),
    )
    ticks = iter(range(1000))
    ctx.remediation = remediate.Tools(clock=lambda: float(next(ticks)), sleep=lambda _s: None)
    remedy = remediate._Remedy(
        ctx,  # type: ignore[arg-type]
        ctx.remediation,
        lambda: prereqs.Report(
            (prereqs.Result("herdr", "ok"),),
        ),
        remediate.Effects(),
    )
    remedy.herdr_down(prereqs.Result("herdr", "not_running", reason="herdr_down"))
    assert "socket set in your config" in said[0] and "run `herdr`" not in said[0]


def test_a_wrong_protocol_gets_guidance_and_no_wait(rig: SetupRig, herdr: HerdrWorld) -> None:
    herdr.install()
    herdr.running = True
    herdr.protocol_ok = False
    rig.answers.default = True
    report = run(rig, herdr)
    assert "herdr update" in rig.text and rig.remedies.clock.sleeps == []
    assert rig.remedies.scripts == [] and report.exit_code != 0 and nothing_written(rig)


# -- Tailscale -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("backend", "question", "argv", "instruction"),
    [
        ("Stopped", "Tailscale is not running", ["-a", "Tailscale"], "Connect Tailscale."),
        ("NeedsLogin", "not signed in", ["-a", "Tailscale"], "Sign in to Tailscale."),
    ],
)
def test_tailscale_is_opened_once_after_consent_then_waited_for(
    rig: SetupRig,
    herdr: HerdrWorld,
    backend: str,
    question: str,
    argv: list[str],
    instruction: str,
) -> None:
    herdr.install()
    herdr.running = True
    serve = tailscale_state(backend)
    rig.remedies.allow_open()
    original = rig.remedies.clock.sleep

    def connects(seconds: float) -> None:
        original(seconds)
        if len(rig.remedies.clock.sleeps) == 2:
            serve.status = {**serve.status, "BackendState": "Running"}

    rig.remedies.tools = lambda: remediate.Tools(  # type: ignore[method-assign]
        opener=rig.remedies.opener, clock=rig.remedies.clock, sleep=connects
    )
    rig.answers.rules = {question: True, "Create these private files": False}
    rig.serve = serve
    run(rig, herdr, probes=herdr.probes(serve), runner=serve)
    assert rig.remedies.opened == [argv]
    assert instruction in rig.text and "Opened: Tailscale." in rig.text
    assert len(rig.remedies.clock.sleeps) == 2


def test_missing_tailscale_opens_its_download_page_once(rig: SetupRig, herdr: HerdrWorld) -> None:
    herdr.install()
    herdr.running = True
    rig.remedies.allow_open()
    rig.answers.rules = {"Tailscale is not installed": True}

    def none() -> str:
        raise prereqs.tailscale.TailscaleError("absent")

    run(rig, herdr, probes=herdr.probes(find_tailscale=none))
    assert rig.remedies.opened == [[remediate.TAILSCALE_DOWNLOAD]]
    assert rig.remedies.clock.now == pytest.approx(remediate.WAIT_S)  # waited, then gave up


def test_tailscale_dns_opens_the_admin_dns_page(rig: SetupRig, herdr: HerdrWorld) -> None:
    herdr.install()
    herdr.running = True
    rig.remedies.allow_open()
    rig.answers.rules = {"MagicDNS": True}
    serve = tailscale_state("Running")
    serve.status = {**serve.status, "CurrentTailnet": {"MagicDNSEnabled": False}}
    run(rig, herdr, probes=herdr.probes(serve), runner=serve)
    assert rig.remedies.opened == [[remediate.TAILSCALE_DNS]]


def test_tailscale_waiting_for_approval_is_guidance_only(rig: SetupRig, herdr: HerdrWorld) -> None:
    herdr.install()
    herdr.running = True
    rig.remedies.allow_open()
    rig.answers.default = True
    serve = tailscale_state("NeedsMachineAuth")
    run(rig, herdr, probes=herdr.probes(serve), runner=serve)
    assert rig.remedies.opened == [] and rig.remedies.clock.sleeps == []
    assert prereqs.GUIDANCE["tailscale_approval"] in rig.text


def test_declining_the_open_question_opens_nothing(rig: SetupRig, herdr: HerdrWorld) -> None:
    herdr.install()
    herdr.running = True
    rig.remedies.allow_open()
    serve = tailscale_state("Stopped")
    run(rig, herdr, probes=herdr.probes(serve), runner=serve)
    assert rig.remedies.opened == [] and rig.remedies.clock.sleeps == []
    assert STOPPED_LINE in rig.text


def test_an_opener_that_fails_is_reported_and_not_waited_on(
    rig: SetupRig, herdr: HerdrWorld
) -> None:
    herdr.install()
    herdr.running = True
    serve = tailscale_state("Stopped")
    rig.answers.rules = {"Tailscale is not running": True}
    run(rig, herdr, probes=herdr.probes(serve), runner=serve)  # the default opener fails
    assert "Could not open Tailscale" in rig.text and rig.remedies.clock.sleeps == []


# -- partial installs, local failures, the shape of a wait ----------------------------------


def partial_run(rig: SetupRig, herdr: HerdrWorld, ran: installer.Ran) -> Any:
    """An installer that puts a file in the destination and then ends as `ran` says."""
    rig.remedies.allow_install()
    original = rig.remedies.run_script

    def writes_then_ends(script: Path, env: Any, terminal: int) -> installer.Ran:
        original(script, env, terminal)
        herdr.destination.parent.mkdir(parents=True, exist_ok=True)
        herdr.destination.write_text("#!/bin/sh\n")
        return ran

    rig.remedies.run_script = writes_then_ends  # type: ignore[method-assign]
    rig.answers.rules = {"Install it now": True}
    return run(rig, herdr)


@pytest.mark.parametrize("ran", [installer.Ran(7), installer.Ran(None)], ids=["exit-7", "timeout"])
def test_an_installer_that_may_have_written_files_is_never_reported_as_nothing_written(
    rig: SetupRig, herdr: HerdrWorld, ran: installer.Ran
) -> None:
    report = partial_run(rig, herdr, ran)
    assert report.exit_code != 0
    assert "Herdr may be partly installed." in rig.said
    assert "Installed: Herdr." not in rig.said
    assert STOPPED_AFTER_CHANGES_LINE in rig.said and STOPPED_LINE not in rig.said
    assert "No RelayDeck private configuration was written" in rig.text
    assert "Nothing was written" not in rig.text and nothing_written(rig)


def test_an_exit_of_zero_without_a_usable_file_is_not_an_install(
    rig: SetupRig, herdr: HerdrWorld
) -> None:
    rig.remedies.allow_install()  # the installer "succeeds" and leaves nothing behind
    rig.answers.rules = {"Install it now": True}
    run(rig, herdr)
    assert "Installed: Herdr." not in rig.said and "Herdr may be partly installed." in rig.said
    assert "is not there" in rig.text and STOPPED_AFTER_CHANGES_LINE in rig.said


def test_an_installer_that_leaves_an_unsafe_file_is_not_called_installed(
    rig: SetupRig, herdr: HerdrWorld
) -> None:
    rig.remedies.allow_install()
    original = rig.remedies.run_script

    def installer_run(script: Path, env: Any, terminal: int) -> installer.Ran:
        herdr.install(mode=0o777)
        return original(script, env, terminal)

    rig.remedies.run_script = installer_run  # type: ignore[method-assign]
    rig.answers.rules = {"Install it now": True}
    run(rig, herdr)
    assert "Installed: Herdr." not in rig.said and "Herdr may be partly installed." in rig.said


@pytest.mark.parametrize("where", ["spawn", "scratch"])
def test_a_local_failure_becomes_fixed_text_with_every_resource_released(
    rig: SetupRig, herdr: HerdrWorld, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    rig.remedies.allow_install()
    private = "/synthetic/private-install-path"
    if where == "spawn":
        rig.remedies.raise_on_run = OSError(13, "synthetic denied", private)
    else:

        def no_scratch(*args: Any, **kwargs: Any) -> str:
            raise OSError(28, "synthetic: no space", private)

        monkeypatch.setattr(remediate.tempfile, "mkdtemp", no_scratch)
    closed: list[int] = []
    real_close = os.close

    def watching_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(remediate.os, "close", watching_close)
    rig.answers.rules = {"Install it now": True}
    report = run(rig, herdr)
    assert report.exit_code != 0
    assert "the installer could not be started" in rig.text and remediate.HERDR_MANUAL in rig.text
    assert private not in rig.text and "synthetic denied" not in rig.text
    assert rig.remedies.opened_terminals[0] in closed  # the terminal was released on this path too
    assert "Installed: Herdr." not in rig.said and nothing_written(rig)


def test_a_reason_that_changes_during_a_wait_ends_it_and_the_next_page_opens_at_once(
    rig: SetupRig, herdr: HerdrWorld
) -> None:
    herdr.install()
    herdr.running = True
    rig.remedies.allow_open()
    rig.answers.rules = {"not signed in": True, "MagicDNS": True, "Create these private": False}
    serve = tailscale_state("NeedsLogin")
    clock = rig.remedies.clock
    opened_at: list[tuple[float, list[str]]] = []

    def opener(argv: Any) -> int:
        opened_at.append((clock.now, list(argv)))
        return 0

    def signs_in(seconds: float) -> None:
        clock.sleep(seconds)
        serve.status = {
            **serve.status,
            "BackendState": "Running",
            "CurrentTailnet": {"MagicDNSEnabled": False},
        }

    rig.remedies.tools = lambda: remediate.Tools(  # type: ignore[method-assign]
        opener=opener, clock=clock, sleep=signs_in, bounded=rig.remedies.bounded
    )
    run(rig, herdr, probes=herdr.probes(serve), runner=serve)
    assert [argv for _at, argv in opened_at] == [["-a", "Tailscale"], [remediate.TAILSCALE_DNS]]
    assert opened_at[1][0] < 2 * remediate.POLL_S  # not after a five minute wait


def test_no_probe_starts_without_time_left_and_each_is_given_only_what_remains(
    rig: SetupRig, herdr: HerdrWorld
) -> None:
    herdr.install()
    herdr.ping_cost = 15.0  # a slow probe, as the fake clock sees it
    clock = rig.remedies.clock
    budgets: list[tuple[float, float]] = []
    started: list[float] = []

    def spy(call: remediate.Recheck, seconds: float) -> prereqs.Report | None:
        budgets.append((clock.now, seconds))
        return rig.remedies.bounded(call, seconds)

    rig.remedies.tools = lambda: remediate.Tools(  # type: ignore[method-assign]
        clock=clock, sleep=clock.sleep, bounded=spy
    )

    def watch(line: str) -> None:
        rig.said.append(line)
        if line.startswith("Herdr's server is not answering"):
            started.append(clock.now)

    run(rig, herdr, emit_line=watch)
    deadline = started[0] + remediate.WAIT_S
    assert budgets
    for at, seconds in budgets:
        assert at < deadline  # nothing begins at or after the deadline
        assert 0 < seconds <= deadline - at  # and a probe may not outlast it


def test_a_probe_that_outlasts_its_budget_is_given_up_on_in_real_time() -> None:
    release = threading.Event()
    report = prereqs.Report(())

    def slow() -> prereqs.Report:
        release.wait(5)
        return report

    began = time.monotonic()
    try:
        assert remediate.run_bounded(slow, 0.1) is None
        assert time.monotonic() - began < 2
    finally:
        release.set()
    assert remediate.run_bounded(lambda: report, 5) is report


def test_a_probe_that_raises_raises_in_the_caller() -> None:
    def broken() -> prereqs.Report:
        raise RuntimeError("synthetic probe failure")

    with pytest.raises(RuntimeError, match="synthetic probe failure"):
        remediate.run_bounded(broken, 5)


# -- the gate after the remedies -----------------------------------------------------------


def test_the_final_preflight_still_blocks_a_funnel_even_after_tailscale_is_fixed(
    rig: SetupRig, herdr: HerdrWorld
) -> None:
    herdr.install()
    herdr.running = True
    rig.remedies.allow_open()
    rig.answers.rules = {"Tailscale is not running": True}
    serve = tailscale_state("Stopped")
    serve.serve = {"Web": {}, "AllowFunnel": {"x.ts.net:8445": True}}
    original = rig.remedies.clock.sleep

    def connects(seconds: float) -> None:
        original(seconds)
        serve.status = {**serve.status, "BackendState": "Running"}

    rig.remedies.tools = lambda: remediate.Tools(  # type: ignore[method-assign]
        opener=rig.remedies.opener, clock=rig.remedies.clock, sleep=connects
    )
    report = run(rig, herdr, probes=herdr.probes(serve), runner=serve)
    assert report.exit_code != 0
    assert "Opened: Tailscale." in rig.text and STOPPED_AFTER_CHANGES_LINE in rig.text
    assert nothing_written(rig)
    assert "Create these private files" not in "\n".join(rig.answers.asked)


def test_the_stop_line_separates_what_was_installed_from_what_was_written(
    rig: SetupRig, herdr: HerdrWorld
) -> None:
    rig.remedies.allow_install()
    original = rig.remedies.run_script

    def installer_run(script: Path, env: Any, terminal: int) -> installer.Ran:
        herdr.install()
        return original(script, env, terminal)

    rig.remedies.run_script = installer_run  # type: ignore[method-assign]
    rig.answers.rules = {"Install it now": True}
    run(rig, herdr)  # Herdr is installed but never opened: the wait ends, setup stops
    lines = rig.said
    assert "Installed: Herdr." in lines and STOPPED_AFTER_CHANGES_LINE in lines
    assert lines.index("Installed: Herdr.") < lines.index(STOPPED_AFTER_CHANGES_LINE)
    assert "Nothing was written" not in rig.text
    assert "No RelayDeck private configuration was written" in rig.text
    assert not rig.config_path.exists()


def test_a_ready_system_asks_nothing_and_touches_nothing(rig: SetupRig, herdr: HerdrWorld) -> None:
    herdr.install()
    herdr.running = True
    run(rig, herdr, assume_yes=True)
    assert rig.remedies.downloads == [] and rig.remedies.opened == []
    assert rig.remedies.clock.sleeps == []
    assert rig.answers.count("Install it now") == 0


def test_pending_actions_name_only_what_would_be_offered() -> None:
    report = prereqs.Report(
        (
            prereqs.Result("tailscale", "not_running", reason="tailscale_stopped"),
            prereqs.Result("herdr", "missing", reason="herdr_missing"),
            prereqs.Result("uv", "missing", reason="uv_missing"),
        )
    )
    lines = remediate.pending_actions(report)
    assert len(lines) == 2 and all("asked first" in line or "never" in line for line in lines)
