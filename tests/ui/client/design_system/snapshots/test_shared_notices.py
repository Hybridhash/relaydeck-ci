"""Notices retain their native bodies, layout and available actions."""

from pathlib import Path

import pytest

from tests.support.host_harness import run
from tests.support.ui.notice_cases import CASES, render_notice
from tests.support.ui.snapshot import snapshot_scenario

GOLDEN = Path(__file__).parents[3] / "snapshots" / "golden/shared-notices"


@pytest.mark.parametrize("case", CASES)
def test_notice_caller_snapshot(case: str) -> None:
    run(snapshot_scenario(lambda: render_notice(case), GOLDEN, case))
