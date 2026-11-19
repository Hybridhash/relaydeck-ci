"""Ordered routes and their authorization dependencies.

After reviewing an intentional route change, regenerate with:
python -m tests.architecture.test_gateway_route_dependencies \
  > tests/fixtures/gateway/route_dependencies.json
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI

from relaydeck.devtools.fakes.gateway import FakeBackend
from relaydeck.gateway.app.bootstrap import create_app
from relaydeck.gateway.app.state import GatewayState
from relaydeck.gateway.http.auth import require_scope
from tests.support.gateway.route_capture import capture_routes

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/gateway/route_dependencies.json"


def table(mode: str) -> list[dict[str, object]]:
    state = GatewayState(backend=FakeBackend(now_ms=lambda: 1_000_000), cursor_secret=b"x" * 32)
    state.now_ms = lambda: 1_000_000
    state.updates = object() if mode == "notice" else None
    state.debug_docs = mode == "docs"
    try:
        return capture_routes(create_app(state))
    finally:
        state.push.close()


@pytest.mark.parametrize("mode", ["bare", "notice", "docs"])
def test_gateway_dependencies_match_the_frozen_table(mode: str) -> None:
    assert table(mode) == json.loads(FIXTURE.read_text())[mode]


def test_capture_detects_dependency_removal_and_capability_changes() -> None:
    def endpoint() -> None:
        pass

    def table(scope: str | None) -> list[dict[str, object]]:
        app = FastAPI()
        dependencies = [Depends(require_scope(scope))] if scope is not None else []
        app.get("/guarded", dependencies=dependencies)(endpoint)
        return capture_routes(app)

    assert table("server.health") != table("sessions.read")
    assert table("server.health") != table(None)


if __name__ == "__main__":
    print(json.dumps({mode: table(mode) for mode in ("bare", "notice", "docs")}, indent=2))
