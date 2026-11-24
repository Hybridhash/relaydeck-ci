"""Release build manifest emitter.

Emits the release manifest as JSON: the package `version`, app revision, Flet version,
Python dev/packaged versions, Flutter version, native extension versions,
build-template revision, lockfile hash, Android SDK/JDK versions,
target/min SDK, upstream schema hash, and APK/AAB hashes.

Also recorded: `patches` (every file under
patches/ with sha256 plus one combined hash), `flutter_sdk` (version, engine
revision and whether the patched files match their recorded hashes),
`lockfiles`, and `web_output` (main.dart.js hash, repair markers, font set,
whole-tree hash). `--check-markers PATH` is the build gate used by
scripts/build_web_client.sh.

Every field is either measured from the local tree/toolchain (or from
explicit CLI inputs) or emitted as {"status": "unavailable", "reason": ...}.
This script never fabricates a value. Stdlib plus relaydeck.releases.bundles (the marker table).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata as md
import importlib.util
import json
import os
import platform
import re
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from relaydeck.releases.bundles import REPAIR_MARKERS, csp_for_index, markers_ok, repair_markers

SCHEMA = "relaydeck.build-manifest/1"
CSP_POLICY_MODE = "report-only"
PATCHES_DIR = Path("patches")
LOCKFILE_NAMES = ("uv.lock", "pubspec.lock")
UPSTREAM_SCHEMA = Path("tests/fixtures/upstream/herdr-0.9.1-schema.json")
LOCKFILE = Path("uv.lock")


def unavailable(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason}


def is_unavailable(value: object) -> bool:
    return isinstance(value, dict) and value.get("status") == "unavailable"


def sha256_file(path: Path) -> dict[str, object]:
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        return unavailable(f"cannot read {path}: {exc}")
    try:
        shown = str(path.relative_to(Path.cwd()))
    except ValueError:
        shown = str(path)
    return {
        "path": shown,
        "sha256": digest,
        "bytes": path.stat().st_size,
    }


def run_capture(argv: list[str], timeout: int = 60) -> str | None:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603 - argv fixed by in-file callers, no shell
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def app_revision(root: Path) -> dict[str, str]:
    out = run_capture(["git", "-C", str(root), "rev-parse", "HEAD"])
    if out:
        return {"revision": out, "source": "git rev-parse HEAD"}
    if not (root / ".git").exists():
        return unavailable("not a git checkout (no .git directory)")
    return unavailable("git rev-parse HEAD failed or git is not installed")


def flet_version() -> dict[str, str]:
    try:
        return {"version": md.version("flet"), "source": "importlib.metadata"}
    except md.PackageNotFoundError:
        return unavailable("flet is not installed in this environment")


def flutter_version() -> dict[str, str]:
    out = run_capture(["flet", "--version"])
    if out is None:
        # `uv run` resolves flet from the venv; a bare `flet` miss means the
        # CLI is not on PATH outside the project environment.
        return unavailable("`flet --version` failed (flet-cli not on PATH?)")
    match = re.search(r"Flutter:\s*(\S+)", out)
    if match:
        return {"version": match.group(1), "source": "`flet --version`"}
    return unavailable(f"`flet --version` did not report Flutter (got: {out!r})")


def _ini_value(text: str, key: str) -> str | None:
    """Read `key = value` (TOML) or `key: value` (pubspec) at line start."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(key):
            continue
        rest = stripped[len(key) :].lstrip()
        if rest.startswith("="):
            _, _, value = rest.partition("=")
        elif rest.startswith(":"):
            _, _, value = rest.partition(":")
        else:
            continue
        cleaned = value.strip().strip(chr(39) + chr(34))
        return cleaned or None
    return None


def _pubspec_section(text: str, section: str) -> dict[str, str]:
    """Parse one top-level mapping section of a simple pubspec.yaml."""
    deps: dict[str, str] = {}
    in_section = False
    current: str | None = None
    for line in text.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            in_section = stripped.rstrip(":") == section
            current = None
            continue
        if not in_section:
            continue
        if indent == 2 and stripped.endswith(":") and " " not in stripped[:-1]:
            current = stripped[:-1]
            deps[current] = ""
        elif indent == 2 and ":" in stripped:
            name, _, constraint = stripped.partition(":")
            deps[name.strip()] = constraint.strip()
        elif current is not None and indent > 2 and ":" in stripped:
            key, _, value = stripped.partition(":")
            extra = f"{key.strip()}: {value.strip()}".strip()
            deps[current] = f"{deps[current]} ({extra})".strip()
    return deps


