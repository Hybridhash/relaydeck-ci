"""Synthetic static assets for in-process hosted tests."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from pathlib import Path

from flet.controls.types import RouteUrlStrategy, WebRenderer
from flet.utils import get_bool_env_var
from flet_web.patch_index import patch_index_html

from relaydeck.contracts import paths


def csp_record(index: bytes, mode: str = "report-only") -> dict[str, object]:
    """Metadata for the plain script blocks in synthetic assets."""
    return {
        "index_sha256": hashlib.sha256(index).hexdigest(),
        "inline_script_hashes": sorted(
            {
                "sha256-" + base64.b64encode(hashlib.sha256(script).digest()).decode()
                for script in re.findall(rb"<script>(.*?)</script>", index, re.S)
            }
        ),
        "policy_mode": mode,
    }


def write_bundle_manifest(source: Path, **fields: object) -> None:
    document = {
        **fields,
        "schema": "relaydeck.build-manifest/1",
        "web": {"csp": csp_record((source / "index.html").read_bytes())},
    }
    source.with_name(source.name + ".build-manifest.json").write_text(json.dumps(document))


def write_web_assets(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    index = root / "index.html"
    index.write_text(
        "<html><head><!-- fletAppConfig --><script>window.synthetic=true;</script>"
        "</head><body>synthetic</body></html>"
    )
    patch_index_html(
        str(index),
        base_href="",
        websocket_endpoint_path="/ws",
        web_renderer=WebRenderer(os.environ.get("FLET_WEB_RENDERER") or "auto"),
        route_url_strategy=RouteUrlStrategy(
            os.environ.get("FLET_WEB_ROUTE_URL_STRATEGY") or "path"
        ),
        no_cdn=get_bool_env_var("FLET_WEB_NO_CDN") or False,
    )
    body = index.read_bytes().replace(
        b"<head>", b"<head>" + f'<script src="{paths.AUTH_RECOVERY_SCRIPT}"></script>'.encode()
    )
    index.write_bytes(body)
    metadata = csp_record(body)
    (root / "csp.json").write_text(json.dumps(metadata))
    (root / "manifest.json").write_text('{"name":"Synthetic"}')
    (root / "main.dart.js").write_text("synthetic")
    (root / "assets").mkdir(exist_ok=True)
    (root / "assets/FontManifest.json").write_text("[]")
    return root
