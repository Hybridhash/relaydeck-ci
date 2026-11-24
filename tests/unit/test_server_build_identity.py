"""Installed build identity is explicit, bounded and available only to health readers."""

from __future__ import annotations

import json
import sys
from types import ModuleType

import pytest

from relaydeck import version
from relaydeck.contracts import paths
from relaydeck.contracts.schemas import SchemaError, validate
from tests.security.helpers import ALL_CAPS, auth, make_client, mint_token


@pytest.mark.parametrize("revision", [None, "a" * 40, "PRIVATE_PATH/token", "A" * 40, 42, "f" * 41])
def test_build_stamp_is_optional_and_accepts_only_a_full_commit(
    monkeypatch: pytest.MonkeyPatch, revision: object
) -> None:
    if revision is None:
        monkeypatch.setitem(sys.modules, "relaydeck._build", None)
    else:
        stamp = ModuleType("relaydeck._build")
        stamp.REVISION = revision  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "relaydeck._build", stamp)
    expected = revision if revision == "a" * 40 else "unknown"
    assert version._resolve_build_revision() == expected


@pytest.mark.parametrize("stamped", [False, True])
def test_only_authenticated_health_readers_receive_the_installed_identity(
    monkeypatch: pytest.MonkeyPatch, stamped: bool
) -> None:
    stamp = ModuleType("relaydeck._build")
    stamp.REVISION = "b" * 40  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "relaydeck._build", stamp if stamped else None)
    revision = version._resolve_build_revision()
    monkeypatch.setattr(version, "__build_revision__", revision)
    client, state, _ = make_client()
    token = mint_token(state, caps=ALL_CAPS | {"server.health"})
    response = client.get(paths.METRICS, headers=auth(token))
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["app_version"] == version.__version__
    assert body["build_revision"] == ("b" * 40 if stamped else "unknown")
    validate("relaydeck/gateway/v1/server-metrics", body)
    assert client.get("/healthz").json() == {
        "ok": True,
        "version": "1.0.0",
        "push": {"configured": False, "delivery_error": "", "enqueue_error": ""},
    }
    before = client.get("/readyz")
    assert before.status_code == 503
    assert before.json() == {"ready": False, "hosts_connected": 0}
    for headers in ({}, auth(mint_token(state))):
        refused = client.get(paths.METRICS, headers=headers)
        assert refused.status_code in (400, 401, 403)
        assert "app_version" not in refused.json() and "build_revision" not in refused.json()
        assert "b" * 40 not in refused.text


def test_identity_fields_are_optional_but_never_accept_path_or_extra_data() -> None:
    from pathlib import Path

    fixture = Path(__file__).resolve().parents[1] / "fixtures/gateway/server-metrics.json"
    body = json.loads(fixture.read_text())
    validate("relaydeck/gateway/v1/server-metrics", body)
    for changes in [
        {"build_revision": "/PRIVATE_PATH"},
        {"app_version": "PRIVATE BODY\nTOKEN"},
        {"build_revision": "f" * 40, "checkout_path": "/PRIVATE_PATH"},
    ]:
        with pytest.raises(SchemaError):
            validate("relaydeck/gateway/v1/server-metrics", {**body, **changes})
