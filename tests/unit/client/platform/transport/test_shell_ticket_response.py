"""Terminal ticket adapter rejects successful responses without a usable ticket."""

from __future__ import annotations

import httpx
import pytest

from relaydeck.client.features.terminal.pictures import PictureApi as PictureApiFactory
from relaydeck.client.ports.terminal import ShellOpError
from tests.support.host_harness import run


def test_ticket_missing_is_typed_error_without_retryable() -> None:
    """A ticket-less 2xx is `upstream_incompatible`, never a KeyError;
    `ShellOpError` carries no `retryable` signal."""

    async def scenario() -> None:
        bodies = [{}, {"ticket": 123}, {"ticket": ""}]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=bodies.pop(0))

        async def token() -> str:
            return "tok"

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            from relaydeck.client.platform.transport.shell_ops import HttpxShellGatewayOps

            ops = HttpxShellGatewayOps(
                base_url="http://t",
                token=token,
                client=client,
                picture_api_factory=PictureApiFactory,
            )
            for _ in range(3):
                with pytest.raises(ShellOpError) as exc:
                    await ops.terminal_ticket("lease-1")
                assert exc.value.code == "upstream_incompatible"
                assert not hasattr(exc.value, "retryable")
        assert not hasattr(ShellOpError("c", "m"), "retryable")

    run(scenario())
