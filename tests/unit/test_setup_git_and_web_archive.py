"""Git for the Changes screen (a warning, never a block), and `setup --web-archive`."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from relaydeck import cli
from relaydeck.companion.inspection import prereqs, readiness
from relaydeck.companion.setup import SetupReport
from relaydeck.companion.setup.context import LocalWeb
from relaydeck.companion.setup.prerequisites import PrerequisitesStep
from relaydeck.companion.setup.private_step import PrivateFilesStep
from relaydeck.companion.setup.web import WebStep
from relaydeck.git.runner import GitRunner, GitUnavailable
from relaydeck.releases import web as webrelease
from relaydeck.version import __version__
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)
from tests.support.setup.prereqs import probes
from tests.support.setup.rig import SetupRig
from tests.support.setup.web_archive import signed_files

MISSING = prereqs.GIT_WARNINGS["git_missing"]

# -- the git check --------------------------------------------------------------------------------


def test_the_warnings_are_the_fixed_lines_the_brief_names() -> None:
    assert MISSING == (
        "Changes screen unavailable: install Apple's developer tools with `xcode-select --install`"
    )
    assert "2.45" in prereqs.GIT_WARNINGS["git_too_old"]


def test_no_git_on_the_path_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prereqs, "trusted_executable", lambda program, **kw: None)
    assert prereqs.git_state() == "git_missing"


def test_the_apple_stub_is_not_run_until_the_developer_tools_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prereqs, "trusted_executable", lambda program, **kw: "/usr/bin/git")
    monkeypatch.setattr(prereqs, "_developer_tools_present", lambda: False)
    monkeypatch.setattr(GitRunner, "create", classmethod(lambda cls, **kw: pytest.fail("ran git")))
    assert prereqs.git_state() == "git_missing"


@pytest.mark.parametrize(
    ("raised", "state"),
    [
        (None, "ok"),
        (GitUnavailable("git_too_old"), "git_too_old"),
        (GitUnavailable("git_missing"), "git_missing"),
        (GitUnavailable("git_timeout"), "git_unknown"),
        (GitUnavailable("git_execution_failed"), "git_unknown"),
        (RuntimeError("synthetic"), "git_unknown"),
    ],
)
def test_the_runners_own_verdict_decides(
    monkeypatch: pytest.MonkeyPatch, raised: Exception | None, state: str
) -> None:
    monkeypatch.setattr(
        prereqs, "trusted_executable", lambda program, **kw: "/opt/homebrew/bin/git"
    )

    def create(cls: Any, **kwargs: Any) -> Any:
        if raised is not None:
            raise raised
        return object()

    monkeypatch.setattr(GitRunner, "create", classmethod(create))
    assert prereqs.git_state() == state


@pytest.mark.parametrize(("code", "present"), [(0, True), (2, False)])
def test_developer_tools_are_asked_with_a_fixed_read_only_command(
    monkeypatch: pytest.MonkeyPatch, code: int, present: bool
) -> None:
    seen: list[list[str]] = []

    def fake(argv: list[str], **kwargs: Any) -> Any:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, code)

    monkeypatch.setattr(prereqs, "trusted_executable", lambda program, **kw: program)
    monkeypatch.setattr(subprocess, "run", fake)
    assert prereqs._developer_tools_present() is present
    assert seen == [["/usr/bin/xcode-select", "-p"]]


def test_default_probes_check_git_and_plain_probes_do_not() -> None:
    assert prereqs.default_probes().git_state is prereqs.git_state
    assert probes().git_state() == "ok"


def test_a_missing_git_is_a_warning_and_never_blocks() -> None:
    report = prereqs.run_prereqs(
        socket="/s", https_port=8445, probes=probes(git_state=lambda: "git_missing")
    )
    assert report.ready and MISSING in report.warnings
    assert f"warning: {MISSING}" in report.lines()


def test_readiness_shows_git_as_a_warning_that_does_not_block_pairing() -> None:
    report = prereqs.run_prereqs(
        socket="/s", https_port=8445, probes=probes(git_state=lambda: "git_too_old")
    )
    item = readiness._prerequisites(report)
    assert item.state == readiness.WARN and "Changes screen" in item.reason
    assert "xcode-select --install" in item.next_step
    assert item.number not in readiness.PAIRING_ITEMS


def test_setup_finishes_with_the_changes_line_and_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = SetupRig(tmp_path, monkeypatch)
    rig.answers.rules = {"Create these private files": True}
    report = rig.run(
        steps=[PrerequisitesStep(), PrivateFilesStep()],
        probes=probes(rig.serve, git_state=lambda: "git_missing"),
    )
    assert report.exit_code == 0 and rig.config_path.is_file()
    assert rig.said[-1] == MISSING + "."
    assert any(line == f"warning: {MISSING}" for line in rig.said)


# -- setup --web-archive / --web-signature -------------------------------------------------------


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def capture(**kwargs: Any) -> SetupReport:
        calls.append(kwargs)
        return SetupReport((), 0)

    monkeypatch.setattr("relaydeck.companion.setup.run_setup", capture)
    monkeypatch.setattr(cli, "_pending_remedies", lambda _config: ())
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    return calls


def files(tmp_path: Path) -> tuple[Path, Path]:
    archive, signature, _key = signed_files(tmp_path, __version__)
    return archive, signature


@pytest.mark.parametrize("flag", ["--web-archive", "--web-signature"])
def test_one_without_the_other_is_refused_before_anything_happens(
    tmp_path: Path, seen: list[dict[str, Any]], capsys: pytest.CaptureFixture[str], flag: str
) -> None:
    archive, signature = files(tmp_path)
    value = archive if flag == "--web-archive" else signature
    assert cli.main(["--config", str(tmp_path / "c.toml"), "setup", flag, str(value)]) == 2
    assert "go together" in capsys.readouterr().out and seen == []


@pytest.mark.parametrize("mode", ["--check", "--detect", "--undo-sources"])
def test_a_report_or_an_undo_refuses_the_archive(
    tmp_path: Path, seen: list[dict[str, Any]], capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    archive, signature = files(tmp_path)
    argv = ["--config", str(tmp_path / "c.toml"), "setup", mode]
    argv += ["--web-archive", str(archive), "--web-signature", str(signature)]
    assert cli.main(argv) == 2
    assert "does not apply" in capsys.readouterr().out and seen == []


def test_files_that_are_not_there_are_refused(
    tmp_path: Path, seen: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["--config", str(tmp_path / "c.toml"), "setup", "--web-archive", str(tmp_path / "a")]
    argv += ["--web-signature", str(tmp_path / "s")]
    assert cli.main(argv) == 2
    assert "not a file" in capsys.readouterr().out and seen == []


def test_a_profile_with_the_web_app_off_refuses_the_archive(
    tmp_path: Path,
    seen: list[dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive, signature = files(tmp_path)
    monkeypatch.setattr(
        "relaydeck.companion.config.load_config", lambda path: type("C", (), {"web_ui": False})()
    )
    argv = ["--config", str(tmp_path / "c.toml"), "setup", "--web-archive", str(archive)]
    argv += ["--web-signature", str(signature)]
    assert cli.main(argv) == 2
    assert "web_ui = false" in capsys.readouterr().out and seen == []


def test_both_files_become_the_web_source_of_the_setup_run(
    tmp_path: Path, seen: list[dict[str, Any]]
) -> None:
    archive, signature = files(tmp_path)
    argv = ["--config", str(tmp_path / "c.toml"), "setup", "--web-archive", str(archive)]
    argv += ["--web-signature", str(signature)]
    assert cli.main(argv) == 0
    web = seen[0]["web"]
    assert isinstance(web, LocalWeb) and (web.archive, web.signature) == (archive, signature)


def test_without_the_flags_the_release_download_is_used(seen: list[dict[str, Any]]) -> None:
    assert cli.main(["setup"]) == 0
    assert "web" not in seen[0]


@pytest.fixture
def ready_rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SetupRig:
    rig = SetupRig(tmp_path, monkeypatch)
    rig.answers.rules = {"Create these private files": True}
    assert rig.run(steps=[PrerequisitesStep(), PrivateFilesStep()]).exit_code == 0
    rig.said.clear()
    rig.answers.rules = {"Install the web app": True}
    return rig


def test_the_local_archive_installs_when_its_signature_verifies(
    ready_rig: SetupRig, tmp_path: Path
) -> None:
    archive, signature, key = signed_files(tmp_path, __version__)
    web = LocalWeb(archive, signature, key=key, store=tmp_path / "store")
    report = ready_rig.run(steps=[WebStep()], web=web)
    assert report.exit_code == 0 and web.installed(__version__)
    assert f"signed bundle for {__version__} installed and verified" in ready_rig.text


def test_a_wrong_signature_is_rejected_and_installs_nothing(
    ready_rig: SetupRig, tmp_path: Path
) -> None:
    archive, signature, key = signed_files(
        tmp_path, __version__, signer=Ed25519PrivateKey.generate()
    )
    web = LocalWeb(archive, signature, key=key, store=tmp_path / "store")
    report = ready_rig.run(steps=[WebStep()], web=web)
    assert report.exit_code == 1 and not web.installed(__version__)
    assert "signature" in ready_rig.text and "Nothing was changed" in ready_rig.text
    assert not (tmp_path / "store").exists() or not any((tmp_path / "store").iterdir())


def test_the_packaged_key_rejects_a_bundle_signed_with_another_key(
    ready_rig: SetupRig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, signature = files(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "other-home"))
    web = LocalWeb(archive, signature, store=tmp_path / "store")  # key=None: the packaged key
    report = ready_rig.run(steps=[WebStep()], web=web)
    assert report.exit_code == 1 and "signature" in ready_rig.text


def test_a_second_run_is_idempotent(ready_rig: SetupRig, tmp_path: Path) -> None:
    archive, signature, key = signed_files(tmp_path, __version__)
    web = LocalWeb(archive, signature, key=key, store=tmp_path / "store")
    assert ready_rig.run(steps=[WebStep()], web=web).exit_code == 0
    ready_rig.said.clear()
    ready_rig.answers.asked.clear()
    assert ready_rig.run(steps=[WebStep()], web=web).exit_code == 0
    assert ready_rig.answers.asked == [] and "is installed" in ready_rig.text


def test_the_local_source_never_reaches_the_network(
    ready_rig: SetupRig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, signature, key = signed_files(tmp_path, __version__)
    monkeypatch.setattr(webrelease, "fetch_release", lambda *a, **k: pytest.fail("network"))
    monkeypatch.setattr(webrelease, "_HttpxGetter", lambda *a, **k: pytest.fail("network"))
    web = LocalWeb(archive, signature, key=key, store=tmp_path / "store")
    assert ready_rig.run(steps=[WebStep()], web=web).exit_code == 0


# -- the read-only paths never run a remedy -------------------------------------------------------


def test_report_detect_and_no_terminal_paths_never_reach_a_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from relaydeck.companion.setup import remediate

    def refuse(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("a read-only path reached a remedy")

    monkeypatch.setattr(remediate, "remediate", refuse)
    monkeypatch.setattr(remediate, "real_tools", refuse)
    monkeypatch.setattr(cli, "_pending_remedies", lambda _config: ("Install Herdr (asked first).",))
    monkeypatch.setattr(cli, "_check_report", lambda _config: readiness.pending_decisions(_config))
    config = ["--config", str(tmp_path / "c.toml"), "setup"]
    assert cli.main([*config, "--detect"]) == 0
    cli.main([*config, "--check"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main([*config, "--yes"]) == 1
    out = capsys.readouterr().out
    assert "Setup would offer, asking first:" in out and "Install Herdr (asked first)." in out
    assert "Nothing was written." in out
