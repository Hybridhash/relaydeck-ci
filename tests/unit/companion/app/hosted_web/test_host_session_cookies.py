"""Session cookie selection, bounded migration and explicit loopback development."""

from __future__ import annotations

import asyncio
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

import httpx
import pytest

from relaydeck.companion.app.hosted_web.serve import SessionCookieRenewalMiddleware, WebSessionStore
from relaydeck.gateway.features.access.api import SessionStore
from relaydeck.host.persistence.contracts import StorageUnavailable
from relaydeck.host.persistence.sqlite_store import PersistentStore
from relaydeck.version import __version__
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)
from tests.support.persistence.persistence import Config, MemoryKeys

NOW = 1_800_000_000_000
DAY = 86_400_000
ORIGIN = "https://cookie.example"
CANONICAL = "__Host-rd_session"
LEGACY = "rd_session"
DEV = "rd_dev_session"


def raw(value: str) -> list[tuple[bytes, bytes]]:
    return [(b"cookie", value.encode())]


@pytest.mark.parametrize("value", ["", "unsigned", "sid.\u00ff", "\u00ff.signature", "sid.\u0080"])
def test_malformed_session_cookie_is_inert(value: str) -> None:
    store = WebSessionStore(ORIGIN, b"s" * 32)
    assert store.verify_cookie(value) is None


def test_https_issue_renew_delete_have_exact_host_attributes() -> None:
    clock = [NOW]
    store = WebSessionStore(ORIGIN, b"s" * 32, now_ms=lambda: clock[0])
    sid = store.create_session("a", "d", "access", "refresh", NOW + 900_000)
    issued = store.cookie_headers(sid)["Set-Cookie"]
    assert issued.startswith(CANONICAL + "=")
    clock[0] += 1000
    store._sessions[sid].expires_ms += 2000
    renewed = store.cookie_renewal(store.sign_sid(sid))["Set-Cookie"]
    cleared = store.clear_cookie_headers()["Set-Cookie"]
    for value in (issued, renewed, cleared):
        jar = SimpleCookie()
        jar.load(value)
        assert set(jar) == {CANONICAL}
        cookie = jar[CANONICAL]
        assert cookie["secure"] and cookie["httponly"]
        assert cookie["samesite"] == "Lax" and cookie["path"] == "/" and not cookie["domain"]
    assert "Max-Age=0" in cleared
    assert store.verify_cookie(store.sign_sid(sid)) == sid


@pytest.mark.parametrize(
    "origin,enabled,allowed",
    [
        ("http://127.0.0.1:8000", True, True),
        ("http://[::1]:8000", True, True),
        ("http://localhost:8000", True, True),
        ("http://127.0.0.1:8000", False, False),
        ("http://other.example", True, False),
        ("http://127.0.0.1.evil.example", True, False),
    ],
)
def test_plaintext_cookie_requires_explicit_loopback_mode(
    origin: str, enabled: bool, allowed: bool
) -> None:
    store = WebSessionStore(origin, b"s" * 32, dev_http=enabled)
    if not allowed:
        with pytest.raises(ValueError):
            store.cookie_headers("sid")
        assert store.cookie_value(raw(f"{DEV}=signed")) == ""
        return
    for value in (
        store.cookie_headers("sid")["Set-Cookie"],
        store.clear_cookie_headers()["Set-Cookie"],
    ):
        assert value.startswith(DEV + "=") and "; Secure" not in value
        assert "HttpOnly" in value and "Path=/" in value and "SameSite=Lax" in value
    assert store.cookie_value(raw(f"{DEV}=signed")) == "signed"
    assert store.cookie_value(raw(f"{LEGACY}=signed; {CANONICAL}=signed")) == ""


@pytest.mark.parametrize(
    "header,expected",
    [
        (f"{CANONICAL}=new", "new"),
        (f"{LEGACY}=old", "old"),
        (f"{CANONICAL}=new; {LEGACY}=old", "new"),
        (f"{CANONICAL}=invalid; {LEGACY}=valid", "invalid"),
        (f"{CANONICAL}=; {LEGACY}=valid", ""),
        (f"{CANONICAL}; {LEGACY}=valid", ""),
        (f"{CANONICAL}=a; {CANONICAL}=b", ""),
        (f"{LEGACY}=a; {LEGACY}=b", ""),
        (f"{DEV}=a; {DEV}=b; {CANONICAL}=new", ""),
        (f"{DEV}=other", ""),
        (f"rd_browser=attempt; {LEGACY}=old", "old"),
    ],
)
def test_https_selection_preserves_presence_and_rejects_duplicates(
    header: str, expected: str
) -> None:
    store = WebSessionStore(ORIGIN, b"s" * 32, now_ms=lambda: NOW)
    asyncio.run(store.start_serving())
    assert store.cookie_value(raw(header)) == expected
    assert store.cookie_value(raw(header) + raw("unrelated=one")) == ""


