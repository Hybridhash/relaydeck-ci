"""Static web compression and cache policy."""

from __future__ import annotations

from typing import Any

from relaydeck.client.design_system.font_versions import is_current as fonts_is_current

_GZIP_SUFFIXES = (".js", ".mjs", ".wasm", ".json", ".css", ".html", ".ttf", ".otf", ".map")


def static_gzip(app: Any) -> Any:
    """Compress the web client's static files (~8 MB of JS) for phones.

    Only GET requests for the page or static asset suffixes are wrapped;
    the gateway API, SSE streams and WebSockets pass through untouched.
    """
    from starlette.middleware.gzip import GZipMiddleware

    zipped = GZipMiddleware(app, minimum_size=1024)

    async def route(scope: Any, receive: Any, send: Any) -> None:
        path = scope.get("path", "") if scope.get("type") == "http" else ""
        if (
            scope.get("method") == "GET"
            and not path.startswith("/v1/")
            and (path == "/" or path.endswith(_GZIP_SUFFIXES))
        ):
            await zipped(scope, receive, send)
        else:
            await app(scope, receive, send)

    return route


NO_CACHE = "no-cache"


IMMUTABLE = "public, max-age=31536000, immutable"


def cache_policy(path: str, query: bytes | str = b"") -> str | None:
    """Return static-path cache policy, preserving API, WebSocket and health responses.
    Revalidate assets except bundled fonts whose requested fingerprint matches their content.
    """
    if path in ("/ws", "/healthz"):
        return None
    if path.lstrip("/").startswith("v1/"):
        return None
    if fonts_is_current(path, query):
        return IMMUTABLE
    return NO_CACHE


def static_cache(app: Any) -> Any:
    """Stamp Cache-Control on the web client's static files (phones).

    Composes outside static_gzip; only successful GET/HEAD responses gain a
    header, and an app-set Cache-Control is replaced, never duplicated.
    """

    async def route(scope: Any, receive: Any, send: Any) -> None:
        policy = None
        if scope.get("type") == "http" and scope.get("method") in ("GET", "HEAD"):
            policy = cache_policy(scope.get("path", ""), scope.get("query_string", b""))
        if policy is None:
            await app(scope, receive, send)
            return

        async def send_cached(message: Any) -> None:
            if message["type"] == "http.response.start" and message["status"] < 400:
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"
                ]
                headers.append((b"cache-control", policy.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await app(scope, receive, send_cached)

    return route
