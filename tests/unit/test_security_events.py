"""Security records represent committed state and never include credential payloads."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from relaydeck.gateway.features.access.identity import SessionStore
from relaydeck.gateway.features.access.pairing import PairingError
from relaydeck.host.observability import security
from relaydeck.host.observability.logging import UTCFormatter
from relaydeck.host.persistence.contracts import StorageUnavailable
from relaydeck.host.persistence.sqlite_store import open_store
from tests.support.pairing import ORIGIN, foundation
from tests.support.persistence.persistence import Config, MemoryKeys


def lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "relaydeck.security"]


def test_grant_commit_failure_has_no_success_event(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    keys = MemoryKeys()
    keys.restore_generation("authentication", create=True)
    disk = open_store(Config(tmp_path / "state"), keys)
    store = SessionStore(persistence=disk, origin="https://synthetic", generation_reader=lambda: 0)
    try:
        with (
            patch.object(security, "EVENTS", security.SecurityEvents()),
            patch.object(store, "_persist_pair", side_effect=StorageUnavailable("unavailable")),
            pytest.raises(StorageUnavailable),
        ):
            store.create("PRIVATE_ACCOUNT", ["sessions.read"], ["PRIVATE_HOST"], 100)
        assert lines(caplog) == ["security_event event=grant_issued outcome=failed"]
        assert "PRIVATE" not in caplog.text
    finally:
        disk.close()


def test_nested_revoke_is_one_event_and_repetition_is_noop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    store = SessionStore()
    for _ in range(2):
        store.create("PRIVATE_ACCOUNT", ["sessions.read"], ["PRIVATE_HOST"], 100, "PRIVATE_DEVICE")
    caplog.clear()
    assert store.revoke_device("PRIVATE_ACCOUNT", "PRIVATE_DEVICE") == 2
    assert store.revoke_device("PRIVATE_ACCOUNT", "PRIVATE_DEVICE") == 0
    assert lines(caplog) == [
        "security_event event=device_revoked outcome=completed",
        "security_event event=device_revoked outcome=noop",
    ]
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize("approve", [True, False])
def test_pairing_decisions_commit_before_event(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    approve: bool,
) -> None:
    state, clock, body = foundation(tmp_path / "state")
    pairing = state.pairing
    assert pairing is not None
    host = state.hosts.record_for_host("home-mac")
    pairing.open_window(host, ORIGIN, clock[0], ttl_s=300)
    request = pairing.request(body, host, clock[0])
    caplog.set_level(logging.INFO)
    caplog.clear()
    pairing.decide(
        request["request_id"], request["matching_code"], host, ORIGIN, clock[0], approve=approve
    )
    expected = "approved" if approve else "denied"
    assert lines(caplog) == [f"security_event event=pairing_decision outcome={expected}"]
    caplog.clear()
    with patch.object(security, "EVENTS", security.SecurityEvents()), pytest.raises(PairingError):
        pairing.decide("PRIVATE_UNKNOWN", "PRIVATE_CODE", host, ORIGIN, clock[0], approve=True)
    assert lines(caplog) == ["security_event event=pairing_decision outcome=rejected"]
    assert "PRIVATE" not in caplog.text


def test_failure_limiter_has_fixed_cardinality_and_recovers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = [0.0]
    events = security.SecurityEvents(lambda: clock[0])
    caplog.set_level(logging.INFO)
    for _ in range(10000):
        events.emit(security.Event.GRANT_ISSUED, security.Outcome.FAILED)
    assert len(lines(caplog)) == 1 and len(events._last) == 1
    clock[0] += 1
    events.emit(security.Event.GRANT_ISSUED, security.Outcome.FAILED)
    assert len(lines(caplog)) == 2
    with pytest.raises(ValueError):
        events.emit("PRIVATE_EVENT", security.Outcome.COMPLETED)  # type: ignore[arg-type]


def test_broken_logging_does_not_change_a_completed_operation() -> None:
    store = SessionStore()
    with patch.object(security.LOG, "log", side_effect=OSError("PRIVATE_PATH")):
        access, _ = store.create("account", [], [], 100)
    assert store.lookup(access, 100) is not None


def test_timestamps_are_explicit_utc() -> None:
    record = logging.LogRecord("synthetic", logging.INFO, "", 0, "ok", (), None)
    record.created = 0.125
    assert UTCFormatter("%(asctime)s %(message)s").format(record) == "1970-01-01T00:00:00.125Z ok"


def test_durable_direct_revocation_emits_after_commit(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    keys = MemoryKeys()
    keys.restore_generation("authentication", create=True)
    disk = open_store(Config(tmp_path / "state"), keys)
    store = SessionStore(persistence=disk, origin="https://synthetic", generation_reader=lambda: 0)
    caplog.set_level(logging.INFO)
    try:
        access, _ = store.create("account", [], [], 1_900_000_000_000, "device")
        caplog.clear()
        store.begin_revocation(access)
        with disk.transaction() as tx:
            store.revoke_in_transaction(tx, access)
            assert lines(caplog) == []
        store.finish_revocation(store.verifier(access))
        assert lines(caplog) == ["security_event event=session_revoked outcome=completed"]
        store.finish_revocation(store.verifier(access))
        assert len(lines(caplog)) == 1
    finally:
        disk.close()


def test_pairing_write_failure_never_reports_approved(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    state, clock, body = foundation(tmp_path / "state")
    pairing = state.pairing
    assert pairing is not None
    host = state.hosts.record_for_host("home-mac")
    pairing.open_window(host, ORIGIN, clock[0], ttl_s=300)
    request = pairing.request(body, host, clock[0])
    caplog.set_level(logging.INFO)
    caplog.clear()
    with (
        patch.object(security, "EVENTS", security.SecurityEvents()),
        patch(
            "relaydeck.gateway.features.access.pairing.write_json",
            side_effect=OSError("PRIVATE_PATH"),
        ),
        pytest.raises(OSError),
    ):
        pairing.decide(
            request["request_id"], request["matching_code"], host, ORIGIN, clock[0], approve=True
        )
    assert lines(caplog) == ["security_event event=pairing_decision outcome=failed"]
    assert pairing.pending(host, ORIGIN, clock[0])[0]["state"] == "pending"


@pytest.mark.parametrize("zone", ["UTC", "America/Los_Angeles", "Asia/Riyadh"])
def test_timestamp_does_not_follow_process_timezone(zone: str) -> None:
    import os
    import subprocess
    import sys

    code = (
        "import logging,time\ntime.tzset()\n"
        "from relaydeck.host.observability.logging import UTCFormatter\n"
        "r=logging.LogRecord('probe',20,'',0,'ok',(),None);r.created=0.125\n"
        "print(UTCFormatter('%(asctime)s').format(r))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "TZ": zone},
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert done.stdout.strip() == "1970-01-01T00:00:00.125Z"


def test_request_correlation_isolated_between_threads() -> None:
    from concurrent.futures import ThreadPoolExecutor

    from relaydeck.host.observability.exceptions import install_exception_safety
    from relaydeck.host.observability.request_context import current

    previous = logging.getLogRecordFactory()

    def make(identity: str) -> str:
        token = current.set(identity)
        try:
            record = logging.getLogger("relaydeck.security").makeRecord(
                "relaydeck.security", 20, "", 0, "fixed", (), None
            )
            return record.request_id
        finally:
            current.reset(token)

    try:
        install_exception_safety()
        with ThreadPoolExecutor(2) as pool:
            assert list(pool.map(make, ["request-one", "request-two"])) == [
                "request-one",
                "request-two",
            ]
        assert current.get() == "-"
    finally:
        logging.setLogRecordFactory(previous)


def test_pairing_origin_refusal_is_fixed_and_rate_bounded(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from fastapi.testclient import TestClient

    from relaydeck.contracts import paths
    from relaydeck.gateway.app.bootstrap import create_app

    state, _, body = foundation(tmp_path / "state")
    caplog.set_level(logging.INFO)
    caplog.clear()
    with patch.object(security, "EVENTS", security.SecurityEvents(lambda: 0.0)):
        client = TestClient(create_app(state))
        for _ in range(3):
            reply = client.post(
                paths.PAIRING_REQUESTS, json=body, headers={"Origin": "https://PRIVATE_ORIGIN"}
            )
            assert reply.status_code == 400
            assert reply.json()["code"] == "forbidden"
    assert lines(caplog) == ["security_event event=pairing_requested outcome=rejected"]
    assert "PRIVATE_ORIGIN" not in caplog.text


@pytest.mark.parametrize(
    "route",
    ["/v1/pairing/requests", "/v1/pairing/synthetic/status", "/v1/pairing/synthetic/redeem"],
)
def test_pairing_body_rejection_uses_the_route_event(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, route: str
) -> None:
    from fastapi.testclient import TestClient

    from relaydeck.gateway.app.bootstrap import create_app

    caplog.set_level(logging.INFO)
    state, _, _ = foundation(tmp_path / "state")
    # Host enrollment emits grants; capture only the request under test.
    caplog.clear()
    with patch.object(security, "EVENTS", security.SecurityEvents(lambda: 0.0)):
        response = TestClient(create_app(state)).post(
            route, json={"origin": "PRIVATE_ORIGIN"}, headers={"Origin": ORIGIN}
        )
    assert response.status_code == 400
    event = "pairing_requested" if route.endswith("requests") else "pairing_redemption"
    assert lines(caplog) == [f"security_event event={event} outcome=rejected"]
    assert "PRIVATE_ORIGIN" not in caplog.text
