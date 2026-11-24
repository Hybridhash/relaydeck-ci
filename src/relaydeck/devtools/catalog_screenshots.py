"""Opt-in local screenshots using a verified prebuilt renderer and a local browser."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import flet as ft
import httpx

from relaydeck.client.design_system.appearance import build_theme
from relaydeck.client.design_system.fonts import page_fonts
from relaydeck.devtools.catalog import Preview
from relaydeck.devtools.catalog_model import ENTRIES, STATES, THEMES, WIDTHS, Case, cases
from relaydeck.devtools.catalog_renderer import verify


@contextmanager
def process(
    args: list[str], log: Path, env: dict[str, str] | None = None
) -> Iterator[subprocess.Popen[bytes]]:
    with log.open("w") as output:
        child = subprocess.Popen(  # noqa: S603 - explicit local interpreter/tool argv; no shell
            args, stdout=output, stderr=subprocess.STDOUT, env=env
        )
        try:
            yield child
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)


def port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_server(origin: str, child: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError("Catalog server exited before becoming ready; see server.log")
        try:
            with httpx.Client(trust_env=False, timeout=1) as client:
                if client.get(origin).status_code == 200:
                    return
        except httpx.HTTPError:
            time.sleep(0.05)
    raise TimeoutError("Catalog server did not become ready")


def serve(server_port: int) -> None:
    def page_main(page: ft.Page) -> None:
        query = parse_qs(urlsplit(page.route).query)
        key = query.get("case", [""])[0]
        case = next(c for c in cases() if c.key == key)
        page.title = case.key
        page.padding = 0
        page.bgcolor = "#000000" if case.dark else "#ffffff"
        page.fonts = page_fonts()
        page.theme = build_theme(case.dark)
        page.dark_theme = build_theme(True)
        page.render(
            lambda: ft.Semantics(label="Catalog specimen " + case.key, content=Preview(case))
        )

    pinned = {
        "FLET_SERVER_IP": "127.0.0.1",
        "FLET_SERVER_PORT": str(server_port),
        "FLET_FORCE_WEB_SERVER": "true",
    }
    previous = {name: os.environ.get(name) for name in pinned}
    os.environ.update(pinned)
    try:
        ft.run(
            page_main,
            host="127.0.0.1",
            port=server_port,
            view=None,
            web_renderer=ft.WebRenderer.CANVAS_KIT,
            export_asgi_app=False,
            no_cdn=True,
            assets_dir=str(Path(__file__).parents[1] / "client/assets"),
        )
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def capture(browser: str, output: Path, selected: list[Case], renderer_digest: str) -> None:
    server_port = port()
    origin = f"http://127.0.0.1:{server_port}"
    env = dict(
        os.environ,
        FLET_FORCE_WEB_SERVER="true",
        FLET_SERVER_IP="127.0.0.1",
        FLET_SERVER_PORT=str(server_port),
    )
    settings = json.dumps(
        {
            "origin": origin,
            "output": str(output.resolve()),
            "digest": renderer_digest,
            "fonts": list(page_fonts()),
            "cases": [
                {
                    "key": c.key,
                    "width": c.width,
                    "focus": c.state == "focus" and c.definition.focusable,
                    "overlay": c.entry == "action-toast",
                }
                for c in selected
            ],
        }
    )
    module = Path(__file__).with_name("catalog_capture.mjs").as_uri()
    script = (
        "const {capture} = await import(" + json.dumps(module) + ");"
        "await capture(" + settings + ", taskSpace);"
    )
    with process(
        [
            sys.executable,
            "-m",
            "relaydeck.devtools.catalog_screenshots",
            "--serve",
            str(server_port),
        ],
        output / "server.log",
        env,
    ) as server:
        wait_server(origin, server)
        with process([browser, "nodejs", "-e", script], output / "browser.log") as driver:
            while driver.poll() is None:
                if server.poll() is not None:
                    raise RuntimeError("Catalog server exited; see server.log")
                time.sleep(0.1)
            if driver.returncode != 0:
                raise RuntimeError("Local browser capture failed; see browser.log")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--renderer", type=Path, default=os.environ.get("FLET_WEB_PATH"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--browser",
        help="Path to an already installed local browser CLI that runs the capture script with "
        "`nodejs -e`",
    )
    parser.add_argument("--output", type=Path, default=Path("temp/catalog-screenshots"))
    parser.add_argument("--control", choices=[e.id for e in ENTRIES])
    parser.add_argument("--width", type=int, choices=WIDTHS)
    parser.add_argument("--theme", choices=THEMES)
    parser.add_argument("--state", choices=STATES)
    parser.add_argument("--serve", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if os.environ.get("CI"):
        print("SKIP: catalog screenshots are a local-only check, disabled in CI.")
        return 0
    if args.serve is not None:
        serve(args.serve)
        return 0
    if args.renderer is None or args.manifest is None:
        print(
            "SKIP: no pinned renderer selected; supply --renderer and --manifest (no build is run)."
        )
        return 0
    if not args.renderer.is_dir() or not args.manifest.is_file():
        print("SKIP: the selected pinned renderer or its build manifest is absent.")
        return 0
    digest = verify(args.renderer, args.manifest)
    browser = args.browser
    if browser is None:
        raise ValueError("Install-free check needs an existing local browser CLI (--browser)")
    os.environ["FLET_WEB_PATH"] = str(args.renderer.resolve())
    if args.output.exists():
        raise ValueError(
            "Choose a new output directory; existing capture artifacts are never overwritten"
        )
    args.output.mkdir(parents=True)
    selected = [
        c
        for c in cases()
        if (args.control is None or c.entry == args.control)
        and (args.width is None or c.width == args.width)
        and (args.theme is None or c.theme == args.theme)
        and (args.state is None or c.state == args.state)
    ]
    (args.output / "capture.json").write_text(
        json.dumps({"status": "incomplete", "cases": [c.key for c in selected]}) + "\n"
    )
    capture(browser, args.output, selected, digest)
    print(f"Captured {len(selected)} local PNGs in {args.output}; inspect them before acceptance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
