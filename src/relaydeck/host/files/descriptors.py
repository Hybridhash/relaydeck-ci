"""Access owner-only files through pinned directories with bounded, no-follow reads. Creation
writes an exclusive destination directly; replacement syncs a temporary file before renaming
it.
"""

from __future__ import annotations

import errno
import os
import secrets
import stat
from pathlib import Path

MAX_READ = 1024 * 1024
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_NEW = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


class PrivateFileError(Exception):
    """`code`: missing, exists, directory, unsafe, too_large, io, or uncertain (the change is in
    place but its sync failed)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def ancestor_ok(info: os.stat_result, next_uid: int | None) -> bool:
    """Whether a directory on the way to a private folder may be trusted: owned by root or us and
    not writable by others, or a root-owned sticky folder (a temp folder) above one of ours."""
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid()):
        return False
    if not info.st_mode & 0o022:
        return True
    # A root-owned sticky directory (a temp folder) is fine only when the next level is ours.
    return bool(info.st_mode & stat.S_ISVTX and info.st_uid == 0 and next_uid == os.getuid())


def open_private_dir(path: Path, *, create_missing: bool = False, leaf_mask: int = 0o077) -> int:
    """A descriptor for `path`: every level opened with O_NOFOLLOW from the root, ancestors owned by
    root or us and not writable by others, the leaf owned by us with none of `leaf_mask` set (by
    default no group or other bits). A missing folder is made 0700. An existing wider directory is
    refused, never repaired."""
    if not path.is_absolute() or ".." in path.parts:
        raise PrivateFileError("directory")
    parts = path.parts[1:]
    chain: list[os.stat_result] = []
    fd = os.open("/", _DIR)
    try:
        chain.append(os.fstat(fd))
        for part in parts:
            try:
                child = os.open(part, _DIR, dir_fd=fd)
            except FileNotFoundError:
                if not create_missing:
                    raise PrivateFileError("missing") from None
                # Judge the existing folder before anything is made under it.
                if not ancestor_ok(chain[-1], os.getuid()):
                    raise PrivateFileError("directory") from None
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
                except OSError:
                    raise PrivateFileError("io") from None
                try:
                    child = os.open(part, _DIR, dir_fd=fd)
                except OSError:
                    raise PrivateFileError("directory") from None
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise PrivateFileError("directory") from None
                raise PrivateFileError("io") from None
            os.close(fd)
            fd = child
            below = os.fstat(fd)
            # Each folder is judged as soon as its child is known, so nothing is made under a
            # path that is going to be refused.
            if not ancestor_ok(chain[-1], below.st_uid):
                raise PrivateFileError("directory")
            chain.append(below)
        leaf = chain[-1]
        if not stat.S_ISDIR(leaf.st_mode) or leaf.st_uid != os.getuid() or leaf.st_mode & leaf_mask:
            raise PrivateFileError("directory")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _strict(info: os.stat_result) -> bool:
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.getuid()
        and not info.st_mode & 0o077
        and info.st_nlink == 1
    )


def read_private_bytes(dir_fd: int, name: str, *, limit: int = MAX_READ) -> bytes:
    """The whole file, or PrivateFileError: missing, unsafe (type, owner, mode, links) or too_large.
    Opens without blocking so a FIFO cannot hang the caller."""
    try:
        fd = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dir_fd
        )
    except FileNotFoundError:
        raise PrivateFileError("missing") from None
    except OSError:
        raise PrivateFileError("unsafe") from None
    try:
        info = os.fstat(fd)
        if not _strict(info):
            raise PrivateFileError("unsafe")
        if info.st_size > limit:
            raise PrivateFileError("too_large")
        data = b""
        while len(data) <= limit:
            try:
                chunk = os.read(fd, limit + 1 - len(data))
            except OSError:
                raise PrivateFileError("io") from None
            if not chunk:
                break
            data += chunk
        if len(data) > limit:
            raise PrivateFileError("too_large")
        return data
    finally:
        os.close(fd)


def _write_new(dir_fd: int, name: str, data: bytes) -> os.stat_result:
    """Create `name` exclusively, write all of `data`, sync it. Removes only the inode it made
    when anything fails.
    """
    try:
        fd = os.open(name, _NEW, 0o600, dir_fd=dir_fd)
    except FileExistsError:
        raise PrivateFileError("exists") from None
    except OSError:
        raise PrivateFileError("io") from None
    made = os.fstat(fd)
    try:
        try:
            if not _strict(made):
                raise PrivateFileError("unsafe")
            os.fchmod(fd, 0o600)
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        finally:
            os.close(fd)
    except BaseException as exc:
        _unlink_own(dir_fd, name, made)
        if isinstance(exc, OSError):
            raise PrivateFileError("io") from None
        raise
    return made


def _unlink_own(dir_fd: int, name: str, made: os.stat_result) -> None:
    try:
        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if (info.st_dev, info.st_ino) == (made.st_dev, made.st_ino):
            os.unlink(name, dir_fd=dir_fd)
    except OSError:
        pass


def create_private(dir_fd: int, name: str, data: bytes) -> None:
    """A new 0600 file that did not exist. Raises PrivateFileError("exists") when it does."""
    _write_new(dir_fd, name, data)
    try:
        os.fsync(dir_fd)
    except OSError:
        raise PrivateFileError(
            "uncertain"
        ) from None  # the file is in place, only durability is not


def replace_private(dir_fd: int, name: str, data: bytes) -> None:
    """Replace an existing private file using a synced temporary file and atomic rename. Recheck
    the target inode before renaming; that check is not an atomic fence against a subsequent
    swap.
    """
    try:
        target = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise PrivateFileError("missing") from None
    except OSError:
        raise PrivateFileError("io") from None
    if not _strict(target):
        raise PrivateFileError("unsafe")
    temp = f".{name}.{secrets.token_hex(8)}.tmp"
    made = _write_new(dir_fd, temp, data)
    try:
        try:
            again = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except OSError:
            raise PrivateFileError("unsafe") from None
        if (again.st_dev, again.st_ino) != (target.st_dev, target.st_ino) or not _strict(again):
            raise PrivateFileError("unsafe")
        os.replace(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException as exc:
        _unlink_own(dir_fd, temp, made)
        if isinstance(exc, OSError):
            raise PrivateFileError("io") from None
        raise
    try:
        os.fsync(dir_fd)
    except OSError:
        raise PrivateFileError("uncertain") from None


def read_private_path(path: Path, *, limit: int = MAX_READ, leaf_mask: int = 0o077) -> bytes:
    dir_fd = open_private_dir(path.parent, leaf_mask=leaf_mask)
    try:
        return read_private_bytes(dir_fd, path.name, limit=limit)
    finally:
        os.close(dir_fd)


def create_private_path(path: Path, data: bytes) -> None:
    dir_fd = open_private_dir(path.parent, create_missing=True)
    try:
        create_private(dir_fd, path.name, data)
    finally:
        os.close(dir_fd)


def replace_private_path(path: Path, data: bytes) -> None:
    dir_fd = open_private_dir(path.parent)
    try:
        replace_private(dir_fd, path.name, data)
    finally:
        os.close(dir_fd)
