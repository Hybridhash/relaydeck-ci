"""Security response policy for the complete hosted ASGI application."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from relaydeck.contracts import paths
from relaydeck.contracts.endpoints import origin
from relaydeck.releases.bundles import BundleError, load_csp

_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'wasm-unsafe-eval'{hashes}; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "worker-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


class _SecurityHeaders:
    def __init__(self, app: Any, public_origin: str, metadata: dict[str, Any]) -> None:
        self.app = app
        self.public_origin = public_origin
        self.metadata = metadata
        scheme = urlsplit(public_origin).scheme
        if scheme == "https":
            origin(public_origin)
        else:
            # Development still validates the complete origin; localhost is explicit loopback.
            checked = public_origin.replace("://localhost", "://127.0.0.1", 1)
            origin(checked, loopback=True)
        self.https = scheme == "https"
        self.csp_name = (
            b"content-security-policy"
            if metadata["policy_mode"] == "enforce"
            else b"content-security-policy-report-only"
        )
        hashes = "".join(" '" + token + "'" for token in metadata["inline_script_hashes"])
        self.csp = _CSP.format(hashes=hashes).encode("ascii")

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def secured(message: Any) -> None:
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                existing = [value for key, value in headers if key.lower() == self.csp_name]
                policies = list(dict.fromkeys([self.csp, *existing]))
                referrers = [
                    value.lower() for key, value in headers if key.lower() == b"referrer-policy"
                ]
                managed = {
                    self.csp_name,
                    b"x-frame-options",
                    b"x-content-type-options",
                    b"referrer-policy",
                    b"permissions-policy",
                    b"strict-transport-security",
                }
                result = [(key, value) for key, value in headers if key.lower() not in managed]
                result.extend(
                    [
                        (self.csp_name, b", ".join(policies)),
                        (b"x-frame-options", b"DENY"),
                        (b"x-content-type-options", b"nosniff"),
                        (
                            b"referrer-policy",
                            b"no-referrer"
                            if b"no-referrer" in referrers
                            else b"strict-origin-when-cross-origin",
                        ),
                        (
                            b"permissions-policy",
                            b"camera=(), microphone=(), geolocation=(), payment=()",
                        ),
                    ]
                )
                if self.https:
                    result.append((b"strict-transport-security", b"max-age=31536000"))
                message = {**message, "headers": result}
            await send(message)

        await self.app(scope, receive, secured)


def _configured_index(body: bytes) -> bytes:
    from flet.controls.types import RouteUrlStrategy, WebRenderer
    from flet.utils import get_bool_env_var
    from flet_web.patch_index import patch_index_html  # type: ignore[import-untyped]

    with tempfile.TemporaryDirectory(prefix="relaydeck-web-config-") as folder:
        index = Path(folder) / "index.html"
        index.write_bytes(body)
        patch_index_html(
            str(index),
            base_href="",
            websocket_endpoint_path="/ws",
            web_renderer=WebRenderer(os.environ.get("FLET_WEB_RENDERER") or "auto"),
            no_cdn=get_bool_env_var("FLET_WEB_NO_CDN") or False,
            route_url_strategy=RouteUrlStrategy(
                os.environ.get("FLET_WEB_ROUTE_URL_STRATEGY") or "path"
            ),
        )
        prepared = index.read_bytes()
        script = f'<script src="{paths.AUTH_RECOVERY_SCRIPT}"></script>'.encode()
        if script not in prepared:
            prepared = prepared.replace(b"<head>", b"<head>" + script, 1)
        return prepared


def _configuration_hash() -> str:
    body = _configured_index(b"<html><head><!-- fletAppConfig --></head></html>")
    script = re.search(rb"<script>(.*?)</script>", body, re.S)
    if script is None:
        raise BundleError("csp_metadata_required")
    return "sha256-" + base64.b64encode(hashlib.sha256(script[1]).digest()).decode("ascii")


def load_policy(web_root: Path) -> dict[str, Any]:
    """Validate the installed index and current hosted runtime configuration."""
    metadata = load_csp(web_root)
    try:
        body = (web_root / "index.html").read_bytes()
        compatible = (
            _configuration_hash() in metadata["inline_script_hashes"]
            and _configured_index(body) == body
        )
    except (OSError, ValueError):
        raise BundleError("csp_metadata_required") from None
    if not compatible:
        raise BundleError("csp_metadata_required")
    return metadata


def security_headers(app: Any, *, public_origin: str, web_root: Path | None = None) -> Any:
    """Wrap the final framework app with its verified build-bound policy."""
    selected = web_root or (
        Path(os.environ["FLET_WEB_PATH"]) if os.environ.get("FLET_WEB_PATH") else None
    )
    if selected is None:
        raise BundleError("csp_metadata_required")
    metadata = load_policy(selected)
    if isinstance(app, _SecurityHeaders):
        if app.public_origin != public_origin:
            raise ValueError("response_policy_already_configured")
        if app.metadata != metadata:
            raise BundleError("csp_metadata_required")
        return app
    return _SecurityHeaders(app, public_origin, metadata)
