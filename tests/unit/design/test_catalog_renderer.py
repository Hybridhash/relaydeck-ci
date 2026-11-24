"""Renderer provenance and opt-in guards without launching a browser."""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from relaydeck.devtools import catalog_screenshots as screenshots
from relaydeck.devtools.catalog_renderer import FLET_VERSION, FLUTTER_VERSION, verify


def fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    renderer = tmp_path / "web"
    renderer.mkdir()
    (renderer / "index.html").write_text("<html></html>")
    (renderer / "main.dart.js").write_text("compiled renderer fixture")
    digest = hashlib.sha256(
        "".join(
            f"{p.name}\0{hashlib.sha256(p.read_bytes()).hexdigest()}\n"
            for p in sorted(renderer.iterdir())
        ).encode()
    ).hexdigest()
    data: dict[str, Any] = {
        "schema": "relaydeck.build-manifest/1",
        "flet": {"version": FLET_VERSION},
        "flutter_sdk": {"flutter_version": FLUTTER_VERSION},
        "web_output": {
            "main_dart_js": {
                "sha256": hashlib.sha256((renderer / "main.dart.js").read_bytes()).hexdigest()
            },
            "tree_sha256": digest,
            "repair_markers": {
                name: {"ok": True}
                for name in (
                    "flet-reconnect-merge-patch",
                    "flutter-file-upload-patch",
                    "flutter-ios-pointer-patch",
                )
            },
        },
    }
    manifest = tmp_path / "renderer.json"
    manifest.write_text(json.dumps(data))
    return renderer, manifest, data


def test_renderer_versions_match_repository_pins() -> None:
    root = Path(__file__).resolve().parents[3]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    assert "flet==" + FLET_VERSION in project["project"]["dependencies"]
    assert FLUTTER_VERSION in (root / "CONTRIBUTING.md").read_text()


def test_valid_local_renderer_matches_complete_tree(tmp_path: Path) -> None:
    renderer, manifest, data = fixture(tmp_path)
    assert verify(renderer, manifest) == data["web_output"]["tree_sha256"]


@pytest.mark.parametrize(
    "part", ["bundle", "asset", "flet", "flutter", "repair", "schema", "symlink"]
)
def test_renderer_rejects_mismatched_inputs(tmp_path: Path, part: str) -> None:
    renderer, manifest, data = fixture(tmp_path)
    if part == "bundle":
        (renderer / "main.dart.js").write_text("changed")
    elif part == "asset":
        (renderer / "index.html").write_text("changed")
    elif part == "flet":
        data["flet"]["version"] = "0.0.0"
    elif part == "flutter":
        data["flutter_sdk"]["flutter_version"] = "0.0.0"
    elif part == "repair":
        data["web_output"]["repair_markers"].clear()
    elif part == "schema":
        data["schema"] = "unknown"
    else:
        (renderer / "linked").symlink_to(renderer / "index.html")
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        verify(renderer, manifest)


@pytest.mark.parametrize("mode", ["unselected", "absent", "ci"])
def test_optional_check_never_launches_without_local_inputs(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, mode: str, tmp_path: Path
) -> None:
    monkeypatch.delenv("FLET_WEB_PATH", raising=False)
    monkeypatch.delenv("CI", raising=False)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Optional guard must not start browser or server")

    monkeypatch.setattr(screenshots, "capture", forbidden)
    monkeypatch.setattr(screenshots, "serve", forbidden)
    args = []
    if mode == "absent":
        args = ["--renderer", str(tmp_path / "absent"), "--manifest", str(tmp_path / "missing")]
    if mode == "ci":
        monkeypatch.setenv("CI", "true")
        args = ["--serve", "12345"]
    assert screenshots.main(args) == 0
    assert capsys.readouterr().out.startswith("SKIP:")
    assert not (tmp_path / "capture.json").exists()


