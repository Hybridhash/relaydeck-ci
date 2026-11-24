"""The complete hosted entries stamp build-bound response policy."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient
from flet_web.patch_index import patch_index_html

from relaydeck.companion.app.hosted_web.binding import HostedWeb
from relaydeck.contracts import paths
from relaydeck.gateway.app.bootstrap import create_app
from tests.security.helpers import make_state
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)

ORIGIN = "https://console.example"


@pytest.fixture
def web_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "web"
    root.mkdir()
    index = root / "index.html"
    index.write_text(
        "<html><head><!-- fletAppConfig --><script>window.ready=true;</script>"
        "</head><body>synthetic</body></html>"
    )
    patch_index_html(str(index), base_href="", websocket_endpoint_path="/ws")
    body = index.read_bytes().replace(
        b"<head>", b"<head>" + f'<script src="{paths.AUTH_RECOVERY_SCRIPT}"></script>'.encode()
    )
    index.write_bytes(body)
    hashes = sorted(
        {
            "sha256-" + base64.b64encode(hashlib.sha256(script).digest()).decode()
            for script in re.findall(rb"<script>(.*?)</script>", body, re.S)
        }
    )
    metadata = {
        "index_sha256": hashlib.sha256(body).hexdigest(),
        "inline_script_hashes": hashes,
        "policy_mode": "report-only",
    }
    (root / "csp.json").write_text(json.dumps(metadata))
    (root / "manifest.json").write_text('{"name":"Synthetic"}')
    (root / "main.dart.js").write_text("synthetic")
    monkeypatch.setenv("FLET_WEB_PATH", str(root))
    return root


def _wrap(app: Any, web_root: Path, origin: str = ORIGIN) -> Any:
    module = importlib.import_module("relaydeck.companion.app.hosted_web.headers")
    return module.security_headers(app, public_origin=origin, web_root=web_root)


def _expected(root: Path) -> str:
    hashes = json.loads((root / "csp.json").read_text())["inline_script_hashes"]
    sources = "".join(f" '{value}'" for value in hashes)
    return (
        f"default-src 'self'; script-src 'self' 'wasm-unsafe-eval'{sources}; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; "
        "connect-src 'self'; worker-src 'self'; object-src 'none'; base-uri 'self'; "
        "form-action 'self'; frame-ancestors 'none'"
    )


@pytest.mark.parametrize(
    "status", [200, 204, 206, 302, 303, 304, 400, 401, 403, 404, 405, 413, 429, 500, 503]
)
@pytest.mark.parametrize(
    "content_type",
    ["text/html", "application/javascript", "application/wasm", "font/ttf", "application/json"],
)
def test_response_start_policy_preserves_status_stream_cookies_and_cache(
    web_root: Path, status: int, content_type: str
) -> None:
    async def scenario() -> None:
        received: list[dict[str, Any]] = []
        chunks = [
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", content_type.encode()),
                    (b"set-cookie", b"first=one; HttpOnly"),
                    (b"set-cookie", b"second=two; HttpOnly"),
                    (b"cache-control", b"private, no-store"),
                    (b"etag", b'"same"'),
                ],
            },
            {"type": "http.response.body", "body": b"first", "more_body": True},
            {"type": "http.response.body", "body": b"second", "more_body": False},
        ]

        async def app(scope: Any, receive: Any, send: Any) -> None:
            for message in chunks:
                await send(message)

        async def send(message: dict[str, Any]) -> None:
            received.append(message)

        await _wrap(app, web_root)({"type": "http", "scheme": "http"}, None, send)
        start = received[0]
        headers = start["headers"]
        assert start["status"] == status
        assert received[1:] == chunks[1:]
        assert [(k, v) for k, v in headers if k == b"set-cookie"] == chunks[0]["headers"][1:3]
        values = dict(headers)
        assert values[b"content-type"] == content_type.encode()
        assert values[b"cache-control"] == b"private, no-store"
        assert values[b"etag"] == b'"same"'
        assert values[b"x-frame-options"] == b"DENY"
        assert values[b"x-content-type-options"] == b"nosniff"
        assert values[b"referrer-policy"] == b"strict-origin-when-cross-origin"
        assert (
            values[b"permissions-policy"] == b"camera=(), microphone=(), geolocation=(), payment=()"
        )
        assert values[b"strict-transport-security"] == b"max-age=31536000"
        assert values[b"content-security-policy-report-only"].decode() == _expected(web_root)
        assert b"content-security-policy" not in values
        assert len([k for k, _ in headers if k == b"x-frame-options"]) == 1

    asyncio.run(scenario())


def test_identity_referrer_and_stricter_route_policy_are_retained(web_root: Path) -> None:
    app = FastAPI()

    @app.get("/")
    async def document() -> Response:
        return Response(
            b"unchanged",
            headers={
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'none'",
            },
        )

    wrapped = _wrap(app, web_root)
    with TestClient(_wrap(wrapped, web_root)) as client:
        response = client.get("/")
    assert response.content == b"unchanged"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["content-security-policy"] == "default-src 'none'"
    assert response.headers.get_list("content-security-policy-report-only") == [_expected(web_root)]
    assert response.headers.get_list("x-frame-options") == ["DENY"]


def test_http_loopback_has_no_hsts_even_with_spoofed_forwarding(web_root: Path) -> None:
    app = FastAPI()

    @app.get("/")
    async def document() -> Response:
        return Response(b"same", headers={"Strict-Transport-Security": "untrusted"})

    with TestClient(_wrap(app, web_root, "http://127.0.0.1:8000")) as client:
        response = client.get("/", headers={"X-Forwarded-Proto": "https"})
    assert "strict-transport-security" not in response.headers
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("scope_type", ["websocket", "lifespan"])
def test_websocket_and_lifespan_messages_are_untouched(web_root: Path, scope_type: str) -> None:
    async def scenario() -> None:
        received = []
        messages = [{"type": "websocket.accept"}, {"type": "websocket.close", "code": 4401}]

        async def app(scope: Any, receive: Any, send: Any) -> None:
            assert scope["type"] == scope_type
            for message in messages:
                await send(message)

        async def send(message: Any) -> None:
            received.append(message)

        await _wrap(app, web_root)({"type": scope_type}, None, send)
        assert received == messages

    asyncio.run(scenario())


def _production(tmp_path: Path) -> tuple[Any, Any, HostedWeb]:
    state, _ = make_state()
    gateway = create_app(state)

    @gateway.get("/__probe_error")
    async def fail() -> Response:
        raise RuntimeError("synthetic")

    async def serve() -> None:
        pass

    owner = HostedWeb(
        SimpleNamespace(
            advertised_origin=ORIGIN,
            local_endpoint="http://127.0.0.1:8000",
            host_id="home-mac",
            state_dir=tmp_path,
        ),
        gateway=lambda: state,
        persistence=lambda: None,
        server_factory=None,
        route_factory=None,
        setup_report=lambda: (),
        request_shutdown=lambda: None,
        serve_owned=serve,
    )
    return owner._web_app(gateway, b"x" * 32), state, owner


@pytest.mark.parametrize(
    "path", ["/", "/manifest.json", "/main.dart.js", "/v1/me", "/missing.txt", "/__probe_error"]
)
def test_production_composition_covers_framework_errors_and_actual_routes(
    tmp_path: Path, web_root: Path, path: str
) -> None:
    app, state, owner = _production(tmp_path)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get(path)
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["content-security-policy-report-only"] == _expected(web_root)
        if path == "/__probe_error":
            assert response.status_code == 500
        if path == "/v1/me":
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"
        if path == "/":
            assert response.content == (web_root / "index.html").read_bytes()
    finally:
        assert owner.web_store is not None
        owner.web_store.command_store.close()
        state.push.close()


def test_development_entry_composes_the_same_policy(
    web_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    from relaydeck.companion.app.hosted_web import serve

    captured: list[Any] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: captured.append(app))
    assert serve.main(["--dev-secret", "--dev-login"]) == 0
    with TestClient(captured[0]) as client:
        response = client.get("/")
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy-report-only"] == _expected(web_root)


@pytest.mark.parametrize(
    "defect", ["unset", "missing-index", "changed-index", "missing-metadata", "renderer"]
)
def test_development_entry_explains_unusable_assets_before_announcing_startup(
    web_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    defect: str,
) -> None:
    import uvicorn

    from relaydeck.companion.app.hosted_web import serve

    started: list[Any] = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: started.append(args))
    if defect == "unset":
        monkeypatch.delenv("FLET_WEB_PATH")
    elif defect == "missing-index":
        (web_root / "index.html").unlink()
    elif defect == "changed-index":
        (web_root / "index.html").write_text("unprepared")
    elif defect == "missing-metadata":
        (web_root / "csp.json").unlink()
    else:
        monkeypatch.setenv("FLET_WEB_RENDERER", "canvaskit")
    assert serve.main(["--dev-secret", "--dev-login", "--dev-demo-sessions"]) == 2
    captured = capsys.readouterr()
    assert "scripts/build_web_client.sh" in captured.err and "FLET_WEB_PATH" in captured.err
    assert "serving RelayDeck web" not in captured.out and "dev login (" not in captured.out
    assert "Traceback" not in captured.err and started == []


def test_missing_or_changed_metadata_refuses_before_production_composition(
    tmp_path: Path, web_root: Path
) -> None:
    (web_root / "index.html").write_text("changed")
    from relaydeck.releases.bundles import BundleError

    with pytest.raises(BundleError, match="^csp_metadata_required$"):
        _production(tmp_path)


@pytest.mark.parametrize("mode", ["report-only", "enforce"])
def test_existing_policy_in_the_same_mode_is_an_additional_policy(
    web_root: Path, mode: str
) -> None:
    metadata = json.loads((web_root / "csp.json").read_bytes())
    metadata["policy_mode"] = mode
    (web_root / "csp.json").write_text(json.dumps(metadata))
    name = "content-security-policy" + ("-report-only" if mode == "report-only" else "")
    app = FastAPI()

    @app.get("/")
    async def document() -> Response:
        return Response(b"same", headers={name: "default-src 'none'"})

    with TestClient(_wrap(app, web_root)) as client:
        response = client.get("/")
    assert response.headers.get_list(name) == [_expected(web_root) + ", default-src 'none'"]


@pytest.mark.parametrize(
    "path,method,challenge",
    [
        (paths.ME, "GET", ["Bearer"]),
        (paths.picture_content("a" * 32), "PUT", ["Bearer"]),
        (paths.AUTH_SESSION_CHECK, "POST", []),
        (paths.AUTH_SESSION_RECOVERY, "POST", []),
    ],
)
def test_actual_api_refusals_preserve_the_applicable_challenge(
    tmp_path: Path, web_root: Path, path: str, method: str, challenge: list[str]
) -> None:
    app, state, owner = _production(tmp_path)
    try:
        with TestClient(app) as client:
            response = client.request(method, path, headers={"Origin": ORIGIN})
        assert response.status_code == 401
        assert response.headers.get_list("www-authenticate") == challenge
        assert response.headers["content-security-policy-report-only"] == _expected(web_root)
    finally:
        assert owner.web_store is not None
        owner.web_store.command_store.close()
        state.push.close()
