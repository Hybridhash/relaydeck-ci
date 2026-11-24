"""The update notice value: parsing, cadence, error rules, the dot, and prefs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from relaydeck.client.features.update_notice import model as m
from relaydeck.client.models import preferences as prefs_module
from relaydeck.client.platform.transport.transport import HttpxGatewayTransport
from relaydeck.client.ports.transport import TransportError

DOC: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[4] / "fixtures/gateway/updates.json").read_text()
)


def test_the_fixture_document_parses_into_the_notice() -> None:
    notice = m.parse(DOC)
    assert notice is not None
    assert notice.installed == "0.3.0"
    assert notice.available_version == "0.3.1"
    assert notice.issued == "2026-12-01"
    assert notice.notes == ("A fix for pairing.",)
    assert notice.check == "on"
    assert notice.last_checked == "2026-12-02"
    assert notice.instructions[0].startswith("uv tool install")


def test_no_available_version_is_a_notice_without_an_offer() -> None:
    notice = m.parse(DOC | {"available": None, "instructions": None})
    assert notice is not None and notice.available_version is None
    assert not m.dot_visible(notice, "")


@pytest.mark.parametrize(
    "patch",
    [
        {"installed": ""},
        {"check": "sometimes"},
        {"last_success": "yesterday"},
        {"available": {"version": "1.2", "issued": "2026-12-01", "notes": []}},
        {"available": {"version": "0.3.1", "issued": "01/12/2026", "notes": []}},
        {"available": {"version": "0.3.1", "issued": "2026-12-01", "notes": "one"}},
        {"instructions": "run it"},
        {"available": []},
    ],
)
def test_off_shape_documents_parse_to_nothing(patch: dict[str, Any]) -> None:
    assert m.parse(DOC | patch) is None
    assert m.parse(None) is None and m.parse("updates") is None


def test_the_fetch_cadence_is_once_then_hourly() -> None:
    assert m.fetch_due(None, 1_000)
    assert not m.fetch_due(1_000, 1_000 + m.FETCH_INTERVAL_MS - 1)
    assert m.fetch_due(1_000, 1_000 + m.FETCH_INTERVAL_MS)


@pytest.mark.parametrize("code", ["not_found", "forbidden", "unauthenticated"])
def test_hidden_failures_clear_and_other_failures_keep_the_notice(code: str) -> None:
    notice = m.parse(DOC)
    assert m.after_error(notice, code) is None
    assert m.after_error(notice, "host_offline") is notice
    assert m.after_error(notice, "upstream_incompatible") is notice


def test_the_dot_waits_for_a_version_the_device_has_not_seen_in_settings() -> None:
    notice = m.parse(DOC)
    assert m.dot_visible(notice, "")
    assert m.dot_visible(notice, "0.3.0")
    assert not m.dot_visible(notice, "0.3.1")
    assert not m.dot_visible(None, "")


def test_the_acknowledged_version_round_trips_through_device_prefs() -> None:
    prefs = prefs_module.TerminalPrefs(acknowledged_update="0.3.1")
    assert prefs_module.clean(json.loads(prefs_module.encode(prefs))) == prefs
    for bad in ("0.3.1; rm", "version one", "x" * 33, 31):
        stored = prefs_module.clean({"acknowledged_update": bad})
        assert stored.acknowledged_update == ""


def _transport(handler: Any) -> HttpxGatewayTransport:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://gw.test")

    async def token() -> str:
        return "test-token"

    return HttpxGatewayTransport(base_url="https://gw.test", token=token, client=client)


def test_the_transport_reads_and_validates_the_updates_document() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/updates"
        return httpx.Response(200, json=DOC)

    assert asyncio.run(_transport(handler).updates()) == DOC


def test_a_bare_gateway_404_surfaces_as_not_found() -> None:
    transport = _transport(lambda request: httpx.Response(404, json={"code": "missing"}))
    with pytest.raises(TransportError) as caught:
        asyncio.run(transport.updates())
    assert caught.value.code == "not_found"


def test_a_failed_preferences_read_is_not_an_absent_document() -> None:
    async def broken(key: str) -> Any:
        raise RuntimeError("synthetic storage error")

    async def absent(key: str) -> Any:
        return None

    assert asyncio.run(prefs_module.try_load(broken)) is None
    assert asyncio.run(prefs_module.try_load(absent)) == prefs_module.TerminalPrefs()
    assert asyncio.run(prefs_module.load(broken)) == prefs_module.TerminalPrefs()


def test_an_off_schema_document_is_refused_by_the_transport() -> None:
    broken = DOC | {"check": "sometimes"}
    transport = _transport(lambda request: httpx.Response(200, json=broken))
    with pytest.raises(TransportError) as caught:
        asyncio.run(transport.updates())
    assert caught.value.code == "upstream_incompatible"
