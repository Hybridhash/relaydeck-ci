"""Adapt explicit terminal ownership and lease operations to authenticated gateway HTTP."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from relaydeck.client.platform.diagnostics.open_timing import mark_current
from relaydeck.client.ports.terminal import AttachmentInfo, LeaseInfo, ShellGatewayOps, ShellOpError
from relaydeck.contracts import paths


@dataclass
class HttpxShellGatewayOps(ShellGatewayOps):
    """HTTP operations for explicit terminal ownership."""

    base_url: str
    token: Any  # async callable returning the access token
    client: httpx.AsyncClient
    timeout: float = 30.0
    native_picture_upload: bool = False  # Trusted entrypoint, never client Page metadata.
    picture_api_factory: Callable[[Any], Any] = field(kw_only=True)

    @property
    def picture_api(self) -> Any:
        return self.picture_api_factory(self)

    async def _auth(self) -> dict[str, str]:
        value = await self.token()
        if not value:
            raise ShellOpError("unauthenticated", "no access token")
        return {"Authorization": f"Bearer {value}"}

    def _checked(self, response: httpx.Response) -> dict[str, Any]:
        if 200 <= response.status_code < 300:
            try:
                data = response.json()
            except ValueError as exc:
                raise ShellOpError("upstream_incompatible", "bad json") from exc
            if not isinstance(data, dict):
                raise ShellOpError("upstream_incompatible", "bad shape")
            return data
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        code = str(data.get("code", "upstream_incompatible"))
        message = str(data.get("message", "gateway error"))
        details = data.get("details")
        raise ShellOpError(code, message, details if isinstance(details, dict) else {})

    async def attach(
        self,
        host_id: str,
        terminal_id: str,
        kind: str,
        takeover: bool,
        cols: int = 80,
        rows: int = 24,
        *,
        expected_control_revision: str | None = None,
    ) -> AttachmentInfo:
        try:
            response = await self.client.post(
                self.base_url + paths.terminal_attachments(host_id, terminal_id),
                headers=await self._auth(),
                json={
                    "kind": kind,
                    "takeover": takeover,
                    "cols": cols,
                    "rows": rows,
                    **(
                        {"expected_control_revision": expected_control_revision}
                        if expected_control_revision is not None
                        else {}
                    ),
                },
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise ShellOpError("host_offline", str(exc)) from exc
        info = AttachmentInfo.from_wire(self._checked(response))
        mark_current("attach")
        return info

    async def control_holder(self, host_id: str, terminal_id: str) -> dict[str, Any]:
        try:
            response = await self.client.get(
                self.base_url + paths.terminal_control(host_id, terminal_id),
                headers=await self._auth(),
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise ShellOpError("host_offline", str(exc)) from exc
        return self._checked(response)

    async def acquire_lease(
        self,
        attachment_id: str,
        mode: str,
        *,
        expected_generation: int | None = None,
    ) -> LeaseInfo:
        try:
            response = await self.client.post(
                self.base_url + paths.attachment_leases(attachment_id),
                headers=await self._auth(),
                json={
                    "mode": mode,
                    **(
                        {"expected_generation": expected_generation}
                        if expected_generation is not None
                        else {}
                    ),
                },
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise ShellOpError("host_offline", str(exc)) from exc
        lease = LeaseInfo.from_wire(self._checked(response))
        mark_current("lease" if mode == "control" else "view_lease")
        return lease

    async def terminal_ticket(self, lease_id: str) -> str:
        try:
            response = await self.client.post(
                self.base_url + paths.TERMINAL_TICKETS,
                headers=await self._auth(),
                json={"lease_id": lease_id},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise ShellOpError("host_offline", str(exc)) from exc
        doc = self._checked(response)
        ticket = doc.get("ticket")
        if not isinstance(ticket, str) or not ticket:
            raise ShellOpError("upstream_incompatible", "ticket missing from response")
        mark_current("ticket")
        return ticket

    async def release_lease(self, lease_id: str) -> None:
        try:
            response = await self.client.delete(
                self.base_url + paths.lease(lease_id),
                headers=await self._auth(),
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise ShellOpError("host_offline", str(exc)) from exc
        self._checked(response)

    async def close_attachment(self, attachment_id: str) -> None:
        try:
            response = await self.client.delete(
                self.base_url + paths.attachment(attachment_id),
                headers=await self._auth(),
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise ShellOpError("host_offline", str(exc)) from exc
        self._checked(response)

    async def close_terminal(self, command: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.client.post(
                self.base_url + paths.host_commands(quote(command["host_id"], safe="")),
                headers=await self._auth(),
                json=command,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise ShellOpError("host_offline", str(exc)) from exc
        return self._checked(response)
