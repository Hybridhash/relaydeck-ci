"""The actual metadata producer and automatic startup share compatible bundle selection."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any

import pytest

from relaydeck.companion import cli
from relaydeck.companion.app.hosted_web.headers import load_policy
from relaydeck.releases import bundles
from relaydeck.version import __version__
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)
from tests.support.companion.asgi_companion import config_file
from tests.support.setup.web_archive import GOOD_JS


def _producer() -> Any:
    spec = importlib.util.spec_from_file_location(
        "policy_handoff_producer", Path(__file__).parents[2] / "scripts/build_manifest.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _produced(root: Path, *, runtime_record: bool = False) -> Path:
    root.mkdir()
    (root / "main.dart.js").write_text(GOOD_JS)
    index = root / "index.html"
    index.write_text('<html><head><base href="/"><!-- fletAppConfig --></head></html>')
    producer = _producer()
    assert producer.main(["--prepare-hosted-index", str(root)]) == 0
    # The worker post-process precedes policy emission; its final inline block is hashed too.
    index.write_bytes(
        index.read_bytes().replace(b"</head>", b"<script>window.worker=true;</script></head>")
    )
    if runtime_record:
        assert producer.main(["--write-web-csp", str(root)]) == 0
    record = producer.web_csp_record(str(root))
    root.with_name(root.name + bundles.MANIFEST_SUFFIX).write_text(
        json.dumps(
            {
                "schema": "relaydeck.build-manifest/1",
                "version": __version__,
                "web": {"csp": record},
            }
        )
    )
    return root


def test_produced_output_starts_explicit_hosting_without_a_manufactured_runtime_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _produced(tmp_path / "out", runtime_record=True)
    sibling = json.loads(root.with_name(root.name + bundles.MANIFEST_SUFFIX).read_bytes())
    assert json.loads((root / "csp.json").read_bytes()) == sibling["web"]["csp"]
    assert load_policy(root) == sibling["web"]["csp"]
    monkeypatch.setenv("FLET_WEB_PATH", str(root))
    cli._select_web_bundle()
    assert os.environ["FLET_WEB_PATH"] == str(root)
    (root / "index.html").write_bytes(b"changed")
    with pytest.raises(cli.WebBundleMissing, match="^csp_metadata_required$"):
        cli._select_web_bundle()


def test_canonical_builder_emits_runtime_record_after_final_worker_index() -> None:
    text = (Path(__file__).parents[2] / "scripts/build_web_client.sh").read_text()
    prepare = text.index('--prepare-hosted-index "$OUT"')
    worker = text.index('web_service_worker.py" "$OUT" --verify')
    runtime = text.index('--write-web-csp "$OUT"')
    manifest = text.index('--web-out "$OUT" \\\n')
    assert prepare < worker < runtime < manifest


def test_producer_does_not_write_a_runtime_record_without_a_final_index(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _producer().main(["--write-web-csp", str(tmp_path)]) == 1
    assert capsys.readouterr().err == "csp_metadata_required\n"
    assert not (tmp_path / "csp.json").exists()


@pytest.mark.parametrize("predecessor", [False, True])
@pytest.mark.parametrize("defect", ["renderer", "unprepared"])
def test_automatic_startup_retains_a_compatible_predecessor_or_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, predecessor: bool, defect: str
) -> None:
    store = tmp_path / "store"
    monkeypatch.setenv("FLET_WEB_PATH", "")
    monkeypatch.delenv("FLET_WEB_PATH", raising=False)
    monkeypatch.delenv("FLET_WEB_RENDERER", raising=False)
    good = None
    if predecessor:
        source = _produced(tmp_path / "compatible")
        monkeypatch.setattr(bundles.time, "strftime", lambda *args: "2026-11-09T00:00:00Z")
        good = bundles.install_signed(
            source, root=store, archive_sha256="a" * 64, version=__version__
        )
    with monkeypatch.context() as configured:
        if defect == "renderer":
            configured.setenv("FLET_WEB_RENDERER", "canvaskit")
        bad = _produced(tmp_path / "incompatible")
    if defect == "unprepared":
        (bad / "index.html").write_bytes(b"<html>not configured for hosting</html>")
        producer = _producer()
        manifest = bad.with_name(bad.name + bundles.MANIFEST_SUFFIX)
        data = json.loads(manifest.read_bytes())
        data["web"]["csp"] = producer.web_csp_record(str(bad))
        manifest.write_text(json.dumps(data))
    monkeypatch.setattr(bundles.time, "strftime", lambda *args: "2026-11-09T00:00:01Z")
    newer = bundles.install_signed(bad, root=store, archive_sha256="b" * 64, version=__version__)
    assert bundles.verify_signed(newer.path)
    assert bundles.signed_bundle_for(__version__, store) == newer.path
    monkeypatch.setattr(bundles, "default_root", lambda: store)
    before = {p.relative_to(store): p.read_bytes() for p in store.rglob("*") if p.is_file()}
    if good is None:
        with pytest.raises(cli.WebBundleMissing, match="^csp_metadata_required$"):
            cli._select_web_bundle()
        assert "FLET_WEB_PATH" not in os.environ
    else:
        cli._select_web_bundle()
        assert os.environ["FLET_WEB_PATH"] == str(good.path)
        assert load_policy(good.path)
    assert {p.relative_to(store): p.read_bytes() for p in store.rglob("*") if p.is_file()} == before
    started: list[str] = []

    class Runtime:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            started.append("runtime")

    async def supervise(state_dir: Path, factory: Any, stop: Any) -> int:
        factory()
        started.append("supervise")
        return 0

    monkeypatch.setattr(cli, "CompanionRuntime", Runtime)
    monkeypatch.setattr(cli, "supervise", supervise)
    monkeypatch.delenv("FLET_WEB_PATH", raising=False)
    config_dir = tmp_path / "config"
    config_dir.mkdir(mode=0o700)
    path = config_file(config_dir)
    assert cli.main(["--config", str(path), "start"]) == (0 if predecessor else 2)
    assert started == (["runtime", "supervise"] if predecessor else [])
    if good is not None:
        assert os.environ["FLET_WEB_PATH"] == str(good.path)
    monkeypatch.setenv("FLET_WEB_PATH", str(newer.path))
    with pytest.raises(cli.WebBundleMissing, match="^csp_metadata_required$"):
        cli._select_web_bundle()
