"""What the owner sees from the commands: the additive `update` object in status and doctor JSON,
the update section of `setup --check`, the setup question and the `[updates]` config table."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from relaydeck import cli
from relaydeck.companion import cli as companion_cli
from relaydeck.companion.config import load_config
from relaydeck.companion.setup.updates_step import UpdatesStep
from relaydeck.companion.updates import schedule as update_schedule
from relaydeck.companion.updates.report import update_lines
from relaydeck.releases import records as release_records
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)
from tests.support.companion.asgi_companion import config_file
from tests.support.paths import TESTS_DIR
from tests.support.setup.rig import SetupRig
from tests.support.updates.signing import COMMIT

NOW = 1_800_000_000
RECORD = release_records.Record("0.3.1", COMMIT, "2026-12-01", ("Pairing is faster.",))


@pytest.fixture
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("FLET_WEB_PATH", raising=False)
    monkeypatch.setattr(companion_cli, "__version__", "0.3.0")
    monkeypatch.setattr("relaydeck.companion.updates.worker.__version__", "0.3.0")
    root = tmp_path.resolve() / "cfg"
    root.mkdir(mode=0o700)
    return config_file(root)


def with_table(path: Path, table: str) -> Path:
    path.write_text(path.read_text() + "\n" + table + "\n")
    return path


def store(profile: Path, state: update_schedule.State) -> None:
    config = load_config(profile)
    config.state_dir.mkdir(mode=0o700, exist_ok=True)
    update_schedule.save_state(config.state_dir, state)


def run_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> dict[str, Any]:
    code = companion_cli.main(argv)
    out = capsys.readouterr().out
    assert code == 0
    parsed = json.loads(out)  # the whole output stays one valid JSON document
    assert isinstance(parsed, dict)
    return parsed


# -- status and doctor ---------------------------------------------------------------------------


def test_status_stays_json_and_gains_the_update_object(
    profile: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = run_json(capsys, ["--config", str(profile), "status"])
    assert body["state"] == "stopped"
    assert body["update"] == {
        "installed": "0.3.0",
        "available": None,
        "check": "on",
        "last_attempt": None,
        "last_success": None,
        "outcome": None,
    }


def test_status_shows_a_stored_newer_release_and_never_fetches(
    profile: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store(
        profile,
        update_schedule.State(last_attempt=NOW, last_success=NOW, outcome="ok", record=RECORD),
    )
    update = run_json(capsys, ["--config", str(profile), "status"])["update"]
    assert update["available"] == {
        "version": "0.3.1",
        "commit": COMMIT,
        "issued": "2026-12-01",
        "notes": ["Pairing is faster."],
    }
    assert update["last_success"] == update_schedule.moment(NOW) and update["outcome"] == "ok"


def test_status_with_the_check_off_reports_off_and_offers_nothing(
    profile: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store(profile, update_schedule.State(record=RECORD))
    with_table(profile, "[updates]\ncheck = false")
    update = run_json(capsys, ["--config", str(profile), "status"])["update"]
    assert update["check"] == "off" and update["available"] is None


def test_status_with_a_corrupt_state_still_prints_json(
    profile: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = load_config(profile)
    config.state_dir.mkdir(mode=0o700)
    (config.state_dir / update_schedule.STATE_FILE).write_text("garbage")
    (config.state_dir / update_schedule.STATE_FILE).chmod(0o600)
    assert run_json(capsys, ["--config", str(profile), "status"])["update"]["outcome"] == "state"


def test_doctor_stays_json_and_gains_the_same_update_object(
    profile: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Prerequisites:
        def as_doc(self) -> dict[str, Any]:
            return {"results": []}

    monkeypatch.setattr(companion_cli, "preflight", lambda _path: Prerequisites())
    store(profile, update_schedule.State(last_success=NOW, outcome="ok", record=RECORD))
    body = run_json(capsys, ["--config", str(profile), "doctor"])
    assert body["update"]["available"]["version"] == "0.3.1"


# -- setup --check -------------------------------------------------------------------------------


def lines_of(profile: Path, now: int = NOW) -> list[str]:
    home = profile.parent
    return update_lines(
        load_config(profile),
        profile,
        now=lambda: now,
        home=home,
        uid=501,
        executable=Path("/synthetic/bin/relaydeck"),
        runner=lambda args: 1,
    )


def test_never_checked_says_so(profile: Path) -> None:
    assert lines_of(profile) == ["Update check: on (never checked yet)"]


def test_the_check_off_says_off(profile: Path) -> None:
    with_table(profile, "[updates]\ncheck = false")
    assert lines_of(profile) == ["Update check: off"]


def test_a_verified_check_shows_its_date_and_an_available_release_its_commands(
    profile: Path,
) -> None:
    store(
        profile,
        update_schedule.State(last_attempt=NOW, last_success=NOW, outcome="ok", record=RECORD),
    )
    lines = lines_of(profile)
    assert lines[0] == f"Update check: on (last verified {update_schedule.moment(NOW)[:10]})"  # type: ignore[index]
    assert lines[1] == "RelayDeck 0.3.1 is available (you have 0.3.0)."
    text = "\n".join(lines)
    assert "relaydeck-0.3.1-py3-none-any.whl" in text and "web fetch" in text
    assert "relaydeck --config" in text


def test_up_to_date_shows_no_release(profile: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store(profile, update_schedule.State(last_success=NOW, outcome="ok", record=RECORD))
    monkeypatch.setattr("relaydeck.companion.updates.worker.__version__", "0.3.1")
    lines = lines_of(profile)
    assert lines[0].startswith("Update check: on (last verified")
    assert not any("available" in line for line in lines)


def test_a_long_silence_is_noted(profile: Path) -> None:
    store(profile, update_schedule.State(last_success=NOW, outcome="ok"))
    lines = lines_of(profile, now=NOW + 61 * 86_400)
    assert any("No successful check since" in line for line in lines)
    assert not any("No successful check" in line for line in lines_of(profile, now=NOW + 86_400))


def test_a_developer_run_is_skipped_not_reported_as_unchecked(
    profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLET_WEB_PATH", "/dev/bundle")
    assert lines_of(profile)[0].startswith("Update check: skipped")


def test_setup_check_prints_the_section_after_the_report(
    profile: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Report:
        items: list[Any] = []
        exit_code = 0
        ready_to_pair = True

    monkeypatch.setattr(cli, "_check_report", lambda _path: Report())
    monkeypatch.setattr("relaydeck.version.__version__", "0.3.0")
    assert cli.main(["--config", str(profile), "setup", "--check"]) == 0
    out = capsys.readouterr().out
    assert "Ready to pair." in out
    assert out.index("Ready to pair.") < out.index("Update check:")


# -- the config table ----------------------------------------------------------------------------


def test_a_config_without_the_table_checks_by_default(profile: Path) -> None:
    assert load_config(profile).updates.check is True


def test_the_table_sets_the_check_and_is_strict(profile: Path) -> None:
    text = profile.read_text()
    with_table(profile, "[updates]\ncheck = false")
    assert load_config(profile).updates.check is False
    for bad in ("[updates]\ncheck = 1", '[updates]\ncheck = "no"', "[updates]\nother = true"):
        profile.write_text(text + "\n" + bad + "\n")
        with pytest.raises(ValueError):
            load_config(profile)


# -- the setup question --------------------------------------------------------------------------


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SetupRig:
    made = SetupRig(tmp_path, monkeypatch)
    made.answers.rules = {"Create these private files": True}
    assert made.run(steps=made_steps()).exit_code == 0
    made.answers.asked.clear()
    made.said.clear()
    return made


def made_steps() -> list[Any]:
    from tests.support.setup.private import private_steps

    return private_steps()


def test_the_question_defaults_to_yes_and_writes_the_choice(rig: SetupRig) -> None:
    rig.answers.rules = {"Check daily": True}
    assert rig.run(steps=[UpdatesStep()]).exit_code == 0
    assert rig.answers.asked == ["Check daily for RelayDeck updates? [Y/n] "]
    assert "check = true" in rig.config_path.read_text()
    assert rig.config().updates.check is True


def test_no_is_written_so_the_question_is_not_asked_again(rig: SetupRig) -> None:
    rig.answers.rules = {"Check daily": False}
    assert rig.run(steps=[UpdatesStep()]).exit_code == 0
    assert "check = false" in rig.config_path.read_text()
    assert rig.config().updates.check is False
    rig.answers.asked.clear()
    rig.run(steps=[UpdatesStep()])
    assert rig.answers.asked == []


def test_an_explicit_value_is_shown_and_never_changed(rig: SetupRig) -> None:
    body = rig.config_path.read_text() + "\n[updates]\ncheck = false\n"
    rig.config_path.write_text(body)
    rig.answers.rules = {"Check daily": True}
    rig.run(steps=[UpdatesStep()])
    assert rig.answers.asked == [] and rig.config_path.read_text() == body
    assert "Update check: off, as chosen in the config (not changed)." in rig.text


def test_an_empty_table_gets_the_line_inside_it(rig: SetupRig) -> None:
    rig.config_path.write_text(rig.config_path.read_text() + "\n[updates]\n")
    rig.answers.rules = {"Check daily": True}
    assert rig.run(steps=[UpdatesStep()]).exit_code == 0
    assert rig.config().updates.check is True
    assert rig.config_path.read_text().splitlines().count("[updates]") == 1


def test_the_yes_flag_does_not_answer_it(rig: SetupRig) -> None:
    rig.run(steps=[UpdatesStep()], assume_yes=True)
    assert rig.answers.count("Check daily") == 1


@pytest.mark.parametrize(
    ("typed", "expected"),
    [("", True), ("y", True), ("Yes", True), ("n", False), ("no", False), ("x", False)],
)
def test_an_empty_reply_is_yes_only_for_a_marked_question(
    monkeypatch: pytest.MonkeyPatch, typed: str, expected: bool
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: typed)
    assert cli._confirm("Check daily for RelayDeck updates? [Y/n] ") is expected
    assert cli._confirm("Pair a device now? [y/N] ") is (typed.lower() in ("y", "yes"))


# -- the words ------------------------------------------------------------------------------------


def test_the_readme_privacy_section_says_what_is_sent_and_how_to_turn_it_off() -> None:
    readme = (TESTS_DIR.parent / "README.md").read_text(encoding="utf-8")
    section = " ".join(readme.split("## Privacy", 1)[1].split("\n## ", 1)[0].split())
    for phrase in (
        "once a day",
        "github.com and its download servers",
        "IP address",
        "retry after six hours",
        "No account, terminal, repository or usage information is sent",
        "`[updates]` `check = false`",
        "relaydeck setup",
    ):
        assert phrase in section


def test_the_changelog_announces_the_notice_and_the_old_version_note() -> None:
    changelog = (TESTS_DIR.parent / "CHANGELOG.md").read_text(encoding="utf-8")
    entry = changelog.split("- **Updates**", 1)[1].split("\n- **", 1)[0]
    flat = " ".join(entry.split())
    assert "about once a day for a newer signed release" in flat
    assert "turn it off with `[updates] check = false`" in flat
    assert "does not load on 0.2.x" in flat


def test_the_template_documents_the_table_without_enabling_anything() -> None:
    template = (TESTS_DIR.parent / "src/relaydeck/companion/companion.template.toml").read_text()
    assert "# [updates]\n# check = true" in template


# -- the notice never breaks the commands ---------------------------------------------------------


@pytest.mark.parametrize(
    "damage",
    [{"outcome": []}, {"last_success": 10**30}, {"record": {"schema": []}}, {"jitter": {}}],
)
def test_status_and_doctor_survive_a_damaged_update_file(
    profile: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    damage: dict[str, object],
) -> None:
    config = load_config(profile)
    config.state_dir.mkdir(mode=0o700)
    target = config.state_dir / update_schedule.STATE_FILE
    target.write_text(json.dumps(update_schedule.State().as_doc() | damage))
    target.chmod(0o600)
    assert run_json(capsys, ["--config", str(profile), "status"])["update"]["outcome"] == "state"

    class Prerequisites:
        def as_doc(self) -> dict[str, Any]:
            return {"results": []}

    monkeypatch.setattr(companion_cli, "preflight", lambda _path: Prerequisites())
    assert run_json(capsys, ["--config", str(profile), "doctor"])["update"]["outcome"] == "state"


def test_a_failure_while_reading_the_notice_cannot_break_status_or_doctor(
    profile: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_config: object) -> None:
        raise OverflowError("synthetic")

    monkeypatch.setattr("relaydeck.companion.updates.worker.read_notice", broken)
    update = run_json(capsys, ["--config", str(profile), "status"])["update"]
    assert update["outcome"] == "state" and update["available"] is None


def test_setup_check_survives_a_failure_building_the_update_section(
    profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise OverflowError("synthetic")

    monkeypatch.setattr("relaydeck.companion.updates.report.update_lines", broken)
    assert cli._update_section(profile) == []


def test_unsafe_profile_characters_are_explained_instead_of_printing_commands(
    profile: Path,
) -> None:
    odd = profile.parent / "odd\nname.toml"
    odd.write_text(profile.read_text())
    odd.chmod(0o600)
    store(odd, update_schedule.State(last_success=NOW, outcome="ok", record=RECORD))
    text = "\n".join(
        update_lines(
            load_config(odd),
            odd,
            now=lambda: NOW,
            home=odd.parent,
            uid=501,
            executable=Path("/x"),
            runner=lambda args: 1,
        )
    )
    assert "RelayDeck 0.3.1 is available" in text and "commands are not shown" in text
    assert "uv tool install" not in text
