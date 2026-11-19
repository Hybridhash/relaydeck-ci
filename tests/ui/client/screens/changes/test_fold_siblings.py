"""Folding one directory never reopens another: rows are cached, their callbacks must not be."""

from __future__ import annotations

import pytest

from tests.support.git_patches import two_hunk_patch
from tests.support.host_harness import run
from tests.ui.git.gateway import FileSpec, Gateway
from tests.ui.git.harness import click, present, render

PATHS = ["a/f0.py", "a/f1.py", "b/f2.py", "b/f3.py"]


def shown(app: object) -> list[bool]:
    return [present(app, f"changes-file-{i}") for i in range(4)]  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("first", "second"), [("changes-dir-3", "changes-dir-0"), ("changes-dir-0", "changes-dir-1")]
)
def test_sibling_directories_fold_independently_in_either_order(first: str, second: str) -> None:
    gateway = Gateway([FileSpec(p, two_hunk_patch(p)) for p in PATHS])

    async def scenario() -> None:
        app = await render(gateway, wide=True)
        await app.wait_for("changes-dir-3")
        assert shown(app) == [True] * 4
        await click(app, first)
        folded_first = shown(app)
        assert folded_first.count(False) == 2 and folded_first.count(True) == 2
        await click(app, second)
        assert shown(app) == [False] * 4  # both stay folded
        await click(app, "changes-dir-0")  # and opening one leaves the other folded
        assert shown(app).count(True) == 2

    run(scenario())
