"""First-serve metadata uses the existing encrypted store and transaction rules."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from relaydeck.host.persistence.contracts import Record, StorageUnavailable, Table
from relaydeck.host.persistence.sqlite_store import PersistentStore, _json
from tests.support.persistence.persistence import Config, MemoryKeys

ORIGIN = "https://rollout.example"
VERSION = "0.3.0"
NOW = 1_800_000_000_000


def test_first_writer_survives_restart_rotation_and_retention(tmp_path: Path) -> None:
    keys = MemoryKeys()
    config = Config(tmp_path / "state")
    store = PersistentStore(config, keys)
    try:
        assert store.first_web_serve(ORIGIN, VERSION, NOW) == NOW
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert (
                list(
                    pool.map(
                        lambda n: store.first_web_serve(ORIGIN, VERSION, n), range(NOW + 1, NOW + 9)
                    )
                )
                == [NOW] * 8
            )
        assert store.first_web_serve(ORIGIN, "0.3.1", NOW + 1) == NOW
        assert store.first_web_serve("https://other.example", VERSION, NOW + 2) == NOW + 2
        with store.transaction() as tx:
            row = tx.get(Table.WEB_ROLLOUTS, _json([ORIGIN]).decode())
        assert row is not None and row.expires_ms is None and row.scope == (ORIGIN,)
        assert json.loads(row.body) == {"first_serve_ms": NOW}
        assert (
            store._db.execute(
                "SELECT key_id FROM records WHERE table_name=?", (Table.WEB_ROLLOUTS.value,)
            ).fetchone()[0]
            == store._key_ids["web"]
        )
        store.rotate_keys()
        store.sweep(NOW + 365 * 86_400_000)
    finally:
        store.close()
    restored = PersistentStore(config, keys)
    try:
        assert restored.first_web_serve(ORIGIN, VERSION, NOW + 365 * 86_400_000) == NOW
        assert restored.status().available
    finally:
        restored.close()


@pytest.mark.parametrize(
    "body",
    [
        b"bad",
        b"{}",
        b"[]",
        b'{"first_serve_ms":true}',
        b'{"first_serve_ms":-1}',
        b'{"first_serve_ms":1,"extra":0}',
        b'{"first_serve_ms":9223372036854775808}',
        b'{"first_serve_ms":1,"first_serve_ms":2}',
    ],
)
def test_invalid_readable_timestamp_replaced_with_revision_check(
    tmp_path: Path, body: bytes
) -> None:
    store = PersistentStore(Config(tmp_path / "state"), MemoryKeys())
    try:
        key = _json([ORIGIN]).decode()
        with store.transaction() as tx:
            tx.put(
                Record(Table.WEB_ROLLOUTS, key, (ORIGIN,), body, 4, NOW),
                expected_revision=None,
            )
        assert store.first_web_serve(ORIGIN, VERSION, NOW) == NOW
        with store.transaction() as tx:
            row = tx.get(Table.WEB_ROLLOUTS, key)
        assert row is not None and row.revision == 5
        assert json.loads(row.body) == {"first_serve_ms": NOW}
    finally:
        store.close()


def test_corrupt_rollout_reset_does_not_touch_other_metadata_or_authority(tmp_path: Path) -> None:
    store = PersistentStore(Config(tmp_path / "state"), MemoryKeys())
    try:
        assert store.first_web_serve(ORIGIN, VERSION, NOW) == NOW
        assert store.first_web_serve("https://other.example", VERSION, NOW) == NOW
        with store.transaction() as tx:
            tx.put(
                Record(
                    Table.WEB_SESSIONS,
                    "authority",
                    (ORIGIN, "a", "d"),
                    b"protected",
                    1,
                    NOW,
                    NOW + 1000,
                ),
                expected_revision=None,
            )
            lookup = tx._lookup(Table.WEB_ROLLOUTS, _json([ORIGIN]).decode())
        before = [
            tuple(row)
            for row in store._db.execute(
                "SELECT * FROM records WHERE NOT(table_name=? AND lookup=?) "
                "ORDER BY table_name,lookup",
                (Table.WEB_ROLLOUTS.value, lookup),
            )
        ]
        store._db.execute(
            "UPDATE records SET ciphertext=? WHERE table_name=? AND lookup=?",
            (b"damaged", Table.WEB_ROLLOUTS.value, lookup),
        )
        assert store.first_web_serve(ORIGIN, VERSION, NOW + 1) == NOW + 1
        after = [
            tuple(row)
            for row in store._db.execute(
                "SELECT * FROM records WHERE NOT(table_name=? AND lookup=?) "
                "ORDER BY table_name,lookup",
                (Table.WEB_ROLLOUTS.value, lookup),
            )
        ]
        assert after == before
        with store.transaction() as tx:
            assert tx.get(Table.WEB_SESSIONS, "authority").body == b"protected"
            assert len(tx.scan(Table.WEB_ROLLOUTS, scope=(ORIGIN,)).records) == 1
        assert store.status().available and store._fault is None
    finally:
        store.close()


@pytest.mark.parametrize("failure", ["keys", "metadata", "authority"])
def test_rollout_repair_does_not_bypass_other_integrity_checks(
    tmp_path: Path, failure: str
) -> None:
    keys = MemoryKeys()
    store = PersistentStore(Config(tmp_path / "state"), keys)
    try:
        assert store.first_web_serve(ORIGIN, VERSION, NOW) == NOW
        if failure == "keys":
            keys.locked = True
        elif failure == "metadata":
            store._db.execute("UPDATE metadata SET mac=?", (b"damaged",))
        else:
            with store.transaction() as tx:
                tx.put(
                    Record(
                        Table.WEB_SESSIONS,
                        "authority",
                        (ORIGIN, "a", "d"),
                        b"protected",
                        1,
                        NOW,
                        NOW + 1000,
                    ),
                    expected_revision=None,
                )
            store._db.execute(
                "UPDATE records SET ciphertext=? WHERE table_name=?",
                (b"damaged", Table.WEB_SESSIONS.value),
            )
            assert store.first_web_serve(ORIGIN, VERSION, NOW + 1) == NOW
            with pytest.raises(StorageUnavailable, match="corrupt"), store.transaction() as tx:
                tx.get(Table.WEB_SESSIONS, "authority")
            return
        with pytest.raises(StorageUnavailable):
            store.first_web_serve(ORIGIN, VERSION, NOW + 1)
    finally:
        store.close()


@pytest.mark.parametrize("now", [-1, True, 1.5, 2**63])
def test_invalid_injected_instant_is_refused(tmp_path: Path, now: int) -> None:
    store = PersistentStore(Config(tmp_path / "state"), MemoryKeys())
    try:
        with pytest.raises(StorageUnavailable, match="invalid_record"):
            store.first_web_serve(ORIGIN, VERSION, now)
        assert store.status().records == 0
    finally:
        store.close()


def test_concurrent_first_insert_has_one_winner(tmp_path: Path) -> None:
    store = PersistentStore(Config(tmp_path / "state"), MemoryKeys())
    try:
        schema = list(store._db.execute("SELECT name,sql FROM sqlite_master ORDER BY name"))
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(
                pool.map(lambda n: store.first_web_serve(ORIGIN, VERSION, n), range(NOW, NOW + 8))
            )
        assert len(set(results)) == 1 and NOW <= results[0] < NOW + 8
        assert store.status().records == 1
        assert list(store._db.execute("SELECT name,sql FROM sqlite_master ORDER BY name")) == schema
    finally:
        store.close()


def test_failed_repair_rolls_back_row_and_scope_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from relaydeck.host.persistence.sqlite_store import Transaction

    store = PersistentStore(Config(tmp_path / "state"), MemoryKeys())
    try:
        store.first_web_serve(ORIGIN, VERSION, NOW)
        store._db.execute(
            "UPDATE records SET ciphertext=? WHERE table_name=?",
            (b"damaged", Table.WEB_ROLLOUTS.value),
        )
        before = [tuple(r) for r in store._db.execute("SELECT * FROM records")]
        scopes = [tuple(r) for r in store._db.execute("SELECT * FROM record_scopes")]

        def full(*args: object) -> None:
            raise StorageUnavailable("full")

        monkeypatch.setattr(Transaction, "_authority_capacity", full)
        with pytest.raises(StorageUnavailable, match="full"):
            store.first_web_serve(ORIGIN, VERSION, NOW + 1)
        assert [tuple(r) for r in store._db.execute("SELECT * FROM records")] == before
        assert [tuple(r) for r in store._db.execute("SELECT * FROM record_scopes")] == scopes
        assert store._fault is None
    finally:
        store.close()


def test_corrupt_old_key_generation_is_not_repaired_as_rollout_data(tmp_path: Path) -> None:
    store = PersistentStore(Config(tmp_path / "state"), MemoryKeys())
    try:
        store.first_web_serve(ORIGIN, VERSION, NOW)
        old = store._key_ids["web"]
        store._begin_rotation("web")
        before = [tuple(r) for r in store._db.execute("SELECT * FROM records")]
        store._db.execute("UPDATE key_generations SET mac=? WHERE key_id=?", (b"damaged", old))
        with pytest.raises(StorageUnavailable, match="corrupt"):
            store.first_web_serve(ORIGIN, VERSION, NOW + 1)
        assert [tuple(r) for r in store._db.execute("SELECT * FROM records")] == before
        assert store._fault == "corrupt"
    finally:
        store.close()


@pytest.mark.parametrize("count", [3, 130])
def test_existing_version_records_migrate_the_earliest_valid_instant(
    tmp_path: Path, count: int
) -> None:
    store = PersistentStore(Config(tmp_path / "state"), MemoryKeys())
    first = NOW - 2 * 86_400_000
    try:
        with store.transaction() as tx:
            scopes = [(ORIGIN, f"0.2.{i}") for i in range(count)]
            earliest = max(
                scopes, key=lambda scope: tx._lookup(Table.WEB_ROLLOUTS, _json(scope).decode())
            )
            for scope in [*scopes, ("https://other.example", VERSION)]:
                instant = first if scope == earliest else NOW
                tx.put(
                    Record(
                        Table.WEB_ROLLOUTS,
                        _json(scope).decode(),
                        scope,
                        _json({"first_serve_ms": instant}),
                        1,
                        NOW,
                    ),
                    expected_revision=None,
                )
            for version, body, expires in [
                ("bad-json", b"{}", None),
                ("bad-number", b'{"first_serve_ms":true}', None),
                ("expired", _json({"first_serve_ms": 0}), NOW + 1),
            ]:
                scope = (ORIGIN, version)
                tx.put(
                    Record(
                        Table.WEB_ROLLOUTS,
                        _json(scope).decode(),
                        scope,
                        body,
                        1,
                        NOW,
                        expires,
                    ),
                    expected_revision=None,
                )
        before = [
            tuple(r) for r in store._db.execute("SELECT * FROM records ORDER BY table_name,lookup")
        ]
        assert store.first_web_serve(ORIGIN, "0.3.1", NOW + 86_400_000) == first
        assert store.first_web_serve(ORIGIN, "0.3.2", NOW + 2 * 86_400_000) == first
        with store.transaction() as tx:
            canonical = tx.get(Table.WEB_ROLLOUTS, _json([ORIGIN]).decode())
            lookup = tx._lookup(Table.WEB_ROLLOUTS, _json([ORIGIN]).decode())
        assert canonical is not None and canonical.scope == (ORIGIN,)
        assert json.loads(canonical.body) == {"first_serve_ms": first}
        after = [
            tuple(r)
            for r in store._db.execute(
                "SELECT * FROM records WHERE NOT(table_name=? AND lookup=?) "
                "ORDER BY table_name,lookup",
                (Table.WEB_ROLLOUTS.value, lookup),
            )
        ]
        assert after == before
    finally:
        store.close()


def test_persisted_origin_instant_wins_over_later_version_records(tmp_path: Path) -> None:
    store = PersistentStore(Config(tmp_path / "state"), MemoryKeys())
    try:
        assert store.first_web_serve(ORIGIN, VERSION, NOW) == NOW
        scope = (ORIGIN, "0.2.0")
        with store.transaction() as tx:
            tx.put(
                Record(
                    Table.WEB_ROLLOUTS,
                    _json(scope).decode(),
                    scope,
                    _json({"first_serve_ms": NOW - 1000}),
                    1,
                    NOW,
                ),
                expected_revision=None,
            )
        assert store.first_web_serve(ORIGIN, "0.3.1", NOW + 86_400_000) == NOW
    finally:
        store.close()
