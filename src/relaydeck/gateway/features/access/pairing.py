"""Bounded Mac-approved device grants shared atomically across CLI and server processes. Restart
clears windows and approvals without persisting token verifiers in the pairing file.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from relaydeck.contracts.endpoints import origin
from relaydeck.contracts.pairing import (
    DEVICE_CAPABILITIES,
    MAX_POLLS,
    MAX_REQUESTS,
    MAX_WINDOW_S,
    POLL_INTERVAL_MS,
    REQUEST_TTL_MS,
)
from relaydeck.contracts.pubkeys import fingerprint_of_raw, openssh_to_raw
from relaydeck.contracts.schemas import validate
from relaydeck.gateway.features.access.hosts import HostRecord
from relaydeck.gateway.features.access.identity import pkce_verify
from relaydeck.host.files.private import check_private, private_dir, read_private, write_json
from relaydeck.host.observability.security import Event, operation


class PairingError(ValueError):
    def __init__(self, code: str = "not_found", message: str = "Pairing unavailable; start again"):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class DeviceGrant:
    account: str
    host_id: str
    device_id: str
    host_binding: str
    capabilities: tuple[str, ...] = DEVICE_CAPABILITIES


def known_host(record: HostRecord | None) -> HostRecord:
    valid = bool(
        record
        and all(isinstance(value, str) for value in asdict(record).values())
        and record.account
        and record.host_id
        and record.pubkey
        and record.fingerprint
        and re.fullmatch(r"rdh_[A-Za-z0-9_-]{8}", record.token_prefix)
    )
    if valid and record is not None:
        try:
            valid = fingerprint_of_raw(openssh_to_raw(record.pubkey)) == record.fingerprint
        except ValueError:
            valid = False
    if not valid or record is None:
        raise PairingError(
            "host_offline", "Enroll this Mac's connector locally before device pairing"
        )
    return record


class PairingStore:
    def __init__(self, path: Path) -> None:
        self.path = path.absolute()
        self.boot: str | None = None
        with self._locked():
            pass

    def _paths(self, *, check_state: bool = True) -> None:
        # Reject ancestor symlinks too, before mkdir/open can follow them.
        for parent in reversed(self.path.parents):
            if parent.is_symlink():
                raise ValueError("Pairing state must not traverse symlinks")
        private_dir(self.path.parent)
        paths = (
            (self.path, self.path.with_suffix(".lock"))
            if check_state
            else (self.path.with_suffix(".lock"),)
        )
        for path in paths:
            if path.exists() or path.is_symlink():
                check_private(path)
                if path.lstat().st_nlink != 1:
                    raise ValueError("Pairing state must not have multiple links")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        # State metadata can describe the unlinked old inode during os.replace.
        # Validate it only under the same flock as the writer, including at init.
        self._paths(check_state=False)
        fd = os.open(self.path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
            ):
                raise ValueError("Pairing lock must be owner-private")
            fcntl.flock(fd, fcntl.LOCK_EX)
            self._paths()
            yield
        finally:
            os.close(fd)

    @contextmanager
    def _transaction(self, *, reset: bool = False) -> Iterator[dict[str, Any]]:
        with self._locked():
            if reset:
                data: dict[str, Any] = {}
            else:
                try:
                    if self.path.stat().st_size > 128 * 1024:
                        raise ValueError("oversized state")
                    data = json.loads(read_private(self.path))
                    self._validate_state(data)
                except (OSError, ValueError, TypeError, KeyError):
                    raise PairingError() from None
                if self.boot is not None and data["boot"] != self.boot:
                    raise PairingError()
            # Persist burns/rate counters even when an operation rejects.
            try:
                yield data
            finally:
                write_json(self.path, data)

    @staticmethod
    def _validate_state(data: Any) -> None:
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "boot", "origin", "window", "requests"}
            or data["version"] != 1
            or not isinstance(data["boot"], str)
            or origin(data["origin"]) != data["origin"]
            or not isinstance(data["requests"], dict)
            or len(data["requests"]) > MAX_REQUESTS
        ):
            raise ValueError("invalid state")
        window = data["window"]
        if window is None:
            if data["requests"]:
                raise ValueError("orphaned requests")
            return
        if set(window) != {
            "window_id",
            "host",
            "expires_ms",
            "opened_ms",
            "count",
            "rate_ms",
            "rate",
            "create_ms",
            "creates",
        }:
            raise ValueError("invalid window")
        if not isinstance(window["window_id"], str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{16,64}", window["window_id"]
        ):
            raise ValueError("invalid window")
        host = HostRecord(**window["host"])
        known_host(host)
        for key in ("expires_ms", "opened_ms", "count", "rate_ms", "rate", "create_ms", "creates"):
            if type(window[key]) is not int or window[key] < 0:
                raise ValueError("invalid window count")
        for key, row in data["requests"].items():
            if set(row) != {
                "request_id",
                "matching_code",
                "device_id",
                "device_label",
                "challenge",
                "expires_ms",
                "state",
                "attempts",
                "polls",
                "last_poll_ms",
            }:
                raise ValueError("invalid request")
            validate(
                "relaydeck/gateway/v1/pairing-request",
                {
                    "origin": data["origin"],
                    "host_id": host.host_id,
                    "code_challenge": row["challenge"],
                    "code_challenge_method": "S256",
                    "device_label": row["device_label"],
                },
            )
            if (
                key != row["request_id"]
                or not re.fullmatch(r"rdp_[A-Za-z0-9_-]{32}", key)
                or not re.fullmatch(r"device_[A-Za-z0-9_-]{32}", row["device_id"])
                or not re.fullmatch(r"\d{3}-\d{3}", row["matching_code"])
                or row["state"] not in {"pending", "approved", "denied", "consumed"}
                or any(
                    type(row[k]) is not int or row[k] < 0
                    for k in ("expires_ms", "attempts", "polls", "last_poll_ms")
                )
            ):
                raise ValueError("invalid request fields")

    def start(self, public_origin: str) -> None:
        base = origin(public_origin)
        self.boot = secrets.token_urlsafe(24)
        with self._transaction(reset=True) as data:
            data.update(version=1, boot=self.boot, origin=base, window=None, requests={})

    def open_window(
        self, record: HostRecord | None, public_origin: str, now: int, ttl_s: int
    ) -> str:
        """Open a window, replacing any open one; returns the new window's id."""
        record = known_host(record)
        if type(ttl_s) is not int or not 1 <= ttl_s <= MAX_WINDOW_S:
            raise ValueError(f"Pairing window must be 1–{MAX_WINDOW_S} seconds")
        with self._transaction() as data:
            if public_origin != data["origin"]:
                raise PairingError()
            return self._open(data, record, now, ttl_s)

    def open_window_if_absent(
        self, record: HostRecord | None, public_origin: str, now: int, ttl_s: int
    ) -> str | None:
        """Open a window only when none is open, as one step; returns its id, or None."""
        record = known_host(record)
        if type(ttl_s) is not int or not 1 <= ttl_s <= MAX_WINDOW_S:
            raise ValueError(f"Pairing window must be 1–{MAX_WINDOW_S} seconds")
        with self._transaction() as data:
            if public_origin != data["origin"]:
                raise PairingError()
            window = data["window"]
            if window is not None and now < window["expires_ms"]:
                return None
            return self._open(data, record, now, ttl_s)

    @staticmethod
    def _open(data: dict[str, Any], record: HostRecord, now: int, ttl_s: int) -> str:
        window_id = secrets.token_urlsafe(16)
        data["window"] = {
            "window_id": window_id,
            "host": asdict(record),
            "expires_ms": now + ttl_s * 1000,
            "opened_ms": now,
            "count": 0,
            "rate_ms": now,
            "rate": 0,
            "create_ms": now,
            "creates": 0,
        }
        data["requests"] = {}
        return window_id

    def window_info(self, record: HostRecord | None, base: str, now: int) -> dict[str, Any] | None:
        """Identity of the open window for this host, or None when none is open (or it expired)."""
        with self._transaction() as data:
            try:
                window = self._window(data, record, base, now)
            except PairingError:
                return None
            return {
                "window_id": window["window_id"],
                "opened_ms": window["opened_ms"],
                "expires_ms": window["expires_ms"],
            }

    def close_window(self) -> None:
        with self._transaction() as data:
            data.update(window=None, requests={})

    def close_window_if_owned(self, window_id: str) -> bool:
        """Close the window only if it is still the one with this id, as one step."""
        with self._transaction() as data:
            window = data["window"]
            if window is None or window["window_id"] != window_id:
                return False
            data.update(window=None, requests={})
            return True

    @staticmethod
    def _window(
        data: dict[str, Any], record: HostRecord | None, base: str, now: int
    ) -> dict[str, Any]:
        window = data["window"]
        if (
            window is None
            or window["expires_ms"] <= now
            or now < window["opened_ms"]
            or base != data["origin"]
            or record is None
            or asdict(record) != window["host"]
        ):
            raise PairingError()
        return window  # type: ignore[no-any-return]

    @staticmethod
    def _rate(window: dict[str, Any], now: int, *, create: bool = False) -> None:
        prefix, limit = ("create", 5) if create else ("rate", 60)
        count = "creates" if create else "rate"
        key = prefix + "_ms"
        if now - window[key] >= 10_000:
            window[key], window[count] = now, 0
        if window[count] >= limit:
            raise PairingError("rate_limited", "Pairing rate exceeded; wait before trying again")
        window[count] += 1

    @staticmethod
    def _view(data: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
        host = data["window"]["host"]
        return {
            "origin": data["origin"],
            "host_id": host["host_id"],
            "request_id": row["request_id"],
            "matching_code": row["matching_code"],
            "device_id": row["device_id"],
            "host_fingerprint": host["fingerprint"],
            "capabilities": list(DEVICE_CAPABILITIES),
            "state": row["state"],
            "expires_at": datetime.fromtimestamp(row["expires_ms"] / 1000, UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "poll_interval_ms": POLL_INTERVAL_MS,
        }

    @operation(Event.PAIRING_REQUESTED, denied=(PairingError,))
    def request(self, body: dict[str, Any], record: HostRecord | None, now: int) -> dict[str, Any]:
        validate("relaydeck/gateway/v1/pairing-request", body)
        with self._transaction() as data:
            window = self._window(data, record, body["origin"], now)
            if body["host_id"] != window["host"]["host_id"]:
                raise PairingError()
            self._rate(window, now)
            self._rate(window, now, create=True)
            if window["count"] >= MAX_REQUESTS:
                raise PairingError("rate_limited", "Pairing window request limit reached")
            code = f"{secrets.randbelow(1_000_000):06d}"
            used_codes = {r["matching_code"].replace("-", "") for r in data["requests"].values()}
            while code in used_codes:
                code = f"{secrets.randbelow(1_000_000):06d}"
            row = {
                "request_id": "rdp_" + secrets.token_urlsafe(24),
                "device_id": "device_" + secrets.token_urlsafe(24),
                "matching_code": code[:3] + "-" + code[3:],
                "device_label": body["device_label"],
                "challenge": body["code_challenge"],
                "expires_ms": min(window["expires_ms"], now + REQUEST_TTL_MS),
                "state": "pending",
                "attempts": 0,
                "polls": 0,
                "last_poll_ms": 0,
            }
            data["requests"][row["request_id"]] = row
            window["count"] += 1
            return self._view(data, row)

    def _proof(
        self,
        data: dict[str, Any],
        request_id: str,
        body: dict[str, Any],
        record: HostRecord | None,
        now: int,
    ) -> dict[str, Any]:
        window = self._window(data, record, body["origin"], now)
        self._rate(window, now)
        row = data["requests"].get(request_id)
        if (
            row is None
            or body["host_id"] != window["host"]["host_id"]
            or row["expires_ms"] <= now
            or row["state"] == "consumed"
            or row["attempts"] >= 5
        ):
            raise PairingError()
        if not pkce_verify(body["verifier"], row["challenge"]):
            row["attempts"] += 1
            raise PairingError()
        return row  # type: ignore[no-any-return]

    def poll(
        self, request_id: str, body: dict[str, Any], record: HostRecord | None, now: int
    ) -> dict[str, Any]:
        validate("relaydeck/gateway/v1/pairing-proof", body)
        with self._transaction() as data:
            row = self._proof(data, request_id, body, record, now)
            if row["polls"] >= MAX_POLLS or (
                row["polls"] and now - row["last_poll_ms"] < POLL_INTERVAL_MS
            ):
                raise PairingError("rate_limited", "Respect the pairing polling interval")
            row["last_poll_ms"], row["polls"] = now, row["polls"] + 1
            return self._view(data, row)

    @operation(Event.PAIRING_REDEMPTION, denied=(PairingError,))
    def redeem(
        self, request_id: str, body: dict[str, Any], record: HostRecord | None, now: int
    ) -> DeviceGrant:
        validate("relaydeck/gateway/v1/pairing-proof", body)
        with self._transaction() as data:
            row = self._proof(data, request_id, body, record, now)
            if row["state"] != "approved":
                raise PairingError()
            row["state"] = "consumed"
            host = data["window"]["host"]
            return DeviceGrant(
                host["account"], host["host_id"], row["device_id"], host["token_prefix"]
            )

    def pending(self, record: HostRecord | None, base: str, now: int) -> list[dict[str, Any]]:
        with self._transaction() as data:
            self._window(data, record, base, now)
            return [
                {**self._view(data, row), "device_label": row["device_label"]}
                for row in data["requests"].values()
                if row["expires_ms"] > now
                and row["state"] in {"pending", "approved"}
                and row["attempts"] < 5
            ]

    @operation(Event.PAIRING_DECISION, denied=(PairingError,), decision=True)
    def decide(
        self,
        request_id: str,
        matching_code: str,
        record: HostRecord | None,
        base: str,
        now: int,
        *,
        approve: bool,
    ) -> None:
        with self._transaction() as data:
            self._window(data, record, base, now)
            row = data["requests"].get(request_id)
            if (
                row is None
                or row["expires_ms"] <= now
                or row["state"] not in ({"pending"} if approve else {"pending", "approved"})
                or row["attempts"] >= 5
                or not secrets.compare_digest(row["matching_code"], matching_code)
            ):
                raise PairingError()
            row["state"] = "approved" if approve else "denied"