def test_capture_selects_case_from_registered_page_route(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    registered = []
    calls = []
    options = {}

    def register(callback: Any, **kwargs: Any) -> None:
        registered.append(callback)
        options.update(kwargs)

    monkeypatch.setattr(screenshots.ft, "run", register)
    screenshots.serve(8555)
    assert options["web_renderer"] == screenshots.ft.WebRenderer.CANVAS_KIT
    page = SimpleNamespace(
        route="/?case=filter-field-320-dark-error",
        query=SimpleNamespace(to_dict={}),
        url="http://127.0.0.1:8555",
        render=lambda *args: calls.append(args),
    )
    registered[0](page)
    assert page.title == "filter-field-320-dark-error"
    assert page.padding == 0
    from tests.support.catalog.rendering import controls
    from tests.support.host_harness import render_component, run

    async def scenario() -> None:
        app = await render_component(calls[0][0])
        assert any(
            isinstance(node, screenshots.ft.Semantics)
            and node.label == "Catalog specimen filter-field-320-dark-error"
            for node in controls(app)
        )
        assert app.find("catalog-preview-filter-field-320-dark-error").width == 320
        assert app.find("sample-control").error_text == "Example validation error"

    run(scenario())


def test_explicit_capture_covers_complete_matrix_without_running_browser(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    renderer, manifest, data = fixture(tmp_path)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("FLET_WEB_PATH", str(renderer))
    captured = []
    monkeypatch.setattr(screenshots, "capture", lambda *args: captured.append(args))
    assert (
        screenshots.main(
            [
                "--renderer",
                str(renderer),
                "--manifest",
                str(manifest),
                "--browser",
                "/local/browser-cli",
                "--output",
                str(tmp_path / "captures"),
            ]
        )
        == 0
    )
    browser, output, selected, digest = captured[0]
    assert browser == "/local/browser-cli"
    assert output == tmp_path / "captures"
    assert selected == list(screenshots.cases())
    assert len(selected) == 1440
    assert digest == data["web_output"]["tree_sha256"]


def test_inherited_bind_environment_cannot_expose_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    app = importlib.import_module("flet.app")
    received = {}

    class RefuseBind(Exception):
        pass

    async def refuse_server(**kwargs: Any) -> None:
        received.update(kwargs)
        raise RefuseBind

    monkeypatch.setenv("FLET_SERVER_IP", "0.0.0.0")  # noqa: S104 - adversarial input; bind is refused
    monkeypatch.setenv("FLET_SERVER_PORT", "49991")
    monkeypatch.setenv("FLET_FORCE_WEB_SERVER", "false")
    monkeypatch.setattr(app, "__run_web_server", refuse_server)
    monkeypatch.setattr(app.signal, "signal", lambda *args: None)
    with pytest.raises(RefuseBind):
        screenshots.serve(45678)
    assert received["host"] == "127.0.0.1"
    assert received["port"] == 45678
    assert screenshots.os.environ["FLET_SERVER_IP"] == "0.0.0.0"  # noqa: S104 - original input restored
    assert screenshots.os.environ["FLET_SERVER_PORT"] == "49991"


def test_failed_capture_keeps_incomplete_proof_and_rejects_reuse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    renderer, manifest, _ = fixture(tmp_path)
    output = tmp_path / "captures"
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("FLET_WEB_PATH", str(renderer))

    def fail_after_image(*args: Any) -> None:
        (output / "partial.png").write_bytes(b"incomplete image")
        raise RuntimeError("Capture interrupted")

    monkeypatch.setattr(screenshots, "capture", fail_after_image)
    args = [
        "--renderer",
        str(renderer),
        "--manifest",
        str(manifest),
        "--browser",
        "/local/browser-cli",
        "--output",
        str(output),
    ]
    with pytest.raises(RuntimeError, match="interrupted"):
        screenshots.main(args)
    proof = json.loads((output / "capture.json").read_text())
    assert proof["status"] == "incomplete" and len(proof["cases"]) == 1440
    (output / "capture.json").write_text('{"status":"complete","cases":["prior"]}')
    with pytest.raises(ValueError, match="new output directory"):
        screenshots.main(args)
    assert json.loads((output / "capture.json").read_text())["cases"] == ["prior"]
    assert (output / "partial.png").read_bytes() == b"incomplete image"


def test_delayed_flutter_fonts_and_native_state_block_capture(tmp_path: Path) -> None:
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the local capture readiness unit probe")
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            node,
            str(root / "tests/support/catalog/readiness.mjs"),
            str(root / "src/relaydeck/devtools/catalog_capture.mjs"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_full_capture_loop_rejects_late_failures_and_lost_state() -> None:
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the local capture loop unit probe")
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            node,
            str(root / "tests/support/catalog/capture_loop.mjs"),
            str(root / "src/relaydeck/devtools/catalog_capture.mjs"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
