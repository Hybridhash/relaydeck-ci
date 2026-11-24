"""Stable route registration and dependency metadata, without captured request state."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Route, WebSocketRoute


def _identity(call: Any) -> str:
    module, name = call.__module__, call.__qualname__
    return f"{module}:{name}"


def _dependency(node: Dependant) -> dict[str, Any]:
    call = node.call
    assert call is not None
    result = {
        "call": _identity(call),
        "name": node.name,
        "use_cache": node.use_cache,
        "security_scopes": list(node.oauth_scopes),
        "scope": node.scope,
        "dependencies": [_dependency(child) for child in node.dependencies],
    }
    if call.__qualname__ == "require_scope.<locals>.dependency":
        closure = dict(zip(call.__code__.co_freevars, call.__closure__ or (), strict=True))
        scope = closure["scope"].cell_contents
        assert isinstance(scope, str)
        result["required_scope"] = scope
    return result


def capture_routes(app: FastAPI) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for route in app.routes:
        assert isinstance(route, Route | WebSocketRoute), type(route)
        row = {
            "kind": "websocket" if isinstance(route, WebSocketRoute) else "http",
            "path": route.path,
            "methods": sorted(getattr(route, "methods", None) or ()),
            "name": route.name,
            "endpoint": _identity(route.endpoint),
            "status_code": getattr(route, "status_code", None),
        }
        if isinstance(route, APIRoute | APIWebSocketRoute):
            row["dependency"] = _dependency(route.dependant)
        rows.append(row)
    return rows
