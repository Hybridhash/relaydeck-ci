"""Native picture selection uses the authenticated upload adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import flet as ft
import pytest

from relaydeck.client.features.terminal.pictures import PictureApi as PictureApiFactory
from relaydeck.client.ports.terminal import ShellGatewayOps
from tests.support.host_harness import run
from tests.support.pictures.attachments import (
    choose,
)
from tests.support.terminal.direct_typing import _direct
from tests.support.terminal.latch_text import _inputs


@pytest.mark.parametrize("shell", [False, True])
def test_native_picker_result_uses_authenticated_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shell: bool
) -> None:
    import httpx

    from relaydeck.client.platform.transport.shell_ops import HttpxShellGatewayOps
    from relaydeck.contracts.pairing import DEVICE_CAPABILITIES
    from relaydeck.gateway.app.bootstrap import create_app
    from relaydeck.gateway.features.pictures.adapters.storage import PictureStore
    from tests.security.helpers import make_state
    from tests.support.pictures.uploads import picture

    async def scenario() -> None:
        state, _ = make_state()
        state.pictures = PictureStore(tmp_path / "private")
        token, _ = state.sessions.create(
            "a", DEVICE_CAPABILITIES, {"home-mac"}, state.now_ms(), device_id="one"
        )

        async def get_token() -> str:
            return token

        async def headerless_upload(*args: Any, **kwargs: Any) -> None:
            pytest.fail("Native upload must not use headerless FilePicker.upload")

        selected = tmp_path / "selected.png"
        selected.write_bytes(picture())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(state)), base_url="https://mac.test"
        ) as client:
            api = HttpxShellGatewayOps(
                "https://mac.test",
                get_token,
                client,
                native_picture_upload=True,
                picture_api_factory=PictureApiFactory,
            ).picture_api
            monkeypatch.setattr(ShellGatewayOps, "picture_api", property(lambda _: api))
            monkeypatch.setattr(ft.FilePicker, "upload", headerless_upload)
            app, session, socket, _ = await _direct(shell)
            try:
                engine = session.current_term
                await choose(app, "terminal-attach-picture", selected)
                record = next(iter(state.pictures.uploads.values()))
                assert record.status == "ready" and record.device_id == "one"
                assert _inputs(socket) == [record.path.encode()]
                assert session.current_term is engine
                if not shell:
                    assert session.store.draft == "retained compose draft"
            finally:
                await session.dispose()

    run(scenario())
