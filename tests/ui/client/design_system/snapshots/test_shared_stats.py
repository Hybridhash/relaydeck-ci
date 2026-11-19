"""Metric tile callers retain their layout and text."""

from pathlib import Path

import pytest

from tests.support.host_harness import run
from tests.support.ui.snapshot import snapshot_scenario
from tests.support.ui.stat_cases import CASES, render_stats

GOLDEN = Path(__file__).parents[3] / "snapshots" / "golden/shared-stats"


@pytest.mark.parametrize("case", CASES)
def test_stat_caller_snapshot(case: str) -> None:
    run(snapshot_scenario(lambda: render_stats(case), GOLDEN, case))
