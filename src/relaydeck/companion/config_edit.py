"""Append or undo managed usage blocks without rewriting unrelated TOML text. Undo removes a
block only while its body still matches the recorded digest.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import re
import secrets
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

from relaydeck.host.files.descriptors import ancestor_ok

MARKER = "# relaydeck-setup: managed-source sha256="
LIMITS_MARKER = "# relaydeck-setup: managed-limits sha256="


@dataclass(frozen=True)
class SourceEntry:
    kind: str
    format: str
    root: str  # absolute, as the loader requires
    display_name: str


@dataclass(frozen=True)
class LimitsEntry:
    """one `[[usage.limits]]` block setup appends (on-demand only)."""

    kind: str
    format: str
    timeout_seconds: int = 90


@dataclass(frozen=True)
class ManagedBlock:
    kind: str
    unchanged: bool
    start: int  # line index of the marker
    end: int  # exclusive: one past the last body line (and the blank line after it, when present)


def set_top_level(text: str, key: str, rendered: str) -> str:
    """Replace exactly one top-level key line while preserving all other text. Raise ValueError
    if no line or more than one line matches.
    """
    lines = text.split("\n")
    hits: list[int] = []
    for index, line in enumerate(lines):
        if line.lstrip().startswith("["):
            break
        if re.match(rf"^{re.escape(key)}\s*=", line):
            hits.append(index)
    if len(hits) != 1:
        raise ValueError(f"{key}: expected one top-level line")
    lines[hits[0]] = f"{key} = {rendered}"
    return "\n".join(lines)


def set_updates_check(text: str, value: bool) -> str:
    """Adds `check = value` to the `[updates]` table, or a new table at the end. Raises ValueError
    when the table already sets `check`: an explicit choice is never changed."""
    rendered = "true" if value else "false"
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if line.strip() == "[updates]":
            for later in lines[index + 1 :]:
                if later.lstrip().startswith("["):
                    break
                if re.match(r"^\s*check\s*=", later):
                    raise ValueError("updates.check is already set")
            lines.insert(index + 1, f"check = {rendered}")
            return "\n".join(lines)
    gap = "" if text.endswith("\n") or not text else "\n"
    return f"{text}{gap}\n[updates]\ncheck = {rendered}\n"


def render_string(value: str) -> str:
    return _quote(value)


def render_string_list(values: list[str]) -> str:
    return "[" + ", ".join(_quote(value) for value in values) + "]"


def digest(body: list[str]) -> str:
    return hashlib.sha256("\n".join(body).encode("utf-8")).hexdigest()


def _body(entry: SourceEntry) -> list[str]:
    return [
        "[[usage.sources]]",
        f"kind = {_quote(entry.kind)}",
        f"format = {_quote(entry.format)}",
        f"root = {_quote(entry.root)}",
        f"display_name = {_quote(entry.display_name)}",
    ]


def _quote(value: str) -> str:
    if any(ord(char) < 32 or char in '"\\' for char in value):
        raise ValueError("value cannot be written safely")
    return f'"{value}"'


def render_block(entry: SourceEntry) -> str:
    body = _body(entry)
    return "\n".join([f"{MARKER}{digest(body)}", *body]) + "\n"


def append_sources(text: str, entries: list[SourceEntry]) -> str:
    """Appends one managed block per entry. Existing text is never changed; the result is
    verified."""
    out = text if text == "" or text.endswith("\n") else text + "\n"
    for entry in entries:
        if out and not out.endswith("\n\n"):
            out += "\n"
        out += render_block(entry)
    verify_sources(out, entries)
    return out


def render_limits_block(entry: LimitsEntry) -> str:
    body = [
        "[[usage.limits]]",
        f"kind = {_quote(entry.kind)}",
        f"format = {_quote(entry.format)}",
        "on_demand = true",
        f"timeout_seconds = {entry.timeout_seconds}",
    ]
    return "\n".join([f"{LIMITS_MARKER}{digest(body)}", *body]) + "\n"


def append_limits(text: str, entries: list[LimitsEntry]) -> str:
    """Appends one managed `[[usage.limits]]` block per entry."""
    out = text if text == "" or text.endswith("\n") else text + "\n"
    for entry in entries:
        if out and not out.endswith("\n\n"):
            out += "\n"
        out += render_limits_block(entry)
    verify_limits(out, entries)
    return out


def verify_limits(text: str, expected: list[LimitsEntry]) -> None:
    """Parses the text and checks that its limits blocks include `expected`."""
    parsed = tomllib.loads(text)
    rows = (parsed.get("usage") or {}).get("limits") or []
    kinds_formats = {(row.get("kind"), row.get("format")) for row in rows}
    for entry in expected:
        if (entry.kind, entry.format) not in kinds_formats:
            raise ValueError(f"written file does not contain the approved limits {entry.kind}")


def managed_blocks(text: str) -> list[ManagedBlock]:
    """Every block this module wrote, with whether its body still matches its digest."""
    lines = text.split("\n")
    found: list[ManagedBlock] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith(MARKER):
            index += 1
            continue
        declared = line[len(MARKER) :].strip()
        cursor = index + 1
        body: list[str] = []
        while cursor < len(lines) and lines[cursor].strip() != "":
            body.append(lines[cursor])
            cursor += 1
        kind = _value(body, "kind")
        end = cursor + 1 if cursor < len(lines) else cursor
        found.append(
            ManagedBlock(
                kind=kind,
                unchanged=digest(body) == declared,
                start=index,
                end=end,
            )
        )
        index = cursor
    return found


def remove_unchanged(text: str) -> tuple[str, list[str], list[str]]:
    """Removes managed blocks whose body still matches. Returns (text, removed kinds, kept
    kinds)."""
    lines = text.split("\n")
    removed: list[str] = []
    kept: list[str] = []
    drop: set[int] = set()
    for block in managed_blocks(text):
        if block.unchanged:
            drop.update(range(block.start, block.end))
            removed.append(block.kind)
        else:
            kept.append(block.kind)
    result = [line for index, line in enumerate(lines) if index not in drop]
    new_text = "\n".join(result)
    return new_text, removed, kept


def _value(body: list[str], key: str) -> str:
    for line in body:
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip().strip('"')
    return ""


def verify_sources(text: str, expected: list[SourceEntry]) -> None:
    """Parses the text and checks that its usage sources are exactly the existing ones plus
    `expected`."""
    parsed = tomllib.loads(text)
    rows = (parsed.get("usage") or {}).get("sources") or []
    kinds_formats = {(row.get("kind"), row.get("format")) for row in rows}
    for entry in expected:
        if (entry.kind, entry.format) not in kinds_formats:
            raise ValueError(f"written file does not contain the approved source {entry.kind}")


class ConcurrentEdit(Exception):
    """The live file differs from the image compared before the write."""


class ConfigLocked(Exception):
    """Another cooperating writer holds the config lock."""


class CommitUncertain(Exception):
    """The rename committed but the parent sync failed; inspect before retrying."""


def _live_image(dir_fd: int, name: str) -> tuple[str, tuple[int, int, int, int]]:
    """Opens, validates and reads the live file through the pinned directory.

    Anything unexpected aborts the write: only a concurrent change can explain
    it, and the rename below must never discard a save it has not seen.
    """
    try:
        live_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except (FileNotFoundError, NotADirectoryError):
        raise ConcurrentEdit(f"{name}: changed while writing") from None
    except OSError as exc:
        if isinstance(exc, IsADirectoryError) or exc.errno == errno.ELOOP:
            raise ConcurrentEdit(f"{name}: changed while writing") from None
        raise
    with os.fdopen(live_fd, encoding="utf-8") as handle:
        # Only shape and size are checked: the pinned parent admits no
        # other writer, so owner and mode cannot have hostile causes, and
        # the atomic rename below cannot corrupt what it cannot replace.
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
            raise ConcurrentEdit(f"{name}: changed while writing")
        try:
            text = handle.read()
        except (OSError, UnicodeError):
            raise ConcurrentEdit(f"{name}: changed while writing") from None
    return text, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def pinned_dir_fd(path: Path, *, create_missing: bool = False) -> int:
    """Open each directory component through pinned, no-follow descriptors. Every ancestor must be
    owned by root or us and not writable by others (a root-owned sticky temp folder is allowed
    above a folder of ours); the leaf must be ours and not writable by others. Create missing
    directories privately only when requested, and only under an ancestor that passes.
    """
    if not path.is_absolute():
        raise ValueError(f"{path}: refusing a relative directory")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        above = os.fstat(fd)
        for part in path.parts[1:]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create_missing:
                    raise ValueError(f"{path}: missing directory") from None
                if not ancestor_ok(above, os.getuid()):
                    raise ValueError(f"{path}: refusing a directory others can write") from None
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                os.fsync(fd)
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except OSError as exc:
                    raise ValueError(f"{path}: refusing an unsafe directory") from exc
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise ValueError(f"{path}: refusing a link in the directory path") from None
                raise
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if not ancestor_ok(above, info.st_uid):
                raise ValueError(f"{path}: refusing a directory others can write")
            above = info
        if above.st_uid != os.getuid() or above.st_mode & 0o022:
            raise ValueError(f"{path}: refusing a directory others can write")
        return fd
    except BaseException:
        os.close(fd)
        raise


def replace_if_match(path: Path, expected_text: str, new_text: str, *, backup_name: str) -> None:
    """Replace the config under a sibling-file lock after comparing expected bytes before
    staging and again before rename. Other settings writers must still stop because
    noncooperating editors can race the checks.
    """
    parent = path.parent
    dir_fd = pinned_dir_fd(parent)
    try:
        lock_fd = os.open(
            f"{path.name}.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
            dir_fd=dir_fd,
        )
    except OSError:
        os.close(dir_fd)
        raise
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConfigLocked(f"{path.name}: locked by another writer") from None
        expected_digest = sha256_of(expected_text)
        live, identity = _live_image(dir_fd, path.name)
        if sha256_of(live) != expected_digest:
            raise ConcurrentEdit(f"{path.name}: changed while writing")
        backup_fd = os.open(
            backup_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=dir_fd,
        )
        with os.fdopen(backup_fd, "w", encoding="utf-8") as stream:
            stream.write(live)
            stream.flush()
            os.fsync(stream.fileno())
        temp_name = f".{path.name}.commit-{os.getpid()}-{secrets.token_hex(8)}"
        temp_fd = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=dir_fd,
        )
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as stream:
                stream.write(new_text)
                stream.flush()
                os.fsync(stream.fileno())
            # Recheck bytes and inode after staging; a noncooperating writer can still race the
            # rename.
            again, again_identity = _live_image(dir_fd, path.name)
            if again_identity != identity or sha256_of(again) != expected_digest:
                raise ConcurrentEdit(f"{path.name}: changed while writing")
            os.replace(temp_name, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except BaseException:
            try:
                os.unlink(temp_name, dir_fd=dir_fd)
            except OSError:
                pass
            raise
        try:
            os.fsync(dir_fd)
        except OSError as exc:
            # The rename above already committed: the new bytes are live and
            # only their durability is uncertain. Say so; never claim the
            # original is unchanged, and never restore the backup unasked.
            raise CommitUncertain(f"{path.name}: replaced but not yet durable") from exc
    finally:
        os.close(lock_fd)
        os.close(dir_fd)


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
