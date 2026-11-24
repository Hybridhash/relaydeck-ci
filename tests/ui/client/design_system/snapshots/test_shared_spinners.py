"""Pending operation indicators preserve their native caller output."""

from pathlib import Path

import pytest

from tests.support.host_harness import run
from tests.support.ui.snapshot import snapshot_scenario
from tests.support.ui.spinner_cases import CASES, render_spinner

GOLDEN = Path(__file__).parents[3] / "snapshots" / "golden/shared-spinners"


@pytest.mark.parametrize("case", CASES)
def test_spinner_caller_snapshot(case: str) -> None:
    run(snapshot_scenario(lambda: render_spinner(case), GOLDEN, case))
