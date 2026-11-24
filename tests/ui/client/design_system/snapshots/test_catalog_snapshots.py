"""Stable control trees for every catalog presentation."""

from __future__ import annotations

from pathlib import Path

import pytest

from relaydeck.devtools.catalog_model import Case, cases
from tests.support.catalog.rendering import render
from tests.support.host_harness import run
from tests.support.ui.snapshot import snapshot_scenario

GOLDEN = Path(__file__).parents[3] / "snapshots" / "golden/catalog"


@pytest.mark.parametrize("case", cases(), ids=lambda case: case.key)
def test_catalog_snapshot(case: Case) -> None:
    run(snapshot_scenario(lambda: render(case), GOLDEN / case.entry, case.key))
