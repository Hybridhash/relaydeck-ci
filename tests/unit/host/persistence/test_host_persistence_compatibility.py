"""Open synthetic persisted data written before the host namespace move."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import plistlib
import sqlite3
import stat
from pathlib import Path
from typing import Any

import pytest

from relaydeck.companion.adapters.launchd import plist_bytes
from relaydeck.host.files.descriptors import read_private_path
from relaydeck.host.persistence.command_journal import CommandJournal
from relaydeck.host.persistence.contracts import DraftTarget, Record, Table
from relaydeck.host.persistence.sqlite_store import PersistentStore, open_store
from tests.support.persistence.persistence import Config, MemoryKeys

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "host_persistence_compatibility"


def _snapshot(path: Path) -> dict[str, tuple[int, str]]:
    """Count and hash every SQL row, including metadata, revisions and nonce history."""
    db = sqlite3.connect(f"file:{path}?mode=rw", uri=True)
    try:
        names = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        result = {}
        for (name,) in names.fetchall():
            quoted = '"' + name.replace('"', '""') + '"'
            rows = sorted(repr(tuple(row)) for row in db.execute(f"SELECT * FROM {quoted}"))  # noqa: S608 - quoted database table names
            digest = hashlib.sha256()
            for row in rows:
                digest.update(row.encode())
                digest.update(b"\n")
            result[name] = len(rows), digest.hexdigest()
        return result
    finally:
        db.close()


def _restore(tmp_path: Path) -> tuple[Path, dict[str, Any], MemoryKeys]:
    expected = json.loads((FIXTURE / "expected.json").read_text())
    state = tmp_path / "state"
    state.mkdir(mode=expected["directory_mode"])
    for name, details in expected["files"].items():
        target = state / name
        target.write_bytes(gzip.decompress((FIXTURE / (name + ".gz")).read_bytes()))
        target.chmod(details["mode"])
        assert target.stat().st_size == details["size"]
    keys = MemoryKeys()
    keys.keys = {name: bytes.fromhex(value) for name, value in expected["keys"].items()}
    return state, expected, keys


def test_existing_rows_revisions_and_private_modes_survive_the_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, expected, keys = _restore(tmp_path)
    monkeypatch.setattr(PersistentStore, "_now_ms", lambda _: expected["clock"])
    before = {name: _snapshot(state / name) for name in ("persistence.db", "commands.db")}
    store = open_store(Config(state), keys)
    try:
        # Loading a draft refreshes its access record; opening alone must preserve every row.
        assert _snapshot(state / "persistence.db") == before["persistence.db"]
        with store.transaction() as tx:
            for data in expected["records"]:
                row = Record(
                    table=Table(data["table"]),
                    key=data["key"],
                    scope=tuple(data["scope"]),
                    body=bytes.fromhex(data["body"]),
                    revision=data["revision"],
                    updated_ms=data["updated_ms"],
                    expires_ms=data["expires_ms"],
                    schema_version=data["schema_version"],
                )
                assert tx.get(row.table, row.key) == row
        drafts = store.scoped_drafts("origin", "account", "device", "tab")
        draft = asyncio.run(drafts.load(DraftTarget("host", "terminal")))
        assert draft is not None
        assert draft.snapshot == expected["draft"]["snapshot"]
        assert draft.revision == expected["draft"]["revision"]
        assert draft.updated_ms == expected["draft"]["updated_ms"]
    finally:
        store.close()
    journal = CommandJournal(state / "commands.db", clock=lambda: expected["clock"])
    try:
        assert journal.record("synthetic-command") == expected["command"]
    finally:
        journal.close()
    assert _snapshot(state / "commands.db") == before["commands.db"]
    assert json.loads(read_private_path(state / "private.json", limit=1024)) == {
        "synthetic": "kept"
    }
    assert stat.S_IMODE(state.stat().st_mode) == expected["directory_mode"]
    assert {p.name: stat.S_IMODE(p.stat().st_mode) for p in state.iterdir()} == {
        name: data["mode"] for name, data in expected["files"].items()
    }
    assert not any(keys.prompts)


def test_launchd_retains_the_pre_move_child_arguments_and_environment() -> None:
    legacy = plistlib.loads((FIXTURE / "launchd.plist").read_bytes())
    current = plistlib.loads(
        plist_bytes(
            Path("/synthetic/bin/relaydeck"),
            home=Path("/synthetic/home"),
            path_env="/synthetic/bin:/usr/bin",
            config=Path("/synthetic/config.toml"),
        )
    )
    # The bounded-output launcher is a behavioral addition; the historical fixture is unchanged.
    from relaydeck.companion.adapters.launchd import program_arguments

    assert current.pop("ProgramArguments") == [
        "/synthetic/bin/relaydeck",
        "--config",
        "/synthetic/config.toml",
        "service-run",
        "--log-directory",
        "/synthetic/home/Library/Logs/RelayDeck",
    ]
    assert program_arguments(
        Path("/synthetic/bin/relaydeck"), Path("/synthetic/config.toml")
    ) == legacy.pop("ProgramArguments")
    for stream in ("StandardOutPath", "StandardErrorPath"):
        assert current.pop(stream) == "/dev/null"
        legacy.pop(stream)
    assert current == legacy
