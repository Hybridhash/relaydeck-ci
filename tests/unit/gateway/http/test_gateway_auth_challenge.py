"""Protected API refusals carry an applicable challenge without changing their body."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from relaydeck.contracts import paths
from relaydeck.devtools.fakes.gateway import FakeBackend
from relaydeck.gateway.app.bootstrap import create_app
from relaydeck.gateway.app.state import GatewayState
from relaydeck.gateway.http.errors import error_response
from relaydeck.gateway.http.helpers import bearer_token
from relaydeck.gateway.http.usage import failure
from relaydeck.gateway.http.webpush import _error
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)


@pytest.mark.parametrize("path", [paths.AUTH_SESSION_CHECK, paths.AUTH_SESSION_RECOVERY])
@pytest.mark.parametrize("bearer", [False, True])
def test_cookie_only_unauthorized_responses_do_not_offer_bearer(path: str, bearer: bool) -> None:
    from relaydeck.companion.app.hosted_web.serve import WebSessionStore, build_web_app
    from tests.security.helpers import make_state, mint_token

    state, _ = make_state()
    origin = "https://console.example"
    app, _ = build_web_app(
        gateway_state=state,
        gateway_app=create_app(state),
        flet_main=lambda page: None,
        public_origin=origin,
        cookie_secret=b"x" * 32,
        store=WebSessionStore(origin, b"x" * 32),
    )
    headers = {"Origin": origin}
    if bearer:
        headers["Authorization"] = "Bearer " + mint_token(state)
    try:
        client = TestClient(app)
        if bearer:
            assert client.get(paths.ME, headers=headers).status_code == 200
        response = client.post(path, headers=headers)
        assert response.status_code == 401 and response.content == b""
        assert response.headers["cache-control"] == "no-store"
        assert "www-authenticate" not in response.headers
    finally:
        state.push.close()


@pytest.mark.parametrize("headers", [{}, {"WWW-Authenticate": 'Basic realm="console"'}])
def test_api_prefix_and_authorization_header_do_not_declare_a_bearer_route(
    headers: dict[str, str],
) -> None:
    from relaydeck.gateway.http.middleware.auth_challenge import BearerChallengeMiddleware

    app = FastAPI()
    app.add_middleware(BearerChallengeMiddleware)

    @app.get("/v1/public-entry")
    async def refused() -> JSONResponse:
        return JSONResponse({"error": "unchanged"}, status_code=401, headers=headers)

    response = TestClient(app).get(
        "/v1/public-entry", headers={"Authorization": "Bearer unrecognized"}
    )
    assert response.status_code == 401 and response.content == b'{"error":"unchanged"}'
    assert response.headers.get_list("www-authenticate") == list(headers.values())


@pytest.mark.parametrize("hosted", [False, True])
def test_refresh_rejection_does_not_offer_bearer(hosted: bool) -> None:
    from relaydeck.companion.app.hosted_web.serve import WebSessionStore, build_web_app
    from tests.security.helpers import make_state, mint_token

    state, _ = make_state()
    app = create_app(state)
    origin = "https://console.example"
    if hosted:
        app, _ = build_web_app(
            gateway_state=state,
            gateway_app=app,
            flet_main=lambda page: None,
            public_origin=origin,
            cookie_secret=b"x" * 32,
            store=WebSessionStore(origin, b"x" * 32),
        )
    try:
        headers = {"Authorization": "Bearer " + mint_token(state)}
        client = TestClient(app)
        assert client.get(paths.ME, headers=headers).status_code == 200
        response = client.post(
            paths.TOKEN,
            json={"grant": "refresh_token", "refresh_token": "invalid-refresh-token"},
            headers=headers,
        )
        assert response.status_code == 401
        assert response.json()["message"] == "refresh rejected"
        assert "www-authenticate" not in response.headers
    finally:
        state.push.close()


@pytest.mark.parametrize("factory", [error_response, failure, _error])
@pytest.mark.parametrize("status", [401, 403, 503])
def test_response_factories_do_not_guess_an_authentication_scheme(
    factory: Any, status: int
) -> None:
    if factory is error_response:
        response = factory("unauthenticated", "missing or expired token", status=status)
        assert (
            response.body
            == b'{"code":"unauthenticated","message":"missing or expired token","retryable":false}'
        )
    elif factory is failure:
        response = factory("unauthenticated", status)
        assert response.body == b'{"error":"unauthenticated"}'
    else:
        response = factory(status, "unauthorized")
        assert response.body == b'{"error":{"code":"unauthorized"}}'
    assert response.status_code == status
    assert "www-authenticate" not in response.headers


@pytest.mark.parametrize("hosted", [False, True])
def test_bare_and_hosted_gateway_protected_route_challenge(hosted: bool) -> None:
    state = GatewayState(backend=FakeBackend(now_ms=lambda: 1))
    app = create_app(state)
    if hosted:
        from relaydeck.companion.app.hosted_web.serve import WebSessionStore, build_web_app

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
        response = TestClient(app).get(paths.ME)
        assert response.status_code == 401
        assert response.headers.get("www-authenticate") == "Bearer"
        assert response.json() == {
            "code": "unauthenticated",
            "message": "missing or expired token",
            "retryable": False,
        }
    finally:
        state.push.close()


def test_hosted_picture_guard_challenges_without_entering_the_gateway() -> None:
    from relaydeck.companion.app.hosted_web.serve import WebSessionStore, build_web_app

    state = GatewayState(backend=FakeBackend(now_ms=lambda: 1))
    origin = "https://console.example"
    app, _ = build_web_app(
        gateway_state=state,
        gateway_app=create_app(state),
        flet_main=lambda page: None,
        public_origin=origin,
        cookie_secret=b"x" * 32,
        store=WebSessionStore(origin, b"x" * 32),
    )
    try:
        response = TestClient(app).put(paths.picture_content("a" * 32), content=b"image")
        assert response.status_code == 401
        assert response.headers.get("www-authenticate") == "Bearer"
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json() == {
            "code": "unauthenticated",
            "message": "Pair with your Mac again.",
        }
    finally:
        state.push.close()


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"WWW-Authenticate": 'Bearer realm="console"'},
        {"WWW-Authenticate": 'Basic realm="console"'},
    ],
)
def test_challenge_middleware_preserves_existing_challenges_and_non_api_documents(
    headers: dict[str, str],
) -> None:
    from relaydeck.gateway.http.middleware.auth_challenge import BearerChallengeMiddleware

    app = FastAPI()
    app.add_middleware(BearerChallengeMiddleware)

    async def denied(request: Request) -> JSONResponse:
        if request.url.path == "/v1/protected":
            bearer_token(request)
        return JSONResponse({"error": "unchanged"}, status_code=401, headers=headers)

    app.get("/v1/protected")(denied)
    app.get("/document")(denied)
    with TestClient(app) as client:
        api = client.get("/v1/protected")
        document = client.get("/document")
    expected = list(headers.values())
    if not expected or not expected[0].startswith("Bearer"):
        expected.append("Bearer")
    assert api.headers.get_list("www-authenticate") == expected
    assert document.headers.get_list("www-authenticate") == list(headers.values())
    assert api.content == document.content == b'{"error":"unchanged"}'


def test_challenge_middleware_passes_websocket_messages_unchanged() -> None:
    from relaydeck.gateway.http.middleware.auth_challenge import BearerChallengeMiddleware

    async def scenario() -> None:
        messages = []

        async def inner(scope: Any, receive: Any, send: Any) -> None:
            await send({"type": "websocket.close", "code": 4401})

        async def send(message: Any) -> None:
            messages.append(message)

        await BearerChallengeMiddleware(inner)(
            {"type": "websocket", "path": "/v1/socket"}, None, send
        )
        assert messages == [{"type": "websocket.close", "code": 4401}]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/v1", True),
        ("/v1/", True),
        ("/v1/route", True),
        ("/v10", False),
        ("/v1x", False),
        ("/document", False),
        ("/assets/v1", False),
        ("", False),
    ],
)
def test_api_namespace_classification(path: str, expected: bool) -> None:
    assert paths.is_api_path(path) is expected
