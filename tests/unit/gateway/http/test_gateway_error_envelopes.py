"""Framework refusals keep status/Allow and expose only bounded, known retry advice."""

from __future__ import annotations

import threading
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException

from relaydeck.companion.app.hosted_web.serve import WebSessionStore, build_web_app
from relaydeck.contracts.schemas import validate
from relaydeck.devtools.fakes.gateway import FakeBackend
from relaydeck.gateway.app.bootstrap import create_app
from relaydeck.gateway.app.state import GatewayState
from relaydeck.gateway.http.bounds import MAX_JSON_BODY
from relaydeck.gateway.http.errors import error_response, install_error_handlers
from relaydeck.gateway.http.middleware.metrics import Metrics
from relaydeck.gateway.http.observers import install_observers
from relaydeck.gateway.http.rate_limits import RateLimiter, command_limiter, retry_details
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)


@pytest.mark.parametrize("hosted", [False, True])
def test_unknown_api_and_wrong_method_are_envelopes_without_document_fallback(hosted: bool) -> None:
    state = GatewayState(backend=FakeBackend(now_ms=lambda: 1))
    app = create_app(state)
    if hosted:
        origin = "https://console.example"
        app, _ = build_web_app(
            gateway_state=state,
            gateway_app=app,
            flet_main=lambda page: None,
            public_origin=origin,
            cookie_secret=b"x" * 32,
            store=WebSessionStore(origin, b"x" * 32),
        )
    try:
        client = TestClient(app)
        missing = client.get("/v1/PRIVATE-ROUTE-CANARY?token=synthetic")
        method = client.post("/v1/me")
        assert missing.status_code == 404
        assert missing.json() == {
            "code": "not_found",
            "message": "unknown API route",
            "retryable": False,
            "details": {"reason": "route_not_found"},
        }
        assert "PRIVATE-ROUTE-CANARY" not in missing.text
        assert method.status_code == 405
        assert method.headers["allow"] == "GET"
        assert method.json() == {
            "code": "forbidden",
            "message": "method not allowed",
            "retryable": False,
            "details": {"reason": "method_not_allowed"},
        }
        validate("relaydeck/gateway/v1/error", missing.json())
        validate("relaydeck/gateway/v1/error", method.json())
        if hosted:
            document = client.get("/PRIVATE-DOCUMENT-CANARY")
            assert document.status_code == 200
            assert document.headers["content-type"].startswith("text/html")
            oversized = client.post("/v1/unknown", content=b"x" * (MAX_JSON_BODY + 1))
            assert oversized.status_code == 413
    finally:
        state.push.close()


