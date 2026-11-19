"""The companion config file and its one writer: template, backups, receipts and port choice."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from relaydeck.companion import config_edit
from relaydeck.companion.config import load_config

RESERVED_HTTPS_PORTS = (8443, 8444)
DEFAULT_HTTPS_PORT = 8445  # the template's tailscale_https_port
DEFAULT_LOCAL_PORT = 8125  # the template's port
RECEIPT_DIR = "setup-receipts"
EXIT_UNCERTAIN = 3  # a write may have committed; the caller must stop dependent steps
EXIT_BY_STATE = {"committed": 0, "failed": 1, "uncertain": EXIT_UNCERTAIN}


def template_text() -> str:
    return (
        resources.files("relaydeck.companion")
        .joinpath("companion.template.toml")
        .read_text(encoding="utf-8")
    )


def create_config(path: Path, text: str) -> str | None:
    """Create the owner-only config exclusively. None on success, a fixed code otherwise
    (`exists`, `directory`, `io`); a half-written file is removed."""
    try:
        dir_fd = config_edit.pinned_dir_fd(path.parent, create_missing=True)
    except (ValueError, OSError):
        return "directory"
    try:
        try:
            fd = os.open(
                path.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=dir_fd,
            )
        except FileExistsError:
            return "exists"
        except OSError:
            return "io"
        try:
            # Exclusively created by us, so tightening it cannot affect anyone else's file.
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.fsync(dir_fd)
        except OSError:
            try:
                os.unlink(path.name, dir_fd=dir_fd)
            except OSError:
                pass
            return "io"
    finally:
        os.close(dir_fd)
    return None


def ensure_config(path: Path) -> str:
    """Create the owner-only config from the template when missing; never overwrite."""
    if path.is_symlink() or path.exists():
        return f"Config present: {path} (not changed)."
    failure = create_config(path, template_text())
    if failure == "exists":
        return f"Config present: {path} (not changed)."
    if failure is not None:
        return f"Config not created ({failure})."
    return f"Config created from the template: {path} (owner-only)."


def choose_https_port(configured: int, used: set[int], public: set[int]) -> int | None:
    """Only the configured port; changing it requires an explicit config edit."""
    return (
        None
        if configured in RESERVED_HTTPS_PORTS or configured in used or configured in public
        else configured
    )


def _receipt(
    path: Path, stamp: str, action: str, entries: list[dict[str, str]], backup: Path
) -> Path:
    directory = path.parent / RECEIPT_DIR
    directory.mkdir(mode=0o700, exist_ok=True)
    receipt = directory / f"sources-{stamp}-{action}.json"
    body = {
        "action": action,
        "time": stamp,
        "backup": backup.name,  # the file name only; the file itself is owner-only
        "sources": entries,  # kind, format and a ~ root: no secret values
    }
    fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        import json

        json.dump(body, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return receipt


def _stamp() -> str:
    """Unique per write: two writes in one second must not collide on a backup name."""

    now = time.time()
    return time.strftime("%Y%m%dT%H%M%S", time.gmtime(now)) + f"{int((now % 1) * 1_000_000):06d}"


@dataclass(frozen=True)
class ApplyResult:
    """`state`: committed (new file in place), uncertain (in place, durability unknown) or failed
    (the original is untouched)."""

    state: str
    lines: list[str]


def apply_text(
    path: Path, expected_text: str, new_text: str, *, action: str, entries: list[dict[str, str]]
) -> list[str]:
    return apply_text_result(path, expected_text, new_text, action=action, entries=entries).lines


def apply_text_result(
    path: Path, expected_text: str, new_text: str, *, action: str, entries: list[dict[str, str]]
) -> ApplyResult:
    """The only write: concurrent-edit check, private backup, verify, atomic replace, receipt.
    Any failure leaves the original file as it was."""
    from relaydeck.host.files.private import check_private, read_private

    try:
        check_private(path)
        current = read_private(path)
    except (ValueError, OSError):
        return ApplyResult(
            "failed",
            [
                "The configuration changed or became unreadable; nothing was written.",
                "Run relaydeck setup again to review it.",
            ],
        )
    if config_edit.sha256_of(current) != config_edit.sha256_of(expected_text):
        return ApplyResult(
            "failed",
            [
                "The configuration changed while setup was running; nothing was written.",
                "Run relaydeck setup again to review the current file.",
            ],
        )
    stamp = _stamp()
    temp = path.with_name(f".{path.name}.check-{stamp}")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(new_text)
        load_config(temp)  # the whole file must still load before anything is replaced
    except (ValueError, OSError):
        return ApplyResult(
            "failed", ["The new configuration does not validate; nothing was written."]
        )
    finally:
        if temp.exists():
            temp.unlink()
    backup_name = f"{path.name}.bak-{stamp}"
    try:
        config_edit.replace_if_match(path, expected_text, new_text, backup_name=backup_name)
    except config_edit.ConcurrentEdit:
        return ApplyResult(
            "failed",
            [
                "The configuration changed while setup was running; nothing was written.",
                "Run relaydeck setup again to review the current file.",
            ],
        )
    except config_edit.ConfigLocked:
        return ApplyResult(
            "failed",
            [
                "The configuration is locked by another writer; nothing was written.",
                "Stop other writers and run relaydeck setup again.",
            ],
        )
    except config_edit.CommitUncertain:
        return ApplyResult(
            "uncertain",
            [
                "The configuration was changed, but the directory sync failed; "
                f"the previous file is kept at {backup_name}.",
                "Inspect the file and the backup before retrying; nothing was restored "
                "automatically.",
            ],
        )
    except OSError:
        return ApplyResult(
            "failed", ["The configuration could not be replaced; the original file is unchanged."]
        )
    backup = path.with_name(backup_name)
    try:
        receipt = _receipt(path, stamp, action, entries, backup)
    except OSError as exc:
        return ApplyResult(
            "committed",
            [
                f"Backup kept (owner-only): {backup.name}",
                f"The change was written, but the receipt failed ({exc}); "
                "the backup above holds the previous file.",
            ],
        )
    return ApplyResult(
        "committed",
        [f"Backup kept (owner-only): {backup.name}", f"Receipt written: {receipt.name}"],
    )