def native_extensions(root: Path) -> dict[str, object]:
    extensions: dict[str, object] = {}
    ext_root = root / "extensions"
    if not ext_root.is_dir():
        return unavailable("extensions/ directory not found")
    for entry in sorted(ext_root.iterdir()):
        if not entry.is_dir():
            continue
        record: dict[str, object] = {}
        pyproject = entry / "pyproject.toml"
        if pyproject.is_file():
            record["python_version"] = _ini_value(
                pyproject.read_text(encoding="utf-8"), "version"
            ) or unavailable(f"no version in {pyproject}")
        else:
            record["python_version"] = unavailable(f"{pyproject} not found")
        pubspecs = sorted(entry.glob("src/flutter/*/pubspec.yaml"))
        if not pubspecs:
            record["dart"] = unavailable("no src/flutter/*/pubspec.yaml found")
        else:
            dart: dict[str, object] = {}
            for pubspec in pubspecs:
                text = pubspec.read_text(encoding="utf-8")
                dart[pubspec.parent.name] = {
                    "version": _ini_value(text, "version")
                    or unavailable(f"no version in {pubspec}"),
                    "dependencies": _pubspec_section(text, "dependencies"),
                    "dev_dependencies": _pubspec_section(text, "dev_dependencies"),
                }
            record["dart"] = dart
        extensions[entry.name] = record
    if not extensions:
        return unavailable("no extensions found under extensions/")
    return extensions


def android_sdk() -> dict[str, str]:
    home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    manager = run_capture(["sdkmanager", "--version"])
    if manager is None and home is None:
        return unavailable(
            "no Android SDK: sdkmanager not on PATH and ANDROID_HOME/ANDROID_SDK_ROOT unset"
        )
    record: dict[str, str] = {}
    if home:
        record["home"] = home
    else:
        record["home"] = unavailable("ANDROID_HOME/ANDROID_SDK_ROOT unset")  # type: ignore[assignment]
    if manager is not None:
        record["sdkmanager"] = manager
    else:
        record["sdkmanager"] = unavailable("sdkmanager not on PATH")  # type: ignore[assignment]
    return record


def jdk_version() -> dict[str, str]:
    # `java --version` prints to stdout on modern JDKs, stderr on JDK 8.
    try:
        proc = subprocess.run(["java", "--version"], capture_output=True, text=True, timeout=60)  # noqa: S607 - argv fixed by in-file callers, no shell
    except (OSError, subprocess.TimeoutExpired):
        return unavailable("java executable not found")
    combined = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0 or not combined:
        return unavailable("`java --version` failed (no Java runtime?)")
    return {"version": combined.splitlines()[0], "source": "`java --version`"}


def target_sdk(manifest_path: str | None, root: Path) -> dict[str, object]:
    if not manifest_path:
        return unavailable(
            "no generated AndroidManifest.xml supplied (pass --android-manifest after a build)"
        )
    path = Path(manifest_path)
    if not path.is_file():
        return unavailable(f"manifest not found: {manifest_path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    record: dict[str, object] = {"manifest": str(path)}
    for attr in ("minSdkVersion", "targetSdkVersion", "compileSdkVersion"):
        match = re.search(re.escape(attr) + r'="([^"]+)"', text)
        record[attr] = match.group(1) if match else unavailable(f"{attr} not in {path}")
    _ = root
    return record


def artifact(path_value: str | None, kind: str) -> dict[str, object]:
    if not path_value:
        return unavailable(f"no {kind} path supplied (pass --{kind.lower()})")
    return sha256_file(Path(path_value))


def sha256_tree(root: Path, *, skip: tuple[str, ...] = ()) -> dict[str, object]:
    """Per-file hashes plus one combined hash (sorted relative paths)."""
    if not root.is_dir():
        return unavailable(f"not a directory: {root}")
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in skip for part in rel.split("/")):
            continue
        files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    combined = hashlib.sha256(
        "".join(f"{rel}\0{digest}\n" for rel, digest in files.items()).encode()
    ).hexdigest()
    return {"files": files, "count": len(files), "combined_sha256": combined}


