"""Heading-only golden changes preserve every preceding rendering property."""

import hashlib
import json
from pathlib import Path

import pytest

from tests.support.ui.heading_semantics import without_heading_annotations

ROOT = Path(__file__).resolve().parents[3]
PROOF = json.loads((ROOT / "tests/fixtures/design/heading_rendering.json").read_text())


@pytest.mark.parametrize("path,expected", sorted(PROOF.items()), ids=lambda item: str(item))
def test_heading_golden_retains_its_original_rendering(path: str, expected: str) -> None:
    tree = json.loads((ROOT / path).read_text())
    projected = without_heading_annotations(tree)
    assert projected != tree
    text = json.dumps(projected, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    assert hashlib.sha256(text.encode()).hexdigest() == expected
