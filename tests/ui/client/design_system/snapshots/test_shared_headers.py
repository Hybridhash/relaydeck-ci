"""Screen headings keep their caller geometry and typography."""

from pathlib import Path

import pytest

from tests.support.host_harness import run
from tests.support.ui.header_cases import CASES, render_heading
from tests.support.ui.snapshot import snapshot_scenario

GOLDEN = Path(__file__).parents[3] / "snapshots" / "golden/shared-headers"


@pytest.mark.parametrize("case", CASES)
def test_heading_caller_snapshot(case: str) -> None:
    run(snapshot_scenario(lambda: render_heading(case), GOLDEN, case))
