"""Production hosted composition excludes development code until an explicit mode."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest


@pytest.mark.parametrize("operation", ["import", "factory", "runner"])
def test_production_paths_refuse_all_development_imports(operation: str) -> None:
    code = """
import importlib.abc, sys
from types import SimpleNamespace
import uvicorn
attempts = []
class RefuseDevelopment(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'relaydeck.devtools' or fullname.startswith('relaydeck.devtools.'):
            attempts.append(fullname)
            raise AssertionError('development code reached a production path')
sys.meta_path.insert(0, RefuseDevelopment())
def listener(*args, **kwargs):
    raise AssertionError('unexpected listener')
uvicorn.run = listener
from relaydeck.companion.app.hosted_web import serve
if OPERATION == 'runner':
    assert serve.main(['--cookie-secret', 'x' * 32, '--cursor-secret', 'y' * 32]) == 2
elif OPERATION == 'factory':
    from relaydeck.contracts import paths
    from relaydeck.gateway.app.bootstrap import create_app
    from relaydeck.gateway.app.state import GatewayState
    state = GatewayState(backend=SimpleNamespace())
    try:
        app, _ = serve.build_web_app(
            gateway_state=state, gateway_app=create_app(state), flet_main=lambda page: None,
            public_origin='https://console.example', cookie_secret=b'x' * 32,
        )
        assert paths.AUTH_DEV_LOGIN not in [getattr(route, 'path', '') for route in app.routes]
    finally:
        state.push.close()
assert attempts == []
assert not any(n == 'relaydeck.devtools' or n.startswith('relaydeck.devtools.')
               for n in sys.modules)
print('production paths exclude development code')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", f"OPERATION={operation!r}\n" + code],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "production paths exclude development code" in result.stdout
    if operation == "runner":
        assert "--dev-login or --dev-demo-sessions" in result.stderr
        assert "relaydeck start" in result.stderr


@pytest.mark.usefixtures("hosted_web_bundle")
@pytest.mark.parametrize(
    "flags",
    [
        ["--dev-login"],
        ["--dev-demo-sessions"],
        ["--dev-login", "--dev-demo-sessions"],
    ],
)
def test_explicit_development_modes_keep_seed_and_login_behavior(
    monkeypatch: pytest.MonkeyPatch, flags: list[str]
) -> None:
    import uvicorn

    from relaydeck.companion.app.hosted_web import serve
    from relaydeck.contracts import paths
    from relaydeck.devtools import dev_server

    monkeypatch.setitem(dev_server._DEV_LOGIN_TOKEN, "value", "")
    monkeypatch.setitem(dev_server._DEV_HOSTS, "value", frozenset({"home-mac"}))
    captured: list[Any] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: captured.append(app))
    assert serve.main(["--dev-secret", *flags]) == 0
    app = captured[0].app
    state = app.state.rd_gateway_state
    try:
        assert (paths.AUTH_DEV_LOGIN in [getattr(r, "path", "") for r in app.routes]) is (
            "--dev-login" in flags
        )
        snapshot = state.backend.snapshot("home-mac")
        assert bool(snapshot["agents"]) is ("--dev-demo-sessions" in flags)
        assert snapshot["host"]["id"] == "home-mac"
    finally:
        state.push.close()


@pytest.mark.usefixtures("hosted_web_bundle")
def test_development_runner_logs_no_request_line_or_exception_text() -> None:
    code = """
import logging
import uvicorn
from relaydeck.companion.app.hosted_web import serve

CANARY = 'canary-secret-token-0123'

def run(self):
    assert self.config.access_log is False
    logging.getLogger('uvicorn.access').info(
        '%s - "%s %s HTTP/%s" %d',
        '127.0.0.1:1', 'GET', '/dev-login?token=' + CANARY, '1.1', 200,
    )
    try:
        raise ValueError(CANARY)
    except ValueError:
        logging.getLogger('uvicorn.error').exception('Exception in ASGI application')
        logging.getLogger('relaydeck.example').exception('failed %s', CANARY)
    self.started = True

uvicorn.Server.run = run
assert serve.main(['--dev-secret', '--dev-login']) == 0
print('runner finished')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "runner finished" in result.stdout
    assert "canary-secret-token-0123" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert "exception category=" in result.stderr
    assert "dev-login" not in result.stderr
