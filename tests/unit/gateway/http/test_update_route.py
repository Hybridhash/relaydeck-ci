"""`GET /v1/updates`: the release notice for the Mac this companion runs on, behind the health
scope, never cached, read-only, and absent from a bare gateway."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from relaydeck.companion.updates import schedule as update_schedule
from relaydeck.companion.updates.worker import UpdateService, UpdateWorker
from relaydeck.contracts import paths
from relaydeck.contracts.schemas import validate
from relaydeck.gateway.app.bootstrap import create_app
from relaydeck.gateway.app.state import GatewayState
from relaydeck.releases import records as release_records
from tests.security.helpers import ALL_CAPS, auth, make_state, mint_token
from tests.support.updates.signing import COMMIT

HEALTH = ALL_CAPS | {"server.health"}
RECORD = release_records.Record("0.3.1", COMMIT, "2026-12-01", ("Pairing is faster.",))
LINES = ["uv tool install ...", "# Manual update: run again."]


def service(*, check: bool = True, worker: UpdateWorker | None = None) -> UpdateService:
    return UpdateService(
        host_id="home-mac",
        installed="0.3.0",
        check=check,
        skipped=None,
        worker=worker,
        instructions=lambda record: list(LINES),
    )


def offering(tmp_path: Path) -> UpdateService:
    state_dir = tmp_path.resolve() / "state"
    state_dir.mkdir(mode=0o700)
    update_schedule.save_state(
        state_dir,
        update_schedule.State(
            last_attempt=1_800_000_000, last_success=1_800_000_000, outcome="ok", record=RECORD
        ),
    )
    worker = UpdateWorker(state_dir, installed="0.3.0", fetch=_forbidden)
    return service(worker=worker)


def _forbidden() -> release_records.Record:
    raise AssertionError("the route must never fetch")


def client_with(feed: UpdateService | None) -> tuple[TestClient, GatewayState]:
    state, _backend = make_state()
    state.updates = feed
    return TestClient(create_app(state)), state


def test_a_health_session_gets_the_notice_without_caching_and_it_matches_the_schema(
    tmp_path: Path,
) -> None:
    client, state = client_with(offering(tmp_path))
    reply = client.get(paths.UPDATES, headers=auth(mint_token(state, caps=HEALTH)))
    assert reply.status_code == 200
    assert reply.headers["cache-control"] == "no-store"
    body = reply.json()
    validate("relaydeck/gateway/v1/updates", body)
    assert body["installed"] == "0.3.0" and body["check"] == "on"
    assert body["available"] == {
        "version": "0.3.1",
        "issued": "2026-12-01",
        "notes": ["Pairing is faster."],
    }
    assert body["instructions"] == LINES and body["last_success"] == "2027-01-15T08:00:00Z"
    assert set(body) == {
        "schema",
        "installed",
        "available",
        "check",
        "last_success",
        "instructions",
    }


def test_nothing_new_is_a_valid_document_with_nulls() -> None:
    client, state = client_with(service())
    body = client.get(paths.UPDATES, headers=auth(mint_token(state, caps=HEALTH))).json()
    validate("relaydeck/gateway/v1/updates", body)
    assert body["available"] is None and body["instructions"] is None
    assert body["last_success"] is None


def test_the_check_off_is_reported_as_off() -> None:
    client, state = client_with(service(check=False))
    body = client.get(paths.UPDATES, headers=auth(mint_token(state, caps=HEALTH))).json()
    assert body["check"] == "off" and body["available"] is None


def test_without_a_sign_in_the_route_is_refused() -> None:
    client, _state = client_with(service())
    assert client.get(paths.UPDATES).status_code == 401


def test_a_session_without_the_health_scope_is_refused() -> None:
    client, state = client_with(service())
    reply = client.get(paths.UPDATES, headers=auth(mint_token(state, caps=ALL_CAPS)))
    assert reply.status_code == 403 and reply.json()["code"] == "unsupported_capability"


def test_a_session_for_another_host_is_refused() -> None:
    client, state = client_with(service())
    token = mint_token(state, caps=HEALTH, hosts=frozenset({"other-mac"}))
    reply = client.get(paths.UPDATES, headers=auth(token))
    assert reply.status_code == 404 and reply.json()["code"] == "not_found"


def test_a_session_that_cannot_read_sessions_is_refused() -> None:
    client, state = client_with(service())
    token = mint_token(state, caps=frozenset({"server.health"}))
    assert client.get(paths.UPDATES, headers=auth(token)).status_code == 403


def test_a_revoked_session_is_refused() -> None:
    client, state = client_with(service())
    token = mint_token(state, caps=HEALTH)
    assert client.get(paths.UPDATES, headers=auth(token)).status_code == 200
    state.sessions.revoke_session(token)
    assert client.get(paths.UPDATES, headers=auth(token)).status_code == 401


def test_a_bare_gateway_has_no_such_route() -> None:
    client, state = client_with(None)
    token = mint_token(state, caps=HEALTH)
    assert client.get(paths.UPDATES, headers=auth(token)).status_code == 404


@pytest.mark.parametrize("method", ["post", "put", "delete", "patch"])
def test_the_route_is_read_only(method: str) -> None:
    client, state = client_with(service())
    call: Callable[..., object] = getattr(client, method)
    reply = call(paths.UPDATES, headers=auth(mint_token(state, caps=HEALTH)))
    assert reply.status_code == 405  # type: ignore[attr-defined]


def test_a_refused_instruction_set_is_null_not_an_empty_list(tmp_path: Path) -> None:
    feed = offering(tmp_path)
    feed._instructions = lambda record: []
    client, state = client_with(feed)
    body = client.get(paths.UPDATES, headers=auth(mint_token(state, caps=HEALTH))).json()
    validate("relaydeck/gateway/v1/updates", body)
    assert body["available"] is not None and body["instructions"] is None
