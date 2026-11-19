"""Existing native button carriers and their surrounding output stay intact."""

from pathlib import Path

import pytest

from tests.support.host_harness import run
from tests.support.ui.button_cases import CASES, render_buttons
from tests.support.ui.snapshot import snapshot_scenario

GOLDEN = Path(__file__).parents[3] / "snapshots" / "golden/shared-buttons"


@pytest.mark.parametrize("case", CASES)
def test_button_caller_snapshot(case: str) -> None:
    run(snapshot_scenario(lambda: render_buttons(case), GOLDEN, case))
