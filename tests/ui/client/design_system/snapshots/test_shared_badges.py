"""Badge callers retain their native frames and text."""

from pathlib import Path

import pytest

from tests.support.host_harness import run
from tests.support.ui.badge_cases import CASES, render_badges
from tests.support.ui.snapshot import snapshot_scenario

GOLDEN = Path(__file__).parents[3] / "snapshots" / "golden/shared-badges"


@pytest.mark.parametrize("case", CASES)
def test_badge_caller_snapshot(case: str) -> None:
    run(snapshot_scenario(lambda: render_badges(case), GOLDEN, case))
