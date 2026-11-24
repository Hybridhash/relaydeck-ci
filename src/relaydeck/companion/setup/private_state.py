"""Describe existing private files and proposed setup actions without creating or changing
anything.
"""

from __future__ import annotations

import hmac
import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from relaydeck.companion.adapters import tailscale
from relaydeck.companion.config import CompanionConfig
from relaydeck.companion.inspection.readiness import PLACEHOLDER_ORIGIN
from relaydeck.companion.setup.context import SetupContext
from relaydeck.companion.setup.files import DEFAULT_HTTPS_PORT, template_text
from relaydeck.connector.enrollment import ConnectorKeys, EnrollmentError, load_keys
from relaydeck.gateway.features.access.api import HostRecord, HostStore
from relaydeck.host.files.descriptors import (
    PrivateFileError,
    open_private_dir,
    read_private_bytes,
)
from relaydeck.host.files.private import read_private

MIN_SECRET_BYTES = 32
OWNER_ACCOUNT = "owner"

PRESENT = "present"
CREATE = "create"
REWRITE = "rewrite"
REPLACE = "replace"
CONFLICT = "conflict"
REFUSED = "refused"


@dataclass(frozen=True)
class Part:
    label: str
    state: str
    note: str = ""  # one fixed line; never file text


@dataclass
class Paths:
    cookie: Path
    cursor: Path
    credentials: Path
    state_dir: Path
    host_id: str
    origin: str


@dataclass
class Findings:
    config: Part
    cookie: Part
    cursor: Part
    key: Part
    enrollment: Part
    paths: Paths | None = None
    new_origin: str | None = None  # what a created or rewritten config gets
    keys: ConnectorKeys | None = None
    replaces: bool = False  # the enrollment part replaces an existing record
    parts: list[Part] = field(init=False)

    def __post_init__(self) -> None:
        self.parts = [self.config, self.cookie, self.cursor, self.key, self.enrollment]

    @property
    def blocked(self) -> Part | None:
        return next((p for p in self.parts if p.state in (CONFLICT, REFUSED)), None)

    @property
    def file_work(self) -> bool:
        return any(p.state in (CREATE, REPLACE) for p in self.parts)

    @property
    def work(self) -> bool:
        return self.file_work or self.config.state == REWRITE


def _expand(raw: str, home: Path) -> Path:
    return home / raw[2:] if raw.startswith("~/") else Path(raw)


def template_paths(ctx: SetupContext, origin: str) -> Paths:
    """Where a config made from the template would put things."""
    raw: dict[str, Any] = tomllib.loads(template_text())
    return Paths(
        cookie=_expand(raw["cookie_secret_file"], ctx.home),
        cursor=_expand(raw["cursor_secret_file"], ctx.home),
        credentials=_expand(raw["connector_credentials"], ctx.home),
        state_dir=_expand(raw["state_dir"], ctx.home),
        host_id=raw["host_id"],
        origin=origin,
    )


def config_paths(config: CompanionConfig) -> Paths:
    return Paths(
        config.cookie_secret_file,
        config.cursor_secret_file,
        config.connector_credentials,
        config.state_dir,
        config.host_id,
        config.advertised_origin,
    )


def _folder(path: Path) -> str:
    """present | create | wide, for the private folder that holds `path`."""
    try:
        os.close(open_private_dir(path.parent))
    except PrivateFileError as exc:
        return CREATE if exc.code == "missing" else "wide"
    return PRESENT


def _secret(path: Path, label: str) -> tuple[Part, bytes | None]:
    folder = _folder(path)
    if folder == "wide":
        return Part(label, CONFLICT, "its folder is not owner-only (0700) or crosses a link"), None
    if folder == CREATE:
        return Part(label, CREATE), None
    try:
        dir_fd = open_private_dir(path.parent)
    except PrivateFileError:
        return Part(label, CONFLICT, "its folder is not owner-only (0700)"), None
    try:
        data = read_private_bytes(dir_fd, path.name)
    except PrivateFileError as exc:
        if exc.code == "missing":
            return Part(label, CREATE), None
        return Part(label, CONFLICT, "it is not one private regular file (0600, one link)"), None
    finally:
        os.close(dir_fd)
    if len(data.strip()) < MIN_SECRET_BYTES:
        return Part(label, CONFLICT, f"it is shorter than {MIN_SECRET_BYTES} bytes"), None
    return Part(label, PRESENT, "present (not changed)"), data.strip()


def _config_part(ctx: SetupContext) -> tuple[Part, CompanionConfig | None, str | None]:
    path = ctx.config_path
    label = "config"
    if not (path.is_symlink() or path.exists()):
        folder = _folder(path)
        if folder == "wide":
            return Part(label, CONFLICT, "its folder is not owner-only (0700)"), None, None
        try:
            origin = tailscale.private_origin(DEFAULT_HTTPS_PORT, ctx.runner)
        except tailscale.TailscaleError:
            return Part(label, CONFLICT, "this Mac's Tailscale name could not be read"), None, None
        return Part(label, CREATE), None, origin
    try:
        config = ctx.load()
        text = read_private(path)
    except (ValueError, OSError, tailscale.TailscaleError):
        return (
            Part(label, CONFLICT, "it does not load; fix it by hand (relaydeck-companion doctor)"),
            None,
            None,
        )
    if PLACEHOLDER_ORIGIN in config.advertised_origin or _origin_line_is_template(text):
        try:
            origin = tailscale.private_origin(config.tailscale_https_port, ctx.runner)
        except tailscale.TailscaleError:
            note = "it still has the template address and this Mac's name could not be read"
            return Part(label, CONFLICT, note), config, None
        return Part(label, REWRITE), config, origin
    return Part(label, PRESENT, "present (not changed)"), config, None


