"""Adapt gateway identity, inventory and command operations to authenticated HTTP and streaming
transports.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any

import httpx
import websockets.asyncio.client as ws_client

from relaydeck.client.platform.transport.sse import SseParser
from relaydeck.client.ports.commands import CommandRecoveryUnavailable
from relaydeck.client.ports.transport import CommandNotAccepted as CommandNotAccepted
from relaydeck.client.ports.transport import GatewayTransport as GatewayTransport
from relaydeck.client.ports.transport import SseFrame as SseFrame
from relaydeck.client.ports.transport import TerminalSocket as TerminalSocket
from relaydeck.client.ports.transport import TransportError as TransportError
from relaydeck.contracts import paths
from relaydeck.contracts.cursors import AFTER_SEQUENCE_HEADER, MAX_CURSOR_LEN
from relaydeck.contracts.schemas import SchemaError, validate


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass
class HttpxGatewayTransport(GatewayTransport):
    base_url: str
    token: Callable[[], Any]  # async callable returning the access token
    client: httpx.AsyncClient
    timeout: float = 30.0
    commands: Any = None

    async def _token(self) -> str:
        value = await self.token()
        if not value:
            raise TransportError("unauthenticated", "no access token", False)
        return str(value)

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            expected_version = kwargs.pop("_command_version", None)
            prepared_token = kwargs.pop("_access_token", None)
            token = prepared_token if prepared_token is not None else await self._token()
            if self.commands is not None and (url.endswith("/commands") or "/commands/" in url):
                self.commands.check()
                if self.commands.version() != expected_version:
                    raise CommandNotAccepted(
                        "command_recovery",
                        "Command lifecycle changed; check status before another mutation.",
                        False,
                    )
            extra_headers = kwargs.pop("headers", {})
            return await self.client.request(
                method,
                url,
                headers={**extra_headers, **_auth_headers(token)},
                timeout=self.timeout,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise TransportError("host_offline", str(exc), True) from exc

    async def me(self) -> dict[str, Any]:
        response = await self._request("GET", self.base_url + paths.ME)
        return self._management_doc(response, "me")

    async def hosts(self) -> dict[str, Any]:
        response = await self._request("GET", self.base_url + paths.HOSTS)
        return self._management_doc(response, "hosts")

    async def usage(
        self,
        host_id: str,
        *,
        days: int | None = None,
        start: str | None = None,
        end: str | None = None,
        space_id: str | None = None,
        cursor: str | None = None,
        bucket: str | None = None,
        if_none_match: str | None = None,
    ) -> dict[str, Any]:
        """GET /v1/hosts/{host}/usage. `days` (1..90) and start/end are mutually exclusive; the
        gateway's `error` envelope (usage_disabled, usage_unavailable) is kept as the code.
        """
        params: dict[str, str] = {}
        if days is not None:
            params["days"] = str(days)
        if start is not None:
            params["from"] = start
        if end is not None:
            params["to"] = end
        if space_id is not None:
            params["space_id"] = space_id
        if cursor is not None:
            params["cursor"] = cursor
        if bucket is not None:
            params["bucket"] = bucket
        response = await self._request(
            "GET",
            self.base_url + paths.usage(host_id),
            params=params,
            **({"headers": {"If-None-Match": if_none_match}} if if_none_match else {}),
        )
        if response.status_code == 304:
            return {"not_modified": True, "report_id": response.headers.get("etag")}
        if 200 <= response.status_code < 300:
            body = self._checked(response)
            if response.headers.get("etag"):
                body["report_id"] = response.headers["etag"]
            return body
        try:
            body = response.json()
        except ValueError:
            body = {}
        code = str(body.get("error", "usage_unavailable")) if isinstance(body, dict) else ""
        raise TransportError(code or "usage_unavailable", "usage request refused", False)

    async def git_summaries(self, host_id: str, agent_ids: Sequence[str]) -> dict[str, Any]:
        response = await self._request(
            "POST",
            self.base_url + paths.git_summaries(host_id),
            json={"agent_ids": list(agent_ids)},
        )
        return self._checked(response)

    async def git_changes(
        self, host_id: str, worktree_id: str, *, scope: str, cursor: str | None = None
    ) -> dict[str, Any]:
        params = {"scope": scope, **({"cursor": cursor} if cursor else {})}
        response = await self._request(
            "GET", self.base_url + paths.git_changes(host_id, worktree_id), params=params
        )
        return self._checked(response)

    async def git_file_diff(
        self,
        host_id: str,
        worktree_id: str,
        file_id: str,
        *,
        cursor: str | None = None,
        load: bool = False,
    ) -> dict[str, Any]:
        params = {**({"cursor": cursor} if cursor else {}), **({"load": "1"} if load else {})}
        response = await self._request(
            "GET",
            self.base_url + paths.git_file_diff(host_id, worktree_id, file_id),
            params=params,
        )
        return self._checked(response)

    async def git_graph(
        self, host_id: str, worktree_id: str, *, mode: str = "all", cursor: str | None = None
    ) -> dict[str, Any]:
        response = await self._request(
            "GET",
            self.base_url + paths.git_graph(host_id, worktree_id),
            params={"mode": mode, **({"cursor": cursor} if cursor else {})},
        )
        return self._checked(response)

    async def git_commit_changes(
        self, host_id: str, worktree_id: str, commit_id: str, *, cursor: str | None = None
    ) -> dict[str, Any]:
        response = await self._request(
            "GET",
            self.base_url + paths.git_commit_changes(host_id, worktree_id, commit_id),
            params={"cursor": cursor} if cursor else None,
        )
        return self._checked(response)

    async def git_bases(
        self, host_id: str, worktree_id: str, *, cursor: str | None = None
    ) -> dict[str, Any]:
        response = await self._request(
            "GET",
            self.base_url + paths.git_bases(host_id, worktree_id),
            params={"cursor": cursor} if cursor else {},
        )
        return self._checked(response)

    async def git_set_base(self, host_id: str, worktree_id: str, base_id: str) -> dict[str, Any]:
        response = await self._request(
            "PUT",
            self.base_url + paths.git_base(host_id, worktree_id),
            json={"base_id": base_id},
        )
        return self._checked(response)

    async def register_device(self, device_id: str, token: str) -> dict[str, Any]:
        response = await self._request(
            "POST",
            self.base_url + paths.DEVICES,
            json={
                "device_id": device_id,
                "token": token,
                "platform": "android",
            },
        )
        doc = self._management_doc(response, "device-registration")
        if doc["device_id"] != device_id:
            raise TransportError("upstream_incompatible", "device scope mismatch", False)
        return doc

    async def resolve_notification(self, value: str, *, link: bool = False) -> dict[str, Any]:
        response = await self._request(
            "POST",
            self.base_url + paths.DEEP_LINK_RESOLVE,
            json={
                "token" if link else "notification_id": value,
            },
        )
        doc = self._management_doc(response, "deep-link-resolution")
        if doc["route"] != {"host_id": doc["host_id"], "session_id": doc["session_id"]}:
            raise TransportError("upstream_incompatible", "notification route mismatch", False)
        return doc

    async def web_push_state(self) -> dict[str, bool]:
        """Whether web push is on for this companion and whether THIS device is subscribed."""
        response = await self._request("GET", self.base_url + paths.PUSH_KEY)
        doc = self._checked(response)
        if (
            set(doc) != {"publicKey", "enabled", "subscribed", "csrf", "choice"}
            or type(doc["enabled"]) is not bool
            or type(doc["subscribed"]) is not bool
        ):
            raise TransportError("upstream_incompatible", "invalid web push state", False)
        return {"enabled": doc["enabled"], "subscribed": doc["subscribed"]}

    async def web_push_preferences(self) -> list[str]:
        """This device's choice: the categories it receives (blocked and done)."""
        response = await self._request("GET", self.base_url + paths.PUSH_PREFS)
        doc = self._checked(response)
        values = doc.get("categories")
        if set(doc) != {"categories"} or not isinstance(values, list):
            raise TransportError("upstream_incompatible", "invalid notification choice", False)
        if not all(isinstance(value, str) for value in values) or not set(values) <= {
            "blocked",
            "done",
        }:
            raise TransportError("upstream_incompatible", "invalid notification choice", False)
        return list(values)

    async def set_web_push_preferences(self, categories: list[str]) -> list[str]:
        response = await self._request(
            "PUT", self.base_url + paths.PUSH_PREFS, json={"categories": list(categories)}
        )
        doc = self._checked(response)
        if set(doc) != {"categories"} or not isinstance(doc["categories"], list):
            raise TransportError("upstream_incompatible", "notification choice not saved", True)
        return list(doc["categories"])

    async def web_push_disable(self) -> None:
        """Settings > Notifications > Turn off: the same DELETE the browser sends, bearer-authed."""
        response = await self._request("DELETE", self.base_url + paths.PUSH_SUBSCRIPTION)
        if response.status_code != 204:
            raise TransportError("upstream_incompatible", "web push not turned off", True)

    async def resolve_web_route(self, token: str) -> dict[str, str]:
        """Resolve a notification tap to a device-owned category, host and session.
        A refused, expired or foreign token raises TransportError; the caller stays on Console."""
        response = await self._request(
            "POST", self.base_url + paths.PUSH_RESOLVE, json={"route": token}
        )
        doc = self._checked(response)
        fields = ("category", "host_id", "session_id")
        if (
            not isinstance(doc, dict)
            or set(doc) != set(fields)
            or not all(isinstance(doc[name], str) and doc[name] for name in fields)
        ):
            raise TransportError("upstream_incompatible", "invalid web route response", False)
        return {name: doc[name] for name in fields}

    def _management_doc(self, response: httpx.Response, kind: str) -> dict[str, Any]:
        doc = self._checked(response)
        try:
            validate(f"relaydeck/gateway/v1/{kind}", doc)
        except SchemaError:
            raise TransportError(
                "upstream_incompatible", "invalid management response", False
            ) from None
        return doc

    async def create_enrollment(self, label: str, pubkey: str) -> dict[str, Any]:
        body = {"host_label": label, "pubkey": pubkey}
        try:
            validate("relaydeck/gateway/v1/enrollment-create", body)
        except SchemaError:
            raise TransportError("forbidden", "invalid enrollment request", False) from None
        response = await self._request("POST", self.base_url + paths.ENROLLMENTS, json=body)
        return self._management_doc(response, "enrollment")

    async def revoke_host(self, host_id: str) -> dict[str, Any]:
        response = await self._request("DELETE", self.base_url + paths.host(host_id))
        return self._management_doc(response, "host-revoke")

    async def devices(self) -> dict[str, Any]:
        response = await self._request("GET", self.base_url + paths.DEVICES)
        return self._management_doc(response, "devices")

    async def metrics(self) -> dict[str, Any]:
        response = await self._request("GET", self.base_url + paths.METRICS)
        return self._management_doc(response, "server-metrics")

    async def updates(self) -> dict[str, Any]:
        """The companion's update notice. A 404 (a bare gateway without the
        route) surfaces as `not_found` so the client hides the section."""
        response = await self._request("GET", self.base_url + paths.UPDATES)
        if response.status_code == 404:
            raise TransportError("not_found", "updates are not available", False)
        return self._management_doc(response, "updates")

    async def enrollment_status(self, enrollment_id: str) -> dict[str, Any]:
        response = await self._request("GET", self.base_url + paths.enrollment(enrollment_id))
        return self._management_doc(response, "enrollment-status")

    async def revoke_device(self, device_id: str) -> dict[str, Any]:
        response = await self._request("DELETE", self.base_url + paths.device(device_id))
        doc = self._checked(response)
        try:
            validate("relaydeck/gateway/v1/device-revoke", doc)
        except SchemaError:
            raise TransportError(
                "upstream_incompatible", "invalid device revoke response", False
            ) from None
        return doc

    async def snapshot(self, host_id: str) -> dict[str, Any]:
        response = await self._request("GET", self.base_url + paths.host_snapshot(host_id))
        return self._checked(response)

    async def agents(
        self,
        host_id: str,
        limit: int = 50,
        page_token: str = "",
        *,
        project: str = "",
        status: str = "",
        query: str = "",
    ) -> dict[str, Any]:
        params: dict[str, str] = {"limit": str(limit)}
        if page_token:
            params["page_token"] = page_token
        for key, value in (("project", project), ("status", status), ("q", query)):
            if value:
                params[key] = value
        response = await self._request(
            "GET", self.base_url + paths.host_agents(host_id), params=params
        )
        doc = self._checked(response)
        try:
            validate("relaydeck/gateway/v1/agents-page", doc)
        except SchemaError:
            raise TransportError("upstream_incompatible", "invalid inventory page", False) from None
        if doc["host_id"] != host_id:
            raise TransportError("upstream_incompatible", "inventory host mismatch", False)
        return doc

    async def mark_agent_read(
        self,
        host_id: str,
        agent_id: str,
        host_epoch: str,
        activity_id: str,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            self.base_url + paths.agent_read(host_id, agent_id),
            json={"host_epoch": host_epoch, "activity_id": activity_id},
        )
        doc = self._checked(response)
        try:
            validate("relaydeck/gateway/v1/agent-read", doc)
        except SchemaError:
            raise TransportError("upstream_incompatible", "invalid read marker", False) from None
        if (
            doc["host_id"],
            doc["agent_id"],
            doc["host_epoch"],
            doc["activity_id"],
            doc["unread"],
        ) != (
            host_id,
            agent_id,
            host_epoch,
            activity_id,
            False,
        ):
            raise TransportError("upstream_incompatible", "read marker context mismatch", False)
        return doc

    async def stream(self, host_id: str, cursor: str) -> AsyncGenerator[SseFrame]:
        params = {"host_id": host_id}
        if cursor:
            params["cursor"] = cursor
        try:
            async with self.client.stream(
                "GET",
                self.base_url + paths.EVENTS,
                params=params,
                headers=_auth_headers(await self._token()),
                timeout=self.timeout,
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    raise self._as_error(response)
                # Client-only readiness marker after accepted HTTP headers.
                # Idle streams may contain only SSE comments; snapshots do
                # not prove that this authenticated request was accepted.
                boundary = response.headers.get(AFTER_SEQUENCE_HEADER)
                opened = ""
                if boundary is not None:
                    if (
                        not boundary
                        or len(boundary) > MAX_CURSOR_LEN
                        or not boundary.isascii()
                        or not boundary.isdecimal()
                    ):
                        raise TransportError("upstream_incompatible", "bad stream boundary", False)
                    opened = json.dumps(
                        {"host_id": host_id, "cursor": cursor, "sequence": int(boundary)}
                    )
                yield SseFrame("stream_open", opened)
                parser = SseParser()
                async for chunk in response.aiter_bytes():
                    for frame in parser.feed(chunk):
                        yield frame
        except httpx.HTTPError as exc:
            raise TransportError("host_offline", str(exc), True) from exc

    async def submit_command(self, command: dict[str, Any]) -> dict[str, Any]:
        version = self.commands.version() if self.commands is not None else None
        # Authentication may await a refresh. No pending intent exists until it succeeds.
        try:
            async with asyncio.timeout(self.timeout):
                token = await self._token()
        except TimeoutError as exc:
            raise CommandNotAccepted(
                "unauthenticated", "Sign-in check timed out. Your draft is kept; try again.", False
            ) from exc
        if self.commands is not None:
            await self.commands.begin(
                command,
                check=partial(
                    self.commands.reconcile, self.command_status, frozenset({command["host_id"]})
                ),
            )
        try:
            # The hosted ASGI adapter does not enforce httpx's socket timeout.
            async with asyncio.timeout(self.timeout):
                response = await self._request(
                    "POST",
                    self.base_url + paths.host_commands(command["host_id"]),
                    json=command,
                    _command_version=version,
                    _access_token=token,
                )
            # Validated pre-journal refusals prove this submission was not sent.
            # Keep preparation/outbox guards aligned under command_lock to preserve that guarantee.
            if 400 <= response.status_code < 500:
                try:
                    refusal = response.json()
                    validate("relaydeck/gateway/v1/error", refusal)
                except (ValueError, SchemaError):
                    refusal = {}
                if refusal.get("code") in {
                    "unauthenticated",
                    "forbidden",
                    "not_found",
                    "stale_epoch",
                    "stale_revision",
                    "unsupported_capability",
                    "rate_limited",
                    "too_large",
                }:
                    message = refusal["message"]
                    if command["kind"] == "agent.prompt":
                        message += " Your draft is kept; check the agent and send again."
                    raise CommandNotAccepted(refusal["code"], message, False)
            doc = self._checked(response)
        except CommandNotAccepted as exc:
            if self.commands is not None:
                await self.commands.observe(
                    command["host_id"],
                    command["command_id"],
                    {
                        "protocol": command["protocol"],
                        "command_id": command["command_id"],
                        "host_id": command["host_id"],
                        "kind": command["kind"],
                        "target": command["target"],
                        "status": "rejected",
                        "error_code": exc.code,
                    },
                )
            raise
        if self.commands is not None:
            await self.commands.observe(command["host_id"], command["command_id"], doc)
        return doc

    async def command_status(self, host_id: str, command_id: str) -> dict[str, Any] | None:
        async with asyncio.timeout(self.timeout):
            return await self._command_status(host_id, command_id)

    async def _command_status(self, host_id: str, command_id: str) -> dict[str, Any] | None:
        response = await self._request(
            "GET",
            self.base_url + paths.host_command(host_id, command_id),
            _command_version=self.commands.version() if self.commands is not None else None,
        )
        doc = None if response.status_code == 404 else self._checked(response)
        if doc is None and self.commands is not None:
            await self.commands.load()
            row = self.commands.records.get((host_id, command_id))
            if row is not None and row["kind"] == "agent.prompt" and self.commands.unresolved(row):
                # A plain 404 is not proof: reserve the absent ID against a delayed POST.
                resolved = await self._request(
                    "POST",
                    self.base_url + paths.host_command_resolve(host_id, command_id),
                    json={
                        "protocol": "relaydeck.gateway.v1",
                        "agent_id": row["target"]["agent_id"],
                        "created_ms": row["created_ms"],
                    },
                    _command_version=self.commands.version(),
                )
                if resolved.status_code == 404:
                    await self.commands.observe(host_id, command_id, None, unresolvable=True)
                    raise CommandRecoveryUnavailable(
                        "Prompt resolution unavailable; outcome remains unknown."
                    )
                doc = self._checked(resolved)
        if self.commands is not None:
            await self.commands.observe(host_id, command_id, doc)
        return doc

    async def terminal_ticket(self, lease_id: str) -> dict[str, Any]:
        response = await self._request(
            "POST", self.base_url + paths.TERMINAL_TICKETS, json={"lease_id": lease_id}
        )
        return self._checked(response)

    async def create_attachment(
        self,
        host_id: str,
        terminal_id: str,
        *,
        kind: str,
        takeover: bool,
        cols: int = 80,
        rows: int = 24,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            self.base_url + paths.terminal_attachments(host_id, terminal_id),
            json={"kind": kind, "takeover": takeover, "cols": cols, "rows": rows},
        )
        return self._checked(response)

    async def create_lease(self, attachment_id: str, *, mode: str) -> dict[str, Any]:
        response = await self._request(
            "POST",
            self.base_url + paths.attachment_leases(attachment_id),
            json={"mode": mode},
        )
        return self._checked(response)

    async def renew_lease(self, lease_id: str) -> dict[str, Any]:
        response = await self._request("POST", self.base_url + paths.lease_renew(lease_id), json={})
        return self._checked(response)

    def _checked(self, response: httpx.Response) -> dict[str, Any]:
        if 200 <= response.status_code < 300:
            try:
                data = response.json()
            except ValueError as exc:
                raise TransportError("upstream_incompatible", "bad json", False) from exc
            if not isinstance(data, dict):
                raise TransportError("upstream_incompatible", "bad shape", False)
            return data
        raise self._as_error(response)

    def _as_error(self, response: httpx.Response) -> TransportError:
        try:
            data = response.json()
        except ValueError:
            data = {}
        code = (
            str(data.get("code", "upstream_incompatible"))
            if isinstance(data, dict)
            else "upstream_incompatible"
        )
        message = (
            str(data.get("message", "gateway error")) if isinstance(data, dict) else "gateway error"
        )
        # Explicit retry set: only transport-level and rate/stale codes
        # reconnect. Every other envelope is terminal for this sync run.
        retryable = response.status_code in (503, 429) or code in (
            "host_offline",
            "stale_epoch",
            "stale_revision",
        )
        if code in ("unauthenticated", "forbidden"):
            retryable = False
        details = data.get("details") if isinstance(data, dict) else None
        details = dict(details) if isinstance(details, dict) else None
        header = response.headers.get("retry-after", "")
        if header.isdecimal() and (details is None or "retry_after_s" not in details):
            details = {**(details or {}), "retry_after_s": int(header)}
        return TransportError(code, message, retryable, details)


# The default close handshake waits 10 s, and after an unread backlog the peer's close frame is
# never read, so every close() stalls that long. Closing is best-effort, so bound it.
WS_CLOSE_TIMEOUT_S = 1.0


@dataclass
class WebsocketTerminalSocket(TerminalSocket):
    url: str
    timeout: float = 30.0
    _ws: Any = None

    async def connect(self) -> None:
        try:
            self._ws = await ws_client.connect(
                self.url, open_timeout=self.timeout, close_timeout=WS_CLOSE_TIMEOUT_S
            )
        except Exception as exc:
            raise TransportError("host_offline", f"ws connect: {exc}") from exc

    async def send_bytes(self, data: bytes) -> None:
        try:
            await self._ws.send(data)
        except Exception as exc:
            raise TransportError("host_offline", f"ws send: {exc}") from exc

    async def send_text(self, data: str) -> None:
        try:
            await self._ws.send(data)
        except Exception as exc:
            raise TransportError("host_offline", f"ws send: {exc}") from exc

    async def receive(self) -> tuple[str, Any]:
        try:
            message = await self._ws.recv()
        except Exception as exc:
            raise TransportError("host_offline", f"ws recv: {exc}") from exc
        if isinstance(message, bytes):
            return ("bytes", message)
        return ("text", message)

    async def close(self) -> None:
        if self._ws is not None:
            ws, self._ws = self._ws, None
            try:
                await asyncio.wait_for(ws.close(), WS_CLOSE_TIMEOUT_S * 2)
            except Exception:  # noqa: S110 - close is best-effort
                pass