def patches_record(root: Path) -> Mapping[str, object]:
    base = root / PATCHES_DIR
    if not base.is_dir():
        return unavailable("patches/ directory not found")
    return {entry.name: sha256_tree(entry) for entry in sorted(base.iterdir()) if entry.is_dir()}


def lockfiles(root: Path) -> Mapping[str, object]:
    found: dict[str, object] = {}
    for name in LOCKFILE_NAMES:
        for path in sorted(root.glob(name)) + sorted(root.glob(f"extensions/*/{name}")):
            found[path.relative_to(root).as_posix()] = sha256_file(path)
    return found or unavailable("no lockfiles found")


def flutter_sdk_record(sdk: str | None, root: Path) -> Mapping[str, object]:
    if not sdk:
        return unavailable("no SDK supplied (pass --flutter-sdk)")
    base = Path(sdk)
    version_file = base / "bin/cache/flutter.version.json"
    try:
        doc = json.loads(version_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return unavailable(f"cannot read {version_file}: {exc}")
    record: dict[str, object] = {
        "path": str(base),
        "flutter_version": doc.get("flutterVersion"),
        "framework_revision": doc.get("frameworkRevision"),
        "engine_revision": doc.get("engineRevision"),
        "dart_sdk_version": doc.get("dartSdkVersion"),
    }
    expected = root / PATCHES_DIR / "flutter-ios-pointer-patch/expected-sha256.txt"
    try:
        lines = expected.read_text(encoding="utf-8").splitlines()
    except OSError:
        record["pointer_patch"] = unavailable(f"{expected} not found")
        return record
    states: dict[str, str] = {}
    for line in lines:
        if not line.strip() or line.startswith("#"):
            continue
        pristine, patched, rel = line.split(None, 2)
        try:
            digest = hashlib.sha256((base / rel).read_bytes()).hexdigest()
        except OSError:
            states[rel] = "missing"
            continue
        states[rel] = (
            "patched" if digest == patched else "pristine" if digest == pristine else "unknown"
        )
    record["pointer_patch"] = {
        "files": states,
        "patched": bool(states) and all(state == "patched" for state in states.values()),
    }
    return record


# Shell first-load budget: the shell set (SHELL_FIXED plus CanvasKit, from
# scripts/web_service_worker.py) may not grow more than this many percent in
# raw or gzip bytes over the previous build manifest's record.
SHELL_BUDGET_MAX_GROWTH_PCT = 5.0


def _shell_member_names() -> tuple[str, ...]:
    """SHELL_FIXED plus CanvasKit, read from the service-worker script (one source)."""
    sibling = Path(__file__).resolve().with_name("web_service_worker.py")
    spec = importlib.util.spec_from_file_location("web_service_worker", sibling)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load scripts/web_service_worker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(module.SHELL_FIXED) + tuple(module.CANVASKIT_FILES)


def shell_sizes(base: Path) -> dict[str, object]:
    """Raw and gzip bytes per shell member present in the built tree, plus totals."""
    files: dict[str, dict[str, int]] = {}
    for name in _shell_member_names():
        path = base / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        files[name] = {"bytes": len(data), "gzip_bytes": len(gzip.compress(data, mtime=0))}
    return {
        "files": files,
        "total_bytes": sum(entry["bytes"] for entry in files.values()),
        "total_gzip_bytes": sum(entry["gzip_bytes"] for entry in files.values()),
    }


def shell_budget_check(
    previous_manifest: str, web_out: str, *, max_growth_pct: float = SHELL_BUDGET_MAX_GROWTH_PCT
) -> dict[str, object]:
    """Compare this build's shell totals with a previous manifest's recorded totals.

    A previous manifest written before the size record existed has no
    baseline: the check reports `baseline: false` and passes, so the first
    new manifest becomes the baseline the next build compares with.
    """
    current = shell_sizes(Path(web_out))
    previous_doc = json.loads(Path(previous_manifest).read_text(encoding="utf-8"))
    previous = previous_doc.get("web_output", {}).get("shell_sizes")
    if not isinstance(previous, dict) or not previous.get("total_bytes"):
        return {"baseline": False, "current": current, "ok": True}
    outcome: dict[str, object] = {"baseline": True, "ok": True}
    for key in ("total_bytes", "total_gzip_bytes"):
        before = previous[key]
        after = current[key]
        if not isinstance(before, int) or not isinstance(after, int):
            raise ValueError(f"shell size record has a non-integer {key}")
        growth = (after - before) * 100.0 / before if before else 0.0
        outcome[key] = {"previous": before, "current": after, "growth_pct": round(growth, 3)}
        if growth > max_growth_pct:
            outcome["ok"] = False
    outcome["max_growth_pct"] = max_growth_pct
    return outcome


def web_output_record(out: str | None) -> Mapping[str, object]:
    if not out:
        return unavailable("no web output supplied (pass --web-out)")
    base = Path(out)
    bundle = base / "main.dart.js"
    try:
        js = bundle.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return unavailable(f"cannot read {bundle}: {exc}")
    tree = sha256_tree(base)
    files = tree.get("files")
    fonts = (
        {rel: digest for rel, digest in files.items() if "/fonts/" in f"/{rel}"}
        if isinstance(files, dict)
        else {}
    )
    return {
        "main_dart_js": sha256_file(bundle),
        "repair_markers": repair_markers(js),
        "fonts": {
            "count": len(fonts),
            "combined_sha256": hashlib.sha256(
                "".join(f"{rel}\0{digest}\n" for rel, digest in sorted(fonts.items())).encode()
            ).hexdigest(),
        },
        "tree_sha256": tree.get("combined_sha256"),
        "file_count": tree.get("count"),
        "shell_sizes": shell_sizes(base),
    }


def prepare_hosted_index(out: Path) -> None:
    """Bake the pinned root configuration before worker and policy hashing."""
    from flet.controls.types import RouteUrlStrategy, WebRenderer
    from flet.utils import get_bool_env_var
    from flet_web.patch_index import patch_index_html

    from relaydeck.contracts import paths

    index = out / "index.html"
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
    body = index.read_bytes()
    script = f'<script src="{paths.AUTH_RECOVERY_SCRIPT}"></script>'.encode()
    if script not in body:
        body = body.replace(b"<head>", b"<head>\n" + script, 1)
    index.write_bytes(body)


def web_csp_record(out: str | None) -> Mapping[str, object]:
    if not out:
        return unavailable("no web output supplied (pass --web-out)")
    try:
        return csp_for_index((Path(out) / "index.html").read_bytes(), CSP_POLICY_MODE)
    except (OSError, ValueError):
        return unavailable("final hosted index is unavailable")


def package_version(root: Path) -> str | dict[str, Any]:
    """The release version, read from pyproject.toml (never from package metadata,
    so a manifest built in a tree always names that tree's version)."""
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        version = data["project"]["version"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return unavailable(f"cannot read the package version: {exc}")
    return version if isinstance(version, str) else unavailable("version is not text")


def build_manifest(
    root: Path,
    *,
    aab: str | None,
    apk: str | None,
    android_manifest: str | None,
    template_rev: str | None,
    packaged_python: str | None,
    flutter_sdk: str | None = None,
    web_out: str | None = None,
) -> dict[str, object]:
    lock = (
        sha256_file(root / LOCKFILE)
        if (root / LOCKFILE).is_file()
        else unavailable("uv.lock not found")
    )
    schema = (
        sha256_file(root / UPSTREAM_SCHEMA)
        if (root / UPSTREAM_SCHEMA).is_file()
        else unavailable(f"{UPSTREAM_SCHEMA} not found")
    )
    return {
        "schema": SCHEMA,
        "version": package_version(root),
        "generated_at": datetime.now(UTC).isoformat(),
        "app": app_revision(root),
        "flet": flet_version(),
        "python_dev": {
            "version": platform.python_version(),
            "source": "platform.python_version()",
        },
        "python_packaged": (
            {"version": packaged_python, "source": "--packaged-python flag"}
            if packaged_python
            else unavailable(
                "packaged interpreter known only after a build "
                "(pass --packaged-python from build output)"
            )
        ),
        "flutter": flutter_version(),
        "native_extensions": native_extensions(root),
        "build_template": (
            {"revision": template_rev, "source": "--template-rev flag"}
            if template_rev
            else unavailable(
                "build-template revision known only at build time "
                "(pass --template-rev from `flet build --template-ref` or logs)"
            )
        ),
        "lockfile": lock,
        "android_sdk": android_sdk(),
        "jdk": jdk_version(),
        "android_sdk_versions": target_sdk(android_manifest, root),
        "upstream_schema": schema,
        "artifacts": {"aab": artifact(aab, "aab"), "apk": artifact(apk, "apk")},
        "patches": patches_record(root),
        "lockfiles": lockfiles(root),
        "flutter_sdk": flutter_sdk_record(flutter_sdk, root),
        "web_output": web_output_record(web_out),
        "web": {"csp": web_csp_record(web_out)},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit the release build manifest.")
    parser.add_argument("--out", help="write JSON to PATH instead of stdout")
    parser.add_argument("--aab", help="path to a built .aab for hashing")
    parser.add_argument("--apk", help="path to a built .apk for hashing")
    parser.add_argument("--android-manifest", help="generated AndroidManifest.xml for SDK levels")
    parser.add_argument("--template-rev", help="flet build-template revision")
    parser.add_argument("--packaged-python", help="packaged interpreter version")
    parser.add_argument("--root", default="", help="repo root (default: script parent)")
    parser.add_argument("--flutter-sdk", help="Flutter SDK directory used for the build")
    parser.add_argument("--web-out", help="built web client directory (holds main.dart.js)")
    parser.add_argument("--prepare-hosted-index", type=Path, help="bake the hosted configuration")
    parser.add_argument("--write-web-csp", type=Path, help="write the final hosted policy record")
    parser.add_argument(
        "--check-markers",
        metavar="MAIN_DART_JS",
        help="exit 1 unless the compiled bundle carries every repair marker",
    )
    parser.add_argument(
        "--check-shell-budget",
        metavar="PREVIOUS_MANIFEST",
        help="exit 1 if --web-out's shell set grew past the budget vs that manifest",
    )
    parser.add_argument(
        "--patches",
        default="",
        help="comma-separated patch folders the marker check applies to (default: all)",
    )
    args = parser.parse_args(argv)
    if args.prepare_hosted_index:
        prepare_hosted_index(args.prepare_hosted_index)
        return 0
    if args.write_web_csp:
        record = web_csp_record(str(args.write_web_csp))
        if "index_sha256" not in record:
            sys.stderr.write("csp_metadata_required\n")
            return 1
        (args.write_web_csp / "csp.json").write_text(
            json.dumps(record, sort_keys=True) + "\n", encoding="utf-8"
        )
        return 0
    if args.check_shell_budget:
        if not args.web_out:
            sys.stderr.write("--check-shell-budget needs --web-out\n")
            return 2
        try:
            outcome = shell_budget_check(args.check_shell_budget, args.web_out)
        except (OSError, ValueError) as exc:
            sys.stderr.write(f"shell budget check failed: {exc}\n")
            return 1
        sys.stdout.write(json.dumps(outcome, indent=2, sort_keys=True) + chr(10))
        if not outcome["ok"]:
            sys.stderr.write(
                f"shell budget exceeded: growth past {SHELL_BUDGET_MAX_GROWTH_PCT}% "
                "over the previous build\n"
            )
            return 1
        return 0
    if args.check_markers:
        try:
            js = Path(args.check_markers).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            sys.stderr.write(f"cannot read {args.check_markers}: {exc}\n")
            return 1
        only = tuple(name for name in args.patches.split(",") if name)
        unknown = [name for name in only if name not in REPAIR_MARKERS]
        if unknown:
            sys.stderr.write(f"no repair markers defined for: {', '.join(unknown)}\n")
            return 1
        markers = repair_markers(js, only)
        sys.stdout.write(json.dumps(markers, indent=2, sort_keys=True) + chr(10))
        if not markers_ok(markers):
            sys.stderr.write("repair markers missing: the patches are not in this bundle\n")
            return 1
        return 0
    root = Path(args.root or Path(__file__).resolve().parent.parent)
    manifest = build_manifest(
        root,
        aab=args.aab,
        apk=args.apk,
        android_manifest=args.android_manifest,
        template_rev=args.template_rev,
        packaged_python=args.packaged_python,
        flutter_sdk=args.flutter_sdk,
        web_out=args.web_out,
    )
    text = json.dumps(manifest, indent=2, sort_keys=True) + chr(10)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
