"""Synthetic unified-diff patches for tests."""

from __future__ import annotations


def added_patch(path: str, rows: int) -> bytes:
    """A new file of `rows` lines: one hunk, so a long file pages as fragments."""
    head = [
        f"diff --git a/{path} b/{path}",
        "new file mode 100644",
        "index 0000000..2222222",
        "--- /dev/null",
        f"+++ b/{path}",
        f"@@ -0,0 +1,{rows} @@",
    ]
    return ("\n".join([*head, *(f"+line {n:05d}" for n in range(rows))]) + "\n").encode()


def two_hunk_patch(path: str) -> bytes:
    """A modified file with two short hunks, each whole on one page."""
    lines = [
        f"diff --git a/{path} b/{path}",
        "index 1111111..2222222 100644",
        f"--- a/{path}",
        f"+++ b/{path}",
        "@@ -1,3 +1,3 @@",
        " first",
        "-second old",
        "+second new",
        " third",
        "@@ -20,3 +20,4 @@",
        " twenty",
        "-twenty-one old",
        "+twenty-one new",
        "+twenty-one extra",
        " twenty-two",
    ]
    return ("\n".join(lines) + "\n").encode()
