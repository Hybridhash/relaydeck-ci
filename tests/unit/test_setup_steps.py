"""The steps after the private files: agent kinds, Serve, web app, login service, waiting, pairing,
and how the runner stops, continues and reports."""

from __future__ import annotations

import json
import plistlib
from pathlib import Path
from typing import Any

import pytest

from relaydeck.companion.adapters import launchd as service
from relaydeck.companion.config import load_config
from relaydeck.companion.inspection import running
from relaydeck.companion.inspection.detect import DetectReport
from relaydeck.companion.setup import default_steps, kinds
from relaydeck.companion.setup.kinds import AgentKindsStep
from relaydeck.companion.setup.login import LoginServiceStep, WaitStep
from relaydeck.companion.setup.pair import PairStep
from relaydeck.companion.setup.prerequisites import PrerequisitesStep
from relaydeck.companion.setup.private_step import PrivateFilesStep
from relaydeck.companion.setup.serve import ServeStep
from relaydeck.companion.setup.steps import Outcome, Plan, Status, Step
from relaydeck.companion.setup.web import WebStep
from relaydeck.version import __version__
from tests.support.companion.setup_sources import row
from tests.support.setup.prereqs import Serve
from tests.support.setup.rig import Answers, SetupRig, healthy_body

HOSTPORT = "mac-a.synthetic-tailnet.example.ts.net:8445"
TARGET = "http://127.0.0.1:8125"
FILES = {"Create these private files": True}


def mapping(target: str = TARGET) -> dict[str, Any]:
    return {
        "TCP": {"8445": {"HTTPS": True}},
        "Web": {HOSTPORT: {"Handlers": {"/": {"Proxy": target}}}},
    }


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SetupRig:
    made = SetupRig(tmp_path, monkeypatch)
    made.answers.rules = dict(FILES)
    assert made.run(steps=[PrerequisitesStep(), PrivateFilesStep()]).exit_code == 0
    made.said.clear()
    made.answers.asked.clear()
    return made


def detected(*kinds: str) -> Any:
    rows = tuple(row(kind, kind.title(), next_step="can be added") for kind in kinds)
    return lambda _path: DetectReport(rows, "ok", "ok")


# -- agent kinds ----------------------------------------------------------------------------------


def test_agent_kinds_are_offered_by_name_default_no_and_written_on_yes(rig: SetupRig) -> None:
    before = rig.config_path.read_text()
    declined = rig.run(steps=[AgentKindsStep()], source_detect=detected("kind-a", "kind-b"))
    assert declined.exit_code == 0 and rig.config_path.read_text() == before
    assert rig.answers.asked == [
        "Allow these agents to be started from your devices: kind-a, kind-b? [y/N] "
    ]
    rig.answers.rules = {"Allow these agents": True}
    rig.run(steps=[AgentKindsStep()], source_detect=detected("kind-a", "kind-b"))
    assert load_config(rig.config_path).agent_kinds == ("kind-a", "kind-b")
    assert any(e.name.startswith("companion.toml.bak-") for e in rig.config_path.parent.iterdir())


def test_agent_kinds_already_set_or_none_installed_ask_nothing(rig: SetupRig) -> None:
    rig.run(steps=[AgentKindsStep()])
    assert rig.answers.asked == [] and "nothing to allow" in rig.text
    rig.answers.rules = {"Allow these agents": True}
    rig.run(steps=[AgentKindsStep()], source_detect=detected("kind-a"))
    rig.answers.asked.clear()
    rig.run(steps=[AgentKindsStep()], source_detect=detected("kind-b"))
    assert rig.answers.asked == [] and load_config(rig.config_path).agent_kinds == ("kind-a",)


