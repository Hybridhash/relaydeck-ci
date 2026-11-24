"""Keep unmatched API requests out of a catch-all document mount."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Literal

from fastapi import FastAPI
from starlette.responses import Response
from starlette.routing import BaseRoute, Match, Mount

from relaydeck.contracts.paths import is_api_path

_ResponseBuilder = Callable[[Literal[404, 405], Mapping[str, str] | None], Response]


class APINamespaceMiddleware:
    def __init__(self, app: Any, routes: list[BaseRoute], response: _ResponseBuilder) -> None:
        self._app = app
        self._routes = routes
        self._response = response

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> Any:
        if scope["type"] != "http" or not is_api_path(scope.get("path", "")):
            return await self._app(scope, receive, send)
        partial: tuple[BaseRoute, Mapping[str, Any]] | None = None
        for route in self._routes:
            if isinstance(route, Mount) and route.path in ("", "/"):
                break
            if not is_api_path(getattr(route, "path", "")):
                continue
            match, child_scope = route.matches(scope)
            if match is Match.FULL:
                return await self._app(scope, receive, send)
            if match is Match.PARTIAL and partial is None:
                partial = route, child_scope
        if partial is not None:
            route, partial_scope = partial
            scope.update(partial_scope)
            response = self._response(405, {"Allow": ", ".join(getattr(route, "methods", ()))})
        else:
            response = self._response(404, None)
        return await response(scope, receive, send)


def install_api_boundary(app: FastAPI, response: _ResponseBuilder) -> None:
    if not any(isinstance(route, Mount) and route.path in ("", "/") for route in app.routes):
        return
    app.add_middleware(APINamespaceMiddleware, routes=app.router.routes, response=response)
    # Admission and browser guards must run before the API namespace check.
    app.user_middleware.append(app.user_middleware.pop(0))
