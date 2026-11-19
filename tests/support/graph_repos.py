"""Disposable history fixtures shared by graph boundary tests."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from relaydeck.git.service import ChangesService
from tests.support.git_identity import AUTHOR_CONFIG, AUTHOR_LINE


def git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *AUTHOR_CONFIG, *args],
        input=data,
        capture_output=True,
        check=True,
    ).stdout.rstrip(b"\n")


def repository(path: Path, count: int = 1, *, object_format: str = "sha1") -> Path:
    path.mkdir()
    git(path, "init", "-q", "-b", "main", f"--object-format={object_format}")
    fast_history(path, count)
    return path


def fast_history(path: Path, count: int, *, ref: str = "refs/heads/main") -> None:
    records = bytearray()
    for index in range(count):
        message = f"Change {index}".encode()
        records.extend(
            f"commit {ref}\ncommitter {AUTHOR_LINE} "
            f"{1700000000 + index} +0000\n"
            f"data {len(message)}\n".encode()
            + message
            + b"\n\n"
        )
    git(path, "fast-import", "--quiet", data=bytes(records))


def commit(path: Path, subject: str = "Update") -> str:
    git(path, "commit", "--allow-empty", "-qm", subject)
    return git(path, "rev-parse", "HEAD").decode()


async def located(service: ChangesService, path: Path, account: str = "account") -> str:
    for _ in range(300):
        rows = await service.summaries(account, "host", {"terminal": os.fspath(path)})
        if rows[0].state == "ok" and not service._workers.flights:
            return rows[0].worktree_id
        await asyncio.sleep(0.01)
    raise AssertionError("repository did not settle")