def test_persisted_first_serve_controls_exact_bridge_deadline(tmp_path: Path) -> None:
    disk = PersistentStore(Config(tmp_path / "state"), MemoryKeys())
    clock = [NOW + DAY - 1]
    try:
        assert disk.first_web_serve(ORIGIN, __version__, NOW) == NOW
        store = WebSessionStore(ORIGIN, b"s" * 32, persistence=disk, now_ms=lambda: clock[0])
        asyncio.run(store.start_serving())
        assert store.cookie_value(raw(f"{LEGACY}=old")) == "old"
        clock[0] += 1
        assert store.cookie_value(raw(f"{LEGACY}=old")) == ""
        assert store.cookie_value(raw(f"{CANONICAL}=new")) == "new"
        restarted = WebSessionStore(ORIGIN, b"s" * 32, persistence=disk, now_ms=lambda: clock[0])
        asyncio.run(restarted.start_serving())
        assert restarted.cookie_value(raw(f"{LEGACY}=old")) == ""
        clock[0] = NOW - 1
        assert restarted.cookie_value(raw(f"{LEGACY}=old")) == ""
    finally:
        disk.close()


@pytest.mark.parametrize("reason", ["busy", "full", "unavailable", "keys_unavailable", "closed"])
def test_unavailable_store_keeps_original_instant_and_later_persisted_value_wins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
) -> None:
    disk = PersistentStore(Config(tmp_path / "state"), MemoryKeys())
    clock = [NOW]
    try:
        disk.first_web_serve(ORIGIN, __version__, NOW - 2 * DAY)
        original = disk.first_web_serve
        calls = []

        def unavailable(origin: str, version: str, instant: int) -> int:
            calls.append((origin, version, instant))
            raise StorageUnavailable(reason)

        monkeypatch.setattr(disk, "first_web_serve", unavailable)
        store = WebSessionStore(ORIGIN, b"s" * 32, persistence=disk, now_ms=lambda: clock[0])
        asyncio.run(store.start_serving())
        assert store.cookie_value(raw(f"{LEGACY}=old")) == "old"
        clock[0] += DAY
        asyncio.run(store.start_serving())
        assert store.cookie_value(raw(f"{LEGACY}=old")) == ""
        assert calls == [(ORIGIN, __version__, NOW)] * 2
        monkeypatch.setattr(disk, "first_web_serve", original)
        asyncio.run(store.start_serving())
        assert store._first_serve_ms == NOW - 2 * DAY
        assert store.cookie_value(raw(f"{LEGACY}=old")) == ""
    finally:
        disk.close()


