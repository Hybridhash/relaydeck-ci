"""Read the published companion status without changing it."""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any

from relaydeck.host.files.private import check_private, read_private

STALE_S = 3.0


def status(directory: Path) -> dict[str, Any]:
    """Lock ownership is authoritative; PID is diagnostic only."""
    if not directory.exists():
        return {"state": "stopped"}
    check_private(directory, directory=True)
    path = directory / "companion.lock"
    if not path.exists():
        return {"state": "stopped"}
    check_private(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                body = json.loads(read_private(directory / "companion-status.json"))
            except (ValueError, OSError):
                return {"state": "starting"}
            if not isinstance(body, dict) or body.get("version") != 1:
                return {"state": "unresponsive"}
            heartbeat = body.get("heartbeat")
            if not isinstance(heartbeat, int | float) or abs(time.time() - heartbeat) > STALE_S:
                return {**body, "state": "unresponsive"}
            return body
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return {"state": "stopped"}
    finally:
        os.close(fd)
