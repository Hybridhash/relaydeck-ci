"""Synthetic, key-aware snapshot recovery; no automatic restore or command replay."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from relaydeck.gateway.features.access.identity import SessionStore
from relaydeck.host.persistence.command_journal import CommandJournal
from relaydeck.host.persistence.contracts import KeyUnavailable, Record, StorageUnavailable, Table
from relaydeck.host.persistence.sqlite_store import PersistentStore, open_store
from tests.support.persistence.persistence import Config, MemoryKeys

NOW = 1_900_000_000_000


def snapshot(source: Path, destination: Path) -> None:
    destination.parent.mkdir(mode=0o700, exist_ok=True)
    with sqlite3.connect(source) as reader, sqlite3.connect(destination) as target:
        reader.backup(target)
    destination.chmod(0o600)


def test_live_snapshot_includes_committed_wal_but_not_an_open_transaction(tmp_path: Path) -> None:
    keys = MemoryKeys()
    config = Config(tmp_path / "live")
    store = open_store(config, keys)
    committed = Record(Table.REVOCATIONS, "committed", ("account",), b"kept", 7, NOW)
    pending = Record(Table.REVOCATIONS, "pending", ("account",), b"not-in-snapshot", 1, NOW)
    restored_config = Config(tmp_path / "restore")
    try:
        with store.transaction() as tx:
            tx.put(committed, expected_revision=None)
        assert (config.state_dir / "persistence.db-wal").stat().st_size > 0
        with store.transaction() as tx:
            tx.put(pending, expected_revision=None)
            snapshot(
                config.state_dir / "persistence.db", restored_config.state_dir / "persistence.db"
            )
    finally:
        store.close()
    restored = open_store(restored_config, keys)
    try:
        with restored.transaction() as tx:
            assert tx.get(Table.REVOCATIONS, "committed") == committed
            assert tx.get(Table.REVOCATIONS, "pending") is None
        assert restored_config.state_dir.stat().st_mode & 0o077 == 0
        assert all(path.stat().st_mode & 0o077 == 0 for path in restored_config.state_dir.iterdir())
        assert not any(keys.prompts)
    finally:
        restored.close()


@pytest.mark.parametrize("failure", ["missing", "locked", "wrong", "schema", "truncated"])
def test_unusable_snapshot_fails_closed_without_replacing_keys(
    tmp_path: Path, failure: str
) -> None:
    keys = MemoryKeys()
    config = Config(tmp_path / "live")
    store = open_store(config, keys)
    with store.transaction() as tx:
        tx.put(
            Record(Table.REVOCATIONS, "row", ("account",), b"private", 1, NOW),
            expected_revision=None,
        )
    target = Config(tmp_path / "restore")
    snapshot(config.state_dir / "persistence.db", target.state_dir / "persistence.db")
    key_id = store._key_ids["authority"]
    store.close()
    path = target.state_dir / "persistence.db"
    if failure == "missing":
        del keys.keys[key_id]
    elif failure == "locked":
        keys.locked = True
    elif failure == "wrong":
        keys.keys[key_id] = b"x" * 32
    elif failure == "schema":
        with sqlite3.connect(path) as db:
            db.execute("PRAGMA user_version=999")
        db.close()
    else:
        path.write_bytes(b"not a SQLite snapshot")
    keys_before = dict(keys.keys)
    before = path.read_bytes()
    reopened = None
    try:
        with pytest.raises(StorageUnavailable):
            reopened = open_store(target, keys)
            with reopened.transaction() as tx:
                tx.get(Table.REVOCATIONS, "row")
    finally:
        if reopened is not None:
            reopened.close()
    assert keys.keys == keys_before
    assert path.read_bytes() == before
    assert not any(keys.prompts)


def test_stale_authority_restore_requires_generation_advance(tmp_path: Path) -> None:
    keys = MemoryKeys()
    keys.restore_generation("authentication", create=True)
    config = Config(tmp_path / "state")
    with patch.object(PersistentStore, "_now_ms", return_value=NOW):
        disk = open_store(config, keys)
        sessions = SessionStore(
            persistence=disk,
            origin="https://synthetic",
            generation_reader=lambda: keys.restore_generation("authentication").value,
        )
        access, refresh = sessions.create("account", ["sessions.read"], ["host"], NOW, "device")
        backup = tmp_path / "snapshot/persistence.db"
        snapshot(config.state_dir / "persistence.db", backup)
        assert sessions.revoke_device("account", "device") == 1
        disk.close()
        keys.advance_restore_generation("authentication")
        shutil.copyfile(backup, config.state_dir / "persistence.db")
        disk = open_store(config, keys)
        try:
            sessions = SessionStore(
                persistence=disk,
                origin="https://synthetic",
                generation_reader=lambda: keys.restore_generation("authentication").value,
            )
            assert sessions.lookup(access, NOW) is None
            assert sessions.refresh(refresh, NOW) is None
            with patch.object(keys, "restore_generation", side_effect=KeyUnavailable()):
                with pytest.raises((StorageUnavailable, KeyUnavailable)):
                    SessionStore(
                        persistence=disk,
                        origin="https://synthetic",
                        generation_reader=lambda: keys.restore_generation("authentication").value,
                    )
        finally:
            disk.close()


def test_stale_command_snapshot_cannot_prove_a_write_was_not_sent(tmp_path: Path) -> None:
    path = tmp_path / "commands.db"
    journal = CommandJournal(path, clock=lambda: NOW)
    journal.submit(
        command_id="uncertain",
        principal="account",
        host_id="host",
        kind="shell.create",
        target={"workspace_id": "w"},
        payload={},
        expires_ms=NOW + 120000,
        now=NOW,
    )
    snapshot(path, tmp_path / "snapshot/commands.db")
    assert journal.mark_dispatched("uncertain", NOW)
    journal.close()
    # The latest durable journal proves uncertainty and must not redispatch.
    latest = CommandJournal(path, clock=lambda: NOW)
    try:
        assert latest.recover(now=NOW)["unknown"] == ["uncertain"]
    finally:
        latest.close()
    stale = CommandJournal(tmp_path / "snapshot/commands.db", clock=lambda: NOW)
    try:
        # A stale backup lacks that evidence. It is NOT a supported replay authority.
        assert stale.record("uncertain")["status"] == "accepted"
    finally:
        stale.close()
