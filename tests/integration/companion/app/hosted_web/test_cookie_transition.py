"""Cookie transition proof through the actual HTTP, websocket and push owners."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import msgpack
import pytest
from fastapi import APIRouter
from starlette.requests import Request

from relaydeck.companion.app import bindings
from relaydeck.companion.app.hosted_web import serve
from relaydeck.companion.config import NotificationsConfig
from relaydeck.gateway.app.bootstrap import create_app
from tests.security.helpers import ALL_CAPS, make_state
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)

pytestmark = pytest.mark.usefixtures("hosted_web_bundle")
ORIGIN = "https://transition.example"
CASES = (
    "canonical",
    "legacy",
    "both",
    "invalid-canonical",
    "empty-canonical",
    "duplicate-canonical",
    "duplicate-legacy",
    "duplicate-fields",
    "expired-legacy",
)


def cookie_fields(case: str, value: str) -> list[tuple[bytes, bytes]]:
    fields = {
        "canonical": f"__Host-rd_session={value}",
        "legacy": f"rd_session={value}",
        "both": f"__Host-rd_session={value}; rd_session=foreign",
        "invalid-canonical": f"__Host-rd_session=invalid; rd_session={value}",
        "empty-canonical": f"__Host-rd_session=; rd_session={value}",
        "duplicate-canonical": f"__Host-rd_session={value}; __Host-rd_session={value}",
        "duplicate-legacy": f"rd_session={value}; rd_session={value}",
        "expired-legacy": f"rd_session={value}",
    }
    if case == "duplicate-fields":
        return [(b"cookie", f"__Host-rd_session={value}".encode()), (b"cookie", b"unrelated=x")]
    return [(b"cookie", fields[case].encode())]


def allowed(case: str) -> bool:
    return case in {"canonical", "legacy", "both"}


def session() -> tuple[Any, serve.WebSessionStore, str, list[int]]:
    state, _ = make_state()
    state.public_origin = ORIGIN
    clock = [state.now_ms()]
    access, refresh = state.sessions.create("acct-1", ALL_CAPS, ["home-mac"], clock[0], "device")
    store = serve.WebSessionStore(ORIGIN, b"s" * 32, now_ms=lambda: clock[0])
    sid = store.create_session("acct-1", "device", access, refresh, clock[0] + 900_000)
    return state, store, sid, clock


@pytest.mark.parametrize("case", CASES)
def test_actual_session_check_uses_transition_policy(case: str) -> None:
    async def scenario() -> None:
        state, store, sid, clock = session()
        try:
            app, _ = serve.build_web_app(
                gateway_state=state,
                gateway_app=create_app(state),
                flet_main=lambda page: None,
                public_origin=ORIGIN,
                cookie_secret=store.cookie_secret,
                store=store,
            )
            await store.start_serving()
            if case == "expired-legacy":
                clock[0] += 86_400_000
            headers = [(b"origin", ORIGIN.encode()), *cookie_fields(case, store.sign_sid(sid))]
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=ORIGIN
            ) as http:
                response = await http.post("/v1/auth/session/check", headers=headers)
            assert response.status_code == (204 if allowed(case) else 401)
            issued = response.headers.get_list("set-cookie")
            if case == "legacy":
                assert len(issued) == 2
                assert issued[0].startswith("__Host-rd_session=")
                assert issued[1].startswith("rd_session=;") and "Max-Age=0" in issued[1]
            elif not allowed(case):
                assert issued == []
        finally:
            state.push.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("case", CASES)
def test_actual_flet_guard_observes_only_selected_cookie(case: str) -> None:
    async def scenario() -> None:
        state, store, sid, clock = session()
        try:
            await store.start_serving()
            if case == "expired-legacy":
                clock[0] += 86_400_000
            seen = []

            async def inner(scope: Any, receive: Any, send: Any) -> None:
                seen.append(serve._current_handshake.get()[0])

            app = serve.SessionCookieRenewalMiddleware(
                serve.FletGuardMiddleware(inner, store), store
            )
            messages = iter(
                [
                    {"type": "websocket.connect"},
                    {
                        "type": "websocket.receive",
                        "bytes": b"\x00"
                        + msgpack.packb([1, {"session_id": "", "page_name": "", "page": {}}]),
                    },
                ]
            )

            async def receive() -> Any:
                return next(messages)

            async def send(message: Any) -> None:
                pass

            await app(
                {
                    "type": "websocket",
                    "path": "/ws",
                    "client": ("192.0.2.1", 1),
                    "headers": [
                        (b"origin", ORIGIN.encode()),
                        *cookie_fields(case, store.sign_sid(sid)),
                    ],
                },
                receive,
                send,
            )
            assert seen == [sid if allowed(case) else None]
        finally:
            state.push.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("case", CASES)
def test_actual_webpush_identity_reader_refuses_ambiguous_or_downgraded_cookie(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    state, store, sid, clock = session()
    callback = []

    def router(**kwargs: Any) -> APIRouter:
        callback.append(kwargs["identity"])
        return APIRouter()

    monkeypatch.setattr(bindings.webpush_routes, "build_router", router)
    config = SimpleNamespace(
        state_dir=tmp_path / "state", advertised_origin=ORIGIN, notifications=NotificationsConfig()
    )
    owner = bindings.WebPushBinding(
        config, gateway=lambda: state, stop=lambda: None, web_store=lambda: store
    )
    try:
        asyncio.run(store.start_serving())
        if case == "expired-legacy":
            clock[0] += 86_400_000
        owner._start_web_push(create_app(state))
        request = Request({"type": "http", "headers": cookie_fields(case, store.sign_sid(sid))})
        assert callback[0](request) == (("acct-1", "device") if allowed(case) else None)
    finally:
        if owner.webpush_provider:
            owner.webpush_provider.close()
        if owner.webpush_store:
            owner.webpush_store.close()
        state.push.close()
