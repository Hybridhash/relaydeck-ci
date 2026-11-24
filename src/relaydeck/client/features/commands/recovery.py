"""Scoped command identities. Persist before dispatch; never replay payloads in recovery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import weakref
from collections.abc import Awaitable, Callable
from typing import Any, cast

from relaydeck.client.models.commands.pending import (
    DISMISSABLE,
    DISMISSED,
    FINAL,
    IDENTITIES,
    IN_FLIGHT,
    KINDS,
    MAX_BYTES,
    MAX_RECORDS,
    OUTCOMES,
    RELEASED,
    REVIEW_GRACE_MS,
    WAITING,
)
from relaydeck.client.ports.commands import RecoveryError, UnconfirmedEarlier
from relaydeck.contracts.schemas import CommandRecord, CommandSubmit, SchemaError, validate
from relaydeck.contracts.storage import CredentialStore

# tab.create remains readable in legacy storage; gateway submissions use shell.create.
_ROW_KEYS = {"host", "id", "kind", "target", "created_ms", "outcome", "created"}
_LOCKS: weakref.WeakKeyDictionary[CredentialStore, dict[str, asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)


def _identity(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 128 and not any(ord(c) < 32 for c in value)


class PendingCommands:
    def __init__(
        self,
        store: CredentialStore,
        origin: str,
        account: str,
        device: str,
        *,
        current: Callable[[], bool] = lambda: True,
        version: Callable[[], Any] = lambda: None,
        clock: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self.store = store
        self.scope = {"origin": origin.rstrip("/"), "account": account, "device": device}
        self.key = (
            "pending_" + hashlib.sha256(json.dumps(self.scope, sort_keys=True).encode()).hexdigest()
        )
        self.current = current
        self.version = version
        self.clock = clock
        self.records: dict[tuple[str, str], dict[str, Any]] = {}
        self.error = ""
        self.loaded = False
        self.closed = False
        self.lock = _LOCKS.setdefault(store, {}).setdefault(self.key, asyncio.Lock())
        self.listeners: set[Callable[[], Any]] = set()
        self.outcome_listeners: set[Callable[[dict[str, Any]], None]] = set()

    def subscribe_outcomes(self, listener: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        self.outcome_listeners.add(listener)
        return lambda: self.outcome_listeners.discard(listener)

    def subscribe(self, listener: Callable[[], Any]) -> Callable[[], None]:
        self.listeners.add(listener)
        return lambda: self.listeners.discard(listener)

    def changed(self) -> None:
        for listener in tuple(self.listeners):
            listener()

    def check(self) -> None:
        if self.closed or not self.current():
            raise RecoveryError("Command identity no longer current.")

    async def load(self) -> None:
        async with self.lock:
            await self._load()

    async def _load(self) -> None:
        self.check()
        try:
            raw = await self.store.get(self.key)
            self.check()
            rows = []
            if raw is not None:
                if not isinstance(raw, str) or len(raw.encode()) > MAX_BYTES:
                    raise ValueError

                def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                    result: dict[str, Any] = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError
                        result[key] = value
                    return result

                doc = json.loads(raw, object_pairs_hook=unique)
                if (
                    not isinstance(doc, dict)
                    or set(doc) != {"version", "scope", "records"}
                    or type(doc["version"]) is not int
                    or doc["version"] != 1
                    or doc["scope"] != self.scope
                ):
                    raise ValueError
                rows = doc["records"]
                if not isinstance(rows, list) or len(rows) > MAX_RECORDS:
                    raise ValueError
                for row in rows:
                    self._validate_row(row)
            self.records = {(r["host"], r["id"]): r for r in rows}
            if len(self.records) != len(rows):
                raise ValueError
            self.loaded = True
            # A proved prompt success has no created resource to review. Keep
            # creations and failed/uncertain sends until their existing review
            # or exact-resource settlement path resolves them.
            await self._save(
                {key: row for key, row in self.records.items() if not self._settled_prompt(row)}
            )
        except (ValueError, TypeError, KeyError, RecursionError):
            self.error = "Pending storage is invalid. Mutations disabled; reads remain available."
            raise RecoveryError(self.error) from None
        except RecoveryError:
            raise
        except Exception:
            self.error = "Pending storage unavailable. Check status before another mutation."
            raise RecoveryError(self.error) from None
        finally:
            self.changed()

    def _validate_row(self, row: Any) -> None:
        if not isinstance(row, dict) or set(row) - {"unresolvable"} != _ROW_KEYS:
            raise ValueError
        # Only an unknown prompt can carry the mark that offers Dismiss.
        if "unresolvable" in row and (
            row["unresolvable"] is not True
            or row["kind"] != "agent.prompt"
            or row["outcome"] != "unknown"
        ):
            raise ValueError
        if (
            not _identity(row["host"])
            or not _identity(row["id"])
            or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", row["id"])
            or row["kind"] not in KINDS
            or row["outcome"] not in OUTCOMES
        ):
            raise ValueError
        if type(row["created_ms"]) is not int or row["created_ms"] < 0:
            raise ValueError
        if (
            not isinstance(row["target"], dict)
            or not row["target"]
            or set(row["target"]) - {"agent_id", "workspace_id", "workspace_mode"}
            or any(not _identity(v) for v in row["target"].values())
        ):
            raise ValueError
        target = row["target"]
        if row["kind"] in ("agent.prompt", "agent.rename"):
            if set(target) != {"agent_id"}:
                raise ValueError
        elif set(target) != {"workspace_id"} and target != {"workspace_mode": "new"}:
            raise ValueError
        if (
            not isinstance(row["created"], dict)
            or set(row["created"]) - IDENTITIES
            or any(not _identity(v) for v in row["created"].values())
        ):
            raise ValueError

    async def _save(self, rows: dict[tuple[str, str], dict[str, Any]]) -> None:
        self.check()
        if rows == self.records and not self.error:
            return
        encoded = json.dumps(
            {"version": 1, "scope": self.scope, "records": list(rows.values())},
            separators=(",", ":"),
        )
        if len(rows) > MAX_RECORDS or len(encoded.encode()) > MAX_BYTES:
            raise RecoveryError(
                "Too many sends are waiting to be confirmed. Check them before sending."
            )
        try:
            await self.store.set(self.key, encoded)
        except Exception:
            self.error = "Pending storage unavailable. Check status before another mutation."
            self.changed()
            raise RecoveryError(self.error) from None
        self.records = rows
        self.check()
        self.error = ""
        self.changed()

    @staticmethod
    def _settled_prompt(row: dict[str, Any]) -> bool:
        return bool(
            row["kind"] in ("agent.prompt", "agent.rename") and row["outcome"] == "succeeded"
        )

    @staticmethod
    def needs_review(row: dict[str, Any], now_ms: int) -> bool:
        """Anything kept for review, or an operation still in flight after the grace period."""
        if row["outcome"] not in IN_FLIGHT:
            return True
        return now_ms - int(row.get("created_ms", 0)) >= REVIEW_GRACE_MS

    @staticmethod
    def unresolved(row: dict[str, Any]) -> bool:
        return row["outcome"] not in FINAL or bool(
            row["created"]
            and row["target"].get("workspace_mode") == "new"
            and row["outcome"] != "succeeded"
        )

    def _blockers(self, submit: CommandSubmit) -> list[dict[str, Any]]:
        """The unresolved rows that keep a new send to the same target from being admitted."""
        return [
            r
            for r in self.records.values()
            if r["host"] == submit["host_id"]
            and r["kind"] == submit["kind"]
            and r["kind"] != "agent.rename"
            and r["target"] == submit["target"]
            and self.unresolved(r)
        ]

    def _standing(self, submit: CommandSubmit) -> str:
        blockers = self._blockers(submit)
        if not blockers:
            return RELEASED
        return DISMISSABLE if all(self.can_dismiss(r) for r in blockers) else WAITING

    async def begin(
        self, command: dict[str, Any], check: Callable[[], Awaitable[None]] | None = None
    ) -> None:
        try:
            validate("relaydeck/gateway/v1/command-submit", command)
        except SchemaError:
            raise RecoveryError("Invalid command identity; nothing was sent.") from None
        submit = cast(CommandSubmit, command)
        async with self.lock:
            await self._load()
            key = (submit["host_id"], submit["command_id"])
            if key in self.records or self._blockers(submit):
                raise UnconfirmedEarlier(submit["kind"], check, lambda: self._standing(submit))
            row = {
                "host": key[0],
                "id": key[1],
                "kind": submit["kind"],
                "target": dict(submit["target"]),
                "created_ms": self.clock(),
                "outcome": "pending",
                "created": {},
            }
            self._validate_row(row)
            await self._save({**self.records, key: row})

    async def observe(
        self,
        host: str,
        command_id: str,
        doc: dict[str, Any] | None,
        *,
        unresolvable: bool = False,
    ) -> None:
        """Fold one status read in. `unresolvable` is the gateway's final "cannot tell" answer."""
        async with self.lock:
            await self._load()
            self.check()
            previous = self.records.get((host, command_id))
            if previous is None:
                return
            outcome, created = "unknown", dict(previous["created"])
            validated = False
            if doc is not None:
                try:
                    validate("relaydeck/gateway/v1/command-record", doc)
                    record = cast(CommandRecord, doc)
                    if (
                        record["host_id"] != host
                        or record["command_id"] != command_id
                        or record["kind"] != previous["kind"]
                        or record["target"] != previous["target"]
                    ):
                        raise ValueError
                    outcome = record["status"] if record["status"] in OUTCOMES else "unknown"
                    result = record.get("result", {})
                    created.update(
                        {k: v for k, v in result.items() if k in IDENTITIES and _identity(v)}
                    )
                    validated = True
                except (SchemaError, ValueError, KeyError, TypeError):
                    outcome = "unknown"
            # Lost/expired reads never weaken an already proven terminal outcome.
            if previous["outcome"] in FINAL:
                outcome = previous["outcome"]
            row = {
                **{k: v for k, v in previous.items() if k != "unresolvable"},
                "outcome": outcome,
                "created": created,
            }
            # A failed read says nothing new, so an earlier final answer stands until a record
            # arrives.
            if (
                outcome == "unknown"
                and previous["kind"] == "agent.prompt"
                and (
                    unresolvable
                    or ((doc is None or not validated) and previous.get("unresolvable"))
                )
            ):
                row["unresolvable"] = True
            rows = {**self.records, (host, command_id): row}
            if self._settled_prompt(row):
                del rows[(host, command_id)]
            await self._save(rows)
            if validated and doc is not None and outcome in FINAL and doc["status"] == outcome:
                for listener in tuple(self.outcome_listeners):
                    listener(doc)

    async def reconcile(
        self,
        status: Callable[[str, str], Awaitable[dict[str, Any] | None]],
        granted: frozenset[str],
    ) -> None:
        await self.load()
        for host, command_id in tuple(self.records):
            self.check()
            if host not in granted:
                continue
            try:
                doc = await status(host, command_id)
                await self.observe(host, command_id, doc)
            except asyncio.CancelledError:
                raise
            except Exception:
                await self.observe(host, command_id, None)

    async def settle_creation(self, host: str, command_id: str, resource_id: str) -> None:
        """Forget a successful creation after the UI verifies its exact resource. Status
        reconciliation alone retains records for explicit review.
        """
        async with self.lock:
            await self._load()
            key = (host, command_id)
            row = self.records.get(key)
            if row is None:
                return
            identity = {"agent.start": "agent_id", "shell.create": "tab_id"}.get(row["kind"])
            if (
                row["outcome"] != "succeeded"
                or identity is None
                or row["created"].get(identity) != resource_id
            ):
                raise RecoveryError("Creation is not verified; check its status before dismissing.")
            await self._save({k: r for k, r in self.records.items() if k != key})

    async def acknowledge(self, host: str, command_id: str) -> None:
        async with self.lock:
            await self._load()
            row = self.records.get((host, command_id))
            if row is None or row["outcome"] not in FINAL:
                raise RecoveryError("An uncertain operation cannot be dismissed as completed.")
            await self._save(
                {key: r for key, r in self.records.items() if key != (host, command_id)}
            )

    @staticmethod
    def can_dismiss(row: dict[str, Any]) -> bool:
        """An unknown prompt that the last status check proved it can no longer settle."""
        return (
            row["kind"] == "agent.prompt"
            and row["outcome"] == "unknown"
            and row.get("unresolvable") is True
        )

    async def dismiss(self, host: str, command_id: str, agent_id: str) -> None:
        """Mark an unresolvable prompt reviewed so new prompts to that agent are allowed. Sends
        nothing and keeps no prompt text.
        """
        async with self.lock:
            await self._load()
            row = self.records.get((host, command_id))
            if row is None or row["target"] != {"agent_id": agent_id} or not self.can_dismiss(row):
                raise RecoveryError("This message can't be dismissed.")
            await self._save(
                {key: r for key, r in self.records.items() if key != (host, command_id)}
            )
            notice = {
                "host_id": host,
                "command_id": command_id,
                "kind": row["kind"],
                "target": row["target"],
                "status": DISMISSED,
            }
            for listener in tuple(self.outcome_listeners):
                listener(notice)

    async def close(self) -> None:
        self.closed = True
        async with self.lock:
            self.listeners.clear()
            self.outcome_listeners.clear()
