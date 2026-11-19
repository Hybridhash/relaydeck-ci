"""Verify web bundle repair markers and manage private installed bundles. Publish only verified
copies and remove failed staging directories.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

MAIN_JS = "main.dart.js"
# Local built bundles use a bounded hexadecimal revision/hash component for their directory.
# Signed bundles instead use a validated archive SHA-256 under signed/.
BUILD_ID = re.compile(r"[0-9a-f]{12}-[0-9a-f]{12}|[0-9a-f]{16}")
_REVISION = re.compile(r"[0-9a-f]{12,64}")
MAX_FILES = 20_000
MAX_DEPTH = 32
MAX_BYTES = 1 << 30
MANIFEST_SUFFIX = ".build-manifest.json"
INSTALLED_NAME = "installed.json"
SIGNED_DIR = "signed"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")

# Calibrated string-literal counts identify required repairs in minified output.
REPAIR_MARKERS: dict[str, dict[str, int]] = {
    "flet-reconnect-merge-patch": {
        "flet_reconnect_merge_rd1": 1,
        "flet_reconnect_wake_rd1": 1,
        "item_extents": 1,
    },
    "flutter-ios-pointer-patch": {"touchend": 4, "touchcancel": 2},
    "flutter-file-upload-patch": {"abort": 3},
}


class BundleError(RuntimeError):
    """A bundle cannot be installed or is not usable; the message is user-facing."""


MAX_MANIFEST_BYTES = 1 << 20


def _read_regular(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise BundleError("csp_metadata_required")
        data = handle.read(limit + 1)
        if len(data) > limit:
            raise BundleError("csp_metadata_required")
        return data


class _InlineScripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.inline = False
        self.text: list[str] = []
        self.hashes: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.inline = not any(name == "src" for name, _value in attrs)
            self.text = []

    def handle_data(self, data: str) -> None:
        if self.inline:
            self.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.inline:
            digest = hashlib.sha256("".join(self.text).encode()).digest()
            self.hashes.add("sha256-" + base64.b64encode(digest).decode("ascii"))
            self.inline = False
            self.text = []


def csp_for_index(index: bytes, policy_mode: str = "report-only") -> dict[str, Any]:
    if policy_mode not in {"report-only", "enforce"}:
        raise BundleError("csp_metadata_required")
    parser = _InlineScripts()
    parser.feed(index.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n"))
    parser.close()
    if parser.inline:
        raise BundleError("csp_metadata_required")
    return {
        "index_sha256": hashlib.sha256(index).hexdigest(),
        "inline_script_hashes": sorted(parser.hashes),
        "policy_mode": policy_mode,
    }


def validate_csp(index: bytes, record: object) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise BundleError("csp_metadata_required")
    try:
        expected = csp_for_index(index, record["policy_mode"])
    except (KeyError, TypeError, ValueError, UnicodeError, BundleError):
        raise BundleError("csp_metadata_required") from None
    if record != expected:
        raise BundleError("csp_metadata_required")
    return expected


def load_csp(root: Path) -> dict[str, Any]:
    try:
        metadata = root / "csp.json"
        index = root / "index.html"
        if metadata.is_symlink() or index.is_symlink():
            raise ValueError
        if metadata.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError
        return validate_csp(
            _read_regular(index, MAX_BYTES), json.loads(_read_regular(metadata, MAX_MANIFEST_BYTES))
        )
    except (OSError, ValueError, UnicodeError, BundleError):
        raise BundleError("csp_metadata_required") from None


def _source_csp(source: Path) -> dict[str, Any]:
    try:
        manifest = source.with_name(source.name + MANIFEST_SUFFIX)
        data = json.loads(_read_regular(manifest, MAX_MANIFEST_BYTES))
        if data["schema"] != "relaydeck.build-manifest/1":
            raise BundleError("csp_metadata_required")
        record = validate_csp(_read_regular(source / "index.html", MAX_BYTES), data["web"]["csp"])
        if (source / "csp.json").exists() and load_csp(source) != record:
            raise BundleError("csp_metadata_required")
        return record
    except (OSError, ValueError, KeyError, TypeError):
        raise BundleError("csp_metadata_required") from None


def repair_markers(js: str, only: tuple[str, ...] = ()) -> dict[str, object]:
    """Count the calibrated string literals per repair; `ok` needs them all."""
    result: dict[str, object] = {}
    for patch, wanted in REPAIR_MARKERS.items():
        if only and patch not in only:
            continue
        counts = {literal: js.count(f'"{literal}"') for literal in wanted}
        result[patch] = {
            "counts": counts,
            "required": wanted,
            "ok": all(counts[lit] >= need for lit, need in wanted.items()),
        }
    return result


def markers_ok(markers: dict[str, object]) -> bool:
    return all(isinstance(record, dict) and record.get("ok") is True for record in markers.values())


def default_root() -> Path:
    return Path("~/Library/Application Support/RelayDeck/web").expanduser()


def _read_js(bundle: Path) -> str:
    try:
        return (bundle / MAIN_JS).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise BundleError(f"cannot read {bundle / MAIN_JS}: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_id(source: Path, digest: str) -> str:
    """`<commit12>-<hash12>` when the sibling build manifest names a commit, else the
    hash prefix. The manifest is `<source>.build-manifest.json` next to the bundle dir."""
    manifest = source.with_name(source.name + MANIFEST_SUFFIX)
    commit = ""
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            app = data.get("app") if isinstance(data, dict) else None
            revision = app.get("revision") if isinstance(app, dict) else None
            commit = revision if isinstance(revision, str) else ""
        except (OSError, ValueError):
            commit = ""
    if not _REVISION.fullmatch(commit):
        commit = ""  # anything else is not a revision and never reaches a path
    return f"{commit[:12]}-{digest[:12]}" if commit else digest[:16]


@dataclass(frozen=True)
class Installed:
    build_id: str
    path: Path
    installed_at: str
    markers: dict[str, object]


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _same(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _open_store(store: Path) -> int:
    """Open the store as a pinned directory descriptor: a real directory, owned by this user,
    that others cannot write. Everything below is addressed relative to this descriptor."""
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    real = store.resolve()
    try:
        fd = os.open(real, _DIR_FLAGS)
    except OSError as exc:
        raise BundleError(f"{store} is not a usable directory; not installed") from exc
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or info.st_mode & 0o022 or not _same(info, real.lstat()):
        os.close(fd)
        raise BundleError(f"{store} is not a private directory owned by you; not installed")
    return fd


def _copy_dir(
    src_fd: int,
    dst_fd: int,
    depth: int,
    budget: dict[str, int],
    prefix: str = "",
    inventory: list[dict[str, object]] | None = None,
) -> None:
    """Copy bounded regular files and directories through pinned, no-follow descriptors. Refuse
    symlinks, special files and entries replaced after listing.
    """
    if depth > MAX_DEPTH:
        raise BundleError("the bundle is nested too deeply; not installed")
    for name in os.listdir(src_fd):
        info = os.stat(name, dir_fd=src_fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, _DIR_FLAGS, dir_fd=src_fd)
            try:
                if not _same(os.fstat(child), info):
                    raise BundleError(f"{name} changed while copying; not installed")
                os.mkdir(name, 0o700, dir_fd=dst_fd)
                out_dir = os.open(name, _DIR_FLAGS, dir_fd=dst_fd)
                try:
                    _copy_dir(child, out_dir, depth + 1, budget, f"{prefix}{name}/", inventory)
                finally:
                    os.close(out_dir)
            finally:
                os.close(child)
            continue
        if not stat.S_ISREG(info.st_mode):
            raise BundleError(f"{name} is a link or special file; a bundle may hold only files")
        budget["files"] += 1
        if budget["files"] > MAX_FILES:
            raise BundleError("the bundle is too large; not installed")
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=src_fd)
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or not _same(opened, info):
                raise BundleError(f"{name} changed while copying; not installed")
            out = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dst_fd
            )
            digest = hashlib.sha256()
            size = 0
            with os.fdopen(out, "wb") as sink:
                while chunk := handle.read(1 << 20):
                    budget["bytes"] += len(chunk)
                    if budget["bytes"] > MAX_BYTES:
                        raise BundleError("the bundle is too large; not installed")
                    digest.update(chunk)
                    size += len(chunk)
                    sink.write(chunk)
            if inventory is not None:
                inventory.append(
                    {"path": f"{prefix}{name}", "size": size, "sha256": digest.hexdigest()}
                )


def _prepare(source: Path) -> tuple[Path, str, str, dict[str, Any]]:
    """The source checks shared by every install: markers, main hash, build id."""
    origin = Path(source).expanduser().resolve()
    if not (origin / MAIN_JS).is_file():
        raise BundleError(f"no {MAIN_JS} in {origin}")
    if (origin / MAIN_JS).is_symlink():
        raise BundleError(f"{MAIN_JS} must be a regular file; not installed")
    markers = repair_markers(_read_js(origin))
    if not markers_ok(markers):
        raise BundleError(
            "repair markers missing: the patches are not in this bundle; not installed"
        )
    digest = _sha256(origin / MAIN_JS)
    csp = _source_csp(origin)
    identity = hashlib.sha256(
        (digest + "\n" + json.dumps(csp, sort_keys=True, separators=(",", ":"))).encode()
    ).hexdigest()
    bid = build_id(origin, identity)
    if BUILD_ID.fullmatch(bid) is None:
        raise BundleError("the bundle identity is not valid; not installed")
    return origin, digest, bid, csp


def install(source: Path, *, root: Path | None = None) -> Installed:
    """Copy a built web bundle into the store after its markers verify on the copy.

    Refuses (BundleError) when the source lacks the markers or the copy fails
    verification; in both cases nothing is left in the store.
    """
    origin, digest, bid, csp = _prepare(source)
    store = root or default_root()
    store_fd = _open_store(store)
    try:
        dest = store.resolve() / bid
        try:
            present = os.stat(bid, dir_fd=store_fd, follow_symlinks=False)
        except FileNotFoundError:
            present = None
        if present is not None:
            existing = read_installed(dest)
            if (
                stat.S_ISDIR(present.st_mode)
                and existing is not None
                and verify(dest)
                and load_csp(dest) == csp
            ):
                return existing
            raise BundleError(f"{dest} exists but does not verify; remove it by hand first")
        return _install_copy(origin, store.resolve(), store_fd, bid, digest, store, csp=csp)
    finally:
        os.close(store_fd)


def _install_copy(
    origin: Path,
    store: Path,
    store_fd: int,
    bid: str,
    digest: str,
    shown: Path,
    dest_name: str | None = None,
    extra: dict[str, object] | None = None,
    *,
    csp: dict[str, Any],
) -> Installed:
    dest_name = dest_name or bid
    # An independent, unpredictable staging directory created through the pinned store
    # descriptor; only it is ever removed.
    token = ".staging-" + secrets.token_hex(8)
    os.mkdir(token, 0o700, dir_fd=store_fd)
    try:
        stage_fd = os.open(token, _DIR_FLAGS, dir_fd=store_fd)
        try:
            os.mkdir("bundle", 0o700, dir_fd=stage_fd)
            bundle_fd = os.open("bundle", _DIR_FLAGS, dir_fd=stage_fd)
            try:
                try:
                    src_fd = os.open(origin, _DIR_FLAGS)
                except OSError as exc:
                    raise BundleError(f"cannot open {origin}: {exc}") from exc
                inventory: list[dict[str, object]] | None = [] if extra is not None else None
                try:
                    _copy_dir(src_fd, bundle_fd, 0, {"files": 0, "bytes": 0}, "", inventory)
                except OSError as exc:
                    # A refused no-follow open means an entry was replaced under the copy.
                    raise BundleError(
                        f"the bundle changed while it was being copied ({exc.strerror}); "
                        "not installed"
                    ) from exc
                finally:
                    os.close(src_fd)
                staged = store / token / "bundle"
                copied = repair_markers(_read_js(staged))
                if not markers_ok(copied) or _sha256(staged / MAIN_JS) != digest:
                    raise BundleError("the copy did not verify; nothing was installed")
                validate_csp(_read_regular(staged / "index.html", MAX_BYTES), csp)
                csp_path = staged / "csp.json"
                if csp_path.exists():
                    if load_csp(staged) != csp:
                        raise BundleError("csp_metadata_required")
                else:
                    csp_bytes = (json.dumps(csp, sort_keys=True) + "\n").encode()
                    csp_fd = os.open(
                        "csp.json",
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=bundle_fd,
                    )
                    with os.fdopen(csp_fd, "wb") as sink:
                        sink.write(csp_bytes)
                    if inventory is not None:
                        inventory.append(
                            {
                                "path": "csp.json",
                                "size": len(csp_bytes),
                                "sha256": hashlib.sha256(csp_bytes).hexdigest(),
                            }
                        )
                installed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                record: dict[str, object] = {
                    "build_id": bid,
                    "source": str(origin),
                    "main_dart_js_sha256": digest,
                    "markers": copied,
                    "csp": csp,
                    "installed_at": installed_at,
                }
                if extra is not None:
                    record.update(extra)
                    record["source"] = str(extra.get("source") or origin)
                    record["files"] = sorted(inventory or (), key=lambda item: str(item["path"]))
                out = os.open(
                    INSTALLED_NAME,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=bundle_fd,
                )
                with os.fdopen(out, "w", encoding="utf-8") as sink:
                    sink.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
            finally:
                os.close(bundle_fd)
            try:
                os.rename("bundle", dest_name, src_dir_fd=stage_fd, dst_dir_fd=store_fd)
            except OSError as exc:
                raise BundleError(f"cannot place the bundle in the store: {exc}") from exc
        finally:
            os.close(stage_fd)
    except BaseException:
        shutil.rmtree(token, ignore_errors=True, dir_fd=store_fd)
        raise
    shutil.rmtree(token, ignore_errors=True, dir_fd=store_fd)
    return Installed(bid, shown / dest_name, installed_at, copied)


def read_installed(bundle: Path) -> Installed | None:
    try:
        data = json.loads((bundle / INSTALLED_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("build_id"), str):
        return None
    markers = data.get("markers")
    return Installed(
        build_id=data["build_id"],
        path=bundle,
        installed_at=str(data.get("installed_at", "")),
        markers=markers if isinstance(markers, dict) else {},
    )


def verify(bundle: Path) -> bool:
    """True when the installed copy still carries its markers and its recorded hash."""
    try:
        csp = load_csp(bundle)
    except BundleError:
        return False
    record = read_installed(bundle)
    if record is None or not (bundle / MAIN_JS).is_file():
        return False
    markers = repair_markers(_read_js(bundle))
    if not markers_ok(markers):
        return False
    try:
        data: dict[str, Any] = json.loads((bundle / INSTALLED_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return data.get("csp") == csp and data.get("main_dart_js_sha256") == _sha256(bundle / MAIN_JS)


def _read_record(bundle: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((bundle / INSTALLED_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def install_signed(
    source: Path,
    *,
    archive_sha256: str,
    version: str,
    release_url: str = "",
    root: Path | None = None,
) -> Installed:
    """Install a verified release bundle under `signed/<archive sha256>/`, apart
    from developer installs, recording a per-file inventory. An existing
    destination is reused only when every file still matches that inventory;
    any difference reinstalls into a fresh directory at the same name."""
    if _SHA256.fullmatch(archive_sha256) is None or _VERSION.fullmatch(version) is None:
        raise BundleError("the signed bundle identity is not valid; not installed")
    origin, digest, bid, csp = _prepare(source)
    store = root or default_root()
    store_fd = _open_store(store)
    try:
        try:
            os.mkdir(SIGNED_DIR, 0o700, dir_fd=store_fd)
        except FileExistsError:
            pass
        signed_fd = os.open(SIGNED_DIR, _DIR_FLAGS, dir_fd=store_fd)
        try:
            dest = store.resolve() / SIGNED_DIR / archive_sha256
            try:
                present = os.stat(archive_sha256, dir_fd=signed_fd, follow_symlinks=False)
            except FileNotFoundError:
                present = None
            if present is not None:
                if not stat.S_ISDIR(present.st_mode):
                    raise BundleError(f"{dest} exists and is not a directory; remove it by hand")
                record = _read_record(dest)
                if (
                    record is not None
                    and record.get("version") == version
                    and verify_signed(dest)
                    and load_csp(dest) == csp
                ):
                    existing = read_installed(dest)
                    if existing is not None:
                        return existing
                shutil.rmtree(archive_sha256, dir_fd=signed_fd)
            return _install_copy(
                origin,
                store.resolve(),
                store_fd,
                bid,
                digest,
                store,
                dest_name=f"{SIGNED_DIR}/{archive_sha256}",
                csp=csp,
                extra={
                    "kind": "signed",
                    "version": version,
                    "archive_sha256": archive_sha256,
                    "source": release_url,
                },
            )
        finally:
            os.close(signed_fd)
    finally:
        os.close(store_fd)


def verify_signed(bundle: Path) -> bool:
    """True when a signed install still matches its record exactly: markers, the
    main hash, and every inventoried file's size and SHA-256, with no extras."""
    if bundle.is_symlink() or not bundle.is_dir():
        return False
    try:
        load_csp(bundle)
    except BundleError:
        return False
    record = _read_record(bundle)
    if record is None or record.get("kind") != "signed":
        return False
    if record.get("archive_sha256") != bundle.name:
        return False
    if not isinstance(record.get("version"), str) or not verify(bundle):
        return False
    files = record.get("files")
    if not isinstance(files, list) or len(files) > MAX_FILES:
        return False
    expected: dict[str, tuple[int, str]] = {}
    for entry in files:
        if not isinstance(entry, dict):
            return False
        path, size, sha = entry.get("path"), entry.get("size"), entry.get("sha256")
        if (
            not isinstance(path, str)
            or not isinstance(size, int)
            or not isinstance(sha, str)
            or path in expected
            or path.startswith("/")
            or ".." in path.split("/")
        ):
            return False
        expected[path] = (size, sha)
    actual: dict[str, tuple[int, str]] = {}
    try:
        for dirpath, dirnames, filenames in os.walk(bundle):
            base = Path(dirpath)
            for name in (*dirnames, *filenames):
                if (base / name).is_symlink():
                    return False
            for name in filenames:
                relative = (base / name).relative_to(bundle).as_posix()
                if relative == INSTALLED_NAME:
                    continue
                actual[relative] = ((base / name).stat().st_size, _sha256(base / name))
    except OSError:
        return False
    return actual == expected