def test_migration_uses_remaining_lifetime_preserves_other_cookies_and_logout() -> None:
    async def scenario() -> None:
        store = WebSessionStore(ORIGIN, b"s" * 32, now_ms=lambda: NOW)
        sid = store.create_session("a", "d", "access", "refresh", NOW + 900_000)
        session = store._sessions[sid]
        session.expires_ms = NOW + 123_456
        original = (
            session.expires_ms,
            session.paired_ms,
            session.extended_ms,
            session.access_token,
            session.refresh_token,
        )

        async def app(scope: Any, receive: Any, send: Any) -> None:
            headers = [(b"set-cookie", b"rd_browser=unchanged; Path=/; HttpOnly; Max-Age=600")]
            if scope["path"] == "/logout":
                store.retire_sid(sid)
                headers = [(b"set-cookie", store.clear_cookie_headers()["Set-Cookie"].encode())]
            await send({"type": "http.response.start", "status": 204, "headers": headers})
            await send({"type": "http.response.body", "body": b""})

        transport = httpx.ASGITransport(app=SessionCookieRenewalMiddleware(app, store))
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
            response = await client.post("/", headers={"Cookie": f"{LEGACY}={store.sign_sid(sid)}"})
            cookies = response.headers.get_list("set-cookie")
            assert len(cookies) == 3
            assert cookies[0] == "rd_browser=unchanged; Path=/; HttpOnly; Max-Age=600"
            assert any(
                value.startswith(CANONICAL + "=") and "Max-Age=123" in value for value in cookies
            )
            assert any(
                value.startswith(LEGACY + "=;") and "Max-Age=0" in value for value in cookies
            )
            assert original == (
                session.expires_ms,
                session.paired_ms,
                session.extended_ms,
                session.access_token,
                session.refresh_token,
            )
            response = await client.post(
                "/logout", headers={"Cookie": f"{LEGACY}={store.sign_sid(sid)}"}
            )
            cookies = response.headers.get_list("set-cookie")
            assert len(cookies) == 2 and all("Max-Age=0" in value for value in cookies)
            assert store.verify_cookie(store.sign_sid(sid)) is None
        browser = store.issue_browser_cookie()
        assert browser.startswith("rd_browser=") and "Max-Age=600" in browser

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["revoked", "locked", "closed", "valid-legacy", "canonical"])
def test_public_get_only_migrates_a_verified_durable_legacy_session(
    tmp_path: Path, case: str
) -> None:
    async def scenario() -> None:
        keys = MemoryKeys()
        keys.restore_generation("authentication", create=True)
        disk = PersistentStore(Config(tmp_path / "state"), keys)
        sessions = SessionStore(
            persistence=disk,
            origin=ORIGIN,
            generation_reader=lambda: keys.restore_generation("authentication").value,
        )
        store = WebSessionStore(
            ORIGIN, b"s" * 32, now_ms=lambda: NOW, persistence=disk, gateway_sessions=sessions
        )
        try:
            access, refresh = sessions.create("account", ["sessions.read"], ["host"], NOW, "device")
            sid = store.create_session("account", "device", access, refresh, NOW + 900_000)
            cookie = store.sign_sid(sid)
            await store.start_serving()
            assert store.verify_cookie(cookie) == sid
            original = (
                store._sessions[sid].expires_ms,
                store._sessions[sid].paired_ms,
                store._sessions[sid].extended_ms,
                store._sessions[sid].access_token,
                store._sessions[sid].refresh_token,
            )
            if case == "revoked":
                assert sessions.revoke_device("account", "device") == 1
            elif case == "locked":
                keys.locked = True
            elif case == "closed":
                disk.close()
            if case in {"revoked", "locked", "closed"}:
                assert store.verify_cookie(cookie) is None
                assert sid in store._sessions

            async def document(scope: Any, receive: Any, send: Any) -> None:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [(b"cache-control", b"public, max-age=0")],
                    }
                )
                await send({"type": "http.response.body", "body": b"public document"})

            name = CANONICAL if case == "canonical" else LEGACY
            transport = httpx.ASGITransport(app=SessionCookieRenewalMiddleware(document, store))
            async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as http:
                response = await http.get("/", headers={"Cookie": f"{name}={cookie}"})
            assert response.status_code == 200 and response.content == b"public document"
            assert response.headers["cache-control"] == "public, max-age=0"
            issued = response.headers.get_list("set-cookie")
            if case in {"revoked", "locked", "closed"}:
                assert issued == []
            else:
                assert len(issued) == (1 if case == "canonical" else 2)
                assert issued[0].startswith(CANONICAL + "=")
                if case == "valid-legacy":
                    assert issued[1].startswith(LEGACY + "=;") and "Max-Age=0" in issued[1]
            assert original == (
                store._sessions[sid].expires_ms,
                store._sessions[sid].paired_ms,
                store._sessions[sid].extended_ms,
                store._sessions[sid].access_token,
                store._sessions[sid].refresh_token,
            )
        finally:
            disk.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("elapsed", [DAY - 1, DAY, DAY + 1])
def test_version_upgrade_and_restart_do_not_reopen_cookie_window(
    tmp_path: Path, elapsed: int
) -> None:
    from relaydeck.host.persistence.contracts import Record, Table
    from relaydeck.host.persistence.sqlite_store import _json

    config, keys = Config(tmp_path / "state"), MemoryKeys()
    disk = PersistentStore(config, keys)
    try:
        scope = (ORIGIN, "0.2.0")
        with disk.transaction() as tx:
            tx.put(
                Record(
                    Table.WEB_ROLLOUTS,
                    _json(scope).decode(),
                    scope,
                    _json({"first_serve_ms": NOW}),
                    1,
                    NOW,
                ),
                expected_revision=None,
            )
    finally:
        disk.close()
    for version in ("0.3.0", "0.3.1"):
        disk = PersistentStore(config, keys)
        try:
            assert disk.first_web_serve(ORIGIN, version, NOW + elapsed) == NOW
            store = WebSessionStore(
                ORIGIN,
                b"s" * 32,
                persistence=disk,
                now_ms=lambda: NOW + elapsed,
            )
            asyncio.run(store.start_serving())
            assert store.cookie_value(raw(f"{LEGACY}=old")) == ("old" if elapsed < DAY else "")
            assert store.cookie_value(raw(f"{CANONICAL}=new")) == "new"
        finally:
            disk.close()
