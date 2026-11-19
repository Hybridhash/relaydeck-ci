"""Explicitly loaded diffs survive a return only within their original identity."""

from __future__ import annotations

from typing import Any

import pytest

from tests.support.host_harness import run
from tests.ui.git.gateway import FileSpec, Gateway
from tests.ui.git.harness import click, present, render, scroll_until_built


@pytest.mark.parametrize("later", [False, True])
def test_return_preserves_explicit_diff_load_and_reading_page(later: bool) -> None:
    patch = (
        b"diff --git a/pkg/a.py b/pkg/a.py\n--- a/pkg/a.py\n+++ b/pkg/a.py\n"
        b"@@ -1,1 +1,1 @@\n-old\n+new\n@@ -20,0 +20,1100 @@\n"
        + b"".join(f"+line {i}\n".encode() for i in range(1100))
        + b"@@ -2000,1 +3100,1 @@\n-before\n+after\n"
    )
    sent: list[tuple[Any, ...]] = []

    def ask(*args: Any) -> bool:
        sent.append(args)
        return True

    async def scenario() -> None:
        gateway = Gateway([FileSpec("pkg/a.py", patch)])
        app = await render(gateway, wide=True, on_ask=ask)
        await app.wait_for("changes-file-0")
        await click(app, "changes-file-0")
        await app.wait_for("diff-load")
        await click(app, "diff-load")
        await app.wait_for("diff-hunk-0-0")
        page, hunk = 0, 0
        if later:
            await click(app, "diff-more")
            await app.settle()
            await click(app, "diff-more")
            page, hunk = 2, 1
        await scroll_until_built(app, "diff-rows", f"diff-hunk-{page}-{hunk}")
        await click(app, f"diff-hunk-{page}-{hunk}")
        await click(app, "diff-ask")
        await click(app, "changes-ask-send")
        bookmark = sent[0][2]
        assert bookmark.hunk == (page, hunk) and bookmark.anchors[-1].page == page
        before = len(gateway.calls)
        restored = await render(gateway, wide=True, on_ask=ask, bookmark=bookmark)
        await scroll_until_built(restored, "diff-rows", f"diff-hunk-{page}-{hunk}")
        assert not present(restored, "diff-load") and not restored.find("diff-ask").disabled
        diff_calls = [call for call in gateway.calls[before:] if call[0] == "diff"]
        assert diff_calls[0][2:] == (f"pg:{page}" if later else None, True)
        gateway.edit()
        changed = await render(gateway, wide=True, on_ask=ask, bookmark=bookmark)
        await changed.wait_for("diff-load")
        assert present(changed, "changes-reading-notice")

    run(scenario())