def signed_bundle_for(
    version: str,
    root: Path | None = None,
    *,
    compatible: Callable[[Path], bool] | None = None,
) -> Path | None:
    """The verified signed bundle for exactly this version (newest if several),
    or None. Read-only: a missing store simply has none."""
    if _VERSION.fullmatch(version) is None:
        return None
    signed = (root or default_root()) / SIGNED_DIR
    if signed.is_symlink() or not signed.is_dir():
        return None
    found: list[tuple[str, Path]] = []
    for child in signed.iterdir():
        if child.is_symlink() or not child.is_dir() or _SHA256.fullmatch(child.name) is None:
            continue
        record = _read_record(child)
        if record is None or record.get("version") != version:
            continue
        if verify_signed(child) and (compatible is None or compatible(child)):
            found.append((str(record.get("installed_at", "")), child))
    if not found:
        return None
    found.sort(key=lambda item: item[0], reverse=True)
    return found[0][1]


def list_bundles(root: Path | None = None) -> list[dict[str, object]]:
    """Installed bundles newest first; `current` marks the newest verified one."""
    store = root or default_root()
    rows: list[dict[str, object]] = []
    if store.is_dir():
        for child in store.iterdir():
            if child.name.startswith(".") or child.is_symlink() or not child.is_dir():
                continue
            record = read_installed(child)
            if record is None:
                continue
            rows.append(
                {
                    "build_id": record.build_id,
                    "path": str(child),
                    "installed_at": record.installed_at,
                    "verified": verify(child),
                }
            )
    rows.sort(key=lambda row: str(row["installed_at"]), reverse=True)
    current_set = False
    for row in rows:
        row["current"] = bool(row["verified"]) and not current_set
        current_set = current_set or bool(row["current"])
    return rows


def current_bundle(root: Path | None = None) -> Path | None:
    """The newest verified installed bundle, or None when nothing verified is installed."""
    for row in list_bundles(root):
        if row["current"]:
            return Path(str(row["path"]))
    return None
