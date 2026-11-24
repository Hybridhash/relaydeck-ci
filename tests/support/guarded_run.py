"""Run a snippet in a child interpreter under a hard time limit, for reads that could block."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[2] / "src")


def run_guarded(code: str, *args: str, limit_s: float = 2.0) -> str:
    """Stdout of `python -c code args...`; a child still running after `limit_s` fails the test."""
    env = {**os.environ, "PYTHONPATH": SRC}
    done = subprocess.run(  # noqa: S603 - fixed interpreter, test-only snippet
        [sys.executable, "-I", "-c", code, *args],
        capture_output=True,
        text=True,
        timeout=limit_s,
        env=env,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-400:]
    return done.stdout.strip()
