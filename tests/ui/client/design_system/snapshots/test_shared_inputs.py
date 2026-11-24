"""Text input callers retain their fields, selection and native layout."""

from pathlib import Path

import pytest

from tests.support.host_harness import run
from tests.support.ui.input_cases import CASES, render_input
from tests.support.ui.snapshot import snapshot_scenario

GOLDEN = Path(__file__).parents[3] / "snapshots" / "golden/shared-inputs"


@pytest.mark.parametrize("case", CASES)
def test_input_caller_snapshot(case: str) -> None:
    run(snapshot_scenario(lambda: render_input(case), GOLDEN, case))