def _origin_line_is_template(text: str) -> bool:
    try:
        value = tomllib.loads(text).get("advertised_origin", "")
    except ValueError:
        return False
    return isinstance(value, str) and PLACEHOLDER_ORIGIN in value


def _key_part(ctx: SetupContext) -> tuple[Part, ConnectorKeys | None]:
    label = "connector key"
    try:
        keys = load_keys(str(ctx.key_file))
    except EnrollmentError:
        return Part(label, CONFLICT, "it is not a valid private key file; fix it by hand"), None
    if keys is not None:
        return Part(label, PRESENT, "present (not changed)"), keys
    folder = _folder(ctx.key_file)
    if folder == "wide":
        return Part(label, CONFLICT, "its folder is not owner-only (0700)"), None
    return Part(label, CREATE), None


def _credentials(path: Path) -> tuple[str, dict[str, str] | None]:
    """(state, doc): absent | unsafe | corrupt | ok."""
    try:
        dir_fd = open_private_dir(path.parent)
    except PrivateFileError as exc:
        return ("absent", None) if exc.code == "missing" else ("unsafe", None)
    try:
        data = read_private_bytes(dir_fd, path.name)
    except PrivateFileError as exc:
        return ("absent", None) if exc.code == "missing" else ("unsafe", None)
    finally:
        os.close(dir_fd)
    try:
        doc = json.loads(data)
    except ValueError:
        return "corrupt", None
    keys = ("gateway_url", "host_id", "host_token")
    if not isinstance(doc, dict) or not all(isinstance(doc.get(k), str) and doc[k] for k in keys):
        return "corrupt", None
    return "ok", {k: doc[k] for k in keys}


def _enrollment_part(
    ctx: SetupContext, paths: Paths, keys: ConnectorKeys | None
) -> tuple[Part, bool]:
    """(part, replaces). The registry is read through the read-only observer."""
    label = "enrollment"
    hosts_file = paths.state_dir / "hosts.json"
    try:
        hosts = HostStore.observe(hosts_file)
    except (ValueError, OSError):
        return Part(label, REFUSED, "the host registry cannot be read; fix it by hand"), False
    state, creds = _credentials(paths.credentials)
    if state == "unsafe":
        return Part(label, REFUSED, "the credentials file is not one private regular file"), False
    record: HostRecord | None = hosts.record_for_host(paths.host_id)
    if record is not None and record.account not in ("", OWNER_ACCOUNT):
        return Part(label, REFUSED, "this host belongs to another account"), False
    if state == "absent":
        if record is None:
            return Part(label, CREATE), False
        return _needs_re_enroll(ctx, label, "a host record exists but this Mac has no credentials")
    if keys is None:
        return (
            Part(label, REFUSED, "credentials exist but the connector key is missing"),
            False,
        )
    if state == "corrupt" or creds is None:
        return _needs_re_enroll(ctx, label, "the credentials file is not valid")
    if creds["host_id"] != paths.host_id or creds["gateway_url"].rstrip("/") != paths.origin.rstrip(
        "/"
    ):
        return _needs_re_enroll(ctx, label, "the credentials belong to another host or address")
    found = hosts.lookup(creds["host_token"])
    if found is None or found.host_id != paths.host_id:
        return _needs_re_enroll(ctx, label, "the credentials' token is not in the host registry")
    if (
        found.account != OWNER_ACCOUNT
        or found.pubkey != keys.public_openssh
        or found.fingerprint != keys.fingerprint
    ):
        return _needs_re_enroll(ctx, label, "the host record does not match this Mac's key")
    if ctx.re_enroll:
        return Part(label, REPLACE, "re-enrolling by request"), True
    return Part(label, PRESENT, "present (not changed)"), False


def _needs_re_enroll(ctx: SetupContext, label: str, why: str) -> tuple[Part, bool]:
    if ctx.re_enroll:
        return Part(label, REPLACE, why), True
    return Part(label, CONFLICT, f"{why}; re-enroll with: relaydeck setup --re-enroll"), False


def inspect(ctx: SetupContext) -> Findings:
    config_part, config, new_origin = _config_part(ctx)
    if config is not None:
        known = config_paths(config)
        if new_origin is not None:
            known.origin = new_origin  # the address the rewritten config will carry
        paths: Paths | None = known
    elif config_part.state == CREATE and new_origin is not None:
        paths = template_paths(ctx, new_origin)
    else:
        paths = None
    if paths is None:
        skipped = Part("", CONFLICT, "not checked until the config is valid")
        return Findings(
            config_part,
            Part("cookie secret", skipped.state, skipped.note),
            Part("cursor secret", skipped.state, skipped.note),
            Part("connector key", skipped.state, skipped.note),
            Part("enrollment", skipped.state, skipped.note),
        )
    cookie, cookie_value = _secret(paths.cookie, "cookie secret")
    cursor, cursor_value = _secret(paths.cursor, "cursor secret")
    if paths.cookie == paths.cursor:
        cursor = Part(cursor.label, CONFLICT, "it is the same file as the cookie secret")
    elif cookie_value is not None and cursor_value is not None:
        if hmac.compare_digest(cookie_value, cursor_value):
            cookie = Part(cookie.label, CONFLICT, "it equals the cursor secret")
            cursor = Part(cursor.label, CONFLICT, "it equals the cookie secret")
    key, keys = _key_part(ctx)
    enrollment, replaces = _enrollment_part(ctx, paths, keys)
    return Findings(config_part, cookie, cursor, key, enrollment, paths, new_origin, keys, replaces)
