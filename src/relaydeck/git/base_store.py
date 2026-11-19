"""The base branch a person chose per worktree, kept in a private file outside every repository."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
import threading

from relaydeck.git.locator import Worktree

MAX_ENTRIES = 2000
MAX_BYTES = 4 * 1024 * 1024
_FILE = "changes-bases.json"
_KEY_FILE = "changes-bases.key"


class BaseStore:
    """Maps (account, host, worktree) to a qualified ref name; atomic, private, never in git."""

    def __init__(self, directory: str) -> None:
        self._dir = directory
        self._lock = threading.Lock()
        self._key = self._secret()

    def get(self, account: str, host: str, worktree: Worktree) -> tuple[str, str] | None:
        """(qualified ref, base revision) of the saved choice; every save gets a new revision."""
        with self._lock:
            value = self._load().get(self._slot(account, host, worktree))
        ref, _, revision = (value or "").partition("\0")
        return (ref, revision) if ref else None

    def put(self, account: str, host: str, worktree: Worktree, ref: str) -> None:
        if not ref.startswith(("refs/heads/", "refs/remotes/")) or len(ref) > 1024:
            raise ValueError("ref_must_be_a_branch")
        with self._lock:
            data = self._load()
            slot = self._slot(account, host, worktree)
            data.pop(slot, None)
            data[slot] = f"{ref}\0{secrets.token_hex(8)}"  # newest last; the oldest go first
            while len(data) > MAX_ENTRIES:
                data.pop(next(iter(data)))
            self._write(_FILE, json.dumps(data, separators=(",", ":")).encode())

    def clear(self, account: str, host: str, worktree: Worktree) -> None:
        """Forget the saved choice, so the base follows the default again."""
        with self._lock:
            data = self._load()
            if data.pop(self._slot(account, host, worktree), None) is not None:
                self._write(_FILE, json.dumps(data, separators=(",", ":")).encode())

    def worktree_key(self, worktree: Worktree) -> str:
        """Stable, path-free identity of a worktree for public IDs."""
        paths = b"\0".join(os.fsencode(p) for p in (worktree.common_dir, worktree.gitdir))
        # The directory identity is included, so a repository recreated at the same path is new.
        material = paths + b"\0" + repr(worktree.identity).encode()
        return hmac.new(self._key, b"worktree\0" + material, hashlib.sha256).hexdigest()[:32]

    def _slot(self, account: str, host: str, worktree: Worktree) -> str:
        material = f"{account}\0{host}\0{self.worktree_key(worktree)}".encode()
        return hmac.new(self._key, b"base\0" + material, hashlib.sha256).hexdigest()

    def _secret(self) -> bytes:
        raw = self._read(_KEY_FILE)
        if raw is not None and len(raw) == 32:
            return raw
        key = secrets.token_bytes(32)
        self._write(_KEY_FILE, key)
        return key

    def _load(self) -> dict[str, str]:
        raw = self._read(_FILE)
        if raw is None:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}

    def _read(self, name: str) -> bytes | None:
        # NONBLOCK so a FIFO opens at once and the type check below rejects it.
        try:
            fd = os.open(os.path.join(self._dir, name), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        except OSError:
            raise PermissionError("unsafe_store") from None
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise PermissionError("unsafe_store")
            data = handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise PermissionError("unsafe_store")
        return data

    def _write(self, name: str, payload: bytes) -> None:
        os.makedirs(self._dir, mode=0o700, exist_ok=True)
        tmp = os.path.join(self._dir, f".{name}.{secrets.token_hex(6)}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, os.path.join(self._dir, name))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
