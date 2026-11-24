"""ASGI body/admission budgets release on every exit and do not time out live streams."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any

import pytest
from starlette.types import Message, Receive, Scope, Send

from relaydeck.contracts import paths
from relaydeck.contracts.schemas import validate
from relaydeck.gateway.http.bounds import MAX_JSON_BODY
from relaydeck.gateway.http.middleware.body_cap import BodyCapMiddleware
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)


def scope(path: str = "/synthetic", **kwargs: Any) -> Scope:
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [],
        "http_version": "1.1",
        **kwargs,
    }


class Capture:
    def __init__(self) -> None:
        self.messages: list[Message] = []

    async def send(self, message: Message) -> None:
        self.messages.append(message)

    def error(self, status: int, reason: str) -> None:
        assert len(self.messages) == 2
        start, end = self.messages
        assert start["status"] == status
        document = json.loads(end["body"])
        validate("relaydeck/gateway/v1/error", document)
        assert document["details"]["reason"] == reason
        assert b"PRIVATE" not in end["body"]
        assert (b"cache-control", b"no-store") in start["headers"]
        assert (b"connection", b"close") in start["headers"]


async def empty() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


async def unavailable() -> Message:
    raise AssertionError("must reject without reading")


async def forbidden_app(scope: Scope, receive: Receive, send: Send) -> None:
    raise AssertionError("must reject before the route")


async def ok(scope: Scope, receive: Receive, send: Send) -> None:
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


@pytest.mark.parametrize(
    "headers,status,reason",
    [
        ([(b"content-length", b"65537")], 413, "body_size"),
        ([(b"content-length", b"9" * 10000)], 413, "body_size"),
        ([(b"content-length", b"PRIVATE_PATH")], 400, "body_framing"),
        ([(b"content-length", b"-1")], 400, "body_framing"),
        ([(b"content-length", b"+1")], 400, "body_framing"),
        ([(b"content-length", b"1"), (b"content-length", b"1")], 400, "body_framing"),
        ([(b"content-length", b"1"), (b"transfer-encoding", b"chunked")], 400, "body_framing"),
    ],
)
def test_declared_length_is_rejected_before_any_read(
    headers: list[tuple[bytes, bytes]], status: int, reason: str
) -> None:
    capture = Capture()
    asyncio.run(BodyCapMiddleware(forbidden_app)(scope(headers=headers), unavailable, capture.send))
    capture.error(status, reason)


def test_streamed_size_is_enforced_even_with_a_false_declared_length() -> None:
    async def scenario() -> Capture:
        chunks = [b"x" * MAX_JSON_BODY, b"x"]

        async def receive() -> Message:
            return {"type": "http.request", "body": chunks.pop(0), "more_body": bool(chunks)}

        capture = Capture()
        await BodyCapMiddleware(forbidden_app)(
            scope(headers=[(b"content-length", b"0")]), receive, capture.send
        )
        return capture

    asyncio.run(scenario()).error(413, "body_size")


@pytest.mark.parametrize("mode", ["stalled", "trickle", "empty_chunks"])
def test_total_receive_deadline_is_not_renewed_by_progress(mode: str) -> None:
    async def scenario() -> Capture:
        capture = Capture()
        calls = 0

        async def receive() -> Message:
            nonlocal calls
            calls += 1
            if mode == "stalled":
                await asyncio.Event().wait()
            elif mode == "trickle":
                await asyncio.sleep(0.005)
            return {
                "type": "http.request",
                "body": b"x" if mode == "trickle" else b"",
                "more_body": True,
            }

        app = BodyCapMiddleware(forbidden_app, body_timeout=0.025)
        await asyncio.wait_for(app(scope(), receive, capture.send), 1)
        assert calls > (1 if mode == "trickle" else 0)
        assert app._http.active == 0
        return capture

    asyncio.run(scenario()).error(429, "body_timeout")


def test_replay_and_disconnect_preserve_the_receive_sequence() -> None:
    async def scenario() -> None:
        pending = [
            {"type": "http.request", "body": b"one", "more_body": True},
            {"type": "http.request", "body": b"two", "more_body": False},
            {"type": "http.disconnect"},
        ]
        seen = []

        async def receive() -> Message:
            return pending.pop(0)

        async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
            seen.extend([await receive(), await receive()])

        app = BodyCapMiddleware(downstream)
        await app(scope(), receive, Capture().send)
        assert seen == [
            {"type": "http.request", "body": b"onetwo", "more_body": False},
            {"type": "http.disconnect"},
        ]
        assert not pending and app._http.active == 0
        seen.clear()
        pending.append({"type": "http.disconnect"})
        await app(scope(), receive, Capture().send)
        assert seen == [{"type": "http.disconnect"}] * 2
        assert app._http.active == 0

    asyncio.run(scenario())


def test_http_admission_has_no_waiters_and_cancellation_releases_it() -> None:
    async def scenario() -> None:
        started, finish = asyncio.Event(), asyncio.Event()

        async def downstream(s: Scope, receive: Receive, send: Send) -> None:
            started.set()
            await finish.wait()
            await ok(s, receive, send)

        app = BodyCapMiddleware(downstream, http_limit=1)
        first = asyncio.create_task(app(scope(), empty, Capture().send))
        await started.wait()
        capture = Capture()
        await asyncio.wait_for(app(scope(), unavailable, capture.send), 1)
        capture.error(429, "capacity")
        first.cancel()
        with suppress(asyncio.CancelledError):
            await first
        assert app._http.active == 0
        finish.set()
        capture = Capture()
        await app(scope(), empty, capture.send)
        assert capture.messages[0]["status"] == 204 and app._http.active == 0

    asyncio.run(scenario())


def test_application_failure_releases_admission() -> None:
    async def scenario() -> None:
        fail = True

        async def downstream(s: Scope, receive: Receive, send: Send) -> None:
            if fail:
                raise RuntimeError("synthetic failure")
            await ok(s, receive, send)

        app = BodyCapMiddleware(downstream, http_limit=1)
        with pytest.raises(RuntimeError):
            await app(scope(), empty, Capture().send)
        assert app._http.active == 0
        fail = False
        capture = Capture()
        await app(scope(), empty, capture.send)
        assert capture.messages[0]["status"] == 204

    asyncio.run(scenario())


def test_sse_and_websockets_have_separate_admission_without_a_lifetime_timeout() -> None:
    async def scenario() -> None:
        started = {kind: asyncio.Event() for kind in ("ordinary", "events", "websocket")}
        finish = asyncio.Event()

        async def downstream(s: Scope, receive: Receive, send: Send) -> None:
            kind = s["test_kind"]
            started[kind].set()
            await finish.wait()

        app = BodyCapMiddleware(
            downstream, http_limit=1, event_limit=1, websocket_limit=1, body_timeout=0.01
        )
        requests = [
            scope(test_kind="ordinary"),
            scope(paths.EVENTS, method="GET", test_kind="events"),
            scope(type="websocket", test_kind="websocket"),
        ]
        tasks = []
        try:
            for s in requests:
                tasks.append(asyncio.create_task(app(s, empty, Capture().send)))
                await asyncio.wait_for(started[s["test_kind"]].wait(), 1)
            await asyncio.sleep(0.03)
            assert all(not task.done() for task in tasks)
            for s in requests:
                capture = Capture()

                async def connect(captured: Capture = capture) -> Message:
                    assert captured.messages == []
                    return {"type": "websocket.connect"}

                await app(s, connect if s["type"] == "websocket" else unavailable, capture.send)
                if s["type"] == "websocket":
                    assert capture.messages == [
                        {"type": "websocket.accept"},
                        {"type": "websocket.close", "code": 1013, "reason": "busy"},
                    ]
                else:
                    capture.error(429, "capacity")
        finally:
            finish.set()
            await asyncio.gather(*tasks)
        assert app._http.active == app._events.active == app._websockets.active == 0
        for s in requests:
            await app(s, empty, Capture().send)

    asyncio.run(scenario())


def test_oversize_response_send_is_not_under_the_receive_deadline() -> None:
    async def scenario() -> None:
        capture = Capture()

        async def receive() -> Message:
            return {"type": "http.request", "body": b"x" * (MAX_JSON_BODY + 1)}

        async def slow_send(message: Message) -> None:
            await asyncio.sleep(0.02)
            await capture.send(message)

        app = BodyCapMiddleware(forbidden_app, body_timeout=0.005)
        await app(scope(), receive, slow_send)
        capture.error(413, "body_size")
        assert app._http.active == 0

    asyncio.run(scenario())


def test_picture_budget_is_not_taken_from_ordinary_http() -> None:
    async def scenario() -> None:
        started, finish = asyncio.Event(), asyncio.Event()
        seen: list[bytes] = []

        async def downstream(s: Scope, receive: Receive, send: Send) -> None:
            if s["path"] == "/ordinary":
                started.set()
                await finish.wait()
            else:
                seen.append((await receive())["body"])
                await asyncio.sleep(0.02)
            await ok(s, receive, send)

        async def upload() -> Message:
            return {"type": "http.request", "body": b"x" * (MAX_JSON_BODY + 1)}

        app = BodyCapMiddleware(downstream, http_limit=1, body_timeout=0.005)
        held = asyncio.create_task(app(scope("/ordinary"), empty, Capture().send))
        await started.wait()
        try:
            capture = Capture()
            await app(
                scope(
                    paths.picture_content("a" * 32),
                    method="PUT",
                    headers=[(b"content-length", b"65537")],
                ),
                upload,
                capture.send,
            )
            assert capture.messages[0]["status"] == 204
            assert len(seen[0]) == MAX_JSON_BODY + 1
            capture = Capture()
            await app(
                scope(paths.picture_content("a" * 32), method="POST"), unavailable, capture.send
            )
            capture.error(429, "capacity")
        finally:
            finish.set()
            await held

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["http", "websocket"])
def test_cancelled_receive_releases_its_pool_without_a_response(kind: str) -> None:
    async def scenario() -> None:
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def receive() -> Message:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            return {"type": "http.disconnect"}

        async def downstream(s: Scope, receive: Receive, send: Send) -> None:
            await receive()

        app = BodyCapMiddleware(downstream)
        capture = Capture()
        task = asyncio.create_task(app(scope(type=kind), receive, capture.send))
        await started.wait()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        assert cancelled.is_set() and capture.messages == []
        assert app._http.active == app._websockets.active == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("hosted", [False, True])
def test_real_gateway_and_hosted_wiring_enforce_the_same_body_deadline(hosted: bool) -> None:
    from relaydeck.companion.app.hosted_web.serve import build_web_app
    from relaydeck.gateway.app.bootstrap import create_app
    from tests.security.helpers import make_state

    async def scenario() -> None:
        state, _ = make_state()
        app = create_app(state)
        if hosted:
            app, _ = build_web_app(
                gateway_state=state,
                gateway_app=app,
                flet_main=lambda page: None,
                public_origin="https://synthetic.invalid",
                cookie_secret=b"x" * 32,
            )
        middleware = next(row for row in app.user_middleware if row.cls is BodyCapMiddleware)
        middleware.kwargs["body_timeout"] = 0.01

        async def stalled() -> Message:
            await asyncio.Event().wait()
            return {"type": "http.disconnect"}

        s = scope(
            "/healthz",
            method="GET",
            asgi={"version": "3.0", "spec_version": "2.4"},
            query_string=b"",
            root_path="",
            scheme="https",
            server=("synthetic.invalid", 443),
            client=("127.0.0.1", 1),
        )
        capture = Capture()
        await asyncio.wait_for(app(s, stalled, capture.send), 1)
        capture.error(429, "body_timeout")
        capture = Capture()
        await app(s, empty, capture.send)
        assert capture.messages[0]["status"] == 200

    asyncio.run(scenario())


def test_authenticated_metadata_stream_remains_available_during_http_overload() -> None:
    from relaydeck.gateway.app.bootstrap import create_app
    from tests.security.helpers import make_state, mint_token

    async def scenario() -> None:
        state, _ = make_state()
        state.started = True
        token = mint_token(state)
        app = BodyCapMiddleware(create_app(state), http_limit=1)
        occupied, streaming = asyncio.Event(), asyncio.Event()

        async def stalled() -> Message:
            occupied.set()
            await asyncio.Event().wait()
            return {"type": "http.disconnect"}

        delivered = False

        async def receive_stream() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return await empty()
            await asyncio.Event().wait()
            return {"type": "http.disconnect"}

        async def send_stream(message: Message) -> None:
            if message["type"] == "http.response.start":
                assert message["status"] == 200
                streaming.set()

        common = {
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "root_path": "",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("127.0.0.1", 1),
        }
        first = asyncio.create_task(app(scope(query_string=b"", **common), stalled, Capture().send))
        await occupied.wait()
        s = scope(
            paths.EVENTS,
            method="GET",
            query_string=b"host_id=home-mac",
            headers=[(b"authorization", f"Bearer {token}".encode())],
            **common,
        )
        stream = asyncio.create_task(app(s, receive_stream, send_stream))
        try:
            await asyncio.wait_for(streaming.wait(), 2)
            capture = Capture()
            await app(scope(), unavailable, capture.send)
            capture.error(429, "capacity")
            assert not stream.done()
        finally:
            first.cancel()
            stream.cancel()
            await asyncio.gather(first, stream, return_exceptions=True)
        assert app._http.active == app._events.active == 0

    asyncio.run(scenario())
