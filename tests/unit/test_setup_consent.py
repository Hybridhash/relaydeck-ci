"""`--yes` answers the Serve question and the install/open questions for a missing prerequisite.
Every other question is still asked, and a refusal of one is respected."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from relaydeck.companion.inspection.detect import DetectReport
from tests.support.companion.setup_sources import row
from tests.support.setup.herdr_world import HerdrWorld, tailscale_state
from tests.support.setup.rig import SetupRig


def detected(*kinds: str) -> Any:
    rows = tuple(row(kind, kind.title(), next_step="can be added") for kind in kinds)
    return lambda _path: DetectReport(rows, "ok", "ok")


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SetupRig:
    return SetupRig(tmp_path, monkeypatch)


def test_yes_still_asks_every_choice_that_is_not_serve_or_a_remedy(rig: SetupRig) -> None:
    rig.answers.rules = {
        "Create these private files": True,
        "Install the web app": True,
        "Start RelayDeck at login": True,
    }
    report = rig.run(assume_yes=True, source_detect=detected("kind-a"))
    asked = "\n".join(rig.answers.asked)
    for fragment in (
        "Create these private files",  # files and enrollment
        "Allow these agents",  # agent kinds
        "Check daily for RelayDeck updates",
        "Install the web app",
        "Start RelayDeck at login",  # the login service
        "Pair a device now",
    ):
        assert fragment in asked, fragment
    assert rig.serve.mutations  # Serve alone was answered by --yes
    assert report.exit_code in (0, 1)


def test_yes_does_not_answer_for_the_owner_when_they_decline(rig: SetupRig) -> None:
    rig.answers.rules = {"Create these private files": True}
    rig.run(assume_yes=True, source_detect=detected("kind-a"))
    config = rig.config_path.read_text()
    assert "kind-a" not in config.split("agent_kinds", 1)[-1].split("\n", 1)[0]


def test_yes_answers_the_remedy_questions(rig: SetupRig) -> None:
    world = HerdrWorld(rig)
    serve = tailscale_state("Stopped")
    rig.remedies.allow_open()
    rig.run(assume_yes=True, probes=world.probes(serve), runner=serve, steps=None)
    assert rig.answers.count("Tailscale is not running") == 0
    assert rig.remedies.opened == [["-a", "Tailscale"]]