def test_agent_kinds_write_only_what_the_owner_was_asked_about(
    rig: SetupRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # detections: check, question, then the write; a third agent appears while the question is open
    seen = iter([["kind-a"], ["kind-a"], ["kind-a", "kind-b"]])
    monkeypatch.setattr(kinds, "detected_kinds", lambda _ctx: next(seen))
    rig.answers.rules = {"Allow these agents": True}
    report = rig.run(steps=[AgentKindsStep()])
    assert report.exit_code == 0, rig.text
    assert rig.answers.asked == [
        "Allow these agents to be started from your devices: kind-a? [y/N] "
    ]
    assert load_config(rig.config_path).agent_kinds == ("kind-a",)


def test_agent_kinds_drop_an_agent_that_is_no_longer_installed(
    rig: SetupRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = iter([["kind-a", "kind-b"], ["kind-a", "kind-b"], ["kind-b"]])
    monkeypatch.setattr(kinds, "detected_kinds", lambda _ctx: next(seen))
    rig.answers.rules = {"Allow these agents": True}
    assert rig.run(steps=[AgentKindsStep()]).exit_code == 0
    assert load_config(rig.config_path).agent_kinds == ("kind-b",)


def test_agent_kinds_write_nothing_when_every_approved_agent_is_gone(
    rig: SetupRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = iter([["kind-a"], ["kind-a"], []])
    monkeypatch.setattr(kinds, "detected_kinds", lambda _ctx: next(seen))
    rig.answers.rules = {"Allow these agents": True}
    assert rig.run(steps=[AgentKindsStep()]).exit_code == 0
    assert load_config(rig.config_path).agent_kinds == ()
    assert "none allowed" in rig.text


def test_agent_kinds_refuse_a_config_edited_during_the_question(
    rig: SetupRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(kinds, "detected_kinds", lambda _ctx: ["kind-a"])
    path = rig.config_path

    class Editing(Answers):
        def __call__(self, prompt: str) -> bool:
            path.write_text(path.read_text() + "# edited meanwhile\n")
            return super().__call__(prompt)

    rig.answers = Editing({"Allow these agents": True})
    report = rig.run(steps=[AgentKindsStep()])
    assert report.exit_code == 1 and "run relaydeck setup again" in rig.text
    assert load_config(path).agent_kinds == ()
    assert path.read_text().endswith("# edited meanwhile\n")


# -- Serve ----------------------------------------------------------------------------------------


def test_serve_maps_after_yes_or_with_the_yes_flag_and_never_otherwise(rig: SetupRig) -> None:
    rig.run(steps=[ServeStep()])
    assert rig.serve.mutations == [] and "Not applied" in rig.text
    rig.run(steps=[ServeStep()], assume_yes=True)
    assert rig.serve.mutations == [["serve", "--bg", "--https=8445", TARGET]]


def test_serve_that_already_maps_is_done_and_a_foreign_mapping_is_a_conflict(
    rig: SetupRig,
) -> None:
    rig.serve.serve = mapping()
    assert rig.run(steps=[ServeStep()], assume_yes=True).exit_code == 0
    assert rig.serve.mutations == [] and "already maps" in rig.text
    rig.serve.serve = mapping("http://127.0.0.1:9000/private?grant=secret")
    rig.said.clear()
    report = rig.run(steps=[ServeStep()], assume_yes=True)
    assert report.exit_code == 1 and rig.serve.mutations == []
    assert "grant=secret" not in rig.text


# -- web app --------------------------------------------------------------------------------------


def test_web_is_skipped_when_the_companion_serves_the_bridge_only(rig: SetupRig) -> None:
    text = rig.config_path.read_text().replace("web_ui = true", "web_ui = false")
    rig.config_path.write_text(text)
    rig.run(steps=[WebStep()])
    assert rig.web.fetched == [] and rig.answers.asked == [] and "bridge only" in rig.text


def test_web_fetches_the_bundle_for_the_installed_version_after_yes(rig: SetupRig) -> None:
    rig.answers.rules = {"Install the web app": True}
    assert rig.run(steps=[WebStep()]).exit_code == 0
    assert rig.web.fetched == [__version__]
    rig.said.clear()
    rig.answers.asked.clear()
    assert rig.run(steps=[WebStep()]).exit_code == 0
    assert rig.answers.asked == [] and rig.web.fetched == [__version__]


def test_web_declined_stops_the_run_and_a_failed_fetch_names_only_the_code(rig: SetupRig) -> None:
    declined = rig.run()
    assert declined.exit_code == 1 and rig.web.fetched == []
    rig.said.clear()
    rig.web.fail = "signature"
    rig.answers.rules = {"Install the web app": True}
    failed = rig.run(steps=[WebStep()])
    assert failed.exit_code == 1 and "signature" in rig.text
    assert not rig.web.is_installed


# -- login service ----------------------------------------------------------------------------


def plist_of(rig: SetupRig) -> dict[str, Any]:
    document: dict[str, Any] = plistlib.loads(service.agent_path(rig.home).read_bytes())
    return document


def test_the_login_service_is_installed_loaded_and_carries_no_secret(rig: SetupRig) -> None:
    rig.answers.rules = {"Start RelayDeck at login": True}
    assert rig.run(steps=[LoginServiceStep()]).exit_code == 0, rig.text
    document = plist_of(rig)
    assert document["ProgramArguments"] == [
        "/synthetic/bin/relaydeck",
        "service-run",
        "--log-directory",
        str(service.log_dir(rig.home)),
    ]
    assert "token" not in json.dumps(document).lower() and "secret" not in json.dumps(document)
    assert rig.launchd.loaded and ["launchctl", "bootstrap"] == rig.launchd.calls[-1][:2]
    assert "<plist" not in rig.text and str(rig.home) not in rig.text
    rig.answers.asked.clear()
    before = service.agent_path(rig.home).read_bytes()
    rig.run(steps=[LoginServiceStep()])
    assert rig.answers.asked == [] and service.agent_path(rig.home).read_bytes() == before


def test_the_login_service_plan_names_the_command_the_plist_runs(rig: SetupRig) -> None:
    rig.answers.rules = {"Start RelayDeck at login": False}
    rig.run(steps=[LoginServiceStep()])
    assert "service-run --log-directory" in rig.text
    assert "starts `/synthetic/bin/relaydeck start --connect-herdr`" in rig.text


def test_an_installed_but_unloaded_agent_is_loaded_without_rewriting_it(rig: SetupRig) -> None:
    rig.answers.rules = {"Start RelayDeck at login": True}
    rig.run(steps=[LoginServiceStep()])
    rig.launchd.loaded = False
    rig.launchd.calls.clear()
    rig.said.clear()
    assert (
        service.agent_state(rig.home, 501, Path("/synthetic/bin/relaydeck"), None, rig.launchd)
        == "load"
    )
    assert rig.run(steps=[LoginServiceStep()]).exit_code == 0
    assert rig.launchd.loaded and "not loaded" in rig.text


def test_a_different_executable_replaces_the_agent_and_shows_both(rig: SetupRig) -> None:
    rig.answers.rules = {"Start RelayDeck at login": True}
    rig.run(steps=[LoginServiceStep()])
    rig.said.clear()
    rig.run(steps=[LoginServiceStep()], executable=Path("/synthetic/other/relaydeck"))
    assert "replaces the installed agent" in rig.text and "/synthetic/bin/relaydeck" in rig.text
    assert plist_of(rig)["ProgramArguments"][0] == "/synthetic/other/relaydeck"
    assert ["launchctl", "bootout"] == rig.launchd.calls[-3][:2] or any(
        call[1] == "bootout" for call in rig.launchd.calls
    )


def test_declining_the_login_service_is_configure_only_and_skips_wait_and_pairing(
    rig: SetupRig,
) -> None:
    rig.pairs = 0

    def pair(_ctx: Any) -> int:
        rig.pairs += 1
        return 0

    report = rig.run(steps=[LoginServiceStep(), WaitStep(), PairStep()], pair=pair)
    assert report.exit_code == 0 and not service.agent_path(rig.home).exists()
    assert "relaydeck start --connect-herdr" in rig.text and "relaydeck pair" in rig.text
    assert "configured, not running" in rig.text and rig.pairs == 0
    assert rig.routes == [] and rig.launchd.calls == []


def second_profile(rig: SetupRig) -> Path:
    """A second, complete-looking profile with its own folder, host id, port and state."""
    home = rig.home / "profile-b"
    home.mkdir(mode=0o700)
    (home / "state").mkdir(mode=0o700)
    config = home / "companion.toml"
    config.write_text(
        "\n".join(
            [
                'advertised_origin = "https://b.example.ts.net:8446"',
                'host_id = "mac-b"',
                "port = 8126",
                "tailscale_https_port = 8446",
                f'state_dir = "{home / "state"}"',
                f'cookie_secret_file = "{home / "cookie"}"',
                f'cursor_secret_file = "{home / "cursor"}"',
                f'connector_credentials = "{home / "connector.json"}"',
                "",
            ]
        )
    )
    config.chmod(0o600)
    return config


def test_a_profile_other_than_the_default_is_carried_into_the_agent(rig: SetupRig) -> None:
    profile = second_profile(rig)
    rig.config_path = profile
    rig.companion.config_path = profile
    rig.answers.rules = {"Start RelayDeck at login": True}
    assert rig.run(steps=[LoginServiceStep()]).exit_code == 0, rig.text
    assert plist_of(rig)["ProgramArguments"][:3] == [
        "/synthetic/bin/relaydeck",
        "--config",
        str(profile),
    ]


def test_operating_on_one_profile_never_changes_or_starts_the_other(rig: SetupRig) -> None:
    profile = second_profile(rig)
    other_files = {path: path.read_bytes() for path in profile.parent.rglob("*") if path.is_file()}
    other_tree = sorted(str(path) for path in profile.parent.rglob("*"))
    rig.answers.rules = {"Re-enroll": True, "Start RelayDeck at login": True}
    rig.run(steps=[LoginServiceStep(), WaitStep()])
    assert {p: p.read_bytes() for p in profile.parent.rglob("*") if p.is_file()} == other_files
    assert sorted(str(path) for path in profile.parent.rglob("*")) == other_tree
    assert plist_of(rig)["ProgramArguments"] == [
        "/synthetic/bin/relaydeck",
        "service-run",
        "--log-directory",
        str(service.log_dir(rig.home)),
    ]


# -- waiting --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, True),
        ({"state": "starting"}, False),
        ({"state": "stopping"}, False),
        ({"state": "failed", "failed": True}, False),
        ({"connector_ready": False}, False),
        ({"api_ready": False}, False),
        ({"pairing_available": False}, False),
        ({"synthetic": True}, False),
        ({"advertised_origin": "https://elsewhere.example.ts.net"}, False),
        ({"local_endpoint": "http://127.0.0.1:9"}, False),
        ({"instance": None}, False),
    ],
)
def test_the_readiness_predicate_uses_the_real_report_shape(
    rig: SetupRig, changes: dict[str, Any], expected: bool
) -> None:
    config = load_config(rig.config_path)
    body = healthy_body(config, **changes)
    assert running.ready(body, config) is expected


def test_the_healthy_published_state_is_setup_required(rig: SetupRig) -> None:
    config = load_config(rig.config_path)
    assert healthy_body(config)["state"] == "setup_required"
    assert running.ready({"state": "stopped"}, config) is False
    assert running.ready({"state": "unresponsive", "instance": "x"}, config) is False


def test_wait_polls_until_ready_then_probes_the_private_route_once(rig: SetupRig) -> None:
    config = load_config(rig.config_path)
    bodies = [{"state": "stopped"}, {"state": "starting"}, healthy_body(config)]
    calls = 0

    def state(_dir: Path) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return bodies[min(calls - 1, 2)]

    assert rig.run(steps=[WaitStep()], companion_state=state).exit_code == 0
    assert calls == 3 and rig.routes == [config.advertised_origin]
    assert f"Private route: reachable at {config.advertised_origin}" in rig.said


def test_wait_gives_up_after_the_deadline_naming_the_log(rig: SetupRig) -> None:
    start = rig.clock.t
    report = rig.run(steps=[WaitStep()], companion_state=lambda _d: {"state": "starting"})
    assert report.exit_code == 1 and rig.routes == []
    assert 45.0 <= rig.clock.t - start <= 46.0
    assert "~/Library/Logs/RelayDeck/companion.err.log" in rig.text
    assert "otherwise set up" in rig.text


def test_a_failing_route_probe_is_reported_as_not_reachable(rig: SetupRig) -> None:
    config = load_config(rig.config_path)
    report = rig.run(
        steps=[WaitStep()],
        companion_state=lambda _d: healthy_body(config),
        route_probe=lambda _url: {"state": "failed", "message": "tailscale-detail"},
    )
    assert report.exit_code == 1 and "tailscale-detail" not in rig.text


# -- pairing ----------------------------------------------------------------------------------


def test_pairing_is_asked_only_after_a_running_companion(rig: SetupRig) -> None:
    config = load_config(rig.config_path)
    seen: list[int] = []

    def pair_as(number: int) -> Any:
        def pair(_ctx: Any) -> int:
            seen.append(number)
            return 0

        return pair

    rig.answers.rules = {"Pair a device now": True}
    rig.run(
        steps=[WaitStep(), PairStep()],
        companion_state=lambda _d: healthy_body(config),
        pair=pair_as(1),
    )
    assert seen == [1] and rig.answers.count("Pair a device now") == 1
    rig.said.clear()
    rig.run(steps=[PairStep()], pair=pair_as(2))
    assert seen == [1] and "not running" in rig.text


# -- the runner -----------------------------------------------------------------------------------


class Recording(Step):
    def __init__(self, name: str, *, core: bool, code: int = 0, state: str = "todo") -> None:
        self.name = name
        self.core = core
        self.code = code
        self.state = state
        self.ran = False

    def check(self, ctx: Any) -> Status:
        return Status(self.state, f"{self.name} state")

    def plan(self, ctx: Any) -> Plan:
        return Plan()

    def apply(self, ctx: Any) -> Outcome:
        self.ran = True
        return Outcome((f"{self.name} ran",), exit_code=self.code)


def test_a_failing_core_step_stops_the_run_and_lists_what_was_not_reached(rig: SetupRig) -> None:
    first, second, third = (
        Recording("First", core=True, code=1),
        Recording("Second", core=True),
        Recording("Third", core=False),
    )
    report = rig.run(steps=[first, second, third])
    assert report.exit_code == 1 and first.ran and not second.ran and not third.ran
    assert "Not reached: Second, Third." in rig.text and "stopped at 'First'" in rig.text


def test_a_failing_optional_step_is_reported_and_the_run_goes_on(rig: SetupRig) -> None:
    first, second = Recording("First", core=False, code=1), Recording("Second", core=True)
    report = rig.run(steps=[first, second])
    assert report.exit_code == 1 and second.ran and "need attention: First" in rig.text


@pytest.mark.parametrize("state", ["blocked", "conflict"])
def test_a_blocked_or_conflicted_core_step_stops_without_applying(
    rig: SetupRig, state: str
) -> None:
    first, second = Recording("First", core=True, state=state), Recording("Second", core=True)
    assert rig.run(steps=[first, second]).exit_code == 1
    assert not first.ran and not second.ran and "First state" in rig.text


def test_an_uncertain_outcome_stops_even_an_optional_step(rig: SetupRig) -> None:
    class Uncertain(Recording):
        def apply(self, ctx: Any) -> Outcome:
            return Outcome(("maybe",), exit_code=1, uncertain=True)

    later = Recording("Later", core=False)
    assert rig.run(steps=[Uncertain("Unsure", core=False), later]).exit_code == 1
    assert not later.ran


def test_an_interrupt_reports_so_far_and_exits_130(rig: SetupRig) -> None:
    class Interrupted(Recording):
        def apply(self, ctx: Any) -> Outcome:
            raise KeyboardInterrupt

    done = Recording("Done", core=False)
    report = rig.run(steps=[done, Interrupted("Busy", core=True), Recording("After", core=True)])
    assert report.exit_code == 130 and done.ran
    assert "Interrupted during 'Busy'" in rig.text


def test_an_interrupt_in_the_private_step_names_what_was_already_written(
    rig: SetupRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(rig.config_path)
    for path in (
        config.cookie_secret_file,
        config.cursor_secret_file,
        config.connector_credentials,
        config.state_dir / "hosts.json",
    ):
        path.unlink()
    monkeypatch.setattr(
        "relaydeck.companion.setup.private_step.publish_keys",
        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    (rig.home / ".config/relaydeck/connector_key.json").unlink()
    rig.answers.rules = {"Create these private files": True}
    report = rig.run(steps=[PrerequisitesStep(), PrivateFilesStep()])
    assert report.exit_code == 130
    assert "Already changed before the interrupt:" in rig.text
    assert "Private files: cookie secret" in rig.text and "Private files: cursor secret" in rig.text
    assert "connector key" not in rig.text.split("Already changed")[1].split("Interrupted")[0]


def test_an_interrupt_before_any_write_says_nothing_was_changed(rig: SetupRig) -> None:
    class Interrupted(Recording):
        def apply(self, ctx: Any) -> Outcome:
            raise KeyboardInterrupt

    rig.run(steps=[Interrupted("Busy", core=True)])
    assert "Nothing had been changed by this run" in rig.text


def test_the_fixed_step_list_is_in_the_planned_order() -> None:
    assert [step.name for step in default_steps()] == [
        "Prerequisites",
        "Private files",
        "Agent kinds",
        "Update check",
        "Usage sources",
        "Plan limits",
        "Probe folder trust",
        "Limits hook",
        "Tailscale Serve",
        "Web app",
        "Start at login",
        "Wait until running",
        "Pair a device",
    ]


def test_the_final_report_says_how_device_sign_ins_are_kept(rig: SetupRig) -> None:
    rig.run(steps=[Recording("One", core=True)])
    assert "kept in memory" in rig.text
    rig.said.clear()
    rig.environ["RELAYDECK_DURABLE_SIGNINS"] = "1"
    rig.run(steps=[Recording("One", core=True)])
    assert "stored in the Keychain" in rig.text


_ = Serve