def test_framework_headers_and_non_api_custom_handler_are_preserved() -> None:
    app = FastAPI()
    server_threads = []
    seen_threads = []

    def previous(request: Any, exc: HTTPException) -> JSONResponse:
        seen_threads.append(threading.get_ident())
        return JSONResponse(
            {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
        )

    app.add_exception_handler(HTTPException, previous)
    install_error_handlers(app)
    install_error_handlers(app)

    @app.get("/v1/explicit")
    @app.get("/document")
    async def explicit() -> None:
        server_threads.append(threading.get_ident())
        raise HTTPException(
            405, "PRIVATE-DETAIL-CANARY", headers={"Allow": "PATCH", "X-Policy": "kept"}
        )

    client = TestClient(app)
    api = client.get("/v1/explicit")
    document = client.get("/document")
    assert api.status_code == document.status_code == 405
    assert api.headers["allow"] == document.headers["allow"] == "PATCH"
    assert api.headers["x-policy"] == document.headers["x-policy"] == "kept"
    assert api.json()["details"] == {"reason": "method_not_allowed"}
    assert "PRIVATE-DETAIL-CANARY" not in api.text
    assert document.json() == {"detail": "PRIVATE-DETAIL-CANARY"}
    assert seen_threads and seen_threads[-1] != server_threads[-1]


def test_namespace_boundary_uses_live_routes_and_preserves_domain_refusals() -> None:
    app = FastAPI()
    install_error_handlers(app)
    fallback = FastAPI()

    @fallback.get("/{path:path}")
    async def document(path: str) -> JSONResponse:
        return JSONResponse({"document": True})

    app.mount("/", fallback)
    install_observers(app, Metrics())

    @app.get("/v1/late")
    async def late() -> JSONResponse:
        return error_response("not_found", "domain refusal", details={"reason": "owned_resource"})

    client = TestClient(app)
    # A document mount precedes this late route; matching it is still the router's job.
    response = client.get("/v1/unknown")
    assert response.status_code == 404
    assert client.get("/v1/late").status_code == 404
    app.router.routes.insert(0, app.router.routes.pop())
    response = client.get("/v1/late")
    assert response.status_code == 404
    assert response.json()["message"] == "domain refusal"
    assert response.json()["details"] == {"reason": "owned_resource"}
    assert client.get("/document").json() == {"document": True}


@pytest.mark.parametrize(
    ("value", "delay"),
    [
        (1, 1),
        (2.01, 3),
        (999999, 3600),
        (10**1000, 3600),
        (None, None),
        (True, None),
        ("2", None),
        (0, None),
        (-2, None),
        (float("inf"), None),
        (float("nan"), None),
    ],
)
def test_retry_hint_is_consistent_bounded_and_does_not_mutate_details(
    value: Any, delay: int | None
) -> None:
    details = {"reason": "busy", "retry_after_s": value}
    response = error_response("rate_limited", "temporarily limited", True, details=details)
    assert response.status_code == 429
    assert response.headers.get("retry-after") == (str(delay) if delay else None)
    import json

    body = json.loads(response.body)
    assert body["details"].get("retry_after_s") == delay
    assert body["details"]["reason"] == "busy"
    assert details["retry_after_s"] is value
    validate("relaydeck/gateway/v1/error", body)


def test_unknown_retry_delay_has_no_invented_header() -> None:
    response = error_response("rate_limited", "temporarily limited", True)
    assert "retry-after" not in response.headers
    assert (
        response.body == b'{"code":"rate_limited","message":"temporarily limited","retryable":true}'
    )
    assert retry_details(object(), "caller", 0) == {}


def test_command_retry_delay_tracks_replenishment_without_mutating_budget() -> None:
    budget = command_limiter()
    for _ in range(20):
        assert budget.check("caller", 0)
    assert not budget.check("caller", 0)
    original = dict(budget._buckets)
    assert retry_details(budget, "caller", 0) == {"retry_after_s": 3}
    assert retry_details(budget, "caller", 2500) == {"retry_after_s": 1}
    assert retry_details(budget, "caller", 3000) == {}
    assert budget._buckets == original
    assert budget.check("caller", 3000)
    assert RateLimiter(1, 0).retry_after_s("caller", 0) is None
    assert budget.retry_after_s("caller", 3000, 100) is None


def test_command_submission_and_resolution_publish_the_real_retry_delay() -> None:
    from tests.security.helpers import NOW, auth, command_body, make_client, mint_token

    client, state, _backend = make_client()
    try:
        token = mint_token(state)
        assert state.commands.check("acct-1", NOW, 20)
        headers = auth(token)
        submitted = client.post("/v1/hosts/home-mac/commands", json=command_body(), headers=headers)
        resolved = client.post(
            "/v1/hosts/home-mac/commands/cmd-recover-001/resolve",
            headers=headers,
            json={"protocol": "relaydeck.gateway.v1", "agent_id": "term-1", "created_ms": NOW},
        )
        for response in (submitted, resolved):
            assert response.status_code == 429
            assert response.headers["retry-after"] == "3"
            assert response.json()["details"] == {"retry_after_s": 3}
            assert response.json()["retryable"] is True
        state.now_ms = lambda: NOW + 2500
        response = client.post("/v1/hosts/home-mac/commands", json=command_body(), headers=headers)
        assert response.status_code == 429
        assert response.headers["retry-after"] == "1"
        assert response.json()["details"]["retry_after_s"] == 1
    finally:
        state.push.close()
