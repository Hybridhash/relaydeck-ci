"""Build-bound policy metadata survives installation and rejects altered input."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from typing import Any

import pytest

from relaydeck.releases import bundles
from tests.support.companion.web_assets import csp_record, write_bundle_manifest, write_web_assets
from tests.support.setup.web_archive import GOOD_JS


def _source(root: Path, mode: str = "report-only") -> Path:
    root.mkdir(parents=True)
    (root / "index.html").write_bytes(b"<html><script>window.ready=true;</script></html>")
    (root / "main.dart.js").write_text(GOOD_JS)
    write_bundle_manifest(root)
    manifest = root.with_name(root.name + bundles.MANIFEST_SUFFIX)
    data = json.loads(manifest.read_bytes())
    data["web"]["csp"]["policy_mode"] = mode
    manifest.write_text(json.dumps(data))
    return root


def _install(source: Path, store: Path, signed: bool) -> bundles.Installed:
    if signed:
        return bundles.install_signed(source, root=store, archive_sha256="a" * 64, version="1.2.3")
    return bundles.install(source, root=store)


def test_inline_hashes_use_browser_newlines_exclude_external_and_deduplicate() -> None:
    index = (
        b'<html><script src="/app.js">ignored</script>'
        b"<script>\r\nlet text='&amp;';\r</script>"
        b"<SCRIPT type='module'>\nlet text='&amp;';\n</SCRIPT></html>"
    )
    script = b"\nlet text='&amp;';\n"
    expected = "sha256-" + base64.b64encode(hashlib.sha256(script).digest()).decode()
    assert bundles.csp_for_index(index) == {
        "index_sha256": hashlib.sha256(index).hexdigest(),
        "inline_script_hashes": [expected],
        "policy_mode": "report-only",
    }


@pytest.mark.parametrize("signed", [False, True])
@pytest.mark.parametrize("defect", ["missing", "old", "digest", "scripts", "mode", "extra"])
def test_incompatible_source_leaves_the_active_compatible_bundle_untouched(
    tmp_path: Path, signed: bool, defect: str
) -> None:
    store = tmp_path / "store"
    good = _install(_source(tmp_path / "good"), store, signed)
    before = {p.relative_to(store): p.read_bytes() for p in store.rglob("*") if p.is_file()}
    source = _source(tmp_path / "bad")
    manifest = source.with_name(source.name + bundles.MANIFEST_SUFFIX)
    data = json.loads(manifest.read_bytes())
    if defect == "missing":
        manifest.unlink()
    elif defect == "old":
        data.pop("web")
    else:
        record = data["web"]["csp"]
        key, value = {
            "digest": ("index_sha256", "0" * 64),
            "scripts": ("inline_script_hashes", []),
            "mode": ("policy_mode", "disabled"),
            "extra": ("unrecognized", True),
        }[defect]
        record[key] = value
    if defect != "missing":
        manifest.write_text(json.dumps(data))
    with pytest.raises(bundles.BundleError, match="^csp_metadata_required$"):
        _install(source, store, signed)
    assert good.path.is_dir()
    assert bundles.verify(good.path)
    assert {p.relative_to(store): p.read_bytes() for p in store.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("signed", [False, True])
def test_installed_metadata_and_index_are_verified_together(tmp_path: Path, signed: bool) -> None:
    source = _source(tmp_path / "source")
    expected = csp_record((source / "index.html").read_bytes())
    installed = _install(source, tmp_path / "store", signed)
    assert bundles.load_csp(installed.path) == expected
    metadata = installed.path / "csp.json"
    data = json.loads(metadata.read_bytes())
    data["policy_mode"] = "enforce"
    metadata.write_text(json.dumps(data))
    assert not bundles.verify(installed.path)
    assert not bundles.verify_signed(installed.path)


def test_same_javascript_with_different_policy_modes_has_distinct_local_identity(
    tmp_path: Path,
) -> None:
    store = tmp_path / "store"
    report = bundles.install(_source(tmp_path / "report"), root=store)
    enforce = bundles.install(_source(tmp_path / "enforce", "enforce"), root=store)
    assert report.path != enforce.path
    assert (report.path / "main.dart.js").read_bytes() == (
        enforce.path / "main.dart.js"
    ).read_bytes()
    assert bundles.load_csp(report.path)["policy_mode"] == "report-only"
    assert bundles.load_csp(enforce.path)["policy_mode"] == "enforce"
    assert bundles.verify(report.path) and bundles.verify(enforce.path)


@pytest.mark.parametrize("signed", [False, True])
def test_installed_bundle_without_metadata_is_not_selected(tmp_path: Path, signed: bool) -> None:
    store = tmp_path / "store"
    installed = _install(_source(tmp_path / "source"), store, signed)
    (installed.path / "csp.json").unlink()
    assert not bundles.verify(installed.path)
    if signed:
        assert bundles.signed_bundle_for("1.2.3", store) is None
    else:
        assert bundles.current_bundle(store) is None


@pytest.mark.parametrize("target", ["index.html", "csp.json"])
@pytest.mark.parametrize("kind", ["symlink", "fifo", "oversize"])
def test_metadata_reader_refuses_nonregular_or_oversize_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str, kind: str
) -> None:
    root = write_web_assets(tmp_path / "web")
    path = root / target
    if kind == "oversize":
        monkeypatch.setattr(bundles, "MAX_MANIFEST_BYTES", 8)
        monkeypatch.setattr(bundles, "MAX_BYTES", 8)
    else:
        data = path.read_bytes()
        path.unlink()
        if kind == "symlink":
            other = tmp_path / "outside"
            other.write_bytes(data)
            path.symlink_to(other)
        else:
            os.mkfifo(path)
    with pytest.raises(bundles.BundleError, match="^csp_metadata_required$"):
        bundles.load_csp(root)


@pytest.mark.parametrize("signed", [False, True])
def test_changed_index_during_copy_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signed: bool
) -> None:
    source = _source(tmp_path / "source")
    original = bundles._copy_dir

    def copy(*args: Any, **kwargs: Any) -> None:
        (source / "index.html").write_bytes(b"<html>changed</html>")
        original(*args, **kwargs)

    monkeypatch.setattr(bundles, "_copy_dir", copy)
    store = tmp_path / "store"
    with pytest.raises(bundles.BundleError, match="^csp_metadata_required$"):
        _install(source, store, signed)
    assert not list(store.rglob("installed.json"))
    assert not list(store.glob(".staging-*"))


def test_build_producer_prepares_final_index_idempotently_and_retains_schema(
    tmp_path: Path,
) -> None:
    script = Path(__file__).parents[3] / "scripts/build_manifest.py"
    spec = importlib.util.spec_from_file_location("csp_build_manifest", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "web"
    root.mkdir()
    index = root / "index.html"
    index.write_text('<html><head><base href="/old/"><!-- fletAppConfig --></head></html>')
    assert module.main(["--prepare-hosted-index", str(root)]) == 0
    final = index.read_bytes()
    assert b'webSocketEndpoint="/ws"' in final
    assert b'base href="/"' in final
    assert module.main(["--prepare-hosted-index", str(root)]) == 0
    assert index.read_bytes() == final
    assert module.web_csp_record(str(root)) == csp_record(final)
    assert module.SCHEMA == "relaydeck.build-manifest/1"


@pytest.mark.parametrize(
    "setting,value",
    [
        ("FLET_WEB_RENDERER", "html"),
        ("FLET_WEB_ROUTE_URL_STRATEGY", "hash"),
        ("FLET_WEB_NO_CDN", "true"),
        ("FLET_WEB_RENDERER", "invalid"),
    ],
)
def test_runtime_configuration_mismatch_refuses_fixed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, setting: str, value: str
) -> None:
    from relaydeck.companion.app.hosted_web.headers import load_policy

    root = write_web_assets(tmp_path / "web")
    monkeypatch.setenv(setting, value)
    with pytest.raises(bundles.BundleError, match="^csp_metadata_required$"):
        load_policy(root)


def test_compatible_hash_alongside_unexpanded_placeholder_is_not_a_final_index(
    tmp_path: Path,
) -> None:
    from relaydeck.companion.app.hosted_web.headers import load_policy

    root = write_web_assets(tmp_path / "web")
    index = root / "index.html"
    body = index.read_bytes().replace(b"</head>", b"<!-- fletAppConfig --></head>")
    index.write_bytes(body)
    (root / "csp.json").write_text(json.dumps(csp_record(body)))
    with pytest.raises(bundles.BundleError, match="^csp_metadata_required$"):
        load_policy(root)
